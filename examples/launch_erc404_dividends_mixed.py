"""Launch ERC404 holder dividends, both venues and five permanent positions."""
from launch_examples import run_launch_example

CASE = {"id": "erc404-dividends-mixed", "description": "ERC404 holder dividends, both venues and five permanent positions", "tokenKind": 1, "rewardMode": 2, "mode": "staged", "markets": [{"venue": "v4", "positions": 2}, {"venue": "abyss", "positions": 3}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
