"""Launch ERC20 across V4 WETH/USDG and Abyss WETH, with one buy per market."""
from launch_examples import run_launch_example

CASE = {"id": "erc20-multi-quote", "description": "ERC20, two V4 quotes plus Abyss WETH, three positions and three buys", "tokenKind": 0, "rewardMode": 0, "mode": "staged", "markets": [{"venue": "v4", "quote": "WETH", "positions": 1}, {"venue": "v4", "quote": "USDG", "positions": 1}, {"venue": "abyss", "quote": "WETH", "positions": 1}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
