"""Exercise real lifecycle fixtures on an explicitly isolated local Anvil.

Usage: LAUNCH_LIFECYCLE_RPC_URL=http://127.0.0.1:8545 \
    python examples/launch_lifecycle_smoke.py /absolute/manifest.json

The manifest and plan rows come from the current lifecycle deployment on an owned local chain.
No keys, default addresses, production broadcasts or fabricated pools are used.
Each fixture executes in an independent local snapshot. Actual Active state,
receipts and market evidence are observed before rollback, not retained on-chain.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web3 import Web3
from eth_abi import decode as abi_decode, encode as abi_encode

from black_market_sdk import (
    ERC20_ABI,
    LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI,
    LAUNCH_LIFECYCLE_V1_ABI,
    LAUNCH_MARKET_ADAPTER_V1_ABI,
    MARKET_IDENTITY_COMPONENTS_V1,
    POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1,
    ControlledLaunchFork,
    LaunchExecutionLimits,
    LaunchStateChanged,
    LaunchLimitContext,
    LifecycleLimitResolver,
    LifecyclePhase,
    PoolBoundHookParametersV1,
    build_pool_bound_hook_deployment_transaction,
    build_next_transaction,
    encode_launch_plan,
    create_controlled_launch_fork,
    decode_lifecycle_pool_bound_v4_market_config,
    decode_lifecycle_events,
    encode_lifecycle_pool_bound_v4_market_config,
    encode_pool_bound_hook_parameters,
    hash_launch_plan,
    get_tick_at_sqrt_ratio,
    launch_id_of,
    launch_plan_from_dict,
    plan_launch,
    predict_launch_token,
    predict_pool_bound_hook_address,
    mine_pool_bound_hook_salt,
    pool_bound_market_commitment,
    prepare_pool_bound_lifecycle_plan,
    preview_lifecycle_fees,
    read_launch_markets,
    read_launch_progress,
    read_lifecycle_profiles,
    read_pool_bound_hook_deployment,
    simulate_launch_plan,
    to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import canonical_abi_type
from black_market_sdk.lifecycle_rpc import LaunchRpcSimulation, LaunchSimulationCall, hex_bytes


def request(client: Web3, method: str, params: list):
    response = client.provider.make_request(method, params)
    if response.get("error") is not None:
        raise RuntimeError(f"{method}: {response['error']}")
    return response["result"]


def observe_market_oracle(client: Web3, identity: dict, seconds_ago: int, block_number: int):
    if identity["venue"] == 0:
        signature = "observeTruncated(bytes32,uint32[])"
        data = Web3.keccak(text=signature)[:4] + abi_encode(
            ["bytes32", "uint32[]"], [bytes.fromhex(identity["poolId"][2:]), [seconds_ago]]
        )
        address = identity["hook"]
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
    rows = payload if isinstance(payload, list) else payload.get("plans", [])
    if any(row.get("expectationMode") == "measured-policy" for row in rows):
        exported_limits = payload.get("executionLimits") if isinstance(payload, dict) else None
        configured_limits = manifest.get("executionLimits")
        if not isinstance(exported_limits, dict) or not exported_limits.get("provenance") or not isinstance(configured_limits, dict) or not configured_limits.get("provenance"):
            raise ValueError("measured-policy fixtures require matching reviewed execution limits and provenance")
        for key, value in exported_limits.items():
            if key not in configured_limits or configured_limits[key] != value:
                raise ValueError(f"fixture execution policy {key} differs from the captured manifest")
    if not rows:
        raise ValueError("the deployment fixture has no complete economic plans")
    return rows


def finalize_bound_fixture(client: Web3, plan, row: dict, *, exercise_remine: bool):
    indices = [index for index, market in enumerate(plan.markets) if market.config_version == 5]
    if not indices:
        return plan, []
    if read_launch_progress(client, plan).phase != LifecyclePhase.NONE:
        raise AssertionError("pool-bound SDK fixture must use its own fresh exporter nonce")
    profiles = read_lifecycle_profiles(client, orchestrator=plan.orchestrator, profile_ids=[plan.markets[index].profile_id for index in indices])
    if any(not profile.admitted or profile.topology.hook_topology != 2 or profile.topology.config_version != 5 for profile in profiles):
        raise AssertionError("SDK discovery did not certify an actual reviewed pool-bound V5 offering")
    by_profile = {profile.id: profile for profile in profiles}
    token = predict_launch_token(client, plan)
    expected = {int(item["marketIndex"]): item for item in row.get("hookDeployments", [])}
    markets = list(plan.markets)
    exported = {}
    for index in indices:
        metadata = read_pool_bound_hook_deployment(client, plan, market_index=index)
        exported[index] = metadata
        vector = expected.get(index)
        if vector is None:
            raise AssertionError("pool-bound exporter row is missing exact hook deployment evidence")
        for field, actual in (("deployer", metadata.deployer), ("initCodeHash", metadata.init_code_hash), ("salt", metadata.salt), ("predictedHook", metadata.predicted_hook)):
            if str(vector[field]).lower() != actual.lower():
                raise AssertionError(f"Python {field} differs from actual Solidity deployment metadata")
        deployment_transaction = build_pool_bound_hook_deployment_transaction(client, plan, market_index=index)
        parameter_type = canonical_abi_type({"type": "tuple", "components": POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1})
        values, salt = abi_decode([parameter_type, "bytes32"], bytes.fromhex(deployment_transaction.data[10:]))
        parameters = PoolBoundHookParametersV1(*values)
        encoded_parameters = encode_pool_bound_hook_parameters(parameters)
        if Web3.to_hex(encoded_parameters) != vector["encodedParameters"].lower() or Web3.to_hex(Web3.keccak(encoded_parameters)) != vector["deploymentConfigHash"].lower():
            raise AssertionError("Python 18-field constructor tuple/hash differs from actual Solidity helper")
        commitment = pool_bound_market_commitment(plan, token=token, registrar=parameters.registrar, market_index=index)
        if commitment != vector["marketCommitment"].lower() or Web3.to_hex(salt) != metadata.salt or by_profile[hex_bytes(plan.markets[index].profile_id)].topology.hook_creation_code_hash != vector["creationCodeHash"].lower():
            raise AssertionError("Python market commitment/salt/creation code differs from certified Solidity graph")
        config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[index].config)
        markets[index] = replace(markets[index], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=bytes(32))))
    unmined = replace(plan, markets=tuple(markets))
    if unmined.nonce != plan.nonce or predict_launch_token(client, unmined).lower() != token.lower():
        raise AssertionError("unmined hook salt changed the committed nonce or token identity")
    for index in indices:
        metadata = read_pool_bound_hook_deployment(client, unmined, market_index=index)
        if metadata.init_code_hash != exported[index].init_code_hash or metadata.salt != hex_bytes(bytes(32)):
            raise AssertionError("unmined metadata rejected or reinterpreted the exact zero hook salt")
    finalized = asyncio.run(prepare_pool_bound_lifecycle_plan(client, unmined))
    if finalized.plan.nonce != plan.nonce or predict_launch_token(client, finalized.plan).lower() != token.lower():
        raise AssertionError("local salt finalization changed the exporter nonce or deterministic token")
    if encode_launch_plan(finalized.plan) != encode_launch_plan(plan):
        raise AssertionError("Python local mining did not reproduce the exporter's exact finalized V5 plan")
    evidence = []
    for deployment in finalized.deployments:
        actual = read_pool_bound_hook_deployment(client, finalized.plan, market_index=deployment.market_index)
        if actual != exported[deployment.market_index]:
            raise AssertionError("Python local mining did not reproduce the exporter's exact deployment tuple")
        evidence.append(asdict(deployment))
    if exercise_remine:
        first = finalized.deployments[0]
        mined = asyncio.run(mine_pool_bound_hook_salt(deployer=first.deployer, init_code_hash=first.init_code_hash, start_salt=int(first.salt, 16) + 1))
        config = decode_lifecycle_pool_bound_v4_market_config(finalized.plan.markets[first.market_index].config)
        markets = list(finalized.plan.markets)
        markets[first.market_index] = replace(markets[first.market_index], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=mined.salt)))
        remined_plan = replace(finalized.plan, markets=tuple(markets))
        actual = read_pool_bound_hook_deployment(client, remined_plan, market_index=first.market_index)
        if actual.init_code_hash != first.init_code_hash or actual.predicted_hook != mined.predicted_hook or actual.salt != mined.salt or actual.predicted_hook == first.predicted_hook or predict_launch_token(client, remined_plan).lower() != token.lower():
            raise AssertionError("remining failed exact CREATE2/token-identity parity")
        evidence[0]["remine"] = asdict(actual)
    return finalized.plan, evidence


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
        raw = dict(manifest.get("executionLimits", {}))
        if row.get("expectationMode") != "measured-policy":
            raw.update(row.get("limits", {}))
        names = {field.name for field in fields(LaunchExecutionLimits)}
        values = {}
        for key, value in raw.items():
            name = aliases.get(key, key)
            if name not in names:
                if row.get("expectationMode") != "measured-policy" and key in row.get("limits", {}):
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
def restore_fixture(client: Web3, fork: ControlledLaunchFork, snapshot, baseline: dict, account: str, funding: list[dict]) -> None:
    """Restore existing funded inputs exactly; never mint/top up or relax the full plan."""
    if request(client, "evm_revert", [snapshot]) is not True:
        raise AssertionError("fixture failed to restore its local source snapshot")
    head = request(client, "eth_getBlockByNumber", ["latest", False])
    if head["hash"] != baseline["hash"] or head["stateRoot"] != baseline["stateRoot"]:
        raise AssertionError("fixture did not restore its exact baseline block/state")
    for item in funding:
        if read_balance(client, item["inputAsset"], account) != item["availableBalance"]:
            raise AssertionError("fixture changed an original funding-input balance after rollback")
    request(fork.client, "anvil_reset", [{"forking": {"jsonRpcUrl": fork.source_rpc_url, "blockNumber": int(baseline["number"], 16)}}])


def selected_v4_pool(client: Web3, plan, row: dict) -> tuple[int, dict]:
    # In mixed-venue plans, an opening buy may target Abyss; resolve the V4 market explicitly.
    index = int(row.get("selectedV4MarketIndex", next(
        (index for index, market in enumerate(plan.markets) if market.config_version in (4, 5)), -1)))
    if index < 0 or index >= len(plan.markets):
        raise AssertionError("fixture has no explicit selectable V4 market")
    market = plan.markets[index]
    profile = read_lifecycle_profiles(client, orchestrator=plan.orchestrator, profile_ids=[market.profile_id])[0]
    adapter = client.eth.contract(address=Web3.to_checksum_address(profile.adapter["implementation"]), abi=LAUNCH_MARKET_ADAPTER_V1_ABI)
    values = adapter.functions.resolve(
        bytes.fromhex(launch_id_of(plan)[2:]), predict_launch_token(client, plan), to_launch_plan_tuple(plan)[7][index]
    ).call()
    identity = {component["name"]: hex_bytes(value) if component["type"].startswith("bytes") else value
                for component, value in zip(MARKET_IDENTITY_COMPONENTS_V1, values)}
    for field in ("poolId", "hook", "currency0", "currency1", "manager"):
        expected = row.get("selectedV4Pool", {}).get(field)
        if expected is not None and identity[field].lower() != expected.lower():
            raise AssertionError(f"selected V4 pool {field} differs from exact Solidity export")
    return index, identity




def factory_binding_proof(client: Web3, launch, fork: ControlledLaunchFork) -> dict:
    """Reject factory runtime drift even when token/hook predictions stay exact."""
    plan = launch.plan
    indices = [index for index, market in enumerate(plan.markets) if market.config_version == 5]
    if not indices or launch.token_factory is None or launch.token_factory_code_hash is None:
        raise AssertionError("factory binding proof requires a reviewed fresh bound launch")
    factory = launch.token_factory
    code = hex_bytes(request(client, "eth_getCode", [factory, "latest"]))
    modified = code + "00"
    before = {index: read_pool_bound_hook_deployment(client, plan, market_index=index) for index in indices}
    def assert_predictions_unchanged():
        if predict_launch_token(client, plan).lower() != launch.predicted_token.lower():
            raise AssertionError("append-STOP proof changed prediction instead of isolating runtime provenance")
        for index, expected in before.items():
            if read_pool_bound_hook_deployment(client, plan, market_index=index) != expected:
                raise AssertionError("factory-only mutation changed exact hook deployment metadata")
    snapshot = request(client, "evm_snapshot", [])
    try:
        request(client, "anvil_setCode", [factory, modified])
        assert_predictions_unchanged()
        try:
            build_next_transaction(client, launch, account=launch.account, fork=fork)
        except ValueError as error:
            reviewed_reason = str(error)
            if "token factory" not in reviewed_reason.lower():
                raise AssertionError(f"reviewed write refused for unrelated reason: {reviewed_reason}") from error
        else:
            raise AssertionError("changed factory runtime yielded a reviewed wallet command")
    finally:
        if request(client, "evm_revert", [snapshot]) is not True or hex_bytes(request(client, "eth_getCode", [factory, "latest"])) != code:
            raise AssertionError("factory mutation proof failed to restore source runtime")
    markets = list(plan.markets)
    first = indices[0]
    deployment = before[first]
    start = 0
    while int(predict_pool_bound_hook_address(deployer=deployment.deployer, init_code_hash=deployment.init_code_hash, salt=start.to_bytes(32, "big")), 16) & 0x3FFF == 0x1AFC:
        start += 1
    config = decode_lifecycle_pool_bound_v4_market_config(markets[first].config)
    markets[first] = replace(markets[first], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=start.to_bytes(32, "big"))))
    draft = replace(plan, markets=tuple(markets))
    changed = False
    def mutate_during_mining(_):
        nonlocal changed
        if not changed:
            request(client, "anvil_setCode", [factory, modified])
            changed = True
    snapshot = request(client, "evm_snapshot", [])
    try:
        try:
            asyncio.run(prepare_pool_bound_lifecycle_plan(client, draft, on_progress=mutate_during_mining))
        except ValueError as error:
            preparation_reason = str(error)
            if not changed or "token factory" not in preparation_reason.lower():
                raise AssertionError(f"salt finalization refused for unrelated reason: {preparation_reason}") from error
        else:
            raise AssertionError("changed factory runtime yielded a finalized pool-bound plan")
        assert_predictions_unchanged()
    finally:
        if request(client, "evm_revert", [snapshot]) is not True or hex_bytes(request(client, "eth_getCode", [factory, "latest"])) != code:
            raise AssertionError("mining mutation proof failed to restore source runtime")
    return {"factory": factory, "reviewedCodeHash": launch.token_factory_code_hash,
            "changedCodeHash": Web3.to_hex(Web3.keccak(bytes.fromhex(modified[2:]))),
            "tokenAndHookPredictionsUnchanged": True, "reviewedWriteRejected": reviewed_reason,
            "miningFinalizationRejected": preparation_reason, "sourceRuntimeRestored": True}

INTENTIONAL_REFUSALS = {"gas-cap-refusal", "sdk-policy-refusal"}


def _receipt_refusal_kind(launch) -> str:
    evidence = launch.simulation.evidence
    transactions = launch.simulation.transactions
    cap = launch.limits.gas_cap(launch.simulation.block)
    if evidence is None or evidence.confidence != "stateful" or evidence.backend != "controlled-fork-ceiling-receipts" or cap <= 0:
        return "unclassified"
    failed = next((index for index, call in enumerate(evidence.calls) if not call.success), None)
    if failed is not None:
        if failed < len(transactions):
            transaction, call = transactions[failed], evidence.calls[failed]
            if transaction.kind in {"activate", "atomic"} and transaction.gas_limit == cap and call.error is not None and call.error.get("outOfGas") is True:
                return "terminal-oog"
        return "unclassified"
    if len(evidence.calls) == len(transactions):
        for transaction, call in zip(transactions, evidence.calls):
            if transaction.kind in {"activate", "atomic"} and transaction.gas_limit == cap and launch.limits.buffered_gas(call.gas_used) > cap:
                return "headroom-only"
    return "unclassified"


def refusal_outcome(launch) -> str:
    """Accept only actual matched-cap receipt headroom or terminal-chain OOG."""
    if _receipt_refusal_kind(launch) in {"terminal-oog", "headroom-only"}:
        return "gas-cap-refusal"
    return "failure:" + ("; ".join(launch.simulation.reasons) or "unmeasured refusal")


def sdk_policy_refusal_evidence(original, receipt_proof) -> dict | None:
    """Keep a refused SDK envelope distinct from a successful exact-ceiling receipt."""
    simulation = original.simulation
    sdk = simulation.evidence
    probe = receipt_proof.simulation.evidence
    cap = original.limits.gas_cap(simulation.block)
    if (simulation.admitted or simulation.confidence != "stateful" or sdk is None
        or sdk.backend not in {"controlled-fork", "eth_simulateV1"} or not sdk.successful
        or sdk.block != simulation.block or original.limits.unknown_constraints()
        or simulation.unknown_constraints or receipt_proof.limits != original.limits
        or probe is None or probe.backend != "controlled-fork-ceiling-receipts"
        or not probe.successful or probe.block != sdk.block or cap <= 0
        or len(sdk.calls) != len(simulation.transactions) or len(probe.calls) != len(sdk.calls)
        or tuple(replace(transaction, gas_limit=cap) for transaction in simulation.transactions)
            != receipt_proof.simulation.transactions
        or any(original.limits.buffered_gas(call.gas_used) > cap for call in probe.calls)):
        return None
    required = next(((index, transaction, call) for index, (transaction, call) in
        enumerate(zip(simulation.transactions, sdk.calls))
        if transaction.kind in {"atomic", "activate"} and call.gas_required is not None
        and original.limits.buffered_gas(call.gas_required) > cap), None)
    if required is None:
        return None
    index, transaction, call = required
    return {
        "classification": "sdk-envelope-headroom", "backend": sdk.backend,
        "blockNumber": sdk.block.number, "blockHash": sdk.block.block_hash,
        "transactionId": transaction.step_id, "transactionIndex": index,
        "executionGasCeiling": cap, "headroomBps": original.limits.headroom_bps,
        "requiredEnvelopeGas": call.gas_required,
        "bufferedRequiredEnvelopeGas": original.limits.buffered_gas(call.gas_required),
        "originalSdkReasons": simulation.reasons,
        "originalSdkCalls": [
            {"transactionId": item.step_id, "kind": item.kind, "to": item.to,
             "inputKeccak": Web3.to_hex(Web3.keccak(hexstr=item.data)), "success": measured.success,
             "gasUsed": measured.gas_used, "gasRequired": measured.gas_required}
            for item, measured in zip(simulation.transactions, sdk.calls)
        ],
        "receiptComparison": {
            "outcome": "successful-exact-ceiling-probe",
            "gasUsed": probe.calls[index].gas_used,
            "bufferedReceiptGas": original.limits.buffered_gas(probe.calls[index].gas_used),
            "sourceSubmissionAdmitted": False,
        },
    }


def refusal_evidence(client: Web3, launch, fork: ControlledLaunchFork, *, receipts: list[dict] | None = None):
    """Obtain receipt/terminal-trace evidence on the already-owned separate fork."""
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
            if receipts is not None:
                canonical = fork.client.eth.get_block(receipt["blockNumber"])
                if canonical["hash"] != receipt["blockHash"]:
                    raise AssertionError("refusal receipt must be canonical when captured")
                receipts.append({"kind": transaction.kind, "transactionHash": Web3.to_hex(transaction_hash),
                    "blockHash": Web3.to_hex(receipt["blockHash"]), "gasUsed": int(receipt["gasUsed"]),
                    "gasLimit": cap, "status": int(receipt["status"]), "canonicalAtCapture": True})
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
    kind = _receipt_refusal_kind(replace(launch, simulation=replace(launch.simulation, evidence=proof, transactions=transactions)))
    reasons = ("Exact ceiling terminal call-chain OOG" if kind == "terminal-oog"
        else "Successful matched-cap receipts exceed explicit headroom" if kind == "headroom-only"
        else "Exact ceiling receipt result does not prove gas exhaustion or headroom failure",)
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
    registry = client.eth.contract(address=core.functions.registry().call(), abi=LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI)
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
    snapshot = request(client, "evm_snapshot", [])
    try:
        return _run_rows(client, fork, manifest, account, rows)
    finally:
        if request(client, "evm_revert", [snapshot]) is not True:
            raise AssertionError("fixture smoke failed to restore its initial local source")


def _run_rows(client: Web3, fork: ControlledLaunchFork, manifest: dict, account: str, rows: list[dict]) -> dict:
    observations = []
    coverage = set()
    venue_coverage = set()
    recovery_observed = False
    bound_coverage = set()
    shared_coverage = set()
    remine_observed = False
    factory_binding_observed = False
    for row in rows:
        baseline = request(client, "eth_getBlockByNumber", ["latest", False])
        funding = [{"inputAsset": item["inputAsset"], "inputAmount": item["inputAmount"],
                    "availableBalance": read_balance(client, item["inputAsset"], account)}
                   for item in row["plan"]["funding"]]
        snapshot = request(client, "evm_snapshot", [])
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
        exported_plan_hash = hash_launch_plan(plan)
        plan, hook_deployments = finalize_bound_fixture(client, plan, row, exercise_remine=not remine_observed)
        remine_observed = remine_observed or any("remine" in item for item in hook_deployments)
        selected_index, selected_pool = selected_v4_pool(client, plan, row)
        limits = execution_limits(manifest, row)
        batch_size = int(row.get("prepareBatchSize", row.get("batchSize", 1)))
        launch = plan_launch(client, plan, account=account, mode=row["mode"], limits=limits, prepare_batch_size=batch_size, fork=fork)
        measured_policy = row.get("expectationMode") == "measured-policy"
        expected_atomic = row.get("expectedAdmitted")
        expected_staged = row.get("expectedStagedAdmitted")
        if not measured_policy and expected_atomic is None and expected_staged is None:
            raise AssertionError(f"fixture {row.get('name')} lacks an explicit exporter expectation or measured-policy mode")
        observation = {"name": row.get("name", str(plan.nonce)), "mode": launch.mode, "tokenKind": int(plan.token.kind), "planHash": launch.plan_hash, "launchId": launch.launch_id, "predictedToken": launch.predicted_token, "admitted": launch.admitted, "confidence": launch.confidence, "backend": launch.simulation.backend, "blockNumber": launch.simulation.block.number, "blockHash": launch.simulation.block.block_hash, "atomicAdmitted": launch.atomic_simulation.admitted if launch.atomic_simulation else None, "expectedAdmitted": expected_atomic, "expectedStagedAdmitted": expected_staged}
        observation.update({"exportedPlanHash": exported_plan_hash, "hookDeployments": hook_deployments,
                            "expectationMode": row.get("expectationMode"), "gasCap": launch.limits.gas_cap(launch.simulation.block),
                            "headroomBps": launch.limits.headroom_bps})
        observation.update({"workload": row.get("workload"), "shapeName": row.get("shapeName"),
            "catalogueProfile": row.get("catalogueProfile"), "corePositionCount": row.get("corePositionCount"),
            "openingBuyCount": len(plan.buys), "selectedV4MarketIndex": selected_index,
            "selectedV4Pool": selected_pool, "policy": manifest.get("executionLimits", {}),
            "scenarioIsolation": {"method": "independent-local-snapshot", "sourceRestored": True,
                "baselineBlockHash": baseline["hash"], "baselineStateRoot": baseline["stateRoot"],
                "funding": funding, "receiptEvidence": "observed-before-rollback"}})
        if not launch.admitted:
            refusal_receipts = []
            evidence_launch = refusal_evidence(client, launch, fork, receipts=refusal_receipts)
            outcome = refusal_outcome(evidence_launch)
            sdk_policy = sdk_policy_refusal_evidence(launch, evidence_launch)
            if sdk_policy is not None:
                outcome = "sdk-policy-refusal"
                observation["sdkPolicyEvidence"] = sdk_policy
            observation["reasons"] = launch.simulation.reasons
            observation["outcome"] = outcome
            evidence = evidence_launch.simulation.evidence
            observation["gasEvidence"] = {"backend": evidence.backend, "blockNumber": evidence.block.number, "blockHash": evidence.block.block_hash, "refusalKind": _receipt_refusal_kind(evidence_launch), "calls": [
                {"kind": transaction.kind, "success": call.success, "gasUsed": call.gas_used, "gasLimit": transaction.gas_limit, "gasRequired": call.gas_required, "error": call.error}
                for transaction, call in zip(evidence_launch.simulation.transactions, evidence.calls)
            ]}
            observation["gasEvidence"]["receipts"] = refusal_receipts
            if sdk_policy is not None:
                observation["gasEvidence"]["refusalKind"] = "none-receipts-succeeded"
                observation["gasEvidence"]["receiptOutcome"] = "successful-exact-ceiling-probe"
            observations.append(observation)
            requested_expected = expected_atomic if row["mode"] == "atomic" else expected_staged
            if not measured_policy and requested_expected is True:
                raise AssertionError(f"required fixture unsupported: {observation}")
            expected_outcome = row.get("expectedOutcome") if row["mode"] == "atomic" else row.get("expectedStagedOutcome")
            if not measured_policy and expected_outcome != outcome:
                raise AssertionError(f"refusal must be the exporter's expected intentional outcome, got {outcome!r} for {observation['name']}; {observation['gasEvidence']}")
            if outcome not in INTENTIONAL_REFUSALS:
                raise AssertionError(f"refusal lacks an authenticated receipt or SDK policy basis: {observation}")
            restore_fixture(client, fork, snapshot, baseline, account, funding)
            continue
        # This is a second real invocation of the public simulation API, not a
        # wrapper returning the stored launch's old confidence/counters.
        resimulation = simulate_launch_plan(client, launch, account=account, fork=fork)
        if not resimulation.admitted or resimulation.block.block_hash != launch.simulation.block.block_hash:
            raise AssertionError("public simulation API did not prove this actual canonical sequence")
        if hook_deployments and not factory_binding_observed:
            observation["factoryBindingProof"] = factory_binding_proof(client, launch, fork)
            factory_binding_observed = True
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
            raise AssertionError("actual SDK receipts are not canonical confirmed commands before fixture rollback")
        markets = read_launch_markets(client, plan)
        if len(markets) != len(plan.markets) or sum(len(market["positions"]) for market in markets) != progress.position_count:
            raise AssertionError("canonical discovery omitted an activated market or position")
        if activation_receipt is None:
            raise AssertionError("full activation did not produce its actual atomic/staged receipt")
        buy_events = [event.args for event in decode_lifecycle_events(plan, activation_receipt["logs"])
                      if event.name == "InitialBuyExecuted"]
        if len(buy_events) != len(plan.buys):
            raise AssertionError("actual activation receipt omitted a committed opening buy")
        for index, (event, buy) in enumerate(zip(buy_events, plan.buys)):
            if (event["buyIndex"] != index or event["marketIndex"] != buy.market_index
                or event["recipient"].lower() != buy.recipient.lower()
                or event["quoteSpent"] > buy.quote_amount_in or event["tokenOut"] < buy.min_token_out):
                raise AssertionError("actual opening receipts changed the committed buy order/economics")
        selected_market = next(market for market in markets if market["marketIndex"] == selected_index)
        if selected_market["identity"]["poolId"].lower() != selected_pool["poolId"].lower():
            raise AssertionError("actual manager selected V4 pool differs from the exact exported identity")
        if row.get("workload") == "representative-one-opening-swap":
            if len(markets) != 1 or len(plan.buys) != 1 or plan.buys[0].market_index != selected_index or progress.position_count != row["corePositionCount"]:
                raise AssertionError("representative catalogue changed its full core/one-buy/one-pool plan")
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
            current = observe_market_oracle(client, identity, 0, activation_receipt["blockNumber"])
            if current.get("error") is not None:
                raise AssertionError(f"real pool-local oracle read failed: {current['error']}")
            ticks, seconds_per_liquidity = abi_decode(["int56[]", "uint160[]"], bytes.fromhex(current["result"][2:]))
            if ticks[0] != normalized_tick * elapsed or seconds_per_liquidity[0] != elapsed << 128:
                raise AssertionError("actual oracle omitted quote-normalized empty history through full activation")
            older = observe_market_oracle(client, identity, elapsed + 1, activation_receipt["blockNumber"])
            if older.get("error", {}).get("data") != Web3.to_hex(Web3.keccak(text="ObservationTooOld()")[:4]):
                raise AssertionError(f"oracle fabricated history before its actual genesis: {older}")
            oracle_history.append({"marketIndex": market["marketIndex"], "venue": identity["venue"], "initializedAt": market["live"]["oracleReadyAt"], "blockHash": Web3.to_hex(activation_receipt["blockHash"]), "tickCumulative": str(ticks[0]), "secondsPerLiquidityCumulativeX128": str(seconds_per_liquidity[0]), "beforeGenesisRejected": True})
            for position in market["positions"]:
                if position["liveLiquidity"] != int(position["liquidity"]) or position["liveOwner"].lower() != market["custody"].lower():
                    raise AssertionError("live canonical position is not in its permanent custody")
            committed = plan.markets[market["marketIndex"]]
            if identity["venue"] == 0 and committed.config_version == 5:
                config = decode_lifecycle_pool_bound_v4_market_config(committed.config)
                metadata = read_pool_bound_hook_deployment(client, plan, market_index=market["marketIndex"])
                if metadata.predicted_hook.lower() != identity["hook"].lower() or market["positionCount"] != len(config.positions):
                    raise AssertionError("prepared bound metadata lost the actual hook or committed position count")
                for actual, expected_position in zip(market["positions"], config.positions):
                    if actual["tickLower"] != expected_position.tick_lower or actual["tickUpper"] != expected_position.tick_upper or int(actual["liquidity"]) != expected_position.liquidity or actual["salt"] != hex_bytes(expected_position.salt):
                        raise AssertionError("bound permanent position differs from exact committed economics")
                bound_coverage.add((launch.mode, int(plan.token.kind), len(plan.markets)))
            elif identity["venue"] == 0 and committed.config_version == 4:
                shared_coverage.add((launch.mode, int(plan.token.kind)))
        payments = preview_lifecycle_fees(client, progress.fee_hub, executor=account)
        if [payment["asset"].lower() for payment in payments] != [policy.asset.lower() for policy in plan.fee_assets]:
            raise AssertionError("claimAndSplit preview did not preserve all canonical fee assets including zeros")
        if build_next_transaction(client, launch, account=account, fork=fork) is not None:
            raise AssertionError("terminal active launch yielded another economic command")
        coverage.add((launch.mode, int(plan.token.kind)))
        observation.update({"outcome": "active", "transactions": receipts, "phase": progress.phase.name, "marketCount": len(markets), "positionCount": progress.position_count, "orderedBuys": len(buy_events), "buyEvents": buy_events, "feePreview": payments, "recovery": recovery, "oracleHistory": oracle_history})
        observations.append(observation)
        restore_fixture(client, fork, snapshot, baseline, account, funding)
    required = {("atomic", 0), ("staged", 0), ("staged", 1)}
    for observation in observations:
        if observation.get("expectationMode") == "measured-policy":
            continue
        expected = observation.get("expectedAdmitted") if observation["mode"] == "atomic" else observation.get("expectedStagedAdmitted")
        if expected is not None and observation["admitted"] is not expected:
            raise AssertionError(f"requested-mode admission differs from the explicit expectation for {observation['name']}")
    if not required <= coverage or venue_coverage != {0, 1} or not recovery_observed:
        raise AssertionError(f"real fixture coverage incomplete: modes/kinds={coverage}, venues={venue_coverage}, recovery={recovery_observed}")
    if any(item.get("hookDeployments") for item in observations):
        expected_bound = {(item["mode"], item["tokenKind"], item["marketCount"]) for item in observations if item.get("hookDeployments") and item.get("outcome") == "active"}
        if not bound_coverage or not expected_bound <= bound_coverage or not shared_coverage or not remine_observed or not factory_binding_observed:
            raise AssertionError(f"bound/shared coexistence coverage incomplete: bound={bound_coverage}, expected={expected_bound}, shared={shared_coverage}, remined={remine_observed}, factoryBinding={factory_binding_observed}")
    return {"schema": "black-market.launch-lifecycle-sdk-smoke.v1", "sdk": "black-market-python-sdk", "chainId": client.eth.chain_id, "executionLimits": manifest.get("executionLimits", {}), "scenarioIsolation": {"method": "independent-local-snapshot", "sourceRestored": True, "receiptEvidence": "observed-before-rollback"}, "coverage": sorted(coverage), "venues": sorted(venue_coverage), "boundCoverage": sorted(bound_coverage), "sharedCoverage": sorted(shared_coverage), "fixtures": observations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest.resolve()), indent=2))


if __name__ == "__main__":
    main()
