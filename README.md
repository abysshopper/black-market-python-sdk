![Abyss header](assets/abyss-header.png)

# black-market-sdk

Canonical Python SDK for the Black Market protocol on [Robinhood Chain](https://robinhoodchain.blockscout.com) — Abyss DEX infrastructure, Atomic token launches, and the lending market.

Built on [web3.py](https://web3py.readthedocs.io). Python 3.10+. Mirrors the TypeScript SDK ([@black-market/sdk](https://www.npmjs.com/package/@black-market/sdk)).

## Install

```sh
pip install black-market-sdk
```

## Quick start

```python
from black_market_sdk import create_protocol_web3, get_addresses, ROBINHOOD_MAINNET_CHAIN_ID

w3 = create_protocol_web3(chain_id=ROBINHOOD_MAINNET_CHAIN_ID)
addresses = get_addresses(ROBINHOOD_MAINNET_CHAIN_ID)  # canonical mainnet deployment
# get_addresses() defaults to the workbench fork (46631); pass the chain id explicitly.
```

### Wallet client (signing)

```python
import os

from black_market_sdk import create_protocol_wallet_web3

w3, account = create_protocol_wallet_web3(
    private_key=os.environ["PRIVATE_KEY"],
    chain_id=4663,
)
```

### Atomic launches

```python
from black_market_sdk import (
    AtomicLaunchPoolRecipeInput,
    build_atomic_launch_calldata,
    derive_atomic_launch_pool_recipe,
    estimate_atomic_launch_initial_buy,
)

# Derive the one-sided launch position from a target FDV.
recipe = derive_atomic_launch_pool_recipe(
    AtomicLaunchPoolRecipeInput(
        paired_token_decimals=18,
        paired_token_usd_price_x18=2_500 * 10**18,
        target_market_cap_usd_x18=5_000 * 10**18,
        launched_token_is_quote=False,
        fee=3_000,
    )
)

# Validate a request and encode deployAndLaunch calldata.
calldata = build_atomic_launch_calldata(request, native_buy_amount=0)
```

### Networks

| Chain ID | Network                       |
| -------: | ----------------------------- |
|   `4663` | Robinhood Chain mainnet       |
|  `46631` | Black Market workbench (fork) |
|  `31337` | Local Anvil                   |

`get_addresses()` defaults to workbench chain `46631`; pass `4663` for canonical mainnet or `31337` for Anvil. Overrides are read once at module import. Launch addresses accept bare, `VITE_`, then `NEXT_PUBLIC_` keys on every chain. Abyss infrastructure overrides apply only to `31337` and `46631`; mainnet Abyss addresses remain canonical. Lending overrides apply to `31337` with all three forms and to `46631` with `VITE_`/`NEXT_PUBLIC_` only. `LIQUIDATION_EXECUTOR` is additionally overridable on mainnet.

## Package layout

| Module                        | Contents                                                             |
| ----------------------------- | -------------------------------------------------------------------- |
| `black_market_sdk.addresses`  | Chain IDs, canonical deployment addresses, env overrides             |
| `black_market_sdk.abis`       | Lending-market ABIs (pool, data providers, lens, oracle, vesting, …) |
| `black_market_sdk.abyss`      | Abyss DEX ABIs and launch-module ABIs, pool profiles, fee tiers      |
| `black_market_sdk.auction`    | Atomic launch supply constants and paired-asset (quote) catalog      |
| `black_market_sdk.launch`     | Launch templates, Atomic launch recipe derivation, calldata building |
| `black_market_sdk.live`       | Reserve/user position normalization from lens & data-provider rows   |
| `black_market_sdk.format`     | RAY/WAD math, health-factor and units formatting helpers             |
| `black_market_sdk.client`     | web3.py client factories                                             |

## Examples

Examples never sign or broadcast transactions. `quickstart.py` builds a fresh launch request offline; `launch_recipe.py` focuses on pricing math; and `abby_launch.py` reconstructs the successful [Abby mainnet launch](https://robinhoodchain.blockscout.com/tx/0xd0dcae27e9ec2f7fb6e2304d7b1d739fd2f45f91d1eb5e762cdfcf01819fb837) byte-for-byte.

```sh
python examples/quickstart.py
python examples/launch_recipe.py
python examples/abby_launch.py
```

## Development

```sh
pip install -e ".[dev]"
pytest
```

## Releasing

Releases are published to PyPI from GitHub Actions with
[trusted publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no API
tokens or passwords are stored in this repository.

1. Bump `version` in `pyproject.toml` and commit.
2. Cut a matching git tag (for example, `v0.1.0`) and create a GitHub Release from it.
3. The `publish` workflow runs on the release `published` event, verifies the tag matches `pyproject.toml`, builds and checks the sdist and wheel, then uploads them to PyPI via OIDC. It can also be run manually with an explicit version input.

The publish job declares `environment: pypi`. Configure the `pypi` environment
in the repository settings (required reviewers, allowed branches/tags) so a
human approves every release, and register the workflow as a trusted publisher
in the PyPI project settings.

## License

MIT
