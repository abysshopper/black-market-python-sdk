"""Launch ERC404 NFT units and metadata with staged pool-bound V4."""
from launch_examples import run_launch_example

CASE = {"id": "erc404-v4", "description": "ERC404 NFT units and metadata, staged pool-bound V4", "tokenKind": 1, "rewardMode": 0, "mode": "staged", "markets": [{"venue": "v4", "positions": 1}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
