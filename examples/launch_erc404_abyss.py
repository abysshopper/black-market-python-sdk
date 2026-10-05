"""Launch ERC404 NFT units and metadata with atomic canonical Abyss."""
from launch_examples import run_launch_example

CASE = {"id": "erc404-abyss", "description": "ERC404 NFT units and metadata, atomic canonical Abyss", "tokenKind": 1, "rewardMode": 0, "mode": "atomic", "markets": [{"venue": "abyss", "positions": 1}], "buysPerMarket": 1}

if __name__ == "__main__":
    raise SystemExit(run_launch_example(CASE))
