![Abyss](https://raw.githubusercontent.com/abysshopper/black-market-python-sdk/main/assets/abyss-social.webp)

# Abyss Python SDK

Token launches, swaps, and lending with [web3.py](https://web3py.readthedocs.io).

## Install

```sh
python -m pip install black-market-sdk
```

## 0.7.0 launch graph and breaking interfaces

The packaged October 8, 2026 construction graph targets Robinhood chain `4663`:

| Contract | Address |
| --- | --- |
| Launch core / orchestrator | `0x91560876033d568d25CDe98C78c33ff8FC43962c` |
| Implementation registry | `0xB2B0f9F36617810D67b8fC175153Aa10024C1358` |

`get_known_lifecycle_deployment`, `get_known_lifecycle_profile`, and
`list_known_lifecycle_profiles` expose five frozen construction profiles:
`abyss-0`, `abyss-1`, `abyss-2`, `abyss-3`, and `v4-fixed-fee-pool`.
The pool-bound V4 profile uses market config **6** and hook constructor parameters
**V2**, including minimum/maximum hook fees and fee sensitivity. Config 5/V1
remains an explicit wire format, not an interchangeable constructor. Always pass
the intended `config_version` to constructor encoders; do not mix the two.
Frozen metadata, creation code, code hashes, and reviewed `LaunchEnvelopeV2`
graphs describe construction only. Use `read_lifecycle_profiles` and fresh
planning to establish current eligibility; a preset is not admission.

The 0.7 interface separates four operations:

1. **Construct:** derive recipes/ranges and build the complete `LaunchPlanV1`.
   `predict_launch_token` can predict a token-only draft; prediction is not
   economic validation. Portable `serialize_launch_plan` / `parse_launch_plan`
   retain Solidity camelCase keys and exact decimal-string wide quantities.
2. **Compile:** `build_launch_transactions(plan, mode="atomic" | "staged")`
   returns unsigned commands without RPC, approvals, gas estimates, or admission.
   Staged `preparation_batches`, when supplied, must cover all markets exactly.
   `build_lifecycle_calldata` encodes an explicit lifecycle command.
3. **Prepare and prove:** await `prepare_and_plan_lifecycle_launch(client, plan,
   account=..., mode=...)` for unified hook-salt mining and current-state proof.
   Optional `buy_slippage_bps` derives ordered-buy minima from actual diagnostic
   receipt outputs, not a static price estimate. Omission preserves committed
   minima; protection cannot rewrite an already-started launch.
   `plan_launch` proves an already-finalized plan. Both require an explicit mode:
   there is no legacy separate preparation API or silent atomic-to-staged fallback.
4. **Recover and submit:** `read_launch_progress` tracks canonical/head state,
   confirmations, submitted receipts, replacements, and reorgs.
   `simulate_launch_plan` and `build_next_transaction` freshly prove remaining
   work, inheriting receipt evidence unless explicitly replaced.
   Review, sign, and submit only the immediate next returned transaction with
   your own wallet; persist its receipt reference and repeat.

Public semantic models use immutable dataclasses and snake_case attributes.
`decode_lifecycle_launch_receipt` now returns `LaunchReceiptV1`, not a dictionary:
read `receipt.token_out` / `receipt.quote_spent` rather than camelCase subscripts.
`LifecyclePositionIdentity` and `LaunchExecutionContextV1` model the exact wire
identities/context; neither is a recipe range or an RPC limit context.
`read_lifecycle_token_at_block(client, plan, block)` reads at a supplied block,
while `predict_launch_token` also rechecks canonical block and chain identity.

Lifecycle limits are optional **tightening policy**, not fabricated chain
capacity. On chain `4663`, admission uses native Nitro compute ceilings and
poster-gas accounting, exact sequential execution, postconditions, and
affordability. Final activation is indivisible. Without a usable stateful
simulator, results are provisional/unavailable, never a substitute execution
proof. A controlled fork must be explicitly disposable and distinct from the
source RPC; only that opt-in simulation path can send snapshot-isolated fork
transactions.

`execution_proof`, `protocol_fit`, and `transport_preflight` are separate
outcomes. An admitted plan is not a wallet-submission guarantee.
`build_next_transaction(..., reviewed_transaction=..., submission_client=...)`
rechecks the exact reviewed gas/gas-price envelope against fresh state and,
optionally, a read-only estimate on the active submission RPC. It does not sign
or broadcast to that RPC.

### Author fees and optional metadata API

Stable author identity is separate from its live payout controller.
`read_lifecycle_author`, `read_author_hubs`, and `read_developer_fees` expose
authenticated hubs, frozen `SourceTermsV3`, supported assets, pending balances,
and reserved credits. `build_set_author_payout_transaction`,
`build_claim_developer_fees_transaction`, and
`build_claim_developer_fees_page_transaction` build unsigned transactions.
Preserve each claim transaction's `claim` context for
`decode_developer_claim_receipt`: cursor completion does not mean every payment
succeeded, and retryable failed rows are reported separately.

`LaunchApiClient` is opt-in and independent of on-chain planning. Callers supply
signed attribution and idempotency keys, protect private upload capabilities,
upload image bytes using the API's exact presigned headers, and publish their
transaction hash explicitly. Transient API requests use bounded retries with
the same payload/key/capability; image PUTs are not retried automatically.
A publish HTTP `202` raises `LaunchPublishPending` with the returned session and
retry delay, not a final publication guarantee. Resume session/indexer recovery
without resending the launch transaction. See the API guide below.

## Examples

Clone this repository to run the examples. Requires Python 3.10+.

```sh
python -m pip install -e .
python examples/quickstart.py
```

The quickstart reads available launch profiles without a wallet.

For token launches, set `LAUNCH_API_URL` and exactly one local signer in your
working folder's `.env`: `PRIVATE_KEY`, or `EXAMPLE_KEYSTORE_PATH` to an owner-only
V3 JSON keystore (`EXAMPLE_KEYSTORE_PASSWORD` defaults to the empty string).
An optional `RPC_URL` overrides the default endpoint. Run one case:

```sh
python examples/launch_erc20_v4.py
python examples/launch_erc20_abyss.py
python examples/launch_erc404_v4.py
python examples/launch_erc404_abyss.py
python examples/launch_erc20_staking_v4.py
python examples/launch_erc20_dividends_abyss.py
python examples/launch_erc20_burn_mixed.py
python examples/launch_erc404_dividends_mixed.py
python examples/launch_erc20_multi_quote.py
python examples/launch_erc20_depth_v4.py
python examples/launch_erc20_dividends_multiquote_mixed.py
```

| File | Case |
| --- | --- |
| `launch_erc20_v4.py` | ERC20/no rewards, one V4 position and buy, atomic |
| `launch_erc20_abyss.py` | ERC20/no rewards, one Abyss position and buy, atomic |
| `launch_erc404_v4.py` | ERC404/no rewards, one V4 position and buy, staged |
| `launch_erc404_abyss.py` | ERC404/no rewards, one Abyss position and buy, atomic |
| `launch_erc20_staking_v4.py` | ERC20/staking, two V4 positions and ordered buys, staged |
| `launch_erc20_dividends_abyss.py` | ERC20/dividends, three Abyss positions and one buy, staged |
| `launch_erc20_burn_mixed.py` | ERC20/3000-bps token burn, two V4 + one Abyss positions, staged |
| `launch_erc404_dividends_mixed.py` | ERC404/dividends, two V4 + three Abyss positions, staged |
| `launch_erc20_multi_quote.py` | ERC20/no rewards, V4 WETH + V4 USDG + Abyss WETH, three buys, staged |
| `launch_erc20_depth_v4.py` | ERC20/no rewards, four adjacent Early Scarcity V4 bands plus final tail, staged |
| `launch_erc20_dividends_multiquote_mixed.py` | ERC20/4000-bps dividends, V4 WETH/USDG (two positions each) + Abyss WETH (three), three buys, staged |

The three new cases are additive validation candidates; the original eight remain.
Each buy pays **0.00001 ETH** by default (`EXAMPLE_BUY_AMOUNT_WEI=10000000000000`),
converted inside launch escrow via a reviewed Kyber build from your Abyss API.
`EXAMPLE_KYBER_API_URL` optionally selects a separate quote API; it defaults to
`LAUNCH_API_URL`. Missing routes or conversion admission stop unsigned, with no
native-wrap/direct-ERC20 fallback. Creators need native ETH for buys **and gas**,
not pre-funded WETH/USDG. WETH uses the existing API USD anchor; USDG uses its
SDK-catalogued $1 peg and six-decimal recipe.

Optional `EXAMPLE_TOKEN_NAME`, `EXAMPLE_TOKEN_SYMBOL`, and
`EXAMPLE_TOKEN_DESCRIPTION` override case identity. `EXAMPLE_IMAGE_PATH` selects
a PNG/JPEG/WebP file (at most 5 MiB): its exact digest/type/length are reviewed,
signed, uploaded, and API-verified before chain submission. Final success additionally
requires the public token DTO's image reference and one bounded HTTP 200 image GET;
served digest/type/length and any inferred API transformation are retained.
Unset means metadata-only.

### Read-only original-launch recovery

`examples/launch_submit.py` never loads a signer, sends source-chain transactions,
or publishes. Pass the exact private plan and original mode/confirmation policy:

```sh
python examples/launch_submit.py "$PLAN_JSON" --rpc-url "$RPC_URL" \
  --account "$CREATOR" --mode staged --confirmations 1 --receipts "$RECEIPTS_JSON"
```

`--receipts` accepts an array of typed SDK reference objects with
`transaction_hash`, optional `replacement_hash`, `confirmations`, and original
`observed_block_number` / `observed_block_hash`. Numbers may be exact decimal
strings; hashes remain `0x`-prefixed bytes32. A plan wrapper's `receipts` is also
loaded, and output includes reusable typed `receipts` alongside canonical progress.
Repeated `--transaction-hash` remains a convenience alias, but bare hashes cannot
retain original inclusion blocks or replacement evidence and cannot detect an
orphaned original inclusion after the same hash is re-mined. A detected reorg
halts the real runner before another signature or publication; reconcile retained
evidence explicitly with this read-only command, never automatically re-launch.
Do not use `--finalize-bound` once receipt evidence exists.

Launch examples spend real funds and publish to your configured API.
Plans, transaction hashes, receipts, and errors are saved in `launch-results/`.
Keep `.env` and private recovery files out of Git.

[Example source](https://github.com/abysshopper/black-market-python-sdk/tree/main/examples)
· [API guide](https://github.com/abysshopper/black-market-python-sdk/blob/main/docs/launch-guide.md)
· [MIT license](https://github.com/abysshopper/black-market-python-sdk/blob/main/LICENSE)

[Agent guide](https://github.com/abysshopper/black-market-python-sdk/blob/main/AGENTS.md)
· [Skills](https://github.com/abysshopper/black-market-python-sdk/tree/main/.agents/skills)
