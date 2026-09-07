"""Parity tests for Atomic launch recipe derivation and calldata encoding."""

import pytest
from eth_utils import keccak

from black_market_sdk import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    AUCTION_SUPPLY,
    FEE_BURN_TEMPLATE_ID,
    LAUNCH_TEMPLATES,
    STANDARD_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchBuySqrtPriceLimitInput,
    AtomicLaunchInitialBuyEstimateInput,
    AtomicLaunchPoolRecipeInput,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    AtomicTokenConfig,
    AbyssPoolProfile,
    FeeDisposition,
    TokenKind,
    build_atomic_launch_calldata,
    derive_atomic_launch_buy_sqrt_price_limit_x96,
    derive_atomic_launch_pool_recipe,
    estimate_atomic_launch_initial_buy,
    get_launch_template,
    get_launch_template_by_hash,
    get_sqrt_ratio_at_tick,
    get_tick_at_sqrt_ratio,
)

PAIRED_TOKEN = "0x2000000000000000000000000000000000000000"
CREATOR = "0x1000000000000000000000000000000000000001"
PAIRED_USD_PRICE_X18 = 2_500 * 10**18
TARGET_MARKET_CAP_USD_X18 = 3_000 * 10**18


def test_template_ids_match_keccak_of_canonical_names():
    assert STANDARD_TEMPLATE_ID == keccak(text="black-market.standard")
    assert FEE_BURN_TEMPLATE_ID == keccak(text="black-market.fee-burn")
    assert get_launch_template("standard").template_id == STANDARD_TEMPLATE_ID
    assert get_launch_template_by_hash(STANDARD_TEMPLATE_ID).id == "standard"
    assert len(LAUNCH_TEMPLATES) == 6


def test_tick_math_bounds():
    # Canonical Uniswap v3 TickMath anchor: tick 0 → 2**96.
    assert get_sqrt_ratio_at_tick(0) == 1 << 96
    assert get_tick_at_sqrt_ratio(1 << 96) == 0
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(887_273)


def test_recipe_derivation_is_deterministic():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    assert recipe.paired_token_amount_maximum == 0
    assert recipe.launched_token_amount_maximum == AUCTION_SUPPLY
    assert recipe.liquidity > 0
    assert recipe.launch_tick % 60 == 0

    # Same inputs → identical recipe.
    again = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    assert again == recipe


def test_initial_buy_estimate_monotonic():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    small = estimate_atomic_launch_initial_buy(
        AtomicLaunchInitialBuyEstimateInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            liquidity=recipe.liquidity,
            paired_token_amount_in=10**18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    large = estimate_atomic_launch_initial_buy(
        AtomicLaunchInitialBuyEstimateInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            liquidity=recipe.liquidity,
            paired_token_amount_in=10**19,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    assert large.launched_token_amount_out > small.launched_token_amount_out


def test_buy_price_limit_moves_in_safe_direction():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    limit = derive_atomic_launch_buy_sqrt_price_limit_x96(
        AtomicLaunchBuySqrtPriceLimitInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            launched_token_is_quote=False,
        )
    )
    # Paired token is token0: input moves price down, so the guard sits below launch.
    assert limit < recipe.launch_sqrt_price_x96


def _standard_request(**overrides) -> AtomicLaunchRequest:
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    request = AtomicLaunchRequest(
        creator=CREATOR,
        template_id=STANDARD_TEMPLATE_ID,
        template_version=1,
        token=AtomicTokenConfig(
            kind=TokenKind.BURNABLE,
            name="Test Token",
            symbol="TEST",
            decimals=18,
            supply=AUCTION_SUPPLY,
        ),
        pool=AtomicPoolConfig(
            paired_token=PAIRED_TOKEN,
            launched_token_is_quote=False,
            profile=AbyssPoolProfile.QUOTE_ORACLE,
            fee=3_000,
            oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
            launch_tick=recipe.launch_tick,
            liquidity=recipe.liquidity,
            launched_token_amount_maximum=recipe.launched_token_amount_maximum,
            paired_token_amount_maximum=0,
        ),
        initial_buy=AtomicInitialBuy(
            paired_token_amount_in=0,
            launched_token_amount_out_minimum=0,
            sqrt_price_limit_x96=0,
        ),
        launched_token_fees=FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=0),
        paired_token_fees=FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
        deadline=1_800_000_000,
    )
    for key, value in overrides.items():
        object.__setattr__(request, key, value)
    return request


def test_build_calldata_encodes_deploy_and_launch():
    calldata = build_atomic_launch_calldata(_standard_request())
    selector = keccak(
        text="deployAndLaunch((address,bytes32,uint32,(uint8,string,string,uint8,uint256),"
        "(address,bool,uint8,uint24,bytes32,int24,uint128,uint256,uint256),"
        "(uint256,uint256,uint160),(uint16,uint16,uint16),(uint16,uint16,uint16),uint256))"
    )[:4]
    assert calldata[:4] == selector
    assert len(calldata) > 4 + 32 * 9


def test_build_calldata_rejects_invalid_requests():
    with pytest.raises(ValueError, match="creator"):
        build_atomic_launch_calldata(_standard_request(creator="0x0000000000000000000000000000000000000000"))

    bad_disposition = _standard_request(
        paired_token_fees=FeeDisposition(owner_bps=5_000, rewards_bps=0, burn_bps=0)
    )
    with pytest.raises(ValueError, match="10,000"):
        build_atomic_launch_calldata(bad_disposition)

    with pytest.raises(ValueError, match="Unknown launch template"):
        build_atomic_launch_calldata(_standard_request(template_id=b"\x01" * 32))


def test_recipe_derivation_rejects_invalid_inputs():
    base = dict(
        paired_token_decimals=18,
        paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
        target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
        launched_token_is_quote=False,
        fee=3_000,
    )
    with pytest.raises(ValueError, match="fee tier"):
        derive_atomic_launch_pool_recipe(AtomicLaunchPoolRecipeInput(**{**base, "fee": 123}))
    with pytest.raises(ValueError, match="paired_token_usd_price_x18 must be positive"):
        derive_atomic_launch_pool_recipe(
            AtomicLaunchPoolRecipeInput(**{**base, "paired_token_usd_price_x18": 0})
        )
    with pytest.raises(ValueError, match="target_market_cap_usd_x18 must be positive"):
        derive_atomic_launch_pool_recipe(
            AtomicLaunchPoolRecipeInput(**{**base, "target_market_cap_usd_x18": 0})
        )
    with pytest.raises(ValueError, match="uint8"):
        derive_atomic_launch_pool_recipe(
            AtomicLaunchPoolRecipeInput(**{**base, "paired_token_decimals": 256})
        )
    with pytest.raises(ValueError, match="boolean"):
        derive_atomic_launch_pool_recipe(
            AtomicLaunchPoolRecipeInput(**{**base, "launched_token_is_quote": 1})
        )


def test_recipe_derivation_quote_orientation_flips_band():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=True,
            fee=3_000,
        )
    )
    assert recipe.launch_tick % 60 == 0
    assert recipe.liquidity > 0
    # Quote orientation: launch price sits at the lower band edge.
    assert recipe.launch_sqrt_price_x96 == get_sqrt_ratio_at_tick(recipe.launch_tick)


def test_initial_buy_estimate_zero_amount_after_fee():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    # A 1-wei input is entirely consumed by the 0.30% fee.
    estimate = estimate_atomic_launch_initial_buy(
        AtomicLaunchInitialBuyEstimateInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            liquidity=recipe.liquidity,
            paired_token_amount_in=1,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    assert estimate.launched_token_amount_out == 0
    assert estimate.paired_token_amount_consumed == 1
    assert estimate.sqrt_price_after_x96 == recipe.launch_sqrt_price_x96


def test_buy_price_limit_quote_orientation_moves_up():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=True,
            fee=3_000,
        )
    )
    limit = derive_atomic_launch_buy_sqrt_price_limit_x96(
        AtomicLaunchBuySqrtPriceLimitInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            launched_token_is_quote=True,
        )
    )
    # Paired token is token1: input moves price up, so the guard sits above launch.
    assert limit > recipe.launch_sqrt_price_x96


def test_buy_price_limit_rejects_bad_slippage():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    for slippage in (0, 10_000):
        with pytest.raises(ValueError, match="slippage_bps"):
            derive_atomic_launch_buy_sqrt_price_limit_x96(
                AtomicLaunchBuySqrtPriceLimitInput(
                    launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
                    launched_token_is_quote=False,
                    slippage_bps=slippage,
                )
            )


def test_build_calldata_rejects_directionless_and_wrong_direction_buys():
    # Nonzero buy without a directional price limit.
    with pytest.raises(ValueError, match="directional price limit"):
        build_atomic_launch_calldata(
            _standard_request(
                initial_buy=AtomicInitialBuy(
                    paired_token_amount_in=10**18,
                    launched_token_amount_out_minimum=0,
                    sqrt_price_limit_x96=0,
                )
            )
        )
    # Zero buy must not set output or price limits.
    with pytest.raises(ValueError, match="zero initial buy"):
        build_atomic_launch_calldata(
            _standard_request(
                initial_buy=AtomicInitialBuy(
                    paired_token_amount_in=0,
                    launched_token_amount_out_minimum=1,
                    sqrt_price_limit_x96=0,
                )
            )
        )


def test_build_calldata_accepts_valid_initial_buy():
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=18,
            paired_token_usd_price_x18=PAIRED_USD_PRICE_X18,
            target_market_cap_usd_x18=TARGET_MARKET_CAP_USD_X18,
            launched_token_is_quote=False,
            fee=3_000,
        )
    )
    limit = derive_atomic_launch_buy_sqrt_price_limit_x96(
        AtomicLaunchBuySqrtPriceLimitInput(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            launched_token_is_quote=False,
        )
    )
    calldata = build_atomic_launch_calldata(
        _standard_request(
            initial_buy=AtomicInitialBuy(
                paired_token_amount_in=10**18,
                launched_token_amount_out_minimum=1,
                sqrt_price_limit_x96=limit,
            )
        )
    )
    assert len(calldata) > 4


def test_build_calldata_rejects_pool_shape_violations():
    # Single-sided launch: paired maximum must stay zero.
    with pytest.raises(ValueError, match="paired_token_amount_maximum must be zero"):
        build_atomic_launch_calldata(
            _standard_request(
                pool=AtomicPoolConfig(
                    paired_token=PAIRED_TOKEN,
                    launched_token_is_quote=False,
                    profile=AbyssPoolProfile.QUOTE_ORACLE,
                    fee=3_000,
                    oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
                    launch_tick=0,
                    liquidity=1,
                    launched_token_amount_maximum=AUCTION_SUPPLY,
                    paired_token_amount_maximum=1,
                )
            )
        )
    # Zero oracle config id.
    with pytest.raises(ValueError, match="oracle_config_id"):
        build_atomic_launch_calldata(
            _standard_request(
                pool=AtomicPoolConfig(
                    paired_token=PAIRED_TOKEN,
                    launched_token_is_quote=False,
                    profile=AbyssPoolProfile.QUOTE_ORACLE,
                    fee=3_000,
                    oracle_config_id=b"\x00" * 32,
                    launch_tick=0,
                    liquidity=1,
                    launched_token_amount_maximum=AUCTION_SUPPLY,
                    paired_token_amount_maximum=0,
                )
            )
        )
    # Launched maximum cannot exceed token supply.
    with pytest.raises(ValueError, match="cannot exceed token.supply"):
        build_atomic_launch_calldata(
            _standard_request(
                token=AtomicTokenConfig(
                    kind=TokenKind.BURNABLE,
                    name="Test Token",
                    symbol="TEST",
                    decimals=18,
                    supply=AUCTION_SUPPLY // 2,
                )
            )
        )
    # Pool orientation must match the template.
    with pytest.raises(ValueError, match="orientation"):
        build_atomic_launch_calldata(
            _standard_request(
                pool=AtomicPoolConfig(
                    paired_token=PAIRED_TOKEN,
                    launched_token_is_quote=True,
                    profile=AbyssPoolProfile.QUOTE_ORACLE,
                    fee=3_000,
                    oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
                    launch_tick=0,
                    liquidity=1,
                    launched_token_amount_maximum=AUCTION_SUPPLY,
                    paired_token_amount_maximum=0,
                )
            )
        )


def test_build_calldata_rejects_disallowed_fee_destination():
    # The standard template routes paired-token fees to OWNER only.
    with pytest.raises(ValueError, match="destination not allowed"):
        build_atomic_launch_calldata(
            _standard_request(
                paired_token_fees=FeeDisposition(owner_bps=5_000, rewards_bps=5_000, burn_bps=0)
            )
        )
