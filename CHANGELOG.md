# Changelog

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

After merging, publish GitHub release `v0.9.1`. The `Publish to PyPI` workflow builds and verifies the distributions and publishes using the `pypi` environment's OIDC trusted publisher. A main-branch merge alone does not publish a package.
