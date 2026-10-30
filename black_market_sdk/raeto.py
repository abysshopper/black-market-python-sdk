"""Deployed Raeto collector settings for ordinary pool-bound lifecycle launches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError

if TYPE_CHECKING:
    from .lifecycle import LaunchPlanV1

RAETO_FIXED_PROFILE_ID = "0x9161833b162e7ca8160b11c64aedb307561e19a9136fd32f99cd797aab6da3ca"
RAETO_DYNAMIC_PROFILE_ID = "0x08387d00ee4f75f860810cc8fb434087328592c2f39ea3321109e589039f80e1"
RAETO_FIXED_ADAPTER_ID = "0x911d7ecefe56489d329235e3484829717dc00400d4181902f0f2de45f9338615"
RAETO_DYNAMIC_ADAPTER_ID = "0x8ab9c8c9b8fd4c8bbf12b780d9474463fa0fa52e3b450f0d5f68fd067dc1674d"
RAETO_PAYOUT_REGISTRY_ADDRESS = "0xBEC2C0e91612090F82df6743F31CDF18B4386b50"
# The deployed collectors accept this menu, not the wider range in later source.
RAETO_SHARE_BPS_OPTIONS = (500, 1000, 2500, 5000, 7500)
RAETO_QUOTE_ASSETS = (
    "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
    "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
)


@dataclass(frozen=True)
class RaetoLaunchConfigV1:
    share_bps: int


def is_raeto_profile(profile_id: bytes | str) -> bool:
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id.lower()
    return identity in (RAETO_FIXED_PROFILE_ID, RAETO_DYNAMIC_PROFILE_ID)


def validate_raeto_launch_config(config: RaetoLaunchConfigV1) -> None:
    from .lifecycle import LifecyclePlanningError
    share = config.share_bps
    if isinstance(share, bool) or not isinstance(share, int) or share not in RAETO_SHARE_BPS_OPTIONS:
        raise LifecyclePlanningError("INVALID_RAETO_CONFIG", "Raeto share_bps must be one of 500, 1000, 2500, 5000 or 7500")


def encode_raeto_launch_tail(config: RaetoLaunchConfigV1) -> bytes:
    """Return exactly abi.encode(uint16 shareBps), one static ABI word."""
    validate_raeto_launch_config(config)
    return abi_encode(["uint16"], [config.share_bps])


def decode_raeto_launch_tail(data: bytes | str) -> RaetoLaunchConfigV1:
    from .lifecycle import LifecyclePlanningError
    from .lifecycle_rpc import hex_bytes
    try:
        raw = bytes.fromhex(hex_bytes(data)[2:])
    except (ValueError, TypeError) as error:
        raise LifecyclePlanningError("INVALID_RAETO_CONFIG", "Raeto tail must be exactly one ABI word") from error
    if len(raw) != 32:
        raise LifecyclePlanningError("INVALID_RAETO_CONFIG", "Raeto tail must be exactly one ABI word")
    share = int.from_bytes(raw, "big")
    if share > 65535:
        raise LifecyclePlanningError("INVALID_RAETO_CONFIG", "Raeto tail must contain a canonical uint16")
    config = RaetoLaunchConfigV1(share)
    validate_raeto_launch_config(config)
    return config


def validate_raeto_launch_plan(plan: LaunchPlanV1) -> None:
    """Validate every Raeto market offline; use the ordinary creator/funding route."""
    from .lifecycle import LifecyclePlanningError, decode_lifecycle_pool_bound_v4_market_config
    from .lifecycle_rpc import hex_bytes
    try:
        if not plan.markets:
            raise ValueError("A Raeto plan requires at least one market")
        for market in plan.markets:
            identity = hex_bytes(market.profile_id)
            adapter = RAETO_FIXED_ADAPTER_ID if identity == RAETO_FIXED_PROFILE_ID else RAETO_DYNAMIC_ADAPTER_ID
            if (not is_raeto_profile(identity) or market.config_version != 6
                    or not any(market.quote_asset.lower() == quote.lower() for quote in RAETO_QUOTE_ASSETS)
                    or hex_bytes(market.adapter_id) != adapter):
                raise ValueError("Every Raeto market requires its Raeto config6 profile/adapter and a WETH or USDG quote")
            config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
            if hex_bytes(config.profile_id) != identity:
                raise ValueError("Raeto config profile differs from its market")
    except (ValueError, TypeError, AttributeError, DecodingError) as error:
        raise LifecyclePlanningError("INVALID_RAETO_PLAN", str(error)) from error
