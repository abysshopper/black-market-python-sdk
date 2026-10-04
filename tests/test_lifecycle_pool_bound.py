"""Bound config, economic binding, CREATE2 and certified-topology boundaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from black_market_sdk import (
    LifecyclePoolBoundV4MarketConfig,
    LifecycleV4MarketConfig,
    LifecycleV4Position,
    LaunchBlock,
    PoolBoundHookDeployment,
    prepare_pool_bound_lifecycle_plan,
    V4_LIFECYCLE_CONFIG_SCHEMA,
    V4_LIFECYCLE_PROFILE_ID,
    V4_POOL_BOUND_LIFECYCLE_ADAPTER_ID,
    V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA,
    V4_POOL_BOUND_LIFECYCLE_PROFILE_ID,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_v4_market_config,
    hash_launch_plan,
    launch_id_of,
    launch_plan_from_dict,
    mine_pool_bound_hook_salt,
    pool_bound_market_commitment,
    predict_pool_bound_hook_address,
    to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import _assert_reviewed_deployments, _profile_topology
from black_market_sdk.lifecycle_rpc import LaunchRpcError


ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = bytes(32)
TOKEN = "0x0000000000000000000000000000000000000010"
REGISTRAR = "0x0000000000000000000000000000000000000090"
DEPLOYER = "0xdeadbeef" + "00" * 16
BLOCK = LaunchBlock(7, "0x" + "ab" * 32, 100, 30_000_000, 1)


def config():
    return LifecyclePoolBoundV4MarketConfig(
        version=3, lp_fee_pips=3000, tick_spacing=60, sqrt_price_x96=2**96,
        hook_fee_pips=10000, fee_mode=0, protocol_fee_denominator=6,
        treasury="0x00000000000000000000000000000000000000a0",
        external_liquidity_disabled=True, oracle_config_id=(0x55).to_bytes(32, "big"),
        hook_salt=(2**255 + 17).to_bytes(32, "big"),
        positions=(LifecycleV4Position(60, 120, 10**18, (2**250 + 31).to_bytes(32, "big"), 10**24),),
    )


def bound_plan():
    path = Path(__file__).with_name("fixtures") / "launch-lifecycle-v1.json"
    plan = launch_plan_from_dict(json.loads(path.read_text())["plan"])
    market = replace(plan.markets[0], adapter_id=V4_POOL_BOUND_LIFECYCLE_ADAPTER_ID,
                     profile_id=V4_POOL_BOUND_LIFECYCLE_PROFILE_ID, config_version=3,
                     config=encode_lifecycle_pool_bound_v4_market_config(config()))
    return replace(plan, markets=(market, *plan.markets[1:]))


def test_v3_roundtrip_retains_exact_units_signed_ticks_and_both_salt_domains():
    position = LifecycleV4Position(-887220, 887220, 2**127 - 1, bytes.fromhex("12" * 32), 2**256 - 1)
    original = replace(config(), sqrt_price_x96=2**160 - 1, positions=(position, *config().positions))
    decoded = decode_lifecycle_pool_bound_v4_market_config(encode_lifecycle_pool_bound_v4_market_config(original))
    assert decoded == original
    assert decoded.hook_salt != decoded.positions[0].salt
    assert decoded.positions[0].max_token_amount == 2**256 - 1


@pytest.mark.parametrize("version", [0, 1, 2, 4])
def test_bound_encoder_does_not_reinterpret_historical_versions(version):
    with pytest.raises(ValueError, match="version must be 3"):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(), version=version))


def test_v2_wire_and_noncanonical_v3_bytes_cannot_be_used_as_bound_config():
    old = LifecycleV4MarketConfig(
        2, 3000, 60, 2**96, 10000, 0, 6, config().treasury, True,
        config().oracle_config_id, config().positions,
    )
    with pytest.raises((ValueError, DecodingError)):
        decode_lifecycle_pool_bound_v4_market_config(encode_lifecycle_v4_market_config(old))
    encoded = encode_lifecycle_pool_bound_v4_market_config(config())
    with pytest.raises(ValueError, match="canonical V3"):
        decode_lifecycle_pool_bound_v4_market_config(encoded + bytes(32))


@pytest.mark.parametrize("change", [
    {"sqrt_price_x96": float(2**96)}, {"lp_fee_pips": True},
    {"hook_salt": b"\x01" * 31}, {"tick_spacing": 2**23},
    {"positions": (LifecycleV4Position(60, 120, 2**128, ZERO_HASH, 10**24),)},
])
def test_bound_economics_reject_lossy_or_out_of_width_values(change):
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(), **change))


def test_only_hook_salt_is_normalized_and_the_signed_plan_still_commits_it():
    original = bound_plan()
    changed_config = replace(config(), hook_salt=bytes.fromhex("fe" * 32))
    changed = replace(original, markets=(replace(original.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(changed_config)), *original.markets[1:]))
    before = pool_bound_market_commitment(original, token=TOKEN, registrar=REGISTRAR, market_index=0)
    assert pool_bound_market_commitment(changed, token=TOKEN, registrar=REGISTRAR, market_index=0) == before
    assert hash_launch_plan(changed) != hash_launch_plan(original)
    assert launch_id_of(changed) == launch_id_of(original)


@pytest.mark.parametrize("change", [
    {"lp_fee_pips": 3001}, {"tick_spacing": 120}, {"sqrt_price_x96": 2**96 + 1},
    {"hook_fee_pips": 10001}, {"fee_mode": 1}, {"protocol_fee_denominator": 7},
    {"treasury": "0x00000000000000000000000000000000000000a1"},
    {"external_liquidity_disabled": False}, {"oracle_config_id": bytes.fromhex("ab" * 32)},
    {"positions": (replace(config().positions[0], salt=bytes.fromhex("cd" * 32)),)},
    {"positions": (replace(config().positions[0], tick_lower=0),)},
    {"positions": (replace(config().positions[0], tick_upper=180),)},
    {"positions": (replace(config().positions[0], liquidity=10**18 + 1),)},
    {"positions": (replace(config().positions[0], max_token_amount=10**24 + 1),)},
    {"positions": (*config().positions, replace(config().positions[0], salt=bytes.fromhex("de" * 32)))},
])
def test_every_market_economic_field_and_position_is_bound(change):
    original = bound_plan()
    changed = replace(original, markets=(replace(original.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(replace(config(), **change))), *original.markets[1:]))
    assert pool_bound_market_commitment(changed, token=TOKEN, registrar=REGISTRAR, market_index=0) != pool_bound_market_commitment(original, token=TOKEN, registrar=REGISTRAR, market_index=0)


@pytest.mark.parametrize("change", [
    lambda plan: replace(plan, chain_id=plan.chain_id + 1),
    lambda plan: replace(plan, orchestrator="0x00000000000000000000000000000000000000f1"),
    lambda plan: replace(plan, markets=(replace(plan.markets[0], adapter_id=bytes.fromhex("ab" * 32)), *plan.markets[1:])),
    lambda plan: replace(plan, markets=(replace(plan.markets[0], token_budget=plan.markets[0].token_budget + 1), *plan.markets[1:])),
    lambda plan: replace(plan, markets=(replace(plan.markets[0], quote_asset="0x0000000000000000000000000000000000000021"), *plan.markets[1:])),
])
def test_constructor_commitment_preserves_market_domain_and_quote_budget_identity(change):
    original = bound_plan()
    expected = pool_bound_market_commitment(original, token=TOKEN, registrar=REGISTRAR, market_index=0)
    assert pool_bound_market_commitment(change(original), token=TOKEN, registrar=REGISTRAR, market_index=0) != expected
    assert pool_bound_market_commitment(original, token="0x0000000000000000000000000000000000000011", registrar=REGISTRAR, market_index=0) != expected
    assert pool_bound_market_commitment(original, token=TOKEN, registrar="0x0000000000000000000000000000000000000091", market_index=0) != expected


@pytest.mark.parametrize("second_profile", [V4_LIFECYCLE_PROFILE_ID, V4_POOL_BOUND_LIFECYCLE_PROFILE_ID])
def test_one_v4_market_per_quote_applies_across_offering_ids(second_profile):
    plan = bound_plan()
    first = replace(plan.markets[0], token_budget=1)
    second = replace(first, profile_id=second_profile, adapter_id=bytes.fromhex("a1" * 32))
    with pytest.raises(ValueError, match="one V4 market per quote"):
        to_launch_plan_tuple(replace(plan, markets=(first, second), buys=()))
    different_quote = replace(second, quote_asset=plan.markets[1].quote_asset)
    values = to_launch_plan_tuple(replace(plan, markets=(first, different_quote), buys=()))
    assert values[7][0][2].lower() != values[7][1][2].lower()


def test_create2_matches_independent_eip1014_vector():
    predicted = predict_pool_bound_hook_address(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), salt=ZERO_HASH)
    assert predicted.lower() == "0xb928f69bb1d91cd65274e3c79d8986362984fda3"


def test_local_mining_uses_exact_permissions_and_preserves_uint256_start():
    start = 2**255 + 7007
    init_hash = keccak(b"pool-bound local mining boundary")
    result = asyncio.run(mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=init_hash, start_salt=start))
    salt = int(result.salt, 16)
    assert start <= salt < 2**256
    assert int(result.predicted_hook, 16) & 0x3FFF == 0x1AFC
    expected = keccak(b"\xff" + bytes.fromhex(DEPLOYER[2:]) + salt.to_bytes(32, "big") + init_hash)[-20:]
    assert result.predicted_hook.lower() == "0x" + expected.hex()


@pytest.mark.parametrize("start", [-1, True, 2**256, 0.5])
def test_local_miner_rejects_invalid_salt_boundaries(start):
    with pytest.raises(ValueError, match="startSalt must fit uint256"):
        asyncio.run(mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), start_salt=start))


def test_mining_honors_preexisting_and_progress_callback_cancellation():
    async def scenario():
        cancelled = asyncio.Event()
        cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), cancel_event=cancelled)
        cancelled.clear()
        def cancel(_):
            cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), cancel_event=cancelled, on_progress=cancel)
    asyncio.run(scenario())


class MissingTopologyRpc:
    def __init__(self, error=None):
        self.error = error

    def make_request(self, method, params):
        assert method == "eth_call"
        return {"error": self.error} if self.error is not None else {"result": "0x"}


def registration(profile_id, schema, version):
    profile = (bytes.fromhex("11" * 32), schema, bytes.fromhex("22" * 32), REGISTRAR,
               ZERO_ADDRESS, TOKEN if profile_id == V4_LIFECYCLE_PROFILE_ID else ZERO_ADDRESS, 91, True)
    adapter = (REGISTRAR, bytes.fromhex("33" * 32), 91, version, True)
    return profile, adapter


def test_legacy_registry_fallback_is_never_a_pool_bound_certificate():
    client = SimpleNamespace(provider=MissingTopologyRpc())
    shared, adapter = registration(V4_LIFECYCLE_PROFILE_ID, V4_LIFECYCLE_CONFIG_SCHEMA, 2)
    topology = _profile_topology(client, REGISTRAR, V4_LIFECYCLE_PROFILE_ID, shared, adapter, BLOCK)
    assert topology.hook_topology == 1 and topology.hook_deployer == ZERO_ADDRESS
    bound, adapter = registration(V4_POOL_BOUND_LIFECYCLE_PROFILE_ID, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA, 3)
    with pytest.raises(ValueError, match="requires recorded topology"):
        _profile_topology(client, REGISTRAR, V4_POOL_BOUND_LIFECYCLE_PROFILE_ID, bound, adapter, BLOCK)
    wrong_schema, adapter = registration(V4_LIFECYCLE_PROFILE_ID, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA, 2)
    with pytest.raises(ValueError, match="cannot certify"):
        _profile_topology(client, REGISTRAR, V4_LIFECYCLE_PROFILE_ID, wrong_schema, adapter, BLOCK)


@pytest.mark.parametrize("error", [
    {"code": -32000, "message": "upstream timeout"},
    {"code": 3, "message": "execution reverted", "data": "0x12345678"},
])
def test_rpc_and_meaningful_contract_errors_do_not_downgrade_to_legacy_topology(error):
    client = SimpleNamespace(provider=MissingTopologyRpc(error))
    profile, adapter = registration(V4_LIFECYCLE_PROFILE_ID, V4_LIFECYCLE_CONFIG_SCHEMA, 2)
    with pytest.raises(LaunchRpcError):
        _profile_topology(client, REGISTRAR, V4_LIFECYCLE_PROFILE_ID, profile, adapter, BLOCK)


@pytest.mark.parametrize("field", ["deployer", "init_code_hash", "salt", "predicted_hook"])
def test_wallet_revalidation_rejects_drift_in_each_reviewed_deployment_field(field):
    deployment = PoolBoundHookDeployment(REGISTRAR, "0x" + "12" * 32, "0x" + "34" * 32, TOKEN)
    change = "0x" + "56" * (20 if field in {"deployer", "predicted_hook"} else 32)
    reviewed = SimpleNamespace(plan=bound_plan(), predicted_token=TOKEN, token_factory=REGISTRAR, token_factory_code_hash="0x" + "78" * 32,
                               market_admissions=({"marketIndex": 0, "hookDeployment": deployment},))
    fresh = SimpleNamespace(simulation=SimpleNamespace(transactions=("next wallet command",)), predicted_token=TOKEN, token_factory=REGISTRAR, token_factory_code_hash="0x" + "78" * 32,
                            market_admissions=({"marketIndex": 0, "hookDeployment": replace(deployment, **{field: change})},))
    with pytest.raises(ValueError):
        _assert_reviewed_deployments(reviewed, fresh)


def test_draft_finalization_rejects_metadata_that_changes_after_local_mining(monkeypatch):
    from black_market_sdk import lifecycle
    plan = bound_plan()
    market = replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(replace(config(), hook_salt=ZERO_HASH)))
    plan = replace(plan, markets=(market, *plan.markets[1:]))
    original = PoolBoundHookDeployment(DEPLOYER, "0x" + keccak(b"\x00").hex(),
                                      "0x" + "00" * 32, "0xb928f69bb1d91cd65274e3c79d8986362984fda3")
    calls = 0
    def read_metadata(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original if calls == 1 else replace(original, init_code_hash="0x" + "ee" * 32)
    monkeypatch.setattr(lifecycle, "_check_client_identity", lambda *args: None)
    monkeypatch.setattr(lifecycle, "read_launch_progress", lambda *args: SimpleNamespace(phase=0, head_block=BLOCK))
    monkeypatch.setattr(lifecycle, "predict_launch_token", lambda *args, **kwargs: TOKEN)
    monkeypatch.setattr(lifecycle, "read_pool_bound_hook_deployment", read_metadata)
    monkeypatch.setattr(lifecycle, "read_block", lambda *args: BLOCK)
    monkeypatch.setattr(lifecycle, "_progress_values", lambda *args: (None,) * 5 + (0,))
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", lambda *args: (REGISTRAR, "0x" + "ab" * 32))
    with pytest.raises(ValueError):
        asyncio.run(prepare_pool_bound_lifecycle_plan(SimpleNamespace(), plan))


@pytest.mark.parametrize("field", ["token_factory", "token_factory_code_hash"])
def test_wallet_revalidation_rejects_factory_drift_with_unchanged_token_and_hook_metadata(field):
    deployment = PoolBoundHookDeployment(REGISTRAR, "0x" + "12" * 32, "0x" + "34" * 32, TOKEN)
    values = {"plan": bound_plan(), "predicted_token": TOKEN, "token_factory": REGISTRAR,
              "token_factory_code_hash": "0x" + "78" * 32,
              "market_admissions": ({"marketIndex": 0, "hookDeployment": deployment},)}
    changed = "0x" + "56" * (20 if field == "token_factory" else 32)
    reviewed = SimpleNamespace(**values)
    fresh = SimpleNamespace(**{**values, field: changed}, simulation=SimpleNamespace(transactions=("next wallet command",)))
    with pytest.raises(ValueError):
        _assert_reviewed_deployments(reviewed, fresh)


def test_salt_finalization_rejects_factory_runtime_drift_even_when_prediction_is_unchanged(monkeypatch):
    from black_market_sdk import lifecycle
    plan = bound_plan()
    market = replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(replace(config(), hook_salt=ZERO_HASH)))
    plan = replace(plan, markets=(market, *plan.markets[1:]))
    deployment = PoolBoundHookDeployment(DEPLOYER, "0x" + keccak(b"\x00").hex(),
                                        "0x" + "00" * 32, "0xb928f69bb1d91cd65274e3c79d8986362984fda3")
    def read_metadata(client, current, *, market_index, **kwargs):
        current_config = decode_lifecycle_pool_bound_v4_market_config(current.markets[market_index].config)
        salt = "0x" + bytes(current_config.hook_salt).hex()
        predicted = predict_pool_bound_hook_address(deployer=deployment.deployer, init_code_hash=deployment.init_code_hash, salt=salt)
        return replace(deployment, salt=salt, predicted_hook=predicted)
    calls = 0
    def read_factory(*args):
        nonlocal calls
        calls += 1
        return REGISTRAR, "0x" + ("ab" if calls == 1 else "cd") * 32
    monkeypatch.setattr(lifecycle, "_check_client_identity", lambda *args: None)
    monkeypatch.setattr(lifecycle, "read_launch_progress", lambda *args: SimpleNamespace(phase=0, head_block=BLOCK))
    monkeypatch.setattr(lifecycle, "predict_launch_token", lambda *args, **kwargs: TOKEN)
    monkeypatch.setattr(lifecycle, "read_pool_bound_hook_deployment", read_metadata)
    monkeypatch.setattr(lifecycle, "read_block", lambda *args: BLOCK)
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", read_factory)
    monkeypatch.setattr(lifecycle, "_progress_values", lambda *args: (None,) * 5 + (0,))
    monkeypatch.setattr(lifecycle, "assert_canonical", lambda *args: None)
    with pytest.raises(ValueError):
        asyncio.run(prepare_pool_bound_lifecycle_plan(SimpleNamespace(), plan))
