![Abyss](https://raw.githubusercontent.com/abysshopper/black-market-python-sdk/main/assets/splash.png)

# Black Market Python SDK

Discover launch offerings, prepare explicit token-launch plans, and work with
Abyss DEX and lending contracts. Built on [web3.py](https://web3py.readthedocs.io)
for Python 3.10+. Imports never sign, send transactions, or publish metadata.

## Install

```sh
python -m pip install black-market-sdk
```

For the examples and tests in this checkout:

```sh
python -m pip install -e ".[dev]"
```

## Read-only quickstart

This is a complete mainnet example: no wallet, private key, or plan file needed.
It reads the live registry, including admission status and configuration topology.

```python
from web3 import Web3
from black_market_sdk import (
    ROBINHOOD_MAINNET_RPC, get_addresses, get_launch_addresses,
    read_lifecycle_profiles,
)

chain_id = 4663
client = Web3(Web3.HTTPProvider(ROBINHOOD_MAINNET_RPC))
assert client.eth.chain_id == chain_id
launch = get_launch_addresses(chain_id)
print("orchestrator:", launch.orchestrator)
print("lending pool:", get_addresses(chain_id).lending_pool)

for profile in read_lifecycle_profiles(
    client, orchestrator=launch.orchestrator, offset=0, limit=100
):
    print(profile.id, profile.venue_kind, profile.admitted, profile.reason)
```

Or run `python examples/quickstart.py` / `python examples/launch_matrix.py`.
Both default to read-only Robinhood mainnet discovery.

## Launch workflow

1. Discover a live admitted profile and inspect its bounds and developer terms.
2. Build a complete `LaunchPlanV1`: explicitly choose chain, orchestrator, creator,
   nonce, deadline, token, funding, fees, markets, and opening buys.
3. Finalize pool-bound hook salts with `prepare_pool_bound_lifecycle_plan` and
   review the resulting `hash_launch_plan` before any submission.
4. Use `plan_launch` with explicit execution mode and observed gas/calldata caps.
   `build_next_transaction` emits only a proven **unsigned** next step.
5. Your wallet submits each chosen transaction. Recover from actual receipts with
   `read_launch_progress`; revalidate before another step.

The deployed mainnet launcher offers pool-bound V4/config5 and canonical
Abyss/config1. Staged preparation still ends in one indivisible activation.
The SDK does not silently change fees, funding, execution mode, or plan identity.

See the [launch guide](https://github.com/abysshopper/black-market-python-sdk/blob/main/docs/launch-guide.md)
for configuration layouts, runnable examples, simulation requirements, recovery, author fees, and metadata API usage.

## Networks and configuration

| Chain | Configuration |
| --- | --- |
| `4663` Robinhood mainnet | Current launch infrastructure, canonical Abyss DEX, and lending addresses |
| `31337` local Anvil | Explicit local deployment and environment overrides; no launch defaults |
| `46631` workbench | DEX/lending configuration and overrides; explicit launch deployment |

Select `get_addresses(4663)` explicitly: `get_addresses()` defaults to workbench.
`get_launch_addresses(chain_id)` requires a chain and raises for unconfigured
launch chains. Plans and EIP-712 builders always require their own orchestrator.

Current mainnet launch addresses:

- Orchestrator: `0xb75CBD17b9aecb7305B4DFcDa69595F783341c0E`
- Registry: `0xaa8a410709B79cBA6F118F1be1FF568877A3B8Ee`
- Fee-owner registry: `0x15778Aad08e12D458B2848F035860e2a8c2a0725`

DEX fee tiers, pool/router/position ABIs, TickMath, lending clients, reserve/user
normalization, and exact RAY/WAD helpers are also exported at the package root.
Override precedence and the full deployment view are documented in the guide.

## Eight standalone token-launch examples

**Running any command below deliberately spends funds, creates permanent tokens
and markets on chain4663, and writes to your configured launch API.** Each command
launches its fixed ERC20/ERC404, reward, burn, venue and position case. There are
no flags, fixture files, default wallet, API-skip mode, or automatic relaunches.

Install once and copy the configuration template to the working directory:

```sh
python -m pip install black-market-sdk
cp examples/.env.example .env
chmod 600 .env
```

Set `PRIVATE_KEY` and `LAUNCH_API_URL` in `.env` (or your process environment).
Use your funded wallet and actual launch API/indexer; the API URL has no default.
`RPC_URL` is optional and defaults to the SDK official Robinhood mainnet RPC.
Optional `SIMULATION_RPC_URL` selects a separate exclusively owned loopback Anvil;
otherwise the SDK uses the RPC's real native simulation backend. Existing process
environment wins; working-directory `.env` is loaded once without shell expansion.
Optional `NFT_BASE_URI` is your hosted ERC404 NFT base URI; it defaults to empty,
without claiming hosted NFT metadata exists.

Run exactly one command for the case you want:

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

To use these outside the checkout, copy the selected example,
`examples/launch_examples.py`, and `examples/_launch_support.py` together beside
each other (plus `.env.example` for setup). They use the installed public
`black_market_sdk`; no monorepo manifest or fixture catalogue is needed.

The SDK admits the full unchanged economics before API staging; the wallet signs
exact SDK envelopes locally. Every run creates fresh
`launch-results/<timestamp-case-random>/` diagnostics. Computed signed hashes are
durably recorded **before broadcast**; raw signed transactions and session recovery
are mode-0600 private files. Failures retain logs/hashes and never retry broadcast,
relaunch, or roll back execution. Inspect the original hash/session before another run.
Default 16M gas, 131072-byte calldata and 1000-bps headroom are **EXAMPLE ceilings**,
not verified provider limits; gas is bounded by the observed block. See the
[setup, economics and recovery guide](https://github.com/abysshopper/black-market-python-sdk/blob/main/docs/launch-guide.md#standalone-token-launch-examples)
for optional environment ceilings and exact evidence.

## Tests and real HTTP API integration

```sh
# Local unit suite: no RPC, API service, wallet, or key required.
python -m pytest -q

# Opt-in GET-only API checks against your running real test service.
export LAUNCH_API_TEST_URL=http://127.0.0.1:18763
export LAUNCH_API_TEST_CHAIN_ID=4663
export LAUNCH_API_TEST_ORCHESTRATOR=0xb75CBD17b9aecb7305B4DFcDa69595F783341c0E
python -m pytest -q tests/test_launch_api_http.py -m 'launch_api_http and not launch_api_write'

# Explicit signed metadata-session suite; supply a disposable test wallet key.
: "${LAUNCH_API_TEST_PRIVATE_KEY:?Set the disposable test wallet private key}"
python -m pytest -q tests/test_launch_api_http.py -m launch_api_http

# Real API example: read-only by default; no injected or simulated transport.
python examples/launch_api_lifecycle.py
# Opt-in signed creation; protects the one-time capability in a mode-0600 file.
python examples/launch_api_lifecycle.py --signed --state-file launch-session.private.json
```

The test API must run the actual launch router and session database, with chain
`4663` and the current orchestrator as its authorization verifying contract.
API clients require HTTPS by default; the test suite/example explicitly enable
`allow_loopback_http=True` for local HTTP only. Image uploads still require HTTPS.
Signed tests/examples refuse non-loopback endpoints. The metadata-only suite
needs no image store, indexed launch, funded wallet, or chain writes. Explicitly
selected suites fail on missing service/key prerequisites; default pytest excludes HTTP.
See the guide for service requirements and what these tests cover.

## License

MIT
