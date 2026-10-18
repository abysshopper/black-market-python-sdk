# Changelog

## 0.10.0

- Breaking HMM V2-only cutover: retain the generic HMM model/helper names and `v4-hmm-fixed-pool` / `v4-hmm-dynamic-pool` keys, retarget them to the October 18 admitted V2 profiles, and replace versioned HMM hook enum values with V2. HMM V1 aliases, identities and 448-byte tails are not supported; this SDK change does not deregister on-chain profiles.
- Encode and canonically decode the exact sixteen-word (512-byte) `HMMLaunchConfigV2` tail. Enforce all factory guards, including positive uint128-bounded `max_bid_quote` and nonnegative int24 `reference_deviation`.
- Require both new risk controls explicitly in `default_hmm_launch_config`; preserve the other budget/quote defaults. Preserve V2 tails through config6 planning, salt mining and lossless plan/conversion-funding serialization, while keeping hook constructors the exact twenty-word `PoolBoundHookParametersV2` tuple.
- Replace both admitted envelopes, immutable dependency graphs, creation-code bytes and mined-receipt provenance. Export full V2 hook, manager, collector and factory ABIs plus generic V2-only strategy/launch tuple components.
- Document per-swap funding from both currencies after treasury, the oracle-reference placement envelope, and fee-owner controls for all fifteen strategy settings, placement pause and future strategy fee share. Update examples and behavior regressions; leave non-HMM V1 APIs unchanged.

## 0.9.1

- Add the canonical Abyss 0.01% fee tier (`fee_pips=100`, `tick_spacing=1`) to the public catalog used by launch recipes and lifecycle geometry validation.
- Preserve all existing fee tiers and example defaults. Cover both token orientations, unit-tick bands, permanent boundary tails, and existing-tier recipe geometry.

## 0.9.0

- Include the previously uncommitted Robinhood Dynamic Fee Hook preset, release evidence, documentation, examples and regression tests unchanged in their own commit.
- Add HMM Fixed (`v4-hmm-fixed-pool`) and HMM Dynamic (`v4-hmm-dynamic-pool`) construction presets from mined registration/admission receipts and hash-checked creation-code children.
- Export immutable `HmmStrategyConfig`/`HmmLaunchConfig`, `encode_hmm_launch_tail`, `decode_hmm_launch_tail`, `validate_hmm_launch_config` and `default_hmm_launch_config`.
- Require and automatically append the exact 448-byte Solidity HMM tail when encoding HMM config6 markets. Preserve it across decoding and salt mining; commit it in the launch plan, but exclude it from the factory's hook constructor market commitment.
- Calibrate strategy defaults to raw market token budget and WETH/USDG quote units; unknown quotes require explicit thresholds. Default to a 50% quote-fee strategy share and disabled premium decay.
- Add Solidity ABI parity fixtures, factory-boundary tests and the read-only `examples/hmm_launch_config.py` discovery/configuration example. Both HMM offerings require zero LP and developer fees and leave permanently locked launch positions untouched.

## Publishing

After merging, publish GitHub release `v0.10.0`. The `Publish to PyPI` workflow builds and verifies the distributions and publishes using the `pypi` environment's OIDC trusted publisher. A main-branch merge alone does not publish a package.
