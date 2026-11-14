"""RWA deployed verdicts, Node wire parity and pre-RPC launch enforcement."""
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from black_market_sdk import (
    RWA_FIXED_PROFILE_ID, RWA_DYNAMIC_PROFILE_ID, RWA_FIXED_ADAPTER_ID,
    RWA_DYNAMIC_ADAPTER_ID, RWA_ASSET_REGISTRY_ADDRESS, RWA_QUOTE_ASSET,
    RWA_MAX_BASKET, RWA_MAX_PREMIUM_BPS, RWA_ANY_PRICE_AGE,
    RWA_REWARD_COLLECTOR_V1_ABI, RWA_ASSET_REGISTRY_V1_ABI, RwaLaunchConfigV1,
    RAETO_QUOTE_ASSETS, RaetoLaunchConfigV1, KnownLifecycleProfile, LifecyclePlanningError,
    LifecycleRewardMode, PlannedLaunch, ADAPTER_REGISTRATION_COMPONENTS_V1,
    PROFILE_REGISTRATION_COMPONENTS_V1, PROFILE_TOPOLOGY_COMPONENTS_V1,
    LAUNCH_ENVELOPE_COMPONENTS_V2, V4_MARKET_CONFIG_COMPONENTS_V6,
    build_launch_transactions, build_next_transaction, decode_lifecycle_pool_bound_v4_market_config,
    decode_rwa_launch_tail, encode_lifecycle_pool_bound_v4_market_config, encode_launch_plan,
    encode_rwa_launch_tail, get_known_lifecycle_profile, get_launch_addresses, hash_launch_bounds,
    hash_launch_dependencies, hash_launch_plan, hash_lifecycle_profile, is_rwa_profile,
    launch_id_of, list_known_lifecycle_profiles, parse_launch_plan, plan_launch,
    prepare_and_plan_lifecycle_launch, serialize_launch_plan, validate_rwa_launch_config,
    validate_rwa_launch_plan, validate_rwa_market_context, validate_v4_lifecycle_market,
)
from black_market_sdk.lifecycle import _known_bound_deployment, _snake, _struct_tuple, _tuple_type
from black_market_sdk.lifecycle_rpc import hex_bytes

FIXTURES = Path(__file__).parent / "fixtures"
VECTORS = json.loads((FIXTURES / "rwa-launch-vectors.json").read_text())
EVIDENCE = json.loads((FIXTURES / "rwa-registry-readback.json").read_text())
PROFILES = (RWA_FIXED_PROFILE_ID, RWA_DYNAMIC_PROFILE_ID)
CORE = get_launch_addresses(4663).orchestrator
TOKEN = VECTORS["cases"][0]["predictedToken"]
LISTED = tuple(sorted(EVIDENCE["assetRegistry"]["listedAssets"], key=lambda asset: int(asset, 16)))
TERMS_TYPE = "(uint16,uint16,uint32,address[],uint16[])"


def terms(value):
    return RwaLaunchConfigV1(value["shareBps"], value["premiumBps"], value["maxPriceAge"],
        tuple(to_checksum_address(asset) for asset in value["basket"]), tuple(value["weightsBps"]))


def case(identity=PROFILES[0], size=2):
    row = next(row for row in VECTORS["cases"] if row["plan"]["markets"][0]["profileId"] == identity and row["size"] == size)
    plan = parse_launch_plan(json.dumps(row["plan"]))
    config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[0].config, expected_version=6)
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity)
    return plan, config, preset


def prefix(config):
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V6)], [_struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V6, config)])


def raw_tail(config):
    return abi_encode([TERMS_TYPE], [(config.share_bps, config.premium_bps, config.max_price_age, config.basket, config.weights_bps)])


def no_rpc():
    def forbidden(*_):
        raise AssertionError("Invalid RWA launches must reject before RPC")
    return SimpleNamespace(provider=SimpleNamespace(make_request=forbidden))


def test_constants_bind_both_deployed_factories_and_registry():
    assert RWA_MAX_BASKET == 6 and RWA_MAX_PREMIUM_BPS == 100 and RWA_ANY_PRICE_AGE == 0xFFFFFFFF
    assert to_checksum_address(RWA_ASSET_REGISTRY_ADDRESS) == RWA_ASSET_REGISTRY_ADDRESS
    assert to_checksum_address(RWA_QUOTE_ASSET) == RWA_QUOTE_ASSET == RAETO_QUOTE_ASSETS[1]
    assert not is_rwa_profile(bytes(32)) and not is_rwa_profile("0x" + "00" * 32)
    assert len(LISTED) >= RWA_MAX_BASKET
    for profile in EVIDENCE["profiles"]:
        assert profile["factoryMetadata"]["assetRegistry"].lower() == RWA_ASSET_REGISTRY_ADDRESS.lower()
        assert profile["factoryMetadata"]["usdg"].lower() == RWA_QUOTE_ASSET.lower()


@pytest.mark.parametrize("check", VECTORS["termChecks"], ids=lambda check: f"{check.get('profileId', 'static')}/{check['name']}")
def test_sdk_matches_every_pinned_deployed_verdict_and_node_static_rejection(check):
    config = terms(check["terms"])
    if check["accepted"]:
        assert encode_rwa_launch_tail(config) == bytes.fromhex(check["tail"][2:])
        assert decode_rwa_launch_tail(check["tail"]) == config
        if "deployedAccepted" in check:
            assert check["deployedAccepted"] or check["name"] == "unlisted stock"
    else:
        for operation in (validate_rwa_launch_config, encode_rwa_launch_tail):
            with pytest.raises(LifecyclePlanningError) as failure:
                operation(config)
            assert failure.value.code == check["code"] == "INVALID_RWA_CONFIG"
        if "tail" in check:
            with pytest.raises(LifecyclePlanningError) as failure:
                decode_rwa_launch_tail(check["tail"])
            assert failure.value.code == "INVALID_RWA_CONFIG"


@pytest.mark.parametrize("row", VECTORS["anyAgeTails"], ids=lambda row: str(len(row["terms"]["basket"])))
def test_one_to_six_stocks_roundtrip_exact_abi_tuple_with_offset(row):
    config = terms(row["terms"])
    tail = encode_rwa_launch_tail(config)
    assert tail == raw_tail(config) == bytes.fromhex(row["tail"][2:])
    assert int.from_bytes(tail[:32], "big") == 32
    assert len(tail) == 32 * (8 + 2 * len(config.basket))
    assert decode_rwa_launch_tail(tail) == config
    assert decode_rwa_launch_tail("0x" + tail.hex().upper()) == config


@pytest.mark.parametrize("field,value", [(field, value) for field in ("share_bps", "premium_bps", "max_price_age")
    for value in (True, False, -1, 0.5, float("nan"), float("inf"), "1", None)] +
    [("weights_bps", weights) for weights in ((True, 9999), (0.5, 9999.5), (-1, 10001))] +
    [("basket", (asset, LISTED[1])) for asset in ("not-an-address", LISTED[0][2:], "0X" + LISTED[0][2:])])
def test_python_boundary_rejects_lossy_or_noninteger_terms(field, value):
    _, config, _ = case()
    for operation in (validate_rwa_launch_config, encode_rwa_launch_tail):
        with pytest.raises(LifecyclePlanningError) as failure:
            operation(replace(config.rwa_launch_config, **{field: value}))
        assert failure.value.code == "INVALID_RWA_CONFIG"


def test_decoder_rejects_truncated_extended_nonhex_and_noncanonical_tails():
    _, config, _ = case()
    tail = encode_rwa_launch_tail(config.rwa_launch_config)
    shifted = (64).to_bytes(32, "big") + bytes(32) + tail[32:]
    for encoded in (b"", tail[:-32], tail + bytes(32), tail + b"\x00", shifted, "0xgg"):
        with pytest.raises(LifecyclePlanningError) as failure:
            decode_rwa_launch_tail(encoded)
        assert failure.value.code == "INVALID_RWA_CONFIG"


@pytest.mark.parametrize("row", VECTORS["cases"], ids=lambda row: f"{row['key']}/{row['size']}")
def test_node_vectors_match_config_plan_hash_constructor_and_hook_commitment_invariance(row):
    plan = parse_launch_plan(json.dumps(row["plan"]))
    config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[0].config, expected_version=6)
    assert config.rwa_launch_config == terms(row["config"]["rwaLaunchConfig"])
    assert encode_rwa_launch_tail(config.rwa_launch_config) == bytes.fromhex(row["tail"][2:])
    assert hex_bytes(encode_lifecycle_pool_bound_v4_market_config(config)) == row["plan"]["markets"][0]["config"]
    assert encode_lifecycle_pool_bound_v4_market_config(config) == prefix(config) + raw_tail(config.rwa_launch_config)
    assert hash_launch_plan(plan) == row["planHash"]
    assert hashlib.sha256(encode_launch_plan(plan)).hexdigest() == row["planEncodingSha256"]
    assert parse_launch_plan(serialize_launch_plan(plan)) == plan
    deployment, parameters = _known_bound_deployment(plan, 0, row["predictedToken"])
    assert parameters.market_commitment == row["marketCommitment"]
    assert deployment.init_code_hash == row["deployment"]["initCodeHash"]
    assert deployment.predicted_hook.lower() == row["deployment"]["predictedHook"].lower()
    for variant in row["hookCommitmentVariants"]:
        changes = {(_snake(name)): terms(value) if name == "rwaLaunchConfig" else value for name, value in variant["changes"].items()}
        changed = replace(plan, markets=(replace(plan.markets[0],
            config=encode_lifecycle_pool_bound_v4_market_config(replace(config, **changes))),))
        actual, bound = _known_bound_deployment(changed, 0, row["predictedToken"])
        assert bound.market_commitment == variant["marketCommitment"] == parameters.market_commitment
        assert actual.init_code_hash == variant["deployment"]["initCodeHash"] == deployment.init_code_hash
        assert actual.predicted_hook.lower() == variant["deployment"]["predictedHook"].lower()
        assert hash_launch_plan(changed) == variant["planHash"] != hash_launch_plan(plan)
        assert hashlib.sha256(encode_launch_plan(changed)).hexdigest() == variant["planEncodingSha256"]
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=plan.markets[0].profile_id)
    validate_v4_lifecycle_market(market=plan.markets[0], config=config, profile=preset.profile, token=TOKEN)
    validate_rwa_launch_plan(plan)
    validate_rwa_launch_plan(replace(plan, markets=tuple(replace(market,
        profile_id="0x" + hex_bytes(market.profile_id)[2:].upper(),
        adapter_id="0x" + hex_bytes(market.adapter_id)[2:].upper(),
        quote_asset="0x" + market.quote_asset[2:].upper()) for market in plan.markets)))
    for mode in ("atomic", "staged"):
        for command in build_launch_transactions(plan, mode=mode):
            assert command.to == CORE and command.from_address == plan.creator
            assert hex_bytes(plan.markets[0].config)[2:] in command.data


@pytest.mark.parametrize("identity", PROFILES)
def test_presets_reproduce_admission_dependency_graph_and_ordinary_hook_bytes(identity):
    _, _, preset = case(identity)
    row = next(row for row in EVIDENCE["profiles"] if row["profileId"] == identity)
    assert EVIDENCE["chainId"] == 4663 and EVIDENCE["blockNumber"] == 113693643
    profile, hook = preset.profile, preset.bound_hook
    assert preset.supported and preset in list_known_lifecycle_profiles(chain_id=4663, orchestrator=CORE)
    assert profile.registration.adapter_id == (RWA_FIXED_ADAPTER_ID if identity == PROFILES[0] else RWA_DYNAMIC_ADAPTER_ID)
    assert hash_lifecycle_profile(profile.envelope) == identity
    assert hash_launch_bounds(profile.envelope.bounds) == hex_bytes(profile.envelope.config_bounds_digest)
    assert profile.envelope.maximum_developer_fee_bps == 0 and profile.envelope.bounds.fee_mode_flags == 2
    assert hook.creation_code == b"".join(bytes.fromhex(chunk["runtime"][4:]) for chunk in row["chunks"])
    ordinary = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE,
        key=KnownLifecycleProfile.V4_FIXED_FEE_POOL if identity == PROFILES[0] else KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL)
    assert hook.creation_code == ordinary.bound_hook.creation_code
    assert hash_launch_dependencies(chain_id=4663, core=CORE, registry=EVIDENCE["registry"],
        registrar=profile.adapter.implementation, graph=profile.envelope.graph) == profile.registration.dependency_digest
    for name, components, value in (
        ("profile", PROFILE_REGISTRATION_COMPONENTS_V1, profile.registration),
        ("adapter", ADAPTER_REGISTRATION_COMPONENTS_V1, profile.adapter),
        ("profileTopology", PROFILE_TOPOLOGY_COMPONENTS_V1, profile.topology),
        ("profileEnvelope", LAUNCH_ENVELOPE_COMPONENTS_V2, profile.envelope),
    ):
        assert hex_bytes(abi_encode([_tuple_type(components)], [_struct_tuple(components, value)])) == row["raw"][name]
    assert row["raw"]["profileEnvelope"] == row["admission"]["envelopeBytes"]
    assert hex_bytes(keccak(bytes.fromhex(row["admission"]["admissionBytes"][2:]))) == row["admission"]["payloadHash"]
    assert get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity.upper()) is preset
    assert is_rwa_profile(identity.upper()) and is_rwa_profile(bytes.fromhex(identity[2:]))


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("change", [
    {"fee_mode": 0}, {"lp_fee_pips": 1}, {"developer_fee_bps": 1}, {"rwa_launch_config": None},
    {"rwa_launch_config": RwaLaunchConfigV1(2500, 101, 0, (LISTED[0],), (10000,))},
    {"raeto_launch_config": RaetoLaunchConfigV1(500)}, {"hmm_launch_config": object()},
    {"ufg_launch_config": object()}, {"auto_compound_config": object()},
])
def test_invalid_market_economics_and_foreign_tails_refuse_locally(identity, change):
    plan, config, preset = case(identity)
    invalid = replace(config, **change)
    for operation in (
        lambda: encode_lifecycle_pool_bound_v4_market_config(invalid),
        lambda: validate_v4_lifecycle_market(market=plan.markets[0], config=invalid, profile=preset.profile, token=TOKEN),
    ):
        with pytest.raises(LifecyclePlanningError) as failure:
            operation()
        assert failure.value.code == ("INVALID_RAETO_CONFIG" if "raeto_launch_config" in change else "INVALID_RWA_CONFIG")


@pytest.mark.parametrize("identity", PROFILES)
def test_wire_requires_its_own_canonical_config6_tail(identity):
    plan, config, _ = case(identity)
    wire = bytes.fromhex(hex_bytes(plan.markets[0].config)[2:])
    for invalid in (prefix(config), wire + bytes(32), wire + b"\x00", prefix(config) + abi_encode(["uint16"], [500])):
        with pytest.raises(LifecyclePlanningError) as failure:
            decode_lifecycle_pool_bound_v4_market_config(invalid, expected_version=6)
        assert failure.value.code == "INVALID_RWA_CONFIG"
    mapping = {field["name"]: getattr(config, _snake(field["name"])) for field in V4_MARKET_CONFIG_COMPONENTS_V6
        if field["name"] not in ("minimumHookFeePips", "feeSensitivityPipsSecondsPerTick")}
    mapping.update(version=5, rwaLaunchConfig=config.rwa_launch_config)
    with pytest.raises(LifecyclePlanningError) as failure:
        encode_lifecycle_pool_bound_v4_market_config(mapping)
    assert failure.value.code == "INVALID_RWA_CONFIG"


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("mode", ["atomic", "staged"])
def test_preparation_only_failures_refuse_planning_preparation_and_continuation_before_rpc(identity, mode):
    plan, config, _ = case(identity, size=3)
    a, b, c = config.rwa_launch_config.basket
    def stock_policy(asset, **changes):
        return replace(plan, fee_assets=tuple(replace(policy, **changes) if policy.asset.lower() == asset.lower() else policy for policy in plan.fee_assets))
    def raw_market(**changes):
        return replace(plan, markets=(replace(plan.markets[0], config=prefix(replace(config, **changes)) + raw_tail(config.rwa_launch_config)),))
    wrong_terms = replace(config.rwa_launch_config, weights_bps=(3334, 3333, 3332))
    unsafe = (
        replace(plan, token=replace(plan.token, reward_mode=LifecycleRewardMode.STAKING)),
        replace(plan, fee_assets=tuple(policy for policy in plan.fee_assets if policy.asset.lower() != c.lower())),
        stock_policy(b, owner_bps=5000, rewards_bps=5000),
        stock_policy(a, owner_bps=0, rewards_bps=9000, burn_bps=1000),
        replace(plan, markets=(replace(plan.markets[0], quote_asset=RAETO_QUOTE_ASSETS[0]),)),
        replace(plan, markets=(replace(plan.markets[0], adapter_id=RWA_DYNAMIC_ADAPTER_ID if identity == PROFILES[0] else RWA_FIXED_ADAPTER_ID),)),
        replace(plan, markets=(replace(plan.markets[0], config_version=5),)),
        raw_market(fee_mode=0), raw_market(lp_fee_pips=1), raw_market(developer_fee_bps=1),
        replace(plan, markets=(replace(plan.markets[0], config=prefix(config) + raw_tail(wrong_terms)),)),
    )
    for candidate in unsafe:
        planned = PlannedLaunch(candidate, hash_launch_plan(candidate), launch_id_of(candidate), TOKEN,
            candidate.chain_id, candidate.creator, mode, (), (), None, None, None, (), "", "")
        for operation in (
            lambda: validate_rwa_launch_plan(candidate),
            lambda: plan_launch(no_rpc(), candidate, account=candidate.creator, mode=mode),
            lambda: asyncio.run(prepare_and_plan_lifecycle_launch(no_rpc(), candidate, account=candidate.creator, mode=mode)),
            lambda: build_next_transaction(no_rpc(), candidate, account=candidate.creator, mode=mode),
            lambda: build_next_transaction(no_rpc(), planned),
        ):
            with pytest.raises(LifecyclePlanningError) as failure:
                operation()
            assert failure.value.code == "INVALID_RWA_CONFIG"


def test_rwa_tails_cannot_ride_other_profiles_and_non_rwa_markets_are_ignored():
    plan, config, _ = case()
    for key in (KnownLifecycleProfile.V4_FIXED_FEE_POOL, KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL,
            KnownLifecycleProfile.V4_HMM_FIXED_POOL, KnownLifecycleProfile.V4_UFG_FIXED_POOL,
            KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL, KnownLifecycleProfile.V4_RAETO_FIXED_POOL):
        preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
        with pytest.raises(LifecyclePlanningError) as failure:
            encode_lifecycle_pool_bound_v4_market_config(replace(config, profile_id=preset.profile.id))
        assert failure.value.code in ("INVALID_RWA_CONFIG", "INVALID_RAETO_CONFIG")
        market = replace(plan.markets[0], profile_id=preset.profile.id)
        validate_rwa_market_context(market)
        validate_rwa_launch_plan(replace(plan, token=replace(plan.token, reward_mode=LifecycleRewardMode.NONE), markets=(market,)))


def test_rwa_abis_reproduce_verified_collector_and_registry_entries():
    for row in EVIDENCE["profiles"]:
        assert row["abiProvenance"]["classification"] == "FULL_VERIFIED"
        for exported, preserved in ((RWA_REWARD_COLLECTOR_V1_ABI, row["collectorAbi"]),
                (RWA_ASSET_REGISTRY_V1_ABI, row["assetRegistryAbi"])):
            for entry in exported:
                assert entry == next(item for item in preserved if item.get("name") == entry["name"])
