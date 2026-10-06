# Launch guide

The SDK separates read-only discovery, explicit economic plans, unsigned transaction
building, wallet submission, and metadata publication. An address configuration is
not a plan: every `LaunchPlanV1` and EIP-712 payload names its own chain and orchestrator.

Version **0.5.0** makes caller-supplied execution policy optional, separates exact
execution/protocol proof from submission-RPC preflight, and reads native Nitro
compute ceilings and poster budgets on chain4663. The default gas headroom is 15%.

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

`LaunchExecutionLimits` and `LifecycleLimitResolver` are optional tightening policy,
not a mandatory backend service. Missing RPC/account/calldata policy stays explicit
in `unknown_constraints` without rejecting otherwise proved execution. Supplied caps
are enforced; a failed/malformed resolver or mismatched supplied block/hash, chain,
orchestrator or account is rejected. The SDK's own canonical simulation context
binds the actual proof even when independent policy provenance was not supplied.

`LaunchSimulation` and each `LifecycleTransaction.admission` expose three separate
outcomes:

| Field | Values | Scope |
| --- | --- | --- |
| `execution_proof` | `proved`, `failed`, `unavailable` | Complete sequential measurement followed by exact gas-envelope replay and all launch postconditions |
| `protocol_fit` | `proved`, `failed`, `unknown` | Pinned protocol ceilings and known tightening policy; an economic revert does not prove gas fit |
| `transport_preflight` | `not-requested`, `passed`, `failed` | Optional immediate-next-transaction estimate on the supplied submission RPC |

`admitted` certifies the exact current execution against proved protocol and known
policy, not wallet acceptance or future execution. On generic EVM chains, the
bounded header and actual validated backend are used; an absent independent
per-transaction cutoff is reported, never replaced with an invented Ethereum cap.
An optional `data_fee_estimator` still adds external chain-specific fees exactly
once. Without it, external data fees remain explicitly unknown.

On chain4663, the SDK pins `ArbSys.arbOSVersion()` at `0x64` and
`ArbGasInfo.getMaxTxGasLimit()` / `getMaxBlockGasLimit()` at `0x6c` to the same
canonical context. ArbSys returns `55 +` the actual ArbOS version; actual version
50 or newer and valid positive decoded ceilings are required. Read failure never
falls back to the unusually large header gas limit or a hard-coded compute cap.
`limits.execution_gas_ceiling` is the native compute ceiling;
`limits.transaction_gas_ceiling` is a separate known total-envelope policy, if any.

Native Nitro permissive simulation measures compute, including a gross
`maxUsedGas` requirement when returned. `NodeInterface.gasEstimateL1Component()` at
`0xc8` supplies a pinned exact-calldata poster budget even for future dependent
steps. Each budget receives integer-ceiling headroom separately. The validated
native replay charges actual poster gas and proves ArbOS compute enforcement;
the SDK never subtracts an approximate poster quote to assert exact compute use.
The total gas envelope and receipt gas may exceed the compute ceiling.
`poster_gas` / `poster_fee` are budget observations, not extra charges:
`data_fee_included_in_gas=True`, `data_fee=0`, and `total_fee` is already
`gas_limit * gas_price`. A generic Anvil fork cannot prove native ArbOS metering
and is refused for Nitro, while generic EVM controlled-fork support is unchanged.

A separate non-persisting native capability probe verifies all three getters
through validated simulation. Only that isolated probe temporarily supplies its
sender balance so an exactly funded payer need not fund probe calls. Actual
lifecycle measurement, exact replay, nonce/state checks and affordability always
use real source state without balance/code/allowance injection.

Pass `submission_client=...` to `build_next_transaction` to estimate only the
immediate next unsigned transaction on the active submission reader. It checks
the reader's chain before and after `eth_estimateGas` and uses the exact reviewed
`from/to/data/value/gas/gasPrice` at `latest`; no gas-envelope expansion, signatures,
or writes occur. A refusal raises `LaunchSubmissionPreflightError` with
`code="SUBMISSION_PREFLIGHT_FAILED"` and a copied simulation retaining its proved
execution admission but `transport_preflight="failed"`. A returned transaction's
admission has `transport_preflight="passed"`. Planning does not estimate future
dependent steps against nonexistent current state. Wallet/account/chain guards
are still required immediately around the eventual signature request.

Source chain identity is checked again at native-probe, sequential-simulation and
admission completion, in addition to canonical block identity. Transport drift to
a different-chain fork sharing the same block hash raises `LaunchStateChanged`
with `code="CHAIN_MISMATCH"`. Headroom is an integer from 0 through 10000 bps;
header and transaction/simulation/execution/compute gas ceilings are positive
uint64 values. Chain/version/provenance and fee quantities retain their own widths;
calldata/request byte caps must be positive safe integers.
Gas-buffer arithmetic itself stays exact at arbitrary integer width. A zero or
uint64-overflow reviewed gas budget is refused before replay, with unavailable
execution proof and failed protocol fit; it cannot emit a next transaction.

Reread after receipts, replacement, reload, wallet switch, or reorg. Direct EOA
execution and settled nonces remain required; contract accounts are not silently
treated as supported wallets. Unconfirmed approvals and local counters are not authority.
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

# Plan/simulate/recover; no caller policy or limits service is required.
: "${RPC_URL:?}" "${CREATOR:?}"
python examples/launch_submit.py "$PLAN_JSON" --rpc-url "$RPC_URL" \
  --account "$CREATOR" --mode staged --finalize-bound
# Optional: add --submission-rpc-url for a read-only immediate-next preflight.
# Known --chain-gas-cap/--rpc-gas-cap/--account-gas-cap/--calldata-cap only tighten.
```

Supply `--transaction-hash` and `--confirmations` for recovery. Do not use
`--finalize-bound` after submission. For complete deliberate launches, use the
standalone examples below rather than a manifest or fixture-driven harness.

## Standalone token-launch examples

These examples use the installed public `black_market_sdk` and run one fixed case
per file. **Invoking an example with a configured wallet is your deliberate
execution action: real funds are spent and token/market/custody state is permanent.**
There are no CLI parameters, fixture files, default keys, chain-only mode, or
unsigned plan-only variant. Use `launch_recipe.py` / `launch_submit.py` above for
the separate unsigned plan workflow.

### Setup once

From this checkout, or with the selected file and its two helper files copied
together into your own `examples/` directory:

```sh
python -m pip install black-market-sdk
cp examples/.env.example .env
chmod 600 .env
```

Edit working-directory `.env` to set `PRIVATE_KEY` and `LAUNCH_API_URL`. Both are
required before any RPC/API call; the creator is derived from the key, never from
an unlocked node account or a public development key. The API must run the real
launch router, session database, current `LaunchActivated` indexer and public DTO
against the execution chain. There is no guessed production API or silent API skip.

`RPC_URL` defaults to `ROBINHOOD_MAINNET_RPC`. Remote RPC/API URLs require HTTPS;
explicit loopback HTTP is accepted for owned local services. API URL credentials,
queries and fragments are rejected. RPC URL credentials are redacted in diagnostics.
The examples resolve canonical chain4663 addresses from `get_launch_addresses`,
verify live escrow WETH/18 decimals, discover admitted profiles and the real
registered **P1 `(1,4096)`** oracle from the bound V4 oracle factory.

Fixed chain4663 examples use the execution RPC's native simulation backend.
Generic Anvil/Hardhat simulation cannot prove ArbOS metering and is not offered
as a fallback. Generic-EVM controlled forks remain available through the SDK and
the saved-plan CLI. The examples do not invent transaction or calldata caps;
optional `LAUNCH_*_CAP` environment values only tighten known policy. Each
immediate step receives a fresh read-only submission preflight on `RPC_URL`.
Unavailable or unsuccessful full-case simulation fails admission without scenario
fallback, market removal or source rollback. The capability-only probe described
above does not alter actual launch funding or state.

Existing process environment wins. The small stdlib `.env` loader reads once and
supports ordinary `KEY=value`, quoted values and comments, with no shell
interpolation, command execution or extra dependency.

### One command per case

```sh
python examples/launch_erc20_v4.py
python examples/launch_erc20_abyss.py
python examples/launch_erc404_v4.py
python examples/launch_erc404_abyss.py
python examples/launch_erc20_staking_v4.py
python examples/launch_erc20_dividends_abyss.py
python examples/launch_erc20_burn_mixed.py
python examples/launch_erc404_dividends_mixed.py
```

| File | Fixed case |
| --- | --- |
| `launch_erc20_v4.py` | ERC20/no rewards, one V4 position, one buy, atomic |
| `launch_erc20_abyss.py` | ERC20/no rewards, one Abyss position, one buy, atomic |
| `launch_erc404_v4.py` | ERC404/no rewards, one V4 position, one buy, staged |
| `launch_erc404_abyss.py` | ERC404/no rewards, one Abyss position, one buy, atomic |
| `launch_erc20_staking_v4.py` | ERC20/staking, two V4 positions, two ordered buys, staged |
| `launch_erc20_dividends_abyss.py` | ERC20/holder dividends, three Abyss positions, one buy, staged |
| `launch_erc20_burn_mixed.py` | ERC20/3000-bps token burn, two dual-fee V4 + one Abyss position, one buy per market, staged |
| `launch_erc404_dividends_mixed.py` | ERC404/holder dividends, two V4 + three Abyss positions, one buy per market, staged |

Each invokes `run_launch_example(case)` in `examples/launch_examples.py`, supported
by `examples/_launch_support.py`. Copy these helpers alongside the selected example
for use with an installed SDK. No source-tree import injection is used.
ERC20 supply is 1,000,000 tokens; ERC404 supply is 10,000 tokens with 100-token NFT
units. Position liquidity is `1000 * 10**18`, each market has a `1100 * 10**18`
launch-token budget, and each opening buy uses `10**15` native input wrapped through
canonical WETH. Config5 bound V4 and config1 canonical Abyss QUOTE_ORACLE profile3
are discovered live; the full plan is salt-finalized and admitted without reducing
its selected economics.

Optional `NFT_BASE_URI` supplies your own hosted ERC404 NFT base URI; the default
is empty and does not pretend a metadata service exists. NFT units/mirrors remain
part of the ERC404 case and on-chain verification.

### Example ceilings, signing, and API publication

Default gas ceilings are 16,000,000 for chain/RPC/account; calldata is 131072 bytes
and headroom is 1000 bps. These are **EXAMPLE ceilings, not verified provider or
account limits**, and chain gas is bounded by every observed block gas limit.
Optional `LAUNCH_CHAIN_GAS_CAP`, `LAUNCH_RPC_GAS_CAP`, `LAUNCH_ACCOUNT_GAS_CAP` and
`LAUNCH_CALLDATA_CAP` environment values select reviewed positive integer ceilings;
they do not bypass exact SDK full-case admission or replay.

The real SDK admission runs before signed API metadata staging. The wallet signs
the complete attribution EIP-712 payload locally with creator/chain/domain checks
and signs exact SDK transaction envelopes without changing supplied gas, value,
calldata, nonce or fees. Normal nonce/fee lookup occurs only for absent fields.
Source RPC signing and unlocked-account sends are forbidden.

Before `eth_sendRawTransaction`, the computed signed hash and exact unsigned
envelope are fsynced to `receipts.json` as a broadcast attempt. Raw bytes are
retained only in `signed-<hash>.private.json` (mode0600). Broadcast has no retry;
timeouts and wrong returned hashes retain the computed hash for manual recovery.
RPC transport retries are disabled and requests have a 120-second bound.

Real receipts and canonical chain invariants prove token, markets, permanent
custody and ordered buys. The API receives only the confirmed atomic/activate
transaction hash. Only actual `202`/`LaunchPublishPending` responses are polled,
respecting `Retry-After` within the 90-second deadline. Other errors fail immediately.
The session and real `/api/v1/launches/<token>?chainId=4663` DTO must match the token,
activation block/hash, canonical status and staged metadata; no invented projection
fills an unavailable indexer.

### Retained results

Every run automatically creates `launch-results/<timestamp-case-random>/`, with
no user output option. Artifacts remain after errors or Ctrl+C:

| Artifact | Evidence |
| --- | --- |
| `run.json`, `plan.json` | Exact case/configuration, fresh nonce/salt, finalized portable plan |
| `events.jsonl`, `admission.json` | Stages/timing, actual backend/admission, gas/envelope proof, redacted RPC failures/revert traces |
| `receipts.json`, `chain.json` | Pre-broadcast computed hashes/envelopes, real receipts, Active token/markets/custody/buys |
| `api.json`, `result.json` | Actual publication DTO when reached, failed stage/traceback/causal/revert/API details |
| `recovery.private.json` | Mode0600 exact signed API request, session capability, activation hash and latest response |
| `signed-<hash>.private.json` | Mode0600 signed raw transaction, exact envelope and computed hash |

Never share private files. Public output recursively redacts all known keys,
signatures, capabilities, authorization, secret environment values and endpoint
credentials. A failure exits nonzero and preserves execution-chain state. There
is no automatic retry, relaunch or rollback. Inspect the original hash/token/session
after a broadcast or API failure: another invocation deliberately creates another
token, not a recovery attempt.

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
