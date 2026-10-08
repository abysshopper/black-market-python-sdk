"""Web3 client factories resolve Node-compatible defaults without RPC calls."""

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
LOCAL_RPC = "http://127.0.0.1:8545"


@pytest.fixture(autouse=True)
def clear_rpc_override(monkeypatch):
    monkeypatch.delenv("RPC_URL", raising=False)


def test_create_protocol_web3_defaults_to_local_rpc():
    w3 = create_protocol_web3()
    assert isinstance(w3, Web3)
    assert w3.provider.endpoint_uri == LOCAL_RPC


def test_create_protocol_web3_explicit_mainnet_selects_mainnet_rpc():
    w3 = create_protocol_web3(chain_id=ROBINHOOD_MAINNET_CHAIN_ID)
    assert w3.provider.endpoint_uri == ROBINHOOD_MAINNET_RPC


@pytest.mark.parametrize("chain_id", [ANVIL_LOCAL_CHAIN_ID, ROBINHOOD_MAINNET_CHAIN_ID, 1])
def test_create_protocol_web3_explicit_rpc_url_wins(monkeypatch, chain_id):
    monkeypatch.setenv("RPC_URL", "https://environment.example/rpc")
    w3 = create_protocol_web3(chain_id=chain_id, rpc_url="https://explicit.example/rpc")
    assert w3.provider.endpoint_uri == "https://explicit.example/rpc"


@pytest.mark.parametrize("chain_id", [ANVIL_LOCAL_CHAIN_ID, ROBINHOOD_MAINNET_CHAIN_ID, 1])
def test_create_protocol_web3_environment_overrides_chain_rpc(monkeypatch, chain_id):
    monkeypatch.setenv("RPC_URL", "https://environment.example/rpc")
    assert create_protocol_web3(chain_id=chain_id).provider.endpoint_uri == "https://environment.example/rpc"


def test_unknown_chain_uses_node_foundry_default():
    assert create_protocol_web3(chain_id=1).provider.endpoint_uri == LOCAL_RPC


def test_create_protocol_wallet_web3_binds_signer_and_local_default():
    w3, account = create_protocol_wallet_web3(private_key=DEV_PRIVATE_KEY)
    assert account.address == DEV_ADDRESS
    assert w3.eth.default_account == DEV_ADDRESS
    assert w3.provider.endpoint_uri == LOCAL_RPC


def test_create_protocol_wallet_web3_uses_same_rpc_precedence(monkeypatch):
    monkeypatch.setenv("RPC_URL", "https://environment.example/rpc")
    w3, account = create_protocol_wallet_web3(
        private_key=DEV_PRIVATE_KEY,
        chain_id=ROBINHOOD_MAINNET_CHAIN_ID,
        rpc_url="https://explicit.example/rpc",
    )
    assert w3.provider.endpoint_uri == "https://explicit.example/rpc"
    assert account.address == w3.eth.default_account == DEV_ADDRESS
