"""Extract public construction evidence from a mined pool-launch release.

Usage: python tools/ci/import_lifecycle_release.py /path/to/mined/deployment
This is data generation only: it never compiles, contacts RPCs, signs or publishes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    release_dir = Path(sys.argv[1])
    read = lambda name: json.loads((release_dir / name).read_text())
    manifest, deployment, readback, release, admission = (
        read(name) for name in ("manifest.json", "deployment.json", "readback.json", "release.json", "admission.json")
    )
    artifact_name = "out/FixedFeePoolHookV1.sol/FixedFeePoolHookV1.json"
    artifact_bytes = (release_dir / "artifacts" / artifact_name).read_bytes()
    if hashlib.sha256(artifact_bytes).hexdigest() != release["artifacts"][artifact_name]:
        raise ValueError("Creation artifact differs from the release-owned SHA-256")
    code = json.loads(artifact_bytes)["bytecode"]["object"]
    contracts = deployment["contracts"]
    factory = next(row for row in contracts if row["address"].lower() == deployment["addresses"]["tokenFactory"].lower())
    data = {
        "chainId": deployment["chainId"], "addresses": deployment["addresses"],
        "adapters": deployment["adapters"], "profiles": deployment["profiles"],
        "envelopeBytes": admission["envelopeBytes"], "creationCode": code,
        "tokenFactoryCodeHash": factory["runtimeHash"],
        "protocolMaximumDeveloperFeeBps": readback["protocolMaximumDeveloperFeeBps"],
        "provenance": {"sourceCommit": manifest["sourceCommit"], "deploymentBlock": manifest["captureBlockNumber"]},
    }
    destination = Path(__file__).resolve().parents[2] / "black_market_sdk" / "_lifecycle_release_20261008.json"
    destination.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Generated {destination.name} from {release_dir.name}")


if __name__ == "__main__":
    main()
