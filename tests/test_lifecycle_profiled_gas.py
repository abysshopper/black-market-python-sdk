"""Pinned capacity/input assessments, never live execution or settlement proof."""
import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from black_market_sdk import (
    AUCTION_QUOTE_OPTIONS, LifecycleAssetFunding, LifecycleInitialBuy,
    LifecyclePlanningError, LaunchExecutionLimits, ROBINHOOD_WETH,
    build_launch_transactions, build_next_transaction, compute_lifecycle_launch_gas_bounds,
    decode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_pool_bound_v4_market_config,
    predict_launch_token, prepare_and_plan_lifecycle_launch,
)
from test_lifecycle_read_counts import CountingTransport
from test_lifecycle_read_reduction import ResumeScenario, ordinary_bound_draft
from test_ufg import PRINCIPAL, ZERO, UfgRpcScenario

RELEASED_QUOTES = tuple(dict.fromkeys([ROBINHOOD_WETH,
    *(quote.address for quote in AUCTION_QUOTE_OPTIONS if quote.address != ZERO)]))


def orient_opening_bands(draft, client, provider):
    predicted = predict_launch_token(client, draft)
    markets = []
    for market in draft.markets:
        config = decode_lifecycle_pool_bound_v4_market_config(market.config)
        assert config.sqrt_price_x96 == 1 << 96
        up = all(position.tick_lower >= 0 for position in config.positions)
        assert up or all(position.tick_upper <= 0 for position in config.positions)
        if up != (int(predicted, 16) < int(market.quote_asset, 16)):
            config = replace(config, positions=tuple(replace(position,
                tick_lower=-position.tick_upper, tick_upper=-position.tick_lower)
                for position in config.positions))
        markets.append(replace(market, config=encode_lifecycle_pool_bound_v4_market_config(config)))
    provider.plan = replace(draft, markets=tuple(markets))
    return provider.plan


def released_draft(count=1, *, buy=False, conversion=False):
    draft = ordinary_bound_draft(count)
    draft = replace(draft, markets=tuple(replace(market, quote_asset=RELEASED_QUOTES[index])
        for index, market in enumerate(draft.markets)))
    funding = ()
    if buy or conversion:
        funding = (LifecycleAssetFunding(ROBINHOOD_WETH, 10**14, 2 if conversion else 1,
            ZERO if conversion else ROBINHOOD_WETH, 10**14,
            "0x8888888888888888888888888888888888888888" if conversion else ZERO,
            b"conversion" if conversion else b""),)
    return replace(draft, funding=funding,
        buys=(LifecycleInitialBuy(0, 10**14, 1, PRINCIPAL, 0),) if buy else ())


def prepare(client, draft, **options):
    mode = options.pop("mode", "staged")
    return asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode=mode, **options))


def scenario(draft=None, **options):
    draft = released_draft() if draft is None else draft
    provider = ResumeScenario(draft, **options)
    return draft, provider, CountingTransport(provider)


def assert_no_execution(client):
    assert not {"eth_simulateV1", "eth_estimateGas", "eth_sendTransaction", "eth_sendRawTransaction",
        "eth_getTransactionCount"}.intersection(client.counts)
    assert client.provider.simulations == client.provider.probes == client.provider.poster_requests == []


def test_assessment_reserves_native_ceiling_and_only_immediate_envelope_with_four_cold_two_warm_reads():
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    assert launch.assessment.ready
    assert launch.assessment.gas_profile.version == "robinhood-4663-capacity-v4"
    bounds = launch.assessment.gas_profile.bounds
    assert bounds.atomic_compute_gas_upper_bound > bounds.activation_compute_gas_upper_bound > 0
    immediate = launch.transactions[0]
    assert immediate.gas_limit == 32_000_000
    assert immediate.estimate.fee_confidence == "native-ceiling-and-data"
    assert immediate.estimate.total_fee == immediate.gas_limit * immediate.gas_price
    assert "nonce" not in immediate.as_transaction()
    assert all(transaction.gas_limit is transaction.gas_price is transaction.assessment is None
        for transaction in launch.transactions[1:])
    assert client.counts == Counter({"eth_chainId": 1, "eth_getBlockByNumber": 1, "eth_call": 1, "eth_getCode": 1})
    assert_no_execution(client)
    client.calls.clear()
    current = build_next_transaction(client, launch, reviewed_transaction=immediate)
    assert current.as_transaction() == immediate.as_transaction()
    assert current.assessment.ready
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})
    assert_no_execution(client)


@pytest.mark.parametrize("conversion", [False, True])
def test_ordered_buy_protection_uses_exact_local_curve_without_executing_router(conversion):
    draft, provider, client = scenario(released_draft(buy=True, conversion=conversion))
    launch = prepare(client, draft, buy_slippage_bps=50, mode="atomic")
    quote, = launch.assessment.gas_profile.initial_buys
    assert launch.assessment.ready
    assert launch.plan.buys[0].min_token_out == quote.token_out * 9950 // 10000 > 1
    assert launch.plan.buys[0].sqrt_price_limit_x96 == 0
    assert draft.buys[0].min_token_out == 1
    from black_market_sdk import launch_plan_to_dict
    assert launch_plan_to_dict(launch.plan)["funding"] == launch_plan_to_dict(draft)["funding"]
    if conversion:
        assert launch.mode == "staged"
        assert launch.assessment.gas_profile.bounds.atomic_compute_gas_upper_bound is None
    assert_no_execution(client)


def test_metadata_storage_words_round_each_utf8_string_independently():
    draft = released_draft()
    draft = replace(draft, token=replace(draft.token, name="é" * 17, symbol="SS", metadata_uri="u" * 33))
    _, provider, client = scenario(draft)
    launch = prepare(client, draft)
    atomic, = build_launch_transactions(launch.plan, mode="atomic")
    bounds = compute_lifecycle_launch_gas_bounds(launch.plan,
        predicted_token=launch.predicted_token, headroom_bps=0)
    positions = decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config).positions
    basis = (22_000_000 + 600_000 * (len(positions) - 1) + 24 * atomic.calldata_bytes
        + 100_000 * len(draft.fee_assets) + 25_000 * 5)
    assert bounds.atomic_compute_gas_upper_bound == (basis * 12500 + 9999) // 10000
    assert launch.assessment.ready
    assert_no_execution(client)


@pytest.mark.parametrize("held_gas,accepted", [(32_000_000, True), (30_000_000, False)])
def test_conversion_recovery_assesses_only_next_command_without_full_replanning(monkeypatch, held_gas, accepted):
    import black_market_sdk.lifecycle as lifecycle
    draft, provider, client = scenario(released_draft(conversion=True))
    launch = prepare(client, draft, mode="atomic")
    assert launch.assessment.ready and launch.mode == "staged"
    assert launch.assessment.gas_profile.bounds.atomic_compute_gas_upper_bound is None
    held = replace(launch.transactions[0], gas_limit=held_gas, gas_price=7)
    def forbidden(*args, **kwargs):
        raise AssertionError("Build-next must not replan the suffix or rediscover profiles")
    monkeypatch.setattr(lifecycle, "_plan_launch", forbidden)
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", forbidden)
    if accepted:
        current = build_next_transaction(client, launch, reviewed_transaction=held)
        assert current.as_transaction() == held.as_transaction()
    else:
        with pytest.raises(LifecyclePlanningError) as failure:
            build_next_transaction(client, launch, reviewed_transaction=held)
        assert failure.value.code == "STALE_REVIEWED_GAS"
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    provider.prepared = 0
    client.calls.clear()
    preparation = build_next_transaction(client, launch)
    assert preparation.kind == "prepare" and preparation.assessment.ready
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})
    provider.prepared = len(launch.plan.markets)
    provider.latest_phase = provider.confirmed_phase = 2
    activation = build_next_transaction(client, launch)
    assert activation.kind == "activate" and activation.assessment.ready
    assert_no_execution(client)


@pytest.mark.parametrize("prepared", [0, 2])
@pytest.mark.parametrize("wire_review", [False, True])
def test_reviewed_preparation_round_trips_the_next_contiguous_span(prepared, wire_review):
    draft, provider, client = scenario(ordinary_bound_draft(3))
    launch = prepare(client, draft)
    assert launch.assessment.ready
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    provider.prepared = prepared
    preparation = build_next_transaction(client, launch)
    assert preparation.kind == "prepare" and preparation.first_market == prepared
    assert preparation.market_count == (2 if prepared == 0 else 1)
    wire = preparation.as_transaction()
    reviewed = ({"id": preparation.step_id, "kind": preparation.kind, **wire}
        if wire_review else preparation)
    current = build_next_transaction(client, launch, reviewed_transaction=reviewed)
    assert current.step_id == preparation.step_id
    assert current.as_transaction() == wire
    assert current.assessment.ready
    assert_no_execution(client)


def test_native_balance_refusal_does_not_fabricate_a_signing_envelope():
    draft, provider, client = scenario(balance=1_000_000)
    launch = prepare(client, draft)
    assert not launch.assessment.ready
    assert launch.assessment.code == "INSUFFICIENT_NATIVE_BALANCE"
    assert launch.assessment.failure_category == "affordability"
    assert launch.assessment.estimate is None
    assert launch.transactions[0].gas_limit is None
    assert_no_execution(client)


def test_expired_plan_is_refused_but_cancellation_remains_available():
    draft = released_draft()
    _, provider, client = scenario(draft, timestamp=draft.deadline)
    launch = prepare(client, draft)
    assert launch.assessment.ready
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    provider.timestamp = draft.deadline + 1
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch)
    assert failure.value.code == "PLAN_EXPIRED"
    assert failure.value.assessment.failure_category == "semantic"
    cancellation = build_next_transaction(client, launch, action="cancel")
    assert cancellation.kind == "cancel" and cancellation.assessment.ready
    assert_no_execution(client)


def test_calldata_and_known_curve_failures_have_actionable_user_safe_reasons():
    draft, provider, client = scenario()
    refused = prepare(client, draft, limits=LaunchExecutionLimits(max_calldata_bytes=1))
    assert refused.assessment.code == "CALLDATA_LIMIT"
    assert refused.assessment.capacity_constraint == "calldata"
    draft = released_draft(buy=True)
    draft = replace(draft, buys=(replace(draft.buys[0], min_token_out=(1 << 256) - 1),))
    _, provider, client = scenario(draft)
    refused = prepare(client, draft)
    assert refused.assessment.code == "BUY_MINIMUM_NOT_MET"
    assert refused.assessment.failure_category == "semantic"
    assert_no_execution(client)


def test_unlisted_quote_currency_does_not_require_an_execution_fallback():
    draft, provider, client = scenario(ordinary_bound_draft(1))
    launch = prepare(client, draft)
    assert launch.assessment.ready and launch.assessment.gas_profile is not None
    assert_no_execution(client)


def test_wallet_account_code_is_not_an_sdk_execution_guard():
    draft, provider, client = scenario(smart_account=True)
    launch = prepare(client, draft)
    assert launch.assessment.ready
    assert client.counts["eth_getCode"] == 1  # Multicall3 attestation, not sender admission.
    assert_no_execution(client)


@pytest.mark.parametrize("approval", [False, True])
def test_fresh_activation_checks_cannot_be_bypassed_with_fabricated_ready_flag(approval):
    from test_lifecycle_read_counts import draft_plan
    from test_ufg import terms
    draft = draft_plan() if approval else released_draft(buy=True)
    draft = replace(draft, buys=(replace(draft.buys[0], min_token_out=(1 << 256) - 1),),
        funding=tuple(replace(funding, kind=0) for funding in draft.funding) if approval else draft.funding)
    provider = UfgRpcScenario(draft)
    client = CountingTransport(provider)
    refused = prepare(client, draft, **({"ufg": terms()} if approval else {}))
    assert refused.assessment.code == "BUY_MINIMUM_NOT_MET"
    provider.plan = refused.plan
    fabricated = replace(refused, assessment=replace(refused.assessment, ready=True))
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, fabricated)
    assert failure.value.code == "BUY_MINIMUM_NOT_MET"
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})
    provider.latest_phase = provider.confirmed_phase = 1
    cancellation = build_next_transaction(client, refused, action="cancel")
    assert cancellation.assessment.ready
    assert_no_execution(client)


def test_cached_refusal_is_not_authority_after_pinned_inputs_recover():
    draft, provider, client = scenario(balance=1_000_000)
    refused = prepare(client, draft)
    assert not refused.assessment.ready
    provider.balance = 10**24
    current = build_next_transaction(client, refused)
    assert current.assessment.ready and current.kind == "begin"
    assert_no_execution(client)


def test_raw_unstarted_plan_requires_review_but_started_recovery_cancel_and_terminal_remain_available(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    provider.plan = launch.plan
    def forbidden(*args, **kwargs):
        raise AssertionError("Raw build-next must not implicitly replan the whole launch")
    monkeypatch.setattr(lifecycle, "_plan_launch", forbidden)
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged")
    assert failure.value.code == "PLAN_REVIEW_REQUIRED"
    provider.latest_phase = provider.confirmed_phase = 1
    preparation = build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged")
    assert preparation.kind == "prepare" and preparation.assessment.ready
    cancellation = build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged", action="cancel")
    assert cancellation.kind == "cancel" and cancellation.assessment.ready
    provider.prepared = len(launch.plan.markets)
    provider.latest_phase = provider.confirmed_phase = lifecycle.LifecyclePhase.ACTIVE
    assert build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged") is None
    assert_no_execution(client)


@pytest.mark.parametrize("mode", ["staged", "atomic"])
def test_ufg_only_abyss_launch_keeps_actual_proxy_route_and_native_ceiling(mode):
    from test_ufg import UfgRpcScenario, request, terms
    draft = request()
    provider = UfgRpcScenario(draft)
    client = CountingTransport(provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode=mode, ufg=terms()))
    assert all(market.config_version == 1 for market in launch.plan.markets)
    assert launch.assessment.ready
    assert launch.transactions[0].gas_limit == 32_000_000
    assert_no_execution(client)


def test_fixed_native_envelope_refuses_a_lower_current_caller_ceiling():
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    held = launch.transactions[0]
    wire = held.as_transaction()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, reviewed_transaction=held,
            limits=LaunchExecutionLimits(account_transaction_gas_limit=held.gas_limit - 1))
    assert failure.value.code == "GAS_POLICY_LIMIT"
    assert held.as_transaction() == wire
    assert_no_execution(client)


@pytest.mark.parametrize("arb_floor,base_fee,held_price,accepted", [
    (1, 1, 0, False), (1, 1, 1, True), (0, 0, 0, True),
    (2, 1, 1, False), (1, 2, 1, False), (2, 1, 2, True), (1, 2, 2, True),
])
def test_assessment_obeys_pinned_fee_floor_without_raising_held_price(arb_floor, base_fee, held_price, accepted):
    draft, provider, client = scenario()
    provider.base_fee_per_gas, provider.gas_price = base_fee, arb_floor
    launch = prepare(client, draft)
    held = replace(launch.transactions[0], gas_price=held_price)
    wire = held.as_transaction()
    if accepted:
        current = build_next_transaction(client, launch, reviewed_transaction=held)
        assert current.as_transaction() == wire and current.assessment.ready
    else:
        with pytest.raises(LifecyclePlanningError) as failure:
            build_next_transaction(client, launch, reviewed_transaction=held)
        assert failure.value.code == "REVIEWED_FEE_TOO_LOW"
    assert held.as_transaction() == wire
    assert_no_execution(client)


@pytest.mark.parametrize("mode,count,conversion,selected", [
    ("atomic", 1, False, "atomic"), ("atomic", 2, False, "staged"),
    ("staged", 1, False, "staged"), ("atomic", 1, True, "staged"),
])
def test_only_single_market_without_conversion_is_atomic_eligible(mode, count, conversion, selected):
    draft, provider, client = scenario(released_draft(count, conversion=conversion))
    draft = orient_opening_bands(draft, client, provider)
    launch = prepare(client, draft, mode=mode)
    assert launch.assessment.ready and launch.mode == selected
    assert launch.transactions[0].kind == ("atomic" if selected == "atomic" else "begin")
    assert launch.transactions[0].gas_limit == 32_000_000
    if count > 1 or conversion:
        assert launch.assessment.gas_profile.bounds.atomic_compute_gas_upper_bound is None
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, mode="atomic" if selected == "staged" else "staged")
    assert failure.value.code == "PLAN_MUTATED" and client.calls == []
    assert_no_execution(client)


def test_approval_keeps_reviewed_identity_but_wallet_prepares_gas_and_price():
    from test_lifecycle_read_counts import draft_plan
    from test_ufg import terms
    draft = draft_plan()
    draft = replace(draft, funding=tuple(replace(funding, kind=0) for funding in draft.funding))
    provider = UfgRpcScenario(draft)
    client = CountingTransport(provider)
    launch = prepare(client, draft, ufg=terms())
    assert launch.assessment.ready
    approval = launch.transactions[0]
    assert approval.kind == "approve" and approval.assessment.ready
    assert approval.gas_limit is approval.gas_price is approval.estimate is None
    identity = {"id": approval.step_id, "kind": approval.kind, **approval.as_transaction()}
    current = build_next_transaction(client, launch, reviewed_transaction=identity)
    assert current.as_transaction() == approval.as_transaction()
    assert current.gas_limit is current.gas_price is current.estimate is None
    assert current.assessment.ready
    assert_no_execution(client)


@pytest.mark.parametrize("headroom", [-1, True, 10001, 0.5])
def test_capacity_bounds_reject_invalid_headroom_without_rpc(headroom):
    draft = released_draft()
    with pytest.raises(LifecyclePlanningError) as failure:
        compute_lifecycle_launch_gas_bounds(draft, predicted_token=PRINCIPAL, headroom_bps=headroom)
    assert failure.value.code == "INVALID_LIMITS"
