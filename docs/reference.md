# SDK reference

Use the [launch guide](launch-guide.md) for the deployment sequence. This page
collects addresses, ABI boundaries, gas accounting, and integration-test setup
for SDK **0.14.0**.

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

The core's deployment block is **82,880,313**. Start event scans there and chunk
requests to the provider's block-range limit (the public RPC permits ≤10M).
Use `get_known_lifecycle_deployment`, `get_known_lifecycle_profile`, and
`list_known_lifecycle_profiles` for release-owned construction metadata.
`read_lifecycle_profiles` remains an explicit live audit, not a planning prerequisite.
Final simulation and on-chain enforcement determine execution eligibility.

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

Salt mining preserves tails. The full plan hash commits them; the hook constructor
commitment uses the salt-normalized prefix only. The pool-bound V2 constructor
remains twenty words / 640 bytes. `launch_recipe.py` only encodes a bare prefix;
`launch_submit.py` neither builds tails nor accepts a UFG planner config.

**HMM V1 → V2 migration:** rebuild unstarted HMM plans with current generic profile
keys, explicit `max_bid_quote` / `reference_deviation`, and 512-byte tails.
The old 448-byte tails and V1 hook aliases are unsupported; do not edit an already
submitted commitment. Release history is in [CHANGELOG](../CHANGELOG.md).

Use `launch_plan_to_dict` / `launch_plan_from_dict` for portable plans (Solidity
camelCase keys and decimal strings for wide integers). `encode_launch_plan`,
`hash_launch_plan`, and `launch_id_of` operate on that exact commitment.
Offline builders/encoding do not establish live funding or execution admission.

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

On chain4663, the SDK reads `ArbSys.arbOSVersion()` at `0x64` and
`ArbGasInfo.getMaxTxGasLimit()` / `getMaxBlockGasLimit()` at `0x6c` in one canonical
context. Subtract the returned version offset **55**; actual ArbOS version must be
**≥50**. Native compute ceilings come from these getters, not the large block
header gas field. Read failures stop admission.

- `LaunchExecutionLimits.execution_gas_ceiling` caps compute;
  `transaction_gas_ceiling` is separate total-envelope policy.
- Native `eth_simulateV1` runs the final calldata with `validation=True` at the
  exact complete envelope returned for signing. Never use a 50M discovery ceiling
  as that final envelope merely because the provider accepts a large request.
  Validated `maxUsedGas` (or `gasUsed`) includes native poster gas; it is not compute.
- `NodeInterface.gasEstimateL1Component()` at `0xc8` supplies one poster estimate
  for the immediate final command. Calibration and later commands do not quote it.
- Reuse protected-buy calibration's compute observation `G`, exposed as
  `simulation.steps[i].compute_gas_required`, when available. If missing, run
  unvalidated sizing. With compute `G`, size
  `max(G + R, ceil(G * (10000 + headroom_bps) / 10000)) + buffered_poster_gas`.
  Default headroom is 1500 bps. For atomic/activation with HMM,
  `R = 4_250_000 + 500_000 * (hmm_markets - 1)`; other commands use zero.
  Keep compute separate from prior complete gas or provisional suffix ceilings;
  a new poster quote can exceed the entire previous total.
- Bound both calibration/sizing discovery and new final proposals by policy and
  pinned native affordability after committed values. Nitro checks upfront fees
  even with `validation=False`. Validate once and return that exact final envelope.
  Discovery limits are per call: unused reservations refund before the next step.
  A balance-capped failure below the native ceiling does not authorize splitting
  a preparation batch as though it exceeded native compute capacity.
  Only a native upfront-funds admission refusal permits one second unvalidated
  sizing attempt with a shared spendable budget, then the one exact validated
  pass. Contract reverts, OOG and provider failures do not select this fallback;
  a repeated funds refusal never switches to a generic fork.
  Only the immediate envelope is final. Later steps prove feasibility with
  conservative affordable budgets and have `provisional=True`, no fee estimate,
  signing gas/price or admission. `build_next_transaction` freshly sizes each at
  its turn. Explicitly reviewed gas/price are never changed.
- Generic-EVM controlled-fork sizing uses gross `eth_estimateGas`; final exact
  validation does not size from refund-reduced receipt gas.
- Poster gas is already inside the envelope: `data_fee_included_in_gas=True`,
  `data_fee=0`, `total_fee=gas_limit * gas_price`. Do not add `poster_fee` again.
  Receipt/total gas can exceed the compute ceiling.
- Initial legacy gas price is the larger of the fresh recommendation and twice the
  pinned base fee. Held reviewed envelopes retain their exact gas and gas price.
- Native capability probing does not fund actual execution. Real affordability,
  nonces, receipts, and final simulation use source state. Generic Anvil cannot prove
  Robinhood Nitro compute/poster enforcement.

Preparation packing estimates **14.5M per HMM V2 market**, **16M otherwise**;
begin estimates **9.5M**, activation **6M per market plus HMM reserve**.
The default **50M aggregate simulation window** can leave a deferred suffix.
A deferred command has no signing envelope until fresh `build_next_transaction`
proof. Activation is indivisible. Set optional `LaunchExecutionLimits` only for
actual provider/account policy; its aggregate override is
`rpc_total_simulation_gas_limit`. Missing policy remains in `unknown_constraints`.

| Admission field | Values / meaning |
| --- | --- |
| `execution_proof` | `proved`, `failed`, `unavailable`: validated sequential execution |
| `protocol_fit` | `proved`, `failed`, `unknown`: protocol ceilings and supplied policy |
| `transport_preflight` | `not-requested`, `passed`, `failed`: immediate submission-RPC estimate |

`submission_client` on `build_next_transaction` estimates only the next envelope.
`LaunchSubmissionPreflightError` retains execution proof while marking transport
failure. Do not treat a public RPC 429 as an economic revert or a gas-cap measurement.

Lifecycle planning reads one authoritative L2 header, then batches independent
progress, factory prediction, Nitro limits and funding balances/allowances at its
explicit block number. Nitro's EVM `block.number`/`blockhash` and Multicall3's
corresponding helpers expose L1 values, so they are never used as L2 pins.
Prepared directory entries and dependent adapter reads are each one aggregate
stage. Poster quotes preserve caller/value and are never aggregated.

Successful Multicall3 runtime attestation, direct-EOA checks and Nitro capability
proofs survive block changes for the same source transport session. Mutable reads
and fees do not survive invocations. Planning does not re-pin after mining or add
a final source-chain/canonical-header guard; actual receipt identity and
confirmation reads remain.

`LifecycleDeploymentPreset.hook_salt_miner` selects the deployed batched miner.
The optional `launch_lens` selects the new `LAUNCH_LENS_ABI.snapshot` path inside
the initial aggregate, including dependent prepared markets and hook candidates.
It is unset until the owner deploys the new lens. This is not the older address
returned by `get_launch_addresses().launch_lens` or `LAUNCH_LENS_V1_ABI`.

`LifecycleFinalEnvelope(gas, gas_price=None)` is the optional exact atomic envelope
for `prepare_and_plan_lifecycle_launch`. Unlike
`account_transaction_gas_limit`, its gas is the actual final proof/signing gas.
An omitted price uses the invocation's single recommended fee; an explicit price
is not raised. Final exact validation, EOA/funding checks and Nitro admission remain.

## Observe planner diagnostics

Attach an optional listener to the Web3 client before planning. It receives
`LifecycleDiagnosticEvent` with stage, phase (`queued/start/success/failure/reuse`),
durations/queue time, request ID, and method; listener failures do not change admission.

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
`simulation.envelope` rescue events are removed. Ordinary final validation uses
`simulation.measure`, also used by an optional sizing pass; only explicitly
reviewed proof retains `simulation.replay`. Use returned admission/outcomes for
completion, not a fixed event sequence or the presence of a particular stage.

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
