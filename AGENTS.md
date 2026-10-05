# Working with the Abyss Python SDK

Read `README.md` first. This repository contains the public `black-market-sdk`
package, runnable examples, and its tests.

## Skills

- `.agents/skills/sdk-usage/SKILL.md`: read protocol state and integrate public APIs.
- `.agents/skills/token-launch/SKILL.md`: run or adapt token-launch/API examples.

Read the relevant skill before working on those paths.

## Code and configuration

- Use Python 3.10+ in a virtual environment; install with `python -m pip install -e '.[dev]'`.
- Consumer code imports `black_market_sdk`; source lives in `black_market_sdk/`.
- Use SDK address/ABI/client helpers; do not copy deployment addresses into apps.
- Select the intended chain explicitly. Use integer token units, never floats.
- Keep launch cases independently runnable with one command and no arguments.
- Load secrets from the environment or an existing `.env`; never overwrite it.
- Keep package and `black_market_sdk.__version__` versions consistent.

## Wallet safety

- Reading/planning is not permission to spend funds or publish metadata.
- Run launch examples only when the user explicitly authorizes chain and API writes.
- Never add a default key, bypass admission, change a refused case, or silently skip the API.
- Keep signed hashes before broadcast and retain failure logs; never automatically relaunch.
- Do not commit `.env`, `launch-results/`, raw signed transactions, or recovery capabilities.

## Checks

- `python -m pytest -q`: offline unit suite; HTTP tests are excluded by default.
- `python examples/quickstart.py`: read-only live smoke.
- Explicit HTTP tests: `python -m pytest -q tests/test_launch_api_http.py -m launch_api_http`.
  They need the actual disposable API/database and an explicitly supplied test key.
- Release artifacts: `python -m build` then `python tools/ci/verify_dist.py`.

Update affected examples and docs with API changes. Keep the README short and
consumer-facing; put execution details in `docs/launch-guide.md`.
