"""Offline calldata builders for the schema-/2 ``UnifiedLauncher``.

The launcher dispatches a typed V3 envelope through a registry.  This module
keeps that envelope, its two deployed pool-config payloads, and transaction
construction local and deterministic: it never creates a client, performs an
RPC read, or submits a transaction.  Registry enablement, factory fee-tier
availability, and template/token registration remain live on-chain checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address

from .abyss import ABYSS_FEE_TIERS, AbyssPoolProfile, TokenKind
from .auction import ZERO_ADDRESS
from .launch import (
    DUAL_DIVIDENDS_TEMPLATE_ID,
    QUOTE_DIVIDENDS_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    FeeDisposition,
    LaunchFeeAssetMode,
    LaunchFeeDestination,
    get_launch_template_by_hash,
    get_sqrt_ratio_at_tick,
)
from .unified_abis import (
    ABYSS_POOL_CONFIG_COMPONENTS_V3,
    LAUNCH_RECEIPT_COMPONENTS_V3,
    LAUNCH_REQUEST_COMPONENTS_V3,
    UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2,
    V4_POOL_DATA_COMPONENTS_V3,
)

__all__ = [
    "ABYSS_POOL_TYPE",
    "UNISWAP_V4_V3_POOL_TYPE",
    "LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2",
    "LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2",
    "LaunchPoolKind",
    "UnifiedTokenConfig",
    "UnifiedLaunchRequest",
    "UnifiedLaunchPool",
    "UniswapV4PoolConfigV2",
    "UnifiedLaunchReceipt",
    "UniswapV4PoolData",
    "launch_pool_type_id",
    "enabled_launch_pool_adapter",
    "encode_abyss_pool_config",
    "encode_uniswap_v4_pool_config_v2",
    "to_uniswap_v4_pool_config_v2",
    "to_unified_launch_request",
    "unified_launch_value",
    "build_unified_launch_calldata",
    "build_unified_launch_transaction",
    "decode_unified_launch_receipt",
    "decode_v4_pool_data",
]


BytesLike: TypeAlias = bytes | bytearray | str

# LaunchTypesV3.sol pool-type IDs for the two current UnifiedLauncher routes.
ABYSS_POOL_TYPE = keccak(text="black-market.pool.abyss.v1")
UNISWAP_V4_V3_POOL_TYPE = keccak(text="black-market.pool.uniswap-v4.v3")

# LaunchTemplateDefaultsV2.registerAll / registered token-deployer IDs.
LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2 = bytes.fromhex(
    "e586aada1251ca18ae2d1ae0dbc7b67412291ae7184e8bae678cd4a652351868"
)
LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2 = bytes.fromhex(
    "84895d7e94d7d03c557ea01d250250b3d77eb2a648bb918acf50ec481843fa37"
)

_ZERO_BYTES32 = b"\x00" * 32
_ABI_BOOL_FALSE = b"\x00" * 32
_ABI_BOOL_TRUE = b"\x00" * 31 + b"\x01"
_MAX_UINT8 = (1 << 8) - 1
_MAX_UINT16 = (1 << 16) - 1
_MAX_UINT24 = (1 << 24) - 1
_MAX_UINT32 = (1 << 32) - 1
_MAX_UINT128 = (1 << 128) - 1
_MAX_UINT160 = (1 << 160) - 1
_MAX_UINT256 = (1 << 256) - 1
_MAX_INT256 = (1 << 255) - 1
_MIN_INT24 = -(1 << 23)
_MAX_INT24 = (1 << 23) - 1
_MIN_TICK = -887_272
_MAX_TICK = 887_272
_MIN_SQRT_RATIO = 4_295_128_739
_MAX_SQRT_RATIO = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342
_V4_POOL_CONFIG_V2_LENGTH = 10 * 32
_V4_MIN_SPLIT_TOTAL = 100
_V4_HOLDBACK_DIVISOR = 100
_V4_MAX_ABYSS_FEE_PIPS = 1_000_000
_HEX_BYTES = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")

# Write targets from the retired Atomic deployment and the schema-/1 Unified
# deployment.  They are deliberately not valid defaults for a schema-/2 V3
# envelope; current targets come from get_unified_launch_addresses().
_RETIRED_LAUNCH_TARGETS = frozenset(
    {
        "0xaf3fdc499b3717ebe8ad51b66ba78cb083552351",
        "0x17313fd1cf6cf6f7990c297f7e8094a0c4e43f6b",
    }
)


class LaunchPoolKind(str, Enum):
    """Explicit routes registered by the current UnifiedLauncher."""

    ABYSS = "abyss"
    UNISWAP_V4_V3 = "uniswap-v4-v3"


@dataclass(frozen=True)
class UnifiedTokenConfig:
    """The generic TokenConfigV3 envelope accepted by a registered token type."""

    token_type: BytesLike
    token_config: BytesLike
    name: str
    symbol: str
    decimals: int
    supply: int


@dataclass(frozen=True)
class UnifiedLaunchRequest:
    """The ABI-equivalent LaunchRequestV3 envelope for ``UnifiedLauncher.launch``."""

    creator: str
    pool_type: BytesLike
    template_id: BytesLike
    template_version: int
    token: UnifiedTokenConfig
    pool_config: BytesLike
    initial_buy: AtomicInitialBuy
    launched_token_fees: FeeDisposition
    paired_token_fees: FeeDisposition
    deadline: int


@dataclass(frozen=True)
class UniswapV4PoolConfigV2:
    """The exact 10-field V4PoolConfigV2 payload used by the V4 V3 route."""

    paired_token: str
    profile: AbyssPoolProfile
    oracle_config_id: BytesLike
    tick_lower: int
    tick_upper: int
    sqrt_price_x96: int
    liquidity: int
    launched_token_amount_maximum: int
    abyss_fee_pips: int
    external_liquidity_disabled: bool


@dataclass(frozen=True)
class UnifiedLaunchPool:
    """A typed config paired with its exact registry route attribution."""

    kind: LaunchPoolKind | str
    config: AtomicPoolConfig | UniswapV4PoolConfigV2


@dataclass(frozen=True)
class UnifiedLaunchReceipt:
    """Decoded LaunchReceiptV3 returned by the unified launch call."""

    token: str
    pool: str
    token_id: int
    liquidity_launched_token_amount: int
    liquidity_paired_token_amount: int
    initial_buy_paired_token_amount: int
    initial_buy_launched_token_amount: int
    rewards: str
    splitter: str
    fee_claimer: str
    pool_data: bytes


@dataclass(frozen=True)
class UniswapV4PoolData:
    """Decoded V4PoolDataV3 carried in a V4 launch receipt's ``pool_data``."""

    hook: str
    v4_liquidity_locker: str
    abyss_bonus_distributor: str
    abyss_pool: str
    uniswap_liquidity: int
    abyss_liquidity: int
    uniswap_token_amount: int
    abyss_token_amount: int


def _canonical_abi_type(entry: Mapping[str, Any]) -> str:
    type_name = entry["type"]
    if type_name.startswith("tuple"):
        suffix = type_name[len("tuple") :]
        return "(" + ",".join(_canonical_abi_type(component) for component in entry["components"]) + ")" + suffix
    return type_name


_LAUNCH_REQUEST_TYPE = "(" + ",".join(
    _canonical_abi_type(component) for component in LAUNCH_REQUEST_COMPONENTS_V3
) + ")"
_LAUNCH_SELECTOR = keccak(text=f"launch({_LAUNCH_REQUEST_TYPE})")[:4]
_LAUNCH_RECEIPT_TYPE = "(" + ",".join(
    _canonical_abi_type(component) for component in LAUNCH_RECEIPT_COMPONENTS_V3
) + ")"
_ABYSS_POOL_CONFIG_TYPE = "(" + ",".join(
    _canonical_abi_type(component) for component in ABYSS_POOL_CONFIG_COMPONENTS_V3
) + ")"
_UNISWAP_V4_POOL_CONFIG_V2_TYPE = "(" + ",".join(
    _canonical_abi_type(component) for component in UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2
) + ")"
_V4_POOL_DATA_TYPE = "(" + ",".join(
    _canonical_abi_type(component) for component in V4_POOL_DATA_COMPONENTS_V3
) + ")"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _assert_uint(value: object, maximum: int, name: str) -> int:
    if not _is_int(value) or value < 0 or value > maximum:
        raise ValueError(f"{name} must be a nonnegative integer within its Solidity type")
    return int(value)


def _assert_positive_uint(value: object, maximum: int, name: str) -> int:
    value = _assert_uint(value, maximum, name)
    if value == 0:
        raise ValueError(f"{name} must be positive")
    return value


def _assert_int24(value: object, name: str) -> int:
    if not _is_int(value) or value < _MIN_INT24 or value > _MAX_INT24:
        raise ValueError(f"{name} must be an int24 integer")
    return int(value)


def _assert_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _assert_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _normalize_bytes(value: object, name: str) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str) and _HEX_BYTES.fullmatch(value):
        return bytes.fromhex(value[2:])
    raise ValueError(f"{name} must be bytes or a 0x-prefixed even-length hexadecimal string")


def _normalize_bytes32(value: object, name: str, *, nonzero: bool = False) -> bytes:
    data = _normalize_bytes(value, name)
    if len(data) != 32:
        raise ValueError(f"{name} must be exactly 32 bytes")
    if nonzero and data == _ZERO_BYTES32:
        raise ValueError(f"{name} must be nonzero")
    return data


def _assert_address(value: object, name: str, *, nonzero: bool = True) -> str:
    if not isinstance(value, str) or not is_address(value):
        raise ValueError(f"{name} must be an address")
    if nonzero and value.lower() == ZERO_ADDRESS:
        raise ValueError(f"{name} must be a nonzero address")
    return value


def _assert_canonical_sqrt_price(value: object, name: str) -> int:
    value = _assert_uint(value, _MAX_UINT160, name)
    if value < _MIN_SQRT_RATIO or value >= _MAX_SQRT_RATIO:
        raise ValueError(f"{name} is outside the canonical swap price bounds")
    return value


def _pool_kind(value: LaunchPoolKind | str) -> LaunchPoolKind:
    try:
        return LaunchPoolKind(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Unsupported launch pool kind: {value!r}") from error


def _pool_kind_for_type(pool_type: bytes) -> LaunchPoolKind | None:
    if pool_type == ABYSS_POOL_TYPE:
        return LaunchPoolKind.ABYSS
    if pool_type == UNISWAP_V4_V3_POOL_TYPE:
        return LaunchPoolKind.UNISWAP_V4_V3
    return None


def launch_pool_type_id(kind: LaunchPoolKind | str) -> bytes:
    """Return the exact registry key for an explicitly attributed launch route."""

    normalized = _pool_kind(kind)
    if normalized is LaunchPoolKind.ABYSS:
        return ABYSS_POOL_TYPE
    return UNISWAP_V4_V3_POOL_TYPE


def enabled_launch_pool_adapter(entry: object) -> str | None:
    """Return an adapter only for a nonzero, enabled ``poolTypes`` registry row.

    Web3 contract results are commonly a positional tuple, but named mapping-like
    results are also accepted.  A malformed external result is not treated as an
    enabled route.
    """

    if entry is None:
        return None
    try:
        if isinstance(entry, Mapping):
            adapter = entry["adapter"]
            disabled = entry["disabled"]
        elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes, bytearray)):
            adapter, disabled = entry[0], entry[1]
        else:
            return None
    except (IndexError, KeyError, TypeError):
        return None
    if not isinstance(adapter, str) or not is_address(adapter) or disabled is not False:
        return None
    return None if adapter.lower() == ZERO_ADDRESS else adapter


def _abyss_pool_config_tuple(config: AtomicPoolConfig) -> tuple[object, ...]:
    if not isinstance(config, AtomicPoolConfig):
        raise ValueError("Abyss pool config must be an AtomicPoolConfig")
    return (
        _assert_address(config.paired_token, "pool.paired_token"),
        _assert_bool(config.launched_token_is_quote, "pool.launched_token_is_quote"),
        _assert_uint(config.profile, _MAX_UINT8, "pool.profile"),
        _assert_uint(config.fee, _MAX_UINT24, "pool.fee"),
        _normalize_bytes32(config.oracle_config_id, "pool.oracle_config_id"),
        _assert_int24(config.launch_tick, "pool.launch_tick"),
        _assert_uint(config.liquidity, _MAX_UINT128, "pool.liquidity"),
        _assert_uint(
            config.launched_token_amount_maximum,
            _MAX_UINT256,
            "pool.launched_token_amount_maximum",
        ),
        _assert_uint(
            config.paired_token_amount_maximum,
            _MAX_UINT256,
            "pool.paired_token_amount_maximum",
        ),
    )


def _v4_pool_config_tuple(config: UniswapV4PoolConfigV2) -> tuple[object, ...]:
    if not isinstance(config, UniswapV4PoolConfigV2):
        raise ValueError("Uniswap V4 pool config must be a UniswapV4PoolConfigV2")
    return (
        _assert_address(config.paired_token, "pool.paired_token"),
        _assert_uint(config.profile, _MAX_UINT8, "pool.profile"),
        _normalize_bytes32(config.oracle_config_id, "pool.oracle_config_id"),
        _assert_int24(config.tick_lower, "pool.tick_lower"),
        _assert_int24(config.tick_upper, "pool.tick_upper"),
        _assert_uint(config.sqrt_price_x96, _MAX_UINT160, "pool.sqrt_price_x96"),
        _assert_uint(config.liquidity, _MAX_UINT128, "pool.liquidity"),
        _assert_uint(
            config.launched_token_amount_maximum,
            _MAX_UINT256,
            "pool.launched_token_amount_maximum",
        ),
        _assert_uint(config.abyss_fee_pips, _MAX_UINT24, "pool.abyss_fee_pips"),
        _assert_bool(config.external_liquidity_disabled, "pool.external_liquidity_disabled"),
    )


def encode_abyss_pool_config(config: AtomicPoolConfig) -> bytes:
    """Encode the exact nine-field ``AbyssPoolConfigV3`` ABI payload."""

    return abi_encode([_ABYSS_POOL_CONFIG_TYPE], [_abyss_pool_config_tuple(config)])


def encode_uniswap_v4_pool_config_v2(config: UniswapV4PoolConfigV2) -> bytes:
    """Encode the exact ten-field ``V4PoolConfigV2`` ABI payload.

    In particular, it has no V1 ``protocolBps`` eleventh field.
    """

    return abi_encode([_UNISWAP_V4_POOL_CONFIG_V2_TYPE], [_v4_pool_config_tuple(config)])


def _signed_remainder(tick: int, spacing: int) -> int:
    """Solidity's signed remainder (Python's ``%`` floors negative values)."""

    return tick % spacing if tick >= 0 else -((-tick) % spacing)


def _align_down(tick: int, spacing: int) -> int:
    remainder = _signed_remainder(tick, spacing)
    return tick if remainder == 0 else tick - remainder


def _align_up(tick: int, spacing: int) -> int:
    remainder = _signed_remainder(tick, spacing)
    return tick if remainder == 0 else tick + (spacing - remainder)


def _known_tick_spacing_or_none(fee: object) -> int | None:
    for tier in ABYSS_FEE_TIERS:
        if tier.fee_pips == fee:
            return tier.tick_spacing
    return None


def _known_tick_spacing(fee: object) -> int:
    tick_spacing = _known_tick_spacing_or_none(fee)
    if tick_spacing is None:
        raise ValueError(
            "pool.fee has no SDK recipe tick spacing; build UniswapV4PoolConfigV2 directly "
            "after checking the authoritative factory"
        )
    return tick_spacing


def _split_safe_v4_liquidity(liquidity: object) -> int:
    liquidity = _assert_uint(liquidity, _MAX_UINT128, "pool.liquidity")
    if liquidity < _V4_MIN_SPLIT_TOTAL + 1:
        raise ValueError("V4 liquidity is too small for split rounding")
    return liquidity - (2 if liquidity % _V4_HOLDBACK_DIVISOR == 0 else 1)


def to_uniswap_v4_pool_config_v2(
    pool: AtomicPoolConfig,
    launch_sqrt_price_x96: int,
    external_liquidity_disabled: bool,
) -> UniswapV4PoolConfigV2:
    """Map an Atomic recipe to the deployed V4 V3 ten-field config.

    This helper intentionally uses the SDK's known fee tiers only to derive a
    tick spacing.  It is not a claim that that tier is currently enabled: the
    V4 adapter's coordinator-bound factory is authoritative at submission.
    """

    encoded = _abyss_pool_config_tuple(pool)
    paired_token, launched_is_quote, profile, oracle_id, launch_tick, liquidity, launched_max = (
        encoded[0],
        encoded[1],
        encoded[2],
        encoded[4],
        encoded[5],
        encoded[6],
        encoded[7],
    )
    fee = encoded[3]
    _validate_profile(profile, "pool.profile")
    tick_spacing = _known_tick_spacing(fee)
    if launch_tick < _MIN_TICK or launch_tick > _MAX_TICK:
        raise ValueError("pool.launch_tick is outside the deployed TickMath bounds")
    if launch_tick % tick_spacing != 0:
        raise ValueError("pool.launch_tick must align with the selected fee tier's tick spacing")

    tick_lower = launch_tick if launched_is_quote else _align_up(_MIN_TICK, tick_spacing)
    tick_upper = _align_down(_MAX_TICK, tick_spacing) if launched_is_quote else launch_tick
    if tick_lower >= tick_upper:
        raise ValueError("pool.launch_tick cannot form a one-sided Uniswap V4 tick band")

    return UniswapV4PoolConfigV2(
        paired_token=paired_token,
        profile=AbyssPoolProfile(profile),
        oracle_config_id=oracle_id,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        sqrt_price_x96=_assert_canonical_sqrt_price(
            launch_sqrt_price_x96, "launch_sqrt_price_x96"
        ),
        liquidity=_split_safe_v4_liquidity(liquidity),
        launched_token_amount_maximum=launched_max,
        abyss_fee_pips=fee,
        external_liquidity_disabled=_assert_bool(
            external_liquidity_disabled, "external_liquidity_disabled"
        ),
    )


def _disposition_tuple(disposition: FeeDisposition, name: str) -> tuple[int, int, int]:
    if not isinstance(disposition, FeeDisposition):
        raise ValueError(f"{name} must be a FeeDisposition")
    owner = _assert_uint(disposition.owner_bps, _MAX_UINT16, f"{name}.owner_bps")
    rewards = _assert_uint(disposition.rewards_bps, _MAX_UINT16, f"{name}.rewards_bps")
    burn = _assert_uint(disposition.burn_bps, _MAX_UINT16, f"{name}.burn_bps")
    total = owner + rewards + burn
    # Every current template fee asset is either inactive (all zero) or has a
    # complete 10,000 bps policy.  Which asset is active remains template state.
    if total not in (0, 10_000):
        raise ValueError(f"{name} must total either zero or 10,000 basis points")
    return owner, rewards, burn


def _validate_template_disposition(
    disposition: FeeDisposition,
    name: str,
    *,
    active: bool,
    allowed_destinations: int,
) -> None:
    owner, rewards, burn = _disposition_tuple(disposition, name)
    total = owner + rewards + burn
    if active:
        if total != 10_000:
            raise ValueError(f"{name} must total 10,000 basis points")
    elif total != 0:
        raise ValueError(f"{name} must total zero basis points")

    destinations = (
        (int(LaunchFeeDestination.OWNER) if owner else 0)
        | (int(LaunchFeeDestination.REWARDS) if rewards else 0)
        | (int(LaunchFeeDestination.BURN) if burn else 0)
    )
    if destinations & ~int(allowed_destinations):
        raise ValueError(f"{name} uses a destination not allowed by the template")


def _template_fee_assets(template: object, profile: int) -> tuple[bool, bool]:
    mode = template.fee_asset_mode
    launched_active = (
        mode == LaunchFeeAssetMode.BOTH
        or mode == LaunchFeeAssetMode.LAUNCHED_ONLY
        or (mode == LaunchFeeAssetMode.PROFILE and profile == int(AbyssPoolProfile.STANDARD_ORACLE))
    )
    paired_active = mode in (
        LaunchFeeAssetMode.PROFILE,
        LaunchFeeAssetMode.PAIRED_ONLY,
        LaunchFeeAssetMode.BOTH,
    )
    return launched_active, paired_active

def _known_template_for_version(template_id: bytes, template_version: int) -> object | None:
    try:
        template = get_launch_template_by_hash(template_id)
    except ValueError:
        return None
    return template if template.version == template_version else None


def _pinned_template_token_config(template_id: bytes) -> bytes | None:
    if template_id == QUOTE_DIVIDENDS_TEMPLATE_ID:
        return _ABI_BOOL_FALSE
    if template_id == DUAL_DIVIDENDS_TEMPLATE_ID:
        return _ABI_BOOL_TRUE
    return None


def _validate_known_token_config(token_type: bytes, token_config: bytes) -> None:
    if token_type == LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2:
        if token_config:
            raise ValueError("burnable token_config must be empty")
    elif (
        token_type == LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2
        and token_config not in (_ABI_BOOL_FALSE, _ABI_BOOL_TRUE)
    ):
        raise ValueError("holder-dividend token_config must be exactly abi.encode(bool)")


def _validate_known_template_request(
    template: object,
    template_id: bytes,
    token_config: bytes,
    config: AtomicPoolConfig | UniswapV4PoolConfigV2 | None,
    launched_token_fees: FeeDisposition,
    paired_token_fees: FeeDisposition,
) -> None:
    pinned_token_config = _pinned_template_token_config(template_id)
    if pinned_token_config is not None and token_config != pinned_token_config:
        raise ValueError("token_config does not match the known template")
    if config is None:
        return

    profile = _pool_profile_for_conversion(config)
    if profile not in {int(item) for item in template.pool_profiles}:
        raise ValueError("Pool profile is not supported by the known template")
    if (
        isinstance(config, AtomicPoolConfig)
        and config.launched_token_is_quote != template.launched_token_is_quote
    ):
        raise ValueError("Pool orientation does not match the known template")

    launched_active, paired_active = _template_fee_assets(template, profile)
    _validate_template_disposition(
        launched_token_fees,
        "launched_token_fees",
        active=launched_active,
        allowed_destinations=template.launched_token_destinations,
    )
    _validate_template_disposition(
        paired_token_fees,
        "paired_token_fees",
        active=paired_active,
        allowed_destinations=template.paired_token_destinations,
    )


def _pool_profile_for_conversion(config: AtomicPoolConfig | UniswapV4PoolConfigV2) -> int:
    if isinstance(config, AtomicPoolConfig):
        return _assert_uint(config.profile, _MAX_UINT8, "pool.profile")
    if isinstance(config, UniswapV4PoolConfigV2):
        return _assert_uint(config.profile, _MAX_UINT8, "pool.profile")
    raise ValueError("Unified launch pool has an unsupported config type")


def _validate_atomic_conversion(
    request: AtomicLaunchRequest, pool: UnifiedLaunchPool
) -> tuple[object, int, bytes, bytes]:
    if not isinstance(request, AtomicLaunchRequest):
        raise ValueError("request must be an AtomicLaunchRequest")
    if not isinstance(pool, UnifiedLaunchPool):
        raise ValueError("pool must be a UnifiedLaunchPool")

    template_id = _normalize_bytes32(request.template_id, "template_id", nonzero=True)
    try:
        template = get_launch_template_by_hash(template_id)
    except ValueError as error:
        raise ValueError("Unknown launch template") from error
    if request.template_version != template.version:
        raise ValueError("Unsupported template version")
    if request.token.kind not in template.token_kinds:
        raise ValueError("Token kind is not supported by the template")

    kind = _pool_kind(pool.kind)
    if kind is LaunchPoolKind.ABYSS:
        if not isinstance(pool.config, AtomicPoolConfig):
            raise ValueError("Abyss launches require an AtomicPoolConfig")
        if pool.config.launched_token_is_quote != template.launched_token_is_quote:
            raise ValueError("Pool orientation does not match the template")
    elif not isinstance(pool.config, UniswapV4PoolConfigV2):
        raise ValueError("Uniswap V4 launches require a UniswapV4PoolConfigV2")

    profile = _pool_profile_for_conversion(pool.config)
    if profile not in {int(item) for item in template.pool_profiles}:
        raise ValueError("Pool profile is not supported by the template")
    launched_active, paired_active = _template_fee_assets(template, profile)
    _validate_template_disposition(
        request.launched_token_fees,
        "launched_token_fees",
        active=launched_active,
        allowed_destinations=template.launched_token_destinations,
    )
    _validate_template_disposition(
        request.paired_token_fees,
        "paired_token_fees",
        active=paired_active,
        allowed_destinations=template.paired_token_destinations,
    )

    if request.token.kind == TokenKind.HOLDER_DIVIDEND:
        token_type = LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2
        token_config = abi_encode(["bool"], [template_id == DUAL_DIVIDENDS_TEMPLATE_ID])
    else:
        token_type = LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2
        token_config = b""
    return template, profile, token_type, token_config


def to_unified_launch_request(
    request: AtomicLaunchRequest, pool: UnifiedLaunchPool
) -> UnifiedLaunchRequest:
    """Convert a known Atomic template request to the generic V3 envelope.

    Atomic conversion intentionally enforces the registered default template
    mapping: burnable templates carry empty config; holder-dividend templates
    carry ``abi.encode(bool dualRewards)``.  Direct ``UnifiedLaunchRequest``
    construction remains available for other registered token/template types.
    """

    _, _, token_type, token_config = _validate_atomic_conversion(request, pool)
    kind = _pool_kind(pool.kind)
    pool_config = (
        encode_abyss_pool_config(pool.config)
        if kind is LaunchPoolKind.ABYSS
        else encode_uniswap_v4_pool_config_v2(pool.config)
    )
    return UnifiedLaunchRequest(
        creator=request.creator,
        pool_type=launch_pool_type_id(kind),
        template_id=request.template_id,
        template_version=request.template_version,
        token=UnifiedTokenConfig(
            token_type=token_type,
            token_config=token_config,
            name=request.token.name,
            symbol=request.token.symbol,
            decimals=request.token.decimals,
            supply=request.token.supply,
        ),
        pool_config=pool_config,
        initial_buy=request.initial_buy,
        launched_token_fees=request.launched_token_fees,
        paired_token_fees=request.paired_token_fees,
        deadline=request.deadline,
    )


def unified_launch_value(
    pool_kind: LaunchPoolKind | str,
    launch_fee: int = 0,
    native_buy_amount: int = 0,
) -> int:
    """Return native value for a launch route without sending or estimating it.

    Abyss consumes its adapter fee plus any wrapped-native first buy.  The V4
    V3 route rejects nonzero ``msg.value`` on-chain, so its native value is zero
    even if a caller supplied irrelevant fee arguments.
    """

    kind = _pool_kind(pool_kind)
    launch_fee = _assert_uint(launch_fee, _MAX_UINT256, "launch_fee")
    native_buy_amount = _assert_uint(native_buy_amount, _MAX_UINT256, "native_buy_amount")
    if kind is not LaunchPoolKind.ABYSS:
        return 0
    value = launch_fee + native_buy_amount
    if value > _MAX_UINT256:
        raise ValueError("launch_fee plus native_buy_amount exceeds uint256")
    return value


def _validate_profile(profile: int, name: str) -> None:
    if profile not in (int(AbyssPoolProfile.STANDARD_ORACLE), int(AbyssPoolProfile.QUOTE_ORACLE)):
        raise ValueError(f"{name} must be an oracle-backed Abyss pool profile")


def _validate_initial_buy(initial_buy: AtomicInitialBuy, effective_amount: int) -> tuple[int, int, int]:
    if not isinstance(initial_buy, AtomicInitialBuy):
        raise ValueError("initial_buy must be an AtomicInitialBuy")
    amount_in = _assert_uint(
        initial_buy.paired_token_amount_in,
        _MAX_UINT256,
        "initial_buy.paired_token_amount_in",
    )
    amount_out_minimum = _assert_uint(
        initial_buy.launched_token_amount_out_minimum,
        _MAX_UINT256,
        "initial_buy.launched_token_amount_out_minimum",
    )
    sqrt_limit = _assert_uint(
        initial_buy.sqrt_price_limit_x96,
        _MAX_UINT160,
        "initial_buy.sqrt_price_limit_x96",
    )
    if effective_amount == 0:
        if amount_out_minimum != 0 or sqrt_limit != 0:
            raise ValueError("zero initial buy cannot set output or price limits")
    else:
        if sqrt_limit == 0:
            raise ValueError("nonzero initial buy requires a directional price limit")
        _assert_canonical_sqrt_price(sqrt_limit, "initial_buy.sqrt_price_limit_x96")
    return amount_in, amount_out_minimum, sqrt_limit

def _validate_initial_buy_direction(
    initial_buy: tuple[int, int, int],
    effective_amount: int,
    launch_sqrt_price_x96: int | None,
    launched_token_is_quote: bool,
) -> None:
    if effective_amount == 0 or launch_sqrt_price_x96 is None:
        return
    sqrt_limit = initial_buy[2]
    if (
        not launched_token_is_quote and sqrt_limit >= launch_sqrt_price_x96
    ) or (
        launched_token_is_quote and sqrt_limit <= launch_sqrt_price_x96
    ):
        raise ValueError(
            "initial buy price limit does not protect the paired-token swap direction"
        )


def _known_abyss_launch_sqrt_price(config: AtomicPoolConfig) -> int | None:
    tick_spacing = _known_tick_spacing_or_none(config.fee)
    if tick_spacing is None:
        return None
    tick_lower = (
        _align_up(config.launch_tick, tick_spacing)
        if config.launched_token_is_quote
        else _align_up(_MIN_TICK, tick_spacing)
    )
    tick_upper = (
        _align_down(_MAX_TICK, tick_spacing)
        if config.launched_token_is_quote
        else _align_down(config.launch_tick, tick_spacing)
    )
    if tick_lower < _MIN_TICK or tick_upper > _MAX_TICK or tick_lower >= tick_upper:
        raise ValueError("pool.launch_tick cannot form a valid Abyss launch band")
    return get_sqrt_ratio_at_tick(
        tick_lower if config.launched_token_is_quote else tick_upper
    )


def _decode_pool_config(
    pool_type: bytes, pool_config: bytes, supply: int
) -> AtomicPoolConfig | UniswapV4PoolConfigV2:
    if pool_type == ABYSS_POOL_TYPE:
        try:
            values = abi_decode([_ABYSS_POOL_CONFIG_TYPE], pool_config)[0]
        except Exception as error:
            raise ValueError("pool_config does not match the selected launch route") from error
        config = AtomicPoolConfig(
            paired_token=values[0],
            launched_token_is_quote=values[1],
            profile=values[2],
            fee=values[3],
            oracle_config_id=values[4],
            launch_tick=values[5],
            liquidity=values[6],
            launched_token_amount_maximum=values[7],
            paired_token_amount_maximum=values[8],
        )
        _validate_abyss_launch_config(config, supply)
        return config

    if len(pool_config) != _V4_POOL_CONFIG_V2_LENGTH:
        raise ValueError("V4 pool_config must be exactly 320 bytes")
    try:
        values = abi_decode([_UNISWAP_V4_POOL_CONFIG_V2_TYPE], pool_config)[0]
    except Exception as error:
        raise ValueError("pool_config does not match the selected launch route") from error
    config = UniswapV4PoolConfigV2(
        paired_token=values[0],
        profile=values[1],
        oracle_config_id=values[2],
        tick_lower=values[3],
        tick_upper=values[4],
        sqrt_price_x96=values[5],
        liquidity=values[6],
        launched_token_amount_maximum=values[7],
        abyss_fee_pips=values[8],
        external_liquidity_disabled=values[9],
    )
    _validate_v4_launch_config(config, supply)
    return config


def _validate_abyss_launch_config(config: AtomicPoolConfig, supply: int) -> None:
    values = _abyss_pool_config_tuple(config)
    profile = values[2]
    _validate_profile(profile, "pool.profile")
    _normalize_bytes32(config.oracle_config_id, "pool.oracle_config_id", nonzero=True)
    if values[3] == 0:
        raise ValueError("pool.fee must be positive")
    if values[5] < _MIN_TICK or values[5] > _MAX_TICK:
        raise ValueError("pool.launch_tick is outside the deployed TickMath bounds")
    if values[6] == 0:
        raise ValueError("pool.liquidity must be positive")
    if values[7] == 0:
        raise ValueError("pool.launched_token_amount_maximum must be positive")
    if values[7] > supply:
        raise ValueError("pool.launched_token_amount_maximum cannot exceed token.supply")


def _validate_v4_launch_config(config: UniswapV4PoolConfigV2, supply: int) -> None:
    values = _v4_pool_config_tuple(config)
    profile = values[1]
    _validate_profile(profile, "pool.profile")
    _normalize_bytes32(config.oracle_config_id, "pool.oracle_config_id", nonzero=True)
    tick_lower, tick_upper = values[3], values[4]
    if tick_lower < _MIN_TICK or tick_upper > _MAX_TICK or tick_lower >= tick_upper:
        raise ValueError("pool tick range is outside the deployed TickMath bounds")
    _assert_canonical_sqrt_price(values[5], "pool.sqrt_price_x96")
    if values[6] < _V4_MIN_SPLIT_TOTAL:
        raise ValueError("pool.liquidity must be at least 100 for the V4 99/1 split")
    if values[7] < _V4_MIN_SPLIT_TOTAL:
        raise ValueError(
            "pool.launched_token_amount_maximum must be at least 100 for the V4 99/1 split"
        )
    if values[7] > supply:
        raise ValueError("pool.launched_token_amount_maximum cannot exceed token.supply")
    # _splitAmount computes total * 100 / 10,000 with checked uint256 arithmetic.
    if values[7] > _MAX_UINT256 // _V4_HOLDBACK_DIVISOR:
        raise ValueError("pool.launched_token_amount_maximum overflows the V4 split")
    if values[8] == 0 or values[8] > _V4_MAX_ABYSS_FEE_PIPS:
        raise ValueError("pool.abyss_fee_pips must be between 1 and 1,000,000")


def _validated_request_tuple(
    request: UnifiedLaunchRequest,
    native_buy_amount: int,
    wrapped_native_token: str | None,
) -> tuple[object, ...]:
    if not isinstance(request, UnifiedLaunchRequest):
        raise ValueError("request must be a UnifiedLaunchRequest")

    creator = _assert_address(request.creator, "creator")
    pool_type = _normalize_bytes32(request.pool_type, "pool_type", nonzero=True)
    template_id = _normalize_bytes32(request.template_id, "template_id", nonzero=True)
    template_version = _assert_positive_uint(
        request.template_version, _MAX_UINT32, "template_version"
    )
    if not isinstance(request.token, UnifiedTokenConfig):
        raise ValueError("token must be a UnifiedTokenConfig")
    token = (
        _normalize_bytes32(request.token.token_type, "token.token_type", nonzero=True),
        _normalize_bytes(request.token.token_config, "token.token_config"),
        _assert_string(request.token.name, "token.name"),
        _assert_string(request.token.symbol, "token.symbol"),
        _assert_uint(request.token.decimals, _MAX_UINT8, "token.decimals"),
        _assert_positive_uint(request.token.supply, _MAX_UINT256, "token.supply"),
    )
    _validate_known_token_config(token[0], token[1])
    pool_config = _normalize_bytes(request.pool_config, "pool_config")
    launched_fees = _disposition_tuple(request.launched_token_fees, "launched_token_fees")
    paired_fees = _disposition_tuple(request.paired_token_fees, "paired_token_fees")
    deadline = _assert_positive_uint(request.deadline, _MAX_UINT256, "deadline")
    native_buy_amount = _assert_uint(native_buy_amount, _MAX_UINT256, "native_buy_amount")
    if not isinstance(request.initial_buy, AtomicInitialBuy):
        raise ValueError("initial_buy must be an AtomicInitialBuy")
    initial_buy_amount = _assert_uint(
        request.initial_buy.paired_token_amount_in,
        _MAX_UINT256,
        "initial_buy.paired_token_amount_in",
    )
    known_template = _known_template_for_version(template_id, template_version)
    config: AtomicPoolConfig | UniswapV4PoolConfigV2 | None = None

    route = _pool_kind_for_type(pool_type)
    if route is LaunchPoolKind.ABYSS:
        config = _decode_pool_config(pool_type, pool_config, token[5])
        assert isinstance(config, AtomicPoolConfig)
        if native_buy_amount:
            if wrapped_native_token is None:
                raise ValueError("wrapped_native_token is required for a native Abyss initial buy")
            wrapped_native = _assert_address(wrapped_native_token, "wrapped_native_token")
            if config.paired_token.lower() != wrapped_native.lower():
                raise ValueError("native Abyss initial buy requires pool.paired_token to be wrapped_native_token")
        effective_amount = initial_buy_amount + native_buy_amount
        if effective_amount > _MAX_UINT256:
            raise ValueError("initial buy plus native_buy_amount exceeds uint256")
        initial_buy = _validate_initial_buy(request.initial_buy, effective_amount)
        _validate_initial_buy_direction(
            initial_buy,
            effective_amount,
            _known_abyss_launch_sqrt_price(config),
            config.launched_token_is_quote,
        )
    elif route is LaunchPoolKind.UNISWAP_V4_V3:
        if native_buy_amount:
            raise ValueError("Uniswap V4 launch routes require zero native_buy_amount")
        config = _decode_pool_config(pool_type, pool_config, token[5])
        assert isinstance(config, UniswapV4PoolConfigV2)
        initial_buy = _validate_initial_buy(request.initial_buy, initial_buy_amount)
        if initial_buy[0] > _MAX_INT256:
            raise ValueError("V4 initial_buy.paired_token_amount_in exceeds int256")
    else:
        if native_buy_amount:
            raise ValueError("native_buy_amount is unsupported for an unknown launch pool type")
        initial_buy = _validate_initial_buy(request.initial_buy, initial_buy_amount)
    if known_template is not None:
        _validate_known_template_request(
            known_template,
            template_id,
            token[1],
            config,
            request.launched_token_fees,
            request.paired_token_fees,
        )
        if isinstance(config, UniswapV4PoolConfigV2):
            _validate_initial_buy_direction(
                initial_buy,
                initial_buy_amount,
                config.sqrt_price_x96,
                known_template.launched_token_is_quote,
            )


    return (
        creator,
        pool_type,
        template_id,
        template_version,
        token,
        pool_config,
        initial_buy,
        launched_fees,
        paired_fees,
        deadline,
    )


def _encode_launch_function(request_tuple: tuple[object, ...]) -> bytes:
    return _LAUNCH_SELECTOR + abi_encode([_LAUNCH_REQUEST_TYPE], [request_tuple])


def build_unified_launch_calldata(
    request: UnifiedLaunchRequest,
    *,
    native_buy_amount: int = 0,
    wrapped_native_token: str | None = None,
) -> bytes:
    """Validate and encode ``UnifiedLauncher.launch`` calldata without I/O.

    ``native_buy_amount`` is only valid for an Abyss request whose paired asset
    is the supplied wrapped-native token.  It is intentionally separate from
    ``initial_buy.paired_token_amount_in`` because the adapter computes their
    sum after wrapping native value.
    """

    return _encode_launch_function(
        _validated_request_tuple(request, native_buy_amount, wrapped_native_token)
    )


def _resolve_unified_launch_target(chain_id: int, launch_factory: str | None) -> str:
    # Kept local to the transaction builder so importing pure envelope helpers
    # does not force address selection or an environment-derived deployment view.
    from .addresses import get_unified_launch_addresses

    try:
        configured = get_unified_launch_addresses(chain_id).unified_launcher
    except (KeyError, ValueError) as error:
        raise ValueError(f"Unsupported chain_id for UnifiedLauncher: {chain_id}") from error

    target = (
        _assert_address(configured, "configured unified_launcher")
        if launch_factory is None
        else _assert_address(launch_factory, "launch_factory")
    )
    if target.lower() in _RETIRED_LAUNCH_TARGETS:
        raise ValueError("launch_factory is a retired launch target")
    return to_checksum_address(target)


def build_unified_launch_transaction(
    request: UnifiedLaunchRequest,
    *,
    chain_id: int,
    launch_factory: str | None = None,
    launch_fee: int = 0,
    native_buy_amount: int = 0,
    wrapped_native_token: str | None = None,
) -> dict[str, object]:
    """Build a web3-compatible unsigned transaction dictionary without I/O.

    Defaults reject zero, unavailable, and retired catalog targets. An explicit
    nonretired target is permitted on a supported chain; the caller remains
    responsible for selecting a deployed route.
    """

    chain_id = _assert_positive_uint(chain_id, _MAX_UINT256, "chain_id")
    calldata = build_unified_launch_calldata(
        request,
        native_buy_amount=native_buy_amount,
        wrapped_native_token=wrapped_native_token,
    )
    pool_type = _normalize_bytes32(request.pool_type, "pool_type", nonzero=True)
    route = _pool_kind_for_type(pool_type)
    launch_fee = _assert_uint(launch_fee, _MAX_UINT256, "launch_fee")
    if route is LaunchPoolKind.ABYSS and launch_fee == 0:
        raise ValueError("launch_fee must be positive for an Abyss launch")
    if route is None:
        if launch_fee:
            raise ValueError("launch_fee is unsupported for an unknown launch pool type")
        value = 0
    else:
        value = unified_launch_value(route, launch_fee, native_buy_amount)

    return {
        "chainId": chain_id,
        "to": _resolve_unified_launch_target(chain_id, launch_factory),
        "data": calldata,
        "value": value,
    }


def _decode_tuple(data: BytesLike, abi_type: str, name: str) -> tuple[object, ...]:
    payload = _normalize_bytes(data, name)
    try:
        return abi_decode([abi_type], payload)[0]
    except Exception as error:
        raise ValueError(f"{name} is not ABI-encoded {abi_type}") from error


def decode_unified_launch_receipt(data: BytesLike) -> UnifiedLaunchReceipt:
    """Decode an ABI-encoded ``LaunchReceiptV3`` returned by ``launch``."""

    values = _decode_tuple(data, _LAUNCH_RECEIPT_TYPE, "launch receipt")
    return UnifiedLaunchReceipt(
        token=values[0],
        pool=values[1],
        token_id=values[2],
        liquidity_launched_token_amount=values[3],
        liquidity_paired_token_amount=values[4],
        initial_buy_paired_token_amount=values[5],
        initial_buy_launched_token_amount=values[6],
        rewards=values[7],
        splitter=values[8],
        fee_claimer=values[9],
        pool_data=values[10],
    )


def decode_v4_pool_data(data: BytesLike) -> UniswapV4PoolData:
    """Decode V4-specific ``LaunchReceiptV3.pool_data`` bytes."""

    values = _decode_tuple(data, _V4_POOL_DATA_TYPE, "V4 pool_data")
    return UniswapV4PoolData(
        hook=values[0],
        v4_liquidity_locker=values[1],
        abyss_bonus_distributor=values[2],
        abyss_pool=values[3],
        uniswap_liquidity=values[4],
        abyss_liquidity=values[5],
        uniswap_token_amount=values[6],
        abyss_token_amount=values[7],
    )


