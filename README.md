![Abyss](https://raw.githubusercontent.com/abysshopper/black-market-python-sdk/main/assets/abyss-ziggurat-v3-pale-text-1500x500.png)

# Abyss Python SDK

Python SDK for token launches, swaps, and lending with [web3.py](https://web3py.readthedocs.io).
Import the public package as `black_market_sdk`. Requires Python 3.10+.

## Install

Install the package with `python -m pip install black-market-sdk`, or use a checkout
to run and customize the examples:

```sh
git clone https://github.com/abysshopper/black-market-python-sdk.git
cd black-market-python-sdk
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Version **0.12.0** adds opt-in [UFG treasury launches](docs/launch-guide.md#ufg-treasury-launches):
canonical native Abyss QUOTE pools and fixed/dynamic UFG V4 profiles can share
one treasury. Use `UfgLaunchConfig`, proxy-specific transaction/approval builders,
and the existing planner's `ufg=config` option. Ordinary core launches are unchanged.

Version **0.11.0** adds HMM-aware gas sizing, automatic preparation packing, and
bounded simulation-prefix admission with explicit deferred steps. See
[simulation and recovery](docs/launch-guide.md#simulation-unsigned-commands-and-recovery).

Version **0.10.0** introduced a breaking HMM V2-only cutover: generic HMM helpers and
profile keys now target the newly admitted V2 graph and 512-byte launch tails.
`default_hmm_launch_config` requires explicit `max_bid_quote` and
`reference_deviation`; other defaults are unchanged. HMM V1 identities, hook
enum values and 448-byte tails are no longer supported. See the
[HMM configuration and owner-controls guide](docs/launch-guide.md#hmm-launch-configuration).

## UFG treasury launches

Set the plan's creator to `UFG_LAUNCH_PROXY_ADDRESS`, use
`ufg_launch_nonce(principal, nonce)` and
`build_ufg_fee_asset_policies(predicted_token, reserves)`. Select
`KnownLifecycleProfile.V4_UFG_FIXED_POOL` / `V4_UFG_DYNAMIC_POOL` for V4, with
`ufg_launch_config=config` on config6; canonical Abyss QUOTE/QUOTE_ORACLE uses
its unchanged config1 without a tail. Reserve fees fund the treasury; launch-token
fees burn. This is ERC20/no-rewards only, with 100% treasury share and no executor fee.

```python
from black_market_sdk import (
    UfgLaunchConfig, validate_ufg_launch_plan, build_ufg_launch_transactions,
    build_ufg_approvals, read_ufg_treasury,
)

config = UfgLaunchConfig(10000, 100, tuple(sorted(reserves, key=lambda asset: int(asset, 16))))
validate_ufg_launch_plan(plan, config, predicted_token)
approvals = build_ufg_approvals(plan)  # ERC20 approvals target the proxy, not escrow.
transactions = build_ufg_launch_transactions(plan, config=config, mode="staged")
# These are unsigned commands, not live execution admission.
# After authorized execution: treasury = read_ufg_treasury(client, predicted_token)
```

The fork-only [`launch_ufg_treasury.py`](examples/launch_ufg_treasury.py) example
executes native Abyss plus fixed V4 atomically and native Abyss plus dynamic V4
in stages, without keys or mainnet writes. See the guide for principal funding,
live planning, config hashes and the Anvil/native-Nitro proof boundary.

## Examples

The most complete case is [`launch_erc20_dividends_multiquote_mixed.py`](examples/launch_erc20_dividends_multiquote_mixed.py).
On Robinhood mainnet (**chain 4663**), it:

1. Creates an ERC20 with 1,000,000 initial tokens and holder dividends.
   Fee-asset allocations split 40% to rewards and 60% to the owner, not a 40% tax.
2. Splits liquidity across V4/WETH (two positions), V4/USDG (two), and Abyss/WETH
   (three), priced from the same whole-supply USD valuation (default $5,000).
3. Funds one opening buy per market with 0.00001 ETH each by default. ETH wraps
   into WETH; USDG uses an admitted Kyber conversion. ETH for gas is additional.
4. Prepares and proves the available staged prefix, prints the economic review,
   and saves artifacts. Buy calibration requires activation outputs to fit the
   selected RPC's simulation budget. The default dry-run never signs or submits.
5. With authorization, stages metadata, begins and prepares the markets, then
   activates all seven positions and three buys together. It verifies chain
   results and API publication; optional images are verified before submission.

### Configure and run

From the checkout root, create `.env` without overwriting existing configuration:

```sh
cp -n examples/.env.example .env
chmod 600 .env
```

Set `LAUNCH_API_URL` and exactly one key source: `PRIVATE_KEY`, or
`EXAMPLE_KEYSTORE_PATH` to an owner-only V3 JSON keystore (`EXAMPLE_KEYSTORE_PASSWORD`
defaults to empty). Optional `RPC_URL` overrides the official chain4663 RPC.
Remote endpoints require HTTPS; existing environment values override `.env`.

```sh
# Dry run: no signing, chain submission, image upload, or publication.
python examples/launch_erc20_dividends_multiquote_mixed.py
# Explicitly authorize spending funds and publishing; no confirmation prompt.
python examples/launch_erc20_dividends_multiquote_mixed.py --broadcast
# After partial execution, replace this path with the ORIGINAL run directory.
python examples/launch_erc20_dividends_multiquote_mixed.py --resume "launch-results/<original-run>"
```

Dry-run needs live RPC/API access, the creator key, funding, and admission.
Review the saved `economic-review.json` in `launch-results/`. Broadcast builds a
**fresh plan**; resume may spend/publish but retains the original plan and session.
Keep recovery files; never commit or share keys, private artifacts, or signed bytes.

## Creating your own

Copy the case wrapper beside its helpers, then customize it:

```sh
cp examples/launch_erc20_dividends_multiquote_mixed.py examples/launch_my_token.py
python examples/launch_my_token.py
```

Edit `CASE`: unique `id`, `tokenKind` (0 ERC20 / 1 ERC404), `rewardMode`
(0 none / 1 staking / 2 dividends), `mode` (`atomic` / `staged`), `markets`
(`venue`, `quote`, `positions`), and `buysPerMarket`. Supported venues are
`v4`/`abyss`, quotes `WETH`/`USDG`. Customize identity, valuation, and buy input
with the `EXAMPLE_*` keys in [`.env.example`](examples/.env.example).
For supply, fees, budgets, or ranges, adapt `construct_plan`, `fee_policies`, and
`derive_market_pricing` in `launch_examples.py`; copy `_launch_support.py` with it.

See the [launch/API guide](docs/launch-guide.md), [runner configuration](examples/README.md),
and [MIT license](LICENSE) for deeper reference.
