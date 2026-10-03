"""Public market builders must reproduce the independent economic wire vector."""

import json
from pathlib import Path
from dataclasses import replace

import pytest

from black_market_sdk import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    LifecycleAbyssMarketConfig,
    LifecycleAbyssPosition,
    LifecycleV4MarketConfig,
    LifecycleV4Position,
    encode_lifecycle_abyss_market_config,
    encode_lifecycle_v4_market_config,
)


FIXTURE = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"


def test_v4_public_config_builder_preserves_all_committed_economics():
    config = LifecycleV4MarketConfig(
        version=2,
        lp_fee_pips=3000,
        tick_spacing=60,
        sqrt_price_x96=2**96,
        hook_fee_pips=10000,
        fee_mode=0,
        protocol_fee_denominator=6,
        treasury="0x00000000000000000000000000000000000000a0",
        external_liquidity_disabled=True,
        oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
        positions=(LifecycleV4Position(60, 120, 10**18, (1).to_bytes(32, "big"), 10**24),),
    )
    expected = json.loads(FIXTURE.read_text())["plan"]["markets"][0]["config"]
    assert "0x" + encode_lifecycle_v4_market_config(config).hex() == expected
    for version in (0, 1, 3):
        with pytest.raises(ValueError):
            encode_lifecycle_v4_market_config(replace(config, version=version))


def test_abyss_public_config_builder_preserves_profile_oracle_and_large_position_budgets():
    config = LifecycleAbyssMarketConfig(
        profile=3,
        fee=3000,
        oracle_config_id=(0x55).to_bytes(32, "big"),
        opening_sqrt_price_x96=2**96,
        positions=(LifecycleAbyssPosition(60, 120, 10**18, 10**24),),
    )
    expected = json.loads(FIXTURE.read_text())["plan"]["markets"][1]["config"]
    assert "0x" + encode_lifecycle_abyss_market_config(config).hex() == expected
