"""Fixed-point math and display helpers (RAY = 1e27, WAD = 1e18)."""

from __future__ import annotations

import math
from typing import Optional

RAY = 10**27
WAD = 10**18
BPS = 10_000


def ray_to_percent(ray: int, digits: int = 2) -> float:
    if ray == 0:
        return 0.0
    scaled = (ray * 10_000) // RAY
    return scaled / 100 / 100


def ray_apr_to_apy_percent(ray: int) -> float:
    """Convert an Aave liquidity/variable borrow rate (ray) to an APY % display number.

    Simple APR display (not compounded) — matches most Aave UI rate tooltips at a glance.
    """
    return (ray * 10_000) // RAY / 100


def format_health_factor(hf_wad: int) -> str:
    if hf_wad == 0:
        return "—"
    value = hf_wad / 1e18
    if not math.isfinite(value) or value > 1e6:
        return "∞"
    return f"{value:.2f}"


def health_factor_status(hf_wad: int) -> str:
    """One of "safe" | "watch" | "danger" | "none"."""
    if hf_wad == 0:
        return "none"
    value = hf_wad / 1e18
    if not math.isfinite(value) or value > 1e6:
        return "safe"
    if value < 1.05:
        return "danger"
    if value < 1.5:
        return "watch"
    return "safe"


def parse_health_factor_to_wad(raw: str) -> Optional[int]:
    """Parse a decimal health factor ("1.25") to wad."""
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        n = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(n) or n <= 0:
        return None
    whole, _, frac = cleaned.partition(".")
    frac_padded = (frac + "0" * 18)[:18]
    return int(whole or "0") * WAD + int(frac_padded or "0")


def parse_amount_to_units(amount: str, decimals: int) -> int:
    cleaned = amount.strip()
    if not cleaned or cleaned == ".":
        return 0
    whole, _, frac = cleaned.partition(".")
    frac_padded = (frac + "0" * decimals)[:decimals]
    whole_part = int(whole or "0") * 10**decimals
    frac_part = int(frac_padded or "0")
    return whole_part + frac_part


def format_units_display(value: int, decimals: int, digits: int = 4) -> str:
    if value == 0:
        return "0"
    neg = value < 0
    v = -value if neg else value
    base = 10**decimals
    whole, frac = divmod(v, base)
    frac_str = str(frac).rjust(decimals, "0")[:digits].rstrip("0")
    body = f"{whole}.{frac_str}" if frac_str else str(whole)
    return f"-{body}" if neg else body


def utilization_from_reserve(available_liquidity: int, total_debt: int) -> float:
    total = available_liquidity + total_debt
    if total == 0:
        return 0.0
    return (total_debt * 10_000) // total / 100
