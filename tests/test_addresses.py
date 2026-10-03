"""Address book and environment-override precedence tests."""

import importlib

import pytest

import black_market_sdk.addresses as addresses_module
from black_market_sdk import addresses as _pkg_addresses  # noqa: F401  (re-export sanity)

ZERO = "0x0000000000000000000000000000000000000000"

CANONICAL_ABYSS = {
    "abyss_factory": "0xe7feF2BC860B25bbdEB6F6AB96d88bAAa77ddad7",
    "abyss_fee_vault": "0x19b04F2E86fFDf26510ACf25344c406240214d5F",
    "abyss_pool_deployer": "0xe49Ff46f2Ca543D5504cDCF533fe84e0c95eF693",
    "abyss_router": "0xF7818c69e31bf98eFF96C721B557Bb519659CD27",
    "abyss_quoter": "0xF1ff7c78605939df2d705F3E12Ac9CEB69Bcfe76",
    "abyss_position_manager": "0x1b2176d4D2C7bd36227D92Ed84F1A3aE30B635bF",
    "abyss_position_locker": "0xa0d4fA31740FA0d8fc6b5b4173Da17864Eb6e888",
    "abyss_token": "0x15f3385625D7e364C5a6216FBbceadf10fa90e7d",
    "abyss_buyback_burner_implementation": "0xd17F05C41FfdebB6b57b3e232c20a340906A0aAd",
    "abyss_buyback_burner": "0xF6C7159e967f28C65d9Fb5b919567E04540cD2FF",
    "abyss_fee_router_implementation": "0xB35b85db23fEF3A146Fbd755D76be12F9e1eBfFC",
    "abyss_fee_router": "0x2c3B1b6fe0EDa8e10C0445567b47e66E825B34cd",
}

CANONICAL_LAUNCH = {
    "launch_token_factory": "0x84225a7b7fd9981f8a3086acfbbb688348247f6f",
    "launch_coordinator": "0xcc3aa2dff0fd6e9505b12b731111ec1b7b49621d",
    "launch_factory": "0xa7a4755fb907593f05fd1e289aa780f0d57f3a12",
    "launch_template_registry": "0x01422012c452f2e363d56bd408c7ed1c44204701",
    "launch_module_factory": "0xe6bb1f77b94fa2003db0f4c2e248649061922c64",
    "launch_fee_owner_registry": "0xa8018950ebb6a35708820c89243ddab8718ee0bc",
}

HISTORICAL_ATOMIC_LAUNCH = {
    "launch_token_factory": "0x7B6F6efb4536F579223423e31d9036D996bc90F0",
    "launch_coordinator": "0xE98A82202D794A7836316971e7ACf61F260f8399",
    "launch_factory": "0xAf3FdC499b3717EBE8aD51B66bA78Cb083552351",
    "launch_template_registry": "0x5C22c6e02bAC8225eed64629447868bAA5774186",
    "launch_module_factory": "0xb2D9988592eE245a36290A0992505ca0474434f2",
    "launch_fee_owner_registry": "0xdD3756269Db20F2a60055b0FE8c101FAd12525f9",
}

CANONICAL_UNIFIED = {
    "unified_launcher": "0xa7a4755fb907593f05fd1e289aa780f0d57f3a12",
    "launch_pool_registry": "0x04f453aac720a5fb410fe81fc50b747f969b352c",
    "abyss_launch_pool_adapter_v3": "0xd0eb22fd4be5d049545e072d8e7fad72475b4cea",
    "uniswap_v4_launch_pool_adapter_v3": "0x9607ddc99381f18985770b4f93685ed90220bc98",
    "burnable_token_deployer_v2": "0xd12ed68dd35dc9f76df8fe5b99cd988430f356d1",
    "holder_dividend_token_deployer_v2": "0xe9ea8555e0e7ee68a0733f45c285e7b5558ab842",
    "uniswap_v4_pool_manager": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
    "uniswap_v4_v3_hook_deployer": "0x78e773891f66f0423789e6d06f3cf613c0296d7a",
    "uniswap_v4_v3_liquidity_locker": "0x44bb63597bff2e7cb6c7fd1716df4abd6e3420ec",
    "uniswap_v4_v3_abyss_bonus_distributor": "0xd3f171677431644aefc17a0cfc66d65b626284b3",
}

_UNIFIED_ROUTE_ENV_BASE_NAMES = (
    "LAUNCH_POOL_REGISTRY",
    "ABYSS_LAUNCH_POOL_ADAPTER_V3",
    "UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3",
    "BURNABLE_TOKEN_DEPLOYER_V2",
    "HOLDER_DIVIDEND_TOKEN_DEPLOYER_V2",
    "UNISWAP_V4_POOL_MANAGER",
    "UNISWAP_V4_V3_HOOK_DEPLOYER",
    "UNISWAP_V4_V3_LIQUIDITY_LOCKER",
    "UNISWAP_V4_V3_ABYSS_BONUS_DISTRIBUTOR",
)

ENV_KEYS = [
    "LAUNCH_FEE_OWNER_REGISTRY",
    "VITE_LAUNCH_FEE_OWNER_REGISTRY",
    "NEXT_PUBLIC_LAUNCH_FEE_OWNER_REGISTRY",
    "LAUNCH_FACTORY",
    "VITE_LAUNCH_FACTORY",
    "NEXT_PUBLIC_LAUNCH_FACTORY",
] + [
    f"{prefix}{name}"
    for name in _UNIFIED_ROUTE_ENV_BASE_NAMES
    for prefix in ("", "VITE_", "NEXT_PUBLIC_")
]


@pytest.fixture
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def reload_addresses():
    return importlib.reload(addresses_module)


def test_mainnet_defaults_match_canonical_deployment(clean_env):
    module = reload_addresses()
    mainnet = module.get_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID)
    for field, expected in CANONICAL_ABYSS.items():
        assert getattr(mainnet, field).lower() == expected.lower()
    for field, expected in CANONICAL_LAUNCH.items():
        assert getattr(mainnet, field).lower() == expected.lower()



def test_historical_atomic_record_remains_explicit_and_distinct(clean_env):
    module = reload_addresses()
    historical = module.ROBINHOOD_ATOMIC_LAUNCH_APPLICATION
    current = module.get_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID)

    for field, expected in HISTORICAL_ATOMIC_LAUNCH.items():
        assert getattr(historical, field).lower() == expected.lower()
    assert current.launch_factory.lower() != historical.launch_factory.lower()


def test_unified_mainnet_routes_match_captured_enabled_records(clean_env):
    module = reload_addresses()
    mainnet = module.get_unified_launch_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID)

    for field, expected in CANONICAL_UNIFIED.items():
        assert getattr(mainnet, field).lower() == expected.lower()
    assert mainnet.unified_launcher.lower() == module.get_addresses(
        module.ROBINHOOD_MAINNET_CHAIN_ID
    ).launch_factory.lower()
    assert all(
        value == ZERO
        for value in vars(module.get_unified_launch_addresses(module.WORKBENCH_CHAIN_ID)).values()
    )

def test_env_override_precedence(clean_env):
    bare = "0x0000000000000000000000000000000000000001"
    vite = "0x0000000000000000000000000000000000000002"
    next_public = "0x0000000000000000000000000000000000000003"

    clean_env.setenv("LAUNCH_FEE_OWNER_REGISTRY", bare)
    clean_env.setenv("VITE_LAUNCH_FEE_OWNER_REGISTRY", vite)
    clean_env.setenv("NEXT_PUBLIC_LAUNCH_FEE_OWNER_REGISTRY", next_public)
    module = reload_addresses()
    assert (
        module.get_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID).launch_fee_owner_registry == bare
    )

    clean_env.delenv("LAUNCH_FEE_OWNER_REGISTRY")
    module = reload_addresses()
    assert (
        module.get_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID).launch_fee_owner_registry == vite
    )

    clean_env.delenv("VITE_LAUNCH_FEE_OWNER_REGISTRY")
    module = reload_addresses()
    assert (
        module.get_addresses(module.ROBINHOOD_MAINNET_CHAIN_ID).launch_fee_owner_registry
        == next_public
    )

    clean_env.delenv("NEXT_PUBLIC_LAUNCH_FEE_OWNER_REGISTRY")
    module = reload_addresses()
    assert module.get_addresses(
        module.ROBINHOOD_MAINNET_CHAIN_ID
    ).launch_fee_owner_registry.lower() == CANONICAL_LAUNCH["launch_fee_owner_registry"].lower()


def test_launch_factory_override_keeps_application_and_unified_target_equal(clean_env):
    bare = "0x0000000000000000000000000000000000000001"
    vite = "0x0000000000000000000000000000000000000002"
    next_public = "0x0000000000000000000000000000000000000003"

    def targets(module):
        chain_id = module.ROBINHOOD_MAINNET_CHAIN_ID
        return (
            module.get_addresses(chain_id).launch_factory,
            module.get_unified_launch_addresses(chain_id).unified_launcher,
        )

    clean_env.setenv("LAUNCH_FACTORY", bare)
    clean_env.setenv("VITE_LAUNCH_FACTORY", vite)
    clean_env.setenv("NEXT_PUBLIC_LAUNCH_FACTORY", next_public)
    assert targets(reload_addresses()) == (bare, bare)

    clean_env.delenv("LAUNCH_FACTORY")
    assert targets(reload_addresses()) == (vite, vite)

    clean_env.delenv("VITE_LAUNCH_FACTORY")
    assert targets(reload_addresses()) == (next_public, next_public)

    # An explicit zero is a caller configuration, not a fallback to mainnet.
    clean_env.setenv("LAUNCH_FACTORY", ZERO)
    assert targets(reload_addresses()) == (ZERO, ZERO)


def test_supported_chain_ids():
    module = reload_addresses()
    assert module.is_supported_chain_id(4663)
    assert module.is_supported_chain_id(46631)
    assert module.is_supported_chain_id(31337)
    assert not module.is_supported_chain_id(1)
