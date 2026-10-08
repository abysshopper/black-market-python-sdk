"""Current bound config, exact author/economic binding and CREATE2 boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest
from eth_abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from black_market_sdk import (
    LifecyclePoolBoundV4MarketConfigV5, LifecyclePoolBoundV4MarketConfigV6,
    LifecycleV4MarketConfig, LifecycleV4Position,
    LaunchBlock, PoolBoundHookDeployment, prepare_and_plan_lifecycle_launch,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_v4_market_config,
    decode_lifecycle_abyss_market_config,
    encode_lifecycle_abyss_market_config,
    hash_launch_plan, launch_id_of, mine_pool_bound_hook_salt,
    pool_bound_market_commitment, predict_pool_bound_hook_address, to_launch_plan_tuple,
)
from black_market_sdk.lifecycle import (
    PoolBoundLifecycleDeployment, _profile_topology, _validate_launch_plan,
    plan_launch, simulate_launch_plan,
)
from test_lifecycle import plan as full_budget_plan, wire_planning
from black_market_sdk.lifecycle_rpc import LaunchRpcError

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = bytes(32)
TOKEN = "0x0000000000000000000000000000000000000010"
REGISTRAR = "0x0000000000000000000000000000000000000090"
DEPLOYER = "0xdeadbeef" + "00" * 16
PROFILE_ID = (42).to_bytes(32, "big")
BLOCK = LaunchBlock(7, "0x" + "ab" * 32, 100, 30_000_000, 1)


def config():
    return LifecyclePoolBoundV4MarketConfigV5(
        version=5, lp_fee_pips=3000, tick_spacing=60, sqrt_price_x96=2**96,
        hook_fee_pips=10000, fee_mode=0, protocol_fee_denominator=6,
        treasury="0x00000000000000000000000000000000000000a0",
        external_liquidity_disabled=True, oracle_config_id=(0x55).to_bytes(32, "big"),
        hook_salt=(2**255 + 17).to_bytes(32, "big"), profile_id=PROFILE_ID,
        terms_digest=(57).to_bytes(32, "big"),
        developer_beneficiary="0x00000000000000000000000000000000000000d0", developer_fee_bps=250,
        positions=(LifecycleV4Position(60, 120, 10**18, (2**250 + 31).to_bytes(32, "big"), 10**24),),
    )


def bound_plan():
    plan = full_budget_plan()
    selected = config()
    market = replace(plan.markets[0], adapter_id=(79).to_bytes(32, "big"),
        profile_id=PROFILE_ID, token_budget=selected.positions[0].max_token_amount,
        config_version=5, config=encode_lifecycle_pool_bound_v4_market_config(selected))
    return replace(plan, token=replace(plan.token, supply=market.token_budget + plan.markets[1].token_budget),
        markets=(market, *plan.markets[1:]))


def test_bound_roundtrip_preserves_signed_ticks_large_units_and_both_salts():
    position = LifecycleV4Position(-887220, 887220, 2**127 - 1, bytes.fromhex("12" * 32), 2**256 - 1)
    original = replace(config(), sqrt_price_x96=2**160 - 1, positions=(position, *config().positions))
    decoded = decode_lifecycle_pool_bound_v4_market_config(encode_lifecycle_pool_bound_v4_market_config(original))
    assert decoded == original
    assert decoded.hook_salt != decoded.positions[0].salt


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_bound_encoder_rejects_every_noncurrent_version(version):
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(), version=version))


def test_bound_decoder_rejects_shared_layout_and_noncanonical_bytes():
    shared = LifecycleV4MarketConfig(4, 3000, 60, 2**96, 10000, 0, 6, config().treasury,
        True, config().oracle_config_id, PROFILE_ID, config().terms_digest,
        config().developer_beneficiary, 250, config().positions)
    with pytest.raises((ValueError, DecodingError)):
        decode_lifecycle_pool_bound_v4_market_config(encode_lifecycle_v4_market_config(shared))
    with pytest.raises(ValueError):
        decode_lifecycle_pool_bound_v4_market_config(encode_lifecycle_pool_bound_v4_market_config(config()) + bytes(32))


@pytest.mark.parametrize("change", [
    {"sqrt_price_x96": float(2**96)}, {"lp_fee_pips": True}, {"developer_fee_bps": None},
    {"hook_salt": b"\x01" * 31}, {"tick_spacing": 2**23},
    {"positions": (LifecycleV4Position(60, 120, 2**128, ZERO_HASH, 10**24),)},
])
def test_bound_config_rejects_lossy_or_out_of_width_values(change):
    with pytest.raises(ValueError):
        encode_lifecycle_pool_bound_v4_market_config(replace(config(), **change))


def test_only_deployment_salt_is_normalized_while_full_plan_still_commits_it():
    original = bound_plan()
    changed = replace(original, markets=(replace(original.markets[0],
        config=encode_lifecycle_pool_bound_v4_market_config(replace(config(), hook_salt=bytes.fromhex("fe" * 32)))), *original.markets[1:]))
    before = pool_bound_market_commitment(original, token=TOKEN, registrar=REGISTRAR, market_index=0)
    assert pool_bound_market_commitment(changed, token=TOKEN, registrar=REGISTRAR, market_index=0) == before
    assert hash_launch_plan(changed) != hash_launch_plan(original)
    assert launch_id_of(changed) == launch_id_of(original)


@pytest.mark.parametrize("change", [
    {"lp_fee_pips": 3001}, {"tick_spacing": 120}, {"sqrt_price_x96": 2**96 + 1},
    {"hook_fee_pips": 10001}, {"fee_mode": 1}, {"protocol_fee_denominator": 7},
    {"treasury": "0x00000000000000000000000000000000000000a1"},
    {"external_liquidity_disabled": False}, {"oracle_config_id": bytes.fromhex("ab" * 32)},
    {"terms_digest": bytes.fromhex("cd" * 32)},
    {"developer_beneficiary": "0x00000000000000000000000000000000000000d1"}, {"developer_fee_bps": 251},
    {"positions": (replace(config().positions[0], salt=bytes.fromhex("de" * 32)),)},
    {"positions": (replace(config().positions[0], tick_lower=0),)},
    {"positions": (replace(config().positions[0], tick_upper=180),)},
    {"positions": (replace(config().positions[0], liquidity=10**18 + 1),)},
    {"positions": (replace(config().positions[0], max_token_amount=10**24 + 1),)},
])
def test_every_author_term_economic_field_and_position_is_constructor_bound(change):
    original = bound_plan()
    changed = replace(original, markets=(replace(original.markets[0],
        config=encode_lifecycle_pool_bound_v4_market_config(replace(config(), **change))), *original.markets[1:]))
    assert pool_bound_market_commitment(changed, token=TOKEN, registrar=REGISTRAR, market_index=0) != pool_bound_market_commitment(original, token=TOKEN, registrar=REGISTRAR, market_index=0)


def test_inner_profile_cannot_be_rebound_to_another_outer_offering():
    original = bound_plan()
    wrong = replace(original, markets=(replace(original.markets[0], profile_id=(43).to_bytes(32, "big")), *original.markets[1:]))
    with pytest.raises(ValueError):
        pool_bound_market_commitment(wrong, token=TOKEN, registrar=REGISTRAR, market_index=0)
    with pytest.raises(ValueError) as failure:
        _validate_launch_plan(wrong)
    assert failure.value.code == "PROFILE_TERMS_MISMATCH"


@pytest.mark.parametrize("version", [4, 5])
def test_one_v4_per_quote_applies_across_arbitrary_admitted_template_ids(version):
    plan = bound_plan()
    selected = replace(config(), positions=(replace(config().positions[0], max_token_amount=1),))
    first = replace(plan.markets[0], token_budget=1, config=encode_lifecycle_pool_bound_v4_market_config(selected))
    plan = replace(plan, token=replace(plan.token, supply=2))
    if version == 4:
        cfg = LifecycleV4MarketConfig(4, 3000, 60, 2**96, 10000, 0, 6, config().treasury,
            True, config().oracle_config_id, (99).to_bytes(32, "big"), config().terms_digest,
            config().developer_beneficiary, 250, selected.positions)
        encoded = encode_lifecycle_v4_market_config(cfg)
    else:
        encoded = encode_lifecycle_pool_bound_v4_market_config(replace(selected, profile_id=(99).to_bytes(32, "big")))
    second = replace(first, profile_id=(99).to_bytes(32, "big"), adapter_id=(100).to_bytes(32, "big"), config_version=version, config=encoded)
    with pytest.raises(ValueError):
        _validate_launch_plan(replace(plan, markets=(first, second), buys=()))
    different = replace(second, quote_asset=plan.markets[1].quote_asset)
    assert to_launch_plan_tuple(replace(plan, markets=(first, different), buys=()))[7][1][2].lower() == different.quote_asset.lower()
    _validate_launch_plan(replace(plan, markets=(first, different), buys=()))
    # Abyss on that same quote is not a second V4 market.
    abyss_config = decode_lifecycle_abyss_market_config(plan.markets[1].config)
    abyss = replace(plan.markets[1], quote_asset=first.quote_asset, token_budget=1,
        config=encode_lifecycle_abyss_market_config(replace(abyss_config,
            positions=(replace(abyss_config.positions[0], token_amount_maximum=1),))))
    _validate_launch_plan(replace(plan, markets=(first, abyss), buys=()))
    assert to_launch_plan_tuple(replace(plan, markets=(first, abyss), buys=()))[7][1][4] == 1


def test_create2_matches_independent_eip1014_vector():
    assert predict_pool_bound_hook_address(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), salt=ZERO_HASH).lower() == "0xb928f69bb1d91cd65274e3c79d8986362984fda3"


def test_local_mining_preserves_uint256_start_and_exact_permissions():
    start = 2**255 + 7007
    init_hash = keccak(b"pool-bound local mining boundary")
    result = asyncio.run(mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=init_hash, start_salt=start))
    salt = int(result.salt, 16)
    assert start <= salt < 2**256 and int(result.predicted_hook, 16) & 0x3FFF == 0x1AFC
    expected = keccak(b"\xff" + bytes.fromhex(DEPLOYER[2:]) + salt.to_bytes(32, "big") + init_hash)[-20:]
    assert result.predicted_hook.lower() == "0x" + expected.hex()


@pytest.mark.parametrize("start", [-1, True, 2**256, 0.5])
def test_local_miner_rejects_invalid_salt_boundaries(start):
    with pytest.raises(ValueError):
        asyncio.run(mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), start_salt=start))


def test_mining_honors_cancellation_before_and_during_search():
    async def scenario():
        event = asyncio.Event()
        event.set()
        with pytest.raises(asyncio.CancelledError):
            await mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), cancel_event=event)
        event.clear()
        def cancel(_):
            event.set()
        with pytest.raises(asyncio.CancelledError):
            await mine_pool_bound_hook_salt(deployer=DEPLOYER, init_code_hash=keccak(b"\x00"), cancel_event=event, on_progress=cancel)
    asyncio.run(scenario())


@pytest.mark.parametrize("error", [None, {"code": -32000, "message": "upstream timeout"}, {"code": 3, "message": "execution reverted", "data": "0x12345678"}])
def test_missing_or_broken_registry_topology_never_becomes_a_certificate(error):
    provider = SimpleNamespace(make_request=lambda *args: {"error": error} if error else {"result": "0x"})
    with pytest.raises((DecodingError, LaunchRpcError)):
        _profile_topology(SimpleNamespace(provider=provider), REGISTRAR, PROFILE_ID, BLOCK)


@pytest.mark.parametrize("field", ["deployer", "init_code_hash", "salt", "predicted_hook", "token_factory", "token_factory_code_hash"])
def test_wallet_revalidation_rejects_hook_deployment_or_factory_drift(monkeypatch, field):
    lifecycle, request, provider, client = wire_planning(monkeypatch, request=bound_plan())
    deployment = PoolBoundLifecycleDeployment(REGISTRAR, "0x" + "12" * 32,
        "0x" + "34" * 32, TOKEN, 0)
    state = {"changed": False}
    changed = "0x" + "56" * (20 if field in {"deployer", "predicted_hook", "token_factory"} else 32)
    def metadata(*_):
        current = replace(deployment, **{field: changed}) if state["changed"] and field not in {
            "token_factory", "token_factory_code_hash"} else deployment
        return (), (current,), ()
    def factory(*_):
        return (changed if state["changed"] and field == "token_factory" else REGISTRAR,
            changed if state["changed"] and field == "token_factory_code_hash" else "0x" + "78" * 32)
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", metadata)
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", factory)
    reviewed = plan_launch(client, request, account=request.creator, mode="atomic")
    assert reviewed.admitted
    state["changed"] = True
    with pytest.raises(ValueError) as failure:
        simulate_launch_plan(client, reviewed)
    assert failure.value.code == ("TOKEN_FACTORY_BINDING" if field.startswith("token_factory") else "HOOK_DEPLOYMENT_CHANGED")


@pytest.mark.parametrize("drift", [None, "factory", "began", "constructor", "source-refusal"])
def test_async_mining_proves_final_plan_and_rejects_binding_or_committed_launch_drift(monkeypatch, drift):
    lifecycle, request, provider, client = wire_planning(monkeypatch, request=bound_plan())
    state = {"changed": False}
    original_factory = lifecycle._read_token_factory_binding
    def factory(*args):
        if state["changed"] and drift == "factory":
            return REGISTRAR, "0x" + "56" * 32
        return original_factory(*args)
    monkeypatch.setattr(lifecycle, "_read_token_factory_binding", factory)
    def deployment(_, current, *, market_index, block=None):
        salt = decode_lifecycle_pool_bound_v4_market_config(current.markets[market_index].config).hook_salt
        salt = bytes.fromhex(salt[2:]) if isinstance(salt, str) else salt
        # A changed factory/predicted-token binding changes constructor metadata;
        # mining must not authorize that different final CREATE2 binding.
        altered = state["changed"] and drift in {"constructor", "factory"}
        init_hash = keccak(b"changed constructor" if altered else b"frozen constructor")
        return PoolBoundHookDeployment(DEPLOYER, "0x" + init_hash.hex(), "0x" + salt.hex(),
            predict_pool_bound_hook_address(deployer=DEPLOYER, init_code_hash=init_hash, salt=salt))
    def metadata(_, current, token, block):
        actual = deployment(client, current, market_index=0, block=block)
        provider.plan = current
        return (), (PoolBoundLifecycleDeployment(actual.deployer, actual.init_code_hash,
            actual.salt, actual.predicted_hook, 0),), ()
    monkeypatch.setattr(lifecycle, "read_pool_bound_hook_deployment", deployment)
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", metadata)
    def advance(_):
        state["changed"] = True
        if drift == "began":
            provider.latest_phase = provider.confirmed_phase = 1
        if drift == "source-refusal":
            provider.replay_failure = "unsupported"
    async def prepare():
        return await prepare_and_plan_lifecycle_launch(client, request,
            account=request.creator, mode="atomic", on_progress=advance)
    if drift in {"factory", "began", "constructor"}:
        with pytest.raises(ValueError) as failure:
            asyncio.run(prepare())
        if drift == "began":
            assert provider.simulations == []
        else:
            assert failure.value.code == "HOOK_DEPLOYMENT_CHANGED"
    else:
        planned = asyncio.run(prepare())
        assert planned.mode == "atomic"
        if drift == "source-refusal":
            assert not planned.admitted and planned.simulation.execution_proof == "unavailable"
            assert planned.simulation.failure_category == "source"
            assert all(transaction.gas_limit is None and transaction.gas_price is None
                and transaction.admission is not None and not transaction.admission.admitted
                for transaction in planned.transactions)
        else:
            assert planned.admitted
            assert planned.simulation.execution_proof == planned.simulation.protocol_fit == "proved"
        assert planned.plan_hash == hash_launch_plan(planned.plan)
        assert int(planned.hook_deployments[0].predicted_hook, 16) & 0x3FFF == 0x1AFC
        finalized = decode_lifecycle_pool_bound_v4_market_config(planned.plan.markets[0].config)
        assert planned.hook_deployments[0].salt == (
            finalized.hook_salt if isinstance(finalized.hook_salt, str) else "0x" + finalized.hook_salt.hex())


def test_v5_and_v6_have_independent_exact_wire_layouts_without_defaults():
    historical = config()
    values = {field.name: getattr(historical, field.name) for field in fields(historical) if field.name != "version"}
    current = LifecyclePoolBoundV4MarketConfigV6(version=6, minimum_hook_fee_pips=1250,
        fee_sensitivity_pips_seconds_per_tick=7654321, **values)
    v5_type = "(uint16,uint24,int24,uint160,uint24,uint8,uint8,address,bool,bytes32,bytes32,bytes32,bytes32,address,uint16,(int24,int24,uint128,bytes32,uint256)[])"
    v6_type = "(uint16,uint24,int24,uint160,uint24,uint24,uint32,uint8,uint8,address,bool,bytes32,bytes32,bytes32,bytes32,address,uint16,(int24,int24,uint128,bytes32,uint256)[])"
    for selected, wire_type in [(historical, v5_type), (current, v6_type)]:
        positions = tuple((position.tick_lower, position.tick_upper, position.liquidity,
            position.salt, position.max_token_amount) for position in selected.positions)
        wire_values = tuple(getattr(selected, field.name) for field in fields(selected) if field.name != "positions") + (positions,)
        expected = abi_encode([wire_type], [wire_values])
        assert encode_lifecycle_pool_bound_v4_market_config(selected) == expected
        assert decode_lifecycle_pool_bound_v4_market_config(expected, expected_version=selected.version) == selected
        with pytest.raises(ValueError):
            decode_lifecycle_pool_bound_v4_market_config(expected, expected_version=11 - selected.version)
    assert not hasattr(historical, "minimum_hook_fee_pips")
    assert not hasattr(historical, "fee_sensitivity_pips_seconds_per_tick")
