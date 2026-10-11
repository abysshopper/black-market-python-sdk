"""HMM admission metadata, exact Solidity tails, guards and salt preservation."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from black_market_sdk import (
    HMM_DYNAMIC_PROFILE_ID, HMM_FIXED_PROFILE_ID, HmmLaunchConfig, HmmStrategyConfig,
    KnownLifecycleProfile, decode_hmm_launch_tail, default_hmm_launch_config,
    decode_lifecycle_pool_bound_v4_market_config, encode_hmm_launch_tail,
    encode_lifecycle_pool_bound_v4_market_config, get_known_lifecycle_profile,
    hash_launch_plan, pool_bound_market_commitment, validate_hmm_launch_config,
)
from black_market_sdk.lifecycle import _known_bound_deployment
from test_lifecycle_pool_bound import bound_plan, config, TOKEN
from black_market_sdk import LifecyclePoolBoundV4MarketConfigV6

CORE = "0x91560876033d568d25CDe98C78c33ff8FC43962c"
BUDGET = 10**24


def defaults():
    return default_hmm_launch_config(token_budget=BUDGET, quote_asset="WETH")


def hmm_config(profile_id=HMM_FIXED_PROFILE_ID):
    original = config()
    return LifecyclePoolBoundV4MarketConfigV6(
        6, 0, original.tick_spacing, original.sqrt_price_x96, 10000, 1000, 7654321,
        original.fee_mode, original.protocol_fee_denominator, original.treasury,
        original.external_liquidity_disabled, original.oracle_config_id, original.hook_salt,
        bytes.fromhex(profile_id[2:]), original.terms_digest, original.developer_beneficiary, 0,
        original.positions, defaults(),
    )


def test_tail_matches_checked_solidity_abi_encode_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures/hmm-launch-tail.json").read_text())
    value = HmmLaunchConfig(fixture["strategyShareBps"], HmmStrategyConfig(*map(int, fixture["strategy"])))
    expected = bytes.fromhex(fixture["encoded"][2:])
    assert len(expected) == 448
    assert value == defaults()
    assert encode_hmm_launch_tail(value) == expected
    assert decode_hmm_launch_tail(expected) == value


@pytest.mark.parametrize("quote", ["USDG", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"])
def test_stable_defaults(quote):
    value = default_hmm_launch_config(token_budget=BUDGET, quote_asset=quote)
    assert value.strategy.min_quote_deploy == 10**6
    assert value.strategy.min_quote_harvest == 10**7
    assert value.strategy.min_token_harvest == BUDGET // 100_000
    assert value.strategy.max_ask_token_exposure == BUDGET // 10
    assert value.strategy.max_acquired_inventory == BUDGET
    assert value.strategy.premium_decay_seconds == 0


def test_default_quote_calibration_requires_explicit_unknown_thresholds():
    with pytest.raises(ValueError, match="no HMM calibration"):
        default_hmm_launch_config(token_budget=BUDGET, quote_asset="AAPL", quote_decimals=18)
    value = default_hmm_launch_config(token_budget=BUDGET, quote_asset="unknown",
        min_quote_deploy=0, min_quote_harvest=1)
    assert value.strategy.min_quote_deploy == 0
    for budget in (0, True, 99_999, 1 << 128):
        with pytest.raises(ValueError):
            default_hmm_launch_config(token_budget=budget, quote_asset="WETH")
    with pytest.raises(ValueError):
        default_hmm_launch_config(token_budget=BUDGET, quote_asset="USDG", quote_decimals=True)


@pytest.mark.parametrize("field,value", [
    ("bid_width", 0), ("bid_width", 1 << 23), ("ask_width", -1), ("gap", -1),
    ("stale_after_seconds", 0), ("stale_after_seconds", 1 << 32), ("stale_displacement", 0),
    ("min_quote_deploy", -1), ("min_quote_deploy", 1 << 256),
    ("min_quote_harvest", 0), ("min_token_harvest", 0), ("resale_premium_bps", 5001),
    ("max_asks", 0), ("max_asks", 9), ("max_asks", True),
    ("max_ask_token_exposure", 3), ("max_ask_token_exposure", 1 << 128),
    ("max_acquired_inventory", 1), ("max_acquired_inventory", 1 << 128),
    ("premium_decay_seconds", -1), ("premium_decay_seconds", 1 << 32),
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
        (1 << 128) - 1, (1 << 128) - 1, (1 << 32) - 1,
    ))
    assert decode_hmm_launch_tail(encode_hmm_launch_tail(value)) == value
    assert encode_hmm_launch_tail(replace(value, strategy_share_bps=0))[:32] == bytes(32)


@pytest.mark.parametrize("profile_id", [HMM_FIXED_PROFILE_ID, HMM_DYNAMIC_PROFILE_ID])
def test_hmm_v6_tail_append_roundtrip_and_salt_reencoding(profile_id):
    original = hmm_config(profile_id)
    wire = encode_lifecycle_pool_bound_v4_market_config(original)
    assert wire[-448:] == encode_hmm_launch_tail(defaults())
    assert decode_lifecycle_pool_bound_v4_market_config(wire) == original
    mined = replace(original, hook_salt=bytes.fromhex("ab" * 32))
    assert encode_lifecycle_pool_bound_v4_market_config(mined)[-448:] == wire[-448:]
    for malformed in (wire[:-448], wire[:-1], wire + bytes(32)):
        with pytest.raises(ValueError):
            decode_lifecycle_pool_bound_v4_market_config(malformed)
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(original, hmm_launch_config=None))
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(original, profile_id=bytes(32)))


@pytest.mark.parametrize("key,identity,code_hash", [
    (KnownLifecycleProfile.V4_HMM_FIXED_POOL, HMM_FIXED_PROFILE_ID,
     "0x699b5108d783395b1a88a6c92cc135ce80a45d190266922942d05ddf388ae1ac"),
    (KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL, HMM_DYNAMIC_PROFILE_ID,
     "0x3b8b7ab8614512ce93d65c6aeb6ed18bb150ef7d74417c7282075ff683f1e35b"),
])
def test_hmm_exact_presets_and_constructor_commitment_excludes_tail(key, identity, code_hash):
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
    assert preset.supported and preset.bound_hook is not None
    assert preset.profile.id == identity
    assert preset.profile.topology.hook_creation_code_hash == code_hash
    assert preset.profile.adapter.capabilities == 123
    assert preset.profile.envelope.maximum_developer_fee_bps == 0
    envelope = preset.profile.envelope
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
