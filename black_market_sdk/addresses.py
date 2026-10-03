"""Chain definitions and canonical Black Market deployment addresses.

Overrides are evaluated once at module import. Launch-application and unified
launch addresses use bare, ``VITE_``, then ``NEXT_PUBLIC_`` precedence on every
chain. Abyss infrastructure overrides apply to ``31337`` and ``46631`` only;
mainnet ``4663`` remains canonical. Lending overrides apply to ``31337`` with
the same precedence and to ``46631`` with public prefixes only. The mainnet
liquidation executor is the sole lending-field exception and supports all three
forms.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional

from .auction import ZERO_ADDRESS

SupportedChainId = int
ANVIL_LOCAL_CHAIN_ID: SupportedChainId = 31337
ROBINHOOD_MAINNET_CHAIN_ID: SupportedChainId = 4663
WORKBENCH_CHAIN_ID: SupportedChainId = 46631

SUPPORTED_CHAIN_IDS = (ANVIL_LOCAL_CHAIN_ID, ROBINHOOD_MAINNET_CHAIN_ID, WORKBENCH_CHAIN_ID)

ROBINHOOD_MAINNET_RPC = "https://rpc.mainnet.chain.robinhood.com/"
ROBINHOOD_MAINNET_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

ROBINHOOD_WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
ROBINHOOD_ETH_USD = "0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9"


def _read_env(*keys: str) -> Optional[str]:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def default_rpc_url() -> str:
    return (
        _read_env("VITE_RPC_URL", "NEXT_PUBLIC_RPC_URL", "RPC_URL") or "http://127.0.0.1:8545"
    )


def _env_addr(name: str, fallback: str = ZERO_ADDRESS) -> str:
    return _read_env(name, f"VITE_{name}", f"NEXT_PUBLIC_{name}") or fallback

def _env_addr_public(name: str, fallback: str = ZERO_ADDRESS) -> str:
    """Workbench fields read only the public (VITE_/NEXT_PUBLIC_) variants."""
    return _read_env(f"VITE_{name}", f"NEXT_PUBLIC_{name}") or fallback


@dataclass(frozen=True)
class AbyssInfrastructureAddresses:
    abyss_factory: str
    abyss_fee_vault: str
    abyss_pool_deployer: str
    abyss_router: str
    abyss_quoter: str
    abyss_position_manager: str
    abyss_position_locker: str
    abyss_token: str
    abyss_buyback_burner_implementation: str
    abyss_buyback_burner: str
    abyss_fee_router_implementation: str
    abyss_fee_router: str


@dataclass(frozen=True)
class LaunchApplicationAddresses:
    launch_token_factory: str
    launch_coordinator: str
    launch_factory: str
    launch_template_registry: str
    launch_module_factory: str
    launch_fee_owner_registry: str


@dataclass(frozen=True)
class UnifiedLaunchAddresses:
    """Verified schema-/2 launcher routes and supporting deployer contracts."""

    unified_launcher: str
    launch_pool_registry: str
    abyss_launch_pool_adapter_v3: str
    uniswap_v4_launch_pool_adapter_v3: str
    burnable_token_deployer_v2: str
    holder_dividend_token_deployer_v2: str
    uniswap_v4_pool_manager: str
    uniswap_v4_v3_hook_deployer: str
    uniswap_v4_v3_liquidity_locker: str
    uniswap_v4_v3_abyss_bonus_distributor: str

@dataclass(frozen=True)
class ProtocolAddresses(AbyssInfrastructureAddresses, LaunchApplicationAddresses):
    lending_pool: str
    addresses_provider: str
    data_provider: str
    ui_pool_data_provider: str
    wallet_balance_provider: str
    aave_oracle: str
    liquidation_executor: str
    protocol_vault: str
    eth_usd_feed: str
    weth: str
    weth_gateway: str
    lens: str
    token_vesting: str
    faucet: str


_ABYSS_ENV_KEYS = {
    "abyss_factory": "ABYSS_FACTORY",
    "abyss_fee_vault": "ABYSS_FEE_VAULT",
    "abyss_pool_deployer": "ABYSS_POOL_DEPLOYER",
    "abyss_router": "ABYSS_ROUTER",
    "abyss_quoter": "ABYSS_QUOTER",
    "abyss_position_manager": "ABYSS_POSITION_MANAGER",
    "abyss_position_locker": "ABYSS_POSITION_LOCKER",
    "abyss_token": "ABYSS_TOKEN",
    "abyss_buyback_burner_implementation": "ABYSS_BUYBACK_BURNER_IMPLEMENTATION",
    "abyss_buyback_burner": "ABYSS_BUYBACK_BURNER",
    "abyss_fee_router_implementation": "ABYSS_FEE_ROUTER_IMPLEMENTATION",
    "abyss_fee_router": "ABYSS_FEE_ROUTER",
}

_LAUNCH_ENV_KEYS = {
    "launch_token_factory": "LAUNCH_TOKEN_FACTORY",
    "launch_coordinator": "LAUNCH_COORDINATOR",
    "launch_factory": "LAUNCH_FACTORY",
    "launch_template_registry": "LAUNCH_TEMPLATE_REGISTRY",
    "launch_module_factory": "LAUNCH_MODULE_FACTORY",
    "launch_fee_owner_registry": "LAUNCH_FEE_OWNER_REGISTRY",
}

_UNIFIED_LAUNCH_ENV_KEYS = {
    "unified_launcher": "LAUNCH_FACTORY",
    "launch_pool_registry": "LAUNCH_POOL_REGISTRY",
    "abyss_launch_pool_adapter_v3": "ABYSS_LAUNCH_POOL_ADAPTER_V3",
    "uniswap_v4_launch_pool_adapter_v3": "UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3",
    "burnable_token_deployer_v2": "BURNABLE_TOKEN_DEPLOYER_V2",
    "holder_dividend_token_deployer_v2": "HOLDER_DIVIDEND_TOKEN_DEPLOYER_V2",
    "uniswap_v4_pool_manager": "UNISWAP_V4_POOL_MANAGER",
    "uniswap_v4_v3_hook_deployer": "UNISWAP_V4_V3_HOOK_DEPLOYER",
    "uniswap_v4_v3_liquidity_locker": "UNISWAP_V4_V3_LIQUIDITY_LOCKER",
    "uniswap_v4_v3_abyss_bonus_distributor": "UNISWAP_V4_V3_ABYSS_BONUS_DISTRIBUTOR",
}


def _abyss_infrastructure_from_env(
    fallback: AbyssInfrastructureAddresses,
) -> AbyssInfrastructureAddresses:
    return AbyssInfrastructureAddresses(
        **{
            field: _env_addr(name, getattr(fallback, field))
            for field, name in _ABYSS_ENV_KEYS.items()
        }
    )


def _launch_application_from_env(
    fallback: LaunchApplicationAddresses,
) -> LaunchApplicationAddresses:
    return LaunchApplicationAddresses(
        **{
            field: _env_addr(name, getattr(fallback, field))
            for field, name in _LAUNCH_ENV_KEYS.items()
        }
    )


def _unified_launch_from_env(
    fallback: UnifiedLaunchAddresses,
) -> UnifiedLaunchAddresses:
    return UnifiedLaunchAddresses(
        **{
            field: _env_addr(name, getattr(fallback, field))
            for field, name in _UNIFIED_LAUNCH_ENV_KEYS.items()
        }
    )


_ZERO_ABYSS_INFRASTRUCTURE = AbyssInfrastructureAddresses(**{f: ZERO_ADDRESS for f in _ABYSS_ENV_KEYS})
_ZERO_LAUNCH_APPLICATION = LaunchApplicationAddresses(**{f: ZERO_ADDRESS for f in _LAUNCH_ENV_KEYS})
_ZERO_UNIFIED_LAUNCH_ADDRESSES = UnifiedLaunchAddresses(
    **{field: ZERO_ADDRESS for field in _UNIFIED_LAUNCH_ENV_KEYS}
)


#: Canonical replacement: abyss/deployments/4663/abyss-canonical-replacement-20260830/deployment.json.
ROBINHOOD_ABYSS_INFRASTRUCTURE = AbyssInfrastructureAddresses(
    abyss_factory="0xe7feF2BC860B25bbdEB6F6AB96d88bAAa77ddad7",
    abyss_fee_vault="0x19b04F2E86fFDf26510ACf25344c406240214d5F",
    abyss_pool_deployer="0xe49Ff46f2Ca543D5504cDCF533fe84e0c95eF693",
    abyss_router="0xF7818c69e31bf98eFF96C721B557Bb519659CD27",
    abyss_quoter="0xF1ff7c78605939df2d705F3E12Ac9CEB69Bcfe76",
    abyss_position_manager="0x1b2176d4D2C7bd36227D92Ed84F1A3aE30B635bF",
    abyss_position_locker="0xa0d4fA31740FA0d8fc6b5b4173Da17864Eb6e888",
    abyss_token="0x15f3385625D7e364C5a6216FBbceadf10fa90e7d",
    abyss_buyback_burner_implementation="0xd17F05C41FfdebB6b57b3e232c20a340906A0aAd",
    abyss_buyback_burner="0xF6C7159e967f28C65d9Fb5b919567E04540cD2FF",
    abyss_fee_router_implementation="0xB35b85db23fEF3A146Fbd755D76be12F9e1eBfFC",
    abyss_fee_router="0x2c3B1b6fe0EDa8e10C0445567b47e66E825B34cd",
)

#: Historical Atomic replacement, retained solely for explicit legacy calldata
#: reconstruction. It must not be used as the current UnifiedLauncher target.
ROBINHOOD_ATOMIC_LAUNCH_APPLICATION = LaunchApplicationAddresses(
    launch_token_factory="0x7B6F6efb4536F579223423e31d9036D996bc90F0",
    launch_coordinator="0xE98A82202D794A7836316971e7ACf61F260f8399",
    launch_factory="0xAf3FdC499b3717EBE8aD51B66bA78Cb083552351",
    launch_template_registry="0x5C22c6e02bAC8225eed64629447868bAA5774186",
    launch_module_factory="0xb2D9988592eE245a36290A0992505ca0474434f2",
    launch_fee_owner_registry="0xdD3756269Db20F2a60055b0FE8c101FAd12525f9",
)

#: schema-/2 deployment record's applicationAddresses object. ``launch_factory``
#: is the current UnifiedLauncher entry point, not an AtomicLaunchFactory.
ROBINHOOD_LAUNCH_APPLICATION = LaunchApplicationAddresses(
    launch_token_factory="0x84225a7b7fd9981f8a3086acfbbb688348247f6f",
    launch_coordinator="0xcc3aa2dff0fd6e9505b12b731111ec1b7b49621d",
    launch_factory="0xa7a4755fb907593f05fd1e289aa780f0d57f3a12",
    launch_template_registry="0x01422012c452f2e363d56bd408c7ed1c44204701",
    launch_module_factory="0xe6bb1f77b94fa2003db0f4c2e248649061922c64",
    launch_fee_owner_registry="0xa8018950ebb6a35708820c89243ddab8718ee0bc",
)

#: Verified routes from the schema-/2 deployment record and the later enabled
#: V3 extension registration.
ROBINHOOD_UNIFIED_LAUNCH_ADDRESSES = UnifiedLaunchAddresses(
    unified_launcher="0xa7a4755fb907593f05fd1e289aa780f0d57f3a12",
    launch_pool_registry="0x04f453aac720a5fb410fe81fc50b747f969b352c",
    abyss_launch_pool_adapter_v3="0xd0eb22fd4be5d049545e072d8e7fad72475b4cea",
    uniswap_v4_launch_pool_adapter_v3="0x9607ddc99381f18985770b4f93685ed90220bc98",
    burnable_token_deployer_v2="0xd12ed68dd35dc9f76df8fe5b99cd988430f356d1",
    holder_dividend_token_deployer_v2="0xe9ea8555e0e7ee68a0733f45c285e7b5558ab842",
    uniswap_v4_pool_manager="0x8366a39cc670b4001a1121b8f6a443a643e40951",
    uniswap_v4_v3_hook_deployer="0x78e773891f66f0423789e6d06f3cf613c0296d7a",
    uniswap_v4_v3_liquidity_locker="0x44bb63597bff2e7cb6c7fd1716df4abd6e3420ec",
    uniswap_v4_v3_abyss_bonus_distributor="0xd3f171677431644aefc17a0cfc66d65b626284b3",
)
#: Workbench deployment snapshot — override via env when redeploying.
_WORKBENCH_DEFAULTS = ProtocolAddresses(
    **vars(ROBINHOOD_ABYSS_INFRASTRUCTURE),
    **vars(_launch_application_from_env(_ZERO_LAUNCH_APPLICATION)),
    lending_pool="0xe1576c5CF12F670911BEd5Cc0AEDBcD4E5E9550c",
    addresses_provider="0x9Fcca440F19c62CDF7f973eB6DDF218B15d4C71D",
    data_provider="0x79E8AB29Ff79805025c9462a2f2F12e9A496f81d",
    ui_pool_data_provider=ZERO_ADDRESS,
    wallet_balance_provider=ZERO_ADDRESS,
    aave_oracle="0x9c65f85425c619A6cB6D29fF8d57ef696323d188",
    liquidation_executor=ZERO_ADDRESS,
    protocol_vault="0xAe120F0df055428E45b264E7794A18c54a2a3fAF",
    eth_usd_feed=ROBINHOOD_ETH_USD,
    weth=ROBINHOOD_WETH,
    weth_gateway=ZERO_ADDRESS,
    lens=ZERO_ADDRESS,
    token_vesting=ZERO_ADDRESS,
    faucet=ZERO_ADDRESS,
)


def _build_addresses() -> dict[SupportedChainId, ProtocolAddresses]:
    anvil = ProtocolAddresses(
        **vars(_abyss_infrastructure_from_env(_ZERO_ABYSS_INFRASTRUCTURE)),
        **vars(_launch_application_from_env(_ZERO_LAUNCH_APPLICATION)),
        lending_pool=_env_addr("LENDING_POOL"),
        addresses_provider=_env_addr("ADDRESSES_PROVIDER"),
        data_provider=_env_addr("DATA_PROVIDER"),
        ui_pool_data_provider=_env_addr("UI_POOL_DATA_PROVIDER"),
        wallet_balance_provider=_env_addr("WALLET_BALANCE_PROVIDER"),
        aave_oracle=_env_addr("AAVE_ORACLE"),
        liquidation_executor=_env_addr("LIQUIDATION_EXECUTOR"),
        protocol_vault=_env_addr("PROTOCOL_VAULT"),
        eth_usd_feed=_env_addr("ETH_USD_FEED"),
        weth=_env_addr("WETH"),
        weth_gateway=_env_addr("WETH_GATEWAY"),
        lens=_env_addr("LENS"),
        token_vesting=_env_addr("TOKEN_VESTING"),
        faucet=_env_addr("FAUCET"),
    )

    # Captured replacement launch defaults; environment variables may override them.
    mainnet = ProtocolAddresses(
        **vars(ROBINHOOD_ABYSS_INFRASTRUCTURE),
        **vars(_launch_application_from_env(ROBINHOOD_LAUNCH_APPLICATION)),
        lending_pool="0x5b8F732A4F7a62D642070bb49255d6C434A76766",
        addresses_provider="0xaaD329d0Da03C8c00E8460b5b208D0A21A48C9df",
        data_provider="0x3B097A7899DF433552B8428E774964661b53C193",
        ui_pool_data_provider="0x2F35A64c7E7cBc0c05A1A1C3e2F3952E103fD5b8",
        wallet_balance_provider="0x833BB152212DD5d2d6C7b0501aB38efb9602AD8B",
        aave_oracle="0xb9441f8D3Eda65Ac7cbb6b542b5345c98067e5Fb",
        liquidation_executor=_env_addr("LIQUIDATION_EXECUTOR"),
        protocol_vault="0x961981916AB6575C3af3eeCecbf6A3b7Fad7C9e1",
        eth_usd_feed=ROBINHOOD_ETH_USD,
        weth=ROBINHOOD_WETH,
        weth_gateway="0xAbc9E3B20e8773536BDCa5ba28C619762C8e0570",
        lens="0x82FA6e601F48d64b4f163Eb47D6001f2d5040f9B",
        token_vesting=ZERO_ADDRESS,
        faucet=ZERO_ADDRESS,
    )

    workbench = replace(
        _WORKBENCH_DEFAULTS,
        **vars(_abyss_infrastructure_from_env(ROBINHOOD_ABYSS_INFRASTRUCTURE)),
        **vars(_launch_application_from_env(_ZERO_LAUNCH_APPLICATION)),
        lending_pool=_env_addr_public("LENDING_POOL", _WORKBENCH_DEFAULTS.lending_pool),
        addresses_provider=_env_addr_public("ADDRESSES_PROVIDER", _WORKBENCH_DEFAULTS.addresses_provider),
        data_provider=_env_addr_public("DATA_PROVIDER", _WORKBENCH_DEFAULTS.data_provider),
        ui_pool_data_provider=_env_addr_public("UI_POOL_DATA_PROVIDER", _WORKBENCH_DEFAULTS.ui_pool_data_provider),
        wallet_balance_provider=_env_addr_public("WALLET_BALANCE_PROVIDER", _WORKBENCH_DEFAULTS.wallet_balance_provider),
        aave_oracle=_env_addr_public("AAVE_ORACLE", _WORKBENCH_DEFAULTS.aave_oracle),
        liquidation_executor=_env_addr(
            "LIQUIDATION_EXECUTOR", _WORKBENCH_DEFAULTS.liquidation_executor
        ),
        protocol_vault=_env_addr_public("PROTOCOL_VAULT", _WORKBENCH_DEFAULTS.protocol_vault),
        eth_usd_feed=_env_addr_public("ETH_USD_FEED", _WORKBENCH_DEFAULTS.eth_usd_feed),
        weth=_env_addr_public("WETH", _WORKBENCH_DEFAULTS.weth),
        weth_gateway=_env_addr_public("WETH_GATEWAY", _WORKBENCH_DEFAULTS.weth_gateway),
        lens=_env_addr_public("LENS", _WORKBENCH_DEFAULTS.lens),
        token_vesting=_env_addr_public("TOKEN_VESTING", _WORKBENCH_DEFAULTS.token_vesting),
        faucet=_env_addr_public("FAUCET", _WORKBENCH_DEFAULTS.faucet),
    )

    return {
        ANVIL_LOCAL_CHAIN_ID: anvil,
        ROBINHOOD_MAINNET_CHAIN_ID: mainnet,
        WORKBENCH_CHAIN_ID: workbench,
    }


ADDRESSES: dict[SupportedChainId, ProtocolAddresses] = _build_addresses()


def _build_unified_launch_addresses() -> dict[SupportedChainId, UnifiedLaunchAddresses]:
    return {
        ANVIL_LOCAL_CHAIN_ID: _unified_launch_from_env(_ZERO_UNIFIED_LAUNCH_ADDRESSES),
        ROBINHOOD_MAINNET_CHAIN_ID: _unified_launch_from_env(
            ROBINHOOD_UNIFIED_LAUNCH_ADDRESSES
        ),
        WORKBENCH_CHAIN_ID: _unified_launch_from_env(_ZERO_UNIFIED_LAUNCH_ADDRESSES),
    }


UNIFIED_LAUNCH_ADDRESSES: dict[SupportedChainId, UnifiedLaunchAddresses] = (
    _build_unified_launch_addresses()
)


def get_addresses(chain_id: SupportedChainId = WORKBENCH_CHAIN_ID) -> ProtocolAddresses:
    return ADDRESSES[chain_id]


def get_unified_launch_addresses(
    chain_id: SupportedChainId = WORKBENCH_CHAIN_ID,
) -> UnifiedLaunchAddresses:
    """Return verified UnifiedLauncher routes for ``chain_id``.

    Undeployed default chains deliberately contain zero addresses rather than
    silently borrowing a route from mainnet.
    """

    return UNIFIED_LAUNCH_ADDRESSES[chain_id]


def is_supported_chain_id(chain_id: int) -> bool:
    return chain_id in SUPPORTED_CHAIN_IDS
