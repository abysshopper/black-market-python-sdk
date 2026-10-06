"""Consumer-visible commitment, admission and canonical recovery regressions.

The portable vectors are shared with the Node SDK. Deterministic RPC fixtures
exercise admission envelopes and faults, not live AMM execution. Live complete
execution still requires an explicitly authorized chain scenario.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
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
    assert limits.buffered_gas(10_000_001) == 11_500_002
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


def test_missing_optional_policy_is_uncertainty_not_execution_refusal():
    request = plan()
    client = SimpleNamespace(provider=CancellationSimulationRpc(request))
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    transaction = LifecycleTransaction("0:cancel", "cancel", request.chain_id, request.creator, request.orchestrator, build_lifecycle_calldata(request, "cancelLaunch"), 0, 0, (), ("Cancelled",), gas_limit=30_000_000, gas_price=1)
    result = _simulate_sequence(client, request, [transaction], block=block, predicted=ZERO, limits=LaunchExecutionLimits(), fork=None, data_fee_estimator=None)
    assert result.confidence == "stateful"
    assert result.admitted is True
    assert set(result.unknown_constraints) == {
        "chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit",
        "max_calldata_bytes", "account_max_calldata_bytes", "rpc_max_request_bytes",
        "rpc_total_simulation_gas_limit", "data_fee",
    }
    assert result.reasons == ()
    limits = LaunchExecutionLimits(chain_transaction_gas_limit=30_000_000, rpc_transaction_gas_limit=30_000_000, account_transaction_gas_limit=30_000_000, max_calldata_bytes=131_072, observed_block_number=51, observed_block_hash=BLOCK_HASH, chain_id=request.chain_id, account=request.creator, orchestrator=request.orchestrator)
    unscoped = replace(limits, observed_block_number=None, observed_block_hash=None, chain_id=None, orchestrator=None, account=None)
    unknown_scope = _simulate_sequence(client, request, [transaction], block=block, predicted=ZERO, limits=unscoped, fork=None, data_fee_estimator=None)
    assert unknown_scope.admitted is True
    assert unknown_scope.unknown_constraints == (*limits.unknown_constraints(), "data_fee")
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
    with pytest.raises(ValueError, match="execution limits"):
        _resolve_limits(client, lambda rpc_client, context: change(limits), block, request.creator, request.chain_id, request.orchestrator)


@pytest.mark.parametrize("source", [lambda *_: None, lambda *_: {}, lambda *_: 1])
def test_supplied_policy_resolver_cannot_disappear_into_optional_absence(source):
    request = plan()
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    with pytest.raises(TypeError, match="supplied execution-limit resolver"):
        _resolve_limits(client, source, block, request.creator, request.chain_id, request.orchestrator)


def test_absent_policy_and_explicit_headroom_keep_exact_integer_arithmetic():
    request = plan()
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    limits = _resolve_limits(client, None, block, request.creator, request.chain_id, request.orchestrator)
    assert limits.headroom_bps == 1500
    assert limits.buffered_gas(10_000_001) == 11_500_002
    assert LaunchExecutionLimits(headroom_bps=2000).buffered_gas(10_000_001) == 12_000_002
    exact = 2**63 + 7
    assert limits.buffered_gas(exact) == (exact * 11500 + 9999) // 10000


def test_supplied_policy_resolution_error_is_not_optional_absence():
    request = plan()
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))

    def broken(*_):
        raise RuntimeError("policy service failed")

    with pytest.raises(RuntimeError, match="policy service failed"):
        _resolve_limits(client, broken, block, request.creator, request.chain_id, request.orchestrator)


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


class AdmissionRpcScenario(CanonicalRpcScenario):
    """Wire-level admission fixture; no real signing, broadcast or AMM proof."""

    def __init__(self, request, *, compute_gas=100_000, poster_gas=0, actual_poster_gas=None,
                 balance=10**24, replay_failure=None, probe_failure=None, smart_account=False,
                 pending_nonce=0, raw_version=105, tx_cap=32_000_000, block_cap=32_000_000,
                 prepare_gas_per_market=None, compute_gas_by_kind=None, **progress):
        super().__init__(request, **progress)
        self.compute_gas = compute_gas
        self.compute_gas_by_kind = compute_gas_by_kind or {}
        self.poster_gas = poster_gas
        self.actual_poster_gas = poster_gas if actual_poster_gas is None else actual_poster_gas
        self.balance = balance
        self.replay_failure = replay_failure
        self.probe_failure = probe_failure
        self.smart_account = smart_account
        self.pending_nonce = pending_nonce
        self.raw_version = raw_version
        self.tx_cap = tx_cap
        self.block_cap = block_cap
        self.prepare_gas_per_market = prepare_gas_per_market
        self.simulations = []
        self.probes = []
        self.poster_requests = []
        self.operations = []
        self.getter_override = None
        self.poster_override = None
        self.endpoint_uri = "https://simulation.example"

    def command(self, transaction):
        calldata = bytes.fromhex(transaction["data"][2:])
        for name, kind in (("launchAtomic", "atomic"), ("beginLaunch", "begin"),
                           ("prepareMarkets", "prepare"), ("activateLaunch", "activate"),
                           ("cancelLaunch", "cancel")):
            selector, types = function_signature(name)
            if calldata[:4] == selector:
                return kind, abi_decode(types, calldata[4:])
        if calldata[:4] == keccak(text="approve(address,uint256)")[:4]:
            return "approval", ()
        raise AssertionError("unexpected modeled lifecycle command")

    def logs(self, transaction):
        kind, arguments = self.command(transaction)
        launch_id = launch_id_of(self.plan)
        predicted = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
        logs = []

        def add(name, values):
            logs.append(event_log(name, values, target=self.plan.orchestrator, index=len(logs)))

        if kind in {"begin", "atomic"}:
            add("LaunchBegun", {"launchId": launch_id, "planHash": hash_launch_plan(self.plan),
                "creator": self.plan.creator, "token": predicted, "feeHub": ZERO,
                "rewards": ZERO, "mode": 0 if kind == "atomic" else 1})
        if kind in {"prepare", "atomic"}:
            first, count = (0, len(self.plan.markets)) if kind == "atomic" else arguments[1:]
            for index in range(first, first + count):
                add("MarketPrepared", {"launchId": launch_id, "marketIndex": index,
                    "canonicalId": "0x" + f"{index + 1:064x}", "adapter": ZERO,
                    "feeSource": ZERO, "positionCount": 1})
        if kind in {"activate", "atomic"}:
            for index, buy in enumerate(self.plan.buys):
                add("InitialBuyExecuted", {"launchId": launch_id, "buyIndex": index,
                    "marketIndex": buy.market_index, "quoteAsset": self.plan.markets[buy.market_index].quote_asset,
                    "quoteSpent": buy.quote_amount_in, "tokenOut": buy.min_token_out, "recipient": buy.recipient})
            add("LaunchActivated", {"launchId": launch_id, "planHash": hash_launch_plan(self.plan),
                "token": predicted, "marketCount": len(self.plan.markets), "positionCount": len(self.plan.markets)})
        if kind == "cancel":
            add("LaunchCancelled", {"launchId": launch_id, "creator": self.plan.creator,
                "inventoryBurned": self.plan.token.burn_on_cancel})
        return logs

    def make_request(self, method, params):
        self.operations.append(method)
        if method == "eth_getBlockByNumber" and self.plan.chain_id == 4663:
            response = super().make_request(method, params)
            response["result"]["gasLimit"] = hex(1 << 50)
            return response
        if method == "eth_getBalance":
            return {"result": hex(self.balance)}
        if method == "eth_getCode":
            code = "0x01" if self.smart_account and params[0].lower() == self.plan.creator.lower() else "0x"
            return {"result": code}
        if method == "eth_getTransactionCount":
            return {"result": hex(self.pending_nonce if params[1] == "pending" else 0)}
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        if method == "eth_call":
            transaction, tag = params
            assert tag == "0x33"
            target = transaction["to"].lower()
            if target in {"0x" + "00" * 19 + "64", "0x" + "00" * 19 + "6c"}:
                if self.getter_override is not None:
                    return self.getter_override
                selector = transaction["data"]
                values = {keccak(text="arbOSVersion()")[:4].hex(): self.raw_version,
                          keccak(text="getMaxTxGasLimit()")[:4].hex(): self.tx_cap,
                          keccak(text="getMaxBlockGasLimit()")[:4].hex(): self.block_cap}
                return {"result": "0x" + abi_encode(["uint256"], [values[selector[2:]]]).hex()}
            if target == "0x" + "00" * 19 + "c8":
                self.poster_requests.append(transaction)
                if self.poster_override is not None:
                    return self.poster_override
                destination, creation, calldata = abi_decode(["address", "bool", "bytes"], bytes.fromhex(transaction["data"][10:]))
                assert destination.lower() == self.plan.orchestrator.lower()
                assert creation is False and calldata
                assert transaction["from"].lower() == self.plan.creator.lower()
                return {"result": "0x" + abi_encode(["uint64", "uint256", "uint256"], [self.poster_gas, 1, 0]).hex()}
        if method == "eth_simulateV1":
            payload, tag = params
            assert tag == "0x33"
            blocks = payload["blockStateCalls"]
            if len(blocks) == 1 and len(blocks[0]["calls"]) == 3:
                self.probes.append(payload)
                if self.probe_failure == "unsupported":
                    return {"error": {"code": -32601, "message": "native Nitro simulation unavailable"}}
                if self.probe_failure == "incomplete":
                    return {"result": [{"calls": []}]}
                values = (self.raw_version, self.tx_cap, self.block_cap)
                if self.probe_failure == "mismatch":
                    values = (self.raw_version, self.tx_cap - 1, self.block_cap)
                return {"result": [{"calls": [
                    {"status": "0x1", "returnData": "0x" + abi_encode(["uint256"], [value]).hex()}
                    for value in values
                ]}]}
            self.simulations.append(payload)
            assert all("stateOverrides" not in block for block in blocks)
            if payload["validation"] and self.replay_failure == "unsupported":
                return {"error": {"code": -32601, "message": "exact validation unsupported"}}
            results = []
            for block in blocks:
                assert len(block["calls"]) == 1
                transaction = block["calls"][0]
                kind, arguments = self.command(transaction)
                compute = self.compute_gas_by_kind.get(kind, self.compute_gas)
                if kind == "prepare" and self.prepare_gas_per_market is not None:
                    compute = self.prepare_gas_per_market * arguments[2]
                gas_used = compute + (self.actual_poster_gas if self.plan.chain_id == 4663 and payload["validation"] else 0)
                requested = int(transaction["gas"], 16)
                logs = self.logs(transaction)
                success = gas_used <= requested
                error = None if success else {"message": "out of gas"}
                if payload["validation"] and self.replay_failure == "revert":
                    success, error, logs = False, {"message": "economic revert"}, []
                if payload["validation"] and self.replay_failure == "missing-activation":
                    logs = [log for log in logs if log["topics"][0] != "0x" + keccak(text="LaunchActivated(bytes32,bytes32,address,uint32,uint32)").hex()]
                results.append({"calls": [{"status": hex(int(success)), "gasUsed": hex(min(gas_used, requested)),
                    "maxUsedGas": hex(min(gas_used, requested)), "returnData": "0x",
                    "logs": logs, "error": error}]})
            if payload["validation"] and self.replay_failure == "incomplete":
                results.pop()
            return {"result": results}
        return super().make_request(method, params)


def admission_fixture(*, nitro=False, policy=None, kind="cancel", **scenario_options):
    request = replace(plan(), chain_id=4663) if nitro else plan()
    provider = AdmissionRpcScenario(request, **scenario_options)
    client = SimpleNamespace(provider=provider)
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, (1 << 50) if nitro else 30_000_000, 1)
    limits = _resolve_limits(client, policy, block, request.creator, request.chain_id, request.orchestrator)
    command = {"cancel": "cancelLaunch", "atomic": "launchAtomic"}[kind]
    transaction = LifecycleTransaction(f"0:{kind}", kind, request.chain_id, request.creator,
        request.orchestrator, build_lifecycle_calldata(request, command), 0, 0, (), (kind,),
        gas_limit=min(limits.compute_cap(block), limits.gas_cap(block)), gas_price=1)
    return request, provider, client, block, limits, transaction


def simulate_fixture(fixture, *, data_fee_estimator=None, fork=None):
    request, provider, client, block, limits, transaction = fixture
    predicted = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
    return _simulate_sequence(client, request, (transaction,), block=block, predicted=predicted,
                              limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)


@pytest.mark.parametrize("kind", ["cancel", "atomic"])
def test_complete_execution_has_separate_proof_protocol_and_transport_outcomes(kind):
    fixture = admission_fixture(kind=kind)
    result = simulate_fixture(fixture)
    assert result.admitted and result.execution_proof == result.protocol_fit == "proved"
    assert result.transport_preflight == "not-requested"
    assert result.account.lower() == fixture[0].creator.lower() and result.chain_id == fixture[0].chain_id
    admission = result.transactions[0].admission
    assert admission.admitted and admission.block_number == 51 and admission.block_hash == BLOCK_HASH
    assert admission.execution_proof == admission.protocol_fit == "proved"
    assert admission.transport_preflight == "not-requested"
    assert [payload["validation"] for payload in fixture[1].simulations] == [False, True]
    assert int(fixture[1].simulations[1]["blockStateCalls"][0]["calls"][0]["gas"], 16) == 115_000


@pytest.mark.parametrize("failure", ["revert", "missing-activation", "unsupported", "incomplete"])
def test_permissive_measurement_alone_never_proves_complete_exact_replay(failure):
    fixture = admission_fixture(kind="atomic", replay_failure=failure)
    if failure == "incomplete":
        with pytest.raises(RuntimeError, match="incomplete block sequence"):
            simulate_fixture(fixture)
        return
    result = simulate_fixture(fixture)
    assert not result.admitted and result.protocol_fit == "unknown"
    assert result.execution_proof == ("unavailable" if failure == "unsupported" else "failed")


@pytest.mark.parametrize("cap_name", ["chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit"])
def test_known_envelope_policy_only_tightens_replay(cap_name):
    fixture = admission_fixture(policy=LaunchExecutionLimits(**{cap_name: 110_000}))
    result = simulate_fixture(fixture)
    assert not result.admitted and result.protocol_fit == "failed"
    assert result.execution_proof == "unavailable"
    assert len(fixture[1].simulations) == 1


@pytest.mark.parametrize("balance,admitted", [(115_009, True), (115_008, False)])
def test_generic_external_data_fee_is_added_once_and_affordability_is_exact(balance, admitted):
    fixture = admission_fixture(balance=balance)
    result = simulate_fixture(fixture, data_fee_estimator=lambda *_: 9)
    assert result.admitted is admitted
    assert result.execution_proof == result.protocol_fit == "proved"
    transaction = result.transactions[0]
    assert transaction.maximum_execution_fee == 115_000 and transaction.data_fee == 9
    assert transaction.total_fee == 115_009 and not transaction.data_fee_included_in_gas
    assert "data_fee" not in result.unknown_constraints


def test_nitro_compute_and_actual_poster_data_are_separate_without_fee_double_charge():
    fixture = admission_fixture(nitro=True, compute_gas=20_000_000, poster_gas=10_000_000,
                                actual_poster_gas=13_000_000, balance=34_500_000)

    def must_not_add_external_fee(*_):
        raise AssertionError("Nitro gas already includes the poster fee")

    result = simulate_fixture(fixture, data_fee_estimator=must_not_add_external_fee)
    assert result.admitted and result.protocol_fit == result.execution_proof == "proved"
    transaction = result.transactions[0]
    assert transaction.compute_gas_estimate == 20_000_000
    assert transaction.gas_limit == 34_500_000 > fixture[4].execution_gas_ceiling
    assert transaction.gas_used == 33_000_000 > fixture[4].execution_gas_ceiling
    assert transaction.poster_gas == transaction.poster_fee == 10_000_000
    assert transaction.data_fee == 0 and transaction.data_fee_included_in_gas
    assert transaction.total_fee == transaction.maximum_execution_fee == 34_500_000
    assert fixture[4].arb_os_version == 50 and fixture[4].max_tx_compute_gas == 32_000_000
    assert fixture[4].max_block_compute_gas == 32_000_000
    assert fixture[4].transaction_gas_ceiling is None
    assert "chain_transaction_gas_limit" not in result.unknown_constraints and "data_fee" not in result.unknown_constraints
    probe = fixture[1].probes[0]
    assert probe["validation"] is True
    assert int(probe["blockStateCalls"][0]["stateOverrides"][fixture[0].creator]["balance"], 16) == 2**256 - 1
    assert all("baseFeePerGas" not in block["blockOverrides"] for payload in fixture[1].simulations for block in payload["blockStateCalls"])
    assert len(fixture[1].poster_requests) == 1


@pytest.mark.parametrize("balance,admitted", [(115_000, True), (114_999, False)])
def test_nitro_exactly_funded_small_cancellation_does_not_need_probe_funds(balance, admitted):
    result = simulate_fixture(admission_fixture(nitro=True, balance=balance))
    assert result.admitted is admitted
    assert result.execution_proof == result.protocol_fit == "proved"


def test_nitro_known_total_envelope_cap_is_not_a_compute_cutoff():
    policy = LaunchExecutionLimits(account_transaction_gas_limit=34_000_000)
    fixture = admission_fixture(nitro=True, policy=policy, compute_gas=20_000_000, poster_gas=10_000_000)
    result = simulate_fixture(fixture)
    assert not result.admitted and result.protocol_fit == "failed"
    assert fixture[4].execution_gas_ceiling == 32_000_000
    assert fixture[4].transaction_gas_ceiling == 34_000_000


@pytest.mark.parametrize("failure", ["unsupported", "incomplete", "mismatch"])
def test_nitro_non_native_or_mismatched_backend_is_explicitly_unavailable(failure):
    fixture = admission_fixture(nitro=True, probe_failure=failure)
    result = simulate_fixture(fixture)
    assert not result.admitted and result.execution_proof == "unavailable" and result.protocol_fit == "unknown"
    assert fixture[1].simulations == [] and fixture[1].poster_requests == []


@pytest.mark.parametrize("override", [
    {"error": {"code": -32000, "message": "historical state unavailable"}},
    {"result": "0x"}, {"result": "0x" + "00" * 64},
])
def test_nitro_cap_read_failure_cannot_fall_back_to_huge_header(override):
    request = replace(plan(), chain_id=4663)
    provider = AdmissionRpcScenario(request)
    provider.getter_override = override
    client = SimpleNamespace(provider=provider)
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 1 << 50, 1)
    with pytest.raises((ValueError, RuntimeError)):
        _resolve_limits(client, None, block, request.creator, request.chain_id, request.orchestrator)
    assert provider.simulations == [] and provider.probes == []


@pytest.mark.parametrize("options", [{"raw_version": 104}, {"tx_cap": 0}, {"block_cap": 0}, {"block_cap": 2**64}])
def test_nitro_getter_values_are_validated_and_version_offset_is_not_ignored(options):
    with pytest.raises((ValueError, RuntimeError, DecodingError)):
        admission_fixture(nitro=True, **options)


@pytest.mark.parametrize("override", [
    {"error": {"code": -32000, "message": "historical poster state unavailable"}},
    {"result": "0x"}, {"result": "0x" + abi_encode(["uint64", "uint256", "uint256"], [1, 0, 0]).hex()},
    {"result": "0x" + abi_encode(["uint256", "uint256", "uint256"], [2**64, 1, 0]).hex()},
])
def test_nitro_poster_budget_must_be_pinned_decoded_and_available(override):
    fixture = admission_fixture(nitro=True)
    fixture[1].poster_override = override
    with pytest.raises((ValueError, RuntimeError, DecodingError)):
        simulate_fixture(fixture)
    assert len(fixture[1].simulations) == 1


def test_nitro_generic_controlled_fork_cannot_claim_native_compute_proof():
    fixture = admission_fixture(nitro=True)
    fork_client = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="http://127.0.0.1:19999"))
    fork = ControlledLaunchFork(client=fork_client, isolated=True)
    result = simulate_fixture(fixture, fork=fork)
    assert not result.admitted and result.execution_proof == "unavailable"
    assert "cannot prove native Nitro" in result.reasons[0]
    assert fixture[1].probes == [] and fixture[1].simulations == []


class SubmissionRpcScenario:
    def __init__(self, chain_id, *, estimate=115_000, changed_chain=None, rpc_error=False):
        self.chain_id = chain_id
        self.estimate = estimate
        self.changed_chain = changed_chain
        self.rpc_error = rpc_error
        self.requests = []

    def make_request(self, method, params):
        self.requests.append((method, params))
        if method == "eth_chainId":
            chain = self.changed_chain if self.changed_chain is not None and len(self.requests) > 1 else self.chain_id
            return {"result": hex(chain)}
        if method == "eth_estimateGas":
            if self.rpc_error:
                return {"error": {"code": -32000, "message": "submission RPC gas cap refused"}}
            return {"result": hex(self.estimate)}
        raise AssertionError("submission preflight must not sign, broadcast or estimate dependent future steps")


def wire_planning(monkeypatch, *, scenario_options=None):
    import black_market_sdk.lifecycle as lifecycle

    request = plan()
    provider = AdmissionRpcScenario(request, **(scenario_options or {}))
    client = SimpleNamespace(provider=provider)
    predicted = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
    monkeypatch.setattr(lifecycle, "_live_admission", lambda *_: (predicted, ZERO, (), ()))
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", lambda *_: (ZERO, ZERO_HASH))
    return lifecycle, request, provider, client


def test_build_next_preflight_is_read_only_exact_and_immediate_only(monkeypatch):
    lifecycle, request, provider, client = wire_planning(monkeypatch)
    submission = SubmissionRpcScenario(request.chain_id)
    step = build_next_transaction(client, request, account=request.creator, mode="staged",
                                  submission_client=SimpleNamespace(provider=submission))
    assert step.kind == "begin" and step.admission.transport_preflight == "passed"
    assert [method for method, _ in submission.requests] == ["eth_chainId", "eth_estimateGas", "eth_chainId"]
    envelope, tag = submission.requests[1][1]
    assert tag == "latest" and envelope == {
        "from": step.from_address, "to": step.to, "data": step.data,
        "value": hex(step.value), "gas": hex(step.gas_limit), "gasPrice": hex(step.gas_price),
    }
    assert all(method not in {"eth_sendTransaction", "eth_sendRawTransaction"} for method in provider.operations)


@pytest.mark.parametrize("options", [
    {"estimate": 115_001}, {"estimate": 0}, {"changed_chain": 31338}, {"rpc_error": True},
])
def test_submission_refusal_does_not_erase_proved_execution_admission(monkeypatch, options):
    from black_market_sdk import LaunchSubmissionPreflightError

    _, request, _, client = wire_planning(monkeypatch)
    submission = SubmissionRpcScenario(request.chain_id, **options)
    with pytest.raises(LaunchSubmissionPreflightError) as failure:
        build_next_transaction(client, request, account=request.creator, mode="atomic",
                               submission_client=SimpleNamespace(provider=submission))
    error = failure.value
    assert error.code == "SUBMISSION_PREFLIGHT_FAILED"
    assert error.simulation.admitted and error.simulation.execution_proof == error.simulation.protocol_fit == "proved"
    assert error.simulation.transport_preflight == "failed"


def test_wrong_submission_chain_never_estimates_an_obsolete_intent(monkeypatch):
    from black_market_sdk import LaunchSubmissionPreflightError

    _, request, _, client = wire_planning(monkeypatch)
    submission = SubmissionRpcScenario(request.chain_id + 1)
    with pytest.raises(LaunchSubmissionPreflightError, match="different chain"):
        build_next_transaction(client, request, account=request.creator, mode="atomic",
                               submission_client=SimpleNamespace(provider=submission))
    assert [method for method, _ in submission.requests] == ["eth_chainId"]


def test_no_submission_client_keeps_transport_scope_not_requested(monkeypatch):
    _, request, _, client = wire_planning(monkeypatch)
    step = build_next_transaction(client, request, account=request.creator, mode="atomic")
    assert step.admission.transport_preflight == "not-requested"


def test_deliberate_cancellation_preflights_without_disabled_registry_admission(monkeypatch):
    _, request, _, client = wire_planning(monkeypatch, scenario_options={"latest_phase": 1, "confirmed_phase": 1})

    def forbidden(*_):
        raise AssertionError("cancellation must not require a still-enabled registry")

    import black_market_sdk.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "_live_admission", forbidden)
    submission = SubmissionRpcScenario(request.chain_id)
    step = build_next_transaction(client, request, account=request.creator, mode="staged", action="cancel",
                                  submission_client=SimpleNamespace(provider=submission))
    assert step.kind == "cancel" and step.admission.transport_preflight == "passed"


@pytest.mark.parametrize("options,reason", [
    ({"smart_account": True}, "contract-account"), ({"pending_nonce": 1}, "unresolved pending"),
])
def test_direct_eoa_and_settled_nonce_remain_required(monkeypatch, options, reason):
    lifecycle, request, provider, client = wire_planning(monkeypatch, scenario_options=options)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert not planned.admitted and planned.simulation.execution_proof == "unavailable"
    assert reason in planned.simulation.reasons[0] and provider.simulations == []


def test_staged_mode_only_partitions_empty_preparation_not_activation(monkeypatch):
    lifecycle, request, _, client = wire_planning(monkeypatch, scenario_options={"prepare_gas_per_market": 18_000_000})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.admitted and planned.mode == "staged"
    preparations = [transaction for transaction in planned.transactions if transaction.kind == "prepare"]
    assert len(preparations) == len(request.markets) and all(transaction.market_count == 1 for transaction in preparations)
    assert sum(transaction.kind == "activate" for transaction in planned.transactions) == 1
    assert planned.plan_hash == hash_launch_plan(request)


def test_atomic_mode_never_silently_falls_back_to_staged(monkeypatch):
    lifecycle, request, _, client = wire_planning(monkeypatch, scenario_options={"compute_gas": 27_000_000})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert not planned.admitted and planned.mode == "atomic"
    assert [transaction.kind for transaction in planned.transactions] == ["atomic"]


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_gas_policy_and_buffering_reject_non_uint_values(value):
    with pytest.raises(ValueError):
        LaunchExecutionLimits(chain_transaction_gas_limit=value)
    with pytest.raises(ValueError):
        LaunchExecutionLimits().buffered_gas(value)


def test_wide_internal_buffering_does_not_narrow_policy_provenance_or_fee_widths():
    exact = 2**256 - 1
    assert LaunchExecutionLimits().buffered_gas(exact) == (exact * 11500 + 9999) // 10000
    for value in (True, 1.5, "51", -1):
        with pytest.raises(ValueError):
            LaunchExecutionLimits(observed_block_number=value)
    for value in (True, 1.5, "31337", 0):
        with pytest.raises(ValueError):
            LaunchExecutionLimits(chain_id=value)


def test_reorg_during_simulation_cannot_leave_execution_admission():
    fixture = admission_fixture(reorganize_during_read=True)
    with pytest.raises(LaunchStateChanged, match="reorganized"):
        simulate_fixture(fixture)


def test_gross_requirement_not_refund_reduced_gas_usage_sets_reviewed_envelope(monkeypatch):
    fixture = admission_fixture()
    provider = fixture[1]
    original = provider.make_request

    def response(method, params):
        result = original(method, params)
        if method == "eth_simulateV1" and not params[0]["validation"]:
            result["result"][0]["calls"][0]["gasUsed"] = hex(50_000)
            result["result"][0]["calls"][0]["maxUsedGas"] = hex(100_000)
        return result

    monkeypatch.setattr(provider, "make_request", response)
    result = simulate_fixture(fixture)
    assert result.admitted
    assert result.transactions[0].gas_estimate == 100_000
    assert result.transactions[0].gas_limit == 115_000


@pytest.mark.parametrize("malformed", [
    {"gasUsed": hex(30_000_001)}, {"maxUsedGas": hex(99_999)}, {"maxUsedGas": hex(30_000_001)},
])
def test_simulation_gas_observations_must_fit_the_exact_requested_envelope(monkeypatch, malformed):
    fixture = admission_fixture()
    provider = fixture[1]
    original = provider.make_request

    def response(method, params):
        result = original(method, params)
        if method == "eth_simulateV1":
            result["result"][0]["calls"][0].update(malformed)
        return result

    monkeypatch.setattr(provider, "make_request", response)
    with pytest.raises(RuntimeError, match="outside the exact requested envelope"):
        simulate_fixture(fixture)


@pytest.mark.parametrize("policy", [
    LaunchExecutionLimits(max_calldata_bytes=1),
    LaunchExecutionLimits(account_max_calldata_bytes=1),
    LaunchExecutionLimits(rpc_max_request_bytes=1),
    LaunchExecutionLimits(rpc_total_simulation_gas_limit=1),
])
def test_known_calldata_and_rpc_payload_constraints_are_enforced(policy):
    result = simulate_fixture(admission_fixture(policy=policy))
    assert not result.admitted


def test_complete_final_activation_cannot_be_partitioned_after_headroom_refusal(monkeypatch):
    lifecycle, request, _, client = wire_planning(monkeypatch, scenario_options={"compute_gas_by_kind": {"activate": 27_000_000}})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert not planned.admitted and planned.mode == "staged"
    assert [transaction.kind for transaction in planned.transactions] == ["begin", "prepare", "activate"]
    assert planned.transactions[-1].data == build_lifecycle_calldata(request, "activateLaunch")
    assert planned.plan_hash == hash_launch_plan(request)


def test_active_source_chain_is_guarded_after_submission_preflight(monkeypatch):
    _, request, provider, client = wire_planning(monkeypatch)
    submission = SubmissionRpcScenario(request.chain_id)
    original = submission.make_request

    def response(method, params):
        result = original(method, params)
        if method == "eth_estimateGas":
            provider.plan = replace(provider.plan, chain_id=request.chain_id + 1)
        return result

    monkeypatch.setattr(submission, "make_request", response)
    with pytest.raises(ValueError, match="chain"):
        build_next_transaction(client, request, account=request.creator, mode="atomic",
                               submission_client=SimpleNamespace(provider=submission))


def test_nitro_headroom_rounds_compute_and_poster_budgets_separately():
    result = simulate_fixture(admission_fixture(nitro=True, compute_gas=10_000_001, poster_gas=1))
    assert result.admitted
    assert result.transactions[0].gas_estimate == 10_000_002
    assert result.transactions[0].gas_limit == 11_500_004


def test_zero_gas_price_remains_valid_when_actual_backend_accepts_exact_replay():
    fixture = admission_fixture(balance=0)
    fixture = (*fixture[:-1], replace(fixture[-1], gas_price=0))
    result = simulate_fixture(fixture)
    assert result.admitted and result.transactions[0].maximum_execution_fee == result.transactions[0].total_fee == 0


@pytest.mark.parametrize("name", ["max_calldata_bytes", "account_max_calldata_bytes", "rpc_max_request_bytes"])
def test_byte_count_policy_cannot_cross_matching_safe_integer_boundary(name):
    with pytest.raises(ValueError, match="safe integer"):
        LaunchExecutionLimits(**{name: 2**53})


@pytest.mark.parametrize("nitro,phase", [
    (False, "measurement"), (False, "replay"), (False, "affordability"),
    (True, "probe"), (True, "measurement"), (True, "replay"), (True, "affordability"),
])
def test_source_chain_drift_with_unchanged_block_hash_cannot_complete_admission(monkeypatch, nitro, phase):
    fixture = admission_fixture(nitro=nitro)
    request, provider, _, _, _, _ = fixture
    original = provider.make_request
    drifted = False

    def response(method, params):
        nonlocal drifted
        result = original(method, params)
        if method == "eth_chainId" and drifted:
            return {"result": hex(request.chain_id + 1)}
        if method == "eth_simulateV1":
            payload = params[0]
            probe = len(payload["blockStateCalls"]) == 1 and len(payload["blockStateCalls"][0]["calls"]) == 3
            current_phase = "probe" if probe else "replay" if payload["validation"] else "measurement"
            if current_phase == phase:
                drifted = True
        elif method == "eth_getBalance" and phase == "affordability":
            drifted = True
        if method == "eth_getBlockByNumber":
            assert result["result"]["hash"] == BLOCK_HASH
        return result

    monkeypatch.setattr(provider, "make_request", response)
    with pytest.raises(LaunchStateChanged, match="source chain changed") as failure:
        simulate_fixture(fixture)
    assert failure.value.code == "CHAIN_MISMATCH"
    assert drifted


@pytest.mark.parametrize("headroom", [0, 1500, 2000, 10_000])
def test_matching_headroom_boundaries_preserve_exact_custom_arithmetic(headroom):
    limits = LaunchExecutionLimits(headroom_bps=headroom)
    exact = 2**63 + 7
    assert limits.buffered_gas(exact) == (exact * (10_000 + headroom) + 9999) // 10_000


@pytest.mark.parametrize("headroom", [-1, True, 1.5, 10_001, 2**53 - 1])
def test_headroom_cannot_exceed_matching_zero_to_one_hundred_percent_range(headroom):
    with pytest.raises(ValueError, match="from 0 to 10000"):
        LaunchExecutionLimits(headroom_bps=headroom)


@pytest.mark.parametrize("name", [
    "chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit",
    "rpc_total_simulation_gas_limit", "execution_gas_ceiling", "transaction_gas_ceiling",
    "max_tx_compute_gas", "max_block_compute_gas",
])
def test_transaction_and_compute_cap_boundaries_are_positive_uint64(name):
    assert getattr(LaunchExecutionLimits(**{name: 2**64 - 1}), name) == 2**64 - 1
    for value in (0, -1, True, 1.5, 2**64, 2**256):
        with pytest.raises(ValueError, match="positive uint64"):
            LaunchExecutionLimits(**{name: value})


def test_header_gas_limit_is_positive_uint64_without_narrowing_other_quantities():
    block = LaunchBlock(2**64 + 1, BLOCK_HASH, 100, 2**64 - 1, 2**128)
    assert block.gas_limit == 2**64 - 1 and block.base_fee_per_gas == 2**128
    for value in (0, -1, True, 1.5, 2**64, 2**256):
        with pytest.raises(ValueError, match="positive uint64"):
            LaunchBlock(51, BLOCK_HASH, 100, value, 1)
    limits = LaunchExecutionLimits(chain_id=2**128, arb_os_version=2**128, observed_block_number=2**128)
    assert limits.chain_id == limits.arb_os_version == limits.observed_block_number == 2**128


@pytest.mark.parametrize("gas", [0, 2**64, 2**256])
def test_discovery_cannot_request_a_non_uint64_physical_gas_envelope(gas):
    fixture = admission_fixture()
    with pytest.raises(ValueError, match="positive uint64"):
        simulate_fixture((*fixture[:-1], replace(fixture[-1], gas_limit=gas)))


def test_nitro_reviewed_compute_plus_poster_envelope_cannot_overflow_uint64():
    fixture = admission_fixture(nitro=True, poster_gas=2**64 - 1)
    result = simulate_fixture(fixture)
    assert not result.admitted and result.execution_proof == "unavailable" and result.protocol_fit == "failed"
    assert len(fixture[1].simulations) == 1


@pytest.mark.parametrize("gas", [0, 2**64, 2**256, True, 1.5])
def test_submission_preflight_rejects_non_uint64_reviewed_gas_without_estimation(gas):
    from black_market_sdk import LaunchSubmissionPreflightError
    from black_market_sdk.lifecycle import _submission_preflight

    fixture = admission_fixture()
    simulation = simulate_fixture(fixture)
    step = replace(simulation.transactions[0], gas_limit=gas)
    submission = SubmissionRpcScenario(fixture[0].chain_id)
    with pytest.raises(LaunchSubmissionPreflightError):
        _submission_preflight(SimpleNamespace(provider=submission), step, simulation)
    assert [method for method, _ in submission.requests] == ["eth_chainId"]


@pytest.mark.parametrize("nitro", [False, True])
def test_zero_metering_cannot_create_a_reviewed_transaction_or_claim_execution_proof(nitro):
    fixture = admission_fixture(nitro=nitro, compute_gas=0, poster_gas=0)
    result = simulate_fixture(fixture)
    assert not result.admitted
    assert result.execution_proof == "unavailable" and result.protocol_fit == "failed"
    assert len(fixture[1].simulations) == 1
