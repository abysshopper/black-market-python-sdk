---
name: token-launch
description: Run or adapt the Abyss Python token-launch examples, including ERC20/ERC404, rewards, multiple markets, metadata publication, and failure recovery. Use before signing or broadcasting.
---

# Launch a token and publish its metadata

## Before execution

- Require explicit user authorization for real chain transactions and API writes.
- Read `docs/launch-guide.md`'s standalone examples section; choose one of the eight
  `launch_erc20_*` or `launch_erc404_*` files. Do not add flags or fixture requirements.
- Set `PRIVATE_KEY` and `LAUNCH_API_URL` through the user's environment or `.env`.
  Never overwrite an existing `.env`, print its values, or supply a development key.
- The wallet must be funded and match the selected deployment. `RPC_URL` is optional.
  The SDK needs complete simulation admission; inspect the guide if the provider
  cannot supply it. Never bypass a refusal or silently alter the case.

## Run or adapt

Basic example: `python examples/launch_erc20_v4.py`.
Complex NFT/dividend example: `python examples/launch_erc404_dividends_mixed.py`.

Each file supplies a fixed case to `launch_examples.py`. Reuse that shared runner
and `_launch_support.py` for local signing and durable artifacts. It uses actual
SDK plans, admitted envelopes, metadata sessions, activation receipts, and published
indexed DTOs. Preserve exact amounts, fees, ordered buys and markets.
API integration is required; a pending-indexing response is not success.

## Failures

Inspect the newest `launch-results/` run's `result.json`, `events.jsonl` and
`receipts.json`. A broadcast timeout may occur after acceptance: look up the saved
signed hash before further action. Do not launch a new token to recover API failure.
Raw signed bytes, signatures and capabilities stay in private recovery files.
Never retry broadcast, relaunch automatically, or claim an unobserved success.
