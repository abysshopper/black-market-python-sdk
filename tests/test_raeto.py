"""Deployed Raeto settings, Node wire parity and non-optional planning enforcement."""
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
    RAETO_FIXED_PROFILE_ID, RAETO_DYNAMIC_PROFILE_ID, RAETO_FIXED_ADAPTER_ID,
    RAETO_DYNAMIC_ADAPTER_ID, RAETO_PAYOUT_REGISTRY_ADDRESS, RAETO_SHARE_BPS_OPTIONS,
    RAETO_QUOTE_ASSETS, RAETO_FEE_COLLECTOR_V1_ABI, RaetoLaunchConfigV1,
    KnownLifecycleProfile, LifecyclePlanningError, PlannedLaunch,
    ADAPTER_REGISTRATION_COMPONENTS_V1, PROFILE_REGISTRATION_COMPONENTS_V1,
    PROFILE_TOPOLOGY_COMPONENTS_V1, LAUNCH_ENVELOPE_COMPONENTS_V2,
    V4_MARKET_CONFIG_COMPONENTS_V6, build_launch_transactions, build_next_transaction,
    decode_lifecycle_pool_bound_v4_market_config, decode_raeto_launch_tail,
    encode_lifecycle_pool_bound_v4_market_config, encode_launch_plan, encode_raeto_launch_tail,
    get_known_lifecycle_profile, get_launch_addresses, hash_launch_bounds, hash_launch_dependencies,
    hash_launch_plan, hash_lifecycle_profile, is_raeto_profile, launch_id_of,
    list_known_lifecycle_profiles, parse_launch_plan, plan_launch, prepare_and_plan_lifecycle_launch,
    serialize_launch_plan, validate_raeto_launch_config, validate_raeto_launch_plan,
    validate_v4_lifecycle_market,
)
from black_market_sdk.lifecycle import _known_bound_deployment, _snake, _struct_tuple, _tuple_type
from black_market_sdk.lifecycle_rpc import hex_bytes

FIXTURES = Path(__file__).parent / "fixtures"
VECTORS = json.loads((FIXTURES / "raeto-launch-vectors.json").read_text())["cases"]
EVIDENCE = json.loads((FIXTURES / "raeto-registry-readback.json").read_text())
PROFILES = (RAETO_FIXED_PROFILE_ID, RAETO_DYNAMIC_PROFILE_ID)
CORE = get_launch_addresses(4663).orchestrator
TOKEN = VECTORS[0]["predictedToken"]


def case(identity=PROFILES[0]):
    row = next(row for row in VECTORS if row["plan"]["markets"][0]["profileId"] == identity)
    plan = parse_launch_plan(json.dumps(row["plan"]))
    config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[0].config, expected_version=6)
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity)
    return plan, config, preset


def prefix(config):
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V6)], [
        _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V6, config)])


def no_rpc():
    def forbidden(*_):
        raise AssertionError("Invalid Raeto economics must reject before RPC")
    return SimpleNamespace(provider=SimpleNamespace(make_request=forbidden))


def test_constants_match_deployed_menu_and_checksummed_addresses():
    assert RAETO_SHARE_BPS_OPTIONS == (500, 1000, 2500, 5000, 7500)
    assert tuple(to_checksum_address(address) for address in RAETO_QUOTE_ASSETS) == RAETO_QUOTE_ASSETS
    assert to_checksum_address(RAETO_PAYOUT_REGISTRY_ADDRESS) == RAETO_PAYOUT_REGISTRY_ADDRESS
    assert not is_raeto_profile(bytes(32))
    assert not is_raeto_profile("0x" + "00" * 32)


@pytest.mark.parametrize("share", RAETO_SHARE_BPS_OPTIONS)
def test_tail_roundtrips_exactly_one_abi_uint16_word(share):
    config = RaetoLaunchConfigV1(share)
    tail = abi_encode(["uint16"], [share])
    validate_raeto_launch_config(config)
    assert encode_raeto_launch_tail(config) == tail
    assert decode_raeto_launch_tail(tail) == config
    assert decode_raeto_launch_tail("0x" + tail.hex().upper()) == config


@pytest.mark.parametrize("share", [0, 499, 1234, 8000, 10000, -1, 65535, True, False,
    500.0, 0.5, float("nan"), float("inf"), "500", None])
def test_tail_rejects_every_nonmenu_or_lossy_share(share):
    for operation in (validate_raeto_launch_config, encode_raeto_launch_tail):
        with pytest.raises(LifecyclePlanningError) as failure:
            operation(RaetoLaunchConfigV1(share))
        assert failure.value.code == "INVALID_RAETO_CONFIG"
    if isinstance(share, int) and not isinstance(share, bool) and share >= 0:
        with pytest.raises(LifecyclePlanningError) as failure:
            decode_raeto_launch_tail(share.to_bytes(32, "big"))
        assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("encoded", [b"", bytes(31), bytes(33), bytes(64),
    (65536).to_bytes(32, "big"), ((1 << 255) + 500).to_bytes(32, "big"), "0x" + "g" * 64])
def test_tail_rejects_truncation_extra_bytes_nonhex_and_noncanonical_uint16(encoded):
    with pytest.raises(LifecyclePlanningError) as failure:
        decode_raeto_launch_tail(encoded)
    assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("row", VECTORS, ids=lambda row: f"{row['key']}/{row['quote']}/{row['shareBps']}")
def test_node_vectors_match_full_encoding_commitment_constructor_and_roundtrip(row):
    plan = parse_launch_plan(json.dumps(row["plan"]))
    config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[0].config, expected_version=6)
    assert config.raeto_launch_config == RaetoLaunchConfigV1(row["shareBps"])
    assert encode_raeto_launch_tail(config.raeto_launch_config).hex() == row["tail"][2:]
    encoded = encode_lifecycle_pool_bound_v4_market_config(config)
    assert encoded == prefix(config) + bytes.fromhex(row["tail"][2:])
    assert hex_bytes(encoded) == row["plan"]["markets"][0]["config"]
    assert hash_launch_plan(plan) == row["planHash"]
    assert hashlib.sha256(encode_launch_plan(plan)).hexdigest() == row["planEncodingSha256"]
    assert parse_launch_plan(serialize_launch_plan(plan)) == plan
    deployment, parameters = _known_bound_deployment(plan, 0, row["predictedToken"])
    assert parameters.market_commitment == row["marketCommitment"]
    assert deployment.init_code_hash == row["deployment"]["initCodeHash"]
    assert deployment.predicted_hook.lower() == row["deployment"]["predictedHook"].lower()
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=plan.markets[0].profile_id)
    validate_v4_lifecycle_market(market=plan.markets[0], config=config, profile=preset.profile, token=TOKEN)
    validate_raeto_launch_plan(plan)
    validate_raeto_launch_plan(replace(plan, markets=tuple(replace(market,
        profile_id="0x" + hex_bytes(market.profile_id)[2:].upper(),
        adapter_id="0x" + hex_bytes(market.adapter_id)[2:].upper(),
        quote_asset="0x" + market.quote_asset[2:].upper()) for market in plan.markets)))


@pytest.mark.parametrize("identity", PROFILES)
def test_presets_reproduce_preserved_admission_registry_graph_and_hook_bytes(identity):
    plan, _, selected = case(identity)
    row = next(row for row in EVIDENCE["profiles"] if row["profileId"] == identity)
    assert EVIDENCE["chainId"] == 4663 and EVIDENCE["blockNumber"] == 101180179
    profile, hook = selected.profile, selected.bound_hook
    assert selected.supported and selected in list_known_lifecycle_profiles(chain_id=4663, orchestrator=CORE)
    assert profile.registration.adapter_id == (RAETO_FIXED_ADAPTER_ID if identity == PROFILES[0] else RAETO_DYNAMIC_ADAPTER_ID)
    assert hash_lifecycle_profile(profile.envelope) == identity
    assert hash_launch_bounds(profile.envelope.bounds) == hex_bytes(profile.envelope.config_bounds_digest)
    assert profile.envelope.maximum_developer_fee_bps == 0
    assert profile.envelope.bounds.fee_mode_flags == 2
    assert hex_bytes(keccak(hook.creation_code)) == profile.topology.hook_creation_code_hash
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
    assert hex_bytes(keccak(bytes.fromhex(row["raw"]["profile"][2:]) +
        bytes.fromhex(row["raw"]["profileEnvelope"][2:]))) == row["admission"]["envelopeHash"]
    assert hex_bytes(keccak(bytes.fromhex(row["admission"]["admissionBytes"][2:]))) == row["admission"]["payloadHash"]
    assert tuple(check["shareBps"] for check in row["shareChecks"]["checks"] if check["accepted"]) == RAETO_SHARE_BPS_OPTIONS
    assert is_raeto_profile(identity.upper()) and is_raeto_profile(bytes.fromhex(identity[2:]))
    assert not hasattr(profile, "admitted")


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("change", [
    {"fee_mode": 0}, {"lp_fee_pips": 1}, {"developer_fee_bps": 1},
    {"raeto_launch_config": None}, {"raeto_launch_config": RaetoLaunchConfigV1(10000)},
    {"hmm_launch_config": object()}, {"ufg_launch_config": object()}, {"auto_compound_config": object()},
])
def test_market_rejects_invalid_economics_missing_tail_and_other_tail_combinations(identity, change):
    plan, config, preset = case(identity)
    invalid = replace(config, **change)
    for operation in (
        lambda: encode_lifecycle_pool_bound_v4_market_config(invalid),
        lambda: validate_v4_lifecycle_market(market=plan.markets[0], config=invalid, profile=preset.profile, token=TOKEN),
    ):
        with pytest.raises(LifecyclePlanningError) as failure:
            operation()
        assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("identity", PROFILES)
def test_wire_rejects_missing_mixed_extra_tails_and_wrong_version(identity):
    plan, config, _ = case(identity)
    wire = bytes.fromhex(hex_bytes(plan.markets[0].config)[2:])
    from black_market_sdk import AutoCompoundConfigV1, encode_auto_compound_tail
    for invalid in (prefix(config), wire + b"\x00", wire + bytes(32),
            prefix(config) + encode_auto_compound_tail(AutoCompoundConfigV1(500))):
        with pytest.raises(LifecyclePlanningError) as failure:
            decode_lifecycle_pool_bound_v4_market_config(invalid, expected_version=6)
        assert failure.value.code == "INVALID_RAETO_CONFIG"
    mapping = {field["name"]: getattr(config, _snake(field["name"]))
        for field in V4_MARKET_CONFIG_COMPONENTS_V6 if field["name"] not in ("minimumHookFeePips", "feeSensitivityPipsSecondsPerTick")}
    mapping.update(version=5, raetoLaunchConfig=config.raeto_launch_config)
    with pytest.raises(LifecyclePlanningError) as failure:
        encode_lifecycle_pool_bound_v4_market_config(mapping)
    assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("identity", PROFILES)
def test_optional_plan_validator_checks_all_markets_quote_profile_and_adapter(identity):
    plan, config, preset = case(identity)
    first = plan.markets[0]
    invalid_markets = (replace(first, quote_asset=TOKEN), replace(first, config_version=5),
        replace(first, profile_id=bytes(32)), replace(first, adapter_id=bytes(32)), replace(first, config=b""),
        replace(first, profile_id=PROFILES[1] if identity == PROFILES[0] else PROFILES[0]))
    for invalid in invalid_markets:
        for markets in ((invalid,), (first, invalid)):
            with pytest.raises(LifecyclePlanningError) as failure:
                validate_raeto_launch_plan(replace(plan, markets=markets))
            assert failure.value.code == "INVALID_RAETO_PLAN"
    with pytest.raises(LifecyclePlanningError) as failure:
        validate_raeto_launch_plan(replace(plan, markets=()))
    assert failure.value.code == "INVALID_RAETO_PLAN"
    with pytest.raises(LifecyclePlanningError) as failure:
        validate_v4_lifecycle_market(market=invalid_markets[0], config=config, profile=preset.profile, token=TOKEN)
    assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("identity", PROFILES)
@pytest.mark.parametrize("mode", ["atomic", "staged"])
@pytest.mark.parametrize("change", [{"quote_asset": "0x0000000000000000000000000000000000000001"},
    {"fee_mode": 0}, {"lp_fee_pips": 1}, {"developer_fee_bps": 1}, {"share_bps": 10000}, {"share_bps": None}])
def test_every_planning_and_continuation_entrypoint_rejects_unsafe_raeto_before_rpc(identity, mode, change):
    plan, config, _ = case(identity)
    if "quote_asset" in change:
        invalid = replace(plan.markets[0], **change)
    else:
        share = change.get("share_bps", config.raeto_launch_config.share_bps)
        economics = {name: value for name, value in change.items() if name != "share_bps"}
        wire = prefix(replace(config, **economics)) + (b"" if share is None else abi_encode(["uint256"], [share]))
        invalid = replace(plan.markets[0], config=wire)
    unsafe = replace(plan, markets=(invalid,))
    planned = PlannedLaunch(unsafe, hash_launch_plan(unsafe), launch_id_of(unsafe), TOKEN,
        unsafe.chain_id, unsafe.creator, mode, (), (), None, None, None, (), "", "")
    operations = (
        lambda: plan_launch(no_rpc(), unsafe, account=unsafe.creator, mode=mode),
        lambda: asyncio.run(prepare_and_plan_lifecycle_launch(no_rpc(), unsafe, account=unsafe.creator, mode=mode)),
        lambda: build_next_transaction(no_rpc(), unsafe, account=unsafe.creator, mode=mode),
        lambda: build_next_transaction(no_rpc(), planned),
    )
    for operation in operations:
        with pytest.raises(LifecyclePlanningError) as failure:
            operation()
        assert failure.value.code == "INVALID_RAETO_CONFIG"


def test_raeto_tail_is_exclusive_to_its_own_profile():
    _, config, _ = case()
    for key in (KnownLifecycleProfile.V4_FIXED_FEE_POOL, KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL,
            KnownLifecycleProfile.V4_HMM_FIXED_POOL, KnownLifecycleProfile.V4_UFG_FIXED_POOL,
            KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL):
        profile = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key).profile
        with pytest.raises(LifecyclePlanningError) as failure:
            encode_lifecycle_pool_bound_v4_market_config(replace(config, profile_id=profile.id))
        assert failure.value.code == "INVALID_RAETO_CONFIG"


@pytest.mark.parametrize("identity", PROFILES)
def test_share_changes_bind_plan_but_preserve_prefix_only_constructor_and_core_route(identity):
    plan, config, _ = case(identity)
    deployment, parameters = _known_bound_deployment(plan, 0, TOKEN)
    for changes in ({"raeto_launch_config": RaetoLaunchConfigV1(7500)}, {"hook_salt": (1234).to_bytes(32, "big")}):
        updated = replace(plan, markets=(replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, **changes))),))
        actual, bound = _known_bound_deployment(updated, 0, TOKEN)
        assert actual.init_code_hash == deployment.init_code_hash
        assert bound.market_commitment == parameters.market_commitment
        assert hash_launch_plan(updated) != hash_launch_plan(plan)
    for mode in ("atomic", "staged"):
        for command in build_launch_transactions(plan, mode=mode):
            assert command.to == CORE and command.from_address == plan.creator
            assert hex_bytes(plan.markets[0].config)[2:] in command.data


def test_collector_abi_is_the_verified_node_subset():
    assert {item["name"] for item in RAETO_FEE_COLLECTOR_V1_ABI} == {
        "shareBps", "owed", "claimRaeto", "setShareBps", "launchToken", "quoteAsset",
        "RaetoShareUpdated", "RaetoFeesBooked", "RaetoFeesClaimed",
    }
    for row in EVIDENCE["profiles"]:
        assert row["abiProvenance"]["classification"] == "FULL_VERIFIED"
        for entry in RAETO_FEE_COLLECTOR_V1_ABI:
            assert entry == next(item for item in row["collectorAbi"] if item.get("name") == entry["name"])
