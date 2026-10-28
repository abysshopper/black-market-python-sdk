"""Opening amounts from retained isolated-fork receipts, not Nitro gas proof."""
from dataclasses import replace

import pytest

from black_market_sdk import (
    KnownLifecycleProfile, LaunchPlanV1, LifecycleInitialBuy, LifecycleMarketConfig,
    LifecyclePlanningError, LifecycleTokenConfig, compute_lifecycle_opening_buys,
    encode_launch_plan, get_known_lifecycle_profile, get_launch_addresses,
)
from black_market_sdk.lifecycle_buy_math import _OpeningState, _input_price, _next_tick, _swap

CREATOR = "0x00000000000000000000000000000000000A11cE"
QUOTE = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
PREDICTED = "0xDE2BC222D9A78ddbdabe09e346D33493524d9281"
# Identical committed curve in both retained Abyss-0 InitialBuyExecuted cases at
# source L2 block 100115534, hash 0x0d5bab455e8513101367cf71a06ad1e3c24370f6e8efb8ef873fdf75be96c3bb.
# Expected amounts below are receipt observations, not this helper's output.
ABYSS_CONFIG = "0x000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000bb80000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000a00000000000000000000000000000000000000000000000000000000000000001fffffffffffffffffffffffffffffffffffffffffffffffffffffffffff2764c000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000d3c21bcecceda100d45100000000000000000000000000000000000000000000d3c21bcecceda1000000"


def curve_plan(amount=10**15):
    orchestrator = get_launch_addresses(4663).orchestrator
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=orchestrator,
        key=KnownLifecycleProfile.ABYSS_0)
    market = LifecycleMarketConfig(preset.profile.registration.adapter_id,
        preset.profile.id, QUOTE, 10**24, 1, ABYSS_CONFIG)
    buy = LifecycleInitialBuy(0, amount, 1, CREATOR, 4_295_128_740)
    return LaunchPlanV1(chain_id=4663, orchestrator=orchestrator, creator=CREATOR, nonce=1,
        token=LifecycleTokenConfig(0, 0, "Curve", "CURVE", 10**24, 0, "", bytes(32), CREATOR, False),
        funding=(), fee_assets=(), markets=(market,), buys=(buy,), deadline=4_000_000_000,
        executor_fee_bps=0)


@pytest.mark.parametrize("amount,expected", [
    (10**15, 996_999_999_005_991),
    (500_000_000_000_000_000_000_000, 332_665_999_332_665_999_338_681),
])
def test_opening_amounts_match_independent_activation_receipts(amount, expected):
    quote, = compute_lifecycle_opening_buys(curve_plan(amount), predicted_token=PREDICTED)
    assert quote.quote_amount_in == quote.quote_spent == amount
    assert quote.token_out == expected
    assert quote.buy_index == quote.market_index == 0


def test_ordered_buys_share_mutable_curve_without_mutating_the_plan():
    request = curve_plan()
    request = replace(request, buys=(request.buys[0], replace(request.buys[0],
        quote_amount_in=500_000_000_000_000_000_000_000)))
    original = encode_launch_plan(request)
    first, second = compute_lifecycle_opening_buys(request, predicted_token=PREDICTED)
    assert first.token_out == 996_999_999_005_991
    assert second.token_out < 332_665_999_332_665_999_338_681
    assert second.sqrt_price_after_x96 < first.sqrt_price_after_x96
    assert (first.buy_index, second.buy_index) == (0, 1)
    assert encode_launch_plan(request) == original


@pytest.mark.parametrize("tick,zero_for_one,expected", [
    (-1, True, -256), (-257, True, -300), (-257, False, -1),
])
def test_negative_bitmap_floor_preserves_uninitialized_word_steps(tick, zero_for_one, expected):
    state = _OpeningState(1 << 96, tick, 1, 1, 3000, 0, {-300: 1, 0: -1}, [-300, 0])
    assert _next_tick(state, zero_for_one) == expected


def test_fee_dust_does_not_reset_the_last_crossed_tick():
    liquidity = 1 << 112
    state = _OpeningState(1 << 96, -1, liquidity, 60, 3000, 0,
        {-60: liquidity, 0: -liquidity}, [-60, 0])
    assert _swap(state, 1, 4_295_128_740, True) == (0, 1)
    assert state.price == 1 << 96 and state.tick == -1 and state.liquidity == liquidity


def test_uint256_overflow_uses_the_canonical_rounded_denominator_branch():
    # Algebraically exact ceil((2**208 - 2**96) / (2**98 + 2**49));
    # multiplying the input by this valid uint160 price overflows uint256.
    expected = (1 << 110) - (1 << 61) + 4096
    assert _input_price(1 << 159, (1 << 112) - 1, (1 << 98) + 1, True) == expected


@pytest.mark.parametrize("changes,code", [
    ({"market_index": -1}, "INVALID_BUY"),
    ({"market_index": True}, "INVALID_BUY"),
    ({"market_index": 1}, "INVALID_BUY"),
    ({"quote_amount_in": 0}, "INVALID_BUY"),
    ({"quote_amount_in": True}, "INVALID_BUY"),
    ({"quote_amount_in": 1 << 255}, "INVALID_BUY"),
    ({"sqrt_price_limit_x96": 1 << 96}, "INVALID_BUY_PRICE_LIMIT"),
    ({"sqrt_price_limit_x96": 4_295_128_739}, "INVALID_BUY_PRICE_LIMIT"),
])
def test_public_curve_boundary_rejects_invalid_inputs(changes, code):
    request = curve_plan()
    request = replace(request, buys=(replace(request.buys[0], **changes),))
    with pytest.raises(LifecyclePlanningError) as failure:
        compute_lifecycle_opening_buys(request, predicted_token=PREDICTED)
    assert failure.value.code == code


def test_unknown_adapter_cannot_claim_release_owned_curve_behavior():
    request = curve_plan()
    request = replace(request, markets=(replace(request.markets[0], adapter_id=bytes([255]) * 32),))
    with pytest.raises(LifecyclePlanningError) as failure:
        compute_lifecycle_opening_buys(request, predicted_token=PREDICTED)
    assert failure.value.code == "OPENING_BUY_NOT_PROFILED"


def test_v4_zero_limit_uses_the_directional_sentinel_without_rewriting_calldata():
    from black_market_sdk import ROBINHOOD_WETH
    from test_lifecycle_read_reduction import ordinary_bound_draft
    request = ordinary_bound_draft(1)
    request = replace(request, markets=(replace(request.markets[0], quote_asset=ROBINHOOD_WETH),),
        buys=(LifecycleInitialBuy(0, 10**15, 1, request.creator, 0),))
    predicted = PREDICTED
    original = encode_launch_plan(request)
    quote, = compute_lifecycle_opening_buys(request, predicted_token=predicted)
    explicit = replace(request, buys=(replace(request.buys[0],
        sqrt_price_limit_x96=4_295_128_740),))
    assert compute_lifecycle_opening_buys(explicit, predicted_token=predicted) == (quote,)
    assert quote.token_out > 0 and encode_launch_plan(request) == original


def test_abyss_zero_limit_is_not_a_v4_sentinel():
    request = curve_plan()
    request = replace(request, buys=(replace(request.buys[0], sqrt_price_limit_x96=0),))
    with pytest.raises(LifecyclePlanningError) as failure:
        compute_lifecycle_opening_buys(request, predicted_token=PREDICTED)
    assert failure.value.code == "INVALID_BUY_PRICE_LIMIT"
