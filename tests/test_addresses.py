"""Current deployment configuration, override precedence and chain isolation."""

import importlib

import pytest

import black_market_sdk.addresses as addresses_module

BARE = "0x0000000000000000000000000000000000000001"
VITE = "0x0000000000000000000000000000000000000002"
PUBLIC = "0x0000000000000000000000000000000000000003"
ZERO = "0x" + "00" * 20


@pytest.fixture
def clean_env(monkeypatch):
    with monkeypatch.context() as environment:
        for name in ("ABYSS_FACTORY", "LENDING_POOL"):
            for prefix in ("", "VITE_", "NEXT_PUBLIC_"):
                environment.delenv(prefix + name, raising=False)
        importlib.reload(addresses_module)
        yield environment
    importlib.reload(addresses_module)


def reload_addresses():
    return importlib.reload(addresses_module)


def test_dex_override_precedence_cannot_retarget_mainnet(clean_env):
    canonical = addresses_module.get_addresses(4663).abyss_factory
    for prefix, value in (("", BARE), ("VITE_", VITE), ("NEXT_PUBLIC_", PUBLIC)):
        clean_env.setenv(prefix + "ABYSS_FACTORY", value)
    for expected, removed in ((BARE, "ABYSS_FACTORY"), (VITE, "VITE_ABYSS_FACTORY"), (PUBLIC, "NEXT_PUBLIC_ABYSS_FACTORY")):
        module = reload_addresses()
        assert module.get_addresses(31337).abyss_factory == expected
        assert module.get_addresses(46631).abyss_factory == expected
        assert module.get_addresses(4663).abyss_factory == canonical
        clean_env.delenv(removed)


def test_lending_workbench_ignores_bare_local_override(clean_env):
    canonical = addresses_module.get_addresses(4663).lending_pool
    clean_env.setenv("LENDING_POOL", BARE)
    clean_env.setenv("VITE_LENDING_POOL", VITE)
    clean_env.setenv("NEXT_PUBLIC_LENDING_POOL", PUBLIC)
    module = reload_addresses()
    assert module.get_addresses(31337).lending_pool == BARE
    assert module.get_addresses(46631).lending_pool == VITE
    assert module.get_addresses(4663).lending_pool == canonical
    clean_env.delenv("VITE_LENDING_POOL")
    module = reload_addresses()
    assert module.get_addresses(46631).lending_pool == PUBLIC


def test_explicit_zero_is_not_silently_replaced_with_another_deployment(clean_env):
    clean_env.setenv("ABYSS_FACTORY", ZERO)
    clean_env.setenv("VITE_ABYSS_FACTORY", VITE)
    assert reload_addresses().get_addresses(31337).abyss_factory == ZERO


def test_unknown_chain_has_no_accidental_deployment_fallback():
    with pytest.raises(KeyError):
        addresses_module.get_addresses(1)


@pytest.mark.parametrize("chain_id", [1, 31337, 46631])
def test_unconfigured_launch_chain_has_no_mainnet_fallback(chain_id):
    from black_market_sdk import get_launch_addresses
    with pytest.raises(KeyError):
        get_launch_addresses(chain_id)


def test_local_lending_and_launch_addresses_do_not_inherit_mainnet(clean_env):
    module = reload_addresses()
    assert module.get_addresses(31337).lending_pool == ZERO
    with pytest.raises(KeyError):
        module.get_launch_addresses(31337)


def test_mainnet_launch_addresses_match_mined_october_8_manifest(clean_env):
    # contracts/deployments/launch/pool-launch-v1/20261008T000141Z-e174ce6/manifest.json
    expected = {
        "orchestrator": "0x91560876033d568d25CDe98C78c33ff8FC43962c",
        "registry": "0xB2B0f9F36617810D67b8fC175153Aa10024C1358",
        "fee_owner_registry": "0x64b5ca1f21B8E84305b0e4D847924dca39Eb1fcb",
        "admin": "0x8394716C8Ce2a6775a691d5f09b2204362A897E1",
        "directory": "0x1c7694Ed0F6624cC5794814261B1523E91986bb5",
        "fee_hub_factory": "0xF14fD8D65D7833612A4bb909F1161731bb7039d3",
        "token_factory": "0xd3cE64E49224a9a96075633f63761FE6BE22FE30",
        "erc20_deployer": "0xD05B7F46Ba8D20C3Eb1EC9CB0323fBCFA9185359",
        "erc404_deployer": "0x11224dd87831aBc4523ec667dad53bA9855557ac",
        "staking_deployer": "0x4e7FdB41225BEAd22eD913E6B40F9d59C904d29D",
        "funding_escrow": "0x7bc77946CeF52A178583FCe1EEB7751983cEC703",
        "validator": "0x1f0b968CB7c70cBB445EEc5a63B19a40ABe2BF2C",
        "lens": "0xAce93e3910561aC67c6179c3a27F133F5Ef78F25",
        "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "oracle_factory": "0xe7feF2BC860B25bbdEB6F6AB96d88bAAa77ddad7",
        "custody": "0x28B84e5B9E905493E24986C91Ef109C752505B58",
        "hook_deployer": "0x1Df787Cc099B047A606059Ac80A8296779266f9e",
        "collector_deployer": "0x1d9Aa696568F6D4b96914C7e35DEF33781826A48",
        "collector_factory": "0x43b394fE6865EE6797c60FCCcd33D7D765214b52",
        "pool_market_adapter": "0xb334b44509a5237a39cE364c159913F14b6D1186",
        "abyss_market_adapter": "0x3ef760fcbbD618Ab6cB7E308e7fB39C93b9deFD0",
        "abyss_source_factory": "0xAba6a07b31fe8DaC17FbF9691c69a6C65B95c7dB",
        "wrapped_native": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
    }
    infrastructure = addresses_module.get_launch_addresses(4663)
    assert {field: value.lower() for field, value in vars(infrastructure).items()} == {
        field: value.lower() for field, value in expected.items()
    }
    application = addresses_module.get_addresses(4663)
    assert application.launch_orchestrator.lower() == expected["orchestrator"].lower()
    assert application.launch_implementation_registry.lower() == expected["registry"].lower()
    assert application.launch_fee_owner_registry.lower() == expected["fee_owner_registry"].lower()
