# SDK reference

Use the [launch guide](launch-guide.md) for the deployment sequence. This page
collects addresses, ABI boundaries, gas accounting, and integration-test setup
for SDK **0.16.0**.

## Resolve deployment addresses

Use `get_launch_addresses(4663)` in integrations. It returns the configured launch
graph and raises `KeyError` for unconfigured chains. A locally deployed core must
be supplied explicitly in the plan; lookup does not rewrite a saved plan.

| Contract / field | Robinhood mainnet address |
| --- | --- |
| Orchestrator | `0x91560876033d568d25CDe98C78c33ff8FC43962c` |
| Implementation registry | `0xB2B0f9F36617810D67b8fC175153Aa10024C1358` |
| Fee-owner registry | `0x64b5ca1f21B8E84305b0e4D847924dca39Eb1fcb` |
| Directory | `0x1c7694Ed0F6624cC5794814261B1523E91986bb5` |
| Fee-hub factory | `0xF14fD8D65D7833612A4bb909F1161731bb7039d3` |
| Token factory | `0xd3cE64E49224a9a96075633f63761FE6BE22FE30` |
| Funding escrow | `0x7bc77946CeF52A178583FCe1EEB7751983cEC703` |
| Existing launch inspection lens | `0xAce93e3910561aC67c6179c3a27F133F5Ef78F25` |
| Fixed V4 adapter / deployer | `0xb334b44509a5237a39cE364c159913F14b6D1186` / `0x1Df787Cc099B047A606059Ac80A8296779266f9e` |
| Dynamic V4 adapter / deployer | `0xDa8581E1241722154B44404a032C086708080519` / `0x59F83bC43A075C40c68dea444dcd9c639BE7F1E7` |
| Collector factory | `0x43b394fE6865EE6797c60FCCcd33D7D765214b52` |
| Canonical Abyss adapter | `0x3ef760fcbbD618Ab6cB7E308e7fB39C93b9deFD0` |
| `UFG_LAUNCH_PROXY_ADDRESS` | `0xf6673d859DE03eB74A0b01696Edb97f941dFD0CA` |
| `UFG_TREASURY_FACTORY_ADDRESS` | `0x26Aa5D52134c8E6c4C01E9fb53A7568c0C64901F` |
| `UFG_UPSTREAM_FACTORY_ADDRESS` | `0xA170b5A58C111334A7237F2Ea2c428ec5022f68E` |
| `RAETO_PAYOUT_REGISTRY_ADDRESS` | `0xBEC2C0e91612090F82df6743F31CDF18B4386b50` |

The core's deployment block is **82,880,313**. Start event scans there and chunk
requests to the provider's block-range limit (the public RPC permits ≤10M).
Use `get_known_lifecycle_deployment`, `get_known_lifecycle_profile`, and
`list_known_lifecycle_profiles` for release-owned construction metadata.
`read_lifecycle_profiles` remains an explicit live audit, not a planning prerequisite.
Planning reports pinned-input and capacity readiness; the wallet and on-chain enforcement determine execution.

## Check plan limits

Bounds below are structural limits, not proof that a transaction fits gas.

| Plan input | Limit |
| --- | --- |
| Markets | 1–16 |
| Funding output assets | 0–8, positive amounts, unique and numerically ascending |
| Fee assets | 1–8, unique and numerically ascending; owner + rewards + burn = 10,000 bps each |
| Ordered opening buys | 0–64; positive quote input and token-output minimum |
| Positions | 1–32 per market, **≤32 total** across all markets |
| V4 markets sharing a quote | At most one per launched-token/quote, across all V4 hooks/fees |
| Supply, ERC20 without rewards | 1 through `2**256 - 1` raw units |
| Supply, reward-enabled ERC20 | 1 through `10**77` raw units |
| Supply, ERC404 | 1 through `2**96 - 1` raw units |
| Executor collection reward | 0–1000 bps |
| Token name / symbol / metadata URI | 1–128 / 1–32 / ≤2048 UTF-8 bytes |
| Encoded market config / swap conversion calldata | 1–16,384 bytes each |
| Range-helper position count | 2–16; use the single-range recipe for one position |
| UFG reserves / exit fee | 1–7 sorted unique reserves / 0–2000 bps |

Every market's token budget is positive; market budgets sum to supply. Position
maxima sum to each market budget. Reward mode must agree with nonzero reward fee
shares. `LIFECYCLE_MAX_ERC20_SUPPLY`, `LIFECYCLE_MAX_REWARD_ERC20_SUPPLY`, and
`LIFECYCLE_MAX_ERC404_SUPPLY` export the raw supply bounds.

## Encode market configs

| Version | Model / codec | Use |
| --- | --- | --- |
| 1 | `LifecycleAbyssMarketConfig`; `encode_lifecycle_abyss_market_config` / `decode_lifecycle_abyss_market_config` | Canonical Abyss, profiles 0–3 |
| 4 | `LifecycleV4MarketConfig`; `encode_lifecycle_v4_market_config` / `decode_lifecycle_v4_market_config` | Shared V4 only on a matching admitted deployment |
| 5 | `LifecyclePoolBoundV4MarketConfigV5`; pool-bound encode/decode codec with expected version 5 | Historical bound V1 layout |
| 6 | `LifecyclePoolBoundV4MarketConfigV6`; `encode_lifecycle_pool_bound_v4_market_config` / `decode_lifecycle_pool_bound_v4_market_config` | Current pool-bound V4; inner and outer versions both 6 |

`LifecyclePoolBoundV4MarketConfig` is a V5/V6 union, not a constructor.
Config6 adds `minimum_hook_fee_pips` and
`fee_sensitivity_pips_seconds_per_tick` after `hook_fee_pips`.
Its minimum/maximum satisfy `0 <= minimum <= maximum <= 1_000_000`; sensitivity is
uint32 fee-pips × seconds/tick. These fields do not select the hook implementation.
Copy immutable treasury/denominator/author terms from the selected envelope.
`LifecycleV4Position` includes its own salt and maximum token amount.

HMM config6 appends `encode_hmm_launch_tail(config)`: sixteen ABI words, **512 bytes**.
`decode_hmm_launch_tail` / `validate_hmm_launch_config` enforce the exact V2 tuple.
UFG config6 appends the identical typed `ufg_launch_config` for each UFG market;
canonical Abyss UFG config1 has no tail. `encode_ufg_launch_config` encodes one
dynamic `(uint16,uint16,address[])` tuple, including its leading `0x20` offset.
`hash_ufg_launch_config` hashes those bytes; the decoder rejects noncanonical bytes.

Auto-compound config6 uses `auto_compound_config=AutoCompoundConfigV1(share_bps)`.
`encode_auto_compound_tail` / `decode_auto_compound_tail` encode exactly **64 bytes**:
`abi.encode(bytes32 version, uint16 compoundShareBps)`, with
`version=AUTO_COMPOUND_CONFIG_VERSION=keccak256("black-market.auto-compound.config.v1")`.
The decoder requires that version, canonical integer padding and no missing/extra
bytes. Shares must be integers in 0–10,000; LP/developer fees must be zero.
Use `is_auto_compound_profile` or the known-profile keys rather than detecting
auto-compounding from config6 alone. Both InputToken and QuoteOnly are supported.

Raeto config6 uses `raeto_launch_config=RaetoLaunchConfigV1(share_bps=500)`.
`encode_raeto_launch_tail` / `decode_raeto_launch_tail` enforce exactly
`abi.encode(uint16 shareBps)` (**32 bytes**) and the deployed menu
**500/1000/2500/5000/7500**. LP/developer fees must be zero, fee mode QuoteOnly,
and the quote WETH/USDG. All four auxiliary tail types are mutually exclusive;
tails cannot be attached to another profile. Invalid Raeto fees/quotes/tails use
`INVALID_RAETO_CONFIG`; `validate_raeto_launch_plan` checks the complete,
exclusively Raeto route with `INVALID_RAETO_PLAN`.

Salt mining preserves tails. The full plan hash commits them. HMM/UFG/Raeto constructors
commit the salt-normalized prefix only; **auto-compound constructors commit the
normalized prefix plus their entire 64-byte tail**. Changing a compound share
therefore changes initcode and the hook address. The V2 constructor remains twenty
words / 640 bytes. `launch_recipe.py` only encodes a bare prefix; `launch_submit.py`
neither builds tails nor accepts a UFG planner config.

**HMM V1 → V2 migration:** rebuild unstarted HMM plans with current generic profile
keys, explicit `max_bid_quote` / `reference_deviation`, and 512-byte tails.
The old 448-byte tails and V1 hook aliases are unsupported; do not edit an already
submitted commitment. Release history is in [CHANGELOG](../CHANGELOG.md).

Use `launch_plan_to_dict` / `launch_plan_from_dict` for portable plans (Solidity
camelCase keys and decimal strings for wide integers). `encode_launch_plan`,
`hash_launch_plan`, and `launch_id_of` operate on that exact commitment.
Offline builders/encoding do not establish live funding or execution admission.

### Auto-compound deployment evidence

The static/dynamic presets expose `AUTO_COMPOUND_STATIC_V1_PROFILE_ID` /
`AUTO_COMPOUND_DYNAMIC_V1_PROFILE_ID` and are generated from the October 28
readmission evidence plus Robinhood L2 block **100,485,106**
(`0x0f4c7b95e02b8258e57cb6dbabf306087f182e00efdb50562130b418e2d799f5`).
`tests/fixtures/auto-compound-registry-readback.json` preserves the four registry
responses, adapter getters, module identity/hash, creation-code chunks and mined
admission receipts. The retired profiles ending `1d68` / `e0d7` are excluded.

Regenerate against the read-only black-market reference checkout (PR #29,
`8d958b6`; pass its `contracts/` directory). `--block` selects an available L2 pin;
without it the generator records the latest header and pins every subsequent read:

```sh
python tools/ci/import_auto_compound_release.py /path/to/black-market/contracts
AUTO_COMPOUND_RPC_URL=https://rpc.mainnet.chain.robinhood.com \
BLACK_MARKET_REFERENCE_ROOT=/path/to/black-market \
python -m pytest -q -s tests/test_auto_compound.py -k live_factory
```

The opt-in test uses only chain reads/`eth_call`: it predicts a token, mines each
profile's hook salt, compares the SDK constructor/prediction with live factory
metadata, checks factory acceptance/rejection, and compares the **entire market**
and tail bytes with `scripts/auto_compound_config.py`. Ordinary pytest stays offline.
Capacity-v4 now includes reviewed auto-compound preparation/atomic/activation
budgets and position-dependent opening-buy costs. Readiness remains a capacity
and pinned-input assessment, not execution proof. After opening, compounding
executes inside `afterSwap`, so never cap swap gas below `eth_estimateGas`'s result.

## Raeto profiles

`KnownLifecycleProfile.V4_RAETO_FIXED_POOL` / `V4_RAETO_DYNAMIC_POOL` select
the October 29 Raeto deployment on the canonical Robinhood graph.

| Export | ID |
| --- | --- |
| `RAETO_FIXED_PROFILE_ID` | `0x9161833b162e7ca8160b11c64aedb307561e19a9136fd32f99cd797aab6da3ca` |
| `RAETO_DYNAMIC_PROFILE_ID` | `0x08387d00ee4f75f860810cc8fb434087328592c2f39ea3321109e589039f80e1` |
| `RAETO_FIXED_ADAPTER_ID` | `0x911d7ecefe56489d329235e3484829717dc00400d4181902f0f2de45f9338615` |
| `RAETO_DYNAMIC_ADAPTER_ID` | `0x8ab9c8c9b8fd4c8bbf12b780d9474463fa0fa52e3b450f0d5f68fd067dc1674d` |

`RAETO_QUOTE_ASSETS` is the checksummed WETH/USDG tuple;
`RAETO_SHARE_BPS_OPTIONS` is the immutable `(500, 1000, 2500, 5000, 7500)` menu.
The optional complete-plan validator does not replace per-market enforcement:
planning, combined preparation and continuation reject malformed Raeto markets
before RPC reads, including known-preset recovery.
`RAETO_FEE_COLLECTOR_V1_ABI` is the verified Node subset: `shareBps()`,
`owed(address)`, `launchToken()`, `quoteAsset()`, `setShareBps(uint16)`,
`claimRaeto(address)` and `RaetoShareUpdated` / `RaetoFeesBooked` / `RaetoFeesClaimed`.
See [collection-time fee ownership and payout semantics](launch-guide.md#raeto-launches).

`tests/fixtures/raeto-registry-readback.json` preserves Node's admission bytes,
registry responses, dependency graph, creation-code chunks, verified collector
ABI provenance and accepted/rejected deployed-share checks at Robinhood L2
block **101,180,179** (`0xe778e5d89ca2cab5843eedeb8c27b10f5463313d4b95b8971986ac72e297933c`).
The generated `_raeto_release_20261029.json` binds both profiles to that evidence.
To regenerate from a built, reviewed Node 0.16.0 checkout, **without RPC or forks**:

```sh
node tools/ci/import_raeto_release.mjs /path/to/black-market-node-sdk
```

The importer also preserves Node's measured 84-case `template-gas-matrix.json`
byte-for-byte and generates 20 Raeto wire/constructor vectors plus per-case Node
capacity/ordered-buy/calldata parity vectors. Tests check every observation and
forwarding estimate, at least 5% observed-gas margin, and exact bounds at
0/1500/5000 bps headroom. The matrix is an ordinary EVM fork, **not native ArbOS
execution evidence**; it is retained, never replayed by the Python suite.

## Configure HMM controls

`default_hmm_launch_config` sets strategy share to 50%; bid/ask widths 5, gap 1,
stale displacement 5 (all in tick spacings), stale time 86,400 seconds, premium
500 bps, four asks, and premium decay off. Token harvest = budget/100,000; offered
cap = 10%; acquired cap = 100%. WETH deploy/harvest thresholds are 0.0005/0.005;
USDG uses 1/10 whole units. Unknown quotes need explicit `min_quote_deploy` and
`min_quote_harvest`. Invalid small-budget or uint128-overflow settings fail.

Choose `max_bid_quote` and `reference_deviation` explicitly. The bid cap is positive
raw quote principal, ≤uint128.max. Reference deviation is a nonnegative int24 count
of tick spacings; zero still enforces the oracle reference boundary.
The strategy share applies to both currencies' post-treasury fees.

The current fee owner can update all fifteen settings with `setStrategyConfig`,
pause placements using `setPlacementsPaused`, and change future accrual using
`setStrategyShareBps`. Use `HMM_LIQUIDITY_MANAGER_V2_ABI` /
`HMM_FEE_COLLECTOR_V2_ABI`. Pausing queues auxiliary-liquidity withdrawal, not fee
collection shutdown. Lower intake caps do not liquidate existing inventory;
existing lots retain their stored premium and age. Locked launch positions stay locked.

## Choose fee units and tiers

**1,000,000 pips = 100%**; **10,000 bps = 100%**. LP fees, hook fees, protocol
share, collection reward, author allocation, and owner/reward/burn splits are
separate quantities. An author allocation comes from attributable owner fees
after the collection reward, not an additional trader tax.

`ABYSS_FEE_TIERS` contains:

| Pips | Percent | Tick spacing |
| ---: | ---: | ---: |
| 100 | 0.01% | 1 |
| 500 | 0.05% | 10 |
| 3000 | 0.3% | 60 |
| 10000 | 1% | 200 |
| 20000 | 2% | 400 |
| 50000 | 5% | 1000 |
| 100000 | 10% | 2000 |
| 150000 | 15% | 3000 |

The recipe infers spacing from these tiers when `tick_spacing` is omitted.
Current examples retain fee3000/spacing60. V4 configuration must separately match
its selected profile's fee bounds, mode, oracle, and external-liquidity policy.

## Account for Nitro gas

On chain4663, the deployment preset supplies the fixed **32M total-gas ceiling**,
including poster gas. `ArbGasInfo.getPricesInWei()` at `0x6c` supplies the
same-block fee context. ArbOS-version/backend probes and dynamic gas-limit
getters are not planning guards.

- `LaunchExecutionLimits.execution_gas_ceiling` is the fixed native envelope;
  `transaction_gas_ceiling` is separate tightening policy that can refuse it.
- `PlannedLaunch.assessment`, `LifecycleTransaction.assessment` and optional
  `LifecyclePlanningError.assessment` use `LifecyclePlanningAssessment`, not
  simulation or admission evidence.
- Assessment fields are `ready`, `block_number`, `block_hash`, `account`,
  `chain_id`, resolved `limits`, optional immediate `estimate` and `gas_profile`.
  Refusals carry a stable `code`, user-safe actionable `reason`, optional
  `failed_transaction_id`, `failure_category` (`semantic`, `capacity`,
  `affordability`) and `capacity_constraint` (`gas-envelope`, `calldata`,
  `activation`, `preparation`).
- `LifecycleGasProfile` contains `version`, `LifecycleLaunchGasBounds` and exact
  ordered `initial_buys`. Version `robinhood-4663-capacity-v4` uses the shared
  measured-max Node table, including Raeto and auto-compounding, with at least
  25% padding, not current execution evidence. `activation_compute_gas_upper_bound`
  is required; `atomic_compute_gas_upper_bound` is absent (`None`) for multiple
  markets or any conversion. Bounds exclude poster allocation and never size
  signing gas. Missing coverage still refuses `UNKNOWN_GAS_PROFILE`.
- `compute_lifecycle_opening_buys` is pure ordered unopened-curve math, including
  overlaps, bitmap-word steps, per-step rounding and opening hook fees.
  `compute_lifecycle_launch_gas_bounds(plan, predicted_token=..., ufg=...,
  headroom_bps=1500)` is the parallel pure capacity helper. Ordinary templates
  do not impose a curated-token allowlist; Raeto specifically allows only WETH/USDG.
- Planning never calls `eth_simulateV1`, executes a fork or runs launch calldata,
  including Kyber routes and consuming activation. Conversion protection is the
  committed calldata amountOutMinimum/deadline; opening buys use exact local
  curve math and the committed output floors.
- Every launch command uses the preset native ceiling, 32M total gas on Robinhood.
  Multi-market or conversion-funded plans are staged. A single-market,
  non-conversion atomic preference is selected only when its known budget fits.
  Preparation batches are contiguous and deterministic, accounting for each
  market's profile, positions, at least 25% padding (or greater supplied headroom),
  full calldata processing and poster allocation. Activation remains
  indivisible: an oversized bound refuses `ACTIVATION_GAS_LIMIT` with an actionable
  request to reduce positions or pools.
- Only the immediate command receives assessed gas/price and a fee estimate;
  future unsigned commands have neither. Approvals always leave gas/price and
  estimation to the wallet. Build-next refreshes current inputs and complete
  activation/funding/floor checks while constructing only its next command.
  A cached ready/refused assessment cannot override these checks.
- For calldata length `C`, the Nitro poster bound uses `N=C+384`,
  `B=N+8*(N//1024)+64`, `fee_bound=B*prices[1]`, and
  `poster_gas=2*ceil(fee_bound/prices[5])` (zero for zero data fee). This reserves
  serialized legacy/EIP-1559 envelopes with an empty access list; wallet-added
  access/authorization lists and AA wrappers need their own wallet envelope.
- Poster gas stays inside the 32M ceiling. Native estimates set
  `data_fee_included_in_gas=True`, `data_fee=0`, `total_fee=gas_limit*gas_price`,
  and `fee_confidence=\"native-ceiling-and-data\"`. Never add `poster_fee` again.
  Generic estimates without an external data-fee estimate use
  `fee_confidence=\"native-ceiling-only\"`. No estimate invents `gas_used`.
- Fresh native prices use the larger of `getPricesInWei()[5]` and twice the pinned
  base fee. Held prices remain exact and must cover the pinned required fee floor;
  held gas must equal the native ceiling or refuse `STALE_REVIEWED_GAS`.
  A lower complete caller gas policy refuses `GAS_POLICY_LIMIT`. The SDK never
  shrinks the native reservation to make an unaffordable transaction look ready.

The removed simulation/proof/admission APIs, fork options and aggregate simulation
limits have no compatibility aliases. Actual canonical receipts remain the
authority for launch progress, replacement reconciliation and settlement.
The wallet owns submission, active account/chain checks and nonce assignment;
there is no redundant send-RPC estimate gate after planning. Do not treat a public
RPC 429 as an economic revert or a capacity observation.

Lifecycle planning reads one authoritative L2 header, then batches independent
progress, factory prediction, prices and funding balances/allowances at its
explicit block number. Nitro's EVM `block.number`/`blockhash` and Multicall3's
corresponding helpers expose L1 values, so they are never used as L2 pins.
Prepared directory entries and dependent adapter reads are each one aggregate
stage. Native balances, allowances and Nitro prices share the same pinned aggregate.

Successful Multicall3 runtime attestation survives block changes for the transport
session. Chain identity is observed once per underlying client; failed observations
are evicted. Mutable reads and fees do not survive invocations. Planning does not
re-pin after mining or add final source-chain/canonical-header guard reads; actual
receipt identity and confirmation reads remain. EOA transaction counts are not
planning inputs. The wallet may recover an unknown next nonce once and then track
actual submitted nonces/replacements locally.

`LifecycleDeploymentPreset.hook_salt_miner` selects the deployed batched miner.
The optional `launch_lens` selects the new `LAUNCH_LENS_ABI.snapshot` path inside
the initial aggregate, including dependent prepared markets and hook candidates.
Robinhood's release preset enables `0xcb4bb3caca71fe52eace8170cf83e901418646f8`
automatically; callers supply no lens address. This is not the older inspection
lens returned by `get_launch_addresses(4663).lens` or `LAUNCH_LENS_V1_ABI`.
`launch_lens_code_hash` records the frozen runtime identity as deployment
metadata; it does not add a guard-only RPC read.

Launch commands use the fixed native ceiling from the deployment preset, not
profile-sized gas, poster additions or an exact-envelope override. Robinhood's
32M is the complete transaction gas limit. The pinned fee floor is
`max(ArbGasInfo.getPricesInWei()[5], block.baseFeePerGas)`. A held price below that
floor is refused rather than raised, even when a higher fresh recommendation is
available.

## Observe planner diagnostics

Attach an optional listener to the Web3 client before planning. It receives
`LifecycleDiagnosticEvent` with stage, phase (`queued/start/success/failure/reuse`),
durations/queue time, request ID, and method; listener failures do not change planning.

```python
from web3 import Web3
from black_market_sdk import LifecycleDiagnosticEvent, ROBINHOOD_MAINNET_RPC

def on_diagnostic(event: LifecycleDiagnosticEvent):
    print(event.stage, event.phase, event.method, event.duration_ms, event.queue_ms)

client = Web3(Web3.HTTPProvider(ROBINHOOD_MAINNET_RPC))
client.on_diagnostic = on_diagnostic
```

Only opt-in observation runs. Avoid adding keys, signed calldata, upload capabilities,
or credential-bearing URLs to your own diagnostic output.

In 0.14.0, build-next no longer emits nested `plan.context`, `plan.domain` or
`plan.inputs` stages for whole-plan metadata or suffix work.
No simulation, sizing or replay stages are emitted. Use the returned assessment
for completion, not a fixed diagnostic-stage sequence.

## Decode author claims

`read_author_hubs` pages 1–100 hubs. Claim builders page 1–10 hubs and at most eight
ascending unique assets; `assets=()` uses each hub's supported assets.
`decode_developer_claim_receipt(receipt, transaction=transaction)` validates emitter
and builder context. Factory rows mean **0 paid / 1 zero / 2 unsupported / 3 failed**.
`cursor_complete` does not imply `payments_succeeded`: retain failed rows for retry.
A successful direct call without the expected event reports `outcome="unobserved"`;
a zero claim emits no hub event. Estimate gas with factory/EIP-150 overhead and
inspect each row even when the outer transaction succeeds.

## Test against an API service

Use a real disposable Abyss API with private session routes enabled and an isolated,
migrated PostgreSQL database. Configure chain4663 and the supported orchestrator
as its attribution verifying contract. The SDK does not start that service.
Metadata-only sessions need no image store; image/publication checks additionally
need real storage, chain receipts, and the indexer.

| Variable | Test-service value |
| --- | --- |
| `LAUNCH_API_TEST_URL` | Actual test origin, for example `http://127.0.0.1:18763` |
| `LAUNCH_API_TEST_CHAIN_ID` | Default `4663`; match the service |
| `LAUNCH_API_TEST_ORCHESTRATOR` | Default current mainnet core; match authorization |
| `LAUNCH_API_TEST_PRIVATE_KEY` | Disposable EOA for explicitly selected signed HTTP tests; no chain funding needed |

```sh
export LAUNCH_API_TEST_URL=http://127.0.0.1:18763
export LAUNCH_API_TEST_CHAIN_ID=4663
export LAUNCH_API_TEST_ORCHESTRATOR=0x91560876033d568d25CDe98C78c33ff8FC43962c
python examples/launch_api_lifecycle.py
python -m pytest -q
python -m pytest -q tests/test_launch_api_http.py -m 'launch_api_http and not launch_api_write'
# Requires a disposable LAUNCH_API_TEST_PRIVATE_KEY and loopback service:
python -m pytest -q tests/test_launch_api_http.py -m launch_api_http
```

Default pytest excludes `launch_api_http`. Explicit HTTP selections fail when
service/key prerequisites are missing. Signed HTTP checks refuse remote services
and never send chain transactions. The API-only example defaults to read-only;
`--signed --state-file <path>` explicitly stages a metadata-only session and saves
private recovery state. Protect it; after uncertain issuance reconcile the original
request before requesting another session.

`LaunchApiConfig(allow_loopback_http=True)` allows HTTP only for explicit local
services; remote API URLs and presigned image URLs require HTTPS. JSON requests
have three attempts by default, including writes; image PUT has one. Use
`retries=1` for one attempt; `retries=0` sends nothing.

## Configure DEX and lending networks

`get_addresses()` defaults to workbench **46631**; pass **4663** for mainnet or
**31337** for local Anvil. This view is separate from `get_launch_addresses`.
Mainnet lending pool: `0x5D8878b145904425C598f12EB8eD550985369a82`;
addresses provider: `0x892faB533E8D04135D902F94974e45dB48C17697`.
The lending lens differs from the launch lens.

On 31337/46631, Abyss fields read bare, then `VITE_`, then `NEXT_PUBLIC_` environment
overrides at import time. Lending fields use that precedence on 31337 and public
prefixes only on 46631. `liquidation_executor` accepts all three prefixes on every
chain. Explicit zero-address overrides stay zero; local lending defaults are zero.
Use exported DEX/lending ABIs, wallet/client factories, pool keys, TickMath, catalog
overlays, and RAY/WAD helpers. Pool creation does not grant lending admission.
