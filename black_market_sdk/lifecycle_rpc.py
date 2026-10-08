"""Pinned-state, fresh-transaction simulation for opt-in launch lifecycle plans.

No state override is used to make an unfunded or unapproved plan appear valid.
Only an explicitly isolated, local Anvil fork may submit transactions; normal
clients use the non-persisting ``eth_simulateV1`` method exclusively.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Literal
from contextlib import contextmanager
from functools import wraps
from threading import BoundedSemaphore, RLock, Thread
from typing import Callable, Protocol
import asyncio
import inspect
import re

from eth_utils import keccak
from urllib.parse import urlsplit

from web3 import Web3


class LaunchRpcError(RuntimeError):
    def __init__(self, method: str, error: Mapping[str, Any]) -> None:
        self.method = method
        code = error.get("code")
        self.code = code if isinstance(code, int) and not isinstance(code, bool) and abs(code) <= (1 << 53) - 1 else None
        self._native = dict(error)
        super().__init__(f"{method} failed" + (f" (RPC code {self.code})" if self.code is not None else ""))


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
    if isinstance(value, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        return value.lower()
    raise ValueError("expected hexadecimal bytes")


class LifecycleRpcClient(Protocol):
    """A raw request client can be supplied instead of a Web3 transport."""
    def request(self, args: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class LifecycleDiagnosticEvent:
    stage: str
    phase: Literal["queued", "start", "success", "failure", "reuse"]
    duration_ms: float | None = None
    queue_ms: float | None = None
    request_id: int | None = None
    method: str | None = None


LifecycleDiagnosticListener = Callable[[LifecycleDiagnosticEvent], Any]
_DIAGNOSTIC_METHODS = frozenset((
    "eth_chainId", "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getCode",
    "eth_getBalance", "eth_call", "eth_estimateGas", "eth_gasPrice", "eth_simulateV1",
    "eth_getTransactionCount", "eth_getTransactionReceipt", "eth_getTransactionByHash",
))


_diagnostic_loop: asyncio.AbstractEventLoop | None = None
_diagnostic_loop_lock = RLock()


def _observer_loop() -> asyncio.AbstractEventLoop:
    """Start no background work unless an explicitly supplied observer awaits."""
    global _diagnostic_loop
    with _diagnostic_loop_lock:
        if _diagnostic_loop is None:
            _diagnostic_loop = asyncio.new_event_loop()
            Thread(target=_diagnostic_loop.run_forever, name="launch-diagnostics", daemon=True).start()
        return _diagnostic_loop


def _emit(listener: LifecycleDiagnosticListener | None, event: LifecycleDiagnosticEvent) -> None:
    if listener is None:
        return
    try:
        pending = listener(event)
        if inspect.isawaitable(pending):
            async def observe() -> None:
                try:
                    await pending
                except BaseException:
                    pass
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            loop = running_loop or getattr(listener, "_diagnostic_loop", None) or _observer_loop()
            observer = observe()
            try:
                if loop is running_loop:
                    loop.create_task(observer)
                else:
                    asyncio.run_coroutine_threadsafe(observer, loop)
            except BaseException:
                observer.close()
                if inspect.iscoroutine(pending):
                    pending.close()
                raise
    except BaseException:
        pass  # Local observers cannot change execution, policy or admission.


def _transport_identity(client: Any) -> tuple[Any, ...]:
    provider = getattr(client, "provider", None)
    request = getattr(client, "request", None) if hasattr(client, "request") else getattr(provider, "make_request", None)
    return (provider, getattr(provider, "endpoint_uri", None),
            getattr(request, "__self__", None), getattr(request, "__func__", request))



class _LifecycleReadClient:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.provider = getattr(source, "provider", None)
        self.on_diagnostic = getattr(source, "on_diagnostic", None)
        if self.on_diagnostic is not None:
            listener = self.on_diagnostic
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            def observe(event):
                return listener(event)
            observe._diagnostic_loop = loop
            self.on_diagnostic = observe
        self.supports_read_batching = getattr(source, "supports_read_batching", False) is True
        self._source = source
        self._transport = _transport_identity(source)
        self._batching = getattr(source, "supports_read_batching", None)
        self._semaphore = BoundedSemaphore(8 if self.supports_read_batching else 1)
        self._lock = RLock()
        self._reads: dict[str, Any] = {}
        self._pending: dict[str, Future] = {}
        self._chain_id: Future | None = None
        self._sequence = 0
        self.closed = False
        self.nitro_proofs: set[tuple[Any, ...]] = set()

    def assert_open(self) -> None:
        if self.closed:
            from .lifecycle import LifecyclePlanningError
            raise LifecyclePlanningError("READ_SCOPE_CLOSED", "Lifecycle read invocation has already settled")
        if self.source is not self._source or _transport_identity(self.source) != self._transport or getattr(self.source, "supports_read_batching", None) != self._batching:
            from .lifecycle import LifecyclePlanningError
            raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Lifecycle source transport or batching changed during its invocation")

    def run(self, method: str, params: Sequence[Any]) -> Any:
        self.assert_open()
        listener = self.on_diagnostic
        if listener is not None:
            queued = time.perf_counter()
            with self._lock:
                self._sequence += 1
                request_id = self._sequence
            observed = method if method in _DIAGNOSTIC_METHODS else "other"
            _emit(listener, LifecycleDiagnosticEvent("rpc", "queued", request_id=request_id, method=observed))
        with self._semaphore:
            self.assert_open()
            if listener is not None:
                start = time.perf_counter()
                _emit(listener, LifecycleDiagnosticEvent("rpc", "start", queue_ms=(start - queued) * 1000, request_id=request_id, method=observed))
            try:
                result = _request(self.source, method, params)
                self.assert_open()
            except Exception:
                if listener is not None:
                    _emit(listener, LifecycleDiagnosticEvent("rpc", "failure", duration_ms=(time.perf_counter() - start) * 1000, request_id=request_id, method=observed))
                raise
            if listener is not None:
                _emit(listener, LifecycleDiagnosticEvent("rpc", "success", duration_ms=(time.perf_counter() - start) * 1000, request_id=request_id, method=observed))
            self.assert_open()
            return result

    def observe_chain_id(self) -> int:
        self.assert_open()
        with self._lock:
            pending = self._chain_id
            owner = pending is None
            if owner:
                pending = Future()
                self._chain_id = pending
        if not owner:
            result = pending.result()
            self.assert_open()
            return result
        try:
            result = quantity(self.run("eth_chainId", []))
            with self._lock:
                self.assert_open()
                pending.set_result(result)
                return result
        except BaseException as error:
            pending.set_exception(error)
            raise

    def read(self, method: str, params: Sequence[Any], block: LaunchBlock) -> Any:
        self.assert_open()
        key = json.dumps([block.number, block.block_hash.lower(), method, params], separators=(",", ":"))
        with self._lock:
            if key in self._reads:
                if self.on_diagnostic is not None:
                    _emit(self.on_diagnostic, LifecycleDiagnosticEvent("rpc", "reuse", method=method))
                self.assert_open()
                return self._reads[key]
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = Future()
                self._pending[key] = pending
        if not owner:
            if self.on_diagnostic is not None:
                _emit(self.on_diagnostic, LifecycleDiagnosticEvent("rpc", "reuse", method=method))
            result = pending.result()
            self.assert_open()
            return result
        try:
            result = self.run(method, params)
            with self._lock:
                self.assert_open()
                self._reads[key] = result
                self._pending.pop(key, None)
                pending.set_result(result)
                return result
        except BaseException as error:
            with self._lock:
                self._pending.pop(key, None)
            pending.set_exception(error)
            raise

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self._reads.clear()
            self._pending.clear()
            self._chain_id = None
            self.nitro_proofs.clear()


@contextmanager
def lifecycle_stage(client: Any, stage: str):
    listener = getattr(client, "on_diagnostic", None)
    if listener is None:
        yield
        return
    start = time.perf_counter()
    _emit(listener, LifecycleDiagnosticEvent(stage, "start"))
    try:
        yield
    except BaseException:
        _emit(listener, LifecycleDiagnosticEvent(stage, "failure", duration_ms=(time.perf_counter() - start) * 1000))
        raise
    else:
        _emit(listener, LifecycleDiagnosticEvent(stage, "success", duration_ms=(time.perf_counter() - start) * 1000))


def read_invocation(stage: str):
    def decorate(work):
        @wraps(work)
        def invoke(client, *args, **kwargs):
            if isinstance(client, _LifecycleReadClient):
                client.assert_open()
                with lifecycle_stage(client, stage):
                    return work(client, *args, **kwargs)
            scope = _LifecycleReadClient(client)
            try:
                with lifecycle_stage(scope, stage):
                    return work(scope, *args, **kwargs)
            finally:
                scope.close()
        return invoke
    return decorate


def async_read_invocation(stage: str):
    def decorate(work):
        @wraps(work)
        async def invoke(client, *args, **kwargs):
            if isinstance(client, _LifecycleReadClient):
                client.assert_open()
                with lifecycle_stage(client, stage):
                    return await work(client, *args, **kwargs)
            scope = _LifecycleReadClient(client)
            try:
                with lifecycle_stage(scope, stage):
                    return await work(scope, *args, **kwargs)
            finally:
                scope.close()
        return invoke
    return decorate


def _request(client: Any, method: str, params: Sequence[Any]) -> Any:
    try:
        if hasattr(client, "request"):
            return client.request({"method": method, "params": list(params)})
        response = client.provider.make_request(method, list(params))
    except (LaunchRpcError, LaunchStateChanged):
        raise
    except Exception as error:
        from .lifecycle import LifecyclePlanningError
        if isinstance(error, LifecyclePlanningError) or getattr(error, "name", None) == "AbortError":
            raise
        native = error.args[0] if error.args and isinstance(error.args[0], Mapping) else {
            "code": getattr(error, "code", None),
            "message": getattr(error, "message", str(error)),
            **({"data": error.data} if hasattr(error, "data") else {}),
            **({"details": error.details} if hasattr(error, "details") else {}),
            **({"status": error.status} if hasattr(error, "status") else {}),
            **({"statusCode": error.statusCode} if hasattr(error, "statusCode") else {}),
            **({"status_code": error.status_code} if hasattr(error, "status_code") else {}),
        }
        raise LaunchRpcError(method, native) from error
    if not isinstance(response, Mapping):
        raise RuntimeError("JSON-RPC response must be an object")
    if response.get("error") is not None:
        if not isinstance(response["error"], Mapping):
            raise RuntimeError("JSON-RPC error must be an object")
        raise LaunchRpcError(method, response["error"])
    if "result" not in response:
        raise RuntimeError(f"{method}: missing JSON-RPC result")
    return response["result"]


def rpc(client: Any, method: str, params: Sequence[Any]) -> Any:
    return client.run(method, params) if isinstance(client, _LifecycleReadClient) else _request(client, method, params)


def read_chain_id(client: Any) -> int:
    """Observe identity once per invocation; canonical/submission guards stay live."""
    return client.observe_chain_id() if isinstance(client, _LifecycleReadClient) else quantity(rpc(client, "eth_chainId", []))


def failure_reason(failure: Any) -> str:
    """Expose fixed refusal text and numeric codes, never native messages/data."""
    from .lifecycle import LifecyclePlanningError
    for _ in range(8):
        if failure is None:
            break
        native = failure._native if isinstance(failure, LaunchRpcError) else failure
        field = native.get if isinstance(native, Mapping) else lambda key: getattr(native, key, None)
        status = field("status")
        if status is None:
            status = field("statusCode")
        if status is None:
            status = field("status_code")
        if isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599:
            return f"HTTP read failed (status {status})"
        code = field("code")
        if isinstance(code, int) and not isinstance(code, bool) and abs(code) <= (1 << 53) - 1:
            return f"RPC request failed (code {code})"
        if isinstance(failure, (LifecyclePlanningError, LaunchStateChanged)):
            return "Lifecycle planning guard rejected the request"
        cause = field("cause")
        failure = cause if cause is not None else getattr(failure, "__cause__", None)
    return "Read or execution proof failed"


def pinned_rpc(client: Any, method: str, params: Sequence[Any], block: LaunchBlock) -> Any:
    if method not in {"eth_call", "eth_getCode", "eth_getBalance"} or len(params) != 2 or params[1] != block.tag:
        raise ValueError("Reusable reads require an exact pinned block and no state overrides")
    return client.read(method, params, block) if isinstance(client, _LifecycleReadClient) else rpc(client, method, params)


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
    if not isinstance(raw, Mapping):
        raise RuntimeError("Canonical block must be an object")
    block_hash = hex_bytes(raw["hash"])
    if len(block_hash) != 66:
        raise RuntimeError("Canonical block hash must have 32 bytes")
    return LaunchBlock(
        number=quantity(raw["number"]),
        block_hash=block_hash,
        timestamp=quantity(raw["timestamp"]),
        gas_limit=quantity(raw["gasLimit"]),
        base_fee_per_gas=quantity(raw["baseFeePerGas"]) if raw.get("baseFeePerGas") is not None else None,
    )


def assert_canonical(client: Web3, block: LaunchBlock) -> None:
    if read_block(client, block.number).block_hash != block.block_hash.lower():
        error = LaunchStateChanged("the estimate/read block was reorganized; read and simulate again")
        error.code = "STATE_REORGED"
        raise error


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
        caps = [value for value in (
            self.chain_transaction_gas_limit, self.rpc_transaction_gas_limit,
            self.account_transaction_gas_limit, self.transaction_gas_ceiling,
        ) if value is not None]
        return min(caps + ([block.gas_limit] if self.protocol == "evm" else [(1 << 64) - 1]))

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
    gas_used: int | None
    return_data: str
    logs: tuple[Mapping[str, Any], ...]
    error: str | None = None
    gas_required: int | None = None
    failure_category: Literal["semantic", "capacity", "opaque", "source", "postcondition", "affordability"] | None = None
    decoded_error: str | None = None
    native_error_code: int | None = None
    native_error_kind: Literal["out-of-gas", "execution-reverted", "vm-error", "other", "missing"] | None = None
    native_error_data_bytes: int | None = None
    native_gas_capped: bool = False
    postcondition_data: tuple[str, ...] = ()


@dataclass(frozen=True)
class LaunchRpcSimulation:
    confidence: str
    backend: str | None
    block: LaunchBlock
    calls: tuple[LaunchSimulationCall, ...]
    reason: str | None = None
    expected: int = 0
    failure_category: str | None = None
    native_error_code: int | None = None
    @property
    def complete(self) -> bool:
        return bool(self.calls) and len(self.calls) == self.expected
    @property
    def successful(self) -> bool:
        return self.confidence == "stateful" and self.complete and all(call.success for call in self.calls)


@dataclass(frozen=True)
class ControlledLaunchFork:
    """An explicitly disposable, distinct loopback Anvil or Hardhat fork.

    Only this opt-in instance may reset, impersonate, snapshot and send unsigned
    simulation transactions. No balances, allowances or code are injected.
    """

    client: Web3 | LifecycleRpcClient
    isolated: bool
    source_rpc_url: str
    fork_rpc_url: str | None = None
    expected_chain_id: int | None = None
    expected_block_hash: str | None = None
    impersonation: Literal["anvil", "hardhat", "none"] = "none"
    receipt_timeout_ms: int = 10000
    reset_method: Literal["anvil_reset", "hardhat_reset"] = "anvil_reset"

    def __post_init__(self) -> None:
        if self.isolated is not True:
            raise ValueError("a controlled launch fork must be explicitly isolated")
        endpoint = self.fork_rpc_url or getattr(getattr(self.client, "provider", None), "endpoint_uri", "")
        if not isinstance(endpoint, str) or urlsplit(endpoint).scheme not in {"http", "https"} or urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("controlled fork transaction execution requires a loopback HTTP RPC endpoint")
        if not isinstance(self.source_rpc_url, str):
            raise ValueError("controlled fork refresh requires an explicit HTTP source RPC URL")
        source = urlsplit(self.source_rpc_url)
        if source.scheme not in {"http", "https"} or not source.hostname:
            raise ValueError("controlled fork source must be an HTTP JSON-RPC URL")
        if _same_rpc_endpoint(endpoint, self.source_rpc_url):
            raise ValueError("controlled fork must be separate from the source execution node")
        object.__setattr__(self, "fork_rpc_url", endpoint)
        if self.impersonation not in {"anvil", "hardhat", "none"} or self.reset_method not in {"anvil_reset", "hardhat_reset"}:
            raise ValueError("Unsupported explicit controlled fork backend")
        if isinstance(self.receipt_timeout_ms, bool) or not isinstance(self.receipt_timeout_ms, int) or self.receipt_timeout_ms <= 0:
            raise ValueError("receipt_timeout_ms must be a positive integer")
        if self.expected_chain_id is not None and (isinstance(self.expected_chain_id, bool) or not isinstance(self.expected_chain_id, int) or self.expected_chain_id <= 0):
            raise ValueError("expected_chain_id must be a positive integer")
        if self.expected_block_hash is not None and len(hex_bytes(self.expected_block_hash)) != 66:
            raise ValueError("expected_block_hash must be bytes32")


def _same_rpc_endpoint(first: str, second: str) -> bool:
    def identity(url: str) -> tuple[Any, ...]:
        parts = urlsplit(url)
        host = parts.hostname
        if host in {"127.0.0.1", "localhost", "::1"}:
            host = "loopback"
        return host, parts.port or (443 if parts.scheme == "https" else 80)
    return identity(first) == identity(second)


def create_controlled_launch_fork(
    source: Web3 | LifecycleRpcClient, fork_client: Web3 | LifecycleRpcClient, *, isolated: bool,
    source_rpc_url: str | None = None, fork_rpc_url: str | None = None,
    expected_chain_id: int | None = None, expected_block_hash: str | None = None,
    impersonation: Literal["anvil", "hardhat", "none"] = "none",
    receipt_timeout_ms: int = 10000, reset_method: Literal["anvil_reset", "hardhat_reset"] = "anvil_reset",
) -> ControlledLaunchFork:
    """Configure a separate local fork with automatic exact source refresh."""
    endpoint = source_rpc_url or getattr(getattr(source, "provider", None), "endpoint_uri", None)
    if not isinstance(endpoint, str):
        raise ValueError("controlled fork refresh requires an explicit HTTP source RPC URL")
    if fork_client is source:
        raise ValueError("controlled fork must be separate from the source execution node")
    return ControlledLaunchFork(fork_client, isolated, endpoint, fork_rpc_url, expected_chain_id,
                                expected_block_hash, impersonation, receipt_timeout_ms, reset_method)



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


def execution_failure(return_data: str | None, error: Any) -> dict[str, Any]:
    """Expose reviewed names and safe numeric facts, never native messages/data."""
    native = error if isinstance(error, Mapping) else None
    data = native.get("data") if native is not None else None
    for _ in range(3):
        if not isinstance(data, Mapping):
            break
        data = data.get("data")
    data_bytes = (len(data) - 2) // 2 if isinstance(data, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", data) else None
    code = native.get("code") if native is not None else None
    code = code if isinstance(code, int) and not isinstance(code, bool) and abs(code) <= (1 << 53) - 1 else None
    message = native.get("message", "") if native is not None else ""
    message = message if isinstance(message, str) else ""
    capped = message.endswith(" (gas limit was capped by the RPC server's global gas cap)")
    oog = return_data in (None, "0x") and native is not None and ("data" not in native or data_bytes == 0) and code in (-32015, -32000) and re.match(r"out of gas\b", message, re.I) is not None
    kind = "missing" if native is None else "out-of-gas" if oog else "execution-reverted" if code == 3 else "vm-error" if code == -32015 else "other"
    facts = dict(native_error_code=code, native_error_kind=kind, native_error_data_bytes=data_bytes, native_gas_capped=capped)
    candidate = data if return_data in (None, "0x") else return_data
    if isinstance(candidate, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2}){4,}", candidate):
        from .lifecycle_abis import FIXED_FEE_POOL_HOOK_V1_ABI
        names = {entry["name"] for entry in FIXED_FEE_POOL_HOOK_V1_ABI if entry["type"] == "error"} | {
            "WrongDomain", "PlanMismatch", "LaunchAlreadyExists", "InvalidMode", "InvalidPhase",
            "InvalidPreparationOrder", "DeadlineExpired", "InvalidBinding", "InvalidMarket", "InsufficientEscrow",
            "InvalidPositions", "InvalidPlan", "InvalidFunding", "InvalidFeePolicy", "InvalidBuy", "DuplicateMarket",
            "IneligibleImplementation", "QuoteDebtForbidden", "SlippageExceeded", "FeeBelowMinimum", "DeploymentFailed",
        }
        selector = bytes.fromhex(candidate[2:10])
        for name in names:
            if selector == keccak(text=f"{name}()")[:4] and len(candidate) == 10:
                return {**facts, "failure_category": "opaque" if name == "DeploymentFailed" else "semantic",
                        "decoded_error": name, "error": f"Execution reverted ({name})"}
        from eth_abi import decode
        for name, signature, types in (("Error", "Error(string)", ["string"]), ("Panic", "Panic(uint256)", ["uint256"])):
            if selector == keccak(text=signature)[:4]:
                try:
                    decode(types, bytes.fromhex(candidate[10:]))
                    return {**facts, "failure_category": "semantic", "decoded_error": name, "error": f"Execution reverted ({name})"}
                except Exception:
                    break
        return {**facts, "failure_category": "opaque", "error": "Execution reverted without a reviewed error"}
    if oog:
        return {**facts, "failure_category": "source" if capped else "capacity",
                "error": "Native execution ran out of gas under the reported RPC gas cap" if capped else "Native execution ran out of gas"}
    return {**facts, "failure_category": "opaque",
            "error": "Native execution reverted without a reviewed error" if kind == "execution-reverted" else "Execution failed without a reviewed error"}


def _simulation_failure_kind(failure: Any) -> Literal["abort", "out-of-gas", "execution", "source"]:
    """Classify bounded native causes without exposing their messages or data."""
    for _ in range(8):
        if failure is None:
            break
        native = failure._native if isinstance(failure, LaunchRpcError) else failure
        field = native.get if isinstance(native, Mapping) else lambda key: getattr(native, key, None)
        if field("name") == "AbortError" or isinstance(failure, asyncio.CancelledError):
            return "abort"
        status = field("status")
        if status is None:
            status = field("statusCode")
        if status is None:
            status = field("status_code")
        if isinstance(status, (int, float)) and not isinstance(status, bool) and 400 <= status <= 599:
            return "source"
        details, message = field("details"), field("message")
        message = details if isinstance(details, str) else message if isinstance(message, str) else ""
        diagnostic = {"code": field("code"), "message": message}
        if isinstance(native, Mapping):
            if "data" in native:
                diagnostic["data"] = native["data"]
        elif hasattr(native, "data"):
            diagnostic["data"] = native.data
        execution = execution_failure(None, diagnostic)
        if execution["native_error_kind"] == "out-of-gas" and execution["native_gas_capped"]:
            return "source"
        if execution["failure_category"] == "capacity":
            return "out-of-gas"
        code = execution["native_error_code"]
        if code == 3 or code == -32000 and re.match(r"execution (?:failed|reverted)\b", message, re.I):
            return "execution"
        cause = field("cause")
        failure = cause if cause is not None else getattr(failure, "__cause__", None)
    return "source"


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
                "blockOverrides": {"number": hex(block.number + index + 1), "time": hex(block.timestamp + index + 1), "gasLimit": hex(block.gas_limit)},
                "calls": [{key: value for key, value in rpc_transaction(transaction).items() if key in {"from", "to", "data", "value", "gas", "gasPrice"}}],
            }
            for index, transaction in enumerate(transactions)
        ],
        "validation": validation,
        "traceTransfers": False,
        "returnFullTransactions": False,
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
        kind = _simulation_failure_kind(error)
        if kind == "abort":
            raise
        category = "capacity" if kind == "out-of-gas" else "opaque" if kind == "execution" else "source"
        return LaunchRpcSimulation("provisional", None, block, (), failure_reason(error), len(transactions), category, error.code)
    if not isinstance(raw, list) or len(raw) != len(transactions):
        raise RuntimeError("eth_simulateV1 returned an incomplete block sequence")
    calls: list[LaunchSimulationCall] = []
    for transaction, simulated_block in zip(transactions, raw):
        if not isinstance(simulated_block, Mapping):
            raise RuntimeError("eth_simulateV1 returned a malformed simulated block")
        results = simulated_block.get("calls")
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("eth_simulateV1 did not return one result per fresh transaction")
        result = results[0]
        if not isinstance(result, Mapping):
            raise RuntimeError("eth_simulateV1 returned a malformed simulated call")
        status = quantity(result["status"])
        if status not in (0, 1):
            raise RuntimeError("eth_simulateV1 returned an invalid call status")
        gas_used = quantity(result["gasUsed"])
        requested_gas = quantity(transaction["gas"])
        gas_required = quantity(result["maxUsedGas"]) if result.get("maxUsedGas") is not None else gas_used
        if not gas_used <= gas_required <= requested_gas:
            raise RuntimeError("eth_simulateV1 returned gas consumption outside the exact requested envelope")
        returned = hex_bytes(result["returnData"])
        facts = {} if status == 1 else execution_failure(returned, result.get("error"))
        logs = result.get("logs")
        calls.append(LaunchSimulationCall(success=status == 1, gas_used=gas_used,
            return_data=returned, logs=tuple(logs) if isinstance(logs, list) else (), gas_required=gas_required, **facts))
        if status != 1:
            break
    return LaunchRpcSimulation("stateful", "eth_simulateV1", block, tuple(calls), None, len(transactions))


def _simulate_fork(
    source: Web3 | LifecycleRpcClient, fork: ControlledLaunchFork,
    transactions: Sequence[Mapping[str, Any]], block: LaunchBlock, validation: bool,
    limits: LaunchExecutionLimits, postconditions: Sequence[Sequence[Mapping[str, str]]] | None,
    postcondition_validator: Callable[[int, LaunchSimulationCall], None] | None,
) -> LaunchRpcSimulation:
    client = fork.client
    original = source.source if isinstance(source, _LifecycleReadClient) else source
    source_endpoint = getattr(getattr(original, "provider", None), "endpoint_uri", None) or fork.source_rpc_url
    fork_endpoint = fork.fork_rpc_url or getattr(getattr(client, "provider", None), "endpoint_uri", "")
    visible_endpoint = getattr(getattr(client, "provider", None), "endpoint_uri", None)
    if visible_endpoint is not None and not _same_rpc_endpoint(visible_endpoint, fork_endpoint):
        raise ValueError("Controlled fork transport differs from its configured loopback RPC")
    if client is original or _same_rpc_endpoint(source_endpoint, fork_endpoint):
        raise ValueError("Controlled fork must be separate from the simulation source")
    source_chain = quantity(rpc(source, "eth_chainId", []))
    head = read_block(client)
    if head.number != block.number or head.block_hash != block.block_hash:
        if block.number > (1 << 53) - 1:
            raise ValueError("Pinned block is not exactly representable by the fork reset API")
        rpc(client, fork.reset_method, [{"forking": {"jsonRpcUrl": fork.source_rpc_url, "blockNumber": block.number}}])
        head = read_block(client)
    if quantity(rpc(client, "eth_chainId", [])) != source_chain or (fork.expected_chain_id is not None and source_chain != fork.expected_chain_id) or head.number != block.number or head.block_hash != block.block_hash or (fork.expected_block_hash is not None and head.block_hash != fork.expected_block_hash.lower()):
        raise LaunchStateChanged("Controlled fork differs from the exact source chain and pinned block")
    creators = tuple(dict.fromkeys(transaction["from"] for transaction in transactions))
    nonces = {creator: quantity(rpc(source, "eth_getTransactionCount", [creator, block.tag])) for creator in creators}
    if any(quantity(rpc(client, "eth_getTransactionCount", [creator, head.tag])) != nonce for creator, nonce in nonces.items()):
        raise LaunchStateChanged("Controlled fork does not have the exact source creator nonce")
    snapshot = rpc(client, "evm_snapshot", [])
    calls: list[LaunchSimulationCall] = []
    impersonated: list[str] = []
    try:
        if fork.impersonation != "none":
            for creator in creators:
                rpc(client, f"{fork.impersonation}_impersonateAccount", [creator])
                impersonated.append(creator)
        for index, transaction in enumerate(transactions):
            request = {key: value for key, value in rpc_transaction(transaction).items() if key in {"from", "to", "data", "value", "gas", "gasPrice"}}
            estimate = None
            try:
                if not validation:
                    estimate_request = {key: value for key, value in request.items() if key != "gas"}
                    estimate = quantity(rpc(client, "eth_estimateGas", [estimate_request]))
                    request["gas"] = hex(min(limits.buffered_gas(estimate), quantity(request["gas"])))
                transaction_hash = rpc(client, "eth_sendTransaction", [request])
                deadline = time.monotonic() + fork.receipt_timeout_ms / 1000
                receipt = None
                while time.monotonic() <= deadline:
                    receipt = rpc(client, "eth_getTransactionReceipt", [transaction_hash])
                    if receipt is not None:
                        break
                    time.sleep(0.025)
                if receipt is None:
                    raise RuntimeError("Controlled fork receipt timed out")
                success = quantity(receipt["status"]) == 1
                current = read_block(client)
                observed = tuple(hex_bytes(rpc(client, "eth_call", [{**condition, "from": transaction["from"]}, current.tag]))
                                 for condition in (postconditions[index] if postconditions is not None else ())) if success else ()
                call = LaunchSimulationCall(success, quantity(receipt["gasUsed"]), "0x", tuple(receipt.get("logs", ())),
                    None if success else "Actual controlled fork transaction reverted", estimate,
                    None if success else "opaque", postcondition_data=observed)
                if success and postcondition_validator is not None:
                    try:
                        postcondition_validator(index, call)
                    except LaunchStateChanged:
                        raise
                    except Exception:
                        from dataclasses import replace
                        call = replace(call, success=False, error="Actual controlled execution failed its committed postcondition", failure_category="postcondition")
                calls.append(call)
                if not call.success:
                    break
            except LaunchStateChanged:
                raise
            except Exception as error:
                from .lifecycle import LifecyclePlanningError
                if isinstance(error, LifecyclePlanningError) and error.code in {"READ_SCOPE_CLOSED", "LIMIT_CONTEXT_MISMATCH", "PLAN_MUTATED", "INVALID_SIMULATION"}:
                    raise
                if _simulation_failure_kind(error) == "abort":
                    raise
                calls.append(LaunchSimulationCall(False, None, "0x", (), failure_reason(error), failure_category="opaque"))
                break
        return LaunchRpcSimulation("stateful", "controlled-fork", block, tuple(calls), expected=len(transactions))
    finally:
        try:
            for creator in impersonated:
                rpc(client, f"{fork.impersonation}_stopImpersonatingAccount", [creator])
        finally:
            if rpc(client, "evm_revert", [snapshot]) is not True:
                raise RuntimeError("Controlled fork snapshot cleanup failed; no simulation may be admitted")


def simulate_transactions(
    client: Web3,
    transactions: Sequence[Mapping[str, Any]],
    *,
    block: LaunchBlock,
    limits: LaunchExecutionLimits,
    fork: ControlledLaunchFork | None = None,
    validation: bool = True,
    postconditions: Sequence[Sequence[Mapping[str, str]]] | None = None,
    postcondition_validator: Callable[[int, LaunchSimulationCall], None] | None = None,
    force_owned_fork: bool = False,
) -> LaunchRpcSimulation:
    """Execute exact ordered transactions without persisting source state."""
    from .lifecycle import LifecyclePlanningError
    assert_canonical(client, block)
    source_chain = quantity(rpc(client, "eth_chainId", []))
    try:
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
        if force_owned_fork:
            if fork is None or limits.protocol == "nitro":
                raise ValueError("Owned replay requires its exact disposable generic-EVM backend")
            return _simulate_fork(client, fork, transactions, block, validation, limits, postconditions, postcondition_validator)
        try:
            result = _simulate_rpc(client, transactions, block, limits, validation)
        except (LaunchStateChanged, LifecyclePlanningError):
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            if _simulation_failure_kind(error) == "abort":
                raise
            result = LaunchRpcSimulation("provisional", None, block, (), "Stateful sequential RPC returned invalid bounded execution results", len(transactions), "source")
        # Only initial generic-EVM measurement may select an explicitly supplied
        # fork after request/structural failure. Returned execution refusals and
        # validated replay cannot switch their backend.
        if not validation and result.confidence != "stateful" and fork is not None and limits.protocol != "nitro":
            return _simulate_fork(client, fork, transactions, block, validation, limits, postconditions, postcondition_validator)
        return result
    finally:
        assert_canonical(client, block)
        assert_chain(client, source_chain)


__all__ = [
    "ControlledLaunchFork", "LaunchBlock", "LaunchExecutionLimits", "LaunchRpcError",
    "LaunchRpcSimulation", "LaunchSimulationCall", "LaunchStateChanged",
    "LaunchLimitContext",
    "create_controlled_launch_fork",
]
