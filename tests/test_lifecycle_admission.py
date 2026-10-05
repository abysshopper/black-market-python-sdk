"""Profile admission codecs and independent creator-selected LP and hook rates."""

import json
from dataclasses import fields, replace
from pathlib import Path

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak

from black_market_sdk import (
    LaunchBoundsV2, LaunchEnvelopeV2, LaunchGraphV2, LifecyclePoolBoundV4MarketConfig,
    decode_lifecycle_v4_market_config, encode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_v4_market_config, launch_plan_from_dict, to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import (
    _profile_id, _struct_tuple, _tuple_type, _validate_v4_market,
)
from black_market_sdk.lifecycle_abis import LAUNCH_BOUNDS_COMPONENTS_V2

BOUNDS_TYPE = "(int24,int24,uint16,uint16,uint8,bool,bytes32)"
TOKEN = "0x000000000000000000000000000000000000000a"
ZERO_ADDRESS = "0x" + "00" * 20


def address(value):
    return f"0x{value:040x}"


def profile_context(version=4, *, author_cap=500):
    fixture = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"
    plan = launch_plan_from_dict(json.loads(fixture.read_text())["plan"])
    config = decode_lifecycle_v4_market_config(plan.markets[0].config)
    if version == 5:
        config = LifecyclePoolBoundV4MarketConfig(version=5, hook_salt=bytes(32), **{
            field.name: getattr(config, field.name) for field in fields(config) if field.name != "version"
        })
    bounds = LaunchBoundsV2(1, 200, 32, 4096, 3, True, config.oracle_config_id)
    graph = LaunchGraphV2(
        manager=address(100), hook_root=address(101), oracle_factory=address(102),
        locker=address(103), collector_factory=address(104), collector_deployer=address(105),
        hook_deployer=address(106), core_code_hash=(100).to_bytes(32, "big"),
        manager_code_hash=(101).to_bytes(32, "big"), hook_runtime_code_hash=(102).to_bytes(32, "big"),
        oracle_factory_code_hash=(103).to_bytes(32, "big"), locker_code_hash=(104).to_bytes(32, "big"),
        collector_factory_code_hash=(105).to_bytes(32, "big"), collector_deployer_code_hash=(106).to_bytes(32, "big"),
        hook_deployer_code_hash=(107).to_bytes(32, "big"), hook_creation_code_hash=(108).to_bytes(32, "big"),
        code_chunk0=address(107), code_chunk0_hash=(109).to_bytes(32, "big"),
        code_chunk1=ZERO_ADDRESS, code_chunk1_hash=bytes(32), shared_hook_salt=bytes(32),
    )
    encoded_bounds = abi_encode([BOUNDS_TYPE], [(1, 200, 32, 4096, 3, True, config.oracle_config_id)])
    envelope = LaunchEnvelopeV2(
        artifact_digest=(1).to_bytes(32, "big"), review_manifest_digest=(2).to_bytes(32, "big"),
        config_bounds_digest=keccak(encoded_bounds), terms_digest=config.terms_digest,
        topology=1 if version == 4 else 2, config_version=version, economic_version=3,
        capabilities=123, flags=0, callback_flags=0x1afc, callback_mask=0x3fff,
        protocol_treasury=config.treasury, protocol_fee_denominator=config.protocol_fee_denominator,
        beneficiary=config.developer_beneficiary, maximum_developer_fee_bps=author_cap, bounds=bounds, graph=graph,
    )
    profile_id = _profile_id(envelope)
    config = replace(config, profile_id=profile_id)
    encoder = encode_lifecycle_v4_market_config if version == 4 else encode_lifecycle_pool_bound_v4_market_config
    market = replace(plan.markets[0], profile_id=profile_id, config_version=version, config=encoder(config))
    return replace(plan, markets=(market, *plan.markets[1:])), config, envelope, encoder


def test_bounds_codec_and_profile_digest_use_exact_seven_field_tuple():
    _, config, envelope, _ = profile_context()
    encoded = abi_encode([_tuple_type(LAUNCH_BOUNDS_COMPONENTS_V2)], [_struct_tuple(LAUNCH_BOUNDS_COMPONENTS_V2, envelope.bounds)])
    expected = abi_encode([BOUNDS_TYPE], [(1, 200, 32, 4096, 3, True, config.oracle_config_id)])
    assert encoded == expected
    assert LaunchBoundsV2(*abi_decode([BOUNDS_TYPE], expected)[0]) == envelope.bounds
    assert keccak(encoded) == envelope.config_bounds_digest
    expected_profile = keccak(abi_encode(
        ["bytes32"] * 5 + ["uint8", "uint32", "uint32", "address", "uint16", "uint64"],
        [keccak(text="black-market.launch-profile.v2"), envelope.artifact_digest,
         envelope.review_manifest_digest, keccak(expected), envelope.terms_digest,
         envelope.topology, envelope.config_version, envelope.economic_version,
         envelope.beneficiary, envelope.maximum_developer_fee_bps, envelope.capabilities],
    ))
    assert config.profile_id == expected_profile


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("lp_rate", [0, 150000, 200000, 999999])
@pytest.mark.parametrize("hook_rate", [0, 150000, 200000, 999999])
def test_creator_lp_and_hook_rates_are_independent_with_zero_author_royalty(version, lp_rate, hook_rate):
    plan, config, envelope, encoder = profile_context(version, author_cap=0)
    config = replace(config, lp_fee_pips=lp_rate, hook_fee_pips=hook_rate, developer_fee_bps=0)
    market = replace(plan.markets[0], config=encoder(config))
    _validate_v4_market(config, market, TOKEN, envelope, 400)
    to_launch_plan_tuple(replace(plan, markets=(market, *plan.markets[1:])))
    with pytest.raises(ValueError, match="developer rate"):
        _validate_v4_market(replace(config, developer_fee_bps=1), market, TOKEN, envelope, 400)


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("fee, abi_name", [("lp_fee_pips", "lpFeePips"), ("hook_fee_pips", "hookFeePips")])
@pytest.mark.parametrize("rate", [1000000, 1500000])
def test_lp_and_hook_rates_at_or_above_one_hundred_percent_are_rejected(version, fee, abi_name, rate):
    plan, config, envelope, encoder = profile_context(version)
    config = replace(config, **{fee: rate}, developer_fee_bps=0)
    market = replace(plan.markets[0], config=encoder(config))
    with pytest.raises(ValueError, match="economics"):
        _validate_v4_market(config, market, TOKEN, envelope, 400)
    with pytest.raises(ValueError, match=abi_name):
        to_launch_plan_tuple(replace(plan, markets=(market, *plan.markets[1:])))


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("change", [
    {"developer_fee_bps": 401}, {"developer_beneficiary": address(200)},
    {"terms_digest": (99).to_bytes(32, "big")}, {"profile_id": (99).to_bytes(32, "big")},
])
def test_author_ceiling_and_frozen_terms_remain_required_at_fifteen_percent(version, change):
    plan, config, envelope, _ = profile_context(version)
    config = replace(config, hook_fee_pips=150000, developer_fee_bps=0)
    with pytest.raises(ValueError, match="developer rate"):
        _validate_v4_market(replace(config, **change), plan.markets[0], TOKEN, envelope, 400)


def test_envelope_author_ceiling_is_independent_of_protocol_ceiling():
    plan, config, envelope, _ = profile_context()
    with pytest.raises(ValueError, match="developer rate"):
        _validate_v4_market(replace(config, hook_fee_pips=150000, developer_fee_bps=251),
            plan.markets[0], TOKEN, replace(envelope, maximum_developer_fee_bps=250), 400)


@pytest.mark.parametrize("change", [
    {"fee_mode": 2}, {"fee_mode": 32},
    {"treasury": address(200)}, {"protocol_fee_denominator": 7},
])
def test_unsupported_fee_modes_and_treasury_policy_remain_rejected(change):
    plan, config, envelope, _ = profile_context()
    config = replace(config, hook_fee_pips=150000, developer_fee_bps=0)
    with pytest.raises(ValueError, match="economics"):
        _validate_v4_market(replace(config, **change), plan.markets[0], TOKEN, envelope, 400)


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("fee", ["lp_fee_pips", "hook_fee_pips"])
@pytest.mark.parametrize("rate", [-1, 1.5, True, None])
def test_lp_and_hook_rates_must_be_unsigned_integral_pip_values(version, fee, rate):
    plan, config, envelope, encoder = profile_context(version)
    invalid = replace(config, **{fee: rate})
    with pytest.raises(ValueError, match="economics"):
        _validate_v4_market(invalid, plan.markets[0], TOKEN, envelope, 400)
    with pytest.raises(ValueError):
        encoder(invalid)
