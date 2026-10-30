# Launch guide

Use SDK **0.16.1** on Robinhood mainnet (**4663**). Start with the
[one-market example](../README.md#run-your-first-launch), or integrate `LaunchPlanV1` below.
Token amounts use raw integer units; USD inputs use 18 decimals.

## Choose an offering

Use the shipped preset catalog below to choose a profile. These optional live audits need neither funds nor a key:

```sh
python examples/quickstart.py
python examples/launch_matrix.py
```

`quickstart.py` prints ID, venue, `admitted`, and refusal `reason`; `launch_matrix.py`
adds registration, topology, bounds, and author terms as JSON. Retired profiles can
remain registered: filter by live admission. For another deployment, pass its actual
`--chain-id`, `--rpc-url`, and `--orchestrator`; only chain4663 has shipped launch addresses.

| Offering | Select | Decide before building |
| --- | --- | --- |
| Canonical Abyss | `ABYSS_0` STANDARD; `ABYSS_1` STANDARD_ORACLE; `ABYSS_2` QUOTE; `ABYSS_3` QUOTE_ORACLE | Standard fees use both currencies; quote fees use the quote asset. Choose an enabled fee tier and oracle mode. |
| Fixed V4 | `V4_FIXED_FEE_POOL` | One pool-bound hook per pool; `hook_fee_pips` is its fixed fee. |
| Dynamic V4 | `V4_DYNAMIC_FEE_POOL` | Zero LP/developer fee; set minimum, maximum, and sensitivity. Upward quote-per-token tick velocity raises the hook fee; quiet/falling prices use the minimum. |
| HMM V2 | `V4_HMM_FIXED_POOL` / `V4_HMM_DYNAMIC_POOL` | Zero LP/developer fee, explicit bid cap and reference deviation; [configure HMM](#hmm-launch-configuration). |
| UFG treasury | Canonical Abyss QUOTE variants or `V4_UFG_FIXED_POOL` / `V4_UFG_DYNAMIC_POOL` | ERC20 without rewards; quote fees become redeemable reserves. [Use the proxy route](#ufg-treasury-launches). |
| Auto-compounding | `V4_AUTO_COMPOUND_STATIC_POOL` / `V4_AUTO_COMPOUND_DYNAMIC_POOL` | Zero LP/developer fee; compound 0–100% of net hook fees into locked full-range liquidity. [Configure compounding](#auto-compounding-launches). |
| Raeto fee sharing | `V4_RAETO_FIXED_POOL` / `V4_RAETO_DYNAMIC_POOL` | WETH/USDG, QuoteOnly, zero LP/developer fee; share 5/10/25/50/75% of post-treasury quote fees. [Configure Raeto](#raeto-launches). |

Choose ERC20/ERC404 and no rewards/staking/dividends separately from the profile.
ERC404 examples use 100 tokens per NFT unit. An oracle pool does not grant lending admission.

Select shipped construction metadata without an RPC:

```python
from black_market_sdk import (
    KnownLifecycleProfile, get_launch_addresses,
    get_known_lifecycle_profile, list_known_lifecycle_profiles,
)

addresses = get_launch_addresses(4663)
catalog = list_known_lifecycle_profiles(
    chain_id=4663, orchestrator=addresses.orchestrator,
)
fixed = get_known_lifecycle_profile(
    chain_id=4663, orchestrator=addresses.orchestrator,
    key=KnownLifecycleProfile.V4_FIXED_FEE_POOL,
)
assert fixed is not None and fixed.supported
print([entry.key.value for entry in catalog])
print(fixed.profile.id)
```

Selectors return `None`/an empty tuple for an unknown deployment. A supported
preset supplies construction fields, not current permission to execute.
`read_lifecycle_profiles(client, orchestrator=addresses.orchestrator)` is available
for explicit live discovery. Normal known-profile planning uses release presets;
the wallet and on-chain enforcement determine execution. Planning is not execution proof.

## Price the launch

### Choose a starting valuation

Opening FDV = token price × **total minted supply**. It is a valuation, not money
deposited: the opening liquidity positions contain only the launch token.
With **1,000,000 tokens** and a **$5,000** target, the unit price is **$0.005**.
At an assumed WETH price of **$2,486.49**, that is about **0.00000201 WETH/token**.

The examples default to `$5,000` through `EXAMPLE_TARGET_MARKET_CAP_USD`.
The app uses a `$15,000` default/minimum, with $15K/$25K/$50K/$100K/custom choices.
Neither default establishes an optimal cap or an anti-sniper guarantee. Holding
shape and fees fixed, the same buy acquires a smaller share at a higher cap.

### Convert decimals and address order

Let `q` be human quote units per token, `dL` launch decimals (18), and `dQ` quote
decimals. The launch token is token0 when its address is numerically smaller.
The historical argument `launched_token_is_quote=True` means **launch token0**.
Use `predict_launch_token` to determine that ordering before deriving ranges.

| Launch token | Raw token1/token0 ratio | Opening end of its one-sided range |
| --- | --- | --- |
| token0 / `True` | `q × 10**(dQ-dL)` | Lower tick; buys move toward the upper boundary |
| token1 / `False` | `1 / (q × 10**(dQ-dL))` | Upper tick; buys move toward the lower boundary |

`sqrtPriceX96 = floor(sqrt(raw_ratio) × 2**96)`. For a six-decimal $1 quote,
$0.005/token gives raw ratio **5 × 10^-15**, not 0.005. `1 << 96` means raw-unit
parity, not a human USD price. Use integer helpers for the final tick alignment.

### Derive the opening pool

This arithmetic example assumes USDG at $1; it does not fetch a live price.
Half the supply funds this pool; the other half must fund another market in a
complete plan. Always pass `supply`: the helper's omitted default is **1B tokens**.

```python
from black_market_sdk import derive_launch_pool_recipe

recipe = derive_launch_pool_recipe(
    paired_token_decimals=6,
    paired_token_usd_price_x18=10**18,
    target_market_cap_usd_x18=5_000 * 10**18,
    launched_token_is_quote=True,
    fee=3000,
    supply=1_000_000 * 10**18,
    token_budget=500_000 * 10**18,
)
fdv_x18 = (
    recipe.launch_sqrt_price_x96**2 * (1_000_000 * 10**18) * 10**18
    // ((1 << 192) * 10**6)
)
print(recipe)
print(fdv_x18)
```

| Output | token0 (`True`) | Same inputs with token1 (`False`) |
| --- | ---: | ---: |
| `launch_tick` | -329280 | 329280 |
| `launch_sqrt_price_x96` | 5610644101265567582035 | 1118784514236214582089729076342544510 |
| `liquidity` | 35408142276778286 | 35408142276778286 |
| Tick range | [-329280, 887220] | [-887220, 329280] |
| Raw launch-token maximum | 500000000000000000000000 | 500000000000000000000000 |
| Raw quote deposit maximum | 0 | 0 |

The printed `fdv_x18` is **5014946157970295405007**: aligned opening FDV
**$5,014.946157970295405007**, about **$0.005014946158/token**. Show both the
requested and aligned valuation in your review. Tick rounding changes the opening.

### Split supply across pools and bands

For multiple pools, keep **one full-supply valuation** and convert it through each
quote's USD price/decimals. Split token inventory between pools, not FDV. Two
half-supply pools each priced at $5,000 FDV do not make a $10,000 launch.
Record each quote address, decimals, USD seed, source, and observation time.
The runner fetches prices through `LAUNCH_API_URL` with a 15-second timeout.

Use `derive_launch_position_ranges` for two to sixteen bands (default five):

```python
from black_market_sdk import derive_launch_position_ranges

bands = derive_launch_position_ranges(
    launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
    launched_token_is_quote=True,
    token_budget=500_000 * 10**18,
    tick_spacing=60,
    venue="uniswap-v4",
    distribution_preset="early-scarcity",
    position_count=4,
    position_gaps="adjacent",
    extend_final_range_to_boundary=True,
)
print([position.inventory_bps for position in bands.positions])
```

This prints **[366, 975, 2805, 5854]**: 3.66% / 9.75% / 28.05% / 58.54% of the
pool budget. Early Scarcity puts less inventory near opening; Staircase uses stepwise
weights; Smooth Ramp increases them gradually. App Single range/Even spread/Layered/
Higher ground presets are separate. Use `venue="abyss"` for Abyss; runner `CASE` uses `"v4"`.

Keep `position_gaps="adjacent"` for continuous opening-to-boundary coverage;
Early Scarcity/Staircase otherwise retain source gaps. Fund the final boundary tail.
Planner exceptions `allow_topology_gaps` / `allow_missing_permanent_tail` change this
requirement; they do not repair mistaken geometry. One position uses the recipe directly.

Market budgets sum to full supply; position maxima sum to each market budget.
All unspent mint inventory is **burned before opening buys**, not a creator reserve.
Review retained supply and burn: undersized positions can burn more than rounding dust.

### Size and protect the opening buy

`EXAMPLE_BUY_AMOUNT_WEI` defaults to **0.00001 ETH per buy**. At the WETH seed above,
that is about $0.02486: smoke buys acquired roughly 4.9 tokens out of 1M, not a
useful depth test for a $100 order. Preview your actual size and show net tokens,
share of retained supply, total spend including fees, and post-buy spot price.
A historical launch bought about 61% of supply with a buy larger than its opening
quote-denominated FDV; spend alone does not describe the allocation.

| Helper / option | What it protects or estimates |
| --- | --- |
| `estimate_launch_initial_buy` | Pure **first-band** estimate; excludes later bands, earlier ordered buys, and hook effects. Do not derive executable minima from it alone. |
| `compute_lifecycle_opening_buys(plan, predicted_token=...)` | Exact ordered unopened release-template curves, including band crossings and hook fees. Not a live quote or execution proof. |
| `derive_launch_buy_sqrt_price_limit_x96` | Directional **price guard**; a tight guard can truncate a buy. It is not a token-output minimum. |
| `buy_slippage_bps=50` | Commits ordered exact local quote minima for supported curves, including conversion-funded plans. |

`min_token_out = quoted_token_out * (10000 - buy_slippage_bps) // 10000`.
For illustrative output 123456789 and 50 bps, the minimum is **122839505**.
Conversion routes rely on their committed calldata output floor and deadline.
A conversion floor protects ETH→quote, not quote→launch token. A minimum of
**1 wei is unprotected**.

## Build the plan

`LaunchPlanV1` and its nested models are frozen dataclasses. Use `dataclasses.replace`
to complete a draft; assigning `creator` or `nonce` directly raises an error.
This function builds one plain Abyss/WETH market. Supply a fresh nonce/salt and a
current WETH USD-X18 price. It reads token prediction but does not sign or submit.

```python
from dataclasses import replace
from web3 import Web3
from black_market_sdk import (
    LaunchPlanV1, LifecycleTokenConfig, LifecycleAssetFunding,
    LifecycleFeeAssetPolicy, LifecycleFundingKind, LifecycleInitialBuy,
    LifecycleMarketConfig, LifecycleAbyssMarketConfig, LifecycleAbyssPosition,
    predict_launch_token, encode_lifecycle_abyss_market_config,
    derive_launch_buy_sqrt_price_limit_x96,
)

def build_abyss_plan(client, *, creator, nonce, salt, quote_usd_x18):
    creator = Web3.to_checksum_address(creator)
    addresses = get_launch_addresses(4663)
    quote = addresses.wrapped_native
    deadline = int(client.eth.get_block("latest")["timestamp"]) + 3600
    draft = LaunchPlanV1(
        chain_id=4663, orchestrator=addresses.orchestrator,
        creator=creator, nonce=nonce,
        token=LifecycleTokenConfig(
            kind=0, reward_mode=0, name="Example Token", symbol="EXAMPLE",
            supply=1_000_000 * 10**18, nft_unit=0, metadata_uri="",
            salt=salt, inventory_recipient=creator, burn_on_cancel=False,
        ),
        funding=(), fee_assets=(), markets=(), buys=(),
        deadline=deadline, executor_fee_bps=275,
    )
    token = predict_launch_token(client, draft)
    token0 = int(token, 16) < int(quote, 16)
    opening = derive_launch_pool_recipe(
        paired_token_decimals=18, paired_token_usd_price_x18=quote_usd_x18,
        target_market_cap_usd_x18=5_000 * 10**18,
        launched_token_is_quote=token0, fee=3000,
        supply=draft.token.supply, token_budget=draft.token.supply,
    )
    preset = get_known_lifecycle_profile(
        chain_id=4663, orchestrator=draft.orchestrator,
        key=KnownLifecycleProfile.ABYSS_0,
    )
    assert preset is not None and preset.supported
    config = LifecycleAbyssMarketConfig(
        profile=0, fee=3000, oracle_config_id=bytes(32),
        opening_sqrt_price_x96=opening.launch_sqrt_price_x96,
        positions=(LifecycleAbyssPosition(
            opening.tick_lower, opening.tick_upper,
            opening.liquidity, draft.token.supply,
        ),),
    )
    amount = 10**13
    return replace(
        draft,
        markets=(LifecycleMarketConfig(
            preset.profile.registration.adapter_id, preset.profile.id, quote,
            draft.token.supply, 1, encode_lifecycle_abyss_market_config(config),
        ),),
        funding=(LifecycleAssetFunding(
            quote, amount, LifecycleFundingKind.NATIVE_WRAP,
            quote, amount, "0x0000000000000000000000000000000000000000", b"",
        ),),
        fee_assets=tuple(LifecycleFeeAssetPolicy(asset, 10000, 0, 0)
                         for asset in sorted((token, quote), key=lambda a: int(a, 16))),
        buys=(LifecycleInitialBuy(
            0, amount, 1, creator,
            derive_launch_buy_sqrt_price_limit_x96(
                launch_sqrt_price_x96=opening.launch_sqrt_price_x96,
                launched_token_is_quote=token0, slippage_bps=50,
            ),
        ),),
    )
```

The buy's draft minimum is 1; use the protected preparation below before signing.
For fixed/dynamic V4, construct `LifecyclePoolBoundV4MarketConfigV6`, encode with
`encode_lifecycle_pool_bound_v4_market_config`, and set outer `config_version=6`.
Copy treasury, denominator, beneficiary, and terms from the **selected** envelope;
use `hook_salt=bytes(32)` before unified preparation. See [codecs](reference.md#encode-market-configs).
Only one V4 market per token/quote is allowed; consolidate its positions. Abyss can
use that quote in another market.

| Funding choice | `LifecycleFundingKind` | Payment / approval |
| --- | --- | --- |
| Native ETH → WETH | `NATIVE_WRAP` | Wrap ETH in escrow; native value, no ERC20 approval. |
| Native ETH → another quote, such as USDG | `SWAP` | Committed Kyber conversion; native value. |
| Existing quote ERC20 | `ERC20` | Approve funding escrow for the exact input; UFG instead approves its proxy. |

For swaps, `asset/amount` is the output quote/floor; `input_asset/input_amount` is
the payment. The admitted router's executable `target/data` belongs in funding,
not a separate wallet swap into an inactive token. Funding outputs are sorted and
unique; aggregate all buys using the same quote. Gas ETH is additional.

The runner sets **plan deadline = now + 3600 seconds** and **conversion deadline =
now + 300 seconds**. Conversion validation permits at most 1200 seconds and cannot
exceed the plan deadline. A long staged run can outlive its route while the plan
is still valid; increasing gas does not refresh the committed conversion.

## Prepare and assess

Use synchronous `Web3` clients. Unified preparation is asynchronous:

```python
from black_market_sdk import prepare_and_plan_lifecycle_launch

async def prepare_first_launch(client, draft):
    launch = await prepare_and_plan_lifecycle_launch(
        client, draft, account=draft.creator, mode="atomic",
        buy_slippage_bps=50, confirmations=1,
    )
    if not launch.assessment.ready:
        raise RuntimeError(launch.assessment.reason)
    return launch
```

Use `asyncio.run(prepare_first_launch(client, draft))` from a synchronous program,
or `await` it in an existing event loop. Keep the returned `launch.plan`, hash,
mode, and confirmation policy: V4 salts and calibrated minima may change the draft.
Preparation mines bound-hook salts with the preset's deployed miner, verifies the
returned CREATE2 addresses locally, reads funding, and assesses deterministic
capacity and pinned inputs. It never executes launch calldata or a conversion.
Without a configured miner it uses local mining. `plan_launch` is synchronous for
an already-finalized plan.

A ready immediate launch command includes gas and gas price; an approval
omits both for wallet preparation. Neither supplies a wallet transaction nonce.
The wallet assigns it; `LaunchPlanV1.nonce` remains committed launch identity,
including UFG principal namespacing. Optional
`LifecycleReceiptReference.nonce` / receipt-status nonce records an actual submitted
transaction, not a planning observation.
If retained nonce evidence conflicts with the actual submitted/replacement
transaction, reconciliation refuses it with `RECEIPT_NONCE`; it is not overwritten.
Use the immediate envelope for review instead of another unreviewed planning pass.
Before signing, call
`build_next_transaction(client, launch, reviewed_transaction=launch.transactions[0])`
once for fresh canonical progress and envelope assessment. The wallet must verify
its active account/chain; the SDK's chain observation is cached per client session.
Canonical launch progress and included/replacement receipt confirmations remain;
unrelated global mempool activity is not a launch-confirmation gate.

Build-next constructs only the immediate command without repeating profile
discovery or executing a suffix. It recomputes complete activation capacity,
ordered local buy floors, deadline and aggregate funding requirements before
starting a launch or approving funding. A cached ready flag is display state,
not authority: refreshed inputs can refuse a previously ready plan or permit
one whose earlier affordability refusal has been resolved.

`launch.assessment` is a `LifecyclePlanningAssessment`: `ready`, the L2 pin and
account/chain, resolved `limits`, optional immediate `estimate`, and a versioned
`gas_profile` with capacity bounds and exact ordered opening quotes. Refusals
include a stable `code`, user-safe actionable `reason`, failure category and any
capacity constraint. These fields do not claim successful contract execution.
Unknown budgets refuse with `UNKNOWN_GAS_PROFILE`, without a simulation fallback.
Expired non-cancel commands refuse; cancellation remains available after expiry.

Pass a `PlannedLaunch` before starting an unstarted plan. Raw `LaunchPlanV1`
remains available only for already-started recovery, cancellation and terminal
reads. No next-only call implicitly replans the whole sequence.

Launch commands use the preset's native transaction ceiling: 32M total gas on
Robinhood, including poster gas. Profiles classify atomic versus staged; they do
not size signing gas. Reviewed gas is validated against that ceiling, never used
as an override. A held price below the pinned fee floor refuses without a bump.
Approval commands omit gas so the wallet can prepare their envelopes.

| Mode | Transactions after approvals | What can be interrupted |
| --- | --- | --- |
| `atomic` | One launch transaction | The whole launch succeeds or reverts. |
| `staged` | Begin → packed preparation transactions → one activation | Preparation is split; activation still mints, locks, buys, and opens every market together. |

Staging is not rollback: an expired begun launch remains begun until cancelled.
Select the mode preference explicitly and review `launch.mode`. Multi-market
plans remain staged. A known single-market plan, including conversion funding,
is atomic-eligible when its padded compute budget plus poster allocation fits
the native ceiling. Explicit staged execution remains staged. Contiguous
preparation batches reserve poster gas and compute headroom; activation is
indivisible. An activation that exceeds its budget refuses with
`ACTIVATION_GAS_LIMIT`: reduce positions or pools before launching.
Unavailable gas budgets never trigger an execution backend.

Capacity-v5 adds **2,000,000 compute gas per Swap funding row** to the atomic
basis before at least 25% padding (or greater supplied headroom). Full conversion
calldata still contributes compute and poster allocation. This empirical reserve
assumes conversion work fits that allowance; it is not a universal arbitrary-router
bound or execution proof. Staged activation does not repeat conversion work and
keeps its existing budget.

Kyber conversions rely on their exact calldata amountOutMinimum and deadline.
Ordered opening-buy output floors use exact local math and `buy_slippage_bps`.
The SDK does not shrink the native ceiling to fit a payer's balance, bump a held
price, assign a wallet nonce or request a signature.

## Sign and confirm

Call `build_next_transaction` after every confirmed receipt, passing the immediate
reviewed transaction when one is held. Review and sign only the returned exact
`as_transaction()` envelope. The wallet owns its active account/chain, nonce and
balance checks. Track actual submitted nonces and replacements locally; an unknown
wallet-session nonce may be recovered once, not reread at every planning stage.
Planning readiness does not replace wallet authority. Unsigned future commands cannot be signed.

This loop **spends funds when called** with your authorized, exclusively used
`eth-account` local wallet and chain-bound client. Supply `persist`, your durable
owner-only journal callback, and a known `next_nonce` from wallet session state
when available. Resolve any previously signed hash first. Exceptions stop execution
without automatically resending.

```python
from dataclasses import asdict, replace
from black_market_sdk import (
    LifecycleReceiptReference, build_next_transaction,
    launch_plan_to_dict, read_launch_progress,
)
import time

def execute_launch(client, launch, wallet, persist, references=(), *, next_nonce=None):
    references = list(references)
    def save():
        persist({"plan": launch_plan_to_dict(launch.plan), "mode": launch.mode,
                 "confirmations": launch.confirmations, "nextNonce": next_nonce,
                 "receipts": [asdict(item) for item in references]})
    while True:
        command = build_next_transaction(
            client, launch, receipts=tuple(references),
        )
        if command is None:
            return read_launch_progress(
                client, launch.plan, mode=launch.mode,
                confirmations=launch.confirmations, receipts=tuple(references),
            )
        if (command.chain_id != launch.plan.chain_id
                or wallet.address.lower() != launch.account.lower()):
            raise RuntimeError("Wallet or chain changed")
        envelope = command.as_transaction()
        if next_nonce is None:  # One unknown-session recovery, at the signing boundary.
            next_nonce = client.eth.get_transaction_count(wallet.address, "pending")
        envelope["nonce"] = next_nonce
        signed = wallet.sign_transaction(envelope)
        tx_hash = Web3.to_hex(signed.hash)
        references.append(LifecycleReceiptReference(
            tx_hash, confirmations=launch.confirmations, nonce=next_nonce,
        ))
        next_nonce += 1
        save()  # Retain the computed hash BEFORE submission.
        client.eth.send_raw_transaction(signed.raw_transaction)
        receipt = client.eth.wait_for_transaction_receipt(tx_hash)
        references[-1] = replace(
            references[-1], observed_block_number=receipt["blockNumber"],
            observed_block_hash=Web3.to_hex(receipt["blockHash"]),
        )
        save()
        if receipt["status"] != 1:
            raise RuntimeError("Transaction reverted; inspect the retained receipt")
        while client.eth.block_number < receipt["blockNumber"] + launch.confirmations - 1:
            time.sleep(2)
        read_launch_progress(
            client, launch.plan, mode=launch.mode,
            confirmations=launch.confirmations, receipts=tuple(references),
        )
```

A normal staged sequence is begin receipt → prepare receipt(s) → activation
receipt → `LifecyclePhase.ACTIVE`. A `None` command can also mean Cancelled:
inspect the returned phase. Confirm markets with `read_launch_markets`, then check
permanent custody, retained supply/burn, and each ordered buy's spend/output.
Keep `replacement_hash` and the original observed block identity when reconciling
replacements/reorgs. Receipt inclusion and your chosen confirmation depth are separate.

## Publish metadata

Use `LaunchApiClient(LaunchApiConfig(base_url="https://api.abyss.trading"))`.
Construction sends nothing. The fixed runners stage metadata **before** execution
and publish only the confirmed activation transaction, not begin/preparation.

| Order | Public API / action |
| --- | --- |
| 1 | Build `LaunchSessionCreateMetadata` with name, symbol, and string description; optionally bind `LaunchSessionImageDescriptor` to exact PNG/JPEG/WebP bytes (≤5 MiB). |
| 2 | Call `build_launch_attribution_typed_data` with chain, wallet, metadata, image, idempotency key, nonce, deadline, and explicit `verifying_contract=plan.orchestrator`; request the wallet's EIP-712 signature. |
| 3 | Persist the exact signed request/key, then call `create_launch_upload_session(LaunchSessionCreateRequest(...), idempotency_key)`. Save returned `sessionId` and private `capability` immediately. |
| 4 | If an image was declared: `put_launch_image(session["upload"], image_bytes)` → `get_launch_upload_session(chain_id, session_id, capability)` → `complete_launch_upload(chain_id, session_id, capability)`. Require image readiness before launch. |
| 5 | Execute and confirm activation; verify canonical Active state and all markets. |
| 6 | Call `publish_launch_upload_session(chain_id, session_id, capability, LaunchSessionPublishRequest(chain_id, activation_hash))`. |
| 7 | Handle `LaunchPublishPending` (HTTP 202): save `session`, respect `retry_after_ms`, read the same session, and continue publication of the same hash. |
| 8 | Call `get_launch(chain_id, token)`; verify token, activation hash/block, metadata/image readiness, and indexed height. Check `canonicalStatus` for optimistic versus final indexing. |

Use `LaunchAttributionAuthorization(nonce, deadline, signature)` inside the create
request. The API assigns the image key; `LaunchSessionMetadata` is not the create
model. `renew_launch_upload_url` renews an expired image URL for the same session.
Never put a session capability or signed request into public logs.

`LaunchApiConfig.retries=3` means **up to three attempts** for all JSON methods,
including POST and metadata PUT, on transport failures, 429/502/503/504, and some
malformed successes. Repeated attempts retain the same body/key/capability.
Image PUT has **one attempt**. Set `retries=1` for one JSON attempt; **`retries=0`
sends nothing**. The fixed runner's bounded publication loop uses one-attempt clients;
it handles pending/indexing retries itself. After uncertain session creation,
reconcile the original signed request: idempotent replay may omit the capability.

Completion has four checks: **receipt confirmed → markets active → metadata
published → API indexed**. A publication or image error after activation leaves
the token live. Resume publication; do not deploy another token to fix an API error.

## Recover or cancel

For a fixed runner, preserve its original broadcast directory and invoke the same
case with `--resume "launch-results/<original-run>"`. Resume can sign remaining
transactions and publish; it is not a dry run. Never start another `--broadcast`
to continue a partially executed plan.

| Saved/on-chain state | Action |
| --- | --- |
| Unsigned, not begun | Rebuild/review if economics or quotes changed. A dry-run directory is not a broadcast recovery session. |
| Signed hash, receipt unknown/pending | Query that exact hash and account nonce; retain any replacement hash. Resolve inclusion before another signature. |
| Begun/prepared, deadlines valid | Resume the original plan, mode, receipts, salts, and minima. Preparation batches may be rebuilt from progress. |
| Plan expired or committed conversion expired before activation | Read cancellation eligibility and build `action="cancel"` for the original plan. A new route cannot replace committed funding. |
| Active, metadata/indexing incomplete | Recover the activation receipt and existing session; resume publication only. Active launches cannot be cancelled. |
| Reorged or replaced receipt | Reconcile original block/hash and replacement read-only; authorize only remaining canonical work. |
| Cancelled | Terminal. Inspect refunds and inactive inventory disposition before planning anything else. |

`build_next_transaction(client, original_plan, account=creator, mode="staged",
action="cancel", receipts=references)` produces the currently assessed cancellation
command. Cancellation still works after the profile stops being admitted.
Before activation, inventory goes to the committed `inventory_recipient` or burns
according to `burn_on_cancel`; it remains inactive. Escrow refunds remaining assets;
wrapped ETH can return as WETH. Spent gas is not refunded.

For ordinary saved core plans, `examples/launch_submit.py` reads progress and emits
unsigned next/cancel commands; `--receipts` accepts snake_case receipt-reference JSON.
`launch_recipe.py` encodes bare V4 prefixes. These CLIs do **not construct UFG/HMM/
auto-compound/Raeto tails**: submit has no `ufg` option, and recipe has no tail input.
Use typed SDK integration for these offerings; complete plans must retain their tails.

| Symptom | Cause | Next action |
| --- | --- | --- |
| HTTP 403/429 on chain ID, balance or a batched read | RPC throttling | Wait or change RPC, then resume the same run. A 429 is not a revert. |
| Begun launch past deadline | Interrupted staged run | Cancel the original launch; do not expect a fresh run to continue it. |
| `eth_getLogs` range error | Genesis scan exceeds the provider's 10M-block range | Start at deployment block 82,880,313 and query chunks ≤10M blocks. |
| `KYBER_UNAVAILABLE` / invalid WETH route | Swap requested for ETH→WETH | Use NativeWrap; reserve Kyber for other quotes. |
| `INVALID_KYBER_INTENT` | Conversion expiry exceeds 1200 seconds or reused plan deadline | Build unstarted funding with its separate 300-second clock. |
| `STALE_REVIEWED_GAS` / `REVIEWED_FEE_TOO_LOW` | Stale reviewed envelope | Refresh the review; never silently replace held gas or price. |
| `ECONOMIC_REVIEW_REQUIRED` hash mismatch | Hash belongs to another invocation | Review the same saved plan. |
| `PUBLISH_TIMEOUT`, `API_INDEX_TIMEOUT`, or image URL failure after activation | Publication/indexing failure | Token is live; resume publication, not deployment. |

## After launch

Locked launch liquidity **can never be withdrawn**. Fee collection, owner credits,
holder rewards, hook royalties, and UFG redemption are separate actions.

| Role | Read / action | Where value goes |
| --- | --- | --- |
| Anyone collecting fees | `preview_lifecycle_fees(client, hub, executor=account)`, then hub `claimAndSplit()` | Splits collected fees; caller earns the configured collection reward, if any. |
| Current fee owner | Hub `claimableOwnerFees(owner, asset)` → `claimOwnerFees(asset, owner)` from that owner | Withdraws reserved owner credits; harvesting alone does not withdraw them. |
| Staking participant | Rewards contract `pendingRewards(beneficiary)` → `claim()` / `claimFor(beneficiary)` | Pays the beneficiary's accrued staking rewards. |
| Dividend holder | Dividend contract `pendingRewards(beneficiary)` → `claim()` / `claimFor(beneficiary)` | Pays holder dividends; app UI supports reward claims. |
| Hook author | `read_developer_fees` → developer-claim builder | Pays the author's live payout; SDK integration, separate from creator ownership. |
| UFG holder | Treasury `redeem(amount, minOut, recipient)` | Burns tokens for current reserves less exit fee; see below. |

Read the hub/rewards addresses from `read_launch_progress` and market state.
Use `LAUNCH_FEE_HUB_V3_ABI`, `MULTI_ASSET_REWARDS_V1_ABI`, and
`LIFECYCLE_DIVIDEND_V1_ABI` with web3.py contracts for the ordinary claim methods.

### Stable authors, payouts, and fee claims

Use `read_lifecycle_author` / `read_author_hubs` with a trusted `registry`; helpers
authenticate hub membership through registry → core → fee factory.
`build_set_author_payout_transaction` updates live routing under current payout/admin control.
Use `build_claim_developer_fees_transaction` for one hub/asset, or
`build_claim_developer_fees_page_transaction` for 1–10 hubs and ≤8 sorted unique assets.
`assets=()` selects actual hub assets. Any caller can pay the live payout without a bounty.
Decode with `decode_developer_claim_receipt(receipt, transaction=transaction)` and retry
failed rows only; [claim statuses](reference.md#decode-author-claims) distinguish each outcome.

## Raeto launches

Raeto is a constrained **fee-sharing offering**, not a UFG treasury or redemption
contract. Select `KnownLifecycleProfile.V4_RAETO_FIXED_POOL` for a fixed maximum
hook fee, or `V4_RAETO_DYNAMIC_POOL` for the minimum/maximum/sensitivity schedule.
The hook creation bytes match the ordinary zero-royalty fixed/dynamic hooks, but
the collector, factory, locker, adapter and frozen terms belong to the Raeto preset.

```python
from black_market_sdk import (
    KnownLifecycleProfile, RaetoLaunchConfigV1, RAETO_QUOTE_ASSETS,
    RAETO_SHARE_BPS_OPTIONS, get_known_lifecycle_profile,
    encode_raeto_launch_tail, decode_raeto_launch_tail, validate_raeto_launch_plan,
)

preset = get_known_lifecycle_profile(
    chain_id=4663, orchestrator=addresses.orchestrator,
    key=KnownLifecycleProfile.V4_RAETO_FIXED_POOL,
)
assert preset is not None and preset.supported
raeto = RaetoLaunchConfigV1(share_bps=500)
assert decode_raeto_launch_tail(encode_raeto_launch_tail(raeto)) == raeto
```

Copy the Raeto preset's treasury, denominator, beneficiary and terms into
`LifecyclePoolBoundV4MarketConfigV6`, set `raeto_launch_config=raeto`, and use
that preset's outer profile/adapter with `config_version=6`. Each market must use
**WETH or USDG**, **`fee_mode=1` (QuoteOnly)**, **`lp_fee_pips=0`** and
**`developer_fee_bps=0`**. One V4 market per quote permits at most two Raeto
markets and 32 total positions. Normal token/reward choices and fee policies
remain available; there is no UFG proxy, treasury, special nonce or proxy approval.

The deployed menu is exactly **500, 1000, 2500, 5000 or 7500 bps** (5/10/25/50/75%).
The 5% floor cannot be disabled; **1234, 8000 and 10000 are not deployed options**,
even though newer contract source accepts a wider range. Encoding appends one
canonical 32-byte uint16 word to the config6 prefix. Missing, extra and mixed
HMM/UFG/auto-compound tails fail with `INVALID_RAETO_CONFIG`.

For an exclusively Raeto plan, call `validate_raeto_launch_plan(plan)` after
building every market; it rejects non-Raeto, mismatched or malformed markets with
`INVALID_RAETO_PLAN`. Independently, ordinary planning, combined preparation and
continuation enforce Raeto market fees, share, quote and profile binding before
RPC reads. The optional validator is not the security boundary. Use the normal
`prepare_and_plan_lifecycle_launch` / `build_next_transaction` flow.
A changed share changes the full plan hash, but not the hook's prefix-only
constructor commitment. Re-review any changed unstarted plan.

At collection, the collector books `floor(post-treasury quote fees * share_bps /
10000)` as Raeto credit and forwards the rest to the normal fee hub. The **current
fee owner** may call `setShareBps` with another menu value, including immediately
before collection; that selection applies to **all uncollected fees**.
Collection never consults or pays the payout registry. Anyone can call
`claimRaeto(quote_asset)` to pay the registry's **current** payout, not a chosen
recipient. Donations do not become fee revenue or Raeto credit.
`RAETO_FEE_COLLECTOR_V1_ABI` exposes the exact share/owed/token/quote getters,
share/claim methods and three Raeto events. See [Raeto reference](reference.md#raeto-profiles).

Both variants use capacity-v5 budgets and exact local opening-buy math.
Signing gas stays at the native 32M total ceiling; a ready assessment is a
budget/input check, **not execution proof**.

## Auto-compounding launches

Both profiles have reviewed preparation, atomic and activation budgets in
`robinhood-4663-capacity-v5`; they use the normal planning and continuation flow.

Select `KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL` for a fixed maximum
hook fee, or `V4_AUTO_COMPOUND_DYNAMIC_POOL` for the config6 minimum/maximum/
sensitivity schedule. Both support `fee_mode=0` (InputToken) and `fee_mode=1`
(QuoteOnly). Set **`lp_fee_pips=0` and `developer_fee_bps=0`**.

Build `LifecyclePoolBoundV4MarketConfigV6` as above, copying treasury, denominator,
beneficiary and terms from the **auto-compound preset's** envelope, and set its
`auto_compound_config=AutoCompoundConfigV1(compound_share_bps=5000)`. Use your
derived positions, opening price and registered oracle. Encode with
`encode_lifecycle_pool_bound_v4_market_config`, and set the outer market's adapter
and profile IDs from that same preset with `config_version=6`.

```python
from black_market_sdk import (
    AutoCompoundConfigV1, KnownLifecycleProfile, get_known_lifecycle_profile,
    encode_auto_compound_tail, decode_auto_compound_tail,
)

preset = get_known_lifecycle_profile(
    chain_id=4663, orchestrator=addresses.orchestrator,
    key=KnownLifecycleProfile.V4_AUTO_COMPOUND_STATIC_POOL,
)
assert preset is not None and preset.supported
compound = AutoCompoundConfigV1(compound_share_bps=5000)
assert decode_auto_compound_tail(encode_auto_compound_tail(compound)) == compound
```

The share is an integer in **0–10,000 bps** of net hook fees, not a trader tax.
The hook owns a locked full-range position; fees outside the compounding share
continue through the launch's ordinary token/quote fee-asset policies.
Its canonical 64-byte suffix is mandatory even for a zero share. Do not combine
it with HMM, UFG or Raeto tails. The retired October profiles ending `1d68` / `e0d7`
are not SDK offerings; rebuild unstarted plans with the current preset.

Use normal unified preparation to mine the hook salt. Unlike HMM/UFG, the exact
auto-compound tail is part of the salt-normalized constructor market commitment:
changing the share changes the hook prediction and requires mining again.
Capacity-v5 retains measured position-dependent work for every opening buy.
Multi-market plans remain staged; single-market conversions use the atomic
conversion reserve described above. Oversized indivisible activation still
refuses rather than executing a fallback or borrowing another hook's budget.
Every post-opening swap compounds in `afterSwap`. Let `eth_estimateGas` include
that work; never cap swap gas below its estimate. Underfunded compounding reverts
with `CompoundOutOfGas()` instead of silently skipping.

## UFG treasury launches

Creating a UFG treasury launch currently requires the SDK; the app displays them
but its creation flow is not shipped. Use the canonical launch proxy and
`ufg=config` on the planner, not an ordinary EOA/core launch.

| Rule | Required value |
| --- | --- |
| Token / rewards | ERC20; no staking/dividends |
| Creator / payer / refunds | Creator is `UFG_LAUNCH_PROXY_ADDRESS`; signer, payer, and refund recipient are the principal |
| Treasury / exit share | `treasury_share_bps=10000`; `exit_fee_bps` 0–2000 |
| Collection reward | `executor_fee_bps=0` |
| Reserves | 1–7 ascending unique nonzero addresses, exactly the set of market quotes; exclude the launch token |
| Abyss markets | Canonical QUOTE / QUOTE_ORACLE (`ABYSS_2` / `ABYSS_3`), config1 without a tail |
| V4 markets | UFG fixed/dynamic profiles only, config6 with identical `ufg_launch_config`, QuoteOnly (`fee_mode=1`), LP fee zero; HMM is excluded |

Set proxy identity **before token prediction or pricing**. For a token-only draft:

```python
from black_market_sdk import (
    UfgLaunchConfig, UFG_LAUNCH_PROXY_ADDRESS, ufg_launch_nonce,
    build_ufg_fee_asset_policies, validate_ufg_launch_plan,
)

def ufg_identity(draft, *, principal, nonce96):
    return replace(
        draft, creator=UFG_LAUNCH_PROXY_ADDRESS,
        nonce=ufg_launch_nonce(principal, nonce96), executor_fee_bps=0,
    )

async def prepare_ufg(client, plan, *, config, principal):
    token = predict_launch_token(client, plan)
    plan = replace(plan, fee_assets=build_ufg_fee_asset_policies(token, config.reserve_assets))
    validate_ufg_launch_plan(plan, config, token)
    return await prepare_and_plan_lifecycle_launch(
        client, plan, account=principal, mode="staged", ufg=config,
        buy_slippage_bps=50,
    )
```

Choose `nonce96` in `[0, 2**96)`; keep inventory/buy recipients external to the proxy.
Build `UfgLaunchConfig(10000, 100, sorted_reserve_tuple)` for a 1% exit fee.
`build_ufg_approvals` approves the **proxy**, not escrow. Native funding uses value.
`PlannedLaunch.ufg` carries the original config through `build_next_transaction`;
raw-plan recovery must pass that same `ufg=config`, including cancellation.
The generic wallet loop also needs to persist this config for UFG recovery.

Quote fees enter the treasury after protocol share; launch-token fees burn. The fee
owner is the funding adapter, not the principal. `read_ufg_treasury(client, token)`
returns a treasury address or `None`. Use `UFG_BLACK_MARKET_TREASURY_ABI` for
`collect()` and `redeem(uint256,uint256[],address)`; minima follow reserve order.
Holders redeem a pro-rata share of **current** reserves less exit fee, not a guaranteed
USD floor. `redeemSkipping` supports explicit reserve exclusions; inspect its ABI.

`examples/launch_ufg_treasury.py` exercises mixed Abyss+fixed atomic and Abyss+dynamic
staged launches on a disposable local Anvil fork, using its unlocked account and
without metadata. It is not a mainnet broadcast runner or native-Nitro gas proof.
See [fork setup](../examples/README.md#ufg-treasury-launches-fork-only) and
[proxy addresses/codecs](reference.md#resolve-deployment-addresses).

## HMM launch configuration

Select `V4_HMM_FIXED_POOL` or `V4_HMM_DYNAMIC_POOL` and copy that preset's envelope.
Both use zero LP/developer fees and require `hmm_launch_config` on config6.
HMM Dynamic responds to absolute oracle tick movement per elapsed second, so
movement in either direction can raise its fee; it differs from Dynamic V4 above.

`default_hmm_launch_config` requires two explicit controls: `max_bid_quote`
(positive raw quote principal cap, ≤uint128.max) and `reference_deviation`
(nonnegative int24 tick-spacing count around the truncated-oracle reference).
Zero reference deviation **does not disable** the reference guard.

```python
from black_market_sdk import (
    default_hmm_launch_config, encode_lifecycle_pool_bound_v4_market_config,
)

def encode_hmm_market(reviewed_v6, *, token_budget, quote_asset,
                      max_bid_quote, reference_deviation, premium_decay_seconds=0):
    hmm = default_hmm_launch_config(
        token_budget=token_budget, quote_asset=quote_asset,
        max_bid_quote=max_bid_quote, reference_deviation=reference_deviation,
    )
    hmm = replace(hmm, strategy=replace(
        hmm.strategy, premium_decay_seconds=premium_decay_seconds,
    ))
    configured = replace(reviewed_v6, hmm_launch_config=hmm)
    return encode_lifecycle_pool_bound_v4_market_config(configured)
```

Apply each `replace` **before encoding**; later changes cannot update the market bytes.
The V2 tail is **512 bytes**; unknown quotes need explicit deploy/harvest thresholds.

The default **50%** strategy share funds auxiliary liquidity from both currencies'
post-treasury fees; it does not burn tokens or unlock launch positions.
See [HMM defaults and owner controls](reference.md#configure-hmm-controls).
