"""Live reserve/user-position normalization tests (offline, no RPC)."""

from dataclasses import dataclass

import pytest

from black_market_sdk import (
    MOCK_ETH_ADDRESS,
    RAY,
    index_by_token,
    index_wallet_balances,
    live_reserve_from_lens,
    live_reserve_from_pdp,
    live_reserve_from_ui,
    live_user_from_lens,
    live_user_from_pdp,
    live_user_from_ui,
    overlay_catalog_with_live,
    ray_mul,
    tokens_to_number,
    usd18_to_number,
    wallet_map_from_lens,
)

TOKEN = "0x2000000000000000000000000000000000000000"
A_TOKEN = "0x3000000000000000000000000000000000000000"
STABLE_DEBT = "0x4000000000000000000000000000000000000000"
VARIABLE_DEBT = "0x5000000000000000000000000000000000000000"

PRICE_USD18 = 2_500 * 10**18  # $2,500
FIVE_PCT_RAY = 5 * RAY // 100


def _ui_row(**overrides):
    row = {
        "underlyingAsset": TOKEN,
        "symbol": "WETH",
        "name": "Wrapped Ether",
        "decimals": 18,
        "priceInUsd": PRICE_USD18,
        "liquidityRate": FIVE_PCT_RAY,
        "variableBorrowRate": 8 * RAY // 100,
        "availableLiquidity": 100 * 10**18,
        "totalPrincipalStableDebt": 0,
        "totalScaledVariableDebt": 40 * 10**18,
        "variableBorrowIndex": RAY,
        "liquidityIndex": RAY,
        "supplyCap": 1_000,
        "borrowCap": 500,
        "isActive": True,
        "isFrozen": False,
        "borrowingEnabled": True,
        "stableBorrowRateEnabled": True,
        "usageAsCollateralEnabled": True,
        "baseLTVasCollateral": 8_000,
        "reserveLiquidationThreshold": 8_500,
        "reserveLiquidationBonus": 10_500,
        "reserveFactor": 1_000,
    }
    row.update(overrides)
    return row


def test_ray_mul_rounds_half_up():
    assert ray_mul(RAY, RAY) == RAY
    assert ray_mul(2 * RAY, RAY // 2) == RAY
    # Half-ray remainder rounds up.
    assert ray_mul(1, RAY // 2 + 1) == 1
    assert ray_mul(1, RAY // 2 - 1) == 0


def test_number_conversions():
    assert usd18_to_number(0) == 0.0
    assert usd18_to_number(PRICE_USD18) == 2_500.0
    assert tokens_to_number(0, 18) == 0.0
    assert tokens_to_number(15 * 10**17, 18) == 1.5
    assert tokens_to_number(2_000_000, 6) == 2.0


def test_live_reserve_from_ui_normalizes_totals_and_apy():
    reserve = live_reserve_from_ui(_ui_row())
    assert reserve.token == TOKEN
    assert reserve.total_borrow == 40 * 10**18
    assert reserve.total_supply == 140 * 10**18
    assert reserve.supply_apy == pytest.approx(5.0)
    assert reserve.variable_borrow_apy == pytest.approx(8.0)
    assert reserve.price_usd == 2_500.0
    assert reserve.total_supply_usd == pytest.approx(140 * 2_500.0)
    assert reserve.supply_cap_usd == pytest.approx(1_000 * 2_500.0)
    assert reserve.borrowing_enabled is True
    assert reserve.ltv_bps == 8_000


def test_live_reserve_from_ui_freeze_disables_borrowing():
    frozen = live_reserve_from_ui(_ui_row(isFrozen=True))
    assert frozen.borrowing_enabled is False
    assert frozen.stable_borrow_enabled is False
    inactive = live_reserve_from_ui(_ui_row(isActive=False))
    assert inactive.borrowing_enabled is False


def test_live_user_from_ui_scales_balances():
    reserve = live_reserve_from_ui(_ui_row())
    user = live_user_from_ui(
        {
            "underlyingAsset": TOKEN,
            "scaledATokenBalance": 10 * 10**18,
            "scaledVariableDebt": 2 * 10**18,
            "principalStableDebt": 10**18,
            "usageAsCollateralEnabledOnUser": True,
        },
        reserve,
    )
    assert user.supplied_raw == 10 * 10**18
    assert user.borrowed_raw == 3 * 10**18
    assert user.supplied == pytest.approx(10.0)
    assert user.borrowed == pytest.approx(3.0)
    assert user.is_collateral is True


def test_live_reserve_from_lens_normalizes_rows():
    reserve = live_reserve_from_lens(
        {
            "token": TOKEN,
            "decimals": 18,
            "priceUsd": PRICE_USD18,
            "collateralPriceUsd": PRICE_USD18,
            "debtPriceUsd": 10**18,
            "liquidityRate": FIVE_PCT_RAY,
            "variableBorrowRate": 8 * RAY // 100,
            "availableLiquidity": 100 * 10**18,
            "totalStableDebt": 10 * 10**18,
            "totalVariableDebt": 30 * 10**18,
            "supplyCap": 1_000,
            "borrowCap": 500,
            "isActive": True,
            "isFrozen": False,
            "isIsolated": False,
            "borrowableInIsolation": False,
            "debtCeiling": 0,
            "isolationModeTotalDebt": 0,
            "borrowingEnabled": True,
            "stableBorrowEnabled": True,
            "collateralEnabled": True,
            "ltvBps": 8_000,
            "liquidationThresholdBps": 8_500,
            "liquidationBonusBps": 10_500,
            "reserveFactorBps": 1_000,
            "aToken": A_TOKEN,
            "stableDebtToken": STABLE_DEBT,
            "variableDebtToken": VARIABLE_DEBT,
        }
    )
    assert reserve.total_borrow == 40 * 10**18
    assert reserve.total_supply == 140 * 10**18
    assert reserve.a_token == A_TOKEN
    assert reserve.variable_debt_token == VARIABLE_DEBT
    assert reserve.debt_ceiling_usd == 0.0
    assert reserve.borrowing_enabled is True


def test_live_user_from_lens_passthrough():
    user = live_user_from_lens(
        {"token": TOKEN, "supplied": 5 * 10**18, "borrowed": 10**18, "isCollateral": False},
        decimals=18,
    )
    assert user.supplied_raw == 5 * 10**18
    assert user.borrowed_raw == 10**18
    assert user.supplied == pytest.approx(5.0)
    assert user.borrowed == pytest.approx(1.0)
    assert user.is_collateral is False


def test_live_reserve_from_pdp_defaults_name_to_symbol():
    reserve = live_reserve_from_pdp(
        token=TOKEN,
        symbol="USDG",
        decimals=6,
        price_usd18=10**18,
        available_liquidity=1_000_000_000,
        total_stable_debt=0,
        total_variable_debt=250_000_000,
        liquidity_rate=FIVE_PCT_RAY,
        variable_borrow_rate=8 * RAY // 100,
    )
    assert reserve.name == "USDG"
    assert reserve.total_supply == 1_250_000_000
    assert reserve.total_borrow == 250_000_000
    assert reserve.supply_cap_usd is None
    assert reserve.borrow_cap_usd is None
    assert reserve.supply_cap == 0


def test_live_user_from_pdp_sums_debts():
    user = live_user_from_pdp(
        token=TOKEN,
        decimals=18,
        current_a_token_balance=7 * 10**18,
        current_stable_debt=10**18,
        current_variable_debt=2 * 10**18,
        usage_as_collateral_enabled=True,
    )
    assert user.supplied_raw == 7 * 10**18
    assert user.borrowed_raw == 3 * 10**18
    assert user.borrowed == pytest.approx(3.0)


def test_index_by_token_lowercases_keys():
    rows = [{"token": "0xABC"}, {"token": "0xdef"}]
    indexed = index_by_token(rows)
    assert indexed["0xabc"] is rows[0]
    assert indexed["0xdef"] is rows[1]


def test_wallet_map_from_lens_uses_mock_eth_sentinel():
    assert wallet_map_from_lens(42) == {MOCK_ETH_ADDRESS.lower(): 42}


def test_index_wallet_balances_lowercases_tokens():
    balances = index_wallet_balances(["0xABC", "0xDEF"], [1, 2])
    assert balances == {"0xabc": 1, "0xdef": 2}


@dataclass(frozen=True)
class _CatalogRow:
    price_usd: float
    supply_apy: float
    variable_borrow_apy: float
    total_supply_usd: float
    total_borrow_usd: float
    supply_cap_usd: float
    borrow_cap_usd: float
    borrowing_enabled: bool
    stable_borrow_enabled: bool
    collateral_enabled: bool
    ltv_bps: int
    liquidation_threshold_bps: int
    liquidation_bonus_bps: int
    reserve_factor_bps: int


def test_overlay_catalog_with_live_none_is_identity():
    catalog = {"price_usd": 1.0}
    assert overlay_catalog_with_live(catalog, None) is catalog


def test_overlay_catalog_with_live_updates_dataclass():
    catalog = _CatalogRow(
        price_usd=0.0,
        supply_apy=0.0,
        variable_borrow_apy=0.0,
        total_supply_usd=0.0,
        total_borrow_usd=0.0,
        supply_cap_usd=0.0,
        borrow_cap_usd=0.0,
        borrowing_enabled=False,
        stable_borrow_enabled=False,
        collateral_enabled=False,
        ltv_bps=0,
        liquidation_threshold_bps=0,
        liquidation_bonus_bps=0,
        reserve_factor_bps=0,
    )
    live = live_reserve_from_ui(_ui_row())
    updated = overlay_catalog_with_live(catalog, live)
    assert updated.price_usd == 2_500.0
    assert updated.supply_apy == pytest.approx(5.0)
    assert updated.ltv_bps == 8_000
    assert updated.borrowing_enabled is True
    # Original untouched (frozen dataclass).
    assert catalog.price_usd == 0.0


def test_overlay_catalog_with_live_updates_mapping():
    catalog = {"price_usd": 0.0, "supply_apy": 0.0, "variable_borrow_apy": 0.0,
               "total_supply_usd": 0.0, "total_borrow_usd": 0.0, "supply_cap_usd": 0.0,
               "borrow_cap_usd": 0.0, "borrowing_enabled": False, "stable_borrow_enabled": False,
               "collateral_enabled": False, "ltv_bps": 0, "liquidation_threshold_bps": 0,
               "liquidation_bonus_bps": 0, "reserve_factor_bps": 0}
    live = live_reserve_from_ui(_ui_row())
    updated = overlay_catalog_with_live(catalog, live)
    assert updated["price_usd"] == 2_500.0
    assert updated["liquidation_threshold_bps"] == 8_500
