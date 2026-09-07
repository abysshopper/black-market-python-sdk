"""web3 client factory shape tests (offline — no RPC calls are made)."""

import pytest
from web3 import Web3

from black_market_sdk import (
    ANVIL_LOCAL_CHAIN_ID,
    ROBINHOOD_MAINNET_CHAIN_ID,
    ROBINHOOD_MAINNET_RPC,
    create_protocol_wallet_web3,
    create_protocol_web3,
)

# Well-known Hardhat/Anvil dev account #0 — never holds real funds.
DEV_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_create_protocol_web3_defaults_to_mainnet_rpc():
    w3 = create_protocol_web3(chain_id=ROBINHOOD_MAINNET_CHAIN_ID)
    assert isinstance(w3, Web3)
    provider = w3.provider
    assert provider.endpoint_uri == ROBINHOOD_MAINNET_RPC


def test_create_protocol_web3_explicit_rpc_url_wins():
    w3 = create_protocol_web3(chain_id=ANVIL_LOCAL_CHAIN_ID, rpc_url="http://127.0.0.1:8545")
    assert w3.provider.endpoint_uri == "http://127.0.0.1:8545"


def test_create_protocol_web3_rejects_unsupported_chain():
    with pytest.raises(ValueError, match="Unsupported chain id"):
        create_protocol_web3(chain_id=1)


def test_create_protocol_wallet_web3_binds_signer():
    w3, account = create_protocol_wallet_web3(
        private_key=DEV_PRIVATE_KEY,
        chain_id=ROBINHOOD_MAINNET_CHAIN_ID,
    )
    assert account.address == DEV_ADDRESS
    # The signer becomes the default account for outbound transactions.
    assert w3.eth.default_account == DEV_ADDRESS
