"""Extract construction profiles from mined release and template evidence.

Usage: python tools/ci/import_lifecycle_release.py RELEASE_DIR TEMPLATE_DIR CHILDREN_DIR
Replace HMM: python tools/ci/import_lifecycle_release.py --replace-hmm-v2 RELEASE_JSON LIVE_JSON
Read-only RPC mode hash-checks every storage child against the admitted graph.
This is data generation only: it never compiles, signs or publishes.
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


def import_template(template_dir: Path, children_dir: Path | None, data: dict, abi_data: dict,
                    *, key: str = "v4-dynamic-fee-pool", rpc_url: str | None = None) -> None:
    admission = json.loads((template_dir / "admission.json").read_text())
    broadcast = json.loads((template_dir / "forge-broadcast" / "AdmitPoolHookTemplateV1.s.sol" /
                            str(data["chainId"]) / "admit-latest.json").read_text())
    if admission["chainId"] != data["chainId"] or admission["registry"].lower() != data["addresses"]["registry"].lower():
        raise ValueError("Template targets a different release registry or chain")
    receipt, = broadcast["receipts"]
    if int(receipt["status"], 16) != 1 or not any(transaction["hash"] == receipt["transactionHash"] for transaction in broadcast["transactions"]):
        raise ValueError("Template admission requires a successful matching mined receipt")
    import_admission(admission, receipt, data, abi_data, key=key, children_dir=children_dir, rpc_url=rpc_url)


def import_admission(admission: dict, receipt: dict, data: dict, abi_data: dict, *,
                     key: str, children_dir: Path | None = None, rpc_url: str | None = None,
                     creation_code: bytes | None = None) -> None:
    if (admission["chainId"] != data["chainId"]
            or admission["registry"].lower() != data["addresses"]["registry"].lower()
            or int(receipt["status"], 16) != 1):
        raise ValueError("Admission requires a successful mined receipt on the selected release graph")
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
        address = graph[f"codeChunk{index}"]
        if int(address, 16) == 0:
            if index == 0 or int(graph[f"codeChunk{index}Hash"], 16) != 0:
                raise ValueError("Invalid empty storage-child graph")
            continue
        if creation_code is not None:
            code = b"\x00" + creation_code[index * 24575:(index + 1) * 24575]
        elif rpc_url is not None:
            from web3 import Web3
            code = bytes(Web3(Web3.HTTPProvider(rpc_url)).eth.get_code(address))
        elif children_dir is not None:
            runtime = json.loads((children_dir / address.lower() / "runtime.json").read_text())
            code = bytes.fromhex(runtime["result"][2:])
        else:
            raise ValueError("Preserved children or an explicit read-only RPC URL are required")
        if not 1 < len(code) <= 24576 or code[0] != 0 or "0x" + keccak(code).hex() != graph[f"codeChunk{index}Hash"]:
            raise ValueError("Storage-child runtime differs from the admitted graph")
        chunks.append(code[1:])
    creation_code = b"".join(chunks)
    if (len(chunks) == 2 and len(chunks[0]) != 24575) or len(creation_code) + 20 * 32 > 49152 or "0x" + keccak(creation_code).hex() != graph["hookCreationCodeHash"]:
        raise ValueError("Storage-child payloads differ from the exact typed deployer initcode")
    data["adapters"].append({"id": admission["adapterId"], "address": admission["adapter"],
                             "codehash": "0x" + code_hash.hex(), "capabilities": capabilities, "configVersion": version})
    data["profiles"].append({**registration, "key": key, "id": admission["profileId"],
        "envelopeBytes": admission["envelopeBytes"], "creationCode": "0x" + creation_code.hex(),
        "topology": {"hookTopology": envelope_values[4], "configVersion": version,
                     "hookDeployer": graph["hookDeployer"], "hookCreationCodeHash": graph["hookCreationCodeHash"]}})
    provenance_key = "dynamicFeeAdmission" if key == "v4-dynamic-fee-pool" else key + "Admission"
    data["provenance"][provenance_key] = {"transactionHash": receipt["transactionHash"],
        "blockNumber": int(receipt["blockNumber"], 16), "blockHash": receipt["blockHash"]}


def main() -> None:
    if sys.argv[1] == "--replace-hmm-v2":
        sdk_dir = Path(__file__).resolve().parents[2] / "black_market_sdk"
        destination = sdk_dir / "_lifecycle_release_20261008.json"
        data = json.loads(destination.read_text())
        release = json.loads(Path(sys.argv[2]).read_text())
        live = json.loads(Path(sys.argv[3]).read_text())
        if (release["chainId"] != data["chainId"] or live["chainId"] != data["chainId"]
                or release["registry"].lower() != data["addresses"]["registry"].lower()
                or release["orchestrator"].lower() != data["addresses"]["orchestrator"].lower()
                or release["tailBytes"] != 512):
            raise ValueError("HMM V2 evidence differs from the selected lifecycle release")
        keys = {"hmm-fixed-v2": "v4-hmm-fixed-pool", "hmm-dynamic-v2": "v4-hmm-dynamic-pool"}
        if set(row["slug"] for row in release["profiles"]) != set(keys) or len(release["profiles"]) != 2:
            raise ValueError("HMM V2 replacement requires both exact offerings")
        old_adapters = {row["adapterId"] for row in data["profiles"] if row["key"] in keys.values()}
        data["profiles"] = [row for row in data["profiles"] if row["key"] not in keys.values()]
        data["adapters"] = [row for row in data["adapters"] if row["id"] not in old_adapters]
        abi_path = sdk_dir / "_sdk_abi_data.json"
        abi_data = {name: abi for name, abi in json.loads(abi_path.read_text()).items() if not name.startswith("hmm")}
        for row in release["profiles"]:
            recorded, = [item for item in live["profiles"] if item["profileId"] == row["profileId"]]
            if (recorded["registrationBytes"] != row["registrationBytes"]
                    or recorded["envelopeBytes"] != row["envelopeBytes"]
                    or recorded["admissionReceipt"]["transactionHash"] != row["admissionTransaction"]):
                raise ValueError("HMM V2 frozen inputs differ from pinned live admission evidence")
            import_admission(row, recorded["admissionReceipt"], data, abi_data,
                key=keys[row["slug"]], creation_code=bytes.fromhex(row["creationCode"][2:]))
            abi_name = "hmmFixedHookV2Abi" if row["slug"] == "hmm-fixed-v2" else "hmmDynamicHookV2Abi"
            abi_data[abi_name] = row["abi"]
        for name, abi in release["abis"].items():
            names = {"HMMLiquidityManagerV2": "hmmLiquidityManagerV2Abi",
                     "HMMFeeCollectorV2": "hmmFeeCollectorV2Abi",
                     "HMMFeeCollectorFactoryV2": "hmmFeeCollectorFactoryV2Abi"}
            abi_data[names[name]] = abi
        setter, = [entry for entry in abi_data["hmmLiquidityManagerV2Abi"] if entry.get("name") == "setStrategyConfig"]
        strategy = [{"name": field["name"], "type": field["type"]} for field in setter["inputs"][0]["components"]]
        abi_data["hmmStrategyComponents"] = strategy
        abi_data["hmmLaunchComponents"] = [
            {"name": "strategyShareBps", "type": "uint16"},
            {"name": "strategy", "type": "tuple", "components": strategy},
        ]
        data["provenance"]["hmmV2Snapshot"] = {"blockNumber": live["blockNumber"], "blockHash": live["blockHash"]}
        destination.write_text(json.dumps(data, indent=2) + "\n")
        abi_path.write_text(json.dumps(abi_data, indent=2) + "\n")
        print("Replaced both HMM construction presets and ABIs from mined V2 admission evidence")
        return
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
