"""Read current mainnet launch offerings, or hash an explicit plan offline.

Run ``python examples/quickstart.py`` for read-only mainnet discovery. An
optional positional JSON file uses the offline commitment path without RPC I/O.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    ROBINHOOD_MAINNET_CHAIN_ID,
    ROBINHOOD_MAINNET_RPC,
    encode_launch_plan,
    get_addresses,
    get_launch_addresses,
    hash_launch_plan,
    launch_id_of,
    launch_plan_from_dict,
    launch_plan_to_dict,
    read_lifecycle_profiles,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, nargs="?", help="Complete portable LaunchPlanV1 JSON for offline hashing")
    parser.add_argument("--rpc-url", default=ROBINHOOD_MAINNET_RPC)
    args = parser.parse_args(argv)
    if args.plan is not None:
        payload = json.loads(args.plan.read_text())
        plan = launch_plan_from_dict(payload.get("plan", payload))
        print(json.dumps({
            "plan": launch_plan_to_dict(plan), "planHash": hash_launch_plan(plan),
            "launchId": launch_id_of(plan), "encodedPlan": "0x" + encode_launch_plan(plan).hex(),
            "admitted": False, "note": "Offline commitment only; no execution or funding proof",
        }, indent=2))
        return 0
    chain_id = ROBINHOOD_MAINNET_CHAIN_ID
    client = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": 30}))
    if client.eth.chain_id != chain_id:
        parser.error("read quickstart requires a Robinhood mainnet (4663) RPC")
    launch = get_launch_addresses(chain_id)
    print("chain:", chain_id, "block:", client.eth.block_number)
    print("orchestrator:", launch.orchestrator)
    print("lending pool:", get_addresses(chain_id).lending_pool)
    for profile in read_lifecycle_profiles(client, orchestrator=launch.orchestrator, offset=0, limit=100):
        print(profile.id, profile.venue_kind, "admitted:", profile.admitted, "reason:", profile.reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
