---
name: launch-pricing
description: Price an Abyss launch from USD valuation, actual supply and quote decimals; fund exact one-sided ranges, protect ordered first buys, and integrate admitted Kyber conversion calldata inside lifecycle funding. Use before economic review or signing.
---

# Price the pools and make the opening purchase

Read `../sdk-usage/SKILL.md`, `../token-launch/SKILL.md` and
`docs/launch-guide.md`. This skill prepares/reviews economics; it does not authorize
signing, broadcasting or API publication. A correct first attempt means proving
the exact final commitment before sending, not guaranteeing future market state
or automatically retrying a refused launch.

## 1. Resolve inputs and price provenance

- Record desired USD valuation, actual minted raw supply, each quote address/decimals,
  quote USD-X18 value, source and observation time. $5,000 is the usual reference,
  not a reason to replace a user-specified target.
- **If you are an AI agent, look up the current quote-token USD price yourself.**
  Use CoinGecko, Dexscreener, web search or another reliable online market source.
  Do not ask whether the user wants pricing, make them choose a provider or require
  a particular API. Price lookup is part of preparing the launch.
- Match the actual token and chain, not just its ticker. Record source/time and
  validate decimals and positive price; compare liquid markets where relevant.
  A tokenized stock's underlying price or an assumed stablecoin peg is not enough.
  If no usable price can be found, explain the missing input before construction;
  never silently substitute zero, $1 or a 1:1 pool ratio.
- Describe stale/incomplete/manual evidence without inventing a universal freshness
  cutoff. Launch USD valuation is not an on-chain lending oracle. A Chainlink lending
  policy must not become the only possible construction price source.
- Parse decimal USD to integer X18 with exact Decimal/integer scaling, never float.
  Read quote decimals; lifecycle recipe supply is raw 18-decimal launch-token units.

## 2. Price from whole-token supply, not a pool's budget

Let `S` be total minted raw supply, `M` desired USD-X18, `P` quote USD-X18,
`d` quote decimals and `R = sqrt_price_x96**2 / 2**192`.
`R` is raw token1 per raw token0, not human quote per launch token.

- Launch token0: raw ratio is `M * 10**d / (P * S)`.
- Launch token1: invert it.
- Decimal conversion is essential. `1 << 96` represents raw-unit parity, not a
  $5,000 FDV or human-unit parity when decimals differ.

The historical `launched_token_is_quote` argument means **launch token is token0**.
Derive it from actual finalized token prediction and numerical address ordering,
not quote-only fee mode, input ordering or checksum capitalization. Predict against
the actual factory/core and revisit any change that affects token identity.

`derive_launch_pool_recipe` takes explicit `supply`, per-pool `token_budget`,
`paired_token_decimals`, `paired_token_usd_price_x18`,
`target_market_cap_usd_x18`, `launched_token_is_quote`, `fee` and `tick_spacing`.
Whole supply prices the token; each pool's budget funds its liquidity. Four pools
with 25% budgets still use total supply in all four price calculations. Do not
split FDV between pools or add their FDVs together.
The omitted-supply default is **one billion** tokens, not the lifecycle example's
one million. Always pass the actual committed supply.

Independently reconstruct aligned opening reference FDV, using integer division:

```text
launch token0: fdv_x18 = sqrt_price_x96**2 * S * P // (2**192 * 10**d)
launch token1: fdv_x18 = 2**192 * S * P // (sqrt_price_x96**2 * 10**d)
```

Use actual spacing-aligned sqrt prices and show target deviation. Multi-quote
pools should imply approximately the same USD token price using their recorded
snapshots. Also report expected retained-supply FDV after burn and post-buy value.
Reference FDV, circulating market cap, pool TVL and funds raised are not synonyms.

## 3. Fund exact geometry

- Market budgets total minted supply; position maxima total each pool's budget.
  Apportion with deterministic integer remainders.
- One position uses the recipe's liquidity/range/maximum. Multiple positions use
  `derive_launch_position_ranges` with source preset, explicit count and per-pool
  budget. For continuous Staircase or Early Scarcity, explicitly choose
  `position_gaps="adjacent"`; the final boundary tail now defaults to enabled.
- Custom bands require exact weights, price boundaries and budget-safe integer
  liquidity, not fixed `1000e18`, equal-tick extrapolation or a percentage reserve.
  Each initial mint must require zero quote deposit in actual token order.
- The forward tail ends at the finite usable spacing-aligned protocol boundary,
  never mathematical infinity. Its allocation belongs inside the budget. A range
  endpoint is not an exclusive router swap-price guard.
- Review canonical rounded mint debits, residual burn and retained supply. The
  current contracts allow materially undersized positions and burn every leftover;
  cap equality is not proof of full LP consumption. Dead-address transfers do not
  enforce `burn()`. Do not label material burned inventory as rounding dust.
- A token1 opening at an upper tick can have zero active liquidity until the first
  downward buy crosses that boundary. Check reachable buy-side bands rather than
  refusing solely on opening active liquidity.

### Read-only arithmetic example

The $1, 6-decimal quote is an explicit **fixture**, not a live oracle or executable
launch. Run with the package installed; no RPC, wallet or secret is used.

```python
from black_market_sdk import derive_launch_pool_recipe, derive_launch_position_ranges
WAD = 10**18
supply = 1_000_000 * WAD
recipe = derive_launch_pool_recipe(
    paired_token_decimals=6, paired_token_usd_price_x18=WAD,
    target_market_cap_usd_x18=5_000 * WAD,
    launched_token_is_quote=True, fee=3000, tick_spacing=60,
    supply=supply, token_budget=supply // 2,
)
bands = derive_launch_position_ranges(
    launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
    launched_token_is_quote=True, token_budget=supply // 2,
    tick_spacing=60, venue="uniswap-v4", distribution_preset="staircase",
    position_count=5, position_gaps="adjacent",
    extend_final_range_to_boundary=True,
)
fdv_x18 = recipe.launch_sqrt_price_x96**2 * supply * WAD // ((1 << 192) * 10**6)
print({"fdv_x18": str(fdv_x18), "positions": bands.positions})
```

## 4. Protect ordered first buys

1. Commit market index, quote input cap, recipient, minimum token output and price
   guard for every buy. Input quote must match its market. Native ETH funds WETH
   via NativeWrap; a different payment currency needs admitted conversion first.
2. `estimate_launch_initial_buy` is a pure fresh-single-position preview, not
   multi-band traversal, previous ordered buys, hook/token effects or a funding
   route. Its paired-input report is not proof of actual partial-fill spend.
   Do not make executable minima from that estimate alone.
3. `derive_launch_buy_sqrt_price_limit_x96` supplies a directional exclusive price
   guard, separately from token-output slippage. A tight guard can truncate a large
   or multi-band purchase; review actual spend/output and leftover quote.
4. On an unstarted launch, await `prepare_and_plan_lifecycle_launch` with explicit
   `buy_slippage_bps`. Exact local curve math quotes every ordered buy and commits
   `floor(quoted_output * (10000 - bps) / 10000)` as its minimum. No diagnostic
   candidate or execution pass exists. Omission retains existing minima. A floored
   zero refuses; do not silently set `1` merely to pass validation.
5. A desired percentage purchase needs actual net-output sizing against retained
   supply, fees and previous buys. $250 at $5,000 FDV is not automatically 5% of
   supply purchased along an AMM curve.
6. Review the final plan/hash, conversion floor, escrow per quote, each buy's net
   output/spend, refunds and gas/data costs. Sign only its freshly assessed immediate
   envelope; never change reviewed gas/price or submitted economics/salts/minima.

## 5. Put Kyber conversion inside lifecycle funding

The supported flow is creator input -> escrow conversion -> measured quote credit
-> activation LP mints/burn/direct ordered venue buys -> quote/input refunds and
public opening. Kyber buys the **quote**, not the predicted inactive launch token.
A later wallet swap for the active token is a separate authorized transaction.

Prefer NativeWrap for ETH->WETH and direct ERC20 funding for the market quote.
When a conversion is needed, build `LifecycleAssetFunding` (the `AssetFundingV1`
wire struct) with `LifecycleFundingKind.SWAP` and exact values:

| Field | Meaning |
| --- | --- |
| `asset` | ERC20 output quote. |
| `amount` | Minimum newly measured quote, sufficient for all buys in that quote. |
| `input_asset` | Input ERC20, or zero address for native ETH. |
| `input_amount` | Exact authorized input in raw units. |
| `target` | Enabled registry-admitted router with matching runtime/spender binding. |
| `data` | Executable conversion bytes, not preview metadata or a launch buy. |

First check `registry.fundingTarget(target)` on this deployment. Its enabled
spender/runtime must match; non-native conversion inputs additionally need
`fundingInputAllowed(input_asset)`. Direct same-token funding is unaffected.
Admission in an old registry does not carry over. Missing admission is an admin
prerequisite, not poor quote liquidity or authority to register a router, bypass
checks, or silently submit a separate wallet swap.

Use the operator's API `POST /api/v1/quote/kyber/build?chainId=4663` with JSON
`tokenIn`, `tokenOut`, `amountIn` (decimal string), `sender`, `recipient`,
`slippageBps`, `deadline` (decimal unix seconds). These HTTP keys are camelCase,
not Python dataclass names. **Sender and recipient both equal the funding escrow**,
not creator/core/legacy executor. The API normalizes native zero-address identity.
Current HTTP slippage is 1..2000 bps and deadline is future/within three hours;
these are distinct from ordered-buy slippage's 0..9999 range.

The build requests a fresh server-owned route. GET `/api/v1/quote/kyber` is only
preview. Validate returned chain/tokens/input/native value, router, recognized
calldata bindings, expiry, route/impact, output floor and input budget. Missing
impact is unknown, not zero. Map `transaction.to` to `target`, exact
`transaction.data` to `data`, and `amountOutMinimum` to `amount`. Decode hex to
the documented bytes representation when required; do not invent another calldata
encoding. The floor must cover all committed buys in that quote. Native outer
value sums native funding inputs; ERC20 conversion carries zero inner native value.

The creator approves **escrow**, not core/router, for ERC20 input. Escrow owns its
router-spender allowance and resets it after conversion. NativeWrap has
`input_asset == asset == wrapped_native`, `input_amount == amount`, zero target/data;
native Swap instead has zero `input_asset`. Never mix their field shapes.
Funding outputs are sorted and unique: aggregate buys sharing a quote; do not add
duplicate funding rows or assume a single route build funds several quote assets.

Build late enough to avoid expiry, then assess the complete committed plan.
Conversions rely on their calldata amountOutMinimum/deadline, never a simulated
output. Single-market conversion funding can be atomic if its known budget plus
poster allocation fits; each Swap adds an empirical 2M compute reserve before
at least 25% padding, not a universal arbitrary-router bound. Multi-market plans
remain staged. Changed calldata before submission means rebuild/review an
unstarted commitment. After begin/submission, recover the exact original plan;
expiry may require cancellation, not a silent refresh. Never replace the
committed first buy with a post-launch wallet swap.

Source/evidence map: `../token-launch/references/audit-history.md`.
