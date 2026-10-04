"""Reference supply quantities and the paired-asset (quote) catalog."""

from __future__ import annotations

from dataclasses import dataclass

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

#: Reference supply; lifecycle plans commit an explicit caller-chosen supply.
AUCTION_SUPPLY = 1_000_000_000 * 10**18
AUCTION_SUPPLY_WHOLE = 1_000_000_000

ROBINHOOD_USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
ROBINHOOD_WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
ROBINHOOD_ABYSS = "0x15f3385625D7e364C5a6216FBbceadf10fa90e7d"
ROBINHOOD_AAPL = "0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9"
ROBINHOOD_AMD = "0x86923f96303D656E4aa86D9d42D1e57ad2023fdC"
ROBINHOOD_AMZN = "0x12f190a9F9d7D37a250758b26824B97CE941bF54"
ROBINHOOD_ASML = "0x47F93d52cBeC7C6D2CfC080e154002370a60dAEA"
ROBINHOOD_BABA = "0xad25Ac6C84D497db898fa1E8387bf6Af3532a1c4"
ROBINHOOD_CLSK = "0xcBB95BBF36099d34dA091dc6Fa6F49EfA257Cee3"
ROBINHOOD_COIN = "0x6330D8C3178a418788dF01a47479c0ce7CCF450b"
ROBINHOOD_CRCL = "0xdF0992E440dD0be65BD8439b609d6D4366bf1CB5"
ROBINHOOD_CRWV = "0x5f10A1C971B69e47e059e1dC91901B59b3fB49C3"
ROBINHOOD_DELL = "0x941AE714EC6D8130c7B75d67160Ca08f1e7d11Dd"
ROBINHOOD_EWY = "0x7f0aBeF0C07280F82c6a08ead09dEd6BAE2C13Fc"
ROBINHOOD_GME = "0x1b0E319c6A659F002271B69dB8A7df2F911c153E"
ROBINHOOD_GOOGL = "0x2e0847E8910a9732eB3fb1bb4b70a580ADAD4FE3"
ROBINHOOD_INTC = "0xc72b96e0E48ecd4DC75E1e45396e26300BC39681"
ROBINHOOD_IONQ = "0x558378E000D634A36593E338eBacdd6207640EfE"
ROBINHOOD_META = "0xc0D6457C16Cc70d6790Dd43521C899C87ce02f35"
ROBINHOOD_MSFT = "0xe93237C50D904957Cf27E7B1133b510C669c2e74"
ROBINHOOD_MSTR = "0xec262a75e413fAfD0dF80480274532C79D42da09"
ROBINHOOD_MU = "0xfF080c8ce2E5feadaCa0Da81314Ae59D232d4afD"
ROBINHOOD_NBIS = "0x9D9c6684F596F66a64C030B93A886D51Fd4D7931"
ROBINHOOD_NVDA = "0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC"
ROBINHOOD_ORCL = "0xb0992820E760d836549ba69BC7598b4af75dEE03"
ROBINHOOD_PLTR = "0x894E1EC2D74FFE5AEF8Dc8A9e84686acCB964F2A"
ROBINHOOD_QQQ = "0xD5f3879160bc7c32ebb4dC785F8a4F505888de68"
ROBINHOOD_RGTI = "0x284358abc07F9359f19f4b5b4aC91901Be2597Ba"
ROBINHOOD_RKLB = "0x3b14C39E89D60D627b42a1A4CA45b5bb45Fc12e2"
ROBINHOOD_SGOV = "0x92FD66527192E3e61d4DDd13322Aa222DE86F9B5"
ROBINHOOD_SLV = "0x411eFb0E7f985935DAec3D4C3ebaEa0d0AD7D89f"
ROBINHOOD_SNDK = "0xB90A19fF0Af67f7779afF50A882A9CfF42446400"
ROBINHOOD_SPCX = "0x4a0E65A3EcceC6dBe60AE065F2e7bb85Fae35eEa"
ROBINHOOD_SPY = "0x117cc2133c37B721F49dE2A7a74833232B3B4C0C"
ROBINHOOD_TSLA = "0x322F0929c4625eD5bAd873c95208D54E1c003b2d"
ROBINHOOD_TSM = "0x58FfE4a942d3885bAa22D7520691F611EF09e7AA"
ROBINHOOD_USAR = "0xd917B029C761D264c6A312BBbcDA868658eF86a6"
ROBINHOOD_USO = "0xa30FA36Db767ad9eD3f7a60fC79526fB4d56D344"


@dataclass(frozen=True)
class AuctionQuoteOption:
    id: str
    symbol: str
    #: Contract address; native ETH is represented by the zero address.
    address: str
    decimals: int
    #: Human-readable asset name used for search and display.
    name: str
    #: UI-only: treat quote as $1 when labeling opening FDV. Not an on-chain flag.
    usd_peg: bool


def _rwa(id: str, name: str, address: str) -> AuctionQuoteOption:
    return AuctionQuoteOption(
        id=id, symbol=id, name=name, address=address, decimals=18, usd_peg=False
    )


#: Paired-asset catalog: native ETH, wrapped WETH, USDG, then catalog stocks.
AUCTION_QUOTE_OPTIONS: list[AuctionQuoteOption] = [
    AuctionQuoteOption(
        id="ETH", symbol="ETH", name="Ether", address=ZERO_ADDRESS, decimals=18, usd_peg=False
    ),
    AuctionQuoteOption(
        id="WETH",
        symbol="WETH",
        name="Wrapped Ether",
        address=ROBINHOOD_WETH,
        decimals=18,
        usd_peg=False,
    ),
    AuctionQuoteOption(
        id="USDG",
        symbol="USDG",
        name="Global Dollar",
        address=ROBINHOOD_USDG,
        decimals=6,
        usd_peg=True,
    ),
    AuctionQuoteOption(
        id="ABYSS", symbol="ABYSS", name="Abyss", address=ROBINHOOD_ABYSS, decimals=18, usd_peg=False
    ),
    _rwa("AAPL", "Apple", ROBINHOOD_AAPL),
    _rwa("AMD", "Advanced Micro Devices", ROBINHOOD_AMD),
    _rwa("AMZN", "Amazon", ROBINHOOD_AMZN),
    _rwa("ASML", "ASML Holding", ROBINHOOD_ASML),
    _rwa("BABA", "Alibaba", ROBINHOOD_BABA),
    _rwa("CLSK", "CleanSpark", ROBINHOOD_CLSK),
    _rwa("COIN", "Coinbase", ROBINHOOD_COIN),
    _rwa("CRCL", "Circle", ROBINHOOD_CRCL),
    _rwa("CRWV", "CoreWeave", ROBINHOOD_CRWV),
    _rwa("DELL", "Dell", ROBINHOOD_DELL),
    _rwa("EWY", "MSCI South Korea", ROBINHOOD_EWY),
    _rwa("GME", "GameStop", ROBINHOOD_GME),
    _rwa("GOOGL", "Alphabet", ROBINHOOD_GOOGL),
    _rwa("INTC", "Intel", ROBINHOOD_INTC),
    _rwa("IONQ", "IonQ", ROBINHOOD_IONQ),
    _rwa("META", "Meta", ROBINHOOD_META),
    _rwa("MSFT", "Microsoft", ROBINHOOD_MSFT),
    _rwa("MSTR", "Strategy", ROBINHOOD_MSTR),
    _rwa("MU", "Micron", ROBINHOOD_MU),
    _rwa("NBIS", "Nebius", ROBINHOOD_NBIS),
    _rwa("NVDA", "NVIDIA", ROBINHOOD_NVDA),
    _rwa("ORCL", "Oracle", ROBINHOOD_ORCL),
    _rwa("PLTR", "Palantir", ROBINHOOD_PLTR),
    _rwa("QQQ", "Nasdaq-100", ROBINHOOD_QQQ),
    _rwa("RGTI", "Rigetti", ROBINHOOD_RGTI),
    _rwa("RKLB", "Rocket Lab", ROBINHOOD_RKLB),
    _rwa("SGOV", "T-Bills", ROBINHOOD_SGOV),
    _rwa("SLV", "Silver", ROBINHOOD_SLV),
    _rwa("SNDK", "Sandisk", ROBINHOOD_SNDK),
    _rwa("SPCX", "SPACs", ROBINHOOD_SPCX),
    _rwa("SPY", "S&P 500", ROBINHOOD_SPY),
    _rwa("TSLA", "Tesla", ROBINHOOD_TSLA),
    _rwa("TSM", "TSMC", ROBINHOOD_TSM),
    _rwa("USAR", "USA Rare Earth", ROBINHOOD_USAR),
    _rwa("USO", "Oil", ROBINHOOD_USO),
]
