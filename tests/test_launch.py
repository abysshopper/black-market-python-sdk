"""Standalone TickMath remains available independently of launch templates."""

import pytest

from black_market_sdk import get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio


def test_tick_math_bounds():
    assert get_sqrt_ratio_at_tick(0) == 1 << 96
    assert get_tick_at_sqrt_ratio(1 << 96) == 0
    assert get_sqrt_ratio_at_tick(-887272) == 4295128739
    assert get_sqrt_ratio_at_tick(887272) == 1461446703485210103287273052203988822378723970342
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(887273)
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(-887273)
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(4295128738)
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(1461446703485210103287273052203988822378723970342)


@pytest.mark.parametrize("tick", [-887272, -60000, -1, 0, 1, 60000, 887271])
def test_inverse_tick_is_floor_at_exact_and_between_adjacent_ratios(tick):
    price = get_sqrt_ratio_at_tick(tick)
    next_price = get_sqrt_ratio_at_tick(tick + 1)
    assert get_tick_at_sqrt_ratio(price) == tick
    assert get_tick_at_sqrt_ratio(next_price - 1) == tick
