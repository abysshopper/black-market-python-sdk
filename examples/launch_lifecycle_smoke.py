"""Exercise real lifecycle fixtures on an explicitly isolated local Anvil.

Usage: LAUNCH_LIFECYCLE_RPC_URL=http://127.0.0.1:8545 \
    python examples/launch_lifecycle_smoke.py /absolute/manifest.json

The manifest and plan rows are generated from the actual new local deployment.
No keys, default addresses, production broadcasts or fabricated pools are used.
Simulation snapshots and recovery branches are reverted; admitted final fixture
transactions are real local transactions retained for the integration evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields, replace
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web3 import Web3
from eth_abi import decode as abi_decode, encode as abi_encode

from black_market_sdk import (
    ERC20_ABI,
    LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI,
    LAUNCH_LIFECYCLE_V1_ABI,
    ControlledLaunchFork,
    LaunchExecutionLimits,
    LaunchStateChanged,
    LaunchLimitContext,
    LifecycleLimitResolver,
    LifecyclePhase,
    build_next_transaction,
    encode_launch_plan,
    create_controlled_launch_fork,
    hash_launch_plan,
    get_tick_at_sqrt_ratio,
    launch_id_of,
    launch_plan_from_dict,
    plan_launch,
    predict_launch_token,
    preview_lifecycle_fees,
    read_launch_markets,
    read_launch_progress,
    simulate_launch_plan,
)
from black_market_sdk.lifecycle_rpc import LaunchRpcSimulation, LaunchSimulationCall


def request(client: Web3, method: str, params: list):
    response = client.provider.make_request(method, params)
    if response.get("error") is not None:
        raise RuntimeError(f"{method}: {response['error']}")
    return response["result"]


def observe_market_oracle(client: Web3, root: str, identity: dict, seconds_ago: int, block_number: int):
    if identity["venue"] == 0:
        signature = "observeTruncated(bytes32,uint32[])"
        data = Web3.keccak(text=signature)[:4] + abi_encode(
            ["bytes32", "uint32[]"], [bytes.fromhex(identity["poolId"][2:]), [seconds_ago]]
        )
        address = root
    else:
        signature = "observeTruncated(uint32[])"
        data = Web3.keccak(text=signature)[:4] + abi_encode(["uint32[]"], [[seconds_ago]])
        address = identity["pool"]
    return client.provider.make_request(
        "eth_call", [{"to": address, "data": Web3.to_hex(data)}, hex(block_number)]
    )


def load_rows(manifest: dict, manifest_path: Path) -> list[dict]:
    fixture = manifest.get("fixtures", {})
    path = fixture.get("plansFile") if isinstance(fixture, dict) else None
    if not path:
        raise ValueError("the actual deployment manifest must link fixtures.plansFile")
    plans_path = Path(path)
    if not plans_path.is_absolute():
        plans_path = manifest_path.parent / plans_path
    payload = json.loads(plans_path.read_text())
    rows = payload if isinstance(payload, list) else payload.get("plans", payload.get("fixtures", []))
    if isinstance(payload, dict) and payload.get("admittedPython"):
        rows = [*rows, payload["admittedPython"]]
    if not rows:
        raise ValueError("the deployment fixture has no complete economic plans")
    return rows


def execution_limits(manifest: dict, row: dict) -> LifecycleLimitResolver:
    aliases = {
        "headroomBps": "headroom_bps",
        "chainTransactionGasLimit": "chain_transaction_gas_limit",
        "rpcTransactionGasLimit": "rpc_transaction_gas_limit",
        "accountTransactionGasLimit": "account_transaction_gas_limit",
        "maxCalldataBytes": "max_calldata_bytes",
        "accountMaxCalldataBytes": "account_max_calldata_bytes",
        "rpcMaxRequestBytes": "rpc_max_request_bytes",
        "rpcTotalSimulationGasLimit": "rpc_total_simulation_gas_limit",
    }
    def resolve(client: Web3, context: LaunchLimitContext) -> LaunchExecutionLimits:
        raw = {**manifest.get("executionLimits", {}), **row.get("limits", {})}
        names = {field.name for field in fields(LaunchExecutionLimits)}
        values = {}
        for key, value in raw.items():
            name = aliases.get(key, key)
            if name not in names:
                if key in row.get("limits", {}):
                    raise ValueError(f"unknown execution limit {key}")
                continue  # Deployment metrics are not admission configuration.
            values[name] = value if name in {"source", "observed_block_hash", "account", "orchestrator"} else int(value)
        values.update(observed_block_number=context.block.number, observed_block_hash=context.block.block_hash, chain_id=context.chain_id, account=context.account, orchestrator=context.orchestrator, source="owned local Anvil manifest")
        return LaunchExecutionLimits(**values)
    return resolve


def send(client: Web3, transaction: dict) -> dict:
    transaction_hash = client.eth.send_transaction(transaction)
    receipt = dict(client.eth.wait_for_transaction_receipt(transaction_hash, timeout=180))
    if receipt["status"] != 1:
        raise AssertionError(f"actual SDK transaction reverted: {Web3.to_hex(transaction_hash)}")
    return receipt


def read_balance(client: Web3, asset: str, creator: str) -> int:
    return client.eth.contract(address=Web3.to_checksum_address(asset), abi=ERC20_ABI).functions.balanceOf(creator).call()

INTENTIONAL_REFUSALS = {"gas-cap-refusal"}


def refusal_outcome(launch) -> str:
    """Fail closed unless the actual indivisible step has numeric cap evidence."""
    simulation = launch.simulation
    reason_text = "; ".join(simulation.reasons) or "unknown refusal"
    evidence = simulation.evidence
    cap = launch.limits.gas_cap(simulation.block)
    if evidence is not None and evidence.confidence == "stateful":
        for index, (transaction, call) in enumerate(zip(simulation.transactions, evidence.calls)):
            if not all(previous.success for previous in evidence.calls[:index]):
                break
            if transaction.kind not in {"activate", "atomic"}:
                continue
            exhausted_trace = evidence.backend == "controlled-fork-ceiling-receipts" and call.error is not None and call.error.get("outOfGas") is True
            if not call.success and transaction.gas_limit == cap and (call.gas_used >= cap - cap // 64 or exhausted_trace):
                return "gas-cap-refusal"
            required = call.gas_required if call.gas_required is not None else call.gas_used
            if call.success and launch.limits.buffered_gas(required) > cap and any(
                reason.startswith("a measured transaction does not fit current limits with conservative headroom")
                for reason in simulation.reasons
            ):
                return "gas-cap-refusal"
    return f"failure:{reason_text}"


def refusal_evidence(client: Web3, launch, fork: ControlledLaunchFork):
    """Obtain receipt/terminal-trace evidence on the already-owned separate fork."""
    if refusal_outcome(launch) == "gas-cap-refusal":
        return launch
    block = launch.simulation.block
    cap = launch.limits.gas_cap(block)
    transactions = tuple(replace(transaction, gas_limit=cap) for transaction in launch.simulation.transactions)
    if request(client, "eth_getBlockByNumber", [block.tag, False])["hash"].lower() != block.block_hash:
        raise AssertionError("refusal proof source must remain canonical")

    def reset() -> None:
        request(fork.client, "anvil_reset", [{"forking": {"jsonRpcUrl": fork.source_rpc_url, "blockNumber": block.number}}])
        head = request(fork.client, "eth_getBlockByNumber", ["latest", False])
        if head["hash"].lower() != block.block_hash or int(request(fork.client, "eth_chainId", []), 16) != launch.chain_id:
            raise AssertionError("refusal proof must restore the exact pinned fork chain/head")
        if request(fork.client, "eth_getTransactionCount", [launch.account, "latest"]) != request(client, "eth_getTransactionCount", [launch.account, block.tag]):
            raise AssertionError("refusal proof must restore the exact pinned creator nonce")

    reset()
    calls = []
    try:
        for transaction in transactions:
            transaction_hash = fork.client.eth.send_transaction(transaction.as_transaction())
            receipt = fork.client.eth.wait_for_transaction_receipt(transaction_hash, timeout=180)
            success = receipt["status"] == 1
            failure_trace = []
            if not success:
                frame = request(fork.client, "debug_traceTransaction", [Web3.to_hex(transaction_hash), {"tracer": "callTracer"}])
                # Trace only the terminal failing chain, not a recovered earlier OOG.
                while frame and frame.get("error"):
                    failure_trace.append(frame["error"])
                    frame = frame.get("calls", [None])[-1]
            error = None if success else {
                "outOfGas": any("out of gas" in error.lower() or "outofgas" in error.lower() for error in failure_trace),
                "failureTrace": failure_trace, "transactionHash": Web3.to_hex(transaction_hash),
            }
            calls.append(LaunchSimulationCall(success, int(receipt["gasUsed"]), "0x", (), error))
            if not success:
                break
    finally:
        reset()
        if request(client, "eth_getBlockByNumber", [block.tag, False])["hash"].lower() != block.block_hash:
            raise AssertionError("refusal proof must not outlive its canonical source block")
    proof = LaunchRpcSimulation("stateful", "controlled-fork-ceiling-receipts", block, tuple(calls), None, len(transactions))
    reasons = ("Exact ceiling receipt failure" if any(not call.success for call in proof.calls)
        else "Exact ceiling sequence succeeded; original refusal is not proven gas exhaustion",)
    return replace(launch, simulation=replace(launch.simulation, evidence=proof, backend=proof.backend,
        confidence=proof.confidence, transactions=transactions, reasons=reasons))



def recovery_branches(client: Web3, launch, limits: LifecycleLimitResolver, batch_size: int, fork: ControlledLaunchFork, root_admin: str) -> dict:
    """Prove interruption/confirmation/reorg/revocation without local counters."""
    plan, account = launch.plan, launch.account
    before = read_launch_progress(client, plan)
    if before.phase != LifecyclePhase.PREPARING:
        return {}
    kwargs = {"account": account, "mode": "staged", "limits": limits, "prepare_batch_size": batch_size, "fork": fork}
    next_step = build_next_transaction(client, plan, **kwargs)
    if next_step is None or next_step.kind != "prepare":
        raise AssertionError("pending canonical preparation did not yield its next contiguous step")
    snapshot = request(client, "evm_snapshot", [])
    try:
        receipt = send(client, next_step.as_transaction())
        transaction_hash = Web3.to_hex(receipt["transactionHash"])
        mined = read_launch_progress(client, plan, transaction_hashes=[transaction_hash])
        if mined.prepared_markets != before.prepared_markets + next_step.market_count:
            raise AssertionError("actual prepared progress differs from the SDK command")
        following = build_next_transaction(client, plan, transaction_hashes=[transaction_hash], **kwargs)
        if following is not None and following.kind == "prepare" and following.first_market != mined.prepared_markets:
            raise AssertionError("reload duplicated a confirmed preparation step")
        try:
            build_next_transaction(client, plan, confirmations=2, transaction_hashes=[transaction_hash], **kwargs)
        except LaunchStateChanged:
            pass
        else:
            raise AssertionError("a mined unconfirmed preparation was incorrectly offered again")
    finally:
        if request(client, "evm_revert", [snapshot]) is not True:
            raise AssertionError("recovery branch snapshot failed to revert")
    request(client, "evm_mine", [])
    recovered = read_launch_progress(client, plan, transaction_hashes=[transaction_hash])
    if recovered.prepared_markets != before.prepared_markets or recovered.receipts[0].status not in {"reorged", "pending-or-replaced"}:
        raise AssertionError("reorg recovery retained an orphaned local preparation counter")
    replacement_block = client.eth.get_block(receipt["blockNumber"])
    if bytes(replacement_block["hash"]) == bytes(receipt["blockHash"]):
        raise AssertionError("removed preparation receipt still belongs to the canonical block")
    # A missing receipt is correctly ambiguous to the SDK. Acknowledge this independently
    # proven orphan before regenerating work; never relax the pending-receipt safety gate.
    replay = build_next_transaction(client, plan, **kwargs)
    if replay is None or replay.kind != "prepare" or replay.first_market != before.prepared_markets:
        raise AssertionError("reorg recovery did not return the exact uncompleted canonical step")

    # Revoke in a disposable actual branch, not by overriding code/storage in a
    # successful simulation. Pending cancellation must bypass disabled adapters.
    core = client.eth.contract(address=plan.orchestrator, abi=LAUNCH_LIFECYCLE_V1_ABI)
    registry = client.eth.contract(address=core.functions.registry().call(), abi=LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI)
    if registry.functions.admin().call().lower() != root_admin.lower():
        raise AssertionError("registry authority differs from the actual manifest root admin")
    admin_abi = [
        {"type": "function", "name": "owner", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
        {"type": "function", "name": "execute", "stateMutability": "payable", "inputs": [{"name": "calls", "type": "tuple[]", "components": [{"name": "target", "type": "address"}, {"name": "value", "type": "uint256"}, {"name": "data", "type": "bytes"}]}], "outputs": [{"name": "results", "type": "bytes[]"}]},
    ]
    admin = client.eth.contract(address=Web3.to_checksum_address(root_admin), abi=admin_abi)
    if admin.functions.owner().call().lower() != account.lower():
        raise AssertionError("the local fixture account does not own the actual root admin")
    snapshot = request(client, "evm_snapshot", [])
    try:
        calldata = registry.functions.disableAdapter(plan.markets[0].adapter_id)._encode_transaction_data()
        send(client, admin.functions.execute([(registry.address, 0, calldata)]).build_transaction({"from": account}))
        try:
            build_next_transaction(client, plan, **kwargs)
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError("retired pending adapter was silently admitted")
        reserved = {item.asset: core.functions.escrowBalance(bytes.fromhex(launch_id_of(plan)[2:]), item.asset).call() for item in plan.funding}
        balances = {asset: read_balance(client, asset, account) for asset in reserved}
        cancellation = build_next_transaction(client, plan, action="cancel", **kwargs)
        cancelled_receipt = send(client, cancellation.as_transaction())
        cancelled = read_launch_progress(client, plan, transaction_hashes=[Web3.to_hex(cancelled_receipt["transactionHash"])])
        if cancelled.phase != LifecyclePhase.CANCELLED:
            raise AssertionError("actual cancellation did not reach terminal inactive state")
        for asset, amount in reserved.items():
            if core.functions.escrowBalance(bytes.fromhex(launch_id_of(plan)[2:]), asset).call() != 0 or read_balance(client, asset, account) - balances[asset] != amount:
                raise AssertionError("cancellation refund was not the exact launch-isolated external escrow")
    finally:
        if request(client, "evm_revert", [snapshot]) is not True:
            raise AssertionError("revocation branch snapshot failed to revert")
    return {"orphanedTransaction": transaction_hash, "orphanedBlockHash": Web3.to_hex(receipt["blockHash"]), "canonicalReplacementBlockHash": Web3.to_hex(replacement_block["hash"]), "orphanedReceiptAcknowledged": True, "recoveredPreparedMarkets": recovered.prepared_markets, "cancelAfterRevocation": True}


def run(manifest_path: Path) -> dict:
    if os.environ.get("LAUNCH_LIFECYCLE_ALLOW_LOCAL_EXECUTION") != "1":
        raise ValueError("local transaction smoke requires LAUNCH_LIFECYCLE_ALLOW_LOCAL_EXECUTION=1")
    endpoint = os.environ["LAUNCH_LIFECYCLE_RPC_URL"]
    if urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("this transaction smoke is restricted to an explicitly local Anvil")
    client = Web3(Web3.HTTPProvider(endpoint, request_kwargs={"timeout": 180}))
    request(client, "anvil_nodeInfo", [])
    manifest = json.loads(manifest_path.read_text())
    if client.eth.chain_id != int(manifest["chainId"]) or manifest.get("stackVersion") != "launch-lifecycle-v1":
        raise ValueError("the actual RPC does not match the explicit lifecycle manifest")
    account = Web3.to_checksum_address(manifest["defaultAccount"])
    if account.lower() not in {address.lower() for address in client.eth.accounts}:
        raise ValueError("fixture creator is not already unlocked on this isolated local node")
    fork_client = Web3(Web3.HTTPProvider(os.environ["LAUNCH_LIFECYCLE_FORK_RPC_URL"], request_kwargs={"timeout": 180}))
    fork = create_controlled_launch_fork(client, fork_client, isolated=True)
    rows = load_rows(manifest, manifest_path)


    observations = []
    coverage = set()
    venue_coverage = set()
    recovery_observed = False
    for row in rows:
        plan = launch_plan_from_dict(row["plan"])
        if plan.creator.lower() != account.lower() or plan.orchestrator.lower() != manifest["addresses"]["orchestrator"].lower():
            raise ValueError("fixture identity differs from its real deployment manifest")
        if row.get("encodedPlan") and Web3.to_hex(encode_launch_plan(plan)) != row["encodedPlan"].lower():
            raise AssertionError("Python tuple encoding differs from Solidity abi.encode(plan)")
        if row.get("planHash") and hash_launch_plan(plan) != row["planHash"].lower():
            raise AssertionError("Python economic hash differs from deployed Solidity fixture")
        if row.get("launchId") and launch_id_of(plan) != row["launchId"].lower():
            raise AssertionError("Python domain identity differs from deployed Solidity fixture")
        if row.get("predictedToken") and predict_launch_token(client, plan).lower() != row["predictedToken"].lower():
            raise AssertionError("Python deterministic prediction differs from Solidity fixture")
        limits = execution_limits(manifest, row)
        batch_size = int(row.get("prepareBatchSize", row.get("batchSize", 1)))
        launch = plan_launch(client, plan, account=account, mode=row["mode"], limits=limits, prepare_batch_size=batch_size, fork=fork)
        expected_atomic = row.get("expectedAdmitted")
        expected_staged = row.get("expectedStagedAdmitted")
        if expected_atomic is None and expected_staged is None:
            raise AssertionError(f"fixture {row.get('name')} lacks an explicit exporter expectation")
        observation = {"name": row.get("name", str(plan.nonce)), "mode": launch.mode, "tokenKind": int(plan.token.kind), "planHash": launch.plan_hash, "launchId": launch.launch_id, "predictedToken": launch.predicted_token, "admitted": launch.admitted, "confidence": launch.confidence, "backend": launch.simulation.backend, "blockNumber": launch.simulation.block.number, "blockHash": launch.simulation.block.block_hash, "atomicAdmitted": launch.atomic_simulation.admitted if launch.atomic_simulation else None, "expectedAdmitted": expected_atomic, "expectedStagedAdmitted": expected_staged}
        if not launch.admitted:
            evidence_launch = refusal_evidence(client, launch, fork)
            outcome = refusal_outcome(evidence_launch)
            observation["reasons"] = launch.simulation.reasons
            observation["outcome"] = outcome
            evidence = evidence_launch.simulation.evidence
            observation["gasEvidence"] = {"backend": evidence.backend, "blockNumber": evidence.block.number, "blockHash": evidence.block.block_hash, "calls": [
                {"kind": transaction.kind, "success": call.success, "gasUsed": call.gas_used, "gasLimit": transaction.gas_limit, "gasRequired": call.gas_required, "error": call.error}
                for transaction, call in zip(evidence_launch.simulation.transactions, evidence.calls)
            ]}
            observations.append(observation)
            if expected_atomic is True:
                raise AssertionError(f"required fixture unsupported: {observation}")
            expected_outcome = row.get("expectedOutcome") if row["mode"] == "atomic" else row.get("expectedStagedOutcome")
            if expected_outcome != outcome:
                raise AssertionError(f"refusal must be the exporter's expected intentional outcome, got {outcome!r} for {observation['name']}; {observation['gasEvidence']}")
            if outcome not in INTENTIONAL_REFUSALS:
                raise AssertionError(f"refusal is not an intentional known gas-cap refusal: {observation}")
            continue
        # This is a second real invocation of the public simulation API, not a
        # wrapper returning the stored launch's old confidence/counters.
        resimulation = simulate_launch_plan(client, launch, account=account, fork=fork)
        if not resimulation.admitted or resimulation.block.block_hash != launch.simulation.block.block_hash:
            raise AssertionError("public simulation API did not prove this actual canonical sequence")
        receipts = []
        activation_receipt = None
        transaction_hashes = []
        recovery = {}
        while True:
            step = build_next_transaction(client, launch, account=account, transaction_hashes=transaction_hashes, fork=fork)
            if step is None:
                break
            receipt = send(client, step.as_transaction())
            transaction_hash = Web3.to_hex(receipt["transactionHash"])
            receipts.append({"kind": step.kind, "transactionHash": transaction_hash, "blockHash": Web3.to_hex(receipt["blockHash"]), "gasUsed": receipt["gasUsed"], "gasLimit": step.gas_limit})
            transaction_hashes.append(transaction_hash)
            if step.kind in {"atomic", "activate"}:
                activation_receipt = receipt
            if step.kind.startswith("approval"):
                try:
                    build_next_transaction(client, launch, account=account, transaction_hashes=transaction_hashes, confirmations=2, fork=fork)
                except LaunchStateChanged:
                    pass
                else:
                    raise AssertionError("an unconfirmed funding approval unlocked its dependent launch")
            if step.kind == "begin" and not recovery_observed:
                recovery = recovery_branches(client, launch, limits, batch_size, fork, manifest["addresses"]["admin"])
                recovery_observed = bool(recovery)
        progress = read_launch_progress(client, plan, transaction_hashes=transaction_hashes)
        if progress.phase != LifecyclePhase.ACTIVE or progress.token.lower() != launch.predicted_token.lower() or progress.prepared_markets != len(plan.markets):
            raise AssertionError("actual SDK sequence did not activate the full deterministic launch")
        if any(receipt.status != "confirmed" for receipt in progress.receipts):
            raise AssertionError("actual retained SDK receipts are not canonical confirmed commands")
        markets = read_launch_markets(client, plan)
        if len(markets) != len(plan.markets) or sum(len(market["positions"]) for market in markets) != progress.position_count:
            raise AssertionError("canonical discovery omitted an activated market or position")
        if activation_receipt is None:
            raise AssertionError("full activation did not retain its actual atomic/staged receipt")
        oracle_block = client.eth.get_block(activation_receipt["blockHash"])
        oracle_history = []
        for market in markets:
            venue_coverage.add(market["identity"]["venue"])
            if not market["live"]["publicTrading"]:
                raise AssertionError("public market readiness did not start at activation")
            # Both fixture venues disclose actual initialization time, not oracle maturity.
            if not 0 < market["live"]["oracleReadyAt"] <= oracle_block["timestamp"]:
                raise AssertionError("real pool-local oracle genesis is missing or lies in the future")
            elapsed = oracle_block["timestamp"] - market["live"]["oracleReadyAt"]
            identity = market["identity"]
            opening_tick = get_tick_at_sqrt_ratio(int(identity["openingSqrtPriceX96"]))
            normalized_tick = opening_tick if identity["currency0"].lower() == progress.token.lower() else -opening_tick
            current = observe_market_oracle(client, manifest["addresses"]["v4Hook"], identity, 0, activation_receipt["blockNumber"])
            if current.get("error") is not None:
                raise AssertionError(f"real pool-local oracle read failed: {current['error']}")
            ticks, seconds_per_liquidity = abi_decode(["int56[]", "uint160[]"], bytes.fromhex(current["result"][2:]))
            if ticks[0] != normalized_tick * elapsed or seconds_per_liquidity[0] != elapsed << 128:
                raise AssertionError("actual oracle omitted quote-normalized empty history through full activation")
            older = observe_market_oracle(client, manifest["addresses"]["v4Hook"], identity, elapsed + 1, activation_receipt["blockNumber"])
            if older.get("error", {}).get("data") != Web3.to_hex(Web3.keccak(text="ObservationTooOld()")[:4]):
                raise AssertionError(f"oracle fabricated history before its actual genesis: {older}")
            oracle_history.append({"marketIndex": market["marketIndex"], "venue": identity["venue"], "initializedAt": market["live"]["oracleReadyAt"], "blockHash": Web3.to_hex(activation_receipt["blockHash"]), "tickCumulative": str(ticks[0]), "secondsPerLiquidityCumulativeX128": str(seconds_per_liquidity[0]), "beforeGenesisRejected": True})
            for position in market["positions"]:
                if position["liveLiquidity"] != int(position["liquidity"]) or position["liveOwner"].lower() != market["custody"].lower():
                    raise AssertionError("live canonical position is not in its permanent custody")
        payments = preview_lifecycle_fees(client, progress.fee_hub, executor=account)
        if [payment["asset"].lower() for payment in payments] != [policy.asset.lower() for policy in plan.fee_assets]:
            raise AssertionError("claimAndSplit preview did not preserve all canonical fee assets including zeros")
        if build_next_transaction(client, launch, account=account, fork=fork) is not None:
            raise AssertionError("terminal active launch yielded another economic command")
        coverage.add((launch.mode, int(plan.token.kind)))
        observation.update({"outcome": "active", "transactions": receipts, "phase": progress.phase.name, "marketCount": len(markets), "positionCount": progress.position_count, "feePreview": payments, "recovery": recovery, "oracleHistory": oracle_history})
        observations.append(observation)
    required = {("atomic", 0), ("staged", 0), ("staged", 1)}
    refused_atomic_404 = any(item["mode"] == "atomic" and item["tokenKind"] == 1 and not item["admitted"] for item in observations)
    for observation in observations:
        if observation.get("expectedAdmitted") is True:
            if not observation["admitted"]:
                raise AssertionError(f"expected admission missing for {observation['name']}")
        if observation.get("expectedAdmitted") is True and observation["mode"] == "atomic" and not observation["admitted"]:
            raise AssertionError(f"expected atomic admission missing for {observation['name']}")
        if observation.get("expectedStagedAdmitted") is True and observation["mode"] == "staged" and not observation["admitted"]:
            raise AssertionError(f"expected staged admission missing for {observation['name']}")
        if observation.get("expectedStagedAdmitted") is False and observation["mode"] == "staged" and observation["admitted"]:
            raise AssertionError(f"unexpected staged admission for {observation['name']}")
    if not required <= coverage or not refused_atomic_404 or venue_coverage != {0, 1} or not recovery_observed:
        raise AssertionError(f"real fixture coverage incomplete: modes/kinds={coverage}, atomic404Refused={refused_atomic_404}, venues={venue_coverage}, recovery={recovery_observed}")
    return {"sdk": "black-market-python-sdk", "chainId": client.eth.chain_id, "coverage": sorted(coverage), "venues": sorted(venue_coverage), "fixtures": observations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest.resolve()), indent=2))


if __name__ == "__main__":
    main()
