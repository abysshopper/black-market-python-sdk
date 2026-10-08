"""Fixed-point formatting helper tests."""

from black_market_sdk import (
    RAY,
    WAD,
    format_health_factor,
    format_units_display,
    health_factor_status,
    parse_amount_to_units,
    parse_health_factor_to_wad,
    ray_apr_to_apy_percent,
    utilization_from_reserve,
)


def test_ray_apr_to_apy_percent():
    assert ray_apr_to_apy_percent(0) == 0
    # 5% APR in ray.
    assert ray_apr_to_apy_percent(5 * RAY // 100) == 5


def test_health_factor_formatting_and_status():
    assert format_health_factor(0) == "—"
    assert format_health_factor(2 * WAD) == "2.00"
    assert format_health_factor(10**30) == "∞"
    assert health_factor_status(0) == "none"
    assert health_factor_status(WAD) == "danger"
    assert health_factor_status(12 * WAD // 10) == "watch"
    assert health_factor_status(2 * WAD) == "safe"


def test_health_factor_matches_binary_js_to_fixed_rounding():
    for wad, expected in (
        (1125000000000000000, "1.13"),
        (1625000000000000000, "1.63"),
        (-1125000000000000000, "-1.13"),
        (1124000000000000000, "1.12"),
        (1126000000000000000, "1.13"),
        (1005000000000000000, "1.00"),
        (-1005000000000000000, "-1.00"),
        (2675000000000000000, "2.67"),
        (1000000000000000000000000, "1000000.00"),
        ((1 << 256) - 1, "∞"),
    ):
        assert format_health_factor(wad) == expected


def test_parse_health_factor_to_wad():
    assert parse_health_factor_to_wad("") is None
    assert parse_health_factor_to_wad("0") is None
    assert parse_health_factor_to_wad("-1") is None
    assert parse_health_factor_to_wad("1.25") == WAD + WAD // 4


def test_parse_amount_to_units():
    assert parse_amount_to_units("", 18) == 0
    assert parse_amount_to_units("1.5", 18) == 15 * 10**17
    assert parse_amount_to_units("2", 6) == 2_000_000


def test_format_units_display():
    assert format_units_display(0, 18) == "0"
    assert format_units_display(15 * 10**17, 18) == "1.5"
    assert format_units_display(-15 * 10**17, 18) == "-1.5"


def test_utilization_from_reserve():
    assert utilization_from_reserve(0, 0) == 0
    assert utilization_from_reserve(50, 50) == 50
