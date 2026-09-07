"""web3.py client factories for the Black Market protocol."""

from __future__ import annotations

from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.middleware import SignAndSendRawMiddlewareBuilder

from .addresses import (
    ANVIL_LOCAL_CHAIN_ID,
    ROBINHOOD_MAINNET_CHAIN_ID,
    ROBINHOOD_MAINNET_RPC,
    WORKBENCH_CHAIN_ID,
    default_rpc_url,
    is_supported_chain_id,
)

_DEFAULT_RPCS = {
    ROBINHOOD_MAINNET_CHAIN_ID: ROBINHOOD_MAINNET_RPC,
}


def _resolve_rpc_url(chain_id: int, rpc_url: Optional[str]) -> str:
    if rpc_url:
        return rpc_url
    if chain_id in _DEFAULT_RPCS:
        return _DEFAULT_RPCS[chain_id]
    return default_rpc_url()


def create_protocol_web3(
    chain_id: int = ANVIL_LOCAL_CHAIN_ID,
    rpc_url: Optional[str] = None,
) -> Web3:
    """Read-only Web3 instance bound to a supported Black Market chain."""
    if not is_supported_chain_id(chain_id):
        raise ValueError(f"Unsupported chain id: {chain_id}")
    return Web3(Web3.HTTPProvider(_resolve_rpc_url(chain_id, rpc_url)))


def create_protocol_wallet_web3(
    private_key: str,
    chain_id: int = ANVIL_LOCAL_CHAIN_ID,
    rpc_url: Optional[str] = None,
) -> tuple[Web3, LocalAccount]:
    """Web3 instance with a signing middleware for the given private key."""
    w3 = create_protocol_web3(chain_id=chain_id, rpc_url=rpc_url)
    account: LocalAccount = Account.from_key(private_key)
    w3.middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(account), layer=0)
    w3.eth.default_account = account.address
    return w3, account
