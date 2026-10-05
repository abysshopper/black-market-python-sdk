![Abyss](https://raw.githubusercontent.com/abysshopper/black-market-python-sdk/main/assets/abyss-social.webp)

# Abyss Python SDK

Token launches, swaps, and lending with [web3.py](https://web3py.readthedocs.io).

## Install

```sh
python -m pip install black-market-sdk
```

## Examples

Clone this repository to run the examples. Requires Python 3.10+.

```sh
python -m pip install -e .
python examples/quickstart.py
```

The quickstart reads available launch profiles without a wallet.

For token launches, set `PRIVATE_KEY` and `LAUNCH_API_URL` in your working
folder's `.env`. An optional `RPC_URL` overrides the default endpoint.
Run one example for the token you want to create:

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

Launch examples spend real funds and publish to your configured API.
Plans, transaction hashes, receipts, and errors are saved in `launch-results/`.
Keep `.env` and private recovery files out of Git.

[Example source](https://github.com/abysshopper/black-market-python-sdk/tree/main/examples)
· [API guide](https://github.com/abysshopper/black-market-python-sdk/blob/main/docs/launch-guide.md)
· [MIT license](https://github.com/abysshopper/black-market-python-sdk/blob/main/LICENSE)
