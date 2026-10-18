"""HMM admission metadata, exact Solidity tails, guards and salt preservation."""
import asyncio
import json
from dataclasses import astuple, fields, replace
from pathlib import Path

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak
from web3 import Web3

from black_market_sdk import (
    HMM_DYNAMIC_PROFILE_ID, HMM_FIXED_PROFILE_ID, HmmLaunchConfig, HmmStrategyConfig,
    KnownLifecycleHook, KnownLifecycleProfile, decode_hmm_launch_tail, default_hmm_launch_config,
    decode_lifecycle_pool_bound_v4_market_config, encode_hmm_launch_tail,
    encode_lifecycle_pool_bound_v4_market_config, get_known_lifecycle_profile,
    hash_launch_plan, pool_bound_market_commitment, validate_hmm_launch_config,
    HMM_LAUNCH_ABI_TYPE, HMM_STRATEGY_COMPONENTS, HMM_LAUNCH_COMPONENTS,
    HMM_LIQUIDITY_MANAGER_V2_ABI, HMM_FEE_COLLECTOR_V2_ABI,
    HMM_FIXED_HOOK_V2_ABI, HMM_DYNAMIC_HOOK_V2_ABI, HMM_FEE_COLLECTOR_FACTORY_V2_ABI,
    encode_pool_bound_hook_parameters, hash_launch_dependencies, hash_lifecycle_profile,
    is_hmm_profile, mine_pool_bound_hook_salt, parse_launch_plan, serialize_launch_plan,
    validate_v4_lifecycle_market,
)
from black_market_sdk.lifecycle import _known_bound_deployment
from test_lifecycle_pool_bound import bound_plan, config, TOKEN
from black_market_sdk import LifecyclePoolBoundV4MarketConfigV6

CORE = "0x91560876033d568d25CDe98C78c33ff8FC43962c"
BUDGET = 10**24


def defaults():
    return default_hmm_launch_config(token_budget=BUDGET, quote_asset="WETH",
        max_bid_quote=1, reference_deviation=0)


def hmm_config(profile_id=HMM_FIXED_PROFILE_ID):
    original = config()
    return LifecyclePoolBoundV4MarketConfigV6(
        6, 0, original.tick_spacing, original.sqrt_price_x96, 10000, 1000, 7654321,
        original.fee_mode, original.protocol_fee_denominator, original.treasury,
        original.external_liquidity_disabled, original.oracle_config_id, original.hook_salt,
        bytes.fromhex(profile_id[2:]), original.terms_digest, original.developer_beneficiary, 0,
        original.positions, defaults(),
    )


def test_tail_matches_contract_field_order_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures/hmm-launch-tail.json").read_text())
    value = HmmLaunchConfig(fixture["strategyShareBps"], HmmStrategyConfig(*map(int, fixture["strategy"])))
    expected = bytes.fromhex(fixture["encoded"][2:])
    assert len(expected) == 512
    assert value == defaults()
    assert encode_hmm_launch_tail(value) == expected
    assert decode_hmm_launch_tail(expected) == value


@pytest.mark.parametrize("quote", ["USDG", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"])
def test_stable_defaults(quote):
    value = default_hmm_launch_config(token_budget=BUDGET, quote_asset=quote,
        max_bid_quote=1, reference_deviation=0)
    assert value.strategy.min_quote_deploy == 10**6
    assert value.strategy.min_quote_harvest == 10**7
    assert value.strategy.min_token_harvest == BUDGET // 100_000
    assert value.strategy.max_ask_token_exposure == BUDGET // 10
    assert value.strategy.max_acquired_inventory == BUDGET
    assert value.strategy.premium_decay_seconds == 0
    assert value.strategy.max_bid_quote == 1
    assert value.strategy.reference_deviation == 0


def test_default_quote_calibration_requires_explicit_unknown_thresholds():
    with pytest.raises(ValueError, match="no HMM calibration"):
        default_hmm_launch_config(token_budget=BUDGET, quote_asset="AAPL", quote_decimals=18,
            max_bid_quote=1, reference_deviation=0)
    value = default_hmm_launch_config(token_budget=BUDGET, quote_asset="unknown",
        min_quote_deploy=0, min_quote_harvest=1, max_bid_quote=1, reference_deviation=0)
    assert value.strategy.min_quote_deploy == 0
    for budget in (0, True, 99_999, 1 << 128):
        with pytest.raises(ValueError):
            default_hmm_launch_config(token_budget=budget, quote_asset="WETH",
                max_bid_quote=1, reference_deviation=0)
    with pytest.raises(ValueError):
        default_hmm_launch_config(token_budget=BUDGET, quote_asset="USDG", quote_decimals=True,
            max_bid_quote=1, reference_deviation=0)


@pytest.mark.parametrize("field,value", [
    ("bid_width", 0), ("bid_width", 1 << 23), ("ask_width", -1), ("gap", -1),
    ("stale_after_seconds", 0), ("stale_after_seconds", 1 << 32), ("stale_displacement", 0),
    ("min_quote_deploy", -1), ("min_quote_deploy", 1 << 256),
    ("min_quote_harvest", 0), ("min_token_harvest", 0), ("resale_premium_bps", 5001),
    ("max_asks", 0), ("max_asks", 9), ("max_asks", True),
    ("max_ask_token_exposure", 3), ("max_ask_token_exposure", 1 << 128),
    ("max_acquired_inventory", 1), ("max_acquired_inventory", 1 << 128),
    ("premium_decay_seconds", -1), ("premium_decay_seconds", 1 << 32),
    ("max_bid_quote", 0), ("max_bid_quote", -1), ("max_bid_quote", 1 << 128),
    ("max_bid_quote", True), ("max_bid_quote", 1.0), ("reference_deviation", -1),
    ("reference_deviation", 1 << 23), ("reference_deviation", True), ("reference_deviation", 0.0),
])
def test_strategy_guards_match_factory(field, value):
    invalid = replace(defaults(), strategy=replace(defaults().strategy, **{field: value}))
    with pytest.raises(ValueError):
        encode_hmm_launch_tail(invalid)


@pytest.mark.parametrize("share", [-1, 10001, True, 1.0])
def test_share_guards(share):
    with pytest.raises(ValueError):
        validate_hmm_launch_config(replace(defaults(), strategy_share_bps=share))


def test_valid_factory_boundaries_and_caps():
    value = HmmLaunchConfig(10000, HmmStrategyConfig(
        (1 << 23) - 1, 1, 0, (1 << 32) - 1, 1, 0, 1, 1, 5000, 8,
        (1 << 128) - 1, (1 << 128) - 1, (1 << 32) - 1, (1 << 128) - 1, (1 << 23) - 1,
    ))
    assert decode_hmm_launch_tail(encode_hmm_launch_tail(value)) == value
    assert encode_hmm_launch_tail(replace(value, strategy_share_bps=0))[:32] == bytes(32)


@pytest.mark.parametrize("profile_id", [HMM_FIXED_PROFILE_ID, HMM_DYNAMIC_PROFILE_ID])
def test_hmm_v6_tail_append_roundtrip_and_salt_reencoding(profile_id):
    original = hmm_config(profile_id)
    wire = encode_lifecycle_pool_bound_v4_market_config(original)
    assert wire[-512:] == encode_hmm_launch_tail(defaults())
    assert decode_lifecycle_pool_bound_v4_market_config(wire) == original
    mined = replace(original, hook_salt=bytes.fromhex("ab" * 32))
    assert encode_lifecycle_pool_bound_v4_market_config(mined)[-512:] == wire[-512:]
    for malformed in (wire[:-512], wire[:-64], wire[:-1], wire + bytes(32)):
        with pytest.raises(ValueError):
            decode_lifecycle_pool_bound_v4_market_config(malformed)
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(original, hmm_launch_config=None))
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(original, profile_id=bytes(32)))


@pytest.mark.parametrize("key,identity,code_hash", [
    (KnownLifecycleProfile.V4_HMM_FIXED_POOL, HMM_FIXED_PROFILE_ID,
     "0xf1b6adc6e0321acb5a0febb1d4affb651155fa0b32ad483df209f51dc81b89be"),
    (KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL, HMM_DYNAMIC_PROFILE_ID,
     "0xa34e62ffbe33e4665a8f35521ab6168830d1b49e69d64a7078eb2bc910c22fc2"),
])
def test_hmm_exact_presets_and_constructor_commitment_excludes_tail(key, identity, code_hash):
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
    assert preset.supported and preset.bound_hook is not None
    assert preset.profile.id == identity
    assert preset.profile.topology.hook_creation_code_hash == code_hash
    assert preset.profile.adapter.capabilities == 123
    assert preset.profile.envelope.maximum_developer_fee_bps == 0
    envelope = preset.profile.envelope
    assert "0x" + keccak(preset.bound_hook.creation_code).hex() == code_hash
    assert hash_lifecycle_profile(envelope) == identity
    assert hash_launch_dependencies(chain_id=4663, core=CORE,
        registry="0xB2B0f9F36617810D67b8fC175153Aa10024C1358",
        registrar=preset.profile.adapter.implementation, graph=envelope.graph) == preset.profile.registration.dependency_digest
    selected = replace(hmm_config(identity), terms_digest=envelope.terms_digest,
        developer_beneficiary=envelope.beneficiary, treasury=envelope.protocol_treasury,
        protocol_fee_denominator=envelope.protocol_fee_denominator)
    plan = bound_plan()
    market = replace(plan.markets[0], profile_id=identity,
        adapter_id=preset.profile.registration.adapter_id, config_version=6,
        config=encode_lifecycle_pool_bound_v4_market_config(selected))
    plan = replace(plan, chain_id=4663, orchestrator=CORE, markets=(market, *plan.markets[1:]))
    changed = replace(selected, hmm_launch_config=replace(defaults(), strategy_share_bps=2500))
    changed_plan = replace(plan, markets=(replace(market,
        config=encode_lifecycle_pool_bound_v4_market_config(changed)), *plan.markets[1:]))
    assert hash_launch_plan(plan) != hash_launch_plan(changed_plan)
    assert pool_bound_market_commitment(plan, market_index=0, token=TOKEN,
        registrar=preset.profile.adapter.implementation) == pool_bound_market_commitment(
        changed_plan, market_index=0, token=TOKEN, registrar=preset.profile.adapter.implementation)
    assert _known_bound_deployment(plan, 0, TOKEN) == _known_bound_deployment(changed_plan, 0, TOKEN)
    deployment, parameters = _known_bound_deployment(plan, 0, TOKEN)
    # Independent static constructor order from the admitted hook ABI, not an HMM tuple.
    constructor_type = "(address,address,address,address,address,address,address,uint24,int24,uint160,uint24,uint24,uint32,uint8,uint8,address,bool,bytes32,bytes32,uint32)"
    expected_parameters = (
        envelope.graph.manager, preset.profile.adapter.implementation, envelope.graph.oracle_factory,
        CORE, envelope.graph.locker, TOKEN, market.quote_asset, 0, selected.tick_spacing,
        selected.sqrt_price_x96, selected.hook_fee_pips, selected.minimum_hook_fee_pips,
        selected.fee_sensitivity_pips_seconds_per_tick, selected.fee_mode,
        envelope.protocol_fee_denominator, envelope.protocol_treasury,
        selected.external_liquidity_disabled, selected.oracle_config_id,
        bytes.fromhex(parameters.market_commitment[2:]), len(selected.positions),
    )
    encoded_parameters = abi_encode([constructor_type], [expected_parameters])
    assert len(encoded_parameters) == 20 * 32
    assert encode_pool_bound_hook_parameters(parameters, config_version=6) == encoded_parameters
    assert deployment.init_code_hash == "0x" + keccak(preset.bound_hook.creation_code + encoded_parameters).hex()
    assert parameters.liquidity_locker.lower() == envelope.graph.locker.lower()
    assert deployment.deployer.lower() == envelope.graph.hook_deployer.lower()
    assert hash_launch_plan(parse_launch_plan(serialize_launch_plan(plan))) == hash_launch_plan(plan)


@pytest.mark.parametrize("options", [{}, {"max_bid_quote": 1}, {"reference_deviation": 0}])
def test_defaults_require_both_explicit_risk_controls(options):
    with pytest.raises(TypeError):
        default_hmm_launch_config(token_budget=BUDGET, quote_asset="WETH", **options)


@pytest.mark.parametrize("length", [0, 32, 448, 480, 513, 544])
def test_decoder_rejects_old_or_inexact_tail_lengths(length):
    with pytest.raises(ValueError, match="exactly 512"):
        decode_hmm_launch_tail(bytes(length))


@pytest.mark.parametrize("word,value", [
    (0, 10001), (1, 0), (7, 0), (10, 9), (14, 0), (14, 1 << 128),
    (15, -1), (15, 1 << 23), (1, 1 << 24),
])
def test_decoder_rejects_invalid_guards_and_noncanonical_integer_words(word, value):
    encoded = bytearray(encode_hmm_launch_tail(defaults()))
    encoded[word * 32:(word + 1) * 32] = value.to_bytes(32, "big", signed=value < 0)
    with pytest.raises((ValueError, DecodingError)):
        decode_hmm_launch_tail(bytes(encoded))


@pytest.mark.parametrize("field", [field.name for field in fields(HmmStrategyConfig)])
@pytest.mark.parametrize("value", [True, 1.5, "1", None])
def test_every_strategy_setting_rejects_lossy_or_noninteger_inputs(field, value):
    with pytest.raises(ValueError):
        encode_hmm_launch_tail(replace(defaults(), strategy=replace(defaults().strategy, **{field: value})))


@pytest.mark.parametrize("change", [{"lp_fee_pips": 1}, {"developer_fee_bps": 1},
    {"hmm_launch_config": None}, {"hmm_launch_config": replace(defaults(),
        strategy=replace(defaults().strategy, max_bid_quote=0))}])
def test_hmm_market_encoding_and_review_reject_invalid_hmm_economics(change):
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE,
        key=KnownLifecycleProfile.V4_HMM_FIXED_POOL)
    selected = replace(hmm_config(), **change)
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(selected)
    with pytest.raises(ValueError) as failure:
        validate_v4_lifecycle_market(market=bound_plan().markets[0],
            config=selected, profile=preset.profile, token=TOKEN)
    assert getattr(failure.value, "code", "INVALID_HMM_CONFIG") == "INVALID_HMM_CONFIG"


@pytest.mark.parametrize("identity", [
    "0x7e51d9aa979940e7867c75a82e05e924aa067e9d436f7731c3e3299f9e55d53d",
    "0x109598e017b8f12648aff0f0dc04dbf8d4c0493f41a63068682d8f9d0a07a83d",
])
def test_removed_hmm_v1_identities_are_not_sdk_offerings(identity):
    assert not is_hmm_profile(identity)
    assert get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity) is None
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(hmm_config(), profile_id=identity))


@pytest.mark.parametrize("identity", ["hmm-fixed-pool-v1", "hmm-dynamic-pool-v1"])
def test_removed_hmm_v1_hook_enum_values_are_rejected(identity):
    with pytest.raises(ValueError):
        KnownLifecycleHook(identity)


def test_v2_controls_encode_exact_fifteen_setting_tuple_with_web3():
    strategy_type = "(int24,int24,int24,uint32,int24,uint256,uint256,uint256,uint16,uint8,uint256,uint256,uint32,uint256,int24)"
    strategy = replace(defaults().strategy, max_bid_quote=(1 << 128) - 1,
        reference_deviation=50, premium_decay_seconds=259200)
    manager = Web3().eth.contract(abi=HMM_LIQUIDITY_MANAGER_V2_ABI)
    expected = keccak(text=f"setStrategyConfig({strategy_type})")[:4] + abi_encode(
        [strategy_type], [astuple(strategy)])
    assert bytes.fromhex(manager.encode_abi("setStrategyConfig", args=[astuple(strategy)])[2:]) == expected
    assert len(expected) == 4 + 15 * 32
    assert manager.encode_abi("setPlacementsPaused", args=[True]) == (
        "0x" + (keccak(text="setPlacementsPaused(bool)")[:4] + abi_encode(["bool"], [True])).hex())
    collector = Web3().eth.contract(abi=HMM_FEE_COLLECTOR_V2_ABI)
    assert collector.encode_abi("setStrategyShareBps", args=[10000]) == (
        "0x" + (keccak(text="setStrategyShareBps(uint16)")[:4] + abi_encode(["uint16"], [10000])).hex())
    from black_market_sdk.lifecycle import canonical_abi_type
    assert canonical_abi_type({"type": "tuple", "components": HMM_LAUNCH_COMPONENTS}) == HMM_LAUNCH_ABI_TYPE
    assert canonical_abi_type({"type": "tuple", "components": HMM_STRATEGY_COMPONENTS}) == strategy_type


@pytest.mark.parametrize("key,hook_abi", [
    (KnownLifecycleProfile.V4_HMM_FIXED_POOL, HMM_FIXED_HOOK_V2_ABI),
    (KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL, HMM_DYNAMIC_HOOK_V2_ABI),
])
def test_mined_v2_constructor_preserves_tail_and_conversion_funding(key, hook_abi):
    from black_market_sdk import LifecycleFundingKind
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
    envelope = preset.profile.envelope
    selected = replace(hmm_config(preset.profile.id), terms_digest=envelope.terms_digest,
        developer_beneficiary=envelope.beneficiary, treasury=envelope.protocol_treasury)
    plan = bound_plan()
    funding = replace(plan.funding[0], kind=LifecycleFundingKind.SWAP,
        input_asset=plan.funding[1].asset, input_amount=2**200 + 17,
        target="0x00000000000000000000000000000000000000f1", data="0x12345678")
    market = replace(plan.markets[0], profile_id=preset.profile.id,
        adapter_id=preset.profile.registration.adapter_id, config_version=6,
        config=encode_lifecycle_pool_bound_v4_market_config(selected))
    plan = replace(plan, chain_id=4663, orchestrator=CORE,
        markets=(market, *plan.markets[1:]), funding=(funding, *plan.funding[1:]))
    deployment, parameters = _known_bound_deployment(plan, 0, TOKEN)
    constructor = Web3().eth.contract(abi=hook_abi, bytecode=preset.bound_hook.creation_code)
    decoded_parameters = abi_decode(
        ["(address,address,address,address,address,address,address,uint24,int24,uint160,uint24,uint24,uint32,uint8,uint8,address,bool,bytes32,bytes32,uint32)"],
        encode_pool_bound_hook_parameters(parameters, config_version=6),
    )[0]
    parameter_tuple = tuple(Web3.to_checksum_address(value) if isinstance(value, str) else value
                            for value in decoded_parameters)
    constructor_data = bytes.fromhex(constructor.constructor(parameter_tuple).data_in_transaction[2:])
    assert deployment.init_code_hash == "0x" + keccak(constructor_data).hex()
    mined = asyncio.run(mine_pool_bound_hook_salt(deployer=deployment.deployer,
        init_code_hash=deployment.init_code_hash, start_salt=0))
    finalized = replace(plan, markets=(replace(market, config=encode_lifecycle_pool_bound_v4_market_config(
        replace(selected, hook_salt=bytes.fromhex(mined.salt[2:])))), *plan.markets[1:]))
    restored = parse_launch_plan(serialize_launch_plan(finalized))
    assert hash_launch_plan(restored) == hash_launch_plan(finalized) and restored.funding == plan.funding
    market_wire = bytes.fromhex(restored.markets[0].config[2:])
    assert market_wire[-512:] == encode_hmm_launch_tail(defaults())
    assert decode_lifecycle_pool_bound_v4_market_config(restored.markets[0].config).hmm_launch_config == defaults()
    assert _known_bound_deployment(restored, 0, TOKEN)[0].predicted_hook == mined.predicted_hook
    factory = Web3().eth.contract(abi=HMM_FEE_COLLECTOR_FACTORY_V2_ABI)
    market_tuple = (bytes.fromhex(preset.profile.registration.adapter_id[2:]),
        bytes.fromhex(preset.profile.id[2:]), market.quote_asset, market.token_budget, 6, market_wire)
    calldata = bytes.fromhex(factory.encode_abi("poolBoundHookParameters",
        args=[parameters.registrar, TOKEN, market_tuple])[2:])
    decoded = abi_decode(["address", "address", "(bytes32,bytes32,address,uint256,uint32,bytes)"], calldata[4:])
    assert decoded[2][-1] == market_wire
