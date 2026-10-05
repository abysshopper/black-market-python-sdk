"""Launch ERC20 staking rewards, two V4 positions and ordered buys."""
from launch_examples import run_launch_example

CASE = {"id": "erc20-staking-v4", "description": "Staking rewards, two V4 positions and two ordered opening buys", "tokenKind": 0, "rewardMode": 1, "mode": "staged", "markets": [{"venue": "v4", "positions": 2}], "buysPerMarket": 2}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
