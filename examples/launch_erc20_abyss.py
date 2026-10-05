"""Launch ERC20 with one canonical Abyss position and atomic opening."""
from launch_examples import run_launch_example

CASE = {"id": "erc20-abyss-basic", "description": "ERC20, one canonical Abyss position, atomic opening", "tokenKind": 0, "rewardMode": 0, "mode": "atomic", "markets": [{"venue": "abyss", "positions": 1}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
