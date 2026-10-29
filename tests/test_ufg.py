"""Canonical UFG Solidity vectors, proxy admission, frozen evidence and funding."""
import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak
from web3 import Web3

from black_market_sdk import (
    UFG_LAUNCH_PROXY_ADDRESS, UFG_TREASURY_FACTORY_ADDRESS, UFG_UPSTREAM_FACTORY_ADDRESS,
    UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID, UFG_FIXED_ADAPTER_ID, UFG_DYNAMIC_ADAPTER_ID,
    UFG_LAUNCH_CONFIG_ABI_TYPE, UFG_LAUNCH_PROXY_V1_ABI, UFG_TREASURY_FACTORY_V1_ABI,
    UFG_BLACK_MARKET_FACTORY_ABI, UFG_BLACK_MARKET_TREASURY_ABI, UfgLaunchConfig,
    KnownLifecycleProfile, LaunchPlanV1, LifecycleTokenConfig, LifecycleMarketConfig,
    LifecycleAbyssMarketConfig, LifecycleAbyssPosition, LifecycleV4Position,
    LifecyclePoolBoundV4MarketConfigV6, LifecycleAssetFunding, LifecycleInitialBuy,
    LifecyclePlanningError, build_ufg_fee_asset_policies, build_ufg_launch_transactions,
    build_ufg_cancel_transaction, build_ufg_approvals, encode_ufg_launch_config,
    decode_ufg_launch_config, hash_ufg_launch_config, ufg_launch_nonce, is_ufg_profile,
    validate_ufg_launch_plan, encode_lifecycle_abyss_market_config,
    encode_lifecycle_pool_bound_v4_market_config, decode_lifecycle_pool_bound_v4_market_config,
    get_known_lifecycle_profile, get_launch_addresses, hash_lifecycle_profile, hash_launch_dependencies,
    hash_launch_plan, pool_bound_market_commitment, parse_launch_plan, serialize_launch_plan,
    plan_launch, prepare_and_plan_lifecycle_launch, ROBINHOOD_WETH,
)
from black_market_sdk.lifecycle import _known_bound_deployment, _execution_account
from test_lifecycle import RecordingRpcScenario, function_signature

ZERO = "0x" + "00" * 20
HASH = bytes(32)
PRINCIPAL = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
TOKEN = "0x1000000000000000000000000000000000000001"
QUOTE = ROBINHOOD_WETH
OTHER = "0x0000000000000000000000000000000000000001"
CORE = get_launch_addresses(4663).orchestrator
FIXTURES = Path(__file__).parent / "fixtures"


def terms():
    return UfgLaunchConfig(10000, 100, (QUOTE,))


def native_market():
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=KnownLifecycleProfile.ABYSS_2)
    config = LifecycleAbyssMarketConfig(2, 3000, HASH, 1 << 96,
        (LifecycleAbyssPosition(-887220, 0, 10**18, 10**24),))
    return LifecycleMarketConfig(preset.profile.registration.adapter_id, preset.profile.id, QUOTE, 10**24, 1,
        encode_lifecycle_abyss_market_config(config))


def bound_config(identity=UFG_FIXED_PROFILE_ID):
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity)
    envelope = preset.profile.envelope
    return LifecyclePoolBoundV4MarketConfigV6(6, 0, 60, 1 << 96, 10000, 1000, 7654321, 1,
        envelope.protocol_fee_denominator, envelope.protocol_treasury, True, (55).to_bytes(32, "big"), HASH,
        bytes.fromhex(identity[2:]), envelope.terms_digest, envelope.beneficiary, 0,
        (LifecycleV4Position(0, 887220, 10**18, HASH, 10**24),), ufg_launch_config=terms())


def request(*, bound=False, identity=UFG_FIXED_PROFILE_ID):
    market = native_market()
    if bound:
        market = replace(market, profile_id=identity,
            adapter_id=UFG_FIXED_ADAPTER_ID if identity == UFG_FIXED_PROFILE_ID else UFG_DYNAMIC_ADAPTER_ID,
            config_version=6, config=encode_lifecycle_pool_bound_v4_market_config(bound_config(identity)))
    return LaunchPlanV1(4663, CORE, UFG_LAUNCH_PROXY_ADDRESS, ufg_launch_nonce(PRINCIPAL, 17),
        LifecycleTokenConfig(0, 0, "UFG test", "UFG", 10**24, 0, "", HASH, PRINCIPAL, True), (),
        build_ufg_fee_asset_policies(TOKEN, terms().reserve_assets), (market,), (), 2_000_000_000, 0)


def test_config_and_market_vectors_match_committed_cast_abi_encode():
    fixture = json.loads((FIXTURES / "ufg-launch-vectors.json").read_text())
    assert fixture["generator"].startswith("cast abi-encode")
    for row in fixture["configs"]:
        config = UfgLaunchConfig(row["treasuryShareBps"], row["exitFeeBps"], tuple(row["reserveAssets"]))
        expected = bytes.fromhex(row["encoded"][2:])
        assert encode_ufg_launch_config(config) == expected
        assert hash_ufg_launch_config(config) == row["hash"]
        assert decode_ufg_launch_config(expected) == config
        assert expected[:32] == (32).to_bytes(32, "big")
    for row in fixture["markets"]:
        config = bound_config(row["profileId"])
        encoded = encode_lifecycle_pool_bound_v4_market_config(config)
        assert encoded.hex() == row["encoded"][2:]
        assert encoded.endswith(encode_ufg_launch_config(terms()))
        assert decode_lifecycle_pool_bound_v4_market_config(encoded) == config
        if shutil.which("cast"):
            actual = subprocess.run(["cast", "abi-encode", row["signature"], row["tuple"]],
                check=True, capture_output=True, text=True).stdout.strip()
            assert actual == row["prefix"]


@pytest.mark.parametrize("identity", [UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID])
def test_tail_preserved_by_salt_and_plan_serialization_but_excluded_from_constructor(identity):
    plan = request(bound=True, identity=identity)
    config = bound_config(identity)
    encoded = plan.markets[0].config
    changed = replace(config, hook_salt=(42).to_bytes(32, "big"))
    assert encode_lifecycle_pool_bound_v4_market_config(changed)[-192:] == encoded[-192:]
    tail_changed = replace(config, ufg_launch_config=replace(terms(), exit_fee_bps=101))
    changed_plan = replace(plan, markets=(replace(plan.markets[0], config=encode_lifecycle_pool_bound_v4_market_config(tail_changed)),))
    registrar = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=identity).profile.adapter.implementation
    assert hash_launch_plan(plan) != hash_launch_plan(changed_plan)
    assert pool_bound_market_commitment(plan, token=TOKEN, registrar=registrar, market_index=0) == pool_bound_market_commitment(changed_plan, token=TOKEN, registrar=registrar, market_index=0)
    assert _known_bound_deployment(plan, 0, TOKEN) == _known_bound_deployment(changed_plan, 0, TOKEN)
    assert hash_launch_plan(parse_launch_plan(serialize_launch_plan(plan))) == hash_launch_plan(plan)
    for malformed in (encoded[:-192], encoded[:-32], encoded + bytes(32), encoded[:64] + bytes(32) + encoded[64:]):
        with pytest.raises((ValueError, DecodingError)):
            decode_lifecycle_pool_bound_v4_market_config(malformed)


@pytest.mark.parametrize("change", [
    {"treasury_share_bps": 9999}, {"treasury_share_bps": True}, {"exit_fee_bps": -1},
    {"exit_fee_bps": 2001}, {"exit_fee_bps": True}, {"reserve_assets": ()},
    {"reserve_assets": (QUOTE,) * 8}, {"reserve_assets": (QUOTE, OTHER)},
    {"reserve_assets": (QUOTE, QUOTE)}, {"reserve_assets": (ZERO,)}, {"reserve_assets": ("bad",)},
])
def test_config_rejects_every_factory_and_proxy_boundary(change):
    with pytest.raises(ValueError):
        encode_ufg_launch_config(replace(terms(), **change))
    with pytest.raises(LifecyclePlanningError, match=".") as failure:
        validate_ufg_launch_plan(request(), replace(terms(), **change), TOKEN)
    assert failure.value.code == "INVALID_UFG_PLAN"


def test_config_decode_rejects_noncanonical_offsets_trailing_bytes_and_padding():
    encoded = encode_ufg_launch_config(terms())
    for malformed in (encoded[32:], encoded + bytes(32), (64).to_bytes(32, "big") + bytes(32) + encoded[32:],
                      encoded[:64] + (1 << 16).to_bytes(32, "big") + encoded[96:]):
        with pytest.raises((ValueError, DecodingError)):
            decode_ufg_launch_config(malformed)
    assert decode_ufg_launch_config(encode_ufg_launch_config(replace(terms(), exit_fee_bps=2000))).exit_fee_bps == 2000


@pytest.mark.parametrize("bound,identity", [(False, UFG_FIXED_PROFILE_ID), (True, UFG_FIXED_PROFILE_ID), (True, UFG_DYNAMIC_PROFILE_ID)])
def test_validator_accepts_native_and_both_admitted_ufg_v4_profiles(bound, identity):
    validate_ufg_launch_plan(request(bound=bound, identity=identity), terms(), TOKEN)


def mutate_market(plan, **changes):
    return replace(plan, markets=(replace(plan.markets[0], **changes),))


@pytest.mark.parametrize("mutate", [
    lambda p: replace(p, token=replace(p.token, kind=1)),
    lambda p: replace(p, token=replace(p.token, reward_mode=1)),
    lambda p: replace(p, executor_fee_bps=1), lambda p: replace(p, creator=PRINCIPAL),
    lambda p: replace(p, chain_id=1), lambda p: replace(p, orchestrator=PRINCIPAL),
    lambda p: replace(p, nonce=17), lambda p: replace(p, markets=()),
    lambda p: replace(p, token=replace(p.token, inventory_recipient=UFG_LAUNCH_PROXY_ADDRESS)),
    lambda p: replace(p, buys=(LifecycleInitialBuy(0, 1, 1, UFG_LAUNCH_PROXY_ADDRESS, 0),)),
    lambda p: mutate_market(p, quote_asset=OTHER), lambda p: replace(p, fee_assets=p.fee_assets[:-1]),
    lambda p: replace(p, fee_assets=tuple(reversed(p.fee_assets))),
    lambda p: replace(p, fee_assets=(replace(p.fee_assets[0], rewards_bps=1), *p.fee_assets[1:])),
    lambda p: replace(p, fee_assets=(replace(p.fee_assets[0], owner_bps=1), *p.fee_assets[1:])),
    lambda p: replace(p, fee_assets=(*p.fee_assets[:-1], replace(p.fee_assets[-1], burn_bps=1))),
    lambda p: replace(p, fee_assets=(*p.fee_assets[:-1], replace(p.fee_assets[-1], owner_bps=1))),
    lambda p: replace(p, fee_assets=(replace(p.fee_assets[0], burn_bps=1), *p.fee_assets[1:])),
    lambda p: mutate_market(p, profile_id=HASH),
    lambda p: mutate_market(p, adapter_id=HASH), lambda p: mutate_market(p, config_version=5),
    lambda p: mutate_market(p, config=p.markets[0].config + bytes(32)),
    lambda p: mutate_market(p, config=encode_lifecycle_abyss_market_config(LifecycleAbyssMarketConfig(0, 3000, HASH, 1 << 96, ()))),
])
def test_validator_rejects_every_proxy_plan_rule(mutate):
    with pytest.raises(LifecyclePlanningError) as failure:
        validate_ufg_launch_plan(mutate(request()), terms(), TOKEN)
    assert failure.value.code == "INVALID_UFG_PLAN"


@pytest.mark.parametrize("config", [UfgLaunchConfig(10000, 100, (OTHER, QUOTE)), UfgLaunchConfig(10000, 100, (QUOTE, TOKEN))])
def test_validator_rejects_unquoted_reserves_and_launch_token_reserve(config):
    with pytest.raises(LifecyclePlanningError) as failure:
        validate_ufg_launch_plan(request(), config, TOKEN)
    assert failure.value.code == "INVALID_UFG_PLAN"


@pytest.mark.parametrize("field,value", [("lp_fee_pips", 1), ("fee_mode", 0), ("profile_id", HASH),
    ("ufg_launch_config", None), ("ufg_launch_config", replace(terms(), exit_fee_bps=101))])
def test_validator_rejects_invalid_v4_fees_profile_and_mismatched_tail(field, value):
    # Build raw ABI for rejected shapes so the encoder guard cannot mask the plan validator.
    from black_market_sdk import V4_MARKET_CONFIG_COMPONENTS_V6
    from black_market_sdk.lifecycle import _struct_tuple, _tuple_type
    config = replace(bound_config(), **{field: value})
    raw = abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V6)], [_struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V6, config)])
    if config.ufg_launch_config is not None:
        raw += encode_ufg_launch_config(config.ufg_launch_config)
    with pytest.raises(LifecyclePlanningError) as failure:
        validate_ufg_launch_plan(mutate_market(request(bound=True), config=raw), terms(), TOKEN)
    assert failure.value.code == "INVALID_UFG_PLAN"


def test_nonce_namespace_and_uint96_width():
    assert ufg_launch_nonce(PRINCIPAL, (1 << 96) - 1) == (int(PRINCIPAL, 16) << 96) | ((1 << 96) - 1)
    assert _execution_account(request()).lower() == PRINCIPAL.lower()
    for nonce in (-1, 1 << 96, True, 1.5):
        with pytest.raises(ValueError):
            ufg_launch_nonce(PRINCIPAL, nonce)
    with pytest.raises(ValueError):
        ufg_launch_nonce(ZERO, 1)


def test_presets_match_all_recorded_registry_bytes_and_creation_code():
    evidence = json.loads((FIXTURES / "ufg-registry-readback.json").read_text())
    assert evidence["blockNumber"] == 94551140
    for row in evidence["profiles"]:
        preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=row["profileId"])
        metadata, decoded = preset.profile, row["decoded"]
        assert preset.key.value == f"v4-ufg-{row['variant']}-pool"
        assert metadata.adapter.implementation == decoded["adapter"]["implementation"]
        assert metadata.adapter.code_hash == decoded["adapter"]["codeHash"]
        assert metadata.registration.adapter_id == row["adapterId"]
        assert metadata.topology.hook_creation_code_hash == decoded["profileTopology"]["hookCreationCodeHash"]
        assert preset.bound_hook.creation_code.hex() == row["creationCode"][2:]
        assert "0x" + keccak(preset.bound_hook.creation_code).hex() == metadata.topology.hook_creation_code_hash
        assert hash_lifecycle_profile(metadata.envelope) == row["profileId"]
        assert hash_launch_dependencies(chain_id=4663, core=CORE, registry=evidence["registry"],
            registrar=metadata.adapter.implementation, graph=metadata.envelope.graph) == metadata.registration.dependency_digest
        old_key = KnownLifecycleProfile.V4_FIXED_FEE_POOL if row["variant"] == "fixed" else KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL
        assert preset.bound_hook.creation_code == get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=old_key).bound_hook.creation_code
        assert is_ufg_profile(row["profileId"]) and is_ufg_profile(bytes.fromhex(row["profileId"][2:]))
    assert not is_ufg_profile(HASH)


@pytest.mark.parametrize("mode,batches,kinds", [("atomic", None, ["atomic"]), ("staged", [1], ["begin", "prepare", "activate"])])
def test_unsigned_transactions_have_proxy_target_principal_value_and_exact_abi(mode, batches, kinds):
    plan = request()
    funding = LifecycleAssetFunding(QUOTE, 19, 1, QUOTE, 19, ZERO, b"")
    plan = replace(plan, funding=(funding,))
    txs = build_ufg_launch_transactions(plan, config=terms(), mode=mode, preparation_batches=batches)
    assert [tx.kind for tx in txs] == kinds
    assert [tx.value for tx in txs] == [19] + [0] * (len(txs) - 1)
    proxy = Web3().eth.contract(abi=UFG_LAUNCH_PROXY_V1_ABI)
    for tx in txs:
        assert tx.to == UFG_LAUNCH_PROXY_ADDRESS and tx.from_address.lower() == PRINCIPAL.lower()
        function, values = proxy.decode_function_input(tx.data)
        assert values["plan"]["nonce"] == plan.nonce
        if tx.kind in {"atomic", "begin"}:
            assert values["config"]["reserveAssets"] == [QUOTE]
        assert "nonce" not in tx.as_transaction() and tx.gas_limit is None
    assert proxy.decode_function_input(build_ufg_cancel_transaction(plan).data)[0].fn_name == "cancelLaunch"


def test_approvals_sum_direct_and_swap_pulls_per_asset_excluding_native():
    plan = replace(request(), funding=(
        LifecycleAssetFunding(QUOTE, 11, 0, QUOTE, 11, ZERO, b""),
        LifecycleAssetFunding(OTHER, 7, 2, QUOTE, 13, PRINCIPAL, b"a"),
        LifecycleAssetFunding(OTHER, 3, 1, OTHER, 3, ZERO, b""),
        LifecycleAssetFunding(OTHER, 5, 2, ZERO, 5, PRINCIPAL, b"b"),
    ))
    approvals = build_ufg_approvals(plan)
    assert len(approvals) == 1
    assert approvals[0].to == QUOTE and approvals[0].from_address.lower() == PRINCIPAL.lower()
    spender, amount = abi_decode(["address", "uint256"], bytes.fromhex(approvals[0].data[10:]))
    assert spender.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() and amount == 24


def test_public_planner_rejects_missing_config_and_wrong_principal_before_rpc():
    def forbidden(*_):
        raise AssertionError("invalid UFG identity must be rejected before RPC")
    client = SimpleNamespace(provider=SimpleNamespace(make_request=forbidden))
    for options, code in (({}, "INVALID_UFG_PLAN"), ({"ufg": terms(), "account": TOKEN}, "ACCOUNT_MISMATCH")):
        with pytest.raises(LifecyclePlanningError) as failure:
            plan_launch(client, request(), account=options.pop("account", PRINCIPAL), mode="atomic", **options)
        assert failure.value.code == code
    with pytest.raises(LifecyclePlanningError) as failure:
        asyncio.run(prepare_and_plan_lifecycle_launch(client, request(), account=TOKEN, mode="staged", ufg=terms()))
    assert failure.value.code == "ACCOUNT_MISMATCH"


def test_upstream_abi_exports_are_complete_consumer_interfaces():
    assert {entry.get("name") for entry in UFG_TREASURY_FACTORY_V1_ABI} >= {"treasuryOf", "validateMarketLaunchConfig", "getOrCreate"}
    assert {entry.get("name") for entry in UFG_BLACK_MARKET_FACTORY_ABI} >= {"deploy", "predict"}
    assert {entry.get("name") for entry in UFG_BLACK_MARKET_TREASURY_ABI} >= {"collect", "redeem", "redeemSkipping", "assets"}


class UfgRpcScenario(RecordingRpcScenario):
    """Exercise principal funding and the actual proxy ABI through native proof."""

    def __init__(self, plan, **options):
        super().__init__(plan, **options)
        self.allowance = 0
        self.config_hash = hash_ufg_launch_config(terms())
        self.principal = PRINCIPAL
        self.treasury = ZERO

    def command(self, transaction):
        calldata = bytes.fromhex(transaction["data"][2:])
        for name, kind in (("launchAtomic", "atomic"), ("beginLaunch", "begin"),
                           ("prepareMarkets", "prepare"), ("activateLaunch", "activate"), ("cancelLaunch", "cancel")):
            selector, types = function_signature(name, UFG_LAUNCH_PROXY_V1_ABI)
            if calldata[:4] == selector:
                assert transaction["from"].lower() == PRINCIPAL.lower()
                assert transaction["to"].lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()
                from black_market_sdk import LAUNCH_PLAN_COMPONENTS_V1, launch_plan_from_dict
                from black_market_sdk.lifecycle import _json_tuple
                arguments = abi_decode(types, calldata[4:])
                self.plan = launch_plan_from_dict(_json_tuple(LAUNCH_PLAN_COMPONENTS_V1, arguments[0]))
                return kind, arguments
        return super().command(transaction)

    def make_request(self, method, params):
        if method in {"eth_getTransactionCount", "eth_getBalance"}:
            assert params[0].lower() == PRINCIPAL.lower()
        if method == "eth_call":
            transaction = params[0]
            target = transaction["to"].lower()
            data = bytes.fromhex(transaction["data"][2:])
            if target == get_launch_addresses(4663).token_factory.lower():
                return {"result": "0x" + abi_encode(["address"], [TOKEN]).hex()}
            if target == UFG_TREASURY_FACTORY_ADDRESS.lower():
                assert data[:4] == keccak(text="treasuryOf(address)")[:4]
                return {"result": "0x" + abi_encode(["address"], [self.treasury]).hex()}
            if target == UFG_LAUNCH_PROXY_ADDRESS.lower():
                if data[:4] == keccak(text="launchConfigHash(bytes32)")[:4]:
                    return {"result": self.config_hash}
                if data[:4] == keccak(text="principalOf(bytes32)")[:4]:
                    return {"result": "0x" + abi_encode(["address"], [self.principal]).hex()}
            if target == QUOTE.lower():
                if data[:4] == keccak(text="balanceOf(address)")[:4]:
                    assert abi_decode(["address"], data[4:])[0].lower() == PRINCIPAL.lower()
                    return {"result": "0x" + abi_encode(["uint256"], [10**24]).hex()}
                if data[:4] == keccak(text="allowance(address,address)")[:4]:
                    owner, spender = abi_decode(["address", "address"], data[4:])
                    assert owner.lower() == PRINCIPAL.lower() and spender.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()
                    return {"result": "0x" + abi_encode(["uint256"], [self.allowance]).hex()}
            if target == "0x" + "00" * 19 + "c8":
                destination, creation, calldata = abi_decode(["address", "bool", "bytes"], data[4:])
                assert destination.lower() in {UFG_LAUNCH_PROXY_ADDRESS.lower(), QUOTE.lower()}
                assert not creation and calldata and transaction["from"].lower() == PRINCIPAL.lower()
                return {"result": "0x" + abi_encode(["uint64", "uint256", "uint256"], [self.poster_gas, 1, 0]).hex()}
        return super().make_request(method, params)


def test_public_ufg_native_planner_and_build_next_preserve_principal_and_config():
    from black_market_sdk import build_next_transaction
    plan = replace(request(), funding=(LifecycleAssetFunding(QUOTE, 11, 0, QUOTE, 11, ZERO, b""),))
    provider = UfgRpcScenario(plan)
    client = SimpleNamespace(provider=provider)
    planned = plan_launch(client, plan, account=PRINCIPAL, mode="atomic", ufg=terms())
    assert planned.assessment.ready
    assert planned.ufg == terms() and planned.ufg_config_hash == hash_ufg_launch_config(terms())
    assert planned.approvals[0].spender == UFG_LAUNCH_PROXY_ADDRESS
    assert planned.mode == "staged"
    assert [transaction.kind for transaction in planned.transactions] == ["approve", "begin", "prepare", "activate"]
    provider.allowance = 11
    next_transaction = build_next_transaction(client, planned)
    assert next_transaction.kind == "begin" and next_transaction.to == UFG_LAUNCH_PROXY_ADDRESS
    assert next_transaction.from_address.lower() == PRINCIPAL.lower()
    with pytest.raises(LifecyclePlanningError) as failure:
        build_next_transaction(client, replace(planned, ufg=replace(terms(), exit_fee_bps=101)))
    assert failure.value.code == "PLAN_MUTATED"


def test_started_ufg_recovery_does_not_read_proxy_binding_guards():
    from test_lifecycle_read_counts import CountingTransport
    provider = UfgRpcScenario(request(), confirmed_phase=1, latest_phase=1)
    provider.config_hash, provider.principal = "0x" + "ff" * 32, TOKEN
    client = CountingTransport(provider)
    planned = plan_launch(client, request(), account=PRINCIPAL, mode="staged", ufg=terms())
    assert planned.assessment.ready
    forbidden = {keccak(text=signature)[:4].hex()
        for signature in ("principalOf(bytes32)", "launchConfigHash(bytes32)")}
    assert not any(call["method"] == "eth_call" and call["params"][0]["data"][2:10] in forbidden
        for call in client.calls)


@pytest.mark.parametrize("mode", ["atomic", "staged"])
def test_original_proxy_receipt_calldata_binds_treasury_config_without_mapping_reads(mode):
    from black_market_sdk.lifecycle import _transaction_matches_receipt
    from black_market_sdk.lifecycle_rpc import LaunchBlock
    plan, config = request(), terms()
    transaction = build_ufg_launch_transactions(plan, config=config, mode=mode)[0].as_transaction()
    block = LaunchBlock(51, "0x" + "ab" * 32, plan.deadline - 100, 30_000_000, 1)
    assert _transaction_matches_receipt(None, plan, transaction, block, hash_ufg_launch_config(config))
    changed = replace(config, exit_fee_bps=config.exit_fee_bps + 1)
    assert not _transaction_matches_receipt(None, plan, transaction, block, hash_ufg_launch_config(changed))


def test_treasury_read_distinguishes_zero_and_checksum_address():
    from black_market_sdk import read_ufg_treasury
    provider = UfgRpcScenario(request())
    client = SimpleNamespace(provider=provider)
    assert read_ufg_treasury(client, TOKEN) is None
    provider.treasury = PRINCIPAL.lower()
    assert read_ufg_treasury(client, TOKEN) == PRINCIPAL


@pytest.mark.parametrize("identity", [UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID])
def test_unified_ufg_preparation_mines_bound_salts_and_proves_proxy_transactions(identity):
    config = bound_config(identity)
    config = replace(config, positions=(replace(config.positions[0], tick_lower=-887220, tick_upper=0),))
    plan = mutate_market(request(bound=True, identity=identity), config=encode_lifecycle_pool_bound_v4_market_config(config))
    provider = UfgRpcScenario(plan)
    planned = asyncio.run(prepare_and_plan_lifecycle_launch(SimpleNamespace(provider=provider),
        plan, account=PRINCIPAL, mode="atomic", ufg=terms()))
    assert planned.assessment.ready and planned.ufg_config_hash == hash_ufg_launch_config(terms())
    assert len(planned.hook_deployments) == 1
    assert int(planned.hook_deployments[0].predicted_hook, 16) & 16383 == 6908
    decoded = decode_lifecycle_pool_bound_v4_market_config(planned.plan.markets[0].config)
    assert decoded.ufg_launch_config == terms()
    assert planned.transactions[0].to == UFG_LAUNCH_PROXY_ADDRESS
    assert planned.transactions[0].from_address.lower() == PRINCIPAL.lower()


def test_validator_accepts_quote_oracle_and_seven_reserve_boundary():
    from black_market_sdk import decode_lifecycle_abyss_market_config
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=KnownLifecycleProfile.ABYSS_3)
    native = decode_lifecycle_abyss_market_config(native_market().config)
    oracle = replace(native, profile=3, oracle_config_id=(55).to_bytes(32, "big"))
    plan = mutate_market(request(), profile_id=preset.profile.id, config=encode_lifecycle_abyss_market_config(oracle))
    validate_ufg_launch_plan(plan, terms(), TOKEN)
    reserves = tuple("0x" + f"{value:040x}" for value in range(1, 8))
    config = UfgLaunchConfig(10000, 2000, reserves)
    plan = replace(request(), markets=tuple(replace(native_market(), quote_asset=asset) for asset in reserves),
        fee_assets=build_ufg_fee_asset_policies(TOKEN, reserves))
    plan = replace(plan, token=replace(plan.token, supply=7 * 10**24))
    validate_ufg_launch_plan(plan, config, TOKEN)


@pytest.mark.parametrize("change", [{"lp_fee_pips": 1}, {"fee_mode": 0}, {"ufg_launch_config": None}])
def test_v4_review_and_codec_reject_invalid_ufg_economics(change):
    from black_market_sdk import validate_v4_lifecycle_market
    plan = request(bound=True)
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, profile_id=UFG_FIXED_PROFILE_ID)
    config = replace(bound_config(), **change)
    for operation in (
        lambda: encode_lifecycle_pool_bound_v4_market_config(config),
        lambda: validate_v4_lifecycle_market(market=plan.markets[0], config=config, profile=preset.profile, token=TOKEN),
    ):
        with pytest.raises(LifecyclePlanningError) as failure:
            operation()
        assert failure.value.code == "INVALID_UFG_PLAN"
