"""Pinned auto-compound offerings, canonical tails, constructor commitments and live planning."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak
from web3 import Web3

from black_market_sdk import (
    AUTO_COMPOUND_CONFIG_VERSION, AUTO_COMPOUND_STATIC_V1_PROFILE_ID,
    AUTO_COMPOUND_DYNAMIC_V1_PROFILE_ID, AutoCompoundConfigV1,
    KnownLifecycleProfile, LaunchPlanV1, LifecycleFeeAssetPolicy, LifecycleMarketConfig,
    LifecyclePoolBoundV4MarketConfigV6, LifecycleTokenConfig, LifecycleV4Position,
    LifecycleInitialBuy, ROBINHOOD_WETH, POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI,
    V4_MARKET_CONFIG_COMPONENTS_V6, compute_lifecycle_opening_buys,
    ADAPTER_REGISTRATION_COMPONENTS_V1, PROFILE_REGISTRATION_COMPONENTS_V1,
    PROFILE_TOPOLOGY_COMPONENTS_V1, LAUNCH_ENVELOPE_COMPONENTS_V2,
    compute_lifecycle_launch_gas_bounds, decode_auto_compound_tail,
    decode_lifecycle_pool_bound_v4_market_config, encode_auto_compound_tail,
    encode_lifecycle_pool_bound_v4_market_config, get_known_lifecycle_profile,
    get_launch_addresses, hash_launch_dependencies, hash_launch_plan, hash_lifecycle_profile,
    is_auto_compound_profile, list_known_lifecycle_profiles, mine_pool_bound_hook_salt,
    parse_launch_plan, pool_bound_market_commitment, predict_launch_token,
    serialize_launch_plan, validate_v4_lifecycle_market, valid_pool_bound_hook_address,
)
from black_market_sdk.lifecycle import (
    _call, _json_tuple, _known_bound_deployment, _market_tuple, _pool_bound_deployment,
    _struct_tuple, _tuple_type,
)
from black_market_sdk.lifecycle_rpc import LaunchRpcError, hex_bytes, read_block

CORE = get_launch_addresses(4663).orchestrator
CREATOR = "0x1000000000000000000000000000000000000001"
TOKEN = "0x2000000000000000000000000000000000000001"
ZERO = bytes(32)
BUDGET = 10**24
ORACLE = keccak(abi_encode(["uint24", "uint16"], [1, 4096]))
PROFILES = (AUTO_COMPOUND_STATIC_V1_PROFILE_ID, AUTO_COMPOUND_DYNAMIC_V1_PROFILE_ID)
RETIRED = (
    "0x1e41841a30f6cbac7cc41f57753bd9bd2eb155b9f4dfc869b19a75afcbf51d68",
    "0x53ddf479f6db40aebb02afe8c46031f69cb22e9712428621beb1652e0adae0d7",
)
FIXTURE = Path(__file__).parent / "fixtures/auto-compound-registry-readback.json"


def preset(identity=PROFILES[0]):
    return get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity)


def config(identity=PROFILES[0], *, token=TOKEN, share=5000, fee_mode=0):
    envelope = preset(identity).profile.envelope
    lower, upper = (0, 887220) if int(token, 16) < int(ROBINHOOD_WETH, 16) else (-887220, 0)
    return LifecyclePoolBoundV4MarketConfigV6(
        6, 0, 60, 1 << 96, 10000, 1000, 7654321, fee_mode,
        envelope.protocol_fee_denominator, envelope.protocol_treasury, True, ORACLE, ZERO,
        bytes.fromhex(identity[2:]), envelope.terms_digest, envelope.beneficiary, 0,
        (LifecycleV4Position(lower, upper, 10**18, ZERO, BUDGET),),
        auto_compound_config=AutoCompoundConfigV1(share),
    )


def request(identity=PROFILES[0], *, token=TOKEN, share=5000, fee_mode=0):
    policies = tuple(LifecycleFeeAssetPolicy(asset, 10000, 0, 0)
        for asset in sorted((token, ROBINHOOD_WETH), key=lambda address: int(address, 16)))
    market = LifecycleMarketConfig(preset(identity).profile.registration.adapter_id,
        identity, ROBINHOOD_WETH, BUDGET, 6,
        encode_lifecycle_pool_bound_v4_market_config(config(identity, token=token, share=share, fee_mode=fee_mode)))
    return LaunchPlanV1(4663, CORE, CREATOR, 280001,
        LifecycleTokenConfig(0, 0, "Auto compound SDK test", "ACS", BUDGET, 0, "", ZERO, CREATOR, True),
        (), policies, (market,), (), 2_000_000_000, 0)


@pytest.mark.parametrize("share", [0, 1, 5000, 9999, 10000])
def test_tail_roundtrip_exact_contract_encoding(share):
    value = AutoCompoundConfigV1(share)
    encoded = encode_auto_compound_tail(value)
    assert len(encoded) == 64
    assert encoded == abi_encode(["bytes32", "uint16"], [
        bytes.fromhex(AUTO_COMPOUND_CONFIG_VERSION[2:]), share])
    assert decode_auto_compound_tail(encoded) == value


@pytest.mark.parametrize("share", [-1, 10001, 65536, True, 1.5, "5000", None])
def test_tail_encoder_rejects_out_of_range_or_lossy_shares(share):
    with pytest.raises(ValueError, match="compound_share_bps"):
        encode_auto_compound_tail(AutoCompoundConfigV1(share))


@pytest.mark.parametrize("length", [0, 32, 63, 65, 96])
def test_tail_decoder_rejects_inexact_lengths(length):
    with pytest.raises(ValueError, match="exactly 64"):
        decode_auto_compound_tail(bytes(length))


@pytest.mark.parametrize("tail", [
    bytes(64),
    bytes.fromhex(AUTO_COMPOUND_CONFIG_VERSION[2:]) + (10001).to_bytes(32, "big"),
    bytes.fromhex(AUTO_COMPOUND_CONFIG_VERSION[2:]) + (65536).to_bytes(32, "big"),
])
def test_tail_decoder_rejects_wrong_version_invalid_share_and_noncanonical_padding(tail):
    with pytest.raises(ValueError):
        decode_auto_compound_tail(tail)


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("fee_mode", [0, 1])
@pytest.mark.parametrize("share", [0, 5000, 10000])
def test_market_config_tail_roundtrip_and_portable_plan(identity, fee_mode, share):
    selected = config(identity, share=share, fee_mode=fee_mode)
    encoded = encode_lifecycle_pool_bound_v4_market_config(selected)
    assert encoded[-64:] == encode_auto_compound_tail(selected.auto_compound_config)
    assert decode_lifecycle_pool_bound_v4_market_config(encoded, expected_version=6) == selected
    plan = request(identity, share=share, fee_mode=fee_mode)
    assert hash_launch_plan(parse_launch_plan(serialize_launch_plan(plan))) == hash_launch_plan(plan)
    mapping = _json_tuple(V4_MARKET_CONFIG_COMPONENTS_V6,
        _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V6, selected))
    # Semantic mapping inputs use integer quantities rather than portable JSON strings.
    mapping["sqrtPriceX96"] = selected.sqrt_price_x96
    mapping["positions"][0]["liquidity"] = selected.positions[0].liquidity
    mapping["positions"][0]["maxTokenAmount"] = selected.positions[0].max_token_amount
    mapping["autoCompoundConfig"] = selected.auto_compound_config
    assert encode_lifecycle_pool_bound_v4_market_config(mapping) == encoded


@pytest.mark.parametrize("identity", PROFILES)
def test_market_decoder_rejects_missing_extra_wrong_version_and_noncanonical_prefix(identity):
    wire = encode_lifecycle_pool_bound_v4_market_config(config(identity))
    for invalid in (wire[:-64], wire[:-1], wire + bytes(32), wire[:-64] + bytes(64),
                    (64).to_bytes(32, "big") + wire[32:]):
        with pytest.raises((ValueError, DecodingError)):
            decode_lifecycle_pool_bound_v4_market_config(invalid)


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("change", [
    {"lp_fee_pips": 1}, {"developer_fee_bps": 1}, {"auto_compound_config": None},
    {"auto_compound_config": AutoCompoundConfigV1(10001)},
])
def test_market_validation_rejects_invalid_compound_economics_before_funding(identity, change):
    selected = replace(config(identity), **change)
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(selected)
    with pytest.raises(ValueError):
        validate_v4_lifecycle_market(market=request(identity).markets[0], config=selected,
            profile=preset(identity).profile, token=TOKEN)


@pytest.mark.parametrize("identity", RETIRED + ("0x" + "00" * 32,))
def test_retired_or_unlisted_profiles_cannot_select_a_compounding_tail(identity):
    assert not is_auto_compound_profile(identity)
    assert get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity) is None
    with pytest.raises(ValueError, match="Only auto-compound"):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(), profile_id=identity))


@pytest.mark.parametrize("identity", PROFILES)
def test_other_profile_tails_and_wrong_versions_are_not_accepted(identity):
    from black_market_sdk import UfgLaunchConfig, HMM_FIXED_PROFILE_ID
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(identity), version=5))
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(identity),
            ufg_launch_config=UfgLaunchConfig(10000, 0, (ROBINHOOD_WETH,))))
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(identity), profile_id=HMM_FIXED_PROFILE_ID))


def test_both_presets_match_pinned_registry_admission_graphs():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["chainId"] == 4663 and evidence["blockNumber"] == 100485106
    catalog = list_known_lifecycle_profiles(chain_id=4663, orchestrator=CORE)
    assert {row.profile.id for row in catalog}.issuperset(PROFILES)
    for row in evidence["profiles"]:
        selected = preset(row["profileId"])
        assert selected.supported and selected.bound_hook is not None
        assert selected.key.value == f"v4-auto-compound-{row['variant']}-pool"
        assert selected.profile.registration.enabled and selected.profile.adapter.enabled
        assert selected.profile.registration.adapter_id == row["adapterId"]
        assert selected.profile.adapter.implementation.lower() == row["decoded"]["adapter"]["implementation"].lower()
        assert selected.profile.adapter.code_hash == row["runtimeHashes"]["adapter"]
        assert selected.profile.topology.config_version == 6
        assert selected.profile.envelope.maximum_developer_fee_bps == 0
        assert selected.profile.envelope.bounds.fee_mode_flags == 3
        assert selected.bound_hook.creation_code.hex() == row["creationCode"][2:]
        assert "0x" + keccak(selected.bound_hook.creation_code).hex() == selected.profile.topology.hook_creation_code_hash
        assert hash_lifecycle_profile(selected.profile.envelope) == row["profileId"]
        assert hash_launch_dependencies(chain_id=4663, core=CORE, registry=evidence["registry"],
            registrar=selected.profile.adapter.implementation,
            graph=selected.profile.envelope.graph) == selected.profile.registration.dependency_digest
        assert is_auto_compound_profile(row["profileId"].upper())
        assert is_auto_compound_profile(bytes.fromhex(row["profileId"][2:]))
        for method, components, value in (
            ("adapter", ADAPTER_REGISTRATION_COMPONENTS_V1, selected.profile.adapter),
            ("profile", PROFILE_REGISTRATION_COMPONENTS_V1, selected.profile.registration),
            ("profileTopology", PROFILE_TOPOLOGY_COMPONENTS_V1, selected.profile.topology),
            ("profileEnvelope", LAUNCH_ENVELOPE_COMPONENTS_V2, selected.profile.envelope),
        ):
            assert hex_bytes(abi_encode([_tuple_type(components)], [
                _struct_tuple(components, value)])) == row["raw"][method]
    assert preset().key == KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL
    assert preset(PROFILES[1]).key == KnownLifecycleProfile.V4_AUTO_COMPOUND_DYNAMIC_POOL


@pytest.mark.parametrize("identity", PROFILES)
def test_compound_share_binds_constructor_while_only_hook_salt_is_normalized(identity):
    plan = request(identity)
    deployment, parameters = _known_bound_deployment(plan, 0, TOKEN)
    selected = config(identity)
    salted = replace(selected, hook_salt=(123).to_bytes(32, "big"))
    salted_plan = replace(plan, markets=(replace(plan.markets[0],
        config=encode_lifecycle_pool_bound_v4_market_config(salted)),))
    assert pool_bound_market_commitment(salted_plan, token=TOKEN,
        registrar=preset(identity).profile.adapter.implementation, market_index=0) == parameters.market_commitment
    assert _known_bound_deployment(salted_plan, 0, TOKEN)[0].init_code_hash == deployment.init_code_hash
    changed = request(identity, share=5001)
    changed_deployment, changed_parameters = _known_bound_deployment(changed, 0, TOKEN)
    assert changed_parameters.market_commitment != parameters.market_commitment
    assert changed_deployment.init_code_hash != deployment.init_code_hash
    assert changed_deployment.predicted_hook != deployment.predicted_hook
    assert hash_launch_plan(changed) != hash_launch_plan(plan)
    mined = asyncio.run(mine_pool_bound_hook_salt(deployer=deployment.deployer, init_code_hash=deployment.init_code_hash))
    mined_config = replace(selected, hook_salt=bytes.fromhex(mined.salt[2:]))
    assert encode_lifecycle_pool_bound_v4_market_config(mined_config)[-64:] == plan.markets[0].config[-64:]
    assert valid_pool_bound_hook_address(mined.predicted_hook)


@pytest.mark.parametrize("identity,fee", [(PROFILES[0], 10000), (PROFILES[1], 1000)])
def test_opening_math_uses_static_maximum_or_dynamic_minimum_with_reviewed_capacity(identity, fee):
    from black_market_sdk.lifecycle_buy_math import _opening_state
    plan = request(identity)
    plan = replace(plan, buys=(LifecycleInitialBuy(0, 10**12, 1, CREATOR, 4_295_128_740),))
    assert _opening_state(plan, 0).hook_fee == fee
    quote, = compute_lifecycle_opening_buys(plan, predicted_token=TOKEN)
    assert quote.token_out > 0
    assert compute_lifecycle_launch_gas_bounds(plan, predicted_token=TOKEN) is not None


@pytest.mark.skipif(not os.environ.get("AUTO_COMPOUND_RPC_URL") or not os.environ.get("BLACK_MARKET_REFERENCE_ROOT"),
                    reason="Opt-in read-only Robinhood RPC and reference contract checkout required")
@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("fee_mode", [0, 1])
def test_live_factory_planning_and_reference_encoder(identity, fee_mode, tmp_path):
    client = Web3(Web3.HTTPProvider(os.environ["AUTO_COMPOUND_RPC_URL"], request_kwargs={"timeout": 60}))
    assert client.eth.chain_id == 4663
    block = read_block(client)
    draft = request(identity, fee_mode=fee_mode)
    token = predict_launch_token(client, draft, block=block)
    plan = request(identity, token=token, fee_mode=fee_mode)
    selected = decode_lifecycle_pool_bound_v4_market_config(plan.markets[0].config)
    local, _ = _known_bound_deployment(plan, 0, token)
    mined = asyncio.run(mine_pool_bound_hook_salt(deployer=local.deployer, init_code_hash=local.init_code_hash))
    selected = replace(selected, hook_salt=bytes.fromhex(mined.salt[2:]))
    plan = replace(plan, markets=(replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(selected)),))
    profile = preset(identity).profile
    actual, _ = _pool_bound_deployment(client, plan, 0, token,
        _struct_tuple(ADAPTER_REGISTRATION_COMPONENTS_V1, profile.adapter),
        _struct_tuple(PROFILE_REGISTRATION_COMPONENTS_V1, profile.registration), profile.topology, block)
    expected, _ = _known_bound_deployment(plan, 0, token)
    assert actual == replace(expected, deployer=Web3.to_checksum_address(expected.deployer))
    assert actual.predicted_hook.lower() == mined.predicted_hook.lower()
    factory = profile.envelope.graph.collector_factory
    metadata = _call(client, factory, POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI,
        "poolBoundDeploymentMetadata", [profile.adapter.implementation, token, _market_tuple(plan, 0)], block)
    assert metadata[3].lower() == actual.predicted_hook.lower()
    accepted = _call(client, factory, POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI,
        "decodeAndValidate", [profile.adapter.implementation, token, _market_tuple(plan, 0)], block)
    assert accepted[0] == 6 and accepted[1] == 0 and accepted[5] == fee_mode
    reference = Path(os.environ["BLACK_MARKET_REFERENCE_ROOT"])
    market_json = tmp_path / "market.json"
    market_json.write_text(json.dumps(_json_tuple(V4_MARKET_CONFIG_COMPONENTS_V6,
        _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V6, selected))))
    reference_encoded = subprocess.run([sys.executable, "scripts/auto_compound_config.py", str(market_json),
        "--compound-share-bps", str(selected.auto_compound_config.compound_share_bps)],
        cwd=reference, check=True, capture_output=True, text=True).stdout.strip()
    assert reference_encoded == hex_bytes(plan.markets[0].config)
    for invalid in (plan.markets[0].config[:-1], plan.markets[0].config + bytes(32),
                    plan.markets[0].config[:-64] + bytes(64),
                    plan.markets[0].config[:-32] + (10001).to_bytes(32, "big")):
        invalid_plan = replace(plan, markets=(replace(plan.markets[0], config=invalid),))
        with pytest.raises(LaunchRpcError) as failure:
            _call(client, factory, POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI, "decodeAndValidate",
                [profile.adapter.implementation, token, _market_tuple(invalid_plan, 0)], block)
        assert failure.value.method == "eth_call" and failure.value.code == 3
    assert read_block(client, block.number).block_hash == block.block_hash
    print(json.dumps({"profileId": identity, "feeMode": fee_mode, "blockNumber": block.number,
        "blockHash": block.block_hash, "predictedToken": token, "predictedHook": actual.predicted_hook,
        "initCodeHash": actual.init_code_hash, "referenceTail": reference_encoded[-128:]}))
