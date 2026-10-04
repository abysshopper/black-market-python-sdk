"""DEX/lending override precedence and chain-isolation boundaries."""

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
