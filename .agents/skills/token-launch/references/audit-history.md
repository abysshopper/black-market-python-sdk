# Launch skill audit: decisions and evidence

Audit date: 2026-10-10. Scope: SDK guidance only, not a new release, contract audit
or authorization to transact. Existing pricing/recovery worktree changes were
preserved. Historical verification claims are archived evidence, not checks rerun
by this audit. Current source and exact deployed graph override old conversations.

## History coverage

Searched all **2,838 JSONL transcripts / 887,183 parseable records** present in the
local OMP agent session store at the snapshot, including child-agent transcripts
and every file referenced by **252 archive-indexed sessions**. User/assistant text
was searched for launches, topology, quotes, hooks, liquidity, pricing/FDV,
slippage, first purchases and Kyber; relevant decisions were read in context.
This is not a semantic review of every unrelated transcript or coverage of
unavailable/other-workstation sessions.

No files were unreadable. One truncated record could not be parsed: child
`FrontendHardeningCoordinator`, record 117, under session
`01a0ac8e-fad4-73a7-b5d4-13574025474f`. It was not repaired or treated as evidence.
No raw prompts, secrets, signatures or unrelated personal material is copied here.
The session IDs below support local archive lookup.

## Durable decisions and regressions

| Session / date | Intent or issue | Required guidance |
| --- | --- | --- |
| `01a0727e-722e-71fd-8d2b-d3fab5526c36`, Sep 5 | AI-guided design with understandable standard/quote fees and token behavior. | Ask for real financial/product choices; derive ticks/amounts rather than making the user solve SDK internals. Examples are starting points. |
| `01a0fbdf-b177-7232-b7fa-396c71e12545`, Oct 2 onward | Positions, venues, quotes and hook deployment scope are independent. Bound isolation was wanted without removing shared compatibility. | One V4 hook per pool, many positions per pool; multiple quotes do not require one shared root. No invented topology reduction. |
| Same session | Eight catalogue structures with one opening buy; earlier synthetic many-buy stress and invented caps were confused with real product constraints. Local bound cost was higher. | Preserve requested workload, mode and buys. Historical local gas is not live Nitro proof or a bound-hook saving. |
| `01a108e3-e381-7510-8797-667346ffb5e6`, Oct 4 | Unrequested 1% fee ceilings were removed; local 15% fee cases and separate custom-hook terms were exercised. | Do not add fee ceilings or treat trading fee/author allocation as the same unit or policy. |
| `01a10e93-8b3f-75c8-88a9-1b7fdece7fad`, Oct 6 | Known construction should avoid repeated discovery and redundant application checks; complex multi-venue five-band flow with forward tail was requested. | Frozen selectors first, SDK-owned unified proof, concise economic review, no second salt/admission loop. |
| `01a1155f-83bf-752e-9a75-7b83b8706a59`, Oct 7 | Budget maxima could fund almost no LP and return supply to creator. Fix enforces allocation equalities and burns unused inventory before buys. | Review actual mint debits/material burn/retained supply. Intended allocation is still not exact consumption; no creator reserve or dead-sink substitute. |
| `01a1186f-b7b4-70fe-af6e-50fbde84fba7`, Oct 7 | Optional cross-venue custody probes caused explorer warnings; `custodyRecipients()` replaced them, requiring hub/source redeployment together. | Receipt Success is not latest-stack proof; immutable old hubs and historical warnings remain. |
| `01a118ae-fabb-71e6-9db0-61a1bf4d6484`, Oct 7 onward | Concrete hook behavior differs from topology/config wire fields. | Stored config6 minimum/sensitivity do not make the fixed hook dynamic; custom hooks need their actual contract/integration. |
| `01a119fc-948b-70f1-bbf9-9347fc1b6c3c`, Oct 8 onward | Hardcoded Q96 parity led to grossly wrong valuation, reported around $2.4m. A lending Chainlink cutoff was then incorrectly imposed as the only valuation source. | Explicit actual supply, decimal/order-aware recipe, quote provenance and aligned/retained FDV before signing. API valuation is a construction input, not an execution quote. |
| `01a10a6b-e8ac-7180-bc3f-4e83f7f95396` / `01a10e93-8b3f-75c8-88a9-1b7fdece7fad`, Oct 5–6 | Kyber should convert inside launch funding, not in a separate wallet transaction; graph-specific admission was confused. | Escrow sender/recipient, committed Swap funding bytes/output floor, direct ordered activation buy; check this registry, not the previous one. |
| `01a118d3-9898-7275-877f-058a03a2bd92`, Oct 10 review | JOSH used October5 graph addresses and old warnings despite October8 release. | Compare actual plan/caller graph, installed package and retained intent rather than assuming defaults were updated. |

Earlier $3,000 experiments evolved into the usual $5,000 target; neither is a
protocol mandate. Quote assets are not a curated-token allowlist. User-facing
explanation should emphasize behavior/value instead of defensive boilerplate,
while execution authority and private diagnostics remain intact.

## Shape catalogue is not a Python enum

`derive_launch_position_ranges` exposes Early Scarcity, Staircase and Smooth Ramp.
The separate historical protocol catalogue has these core layouts:

| Shape | Core positions | Tail share/start at $5k reference | Objective |
| --- | ---: | --- | --- |
| Anchor | 11 | 10% from $5k | Acceleration with substantial base. |
| Ladder | 9 | 10% from $5k | Similar buying effort per doubling. |
| Orbit | 9 | 5% from $80k | Overlapping horizons. |
| Rocket | 11 | 2% from $5k | Acceleration with smaller base. |
| Cruise | 10 | 6% from $320k | Discovery then depth. |
| Spread | 9 | 10% from $5k | Inventory across valuations. |
| Depth | 8 | 28% from $640k | Later inventory. |
| Bundle | 8 | 3% from $320k | Opening inventory access. |

These are design facts, not accepted `distribution_preset` strings or current
production admission promises. Resolve exact bands/weights from protocol
`docs/launch-lp-structure-designs.md` and its `LaunchBandCatalog`-based local proof.
Optional Abyss bonus means another pool, funded from the same total inventory.
Tail allocations are inside the budget and end at the finite usable protocol
boundary; no unintended gap may precede the tail. Historical dollar curves omit
actual fees, burns, quote-price movement and tick rounding: recalculate and prove.

## Current source map

- `black_market_sdk/launch.py`: recipe, range presets, indicative single-band buy
  and exclusive price guard. Default recipe supply is one billion tokens; pass
  actual lifecycle supply explicitly.
- `black_market_sdk/lifecycle.py`, `lifecycle_presets.py`, `lifecycle_abis.py` and
  public `__init__.py`: frozen semantic models, exact wire codecs, graph selection,
  final protected proof, synchronous recovery/build-next and async unified preparation.
- `docs/launch-guide.md`: operator flow; installed wheels and pending worktree
  example corrections are different evidence.
- Protocol `contracts/src/launch/lifecycle/v1/LaunchFundingEscrowV1.sol`: measured
  output credit, exact input pull, target/spender policy, allowance reset/refunds.
  Conversion data is opaque to the SDK and bounded to 16,384 bytes; the initial-buy
  struct has no external-target/calldata field.
- Protocol `docs/launch-lifecycle-v1-operations.md`, "Admit Kyber on the current
  Robinhood deployment": graph-specific router/input admission and escrow-bound
  builds/fork prerequisites. Read current admission, never infer it from a package.
- API `src/api/routes/quoteKyber.ts`: preview versus fresh intent build, camelCase
  request, native normalization, 1..2000-bps HTTP slippage, deadline/output floor
  and unsigned transaction DTO. Python SDK has no Kyber HTTP quote/build wrapper.
- Protocol `contracts/deployments/launch/pool-launch-v1/20261008T000141Z-e174ce6/`:
  canonical mined graph evidence; old config5/October5 artifacts are not this graph.

## Audit limits

These docs do not fix old pools, admit hooks/Kyber or publish a package. Current
pure/read-only smoke belongs in the completion report, separate from historical
checks. Real funding/activation needs actual admitted dependencies and a usable
stateful backend. A "first try" objective cannot guarantee an unreviewed future state.
