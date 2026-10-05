![Abyss header](assets/abyss-header.png)

# black-market-sdk

Python SDK for Black Market: reviewed multi-market lifecycle launches, explicit
launch-metadata API integration, Abyss DEX primitives, and lending-market helpers.
Built on [web3.py](https://web3py.readthedocs.io). Python 3.10+.

## Install

```sh
pip install black-market-sdk
```

## Lifecycle cutover

The launch API is `LaunchPlanV1` with the V2 implementation registry and
V3 source-aware fee economics. Historical Atomic/Unified builders, template
catalogs, conversion helpers, fee-only ABIs, old launch-address views and V4
profile/adapter ID constants have been removed. Old shared config2 and bound
config3 are rejected, not converted or supported through fallback selectors.
This SDK change does not modify immutable deployments or existing pools.

Every plan names its orchestrator, creator, chain, nonce, deadline and complete
economics. No production orchestrator, developer rate, or execution mode is
selected implicitly. Importing the SDK never signs, submits or publishes anything.
Unrelated Abyss pool/router/custody and lending utilities retain their meanings.

### Registry discovery and admission

```python
from black_market_sdk import read_lifecycle_profiles

profiles = read_lifecycle_profiles(client, orchestrator=orchestrator, offset=0, limit=100)
for profile in profiles:
    print(profile.id, profile.venue_kind, profile.admitted, profile.reason)
    if profile.envelope is not None:
        print(profile.envelope.beneficiary, profile.developer_terms,
              profile.protocol_maximum_developer_fee_bps)
```

Enumeration calls the registry's exact `profileIds(offset,limit)`, `profile`,
`adapter`, `profileTopology` and `requireEligible` getters. IDs are registry data,
not SDK allowlists. The supported codecs are shared V4/schema4,
pool-bound V4/schema5, and canonical Abyss/schema1. Unknown schemas
produce an explicit unsupported-schema reason, distinct from position bounds or
missing multi-position capability. Admission verifies adapter runtime and core/dependency binding.
V4 additionally verifies the frozen envelope, live developer terms and exact admitted
graph. Canonical Abyss has no author envelope or developer terms; its config-1 graph
is certified by the registry. Disabled offerings remain discoverable but cannot launch.

`LifecycleProfile.envelope` is a `LaunchEnvelopeV2` dataclass with
`LaunchBoundsV2` and `LaunchGraphV2`. `developer_terms` contains
`adapter`, stable `beneficiary`, `maximum_developer_fee_bps`, `terms_digest`, and
current `enabled`. `topology` remains the registry-certified `ProfileTopologyV1`.
Both V4 topologies record their deployer and creation-code hash.

The keystore-only `DeployPoolLaunchV1` installs fixed pool-bound V4 and canonical Abyss:
two adapters and five automatically admitted profiles. The legacy certification child and
arbitrary unsigned V4 admission are absent. Registry ABI reads use `profileEnvelope` / `profileId`;
V4 uses signed six-argument `registerProfile`. Admin-only
`registerAbyssProfile(uint8,registration)` independently certifies four canonical config-1
variants without author terms or royalties.
Pool-specific ABI exports are `POOL_HOOK_DEPLOYER_V1_ABI`, `POOL_MARKET_ADAPTER_V1_ABI`
and `POOL_FEE_COLLECTOR_FACTORY_V1_ABI`. Renamed types/ABI exports have no old-name aliases.
Admission uses EIP-712 domain `Black Market Launch Registry`, version `2`; old author consent
must be regenerated. Market configuration tuple layouts remain unchanged; bounds encoding changes
below require fresh profile identities and consent.

`LaunchBoundsV2` now has five fields beginning with `minimum_tick_spacing`: spacing bounds,
maximum positions, maximum oracle cardinality and fee-mode flags. Former LP/hook fee ceilings
and profile-level oracle/external-liquidity pins are removed. Creators choose valid LP/hook
rates below 1,000,000 pips, their per-pool `oracle_config_id` and
`external_liquidity_disabled`. Author royalty ceilings remain separate.
Regenerate bounds digests, profile IDs and consent for the new tuple.

All three registered Robinhood oracle configurations P1 `(1,4096)`, P2 `(6,4096)` and
P3 `(17,4096)` remain usable on the same admitted profile. Preparation and wallet admission
validate the selected oracle against the certified factory at the pinned block, including
movement/cardinality limits. An unregistered ID still fails. With
`external_liquidity_disabled=False`, third-party LP add/remove is permitted after opening;
permanent launch custody and pre-opening protection remain intact.

Shared contracts are `FixedFeeSharedHookV1`, `SharedHookDeployerV1` and
`SharedMarketAdapterV1`; the shared adapter ABI export is
`SHARED_MARKET_ADAPTER_V1_ABI`. The factory getter is `dependencyDigest(address)`.
Identity domains are `black-market.launch-profile.v2`,
`black-market.v4-dependencies.v2` and `black-market.pool-bound-market-economics.v1`.
There are no old-name aliases; regenerate profile identities, commitments and author consent.

### Exact current configuration

`LifecycleV4MarketConfig` requires inner `version=4` and outer `config_version=4`:

```text
(uint16,uint24,int24,uint160,uint24,uint8,uint8,address,bool,bytes32,
 bytes32,bytes32,address,uint16,(int24,int24,uint128,bytes32,uint256)[])
```

Its fields are `version`, `lp_fee_pips`, `tick_spacing`, `sqrt_price_x96`,
`hook_fee_pips`, `fee_mode`, `protocol_fee_denominator`, `treasury`,
`external_liquidity_disabled`, `oracle_config_id`, `profile_id`, `terms_digest`,
`developer_beneficiary`, `developer_fee_bps`, then `positions`.

`LifecyclePoolBoundV4MarketConfig` requires inner/outer version **5**, inserting
`hook_salt: bytes32` immediately before `profile_id`. The position tuple remains
`tick_lower`, `tick_upper`, `liquidity`, `salt`, `max_token_amount`. Position salts
and the deployment salt are independent commitments.

Use `encode_lifecycle_v4_market_config` / `decode_lifecycle_v4_market_config` and
`encode_lifecycle_pool_bound_v4_market_config` /
`decode_lifecycle_pool_bound_v4_market_config`. Both decoders require canonical
current bytes, including the version and all author terms. The inner profile ID
must match the outer market. The developer beneficiary is the stable admitted
**authorId**, not today's payout address. The creator must choose an explicit
rate (zero is allowed); there is no default rate. That rate may not exceed either
the envelope maximum or the registry's protocol maximum. Treasury and protocol fee are
frozen terms; fee mode, tick spacing and position count must stay within the admitted bounds.
Oracle ID and external-liquidity policy are creator-selected per-pool parameters. The canonical
collector helper validates actual selected oracle metadata and economic/position rules on chain.

Abyss retains `LifecycleAbyssMarketConfig`, `LifecycleAbyssPosition`, and
`encode_lifecycle_abyss_market_config`. Mixed Abyss/V4 plans are supported. At most
one V4 market per quote is permitted across every admitted template and both V4
topologies; an Abyss market may use that same quote.

### Pool-bound preparation

```python
finalized = await prepare_pool_bound_lifecycle_plan(client, draft_plan)
plan = finalized.plan
for deployment in finalized.deployments:
    print(deployment.market_index, deployment.init_code_hash, deployment.predicted_hook)
```

`read_pool_bound_hook_deployment(client,plan,market_index=...)` supports an unmined
draft. `mine_pool_bound_hook_salt` performs cancellable local CREATE2 search.
`prepare_pool_bound_lifecycle_plan` changes only draft deployment salts, rereads
the finalized graph and factory binding, and preserves token identity. Once a
launch has begun, its economic plan cannot be rewritten.

The commitment domain is exactly
`black-market.pool-bound-market-economics.v1`. Only `hook_salt` is
normalized; profile, terms, author, developer rate and every economic/position
field remain bound. Prediction uses the exact reviewed creation chunks plus all
18 scalar constructor words. Runtime/initcode limits are 24,576/49,152 bytes.
An existing hook must have the deployer's recorded runtime hash, exact constructor
commitment and dependency/key/opening metadata; permission bits alone are not
provenance. Shared roots likewise require their exact three-word constructor,
reviewed salt, runtime hash and recorded deployment.

`build_pool_bound_hook_deployment_transaction` returns optional typed unsigned
predeployment calldata. Predeployment neither binds a fee source nor initializes,
mints, seals, buys or opens a pool.

### Planning, simulation and recovery

| Public API | Behavior |
| --- | --- |
| `plan_launch(client,plan,account=...,mode=...)` | Current schema/topology admission, funding checks, exact approvals, atomic-first gas proof, and explicit atomic/staged grouping |
| `simulate_launch_plan(client,launch,account=...)` | Reexecute against current canonical progress and limits |
| `build_next_transaction(client,launch,account=...)` | Revalidate identity, receipts, eligibility, deployment provenance and funding; return only the next proven unsigned step |
| `read_launch_progress(client,plan,confirmations=...,transaction_hashes=...)` | Recover canonical phase/counts plus confirmed, orphaned, pending and replaced receipt evidence |
| `read_launch_markets(client,plan,offset=...,limit=...)` | Read full canonical market/position/custody identities and live pool state |
| `preview_lifecycle_fees(client,hub,executor=...)` | Execute the current V3 **no-argument** `claimAndSplit()` through `eth_call` |

Staged mode only partitions preparation. Minting every position, permanent
custody sealing, all ordered opening buys, refunds and public activation remain
one indivisible activation transaction. A measured overlimit activation is
refused; the SDK never splits it, removes buys, raises chain limits or switches
modes silently. Changing execution grouping does not change plan economics.

Quote/external fee assets and direct ERC20/native-wrap funding are permissionless;
the planner checks actual code and transfers rather than a deployment quote list.
Swap conversions still require registry-approved input, target/spender and code
hash. Exact balances, per-asset budgets and native-wrap binding remain mandatory.

Sequential simulation uses `eth_simulateV1` or an explicitly isolated, separate
loopback Anvil fork created with `create_controlled_launch_fork`. A controlled fork
executes actual transactions only in its owned snapshot, verifies source/fork
identity and account state, and restores it. It cannot broadcast on the source
node or a remote/unowned node. Creator accounts must already be unlocked on the
owned fork; the SDK never signs or impersonates automatically. Disconnected calls
to future deployments or an unsupported backend are provisional, not admission.

Provide `LaunchExecutionLimits` or a `LifecycleLimitResolver`. Chain, RPC,
account and calldata caps must be scoped to the observed block/hash, chain,
orchestrator and account. Unknown constraints fail admission. Reread after
receipts, replacement, reload, wallet switch or reorg; local step counters and
unconfirmed approvals cannot authorize another command. `cancel=True` on
`build_next_transaction` explicitly requests eligible cancellation, refunding
only unspent launch-isolated external funding, never gas, setup cost or inventory
already disposed under the committed cancellation policy.

Portable plans use Solidity camelCase JSON and exact decimal strings.
`launch_plan_from_dict`, `launch_plan_to_dict`, `encode_launch_plan`,
`hash_launch_plan`, `launch_id_of` and `build_lifecycle_calldata` are public.

### Stable authors, live payouts and V3 claims

All helpers below are exported at the package root. `registry` is an explicit
trusted authority. Canonical hub authentication starts at `registry.core()` →
`core.feeFactory()` → `factory.isHub(hub)`; attacker-controlled hub self-reports
never establish membership. A hub's current version/registry are supplementary
checks only after canonical membership.

```python
author = read_lifecycle_author(client, registry=registry, author_id=author_id)
page = read_author_hubs(client, registry=registry, author_id=author_id, offset=0, limit=100)
credits = read_developer_fees(client, registry=registry, hub=hub, author_id=author_id)
transaction = build_claim_developer_fees_transaction(
    client, registry=registry, hub=hub, author_id=author_id, asset=asset,
    account=caller, chain_id=chain_id,
)
# Caller controls submission. Decode only its matching, actual receipt.
outcome = decode_developer_claim_receipt(receipt, transaction=transaction)
```

- `read_lifecycle_author` reads live `authorPayout` and hub count. Unknown authors
  have a zero payout. Envelope/config beneficiary remains the stable authorId.
- `build_set_author_payout_transaction(...,payout=...,account=...,chain_id=...)`
  builds `setAuthorPayout(authorId,payout)`. Registry admin or current payout
  controls that route; neither updates the stable identity nor frozen terms.
- `read_author_hubs` implements `authorHubs` pages of **1..100** hubs. Offset may
  equal total for an empty completed page, but may not exceed total.
- `read_developer_fees` returns supported-asset credits/reserves and the author's
  frozen source terms without harvesting or consulting retired admission. An
  unsupported requested asset is distinct from zero credit. `assets=()` uses
  the hub's actual assets.
- `build_claim_developer_fees_transaction` builds
  `claimDeveloperFees(authorId,asset)` with no recipient argument. Any caller can
  pay reserved credits only to the live registry payout, without a harvest bounty.
- `build_claim_developer_fees_page_transaction(...,offset=...,limit=...,assets=...)`
  targets the canonical factory's `claimDeveloperFeesPage`. Limit is **1..10**
  hubs; explicit assets must be ascending, unique, nonzero, at most eight.
  `assets=()` selects each hub's actual assets. SDK builders never silently sort.

Unsigned author builders return exactly `chainId`, `from`, `to`, `data`, `value`
(with zero value). They perform no signing or default execution.

`decode_developer_claim_receipt(receipt,transaction=...)` binds the exact builder
context and event emitter. Factory row statuses are **0 paid, 1 zero, 2 unsupported,
3 failed**, with amount and error selector. `cursor_complete` only describes
pagination; `payments_succeeded` is separate. Failed rows remain in
`retryable_rows` even when the cursor reaches total: retry those directly or
rescan later. A failed transaction does not advance a cursor. A successful direct
claim with no payment event is `outcome='unobserved'`, not an invented zero or
payment; zero claims emit no hub event.

Supply an explicit page gas budget within the current account/RPC/chain limits,
including all bounded hub/asset calls, factory overhead and EIP-150 headroom.
`eth_estimateGas` may succeed with failed rows when caller gas is insufficient;
successful execution and cursor completion do not prove payment. Decode every
row and retry preserved failures.

`LAUNCH_FEE_HUB_V3_ABI` includes current harvest, owner credits/withdrawal,
executor-rate updates, source terms and developer events.
`LAUNCH_FEE_HUB_FACTORY_V3_ABI`, `LAUNCH_FEE_OWNER_REGISTRY_V2_ABI` and
`LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI` expose current factory, two-step fee
ownership and author discovery/claim/payout ABIs. There is no fee-only fallback ABI.

## Networks, DEX and lending

`get_addresses()` defaults to workbench `46631`; supported chain IDs remain
`31337`, `4663`, `46631`. The address book now contains only unchanged Abyss and
lending records. Reviewed lifecycle targets are explicit plan inputs, not old
captured launch defaults. Abyss infrastructure overrides apply to `31337` and
`46631`, with mainnet canonical. Lending fields use bare/`VITE_`/`NEXT_PUBLIC_`
precedence on `31337`, public prefixes on `46631`; the liquidation executor
retains its documented full-prefix exception.

Abyss fee tiers, pool profiles/keys, factory/pool/router/position-manager/locker
ABIs and exact TickMath helpers remain available. Lending client factories,
reserve/user normalization, catalog overlays, lending ABIs and RAY/WAD/display
helpers are unaffected.

## Optional launch API integration

`LaunchApiClient` is opt-in; construction performs no request. Configure
`LaunchApiConfig` explicitly or inject `LaunchApiTransport`. Typed-data builders
require an explicit `verifying_contract` naming the selected lifecycle
orchestrator. They never default to a historical launch record.

The caller persists the idempotency key, signs the full EIP-712 authorization,
and protects the once-returned upload capability. Presigned PUTs send every
issued header, including the create-only condition. Complete the upload before
publishing an actual confirmed transaction. A `202` raises `LaunchPublishPending`
with the returned session and retry delay; no write is retried automatically.
Session reads/URL renewal provide explicit recovery. Metadata and replacement
image authorization APIs, public launch projections, safe typed API errors and
bounded HTTP/upload behavior are unchanged.

## Examples

```sh
# Offline: validate/hash an actual caller-supplied current plan.
python examples/quickstart.py /absolute/current-plan.json

# Offline: encode an explicit complete shared4 or bound5 config JSON.
python examples/launch_recipe.py /absolute/current-v4-config.json

# Read exact dynamic registry offerings; no template catalog.
python examples/launch_matrix.py --rpc-url http://127.0.0.1:8545 --orchestrator 0x...

# Plan/simulate/recover and emit the next unsigned transaction; never source-broadcast.
python examples/launch_submit.py /absolute/current-plan.json --rpc-url http://127.0.0.1:8545 \
  --account 0x... --mode staged --chain-gas-cap 16777216 --rpc-gas-cap 16777216 \
  --account-gas-cap 16777216 --calldata-cap 131072

# Offline metadata transport example, explicitly selected signature domain.
python examples/launch_api_lifecycle.py
```

Caps in the example are caller inputs, not asserted network policy. The real local
integration surface is `examples/launch_lifecycle_smoke.py`, using an explicitly
isolated fixture and real manager/pools/receipts. Prior historical-stack evidence
is not current reviewed-graph verification.

## Package layout

| Module | Contents |
| --- | --- |
| `lifecycle` / `lifecycle_abis` / `lifecycle_rpc` | Current codecs, reviewed admission, planning/simulation/recovery and V3 author APIs |
| `launch_api` | Explicit metadata API client and signature/upload helpers |
| `launch` | Exact TickMath utilities |
| `abyss` / `abyss_abis` | Unchanged DEX primitives and ABIs |
| `addresses` / `abis` / `client` | DEX/lending addresses, lending ABIs and client factories |
| `live` / `format` | Reserve/user normalization and RAY/WAD/display math |

## Development

```sh
pip install -e ".[dev]"
pytest
```

## 0.2.0 migration

- Breaking cutover to explicit lifecycle plans and signed V2 registry profiles.
  Historical Atomic/Unified builders, template catalogs and old-name aliases are removed.
- Shared V4 config 4 and pool-bound V4 config 5 require frozen author terms and an
  explicit developer rate. Retired configs are rejected, not converted.
- Registry admission uses `registerProfile`, reads use `profileEnvelope` / `profileId`,
  and the EIP-712 domain is `Black Market Launch Registry`, version `2`.
  Regenerate author consent for the exact new deployment graph.
- Current exports include `LaunchEnvelopeV2`, `LaunchBoundsV2`, `LaunchGraphV2`,
  `POOL_HOOK_DEPLOYER_V1_ABI`, `POOL_MARKET_ADAPTER_V1_ABI` and
  `POOL_FEE_COLLECTOR_FACTORY_V1_ABI`. Profile/dependency hash preimages and current
  economic tuples remain unchanged.
- No production launch deployment or author authorization is bundled with this release.

## Releasing

Releases use GitHub Actions [trusted publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC). Bump the package version, cut a matching release tag, and use the existing
build/check/publish workflow and `pypi` environment approval. No publication is
performed by SDK launch operations.

## License

MIT
