"""Exact protocol ABIs imported from the reviewed Node SDK release.

Regenerate the packaged data with ``tools/ci/import_node_abis.mjs``. Values are
ordinary ABI lists suitable for ``web3.eth.contract(abi=...)``.
"""

from __future__ import annotations

import json
from importlib.resources import files

_SDK_ABI_DATA = json.loads(files(__package__).joinpath("_sdk_abi_data.json").read_text(encoding="utf-8"))

AAVE_ORACLE_ABI = _SDK_ABI_DATA["aaveOracleAbi"]
ABYSS_LIQUIDATION_EXECUTOR_ABI = _SDK_ABI_DATA["abyssLiquidationExecutorAbi"]
AGGREGATOR_V3_ABI = _SDK_ABI_DATA["aggregatorV3Abi"]
BLACK_MARKET_LENS_ABI = _SDK_ABI_DATA["blackMarketLensAbi"]
BURNABLE_FIXED_SUPPLY_TOKEN_ABI = _SDK_ABI_DATA["burnableFixedSupplyTokenAbi"]
CHAINED_PRICE_FEED_ABI = _SDK_ABI_DATA["chainedPriceFeedAbi"]
CREDIT_DELEGATION_ABI = _SDK_ABI_DATA["creditDelegationAbi"]
ERC20_ABI = _SDK_ABI_DATA["erc20Abi"]
LENDING_POOL_ABI = _SDK_ABI_DATA["lendingPoolAbi"]
PRICE_FEED_ABI = _SDK_ABI_DATA["priceFeedAbi"]
PROTOCOL_DATA_PROVIDER_ABI = _SDK_ABI_DATA["protocolDataProviderAbi"]
TOKEN_VESTING_ABI = _SDK_ABI_DATA["tokenVestingAbi"]
UI_POOL_DATA_PROVIDER_ABI = _SDK_ABI_DATA["uiPoolDataProviderAbi"]
WALLET_BALANCE_PROVIDER_ABI = _SDK_ABI_DATA["walletBalanceProviderAbi"]
WETH_ABI = _SDK_ABI_DATA["wethAbi"]
WETH_GATEWAY_ABI = _SDK_ABI_DATA["wethGatewayAbi"]
WORKBENCH_FAUCET_ABI = _SDK_ABI_DATA["workbenchFaucetAbi"]
