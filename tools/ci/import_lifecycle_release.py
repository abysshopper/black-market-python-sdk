"""Extract six construction profiles from mined release and template evidence.

Usage: python tools/ci/import_lifecycle_release.py RELEASE_DIR TEMPLATE_DIR CHILDREN_DIR
This is data generation only: it never compiles, contacts RPCs, signs or publishes.
The template admission.json is unsigned preparation; its mined receipt authenticates it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_utils import keccak, to_checksum_address


def canonical_type(component: dict) -> str:
    if component["type"].startswith("tuple"):
        return "(" + ",".join(canonical_type(child) for child in component["components"]) + ")" + component["type"][5:]
    return component["type"]


def import_template(template_dir: Path, children_dir: Path, data: dict, abi_data: dict) -> None:
    admission = json.loads((template_dir / "admission.json").read_text())
    broadcast = json.loads((template_dir / "forge-broadcast" / "AdmitPoolHookTemplateV1.s.sol" /
                            str(data["chainId"]) / "admit-latest.json").read_text())
    if admission["chainId"] != data["chainId"] or admission["registry"].lower() != data["addresses"]["registry"].lower():
        raise ValueError("Template targets a different release registry or chain")
    receipt, = broadcast["receipts"]
    if int(receipt["status"], 16) != 1 or not any(transaction["hash"] == receipt["transactionHash"] for transaction in broadcast["transactions"]):
        raise ValueError("Template admission requires a successful matching mined receipt")
    logs = [log for log in receipt["logs"] if log["address"].lower() == admission["registry"].lower() and not log["removed"]]
    def event(signature: str, identity: str) -> dict:
        matches = [log for log in logs if log["topics"][:2] == ["0x" + keccak(text=signature).hex(), identity]]
        result, = matches
        return result
    adapter_log = event("AdapterRegistered(bytes32,address,bytes32,uint64,uint32)", admission["adapterId"])
    code_hash, capabilities, version = abi_decode(["bytes32", "uint64", "uint32"], bytes.fromhex(adapter_log["data"][2:]))
    if adapter_log["topics"][2][-40:].lower() != admission["adapter"][2:].lower():
        raise ValueError("Mined adapter differs from template preparation")
    components = abi_data["profileRegistrationV1Components"]
    values = abi_decode(["(" + ",".join(canonical_type(component) for component in components) + ")"],
                        bytes.fromhex(admission["registrationBytes"][2:]))[0]
    registration = {component["name"]: "0x" + value.hex() if isinstance(value, bytes) else
                    to_checksum_address(value) if component["type"] == "address" else value
                    for component, value in zip(components, values)}
    registered = event("ProfileRegistered(bytes32,bytes32,bytes32,bytes32)", admission["profileId"])
    schema, dependency = abi_decode(["bytes32", "bytes32"], bytes.fromhex(registered["data"][2:]))
    if (registered["topics"][2] != admission["adapterId"] or registration["adapterId"] != admission["adapterId"]
            or "0x" + schema.hex() != registration["configSchema"] or "0x" + dependency.hex() != registration["dependencyDigest"]):
        raise ValueError("Mined profile differs from prepared registration")
    admitted = event("ProfileAdmitted(bytes32,address,bytes32,bytes32,bytes32,uint16)", admission["profileId"])
    terms, envelope_hash, author_cap = abi_decode(["bytes32", "bytes32", "uint16"], bytes.fromhex(admitted["data"][2:]))
    envelope = bytes.fromhex(admission["envelopeBytes"][2:])
    envelope_values = abi_decode(["(" + ",".join(canonical_type(component) for component in abi_data["launchEnvelopeV2Components"]) + ")"], envelope)[0]
    if (keccak(bytes.fromhex(admission["registrationBytes"][2:]) + envelope) != envelope_hash or author_cap != 0 or envelope_values[14] != author_cap
            or envelope_values[3] != terms or admitted["topics"][3] != "0x" + envelope_values[1].hex()
            or admitted["topics"][2][-40:].lower() != envelope_values[13][2:].lower()
            or envelope_values[5] != version or envelope_values[7] != capabilities):
        raise ValueError("Prepared envelope differs from mined zero-royalty admission")
    graph = admission["graph"]
    decoded_graph = envelope_values[-1]
    for component, value in zip(abi_data["launchGraphV2Components"], decoded_graph):
        expected = graph[component["name"]]
        actual = "0x" + value.hex() if isinstance(value, bytes) else value
        if actual.lower() != expected.lower():
            raise ValueError("Prepared graph differs from the admitted envelope")
    chunks = []
    for index in (0, 1):
        runtime = json.loads((children_dir / graph[f"codeChunk{index}"].lower() / "runtime.json").read_text())
        code = bytes.fromhex(runtime["result"][2:])
        if not 1 < len(code) <= 24576 or code[0] != 0 or "0x" + keccak(code).hex() != graph[f"codeChunk{index}Hash"]:
            raise ValueError("Preserved storage-child runtime differs from the admitted graph")
        chunks.append(code[1:])
    creation_code = b"".join(chunks)
    if len(chunks[0]) != 24575 or len(creation_code) + 20 * 32 > 49152 or "0x" + keccak(creation_code).hex() != graph["hookCreationCodeHash"]:
        raise ValueError("Storage-child payloads differ from the exact typed deployer initcode")
    data["adapters"].append({"id": admission["adapterId"], "address": admission["adapter"],
                             "codehash": "0x" + code_hash.hex(), "capabilities": capabilities, "configVersion": version})
    data["profiles"].append({**registration, "key": "v4-dynamic-fee-pool", "id": admission["profileId"],
        "envelopeBytes": admission["envelopeBytes"], "creationCode": "0x" + creation_code.hex(),
        "topology": {"hookTopology": envelope_values[4], "configVersion": version,
                     "hookDeployer": graph["hookDeployer"], "hookCreationCodeHash": graph["hookCreationCodeHash"]}})
    data["provenance"]["dynamicFeeAdmission"] = {"transactionHash": receipt["transactionHash"],
        "blockNumber": int(receipt["blockNumber"], 16), "blockHash": receipt["blockHash"]}


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
    profiles = []
    for row in deployment["profiles"]:
        row = dict(row)
        if row["topology"]["hookTopology"] == 0:
            row["key"] = f"abyss-{row['variant']}"
        elif row["topology"]["hookTopology"] == 2:
            row.update(key="v4-fixed-fee-pool", envelopeBytes=admission["envelopeBytes"], creationCode=code)
        else:
            raise ValueError("Unexpected original release profile topology")
        profiles.append(row)
    contracts = deployment["contracts"]
    factory = next(row for row in contracts if row["address"].lower() == deployment["addresses"]["tokenFactory"].lower())
    data = {
        "chainId": deployment["chainId"], "addresses": deployment["addresses"],
        "adapters": deployment["adapters"], "profiles": profiles,
        "tokenFactoryCodeHash": factory["runtimeHash"],
        "protocolMaximumDeveloperFeeBps": readback["protocolMaximumDeveloperFeeBps"],
        "provenance": {"sourceCommit": manifest["sourceCommit"], "deploymentBlock": manifest["captureBlockNumber"]},
    }
    sdk_dir = Path(__file__).resolve().parents[2] / "black_market_sdk"
    abi_data = json.loads((sdk_dir / "_sdk_abi_data.json").read_text())
    import_template(Path(sys.argv[2]), Path(sys.argv[3]), data, abi_data)
    destination = Path(__file__).resolve().parents[2] / "black_market_sdk" / "_lifecycle_release_20261008.json"
    destination.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Generated {destination.name} with {len(data['profiles'])} keyed construction profiles")


if __name__ == "__main__":
    main()
