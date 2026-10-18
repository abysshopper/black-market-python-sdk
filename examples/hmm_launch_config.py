"""Construct HMM V2 tails with explicit risk controls; optionally certify live (no wallet)."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web3 import Web3
from black_market_sdk import (
    KnownLifecycleProfile, ROBINHOOD_MAINNET_RPC, default_hmm_launch_config,
    encode_hmm_launch_tail, get_known_lifecycle_profile, get_launch_addresses,
    read_lifecycle_profiles,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Read-only Robinhood admission discovery")
    parser.add_argument("--max-bid-quote", required=True, type=int,
                        help="Reviewed bid-principal cap in raw WETH units (positive uint128)")
    parser.add_argument("--reference-deviation", required=True, type=int,
                        help="Reviewed nonnegative deviation from the oracle reference, in tick spacings")
    args = parser.parse_args()
    core = get_launch_addresses(4663).orchestrator
    config = default_hmm_launch_config(token_budget=1_000_000 * 10**18, quote_asset="WETH",
        max_bid_quote=args.max_bid_quote, reference_deviation=args.reference_deviation)
    presets = [get_known_lifecycle_profile(chain_id=4663, orchestrator=core, key=key)
               for key in (KnownLifecycleProfile.V4_HMM_FIXED_POOL, KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL)]
    result = {"hmmLaunchConfig": asdict(config), "tail": "0x" + encode_hmm_launch_tail(config).hex(),
              "profiles": [{"key": preset.key.value, "id": preset.profile.id} for preset in presets]}
    if args.live:
        client = Web3(Web3.HTTPProvider(ROBINHOOD_MAINNET_RPC, request_kwargs={"timeout": 30}))
        if client.eth.chain_id != 4663:
            raise ValueError("RPC must be Robinhood mainnet")
        profiles = read_lifecycle_profiles(client, orchestrator=core,
                                          profile_ids=[preset.profile.id for preset in presets])
        result["liveAdmission"] = [{"id": profile.id, "admitted": profile.admitted, "reason": profile.reason}
                                   for profile in profiles]
        if not all(profile.admitted for profile in profiles):
            raise ValueError(f"HMM profiles refused: {result['liveAdmission']}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
