![Abyss](https://raw.githubusercontent.com/abysshopper/black-market-python-sdk/main/assets/abyss-ziggurat-v3-pale-text-1500x500.png)

# Abyss Python SDK

Launch tokens, read pools, swap, and use lending with [web3.py](https://web3py.readthedocs.io).
Package **0.15.0**, Python **3.10+**, import `black_market_sdk`.
The launch examples target Robinhood Chain mainnet (**4663**).

## Install

Install the published package in your Python environment:

```sh
python -m pip install black-market-sdk==0.15.0
```

For the runnable examples, install an editable checkout:

```sh
git clone https://github.com/abysshopper/black-market-python-sdk.git
cd black-market-python-sdk
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Discover without a wallet

```sh
python examples/quickstart.py
python examples/launch_matrix.py
```

These optional audits read profile IDs, venues, admission, and refusal reasons.
Use `list_known_lifecycle_profiles` for the shipped zero-RPC offering catalog;
new registrations appear there when the SDK release is updated.

## Run your first launch

Start with [`launch_erc20_abyss.py`](examples/launch_erc20_abyss.py): one ERC20,
one canonical Abyss pool, one permanent position, and one opening buy, atomically.

```sh
cp -n examples/.env.example .env
chmod 600 .env
```

Set `LAUNCH_API_URL=https://api.abyss.trading` and exactly one key source:
`PRIVATE_KEY`, or `EXAMPLE_KEYSTORE_PATH` with `EXAMPLE_KEYSTORE_PASSWORD` if needed.
Fund that creator with ETH for the buy **and gas**. `RPC_URL` optionally overrides
the official mainnet RPC. Process environment values override `.env`.

```sh
# Dry run: discovery, pricing and admission; no signatures or writes.
python examples/launch_erc20_abyss.py
# Authorizes spending and publication, without an interactive prompt.
python examples/launch_erc20_abyss.py --broadcast
# After partial execution, use the ORIGINAL broadcast run directory.
python examples/launch_erc20_abyss.py --resume "launch-results/<original-run>"
```

Even a dry run needs the creator key, sufficient funds, and live RPC/API access.
Review the full supply, aligned starting valuation, fees, buy output/share, minimum
output, and gas before broadcasting. `--broadcast` builds a **fresh plan**, not the
dry-run plan. `--resume` retains the original identity, plan, receipts, and session.
Keep recovery files private; never commit keys, capabilities, or signed bytes.

### Understand the defaults

- ERC20 supply: **1,000,000** tokens; ERC404 supply: **10,000**, with 100-token NFT units.
- `EXAMPLE_TARGET_MARKET_CAP_USD=5000`: full minted-supply valuation, not cash raised.
  At 1M tokens, that is $0.005/token before tick rounding. It is not a recommendation.
- `EXAMPLE_BUY_AMOUNT_WEI=10000000000000`: **0.00001 ETH per buy**, plus gas.
  WETH funding wraps ETH; USDG funding uses a Kyber conversion.
- Abyss fee: **3000 pips (0.3%)**, spacing 60. Fixed V4 examples add a **1% hook fee**
  to their 0.3% LP fee. Their config6 minimum does not select dynamic behavior.
- Reward examples allocate **40% of distributed fees** to rewards and 60% to the
  owner. The **275 bps** collection reward is a fee-distribution share, not a trader tax.
- Launch commands reserve the preset's **32M total gas** on Robinhood, including
  poster gas. Profiles select mode, never signing-gas size. An atomic preference
  can select staged execution; review the returned `launch.mode`.
- Approvals leave gas/fee preparation to the wallet. Held launch gas must match the
  native ceiling, and held prices below the pinned fee floor refuse without a bump.

## Examples

All eleven cases use `launch_examples.py` and `_launch_support.py`.
Counts below are markets / positions / ordered buys.

| Script in `examples/` | Token / rewards | Counts | Mode |
| --- | --- | --- | --- |
| [`launch_erc20_abyss.py`](examples/launch_erc20_abyss.py) | ERC20 | 1 / 1 / 1 | atomic |
| [`launch_erc20_v4.py`](examples/launch_erc20_v4.py) | ERC20 | 1 / 1 / 1 | atomic |
| [`launch_erc20_staking_v4.py`](examples/launch_erc20_staking_v4.py) | ERC20 staking | 1 / 2 / 2 | staged |
| [`launch_erc20_dividends_abyss.py`](examples/launch_erc20_dividends_abyss.py) | ERC20 dividends | 1 / 3 / 1 | staged |
| [`launch_erc20_burn_mixed.py`](examples/launch_erc20_burn_mixed.py) | ERC20 burn split | 2 / 3 / 2 | staged |
| [`launch_erc404_v4.py`](examples/launch_erc404_v4.py) | ERC404 | 1 / 1 / 1 | staged |
| [`launch_erc404_abyss.py`](examples/launch_erc404_abyss.py) | ERC404 | 1 / 1 / 1 | atomic |
| [`launch_erc404_dividends_mixed.py`](examples/launch_erc404_dividends_mixed.py) | ERC404 dividends | 2 / 5 / 2 | staged |
| [`launch_erc20_multi_quote.py`](examples/launch_erc20_multi_quote.py) | ERC20, WETH + USDG | 3 / 3 / 3 | staged |
| [`launch_erc20_depth_v4.py`](examples/launch_erc20_depth_v4.py) | ERC20, four bands | 1 / 4 / 1 | staged |
| [`launch_erc20_dividends_multiquote_mixed.py`](examples/launch_erc20_dividends_multiquote_mixed.py) | ERC20 dividends, WETH + USDG | 3 / 7 / 3 | staged |

Multi-market conversion-funded reviews prove their state prerequisites through
activation in one native simulation request. There is no default 50M aggregate
cap or silently admitted prefix: an actual provider limit or explicit caller
policy can refuse the review. Direct-funded covered profiles need no simulation
window. See [prepare and simulate](docs/launch-guide.md#prepare-and-simulate).

## Choose an offering

| Offering | Use it for | Guide |
| --- | --- | --- |
| Canonical Abyss | STANDARD / STANDARD_ORACLE / QUOTE / QUOTE_ORACLE pools | [Choose an offering](docs/launch-guide.md#choose-an-offering) |
| Fixed V4 | Pool-bound hook with a fixed trading fee | [Build the plan](docs/launch-guide.md#build-the-plan) |
| Dynamic V4 | Fee minimum, maximum, and tick-velocity sensitivity | [Choose an offering](docs/launch-guide.md#choose-an-offering) |
| HMM V2 | Fixed/dynamic V4 plus fee-funded auxiliary liquidity | [HMM launch configuration](docs/launch-guide.md#hmm-launch-configuration) |
| UFG treasury | ERC20 with quote-fee reserves and holder redemption | [UFG treasury launches](docs/launch-guide.md#ufg-treasury-launches) |

## Integrate and recover

Follow the [launch guide](docs/launch-guide.md) for pricing, signing, publication,
recovery, and claims. Check **receipt confirmed → markets active → metadata
published → API indexed** separately; an API timeout does not undo an open token.

Release-owned openings use versioned gas profiles and exact ordered local buy
math. Kyber-funded execution and its consuming activation retain exact simulation.
`build_next_transaction` handles only the immediate command. The wallet assigns
its transaction nonce; the committed `LaunchPlanV1.nonce` remains launch identity.

See [runner configuration](examples/README.md), [limits and codecs](docs/reference.md),
[release changes](CHANGELOG.md), and the [MIT license](LICENSE).
