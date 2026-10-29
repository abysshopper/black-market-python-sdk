"""Capture first-party auto-compounding presets from admission evidence and pinned RPC reads.

Usage: python tools/ci/import_auto_compound_release.py CONTRACT_ROOT [--block L2_BLOCK]
Only eth_chainId, eth_getBlockByNumber, eth_call, eth_getCode and receipt reads are used.
The recorded block is an L2 RPC header, never Nitro's L1-valued block-number helpers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak, to_checksum_address
from web3 import Web3

from import_lifecycle_release import canonical_type, import_admission

RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
MODULE_HASH = "0x32cc7319b115951f5b0c2819ddd6d92e1f1501db3d42d35f3a1fb7ab1c4de291"
EXPECTED = {
    "static": {
        "directory": "20261028T233808Z-auto-compounding-static",
        "profileId": "0xdd53c2c686f822b2d17536ccf4f9db954b4976d1be80404bc74571253179fae0",
        "adapterId": "0x47f9efabc7c0427db5166ecf11702e0e0d2ca307a475d015d400a1584e572bc5",
        "adapter": "0xae70e5acb118eade1ddf97a86a8ef681d5b71686",
        "hookDeployer": "0x78811b0b1bb0f0f896e65c539c5bc62c4d33fbe2",
        "locker": "0x0eb419d291abc5f643acb5b6035236df231e479d",
        "collectorDeployer": "0x1b256029541ee5bef2db363211a7a05f9c36c18c",
        "collectorFactory": "0x1d93705968dd03a27d86cb1590889882153ac0af",
        "module": "0x84858337ae7b98d265be7d4d02712da6e9633827",
        "hookCreationCodeHash": "0x2c7bd199217fedf96a5b83d3ae3d630589eee86756c3a33bcc9a8d5e35efdc52",
    },
    "dynamic": {
        "directory": "20261028T234409Z-auto-compounding-dynamic",
        "profileId": "0x46b81bfe9316fbaa90e930c6f1d5ad5e6ecfced2c886fd5520ca2bae11b92744",
        "adapterId": "0x68a0b17d78c8c1a99762de862182940d795bbdc9617627ef6733e53378267201",
        "adapter": "0x37b81fce117973f4e69bfbf58a20385cc24492e0",
        "hookDeployer": "0x6911572b94e5da5a23073b49d857fe7a3f925b7c",
        "locker": "0xd6869122933143f72cf30e3a0dfd8ac67246e139",
        "collectorDeployer": "0x2010944ebeda7571b67cd30f2e596037fb18f8c7",
        "collectorFactory": "0xb04735e49a5cfccfe8572afef0ad0f0addcdc698",
        "module": "0x81ccf0ad6aebfcffb0fb8a5eaee70f398ba47022",
        "hookCreationCodeHash": "0x6b3cdd53a654f08b0c2d42d7906ec7f70b22b40f463b1508fab3a535e9b412c9",
    },
}
RETIRED = (
    "0x1e41841a30f6cbac7cc41f57753bd9bd2eb155b9f4dfc869b19a75afcbf51d68",
    "0x53ddf479f6db40aebb02afe8c46031f69cb22e9712428621beb1652e0adae0d7",
)


def hex_data(value: bytes) -> str:
    return "0x" + bytes(value).hex()


def decoded(component: dict, value):
    if component["type"] == "tuple":
        return {child["name"]: decoded(child, item) for child, item in zip(component["components"], value)}
    if component["type"].startswith("bytes"):
        return hex_data(value)
    if component["type"] == "address":
        return to_checksum_address(value)
    return value


def require_equal(actual, expected, name: str) -> None:
    if isinstance(actual, str) and isinstance(expected, str):
        actual, expected = actual.lower(), expected.lower()
    if actual != expected:
        raise ValueError(f"{name} differs from the admitted auto-compound specification")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract_root", type=Path)
    parser.add_argument("--block", type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sdk = root / "black_market_sdk"
    release = json.loads((sdk / "_lifecycle_release_20261008.json").read_text())
    abis = json.loads((sdk / "_sdk_abi_data.json").read_text())
    client = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))
    require_equal(client.eth.chain_id, release["chainId"], "chainId")
    block = client.eth.get_block(args.block if args.block is not None else "latest")
    number = block["number"]
    block_hash = hex_data(block["hash"])
    addresses = release["addresses"]
    registry = addresses["registry"]
    data = {"chainId": release["chainId"], "addresses": addresses, "adapters": [], "profiles": [],
            "provenance": {"sourceCommit": "8d958b6", "blockNumber": number, "blockHash": block_hash}}
    evidence = {"chainId": release["chainId"], "registry": registry, "orchestrator": addresses["orchestrator"],
                "rpcUrl": RPC_URL, "blockNumber": number, "blockHash": block_hash, "profiles": []}

    def call(address: str, entry: dict, arguments=()):
        inputs, outputs = entry["inputs"], entry["outputs"]
        signature = entry["name"] + "(" + ",".join(canonical_type(item) for item in inputs) + ")"
        payload = keccak(text=signature)[:4] + abi_encode([canonical_type(item) for item in inputs], arguments)
        raw = bytes(client.eth.call({"to": to_checksum_address(address), "data": payload}, block_identifier=number))
        values = abi_decode([canonical_type(item) for item in outputs], raw)
        if abi_encode([canonical_type(item) for item in outputs], values) != raw:
            raise ValueError("RPC returned noncanonical ABI bytes")
        value = decoded(outputs[0], values[0]) if len(outputs) == 1 else {
            item["name"]: decoded(item, value) for item, value in zip(outputs, values)}
        return hex_data(raw), value

    def registry_call(method: str, identity: str):
        entry, = [entry for entry in abis["lifecycleRegistryAbi"] if entry.get("name") == method]
        return call(registry, entry, [bytes.fromhex(identity[2:])])

    def getter(address: str, method: str, kind: str):
        return call(address, {"name": method, "inputs": [], "outputs": [{"type": kind}]})

    def runtime_hash(address: str) -> str:
        code = bytes(client.eth.get_code(to_checksum_address(address), block_identifier=number))
        if not code:
            raise ValueError("Missing runtime in admitted graph")
        return hex_data(keccak(code))

    for variant, expected in EXPECTED.items():
        directory = args.contract_root / "deployments/launch/auto-compounding" / expected["directory"]
        admission = json.loads((directory / "admission.json").read_text())
        for name in ("profileId", "adapterId", "adapter"):
            require_equal(admission[name], expected[name], name)
        row = {"variant": variant, "profileId": expected["profileId"], "adapterId": expected["adapterId"],
               "admissionPath": str(directory.relative_to(args.contract_root)), "raw": {}, "decoded": {}}
        for method in ("adapter", "profile", "profileTopology", "profileEnvelope"):
            row["raw"][method], row["decoded"][method] = registry_call(
                method, expected["adapterId"] if method == "adapter" else expected["profileId"])
        require_equal(row["raw"]["profile"], admission["registrationBytes"], "registrationBytes")
        require_equal(row["raw"]["profileEnvelope"], admission["envelopeBytes"], "envelopeBytes")
        adapter, profile, topology, envelope = (row["decoded"][name] for name in (
            "adapter", "profile", "profileTopology", "profileEnvelope"))
        require_equal(adapter["enabled"] and profile["enabled"], True, "enabled")
        require_equal(adapter["implementation"], expected["adapter"], "adapter implementation")
        require_equal(adapter["codeHash"], runtime_hash(expected["adapter"]), "adapter runtime")
        require_equal(topology, {"hookTopology": 2, "configVersion": 6,
            "hookDeployer": to_checksum_address(expected["hookDeployer"]),
            "hookCreationCodeHash": expected["hookCreationCodeHash"]}, "profileTopology")
        graph = envelope["graph"]
        for name in ("hookDeployer", "locker", "collectorFactory", "collectorDeployer", "hookCreationCodeHash"):
            require_equal(graph[name], expected[name], name)
        row["adapterMetadata"] = {}
        metadata = {"PROFILE_ID": ("bytes32", expected["profileId"]), "CONFIG_VERSION": ("uint32", 6),
                    "implementationRegistry": ("address", registry), "core": ("address", addresses["orchestrator"]),
                    "poolManager": ("address", graph["manager"]), "oracleFactory": ("address", graph["oracleFactory"]),
                    "hookDeployer": ("address", graph["hookDeployer"]), "locker": ("address", graph["locker"]),
                    "collectorFactory": ("address", graph["collectorFactory"])}
        for name, (kind, value) in metadata.items():
            raw, actual = getter(expected["adapter"], name, kind)
            require_equal(actual, value, name)
            row["adapterMetadata"][name] = {"raw": raw, "value": actual}
        row["runtimeHashes"] = {"adapter": adapter["codeHash"]}
        for name in ("manager", "oracleFactory", "hookDeployer", "locker", "collectorFactory", "collectorDeployer"):
            actual = runtime_hash(graph[name])
            require_equal(actual, graph[name + "CodeHash"], name + " runtime")
            row["runtimeHashes"][name] = actual
        row["moduleMetadata"] = {}
        for name in ("collectorFactory", "collectorDeployer"):
            module_raw, module = getter(graph[name], "compoundModule", "address")
            hash_raw, module_hash = getter(graph[name], "compoundModuleCodeHash", "bytes32")
            require_equal(module, expected["module"], "compoundModule")
            require_equal(module_hash, MODULE_HASH, "compoundModuleCodeHash")
            require_equal(runtime_hash(module), MODULE_HASH, "module runtime")
            row["moduleMetadata"][name] = {"module": module, "moduleRaw": module_raw,
                                           "codeHash": module_hash, "codeHashRaw": hash_raw}
        row["chunks"] = []
        for index in (0, 1):
            address = graph[f"codeChunk{index}"]
            if int(address, 16) == 0:
                continue
            runtime = bytes(client.eth.get_code(address, block_identifier=number))
            require_equal(hex_data(keccak(runtime)), graph[f"codeChunk{index}Hash"], "storage child")
            row["chunks"].append({"address": address, "runtime": hex_data(runtime)})
        creation_code = b"".join(bytes.fromhex(chunk["runtime"][4:]) for chunk in row["chunks"])
        row["creationCode"] = hex_data(creation_code)
        receipt_file, = (directory / "chain").glob("admission-*.json")
        saved_receipt = json.loads(receipt_file.read_text())["receipt"]
        receipt = client.provider.make_request("eth_getTransactionReceipt", [saved_receipt["transactionHash"]])["result"]
        require_equal(receipt["blockHash"], saved_receipt["blockHash"], "admission block hash")
        require_equal(receipt["status"], "0x1", "admission success")
        if int(receipt["blockNumber"], 16) > number:
            raise ValueError("Admission is later than the selected readback block")
        row["admissionReceipt"] = receipt
        import_admission(admission, receipt, data, abis, key=f"v4-auto-compound-{variant}-pool",
                         creation_code=creation_code)
        evidence["profiles"].append(row)
    evidence["retiredProfiles"] = {}
    for identity in RETIRED:
        raw, registration = registry_call("profile", identity)
        require_equal(registration["enabled"], False, "retired profile enabled")
        evidence["retiredProfiles"][identity] = raw
    require_equal(hex_data(client.eth.get_block(number)["hash"]), block_hash, "canonical readback block")
    generated = {key: evidence[key] for key in ("chainId", "registry", "orchestrator", "blockNumber", "blockHash")}
    generated.update(adapters=data["adapters"], profiles=data["profiles"], provenance=data["provenance"])
    (sdk / "_auto_compound_release_20261028.json").write_text(json.dumps(generated, indent=2) + "\n")
    (root / "tests/fixtures/auto-compound-registry-readback.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Generated two auto-compound presets at Robinhood L2 block {number} ({block_hash})")


if __name__ == "__main__":
    main()
