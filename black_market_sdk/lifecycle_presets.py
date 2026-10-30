"""Frozen release and admitted-template construction metadata, never live eligibility."""
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
    V4_DYNAMIC_FEE_POOL = "v4-dynamic-fee-pool"
    V4_HMM_FIXED_POOL = "v4-hmm-fixed-pool"
    V4_HMM_DYNAMIC_POOL = "v4-hmm-dynamic-pool"
    V4_UFG_FIXED_POOL = "v4-ufg-fixed-pool"
    V4_UFG_DYNAMIC_POOL = "v4-ufg-dynamic-pool"
    V4_AUTO_COMPOUND_STATIC_POOL = "v4-auto-compound-static-pool"
    V4_AUTO_COMPOUND_DYNAMIC_POOL = "v4-auto-compound-dynamic-pool"
    V4_RAETO_FIXED_POOL = "v4-raeto-fixed-pool"
    V4_RAETO_DYNAMIC_POOL = "v4-raeto-dynamic-pool"


class KnownLifecycleHook(str, Enum):
    FIXED_FEE_POOL_V1 = "fixed-fee-pool-v1"
    DYNAMIC_FEE_POOL_V1 = "dynamic-fee-pool-v1"
    HMM_FIXED_POOL_V2 = "hmm-fixed-pool-v2"
    HMM_DYNAMIC_POOL_V2 = "hmm-dynamic-pool-v2"
    AUTO_COMPOUND_STATIC_V1 = "auto-compound-static-v1"
    AUTO_COMPOUND_DYNAMIC_V1 = "auto-compound-dynamic-v1"

# HMMLiquidityHookV2.sol:98-119 requires ~3.06M gas available before manager
# calls (opening and buys), invisible in gasUsed/maxUsedGas. Robinhood fork
# measurements: activation used/minimum 5.85/9.60M, 11.13/15.40M, 16.54/21.11M;
# one-pool atomic 27.36/31.32M; prepare per HMM market 13.8-14.34M.
HMM_V2_FORWARDING_RESERVE = 4_250_000
HMM_V2_ADDITIONAL_MARKET_RESERVE = 500_000
ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT = 32_000_000




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
    hook_salt_miner: str | None = None
    launch_lens: str | None = None
    launch_lens_code_hash: str | None = None
    native_transaction_gas_limit: int | None = None


def _load_deployment() -> LifecycleDeploymentPreset:
    data = json.loads(files("black_market_sdk").joinpath("_lifecycle_release_20261008.json").read_text())
    addresses = data["addresses"]
    ufg = json.loads(files("black_market_sdk").joinpath("_ufg_release_20261021.json").read_text())
    if (ufg["chainId"] != data["chainId"] or ufg["orchestrator"].lower() != addresses["orchestrator"].lower()
            or ufg["registry"].lower() != addresses["registry"].lower()):
        raise LifecyclePlanningError("PROFILE_PRESET_MISMATCH", "UFG presets must bind to the canonical lifecycle deployment")
    data["adapters"].extend(ufg["adapters"])
    data["profiles"].extend(ufg["profiles"])
    compound = json.loads(files("black_market_sdk").joinpath("_auto_compound_release_20261028.json").read_text())
    if (compound["chainId"] != data["chainId"] or compound["orchestrator"].lower() != addresses["orchestrator"].lower()
            or compound["registry"].lower() != addresses["registry"].lower()):
        raise LifecyclePlanningError("PROFILE_PRESET_MISMATCH", "Auto-compound presets must bind to the canonical lifecycle deployment")
    data["adapters"].extend(compound["adapters"])
    data["profiles"].extend(compound["profiles"])
    raeto = json.loads(files("black_market_sdk").joinpath("_raeto_release_20261029.json").read_text())
    if (raeto["chainId"] != data["chainId"] or raeto["orchestrator"].lower() != addresses["orchestrator"].lower()
            or raeto["registry"].lower() != addresses["registry"].lower()):
        raise LifecyclePlanningError("PROFILE_PRESET_MISMATCH", "Raeto presets must bind to the canonical lifecycle deployment")
    data["adapters"].extend(raeto["adapters"])
    data["profiles"].extend(raeto["profiles"])
    adapters = {row["id"]: LifecycleAdapterRegistration(row["address"], row["codehash"],
        row["capabilities"], row["configVersion"], True) for row in data["adapters"]}
    profiles = []
    keys = [KnownLifecycleProfile(row["key"]) for row in data["profiles"]]
    if len(keys) != len(set(keys)) or set(keys) != set(KnownLifecycleProfile):
        raise LifecyclePlanningError("PROFILE_PRESET_MISMATCH", "Frozen catalogue must contain every known profile exactly once")
    for row in data["profiles"]:
        key = KnownLifecycleProfile(row["key"])
        registration = LifecycleProfileRegistration(*(row[field] for field in (
            "adapterId", "configSchema", "dependencyDigest", "venue", "factory", "hook", "capabilities", "enabled",
        )))
        topology = row["topology"]
        topology = ProfileTopologyV1(LifecycleHookTopology(topology["hookTopology"]), topology["configVersion"],
                                     topology["hookDeployer"], topology["hookCreationCodeHash"])
        adapter = adapters[row["adapterId"]]
        bound = topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4
        envelope = decode_launch_envelope(row["envelopeBytes"]) if bound else None
        creation_code = bytes.fromhex(row["creationCode"].removeprefix("0x")) if bound else None
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
                                     tuple(profiles), LifecycleDeploymentProvenance(provenance["sourceCommit"], provenance["deploymentBlock"]),
                                     hook_salt_miner="0x2f06176ba739b609b6a7334e2e6647558ba78ad5",
                                     launch_lens="0xcb4bb3caca71fe52eace8170cf83e901418646f8",
                                     launch_lens_code_hash="0xc19e86bfed47afbb6ff719293edd8fb8bb3399d5d017b3f533d90cc72e58f1d3",
                                     native_transaction_gas_limit=ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT)


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
