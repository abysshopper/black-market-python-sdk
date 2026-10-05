"""Chain definitions and canonical Black Market deployment addresses.

Overrides are evaluated once at module import. Launch infrastructure is configured
only for mainnet; every lifecycle plan and signature still names its orchestrator explicitly.
Abyss infrastructure overrides apply to ``31337`` and ``46631`` only; mainnet
``4663`` remains canonical. Lending overrides apply to ``31337`` with bare,
``VITE_``, then ``NEXT_PUBLIC_`` precedence and to ``46631`` with public prefixes only.
The mainnet liquidation executor is the sole lending-field exception and supports all three
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
class LaunchInfrastructureAddresses:
    """Current lifecycle infrastructure; does not supply plan or signing defaults."""

    orchestrator: str
    registry: str
    fee_owner_registry: str
    admin: str
    directory: str
    fee_hub_factory: str
    token_factory: str
    erc20_deployer: str
    erc404_deployer: str
    staking_deployer: str
    funding_escrow: str
    validator: str
    lens: str
    pool_manager: str
    oracle_factory: str
    custody: str
    hook_deployer: str
    collector_deployer: str
    collector_factory: str
    pool_market_adapter: str
    abyss_market_adapter: str
    abyss_source_factory: str
    wrapped_native: str


ROBINHOOD_LAUNCH_INFRASTRUCTURE = LaunchInfrastructureAddresses(
    orchestrator="0xb75CBD17b9aecb7305B4DFcDa69595F783341c0E",
    registry="0xaa8a410709B79cBA6F118F1be1FF568877A3B8Ee",
    fee_owner_registry="0x15778Aad08e12D458B2848F035860e2a8c2a0725",
    admin="0x119bB08D90753C5fa06C8f68cEb897af157D6416",
    directory="0xa9d0F9Ff93AF569B7C11b5cDCE1E98D5a59F52DC",
    fee_hub_factory="0x5c1FFa0F9fEB3f3f2704b752397f36f00072d252",
    token_factory="0xa7467624E5A7a962f49a341Aa3fCE3eD20257b67",
    erc20_deployer="0xA54Cf521d2566F030129a0DDEFff13366A049a4C",
    erc404_deployer="0xba01E3Ce83F7192C03067e0A6f05050F0183FdFF",
    staking_deployer="0x187E8204C1eFf3F20e254ceb6C6328C6eABb838e",
    funding_escrow="0x13be9F6C4087d7eA3937c49bc174793162716312",
    validator="0xb1e32359C4234993B0B1D245FFeF86688BaaaDC7",
    lens="0x50bdA6937e3753665637538d62A8995b12a86A06",
    pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
    oracle_factory="0xe7feF2BC860B25bbdEB6F6AB96d88bAAa77ddad7",
    custody="0xF0E5e79d0BC8243c1B0Afe031A39A8EAC5627f8b",
    hook_deployer="0x9Cd433644237E6925deB13F3196cb44843de2DF6",
    collector_deployer="0xb2A46C9C21d207455258E78ec448a307D6ea1E92",
    collector_factory="0xf444dC28Aa7F6B3a8Ad5B7B3975d05a7A1fDF409",
    pool_market_adapter="0xd9A7f35F2251dDa2506E50fCab2e52E6CE7a37d9",
    abyss_market_adapter="0xeF509c6c9049D3260D5a90a56475d33f5BB47827",
    abyss_source_factory="0x123669D891133B2FdB24e5613702530AA9be3E82",
    wrapped_native=ROBINHOOD_WETH,
)

LAUNCH_ADDRESSES: dict[SupportedChainId, LaunchInfrastructureAddresses] = {
    ROBINHOOD_MAINNET_CHAIN_ID: ROBINHOOD_LAUNCH_INFRASTRUCTURE,
}


def get_launch_addresses(chain_id: SupportedChainId) -> LaunchInfrastructureAddresses:
    """Select configured infrastructure explicitly; unconfigured chains raise ``KeyError``."""
    return LAUNCH_ADDRESSES[chain_id]


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
class ProtocolAddresses(AbyssInfrastructureAddresses):
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



def _abyss_infrastructure_from_env(
    fallback: AbyssInfrastructureAddresses,
) -> AbyssInfrastructureAddresses:
    return AbyssInfrastructureAddresses(
        **{
            field: _env_addr(name, getattr(fallback, field))
            for field, name in _ABYSS_ENV_KEYS.items()
        }
    )




_ZERO_ABYSS_INFRASTRUCTURE = AbyssInfrastructureAddresses(**{f: ZERO_ADDRESS for f in _ABYSS_ENV_KEYS})


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

#: Workbench deployment snapshot — override via env when redeploying.
_WORKBENCH_DEFAULTS = ProtocolAddresses(
    **vars(ROBINHOOD_ABYSS_INFRASTRUCTURE),
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

    mainnet = ProtocolAddresses(
        **vars(ROBINHOOD_ABYSS_INFRASTRUCTURE),
        lending_pool="0x5D8878b145904425C598f12EB8eD550985369a82",
        addresses_provider="0x892faB533E8D04135D902F94974e45dB48C17697",
        data_provider="0x1f3faA42C1D5cC330f6BD0242B9a56d611bdC78a",
        ui_pool_data_provider="0x02D2CA3bBbBaBD3C25bEDD4bE0eE6E5885C4D152",
        wallet_balance_provider="0xBbb5D81123C3d514456974e9Fe6C7C8d7a0E4E2A",
        aave_oracle="0x6837B3cF5d959d01e07bf6DaB53f562877BF7d53",
        liquidation_executor=_env_addr("LIQUIDATION_EXECUTOR"),
        protocol_vault="0x83Ec5DbFEd6d972be89df88d3654EA2c70Fa2FB3",
        eth_usd_feed=ROBINHOOD_ETH_USD,
        weth=ROBINHOOD_WETH,
        weth_gateway="0xa16aB7646267327cB26dD3533526309cDe676d9d",
        lens="0xB56079f966597CB9edaE27c589F8190f4dCD12df",
        token_vesting=ZERO_ADDRESS,
        faucet=ZERO_ADDRESS,
    )

    workbench = replace(
        _WORKBENCH_DEFAULTS,
        **vars(_abyss_infrastructure_from_env(ROBINHOOD_ABYSS_INFRASTRUCTURE)),
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




def get_addresses(chain_id: SupportedChainId = WORKBENCH_CHAIN_ID) -> ProtocolAddresses:
    return ADDRESSES[chain_id]




def is_supported_chain_id(chain_id: int) -> bool:
    return chain_id in SUPPORTED_CHAIN_IDS
