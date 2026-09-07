"""Atomic launch templates, pool recipe derivation, and calldata building.

Ported from the canonical TypeScript SDK (@black-market/sdk, src/launch.ts).
All integer math mirrors the Solidity contracts bit-for-bit, including
signed-remainder tick alignment semantics from AtomicLaunchFactory.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Optional

from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak

from .abyss import (
    ABYSS_FEE_TIERS,
    ATOMIC_LAUNCH_FACTORY_ABI,
    AbyssFeeTier,
    AbyssPoolProfile,
    TokenKind,
)
from .auction import AUCTION_SUPPLY, ZERO_ADDRESS

INITIAL_TEMPLATE_VERSION = 1


def _template_id(name: str) -> bytes:
    return keccak(text=name)


STANDARD_TEMPLATE_ID = _template_id("black-market.standard")
QUOTE_STAKING_TEMPLATE_ID = _template_id("black-market.quote-staking")
QUOTE_DIVIDENDS_TEMPLATE_ID = _template_id("black-market.quote-dividends")
DUAL_STAKING_TEMPLATE_ID = _template_id("black-market.dual-staking")
DUAL_DIVIDENDS_TEMPLATE_ID = _template_id("black-market.dual-dividends")
FEE_BURN_TEMPLATE_ID = _template_id("black-market.fee-burn")
STANDARD_TEMPLATE_VERSION = INITIAL_TEMPLATE_VERSION
LAUNCH_REWARD_DURATION = 7 * 24 * 60 * 60

#: Canonical registered volatile/P3 oracle configuration for Atomic launches.
#: Deployment evidence records maxAbsTickMove=17 and cardinality=4096 for this ID.
ATOMIC_LAUNCH_ORACLE_CONFIG_ID = bytes.fromhex(
    "c0e9bed88d70a13fd3ab31451fefdd073b7266e838aee0ad1c236c8c9eff855d"
)

#: Existing Abyss execution policy: Atomic launch requests expire after 20 minutes.
ATOMIC_LAUNCH_DEADLINE_SECONDS = 20 * 60

#: Existing Abyss execution policy: initial Atomic buys tolerate 50 basis points of price movement.
ATOMIC_LAUNCH_BUY_SLIPPAGE_BPS = 50

#: Atomic lifecycle recipes and tests use a $5,000 initial fully diluted market cap.
DEFAULT_ATOMIC_LAUNCH_TARGET_MARKET_CAP_USD = 5_000


class LaunchRewardMode(IntEnum):
    NONE = 0
    STAKING = 1
    DIVIDENDS = 2


class LaunchFeeAssetMode(IntEnum):
    PROFILE = 0
    PAIRED_ONLY = 1
    BOTH = 2
    LAUNCHED_ONLY = 3


class LaunchFeeDestination(IntFlag):
    OWNER = 1 << 0
    REWARDS = 1 << 1
    BURN = 1 << 2


OWNER = LaunchFeeDestination.OWNER
REWARDS = LaunchFeeDestination.REWARDS
BURN = LaunchFeeDestination.BURN

LaunchTemplateId = str  # "standard" | "quote-staking" | ... | "fee-burn"


@dataclass(frozen=True)
class FeeDisposition:
    owner_bps: int
    rewards_bps: int
    burn_bps: int


@dataclass(frozen=True)
class LaunchTemplate:
    id: LaunchTemplateId
    template_id: bytes
    version: int
    label: str
    token_kinds: tuple[TokenKind, ...]
    pool_profiles: tuple[AbyssPoolProfile, ...]
    launched_token_is_quote: bool
    reward_mode: LaunchRewardMode
    reward_duration: int
    fee_asset_mode: LaunchFeeAssetMode
    launched_token_destinations: int
    paired_token_destinations: int


_BURNABLE_ONLY = (TokenKind.BURNABLE,)
_DIVIDEND_ONLY = (TokenKind.HOLDER_DIVIDEND,)

LAUNCH_TEMPLATES: tuple[LaunchTemplate, ...] = (
    LaunchTemplate(
        id="standard",
        template_id=STANDARD_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Standard",
        token_kinds=_BURNABLE_ONLY,
        pool_profiles=(AbyssPoolProfile.STANDARD_ORACLE, AbyssPoolProfile.QUOTE_ORACLE),
        launched_token_is_quote=False,
        reward_mode=LaunchRewardMode.NONE,
        reward_duration=0,
        fee_asset_mode=LaunchFeeAssetMode.PROFILE,
        launched_token_destinations=OWNER,
        paired_token_destinations=OWNER,
    ),
    LaunchTemplate(
        id="quote-staking",
        template_id=QUOTE_STAKING_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Quote Staking",
        token_kinds=_BURNABLE_ONLY,
        pool_profiles=(AbyssPoolProfile.QUOTE_ORACLE,),
        launched_token_is_quote=False,
        reward_mode=LaunchRewardMode.STAKING,
        reward_duration=LAUNCH_REWARD_DURATION,
        fee_asset_mode=LaunchFeeAssetMode.PAIRED_ONLY,
        launched_token_destinations=0,
        paired_token_destinations=OWNER | REWARDS,
    ),
    LaunchTemplate(
        id="quote-dividends",
        template_id=QUOTE_DIVIDENDS_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Quote Dividends",
        token_kinds=_DIVIDEND_ONLY,
        pool_profiles=(AbyssPoolProfile.QUOTE_ORACLE,),
        launched_token_is_quote=False,
        reward_mode=LaunchRewardMode.DIVIDENDS,
        reward_duration=LAUNCH_REWARD_DURATION,
        fee_asset_mode=LaunchFeeAssetMode.PAIRED_ONLY,
        launched_token_destinations=0,
        paired_token_destinations=OWNER | REWARDS,
    ),
    LaunchTemplate(
        id="dual-staking",
        template_id=DUAL_STAKING_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Dual Staking",
        token_kinds=_BURNABLE_ONLY,
        pool_profiles=(AbyssPoolProfile.STANDARD_ORACLE,),
        launched_token_is_quote=False,
        reward_mode=LaunchRewardMode.STAKING,
        reward_duration=LAUNCH_REWARD_DURATION,
        fee_asset_mode=LaunchFeeAssetMode.BOTH,
        launched_token_destinations=OWNER | REWARDS | BURN,
        paired_token_destinations=OWNER | REWARDS,
    ),
    LaunchTemplate(
        id="dual-dividends",
        template_id=DUAL_DIVIDENDS_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Dual Dividends",
        token_kinds=_DIVIDEND_ONLY,
        pool_profiles=(AbyssPoolProfile.STANDARD_ORACLE,),
        launched_token_is_quote=False,
        reward_mode=LaunchRewardMode.DIVIDENDS,
        reward_duration=LAUNCH_REWARD_DURATION,
        fee_asset_mode=LaunchFeeAssetMode.BOTH,
        launched_token_destinations=OWNER | REWARDS | BURN,
        paired_token_destinations=OWNER | REWARDS,
    ),
    LaunchTemplate(
        id="fee-burn",
        template_id=FEE_BURN_TEMPLATE_ID,
        version=INITIAL_TEMPLATE_VERSION,
        label="Fee Burn",
        token_kinds=_BURNABLE_ONLY,
        pool_profiles=(AbyssPoolProfile.QUOTE_ORACLE,),
        launched_token_is_quote=True,
        reward_mode=LaunchRewardMode.NONE,
        reward_duration=0,
        fee_asset_mode=LaunchFeeAssetMode.LAUNCHED_ONLY,
        launched_token_destinations=BURN,
        paired_token_destinations=0,
    ),
)


@dataclass(frozen=True)
class AtomicTokenConfig:
    kind: TokenKind
    name: str
    symbol: str
    decimals: int
    supply: int


@dataclass(frozen=True)
class AtomicPoolConfig:
    paired_token: str
    launched_token_is_quote: bool
    profile: AbyssPoolProfile
    fee: int
    oracle_config_id: bytes
    launch_tick: int
    liquidity: int
    launched_token_amount_maximum: int
    paired_token_amount_maximum: int


@dataclass(frozen=True)
class AtomicInitialBuy:
    paired_token_amount_in: int
    launched_token_amount_out_minimum: int
    sqrt_price_limit_x96: int


@dataclass(frozen=True)
class AtomicLaunchRequest:
    creator: str
    template_id: bytes
    template_version: int
    token: AtomicTokenConfig
    pool: AtomicPoolConfig
    initial_buy: AtomicInitialBuy
    launched_token_fees: FeeDisposition
    paired_token_fees: FeeDisposition
    deadline: int


@dataclass(frozen=True)
class AtomicLaunchPoolRecipeInput:
    paired_token_decimals: int
    paired_token_usd_price_x18: int
    target_market_cap_usd_x18: int
    launched_token_is_quote: bool
    fee: int


@dataclass(frozen=True)
class AtomicLaunchPoolRecipe:
    launch_tick: int
    launch_sqrt_price_x96: int
    liquidity: int
    launched_token_amount_maximum: int
    paired_token_amount_maximum: int


@dataclass(frozen=True)
class AtomicLaunchBuySqrtPriceLimitInput:
    launch_sqrt_price_x96: int
    launched_token_is_quote: bool
    slippage_bps: Optional[int] = None


@dataclass(frozen=True)
class AtomicLaunchInitialBuyEstimateInput:
    launch_sqrt_price_x96: int
    liquidity: int
    paired_token_amount_in: int
    launched_token_is_quote: bool
    fee: int
    sqrt_price_limit_x96: Optional[int] = None


@dataclass(frozen=True)
class AtomicLaunchInitialBuyEstimate:
    launched_token_amount_out: int
    paired_token_amount_consumed: int
    sqrt_price_after_x96: int


_Q32 = 1 << 32
_Q96 = 1 << 96
_MAX_UINT128 = (1 << 128) - 1
_MAX_UINT160 = (1 << 160) - 1
_MAX_UINT256 = (1 << 256) - 1
_MIN_INT24 = -(1 << 23)
_MAX_INT24 = (1 << 23) - 1

# These are the deployed TickMathLib bounds. Swap limits are exclusive at both ends.
_MIN_TICK = -887_272
_MAX_TICK = 887_272
_MIN_SQRT_RATIO = 4_295_128_739
_MAX_SQRT_RATIO = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342
_ZERO_BYTES32 = b"\x00" * 32
_BYTES32_HEX = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Canonical Uniswap v3 TickMath multipliers, matching contracts/src/oracles/TickMathLib.sol.
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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _assert_uint(value: object, maximum: int, name: str) -> None:
    if not _is_int(value) or value < 0 or value > maximum:
        raise ValueError(f"{name} must be a nonnegative integer within its Solidity type")


def _assert_positive_uint(value: object, maximum: int, name: str) -> None:
    _assert_uint(value, maximum, name)
    if value == 0:
        raise ValueError(f"{name} must be positive")


def _assert_int24(value: object, name: str) -> None:
    if not _is_int(value) or value < _MIN_INT24 or value > _MAX_INT24:
        raise ValueError(f"{name} must be an int24 integer")


def _assert_token_decimals(value: object, name: str) -> None:
    if not _is_int(value) or value < 0 or value > 255:
        raise ValueError(f"{name} must be a uint8 integer")


def _normalize_bytes32(value: object) -> bytes:
    if isinstance(value, (bytes, bytearray)) and len(value) == 32:
        return bytes(value)
    if isinstance(value, str) and _BYTES32_HEX.match(value):
        return bytes.fromhex(value[2:])
    raise ValueError("pool.oracle_config_id must be a nonzero bytes32")


def _assert_oracle_config_id(value: object) -> bytes:
    config_id = _normalize_bytes32(value)
    if config_id == _ZERO_BYTES32:
        raise ValueError("pool.oracle_config_id must be a nonzero bytes32")
    return config_id


def _assert_canonical_sqrt_price(value: object, name: str) -> None:
    _assert_uint(value, _MAX_UINT160, name)
    if value < _MIN_SQRT_RATIO or value >= _MAX_SQRT_RATIO:
        raise ValueError(f"{name} is outside the canonical swap price bounds")


def _get_fee_tier(fee: object) -> AbyssFeeTier:
    for tier in ABYSS_FEE_TIERS:
        if tier.fee_pips == fee:
            return tier
    raise ValueError("pool.fee is not an Abyss fee tier")


def _integer_sqrt(value: int) -> int:
    if value < 0:
        raise ValueError("Cannot calculate a negative square root")
    return math.isqrt(value)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    if not _is_int(tick) or tick < _MIN_TICK or tick > _MAX_TICK:
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
    _assert_canonical_sqrt_price(sqrt_price_x96, "sqrt_price_x96")

    low = _MIN_TICK
    high = _MAX_TICK
    while low < high:
        middle = math.ceil((low + high) / 2)
        if get_sqrt_ratio_at_tick(middle) <= sqrt_price_x96:
            low = middle
        else:
            high = middle - 1
    return low


def _signed_remainder(tick: int, tick_spacing: int) -> int:
    """Solidity/JS truncated signed remainder (Python's % floors instead)."""
    return tick % tick_spacing if tick >= 0 else -((-tick) % tick_spacing)


# Deliberately retain Solidity's signed-remainder alignment semantics from AtomicLaunchFactory.
def _align_down(tick: int, tick_spacing: int) -> int:
    remainder = _signed_remainder(tick, tick_spacing)
    return tick if remainder == 0 else tick - remainder


def _align_up(tick: int, tick_spacing: int) -> int:
    remainder = _signed_remainder(tick, tick_spacing)
    return tick if remainder == 0 else tick + (tick_spacing - remainder)


def _launch_band(
    launch_tick: int,
    tick_spacing: int,
    launched_token_is_quote: bool,
) -> tuple[int, int]:
    tick_lower = (
        _align_up(launch_tick, tick_spacing)
        if launched_token_is_quote
        else _align_up(_MIN_TICK, tick_spacing)
    )
    tick_upper = (
        _align_down(_MAX_TICK, tick_spacing)
        if launched_token_is_quote
        else _align_down(launch_tick, tick_spacing)
    )
    if tick_lower < _MIN_TICK or tick_upper > _MAX_TICK or tick_lower >= tick_upper:
        raise ValueError("launch_tick cannot form a valid Atomic launch band")
    return tick_lower, tick_upper


def _liquidity_for_amount0(amount0: int, sqrt_lower_x96: int, sqrt_upper_x96: int) -> int:
    return (((amount0 * sqrt_lower_x96) // _Q96) * sqrt_upper_x96) // (sqrt_upper_x96 - sqrt_lower_x96)


def _liquidity_for_amount1(amount1: int, sqrt_lower_x96: int, sqrt_upper_x96: int) -> int:
    return (amount1 * _Q96) // (sqrt_upper_x96 - sqrt_lower_x96)


def derive_atomic_launch_pool_recipe(
    input: AtomicLaunchPoolRecipeInput,
) -> AtomicLaunchPoolRecipe:
    """Derive the one-sided Atomic launch position from a target fully diluted market cap.

    The raw price remains a rational integer until the canonical Q64.96 square root conversion.
    """
    _assert_token_decimals(input.paired_token_decimals, "paired_token_decimals")
    _assert_positive_uint(input.paired_token_usd_price_x18, _MAX_UINT256, "paired_token_usd_price_x18")
    _assert_positive_uint(input.target_market_cap_usd_x18, _MAX_UINT256, "target_market_cap_usd_x18")
    if not isinstance(input.launched_token_is_quote, bool):
        raise ValueError("launched_token_is_quote must be a boolean")

    fee_tier = _get_fee_tier(input.fee)
    paired_token_unit = 10 ** input.paired_token_decimals
    if input.launched_token_is_quote:
        price_numerator = input.target_market_cap_usd_x18 * paired_token_unit
        price_denominator = input.paired_token_usd_price_x18 * AUCTION_SUPPLY
    else:
        price_numerator = input.paired_token_usd_price_x18 * AUCTION_SUPPLY
        price_denominator = input.target_market_cap_usd_x18 * paired_token_unit
    target_sqrt_price_x96 = _integer_sqrt((price_numerator << 192) // price_denominator)
    raw_tick = get_tick_at_sqrt_ratio(target_sqrt_price_x96)
    if input.launched_token_is_quote:
        launch_tick = math.ceil(raw_tick / fee_tier.tick_spacing) * fee_tier.tick_spacing
    else:
        launch_tick = math.floor(raw_tick / fee_tier.tick_spacing) * fee_tier.tick_spacing
    _assert_int24(launch_tick, "launch_tick")

    tick_lower, tick_upper = _launch_band(
        launch_tick, fee_tier.tick_spacing, input.launched_token_is_quote
    )
    sqrt_lower_x96 = get_sqrt_ratio_at_tick(tick_lower)
    sqrt_upper_x96 = get_sqrt_ratio_at_tick(tick_upper)
    launch_sqrt_price_x96 = sqrt_lower_x96 if input.launched_token_is_quote else sqrt_upper_x96
    liquidity = (
        _liquidity_for_amount0(AUCTION_SUPPLY, sqrt_lower_x96, sqrt_upper_x96)
        if input.launched_token_is_quote
        else _liquidity_for_amount1(AUCTION_SUPPLY, sqrt_lower_x96, sqrt_upper_x96)
    )
    if liquidity <= 0 or liquidity > _MAX_UINT128:
        raise ValueError("Derived liquidity is outside the uint128 range")

    return AtomicLaunchPoolRecipe(
        launch_tick=launch_tick,
        launch_sqrt_price_x96=launch_sqrt_price_x96,
        liquidity=liquidity,
        launched_token_amount_maximum=AUCTION_SUPPLY,
        paired_token_amount_maximum=0,
    )


def estimate_atomic_launch_initial_buy(
    input: AtomicLaunchInitialBuyEstimateInput,
) -> AtomicLaunchInitialBuyEstimate:
    """Pure estimate for the first exact-input buy against a fresh one-sided Atomic
    launch position. No wallet balance, allowance, pool deployment, or RPC state.
    Uses conservative input-fee rounding; execution simulation remains the final
    authority before submission.
    """
    _assert_canonical_sqrt_price(input.launch_sqrt_price_x96, "launch_sqrt_price_x96")
    _assert_positive_uint(input.liquidity, _MAX_UINT128, "liquidity")
    _assert_uint(input.paired_token_amount_in, _MAX_UINT256, "paired_token_amount_in")
    fee = _get_fee_tier(input.fee).fee_pips
    amount_after_fee = (input.paired_token_amount_in * (1_000_000 - fee)) // 1_000_000
    if amount_after_fee == 0:
        return AtomicLaunchInitialBuyEstimate(
            launched_token_amount_out=0,
            paired_token_amount_consumed=input.paired_token_amount_in,
            sqrt_price_after_x96=input.launch_sqrt_price_x96,
        )

    limit = input.sqrt_price_limit_x96
    if input.launched_token_is_quote:
        # Paired token is token1; exact token1 input moves price upward and buys token0.
        delta = (amount_after_fee * _Q96) // input.liquidity
        sqrt_price_after_x96 = input.launch_sqrt_price_x96 + delta
        if limit is not None and sqrt_price_after_x96 > limit:
            sqrt_price_after_x96 = limit
        launched_token_amount_out = (
            input.liquidity * (sqrt_price_after_x96 - input.launch_sqrt_price_x96) * _Q96
        ) // (sqrt_price_after_x96 * input.launch_sqrt_price_x96)
    else:
        # Paired token is token0; exact token0 input moves price downward and buys token1.
        numerator = input.liquidity * _Q96 * input.launch_sqrt_price_x96
        denominator = input.liquidity * _Q96 + amount_after_fee * input.launch_sqrt_price_x96
        sqrt_price_after_x96 = numerator // denominator
        if limit is not None and sqrt_price_after_x96 < limit:
            sqrt_price_after_x96 = limit
        launched_token_amount_out = (
            input.liquidity * (input.launch_sqrt_price_x96 - sqrt_price_after_x96)
        ) // _Q96
    return AtomicLaunchInitialBuyEstimate(
        launched_token_amount_out=launched_token_amount_out,
        paired_token_amount_consumed=input.paired_token_amount_in,
        sqrt_price_after_x96=sqrt_price_after_x96,
    )


def _ceil_sqrt_ratio(numerator: int, denominator: int) -> int:
    root = _integer_sqrt(numerator // denominator)
    return root if root * root * denominator == numerator else root + 1


def derive_atomic_launch_buy_sqrt_price_limit_x96(
    input: AtomicLaunchBuySqrtPriceLimitInput,
) -> int:
    """Produce the router's exclusive sqrt-price guard for the paired-token initial buy.

    A paired input moves down for deployAbove and up for fee-burn/deployBelow.
    """
    _assert_canonical_sqrt_price(input.launch_sqrt_price_x96, "launch_sqrt_price_x96")
    if not isinstance(input.launched_token_is_quote, bool):
        raise ValueError("launched_token_is_quote must be a boolean")

    slippage_bps = (
        input.slippage_bps if input.slippage_bps is not None else ATOMIC_LAUNCH_BUY_SLIPPAGE_BPS
    )
    if not _is_int(slippage_bps) or slippage_bps < 1 or slippage_bps > 9_999:
        raise ValueError("slippage_bps must be an integer from 1 through 9,999")

    squared_launch_sqrt = input.launch_sqrt_price_x96 * input.launch_sqrt_price_x96
    if input.launched_token_is_quote:
        limit = _integer_sqrt((squared_launch_sqrt * (10_000 + slippage_bps)) // 10_000)
        return _MAX_SQRT_RATIO - 1 if limit >= _MAX_SQRT_RATIO else limit

    limit = _ceil_sqrt_ratio(squared_launch_sqrt * (10_000 - slippage_bps), 10_000)
    return _MIN_SQRT_RATIO + 1 if limit <= _MIN_SQRT_RATIO else limit


def get_launch_template(id: LaunchTemplateId) -> LaunchTemplate:
    for template in LAUNCH_TEMPLATES:
        if template.id == id:
            return template
    raise ValueError(f"Unknown launch template: {id}")


def get_launch_template_by_hash(template_id: bytes | str) -> LaunchTemplate:
    wanted = _normalize_bytes32(template_id)
    for template in LAUNCH_TEMPLATES:
        if template.template_id == wanted:
            return template
    raise ValueError(f"Unknown launch template id: {template_id!r}")


def _disposition_total(disposition: FeeDisposition) -> int:
    return disposition.owner_bps + disposition.rewards_bps + disposition.burn_bps


def _disposition_destinations(disposition: FeeDisposition) -> int:
    return (
        (0 if disposition.owner_bps == 0 else OWNER)
        | (0 if disposition.rewards_bps == 0 else REWARDS)
        | (0 if disposition.burn_bps == 0 else BURN)
    )


def _validate_disposition(
    name: str,
    disposition: FeeDisposition,
    active: bool,
    allowed_destinations: int,
) -> None:
    if (
        not _is_int(disposition.owner_bps)
        or not _is_int(disposition.rewards_bps)
        or not _is_int(disposition.burn_bps)
        or disposition.owner_bps < 0
        or disposition.rewards_bps < 0
        or disposition.burn_bps < 0
    ):
        raise ValueError(f"{name} must contain nonnegative integer basis points")

    total = _disposition_total(disposition)
    if active:
        if total != 10_000:
            raise ValueError(f"{name} must total 10,000 basis points")
    elif total != 0:
        raise ValueError(f"{name} must total zero basis points")
    if _disposition_destinations(disposition) & ~allowed_destinations:
        raise ValueError(f"{name} uses a destination not allowed by the template")


def _canonical_type(entry: dict) -> str:
    if entry["type"] == "tuple":
        return "(" + ",".join(_canonical_type(c) for c in entry["components"]) + ")"
    return entry["type"]


def _encode_function_data(abi: list[dict], function_name: str, args: list) -> bytes:
    fn = next(
        e for e in abi if e.get("type") == "function" and e.get("name") == function_name
    )
    types = [_canonical_type(i) for i in fn["inputs"]]
    signature = f"{function_name}(" + ",".join(types) + ")"
    selector = keccak(text=signature)[:4]
    return selector + abi_encode(types, args)


def build_atomic_launch_calldata(
    request: AtomicLaunchRequest,
    native_buy_amount: int = 0,
) -> bytes:
    """Validate an Atomic launch request and encode ``deployAndLaunch`` calldata."""
    if not is_address(request.creator) or request.creator.lower() == ZERO_ADDRESS:
        raise ValueError("creator must be a nonzero address")
    template = get_launch_template_by_hash(request.template_id)
    if request.template_version != template.version:
        raise ValueError("Unsupported template version")
    if request.token.kind not in template.token_kinds:
        raise ValueError("Token kind is not supported by the template")
    if request.pool.profile not in template.pool_profiles:
        raise ValueError("Pool profile is not supported by the template")
    if not is_address(request.pool.paired_token) or request.pool.paired_token.lower() == ZERO_ADDRESS:
        raise ValueError("paired_token must be a nonzero address")
    if request.pool.launched_token_is_quote != template.launched_token_is_quote:
        raise ValueError("Pool orientation does not match the template")

    fee_tier = _get_fee_tier(request.pool.fee)
    oracle_config_id = _assert_oracle_config_id(request.pool.oracle_config_id)
    _assert_int24(request.pool.launch_tick, "pool.launch_tick")
    _assert_token_decimals(request.token.decimals, "token.decimals")
    _assert_positive_uint(request.token.supply, _MAX_UINT256, "token.supply")
    _assert_positive_uint(request.pool.liquidity, _MAX_UINT128, "pool.liquidity")
    _assert_positive_uint(
        request.pool.launched_token_amount_maximum,
        _MAX_UINT256,
        "pool.launched_token_amount_maximum",
    )
    _assert_uint(
        request.pool.paired_token_amount_maximum, _MAX_UINT256, "pool.paired_token_amount_maximum"
    )
    if request.pool.paired_token_amount_maximum != 0:
        raise ValueError("pool.paired_token_amount_maximum must be zero for a single-sided Atomic launch")
    if request.pool.launched_token_amount_maximum > request.token.supply:
        raise ValueError("pool.launched_token_amount_maximum cannot exceed token.supply")
    _assert_positive_uint(request.deadline, _MAX_UINT256, "deadline")

    _assert_uint(
        request.initial_buy.paired_token_amount_in, _MAX_UINT256, "initial_buy.paired_token_amount_in"
    )
    _assert_uint(native_buy_amount, _MAX_UINT256, "native_buy_amount")
    _assert_uint(
        request.initial_buy.launched_token_amount_out_minimum,
        _MAX_UINT256,
        "initial_buy.launched_token_amount_out_minimum",
    )
    _assert_uint(
        request.initial_buy.sqrt_price_limit_x96, _MAX_UINT160, "initial_buy.sqrt_price_limit_x96"
    )
    effective_buy_amount = request.initial_buy.paired_token_amount_in + native_buy_amount
    if effective_buy_amount == 0:
        if (
            request.initial_buy.launched_token_amount_out_minimum != 0
            or request.initial_buy.sqrt_price_limit_x96 != 0
        ):
            raise ValueError("zero initial buy cannot set output or price limits")
    else:
        if request.initial_buy.sqrt_price_limit_x96 == 0:
            raise ValueError("nonzero initial buy requires a directional price limit")
        _assert_canonical_sqrt_price(
            request.initial_buy.sqrt_price_limit_x96, "initial_buy.sqrt_price_limit_x96"
        )
        tick_lower, tick_upper = _launch_band(
            request.pool.launch_tick, fee_tier.tick_spacing, request.pool.launched_token_is_quote
        )
        launch_sqrt_price_x96 = get_sqrt_ratio_at_tick(
            tick_lower if request.pool.launched_token_is_quote else tick_upper
        )
        if (
            not request.pool.launched_token_is_quote
            and request.initial_buy.sqrt_price_limit_x96 >= launch_sqrt_price_x96
        ) or (
            request.pool.launched_token_is_quote
            and request.initial_buy.sqrt_price_limit_x96 <= launch_sqrt_price_x96
        ):
            raise ValueError("initial buy price limit does not protect the paired-token swap direction")

    launched_fees_active = (
        template.fee_asset_mode == LaunchFeeAssetMode.BOTH
        or template.fee_asset_mode == LaunchFeeAssetMode.LAUNCHED_ONLY
        or (
            template.fee_asset_mode == LaunchFeeAssetMode.PROFILE
            and request.pool.profile == AbyssPoolProfile.STANDARD_ORACLE
        )
    )
    paired_fees_active = (
        template.fee_asset_mode == LaunchFeeAssetMode.PROFILE
        or template.fee_asset_mode == LaunchFeeAssetMode.PAIRED_ONLY
        or template.fee_asset_mode == LaunchFeeAssetMode.BOTH
    )
    _validate_disposition(
        "launched_token_fees",
        request.launched_token_fees,
        launched_fees_active,
        template.launched_token_destinations,
    )
    _validate_disposition(
        "paired_token_fees",
        request.paired_token_fees,
        paired_fees_active,
        template.paired_token_destinations,
    )

    encoded_request = (
        request.creator,
        template.template_id,
        request.template_version,
        (
            int(request.token.kind),
            request.token.name,
            request.token.symbol,
            request.token.decimals,
            request.token.supply,
        ),
        (
            request.pool.paired_token,
            request.pool.launched_token_is_quote,
            int(request.pool.profile),
            request.pool.fee,
            oracle_config_id,
            request.pool.launch_tick,
            request.pool.liquidity,
            request.pool.launched_token_amount_maximum,
            request.pool.paired_token_amount_maximum,
        ),
        (
            request.initial_buy.paired_token_amount_in,
            request.initial_buy.launched_token_amount_out_minimum,
            request.initial_buy.sqrt_price_limit_x96,
        ),
        (
            request.launched_token_fees.owner_bps,
            request.launched_token_fees.rewards_bps,
            request.launched_token_fees.burn_bps,
        ),
        (
            request.paired_token_fees.owner_bps,
            request.paired_token_fees.rewards_bps,
            request.paired_token_fees.burn_bps,
        ),
        request.deadline,
    )
    return _encode_function_data(ATOMIC_LAUNCH_FACTORY_ABI, "deployAndLaunch", [encoded_request])
