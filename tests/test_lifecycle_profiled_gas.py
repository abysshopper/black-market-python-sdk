"""Transport-level profile/one-proof behavior, not live native admission."""
import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from black_market_sdk import (
    AUCTION_QUOTE_OPTIONS, AuctionQuoteOption, LifecycleAssetFunding, LifecycleInitialBuy,
    LifecyclePlanningError, LaunchExecutionLimits, ROBINHOOD_WETH,
    build_launch_transactions, build_next_transaction, compute_lifecycle_launch_gas_bounds,
    decode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_pool_bound_v4_market_config,
    predict_launch_token, prepare_and_plan_lifecycle_launch, simulate_launch_plan,
)
from test_lifecycle_read_counts import CountingTransport
from test_lifecycle_read_reduction import ResumeScenario, ordinary_bound_draft
from test_ufg import PRINCIPAL, ZERO

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
    return asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode="staged", **options))


def scenario(draft=None, **options):
    draft = released_draft() if draft is None else draft
    provider = ResumeScenario(draft, raw_version=116, **options)
    return draft, provider, CountingTransport(provider)


def test_covered_profile_has_no_invented_steps_or_gas_used_and_two_warm_reads():
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    assert launch.admitted and launch.confidence == "profiled"
    assert launch.simulation.backend == "profiled"
    assert (launch.simulation.execution_proof, launch.simulation.protocol_fit) == ("not-run", "profiled")
    assert launch.simulation.steps == launch.simulation.evidence.calls == ()
    assert launch.simulation.gas_profile.version == "robinhood-4663-arbos61-v1"
    bounds = launch.simulation.gas_profile.bounds
    assert bounds.atomic_compute_gas_upper_bound > bounds.activation_compute_gas_upper_bound > 0
    immediate = launch.transactions[0]
    assert immediate.estimate.gas_used is None
    assert immediate.gas_limit == 32_000_000
    assert immediate.estimate.fee_confidence == "native-ceiling-and-data"
    assert immediate.estimate.total_fee == immediate.gas_limit * immediate.gas_price
    assert "nonce" not in immediate.as_transaction()
    assert all(transaction.gas_limit is transaction.gas_price is transaction.admission is None
        for transaction in launch.transactions[1:])
    assert client.counts == Counter({"eth_chainId": 1, "eth_getBlockByNumber": 1, "eth_call": 1, "eth_getCode": 2})
    assert provider.simulations == provider.probes == provider.poster_requests == []

    client.calls.clear()
    current = build_next_transaction(client, launch, reviewed_transaction=immediate)
    assert current.as_transaction() == immediate.as_transaction()
    assert current.admission.confidence == "profiled"
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})


def test_known_buy_protection_uses_ordered_local_curve_without_diagnostic_rpc():
    draft, provider, client = scenario(released_draft(buy=True))
    launch = prepare(client, draft, buy_slippage_bps=50)
    quote, = launch.simulation.gas_profile.initial_buys
    assert launch.admitted and launch.confidence == "profiled"
    assert launch.plan.buys[0].min_token_out == quote.token_out * 9950 // 10000 > 1
    assert launch.plan.buys[0].sqrt_price_limit_x96 == 0
    assert draft.buys[0].min_token_out == 1
    assert provider.simulations == provider.probes == []
    assert client.counts["eth_getTransactionCount"] == client.counts["eth_gasPrice"] == 0


def test_metadata_storage_words_round_each_utf8_string_independently():
    draft = released_draft()
    draft = replace(draft, token=replace(draft.token, name="é" * 17, symbol="SS", metadata_uri="u" * 33))
    _, provider, client = scenario(draft)
    launch = prepare(client, draft)
    atomic, = build_launch_transactions(launch.plan, mode="atomic")
    bounds = compute_lifecycle_launch_gas_bounds(launch.plan,
        predicted_token=launch.predicted_token, headroom_bps=0)
    positions = decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config).positions
    basis = (19_500_000 + 600_000 * (len(positions) - 1) + 24 * atomic.calldata_bytes
        + 100_000 * len(draft.fee_assets) + 22_000 * 5)
    assert bounds.atomic_compute_gas_upper_bound == basis
    assert launch.transactions[0].gas_limit == 32_000_000
    assert launch.admitted and provider.simulations == []


def test_conversion_review_proves_activation_once_and_next_build_is_immediate_only(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft, provider, client = scenario(released_draft(conversion=True))
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == len(launch.transactions) == 3
    assert all(step.gas_limit == 32_000_000 for step in launch.simulation.steps)
    assert all(step.provisional and step.estimate is None for step in launch.simulation.steps[1:])

    def forbidden(*args, **kwargs):
        raise AssertionError("Build-next must not replan the remaining suffix")
    monkeypatch.setattr(lifecycle, "_plan_launch", forbidden)
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    provider.prepared = 0
    provider.simulations.clear()
    client.calls.clear()
    preparation = build_next_transaction(client, launch)
    assert preparation.kind == "prepare" and preparation.admission.confidence == "profiled"
    assert provider.simulations == []
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})

    provider.prepared = len(launch.plan.markets)
    provider.latest_phase = provider.confirmed_phase = 2
    activation = build_next_transaction(client, launch)
    assert activation.kind == "activate" and activation.admission.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 1
    assert launch.plan.buys == ()  # Consuming activation is exact even without buys.


@pytest.mark.parametrize("held_gas,accepted", [(32_000_000, True), (30_000_000, False)])
def test_opaque_conversion_uses_native_ceiling_once_without_resizing_a_held_envelope(held_gas, accepted):
    draft, provider, client = scenario(released_draft(conversion=True),
        compute_gas_by_kind={"begin": 28_000_000, "prepare": 100_000, "activate": 100_000})
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 3
    assert launch.transactions[0].gas_limit == 32_000_000
    assert launch.transactions[0].gas_used == 28_000_000
    assert launch.simulation.gas_profile is None
    held = replace(launch.transactions[0], gas_limit=held_gas, gas_price=7)
    provider.simulations.clear()
    if accepted:
        current = build_next_transaction(client, launch, reviewed_transaction=held)
        assert current.as_transaction() == held.as_transaction()
        assert [payload["validation"] for payload in provider.simulations] == [True]
        assert len(provider.simulations[0]["blockStateCalls"]) == 1
    else:
        with pytest.raises(LifecyclePlanningError) as failure:
            build_next_transaction(client, launch, reviewed_transaction=held)
        assert failure.value.code == "STALE_REVIEWED_GAS"
        assert provider.simulations == []


def test_a_later_conversion_activation_failure_is_not_admitted_before_begin():
    draft, provider, client = scenario(released_draft(conversion=True), replay_failure="missing-activation")
    launch = prepare(client, draft)
    assert not launch.admitted and launch.simulation.failed_transaction_id == "activate"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 3


def test_native_balance_reservation_refuses_without_resizing_or_simulating():
    draft, provider, client = scenario(balance=1_000_000)
    launch = prepare(client, draft)
    assert not launch.admitted and launch.simulation.failure_category == "affordability"
    assert launch.transactions[0].gas_limit == 32_000_000
    assert launch.simulation.steps == () and provider.simulations == provider.probes == []


def test_expired_profile_is_refused_but_cancellation_remains_available():
    draft = released_draft()
    _, provider, client = scenario(draft, timestamp=draft.deadline)
    launch = prepare(client, draft)
    assert launch.admitted  # Orchestrator permits equality at the pinned timestamp.
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    provider.timestamp = draft.deadline + 1
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch)
    assert failure.value.simulation.failure_category == "semantic"
    assert "deadline" in str(failure.value).lower()
    cancellation = build_next_transaction(client, launch, action="cancel")
    assert cancellation.kind == "cancel" and cancellation.admission.admitted
    assert provider.simulations == []


def test_explicit_simulation_still_executes_a_covered_internal_plan():
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    provider.simulations.clear()
    simulation = simulate_launch_plan(client, launch)
    assert simulation.admitted and simulation.confidence == "stateful"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert simulation.gas_profile.version == launch.simulation.gas_profile.version


def test_profile_policy_and_known_curve_failures_do_not_fabricate_execution():
    draft, provider, client = scenario()
    refused = prepare(client, draft, limits=LaunchExecutionLimits(max_calldata_bytes=1))
    assert not refused.admitted and refused.simulation.capacity_constraint == "calldata"
    assert refused.simulation.execution_proof == "not-run" and provider.simulations == []

    draft = released_draft(buy=True)
    draft = replace(draft, buys=(replace(draft.buys[0], min_token_out=(1 << 256) - 1),))
    _, provider, client = scenario(draft)
    refused = prepare(client, draft)
    assert not refused.admitted and refused.simulation.failure_category == "semantic"
    assert refused.simulation.steps == () and provider.simulations == []


def test_catalog_mutation_does_not_grant_coverage_or_ban_an_unlisted_currency(monkeypatch):
    import black_market_sdk.auction as auction
    draft = ordinary_bound_draft(1)
    unknown = draft.markets[0].quote_asset
    monkeypatch.setattr(auction, "AUCTION_QUOTE_OPTIONS", [*auction.AUCTION_QUOTE_OPTIONS,
        AuctionQuoteOption("OTHER", "OTHER", unknown, 18, "Other", False)])
    _, provider, client = scenario(draft)
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.gas_profile is None
    assert launch.simulation.execution_proof == "proved" and provider.simulations


def test_delegated_or_aa_sender_is_not_certified_by_an_eoa_profile():
    draft, provider, client = scenario(smart_account=True)
    launch = prepare(client, draft)
    assert not launch.admitted and launch.simulation.execution_proof == "unavailable"
    assert provider.simulations == []
    assert client.counts["eth_getCode"] == 2  # Multicall plus one shared sender observation.


@pytest.mark.parametrize("approval", [False, True])
def test_refused_activation_review_cannot_start_begin_or_approval(approval):
    from test_lifecycle_read_counts import NativeBuyScenario, draft_plan
    from test_ufg import terms
    draft = draft_plan() if approval else released_draft(buy=True)
    draft = replace(draft, buys=(replace(draft.buys[0], min_token_out=(1 << 256) - 1),),
        funding=tuple(replace(funding, kind=0) for funding in draft.funding) if approval else draft.funding)
    provider = NativeBuyScenario(draft, raw_version=116)
    client = CountingTransport(provider)
    refused = prepare(client, draft, **({"ufg": terms()} if approval else {}))
    assert not refused.admitted and refused.simulation.failure_category == "semantic"
    assert refused.transactions[0].kind == ("approve" if approval else "begin")
    provider.plan = refused.plan
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, refused)
    assert failure.value.code == "PLAN_NOT_ADMITTED"
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})
    assert provider.simulations == []
    provider.latest_phase = provider.confirmed_phase = 1
    cancellation = build_next_transaction(client, refused, action="cancel")
    assert cancellation.kind == "cancel" and cancellation.admission.admitted


def test_raw_plan_cannot_start_but_started_recovery_cancel_and_terminal_remain_available(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    provider.plan = launch.plan
    def forbidden(*args, **kwargs):
        raise AssertionError("Raw build-next must not implicitly replan the whole launch")
    monkeypatch.setattr(lifecycle, "_plan_launch", forbidden)
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged")
    assert failure.value.code == "PLAN_NOT_ADMITTED"
    assert client.counts == Counter({"eth_getBlockByNumber": 1, "eth_call": 1})
    provider.latest_phase = provider.confirmed_phase = 1
    preparation = build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged")
    assert preparation.kind == "prepare" and preparation.admission.admitted
    cancellation = build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged", action="cancel")
    assert cancellation.kind == "cancel" and cancellation.admission.admitted
    provider.prepared = len(launch.plan.markets)
    provider.latest_phase = provider.confirmed_phase = lifecycle.LifecyclePhase.ACTIVE
    assert build_next_transaction(client, launch.plan, account=PRINCIPAL, mode="staged") is None
    assert provider.simulations == []


@pytest.mark.parametrize("mode", ["staged", "atomic"])
def test_ufg_only_abyss_launch_uses_the_fixed_native_transaction_ceiling(mode):
    from test_ufg import UfgRpcScenario, request, terms
    draft = request()
    provider = UfgRpcScenario(draft, raw_version=116)
    client = CountingTransport(provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode=mode, ufg=terms()))
    assert all(market.config_version == 1 for market in launch.plan.markets)
    assert launch.admitted and launch.confidence == "profiled"
    assert launch.transactions[0].gas_limit == 32_000_000
    assert provider.simulations == provider.probes == []


def test_fixed_native_envelope_refuses_a_lower_current_caller_ceiling():
    draft, provider, client = scenario()
    launch = prepare(client, draft)
    held = launch.transactions[0]
    wire = held.as_transaction()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, reviewed_transaction=held,
            limits=LaunchExecutionLimits(account_transaction_gas_limit=held.gas_limit - 1))
    assert failure.value.code == "PLAN_NOT_ADMITTED"
    assert held.as_transaction() == wire
    assert provider.simulations == provider.probes == []


@pytest.mark.parametrize("arb_floor,base_fee,held_price,accepted", [
    (1, 1, 0, False), (1, 1, 1, True), (0, 0, 0, True),
    (2, 1, 1, False), (1, 2, 1, False), (2, 1, 2, True), (1, 2, 2, True),
])
def test_profile_obeys_pinned_fee_floor_without_raising_held_price(arb_floor, base_fee, held_price, accepted):
    draft, provider, client = scenario()
    provider.base_fee_per_gas, provider.gas_price = base_fee, arb_floor
    launch = prepare(client, draft)
    held = replace(launch.transactions[0], gas_price=held_price)
    wire = held.as_transaction()
    if accepted:
        current = build_next_transaction(client, launch, reviewed_transaction=held)
        assert current.as_transaction() == wire
        assert current.admission.admitted and current.confidence == "profiled"
    else:
        with pytest.raises(LifecyclePlanningError) as failure:
            build_next_transaction(client, launch, reviewed_transaction=held)
        assert failure.value.code == "PLAN_NOT_ADMITTED"
    assert held.as_transaction() == wire
    assert provider.simulations == provider.probes == []


@pytest.mark.parametrize("mode,count,selected", [
    ("atomic", 1, "atomic"), ("atomic", 3, "staged"), ("staged", 1, "staged"),
])
def test_capacity_selects_mode_without_sizing_signing_gas(mode, count, selected):
    draft, provider, client = scenario(released_draft(count))
    draft = orient_opening_bands(draft, client, provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode=mode))
    assert launch.admitted and launch.mode == selected
    assert launch.transactions[0].kind == ("atomic" if selected == "atomic" else "begin")
    assert launch.transactions[0].gas_limit == 32_000_000
    assert provider.simulations == provider.probes == []
    client.calls.clear()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch, mode="atomic" if selected == "staged" else "staged")
    assert failure.value.code == "PLAN_MUTATED" and client.calls == []


@pytest.mark.parametrize("activation_gas,admitted", [(100_000, True), (32_000_001, False)])
def test_uncertain_activation_requires_full_exact_admission_before_begin(activation_gas, admitted):
    from test_lifecycle_gas_packing import hmm_plan
    draft = hmm_plan(3)
    draft = replace(draft, markets=tuple(replace(market, quote_asset=RELEASED_QUOTES[index])
        for index, market in enumerate(draft.markets)))
    _, provider, client = scenario(draft, compute_gas_by_kind={"activate": activation_gas})
    draft = orient_opening_bands(draft, client, provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=draft.creator, mode="staged"))
    assert launch.simulation.gas_profile.bounds.activation_compute_gas_upper_bound > 32_000_000
    assert launch.admitted is admitted
    assert [payload["validation"] for payload in provider.simulations] == [True]
    calls = provider.simulations[0]["blockStateCalls"]
    assert len(calls) == 4
    assert all(int(block["calls"][0]["gas"], 16) == 32_000_000 for block in calls)
    if admitted:
        assert launch.simulation.execution_proof == "proved"
        provider.plan = launch.plan
        provider.simulations.clear()
        current = build_next_transaction(client, launch)
        assert current.kind == "begin" and current.admission.confidence == "profiled"
        assert provider.simulations == []
    else:
        assert launch.simulation.failed_transaction_id == "activate"
        assert launch.simulation.capacity_constraint == "activation"
        with pytest.raises(LifecyclePlanningError) as failure:
            build_next_transaction(client, launch)
        assert failure.value.code == "PLAN_NOT_ADMITTED"


def test_opaque_atomic_capacity_failure_selects_staged_and_proves_its_activation():
    draft, provider, client = scenario(released_draft(conversion=True),
        compute_gas_by_kind={"atomic": 32_000_001, "begin": 100_000,
            "prepare": 100_000, "activate": 100_000})
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode="atomic"))
    assert launch.admitted and launch.mode == "staged"
    assert launch.simulation.gas_profile is None
    assert [len(payload["blockStateCalls"]) for payload in provider.simulations] == [1, 3]
    assert all(payload["validation"] for payload in provider.simulations)
    assert compute_lifecycle_launch_gas_bounds(launch.plan, predicted_token=launch.predicted_token) is None


def test_approval_uses_reviewed_identity_but_leaves_envelope_preparation_to_wallet():
    from test_lifecycle_read_counts import NativeBuyScenario, draft_plan
    from test_ufg import terms
    draft = draft_plan()
    draft = replace(draft, funding=tuple(replace(funding, kind=0) for funding in draft.funding))
    provider = NativeBuyScenario(draft, raw_version=116)
    client = CountingTransport(provider)
    launch = prepare(client, draft, ufg=terms())
    assert launch.admitted
    approval = launch.transactions[0]
    assert approval.kind == "approve" and approval.admission.admitted
    assert approval.gas_limit is approval.gas_price is approval.estimate is None
    identity = {"id": approval.step_id, "kind": approval.kind, **approval.as_transaction()}
    current = build_next_transaction(client, launch, reviewed_transaction=identity)
    assert current.as_transaction() == approval.as_transaction()
    assert current.gas_limit is current.gas_price is current.estimate is None
    assert current.admission.admitted and provider.simulations == []


@pytest.mark.parametrize("headroom", [-1, True, 10001, 0.5])
def test_capacity_bounds_reject_invalid_headroom_without_rpc(headroom):
    draft = released_draft()
    with pytest.raises(LifecyclePlanningError) as failure:
        compute_lifecycle_launch_gas_bounds(draft, predicted_token=PRINCIPAL, headroom_bps=headroom)
    assert failure.value.code == "INVALID_LIMITS"
