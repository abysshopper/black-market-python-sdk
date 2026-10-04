"""Consumer-visible commitment, admission and canonical recovery regressions.

The portable vectors are shared with the Node SDK. Real complete AMM execution
is exercised by examples/launch_lifecycle_smoke.py against the local manifest;
these deterministic tests inject RPC failures/reorgs, never successful mock AMMs.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from black_market_sdk import (
    LAUNCH_PLAN_V1_ABI_TYPE,
    LAUNCH_LIFECYCLE_V1_ABI,
    LaunchBlock,
    LaunchExecutionLimits,
    LaunchStateChanged,
    LifecyclePhase,
    LifecycleTransaction,
    build_lifecycle_calldata,
    build_next_transaction,
    create_controlled_launch_fork,
    decode_lifecycle_events,
    encode_launch_plan,
    decode_lifecycle_v4_market_config,
    encode_lifecycle_v4_market_config,
    hash_launch_plan,
    launch_id_of,
    launch_plan_from_dict,
    launch_plan_to_dict,
    read_launch_progress,
    to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import _simulate_sequence, canonical_abi_type
from black_market_sdk.lifecycle_rpc import ControlledLaunchFork, simulate_transactions
from black_market_sdk.lifecycle import _resolve_limits


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"
ZERO = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
BLOCK_HASH = "0x" + "aa" * 32
ORPHAN_HASH = "0x" + "bb" * 32
TRANSACTION_HASH = "0x" + "cc" * 32


def vector():
    payload = json.loads(FIXTURE_PATH.read_text())
    if "plan" in payload:
        return payload
    for key in ("fixtures", "vectors", "cases", "plans"):
        if payload.get(key):
            return payload[key][0]
    raise AssertionError("shared lifecycle fixture has no complete economic vector")


def plan():
    return launch_plan_from_dict(vector()["plan"])


def function_signature(name):
    entry = next(item for item in LAUNCH_LIFECYCLE_V1_ABI if item.get("name") == name and item["type"] == "function")
    types = [canonical_abi_type(item) for item in entry["inputs"]]
    return keccak(text=name + "(" + ",".join(types) + ")")[:4], types


def event_log(name, arguments, *, target=None, index=0):
    entry = next(item for item in LAUNCH_LIFECYCLE_V1_ABI if item.get("name") == name and item["type"] == "event")
    signature = name + "(" + ",".join(canonical_abi_type(item) for item in entry["inputs"]) + ")"
    topics = ["0x" + keccak(text=signature).hex()]
    plain_types, plain_values = [], []
    for item in entry["inputs"]:
        value = arguments[item["name"]]
        if item["type"] == "bytes32" and isinstance(value, str):
            value = bytes.fromhex(value[2:])
        if item["indexed"]:
            topics.append("0x" + abi_encode([item["type"]], [value]).hex())
        else:
            plain_types.append(item["type"])
            plain_values.append(value)
    return {"address": target or plan().orchestrator, "topics": topics, "data": "0x" + abi_encode(plain_types, plain_values).hex(), "logIndex": hex(index)}


def test_independent_commitment_vector_roundtrips_exact_large_quantities():
    fixture, request = vector(), plan()
    expected_hash = fixture.get("planHash", fixture.get("hashPlan"))
    assert hash_launch_plan(request) == expected_hash.lower()
    assert launch_id_of(request) == fixture["launchId"].lower()
    restored = launch_plan_from_dict(json.loads(json.dumps(launch_plan_to_dict(request))))
    assert encode_launch_plan(restored) == encode_launch_plan(request)


@pytest.mark.parametrize("change", [
    lambda item: replace(item, chain_id=item.chain_id + 1),
    lambda item: replace(item, creator="0x1000000000000000000000000000000000000007"),
    lambda item: replace(item, nonce=item.nonce + 1),
    lambda item: replace(item, token=replace(item.token, inventory_recipient="0x1000000000000000000000000000000000000008")),
    lambda item: replace(item, token=replace(item.token, salt=b"\x77" * 32)),
    lambda item: replace(item, deadline=item.deadline + 1),
    lambda item: replace(item, markets=(replace(item.markets[0], config=encode_lifecycle_v4_market_config(replace(decode_lifecycle_v4_market_config(item.markets[0].config), developer_fee_bps=251))), *item.markets[1:])),
])
def test_full_economic_commitment_cannot_reuse_other_identity_or_mutated_config(change):
    original = plan()
    changed = change(original)
    assert hash_launch_plan(changed) != vector().get("planHash", vector().get("hashPlan")).lower()


def test_execution_grouping_changes_no_economics_but_commands_bind_full_plan():
    request = plan()
    atomic = build_lifecycle_calldata(request, "launchAtomic")
    staged = build_lifecycle_calldata(request, "beginLaunch")
    atomic_selector, atomic_types = function_signature("launchAtomic")
    staged_selector, staged_types = function_signature("beginLaunch")
    assert bytes.fromhex(atomic[2:])[:4] == atomic_selector
    decoded_atomic = abi_decode(atomic_types, bytes.fromhex(atomic[10:]))
    decoded_staged = abi_decode(staged_types, bytes.fromhex(staged[10:]))
    assert decoded_atomic[0] == decoded_staged[0] == abi_decode([LAUNCH_PLAN_V1_ABI_TYPE], encode_launch_plan(request))[0]
    assert decoded_staged[1] == 1
    preparation = build_lifecycle_calldata(request, "prepareMarkets", first_market=0, count=1)
    _, types = function_signature("prepareMarkets")
    assert abi_decode(types, bytes.fromhex(preparation[10:]))[1:] == (0, 1)
    with pytest.raises(ValueError, match="nonempty contiguous"):
        build_lifecycle_calldata(request, "prepareMarkets", first_market=len(request.markets), count=1)
    with pytest.raises(ValueError, match="launch-specific"):
        build_lifecycle_calldata(request, "multicall")


def test_json_rejects_lossy_amounts_economic_metadata_and_implicit_asset_reordering():
    data = launch_plan_to_dict(plan())
    data["token"]["supply"] = float(data["token"]["supply"])
    with pytest.raises(ValueError, match="uint256"):
        launch_plan_from_dict(data)
    data = launch_plan_to_dict(plan())
    data["mode"] = "staged"
    with pytest.raises(ValueError, match="exactly match"):
        launch_plan_from_dict(data)
    request = plan()
    with pytest.raises(ValueError, match="unique and ascending"):
        to_launch_plan_tuple(replace(request, fee_assets=tuple(reversed(request.fee_assets))))
    with pytest.raises(ValueError, match="sum to 10000"):
        to_launch_plan_tuple(replace(request, fee_assets=(replace(request.fee_assets[0], owner_bps=request.fee_assets[0].owner_bps + 1), *request.fee_assets[1:])))


def test_reusing_funding_or_supply_across_markets_is_rejected_per_asset():
    request = plan()
    excessive = replace(request.markets[0], token_budget=request.token.supply + 1)
    with pytest.raises(ValueError, match="one committed supply"):
        to_launch_plan_tuple(replace(request, markets=(excessive, *request.markets[1:])))
    if request.buys:
        buy = request.buys[0]
        quote = request.markets[buy.market_index].quote_asset.lower()
        funding = next(item for item in request.funding if item.asset.lower() == quote)
        excessive = replace(buy, quote_amount_in=funding.amount + 1)
        with pytest.raises(ValueError, match="per-asset funding"):
            to_launch_plan_tuple(replace(request, buys=(excessive, *request.buys[1:])))


def test_receipt_events_cannot_be_rebound_to_another_launch_or_creator():
    request = plan()
    arguments = {"launchId": launch_id_of(request), "planHash": hash_launch_plan(request), "creator": request.creator, "token": vector().get("predictedToken", "0x1000000000000000000000000000000000000001"), "feeHub": "0x1000000000000000000000000000000000000002", "rewards": ZERO, "mode": 1}
    wrong_id = event_log("LaunchBegun", {**arguments, "launchId": ZERO_HASH})
    with pytest.raises(ValueError, match="another launch identity"):
        decode_lifecycle_events(request, [wrong_id])
    wrong_hash = event_log("LaunchBegun", {**arguments, "planHash": ZERO_HASH})
    with pytest.raises(ValueError, match="economic commitment"):
        decode_lifecycle_events(request, [wrong_hash])
    wrong_creator = event_log("LaunchBegun", {**arguments, "creator": "0x1000000000000000000000000000000000000009"})
    with pytest.raises(ValueError, match="receipt creator"):
        decode_lifecycle_events(request, [wrong_creator])


class CanonicalRpcScenario:
    """Fault-injected canonical state, with no successful simulation backend."""

    def __init__(self, request, *, latest_phase=0, confirmed_phase=0, orphan=False, reorganize_during_read=False):
        self.plan = request
        self.latest_phase = latest_phase
        self.confirmed_phase = confirmed_phase
        self.orphan = orphan
        self.reorganize_during_read = reorganize_during_read
        self.block_reads = 0

    def progress(self, phase):
        if phase == 0:
            return (b"\0" * 32, b"\0" * 32, ZERO, 0, 0, 0, ZERO, ZERO, ZERO, 0, 0, 0, 0, 0)
        prepared = len(self.plan.markets) if phase == 2 else 0
        return (bytes.fromhex(launch_id_of(self.plan)[2:]), bytes.fromhex(hash_launch_plan(self.plan)[2:]), self.plan.creator, self.plan.nonce, 1, phase, vector().get("predictedToken", "0x1000000000000000000000000000000000000001"), "0x1000000000000000000000000000000000000002", ZERO, prepared, len(self.plan.markets), len(self.plan.buys), 0, self.plan.deadline)

    def make_request(self, method, params):
        if method == "eth_chainId":
            result = hex(self.plan.chain_id)
        elif method == "eth_getBlockByNumber":
            self.block_reads += 1
            number = 51 if params[0] == "latest" else int(params[0], 16)
            block_hash = ORPHAN_HASH if self.reorganize_during_read and self.block_reads > 1 else BLOCK_HASH
            result = {"number": hex(number), "hash": block_hash, "timestamp": hex(self.plan.deadline - 100), "gasLimit": hex(30_000_000), "baseFeePerGas": "0x1"}
        elif method == "eth_call":
            data = bytes.fromhex(params[0]["data"][2:])
            selector, _ = function_signature("readLaunchProgress")
            if data[:4] == selector:
                phase = self.latest_phase if params[1] == "0x33" else self.confirmed_phase
                progress_entry = next(item for item in LAUNCH_LIFECYCLE_V1_ABI if item.get("name") == "readLaunchProgress")
                result = "0x" + abi_encode([canonical_abi_type(progress_entry["outputs"][0])], [self.progress(phase)]).hex()
            else:
                selector, _ = function_signature("predictToken")
                assert data[:4] == selector
                result = "0x" + abi_encode(["address"], [vector().get("predictedToken", "0x1000000000000000000000000000000000000001")]).hex()
        elif method == "eth_getBalance":
            result = hex(10**24)
        elif method == "eth_getTransactionReceipt":
            result = {"blockNumber": "0x32", "blockHash": ORPHAN_HASH, "status": "0x1", "logs": []} if self.orphan else None
        elif method == "eth_simulateV1":
            return {"error": {"code": -32601, "message": "eth_simulateV1 is not supported"}}
        else:
            raise AssertionError(f"unexpected scenario operation {method}")
        return {"result": result}


def test_mined_unconfirmed_steps_do_not_create_duplicate_preparation_transactions():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request, latest_phase=2, confirmed_phase=1))
    progress = read_launch_progress(client, request, confirmations=2)
    assert progress.phase == LifecyclePhase.PREPARING
    assert progress.prepared_markets == 0
    assert progress.awaiting_confirmations is True
    with pytest.raises(LaunchStateChanged, match="do not duplicate"):
        build_next_transaction(client, request, account=request.creator, mode="staged", confirmations=2)


def test_orphaned_receipt_does_not_advance_canonical_progress_after_reload():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request, orphan=True))
    progress = read_launch_progress(client, request, transaction_hashes=[TRANSACTION_HASH])
    assert progress.phase == LifecyclePhase.NONE
    assert progress.prepared_markets == 0
    assert progress.receipts[0].status == "reorged"
    assert progress.receipts[0].confirmations == 0


def test_reorg_during_read_fails_instead_of_returning_cross_block_identity():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request, reorganize_during_read=True))
    with pytest.raises(LaunchStateChanged, match="reorganized"):
        read_launch_progress(client, request)


def test_wallet_switch_cannot_build_a_different_creators_funding_transaction():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    with pytest.raises(ValueError, match="current wallet account"):
        build_next_transaction(client, request, account="0x1000000000000000000000000000000000000011", mode="staged")


def test_unsupported_stateful_rpc_remains_provisional_and_never_admitted():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    transaction = LifecycleTransaction("0:atomic", "atomic", request.chain_id, request.creator, request.orchestrator, build_lifecycle_calldata(request, "launchAtomic"), 0, 0, (), ("Active",), gas_limit=30_000_000, gas_price=1)
    result = _simulate_sequence(client, request, [transaction], block=block, predicted=vector().get("predictedToken", ZERO), limits=LaunchExecutionLimits(), fork=None, data_fee_estimator=None)
    assert result.admitted is False
    assert result.confidence == "provisional"
    assert result.transactions[0].gas_estimate is None
    assert "not supported" in result.reasons[0]


def test_live_chain_account_rpc_and_calldata_caps_prevent_false_admission():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 29_000_000, 1)
    limits = LaunchExecutionLimits(chain_transaction_gas_limit=16_777_216, rpc_transaction_gas_limit=20_000_000, account_transaction_gas_limit=12_000_000, max_calldata_bytes=100)
    assert limits.gas_cap(block) == 12_000_000
    assert limits.buffered_gas(10_000_001) == 12_000_002
    data = build_lifecycle_calldata(request, "launchAtomic")
    result = simulate_transactions(client, [{"from": request.creator, "to": request.orchestrator, "data": data, "gas": 12_000_000}], block=block, limits=limits)
    assert result.successful is False
    assert result.reason == "a transaction exceeds the current calldata cap"


def test_controlled_fork_cannot_broadcast_to_remote_or_unowned_nodes():
    remote = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="https://rpc.example.org"))
    with pytest.raises(ValueError, match="loopback"):
        ControlledLaunchFork(client=remote, isolated=True)
    local = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="http://127.0.0.1:8545"))
    with pytest.raises(ValueError, match="explicitly isolated"):
        ControlledLaunchFork(client=local, isolated=False)


class CancellationSimulationRpc(CanonicalRpcScenario):
    """A valid cancellation receipt isolates the limits-admission decision."""

    def make_request(self, method, params):
        if method == "eth_simulateV1":
            log = event_log("LaunchCancelled", {
                "launchId": launch_id_of(self.plan), "creator": self.plan.creator,
                "inventoryBurned": self.plan.token.burn_on_cancel,
            })
            return {"result": [{"calls": [{"status": "0x1", "gasUsed": hex(55_000), "returnData": "0x", "logs": [log]}]}]}
        return super().make_request(method, params)


def test_stateful_execution_does_not_admit_unknown_submission_caps():
    request = plan()
    client = SimpleNamespace(provider=CancellationSimulationRpc(request))
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    transaction = LifecycleTransaction("0:cancel", "cancel", request.chain_id, request.creator, request.orchestrator, build_lifecycle_calldata(request, "cancelLaunch"), 0, 0, (), ("Cancelled",), gas_limit=30_000_000, gas_price=1)
    result = _simulate_sequence(client, request, [transaction], block=block, predicted=ZERO, limits=LaunchExecutionLimits(), fork=None, data_fee_estimator=None)
    assert result.confidence == "stateful"
    assert result.admitted is False
    assert set(result.unknown_constraints) == {"chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit", "max_calldata_bytes", "observed_block_number", "observed_block_hash", "chain_id", "orchestrator", "account"}
    assert "constraints are unknown" in result.reasons[0]
    limits = LaunchExecutionLimits(chain_transaction_gas_limit=30_000_000, rpc_transaction_gas_limit=30_000_000, account_transaction_gas_limit=30_000_000, max_calldata_bytes=131_072, observed_block_number=51, observed_block_hash=BLOCK_HASH, chain_id=request.chain_id, account=request.creator, orchestrator=request.orchestrator)
    unscoped = replace(limits, observed_block_number=None, observed_block_hash=None, chain_id=None, orchestrator=None, account=None)
    unknown_scope = _simulate_sequence(client, request, [transaction], block=block, predicted=ZERO, limits=unscoped, fork=None, data_fee_estimator=None)
    assert unknown_scope.admitted is False
    assert set(unknown_scope.unknown_constraints) == {"observed_block_number", "observed_block_hash", "chain_id", "orchestrator", "account"}
    admitted = _simulate_sequence(client, request, [transaction], block=block, predicted=ZERO, limits=limits, fork=None, data_fee_estimator=None)
    assert admitted.admitted is True
    assert admitted.transactions[0].data_fee is None


@pytest.mark.parametrize("change", [
    lambda limits: replace(limits, observed_block_number=50),
    lambda limits: replace(limits, observed_block_hash=ORPHAN_HASH),
    lambda limits: replace(limits, chain_id=31338),
    lambda limits: replace(limits, account="0x0000000000000000000000000000000000000099"),
    lambda limits: replace(limits, orchestrator="0x0000000000000000000000000000000000000099"),
])
def test_dynamic_limit_source_cannot_authorize_another_account_chain_or_block(change):
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    limits = LaunchExecutionLimits(chain_transaction_gas_limit=16_777_216, rpc_transaction_gas_limit=16_000_000, account_transaction_gas_limit=16_000_000, max_calldata_bytes=131_072, observed_block_number=51, observed_block_hash=BLOCK_HASH, chain_id=request.chain_id, account=request.creator, orchestrator=request.orchestrator)
    resolved, error = _resolve_limits(client, lambda rpc_client, context: change(limits), block, request.creator, request.chain_id, request.orchestrator)
    assert resolved.unknown_constraints()
    assert error == "execution limits could not be resolved: ValueError"


def test_ordered_repeat_market_buys_are_not_commutative_economics():
    request = plan()
    changed = replace(request, buys=tuple(reversed(request.buys)))
    assert hash_launch_plan(changed) != vector()["planHash"]
    assert launch_id_of(changed) == vector()["launchId"]


@pytest.mark.parametrize("reward_mode", [1, 2])
def test_reward_enabled_erc20_supply_obeys_the_precision_boundary(reward_mode):
    request = plan()
    token = replace(request.token, kind=0, reward_mode=reward_mode, nft_unit=0, metadata_uri="", supply=10**77)
    bounded = replace(request, token=token)
    assert to_launch_plan_tuple(bounded)[4][4] == 10**77
    with pytest.raises(ValueError, match="must not exceed 10\\*\\*77"):
        to_launch_plan_tuple(replace(bounded, token=replace(token, supply=10**77 + 1)))


def test_owner_only_erc20_preserves_full_uint256_supply():
    request = plan()
    token = replace(request.token, kind=0, reward_mode=0, nft_unit=0, metadata_uri="", supply=2**256 - 1)
    policies = tuple(replace(policy, owner_bps=policy.owner_bps + policy.rewards_bps, rewards_bps=0) for policy in request.fee_assets)
    assert to_launch_plan_tuple(replace(request, token=token, fee_assets=policies))[4][4] == 2**256 - 1


class UnconfirmedApprovalRpc(CanonicalRpcScenario):
    def make_request(self, method, params):
        if method == "eth_getTransactionCount":
            return {"result": "0x1" if params[1] == "0x33" else "0x0"}
        return super().make_request(method, params)


def test_untracked_mined_approval_must_confirm_before_dependent_launch():
    request = plan()
    client = SimpleNamespace(provider=UnconfirmedApprovalRpc(request))
    with pytest.raises(LaunchStateChanged, match="approvals/funding/commands"):
        build_next_transaction(client, request, account=request.creator, mode="staged", confirmations=2)


def test_controlled_fork_cannot_alias_the_source_execution_node():
    source = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="http://127.0.0.1:8545"))
    alias = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="http://localhost:8545/fork?ignored=1"))
    with pytest.raises(ValueError, match="separate from the source"):
        create_controlled_launch_fork(source, alias, isolated=True)
