"""Generate UFG construction metadata from pinned, read-only registry evidence.

Usage: python tools/ci/import_ufg_release.py /path/to/ufg-registry-readback.json
The Node capture script scripts/generate-ufg-presets.mjs --readback CONTRACT_ROOT
records block 94551140, all four registry calls, and exact creation-code chunks.
This importer verifies those bytes and never sends RPC, signs, or publishes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from eth_abi import encode as abi_encode
from eth_utils import keccak


def canonical_type(component: dict) -> str:
    if component["type"].startswith("tuple"):
        return "(" + ",".join(canonical_type(child) for child in component["components"]) + ")" + component["type"][5:]
    return component["type"]


def wire(component: dict, value):
    kind = component["type"]
    if kind == "tuple":
        return tuple(wire(child, value[child["name"]]) for child in component["components"])
    if kind.startswith("bytes"):
        return bytes.fromhex(value[2:])
    if kind.startswith(("uint", "int")):
        return int(value)
    return value


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads(Path(sys.argv[1]).read_text())
    release = json.loads((root / "black_market_sdk/_lifecycle_release_20261008.json").read_text())
    abis = json.loads((root / "black_market_sdk/_sdk_abi_data.json").read_text())
    if (evidence["blockNumber"] != 94551140 or evidence["chainId"] != release["chainId"]
            or evidence["registry"].lower() != release["addresses"]["registry"].lower()
            or evidence["orchestrator"].lower() != release["addresses"]["orchestrator"].lower()):
        raise ValueError("UFG evidence must target the pinned canonical deployment")
    profiles, adapters = [], []
    expected = {
        "fixed": ("0x5c6045af480814e469f3dba7c120d264047c9320b869c63a064536041a2dbae6", "0x073a4d46af70a3d4f69641e4f9dcdc9d8c279cd443daff960bbdf65a3b5fc758"),
        "dynamic": ("0x03de8d5e38f2957d80ecb9f2e59d555601ff091dfb17bce84178956a2e8d9b41", "0xc0906e17625b48240a87a3afc6ee921b653bdacd72906a16b359d0776d938518"),
    }
    if len(evidence["profiles"]) != 2 or {row["variant"] for row in evidence["profiles"]} != set(expected):
        raise ValueError("Both UFG offerings are required exactly once")
    registry_abi = abis["lifecycleRegistryAbi"]
    for row in evidence["profiles"]:
        if (row["profileId"], row["adapterId"]) != expected[row["variant"]]:
            raise ValueError("Unexpected UFG profile identity")
        for method in ("profileEnvelope", "adapter", "profile", "profileTopology"):
            entry, = [item for item in registry_abi if item.get("name") == method and item["type"] == "function"]
            outputs = entry["outputs"]
            decoded = row["decoded"][method]
            values = [wire(outputs[0], decoded)] if len(outputs) == 1 else [wire(output, decoded[output["name"]]) for output in outputs]
            if "0x" + abi_encode([canonical_type(output) for output in outputs], values).hex() != row["raw"][method]:
                raise ValueError(f"Decoded {method} differs from the recorded RPC bytes")
        envelope = row["decoded"]["profileEnvelope"]
        graph = envelope["graph"]
        chunks = []
        for index, chunk in enumerate(row["chunks"]):
            runtime = bytes.fromhex(chunk["runtime"][2:])
            if (chunk["address"].lower() != graph[f"codeChunk{index}"].lower()
                    or not 1 < len(runtime) <= 24576 or runtime[0] != 0
                    or "0x" + keccak(runtime).hex() != graph[f"codeChunk{index}Hash"]):
                raise ValueError("UFG storage child differs from its certified graph")
            chunks.append(runtime[1:])
        code = b"".join(chunks)
        if ("0x" + code.hex() != row["creationCode"] or "0x" + keccak(code).hex() != graph["hookCreationCodeHash"]
                or (len(chunks) == 2 and len(chunks[0]) != 24575)):
            raise ValueError("UFG creation code differs from the recorded storage children")
        adapter = dict(row["decoded"]["adapter"])
        registration = dict(row["decoded"]["profile"])
        if not adapter["enabled"] or not registration["enabled"]:
            raise ValueError("UFG evidence must record enabled offerings")
        registration["capabilities"] = int(registration["capabilities"])
        adapters.append({"id": row["adapterId"], "address": adapter["implementation"], "codehash": adapter["codeHash"],
                         "capabilities": int(adapter["capabilities"]), "configVersion": adapter["configVersion"]})
        profiles.append({**registration, "key": f"v4-ufg-{row['variant']}-pool", "id": row["profileId"],
                         "envelopeBytes": row["raw"]["profileEnvelope"], "creationCode": row["creationCode"],
                         "topology": row["decoded"]["profileTopology"]})
    generated = {key: evidence[key] for key in ("chainId", "registry", "orchestrator", "blockNumber", "blockHash")}
    generated.update(adapters=adapters, profiles=profiles)
    (root / "black_market_sdk/_ufg_release_20261021.json").write_text(json.dumps(generated, indent=2) + "\n")
    (root / "tests/fixtures/ufg-registry-readback.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Generated two UFG presets from block {evidence['blockNumber']} ({evidence['blockHash']})")


if __name__ == "__main__":
    main()
