"""Fixed-point math and display helpers (RAY = 1e27, WAD = 1e18)."""

from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Optional

RAY = 10**27
WAD = 10**18
BPS = 10_000

# ECMAScript whitespace is narrower than Python str.strip(), and signature-bound
# metadata and the public numeric parsers must agree on this exact boundary.
_ECMASCRIPT_TRIM_CHARACTERS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _trunc_div(numerator: int, denominator: int) -> int:
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _parse_bigint(raw: str) -> int:
    value = raw.strip(_ECMASCRIPT_TRIM_CHARACTERS)
    if not value:
        return 0
    if re.fullmatch(r"[+-]?[0-9]+", value):
        return int(value, 10)
    if re.fullmatch(r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+", value):
        return int(value, 0)
    raise ValueError("Invalid exact integer quantity")


def _numeric_value(raw: str) -> float:
    if re.fullmatch(r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+", raw):
        try:
            return float(int(raw, 0))
        except OverflowError:
            return math.inf
    if not re.fullmatch(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw):
        return math.nan
    return float(raw)


def ray_to_percent(ray: int, digits: int = 2) -> float:
    if ray == 0:
        return 0.0
    scaled = _trunc_div(ray * 10_000, RAY)
    return float(scaled) / 100 / 100


def ray_apr_to_apy_percent(ray: int) -> float:
    """Convert an Aave liquidity/variable borrow rate (ray) to an APY % display number.

    Simple APR display (not compounded) — matches most Aave UI rate tooltips at a glance.
    """
    return float(_trunc_div(ray * 10_000, RAY)) / 100


def format_health_factor(hf_wad: int) -> str:
    if hf_wad == 0:
        return "—"
    value = hf_wad / 1e18
    if not math.isfinite(value) or value > 1e6:
        return "∞"
    # JS toFixed rounds the exact binary Number, with ties away from zero.
    # Decimal(str(value)) would incorrectly round binary-adjacent values such
    # as 1.005; Python's format instead uses the wrong half-even tie rule.
    if abs(value) >= 1e21:
        return str(value).replace(".0e", "e")
    with localcontext() as context:
        context.prec = 32
        return format(Decimal.from_float(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


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
    cleaned = raw.strip(_ECMASCRIPT_TRIM_CHARACTERS)
    if not cleaned:
        return None
    n = _numeric_value(cleaned)
    if not math.isfinite(n) or n <= 0:
        return None
    parts = cleaned.split(".")
    whole, frac = parts[0], parts[1] if len(parts) > 1 else ""
    frac_padded = (frac + "0" * 18)[:18]
    return _parse_bigint(whole) * WAD + _parse_bigint(frac_padded)


def parse_amount_to_units(amount: str, decimals: int) -> int:
    cleaned = amount.strip(_ECMASCRIPT_TRIM_CHARACTERS)
    if not cleaned or cleaned == ".":
        return 0
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        raise ValueError("decimals must be a nonnegative integer")
    parts = cleaned.split(".")
    whole, frac = parts[0], parts[1] if len(parts) > 1 else ""
    frac_padded = (frac + "0" * decimals)[:decimals]
    return _parse_bigint(whole) * 10**decimals + _parse_bigint(frac_padded)


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
    return float(_trunc_div(total_debt * 10_000, total)) / 100
