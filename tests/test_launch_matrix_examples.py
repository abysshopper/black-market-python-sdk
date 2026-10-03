"""Behavioral coverage for the offline current-launch scenario matrix."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import http.client
import runpy
import socket
import urllib.request

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
import pytest

from black_market_sdk.abyss import AbyssPoolProfile, TokenKind
from black_market_sdk.launch import (
    AtomicLaunchPoolRecipeInput,
    FeeDisposition,
    LaunchFeeAssetMode,
    LaunchFeeDestination,
    derive_atomic_launch_pool_recipe,
    get_launch_template,
)
from black_market_sdk.unified import (
    LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2,
    LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2,
    LaunchPoolKind,
    UniswapV4PoolConfigV2,
    build_unified_launch_calldata,
    launch_pool_type_id,
)
from examples.launch_matrix import (
    build_scenario_request,
    iter_launch_scenarios,
    v4_lighthouse_liquidity_shares,
)


CREATOR = "0x1000000000000000000000000000000000000001"
PAIRED_TOKEN = "0x2000000000000000000000000000000000000002"

_LAUNCH_REQUEST_TYPE = (
    "(address,bytes32,bytes32,uint32,(bytes32,bytes,string,string,uint8,uint256),"
    "bytes,(uint256,uint256,uint160),(uint16,uint16,uint16),(uint16,uint16,uint16),uint256)"
)
_ABYSS_POOL_CONFIG_TYPE = "(address,bool,uint8,uint24,bytes32,int24,uint128,uint256,uint256)"
_V4_POOL_CONFIG_TYPE = "(address,uint8,bytes32,int24,int24,uint160,uint128,uint256,uint24,bool)"


def _request_for(scenario, **overrides):
    return build_scenario_request(
        scenario,
        creator=CREATOR,
        paired_token=PAIRED_TOKEN,
        **overrides,
    )


def _capability_key(scenario):
    return (
        scenario.template_id,
        scenario.profile,
        scenario.token_kind,
        scenario.dual_rewards,
    )


def _expected_capability_rows():
    rows = set()
    burnable = (TokenKind.BURNABLE, None)
    for profile in (AbyssPoolProfile.STANDARD_ORACLE, AbyssPoolProfile.QUOTE_ORACLE):
        rows.add(("standard", profile, *burnable))
    for template_id, profile in (
        ("quote-staking", AbyssPoolProfile.QUOTE_ORACLE),
        ("dual-staking", AbyssPoolProfile.STANDARD_ORACLE),
        ("fee-burn", AbyssPoolProfile.QUOTE_ORACLE),
    ):
        rows.add((template_id, profile, *burnable))
    rows.add(
        (
            "quote-dividends",
            AbyssPoolProfile.QUOTE_ORACLE,
            TokenKind.HOLDER_DIVIDEND,
            False,
        )
    )
    rows.add(
        (
            "dual-dividends",
            AbyssPoolProfile.STANDARD_ORACLE,
            TokenKind.HOLDER_DIVIDEND,
            True,
        )
    )
    return rows


def _disposition_tuple(disposition: FeeDisposition) -> tuple[int, int, int]:
    return disposition.owner_bps, disposition.rewards_bps, disposition.burn_bps


def _active_fee_assets(template_id: str, profile: AbyssPoolProfile) -> tuple[bool, bool]:
    template = get_launch_template(template_id)
    launched = (
        template.fee_asset_mode in (LaunchFeeAssetMode.BOTH, LaunchFeeAssetMode.LAUNCHED_ONLY)
        or (
            template.fee_asset_mode is LaunchFeeAssetMode.PROFILE
            and profile is AbyssPoolProfile.STANDARD_ORACLE
        )
    )
    paired = template.fee_asset_mode in (
        LaunchFeeAssetMode.PROFILE,
        LaunchFeeAssetMode.PAIRED_ONLY,
        LaunchFeeAssetMode.BOTH,
    )
    return launched, paired


def _destinations(disposition: FeeDisposition) -> int:
    return (
        (int(LaunchFeeDestination.OWNER) if disposition.owner_bps else 0)
        | (int(LaunchFeeDestination.REWARDS) if disposition.rewards_bps else 0)
        | (int(LaunchFeeDestination.BURN) if disposition.burn_bps else 0)
    )


def _v4_config_from_request(request) -> UniswapV4PoolConfigV2:
    values = abi_decode([_V4_POOL_CONFIG_TYPE], request.pool_config)[0]
    return UniswapV4PoolConfigV2(
        paired_token=values[0],
        profile=AbyssPoolProfile(values[1]),
        oracle_config_id=values[2],
        tick_lower=values[3],
        tick_upper=values[4],
        sqrt_price_x96=values[5],
        liquidity=values[6],
        launched_token_amount_maximum=values[7],
        abyss_fee_pips=values[8],
        external_liquidity_disabled=values[9],
    )


def test_current_catalog_covers_each_capability_valid_template_profile_configuration():
    current = iter_launch_scenarios()

    assert {scenario.pool_kind for scenario in current} == {
        LaunchPoolKind.ABYSS,
        LaunchPoolKind.UNISWAP_V4_V3,
    }
    expected_rows = _expected_capability_rows()
    for pool_kind in (LaunchPoolKind.ABYSS, LaunchPoolKind.UNISWAP_V4_V3):
        assert {
            _capability_key(scenario)
            for scenario in current
            if scenario.pool_kind is pool_kind
        } == expected_rows
    assert Counter(scenario.pool_kind for scenario in current) == Counter(
        {
            LaunchPoolKind.ABYSS: 17,
            LaunchPoolKind.UNISWAP_V4_V3: 17,
        }
    )


def test_generic_requests_encode_registered_token_configs_and_exact_fee_policies():
    scenarios = iter_launch_scenarios()
    requests = [_request_for(scenario) for scenario in scenarios]

    assert len({scenario.scenario_id for scenario in scenarios}) == len(scenarios)
    assert len({request.token.name for request in requests}) == len(requests)
    assert len({request.token.symbol for request in requests}) == len(requests)
    assert all(len(request.token.symbol) <= 12 for request in requests)
    replayed_requests = [_request_for(scenario) for scenario in scenarios]
    assert [(request.token.name, request.token.symbol) for request in requests] == [
        (request.token.name, request.token.symbol) for request in replayed_requests
    ]

    for scenario, request in zip(scenarios, requests):
        decoded = abi_decode([_LAUNCH_REQUEST_TYPE], build_unified_launch_calldata(request)[4:])[0]
        assert decoded[1] == launch_pool_type_id(scenario.pool_kind)
        assert decoded[2] == get_launch_template(scenario.template_id).template_id
        assert decoded[7] == _disposition_tuple(scenario.launched_token_fees)
        assert decoded[8] == _disposition_tuple(scenario.paired_token_fees)

        if scenario.token_kind is TokenKind.BURNABLE:
            assert decoded[4][0] == LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2
            assert decoded[4][1] == b""
        else:
            assert decoded[4][0] == LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2
            assert decoded[4][1] == abi_encode(["bool"], [scenario.dual_rewards])
        template = get_launch_template(scenario.template_id)
        assert scenario.token_kind is template.token_kinds[0]
        assert scenario.dual_rewards == (
            template.id == "dual-dividends"
            if scenario.token_kind is TokenKind.HOLDER_DIVIDEND
            else None
        )

        if scenario.pool_kind is LaunchPoolKind.ABYSS:
            pool = abi_decode([_ABYSS_POOL_CONFIG_TYPE], decoded[5])[0]
            assert pool[2] == int(scenario.profile)
        else:
            pool = abi_decode([_V4_POOL_CONFIG_TYPE], decoded[5])[0]
            assert pool[1] == int(scenario.profile)

        launched_active, paired_active = _active_fee_assets(
            scenario.template_id, scenario.profile
        )
        assert sum(_disposition_tuple(scenario.launched_token_fees)) == (
            10_000 if launched_active else 0
        )
        assert sum(_disposition_tuple(scenario.paired_token_fees)) == (
            10_000 if paired_active else 0
        )
        assert _destinations(scenario.launched_token_fees) & ~int(
            template.launched_token_destinations
        ) == 0
        assert _destinations(scenario.paired_token_fees) & ~int(
            template.paired_token_destinations
        ) == 0
        assert scenario.paired_token_fees.burn_bps == 0


def test_representative_fee_variants_cover_each_distinct_supported_policy():
    current = iter_launch_scenarios()

    quote_staking_policies = {
        (
            _disposition_tuple(scenario.launched_token_fees),
            _disposition_tuple(scenario.paired_token_fees),
        )
        for scenario in current
        if scenario.template_id == "quote-staking"
    }
    assert quote_staking_policies == {
        ((0, 0, 0), (10_000, 0, 0)),
        ((0, 0, 0), (0, 10_000, 0)),
        ((0, 0, 0), (6_000, 4_000, 0)),
    }

    dual_policies = {
        (
            _disposition_tuple(scenario.launched_token_fees),
            _disposition_tuple(scenario.paired_token_fees),
        )
        for scenario in current
        if scenario.template_id in {"dual-staking", "dual-dividends"}
    }
    assert ((5_000, 3_000, 2_000), (6_000, 4_000, 0)) in dual_policies
    assert ((0, 10_000, 0), (0, 10_000, 0)) in dual_policies

    fee_burn_scenarios = [
        scenario for scenario in current if scenario.template_id == "fee-burn"
    ]
    assert {
        (_disposition_tuple(scenario.launched_token_fees), _disposition_tuple(scenario.paired_token_fees))
        for scenario in fee_burn_scenarios
    } == {((0, 0, 10_000), (0, 0, 0))}


def test_v4_split_uses_reduced_config_liquidity_and_preserves_integer_rounding():
    scenario = next(
        item
        for item in iter_launch_scenarios()
        if item.pool_kind is LaunchPoolKind.UNISWAP_V4_V3
        and item.template_id == "standard"
        and item.profile is AbyssPoolProfile.STANDARD_ORACLE
        and item.token_kind is TokenKind.BURNABLE
    )
    request = _request_for(scenario)
    config = _v4_config_from_request(request)
    template = get_launch_template(scenario.template_id)
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=2_500 * 10**18,
            target_market_cap_usd_x18=5_000 * 10**18,
            launched_token_is_quote=template.launched_token_is_quote,
            fee=3_000,
        )
    )

    v4_liquidity, abyss_lighthouse_liquidity = v4_lighthouse_liquidity_shares(config)
    expected_reduced_liquidity = recipe.liquidity - (
        2 if recipe.liquidity % 100 == 0 else 1
    )
    assert config.liquidity == expected_reduced_liquidity
    assert abyss_lighthouse_liquidity == (config.liquidity * 100) // 10_000
    assert v4_liquidity == config.liquidity - abyss_lighthouse_liquidity
    assert v4_liquidity + abyss_lighthouse_liquidity == config.liquidity


def test_initial_buy_guards_follow_template_orientation_and_native_is_abyss_only():
    current = iter_launch_scenarios()
    abyss_standard = next(
        item
        for item in current
        if item.pool_kind is LaunchPoolKind.ABYSS
        and item.template_id == "standard"
        and item.profile is AbyssPoolProfile.STANDARD_ORACLE
        and item.token_kind is TokenKind.BURNABLE
    )
    fee_burn = next(
        item
        for item in current
        if item.pool_kind is LaunchPoolKind.ABYSS
        and item.template_id == "fee-burn"
        and item.token_kind is TokenKind.BURNABLE
    )
    v4_standard = next(
        item
        for item in current
        if item.pool_kind is LaunchPoolKind.UNISWAP_V4_V3
        and item.template_id == "standard"
        and item.profile is AbyssPoolProfile.STANDARD_ORACLE
        and item.token_kind is TokenKind.BURNABLE
    )

    standard_recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=2_500 * 10**18,
            target_market_cap_usd_x18=5_000 * 10**18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    fee_burn_recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=2_500 * 10**18,
            target_market_cap_usd_x18=5_000 * 10**18,
            launched_token_is_quote=True,
            fee=3_000,
        )
    )
    erc20_buy = _request_for(abyss_standard, initial_buy_amount=10**18)
    native_buy = _request_for(abyss_standard, native_buy_amount=10**18)
    quote_buy = _request_for(fee_burn, initial_buy_amount=10**18)

    assert erc20_buy.initial_buy.sqrt_price_limit_x96 < standard_recipe.launch_sqrt_price_x96
    assert native_buy.initial_buy.paired_token_amount_in == 0
    assert native_buy.initial_buy.sqrt_price_limit_x96 < standard_recipe.launch_sqrt_price_x96
    assert quote_buy.initial_buy.sqrt_price_limit_x96 > fee_burn_recipe.launch_sqrt_price_x96
    with pytest.raises(ValueError):
        _request_for(v4_standard, native_buy_amount=1)


def test_import_and_default_offline_cli_do_not_open_network_connections(monkeypatch, capsys):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("offline matrix attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden_network)

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "examples" / "launch_matrix.py"),
        run_name="offline_launch_matrix_import",
    )
    assert namespace["main"]([]) == 0
    output_rows = set(capsys.readouterr().out.splitlines())
    assert {
        scenario.scenario_id for scenario in namespace["iter_launch_scenarios"]()
    } <= output_rows
