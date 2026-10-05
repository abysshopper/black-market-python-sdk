# Launch guide

The SDK separates read-only discovery, explicit economic plans, unsigned transaction
building, wallet submission, and metadata publication. An address configuration is
not a plan: every `LaunchPlanV1` and EIP-712 payload names its own chain and orchestrator.

## Deployed infrastructure

`get_launch_addresses(4663)` returns the current mined `pool-launch-v1` infrastructure.
The deployment's canonical manifest records successful receipts and runtime evidence
at block **80,548,181**. Its live implementation registry contains two adapters and
five admitted profiles: one pool-bound V4/config5 offering and four canonical
Abyss/config1 variants. Discover profile IDs from the registry rather than hard-coding
an SDK allowlist. Shared V4/config4 is a supported codec/topology for an explicitly
selected, admitted deployment; it is not an offering of this mainnet deployment.

| Contract | Address |
| --- | --- |
| Orchestrator | `0xb75CBD17b9aecb7305B4DFcDa69595F783341c0E` |
| Implementation registry | `0xaa8a410709B79cBA6F118F1be1FF568877A3B8Ee` |
| Fee-owner registry | `0x15778Aad08e12D458B2848F035860e2a8c2a0725` |
| Directory | `0xa9d0F9Ff93AF569B7C11b5cDCE1E98D5a59F52DC` |
| Fee-hub factory | `0x5c1FFa0F9fEB3f3f2704b752397f36f00072d252` |
| Token factory | `0xa7467624E5A7a962f49a341Aa3fCE3eD20257b67` |
| Funding escrow | `0x13be9F6C4087d7eA3937c49bc174793162716312` |
| Launch lens | `0x50bdA6937e3753665637538d62A8995b12a86A06` |
| Pool-bound market adapter | `0xd9A7f35F2251dDa2506E50fCab2e52E6CE7a37d9` |
| Hook deployer | `0x9Cd433644237E6925deB13F3196cb44843de2DF6` |
| Collector factory | `0xf444dC28Aa7F6B3a8Ad5B7B3975d05a7A1fDF409` |
| Canonical Abyss market adapter | `0xeF509c6c9049D3260D5a90a56475d33f5BB47827` |

The frozen `LaunchInfrastructureAddresses` also includes the admin, validator,
token/reward deployers, collector deployer, custody, pool manager, oracle factory,
Abyss fee-source factory, and wrapped native asset. `LAUNCH_ADDRESSES` configures only
chain `4663`; unconfigured chains raise `KeyError`. Local/workbench launches must
supply the actual locally deployed orchestrator explicitly. Configuration lookup
never rewrites a supplied plan, selects execution mode, signs, or sends a transaction.

## Registry discovery and admission

```sh
# Reads the current mainnet deployment; no wallet or private key.
python examples/launch_matrix.py
# Select an actual local deployment explicitly.
: "${LOCAL_ORCHESTRATOR:?Set the actual local orchestrator}"
python examples/launch_matrix.py --chain-id 31337 --rpc-url http://127.0.0.1:8545 \
  --orchestrator "$LOCAL_ORCHESTRATOR"
```

`read_lifecycle_profiles(client, orchestrator=..., offset=0, limit=100)` calls the
exact `profileIds`, `profile`, `adapter`, `profileTopology`, and `requireEligible`
getters. A `LifecycleProfile` contains:

- `id`, `registration`, `adapter`, and certified `topology`.
- `venue_kind`, `admitted`, and an explicit refusal `reason` when applicable.
- For V4: a frozen `LaunchEnvelopeV2`, live `developer_terms`, and the registry's
  `protocol_maximum_developer_fee_bps`.
- For canonical Abyss: its independently certified graph without a V4 author envelope.

Discovery validates actual runtime, core/dependency bindings, and configuration
schema. Disabled offerings remain discoverable but cannot authorize a new launch.
Unknown schemas are distinguished from position bounds or missing capabilities.
Admission is pinned to the observed block, not an assumption about an address label.

V4 `LaunchEnvelopeV2` binds the exact artifact/review/terms digests, topology,
configuration/economic versions, callbacks, protocol treasury/fee denominator,
stable beneficiary, royalty ceiling, executable bounds, and complete graph.
`LaunchBoundsV2` consists of `minimum_tick_spacing`, `maximum_tick_spacing`,
`maximum_positions`, `maximum_oracle_cardinality`, and `fee_mode_flags`.
Registry author consent uses EIP-712 domain `Black Market Launch Registry`, version
`2`, with six-argument `registerProfile`. Canonical Abyss uses the admin-only
`registerAbyssProfile(uint8,registration)` certification route.

## Plans and market configuration

A `LaunchPlanV1` commits `chain_id`, `orchestrator`, `creator`, `nonce`, `token`,
`funding`, `fee_assets`, `markets`, ordered `buys`, `deadline`, and `executor_fee_bps`.
Use `launch_plan_from_dict` / `launch_plan_to_dict` for portable Solidity camelCase
JSON with canonical decimal strings for large exact integers. `encode_launch_plan`,
`hash_launch_plan`, `launch_id_of`, and `build_lifecycle_calldata` are public.
Offline encoding/hashing is not registry, funding, gas, or sequential-execution proof.
`predict_launch_token` also accepts a typed token-only draft with empty market/funding/
fee arrays, so its predicted address can determine token-only range orientation.
Prediction does not admit that draft for execution; complete-plan validation remains
required by all execution builders.

`LifecyclePoolBoundV4MarketConfig` requires inner and outer version **5**. Its fields
in order are:

```text
version, lp_fee_pips, tick_spacing, sqrt_price_x96, hook_fee_pips, fee_mode,
protocol_fee_denominator, treasury, external_liquidity_disabled, oracle_config_id,
hook_salt, profile_id, terms_digest, developer_beneficiary, developer_fee_bps,
positions
```

The exact config5 tuple is:

```text
(uint16,uint24,int24,uint160,uint24,uint8,uint8,address,bool,bytes32,bytes32,
 bytes32,bytes32,address,uint16,(int24,int24,uint128,bytes32,uint256)[])
```

Each `LifecycleV4Position` has `tick_lower`, `tick_upper`, `liquidity`, `salt`, and
`max_token_amount`. Position salts and the hook deployment salt are independent.
`LifecycleV4MarketConfig` uses inner/outer version **4** and the same sequence
without `hook_salt`. Current encode/decode helpers reject noncanonical bytes and
require the exact author-bound fields; no fee or author rate is chosen for you.

The inner profile ID must equal the market's outer ID. `developer_beneficiary` is
the stable admitted author identity, not today's payout address. An explicit rate,
including zero, must fit both the frozen envelope and live protocol maximum.
Treasury and protocol denominator are frozen terms. LP/hook rates must be below
1,000,000 pips; fee mode, tick spacing, and position count must fit admitted bounds.

Creators select `oracle_config_id` and `external_liquidity_disabled` per pool.
Robinhood oracle configurations P1 `(1,4096)`, P2 `(6,4096)`, and P3 `(17,4096)` can
use the same admitted profile. The planner verifies the chosen ID against actual
factory metadata at the pinned block. Unregistered IDs fail. Allowing external
liquidity permits third-party LP add/remove after opening; permanent launch custody
and pre-opening protection still apply.

Canonical Abyss uses `LifecycleAbyssMarketConfig`, `LifecycleAbyssPosition`, and
`encode_lifecycle_abyss_market_config`. Mixed Abyss/V4 plans are supported. At most
one V4 market per quote is permitted across both V4 topologies; an Abyss market
may use that quote. Funding/fee-asset arrays are ascending and unique, fee
allocations sum to 10,000 bps, and all committed quantities stay exact.

## Pool-bound hook preparation

`await prepare_pool_bound_lifecycle_plan(client, draft_plan)` returns a finalized
plan plus exact per-market deployments. It locally mines valid CREATE2 hook salts,
rereads the graph/factory binding, and preserves token identity. Review the finalized
plan hash before submission. Economic plans cannot be rewritten after launch begins.
`read_pool_bound_hook_deployment` accepts an unmined draft; `mine_pool_bound_hook_salt`
supports cancellable local search. `build_pool_bound_hook_deployment_transaction`
returns optional unsigned predeployment calldata without registration, initialization,
fee-source binding, minting, custody sealing, buying, or opening.

The market commitment domain is `black-market.pool-bound-market-economics.v1`.
Only `hook_salt` is normalized: profile, terms, author, rate, economics, and every
position remain committed. Prediction hashes the exact certified creation-code
chunks plus the **one 18-field `PoolBoundHookParametersV1` constructor tuple** of
`FixedFeePoolHookV1`. Initcode/runtime limits are 49,152/24,576 bytes. An existing
hook must match deployer-recorded runtime and exact constructor, key, dependency,
and opening-state commitments; callback bits alone do not prove provenance.
Shared topology separately authenticates its typed three-argument constructor.

Public ABI fragments include `FIXED_FEE_POOL_HOOK_V1_ABI`,
`POOL_HOOK_DEPLOYER_V1_ABI`, `POOL_MARKET_ADAPTER_V1_ABI`,
`SHARED_MARKET_ADAPTER_V1_ABI`, and `POOL_FEE_COLLECTOR_FACTORY_V1_ABI`.
Actual lifecycle reads also export `ABYSS_MARKET_ADAPTER_V1_ABI` (`profileId(3)`),
`LAUNCH_TOKEN_CONTEXT_V1_ABI`, `LAUNCH_ERC404_V1_ABI`,
`V4_FEE_LIQUIDITY_LOCKER_V2_ABI`, and the token factory's `TokenDeployed` event.
Profile/dependency domains are `black-market.launch-profile.v2` and
`black-market.v4-dependencies.v2`; the collector factory getter is
`dependencyDigest(address)`.

## Simulation, unsigned commands, and recovery

| API | Purpose |
| --- | --- |
| `plan_launch(client,plan,account=...,mode=...)` | Admission, funding, approvals, atomic-first proof, explicit grouping |
| `simulate_launch_plan(client,launch,account=...)` | Reexecute against current canonical progress and caps |
| `build_next_transaction(client,launch,account=...)` | Revalidate and emit only the next proven unsigned step |
| `read_launch_progress(client,plan,confirmations=...,transaction_hashes=...)` | Canonical progress and confirmed/pending/replaced/orphaned receipt evidence |
| `read_launch_markets(client,plan,offset=...,limit=...)` | Canonical market/position/custody identities and live state |
| `preview_lifecycle_fees(client,hub,executor=...)` | Current no-argument V3 `claimAndSplit()` through `eth_call` |

Staged mode partitions preparation only. Minting all positions, permanent custody
sealing, ordered opening buys, refunds, and public activation form one indivisible
activation transaction. An overlimit activation is refused, not split or silently
changed. Execution grouping never changes committed economics.

Quote/fee assets and exact direct ERC20/native-wrap funding are permissionless;
admission checks actual code, transfers, balances, budgets, and wrapped-native
binding. Swap conversion requires registry-approved input, target/spender, and code
hash. Sequential simulation needs `eth_simulateV1` or an explicitly owned, separate
loopback Anvil fork created with `create_controlled_launch_fork`. A controlled fork
executes real transactions only in its owned snapshot, verifies source/fork identity
and account state, and restores it. The creator must already be unlocked there;
the SDK does not sign or impersonate automatically. Disconnected deployment calls
or unsupported simulation backends remain provisional and cannot authorize a step.

Provide `LaunchExecutionLimits` or a `LifecycleLimitResolver`. Chain, RPC, account,
and calldata caps are scoped to the observed block/hash, chain, orchestrator, and
account. Unknown limits fail admission. Reread after receipts, replacement, reload,
wallet switch, or reorg. Unconfirmed approvals and local counters are not authority.
`build_next_transaction(..., action="cancel")` explicitly requests an eligible
cancellation, refunding only unspent launch-isolated external funding, not gas,
setup cost, or inventory disposed under the committed cancellation policy.

```sh
# Hash your complete current economic plan offline; does not prove admission.
: "${PLAN_JSON:?Set the path to your reviewed complete plan}"
python examples/quickstart.py "$PLAN_JSON"
# Encode a complete current config5 JSON (Solidity camelCase fields).
: "${V4_CONFIG_JSON:?Set the path to your complete config}"
python examples/launch_recipe.py "$V4_CONFIG_JSON"

# Plan/simulate/recover with caller-observed caps; emits unsigned output only.
: "${RPC_URL:?}" "${CREATOR:?}" "${CHAIN_GAS_CAP:?}" "${RPC_GAS_CAP:?}" \
  "${ACCOUNT_GAS_CAP:?}" "${CALLDATA_CAP:?}"
python examples/launch_submit.py "$PLAN_JSON" --rpc-url "$RPC_URL" \
  --account "$CREATOR" --mode staged --chain-gas-cap "$CHAIN_GAS_CAP" \
  --rpc-gas-cap "$RPC_GAS_CAP" --account-gas-cap "$ACCOUNT_GAS_CAP" \
  --calldata-cap "$CALLDATA_CAP" --finalize-bound
```

Supply `--transaction-hash` and `--confirmations` for recovery. Do not use
`--finalize-bound` after submission. For real owned-local-chain fixture execution,
`LAUNCH_LIFECYCLE_RPC_URL=http://127.0.0.1:8545 python examples/launch_lifecycle_smoke.py "$MANIFEST_JSON"`
requires an actual current deployment manifest linking `fixtures.plansFile`, complete
plans, real pools/receipts, and matching execution limits. Each row is snapshot-isolated
and rolled back. Unit wire fixtures are not deployed-chain smoke inputs.

## Individually runnable token-creation smoke

`examples/smoke_launch.py` is separate from the snapshot/rollback proof driver above.
It creates real tokens through the current deployed lifecycle on an owned local
fork, leaves source-chain state intact for debugging, and selects exactly one case.
The [shared setup guide](../../black-market/docs/sdk-launch-smoke.md) starts two
separate Anvil nodes, checks current deployment provenance, and copies the eight
shared cases into `black-market.launch-smoke-fixture.v1`. Run cases serially while
that setup terminal stays open; the simulation node is an exclusively owned sandbox.

From this SDK checkout, after installing `.[dev]`:

```sh
# In another terminal; requires Foundry and upstream read-only RPC access.
python ../black-market/scripts/start_sdk_launch_smoke.py --output /tmp/my-launch-smoke

# Offline catalogue/help; neither contacts RPC or API.
python examples/smoke_launch.py --help
python examples/smoke_launch.py --fixture /tmp/my-launch-smoke/fixture.json --list

# Unsigned, read-only provenance + offchain salt finalization; no simulation writes.
python examples/smoke_launch.py --fixture /tmp/my-launch-smoke/fixture.json \
  --case erc20-v4-basic --output /tmp/python-launch-plan

# Real token/markets/custody/buys, explicitly omitting the API.
python examples/smoke_launch.py --fixture /tmp/my-launch-smoke/fixture.json \
  --case erc404-dividends-mixed --execute --chain-only --output /tmp/python-chain-run

# Full path: supply your actual local API/indexer configured against execution Anvil.
python examples/smoke_launch.py --fixture /tmp/my-launch-smoke/fixture.json \
  --case erc20-abyss-basic --execute --api-url http://127.0.0.1:42069 \
  --publish-timeout-seconds 90 --output /tmp/python-api-run
```

Every output directory must be fresh. No default live RPC/API, signing key, all-case
mode, silent execution-mode fallback, market removal, or implicit relaunch exists.
Without `--execute`, the result is `scope: plan-only`, with execution/API `not-run`;
it says **“Plan prepared; launch not executed”**, not that a token was launched.
`--chain-only` reports API `skipped` and is never full end-to-end proof.

Executed cases use fresh persisted nonce/token salt, discover bound V4/config5 and
canonical Abyss/config1 **QUOTE_ORACLE profile3** from the live registry/adapter,
and use the selected real registered oracle ID. ERC20 supply is 1,000,000 tokens;
ERC404 supply is 10,000 tokens with 100-token NFT units. `plan_launch` and
`build_next_transaction` prove the unchanged full economics with fixture-scoped
gas/calldata limits. The dev creator must already be unlocked on both nodes;
the runner does not impersonate, inject balances, or revert the execution node.
Simulation alone restores its owned fork.
Local RPC requests have a 120-second bound for cold nested forks, with automatic
transport retries disabled. A timeout remains a logged failure, not an admitted launch.

Full execution first signs the SDK's complete attribution EIP-712 payload through
execution Anvil `eth_signTypedData_v4`, stages metadata with the exact token name
and symbol using `LaunchApiClient`, then publishes only the confirmed atomic/activate
transaction hash. Only actual `202`/`LaunchPublishPending` responses are polled,
respecting `Retry-After` within the deadline. Other errors fail immediately.
The returned session and real `/api/v1/launches/<token>?chainId=4663` representation
must match the observed token, activation block/hash, canonical status, and metadata.
An API/indexer without current `LaunchActivated` support is a real failure, not
something the SDK fills with invented projections.

Artifacts remain available after errors or Ctrl+C:

| Artifact | Evidence |
| --- | --- |
| `run.json`, `plan.json` | Exact case/configuration and nonce/salt; full finalized portable plan before execution |
| `events.jsonl`, `admission.json` | Stages/timing, real backend/admission, simulation envelopes/gas and safe RPC errors/revert traces |
| `receipts.json`, `chain.json` | Hashes persisted before waits, receipts before status checks; actual Active token/markets/permanent custody/ordered buys |
| `api.json`, `result.json` | Real publication representation when reached; final scope/failed stage and traceback/causal/revert/API error details |
| `recovery.private.json` | Mode-0600 exact signed request, once-returned session capability, activation hash and latest publish response |

Do not share the private recovery file. Public artifacts recursively redact
signatures, keys, capabilities, authorization headers, secret environment values,
and URL credentials/query secrets. A failure exits nonzero and preserves submitted
chain state. Inspect the original token/hash/session after API failure: rerunning
with fresh output deliberately constructs a different token, not a retry of that launch.


## Stable authors, payouts, and fee claims

Author helpers require an explicit trusted `registry`. Canonical hub authentication
starts at `registry.core()` → `core.feeFactory()` → `factory.isHub(hub)`; a hub's
self-reported version or registry does not establish membership.

- `read_lifecycle_author` reads live payout and hub count; unknown authors have zero payout.
- `build_set_author_payout_transaction` builds `setAuthorPayout(authorId,payout)`.
  Registry admin/current payout controls routing, not frozen identity or terms.
- `read_author_hubs` pages **1..100** hubs; offset may equal total, not exceed it.
- `read_developer_fees` reads supported-asset credits/reserves and frozen source terms
  without harvesting. Unsupported asset and zero credit are distinct.
- `build_claim_developer_fees_transaction` builds `claimDeveloperFees(authorId,asset)`.
  Any caller pays reserved credits only to the live payout, without a harvest bounty.
- `build_claim_developer_fees_page_transaction` targets canonical factory pagination:
  **1..10** hubs, at most eight ascending unique nonzero assets; `assets=()` selects
  each hub's actual assets. Builders never silently sort inputs.

Unsigned author builders return `chainId`, `from`, `to`, `data`, and zero `value`.
`decode_developer_claim_receipt(receipt,transaction=...)` binds exact builder context
and event emitter. Factory row statuses are **0 paid, 1 zero, 2 unsupported, 3 failed**.
`cursor_complete` is separate from `payments_succeeded`; failed rows stay retryable
even at the last page. A successful direct claim without a matching event is
`outcome='unobserved'`, not an invented payment or zero. Zero claims emit no hub event.

Use an explicit current gas budget for bounded hub/asset calls, factory overhead,
and EIP-150 headroom. Gas estimation can succeed despite failed rows; decode every
row and preserve failed claims for caller-controlled retry. The exported V3 hub,
factory, V2 fee-owner registry, and V2 implementation registry ABIs include current
credits, source terms, payouts, two-step ownership, and claim events.

## Real metadata API usage and tests

`LaunchApiClient(LaunchApiConfig(base_url=...))` is opt-in and performs no request
on construction. API URLs require HTTPS by default. For an explicitly selected
local test service, use `LaunchApiConfig(base_url="http://127.0.0.1:18763",
allow_loopback_http=True)`. This allows HTTP only for numeric loopback IPs or
`localhost`, without credentials, query, or fragment; it never enables remote HTTP.
The test suite and example opt into that local mode. Presigned upload URLs still
require HTTPS regardless of this option.

The real stdlib transport bounds responses, returns safe typed errors, and performs
no proxy discovery or redirect forwarding.
`build_launch_attribution_typed_data` and `build_launch_metadata_update_typed_data`
require explicit `verifying_contract`. Callers own wallet signing, idempotency,
recovery state, and retry decisions; no write is retried automatically.

The example uses a real service and omits images so no object store is needed:

```sh
export LAUNCH_API_TEST_URL=http://127.0.0.1:18763
export LAUNCH_API_TEST_CHAIN_ID=4663
export LAUNCH_API_TEST_ORCHESTRATOR=0xb75CBD17b9aecb7305B4DFcDa69595F783341c0E
python examples/launch_api_lifecycle.py

# Explicitly sign/write only against a loopback test service.
: "${LAUNCH_API_TEST_PRIVATE_KEY:?Set a disposable test wallet private key}"
python examples/launch_api_lifecycle.py --signed --state-file launch-session.private.json
# Read persisted session state without signing or writing.
python examples/launch_api_lifecycle.py --state-file launch-session.private.json
```

The capability is returned exactly once and saved in a new mode-0600 recovery file,
never printed. An identical signed create request replays its session ID without
reissuing capability/upload URLs. Protect that file, idempotency key, and exact
signed request; regenerating a wall-clock deadline changes the body. Metadata-only
sessions start `ready_to_launch` but do **not** represent an on-chain launch.

With an actual image-store configuration, callers can bind a descriptor, PUT the
exact bytes with every issued header/create-only condition, complete verification,
and explicitly publish an actual confirmed launch hash. A `202` raises
`LaunchPublishPending` with the returned state and retry delay. Session reads/URL
renewal provide recovery. These image/publish flows are not part of the metadata-only
HTTP suite and require real storage/chain/indexer prerequisites.

### Service contract and suite selection

Run a real Abyss API test service using `createLaunchesRouter`, its actual session
repository, and an isolated migrated PostgreSQL database. Enable chain `4663` and
set the API deployment's `contracts.launchFactory` to the current orchestrator
address above; this is the attribution EIP-712 verifying contract. A healthy HTTP
listener alone is insufficient: private session routes and authorization/persistence
must be enabled. The SDK does not start an API service or substitute echo responses.

Environment contract:

| Variable | Meaning |
| --- | --- |
| `LAUNCH_API_TEST_URL` | Required actual test HTTP service URL |
| `LAUNCH_API_TEST_CHAIN_ID` | Defaults to `4663`; must match the service |
| `LAUNCH_API_TEST_ORCHESTRATOR` | Defaults to current mainnet orchestrator; must match service authorization |
| `LAUNCH_API_TEST_PRIVATE_KEY` | Required only for explicitly selected signed tests/example; disposable EOA, no funding needed |

```sh
python -m pytest -q
python -m pytest -q tests/test_launch_api_http.py -m 'launch_api_http and not launch_api_write'
python -m pytest -q tests/test_launch_api_http.py -m launch_api_http
```

Default pytest selects only local unit tests. The GET-only HTTP subset checks malformed
capability rejection and unknown-session handling with no key or indexed launch.
The full HTTP suite signs with the public SDK typed-data builder and `eth-account`,
then exercises actual metadata-only creation, normalized persisted reads, exact
idempotent replay, signed conflict, malformed/wrong capability, wallet/domain/metadata
binding, expired deadline, and invalid signature. Missing service configuration/key
fails an explicitly selected suite; it is not a skipped or pretend-passing integration.
Signed tests/examples refuse non-loopback URLs. No suite sends chain transactions.

## DEX/lending network configuration

`get_addresses()` defaults to workbench `46631`; explicitly pass `4663` for mainnet.
Abyss infrastructure is canonical on mainnet. On `31337` and `46631`, Abyss fields
read bare, then `VITE_`, then `NEXT_PUBLIC_` environment overrides at import time.
Lending fields use that precedence on `31337` and public prefixes only on `46631`.
`liquidation_executor` is the exception: all three prefixes work on every chain.
Explicit zero-address overrides remain zero rather than selecting another deployment.
Local `31337` lending defaults remain zero.

Current mainnet lending pool is `0x5D8878b145904425C598f12EB8eD550985369a82` and its
addresses provider is `0x892faB533E8D04135D902F94974e45dB48C17697`. The address view
also includes current data/UI/wallet providers, oracle, protocol vault, native gateway,
and lending lens. This lens is distinct from the lifecycle lens in the launch view.
DEX ABIs/fee tiers/pool keys, exact TickMath, lending wallet/client factories,
reserve/user normalization, catalog overlays, and RAY/WAD helpers remain available.
