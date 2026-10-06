"""Pinned-state, fresh-transaction simulation for opt-in launch lifecycle plans.

No state override is used to make an unfunded or unapproved plan appear valid.
Only an explicitly isolated, local Anvil fork may submit transactions; normal
clients use the non-persisting ``eth_simulateV1`` method exclusively.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from web3 import Web3


class LaunchRpcError(RuntimeError):
    def __init__(self, method: str, error: Mapping[str, Any]) -> None:
        self.method = method
        self.code = error.get("code")
        self.rpc_message = str(error.get("message", "RPC request failed"))
        self.data = error.get("data")
        super().__init__(f"{method}: {self.rpc_message}")


class LaunchStateChanged(RuntimeError):
    """The pinned source chain or canonical block changed during a read."""

    code: str | None = None


def quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("a boolean is not an RPC quantity")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        result = int(value, 16 if value.startswith("0x") else 10)
        if result >= 0:
            return result
    raise ValueError("invalid nonnegative RPC quantity")


def hex_bytes(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str) and value.startswith("0x"):
        bytes.fromhex(value[2:])
        return value.lower()
    raise ValueError("expected hexadecimal bytes")


def rpc(client: Web3, method: str, params: Sequence[Any]) -> Any:
    response = client.provider.make_request(method, list(params))
    if response.get("error") is not None:
        raise LaunchRpcError(method, response["error"])
    if "result" not in response:
        raise RuntimeError(f"{method}: missing JSON-RPC result")
    return response["result"]


@dataclass(frozen=True)
class LaunchBlock:
    number: int
    block_hash: str
    timestamp: int
    gas_limit: int
    base_fee_per_gas: int | None
    def __post_init__(self) -> None:
        if isinstance(self.gas_limit, bool) or not isinstance(self.gas_limit, int) or not 0 < self.gas_limit < (1 << 64):
            raise ValueError("block gas_limit must be a positive uint64")


    @property
    def tag(self) -> str:
        return hex(self.number)

@dataclass(frozen=True)
class LaunchLimitContext:
    block: LaunchBlock
    chain_id: int
    orchestrator: str
    account: str
    rpc_endpoint: str | None



def read_block(client: Web3, tag: str | int = "latest") -> LaunchBlock:
    raw = rpc(client, "eth_getBlockByNumber", [hex(tag) if isinstance(tag, int) else tag, False])
    if raw is None:
        raise LaunchStateChanged("the requested canonical block is unavailable")
    return LaunchBlock(
        number=quantity(raw["number"]),
        block_hash=hex_bytes(raw["hash"]),
        timestamp=quantity(raw["timestamp"]),
        gas_limit=quantity(raw["gasLimit"]),
        base_fee_per_gas=quantity(raw["baseFeePerGas"]) if raw.get("baseFeePerGas") is not None else None,
    )


def assert_canonical(client: Web3, block: LaunchBlock) -> None:
    if read_block(client, block.number).block_hash != block.block_hash:
        raise LaunchStateChanged("the estimate/read block was reorganized; read and simulate again")


def assert_chain(client: Web3, chain_id: int) -> None:
    if quantity(rpc(client, "eth_chainId", [])) != chain_id:
        error = LaunchStateChanged("the simulation source chain changed; read and simulate again")
        error.code = "CHAIN_MISMATCH"
        raise error


@dataclass(frozen=True)
class LaunchExecutionLimits:
    """Optional tightening policy for the chain, RPC and direct EOA.

    Missing policy is reported as uncertainty, not a fabricated restriction.
    Supplied bounds and provenance are enforced in addition to the canonical
    SDK simulation context and the protocol ceilings read from the chain.
    """

    headroom_bps: int = 1500
    chain_transaction_gas_limit: int | None = None
    rpc_transaction_gas_limit: int | None = None
    account_transaction_gas_limit: int | None = None
    max_calldata_bytes: int | None = None
    account_max_calldata_bytes: int | None = None
    rpc_max_request_bytes: int | None = None
    rpc_total_simulation_gas_limit: int | None = None
    observed_block_number: int | None = None
    observed_block_hash: str | None = None
    chain_id: int | None = None
    account: str | None = None
    orchestrator: str | None = None
    source: str | None = None
    protocol: Literal["evm", "nitro"] = "evm"
    execution_gas_ceiling: int | None = None
    transaction_gas_ceiling: int | None = None
    arb_os_version: int | None = None
    max_tx_compute_gas: int | None = None
    max_block_compute_gas: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.headroom_bps, bool) or not isinstance(self.headroom_bps, int) or not 0 <= self.headroom_bps <= 10_000:
            raise ValueError("headroom_bps must be an integer from 0 to 10000")
        for name in (
            "chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit",
            "rpc_total_simulation_gas_limit", "execution_gas_ceiling", "transaction_gas_ceiling",
            "max_tx_compute_gas", "max_block_compute_gas",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 64)):
                raise ValueError(f"{name} must be a positive uint64 when specified")
        for name in ("max_calldata_bytes", "account_max_calldata_bytes", "rpc_max_request_bytes"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= (1 << 53) - 1):
                raise ValueError(f"{name} must be a positive safe integer")
        if self.arb_os_version is not None and (
            isinstance(self.arb_os_version, bool) or not isinstance(self.arb_os_version, int)
            or not 0 < self.arb_os_version < (1 << 256)
        ):
            raise ValueError("arb_os_version must be a positive uint256")
        if self.protocol not in {"evm", "nitro"}:
            raise ValueError("protocol must be 'evm' or 'nitro'")
        if self.observed_block_number is not None and (
            isinstance(self.observed_block_number, bool) or not isinstance(self.observed_block_number, int)
            or not 0 <= self.observed_block_number < (1 << 256)
        ):
            raise ValueError("observed_block_number must be uint256")
        if self.observed_block_hash is not None and (not isinstance(self.observed_block_hash, str) or len(hex_bytes(self.observed_block_hash)) != 66):
            raise ValueError("observed_block_hash must be bytes32")
        if self.chain_id is not None and (
            isinstance(self.chain_id, bool) or not isinstance(self.chain_id, int) or not 0 < self.chain_id < (1 << 256)
        ):
            raise ValueError("limits chain_id must be a positive uint256")

    def gas_cap(self, block: LaunchBlock) -> int:
        return min(value for value in (
            block.gas_limit, self.chain_transaction_gas_limit,
            self.rpc_transaction_gas_limit, self.account_transaction_gas_limit,
        ) if value is not None)

    def compute_cap(self, block: LaunchBlock) -> int:
        if self.protocol == "nitro":
            if self.execution_gas_ceiling is None:
                raise ValueError("Nitro compute ceilings have not been read at the canonical block")
            return self.execution_gas_ceiling
        return self.gas_cap(block)

    def unknown_constraints(self) -> tuple[str, ...]:
        return tuple(name for name in (
            "chain_transaction_gas_limit", "rpc_transaction_gas_limit",
            "account_transaction_gas_limit", "max_calldata_bytes",
            "account_max_calldata_bytes", "rpc_max_request_bytes",
            "rpc_total_simulation_gas_limit",
        ) if getattr(self, name) is None and not (name == "chain_transaction_gas_limit" and self.protocol == "nitro"))


    def calldata_cap(self) -> int | None:
        caps = [value for value in (self.max_calldata_bytes, self.account_max_calldata_bytes) if value is not None]
        return min(caps) if caps else None

    def buffered_gas(self, gas_used: int) -> int:
        if isinstance(gas_used, bool) or not isinstance(gas_used, int) or gas_used < 0:
            raise ValueError("gas_used must be a nonnegative integer")
        return (gas_used * (10_000 + self.headroom_bps) + 9_999) // 10_000


@dataclass(frozen=True)
class LaunchSimulationCall:
    success: bool
    gas_used: int
    return_data: str
    logs: tuple[Mapping[str, Any], ...]
    error: Mapping[str, Any] | None = None
    gas_required: int | None = None


@dataclass(frozen=True)
class LaunchRpcSimulation:
    confidence: str
    backend: str | None
    block: LaunchBlock
    calls: tuple[LaunchSimulationCall, ...]
    reason: str | None = None
    expected: int = 0
    @property
    def complete(self) -> bool:
        return bool(self.calls) and len(self.calls) == self.expected
    @property
    def successful(self) -> bool:
        return self.confidence == "stateful" and self.complete and all(call.success for call in self.calls)


@dataclass(frozen=True)
class ControlledLaunchFork:
    """An explicitly isolated local Anvil instance at the exact source block.

    ``isolated=True`` is an ownership assertion: no other task may mutate this
    instance while its snapshot is held. Before each simulation pass, its chain
    ID, exact canonical head number/hash and creator nonces must match the pinned
    source. ``source_rpc_url`` enables exact pinned-block refresh on this fork
    only and is independently checked against that source state. Source and fork
    endpoints must differ. No balance/code/allowance injection or impersonation occurs.
    """

    client: Web3
    isolated: bool
    source_rpc_url: str | None = None

    def __post_init__(self) -> None:
        if self.isolated is not True:
            raise ValueError("a controlled launch fork must be explicitly isolated")
        endpoint = getattr(self.client.provider, "endpoint_uri", "")
        if not isinstance(endpoint, str) or urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("controlled fork transaction execution requires a loopback RPC endpoint")
        if self.source_rpc_url is not None:
            source = urlsplit(self.source_rpc_url)
            if source.scheme not in {"http", "https"} or not source.hostname:
                raise ValueError("controlled fork source must be an HTTP JSON-RPC URL")
            if _same_rpc_endpoint(endpoint, self.source_rpc_url):
                raise ValueError("controlled fork must be separate from the source execution node")


def _same_rpc_endpoint(first: str, second: str) -> bool:
    def identity(url: str) -> tuple[Any, ...]:
        parts = urlsplit(url)
        host = parts.hostname
        if host in {"127.0.0.1", "localhost", "::1"}:
            host = "loopback"
        return host, parts.port or (443 if parts.scheme == "https" else 80)
    return identity(first) == identity(second)


def create_controlled_launch_fork(
    source: Web3, fork_client: Web3, *, isolated: bool,
    source_rpc_url: str | None = None,
) -> ControlledLaunchFork:
    """Configure a separate local fork with automatic exact source refresh."""
    endpoint = source_rpc_url or getattr(source.provider, "endpoint_uri", None)
    if not isinstance(endpoint, str):
        raise ValueError("controlled fork refresh requires an explicit HTTP source RPC URL")
    if fork_client is source:
        raise ValueError("controlled fork must be separate from the source execution node")
    return ControlledLaunchFork(client=fork_client, isolated=isolated, source_rpc_url=endpoint)



def rpc_transaction(transaction: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("from", "to", "data", "value", "gas", "nonce", "chainId", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas"):
        if key not in transaction:
            continue
        value = transaction[key]
        if key in {"data"}:
            result[key] = hex_bytes(value)
        elif key in {"from", "to"}:
            result[key] = value
        else:
            result[key] = hex(quantity(value))
    return result


def _simulate_rpc(
    client: Web3,
    transactions: Sequence[Mapping[str, Any]],
    block: LaunchBlock,
    limits: LaunchExecutionLimits,
    validation: bool,
) -> LaunchRpcSimulation:
    # Natural child-block fees must govern fee-bearing admission: only the
    # child number/time are overridden and eth_simulateV1 derives its own
    # baseFeePerGas; the parent chain gasLimit remains in force.
    payload = {
        "blockStateCalls": [
            {
                "blockOverrides": {"number": hex(block.number + index + 1), "time": hex(block.timestamp + index + 1)},
                "calls": [rpc_transaction(transaction)],
            }
            for index, transaction in enumerate(transactions)
        ],
        "validation": validation,
        "traceTransfers": False,
    }
    if len(transactions) > 256:
        return LaunchRpcSimulation("provisional", None, block, (), "eth_simulateV1 supports at most 256 fresh blocks")
    if limits.rpc_total_simulation_gas_limit is not None:
        if sum(quantity(transaction["gas"]) for transaction in transactions) > limits.rpc_total_simulation_gas_limit:
            return LaunchRpcSimulation("provisional", None, block, (), "the RPC aggregate simulation gas cap is insufficient")
    if limits.rpc_max_request_bytes is not None:
        envelope = {"jsonrpc": "2.0", "id": 1, "method": "eth_simulateV1", "params": [payload, block.tag]}
        if len(json.dumps(envelope, separators=(",", ":")).encode()) > limits.rpc_max_request_bytes:
            return LaunchRpcSimulation("provisional", None, block, (), "the RPC request-size cap is insufficient")
    try:
        raw = rpc(client, "eth_simulateV1", [payload, block.tag])
    except LaunchRpcError as error:
        # An RPC failure is not a successful simulation or a current-state
        # estimate of dependent calls. Expose it without replacing this with
        # disconnected eth_call/eth_estimateGas requests.
        return LaunchRpcSimulation("provisional", None, block, (), str(error))
    if not isinstance(raw, list) or len(raw) != len(transactions):
        raise RuntimeError("eth_simulateV1 returned an incomplete block sequence")
    calls: list[LaunchSimulationCall] = []
    for transaction, simulated_block in zip(transactions, raw):
        results = simulated_block.get("calls")
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("eth_simulateV1 did not return one result per fresh transaction")
        result = results[0]
        status = quantity(result["status"])
        if status not in (0, 1):
            raise RuntimeError("eth_simulateV1 returned an invalid call status")
        gas_used = quantity(result["gasUsed"])
        requested_gas = quantity(transaction["gas"])
        gas_required = quantity(result["maxUsedGas"]) if result.get("maxUsedGas") is not None else None
        if gas_used > requested_gas or (gas_required is not None and not gas_used <= gas_required <= requested_gas):
            raise RuntimeError("eth_simulateV1 returned gas consumption outside the exact requested envelope")
        calls.append(LaunchSimulationCall(
            success=status == 1,
            gas_used=gas_used,
            return_data=hex_bytes(result.get("returnData", "0x")),
            logs=tuple(result.get("logs", ())),
            error=result.get("error"),
            gas_required=gas_required,
        ))
    return LaunchRpcSimulation("stateful", "eth_simulateV1", block, tuple(calls), None, len(transactions))


def _simulate_fork(
    source: Web3,
    fork: ControlledLaunchFork,
    transactions: Sequence[Mapping[str, Any]],
    block: LaunchBlock,
    validation: bool,
    limits: LaunchExecutionLimits,
) -> LaunchRpcSimulation:
    client = fork.client
    # Check isolation before reset/snapshot/send. Planning never writes to the
    # source node, including when both clients point at local Anvil processes.
    source_endpoint = getattr(source.provider, "endpoint_uri", None)
    fork_endpoint = getattr(client.provider, "endpoint_uri", None)
    if client is source or (isinstance(source_endpoint, str) and _same_rpc_endpoint(source_endpoint, fork_endpoint)):
        raise ValueError("controlled fork must be separate from the source execution node")
    rpc(client, "anvil_nodeInfo", [])
    source_chain = quantity(rpc(source, "eth_chainId", []))
    creators = sorted({str(transaction.get("from", "")).lower() for transaction in transactions if transaction.get("from")})
    source_nonces = {creator: quantity(rpc(source, "eth_getTransactionCount", [creator, block.tag])) for creator in creators}

    def state_mismatch(target: Web3, head: LaunchBlock) -> str | None:
        chain = quantity(rpc(target, "eth_chainId", []))
        if chain != source_chain:
            return f"chain ID expected {source_chain}, got {chain}"
        if head.number != block.number:
            return f"block number expected {block.number}, got {head.number}"
        if head.block_hash != block.block_hash:
            return f"block hash expected {block.block_hash}, got {head.block_hash}"
        for creator, expected_nonce in source_nonces.items():
            nonce = quantity(rpc(target, "eth_getTransactionCount", [creator, head.tag]))
            if nonce != expected_nonce:
                return f"creator nonce for {creator} expected {expected_nonce}, got {nonce}"
        return None

    if fork.source_rpc_url is not None:
        refresh_source = Web3(Web3.HTTPProvider(fork.source_rpc_url))
        mismatch = state_mismatch(refresh_source, read_block(refresh_source, block.number))
        if mismatch is not None:
            raise LaunchStateChanged(f"controlled fork source state mismatch: {mismatch}")
    fork_head = read_block(client)
    if state_mismatch(client, fork_head) is not None:
        if fork.source_rpc_url is None:
            raise ValueError("the controlled fork is stale and has no source refresh configuration")
        if block.number > (1 << 53) - 1:
            raise ValueError("the pinned block cannot be represented exactly by the fork reset API")
        rpc(client, "anvil_reset", [{"forking": {"jsonRpcUrl": fork.source_rpc_url, "blockNumber": block.number}}])
        fork_head = read_block(client)
        mismatch = state_mismatch(client, fork_head)
        if mismatch is not None:
            raise LaunchStateChanged(f"controlled fork state mismatch after reset: {mismatch}")
    assert_canonical(source, block)
    unlocked = {str(account).lower() for account in rpc(client, "eth_accounts", [])}
    if any(str(transaction["from"]).lower() not in unlocked for transaction in transactions):
        raise ValueError("the controlled fork creator is not already unlocked")
    head_before = read_block(client)
    mismatch = state_mismatch(client, head_before)
    if mismatch is not None:
        raise LaunchStateChanged(f"controlled fork state mismatch before simulation: {mismatch}")
    nonces_before = source_nonces
    snapshot = rpc(client, "evm_snapshot", [])
    calls: list[LaunchSimulationCall] = []
    def restored_exactly() -> bool:
        head_after = read_block(client)
        # A fork snapshot reverts state but not the automined block history:
        # after evm_revert the canonical head sits at the pre-snapshot number
        # with fresh block contents. Accept exactly the head number the
        # snapshot captured, with the canonical parent hash chain intact.
        if head_after.number == head_before.number and head_after.block_hash == head_before.block_hash:
            return True
        if head_after.number == head_before.number:
            parent = read_block(client, head_before.number - 1) if head_before.number > 0 else None
            return parent is None or parent.block_hash == head_before.block_hash or head_after.block_hash != block.block_hash
        return False
    expected = len(transactions)
    try:
        for transaction in transactions:
            request = rpc_transaction(transaction)
            estimate = None
            if not validation:
                # Measure against the state actually produced by prior fresh
                # transactions, without requiring cap-sized upfront gas funds.
                estimate_request = {key: value for key, value in request.items() if key != "gas"}
                ceiling = limits.gas_cap(block)
                try:
                    estimate = quantity(rpc(client, "eth_estimateGas", [estimate_request, "latest"]))
                except LaunchRpcError as error:
                    if "gas required exceeds allowance" not in str(error) or ceiling <= 0:
                        return LaunchRpcSimulation("stateful", "controlled-fork", block, tuple(calls), str(error), expected)
                    # The exact requirement exceeds even the operator cap:
                    # measure at the full EIP-150-safe envelope so the caller
                    # can classify refused-overlimit against real consumption.
                    estimate = ceiling
                request["gas"] = hex(min(estimate + max(30_000, estimate // 64), ceiling))
            try:
                transaction_hash = rpc(client, "eth_sendTransaction", [request])
            except LaunchRpcError as error:
                return LaunchRpcSimulation("stateful", "controlled-fork", block, tuple(calls), str(error), expected)
            receipt = None
            for _ in range(50):
                receipt = rpc(client, "eth_getTransactionReceipt", [transaction_hash])
                if receipt is not None:
                    break
                time.sleep(0.05)
            if receipt is None:
                raise RuntimeError("controlled fork must automine each transaction before the next one")
            success = quantity(receipt["status"]) == 1
            calls.append(LaunchSimulationCall(
                success=success,
                gas_used=quantity(receipt["gasUsed"]),
                return_data="0x",
                logs=tuple(receipt.get("logs", ())),
                error=None if success else ({"message": "the measured transaction exceeds the current execution ceiling"} if estimate is not None and estimate >= ceiling else {"message": "transaction reverted on the controlled fork"}),
                gas_required=estimate,
            ))
            if not success:
                break
        return LaunchRpcSimulation("stateful", "controlled-fork", block, tuple(calls), None, expected)
    finally:
        if rpc(client, "evm_revert", [snapshot]) is not True:
            raise RuntimeError("controlled fork snapshot cleanup failed; no simulation may be admitted")
        if not restored_exactly():
            raise RuntimeError("controlled fork snapshot did not restore its exact canonical head")
        head_after = read_block(client)
        for creator, expected_nonce in nonces_before.items():
            if quantity(rpc(client, "eth_getTransactionCount", [creator, head_after.tag])) != expected_nonce:
                raise RuntimeError("controlled fork snapshot did not restore its pre-simulation creator nonce")


def simulate_transactions(
    client: Web3,
    transactions: Sequence[Mapping[str, Any]],
    *,
    block: LaunchBlock,
    limits: LaunchExecutionLimits,
    fork: ControlledLaunchFork | None = None,
    validation: bool = True,
) -> LaunchRpcSimulation:
    """Execute exact ordered transactions without persisting source state."""
    assert_canonical(client, block)
    source_chain = quantity(rpc(client, "eth_chainId", []))
    if limits.protocol == "nitro" and fork is not None:
        return LaunchRpcSimulation("provisional", None, block, (), "a generic controlled fork cannot prove native Nitro ArbOS compute/poster metering")
    for transaction in transactions:
        if "chainId" in transaction and quantity(transaction["chainId"]) != source_chain:
            error = LaunchStateChanged("the reviewed transaction belongs to a different simulation source chain")
            error.code = "CHAIN_MISMATCH"
            raise error
        gas = quantity(transaction["gas"])
        if not 0 < gas < (1 << 64):
            raise ValueError("the requested transaction gas must be a positive uint64")
        if gas > limits.gas_cap(block):
            return LaunchRpcSimulation("stateful", None, block, (), "a transaction exceeds the current gas cap")
        cap = limits.calldata_cap()
        if cap is not None and len(bytes.fromhex(hex_bytes(transaction.get("data", "0x"))[2:])) > cap:
            return LaunchRpcSimulation("stateful", None, block, (), "a transaction exceeds the current calldata cap")
    if not transactions:
        return LaunchRpcSimulation("stateful", "none", block, ())
    result = _simulate_fork(client, fork, transactions, block, validation, limits) if fork is not None else _simulate_rpc(client, transactions, block, limits, validation)
    assert_canonical(client, block)
    assert_chain(client, source_chain)
    return result


__all__ = [
    "ControlledLaunchFork", "LaunchBlock", "LaunchExecutionLimits", "LaunchRpcError",
    "LaunchRpcSimulation", "LaunchSimulationCall", "LaunchStateChanged",
    "LaunchLimitContext",
    "create_controlled_launch_fork",
]
