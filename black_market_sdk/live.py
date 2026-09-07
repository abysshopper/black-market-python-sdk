"""Reserve and user-position normalization from lens & data-provider rows.

Ported from the canonical TypeScript SDK (@black-market/sdk, src/live.ts).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence, TypeVar

from .format import RAY, ray_apr_to_apy_percent

#: Native ETH sentinel used by `WalletBalanceProvider`.
MOCK_ETH_ADDRESS = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

MARKET_REFERENCE_CURRENCY_UNIT = 10**18


def ray_mul(a: int, b: int) -> int:
    return (a * b + RAY // 2) // RAY


def usd18_to_number(price: int) -> float:
    if price == 0:
        return 0.0
    return price / 1e18


def tokens_to_number(amount: int, decimals: int) -> float:
    if amount == 0:
        return 0.0
    return amount / 10**decimals


@dataclass(frozen=True)
class LiveReserve:
    token: str
    symbol: str
    name: str
    is_active: bool
    decimals: int
    price_usd: float
    supply_apy: float
    variable_borrow_apy: float
    total_supply_usd: float
    total_borrow_usd: float
    total_supply: int
    total_borrow: int
    supply_cap: int
    borrow_cap: int
    available_liquidity: int
    liquidity_index: int
    variable_borrow_index: int
    collateral_price_usd: Optional[float] = None
    debt_price_usd: Optional[float] = None
    supply_cap_usd: Optional[float] = None
    borrow_cap_usd: Optional[float] = None
    is_frozen: Optional[bool] = None
    is_isolated: Optional[bool] = None
    borrowable_in_isolation: Optional[bool] = None
    debt_ceiling_usd: Optional[float] = None
    isolation_mode_total_debt_usd: Optional[float] = None
    borrowing_enabled: Optional[bool] = None
    stable_borrow_enabled: Optional[bool] = None
    collateral_enabled: Optional[bool] = None
    ltv_bps: Optional[int] = None
    liquidation_threshold_bps: Optional[int] = None
    liquidation_bonus_bps: Optional[int] = None
    reserve_factor_bps: Optional[int] = None
    a_token: Optional[str] = None
    stable_debt_token: Optional[str] = None
    variable_debt_token: Optional[str] = None


@dataclass(frozen=True)
class LiveUserReserve:
    token: str
    supplied: float
    borrowed: float
    is_collateral: bool
    supplied_raw: int
    borrowed_raw: int


def _get(row: Any, name: str) -> Any:
    """Read a field from a dataclass, dict, or web3 AttributeDict row."""
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def index_by_token(rows: Sequence[Any]) -> dict[str, Any]:
    """Index helper rows by underlying token. The pool reserve list is the listing set."""
    return {_get(row, "token").lower(): row for row in rows}


def live_reserve_from_ui(row: Any) -> LiveReserve:
    decimals = int(_get(row, "decimals"))
    variable_debt = ray_mul(
        int(_get(row, "totalScaledVariableDebt")), int(_get(row, "variableBorrowIndex"))
    )
    total_borrow = int(_get(row, "totalPrincipalStableDebt")) + variable_debt
    available_liquidity = int(_get(row, "availableLiquidity"))
    total_supply = available_liquidity + total_borrow
    price_usd = usd18_to_number(int(_get(row, "priceInUsd")))
    is_active = bool(_get(row, "isActive"))
    is_frozen = bool(_get(row, "isFrozen"))
    return LiveReserve(
        token=_get(row, "underlyingAsset"),
        symbol=_get(row, "symbol"),
        name=_get(row, "name"),
        is_active=is_active,
        decimals=decimals,
        price_usd=price_usd,
        supply_apy=ray_apr_to_apy_percent(int(_get(row, "liquidityRate"))),
        variable_borrow_apy=ray_apr_to_apy_percent(int(_get(row, "variableBorrowRate"))),
        total_supply_usd=tokens_to_number(total_supply, decimals) * price_usd,
        total_borrow_usd=tokens_to_number(total_borrow, decimals) * price_usd,
        supply_cap_usd=int(_get(row, "supplyCap")) * price_usd,
        borrow_cap_usd=int(_get(row, "borrowCap")) * price_usd,
        total_supply=total_supply,
        total_borrow=total_borrow,
        supply_cap=int(_get(row, "supplyCap")),
        borrow_cap=int(_get(row, "borrowCap")),
        borrowing_enabled=bool(_get(row, "borrowingEnabled")) and is_active and not is_frozen,
        stable_borrow_enabled=bool(_get(row, "stableBorrowRateEnabled"))
        and is_active
        and not is_frozen,
        collateral_enabled=bool(_get(row, "usageAsCollateralEnabled")),
        ltv_bps=int(_get(row, "baseLTVasCollateral")),
        liquidation_threshold_bps=int(_get(row, "reserveLiquidationThreshold")),
        liquidation_bonus_bps=int(_get(row, "reserveLiquidationBonus")),
        reserve_factor_bps=int(_get(row, "reserveFactor")),
        available_liquidity=available_liquidity,
        liquidity_index=int(_get(row, "liquidityIndex")),
        variable_borrow_index=int(_get(row, "variableBorrowIndex")),
    )


def live_user_from_ui(user: Any, reserve: LiveReserve) -> LiveUserReserve:
    supplied_raw = ray_mul(int(_get(user, "scaledATokenBalance")), reserve.liquidity_index)
    variable_debt = ray_mul(int(_get(user, "scaledVariableDebt")), reserve.variable_borrow_index)
    borrowed_raw = variable_debt + int(_get(user, "principalStableDebt"))
    return LiveUserReserve(
        token=_get(user, "underlyingAsset"),
        supplied=tokens_to_number(supplied_raw, reserve.decimals),
        borrowed=tokens_to_number(borrowed_raw, reserve.decimals),
        is_collateral=bool(_get(user, "usageAsCollateralEnabledOnUser")),
        supplied_raw=supplied_raw,
        borrowed_raw=borrowed_raw,
    )


def live_reserve_from_lens(row: Any) -> LiveReserve:
    decimals = int(_get(row, "decimals"))
    total_borrow = int(_get(row, "totalStableDebt")) + int(_get(row, "totalVariableDebt"))
    available_liquidity = int(_get(row, "availableLiquidity"))
    total_supply = available_liquidity + total_borrow
    price_usd = usd18_to_number(int(_get(row, "priceUsd")))
    is_active = bool(_get(row, "isActive"))
    is_frozen = bool(_get(row, "isFrozen"))
    return LiveReserve(
        token=_get(row, "token"),
        symbol="",
        name="",
        is_active=is_active,
        decimals=decimals,
        price_usd=price_usd,
        collateral_price_usd=usd18_to_number(int(_get(row, "collateralPriceUsd"))),
        debt_price_usd=usd18_to_number(int(_get(row, "debtPriceUsd"))),
        supply_apy=ray_apr_to_apy_percent(int(_get(row, "liquidityRate"))),
        variable_borrow_apy=ray_apr_to_apy_percent(int(_get(row, "variableBorrowRate"))),
        total_supply_usd=tokens_to_number(total_supply, decimals) * price_usd,
        total_borrow_usd=tokens_to_number(total_borrow, decimals) * price_usd,
        supply_cap_usd=int(_get(row, "supplyCap")) * price_usd,
        borrow_cap_usd=int(_get(row, "borrowCap")) * price_usd,
        is_frozen=is_frozen,
        is_isolated=bool(_get(row, "isIsolated")),
        borrowable_in_isolation=bool(_get(row, "borrowableInIsolation")),
        debt_ceiling_usd=float(int(_get(row, "debtCeiling"))),
        total_supply=total_supply,
        total_borrow=total_borrow,
        supply_cap=int(_get(row, "supplyCap")),
        borrow_cap=int(_get(row, "borrowCap")),
        isolation_mode_total_debt_usd=usd18_to_number(int(_get(row, "isolationModeTotalDebt"))),
        borrowing_enabled=bool(_get(row, "borrowingEnabled")) and is_active and not is_frozen,
        stable_borrow_enabled=bool(_get(row, "stableBorrowEnabled")) and is_active and not is_frozen,
        collateral_enabled=bool(_get(row, "collateralEnabled")),
        ltv_bps=int(_get(row, "ltvBps")),
        liquidation_threshold_bps=int(_get(row, "liquidationThresholdBps")),
        liquidation_bonus_bps=int(_get(row, "liquidationBonusBps")),
        reserve_factor_bps=int(_get(row, "reserveFactorBps")),
        available_liquidity=available_liquidity,
        liquidity_index=0,
        variable_borrow_index=0,
        a_token=_get(row, "aToken"),
        stable_debt_token=_get(row, "stableDebtToken"),
        variable_debt_token=_get(row, "variableDebtToken"),
    )


def live_user_from_lens(row: Any, decimals: int) -> LiveUserReserve:
    return LiveUserReserve(
        token=_get(row, "token"),
        supplied=tokens_to_number(int(_get(row, "supplied")), decimals),
        borrowed=tokens_to_number(int(_get(row, "borrowed")), decimals),
        is_collateral=bool(_get(row, "isCollateral")),
        supplied_raw=int(_get(row, "supplied")),
        borrowed_raw=int(_get(row, "borrowed")),
    )


def live_reserve_from_pdp(
    *,
    token: str,
    symbol: str,
    decimals: int,
    price_usd18: int,
    available_liquidity: int,
    total_stable_debt: int,
    total_variable_debt: int,
    liquidity_rate: int,
    variable_borrow_rate: int,
    name: Optional[str] = None,
    is_active: bool = True,
    is_frozen: bool = False,
    borrowing_enabled: bool = True,
    stable_borrow_enabled: bool = False,
    collateral_enabled: Optional[bool] = None,
    ltv_bps: Optional[int] = None,
    liquidation_threshold_bps: Optional[int] = None,
    liquidation_bonus_bps: Optional[int] = None,
    reserve_factor_bps: Optional[int] = None,
    supply_cap_tokens: Optional[int] = None,
    borrow_cap_tokens: Optional[int] = None,
) -> LiveReserve:
    total_borrow = total_stable_debt + total_variable_debt
    total_supply = available_liquidity + total_borrow
    price_usd = usd18_to_number(price_usd18)
    return LiveReserve(
        token=token,
        symbol=symbol,
        name=name if name is not None else symbol,
        is_active=is_active,
        decimals=decimals,
        price_usd=price_usd,
        supply_apy=ray_apr_to_apy_percent(liquidity_rate),
        variable_borrow_apy=ray_apr_to_apy_percent(variable_borrow_rate),
        total_supply_usd=tokens_to_number(total_supply, decimals) * price_usd,
        total_borrow_usd=tokens_to_number(total_borrow, decimals) * price_usd,
        supply_cap_usd=(
            supply_cap_tokens * price_usd if supply_cap_tokens is not None else None
        ),
        borrow_cap_usd=(
            borrow_cap_tokens * price_usd if borrow_cap_tokens is not None else None
        ),
        borrowing_enabled=borrowing_enabled and is_active and not is_frozen,
        stable_borrow_enabled=stable_borrow_enabled and is_active and not is_frozen,
        collateral_enabled=collateral_enabled,
        ltv_bps=ltv_bps,
        liquidation_threshold_bps=liquidation_threshold_bps,
        liquidation_bonus_bps=liquidation_bonus_bps,
        reserve_factor_bps=reserve_factor_bps,
        available_liquidity=available_liquidity,
        liquidity_index=0,
        total_supply=total_supply,
        total_borrow=total_borrow,
        supply_cap=supply_cap_tokens if supply_cap_tokens is not None else 0,
        borrow_cap=borrow_cap_tokens if borrow_cap_tokens is not None else 0,
        variable_borrow_index=0,
    )


def live_user_from_pdp(
    *,
    token: str,
    decimals: int,
    current_a_token_balance: int,
    current_stable_debt: int,
    current_variable_debt: int,
    usage_as_collateral_enabled: bool,
) -> LiveUserReserve:
    return LiveUserReserve(
        token=token,
        supplied=tokens_to_number(current_a_token_balance, decimals),
        borrowed=tokens_to_number(current_stable_debt + current_variable_debt, decimals),
        is_collateral=usage_as_collateral_enabled,
        supplied_raw=current_a_token_balance,
        borrowed_raw=current_stable_debt + current_variable_debt,
    )


def overlay_catalog_with_live(catalog: Any, live: Optional[LiveReserve]) -> Any:
    """Join catalog display metadata onto a deployed reserve.

    Does not add or drop listings. Accepts a dataclass or mapping catalog row.
    """
    if live is None:
        return catalog

    updates = {
        "price_usd": live.price_usd,
        "supply_apy": live.supply_apy,
        "variable_borrow_apy": live.variable_borrow_apy,
        "total_supply_usd": live.total_supply_usd,
        "total_borrow_usd": live.total_borrow_usd,
        "supply_cap_usd": (
            live.supply_cap_usd
            if live.supply_cap_usd is not None
            else _get(catalog, "supply_cap_usd")
        ),
        "borrow_cap_usd": (
            live.borrow_cap_usd
            if live.borrow_cap_usd is not None
            else _get(catalog, "borrow_cap_usd")
        ),
        "borrowing_enabled": (
            live.borrowing_enabled
            if live.borrowing_enabled is not None
            else _get(catalog, "borrowing_enabled")
        ),
        "stable_borrow_enabled": (
            live.stable_borrow_enabled
            if live.stable_borrow_enabled is not None
            else _get(catalog, "stable_borrow_enabled")
        ),
        "collateral_enabled": (
            live.collateral_enabled
            if live.collateral_enabled is not None
            else _get(catalog, "collateral_enabled")
        ),
        "ltv_bps": live.ltv_bps if live.ltv_bps is not None else _get(catalog, "ltv_bps"),
        "liquidation_threshold_bps": (
            live.liquidation_threshold_bps
            if live.liquidation_threshold_bps is not None
            else _get(catalog, "liquidation_threshold_bps")
        ),
        "liquidation_bonus_bps": (
            live.liquidation_bonus_bps
            if live.liquidation_bonus_bps is not None
            else _get(catalog, "liquidation_bonus_bps")
        ),
        "reserve_factor_bps": (
            live.reserve_factor_bps
            if live.reserve_factor_bps is not None
            else _get(catalog, "reserve_factor_bps")
        ),
    }

    if isinstance(catalog, Mapping):
        return {**catalog, **updates}
    return replace(catalog, **updates)


def wallet_map_from_lens(native_eth: int) -> dict[str, int]:
    """Native ETH reported by the lens. ERC-20 wallet balances must be read separately."""
    return {MOCK_ETH_ADDRESS.lower(): native_eth}


def index_wallet_balances(tokens: Sequence[str], balances: Sequence[int]) -> dict[str, int]:
    return {
        token.lower(): balance
        for token, balance in zip(tokens, balances)
    }
