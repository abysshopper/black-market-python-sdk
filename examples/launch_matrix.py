"""Read current reviewed registry offerings without an SDK template allowlist."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from black_market_sdk import ROBINHOOD_MAINNET_CHAIN_ID, ROBINHOOD_MAINNET_RPC, get_launch_addresses, read_lifecycle_profiles


def json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    raise TypeError(f"Unsupported discovery output: {type(value).__name__}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=ROBINHOOD_MAINNET_RPC)
    parser.add_argument("--chain-id", type=int, default=ROBINHOOD_MAINNET_CHAIN_ID)
    parser.add_argument("--orchestrator", help="Explicit deployment override; required on unconfigured chains")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    orchestrator = args.orchestrator
    if orchestrator is None:
        try:
            orchestrator = get_launch_addresses(args.chain_id).orchestrator
        except KeyError:
            parser.error("--orchestrator is required for an unconfigured launch chain")
    client = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": 30}))
    if client.eth.chain_id != args.chain_id:
        parser.error("RPC chain ID differs from the explicitly selected discovery chain")
    profiles = read_lifecycle_profiles(client, orchestrator=orchestrator, offset=args.offset, limit=args.limit)
    print(json.dumps({"chainId": args.chain_id, "orchestrator": orchestrator, "offset": args.offset, "profiles": profiles}, default=json_value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
