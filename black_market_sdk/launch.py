"""Exact integer launch recipes, mint ceilings, buy estimates and TickMath."""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Literal

from .abyss import ABYSS_FEE_TIERS
from .auction import AUCTION_SUPPLY

LAUNCH_REWARD_DURATION = 7 * 24 * 60 * 60
LAUNCH_ORACLE_CONFIG_ID = "0xc0e9bed88d70a13fd3ab31451fefdd073b7266e838aee0ad1c236c8c9eff855d"
LAUNCH_DEADLINE_SECONDS = 20 * 60
LAUNCH_BUY_SLIPPAGE_BPS = 50
DEFAULT_LAUNCH_TARGET_MARKET_CAP_USD = 5_000

LaunchDistributionPreset = Literal["early-scarcity", "staircase", "smooth-ramp"]
LaunchStaircaseBoundaries = Literal["source-gaps", "adjacent"]
LaunchVenue = Literal["abyss", "uniswap-v4"]

_Q32 = 1 << 32
_MAX_UINT256 = (1 << 256) - 1
_MIN_TICK = -887_272
_MAX_TICK = 887_272
_MIN_SQRT_RATIO = 4_295_128_739
_MAX_SQRT_RATIO = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342

# Canonical TickMath multipliers, matching contracts/src/oracles/TickMathLib.sol.
_TICK_MULTIPLIERS = (
    0xFFFCB933BD6FAD37AA2D162D1A594001,
    0xFFF97272373D413259A46990580E213A,
    0xFFF2E50F5F656932EF12357CF3C7FDCC,
    0xFFE5CACA7E10E4E61C3624EAA0941CD0,
    0xFFCB9843D60F6159C9DB58835C926644,
    0xFF973B41FA98C081472E6896DFB254C0,
    0xFF2EA16466C96A3843EC78B326B52861,
    0xFE5DEE046A99A2A811C461F1969C3053,
    0xFCBE86C7900A88AEDCFFC83B479AA3A4,
    0xF987A7253AC413176F2B074CF7815E54,
    0xF3392B0822B70005940C7A398E4B70F3,
    0xE7159475A2C29B7443B29C7FA6E889D9,
    0xD097F3BDFD2022B8845AD8F792AA5825,
    0xA9F746462D870FDF8A65DC1F90E061E5,
    0x70D869A156D2A1B890BB3DF62BAF32F7,
    0x31BE135F97D08FD981231505542FCFA6,
    0x9AA508B5B7A84E1C677DE54F3E99BC9,
    0x5D6AF8DEDB81196699C329225EE604,
    0x2216E584F5FA1EA926041BEDFE98,
    0x48A170391F7DC42444E8FA2,
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    if isinstance(tick, bool) or not isinstance(tick, int) or not _MIN_TICK <= tick <= _MAX_TICK:
        raise ValueError("Tick is outside the canonical TickMath range")
    absolute_tick = abs(tick)
    ratio = _TICK_MULTIPLIERS[0] if absolute_tick & 1 else 0x100000000000000000000000000000000
    for bit in range(1, len(_TICK_MULTIPLIERS)):
        if absolute_tick & (1 << bit):
            ratio = (ratio * _TICK_MULTIPLIERS[bit]) >> 128
    if tick > 0:
        ratio = _MAX_UINT256 // ratio
    return (ratio >> 32) + (0 if ratio % _Q32 == 0 else 1)


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    if isinstance(sqrt_price_x96, bool) or not isinstance(sqrt_price_x96, int) or not _MIN_SQRT_RATIO <= sqrt_price_x96 < _MAX_SQRT_RATIO:
        raise ValueError("sqrt_price_x96 is outside the canonical swap price bounds")
    low, high = _MIN_TICK, _MAX_TICK
    while low < high:
        middle = (low + high + 1) // 2
        if get_sqrt_ratio_at_tick(middle) <= sqrt_price_x96:
            low = middle
        else:
            high = middle - 1
    return low


@dataclass(frozen=True)
class LaunchPoolRecipe:
    launch_tick: int
    launch_sqrt_price_x96: int
    liquidity: int
    launched_token_amount_maximum: int
    paired_token_amount_maximum: int
    tick_lower: int
    tick_upper: int


@dataclass(frozen=True)
class LaunchPositionRange:
    tick_lower: int
    tick_upper: int
    sqrt_price_lower_x96: int
    sqrt_price_upper_x96: int
    inventory_bps: int
    allocated_token_amount: int
    liquidity: int
    max_token_amount: int
    max_quote_amount: int = 0


@dataclass(frozen=True)
class LaunchPositionRanges:
    launch_tick: int
    launch_sqrt_price_x96: int
    boundary_tick: int
    boundary_sqrt_price_x96: int
    positions: tuple[LaunchPositionRange, ...]
    token_amount_maximum: int
    unspent_token_amount: int
    total_liquidity: int
    opening_active_liquidity: int
    buy_side_liquidity: int
    max_liquidity_per_tick: int


@dataclass(frozen=True)
class LaunchInitialBuyEstimate:
    launched_token_amount_out: int
    paired_token_amount_consumed: int
    sqrt_price_after_x96: int


def _integer(value: int, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _orientation(value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError("launched_token_is_quote must be a boolean")


def _launch_band(launch_tick: int, spacing: int, token0: bool) -> tuple[int, int]:
    lower = -(-launch_tick // spacing) * spacing if token0 else -(-_MIN_TICK // spacing) * spacing
    upper = _MAX_TICK // spacing * spacing if token0 else launch_tick // spacing * spacing
    if lower < _MIN_TICK or upper > _MAX_TICK or lower >= upper:
        raise ValueError("launch_tick cannot form a valid one-sided launch band")
    return lower, upper


def derive_launch_pool_recipe(
    *, paired_token_decimals: int, paired_token_usd_price_x18: int,
    target_market_cap_usd_x18: int, launched_token_is_quote: bool, fee: int,
    supply: int = AUCTION_SUPPLY, token_budget: int | None = None,
    tick_spacing: int | None = None,
) -> LaunchPoolRecipe:
    """Derive a spacing-aligned one-sided pool from an exact rational FDV.

    The historical ``launched_token_is_quote=True`` convention means token0.
    No display floats or external price/balance observations enter this recipe.
    """
    _integer(paired_token_decimals, 0, 255, "paired_token_decimals")
    _integer(paired_token_usd_price_x18, 1, _MAX_UINT256, "paired_token_usd_price_x18")
    _integer(target_market_cap_usd_x18, 1, _MAX_UINT256, "target_market_cap_usd_x18")
    _orientation(launched_token_is_quote)
    budget = supply if token_budget is None else token_budget
    _integer(supply, 1, _MAX_UINT256, "supply")
    _integer(budget, 1, _MAX_UINT256, "token_budget")
    if budget > supply:
        raise ValueError("token_budget cannot exceed supply")
    if tick_spacing is None:
        tier = next((tier for tier in ABYSS_FEE_TIERS if tier.fee_pips == fee), None)
        if tier is None:
            raise ValueError("fee is not an Abyss fee tier")
        tick_spacing = tier.tick_spacing
    _integer(tick_spacing, 1, 32767, "tick_spacing")
    pair_unit = 10**paired_token_decimals
    numerator, denominator = (
        (target_market_cap_usd_x18 * pair_unit, paired_token_usd_price_x18 * supply)
        if launched_token_is_quote else
        (paired_token_usd_price_x18 * supply, target_market_cap_usd_x18 * pair_unit)
    )
    tick = get_tick_at_sqrt_ratio(isqrt((numerator << 192) // denominator))
    tick = (-(-tick // tick_spacing) if launched_token_is_quote else tick // tick_spacing) * tick_spacing
    lower, upper = _launch_band(tick, tick_spacing, launched_token_is_quote)
    sqrt_lower, sqrt_upper = get_sqrt_ratio_at_tick(lower), get_sqrt_ratio_at_tick(upper)
    q96 = 1 << 96
    liquidity = (
        ((budget * sqrt_lower // q96) * sqrt_upper) // (sqrt_upper - sqrt_lower)
        if launched_token_is_quote else budget * q96 // (sqrt_upper - sqrt_lower)
    )
    _integer(liquidity, 1, (1 << 128) - 1, "derived liquidity")
    return LaunchPoolRecipe(tick, sqrt_lower if launched_token_is_quote else sqrt_upper,
                            liquidity, budget, 0, lower, upper)


def _apportion(total: int, scores: list[int]) -> list[int]:
    # JavaScript Math.round on positive rationals, not Python's ties-to-even.
    denominator, cumulative, allocated = sum(scores), 0, 0
    result = []
    for score in scores:
        cumulative += score
        next_share = (2 * total * cumulative + denominator) // (2 * denominator)
        result.append(next_share - allocated)
        allocated = next_share
    return result


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def derive_launch_position_ranges(
    *, launch_sqrt_price_x96: int, launched_token_is_quote: bool, token_budget: int,
    tick_spacing: int, venue: LaunchVenue, distribution_preset: LaunchDistributionPreset,
    position_count: int = 5, staircase_boundaries: LaunchStaircaseBoundaries | None = None,
    extend_final_range_to_boundary: bool = False,
) -> LaunchPositionRanges:
    """Source-weighted Early Scarcity, Staircase or Smooth Ramp one-sided bands.

    Only an explicit final tail reaches the finite usable boundary. Allocation
    caps sum exactly to this pool's budget; actual rounded-up mint debits may be
    lower and all unspent inventory is burned by lifecycle activation.
    """
    tick = get_tick_at_sqrt_ratio(launch_sqrt_price_x96)
    _integer(token_budget, 1, _MAX_UINT256, "token_budget")
    _orientation(launched_token_is_quote)
    _integer(tick_spacing, 1, 32767, "tick_spacing")
    if venue not in {"abyss", "uniswap-v4"}:
        raise ValueError("venue must be abyss or uniswap-v4")
    if distribution_preset not in {"early-scarcity", "staircase", "smooth-ramp"}:
        raise ValueError("distribution_preset must be early-scarcity, staircase or smooth-ramp")
    _integer(position_count, 2, 16, "position_count")
    if not isinstance(extend_final_range_to_boundary, bool):
        raise ValueError("extend_final_range_to_boundary must be a boolean")
    if staircase_boundaries is not None and distribution_preset != "staircase":
        raise ValueError("staircase_boundaries is only valid for the staircase preset")
    if staircase_boundaries not in {None, "source-gaps", "adjacent"}:
        raise ValueError("staircase_boundaries must be source-gaps or adjacent")
    if tick % tick_spacing or get_sqrt_ratio_at_tick(tick) != launch_sqrt_price_x96:
        raise ValueError("Opening sqrt price must be an exact spacing-aligned tick")
    lower, upper = _launch_band(tick, tick_spacing, launched_token_is_quote)
    boundary = upper if launched_token_is_quote else lower
    n = position_count
    weights = _apportion(10_000, [
        (n - 1)**2 + 15 * i**2 if distribution_preset == "early-scarcity"
        else (i * 4 // n + 1)**2 if distribution_preset == "staircase"
        else n + 2 * i for i in range(n)
    ])
    widths = _apportion(64, [
        n - 1 + 3 * i if distribution_preset == "early-scarcity"
        else i * 4 // n + 1 if distribution_preset == "staircase"
        else 2 * n - i for i in range(n)
    ])
    minimum_index = _MIN_TICK // tick_spacing if venue == "uniswap-v4" else -(-_MIN_TICK // tick_spacing)
    capacity = ((1 << 128) - 1) // (_MAX_TICK // tick_spacing - minimum_index + 1)
    gross: dict[int, int] = {}
    distance = allocated = total_liquidity = maximum = active = 0
    positions = []
    direction = 1 if launched_token_is_quote else -1
    q96 = 1 << 96
    for i, weight in enumerate(weights):
        cap = token_budget - allocated if i == n - 1 else token_budget * weight // 10_000
        if cap <= 0:
            raise ValueError("Every position must have a positive allocated token cap")
        allocated += cap
        if i:
            if distribution_preset == "early-scarcity":
                distance += 2 + 4 * i // (n - 1)
            elif distribution_preset == "staircase" and staircase_boundaries != "adjacent" and i * 4 // n != (i - 1) * 4 // n:
                distance += 3
        start = tick + direction * distance * tick_spacing
        distance += widths[i]
        end = tick + direction * distance * tick_spacing
        if min(start, end) < lower or max(start, end) > upper:
            raise ValueError("The launch tick leaves too little room for this distribution preset")
        if extend_final_range_to_boundary and i == n - 1:
            end = boundary
        lo, hi = sorted((start, end))
        a, b = get_sqrt_ratio_at_tick(lo), get_sqrt_ratio_at_tick(hi)
        liquidity = cap * a * b // (q96 * (b - a)) if launched_token_is_quote else cap * q96 // (b - a)
        _integer(liquidity, 1, (1 << 127) - 1, "derived position liquidity")
        debit = _ceil_div(_ceil_div((liquidity << 96) * (b - a), b), a) if launched_token_is_quote else _ceil_div(liquidity * (b - a), q96)
        if debit <= 0 or debit > cap or (venue == "uniswap-v4" and debit > (1 << 127) - 1):
            raise ValueError("Derived position mint debit exceeds its budget or settlement range")
        for endpoint in (lo, hi):
            gross[endpoint] = gross.get(endpoint, 0) + liquidity
            if gross[endpoint] > capacity:
                raise ValueError(f"Gross position liquidity exceeds the tick capacity at {endpoint}")
        total_liquidity += liquidity
        maximum += debit
        if lo <= tick < hi:
            active += liquidity
        positions.append(LaunchPositionRange(lo, hi, a, b, weight, cap, liquidity, cap))
    return LaunchPositionRanges(tick, launch_sqrt_price_x96, boundary, get_sqrt_ratio_at_tick(boundary),
                                tuple(positions), token_budget, token_budget - maximum, total_liquidity,
                                active, positions[0].liquidity, capacity)


def estimate_launch_initial_buy(
    *, launch_sqrt_price_x96: int, liquidity: int, paired_token_amount_in: int,
    launched_token_is_quote: bool, fee: int, sqrt_price_limit_x96: int | None = None,
) -> LaunchInitialBuyEstimate:
    """Pure first-position estimate; actual stateful execution is final authority."""
    get_tick_at_sqrt_ratio(launch_sqrt_price_x96)
    _integer(liquidity, 1, (1 << 128) - 1, "liquidity")
    _integer(paired_token_amount_in, 0, _MAX_UINT256, "paired_token_amount_in")
    _integer(fee, 0, 999_999, "fee")
    amount = paired_token_amount_in * (1_000_000 - fee) // 1_000_000
    if amount == 0:
        return LaunchInitialBuyEstimate(0, paired_token_amount_in, launch_sqrt_price_x96)
    q96 = 1 << 96
    if launched_token_is_quote:
        after = launch_sqrt_price_x96 + amount * q96 // liquidity
        if sqrt_price_limit_x96 is not None:
            after = min(after, sqrt_price_limit_x96)
        output = liquidity * (after - launch_sqrt_price_x96) * q96 // (after * launch_sqrt_price_x96)
    else:
        after = liquidity * q96 * launch_sqrt_price_x96 // (liquidity * q96 + amount * launch_sqrt_price_x96)
        if sqrt_price_limit_x96 is not None:
            after = max(after, sqrt_price_limit_x96)
        output = liquidity * (launch_sqrt_price_x96 - after) // q96
    return LaunchInitialBuyEstimate(output, paired_token_amount_in, after)


def derive_launch_buy_sqrt_price_limit_x96(
    *, launch_sqrt_price_x96: int, launched_token_is_quote: bool,
    slippage_bps: int = LAUNCH_BUY_SLIPPAGE_BPS,
) -> int:
    """Exclusive router price guard; distinct from receipt-derived buy minima."""
    get_tick_at_sqrt_ratio(launch_sqrt_price_x96)
    _orientation(launched_token_is_quote)
    _integer(slippage_bps, 1, 9999, "slippage_bps")
    numerator = launch_sqrt_price_x96**2 * (10_000 + slippage_bps if launched_token_is_quote else 10_000 - slippage_bps)
    root = isqrt(numerator // 10_000)
    if launched_token_is_quote:
        return min(root, _MAX_SQRT_RATIO - 1)
    if root * root * 10_000 != numerator:
        root += 1
    return max(root, _MIN_SQRT_RATIO + 1)
