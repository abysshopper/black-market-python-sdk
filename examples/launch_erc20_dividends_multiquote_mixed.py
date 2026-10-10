"""Launch ERC20 holder dividends, V4 WETH/USDG and Abyss WETH, seven positions."""
from launch_examples import run_launch_example

CASE = {"id": "erc20-dividends-multiquote-mixed", "description": "ERC20 holder dividends, V4 WETH/USDG plus Abyss WETH, seven positions and three buys", "tokenKind": 0, "rewardMode": 2, "mode": "staged", "markets": [{"venue": "v4", "quote": "WETH", "positions": 2}, {"venue": "v4", "quote": "USDG", "positions": 2}, {"venue": "abyss", "quote": "WETH", "positions": 3}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
