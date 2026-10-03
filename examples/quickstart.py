"""Construct current UnifiedLauncher routes offline.

Run with ``python examples/quickstart.py``. This example only validates and
encodes unsigned transaction dictionaries: it creates no client, signs nothing,
and never broadcasts a transaction.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    AUCTION_SUPPLY,
    ROBINHOOD_MAINNET_CHAIN_ID,
    STANDARD_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchPoolRecipeInput,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    AtomicTokenConfig,
    AbyssPoolProfile,
    FeeDisposition,
    LaunchPoolKind,
    TokenKind,
    UnifiedLaunchPool,
    build_unified_launch_transaction,
    derive_atomic_launch_pool_recipe,
    get_addresses,
    get_sqrt_ratio_at_tick,
    get_unified_launch_addresses,
    to_unified_launch_request,
    to_uniswap_v4_pool_config_v2,
)

# The captured schema-/2 Abyss adapter charges this native launch fee.  The V4
# routes deliberately produce a zero-value transaction regardless of this input.
LAUNCH_FEE_WEI = 500_000_000_000_000

# Current mainnet addresses are explicit: chain 4663 is Robinhood Chain mainnet.
chain_id = ROBINHOOD_MAINNET_CHAIN_ID
protocol = get_addresses(chain_id)
routes = get_unified_launch_addresses(chain_id)

recipe = derive_atomic_launch_pool_recipe(
    AtomicLaunchPoolRecipeInput(
        paired_token_decimals=18,
        paired_token_usd_price_x18=2_500 * 10**18,
        target_market_cap_usd_x18=5_000 * 10**18,
        launched_token_is_quote=False,
        fee=3_000,
    )
)

atomic_request = AtomicLaunchRequest(
    creator="0x1000000000000000000000000000000000000001",
    template_id=STANDARD_TEMPLATE_ID,
    template_version=1,
    token=AtomicTokenConfig(
        kind=TokenKind.BURNABLE,
        name="Example Unified Token",
        symbol="UNIFIED",
        decimals=18,
        supply=AUCTION_SUPPLY,
    ),
    pool=AtomicPoolConfig(
        paired_token=protocol.weth,
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
        paired_token_amount_in=0,
        launched_token_amount_out_minimum=0,
        sqrt_price_limit_x96=0,
    ),
    launched_token_fees=FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=0),
    paired_token_fees=FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
    deadline=1_800_000_000,
)

v4_config = to_uniswap_v4_pool_config_v2(
    atomic_request.pool,
    launch_sqrt_price_x96=get_sqrt_ratio_at_tick(recipe.launch_tick),
    external_liquidity_disabled=True,
)

# Each kind maps to a different registry key. Abyss and optimized V4 V3 are the
# current new-launch choices and are never substituted for one another.
selections = (
    UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=atomic_request.pool),
    UnifiedLaunchPool(kind=LaunchPoolKind.UNISWAP_V4_V3, config=v4_config),
)

print("schema-/2 UnifiedLauncher:", routes.unified_launcher)
for selected_pool in selections:
    request = to_unified_launch_request(atomic_request, selected_pool)
    transaction = build_unified_launch_transaction(
        request,
        chain_id=chain_id,
        launch_fee=LAUNCH_FEE_WEI,
    )
    print(
        f"{selected_pool.kind.value:16} "
        f"pool type=0x{request.pool_type.hex()} "
        f"calldata={len(transaction['data'])} bytes "
        f"native value={transaction['value']}"
    )
