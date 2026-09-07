"""Atomic launch supply constants and quote catalog tests."""

from black_market_sdk import (
    AUCTION_QUOTE_OPTIONS,
    AUCTION_SUPPLY,
    AUCTION_SUPPLY_WHOLE,
    ROBINHOOD_USDG,
    ROBINHOOD_WETH,
    AuctionQuoteOption,
)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def test_auction_supply_constants_agree():
    assert AUCTION_SUPPLY_WHOLE == 1_000_000_000
    assert AUCTION_SUPPLY == AUCTION_SUPPLY_WHOLE * 10**18


def test_quote_catalog_core_entries():
    by_id = {option.id: option for option in AUCTION_QUOTE_OPTIONS}
    # Native ETH is represented by the zero address.
    assert by_id["ETH"].address == ZERO_ADDRESS
    assert by_id["ETH"].decimals == 18
    assert by_id["ETH"].usd_peg is False
    # WETH wraps the canonical Robinhood WETH.
    assert by_id["WETH"].address == ROBINHOOD_WETH
    # USDG is the only USD-pegged, 6-decimal quote.
    assert by_id["USDG"].address == ROBINHOOD_USDG
    assert by_id["USDG"].decimals == 6
    assert by_id["USDG"].usd_peg is True
    pegged = [option.id for option in AUCTION_QUOTE_OPTIONS if option.usd_peg]
    assert pegged == ["USDG"]


def test_quote_catalog_invariants():
    ids = [option.id for option in AUCTION_QUOTE_OPTIONS]
    assert len(ids) == len(set(ids))
    # ETH, WETH, USDG, ABYSS lead the catalog, then the stock tickers.
    assert ids[:4] == ["ETH", "WETH", "USDG", "ABYSS"]
    for option in AUCTION_QUOTE_OPTIONS:
        assert isinstance(option, AuctionQuoteOption)
        if option.id != "ETH":
            assert option.address != ZERO_ADDRESS
        assert option.address.startswith("0x")
        assert len(option.address) == 42
        assert option.decimals > 0
        assert option.symbol and option.name
