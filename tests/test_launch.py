"""Pure TickMath, launch range construction and position-topology guards."""

import pytest

from black_market_sdk import (
    assert_launch_topology_coverage, derive_launch_pool_recipe, derive_launch_position_ranges,
    get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio,
)


@pytest.mark.parametrize("fee,spacing", [
    (100, 1), (500, 10), (3_000, 60), (10_000, 200),
    (20_000, 400), (50_000, 1_000), (100_000, 2_000), (150_000, 3_000),
])
@pytest.mark.parametrize("token0", [True, False])
def test_abyss_recipe_infers_canonical_spacing_and_one_sided_geometry(fee, spacing, token0):
    options = dict(paired_token_decimals=6, paired_token_usd_price_x18=10**18,
        target_market_cap_usd_x18=5_000 * 10**18, launched_token_is_quote=token0,
        fee=fee, supply=10**24, token_budget=5 * 10**23)
    recipe = derive_launch_pool_recipe(**options)
    assert recipe == derive_launch_pool_recipe(**options, tick_spacing=spacing)
    assert recipe.tick_lower % spacing == recipe.tick_upper % spacing == 0
    assert (recipe.tick_upper if token0 else recipe.tick_lower) == (
        887272 // spacing * spacing * (1 if token0 else -1))
    assert recipe.launch_sqrt_price_x96 == get_sqrt_ratio_at_tick(
        recipe.tick_lower if token0 else recipe.tick_upper)
    assert recipe.liquidity > 0
    assert recipe.launched_token_amount_maximum == options["token_budget"]
    assert recipe.paired_token_amount_maximum == 0


def test_abyss_recipe_rejects_unknown_fee_without_explicit_spacing():
    with pytest.raises(ValueError, match="fee is not an Abyss fee tier"):
        derive_launch_pool_recipe(paired_token_decimals=18,
            paired_token_usd_price_x18=10**18, target_market_cap_usd_x18=5_000 * 10**18,
            launched_token_is_quote=True, fee=101)


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


def ranges(*, token0=True, venue="abyss", preset="staircase", **options):
    return derive_launch_position_ranges(launch_sqrt_price_x96=1 << 96,
        launched_token_is_quote=token0, token_budget=10**24, tick_spacing=60,
        venue=venue, distribution_preset=preset, **options)


def bands(intervals, token0):
    return [dict(tick_lower=lower if token0 else -upper,
        tick_upper=upper if token0 else -lower, max_token_amount=cap)
        for lower, upper, cap in intervals]


def validate(positions, token0, **options):
    assert_launch_topology_coverage(positions, opening_tick=0, tick_spacing=60,
        launched_token_is_quote=token0, **options)


def test_topology_validator_is_exported_from_root_and_lifecycle():
    from black_market_sdk.lifecycle import assert_launch_topology_coverage as lifecycle_validator
    assert lifecycle_validator is assert_launch_topology_coverage


@pytest.mark.parametrize("venue", ["abyss", "uniswap-v4"])
@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("preset", ["early-scarcity", "staircase", "smooth-ramp"])
def test_default_range_tail_and_contiguous_topology_on_both_venues_and_orientations(venue, token0, preset):
    result = ranges(token0=token0, venue=venue, preset=preset, position_gaps="adjacent")
    tail = result.positions[-1]
    assert (tail.tick_upper if token0 else tail.tick_lower) == result.boundary_tick
    assert result.boundary_tick == (887220 if token0 else -887220)
    assert tail.max_token_amount > 0
    assert sum(position.max_token_amount for position in result.positions) == 10**24
    assert (result.opening_active_liquidity > 0) is token0
    assert result.buy_side_liquidity > 0
    validate(result.positions, token0)


@pytest.mark.parametrize("token0", [True, False])
def test_explicit_finite_final_range_still_constructs_but_fails_topology(token0):
    result = ranges(token0=token0, position_gaps="adjacent", extend_final_range_to_boundary=False)
    tail = result.positions[-1]
    assert (tail.tick_upper if token0 else tail.tick_lower) != result.boundary_tick
    with pytest.raises(ValueError, match="MISSING_PERMANENT_TAIL"):
        validate(result.positions, token0)


@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("intervals", [[(0, 120, 1)], [(0, 120, 1), (120, 887220, 0)]])
@pytest.mark.parametrize("allow_gaps", [False, True])
def test_missing_or_zero_allocation_tail_is_always_rejected(token0, intervals, allow_gaps):
    with pytest.raises(ValueError, match="MISSING_PERMANENT_TAIL"):
        validate(bands(intervals, token0), token0, allow_interior_gaps=allow_gaps)


@pytest.mark.parametrize("token0", [True, False])
def test_terminal_gap_before_permanent_tail_is_rejected(token0):
    with pytest.raises(ValueError, match="TOPOLOGY_INTERIOR_GAP"):
        validate(bands([(0, 120, 1), (180, 887220, 1)], token0), token0)


@pytest.mark.parametrize("token0", [True, False])
def test_interior_gap_requires_explicit_opt_out(token0):
    positions = bands([(0, 120, 1), (180, 240, 1), (240, 887220, 1)], token0)
    with pytest.raises(ValueError, match="TOPOLOGY_INTERIOR_GAP"):
        validate(positions, token0)
    validate(positions, token0, allow_interior_gaps=True)


@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("allow_gaps", [False, True])
def test_off_grid_tick_is_always_rejected(token0, allow_gaps):
    with pytest.raises(ValueError, match="TOPOLOGY_OFF_GRID_TICK"):
        validate(bands([(0, 121, 1), (120, 887220, 1)], token0), token0,
            allow_interior_gaps=allow_gaps)


@pytest.mark.parametrize("token0", [True, False])
def test_unsorted_orbit_style_overlaps_cover_union_without_mutating_input(token0):
    positions = bands([(300, 887220, 1), (120, 360, 1), (-120, -60, 1), (0, 240, 1)], token0)
    original = [dict(position) for position in positions]
    validate(positions, token0)
    assert positions == original


@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("intervals", [[], [(60, 887220, 1)], [(-120, 0, 1), (60, 887220, 1)]])
@pytest.mark.parametrize("allow_gaps", [False, True])
def test_opening_containment_is_orientation_aware_and_cannot_be_waived(token0, intervals, allow_gaps):
    with pytest.raises(ValueError, match="TOPOLOGY_OPENING_GAP"):
        validate(bands(intervals, token0), token0, allow_interior_gaps=allow_gaps)


@pytest.mark.parametrize("token0", [True, False])
def test_opening_tick_need_not_be_spacing_aligned(token0):
    assert_launch_topology_coverage(bands([(0, 887220, 1)], token0), opening_tick=1 if token0 else -1,
        tick_spacing=60, launched_token_is_quote=token0)


@pytest.mark.parametrize("preset", ["early-scarcity", "staircase"])
@pytest.mark.parametrize("token0", [True, False])
def test_position_gaps_adjacent_preserves_source_weights_and_widths(preset, token0):
    options = dict(preset=preset, token0=token0, extend_final_range_to_boundary=False)
    source = ranges(**options)
    assert source == ranges(**options, position_gaps="source-gaps")
    adjacent = ranges(**options, position_gaps="adjacent")
    for before, after in zip(source.positions, adjacent.positions):
        assert before.inventory_bps == after.inventory_bps
        assert before.max_token_amount == after.max_token_amount
        assert before.tick_upper - before.tick_lower == after.tick_upper - after.tick_lower
    ordered = sorted(adjacent.positions, key=lambda position: position.tick_lower)
    assert all(left.tick_upper == right.tick_lower for left, right in zip(ordered, ordered[1:]))
    with pytest.raises(ValueError, match="TOPOLOGY_INTERIOR_GAP"):
        validate(ranges(preset=preset, token0=token0).positions, token0)
    validate(ranges(preset=preset, token0=token0).positions, token0, allow_interior_gaps=True)


def test_smooth_ramp_is_contiguous_and_rejects_explicit_source_gaps():
    default = ranges(preset="smooth-ramp")
    assert default == ranges(preset="smooth-ramp", position_gaps="adjacent")
    validate(default.positions, True)
    with pytest.raises(ValueError, match="smooth-ramp does not support source-gaps"):
        ranges(preset="smooth-ramp", position_gaps="source-gaps")
