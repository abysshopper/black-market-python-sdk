"""Generate independent UFG wire fixtures with Foundry's cast abi-encode."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from eth_utils import keccak

from black_market_sdk import (
    UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID, UFG_LAUNCH_CONFIG_ABI_TYPE,
    ROBINHOOD_WETH, get_launch_addresses, get_known_lifecycle_profile,
)
from black_market_sdk.lifecycle import canonical_abi_type
from black_market_sdk import V4_MARKET_CONFIG_COMPONENTS_V6


def cast_tuple(values) -> str:
    def value(item):
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, bytes):
            return "0x" + item.hex()
        if isinstance(item, tuple):
            return cast_tuple(item)
        if isinstance(item, list):
            return "[" + ",".join(value(child) for child in item) + "]"
        return str(item)
    return "(" + ",".join(value(item) for item in values) + ")"


def encode(signature: str, values: str) -> str:
    return subprocess.run(["cast", "abi-encode", signature, values], check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    fixture = {"generator": "cast abi-encode (Foundry 1.6.0-nightly)", "configs": [], "markets": []}
    for fee, reserves in ((100, [ROBINHOOD_WETH]), (2000, ["0x0000000000000000000000000000000000000001", ROBINHOOD_WETH])):
        signature, values = "f(" + UFG_LAUNCH_CONFIG_ABI_TYPE + ")", cast_tuple((10000, fee, reserves))
        encoded = encode(signature, values)
        fixture["configs"].append({"treasuryShareBps": 10000, "exitFeeBps": fee, "reserveAssets": reserves,
            "signature": signature, "tuple": values, "encoded": encoded, "hash": "0x" + keccak(bytes.fromhex(encoded[2:])).hex()})
    config_type = "(" + ",".join(canonical_abi_type(field) for field in V4_MARKET_CONFIG_COMPONENTS_V6) + ")"
    zero = "0x" + "00" * 32
    for identity in (UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID):
        envelope = get_known_lifecycle_profile(chain_id=4663, orchestrator=get_launch_addresses(4663).orchestrator, profile_id=identity).profile.envelope
        values = cast_tuple((6, 0, 60, 1 << 96, 10000, 1000, 7654321, 1, envelope.protocol_fee_denominator,
            envelope.protocol_treasury, True, "0x" + (55).to_bytes(32, "big").hex(), zero, identity,
            envelope.terms_digest, envelope.beneficiary, 0, [(0, 887220, 10**18, zero, 10**24)]))
        signature = "f(" + config_type + ")"
        prefix = encode(signature, values)
        fixture["markets"].append({"profileId": identity, "signature": signature, "tuple": values,
            "prefix": prefix, "encoded": prefix + fixture["configs"][0]["encoded"][2:]})
    destination = Path(__file__).resolve().parents[2] / "tests/fixtures/ufg-launch-vectors.json"
    destination.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"Generated {destination.name}: two config vectors and two tailed config6 vectors")


if __name__ == "__main__":
    main()
