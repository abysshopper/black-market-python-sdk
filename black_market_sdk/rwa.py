"""Deployed RWA collector terms and offline Dividends launch invariants."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import is_address, to_checksum_address

if TYPE_CHECKING:
    from .lifecycle import LaunchPlanV1, LifecycleMarketConfig

RWA_FIXED_PROFILE_ID = "0x3b170b03f1ff1c808970f43827df234cde866d73608b6e3e3937601ae7c8676b"
RWA_DYNAMIC_PROFILE_ID = "0xab6c24038ff6ec16a300aa268dc4116f1649ae07c272dfc7e814c6f198515043"
RWA_FIXED_ADAPTER_ID = "0x0f13f4a8553a4dece69888deb425b04ff169d41132f3e2097034aff00f584dd6"
RWA_DYNAMIC_ADAPTER_ID = "0x46a5f7c611f6584cc99cffcdeb55183d6c3dcafe5fc6fcce85995be82639bbe1"
# ERC1967 stock-registry proxy and the only quote its deployed collectors accept.
RWA_ASSET_REGISTRY_ADDRESS = "0x40356d61871C7Af760903FDD6ceb9344D7c3F328"
RWA_QUOTE_ASSET = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
RWA_MAX_BASKET = 6
RWA_MAX_PREMIUM_BPS = 100
# Disables the stock-feed age check; the USDG feed keeps its own window.
RWA_ANY_PRICE_AGE = 0xFFFFFFFF
_RWA_LAUNCH_ABI_TYPE = "(uint16,uint16,uint32,address[],uint16[])"


@dataclass(frozen=True)
class RwaLaunchConfigV1:
    share_bps: int
    premium_bps: int
    max_price_age: int
    basket: tuple[str, ...]
    weights_bps: tuple[int, ...]


def is_rwa_profile(profile_id: bytes | str) -> bool:
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id.lower()
    return identity in (RWA_FIXED_PROFILE_ID, RWA_DYNAMIC_PROFILE_ID)


def _uint(value: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= maximum


def validate_rwa_launch_config(config: RwaLaunchConfigV1) -> None:
    """Validate static terms; live registry listing is enforced on-chain at begin."""
    from .lifecycle import LifecyclePlanningError
    def invalid(reason: str) -> None:
        raise LifecyclePlanningError("INVALID_RWA_CONFIG", reason)
    if not _uint(config.share_bps, 10000):
        invalid("RWA share_bps must be an integer from 0 to 10000")
    if not _uint(config.premium_bps, RWA_MAX_PREMIUM_BPS):
        invalid("RWA premium_bps must be an integer from 0 to 100")
    if not _uint(config.max_price_age, RWA_ANY_PRICE_AGE):
        invalid("RWA max_price_age must be a uint32")
    count = len(config.basket)
    if not 1 <= count <= RWA_MAX_BASKET:
        invalid("An RWA basket holds 1 to 6 stock tokens")
    if len(config.weights_bps) != count:
        invalid("RWA weights_bps needs exactly one weight per basket stock")
    previous, total = 0, 0
    for asset, weight in zip(config.basket, config.weights_bps):
        if not isinstance(asset, str) or not asset.startswith("0x") or not is_address(asset) or int(asset, 16) <= previous:
            invalid("RWA basket stocks must be unique ascending addresses")
        if asset.lower() == RWA_QUOTE_ASSET.lower():
            invalid("USDG cannot be an RWA basket stock")
        if not _uint(weight, 10000):
            invalid("RWA weights must be integer basis points")
        previous, total = int(asset, 16), total + weight
    if total != 10000:
        invalid("RWA weights_bps must sum to exactly 10000")


def encode_rwa_launch_tail(config: RwaLaunchConfigV1) -> bytes:
    """Return exactly abi.encode(RwaLaunchConfigV1), including its tuple offset."""
    validate_rwa_launch_config(config)
    return abi_encode([_RWA_LAUNCH_ABI_TYPE], [(config.share_bps, config.premium_bps,
        config.max_price_age, tuple(to_checksum_address(asset) for asset in config.basket), config.weights_bps)])


def decode_rwa_launch_tail(data: bytes | str) -> RwaLaunchConfigV1:
    from .lifecycle import LifecyclePlanningError
    from .lifecycle_rpc import hex_bytes
    try:
        raw = bytes.fromhex(hex_bytes(data)[2:])
        share, premium, age, basket, weights = abi_decode([_RWA_LAUNCH_ABI_TYPE], raw)[0]
    except (ValueError, TypeError, DecodingError) as error:
        raise LifecyclePlanningError("INVALID_RWA_CONFIG", "RWA tail must be abi.encode(RwaLaunchConfigV1)") from error
    config = RwaLaunchConfigV1(share, premium, age, tuple(to_checksum_address(asset) for asset in basket), weights)
    if encode_rwa_launch_tail(config) != raw:
        raise LifecyclePlanningError("INVALID_RWA_CONFIG", "RWA tail is not canonical")
    return config


def validate_rwa_market_context(market: LifecycleMarketConfig) -> None:
    """Bind each RWA market to config6, the variant's adapter and USDG."""
    from .lifecycle import LifecyclePlanningError
    from .lifecycle_rpc import hex_bytes
    if not is_rwa_profile(market.profile_id):
        return
    adapter = RWA_FIXED_ADAPTER_ID if hex_bytes(market.profile_id) == RWA_FIXED_PROFILE_ID else RWA_DYNAMIC_ADAPTER_ID
    if market.config_version != 6 or hex_bytes(market.adapter_id) != adapter or market.quote_asset.lower() != RWA_QUOTE_ASSET.lower():
        raise LifecyclePlanningError("INVALID_RWA_CONFIG", "RWA markets require config6, their RWA adapter and a USDG quote")


def validate_rwa_launch_plan(plan: LaunchPlanV1) -> None:
    """Refuse collector-construction failures before begin; no-op without RWA markets."""
    from .lifecycle import LifecyclePlanningError, LifecycleRewardMode, decode_lifecycle_pool_bound_v4_market_config
    policies = {policy.asset.lower(): policy for policy in plan.fee_assets}
    for market in plan.markets:
        if not is_rwa_profile(market.profile_id):
            continue
        validate_rwa_market_context(market)
        config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
        if config.version != 6 or config.rwa_launch_config is None:
            raise LifecyclePlanningError("INVALID_RWA_CONFIG", "RWA markets require an RWA tail")
        if plan.token.reward_mode != LifecycleRewardMode.DIVIDENDS:
            raise LifecyclePlanningError("INVALID_RWA_CONFIG", "RWA launches require a Dividends launch token")
        for asset in config.rwa_launch_config.basket:
            policy = policies.get(asset.lower())
            if policy is None or (policy.owner_bps, policy.rewards_bps, policy.burn_bps) != (0, 10000, 0):
                raise LifecyclePlanningError("INVALID_RWA_CONFIG", "Every RWA basket stock must be a fee asset with a 0/10000/0 owner/rewards/burn policy")
