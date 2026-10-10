---
name: token-launch
description: Design and execute an Abyss Python lifecycle launch across venues, quotes, positions and admitted hooks. Use for token/reward selection, funding, atomic or staged execution, metadata publication and recovery; read launch-pricing before choosing prices or buys.
---

# Design, launch and recover a token

Read `../sdk-usage/SKILL.md`, `../launch-pricing/SKILL.md` and
`docs/launch-guide.md`. The examples are starting points, not a restriction to
eight supported token/topology combinations.

## Resolve the design before producing calldata

Ask only for unresolved financial/product choices. Reuse supplied intent and
SDK/config evidence instead of asking for repository-provided information:

- Name/symbol, supply, ERC20/ERC404 behavior, NFT unit where applicable, reward
  mode and fee assets. Token behavior, fee distribution and pool hook are distinct.
- Venue(s), quote asset(s), per-pool allocations and distribution shape. Explain
  standard fees as accruing in both pool currencies and quote-only fees as accruing
  in the selected quote currency, rather than exposing implementation jargon alone.
- Opening valuation, ordered buy amounts/recipients and payment currency.
  Use the project's $5,000 reference target when none was supplied; respect
  explicit targets/purchase amounts. Look up quote prices yourself as part of
  preparation; do not offer pricing as an extra service or ask for a price provider.
- Exact admitted hook behavior, royalties, pool fees, oracle and external-LP policy.
- Explicit atomic/staged execution and the intended metadata/API workflow.

User-facing review should show token, valuation, quotes, allocation, fees, initial
purchases and total funding. Keep digests/raw calldata in diagnostic evidence,
not as decisions the user must make to compensate for incomplete SDK integration.

## Pool topology is not hook topology

| Dimension | Meaning / construction rule |
| --- | --- |
| Position | One tick range/liquidity amount in a pool; no separate hook per position. |
| Market/pool | One launched-token/quote/venue identity with pool-wide settings. |
| Multi-quote | Different pools, each using the same whole-token valuation and its own quote/USD price. |
| Multi-venue | Abyss and V4 may share a quote; budgets and market identities are separate. |
| Bound V4 | A mined constructor-bound instance per pool; shipped config6/V2 graph. |
| Shared V4 | An admitted multipool root with isolated PoolId state; codec config4, not a shipped default offering. |

Abyss variants are STANDARD, STANDARD_ORACLE, QUOTE and QUOTE_ORACLE. Select
the exact preset/discovered profile and enabled fee/spacing combination.
Oracle-enabled pools do not automatically qualify for lending.

At most **one V4 market per launched-token/quote**, across hook templates, fee
tiers and topologies. Consolidate that quote's V4 positions into one market;
Abyss may also use the quote. A/B/C quotes with V4+Abyss each mean six pools,
with three hook instances in the bound case or one compatible shared root.

Structural bounds: 16 markets, 32 total positions, 8 fee assets, 8 funding outputs
and 64 ordered buys. They do not establish gas feasibility. Do not reduce the
requested topology after an atomic refusal.

`derive_launch_position_ranges` exposes Early Scarcity, Staircase and Smooth Ramp.
Use `position_gaps="adjacent"` explicitly to remove Staircase/Early Scarcity
source gaps. `extend_final_range_to_boundary=True` is the default. Adjacent ranges
can be continuous without overlap. Anchor/Ladder/Orbit/Rocket/Cruise/Spread/Depth/
Bundle are a separate historical contract allocation catalogue, not SDK
`distribution_preset` strings. Resolve custom bands and exact weights instead
of calling equal-width tick partitions a source preset.
See `references/audit-history.md`.

## Hook and fee contract

- Select an admitted execution profile per market, not an arbitrary hook address.
  A V4 pool has one hook; multiple behaviors require one admitted implementation.
- Match exact schema/version, runtime/constructor/dependency graph, capabilities,
  oracle, custody and author terms. Callback permission bits are insufficient.
- `FixedFeePoolHookV1` uses a fixed maximum-pips schedule. Config6 minimum and
  sensitivity fields do not make it a dynamic hook. A reference Surge/dynamic
  artifact or working encoder is not live production admission.
- Other compatible hooks may need different launch fields or swap-time data;
  topology/config6 alone does not justify reusing a fixed-hook form or empty data.
- LP fees, hook trading fees, protocol share, executor bounty, author allocation
  and owner/reward/burn splits are different quantities. Author allocation comes
  from attributable owner fees after bounty, not an additional trader tax.
- Copy immutable terms/treasury/denominator from the selected profile. Preserve
  supported requested author rates/external-liquidity choices; never invent fee
  ceilings or disable external LP participation to simplify a launch.

## Prove and execute the final commitment

1. Confirm the intended graph. October8 includes enforced allocation/burn and
   custody-read fixes; October5 addresses do not acquire those changes.
2. Use the pricing skill for price, budget-funded liquidity and protected buys.
   Market budgets total supply; position maxima total each market budget.
   Actual mint debits can be smaller: review burned leftovers and retained supply.
3. Complete funding and ascending unique fee/funding assets; each fee allocation
   totals 10,000 bps. Kyber conversion belongs inside committed launch funding,
   not an unsolicited separate wallet swap.
4. Freeze economics and ordered buys, then await
   `prepare_and_plan_lifecycle_launch` with explicit mode and optional
   `buy_slippage_bps` for an unstarted launch. Review its final plan/hash,
   stateful proof and immediate envelope.
5. Require explicit authorization for real chain/API writes. Never overwrite
   `.env`, print secrets or provide a default development key.
6. Sign only the exact immediate reviewed command. Save its signed hash before
   sending, then actual included/replacement hash and confirmation evidence.
   Fresh build-next proof must not change committed economics.
7. Verify canonical Active state, receipt, custody/positions, burn/supply and
   ordered buys before reporting chain success. Verify publication separately;
   pending indexing is not completed publication.

Existing `launch_erc20_*` / `launch_erc404_*` cases use `launch_examples.py` and
`_launch_support.py`. Inspect the actual installed/local version: working-tree
pricing/recovery corrections are not automatically published. Read-only audits
must not invoke funded launch runners.

## Recover the original launch

Read retained `result.json`, events, receipts and the mode-0600 exact private
recovery plan. Redacted public plans are diagnostics, not recovery authority.
After a timeout, query the saved signed hash before deciding work is missing.
Use canonical SDK progress/receipt/replacement/reorg handling; inclusion is not
finality. Resume only uncompleted original work with deliberate authorization.
Rehydrate with `parse_launch_plan`, call `plan_launch` with original mode/receipts/
confirmations, then obtain only the immediate `build_next_transaction`.
Its `None` result can mean Active **or Cancelled**; inspect canonical phase.
Explicit cancellation uses `action="cancel"` on the original planned launch.
Never automatically resend, create another token, regenerate salts/minima or
mutate a submitted plan. Metadata/upload/publication failure does not require
another chain launch. Do not log or commit private signatures/capabilities.
