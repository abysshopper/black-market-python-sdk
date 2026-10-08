"""Frozen October 8 construction metadata, never a live admission certificate."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files

from .lifecycle import (
    LifecycleAdapterRegistration, LifecycleProfileMetadata, LifecycleProfileRegistration,
    LifecycleDeveloperTerms, LifecycleHookTopology, LaunchEnvelopeV2, ProfileTopologyV1,
    LifecyclePlanningError, decode_launch_envelope, hash_lifecycle_profile,
)
from eth_utils import keccak


class KnownLifecycleProfile(str, Enum):
    ABYSS_0 = "abyss-0"
    ABYSS_1 = "abyss-1"
    ABYSS_2 = "abyss-2"
    ABYSS_3 = "abyss-3"
    V4_FIXED_FEE_POOL = "v4-fixed-fee-pool"


class KnownLifecycleHook(str, Enum):
    FIXED_FEE_POOL_V1 = "fixed-fee-pool-v1"




@dataclass(frozen=True)
class LifecycleBoundHookPreset:
    creation_code: bytes
    deployer: str
    pool_manager: str
    oracle_factory: str
    liquidity_locker: str
    registrar: str


@dataclass(frozen=True)
class LifecycleProfilePreset:
    key: KnownLifecycleProfile
    supported: bool
    profile: LifecycleProfileMetadata
    bound_hook: LifecycleBoundHookPreset | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class LifecycleDeploymentProvenance:
    source_commit: str
    deployment_block: int


@dataclass(frozen=True)
class LifecycleDeploymentPreset:
    chain_id: int
    orchestrator: str
    registry: str
    token_factory: str
    token_factory_code_hash: str
    funding_escrow: str
    profiles: tuple[LifecycleProfilePreset, ...]
    provenance: LifecycleDeploymentProvenance


def _load_deployment() -> LifecycleDeploymentPreset:
    data = json.loads(files("black_market_sdk").joinpath("_lifecycle_release_20261008.json").read_text())
    addresses = data["addresses"]
    adapters = {row["id"]: LifecycleAdapterRegistration(row["address"], row["codehash"],
        row["capabilities"], row["configVersion"], True) for row in data["adapters"]}
    envelope = decode_launch_envelope(data["envelopeBytes"])
    creation_code = bytes.fromhex(data["creationCode"].removeprefix("0x"))
    profiles = []
    for row, key in zip(data["profiles"], KnownLifecycleProfile):
        registration = LifecycleProfileRegistration(*(row[field] for field in (
            "adapterId", "configSchema", "dependencyDigest", "venue", "factory", "hook", "capabilities", "enabled",
        )))
        topology = row["topology"]
        topology = ProfileTopologyV1(LifecycleHookTopology(topology["hookTopology"]), topology["configVersion"],
                                     topology["hookDeployer"], topology["hookCreationCodeHash"])
        adapter = adapters[row["adapterId"]]
        bound = topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4
        profile = LifecycleProfileMetadata(
            row["id"], registration, adapter, topology, "uniswap-v4" if bound else "abyss",
            envelope if bound else None,
            LifecycleDeveloperTerms(adapter.implementation, envelope.beneficiary,
                                    envelope.maximum_developer_fee_bps, envelope.terms_digest, True) if bound else None,
            data["protocolMaximumDeveloperFeeBps"] if bound else None,
        )
        hook = LifecycleBoundHookPreset(creation_code, envelope.graph.hook_deployer, envelope.graph.manager,
                                       envelope.graph.oracle_factory, envelope.graph.locker,
                                       adapter.implementation) if bound else None
        if bound and (keccak(creation_code) != bytes.fromhex(topology.hook_creation_code_hash[2:]) or
                      hook.deployer.lower() != topology.hook_deployer.lower() or
                      hook.registrar.lower() != adapter.implementation.lower() or
                      hash_lifecycle_profile(envelope).lower() != profile.id.lower()):
            raise LifecyclePlanningError("HOOK_CREATION_CODE_MISMATCH", "Frozen constructor graph differs from the mined release evidence")
        profiles.append(LifecycleProfilePreset(key, True, profile, hook))
    provenance = data["provenance"]
    return LifecycleDeploymentPreset(data["chainId"], addresses["orchestrator"], addresses["registry"],
                                     addresses["tokenFactory"], data["tokenFactoryCodeHash"], addresses["fundingEscrow"],
                                     tuple(profiles), LifecycleDeploymentProvenance(provenance["sourceCommit"], provenance["deploymentBlock"]))


_DEPLOYMENT = _load_deployment()


def get_known_lifecycle_deployment(*, chain_id: int, orchestrator: str) -> LifecycleDeploymentPreset | None:
    if isinstance(chain_id, bool) or chain_id != _DEPLOYMENT.chain_id or orchestrator.lower() != _DEPLOYMENT.orchestrator.lower():
        return None
    return _DEPLOYMENT


def get_known_lifecycle_profile(
    *, chain_id: int, orchestrator: str, key: KnownLifecycleProfile | None = None,
    profile_id: bytes | str | None = None,
) -> LifecycleProfilePreset | None:
    if (key is None) == (profile_id is None):
        raise ValueError("Select exactly one known profile key or release-owned profile_id")
    deployment = get_known_lifecycle_deployment(chain_id=chain_id, orchestrator=orchestrator)
    if deployment is None:
        return None
    if key is not None:
        return next((preset for preset in deployment.profiles if preset.key == key), None)
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id
    return next((preset for preset in deployment.profiles if preset.profile.id.lower() == identity.lower()), None)


def list_known_lifecycle_profiles(*, chain_id: int, orchestrator: str) -> tuple[LifecycleProfilePreset, ...]:
    deployment = get_known_lifecycle_deployment(chain_id=chain_id, orchestrator=orchestrator)
    return () if deployment is None else deployment.profiles
