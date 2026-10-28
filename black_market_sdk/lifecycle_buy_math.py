"""Exact unopened release-template curves, not live quotes or execution proof.

Independent integer equations mirror Uniswap v4 SqrtPriceMath/SwapMath (MIT)
and the corresponding Abyss libraries. Input and fees round up, output rounds
down, including at bitmap-word boundaries. Values are raw token units and
canonical token1/token0 Q64.96 prices; token decimals do not enter these equations.
"""

from bisect import bisect_left
from dataclasses import dataclass

from .abyss import ABYSS_FEE_TIERS
from .launch import (
    _MAX_TICK, _MIN_TICK, _MAX_UINT256, _MIN_SQRT_RATIO, _MAX_SQRT_RATIO,
    get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio,
)
from .lifecycle import (
    LaunchPlanV1, LifecycleOpeningBuyQuote, LifecyclePlanningError,
    decode_lifecycle_abyss_market_config, decode_lifecycle_pool_bound_v4_market_config,
)
from .lifecycle_presets import KnownLifecycleProfile, get_known_lifecycle_profile
from .lifecycle_rpc import hex_bytes

_Q96 = 1 << 96
_PIPS = 1_000_000


def _ceil(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


@dataclass
class _OpeningState:
    price: int
    tick: int
    liquidity: int
    spacing: int
    lp_fee: int
    hook_fee: int
    liquidity_net: dict[int, int]
    ticks: list[int]


def has_profiled_opening_math(plan: LaunchPlanV1) -> bool:
    for market in plan.markets:
        preset = get_known_lifecycle_profile(chain_id=plan.chain_id,
            orchestrator=plan.orchestrator, profile_id=market.profile_id)
        if (preset is None or not preset.supported
                or preset.profile.topology.config_version != market.config_version
                or hex_bytes(preset.profile.registration.adapter_id).lower() != hex_bytes(market.adapter_id).lower()):
            return False
    return True


def _opening_state(plan: LaunchPlanV1, market_index: int) -> _OpeningState:
    if market_index >= len(plan.markets):
        raise LifecyclePlanningError("INVALID_BUY", "Opening buy market index is outside the plan")
    market = plan.markets[market_index]
    preset = get_known_lifecycle_profile(chain_id=plan.chain_id,
        orchestrator=plan.orchestrator, profile_id=market.profile_id)
    if (preset is None or not preset.supported
            or preset.profile.topology.config_version != market.config_version
            or hex_bytes(preset.profile.registration.adapter_id).lower() != hex_bytes(market.adapter_id).lower()):
        raise LifecyclePlanningError("OPENING_BUY_NOT_PROFILED", "Opening buy math requires the exact release-owned profile and schema")
    hook_fee = 0
    if market.config_version == 1:
        config = decode_lifecycle_abyss_market_config(market.config)
        tier = next((tier for tier in ABYSS_FEE_TIERS if tier.fee_pips == config.fee), None)
        if tier is None or preset.key != f"abyss-{config.profile}":
            raise LifecyclePlanningError("INVALID_MARKET", "Abyss opening must use its committed profile and canonical fee tier")
        price, spacing, lp_fee = config.opening_sqrt_price_x96, tier.tick_spacing, config.fee
    else:
        # Dynamic opening buys share the block's minimum fee. HMM work starts
        # onOpened, after all ordered buys, not during this unopened curve.
        config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
        if hex_bytes(config.profile_id).lower() != hex_bytes(market.profile_id).lower():
            raise LifecyclePlanningError("INVALID_MARKET", "V4 opening config differs from the selected profile")
        price, spacing, lp_fee = config.sqrt_price_x96, config.tick_spacing, config.lp_fee_pips
        dynamic = preset.key in {KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL,
            KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL, KnownLifecycleProfile.V4_UFG_DYNAMIC_POOL}
        hook_fee = config.minimum_hook_fee_pips if dynamic else config.hook_fee_pips
    tick = get_tick_at_sqrt_ratio(price)
    liquidity_net: dict[int, int] = {}
    liquidity = 0
    for position in config.positions:
        liquidity_net[position.tick_lower] = liquidity_net.get(position.tick_lower, 0) + position.liquidity
        liquidity_net[position.tick_upper] = liquidity_net.get(position.tick_upper, 0) - position.liquidity
        if position.tick_lower <= tick < position.tick_upper:
            liquidity += position.liquidity
    return _OpeningState(price, tick, liquidity, spacing, lp_fee, hook_fee,
        liquidity_net, sorted(liquidity_net))


def _next_tick(state: _OpeningState, zero_for_one: bool) -> int:
    compressed = state.tick // state.spacing + (0 if zero_for_one else 1)
    first = compressed // 256 * 256
    last = first + 255
    index = bisect_left(state.ticks, (compressed + (1 if zero_for_one else 0)) * state.spacing) - (1 if zero_for_one else 0)
    initialized = state.ticks[index] if 0 <= index < len(state.ticks) else None
    selected = initialized if initialized is not None and first * state.spacing <= initialized <= last * state.spacing else (first if zero_for_one else last) * state.spacing
    return max(_MIN_TICK, min(_MAX_TICK, selected))


def _amount0(a: int, b: int, liquidity: int, round_up: bool) -> int:
    if a > b:
        a, b = b, a
    numerator, denominator = liquidity * _Q96 * (b - a), a * b
    return _ceil(numerator, denominator) if round_up else numerator // denominator


def _amount1(a: int, b: int, liquidity: int, round_up: bool) -> int:
    numerator = liquidity * abs(b - a)
    return _ceil(numerator, _Q96) if round_up else numerator // _Q96


def _input_price(price: int, liquidity: int, amount: int, zero_for_one: bool) -> int:
    if amount == 0:
        return price
    if not zero_for_one:
        return price + amount * _Q96 // liquidity
    numerator, product = liquidity * _Q96, amount * price
    denominator = numerator + product
    if product <= _MAX_UINT256 and denominator <= _MAX_UINT256:
        return _ceil(numerator * price, denominator)
    return _ceil(numerator, numerator // price + amount)


def _swap(state: _OpeningState, amount: int, limit: int, zero_for_one: bool) -> tuple[int, int]:
    remaining, token_out = amount, 0
    while remaining > 0 and state.price != limit:
        tick = _next_tick(state, zero_for_one)
        boundary, before = get_sqrt_ratio_at_tick(tick), state.price
        target = max(boundary, limit) if zero_for_one else min(boundary, limit)
        available = remaining * (_PIPS - state.lp_fee) // _PIPS
        required = _amount0(target, before, state.liquidity, True) if zero_for_one else _amount1(before, target, state.liquidity, True)
        if available >= required:
            next_price = target
            spent = required + (0 if state.lp_fee == _PIPS else _ceil(required * state.lp_fee, _PIPS - state.lp_fee))
        else:
            next_price = _input_price(before, state.liquidity, available, zero_for_one)
            spent = remaining
        token_out += _amount1(next_price, before, state.liquidity, False) if zero_for_one else _amount0(before, next_price, state.liquidity, False)
        remaining -= spent
        state.price = next_price
        if next_price == boundary:
            net = state.liquidity_net.get(tick)
            if net is not None:
                state.liquidity += -net if zero_for_one else net
            state.tick = tick - 1 if zero_for_one else tick
        elif next_price != before:
            state.tick = get_tick_at_sqrt_ratio(next_price)
    return token_out, amount - remaining


def compute_opening_buys(plan: LaunchPlanV1, predicted_token: str) -> tuple[LifecycleOpeningBuyQuote, ...]:
    states: dict[int, _OpeningState] = {}
    quotes = []
    for buy_index, buy in enumerate(plan.buys):
        if (isinstance(buy.market_index, bool) or not isinstance(buy.market_index, int) or buy.market_index < 0
                or isinstance(buy.quote_amount_in, bool) or not isinstance(buy.quote_amount_in, int)
                or not 0 < buy.quote_amount_in <= _MAX_UINT256 >> 1):
            raise LifecyclePlanningError("INVALID_BUY", "Opening buy requires a valid market and positive int256 input")
        state = states.get(buy.market_index)
        if state is None:
            state = _opening_state(plan, buy.market_index)
            states[buy.market_index] = state
        market = plan.markets[buy.market_index]
        zero_for_one = int(market.quote_asset, 16) < int(predicted_token, 16)
        limit = buy.sqrt_price_limit_x96
        if isinstance(limit, int) and not isinstance(limit, bool) and limit == 0 and market.config_version != 1:
            limit = _MIN_SQRT_RATIO + 1 if zero_for_one else _MAX_SQRT_RATIO - 1
        if (market.quote_asset.lower() == predicted_token.lower() or isinstance(limit, bool) or not isinstance(limit, int)
                or not _MIN_SQRT_RATIO < limit < _MAX_SQRT_RATIO
                or (limit >= state.price if zero_for_one else limit <= state.price)):
            raise LifecyclePlanningError("INVALID_BUY_PRICE_LIMIT", "Opening buy price limit must be strictly beyond the current curve price in the input direction")
        hook_charge = buy.quote_amount_in * state.hook_fee // _PIPS
        adjusted = buy.quote_amount_in - hook_charge
        token_out, quote_spent = _swap(state, adjusted, limit, zero_for_one)
        if hook_charge > 0 and quote_spent != adjusted:
            raise LifecyclePlanningError("INCOMPLETE_BUY_FILL", "A specified-input hook fee requires the complete adjusted input to fill")
        quotes.append(LifecycleOpeningBuyQuote(buy_index, buy.market_index, buy.quote_amount_in,
            quote_spent + hook_charge, token_out, state.price))
    return tuple(quotes)
