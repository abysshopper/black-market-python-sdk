"""README quick start, end to end and offline: addresses, recipe, calldata, formatting.

Run with: python examples/quickstart.py
No RPC connection or private key is required.
"""
import time


from black_market_sdk import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    AUCTION_SUPPLY,
    ROBINHOOD_MAINNET_CHAIN_ID,
    STANDARD_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchBuySqrtPriceLimitInput,
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
    format_units_display,
    get_addresses,
)

# 1. Canonical deployment addresses for Robinhood Chain mainnet (chain 4663).
addresses = get_addresses(ROBINHOOD_MAINNET_CHAIN_ID)
print(f"abyss router:    {addresses.abyss_router}")
print(f"launch factory:  {addresses.launch_factory}")
print(f"lending pool:    {addresses.lending_pool}")

# 2. Derive the one-sided launch position from a target FDV.
recipe = derive_atomic_launch_pool_recipe(
    AtomicLaunchPoolRecipeInput(
        paired_token_decimals=18,
        paired_token_usd_price_x18=2_500 * 10**18,  # ETH at $2,500
        target_market_cap_usd_x18=5_000 * 10**18,  # $5,000 opening FDV
        launched_token_is_quote=False,
        fee=3_000,  # 0.30% tier
    )
)
print(f"launch tick:     {recipe.launch_tick}")
print(f"liquidity:       {recipe.liquidity}")

# 3. Build and validate a deployAndLaunch request, then encode its calldata.
price_limit = derive_atomic_launch_buy_sqrt_price_limit_x96(
    AtomicLaunchBuySqrtPriceLimitInput(
        launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
        launched_token_is_quote=False,
    )
)
request = AtomicLaunchRequest(
    creator="0x1000000000000000000000000000000000000001",
    template_id=STANDARD_TEMPLATE_ID,
    template_version=1,
    token=AtomicTokenConfig(
        kind=TokenKind.BURNABLE,
        name="Example Token",
        symbol="EXAMPLE",
        decimals=18,
        supply=AUCTION_SUPPLY,
    ),
    pool=AtomicPoolConfig(
        paired_token=addresses.weth,
        launched_token_is_quote=False,
        profile=AbyssPoolProfile.QUOTE_ORACLE,
        fee=3_000,
        oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
        launch_tick=recipe.launch_tick,
        liquidity=recipe.liquidity,
        launched_token_amount_maximum=recipe.launched_token_amount_maximum,
        paired_token_amount_maximum=recipe.paired_token_amount_maximum,
    ),
    initial_buy=AtomicInitialBuy(
        paired_token_amount_in=10**18,  # 1 WETH initial buy
        launched_token_amount_out_minimum=0,
        sqrt_price_limit_x96=price_limit,
    ),
    launched_token_fees=FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=0),
    paired_token_fees=FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
    deadline=int(time.time()) + 1_200,
)
calldata = build_atomic_launch_calldata(request, native_buy_amount=0)
print(f"calldata:        0x{calldata[:4].hex()}… ({len(calldata)} bytes)")

# 4. Format raw units for display.
print(f"initial buy:     {format_units_display(10**18, 18)} WETH")
