"""Deterministic preparation packing and indivisible activation budgets."""
import asyncio
from dataclasses import replace

import pytest

from black_market_sdk import (
    KnownLifecycleProfile, LifecycleAbyssMarketConfig, LifecycleAbyssPosition,
    LifecycleAssetFunding, LifecycleFeeAssetPolicy, LifecycleInitialBuy, LifecycleV4Position,
    LifecyclePlanningError, ROBINHOOD_WETH, build_launch_transactions,
    build_next_transaction, derive_launch_position_ranges,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_abyss_market_config, encode_lifecycle_pool_bound_v4_market_config,
    get_known_lifecycle_profile, get_sqrt_ratio_at_tick, prepare_and_plan_lifecycle_launch,
)
from black_market_sdk.lifecycle import _groups
from test_hmm import CORE, hmm_config
from test_lifecycle import plan
from test_lifecycle_profiled_gas import assert_no_execution
from test_lifecycle_read_counts import CountingTransport
from test_lifecycle_read_reduction import ResumeScenario, ordinary_bound_draft
from test_ufg import PRINCIPAL, TOKEN, ZERO


def hmm_plan(count, *, dynamic=False):
    original = plan()
    markets = []
    for index in range(count):
        key = KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL if dynamic else KnownLifecycleProfile.V4_HMM_FIXED_POOL
        preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
        config = hmm_config(preset.profile.id)
        budget = sum(position.max_token_amount for position in config.positions)
        markets.append(replace(original.markets[0], profile_id=preset.profile.id,
            adapter_id=preset.profile.registration.adapter_id, quote_asset=f"0x{512 + index:040x}",
            config_version=6, token_budget=budget,
            config=encode_lifecycle_pool_bound_v4_market_config(config)))
    return replace(original, chain_id=4663, orchestrator=CORE,
        token=replace(original.token, supply=sum(market.token_budget for market in markets)),
        markets=tuple(markets), funding=(), buys=())


def taze_shape():
    """Node's synthetic holders-earn shape, not a native execution observation."""
    draft = ordinary_bound_draft(3)
    quotes = ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", ROBINHOOD_WETH,
        "0xffffffffffffffffffffffffffffffffffffffff")
    unit = 10**18
    budgets = (700_000_000 * unit, 150_000_000 * unit, 150_000_000 * unit)
    markets = []
    for index, market in enumerate(draft.markets):
        ranges = derive_launch_position_ranges(launch_sqrt_price_x96=1 << 96,
            launched_token_is_quote=int(TOKEN, 16) < int(quotes[index], 16), token_budget=budgets[index], tick_spacing=60,
            venue="abyss" if index == 0 else "uniswap-v4", distribution_preset="staircase",
            position_count=8, position_gaps="adjacent")
        if index == 0:
            preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=KnownLifecycleProfile.ABYSS_3)
            positions = tuple(LifecycleAbyssPosition(position.tick_lower, position.tick_upper,
                position.liquidity, position.max_token_amount) for position in ranges.positions)
            config = encode_lifecycle_abyss_market_config(LifecycleAbyssMarketConfig(3, 3000,
                (9).to_bytes(32, "big"), 1 << 96, positions))
            version = 1
        else:
            preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE,
                key=KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL)
            envelope = preset.profile.envelope
            config = decode_lifecycle_pool_bound_v4_market_config(market.config)
            positions = tuple(LifecycleV4Position(position.tick_lower, position.tick_upper,
                position.liquidity, bytes(32), position.max_token_amount) for position in ranges.positions)
            config = encode_lifecycle_pool_bound_v4_market_config(replace(config,
                profile_id=preset.profile.id, lp_fee_pips=0, tick_spacing=60, sqrt_price_x96=1 << 96,
                hook_fee_pips=100000, minimum_hook_fee_pips=10000, fee_sensitivity_pips_seconds_per_tick=6000,
                fee_mode=1, protocol_fee_denominator=envelope.protocol_fee_denominator,
                treasury=envelope.protocol_treasury, external_liquidity_disabled=False,
                oracle_config_id=(9).to_bytes(32, "big"), terms_digest=envelope.terms_digest,
                developer_beneficiary=envelope.beneficiary, developer_fee_bps=0,
                hook_salt=bytes(32), positions=positions))
            version = 6
        markets.append(replace(market, adapter_id=preset.profile.registration.adapter_id,
            profile_id=preset.profile.id, quote_asset=quotes[index], token_budget=budgets[index],
            config_version=version, config=config))
    buys = tuple(LifecycleInitialBuy(index, 900000000000000 if index == 1 else unit, 1, PRINCIPAL,
        get_sqrt_ratio_at_tick(887272) - 1 if index == 0 else 0)
        for index in range(3))
    funding = tuple(sorted((LifecycleAssetFunding(quote, buys[index].quote_amount_in,
        1 if index == 1 else 2, quote if index == 1 else ZERO,
        (4200000000000000, 900000000000000, 900000000000000)[index],
        ZERO if index == 1 else "0x8888888888888888888888888888888888888888",
        b"" if index == 1 else b"\x12\x34") for index, quote in enumerate(quotes)),
        key=lambda item: int(item.asset, 16)))
    policies = tuple(LifecycleFeeAssetPolicy(asset, 8000, 2000, 0)
        for asset in sorted((TOKEN, *quotes), key=lambda item: int(item, 16)))
    return replace(draft, token=replace(draft.token, reward_mode=2, supply=1_000_000_000 * unit,
        burn_on_cancel=True), fee_assets=policies, markets=tuple(markets), buys=buys,
        funding=funding, deadline=253402300799, executor_fee_bps=10)


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_hmm_preparation_headroom_keeps_each_market_inside_the_native_cap(count):
    request = hmm_plan(count)
    assert _groups(request, 0, 32_000_000) == [(index, 1) for index in range(count)]
    assert _groups(request, 0, 32_000_000, 0) == [(index, 1) for index in range(count)]


def test_preparation_rejects_an_individually_oversized_market_without_splitting_its_positions():
    with pytest.raises(LifecyclePlanningError) as failure:
        _groups(hmm_plan(1), 0, 1_000_000)
    assert failure.value.code == "PREPARATION_GAS_LIMIT"


def test_pure_compiler_keeps_explicit_ordered_batches_and_indivisible_activation():
    request = hmm_plan(5)
    commands = build_launch_transactions(request, mode="staged", preparation_batches=(1, 3, 1))
    assert [(command.first_market, command.market_count) for command in commands if command.kind == "prepare"] == [(0, 1), (1, 3), (4, 1)]
    assert [command.kind for command in commands].count("activate") == 1
    assert all(command.gas_limit is command.gas_price is command.assessment is None for command in commands)


def test_taze_shape_is_staged_with_two_one_batches_and_zero_launch_execution():
    draft = taze_shape()
    provider = ResumeScenario(draft)
    client = CountingTransport(provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=PRINCIPAL, mode="atomic", buy_slippage_bps=50))
    assert launch.assessment.ready, launch.assessment.reason
    assert launch.mode == "staged"
    assert [(command.first_market, command.market_count) for command in launch.transactions
        if command.kind == "prepare"] == [(0, 2), (2, 1)]
    assert launch.assessment.gas_profile.bounds.atomic_compute_gas_upper_bound is None
    assert all(buy.min_token_out > 1 for buy in launch.plan.buys)
    from black_market_sdk import launch_plan_to_dict
    assert launch_plan_to_dict(launch.plan)["funding"] == launch_plan_to_dict(draft)["funding"]
    assert launch.transactions[0].gas_limit == 32_000_000
    provider.plan = launch.plan
    provider.latest_phase = provider.confirmed_phase = 1
    next_command = build_next_transaction(client, launch)
    assert (next_command.first_market, next_command.market_count) == (0, 2)
    provider.prepared = 2
    next_command = build_next_transaction(client, launch)
    assert (next_command.first_market, next_command.market_count) == (2, 1)
    assert_no_execution(client)


def test_measured_abyss_preparation_cost_and_calldata_padding_prevent_three_market_batch():
    draft = taze_shape()
    config = LifecycleAbyssMarketConfig(3, 3000, (9).to_bytes(32, "big"), 1 << 96,
        (LifecycleAbyssPosition(0, 887220, 10**18, draft.markets[0].token_budget),))
    first = replace(draft.markets[0], config=encode_lifecycle_abyss_market_config(config))
    markets = tuple(replace(first, quote_asset=market.quote_asset,
        config=encode_lifecycle_abyss_market_config(replace(config, positions=tuple(
            replace(position, tick_lower=-position.tick_upper, tick_upper=-position.tick_lower)
            for position in config.positions))) if int(market.quote_asset, 16) < int(TOKEN, 16) else first.config)
        for market in draft.markets)
    draft = replace(draft, markets=markets,
        token=replace(draft.token, supply=sum(market.token_budget for market in markets)), buys=(), funding=())
    provider = ResumeScenario(draft)
    client = CountingTransport(provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft, account=PRINCIPAL, mode="staged"))
    assert launch.assessment.ready, launch.assessment.reason
    assert [command.market_count for command in launch.transactions if command.kind == "prepare"] == [2, 1]
    # Native one-position Abyss preparation observed9,723,187 compute. The
    # budget must reserve this measured cost before at least25% padding.
    from black_market_sdk.lifecycle import _prepare_gas_estimate
    assert _prepare_gas_estimate(launch.plan, 0) >= 9_723_187
    assert_no_execution(client)


def test_oversized_indivisible_activation_refuses_before_begin_without_execution():
    from black_market_sdk import decode_lifecycle_abyss_market_config
    draft = taze_shape()
    market = draft.markets[0]
    config = decode_lifecycle_abyss_market_config(market.config)
    positions = tuple(replace(position, liquidity=position.liquidity // 4,
        token_amount_maximum=position.token_amount_maximum // 4)
        for _ in range(4) for position in config.positions)
    missing = market.token_budget - sum(position.token_amount_maximum for position in positions)
    positions = (*positions[:-1], replace(positions[-1], token_amount_maximum=positions[-1].token_amount_maximum + missing))
    market = replace(market, config=encode_lifecycle_abyss_market_config(replace(config, positions=positions)))
    draft = replace(draft, markets=(market,), token=replace(draft.token, supply=market.token_budget), funding=(), buys=())
    provider = ResumeScenario(draft)
    client = CountingTransport(provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft, account=PRINCIPAL, mode="atomic"))
    assert not launch.assessment.ready
    assert launch.assessment.code == "ACTIVATION_GAS_LIMIT"
    assert launch.assessment.capacity_constraint == "activation"
    assert "Reduce" in launch.assessment.reason
    provider.plan = launch.plan
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, launch)
    assert failure.value.code == "ACTIVATION_GAS_LIMIT"
    assert_no_execution(client)
