"""Current reviewed config bindings and independent Node economic wire parity."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from eth_abi.exceptions import DecodingError

from black_market_sdk import (
    LifecycleAbyssMarketConfig, LifecycleAbyssPosition, LifecycleV4MarketConfig,
    LifecycleV4Position, decode_lifecycle_v4_market_config,
    encode_lifecycle_abyss_market_config, encode_lifecycle_v4_market_config,
)

FIXTURE = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"


def shared_config():
    return LifecycleV4MarketConfig(
        version=4, lp_fee_pips=3000, tick_spacing=60, sqrt_price_x96=2**96,
        hook_fee_pips=10000, fee_mode=0, protocol_fee_denominator=6,
        treasury="0x00000000000000000000000000000000000000a0",
        external_liquidity_disabled=True,
        oracle_config_id=bytes.fromhex("c0e9bed88d70a13fd3ab31451fefdd073b7266e838aee0ad1c236c8c9eff855d"),
        profile_id=(11).to_bytes(32, "big"), terms_digest=(21).to_bytes(32, "big"),
        developer_beneficiary="0x00000000000000000000000000000000000000d0",
        developer_fee_bps=250,
        positions=(LifecycleV4Position(60, 120, 10**18, (1).to_bytes(32, "big"), 10**24),),
    )


def test_shared_current_bytes_match_independent_wire_vector():
    expected = json.loads(FIXTURE.read_text())["plan"]["markets"][0]["config"]
    assert "0x" + encode_lifecycle_v4_market_config(shared_config()).hex() == expected


@pytest.mark.parametrize("version", [0, 1, 2, 3, 5])
def test_shared_encoder_rejects_every_noncurrent_version(version):
    with pytest.raises(ValueError):
        encode_lifecycle_v4_market_config(replace(shared_config(), version=version))


@pytest.mark.parametrize("change", [
    {"profile_id": bytes(32)}, {"terms_digest": bytes(32)},
    {"developer_beneficiary": "0x" + "00" * 20}, {"developer_fee_bps": None},
    {"developer_fee_bps": True}, {"developer_fee_bps": 10000},
])
def test_creator_must_supply_exact_nonzero_author_terms_and_explicit_valid_rate(change):
    with pytest.raises(ValueError):
        encode_lifecycle_v4_market_config(replace(shared_config(), **change))


def test_current_decoders_reject_trailing_bytes_and_old_tuple_layout():
    encoded = encode_lifecycle_v4_market_config(shared_config())
    with pytest.raises((ValueError, DecodingError)):
        decode_lifecycle_v4_market_config(encoded + bytes(32))
    # A retired V2 layout had 11 fields; this is not a reviewed 15-field tuple.
    old = bytes.fromhex("00" * 31 + "20" + "00" * 31 + "02") + bytes(10 * 32)
    with pytest.raises((ValueError, DecodingError)):
        decode_lifecycle_v4_market_config(old)


def test_abyss_keeps_its_independent_profile_oracle_and_large_budget_wire():
    config = LifecycleAbyssMarketConfig(3, 3000, (0x55).to_bytes(32, "big"), 2**96,
        (LifecycleAbyssPosition(60, 120, 10**18, 10**24),))
    expected = json.loads(FIXTURE.read_text())["plan"]["markets"][1]["config"]
    assert "0x" + encode_lifecycle_abyss_market_config(config).hex() == expected
