"""Wire-method budgets and safety boundaries for one native-buy UFG market.

These deterministic transport fixtures test SDK behavior, not live AMM admission.
The paced native before/after measurements are separate read-only evidence.
"""
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak
from test_lifecycle import BLOCK_HASH, ORPHAN_HASH
from test_ufg import (
    PRINCIPAL,
    QUOTE,
    ZERO,
    UfgRpcScenario,
    bound_config,
    request,
    terms,
)

from black_market_sdk import (
    ROBINHOOD_MULTICALL3,
    UFG_DYNAMIC_PROFILE_ID,
    UFG_LAUNCH_PROXY_ADDRESS,
    LifecycleAssetFunding,
    LifecycleFinalEnvelope,
    LifecycleInitialBuy,
    LifecyclePlanningError,
    LifecycleReceiptReference,
    build_next_transaction,
    build_ufg_launch_transactions,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_pool_bound_v4_market_config,
    plan_launch,
    prepare_and_plan_lifecycle_launch,
    read_launch_progress,
    simulate_launch_plan,
)
from black_market_sdk.lifecycle import (
    _LIFECYCLE_MULTICALL_CODE_HASH,
    _tuple_type,
    read_lifecycle_token_at_block,
)
from black_market_sdk.lifecycle_abis import LAUNCH_RECEIPT_COMPONENTS_V1
from black_market_sdk.lifecycle_rpc import LaunchBlock, LaunchRpcError, read_invocation


def draft_plan():
    plan = request(bound=True, identity=UFG_DYNAMIC_PROFILE_ID)
    config = bound_config(UFG_DYNAMIC_PROFILE_ID)
    edges = (-887220, -300, -240, -180, -120, -60, 0)
    budget = plan.token.supply // 6
    positions = tuple(replace(config.positions[0], tick_lower=lower, tick_upper=upper,
        max_token_amount=budget if index < 5 else plan.token.supply - budget * 5)
        for index, (lower, upper) in enumerate(pairwise(edges)))
    config = replace(config, positions=positions)
    return replace(plan, markets=(replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(config)),),
        funding=(LifecycleAssetFunding(QUOTE, 10**14, 1, QUOTE, 10**14, ZERO, b""),),
        buys=(LifecycleInitialBuy(0, 10**14, 1, PRINCIPAL, 0),))


class NativeBuyScenario(UfgRpcScenario):
    def return_data(self, transaction):
        data = super().return_data(transaction)
        kind, _ = self.command(transaction)
        if kind not in {"atomic", "activate"}:
            return data
        abi_type = _tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)
        values = list(abi_decode([abi_type], bytes.fromhex(data[2:]))[0])
        values[8] = tuple(1000 for _ in self.plan.buys)
        return "0x" + abi_encode([abi_type], [tuple(values)]).hex()


class CountingTransport:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []
        self.after_response = None

    def request(self, args):
        self.calls.append(deepcopy(args))
        response = self.provider.make_request(args["method"], args["params"])
        if self.after_response is not None:
            self.after_response(args, response)
        if response.get("error") is not None:
            raise LaunchRpcError(args["method"], response["error"])
        return response["result"]

    @property
    def counts(self):
        return Counter(call["method"] for call in self.calls)


def prepare(client, plan, **options):
    return asyncio.run(prepare_and_plan_lifecycle_launch(client, plan,
        account=PRINCIPAL, mode="atomic", confirmations=2, ufg=terms(),
        buy_slippage_bps=50, **options))


@pytest.fixture
def prepared():
    draft = draft_plan()
    client = CountingTransport(NativeBuyScenario(draft))
    launch = prepare(client, draft)
    assert launch.admitted
    return draft, client, launch


def assert_budget(client, *, calls, simulations, gas_prices=1, total):
    assert client.counts == Counter({
        "eth_getBlockByNumber": 2, "eth_chainId": 1,
        "eth_getTransactionCount": 3, "eth_call": calls,
        "eth_simulateV1": simulations,
        **({"eth_gasPrice": gas_prices} if gas_prices else {}),
    })
    assert len(client.calls) == total


def test_protected_preparation_pins_once_and_keeps_calibration_separate(prepared):
    _, client, launch = prepared
    assert client.counts == Counter({"eth_chainId": 1, "eth_getBlockByNumber": 2,
        "eth_getTransactionCount": 3, "eth_call": 4,
        "eth_gasPrice": 1, "eth_getCode": 2, "eth_simulateV1": 3})
    assert len(client.calls) == 16
    assert launch.transactions[0].nonce == launch.progress.pending_account_nonce
    config = decode_lifecycle_pool_bound_v4_market_config(launch.plan.markets[0].config)
    assert len(config.positions) == 6 and config.ufg_launch_config == terms()
    assert launch.plan.buys[0].min_token_out == 995


@pytest.mark.parametrize("entry,total,calls,simulations,prices", [
    ("plan", 11, 3, 1, 1), ("simulate", 11, 3, 1, 1),
    ("build", 11, 3, 1, 1), ("reviewed", 9, 2, 1, 0),
    ("staged", 11, 3, 1, 1), ("staged-reviewed", 9, 2, 1, 0),
])
def test_entrypoint_read_budgets(prepared, entry, total, calls, simulations, prices):
    _, client, launch = prepared
    if entry == "staged-reviewed":
        launch = plan_launch(client, launch.plan, account=PRINCIPAL, mode="staged", confirmations=2, ufg=terms())
    client.calls.clear()
    if entry in {"plan", "staged"}:
        result = plan_launch(client, launch.plan, account=PRINCIPAL,
            mode="staged" if entry == "staged" else "atomic", confirmations=2, ufg=terms())
        assert result.admitted
    elif entry == "simulate":
        assert simulate_launch_plan(client, launch).admitted
    elif entry == "build":
        assert build_next_transaction(client, launch).kind == "atomic"
    else:
        result = build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0])
        assert result.as_transaction() == launch.transactions[0].as_transaction()
    assert_budget(client, calls=calls, simulations=simulations, gas_prices=prices, total=total)


def test_standalone_progress_retains_confirmation_and_pending_nonces(prepared):
    _, client, launch = prepared
    client.calls.clear()
    progress = read_launch_progress(client, launch.plan, confirmations=2, mode="atomic")
    assert progress.confirmation_safe
    assert client.counts == Counter({"eth_getBlockByNumber": 2, "eth_chainId": 1,
        "eth_getTransactionCount": 3, "eth_call": 2})
    assert len(client.calls) == 8
    assert [call["params"][1] for call in client.calls if call["method"] == "eth_getTransactionCount"] == ["0x32", "0x33", "pending"]


def test_submission_preflight_checks_its_provider_without_source_rechecks(prepared):
    _, client, launch = prepared
    client.calls.clear()
    original = client.provider.make_request
    def respond(method, params):
        if method == "eth_estimateGas":
            return {"result": hex(100_000)}
        return original(method, params)
    client.provider.make_request = respond
    result = build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0], submission_client=client)
    assert result.admission.transport_preflight == "passed"
    assert len(client.calls) == 12
    assert client.counts["eth_chainId"] == 3
    estimate = next(index for index, call in enumerate(client.calls) if call["method"] == "eth_estimateGas")
    assert [call["method"] for call in client.calls[estimate + 1:]] == ["eth_chainId"]


@pytest.mark.parametrize("price,expected_total", [(None, 15), (7, 14)])
def test_fixed_final_envelope_preserves_exact_gas_and_one_selected_fee(price, expected_total):
    draft = draft_plan()
    client = CountingTransport(NativeBuyScenario(draft))
    launch = prepare(client, draft, fixed_final_envelope=LifecycleFinalEnvelope(31_000_000, price))
    assert launch.admitted and len(client.calls) == expected_total
    transaction = launch.transactions[0]
    assert transaction.gas_limit == 31_000_000
    assert transaction.gas_price == (2 if price is None else price)
    assert client.counts["eth_simulateV1"] == 3
    assert client.counts["eth_gasPrice"] == (1 if price is None else 0)
    executions = client.provider.simulations
    assert [payload["validation"] for payload in executions] == [False, True]
    assert int(executions[-1]["blockStateCalls"][0]["calls"][0]["gas"], 16) == 31_000_000
    poster_calls = [call for call in client.calls if call["method"] == "eth_call" and call["params"][0]["to"].lower().endswith("00c8")]
    assert poster_calls == []  # Neither calibration nor an explicitly fixed envelope needs a quote.


@pytest.mark.parametrize("envelope,mode", [
    (LifecycleFinalEnvelope(0), "atomic"), (LifecycleFinalEnvelope(True), "atomic"),
    (LifecycleFinalEnvelope(1 << 64), "atomic"), (LifecycleFinalEnvelope(1, -1), "atomic"),
    (LifecycleFinalEnvelope(1), "staged"),
])
def test_invalid_fixed_envelopes_fail_before_rpc(envelope, mode):
    client = CountingTransport(NativeBuyScenario(draft_plan()))
    with pytest.raises(LifecyclePlanningError) as failure:
        asyncio.run(prepare_and_plan_lifecycle_launch(client, draft_plan(), account=PRINCIPAL,
            mode=mode, ufg=terms(), fixed_final_envelope=envelope))
    assert failure.value.code == "INVALID_FINAL_ENVELOPE" and client.calls == []


def test_held_fixed_envelope_failure_is_not_resized_or_remeasured(prepared):
    _, client, launch = prepared
    client.provider.replay_failure = "revert"
    client.calls.clear()
    client.provider.simulations.clear()
    result = asyncio.run(prepare_and_plan_lifecycle_launch(client, launch.plan,
        account=PRINCIPAL, mode="atomic", confirmations=2, ufg=terms(),
        fixed_final_envelope=LifecycleFinalEnvelope(31_000_000, 7)))
    assert not result.admitted
    assert client.counts["eth_simulateV1"] == 1  # Successful session capability is reused.
    assert len(client.provider.simulations) == 1 and client.provider.simulations[0]["validation"]
    assert all(call["params"][0]["to"].lower() != "0x00000000000000000000000000000000000000c8"
        for call in client.calls if call["method"] == "eth_call")




def test_multicall_attestation_rejects_changed_runtime_before_execution():
    client = CountingTransport(NativeBuyScenario(draft_plan()))
    def change_code(args, response):
        if args["method"] == "eth_getCode" and args["params"][0].lower() == ROBINHOOD_MULTICALL3.lower():
            response["result"] = "0x6000"
    client.after_response = change_code
    with pytest.raises(LifecyclePlanningError) as failure:
        prepare(client, draft_plan())
    assert failure.value.code == "MULTICALL_CODE_MISMATCH"
    assert client.counts["eth_simulateV1"] == 0


def test_multicall_contains_no_caller_sensitive_poster_quote(prepared):
    _, client, _ = prepared
    aggregates = [call for call in client.calls if call["method"] == "eth_call" and call["params"][0]["to"].lower() == ROBINHOOD_MULTICALL3.lower()]
    assert len(aggregates) == 2  # Head and the explicit two-confirmation observation.
    subcalls = [entry for aggregate in aggregates for entry in abi_decode(
        ["(address,bool,bytes)[]"], bytes.fromhex(aggregate["params"][0]["data"][10:]))[0]]
    assert not any(target.lower().endswith("00c8") for target, _, _ in subcalls)
    code = bytes.fromhex((Path(__file__).parent / "fixtures/multicall3-robinhood-runtime.hex").read_text().strip()[2:])
    assert "0x" + keccak(code).hex() == _LIFECYCLE_MULTICALL_CODE_HASH


def test_immutable_prediction_reuses_only_within_the_open_invocation(prepared):
    _, client, launch = prepared
    first = LaunchBlock(51, BLOCK_HASH, 100, 30_000_000, 1)
    second = replace(first, number=52, block_hash=ORPHAN_HASH)
    @read_invocation("token.predict")
    def read_twice(scoped):
        assert read_lifecycle_token_at_block(scoped, launch.plan, first) == read_lifecycle_token_at_block(scoped, launch.plan, second)
    client.calls.clear()
    read_twice(client)
    assert client.counts["eth_call"] == 1
    read_twice(client)
    assert client.counts["eth_call"] == 2


def test_equal_receipt_blocks_share_headers_but_are_checked_after_identity_reads(prepared):
    _, client, launch = prepared
    original = client.provider.make_request
    def receipts(method, params):
        if method == "eth_getTransactionReceipt":
            return {"result": {"blockNumber": "0x31", "blockHash": BLOCK_HASH, "status": "0x1"}}
        if method == "eth_getTransactionByHash":
            return {"result": {"from": PRINCIPAL, "to": UFG_LAUNCH_PROXY_ADDRESS,
                "input": build_ufg_launch_transactions(launch.plan, config=terms(), mode="atomic")[0].data,
                "value": hex(10**14)}}
        return original(method, params)
    client.provider.make_request = receipts
    references = tuple(LifecycleReceiptReference("0x" + byte * 32, observed_block_number=49,
        observed_block_hash=BLOCK_HASH) for byte in ("cc", "dd"))
    client.calls.clear()
    progress = read_launch_progress(client, launch.plan, confirmations=2, mode="atomic", receipts=references)
    assert [receipt.status for receipt in progress.receipts] == ["confirmed", "confirmed"]
    assert len(client.calls) == 13
    assert len([call for call in client.calls if call["method"] == "eth_getBlockByNumber" and call["params"][0] == "0x31"]) == 1


def test_reviewed_nonce_cannot_be_rewritten_when_account_history_advances(prepared):
    _, client, launch = prepared
    original = client.provider.make_request
    def advance_nonce(method, params):
        if method == "eth_getTransactionCount":
            return {"result": "0x1"}
        return original(method, params)
    client.provider.make_request = advance_nonce
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0])
    assert failure.value.code == "REVIEWED_TRANSACTION_MISMATCH"
    assert client.counts["eth_simulateV1"] == 0


@pytest.mark.parametrize("nonce", [-1, True, 1 << 64])
def test_reviewed_nonce_rejects_invalid_quantities_before_rpc(prepared, nonce):
    _, client, launch = prepared
    transaction = replace(launch.transactions[0], nonce=nonce)
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, reviewed_transaction=transaction)
    assert failure.value.code == "INVALID_REVIEWED_TRANSACTION"
    assert client.calls == []


@pytest.mark.parametrize("invalid", ["missing", "failed"])
def test_multicall_incomplete_or_failed_results_do_not_seed_successful_reads(invalid):
    client = CountingTransport(NativeBuyScenario(draft_plan()))
    def corrupt_aggregate(args, response):
        if args["method"] != "eth_call" or args["params"][0]["to"].lower() != ROBINHOOD_MULTICALL3.lower():
            return
        results = list(abi_decode(["(bool,bytes)[]"], bytes.fromhex(response["result"][2:]))[0])
        if invalid == "missing":
            results.pop()
        else:
            results[0] = (False, results[0][1])
        response["result"] = "0x" + abi_encode(["(bool,bytes)[]"], [results]).hex()
    client.after_response = corrupt_aggregate
    with pytest.raises(LifecyclePlanningError) as failure:
        prepare(client, draft_plan())
    assert failure.value.code == "INVALID_RPC_RESPONSE"
    assert client.counts["eth_simulateV1"] == 0
    client.after_response = None
    assert prepare(client, draft_plan()).admitted


def test_fixed_preparation_rejects_a_preceding_funding_approval(prepared):
    _, client, launch = prepared
    plan = replace(launch.plan, funding=(replace(launch.plan.funding[0], kind=0),))
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        asyncio.run(prepare_and_plan_lifecycle_launch(client, plan, account=PRINCIPAL,
            mode="atomic", confirmations=2, ufg=terms(),
            fixed_final_envelope=LifecycleFinalEnvelope(31_000_000)))
    assert failure.value.code == "INVALID_FINAL_ENVELOPE"
    assert client.counts["eth_simulateV1"] == 0


def test_fee_recommendation_is_frozen_inside_preparation_but_not_across_invocations():
    client = CountingTransport(NativeBuyScenario(draft_plan()))
    client.provider.gas_price = 15
    def change_recommendation(args, _response):
        if args["method"] == "eth_gasPrice":
            client.provider.gas_price = 999
    client.after_response = change_recommendation
    launch = prepare(client, draft_plan())
    assert launch.transactions[0].gas_price == 15
    assert client.counts["eth_gasPrice"] == 1
    assert all(int(block["calls"][0]["gasPrice"], 16) == 15
        for payload in client.provider.simulations for block in payload["blockStateCalls"])
    client.calls.clear()
    current = plan_launch(client, launch.plan, account=PRINCIPAL, mode="atomic",
        confirmations=2, ufg=terms())
    assert current.transactions[0].gas_price == 999
    assert client.counts["eth_gasPrice"] == 1


def test_fixed_envelope_also_applies_without_mining_or_buy_calibration():
    plan = request()
    client = CountingTransport(NativeBuyScenario(plan))
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, plan, account=PRINCIPAL,
        mode="atomic", ufg=terms(), fixed_final_envelope=LifecycleFinalEnvelope(31_000_000, 7)))
    assert launch.admitted
    assert launch.transactions[0].gas_limit == 31_000_000
    assert launch.transactions[0].gas_price == 7
    assert client.counts["eth_gasPrice"] == 0
    assert client.counts["eth_simulateV1"] == 2
