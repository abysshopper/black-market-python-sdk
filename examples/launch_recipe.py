"""Derive an Atomic launch pool recipe and estimate the initial buy — no RPC needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    AtomicLaunchInitialBuyEstimateInput,
    AtomicLaunchPoolRecipeInput,
    derive_atomic_launch_pool_recipe,
    estimate_atomic_launch_initial_buy,
)

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
print(f"sqrt price X96:  {recipe.launch_sqrt_price_x96}")
print(f"liquidity:       {recipe.liquidity}")

estimate = estimate_atomic_launch_initial_buy(
    AtomicLaunchInitialBuyEstimateInput(
        launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
        liquidity=recipe.liquidity,
        paired_token_amount_in=10**18,  # 1 paired token
        launched_token_is_quote=False,
        fee=3_000,
    )
)
print(f"1 paired token buys ~{estimate.launched_token_amount_out / 10**18:,.0f} launched tokens")
