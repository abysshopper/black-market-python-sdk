"""Transport-level profile/one-proof behavior, not live native admission."""
import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from black_market_sdk import (
    AuctionQuoteOption, LifecycleAssetFunding, LifecycleInitialBuy,
    LifecyclePlanningError, LaunchExecutionLimits, ROBINHOOD_WETH,
    build_next_transaction, prepare_and_plan_lifecycle_launch, simulate_launch_plan,
)
from test_lifecycle_read_counts import CountingTransport
from test_lifecycle_read_reduction import ResumeScenario, ordinary_bound_draft
from test_ufg import PRINCIPAL, ZERO


def released_draft(count=1, *, buy=False, conversion=False):
    draft = ordinary_bound_draft(count)
    draft = replace(draft, markets=tuple(replace(market, quote_asset=ROBINHOOD_WETH)
        for market in draft.markets))
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
    assert set(launch.simulation.gas_profile.estimates) == {transaction.step_id for transaction in launch.transactions}
    immediate = launch.transactions[0]
    assert immediate.estimate.gas_used is None
    assert immediate.estimate.fee_confidence == "profiled-execution-and-data"
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
    transaction = launch.transactions[0]
    basis = 8_750_000 + 24 * transaction.calldata_bytes + 100_000 * len(draft.fee_assets) + 22_000 * 5
    assert transaction.gas_limit == (basis * 11_500 + 9_999) // 10_000
    assert launch.admitted and provider.simulations == []


def test_conversion_review_proves_activation_once_and_next_build_is_immediate_only(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft, provider, client = scenario(released_draft(conversion=True))
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == len(launch.transactions) == 3
    assert all(step.compute_gas_required is None for step in launch.simulation.steps)
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


def test_opaque_conversion_uses_native_ceiling_once_without_resizing_a_held_envelope():
    draft, provider, client = scenario(released_draft(conversion=True),
        compute_gas_by_kind={"begin": 28_000_000, "prepare": 100_000, "activate": 100_000})
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 3
    assert launch.transactions[0].gas_limit == 32_000_000
    assert launch.transactions[0].gas_used == 28_000_000
    assert launch.simulation.gas_profile.estimates["begin"].gas_limit == 32_000_000
    held = replace(launch.transactions[0], gas_limit=30_000_000)
    provider.simulations.clear()
    current = build_next_transaction(client, launch, reviewed_transaction=held)
    assert current.as_transaction() == held.as_transaction()
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 1


def test_a_later_conversion_activation_failure_is_not_admitted_before_begin():
    draft, provider, client = scenario(released_draft(conversion=True), replay_failure="missing-activation")
    launch = prepare(client, draft)
    assert not launch.admitted and launch.simulation.failed_transaction_id == "activate"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 3


@pytest.mark.parametrize("options", [{"balance": 1_000_000}, {"tx_cap": 1_000_000, "block_cap": 1_000_000}])
def test_conservative_profile_boundary_uses_one_capped_exact_proof(options):
    draft, provider, client = scenario(**options)
    launch = prepare(client, draft)
    assert launch.admitted and launch.simulation.execution_proof == "proved"
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert all(step.compute_gas_required is None for step in launch.simulation.steps)
    first = launch.transactions[0]
    assert first.gas_used == provider.compute_gas
    assert first.gas_limit < launch.simulation.gas_profile.estimates[first.step_id].gas_limit
    assert int(provider.simulations[0]["blockStateCalls"][0]["calls"][0]["gas"], 16) == first.gas_limit
    assert "nonce" not in first.as_transaction()


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
