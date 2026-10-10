"""Launch ERC20 with four adjacent Early Scarcity V4 bands and a final tail."""
from launch_examples import run_launch_example

CASE = {"id": "erc20-depth-v4", "description": "ERC20, four adjacent Early Scarcity V4 positions with increasing inventory and final tail", "tokenKind": 0, "rewardMode": 0, "mode": "staged", "markets": [{"venue": "v4", "positions": 4, "distributionPreset": "early-scarcity"}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
