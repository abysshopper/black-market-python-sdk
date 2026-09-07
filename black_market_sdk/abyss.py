"""Abyss DEX primitives: pool profiles, token kinds, fee tiers, and ABIs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .abyss_abis import (
    ABYSS_FACTORY_ABI,
    ABYSS_FIXED_SUPPLY_TOKEN_ABI,
    ABYSS_LAUNCH_COORDINATOR_ABI,
    ABYSS_POOL_ABI,
    ABYSS_POSITION_LOCKER_ABI,
    ABYSS_POSITION_MANAGER_ABI,
    ABYSS_ROUTER_ABI,
    ATOMIC_INITIAL_BUY_COMPONENTS,
    ATOMIC_LAUNCH_FACTORY_ABI,
    ATOMIC_LAUNCH_RECEIPT_COMPONENTS,
    ATOMIC_LAUNCH_REQUEST_COMPONENTS,
    ATOMIC_POOL_CONFIG_COMPONENTS,
    ATOMIC_TOKEN_CONFIG_COMPONENTS,
    BURNABLE_FIXED_SUPPLY_TOKEN_ABI,
    DISPOSITION_COMPONENTS,
    HOLDER_DIVIDEND_TOKEN_ABI,
    HOLDER_DIVIDEND_TRACKER_ABI,
    LAUNCH_FEE_OWNER_REGISTRY_ABI,
    LAUNCH_FEE_SPLITTER_ABI,
    LAUNCH_TEMPLATE_COMPONENTS,
    LAUNCH_TEMPLATE_REGISTRY_ABI,
    LAUNCH_TOKEN_BURN_SINK_ABI,
    LAUNCH_TOKEN_FACTORY_ABI,
    LOCKED_FEE_CLAIMER_ABI,
    STAKING_REWARD_VAULT_ABI,
)

__all__ = [
    "ABYSS_FACTORY_ABI",
    "ABYSS_FIXED_SUPPLY_TOKEN_ABI",
    "ABYSS_LAUNCH_COORDINATOR_ABI",
    "ABYSS_POOL_ABI",
    "ABYSS_POSITION_LOCKER_ABI",
    "ABYSS_POSITION_MANAGER_ABI",
    "ABYSS_ROUTER_ABI",
    "ATOMIC_INITIAL_BUY_COMPONENTS",
    "ATOMIC_LAUNCH_FACTORY_ABI",
    "ATOMIC_LAUNCH_RECEIPT_COMPONENTS",
    "ATOMIC_LAUNCH_REQUEST_COMPONENTS",
    "ATOMIC_POOL_CONFIG_COMPONENTS",
    "ATOMIC_TOKEN_CONFIG_COMPONENTS",
    "BURNABLE_FIXED_SUPPLY_TOKEN_ABI",
    "DISPOSITION_COMPONENTS",
    "HOLDER_DIVIDEND_TOKEN_ABI",
    "HOLDER_DIVIDEND_TRACKER_ABI",
    "LAUNCH_FEE_OWNER_REGISTRY_ABI",
    "LAUNCH_FEE_SPLITTER_ABI",
    "LAUNCH_TEMPLATE_COMPONENTS",
    "LAUNCH_TEMPLATE_REGISTRY_ABI",
    "LAUNCH_TOKEN_BURN_SINK_ABI",
    "LAUNCH_TOKEN_FACTORY_ABI",
    "LOCKED_FEE_CLAIMER_ABI",
    "STAKING_REWARD_VAULT_ABI",
    "AbyssPoolProfile",
    "TokenKind",
    "AbyssPoolKey",
    "AbyssFeeTier",
    "ABYSS_FEE_TIERS",
]


class AbyssPoolProfile(IntEnum):
    STANDARD = 0
    STANDARD_ORACLE = 1
    QUOTE = 2
    QUOTE_ORACLE = 3


class TokenKind(IntEnum):
    BURNABLE = 0
    HOLDER_DIVIDEND = 1


@dataclass(frozen=True)
class AbyssPoolKey:
    token0: str
    token1: str
    profile: AbyssPoolProfile
    fee: int
    quote_is_token0: bool
    oracle_config_id: bytes


@dataclass(frozen=True)
class AbyssFeeTier:
    fee_pips: int
    tick_spacing: int
    label: str


ABYSS_FEE_TIERS: tuple[AbyssFeeTier, ...] = (
    AbyssFeeTier(fee_pips=500, tick_spacing=10, label="0.05%"),
    AbyssFeeTier(fee_pips=3_000, tick_spacing=60, label="0.30%"),
    AbyssFeeTier(fee_pips=10_000, tick_spacing=200, label="1%"),
    AbyssFeeTier(fee_pips=20_000, tick_spacing=400, label="2%"),
    AbyssFeeTier(fee_pips=50_000, tick_spacing=1_000, label="5%"),
    AbyssFeeTier(fee_pips=100_000, tick_spacing=2_000, label="10%"),
    AbyssFeeTier(fee_pips=150_000, tick_spacing=3_000, label="15%"),
)
