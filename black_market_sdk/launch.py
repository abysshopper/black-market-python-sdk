"""Exact TickMath utilities shared by Abyss and V4 DEX consumers."""

from __future__ import annotations

__all__ = ["get_sqrt_ratio_at_tick", "get_tick_at_sqrt_ratio"]

_Q32 = 1 << 32
_MAX_UINT256 = (1 << 256) - 1
_MIN_TICK = -887_272
_MAX_TICK = 887_272
_MIN_SQRT_RATIO = 4_295_128_739
_MAX_SQRT_RATIO = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342

# Canonical TickMath multipliers, matching contracts/src/oracles/TickMathLib.sol.
_TICK_MULTIPLIERS = (
    0xFFFCB933BD6FAD37AA2D162D1A594001,
    0xFFF97272373D413259A46990580E213A,
    0xFFF2E50F5F656932EF12357CF3C7FDCC,
    0xFFE5CACA7E10E4E61C3624EAA0941CD0,
    0xFFCB9843D60F6159C9DB58835C926644,
    0xFF973B41FA98C081472E6896DFB254C0,
    0xFF2EA16466C96A3843EC78B326B52861,
    0xFE5DEE046A99A2A811C461F1969C3053,
    0xFCBE86C7900A88AEDCFFC83B479AA3A4,
    0xF987A7253AC413176F2B074CF7815E54,
    0xF3392B0822B70005940C7A398E4B70F3,
    0xE7159475A2C29B7443B29C7FA6E889D9,
    0xD097F3BDFD2022B8845AD8F792AA5825,
    0xA9F746462D870FDF8A65DC1F90E061E5,
    0x70D869A156D2A1B890BB3DF62BAF32F7,
    0x31BE135F97D08FD981231505542FCFA6,
    0x9AA508B5B7A84E1C677DE54F3E99BC9,
    0x5D6AF8DEDB81196699C329225EE604,
    0x2216E584F5FA1EA926041BEDFE98,
    0x48A170391F7DC42444E8FA2,
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    if isinstance(tick, bool) or not isinstance(tick, int) or not _MIN_TICK <= tick <= _MAX_TICK:
        raise ValueError("Tick is outside the canonical TickMath range")
    absolute_tick = abs(tick)
    ratio = _TICK_MULTIPLIERS[0] if absolute_tick & 1 else 0x100000000000000000000000000000000
    for bit in range(1, len(_TICK_MULTIPLIERS)):
        if absolute_tick & (1 << bit):
            ratio = (ratio * _TICK_MULTIPLIERS[bit]) >> 128
    if tick > 0:
        ratio = _MAX_UINT256 // ratio
    return (ratio >> 32) + (0 if ratio % _Q32 == 0 else 1)


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    if isinstance(sqrt_price_x96, bool) or not isinstance(sqrt_price_x96, int) or not _MIN_SQRT_RATIO <= sqrt_price_x96 < _MAX_SQRT_RATIO:
        raise ValueError("sqrt_price_x96 is outside the canonical swap price bounds")
    low, high = _MIN_TICK, _MAX_TICK
    while low < high:
        middle = (low + high + 1) // 2
        if get_sqrt_ratio_at_tick(middle) <= sqrt_price_x96:
            low = middle
        else:
            high = middle - 1
    return low
