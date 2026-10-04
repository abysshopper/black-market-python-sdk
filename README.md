![Abyss header](assets/abyss-header.png)

# black-market-sdk

Python SDK for Black Market on [Robinhood Chain](https://robinhoodchain.blockscout.com):
current schema-/2 UnifiedLauncher construction, optional launch-metadata API
integration, Abyss DEX primitives, historical Atomic tools, and lending-market
helpers. Built on [web3.py](https://web3py.readthedocs.io). Python 3.10+.

## Install

```sh
pip install black-market-sdk
```

## Networks and address records

| Chain ID | Network | Default address record |
| -------: | ------- | ---------------------- |
|   `4663` | Robinhood Chain mainnet | Captured schema-/2 UnifiedLauncher stack |
|  `46631` | Black Market workbench fork | Zero Unified route addresses until configured |
|  `31337` | Local Anvil | Zero Unified route addresses until configured |

`get_addresses()` defaults to workbench chain `46631`; always pass `4663` when
constructing a canonical mainnet request. On mainnet,
`get_addresses(4663).launch_factory` is the schema-/2
`UnifiedLauncher` at `0xa7a4755fb907593f05fd1e289aa780f0d57f3a12`, not a
historical `AtomicLaunchFactory`.

```python
from black_market_sdk import (
    ROBINHOOD_MAINNET_CHAIN_ID,
    get_addresses,
    get_unified_launch_addresses,
)

application = get_addresses(ROBINHOOD_MAINNET_CHAIN_ID)
routes = get_unified_launch_addresses(ROBINHOOD_MAINNET_CHAIN_ID)
assert application.launch_factory == routes.unified_launcher
print(routes.uniswap_v4_launch_pool_adapter_v3)
```

`UnifiedLaunchAddresses` holds the separately verified pool registry, adapters,
token deployers, PoolManager, and supported current-route dependencies. The
`get_addresses()` and `get_unified_launch_addresses()` lookup surfaces share the
existing bare, `VITE_`, then `NEXT_PUBLIC_` `LAUNCH_FACTORY` precedence, so
their current launcher fields remain equal even for an explicit zero override.
Other Unified route fields use the same precedence and are evaluated once when
`addresses` is imported. Abyss and lending override scopes remain as documented
by the existing address book.

The mainnet values are captured from the following public records in the
reference Black Market repository:

- `contracts/deployments/launch/robinhood-mainnet-launch-unified-v2-deployment-record.json`
  (`black-market.launch-unified-deployment/2`);
- `contracts/deployments/launch/robinhood-mainnet-uniswap-v4-v3-extension-record.json`
  (the captured enabled optimized V3 registration); and
- `apps/web/src/config/launch-addresses.ts`.

## Current UnifiedLauncher launches

Use `UnifiedLaunchPool` to attribute a request to one exact registry route:

- `LaunchPoolKind.ABYSS` → `ABYSS_POOL_TYPE`; and
- `LaunchPoolKind.UNISWAP_V4_V3` → `UNISWAP_V4_V3_POOL_TYPE`.

The optimized V4 V3 route encodes the exact 10-field, 320-byte
`UniswapV4PoolConfigV2` payload:

```text
(address pairedToken, uint8 profile, bytes32 oracleConfigId,
 int24 tickLower, int24 tickUpper, uint160 sqrtPriceX96, uint128 liquidity,
 uint256 launchedTokenAmountMaximum, uint24 abyssFeePips,
 bool externalLiquidityDisabled)
```

There is no `protocolBps` field in the payload. `abyssFeePips` is the selected
supported trading fee.

The address and ABI catalog is offline serialization support, not live launch
eligibility. Before presenting or submitting a route, callers MUST read or
simulate the current registry entry, verify it has a nonzero enabled adapter,
verify the selected fee and `oracleConfigId` remain available for the chosen
route, and determine the current required launch fee/native value (including
the Abyss adapter's `launchFee()` where applicable). Builders intentionally do
not perform those reads or substitute a route when any prerequisite changes.

`to_unified_launch_request()` converts a validated historical-style
`AtomicLaunchRequest` into the generic V3 envelope for the registered current
token type. It remains possible to construct `UnifiedLaunchRequest` directly
for a custom registered token type without forcing that conversion.

```python
from black_market_sdk import (
    LaunchPoolKind,
    UnifiedLaunchPool,
    build_unified_launch_transaction,
    to_unified_launch_request,
)

# atomic_request is caller-owned input and v4_config is an
# UniswapV4PoolConfigV2 built with to_uniswap_v4_pool_config_v2().
request = to_unified_launch_request(
    atomic_request,
    UnifiedLaunchPool(kind=LaunchPoolKind.UNISWAP_V4_V3, config=v4_config),
)
transaction = build_unified_launch_transaction(
    request,
    chain_id=4663,
    launch_fee=0,
)
# transaction is only {"chainId", "to", "data", "value"}; sender, nonce,
# gas, signing, simulation, and broadcast remain caller-managed.
```

`build_unified_launch_calldata()` and `build_unified_launch_transaction()` are
pure/offline constructors. They do not create a client, estimate gas, sign, or
broadcast. The transaction builder rejects zero/default-unavailable records and
the two known retired write targets locally.

The current ABI and component constants are exported from
`black_market_sdk.unified_abis`, including `UNIFIED_LAUNCHER_ABI`,
`LAUNCH_POOL_REGISTRY_V3_ABI`, `ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI`,
`UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI`, and
`ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI` /
`ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI`. The hook-deployer exports are ABI
surfaces only; the SDK does not add low-level mining or deployment helpers.
Current V2 support interfaces use explicit `*_V2_ABI` names; the older
unversioned Atomic ABI exports retain their historical meaning.

## Opt-in multi-market lifecycle V1

`black_market_sdk.lifecycle` implements the new `LaunchPlanV1` stack. It does
**not** replace `UnifiedLaunchRequest`, select a deployed production address,
publish a package, or rebind an existing launcher. The new orchestrator and
creator are explicit in every economic plan.

Quote and external fee assets and direct ERC20/native-wrap funding are permissionless:
the planner checks deployed contract code, not administrator approval. Deployment quote
metadata is not an allowlist. Only ERC20 inputs to swap conversions use the registry's
`fundingInputAllowed` admission; target/spender and codehash checks remain mandatory.
Exact transfers, balances, budgets and native-wrap bindings remain enforced.

| Public API | Behavior |
| --- | --- |
| `plan_launch(client, plan, account=..., mode=...)` | Resolve current approved adapter/profile identities, exact funding-escrow approvals, atomic-first gas admission, and an explicitly chosen atomic or staged sequence |
| `simulate_launch_plan(client, launch, account=...)` | Re-execute from actual current canonical progress; never reuse old estimates or pretend future deployments exist |
| `build_next_transaction(client, launch, account=...)` | Reread confirmed and latest progress, revalidate eligibility/funding/opening state, resimulate, and return only the next admitted command |
| `read_launch_progress(client, plan, confirmations=..., transaction_hashes=...)` | Recover the authoritative phase, deterministic token, exact preparation count and canonical/reorged/replaced receipt evidence |
| `read_launch_markets(client, plan, offset=..., limit=...)` | Paginated full canonical pool/position identities plus actual manager, liquidity, permanent-custody owner and activation-time oracle reads |
| `preview_lifecycle_fees(client, hub, executor=...)` | Run the hub's exact non-view, **no-argument** `claimAndSplit()` through `eth_call` with the intended executor |
| `prepare_pool_bound_lifecycle_plan(client, plan, cancel_event=..., on_progress=...)` | Async offchain salt finalization of config-3 markets with exact certified factory/initcode/source-chain checks; returns a new immutable plan |
| `read_pool_bound_hook_deployment(client, plan, market_index=...)` | Read verified factory/initcode/salt/predicted-hook metadata even for an unmined draft |
| `build_pool_bound_hook_deployment_transaction(client, plan, market_index=...)` | Build optional typed permissionless predeployment calldata; does not sign, bind or initialize a pool |

All are exported from `black_market_sdk`. Pure helpers include
`launch_plan_from_dict`, `launch_plan_to_dict`, `encode_launch_plan`,
`hash_launch_plan`, `launch_id_of`, `build_lifecycle_calldata`, and the exact
`encode_lifecycle_v4_market_config`, `encode_lifecycle_pool_bound_v4_market_config`
and `encode_lifecycle_abyss_market_config`
encoders. Public immutable input types are `LaunchPlanV1`,
`LifecycleTokenConfig`, `LifecycleAssetFunding`, `LifecycleFeeAssetPolicy`,
`LifecycleMarketConfig`, `LifecycleInitialBuy`, and the venue-specific
`Lifecycle*MarketConfig` / `Lifecycle*Position` types.

`LifecycleV4MarketConfig` is the lifecycle V2 fee-and-oracle schema:
`version`, `lp_fee_pips`, `tick_spacing`, `sqrt_price_x96`, `hook_fee_pips`,
`fee_mode`, `protocol_fee_denominator`, `treasury`, `external_liquidity_disabled`,
`oracle_config_id`, then unchanged positions (ticks/liquidity/salt/token maximum).
Shared V4 remains fully supported with profile
`V4_LIFECYCLE_PROFILE_ID = keccak256("black-market.v4-lifecycle-market.v3")`,
outer `config_version=2` and inner `version=2`. The `.v3` profile suffix is not
config version 3. Retired lifecycle profiles/old versions are rejected, not
reinterpreted; fee-only V1 remains a separate unchanged deployment. Exact shared ABI:

```text
(uint16,uint24,int24,uint160,uint24,uint8,uint8,address,bool,bytes32,(int24,int24,uint128,bytes32,uint256)[])
```

Use the canonical factory's admitted oracle ID in the committed config:

```python
from dataclasses import replace
from black_market_sdk import encode_lifecycle_v4_market_config

config = replace(reviewed_v4_config, version=2,
                 oracle_config_id=admitted_canonical_oracle_config_id)
encoded_config = encode_lifecycle_v4_market_config(config)
# Commit encoded_config with outer config_version=2 and the approved V2 profile.
```

### Additive pool-bound selection and offchain finalization

`LifecyclePoolBoundV4MarketConfig` uses inner `version=3` and outer
`config_version=3`, adding `hook_salt` immediately before the unchanged positions.
Use `V4_POOL_BOUND_LIFECYCLE_PROFILE_ID =
keccak256("black-market.v4-pool-bound-lifecycle-market.v1")` and
`V4_POOL_BOUND_LIFECYCLE_ADAPTER_ID =
keccak256("black-market.adapter.v4-pool-bound-lifecycle.v1")`. Exact ABI:

```text
(uint16,uint24,int24,uint160,uint24,uint8,uint8,address,bool,bytes32,bytes32,(int24,int24,uint128,bytes32,uint256)[])
```

The certified bound profile's zero hook address means the exact typed factory
derives the root, not arbitrary hook selection. Each immutable root serves one
token/quote/fee PoolKey; every position of that market uses it, foreign IDs/keys
are rejected and different bound quotes get different roots. At most one V4
market per quote is permitted across shared/bound offerings, fees and salts;
put all ranges for that quote in one market's positions. Abyss may share the quote.

The following callable example converts a complete reviewed draft's selected
market using a valid `LifecycleV4MarketConfig`. It retains that market's quote,
token budget and reviewed position economics. Prices/ranges must already be
valid for the predicted token's actual orientation and require zero quote deposit:

```python
import asyncio
from dataclasses import fields, replace
from black_market_sdk import (
    LaunchPlanV1, LifecycleV4MarketConfig, LifecyclePoolBoundV4MarketConfig,
    V4_POOL_BOUND_LIFECYCLE_ADAPTER_ID, V4_POOL_BOUND_LIFECYCLE_PROFILE_ID,
    encode_lifecycle_pool_bound_v4_market_config,
    prepare_pool_bound_lifecycle_plan,
)

async def finalize_bound_market(
    client, draft: LaunchPlanV1, market_index: int,
    reviewed_config: LifecycleV4MarketConfig,
    cancel_event: asyncio.Event | None = None,
):
    if not 0 <= market_index < len(draft.markets):
        raise ValueError("market index outside plan")
    economics = {
        field.name: getattr(reviewed_config, field.name)
        for field in fields(reviewed_config) if field.name != "version"
    }
    config = LifecyclePoolBoundV4MarketConfig(
        **economics, version=3, hook_salt=bytes(32),
    )
    markets = list(draft.markets)
    markets[market_index] = replace(
        markets[market_index],
        adapter_id=V4_POOL_BOUND_LIFECYCLE_ADAPTER_ID,
        profile_id=V4_POOL_BOUND_LIFECYCLE_PROFILE_ID,
        config_version=3,
        config=encode_lifecycle_pool_bound_v4_market_config(config),
    )
    return await prepare_pool_bound_lifecycle_plan(
        client, replace(draft, markets=tuple(markets)),
        cancel_event=cancel_event,
        on_progress=lambda p: print(
            p.market_index, p.attempts, p.salt, p.predicted_hook,
        ),
    )
```

All quantities are integer base units, never floats: `1_000_000` represents
one token only for six decimals; liquidity is uint128, and `1 << 96` is a
Q64.96 raw-unit price ratio of 1, not an arbitrary valid opening-price choice.
Freeze chain/core/creator/nonce, token configuration, sorted fee policies
including the predicted token, positions, budgets, funding and ordered buys
before mining. Call `cancel_event.set()` to stop local mining
(`asyncio.CancelledError`), not to cancel an onchain launch.

The async helper is draft-only and returns `PreparedPoolBoundLifecyclePlan`.
Persist its **returned** `.plan` through `launch_plan_to_dict`; review `.deployments`,
not an old draft salt. Economic edits change the constructor/initcode commitment
and invalidate mining. A salt-only edit preserves normalized market commitment
and deterministic token identity, while the signed plan commits the real salt.
Never re-finalize a begun launch's immutable economic commitment.

Exact source-chain certification covers registry topology, adapter/core/dependency
runtime hashes, typed factory, STOP-prefixed creation-code chunks, locally
reconstructed initcode and CREATE2 prediction. The 18-word constructor binds
manager/registrar/oracle factory/core/owning locker, token/quote, fees/spacing/
opening price, policies, market commitment and position count. `hook_salt` is
used verbatim with that factory/initcode, not passed to the constructor. Required
callback bits `0x1afc` under mask `0x3fff` do not establish topology by themselves.
There is no onchain search; shared deployment already mines offchain too,
so this does not establish a newly saved mining-gas cost.

In an async application, use the finalized result with the normal planner:

```python
from black_market_sdk import (
    build_pool_bound_hook_deployment_transaction, plan_launch,
    build_next_transaction, read_launch_progress,
)

async def review_bound_launch(
    client, draft, market_index, reviewed_config, limits, fork,
):
    cancel_event = asyncio.Event()
    finalized = await finalize_bound_market(
        client, draft, market_index, reviewed_config, cancel_event,
    )
    # Optional calldata for a not-yet-deployed exact hook; no broadcast here.
    predeploy = build_pool_bound_hook_deployment_transaction(
        client, finalized.plan, market_index=market_index,
    )
    print(predeploy.as_transaction(), predeploy.deployment)
    # Application separately reviews/admits/signs any predeploy transaction.
    # Omit predeploy entirely to let adapter preparation deploy the hook.
    launch = plan_launch(
        client, finalized.plan, account=finalized.plan.creator,
        mode="staged", limits=limits, fork=fork,
    )
    progress = read_launch_progress(client, finalized.plan)
    step = build_next_transaction(
        client, launch, account=finalized.plan.creator, fork=fork,
    )
    return launch, progress, step
```

The next-step builder compares the reviewed token factory address/runtime hash
and each bound market's exact deployment metadata again. A changed binding
requires a new explicit review; it is not silently replaced. Optional predeploy
is permissionless and may precede token creation, but preparation adopts only
exact typed-factory provenance: nonzero `deployedCodeHash(hook)` equals live
runtime hash, exact constructor/key/commitment, and unregistered/uninitialized/
empty bindings. Code presence or self-reported getters alone are insufficient.
Predeployment does not register a collector, initialize a pool, seal custody
or activate a launch, and its gas/code-deposit cost is not erased.

The current graph has three adapters/six profiles: shared V4, pool-bound V4 and
four Abyss profiles. It retains the shared root, adds bound typed deployer/code
chunks, its owning locker and adapter, and uses the common collector factory.
Stack deployment does not create a per-launch bound root. Offering switches are
explicit `sharedV4`, `poolBoundV4`, `abyss`; quote/fee permissionlessness and
funding-conversion restrictions above are unchanged.


The shared root is `SharedLaunchFeeHookV2` / `SharedLaunchFeeHookDeployerV2`.
The independent bound root is `PoolBoundLaunchFeeHookV1` /
`PoolBoundLaunchFeeHookDeployerV1`. Both reuse `fees/v2/V4FeeCollectorV2` and
`launch/fees/v2/V4FeeLiquidityLockerV2` custody with each adapter's own locker.
Fee-only V1 contracts and historical hooks stay unchanged. Each root binds
manager, adapter registrar and canonical Abyss factory oracle authority.
Registration snapshots `oracleConfigs(id)` once, with movement **1..887272** and
cardinality cap **2..4096**. On shared V4, the unchanged real `TruncatedOracle`
maintains independent full-PoolId state and observations; pre-swap/pre-active-liquidity sampling is once
per block, before zero/wrong-currency fee returns, with quote-normalized clamping.

Root APIs are `observeTruncated(poolId, secondsAgos)`,
`increaseObservationCardinalityNext(poolId, requested)` and `oracleInitializedAt(poolId)`.
Initial populated/prepared cardinality is **1**; permissionless capped monotonic lazy
growth does not populate fabricated history. `readMarket().oracleReadyAt` reports
actual genesis, not maturity, on both venues: atomic history starts in that transaction;
staged history exists only since preparation, and pre-genesis reads fail. Token
restrictions and canonical opening-state continuity remain enforced, without a swap gate.
V4 additions are custody-only until registrar-only `completePoolOpening`; afterwards
the committed `externalLiquidityDisabled` policy applies unchanged, so `False` preserves
external-liquidity support. `openingCompletedAt` is not oracle genesis or maturity.
Trader deltas, ERC6909/liabilities, floored treasury accrual and exact burn/take
settlement/permanent custody remain preserved.
Bound V4 preserves the same indexed oracle/custody/fee selectors with exact-bound-ID
checks rather than multipool state. Pull owner credits and rewards below are reused.
The lending `UniswapV4PriceFeed` remains unchanged: exact key, maturity, depth and
recency checks are still required; genesis is not lending readiness.

### Prior shared-V2 restoration evidence

The following counts are prior runs, not final bound Python/UI verification:
[review](../black-market/docs/launch-lifecycle-v1-review.md#lifecycle-v4-oracle-restoration-evidence),
[contract results](../black-market/contracts/evidence/lifecycle-v4-oracle-restoration/contract-proof-results.json)
and [non-test runtime JSON](../black-market/contracts/evidence/lifecycle-v4-oracle-restoration/sdk-non-test-oracle-launch-lifecycle-v4-oracle-proof-7.json).
The 147 unique named tests had final passing evidence across those runs (146 initial
passes plus one targeted test-ordering correction), not a single all-green 147 run;
fresh live Kyber proof separately passed **9/9** at block **78750379**.
The complete graph for that prior run was
[`launch-lifecycle-v4-oracle-proof-7`](../black-market/contracts/deployments/local/launch-lifecycle-v4-oracle-proof-7/manifest.json).
Its [actual runner](../black-market/contracts/evidence/lifecycle-v4-oracle-restoration/sdk-runtime-attempt7.command.json)
exited **0**, including both SDKs, actual Chromium, cross-block ordinary router swaps
and rollback-negative deployment capture. The
[runtime summary](../black-market/contracts/evidence/lifecycle-v4-oracle-restoration/sdk-runtime-summary-launch-lifecycle-v4-oracle-proof-7.json)
records Python **13 plans: 3 Active (atomic ERC20, staged ERC20, staged ERC404),
10 refusals**, real orphan acknowledgement/recovery and cancellation after revocation,
and both venues' genesis/pre-genesis reads. The full offline Python suite passed
**133/133** in that prior run. Oversized indivisible activations were truthful refusals
under its local **16,000,000** account/RPC stress envelope, not production constraints
or topology-wide unsupported claims. Historical Ethereum portability bounds were
**24,576 / 49,152 bytes**, chain transaction gas **16,777,216**, block gas **30,000,000**.
That run's actual browser proof passed **2/2,
exit 0, zero console/page errors**: atomic Active and staged ERC404 Ready/reload → Active
with all buys/NFTs; exact submitted calldata and explicit fresh identity differences
are retained in the summary. Ordinary non-test swaps in blocks **92/93** observed
spot **3930 → 4091 → 4252**, truncated **0 → 17 → 34**, cursor **0 → 1 → 2**,
capacity **1 → 4** and pre-genesis rejection without fabricated history.
Older V1 graphs/browser results and intermediate -5/-6 attempts remain historical;
-6 passed two browser tests but its runner failed a throwaway post-test bind check.
The [operations recipe](../black-market/docs/launch-lifecycle-v1-operations.md#settled-oracle-restoration-proof--7)
discloses removed verification-only callbacks while retaining exact command metadata.
The [final summary](../black-market/contracts/evidence/lifecycle-v4-oracle-restoration/final-summary.json)
recorded **no proof blockers** for that run, no production broadcast/default rebinding.

### Completed non-UI named-topology proof

The **actual Python SDK entrypoint** now completed all **61 original-plus-catalogue
fixtures**: **50 Active, ten authenticated terminal OOG stress refusals, one distinct
SDK-policy refusal**. **All32 one-opening-swap catalogue rows reached Active**:
Anchor/Ladder/Orbit/Rocket/Cruise/Spread/Depth/Bundle, each on shared/config2 and scalar
pool-bound/config3 in explicitly requested atomic/staged modes. Each core keeps its
complete inventory allocation and mandatory tail, one V4 market, exact token/nonce,
positions, funding and one-buy economics across offerings. Optional Abyss is a separate
pool, not an extra V4 LP or required mixed-venue substitute.

Python captured **212 actual command receipts plus34 refusal-probe receipts**, actual
orphan acknowledgement/recovery, cancellation after revocation, and deployment evidence
rejection after real rollback. Per-fixture exact state-root/funding restoration preserves
source account state without top-ups or reducing economics. The changed smoke's boundary
regressions passed **13/13**.

The original synthetic `atomic-erc404-q1` is **SDK-policy-refusal**, not receipt OOG or
actual receipt-headroom exhaustion: original SDK required envelope **30,562,386** buffers
to **33,618,625 > 32M**, while the successful exact-ceiling probe consumes **27,253,523**
buffered to **29,978,876 < 32M**. Original stateful calls/required gas and same-block,
same-transaction actual receipts are archived separately; the original plan stays
unadmitted. Probe success neither bypasses the SDK nor silently changes mode.

The completed paired gas report has **16/16 one-swap pairs /32 Active offerings,
96 launch receipts /300 locked manager positions**; bound marginal cost was
**5,154,553–5,185,959 gas higher per pool**. Its code deposit is already included.
Full graph setup **121,446,084 gas** is not separately measured standalone bound-only
provisioning. The actual Node smoke also completed all32 catalogue rows Active;
Python was not substituted with Node execution of Python-labelled fixtures.
See [exact gas/SDK outcomes and source evidence](../black-market/docs/launch-lifecycle-v1-operations.md#completed-one-opening-swap-topology-proof).

Successful gas/Node evidence was preserved in retry2, whose runner later exited1 at
Python. Only incomplete Python/reorg phases were rerun on a fresh captured graph:
python-recovery2 exited0 with `sdks=["python"]`, `gasComparison=null` and deployment-reorg
rejection. **No single uninterrupted both-SDK runner success is claimed.** Both owned
ports28735/28736 closed. The controlled Cancun31337 policy remains **32M for block,
chain transaction, RPC and account plus separate1,000-bps headroom**, with24KB/48KB
Ethereum portability rather than Robinhood96KiB/192KiB limits. Normalized preset
characteristics are not absolute USD market-cap proof. No UI/browser/app or live
broadcast occurred; pinned getters do not prove live provider/account/Nitro admission.

#### Retained diagnostic and synthetic stress scope

The earlier [canonical-32M original-topology diagnostic](../black-market/contracts/deployments/local/pool-bound-runtime-20261003-canonical-probe-b/canonical-mixed-measurement.json) deployed/captured real AMMs
and executed **72 exact original-mode rows: 52 Active, 20 genuine atomic OutOfGas**;
**all 48 staged paths reached Active**. These are built Node executions of both
fixture origins, **not actual Python executions or final browser proof**.
Final rebuilt-SDK/browser and paired-gas verification is not established by that
diagnostic. No gas-saving total is claimed. Paired reporting must cover all 12
position pairs and original mixed pairs, including actual setup/CREATE2 code
deposit and maximum individual transaction; shared full-graph setup is not a
separately measured standalone bound-only architecture cost.

**Historical synthetic multi-buy cost caveat:** the completed final-c stress measurement recorded
**30 pairs (24 complete, 6 with refusals), 50 Active / 10 atomic OutOfGas,
329 receipts and 607 creations**. Full captured-graph setup was
**121,446,084 gas**. Successful **V4-only** bound launches added approximately
**5.12–5.23 million gas per V4 root** versus matched shared launches. Original
mixed two-quote pairs instead added approximately **10.82 million gas across
two V4 roots** (about **5.41 million per root**); the V4-only range is not universal.
Bound cost more in every successful matched pair: it is an isolation/topology
choice, not a demonstrated gas optimization. The bound hook's
**24,564-byte runtime / 4,912,800-gas code deposit** is already included in
preparation/atomic receipts and must not be added twice.
V4-only q1/q2/q3 plans contain 3/4/5 opening buys; mixed plans contain 4/6/8.
These retained stress rows are not exactly-one-opening-swap product gas comparisons.

See the durable [paired gas results](../black-market/docs/launch-lifecycle-v1-operations.md#measured-sharedbound-gas-on-the-captured-graph)
for exact setup/launch, maximum-transaction and marginal costs; mixed atomic
refusals have no invented complete-launch totals/deltas. The shared full graph
is not separately measured standalone bound-only provisioning. These controlled
EVM receipts are **not actual Python or final browser proof**, full Nitro
execution or proven live RPC/account admission. Current 32M policy/provenance
and independent headroom qualifications below remain mandatory.

### Commitment and execution

The portable JSON uses the exact Solidity camelCase fields; large quantities
are decimal strings, never floats. The hash is
`keccak256(abi.encode(keccak256("BLACK_MARKET_LAUNCH_PLAN_V1"), plan))`.
Launch identity is
`keccak256(abi.encode(chainId, orchestrator, creator, nonce))`.
Token prediction uses this domain and token economics, **not** block randomness
or fee-asset addresses. `predict_launch_token` may be called on a draft plan
containing its quote policies; then include that exact predicted token in the
ascending, unique fee-asset set. There is no zero-address token sentinel.

New V2 hubs cap the initial `executor_fee_bps` at 1,000 (10%). The current registered
fee owner can use `LAUNCH_FEE_HUB_V2_ABI` to call `setExecutorFeeBps(uint16)` immediately.
`executorFeeBps()` reads the current rate; `MAX_EXECUTOR_FEE_BPS()` returns 1,000,
and `ExecutorFeeUpdated` records updates. Accepted ownership transfers move this
authority. The owner/rewards/burn fractions remain fixed. Each harvest snapshots
one rate for all freshly collected fees, including earlier accrual; old owner credits
are unaffected. Callback updates are blocked. Preview is not a minimum-payment
guarantee after an owner rate change. Existing immutable hubs and fee-only V1 remain
unchanged.

New lifecycle hubs always reserve owner allocations rather than pushing them.
With `LAUNCH_FEE_HUB_V2_ABI`, read `claimableOwnerFees(owner, asset)` and call
`claimOwnerFees(asset, recipient)` as the credited owner. Withdrawal neither
harvests pools nor charges a bounty; old credits remain with the old owner after handoff.

`LIFECYCLE_DIVIDEND_V1_ABI` exposes new ERC20/ERC404 dividend tokens'
`dividendBountyBps()` and `setDividendBountyBps(uint16)`. The current registered fee
owner controls this independent setting, initially zero, capped at 1,000 bps.
Third-party `claimFor` / `claimRange` pays the caller
`floor(gross_dividend * bounty_bps / 10_000)` per asset and the beneficiary the remainder.
Self-claims receive the full amount; zero still permits third-party payout. One
current-rate snapshot applies to every asset and previously accrued dividends,
without an opt-in, delay or minimum-payment guarantee. `DividendBountyUpdated`
records rate changes and `RewardClaimBountyPaid` records the caller payout.
`earned` / `pendingRewards` are gross; claim returns, `RewardPaid` and
`lifetimeRewardsPaid` are net beneficiary receipts. `MULTI_ASSET_REWARDS_V1_ABI`
also supports staking claims, which have no payout bounty. These ABIs are package-root
exports for the opt-in lifecycle; legacy reward contracts are not relabeled.

Funding outputs and 1–8 fee assets are strictly ascending and never silently
sorted. Fee dispositions sum to 10,000; burn is launch-token-only. Creator,
payer, and refund authority are identical. Token budgets cannot be reused
across markets. Funding approvals go to the core's **`fundingEscrow()`**, not
the core or adapters, and aggregate actual committed ERC-20 input amounts,
including conversion inputs. Native wrapping and allowlisted swaps use their
exact committed native value, target and calldata. No approval permit support
is inferred from an arbitrary ERC-20.
Reward-enabled lifecycle ERC20 supply (Staking/Dividends) is bounded by `10**77`
to preserve the module's fractional accounting domain. `LifecycleRewardMode.NONE`
(enum value 0) retains the full uint256 ERC20 supply domain; ERC404 retains its
stricter on-chain uint96/NFT bounds. Reward amounts remain uint256, subject to
actual custody and cumulative-counter limits; the economic tuple/hash is unchanged.

`mode="atomic"` means a dedicated `launchAtomic(plan)` transaction (plus any
separate required approvals). `mode="staged"` means `beginLaunch(plan,1)`,
ordered contiguous `prepareMarkets(plan,first,count)` calls, then **one**
indivisible `activateLaunch(plan)`. Mode and preparation grouping do not change
economics. An atomic refusal requires separate explicit staged consent; an atomic
plan is never silently converted to staged. Only oversized
preparation groups are partitioned; the SDK never splits final mint/lock/buys
or opens a partial set of markets. Both canonical venues support atomic and
staged execution with the identical capability mask
(`LAUNCH_REQUIRED_CAPABILITIES_V1`, TOKEN_ONLY|EMPTY_PREPARE|PERMANENT_CUSTODY|
CANONICAL_FEES; ERC404 tokens additionally require the ERC404 bit). The retired
POOL_GATE bit is never requested.

### Actual sequential simulation and current limits

The normal RPC backend is `eth_simulateV1`, with one fresh transaction per
simulated block, preserving prerequisite-produced deployments, balances and
storage without cross-transaction warming/transient-storage savings. Missing
approval transactions are included. A high-cap `validation:false` pass is
**measurement only**; admission requires a second exact, buffered-gas pass with
`validation:true`, real fees/nonce/funding, and matching lifecycle postconditions.
This also avoids assuming `gasUsed` alone proves EIP-150 forwarded-gas sufficiency.
No account balances, contract code, pool state or allowances are overridden.

`LaunchExecutionLimits` combines the current block gas limit with supplied
chain per-transaction, RPC and account gas caps; it also enforces supplied
calldata/account-data, RPC request-size and aggregate RPC simulation-gas caps.
Verified admission requires an explicit current chain transaction gas cap, RPC
transaction gas cap, account transaction gas cap, and resolved calldata ceiling
(including EIP-7825 where enabled). There is no historical 32M promise.
Successful stateful execution is not proof of unknown production RPC/account
ceilings: `confidence="stateful"` may coexist with `admitted=False` and
`unknown_constraints`. `build_next_transaction` refuses that result.
`limits` may also be a `LifecycleLimitResolver(client, context)` callable;
the `LaunchLimitContext` includes the actual block, chain, orchestrator, account
and RPC endpoint. It is invoked afresh for every plan/simulation/next-command
build. Known admission requires both the four caps and matching
`observed_block_number`, `observed_block_hash`, `chain_id`, `orchestrator`, and
`account` stamps, even for a static limits object. Missing stamps remain unknown;
stale or mismatched observations cannot authorize submission. An unavailable
resolver reports unadmitted constraints without hiding canonical progress.
Reported buffered limits use
integer-ceiling headroom. Each step includes dependencies, expected
postconditions, estimate block number/hash, confidence, gas estimate/limit,
current gas price, estimated/maximum execution fee and optional data fee.
`data_fee_estimator` is caller-supplied for chains exposing a genuine data-fee
quote; unsupported data fees are `None`, never zero-valued fake estimates.

The current manifest copies `limitsProvenance` to `executionLimits.provenance`.
Review it alongside every cap. The chain-31337 controlled Anvil policy explicitly
sets block/chain-transaction/RPC/account limits to **32,000,000** gas with separate
**1,000-bps** headroom. This is a controlled Cancun EVM model, not full Nitro
execution or proof of live RPC/account ceilings.

Read-only Robinhood chain-4663 block **79595428**, hash
`0x563abc99145d922ae21aaa08e21fcc7d04e3cbe8781d89beacdd975085534f83`,
reported ArbOS **116** and ArbGasInfo `getMaxTxGasLimit()` /
`getMaxBlockGasLimit()` both **32,000,000**. Live RPC/account ceilings remain
**unknown**, not inferred from getters. Nitro separates L1 poster-data gas from
compute gas; ArbOS >=50 block accounting permits `PerBlock + PerTx`, so getters
are neither a universal RPC envelope nor a strict aggregate receipt-gas limit.
See [Nitro block accounting, lines 514–547](https://github.com/OffchainLabs/nitro/blob/master/arbos/block_processor.go#L514-L547)
and [Robinhood gas and fees](https://docs.robinhood.com/chain/gas-and-fees/).
Ethereum **24,576 / 49,152-byte** runtime/initcode bounds are retained only for
portability; Robinhood documents **96 KiB / 192 KiB**. Prior 16M/16,777,216
archives are optional local stress evidence, never production limits.


If stateful simulation is unavailable, the result is `confidence="provisional"`
and `admitted=False`; dependent `eth_call`/`eth_estimateGas` calls are not
substituted as end-to-end proof. Direct contract-account/wallet-batch routes are
also unadmitted: the backend does not prove wallet signatures or contract-sender
execution. Outstanding account nonces must settle before constructing another
canonical sequence.

For an **exclusively owned separate local Anvil**, call
`create_controlled_launch_fork(source_client, fork_client, isolated=True)`.
The two RPC endpoints must differ, including loopback aliases. This is a
configuration helper: simulation validates Anvil capability and resets **only
the separate fork**, when needed, to the exact source block through its explicit
source URL. The refresh URL is independently checked against the planning
source's pinned chain ID, block number/hash and every creator nonce; a different
same-chain endpoint is not trusted merely because reset succeeds. After reset
and immediately before each measurement or exact-gas admission snapshot, those
same dimensions must match on the resulting fork or simulation fails closed
with `LaunchStateChanged`. Only then does it estimate against actual preceding
state, submit real fresh fork transactions, check receipts, and revert its
snapshot in `finally`. The exact-gas admission pass also submits and reverts
actual fork transactions. It never impersonates or
injects funds; an unrestored snapshot is a hard failure. Planning never snapshots,
resets, or sends transactions to the source execution node. Do not pass a shared
node or production client as a controlled fork.

Snapshot cleanup's existing `restored_exactly` predicate tolerates some
same-height changed hashes; it is not proof of exact restored-header identity.
That tolerance is not used as admission evidence: the next measurement or
exact-gas pass independently checks the exact pinned chain/header/nonces and
refreshes a mismatched fork before executing.

### Real local manifest example

The Black Market local lifecycle integration exports an actual deployment
`manifest.json` and `sdk-plans.json`; they contain real V4/Abyss addresses and
Solidity-generated plans/hashes, not synthetic future pools.

```python
import json
import os
from pathlib import Path
from web3 import Web3
from black_market_sdk import (
    LaunchExecutionLimits, LaunchLimitContext, create_controlled_launch_fork,
    launch_plan_from_dict,
    plan_launch, simulate_launch_plan, build_next_transaction,
    read_launch_progress,
)

manifest_path = Path(os.environ["LAUNCH_LIFECYCLE_MANIFEST"])
manifest = json.loads(manifest_path.read_text())
plans_path = manifest_path.parent / manifest["fixtures"]["plansFile"]
fixture = json.loads(plans_path.read_text())["plans"][0]
plan = launch_plan_from_dict(fixture["plan"])
client = Web3(Web3.HTTPProvider(os.environ["LAUNCH_LIFECYCLE_RPC_URL"]))
fork_client = Web3(Web3.HTTPProvider(os.environ["LAUNCH_LIFECYCLE_FORK_RPC_URL"]))
fork = create_controlled_launch_fork(client, fork_client, isolated=True)

def limits(client: Web3, context: LaunchLimitContext) -> LaunchExecutionLimits:
    # Reviewed provenance describes the exclusively owned local nodes only.
    caps = manifest["executionLimits"]
    if caps["provenance"]["scope"] != "controlled-local-measurement":
        raise ValueError("this resolver requires controlled-local policy")
    return LaunchExecutionLimits(
        chain_transaction_gas_limit=int(caps["chainTransactionGasLimit"]),
        rpc_transaction_gas_limit=int(caps["rpcTransactionGasLimit"]),
        account_transaction_gas_limit=int(caps["accountTransactionGasLimit"]),
        max_calldata_bytes=int(caps["maxCalldataBytes"]),
        headroom_bps=int(caps["headroomBps"]),
        observed_block_number=context.block.number,
        observed_block_hash=context.block.block_hash,
        chain_id=context.chain_id, orchestrator=context.orchestrator,
        account=context.account, source="owned local Anvil manifest",
    )
launch = plan_launch(
    client, plan, account=plan.creator, mode="staged", limits=limits,
    prepare_batch_size=1, fork=fork,
)
proof = simulate_launch_plan(client, launch, account=plan.creator, fork=fork)
if not proof.admitted:
    raise RuntimeError("; ".join(proof.reasons))
step = build_next_transaction(client, launch, account=plan.creator, fork=fork)
if step is not None:
    print(step.kind, step.as_transaction())  # wallet/signing remains caller-owned
print(read_launch_progress(client, plan).phase.name)
```

The transaction builder itself never broadcasts. The full actual local smoke
deliberately does, restricted to loopback Anvil with an explicit manifest:

```sh
LAUNCH_LIFECYCLE_RPC_URL=http://127.0.0.1:8545 \
LAUNCH_LIFECYCLE_FORK_RPC_URL=http://127.0.0.1:8546 \
LAUNCH_LIFECYCLE_ALLOW_LOCAL_EXECUTION=1 \
  .venv/bin/python examples/launch_lifecycle_smoke.py /absolute/manifest.json
```

It exercises both token kinds and venues, atomic/staged actual SDK-generated
sequences, reloads, confirmation gating, orphaned receipts, registry retirement
and exact launch-isolated cancellation refunds, then reads live custody and
previews every canonical fee asset. This is local integration, not production
admission or publication.

The Solidity exporter declares each row's expected positive/negative outcome. Every
expected-positive row must actually become `Active`, including staged ERC20/ERC404
coverage by decoded token kind. A negative row passes only with numeric measured
headroom/cap evidence for the indivisible step, an exhausted exact-ceiling receipt, or
an authenticated terminal failing `callTracer` chain reaching `OutOfGas` at that cap.
Generic RPC/estimator reverts, staging advice, affordability and unrelated ABI/identity/
funding/oracle failures abort. If SDK evidence is opaque, the smoke replays the exact
sequence at the unchanged cap only on the separate owned fork, records per-row
`gasEvidence`, and resets its pinned head. It never changes source balances/code/allowances.

### Recovery and cancellation

Always reread progress after a receipt, replacement, wallet change or reload.
The SDK checks receipt block hashes against canonical blocks and validates the
actual encoded full-plan transaction; local counters never advance a step.

Submit one confirmed step, retain its hash, and gate the next step on canonical
confirmation evidence:

```python
from web3 import Web3
from black_market_sdk import (
    decode_lifecycle_events, build_next_transaction, read_launch_progress,
)

step = build_next_transaction(client, launch, account=plan.creator, fork=fork)
if step is not None:
    # Signing/broadcast is caller-owned; the SDK only builds the exact call.
    sent = client.eth.send_transaction(step.as_transaction())
    receipt = client.eth.wait_for_transaction_receipt(sent, timeout=180)
    if receipt["status"] != 1:
        raise RuntimeError("lifecycle step reverted; reread canonical progress")
    # Lifecycle events are validated against this plan's identity/hash/creator.
    events = decode_lifecycle_events(plan, receipt["logs"])
    assert events, "the mined step emitted no matching lifecycle event"
    tx_hash = Web3.to_hex(receipt["transactionHash"])
    # The next builder refuses while this receipt is not sufficiently confirmed;
    # a reorged or replaced hash surfaces as such, never as silent progress.
    progress = read_launch_progress(
        client, plan, confirmations=2, transaction_hashes=[tx_hash],
    )
    next_step = build_next_transaction(
        client, launch, account=plan.creator, confirmations=2,
        transaction_hashes=[tx_hash], fork=fork,
    )
```

The SDK checks receipt block hashes against canonical blocks and validates the
actual encoded full-plan transaction; local counters never advance a step.
Approval receipts are validated against committed input assets and the immutable
funding escrow, but remain prerequisites, not evidence of market preparation.
If latest state includes a lifecycle transition **or creator nonce change**
that is not yet sufficiently confirmed, the next builder refuses to advance.
A changed chain/account/plan is rejected. Estimate/read blocks are checked again
after RPC work for reorgs.

Created inactive tokens and empty pools are **pending**, not launched. Failed
activation remains retryable `Ready`. For a pending staged launch:

```python
cancel = build_next_transaction(
    client, launch, account=plan.creator, action="cancel", fork=fork,
)
```

Cancellation remains available after deadline expiry or registry retirement and
does not call retired adapters or require renewed market/profile/funding admission.
Keep the original reviewed `launch` (or pass its exact persisted plan and explicit
mode to the builder); do not require a successful fresh `plan_launch` to expose Cancel.
Current cancellation gas/limit admission and confirmation safety still apply.
It refunds only unspent external escrow to the
creator; setup gas/deployed contracts are irreversible. Launch-token inventory
stays inactive or burns according to committed `burnOnCancel`; it is not a
funding refund. `Active` and `Cancelled` are terminal.
In preparation the bound hook/pool may be deployed, bound and initialized empty,
but minting, custody sealing, all ordered buys and final opening are one atomic
activation. A failure rolls those activation effects back together and retains
Ready/retry/cancel safety; retry uses the same commitment. Local mining cancellation
is separate and never substitutes for `action="cancel"` on a pending launch.

Versioned ABI fragments and components live in `lifecycle_abis`: lifecycle core,
registry, directory/lens, token factory, funding escrow, ordinary-call adapter and
generalized `LAUNCH_FEE_HUB_V2_ABI`. The original fee-only V1 and every existing
deployed-version/default-address record remain unchanged. Shared commitment
vectors are in `tests/fixtures/launch-lifecycle-v1.json`, identical to the Node
SDK fixture.

## Historical Atomic APIs

The Atomic recipe/calldata helpers remain available for historical records,
pricing derivation, and byte-for-byte reconstruction:

```python
from black_market_sdk import (
    ROBINHOOD_ATOMIC_LAUNCH_APPLICATION,
    build_atomic_launch_calldata,
)

# This produces historical AtomicLaunchFactory calldata only. It does not turn
# the current UnifiedLauncher into an Atomic factory.
calldata = build_atomic_launch_calldata(historical_atomic_request)
historical_factory = ROBINHOOD_ATOMIC_LAUNCH_APPLICATION.launch_factory
```

Never direct Atomic calldata at `get_addresses(4663).launch_factory`: that
address is the current UnifiedLauncher and accepts `LaunchRequestV3` instead.
`examples/abby_launch.py` uses the explicit historical record and reconstructs
the Abby launch byte-for-byte without signing, simulating, or broadcasting.

## Optional launch API integration

`LaunchApiClient` is opt-in: importing the module creates no client and makes
no network request. Construct `LaunchApiConfig` explicitly to opt in to the
stdlib HTTP transport, or inject a `LaunchApiTransport` for deterministic tests
and offline workflows.

```python
from black_market_sdk import LaunchApiClient, LaunchApiConfig

client = LaunchApiClient(LaunchApiConfig())  # config only; no request yet
```

The caller owns all sensitive lifecycle state:

1. Generate and persist an idempotency key before
   `create_launch_upload_session()`.
2. Prepare the full EIP-712 dictionary with
   `build_launch_attribution_typed_data()` and sign it with the caller's wallet;
   the SDK never signs it.
3. Persist the upload capability returned exactly once at session creation as
   private recovery material. Do not put it in public URLs, analytics, or logs.
4. Send every API-issued presigned-upload header, including the create-only
   `if-none-match: *` condition, via `put_launch_image()`, then call
   `complete_launch_upload()`.
5. Submit the confirmed transaction hash with
   `publish_launch_upload_session()`. A `202` raises `LaunchPublishPending`
   with the returned session and `retry_after_ms`; there is no automatic write
   retry.

Typed-data helpers default to `get_addresses(chain_id).launch_factory`, so a
configured schema-/2 transaction target and signature verifier stay aligned,
including the documented environment override precedence. An unconfigured or
unsupported chain requires an explicit `verifying_contract`.
`build_launch_metadata_update_typed_data()` covers later signed mutable metadata
updates. API response bodies remain typed mapping dictionaries rather than
silently coerced models.

`get_launch_upload_session()` and `renew_launch_upload_url()` support explicit
recovery without recreating a write. `list_launches()` and `get_launch()` read
public projections; `replace_launch_metadata()` and
`create_launch_image_upload()` prepare signed metadata and replacement-image
writes, followed by `put_replacement_launch_image()`. Safe failures expose
`LaunchApiError.status`, `.code`, and optional `.retry_after_ms`; image PUT
failures use `LaunchUploadError`.

## Examples

Every example is offline by default. `launch_submit.py` additionally has an explicit live opt-in; it never signs, creates a client, or broadcasts without `--execute` and a private key from `--private-key`, `--private-key-env`, or the `PRIVATE_KEY` environment variable:

```sh
# Current UnifiedLauncher: Abyss and optimized V4 V3 new-launch construction.
python examples/quickstart.py

# Opt-in API lifecycle, entirely through an injected fake transport.
python examples/launch_api_lifecycle.py

# Historical Atomic pricing math.
python examples/launch_recipe.py

# Historical Abby Atomic calldata reconstruction, byte-for-byte.
python examples/abby_launch.py
```

### Launch matrix preview and single-case execution

`examples/launch_matrix.py` enumerates the canonical current catalog:
34 scenarios across Abyss and optimized V4 V3, each pinned to the
`LaunchTemplateDefaultsV2` token mapping (burnable for standard, quote-staking,
dual-staking, and fee-burn; holder-dividend with the pinned dualRewards flag
for quote-dividends and dual-dividends). Matrix and submit previews are
pure/offline: they do not initialize Web3, an API client, a signer, or an HTTP
connection.

```sh
# List current stable IDs, then build a selected public envelope offline.
python examples/launch_matrix.py --list
python examples/launch_matrix.py \
  --scenario abyss.standard.standard-oracle.burnable.owner-only

# Submit-example previews print only public summaries. With no selection it
# previews the current catalog; use --case or --all to be explicit.
python examples/launch_submit.py \
  --case uniswap-v4-v3.quote-dividends.quote-oracle.holder-dividends-false.rewards-only
python examples/launch_submit.py --all
```

The offline paired-asset price default (`2500 * 10**18`) is illustrative
recipe input only. A live execution with a WETH paired token prices it
automatically from the configured Chainlink ETH/USD feed; a non-WETH paired
token still requires an explicit `--paired-token-usd-price-x18` supplied by
the caller, and the example never fetches or attests an economic price source
for such tokens.

#### Single-case execution with the launch API lifecycle

`--execute` is the only path that creates a wallet Web3 client, signs, sends an
approval, or broadcasts. It requires exactly one `--case ID` — `--all` is an
offline preview only and is rejected with `--execute` — and a private key from
`--private-key`, the safer named `--private-key-env NAME`, or the
`PRIVATE_KEY` environment variable, which is read automatically when no
explicit key is given and is never echoed. There is no mandatory `--rpc-url`:
it defaults to the canonical Robinhood mainnet RPC, and `--rpc-url` remains
available as an explicit override. Prefer a narrowly scoped named environment
variable instead of putting a key in shell history:

```sh
export BLACK_MARKET_LAUNCH_PRIVATE_KEY='0xreplace-with-your-key'

# --execute always performs the launch API lifecycle, then the on-chain
# launch, then publication polling. The paired token is the chain WETH, so its
# USD price is read automatically from the configured Chainlink ETH/USD feed;
# --initial-buy 0.0025 is a decimal scoped by the paired token's decimals and,
# for a live Abyss launch paired with the chain WETH, rides msg.value as a
# native buy: the request carries a zero ERC-20 pairedTokenAmountIn, the
# launch transaction's value is the adapter launch fee plus 0.0025 ETH, and no
# WETH deposit or approval occurs.
python examples/launch_submit.py \
  --execute \
  --case abyss.standard.standard-oracle.burnable.owner-only \
  --private-key-env BLACK_MARKET_LAUNCH_PRIVATE_KEY \
  --chain-id 4663 \
  --paired-token 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73 \
  --initial-buy 0.0025 \
  --fee-pips 10000 \
  --oracle-config-id 0xc0e9bed88d70a13fd3ab31451fefdd073b7266e838aee0ad1c236c8c9eff855d
```

Before its one launch broadcast, the example verifies the RPC chain ID,
runtime code at the effective UnifiedLauncher, its exact on-chain registry
route, enabled adapter binding, selected adapter's Abyss factory fee spacing
and oracle registration. It reads the live Abyss `launchFee()` and
`wrappedNative()`; V4 launch transactions are required to have exactly zero
native value. `--fee-pips` defaults to 10000 (1%) and is validated against
`ABYSS_FEE_TIERS`. A nonzero first buy is a decimal `--initial-buy`
scoped by `--paired-token-decimals`. On a live Abyss launch paired with the
chain WETH it is mapped automatically onto the launch transaction's
`msg.value` as a native buy (launch fee plus the buy amount) with no WETH
wrap, approval, or pull; an explicit conflicting `--native-buy-amount` is
rejected. Otherwise — offline previews, non-WETH paired tokens, and every
V4 route — the ERC-20 path is used: when the paired token is the chain WETH
and the creator's WETH balance is short, the example broadcasts one
`WETH.deposit` of exactly the shortfall and waits for its receipt; if the
launcher allowance is below the buy amount it approves exactly that buy
amount, waits for the receipt, and never grants an unlimited allowance.

For a nonzero first buy, the CLI derives the gross post-buy sqrt-price guard
from the recipe before applying slippage, first simulates the provisional
zero-minimum request, decodes its `UnifiedLaunchReceipt` quote, then
simulates the exact slippage-bounded final request. Those simulated values
are quotes, not launch or lighthouse observations. A launch transaction is
signed with the explicit account, sent once, and never automatically retried
or resubmitted; a failed or unavailable receipt is a recovery handoff, not
permission to send again. In `--execute` mode the launch uses a dynamic

Scenario template, token configuration, fee dispositions, paired asset,
oracle, V4 external-liquidity choice, and the V4 99%/1% allocation are
on-chain immutable launch choices. Abyss value is its current adapter launch
fee plus any native first buy; V4 value is zero. The public result reports
actual receipt token/module addresses and market data. For V4,
`LaunchCompleted.pool` is the V4 hook, not the Abyss companion: the CLI
resolves the 1% Abyss lighthouse pool from the coordinator position manager
and reports the actual V4 locker and protocol fee distributor. Its permanent
custody is verified from the coordinator-owned lock terms; `unlockTime=0`
alone is not interpreted as an infinite time lock.

#### Metadata, image, and publication

`--execute` always performs the complete metadata lifecycle; there is no
separate opt-in. The token name and symbol in API lifecycle metadata are taken
from the exact on-chain request. The description and social fields below are
optional public metadata; a PNG, JPEG, or WebP image is optional and is
uploaded when supplied, its type inferred unless overridden. `--api-base-url`
remains an optional explicit endpoint override.

```sh
python examples/launch_submit.py \
  --execute \
  --case uniswap-v4-v3.quote-dividends.quote-oracle.holder-dividends-false.rewards-only \
  --private-key-env BLACK_MARKET_LAUNCH_PRIVATE_KEY \
  --chain-id 4663 \
  --paired-token 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73 \
  --initial-buy 0.0025 \
  --api-base-url 'https://api.abyss.trading' \
  --description 'Public description for this exact launch.' \
  --website-url 'https://example.invalid/' \
  --twitter-url 'https://x.com/example' \
  --telegram-url 'https://t.me/example' \
  --discord-url 'https://discord.gg/example' \
  --image ./launch-image.png \
  --private-state-output "$HOME/.local/state/black-market/launch.json"
```

The API flow creates and locally signs the SDK EIP-712 attribution, persists
an idempotency key before session creation, sends the direct image PUT with
every API-issued header, and completes it, performing bounded private-session
GET polling until `ready_to_launch` if completion is asynchronous. Only then
does it execute the on-chain sequence above. After a confirmed receipt, it
publishes the public transaction hash. `--private-state-output` is optional
but strongly recommended for `--execute`: the one-time capability is retained
in memory for the run, but writing the `0600` private state file lets a
publish or polling interruption be recovered. A `202` publish response raises
`LaunchPublishPending`; the example waits for the returned `retry_after_ms`,
then performs bounded `get_launch_upload_session()` polling while the status
remains `awaiting_indexer` or `transaction_submitted`, retaining the same
capability, until a non-awaiting status. The launch is confirmed on-chain but
the API projection is not claimed final, and the example does not retry the
publish write.

`--all` is an offline preview only: `--execute` launches exactly one case and
is rejected together with `--all`. There is no batch execution mode.

If the process exits while a publish is still pending, recover it explicitly
with the `--resume-publish` mode, which re-publishes the confirmed transaction
hash and then polls the session to a non-awaiting status. It requires all
three arguments: `--session SESSION_ID`, `--capability CAPABILITY`, and
`--transaction TRANSACTION_HASH`:

```sh
python examples/launch_submit.py \
  --resume-publish \
  --session 0x... \
  --capability 0x... \
  --transaction 0x...
```

Never print, commit, upload, or share a private state file. It may contain the
idempotency key, attribution signature, private upload capability, session ID,
and transaction hashes needed for recovery. The CLI never writes a private key,
signature, capability, presigned URL/query, or credential-bearing RPC/API URL
to stdout. If image completion, broadcast receipt, or publish fails, preserve
that file and recover deliberately with `LaunchApiClient.get_launch_upload_session()`,
`renew_launch_upload_url()`, or `publish_launch_upload_session()` as appropriate;
do not recreate a session or rebroadcast merely because a later step failed.

## Package layout

| Module | Contents |
| --- | --- |
| `black_market_sdk.addresses` | Chain IDs, canonical records, current Unified routes, environment overrides |
| `black_market_sdk.unified` | Frozen V3 request/config records, exact route conversion, pure calldata/transaction builders |
| `black_market_sdk.unified_abis` | Current UnifiedLauncher, registry, adapters, hook deployers, and V2-stack ABI/component constants |
| `black_market_sdk.launch_api` | Explicit launch API client, EIP-712 preparation, direct-upload helpers |
| `black_market_sdk.launch` | Historical Atomic templates, pricing recipes, and calldata builder |
| `black_market_sdk.abyss` | Abyss DEX primitives, historical launch ABIs, pool profiles, fee tiers |
| `black_market_sdk.abis` | Lending-market ABIs |
| `black_market_sdk.live` | Reserve/user normalization from lens and data-provider rows |
| `black_market_sdk.format` | RAY/WAD math and display helpers |
| `black_market_sdk.client` | web3.py client factories |

## Development

```sh
pip install -e ".[dev]"
pytest
```

## Releasing

Releases are published to PyPI from GitHub Actions with [trusted
publishing](https://docs.pypi.org/trusted-publishers/) (OIDC); no API tokens or
passwords are stored in this repository.

1. Bump `version` in `pyproject.toml` and commit.
2. Cut a matching git tag (for example, `v0.1.0`) and create a GitHub Release.
3. The publish workflow verifies the tag, builds/checks the sdist and wheel,
   then uploads through OIDC.

The publish job declares `environment: pypi`; configure it with the desired
reviewers and allowed branches/tags, and register the workflow as a trusted
publisher in PyPI.

## License

MIT
