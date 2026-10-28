# Changelog

## 0.15.0

- Remove EOA nonce reads from lifecycle planning, progress, simulation and next-command construction. Unsigned envelopes omit nonce; optional receipt-reference/status nonce records actual submitted transaction evidence. Launch/domain nonces and UFG namespace identity are unchanged.
- Reject an actual submitted/replacement nonce that conflicts with retained receipt evidence (`RECEIPT_NONCE`), using the existing transaction lookup without additional RPC calls.
- Cache successful chain identity once per underlying client session, evict failed observations, and remove final chain rereads and nonce-based global-mempool quietness gates.
- Batch `ArbGasInfo.getPricesInWei` at the same L2 pin. Its total L2 price supplies Robinhood's recommendation; a conservative EOA/Brotli data bound replaces per-transaction NodeInterface poster calls. Held reviewed gas prices remain exact.
- Add `compute_lifecycle_opening_buys` and ordered `LifecycleOpeningBuyQuote` results for release-owned unopened curves, including overlap, bitmap-word rounding, dynamic opening minimum fees, dust and uint256 overflow branches. These mathematical quotes are not live-pool or execution proof.
- Extend admission with explicit `profiled` confidence/protocol fit and `not-run` execution proof. `LifecycleGasProfileEvidence` carries versioned `LifecycleLaunchGasBounds` and opening quotes in `LaunchSimulation.gas_profile`. Add the pure `compute_lifecycle_launch_gas_bounds` helper; opaque conversion routes return no invented bound.
- Prove conversion-funded stages through activation in one validated native request, including zero-buy activation. Explicit simulation still executes covered templates; no incomplete prefix is admitted.
- Require an admitted `PlannedLaunch` before starting approvals/begin. Retire the Python-only raw unstarted build-next path without implicit whole-sequence replanning or new RPC guards. Raw already-started recovery, terminal reads and cancellation remain available.
- Remove UFG principal/config-hash mapping guard reads. Original submitted begin/atomic calldata still binds the treasury config during receipt reconciliation; Python progress callers supply `ufg=config` for that context.
- Migrate example economic review artifacts to schema v2 and `openingBuys`, distinguishing local exact-curve quotes from actual simulated receipts.
- Remove `submission_client`, `transport_preflight` and the dedicated submission-preflight error rather than replaying chain/gas-estimate guards after SDK admission. The wallet owns submission.
- Use the preset's fixed native ceiling for every launch command (32M total gas on Robinhood, including poster gas), with generic chains retaining their resolved ceiling. Remove profile-sized signing gas, affordable-envelope resizing and `LifecycleFinalEnvelope`/`fixed_final_envelope`; reviewed gas must match the native ceiling. Approvals leave gas preparation to the wallet.
- Profiles classify mode, not signing gas: atomic is a preference that can select staged, while explicitly staged remains staged. Verify and publish with the returned mode without mutating shared example templates. Proven-impossible activation refuses; uncertain activation requires exact full-sequence admission before begin.
- Refuse held prices below `max(ArbGasInfo.getPricesInWei()[5], block.baseFeePerGas)` without a bump, and refuse a fixed envelope above a real current caller ceiling. Retain explicit aggregate simulation policy without inventing a default cap.
- Preserve the pinned ArbOS50 minimum: raw versions below105 or unavailable observations refuse with the parallel `NITRO_LIMITS_UNAVAILABLE` error.

## 0.14.1

- Enable the deployed Robinhood (4663) planning lens in `LifecycleDeploymentPreset.launch_lens`, without caller configuration or API changes. The existing inspection lens is unchanged.
- Deployment: `0xcb4bb3caca71fe52eace8170cf83e901418646f8`, transaction `0xafdf35fa50d2c3fe08f1ab231a4e5d5e4dd5629c57f14dfe0a3aff5b0b8404ff`, block `100095353`, runtime codehash `0xc19e86bfed47afbb6ff719293edd8fb8bb3399d5d017b3f533d90cc72e58f1d3`.

## 0.14.0

- Pin each planning invocation once and batch same-block progress, prediction, Nitro limits, native balances, ERC20 balances and allowances through Multicall3 without a minimum batch size. Batch prepared-directory reads and dependent adapter reads in their respective stages.
- Reuse successful Multicall3 runtime, direct-EOA and native-Nitro capability checks for the same transport session. Mutable balances, progress, allowances and limits remain invocation-local.
- Size the immediate signing envelope from protected-buy calibration or a retained compute-only `simulation.steps[i].compute_gas_required` observation. If neither exists, run one unvalidated sizing pass. Bound both discovery and final proposals by pinned native balance and committed value: Nitro checks upfront fees in both validation modes. Return exactly the final validated gas/price. Reviewed envelopes remain unchanged; generic-EVM fork sizing uses gross `eth_estimateGas`, not refund-reduced receipt gas.
- Prove the staged prefix's full feasibility with conservative suffix budgets, but attach signing gas, fee estimates and admission only to the immediate transaction. Later `simulation.steps` have `provisional=True`; `build_next_transaction` uses retained compute and a fresh poster quote at each turn, never treating a suffix simulation ceiling or previous complete total as compute. Missing compute observations trigger sizing, not stale-envelope reuse.
- Bound raw discovery per call rather than dividing spendable gas equally across commands; unused reservations refund before subsequent calls. Only a native upfront-funds admission refusal permits one shared-budget sizing retry, followed by the one exact final validation. Contract reverts, OOG and provider failures do not trigger that retry; a repeated funds refusal never selects a fork. Do not infer native capacity or split preparations from a failure at a lower affordability ceiling.
- Document the low-margin limitation: a sequence the payer can actually fund may still be refused by the single shared-budget sizing retry. Report below-native-ceiling retry OOG as affordability, not native capacity. The previous release also refused these cases; no successful-prefix observations are synthesized from top-level RPC refusals.
- Build and simulate only the next canonical command after a receipt, clipping stored preparation batches to unfinished markets and reconstructing missing suffixes from waiting plans. Do not replan or reread profile metadata for the remaining launch.
- Add `LifecycleDeploymentPreset.hook_salt_miner` and `.launch_lens`, `HOOK_SALT_MINER_ABI`, and the new `LAUNCH_LENS_ABI`. Robinhood uses the deployed batched miner and verifies its salts locally. The new dependent planning lens stays unset pending owner deployment; the existing inspection lens remains unchanged.
- Use one authoritative L2 RPC header for the planning pin and issue the aggregate at its explicit block number. Nitro's EVM block-number/hash getters expose L1 values and are not used as L2 pins.
- Diagnostic migration: ordinary final proof no longer emits `simulation.replay`, and `simulation.envelope` rescue events are removed. `simulation.measure` can cover optional sizing and final validation; reviewed-only proof retains `simulation.replay`. Track completion with returned admission/outcomes rather than a fixed stage sequence. Build-next no longer emits nested `plan.context`, `plan.domain` or `plan.inputs` stages for whole-plan metadata/suffix work.

## 0.13.0

- Aggregate the reviewed Robinhood Nitro getters, immutable factory prediction and head progress with pinned, runtime-attested Multicall3. Keep caller-sensitive poster estimates on their original direct path.
- Finalize each lifecycle invocation with one fresh source-chain check and one canonical check per distinct proof/receipt block, after any submission preflight. Retain confirmation-bound and pending nonces, receipt identity, reorg detection and exact replay.
- Share one fee recommendation and immutable known-factory prediction inside preparation, including diagnostic/final protection passes. No mutable state is cached across invocations or block pins.
- Add `LifecycleFinalEnvelope(gas, gas_price=None)` and `fixed_final_envelope` to atomic preparation. Omitted price uses the operation's single recommendation; supplied price is exact. The final protected command still receives validated exact-envelope replay and never auto-enlarges.
- Use admitted `PlannedLaunch.transactions[0]` as the review envelope and one reviewed `build_next_transaction` as final SDK admission. Signing integrations must still refresh the active provider account/chain, pending nonce and pending balance immediately before signing.
- Rewrite launch guidance around pricing, one-market deployment, publication, and recovery; move limits/codecs/gas details into `docs/reference.md` and correct funding, deadlines, and API retry instructions.

## 0.12.0

- Add opt-in UFG treasury launches through the canonical principal-funded proxy, without changing the existing core launch path.
- Export the immutable UFG launch config, canonical dynamic-tuple codec/hash, namespaced nonce, exact fee-policy builder and offline proxy-rule validator.
- Add fixed/dynamic UFG profile presets and hash-checked constructor code from committed registry readback at block 94,551,140; include the reproducible evidence importer.
- Extend only UFG config6 markets with a typed treasury tail, preserved through salt mining and portable plans while excluded from the hook constructor market commitment.
- Add unsigned proxy launch/cancellation and aggregated ERC20 proxy-approval builders. Extend preparation/planning/simulation/build-next with `ufg=config`, principal balances/nonces/receipts, and inherited treasury configuration.
- Export full proxy, treasury factory and upstream factory/treasury ABIs plus pinned treasury lookup. Document funding, lifecycle rules and the Anvil/native-Nitro proof boundary.
- Add independent `cast abi-encode` fixtures, acceptance/rejection coverage and a fork-only public SDK example covering native Abyss plus fixed V4 atomically and native Abyss plus dynamic V4 in stages.

## 0.11.0

- Size atomic/activation envelopes with the shipped HMM V2 forwarding reserve, once per transaction; clamp optional reserve/headroom targets to legal compute and total gas caps, then prove the exact envelope.
- Greedily pack contiguous preparation markets from conservative profile costs (14.5M HMM V2, 16M otherwise); split only underestimated packed preparations that fail measurement near the gas ceiling.
- Default Robinhood's aggregate simulation budget to 50M. Prove a fitting prefix and expose `simulation.deferred_transaction_ids`; deferred commands have no signing gas, price or admission until fresh build-next proof. Buy calibration refuses when activation outputs are deferred.
- Attribute no-provable-prefix refusals to the per-transaction compute ceiling before the aggregate RPC budget when the immediate estimate exceeds both; show one-decimal millions and suggest staged execution for atomic launches. Conservative estimates do not introduce a new rejection for otherwise provable envelopes.
- Remove `prepare_batch_size` from every planner and `PlannedLaunch`; migrate examples to exact transaction spans. Pure offline `build_launch_transactions(..., preparation_batches=...)` remains supported.
- Collapse nested chain/header guards into owner-level checks and replay only the immediate reviewed gas/price envelope, without remeasurement, repricing or suffix replay. Retain native Nitro capability, identity, receipts, nonce, postcondition and submission checks.
- Retry throttled or transiently failed RPC requests (HTTP 408/429/5xx, JSON-RPC 429/-32005, connection timeouts) up to three times with 150/300/600 ms backoff, matching viem's default transport, so a rate-limited public RPC no longer surfaces as a failed execution proof.
- Update launch documentation and focused regression coverage for sizing, packing, prefix proof and immediate-envelope execution.

## 0.10.0

- Breaking HMM V2-only cutover: retain the generic HMM model/helper names and `v4-hmm-fixed-pool` / `v4-hmm-dynamic-pool` keys, retarget them to the October 18 admitted V2 profiles, and replace versioned HMM hook enum values with V2. HMM V1 aliases, identities and 448-byte tails are not supported; this SDK change does not deregister on-chain profiles.
- Encode and canonically decode the exact sixteen-word (512-byte) `HMMLaunchConfigV2` tail. Enforce all factory guards, including positive uint128-bounded `max_bid_quote` and nonnegative int24 `reference_deviation`.
- Require both new risk controls explicitly in `default_hmm_launch_config`; preserve the other budget/quote defaults. Preserve V2 tails through config6 planning, salt mining and lossless plan/conversion-funding serialization, while keeping hook constructors the exact twenty-word `PoolBoundHookParametersV2` tuple.
- Replace both admitted envelopes, immutable dependency graphs, creation-code bytes and mined-receipt records. Export full V2 hook, manager, collector and factory ABIs plus generic V2-only strategy/launch tuple components.
- Document per-swap funding from both currencies after treasury, the oracle-reference placement envelope, and fee-owner controls for all fifteen strategy settings, placement pause and future strategy fee share. Update examples and behavior regressions; leave non-HMM V1 APIs unchanged.

## 0.9.1

- Add the canonical Abyss 0.01% fee tier (`fee_pips=100`, `tick_spacing=1`) to the public catalog used by launch recipes and lifecycle geometry validation.
- Preserve all existing fee tiers and example defaults. Cover both token orientations, unit-tick bands, permanent boundary tails, and existing-tier recipe geometry.

## 0.9.0

- Add the Robinhood Dynamic Fee Hook preset for launches with explicit minimum/maximum fees and tick-velocity sensitivity, alongside the fixed-fee offering.
- Add HMM Fixed (`v4-hmm-fixed-pool`) and HMM Dynamic (`v4-hmm-dynamic-pool`) construction presets from mined registration/admission receipts and hash-checked creation-code children.
- Export immutable `HmmStrategyConfig`/`HmmLaunchConfig`, `encode_hmm_launch_tail`, `decode_hmm_launch_tail`, `validate_hmm_launch_config` and `default_hmm_launch_config`.
- Require and automatically append the exact 448-byte Solidity HMM tail when encoding HMM config6 markets. Preserve it across decoding and salt mining; commit it in the launch plan, but exclude it from the factory's hook constructor market commitment.
- Calibrate strategy defaults to raw market token budget and WETH/USDG quote units; unknown quotes require explicit thresholds. Default to a 50% quote-fee strategy share and disabled premium decay.
- Add Solidity ABI parity fixtures, factory-boundary tests and the read-only `examples/hmm_launch_config.py` discovery/configuration example. Both HMM offerings require zero LP and developer fees and leave permanently locked launch positions untouched.

## 0.8.0

- Validate unstarted-launch position topology, enable permanent boundary tails by default, and replace `staircase_boundaries` with `position_gaps`.
- Default funded examples to unsigned dry runs. `--broadcast` explicitly authorizes a fresh launch without a hash prompt; `--resume` retains the original plan and metadata session.
- Replay the exact reviewed transaction envelope without raising its held gas limit or gas price; retain funding, nonce, receipt, reorg, and deadline checks.

## Publishing

After merging, publish GitHub release `v0.15.0`. The `Publish to PyPI` workflow builds and verifies the distributions and publishes using the `pypi` environment's OIDC trusted publisher. A main-branch merge alone does not publish a package.
