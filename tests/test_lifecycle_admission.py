"""Reviewed profile economics, independent creator rates and pinned oracle reads."""

import json
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak

from black_market_sdk.lifecycle import (
    LaunchBoundsV2, LaunchEnvelopeV2, LaunchGraphV2, LifecycleAdapterRegistration,
    LifecycleDeveloperTerms, LifecycleHookTopology, LifecyclePoolBoundV4MarketConfigV5,
    LifecyclePoolBoundV4MarketConfigV6, LifecycleProfileMetadata, LifecycleProfileRegistration,
    ProfileTopologyV1, V4_LIFECYCLE_CONFIG_SCHEMA, _admit_market_config, _profile_id,
    _profile_metadata, _struct_tuple, _tuple_type, decode_lifecycle_v4_market_config,
    encode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_v4_market_config,
    launch_plan_from_dict, pool_bound_v4_lifecycle_config_schema,
    validate_v4_lifecycle_market, validate_v4_lifecycle_oracle,
)
from black_market_sdk.lifecycle_abis import LAUNCH_BOUNDS_COMPONENTS_V2, LAUNCH_ENVELOPE_COMPONENTS_V2
from black_market_sdk.lifecycle_rpc import LaunchBlock

BOUNDS_TYPE = "(int24,int24,uint16,uint16,uint8)"
TOKEN = "0x000000000000000000000000000000000000000a"
ZERO_ADDRESS = "0x" + "00" * 20
ORACLE_BLOCK = LaunchBlock(42, "0x" + "2a" * 32, 100, 30_000_000, None)


def address(value):
    return f"0x{value:040x}"


def profile_context(version=4, *, author_cap=500):
    fixture = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"
    plan = launch_plan_from_dict(json.loads(fixture.read_text())["plan"])
    config = decode_lifecycle_v4_market_config(plan.markets[0].config)
    if version != 4:
        arguments = {field.name: getattr(config, field.name) for field in fields(config) if field.name != "version"}
        if version == 5:
            config = LifecyclePoolBoundV4MarketConfigV5(version=5, hook_salt=bytes(32), **arguments)
        else:
            config = LifecyclePoolBoundV4MarketConfigV6(version=6, hook_salt=bytes(32),
                minimum_hook_fee_pips=0, fee_sensitivity_pips_seconds_per_tick=0, **arguments)
    bounds = LaunchBoundsV2(1, 200, 32, 4096, 3)
    graph = LaunchGraphV2(
        manager=address(100), hook_root=address(101) if version == 4 else ZERO_ADDRESS,
        oracle_factory=address(102), locker=address(103), collector_factory=address(104),
        collector_deployer=address(105), hook_deployer=address(106),
        core_code_hash=(100).to_bytes(32, "big"), manager_code_hash=(101).to_bytes(32, "big"),
        hook_runtime_code_hash=(102).to_bytes(32, "big") if version == 4 else bytes(32),
        oracle_factory_code_hash=(103).to_bytes(32, "big"), locker_code_hash=(104).to_bytes(32, "big"),
        collector_factory_code_hash=(105).to_bytes(32, "big"), collector_deployer_code_hash=(106).to_bytes(32, "big"),
        hook_deployer_code_hash=(107).to_bytes(32, "big"), hook_creation_code_hash=(108).to_bytes(32, "big"),
        code_chunk0=address(107), code_chunk0_hash=(109).to_bytes(32, "big"),
        code_chunk1=ZERO_ADDRESS, code_chunk1_hash=bytes(32), shared_hook_salt=bytes(32),
    )
    envelope = LaunchEnvelopeV2(
        artifact_digest=(1).to_bytes(32, "big"), review_manifest_digest=(2).to_bytes(32, "big"),
        config_bounds_digest=keccak(abi_encode([BOUNDS_TYPE], [(1, 200, 32, 4096, 3)])),
        terms_digest=config.terms_digest, topology=1 if version == 4 else 2,
        config_version=version, economic_version=3, capabilities=123, flags=0,
        callback_flags=0x1afc, callback_mask=0x3fff, protocol_treasury=config.treasury,
        protocol_fee_denominator=config.protocol_fee_denominator,
        beneficiary=config.developer_beneficiary, maximum_developer_fee_bps=author_cap,
        bounds=bounds, graph=graph,
    )
    profile_id = _profile_id(envelope)
    config = replace(config, profile_id=profile_id)
    encoder = encode_lifecycle_v4_market_config if version == 4 else encode_lifecycle_pool_bound_v4_market_config
    market = replace(plan.markets[0], profile_id=profile_id, config_version=version, config=encoder(config))
    schema = V4_LIFECYCLE_CONFIG_SCHEMA if version == 4 else pool_bound_v4_lifecycle_config_schema(version)
    profile = LifecycleProfileMetadata(
        "0x" + profile_id.hex(),
        LifecycleProfileRegistration("0x" + bytes.fromhex(market.adapter_id[2:]).hex(),
            "0x" + schema.hex(), "0x" + "11" * 32, graph.manager, ZERO_ADDRESS,
            graph.hook_root, envelope.capabilities, True),
        LifecycleAdapterRegistration(address(1), "0x" + "22" * 32, envelope.capabilities, version, True),
        ProfileTopologyV1(LifecycleHookTopology(envelope.topology), version, graph.hook_deployer,
            "0x" + graph.hook_creation_code_hash.hex()),
        "uniswap-v4", envelope,
        LifecycleDeveloperTerms(address(1), envelope.beneficiary, author_cap, envelope.terms_digest, True),
        400,
    )
    return market, config, profile, encoder


def validate(market, config, profile):
    validate_v4_lifecycle_market(market=market, config=config, profile=profile, token=TOKEN)


def test_bounds_codec_and_profile_digest_use_exact_five_field_tuple():
    _, config, profile, _ = profile_context()
    envelope = profile.envelope
    encoded = abi_encode([_tuple_type(LAUNCH_BOUNDS_COMPONENTS_V2)], [_struct_tuple(LAUNCH_BOUNDS_COMPONENTS_V2, envelope.bounds)])
    expected = abi_encode([BOUNDS_TYPE], [(1, 200, 32, 4096, 3)])
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


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("lp_rate", [0, 150000, 200000, 999999])
@pytest.mark.parametrize("hook_rate", [0, 150000, 200000, 999999])
def test_creator_lp_and_hook_rates_are_independent_with_zero_author_royalty(version, lp_rate, hook_rate):
    market, config, profile, encoder = profile_context(version, author_cap=0)
    config = replace(config, lp_fee_pips=lp_rate, hook_fee_pips=hook_rate, developer_fee_bps=0)
    market = replace(market, config=encoder(config))
    validate(market, config, profile)
    with pytest.raises(ValueError) as failure:
        validate(market, replace(config, developer_fee_bps=1), profile)
    assert failure.value.code == "DEVELOPER_FEE_CEILING"


def test_v6_hook_fee_can_reach_one_hundred_percent_without_changing_lp_ceiling():
    market, config, profile, encoder = profile_context(6)
    selected = replace(config, hook_fee_pips=1000000, minimum_hook_fee_pips=1000000,
        fee_sensitivity_pips_seconds_per_tick=2**32 - 1)
    validate(replace(market, config=encoder(selected)), selected, profile)


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("fee", ["lp_fee_pips", "hook_fee_pips"])
@pytest.mark.parametrize("rate", [-1, 1500000, 1.5, True, None])
def test_lp_and_hook_rates_reject_invalid_pip_values(version, fee, rate):
    market, config, profile, encoder = profile_context(version)
    selected = replace(config, **{fee: rate})
    with pytest.raises(ValueError):
        validate(market, selected, profile)


@pytest.mark.parametrize("version,fee,rate", [(4, "lp_fee_pips", 1000000),
    (4, "hook_fee_pips", 1000000), (5, "lp_fee_pips", 1000000),
    (5, "hook_fee_pips", 1000000), (6, "lp_fee_pips", 1000000), (6, "hook_fee_pips", 1000001)])
def test_lp_and_hook_fee_boundaries_are_version_specific(version, fee, rate):
    market, config, profile, _ = profile_context(version)
    with pytest.raises(ValueError):
        validate(market, replace(config, **{fee: rate}), profile)


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("change,code", [
    ({"developer_fee_bps": 401}, "DEVELOPER_FEE_CEILING"),
    ({"developer_beneficiary": address(200)}, "PROFILE_TERMS_MISMATCH"),
    ({"terms_digest": (99).to_bytes(32, "big")}, "PROFILE_TERMS_MISMATCH"),
    ({"profile_id": (99).to_bytes(32, "big")}, "PROFILE_TERMS_MISMATCH"),
])
def test_author_ceiling_and_frozen_terms_remain_required_with_zero_royalty(version, change, code):
    market, config, profile, _ = profile_context(version)
    config = replace(config, hook_fee_pips=150000, developer_fee_bps=0)
    with pytest.raises(ValueError) as failure:
        validate(market, replace(config, **change), profile)
    assert failure.value.code == code


def test_envelope_author_ceiling_is_independent_of_protocol_ceiling():
    market, config, profile, _ = profile_context(author_cap=250)
    with pytest.raises(ValueError) as failure:
        validate(market, replace(config, developer_fee_bps=251), profile)
    assert failure.value.code == "DEVELOPER_FEE_CEILING"
    validate(market, replace(config, developer_fee_bps=250), profile)


@pytest.mark.parametrize("change", [{"fee_mode": 2}, {"fee_mode": 32},
    {"treasury": address(200)}, {"protocol_fee_denominator": 7}])
def test_unsupported_fee_modes_and_treasury_policy_remain_rejected(change):
    market, config, profile, _ = profile_context()
    with pytest.raises(ValueError) as failure:
        validate(market, replace(config, **change), profile)
    assert failure.value.code == "PROFILE_BOUNDS_MISMATCH"


@pytest.mark.parametrize("change", [
    lambda p: replace(p, id="0x" + "99" * 32),
    lambda p: replace(p, registration=replace(p.registration, adapter_id="0x" + "99" * 32)),
    lambda p: replace(p, registration=replace(p.registration, config_schema="0x" + "99" * 32)),
    lambda p: replace(p, adapter=replace(p.adapter, implementation=address(200))),
    lambda p: replace(p, adapter=replace(p.adapter, config_version=6)),
    lambda p: replace(p, topology=replace(p.topology, hook_topology=LifecycleHookTopology.POOL_BOUND_V4)),
    lambda p: replace(p, envelope=replace(p.envelope, economic_version=4)),
    lambda p: replace(p, developer_terms=replace(p.developer_terms, enabled=False)),
    lambda p: replace(p, developer_terms=replace(p.developer_terms, terms_digest=bytes(32))),
    lambda p: replace(p, developer_terms=None),
    lambda p: replace(p, protocol_maximum_developer_fee_bps=None),
])
def test_construction_validation_requires_complete_matching_profile_metadata(change):
    market, config, profile, _ = profile_context()
    with pytest.raises(ValueError) as failure:
        validate(market, config, change(profile))
    assert failure.value.code == "PROFILE_TERMS_MISMATCH"


def oracle_id(move, cardinality=4096):
    return keccak(abi_encode(["uint24", "uint16"], [move, cardinality]))


def oracle_client(envelope, configurations):
    def request(method, params):
        assert method == "eth_call"
        transaction, block_tag = params
        assert transaction["to"].lower() == envelope.graph.oracle_factory.lower()
        assert block_tag == ORACLE_BLOCK.tag
        calldata = bytes.fromhex(transaction["data"][2:])
        assert calldata[:4] == keccak(text="oracleConfigs(bytes32)")[:4]
        selected_id = abi_decode(["bytes32"], calldata[4:])[0]
        return {"result": "0x" + abi_encode(["uint24", "uint16"], configurations.get(selected_id, (0, 0))).hex()}
    return SimpleNamespace(provider=SimpleNamespace(make_request=request))


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("move", [1, 6, 17])
@pytest.mark.parametrize("external_liquidity_disabled", [False, True])
@pytest.mark.parametrize("fee_mode", [0, 1])
def test_same_profile_accepts_registered_oracles_and_either_liquidity_choice(version, move, external_liquidity_disabled, fee_mode):
    market, config, profile, encoder = profile_context(version)
    selected = replace(config, oracle_config_id=oracle_id(move), external_liquidity_disabled=external_liquidity_disabled,
        fee_mode=fee_mode, lp_fee_pips=150000, hook_fee_pips=200000)
    market = replace(market, config=encoder(selected))
    client = oracle_client(profile.envelope, {oracle_id(value): (value, 4096) for value in (1, 6, 17)})
    validate(market, selected, profile)
    validate_v4_lifecycle_oracle(client, envelope=profile.envelope, oracle_config_id=selected.oracle_config_id, block=ORACLE_BLOCK)
    assert market.profile_id == config.profile_id


@pytest.mark.parametrize("move,cardinality", [(0, 0), (887273, 4096), (1, 1), (1, 4097)])
def test_selected_oracle_rejects_unregistered_or_out_of_bounds_parameters(move, cardinality):
    _, _, profile, _ = profile_context()
    selected_id = oracle_id(move, cardinality)
    client = oracle_client(profile.envelope, {selected_id: (move, cardinality)})
    with pytest.raises(ValueError) as failure:
        validate_v4_lifecycle_oracle(client, envelope=profile.envelope, oracle_config_id=selected_id, block=ORACLE_BLOCK)
    assert failure.value.code == "INVALID_ORACLE_CONFIG"


def test_selected_oracle_obeys_profile_cardinality_ceiling_and_nonzero_id():
    market, config, profile, _ = profile_context()
    client = oracle_client(profile.envelope, {oracle_id(1): (1, 4096)})
    restricted = replace(profile.envelope, bounds=replace(profile.envelope.bounds, maximum_oracle_cardinality=2048))
    for envelope, selected_id in [(restricted, oracle_id(1)), (profile.envelope, oracle_id(99)), (profile.envelope, bytes(32))]:
        with pytest.raises(ValueError) as failure:
            validate_v4_lifecycle_oracle(client, envelope=envelope, oracle_config_id=selected_id, block=ORACLE_BLOCK)
        assert failure.value.code == "INVALID_ORACLE_CONFIG"
    with pytest.raises(ValueError) as failure:
        validate(market, replace(config, oracle_config_id=bytes(32)), profile)
    assert failure.value.code == "INVALID_ORACLE_CONFIG"


@pytest.mark.parametrize("version", [4, 5, 6])
def test_market_admission_checks_selected_oracle_before_further_admission(monkeypatch, version):
    import black_market_sdk.lifecycle as module
    market, config, metadata, encoder = profile_context(version)
    market = replace(market, config=encoder(replace(config, oracle_config_id=oracle_id(99))))
    monkeypatch.setattr(module, "_profile_metadata", lambda *args: (metadata.envelope, metadata.developer_terms))
    calls = []
    def call(client, target, abi, name, args, block):
        calls.append(name)
        assert block == ORACLE_BLOCK
        if name == "protocolMaximumDeveloperFeeBps":
            return 400
        assert name == "oracleConfigs"
        assert target.lower() == metadata.envelope.graph.oracle_factory.lower()
        assert args == [oracle_id(99)]
        return (0, 0)
    monkeypatch.setattr(module, "_call", call)
    adapter = tuple(getattr(metadata.adapter, field.name) for field in fields(metadata.adapter))
    registration = tuple(getattr(metadata.registration, field.name) for field in fields(metadata.registration))
    registration = (*[bytes.fromhex(value[2:]) for value in registration[:3]], *registration[3:])
    with pytest.raises(ValueError) as failure:
        _admit_market_config(object(), address(200), market, TOKEN, adapter, registration, metadata.topology, ORACLE_BLOCK, 0)
    assert failure.value.code == "INVALID_ORACLE_CONFIG"
    assert calls == ["protocolMaximumDeveloperFeeBps", "oracleConfigs"]


@pytest.mark.parametrize("version", [4, 5, 6])
def test_full_profile_envelope_readback_decodes_five_member_bounds(version):
    _, config, profile, _ = profile_context(version)
    envelope, terms = profile.envelope, profile.developer_terms
    registry = address(200)
    encoded_envelope = abi_encode([_tuple_type(LAUNCH_ENVELOPE_COMPONENTS_V2)], [_struct_tuple(LAUNCH_ENVELOPE_COMPONENTS_V2, envelope)])
    encoded_terms = abi_encode(["address", "address", "uint16", "bytes32", "bool"],
        [terms.adapter, terms.beneficiary, terms.maximum_developer_fee_bps, terms.terms_digest, terms.enabled])
    selectors = {keccak(text="profileEnvelope(bytes32)")[:4]: encoded_envelope,
        keccak(text="developerTerms(bytes32)")[:4]: encoded_terms}
    def request(method, params):
        assert method == "eth_call"
        transaction, block_tag = params
        assert transaction["to"].lower() == registry.lower() and block_tag == ORACLE_BLOCK.tag
        calldata = bytes.fromhex(transaction["data"][2:])
        assert abi_decode(["bytes32"], calldata[4:])[0] == config.profile_id
        return {"result": "0x" + selectors[calldata[:4]].hex()}
    client = SimpleNamespace(provider=SimpleNamespace(make_request=request))
    actual_envelope, actual_terms = _profile_metadata(client, registry, config.profile_id, ORACLE_BLOCK)
    assert actual_envelope == envelope
    assert actual_terms == terms
    assert len(fields(actual_envelope.bounds)) == 5
