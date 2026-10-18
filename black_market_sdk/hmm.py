"""Exact HMM V2 launch-tail ABI, Solidity factory guards, and calibrated defaults."""
from __future__ import annotations

from dataclasses import dataclass, fields

from eth_abi import decode as abi_decode, encode as abi_encode

from .auction import AUCTION_QUOTE_OPTIONS

HMM_FIXED_PROFILE_ID = "0x45e957bcc7068350a65f5b4de5453b674d54d0f5eb2a88fc839da13197e80e98"
HMM_DYNAMIC_PROFILE_ID = "0x8e500f203ace505072e209c5f077e11de6d071ac7f298cd75c0e05c6a55c36e6"
HMM_LAUNCH_TAIL_LENGTH = 512
HMM_LAUNCH_ABI_TYPE = "(uint16,(int24,int24,int24,uint32,int24,uint256,uint256,uint256,uint16,uint8,uint256,uint256,uint32,uint256,int24))"


@dataclass(frozen=True)
class HmmStrategyConfig:
    bid_width: int
    ask_width: int
    gap: int
    stale_after_seconds: int
    stale_displacement: int
    min_quote_deploy: int
    min_quote_harvest: int
    min_token_harvest: int
    resale_premium_bps: int
    max_asks: int
    max_ask_token_exposure: int
    max_acquired_inventory: int
    premium_decay_seconds: int
    max_bid_quote: int
    reference_deviation: int


@dataclass(frozen=True)
class HmmLaunchConfig:
    strategy_share_bps: int
    strategy: HmmStrategyConfig


def is_hmm_profile(profile_id: bytes | str) -> bool:
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id.lower()
    return identity in (HMM_FIXED_PROFILE_ID, HMM_DYNAMIC_PROFILE_ID)


def _integer(value: int, minimum: int, maximum: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")


def validate_hmm_launch_config(config: HmmLaunchConfig) -> None:
    """Mirror HMMFeeCollectorFactoryV2._decodeHMM, including ABI field widths."""
    _integer(config.strategy_share_bps, 0, 10_000, "strategy_share_bps")
    strategy = config.strategy
    for name in ("bid_width", "ask_width", "stale_displacement"):
        _integer(getattr(strategy, name), 1, (1 << 23) - 1, name)
    _integer(strategy.gap, 0, (1 << 23) - 1, "gap")
    _integer(strategy.stale_after_seconds, 1, (1 << 32) - 1, "stale_after_seconds")
    _integer(strategy.premium_decay_seconds, 0, (1 << 32) - 1, "premium_decay_seconds")
    _integer(strategy.min_quote_deploy, 0, (1 << 256) - 1, "min_quote_deploy")
    for name in ("min_quote_harvest", "min_token_harvest"):
        _integer(getattr(strategy, name), 1, (1 << 256) - 1, name)
    _integer(strategy.resale_premium_bps, 0, 5_000, "resale_premium_bps")
    _integer(strategy.max_asks, 1, 8, "max_asks")
    _integer(strategy.max_ask_token_exposure, max(strategy.max_asks, strategy.min_token_harvest),
             (1 << 128) - 1, "max_ask_token_exposure")
    _integer(strategy.max_acquired_inventory, strategy.min_token_harvest,
             (1 << 128) - 1, "max_acquired_inventory")
    _integer(strategy.max_bid_quote, 1, (1 << 128) - 1, "max_bid_quote")
    _integer(strategy.reference_deviation, 0, (1 << 23) - 1, "reference_deviation")


def encode_hmm_launch_tail(config: HmmLaunchConfig) -> bytes:
    """Return abi.encode(HMMLaunchConfigV2), exactly sixteen ABI words."""
    validate_hmm_launch_config(config)
    strategy = tuple(getattr(config.strategy, field.name) for field in fields(HmmStrategyConfig))
    return abi_encode([HMM_LAUNCH_ABI_TYPE], [(config.strategy_share_bps, strategy)])


def decode_hmm_launch_tail(data: bytes) -> HmmLaunchConfig:
    if len(data) != HMM_LAUNCH_TAIL_LENGTH:
        raise ValueError("HMM launch tail must be exactly 512 bytes")
    share, strategy = abi_decode([HMM_LAUNCH_ABI_TYPE], data)[0]
    config = HmmLaunchConfig(share, HmmStrategyConfig(*strategy))
    if encode_hmm_launch_tail(config) != data:
        raise ValueError("HMM launch tail is not canonical")
    return config


def default_hmm_launch_config(*, token_budget: int, quote_asset: str, max_bid_quote: int,
                              reference_deviation: int, quote_decimals: int | None = None,
                              min_quote_deploy: int | None = None,
                              min_quote_harvest: int | None = None) -> HmmLaunchConfig:
    """Calibrate raw amounts to the market budget; unknown quotes need explicit thresholds.

    Bid quote exposure and reference deviation require explicit caller policy.
    Defaults do not clamp small budgets or uint128 caps: invalid calibrations fail.
    WETH thresholds are 0.0005/0.005 ether; USD-pegged quotes use 1/10 units.
    """
    _integer(token_budget, 1, (1 << 256) - 1, "token_budget")
    quote = next((entry for entry in AUCTION_QUOTE_OPTIONS
                  if entry.id.lower() == quote_asset.lower() or entry.address.lower() == quote_asset.lower()), None)
    decimals = quote_decimals if quote_decimals is not None else quote.decimals if quote else None
    if decimals is not None:
        _integer(decimals, 0, 255, "quote_decimals")
    if min_quote_deploy is None or min_quote_harvest is None:
        if quote is None or decimals is None or not (quote.id == "WETH" or quote.usd_peg):
            raise ValueError("Quote has no HMM calibration; supply min_quote_deploy and min_quote_harvest")
        if quote.id == "WETH":
            if decimals < 4:
                raise ValueError("WETH decimals cannot represent the HMM quote thresholds")
            deploy, harvest = 5 * 10 ** (decimals - 4), 5 * 10 ** (decimals - 3)
        else:
            deploy, harvest = 10**decimals, 10 * 10**decimals
        min_quote_deploy = deploy if min_quote_deploy is None else min_quote_deploy
        min_quote_harvest = harvest if min_quote_harvest is None else min_quote_harvest
    config = HmmLaunchConfig(5_000, HmmStrategyConfig(
        5, 5, 1, 86_400, 5, min_quote_deploy, min_quote_harvest, token_budget // 100_000,
        500, 4, token_budget // 10, token_budget, 0, max_bid_quote, reference_deviation,
    ))
    validate_hmm_launch_config(config)
    return config
