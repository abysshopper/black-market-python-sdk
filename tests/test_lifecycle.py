"""Consumer-visible commitments, planning validation and canonical recovery.

Portable vectors are shared with Node. Recording RPC fixtures exercise reads
and deterministic capacity assessments, never real AMM execution.
"""

from __future__ import annotations

import json
from dataclasses import fields, replace
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
    ABYSS_FEE_TIERS,
    LAUNCH_DIRECTORY_V1_ABI,
    LAUNCH_MARKET_ADAPTER_V1_ABI,
    LaunchBlock,
    LaunchExecutionLimits,
    LaunchStateChanged,
    NITRO_ARB_SYS_ADDRESS,
    LifecyclePhase,
    LifecycleTransaction,
    LifecycleLaunchPostcondition,
    LifecycleReceiptReference,
    LifecyclePoolBoundV4MarketConfigV6,
    decode_lifecycle_abyss_market_config,
    encode_lifecycle_abyss_market_config,
    decode_lifecycle_launch_receipt,
    build_lifecycle_calldata,
    build_next_transaction,
    decode_lifecycle_events,
    encode_launch_plan,
    decode_lifecycle_v4_market_config,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_v4_market_config,
    encode_lifecycle_pool_bound_v4_market_config,
    hash_launch_plan,
    launch_id_of,
    launch_plan_from_dict,
    launch_plan_to_dict,
    read_launch_progress,
    to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import canonical_abi_type
from black_market_sdk.lifecycle import _resolve_limits, _validate_launch_plan
from black_market_sdk.lifecycle_abis import LAUNCH_PROGRESS_COMPONENTS_V1, LAUNCH_RECEIPT_COMPONENTS_V1
from black_market_sdk.lifecycle import _tuple_type


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


def historical_plan():
    """Keep the independent ABI/hash commitment untouched, not execution-valid."""
    return launch_plan_from_dict(vector()["plan"])


def plan(*, supply=None):
    """Derive full-budget permanent tails without editing golden wire vectors."""
    request = historical_plan()
    supply = request.token.supply if supply is None else supply
    budgets = (supply // 2, supply - supply // 2)
    v4 = decode_lifecycle_v4_market_config(request.markets[0].config)
    abyss = decode_lifecycle_abyss_market_config(request.markets[1].config)
    v4 = replace(v4, positions=(replace(v4.positions[0], tick_lower=-887220,
        tick_upper=0, max_token_amount=budgets[0]),))
    abyss = replace(abyss, positions=(replace(abyss.positions[0], tick_lower=-887220,
        tick_upper=0, token_amount_maximum=budgets[1]),))
    return replace(request, token=replace(request.token, supply=supply), markets=(
        replace(request.markets[0], token_budget=budgets[0], config=encode_lifecycle_v4_market_config(v4)),
        replace(request.markets[1], token_budget=budgets[1], config=encode_lifecycle_abyss_market_config(abyss)),
    ))


def test_current_planner_allocation_has_no_inventory_reserve_or_position_haircut():
    request = plan()
    _validate_launch_plan(request)
    assert sum(market.token_budget for market in request.markets) == request.token.supply
    for changed in (
        replace(request, token=replace(request.token, supply=request.token.supply + 1)),
        replace(request, markets=(replace(request.markets[0], token_budget=request.markets[0].token_budget - 1),
            replace(request.markets[1], token_budget=request.markets[1].token_budget + 1))),
    ):
        with pytest.raises(ValueError) as failure:
            _validate_launch_plan(changed)
        assert failure.value.code == "INVALID_TOKEN_BUDGET"


def test_launch_receipt_is_a_typed_exact_ordered_result():
    request = plan()
    provider = RecordingRpcScenario(request)
    transaction = {"data": build_lifecycle_calldata(request, "launchAtomic")}
    receipt = decode_lifecycle_launch_receipt(provider.return_data(transaction), request)
    assert receipt.launch_id == launch_id_of(request)
    assert receipt.plan_hash == hash_launch_plan(request)
    assert receipt.market_count == receipt.position_count == len(request.markets)
    assert receipt.fee_hub == receipt.rewards == ZERO
    assert receipt.quote_spent == tuple(buy.quote_amount_in for buy in request.buys)
    assert receipt.token_out == tuple(buy.min_token_out for buy in request.buys)
    assert receipt.token.lower() == vector().get("predictedToken", "0x1000000000000000000000000000000000000001").lower()

def function_signature(name, abi=LAUNCH_LIFECYCLE_V1_ABI):
    entry = next(item for item in abi if item.get("name") == name and item["type"] == "function")
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
    fixture, request = vector(), historical_plan()
    expected_hash = fixture.get("planHash", fixture.get("hashPlan"))
    assert hash_launch_plan(request) == expected_hash.lower()
    assert launch_id_of(request) == fixture["launchId"].lower()
    assert "0x" + encode_launch_plan(request).hex() == fixture["encodedPlan"].lower()
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
    original = historical_plan()
    changed = change(original)
    assert hash_launch_plan(changed) != vector().get("planHash", vector().get("hashPlan")).lower()


@pytest.mark.parametrize("mode", [None, "", "auto", "native"])
def test_public_planner_requires_explicit_atomic_or_staged_before_rpc(mode):
    from black_market_sdk.lifecycle import plan_launch
    request = plan()
    def forbidden(*_):
        raise AssertionError("An implicit or unsupported mode must fail before RPC")
    client = SimpleNamespace(provider=SimpleNamespace(make_request=forbidden))
    with pytest.raises(ValueError) as failure:
        plan_launch(client, request, account=request.creator, mode=mode)
    assert failure.value.code == "EXPLICIT_MODE_REQUIRED"


def test_public_planner_rejects_historical_reserve_allocation_before_rpc():
    from black_market_sdk.lifecycle import plan_launch
    request = historical_plan()
    def forbidden(*_):
        raise AssertionError("An incomplete supply budget must fail before RPC")
    client = SimpleNamespace(provider=SimpleNamespace(make_request=forbidden))
    with pytest.raises(ValueError) as failure:
        plan_launch(client, request, account=request.creator, mode="atomic")
    assert failure.value.code == "INVALID_TOKEN_BUDGET"


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
    with pytest.raises(ValueError):
        build_lifecycle_calldata(request, "prepareMarkets", first_market=len(request.markets), count=1)
    with pytest.raises(ValueError):
        build_lifecycle_calldata(request, "multicall")


def test_json_rejects_lossy_amounts_and_invalid_fee_policy():
    data = launch_plan_to_dict(plan())
    data["token"]["supply"] = float(data["token"]["supply"])
    with pytest.raises(ValueError):
        launch_plan_from_dict(data)
    request = plan()
    with pytest.raises(ValueError):
        _validate_launch_plan(replace(request, fee_assets=tuple(reversed(request.fee_assets))))
    with pytest.raises(ValueError):
        _validate_launch_plan(replace(request, fee_assets=(replace(request.fee_assets[0], owner_bps=request.fee_assets[0].owner_bps + 1), *request.fee_assets[1:])))


def test_reusing_funding_or_supply_across_markets_is_rejected_per_asset():
    request = plan()
    excessive = replace(request.markets[0], token_budget=request.token.supply + 1)
    with pytest.raises(ValueError):
        _validate_launch_plan(replace(request, markets=(excessive, *request.markets[1:])))
    if request.buys:
        buy = request.buys[0]
        quote = request.markets[buy.market_index].quote_asset.lower()
        funding = next(item for item in request.funding if item.asset.lower() == quote)
        excessive = replace(buy, quote_amount_in=funding.amount + 1)
        with pytest.raises(ValueError):
            _validate_launch_plan(replace(request, buys=(excessive, *request.buys[1:])))


def test_receipt_events_cannot_be_rebound_to_another_launch_or_creator():
    request = plan()
    arguments = {"launchId": launch_id_of(request), "planHash": hash_launch_plan(request), "creator": request.creator, "token": vector().get("predictedToken", "0x1000000000000000000000000000000000000001"), "feeHub": "0x1000000000000000000000000000000000000002", "rewards": ZERO, "mode": 1}
    wrong_id = event_log("LaunchBegun", {**arguments, "launchId": ZERO_HASH})
    with pytest.raises(ValueError):
        decode_lifecycle_events(request, [wrong_id])
    wrong_hash = event_log("LaunchBegun", {**arguments, "planHash": ZERO_HASH})
    with pytest.raises(ValueError):
        decode_lifecycle_events(request, [wrong_hash])
    wrong_creator = event_log("LaunchBegun", {**arguments, "creator": "0x1000000000000000000000000000000000000009"})
    with pytest.raises(ValueError):
        decode_lifecycle_events(request, [wrong_creator])


class CanonicalRpcScenario:
    """Fault-injected canonical state, with no successful simulation backend."""

    def __init__(self, request, *, latest_phase=0, confirmed_phase=0, orphan=False, reorganize_during_read=False, timestamp=None):
        self.plan = request
        self.latest_phase = latest_phase
        self.confirmed_phase = confirmed_phase
        self.orphan = orphan
        self.reorganize_during_read = reorganize_during_read
        self.block_reads = 0
        self.block_number = 51
        self.block_hash = BLOCK_HASH
        self.base_fee_per_gas = 1
        self.timestamp = request.deadline - 100 if timestamp is None else timestamp

    def lens_snapshot(self, transaction, tag):
        from black_market_sdk import LAUNCH_LENS_ABI, LAUNCH_LENS_SNAPSHOT_COMPONENTS, LAUNCH_PLAN_COMPONENTS_V1
        from black_market_sdk.lifecycle import (
            _ERC20_READ_ABI, _encode_function, _json_tuple, _known_bound_deployment, _tuple_type,
            encode_pool_bound_hook_parameters, predict_pool_bound_hook_address,
        )
        entry = LAUNCH_LENS_ABI[0]
        types = [canonical_abi_type(item) for item in entry["inputs"]]
        values, owner, spender, tokens, attempts = abi_decode(types, bytes.fromhex(transaction["data"][10:]))
        request = launch_plan_from_dict(_json_tuple(LAUNCH_PLAN_COMPONENTS_V1, values))
        number = self.block_number if tag == "latest" else int(tag, 16)
        phase = self.latest_phase if number == self.block_number else self.confirmed_phase
        predicted = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
        balances = []
        for token in tokens:
            observations = []
            for name, args in (("balanceOf", [owner]), ("allowance", [owner, spender])):
                response = self.make_request("eth_call", [{"to": token,
                    "data": _encode_function(_ERC20_READ_ABI, name, args)}, tag])
                observations.append(abi_decode(["uint256"], bytes.fromhex(response["result"][2:]))[0])
            balances.append((token, *observations))
        hooks = []
        for index, market in enumerate(request.markets):
            if market.config_version not in (5, 6):
                continue
            deployment, parameters = _known_bound_deployment(request, index, predicted)
            salt, hook = int(deployment.salt, 16), deployment.predicted_hook
            found = False
            if attempts:
                for _ in range(attempts):
                    if int(hook, 16) & 0x3fff == 0x1afc:
                        found = True
                        break
                    salt += 1
                    hook = predict_pool_bound_hook_address(deployer=deployment.deployer,
                        init_code_hash=deployment.init_code_hash, salt=salt.to_bytes(32, "big"))
            hooks.append((index, parameters.registrar, deployment.deployer,
                encode_pool_bound_hook_parameters(parameters, config_version=market.config_version),
                bytes.fromhex(deployment.init_code_hash[2:]), salt.to_bytes(32, "big"),
                hook if found else ZERO, found))
        prepared = []
        if phase:
            for index in range(len(request.markets)):
                adapter, market = self.prepared_market(index)
                success = not getattr(self, "live_failure", False)
                prepared.append((index, adapter, market, success, (market[0][-1], 0, 0, False, 0),
                    b"" if success else b"unavailable"))
        balance = int(self.make_request("eth_getBalance", [owner, tag])["result"], 16)
        snapshot = (request.chain_id, number, bytes.fromhex(self.block_hash[2:]),
            self.timestamp, balance, balances, predicted, bytes.fromhex(launch_id_of(request)[2:]),
            self.progress(phase), prepared, hooks)
        return "0x" + abi_encode([_tuple_type(LAUNCH_LENS_SNAPSHOT_COMPONENTS)], [snapshot]).hex()

    def progress(self, phase):
        if phase == 0:
            return (b"\0" * 32, b"\0" * 32, ZERO, 0, 0, 0, ZERO, ZERO, ZERO, 0, 0, 0, 0, 0)
        prepared = len(self.plan.markets) if phase == 2 else 0
        return (bytes.fromhex(launch_id_of(self.plan)[2:]), bytes.fromhex(hash_launch_plan(self.plan)[2:]), self.plan.creator, self.plan.nonce, 1, phase, vector().get("predictedToken", "0x1000000000000000000000000000000000000001"), "0x1000000000000000000000000000000000000002", ZERO, prepared, len(self.plan.markets), len(self.plan.buys), 0, self.plan.deadline)

    def make_request(self, method, params):
        if method == "eth_getTransactionCount":
            result = "0x0"
        elif method == "eth_getTransactionByHash":
            result = {"from": self.plan.creator, "to": self.plan.orchestrator,
                "input": build_lifecycle_calldata(self.plan, "beginLaunch"), "value": "0x0"}
        elif method == "eth_chainId":
            result = hex(self.plan.chain_id)
        elif method == "eth_call" and params[0]["to"].lower() == "0xcb4bb3caca71fe52eace8170cf83e901418646f8":
            result = self.lens_snapshot(params[0], params[1])
        elif method == "eth_call" and params[0]["data"][:10] == "0x" + keccak(text="findMany((address,bytes32,uint256,uint160)[],uint256)")[:4].hex():
            searches, attempts = abi_decode(["(address,bytes32,uint256,uint160)[]", "uint256"], bytes.fromhex(params[0]["data"][10:]))
            salts, hooks, found = [], [], []
            for deployer, init_hash, start, flags in searches:
                for candidate in range(start, start + attempts):
                    digest = keccak(b"\xff" + bytes.fromhex(deployer[2:]) + candidate.to_bytes(32, "big") + init_hash)[-20:]
                    if int.from_bytes(digest, "big") & 0x3fff == flags:
                        break
                else:
                    candidate, digest = start + attempts, bytes(20)
                salts.append(candidate.to_bytes(32, "big"))
                hooks.append("0x" + digest.hex())
                found.append(digest != bytes(20))
            result = "0x" + abi_encode(["bytes32[]", "address[]", "bool[]"], [salts, hooks, found]).hex()
        elif method == "eth_call" and params[0]["to"].lower() == "0xca11bde05977b3631167028862be2a173976ca11" and params[0]["data"][:10] != "0x" + keccak(text="aggregate3((address,bool,bytes)[])")[:4].hex():
            data = bytes.fromhex(params[0]["data"][2:])
            assert data[:4] == keccak(text="getEthBalance(address)")[:4], "Nitro EVM block helpers are not L2 pins"
            account = abi_decode(["address"], data[4:])[0]
            balance = self.make_request("eth_getBalance", [account, params[1]])["result"]
            result = "0x" + abi_encode(["uint256"], [int(balance, 16)]).hex()
        elif method == "eth_call" and params[0]["to"].lower() == "0xca11bde05977b3631167028862be2a173976ca11":
            calls = abi_decode(["(address,bool,bytes)[]"], bytes.fromhex(params[0]["data"][10:]))[0]
            self._in_multicall = True
            try:
                results = []
                for target, allow_failure, data in calls:
                    response = self.make_request("eth_call", [{"to": target, "data": "0x" + data.hex()}, params[1]])
                    assert "error" not in response or allow_failure
                    results.append(("error" not in response, bytes.fromhex(response.get("result", "0x")[2:])))
                result = "0x" + abi_encode(["(bool,bytes)[]"], [results]).hex()
            finally:
                self._in_multicall = False
        elif method == "eth_getCode":
            result = (Path(__file__).with_name("fixtures") / "multicall3-robinhood-runtime.hex").read_text().strip() if params[0].lower() == "0xca11bde05977b3631167028862be2a173976ca11" else "0x"
        elif method == "eth_getBlockByNumber":
            self.block_reads += 1
            number = self.block_number if params[0] == "latest" else int(params[0], 16)
            block_hash = ORPHAN_HASH if self.reorganize_during_read and self.block_reads > 1 else self.block_hash
            result = {"number": hex(number), "hash": block_hash, "timestamp": hex(self.timestamp), "gasLimit": hex(30_000_000), "baseFeePerGas": hex(self.base_fee_per_gas)}
        elif method == "eth_call":
            data = bytes.fromhex(params[0]["data"][2:])
            selector, _ = function_signature("readLaunchProgress")
            if data[:4] == selector:
                phase = self.latest_phase if params[1] == hex(self.block_number) else self.confirmed_phase
                progress_entry = next(item for item in LAUNCH_LIFECYCLE_V1_ABI if item.get("name") == "readLaunchProgress")
                result = "0x" + abi_encode([canonical_abi_type(progress_entry["outputs"][0])], [self.progress(phase)]).hex()
            else:
                from black_market_sdk.lifecycle import _LIFECYCLE_TOKEN_PREDICTION_ABI
                selectors = (function_signature("predictToken")[0], function_signature("predictToken", _LIFECYCLE_TOKEN_PREDICTION_ABI)[0])
                assert data[:4] in selectors
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
    with pytest.raises(ValueError) as failure:
        build_next_transaction(client, request, account=request.creator, mode="staged", confirmations=2)
    assert failure.value.code == "UNCONFIRMED_STATE"


def test_orphaned_receipt_does_not_advance_canonical_progress_after_reload():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request, orphan=True))
    progress = read_launch_progress(client, request, receipts=[LifecycleReceiptReference(TRANSACTION_HASH)])
    assert progress.phase == LifecyclePhase.NONE
    assert progress.prepared_markets == 0
    assert progress.receipts[0].status == "reorged"
    assert progress.receipts[0].confirmations == 0


def test_wallet_switch_cannot_build_a_different_creators_funding_transaction():
    request = plan()
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    with pytest.raises(ValueError):
        build_next_transaction(client, request, account="0x1000000000000000000000000000000000000011", mode="staged")

















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
    with pytest.raises(ValueError):
        _resolve_limits(client, lambda rpc_client, context: change(limits), block, request.creator, request.chain_id, request.orchestrator)


@pytest.mark.parametrize("source", [lambda *_: None, lambda *_: {}, lambda *_: 1])
def test_supplied_policy_resolver_cannot_disappear_into_optional_absence(source):
    request = plan()
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, 30_000_000, 1)
    client = SimpleNamespace(provider=CanonicalRpcScenario(request))
    with pytest.raises(ValueError) as error:
        _resolve_limits(client, source, block, request.creator, request.chain_id, request.orchestrator)
    assert error.value.code == "INVALID_LIMITS"


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

    with pytest.raises(ValueError) as error:
        _resolve_limits(client, broken, block, request.creator, request.chain_id, request.orchestrator)
    assert error.value.code == "LIMIT_SOURCE_FAILED"


def test_ordered_repeat_market_buys_are_not_commutative_economics():
    request = historical_plan()
    changed = replace(request, buys=tuple(reversed(request.buys)))
    assert hash_launch_plan(changed) != vector()["planHash"]
    assert launch_id_of(changed) == vector()["launchId"]


@pytest.mark.parametrize("reward_mode", [1, 2])
def test_reward_enabled_erc20_supply_obeys_the_precision_boundary(reward_mode):
    request = plan(supply=10**77)
    token = replace(request.token, kind=0, reward_mode=reward_mode, nft_unit=0, metadata_uri="", supply=10**77)
    bounded = replace(request, token=token)
    _validate_launch_plan(bounded)
    assert to_launch_plan_tuple(bounded)[4][4] == 10**77
    with pytest.raises(ValueError):
        _validate_launch_plan(replace(bounded, token=replace(token, supply=10**77 + 1)))


def test_owner_only_erc20_preserves_full_uint256_supply():
    request = plan(supply=2**256 - 1)
    token = replace(request.token, kind=0, reward_mode=0, nft_unit=0, metadata_uri="", supply=2**256 - 1)
    policies = tuple(replace(policy, owner_bps=policy.owner_bps + policy.rewards_bps, rewards_bps=0) for policy in request.fee_assets)
    bounded = replace(request, token=token, fee_assets=policies)
    _validate_launch_plan(bounded)
    assert to_launch_plan_tuple(bounded)[4][4] == 2**256 - 1






class RecordingRpcScenario(CanonicalRpcScenario):
    """Wire-level read fixture; execution and broadcasts always fail."""

    def __init__(self, request, *, poster_gas=0, balance=10**24, smart_account=False,
                 pending_nonce=0, raw_version=105, tx_cap=32_000_000, block_cap=32_000_000,
                 l1_price_per_byte=None, **progress):
        super().__init__(request, **progress)
        self.gas_price = 1
        self.l1_price_per_byte = l1_price_per_byte
        self.poster_gas = poster_gas
        self.balance = balance
        self.smart_account = smart_account
        self.pending_nonce = pending_nonce
        self.raw_version = raw_version
        self.tx_cap = tx_cap
        self.block_cap = block_cap
        self.simulations = []
        self.probes = []
        self.poster_requests = []
        self.operations = []
        self.getter_override = None
        self.poster_override = None
        self.endpoint_uri = "https://reads.example"

    def position_count(self, market_index=None):
        count = 0
        markets = self.plan.markets if market_index is None else (self.plan.markets[market_index],)
        for market in markets:
            if market.config_version == 1:
                config = decode_lifecycle_abyss_market_config(market.config)
            elif market.config_version == 4:
                config = decode_lifecycle_v4_market_config(market.config)
            else:
                config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
            count += len(config.positions)
        return count

    def prepared_market(self, market_index):
        market = self.plan.markets[market_index]
        token = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
        currency0, currency1 = sorted((token, market.quote_asset), key=lambda value: int(value, 16))
        manager = "0x" + f"{0xc0:040x}"
        hook = "0x" + f"{0x1afc:040x}"
        if market.config_version == 1:
            config = decode_lifecycle_abyss_market_config(market.config)
            fee, spacing = config.fee, next(tier.tick_spacing for tier in ABYSS_FEE_TIERS if tier.fee_pips == config.fee)
            price, venue, factory, pool, pool_id = config.opening_sqrt_price_x96, 1, manager, manager, bytes(32)
        else:
            config = decode_lifecycle_v4_market_config(market.config) if market.config_version == 4 else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
            fee, spacing, price, venue, factory, pool = config.lp_fee_pips, config.tick_spacing, config.sqrt_price_x96, 0, ZERO, ZERO
            pool_id = keccak(abi_encode(["address", "address", "uint24", "int24", "address"],
                [currency0, currency1, fee, spacing, hook]))
        profile_id = bytes.fromhex(market.profile_id[2:]) if isinstance(market.profile_id, str) else market.profile_id
        canonical_id = keccak(abi_encode(["uint256", "uint8", "address", "address", "address", "bytes32", "bytes32"],
            [self.plan.chain_id, venue, manager, factory, pool, pool_id, profile_id]))
        identity = (venue, canonical_id, manager, factory, pool, pool_id, profile_id,
            currency0, currency1, fee, spacing, hook, price)
        adapter = "0x" + f"{0xc2:040x}"
        return adapter, (identity, manager, manager, adapter, adapter, (), len(config.positions))

    

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

    

    def return_data(self, transaction):
        kind, _ = self.command(transaction)
        predicted = vector().get("predictedToken", "0x1000000000000000000000000000000000000001")
        if kind == "begin":
            return "0x" + abi_encode([_tuple_type(LAUNCH_PROGRESS_COMPONENTS_V1)], [self.progress(1)]).hex()
        if kind in {"atomic", "activate"}:
            receipt = (bytes.fromhex(launch_id_of(self.plan)[2:]), bytes.fromhex(hash_launch_plan(self.plan)[2:]),
                predicted, ZERO, ZERO, len(self.plan.markets), self.position_count(),
                tuple(buy.quote_amount_in for buy in self.plan.buys),
                tuple(buy.min_token_out for buy in self.plan.buys))
            return "0x" + abi_encode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], [receipt]).hex()
        return "0x"

    def make_request(self, method, params):
        if not getattr(self, "_in_multicall", False):
            self.operations.append(method)
        if method == "eth_getBlockByNumber" and self.plan.chain_id == 4663:
            response = super().make_request(method, params)
            response["result"]["gasLimit"] = hex(1 << 50)
            return response
        if method == "eth_getBalance":
            return {"result": hex(self.balance)}
        if method == "eth_getCode":
            if params[0].lower() == "0xca11bde05977b3631167028862be2a173976ca11":
                return super().make_request(method, params)
            code = "0x01" if self.smart_account and params[0].lower() == self.plan.creator.lower() else "0x"
            return {"result": code}
        if method == "eth_getTransactionCount":
            return {"result": hex(self.pending_nonce if params[1] == "pending" else 0)}
        if method == "eth_gasPrice":
            return {"result": hex(self.gas_price)}
        if method == "eth_call":
            transaction, tag = params
            assert tag == "latest" or int(tag, 16) <= self.block_number
            target = transaction["to"].lower()
            if target == "0xca11bde05977b3631167028862be2a173976ca11":
                return super().make_request(method, params)
            if target in {"0x" + "00" * 19 + "64", "0x" + "00" * 19 + "6c"}:
                if self.getter_override is not None:
                    return self.getter_override
                selector = transaction["data"]
                if selector == "0x" + keccak(text="getPricesInWei()")[:4].hex():
                    price = self.l1_price_per_byte
                    if price is None:
                        # Legacy wire cases specify poster gas; expose a compatible
                        # per-byte observation instead of a transaction oracle.
                        size = (len(build_lifecycle_calldata(self.plan, "cancelLaunch")) - 2) // 2 + 384
                        bound = size + 8 * (size // 1024) + 64
                        price = (self.poster_gas * self.gas_price + 2 * bound - 1) // (2 * bound)
                    prices = (0, price, 0, self.gas_price, 0, self.gas_price)
                    return {"result": "0x" + abi_encode(["uint256"] * 6, prices).hex()}
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
            calldata = bytes.fromhex(transaction["data"][2:])
            if calldata[:4] == function_signature("directory")[0]:
                assert target == self.plan.orchestrator.lower()
                return {"result": "0x" + abi_encode(["address"], ["0x" + f"{0xc1:040x}"]).hex()}
            for name, abi in (("market", LAUNCH_DIRECTORY_V1_ABI), ("readMarket", LAUNCH_MARKET_ADAPTER_V1_ABI)):
                selector, types = function_signature(name, abi)
                if calldata[:4] != selector:
                    continue
                launch_id, index = abi_decode(types, calldata[4:])
                assert launch_id == bytes.fromhex(launch_id_of(self.plan)[2:])
                adapter, prepared = self.prepared_market(index)
                from black_market_sdk import get_known_lifecycle_deployment, get_launch_addresses
                preset = get_known_lifecycle_deployment(chain_id=self.plan.chain_id, orchestrator=self.plan.orchestrator)
                directory = get_launch_addresses(self.plan.chain_id).directory.lower() if preset is not None else "0x" + f"{0xc1:040x}"
                assert target == (directory if name == "market" else adapter)
                entry = next(item for item in abi if item.get("name") == name and item["type"] == "function")
                values = (adapter, prepared) if name == "market" else ((prepared[0][-1], 0, 0, False, 0),)
                return {"result": "0x" + abi_encode([canonical_abi_type(item) for item in entry["outputs"]], values).hex()}
        if method in {"eth_simulateV1", "eth_estimateGas", "eth_sendTransaction", "eth_sendRawTransaction"}:
            raise AssertionError("Lifecycle planning must not execute launch calldata or estimate a transaction")
        return super().make_request(method, params)


def read_fixture(*, nitro=False, policy=None, kind="cancel", **scenario_options):
    request = replace(plan(), chain_id=4663) if nitro else plan()
    provider = RecordingRpcScenario(request, **scenario_options)
    client = SimpleNamespace(provider=provider)
    block = LaunchBlock(51, BLOCK_HASH, request.deadline - 100, (1 << 50) if nitro else 30_000_000, 1)
    limits = _resolve_limits(client, policy, block, request.creator, request.chain_id, request.orchestrator)
    command = {"cancel": "cancelLaunch", "atomic": "launchAtomic"}[kind]
    transaction = LifecycleTransaction(f"0:{kind}", kind, request.chain_id, request.creator, request.orchestrator, build_lifecycle_calldata(request, command), 0, (), (LifecycleLaunchPostcondition("Cancelled" if kind == "cancel" else "Active", 0 if kind == "cancel" else len(request.markets)),), gas_limit=min(limits.compute_cap(block), limits.gas_cap(block)), gas_price=1)
    return request, provider, client, block, limits, transaction









































def wire_planning(monkeypatch, *, scenario_options=None, request=None):
    import black_market_sdk.lifecycle as lifecycle

    request = plan() if request is None else request
    provider = RecordingRpcScenario(request, **(scenario_options or {}))
    client = SimpleNamespace(provider=provider)
    # Wire-level source metadata is isolated here; profile binding is exercised
    # independently in test_lifecycle_admission rather than echoing a planner result.
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", lambda *_: ((), (), ()))
    monkeypatch.setattr(lifecycle, "_read_funding", lambda *_: ((), ()))
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", lambda *_: (ZERO, ZERO_HASH))
    return lifecycle, request, provider, client


def topology_plan(version, token0, *, intervals=None, fee=500):
    request = plan()
    market = request.markets[1 if version == 1 else 0]
    spacing = next(tier.tick_spacing for tier in ABYSS_FEE_TIERS if tier.fee_pips == fee) if version == 1 else 10 if version == 6 else 60
    boundary = 887272 // spacing * spacing
    intervals = [(0, boundary)] if intervals is None else intervals
    budget = market.token_budget
    positions = []
    for index, (lower, upper) in enumerate(intervals):
        lower, upper = (lower, upper) if token0 else (-upper, -lower)
        cap = budget - budget // len(intervals) * index if index == len(intervals) - 1 else budget // len(intervals)
        if version == 1:
            original = decode_lifecycle_abyss_market_config(market.config).positions[0]
            positions.append(replace(original, tick_lower=lower, tick_upper=upper, token_amount_maximum=cap))
        else:
            original = decode_lifecycle_v4_market_config(market.config).positions[0]
            positions.append(replace(original, tick_lower=lower, tick_upper=upper,
                salt=index.to_bytes(32, "big"), max_token_amount=cap))
    if version == 1:
        config = replace(decode_lifecycle_abyss_market_config(market.config), fee=fee,
            opening_sqrt_price_x96=1 << 96, positions=tuple(positions))
        encoded = encode_lifecycle_abyss_market_config(config)
    else:
        config = replace(decode_lifecycle_v4_market_config(market.config), tick_spacing=spacing,
            sqrt_price_x96=1 << 96, positions=tuple(positions))
        if version == 6:
            values = {field.name: getattr(config, field.name) for field in fields(config) if field.name != "version"}
            config = LifecyclePoolBoundV4MarketConfigV6(version=6, hook_salt=bytes(32),
                minimum_hook_fee_pips=0, fee_sensitivity_pips_seconds_per_tick=0, **values)
            encoded = encode_lifecycle_pool_bound_v4_market_config(config)
        else:
            encoded = encode_lifecycle_v4_market_config(config)
    market = replace(market, config_version=version, config=encoded,
        quote_asset="0x" + "ff" * 20 if token0 else market.quote_asset)
    return replace(request, token=replace(request.token, supply=budget), markets=(market,), buys=())


@pytest.mark.parametrize("version", [1, 4, 6])
@pytest.mark.parametrize("token0", [True, False])
def test_valid_legacy_topology_reaches_capacity_refusal_without_changing_commitment(monkeypatch, version, token0):
    lifecycle, request, _, client = wire_planning(monkeypatch, request=topology_plan(version, token0))
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE"
    assert not planned.allow_topology_gaps and not planned.allow_missing_permanent_tail
    assert planned.plan_hash == hash_launch_plan(request)
    assert planned.transactions[0].data == build_lifecycle_calldata(request, "beginLaunch")


@pytest.mark.parametrize("token0", [True, False])
def test_planner_admits_fee100_unit_tick_bands_and_protocol_boundary(monkeypatch, token0):
    request = topology_plan(1, token0, fee=100, intervals=[(0, 121), (121, 887272)])
    lifecycle, request, _, client = wire_planning(monkeypatch, request=request)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE"
    config = decode_lifecycle_abyss_market_config(request.markets[0].config)
    assert config.fee == 100
    assert (config.positions[0].tick_upper if token0 else config.positions[0].tick_lower) == (121 if token0 else -121)
    assert planned.transactions[0].data == build_lifecycle_calldata(request, "beginLaunch")


@pytest.mark.parametrize("token0", [True, False])
def test_planner_fee100_still_requires_full_spacing_one_tail(monkeypatch, token0):
    request = topology_plan(1, token0, fee=100, intervals=[(0, 887270)])
    lifecycle, request, provider, client = wire_planning(monkeypatch, request=request)
    with pytest.raises(ValueError) as failure:
        lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert failure.value.code == "MISSING_PERMANENT_TAIL"
    assert provider.simulations == []


@pytest.mark.parametrize("version", [1, 4, 6])
@pytest.mark.parametrize("token0", [True, False])
def test_planner_requires_explicit_missing_tail_waiver_and_preserves_it_on_replanning(monkeypatch, version, token0):
    lifecycle, request, provider, client = wire_planning(monkeypatch,
        request=topology_plan(version, token0, intervals=[(0, 120)]))
    committed = hash_launch_plan(request), encode_launch_plan(request)
    for options in ({}, dict(allow_topology_gaps=True)):
        with pytest.raises(ValueError) as failure:
            lifecycle.plan_launch(client, request, account=request.creator, mode="atomic", **options)
        assert failure.value.code == "MISSING_PERMANENT_TAIL"
        assert provider.simulations == []
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic",
        allow_missing_permanent_tail=True)
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE" and planned.allow_missing_permanent_tail
    assert not planned.allow_topology_gaps
    assert planned.transactions[0].data == build_lifecycle_calldata(request, "beginLaunch")
    assert (hash_launch_plan(request), encode_launch_plan(request)) == committed


@pytest.mark.parametrize("version", [1, 4, 6])
@pytest.mark.parametrize("token0", [True, False])
def test_planner_gap_waiver_does_not_waive_the_tail_requirement(monkeypatch, version, token0):
    boundary = 887270 if version in {1, 6} else 887220
    lifecycle, request, provider, client = wire_planning(monkeypatch,
        request=topology_plan(version, token0, intervals=[(0, 120), (180, boundary)]))
    for options in ({}, dict(allow_missing_permanent_tail=True)):
        with pytest.raises(ValueError) as failure:
            lifecycle.plan_launch(client, request, account=request.creator, mode="atomic", **options)
        assert failure.value.code == "TOPOLOGY_INTERIOR_GAP"
        assert provider.simulations == []
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic",
        allow_topology_gaps=True)
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE" and planned.allow_topology_gaps
    assert not planned.allow_missing_permanent_tail
    assert planned.transactions[0].data == build_lifecycle_calldata(request, "beginLaunch")


@pytest.mark.parametrize("version", [1, 4, 6])
@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("lower,code", [(60, "TOPOLOGY_OPENING_GAP"), (1, "TOPOLOGY_OFF_GRID_TICK")])
def test_planner_opt_out_flags_never_waive_opening_or_grid_errors(monkeypatch, version, token0, lower, code):
    boundary = 887270 if version in {1, 6} else 887220
    lifecycle, request, provider, client = wire_planning(monkeypatch,
        request=topology_plan(version, token0, intervals=[(lower, boundary)]))
    with pytest.raises(ValueError) as failure:
        lifecycle.plan_launch(client, request, account=request.creator, mode="atomic",
            allow_topology_gaps=True, allow_missing_permanent_tail=True)
    assert failure.value.code == code
    assert provider.simulations == []


@pytest.mark.parametrize("version", [1, 4])
def test_async_preparation_threads_topology_options_without_weakening_hard_guards(monkeypatch, version):
    import asyncio

    lifecycle, request, _, client = wire_planning(monkeypatch,
        request=topology_plan(version, False, intervals=[(0, 120), (180, 240)]))
    async def prepare(**options):
        return await lifecycle.prepare_and_plan_lifecycle_launch(client, request,
            account=request.creator, mode="atomic", **options)
    with pytest.raises(ValueError) as failure:
        asyncio.run(prepare())
    assert failure.value.code == "TOPOLOGY_INTERIOR_GAP"
    with pytest.raises(ValueError) as failure:
        asyncio.run(prepare(allow_topology_gaps=True))
    assert failure.value.code == "MISSING_PERMANENT_TAIL"
    planned = asyncio.run(prepare(allow_topology_gaps=True, allow_missing_permanent_tail=True))
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE" and planned.allow_topology_gaps and planned.allow_missing_permanent_tail


def test_topology_opt_outs_do_not_weaken_supply_or_position_budget_equality(monkeypatch):
    lifecycle, request, provider, client = wire_planning(monkeypatch)
    config = decode_lifecycle_v4_market_config(request.markets[0].config)
    config = replace(config, positions=(replace(config.positions[0], max_token_amount=config.positions[0].max_token_amount - 1),))
    for changed in (
        replace(request, token=replace(request.token, supply=request.token.supply + 1)),
        replace(request, markets=(replace(request.markets[0], config=encode_lifecycle_v4_market_config(config)), *request.markets[1:])),
    ):
        with pytest.raises(ValueError) as failure:
            lifecycle.plan_launch(client, changed, account=request.creator, mode="atomic",
                allow_topology_gaps=True, allow_missing_permanent_tail=True)
        assert failure.value.code == "INVALID_TOKEN_BUDGET"
    assert provider.simulations == []


def test_other_market_tail_cannot_cover_a_missing_abyss_tail(monkeypatch):
    request = plan()
    config = decode_lifecycle_abyss_market_config(request.markets[1].config)
    config = replace(config, positions=(replace(config.positions[0], tick_lower=-120),))
    request = replace(request, markets=(request.markets[0],
        replace(request.markets[1], config=encode_lifecycle_abyss_market_config(config))))
    lifecycle, request, provider, client = wire_planning(monkeypatch, request=request)
    with pytest.raises(ValueError) as failure:
        lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert failure.value.code == "MISSING_PERMANENT_TAIL"
    assert str(failure.value).startswith("Market 1:")
    assert provider.simulations == []


def test_deliberate_cancellation_does_not_require_disabled_registry_admission(monkeypatch):
    _, request, _, client = wire_planning(monkeypatch, scenario_options={"latest_phase": 1, "confirmed_phase": 1})

    def forbidden(*_):
        raise AssertionError("cancellation must not require a still-enabled registry")

    import black_market_sdk.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", forbidden)
    step = build_next_transaction(client, request, account=request.creator, mode="staged", action="cancel")
    assert step.kind == "cancel" and step.assessment.ready





























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
























@pytest.mark.parametrize("name", ["max_calldata_bytes", "account_max_calldata_bytes"])
def test_byte_count_policy_cannot_cross_matching_safe_integer_boundary(name):
    with pytest.raises(ValueError):
        LaunchExecutionLimits(**{name: 2**53})




@pytest.mark.parametrize("headroom", [0, 1500, 2000, 10_000])
def test_matching_headroom_boundaries_preserve_exact_custom_arithmetic(headroom):
    limits = LaunchExecutionLimits(headroom_bps=headroom)
    exact = 2**63 + 7
    assert limits.buffered_gas(exact) == (exact * (10_000 + headroom) + 9999) // 10_000


@pytest.mark.parametrize("headroom", [-1, True, 1.5, 10_001, 2**53 - 1])
def test_headroom_cannot_exceed_matching_zero_to_one_hundred_percent_range(headroom):
    with pytest.raises(ValueError):
        LaunchExecutionLimits(headroom_bps=headroom)


@pytest.mark.parametrize("name", [
    "chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit",
    "execution_gas_ceiling", "transaction_gas_ceiling",
])
def test_transaction_and_compute_cap_boundaries_are_positive_uint64(name):
    assert getattr(LaunchExecutionLimits(**{name: 2**64 - 1}), name) == 2**64 - 1
    for value in (0, -1, True, 1.5, 2**64, 2**256):
        with pytest.raises(ValueError):
            LaunchExecutionLimits(**{name: value})


def test_header_gas_limit_is_positive_uint64_without_narrowing_other_quantities():
    block = LaunchBlock(2**64 + 1, BLOCK_HASH, 100, 2**64 - 1, 2**128)
    assert block.gas_limit == 2**64 - 1 and block.base_fee_per_gas == 2**128
    for value in (0, -1, True, 1.5, 2**64, 2**256):
        with pytest.raises(ValueError):
            LaunchBlock(51, BLOCK_HASH, 100, value, 1)
    limits = LaunchExecutionLimits(chain_id=2**128, observed_block_number=2**128)
    assert limits.chain_id == limits.observed_block_number == 2**128





@pytest.mark.parametrize("scenario_options", [
    {"latest_phase": LifecyclePhase.PREPARING, "confirmed_phase": LifecyclePhase.NONE},
    {"latest_phase": LifecyclePhase.ACTIVE, "confirmed_phase": LifecyclePhase.ACTIVE},
    {"latest_phase": LifecyclePhase.CANCELLED, "confirmed_phase": LifecyclePhase.CANCELLED},
])
def test_topology_creation_policy_does_not_block_waiting_or_terminal_recovery(monkeypatch, scenario_options):
    lifecycle, request, provider, client = wire_planning(monkeypatch,
        request=topology_plan(4, False, intervals=[(0, 120)]), scenario_options=scenario_options)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged", confirmations=2)
    assert not planned.allow_topology_gaps and not planned.allow_missing_permanent_tail
    assert planned.plan_hash == hash_launch_plan(request)
    assert planned.assessment.ready is (scenario_options.get("latest_phase") in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED})
    assert provider.simulations == []


@pytest.mark.parametrize("phase", [LifecyclePhase.PREPARING, LifecyclePhase.READY])
def test_unprofiled_started_recovery_refuses_continuation_without_blocking_cancel(monkeypatch, phase):
    lifecycle, request, _, client = wire_planning(monkeypatch,
        request=topology_plan(4, False, intervals=[(0, 120)]),
        scenario_options={"latest_phase": phase, "confirmed_phase": phase})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.assessment.code == "UNKNOWN_GAS_PROFILE"
    assert not planned.allow_topology_gaps and not planned.allow_missing_permanent_tail
    with pytest.raises(ValueError) as failure:
        build_next_transaction(client, request, mode="staged")
    assert failure.value.code == "UNKNOWN_GAS_PROFILE"
    cancellation = build_next_transaction(client, request, mode="staged", action="cancel")
    assert cancellation.kind == "cancel" and cancellation.assessment.ready
