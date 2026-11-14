"""RWA collector and stock-registry ABIs from the pinned Node 0.17.0 release.

Provenance: tests/fixtures/rwa-registry-readback.json, per-profile abiProvenance.
"""
from .abis import _SDK_ABI_DATA

RWA_REWARD_COLLECTOR_V1_ABI = _SDK_ABI_DATA["rwaRewardCollectorV1Abi"]
RWA_ASSET_REGISTRY_V1_ABI = _SDK_ABI_DATA["rwaAssetRegistryV1Abi"]
