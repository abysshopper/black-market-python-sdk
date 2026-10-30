"""Replay only local math/encoding over preserved Node gas observations, never forks."""
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from black_market_sdk import (
    RAETO_QUOTE_ASSETS, ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT, ROBINHOOD_WETH,
    KnownLifecycleProfile, LifecycleAssetFunding, LifecycleFeeAssetPolicy,
    LifecycleInitialBuy, LifecycleV4Position, build_launch_transactions,
    build_next_transaction, compute_lifecycle_launch_gas_bounds, compute_lifecycle_opening_buys,
    decode_lifecycle_pool_bound_v4_market_config, derive_launch_position_ranges,
    encode_lifecycle_pool_bound_v4_market_config,
    encode_launch_plan, hash_launch_plan, is_raeto_profile, launch_plan_to_dict, parse_launch_plan,
    prepare_and_plan_lifecycle_launch, validate_raeto_launch_plan,
)
from black_market_sdk.lifecycle import _prepare_gas_estimate, _LIFECYCLE_CAPACITY_PADDING_BPS
from black_market_sdk.lifecycle_rpc import hex_bytes
from test_lifecycle_profiled_gas import assert_no_execution, orient_opening_bands
from test_lifecycle_read_counts import CountingTransport
from test_lifecycle_read_reduction import ResumeScenario

FIXTURES = Path(__file__).parent / "fixtures"
MATRIX_PATH = FIXTURES / "template-gas-matrix.json"
MATRIX = json.loads(MATRIX_PATH.read_text())
PARITY = json.loads((FIXTURES / "template-gas-node-parity.json").read_text())
KEYS = (KnownLifecycleProfile.V4_RAETO_FIXED_POOL, KnownLifecycleProfile.V4_RAETO_DYNAMIC_POOL,
    KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL, KnownLifecycleProfile.V4_AUTO_COMPOUND_DYNAMIC_POOL)
CEILING = ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT


def padded(value):
    return (value * (10000 + _LIFECYCLE_CAPACITY_PADDING_BPS) + 9999) // 10000


def test_preserved_matrix_covers_all_templates_dimensions_without_duplicate_executions():
    assert MATRIX["chainId"] == 4663 and MATRIX["forkBlock"] > 100_721_302
    assert len(bytes.fromhex(MATRIX["forkBlockHash"][2:])) == 32
    assert "anvil" in MATRIX["clientVersion"].lower()
    assert "NOT native ArbOS" in MATRIX["caveat"]
    assert MATRIX["archiveProvider"] == "alchemy" and MATRIX["retainedPublicArchiveCases"] == 59
    assert len(MATRIX["cases"]) == len(PARITY["cases"]) == 84
    assert hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest() == PARITY["matrixSha256"]
    signatures = {tuple(row["scenario"].get(key, 1) for key in
        ("key", "marketCount", "positions", "buyCount", "quoteIndex", "mode", "feeMode")) for row in MATRIX["cases"]}
    assert len(signatures) == 84
    for key in KEYS:
        rows = [row["scenario"] for row in MATRIX["cases"] if row["scenario"]["key"] == key]
        for positions in (1, 8, 32):
            for mode in ("atomic", "staged"):
                assert any(row["marketCount"] == 1 and row["positions"] == positions and row["mode"] == mode for row in rows)
        for buys in (0, 1, 4):
            for mode in ("atomic", "staged"):
                assert any(row["marketCount"] == 1 and row["buyCount"] == buys and row["mode"] == mode for row in rows)
        for positions, buys in ((8, 0), (32, 4)):
            assert any(row["marketCount"] == 2 and row["positions"] == positions and row["buyCount"] == buys and row["mode"] == "staged" for row in rows)
        if key in KEYS[:2]:
            assert {row["quoteIndex"] for row in rows if row["marketCount"] == 1} == {0, 1}


@pytest.mark.parametrize("index,row", list(enumerate(MATRIX["cases"])),
    ids=[f"{index + 1}/{row['scenario']['key']}/{row['scenario']['mode']}/{row['scenario']['positions']}/{row['scenario']['buyCount']}"
        for index, row in enumerate(MATRIX["cases"])])
def test_every_observation_fits_compute_forwarding_margin_and_exact_node_bounds(index, row):
    plan = parse_launch_plan(json.dumps(row["plan"]))
    expected = PARITY["cases"][index]
    assert hash_launch_plan(plan) == expected["planHash"]
    assert hashlib.sha256(encode_launch_plan(plan)).hexdigest() == expected["planEncodingSha256"]
    for headroom in expected["bounds"]:
        bounds = compute_lifecycle_launch_gas_bounds(plan, predicted_token=row["predictedToken"], headroom_bps=headroom["headroomBps"])
        assert bounds is not None
        assert bounds.atomic_compute_gas_upper_bound == (int(headroom["atomicComputeGasUpperBound"]) if "atomicComputeGasUpperBound" in headroom else None)
        assert bounds.activation_compute_gas_upper_bound == int(headroom["activationComputeGasUpperBound"])
    bounds = compute_lifecycle_launch_gas_bounds(plan, predicted_token=row["predictedToken"])
    commands = build_launch_transactions(plan, mode=row["scenario"]["mode"],
        preparation_batches=[transaction["marketCount"] for transaction in row["transactions"] if transaction["kind"] == "prepare"]
        if row["scenario"]["mode"] == "staged" else None)
    assert len(commands) == len(row["transactions"]) == len(expected["transactions"])
    for command, observation, node in zip(commands, row["transactions"], expected["transactions"]):
        assert command.kind == observation["kind"] == node["kind"]
        assert command.calldata_bytes == observation["calldataBytes"]
        assert hashlib.sha256(bytes.fromhex(command.data[2:])).hexdigest() == node["calldataSha256"]
        if command.kind == "atomic":
            bound = bounds.atomic_compute_gas_upper_bound
        elif command.kind == "activate":
            bound = bounds.activation_compute_gas_upper_bound
        elif command.kind == "prepare":
            preparation = sum(_prepare_gas_estimate(plan, market) for market in
                range(command.first_market, command.first_market + command.market_count))
            assert preparation == int(node["preparationGas"])
            bound = padded(preparation + 24 * command.calldata_bytes)
        else:
            bound = CEILING  # Begin has no invented component capacity estimate.
        used, forwarding = int(observation["gasUsed"]), int(observation["minimumGasEstimate"])
        assert bound is not None and bound >= max(used, forwarding)
        assert bound * 100 >= used * 105
        if used <= CEILING and forwarding <= CEILING:
            assert observation["nativeCeilingCallSucceeded"]
        if observation["nativeCeilingCallSucceeded"]:
            assert bound <= CEILING
    quotes = compute_lifecycle_opening_buys(plan, predicted_token=row["predictedToken"])
    assert len(quotes) == len(expected["initialBuys"])
    for quote, node in zip(quotes, expected["initialBuys"]):
        assert (quote.buy_index, quote.market_index, quote.quote_amount_in, quote.quote_spent,
            quote.token_out, quote.sqrt_price_after_x96) == tuple(int(node[name]) for name in
                ("buyIndex", "marketIndex", "quoteAmountIn", "quoteSpent", "tokenOut", "sqrtPriceAfterX96"))
    for market in plan.markets:
        config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
        assert hex_bytes(encode_lifecycle_pool_bound_v4_market_config(config)) == hex_bytes(market.config)
    assert row["observed"]["phase"] == "Active"
    assert row["observed"]["positionCount"] == row["scenario"]["positions"]
    assert row["observed"]["opening"]["exactBuyOutputsMatched"] == len(plan.buys)
    if is_raeto_profile(plan.markets[0].profile_id):
        validate_raeto_launch_plan(plan)
        assert len(row["observed"]["collectors"]) == len(plan.markets)
        for collector in row["observed"]["collectors"]:
            market = next(market for market in plan.markets if market.quote_asset.lower() == collector["quoteAsset"].lower())
            assert collector["shareBps"] == decode_lifecycle_pool_bound_v4_market_config(market.config).raeto_launch_config.share_bps
    else:
        assert row["observed"]["opening"]["compoundDeferredForOpening"] == len(plan.buys)


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("quote", RAETO_QUOTE_ASSETS)
@pytest.mark.parametrize("mode", ["atomic", "staged"])
def test_new_templates_plan_and_continue_with_native_ceiling_without_execution(key, quote, mode):
    row = next(row for row in MATRIX["cases"] if row["scenario"]["key"] == key
        and row["scenario"]["marketCount"] == row["scenario"]["positions"] == 1 and row["scenario"]["buyCount"] == 0)
    draft = parse_launch_plan(json.dumps(row["plan"]))
    # This separate mocked transport smoke uses unit-price bands, not fork replay.
    config = decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config)
    config = replace(config, sqrt_price_x96=1 << 96,
        positions=tuple(replace(position, tick_lower=0, tick_upper=887220) for position in config.positions))
    draft = replace(draft, markets=(replace(draft.markets[0], quote_asset=quote,
        config=encode_lifecycle_pool_bound_v4_market_config(config)),),
        fee_assets=(LifecycleFeeAssetPolicy(quote, 10000, 0, 0),))
    provider = ResumeScenario(draft)
    client = CountingTransport(provider)
    draft = orient_opening_bands(draft, client, provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft, account=draft.creator, mode=mode))
    assert launch.assessment.ready and launch.mode == mode
    assert launch.assessment.gas_profile.version == "robinhood-4663-capacity-v5"
    assert launch.transactions[0].gas_limit == CEILING
    assert len(launch.hook_deployments) == 1
    provider.plan = launch.plan
    command = build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0])
    assert command.assessment.ready and command.gas_limit == CEILING
    assert command.as_transaction() == launch.transactions[0].as_transaction()
    assert_no_execution(client)


@pytest.mark.parametrize("key", KEYS[:2])
@pytest.mark.parametrize("position_count,selected", [(6, "atomic"), (8, "staged")])
def test_raeto_dividends_native_conversion_uses_atomic_budget_until_positions_exceed_it(key, position_count, selected):
    row = next(row for row in MATRIX["cases"] if row["scenario"]["key"] == key
        and row["scenario"]["marketCount"] == row["scenario"]["positions"] == 1 and row["scenario"]["buyCount"] == 0)
    draft = parse_launch_plan(json.dumps(row["plan"]))
    quote = next(asset for asset in RAETO_QUOTE_ASSETS if asset != ROBINHOOD_WETH)
    config = decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config)
    # Synthetic unit-price bands exercise planning, not the retained fork matrix.
    ranges = derive_launch_position_ranges(launch_sqrt_price_x96=1 << 96,
        launched_token_is_quote=True, token_budget=draft.token.supply, tick_spacing=config.tick_spacing,
        venue="uniswap-v4", distribution_preset="staircase", position_count=position_count,
        position_gaps="adjacent")
    config = replace(config, sqrt_price_x96=1 << 96, positions=tuple(LifecycleV4Position(
        position.tick_lower, position.tick_upper, position.liquidity, bytes(32), position.max_token_amount)
        for position in ranges.positions))
    draft = replace(draft, token=replace(draft.token, reward_mode=2),
        markets=(replace(draft.markets[0], quote_asset=quote,
            config=encode_lifecycle_pool_bound_v4_market_config(config)),),
        fee_assets=(LifecycleFeeAssetPolicy(quote, 8000, 2000, 0),),
        funding=(LifecycleAssetFunding(quote, 10**14, 2, "0x" + "00" * 20, 10**16,
            "0x8888888888888888888888888888888888888888", b"conversion"),),
        buys=(LifecycleInitialBuy(0, 10**14, 1, draft.creator, 0),))
    provider = ResumeScenario(draft)
    client = CountingTransport(provider)
    draft = orient_opening_bands(draft, client, provider)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(client, draft,
        account=draft.creator, mode="atomic", buy_slippage_bps=50))
    assert launch.assessment.ready and launch.mode == selected
    bounds = launch.assessment.gas_profile.bounds
    assert (bounds.atomic_compute_gas_upper_bound <= CEILING) == (selected == "atomic")
    assert bounds.activation_compute_gas_upper_bound < CEILING
    assert launch.plan.buys[0].min_token_out == launch.assessment.gas_profile.initial_buys[0].token_out * 9950 // 10000 > 1
    assert launch_plan_to_dict(launch.plan)["funding"] == launch_plan_to_dict(draft)["funding"]
    assert launch.transactions[0].value == 10**16 and launch.transactions[0].gas_limit == CEILING
    provider.plan = launch.plan
    current = build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0])
    assert current.as_transaction() == launch.transactions[0].as_transaction()
    assert current.assessment.ready
    assert_no_execution(client)
