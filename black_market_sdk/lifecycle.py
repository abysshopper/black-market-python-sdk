"""Explicit opt-in, receipt-driven LaunchPlanV1 planning and execution builders.

Callers provide the orchestrator and creator. This module neither selects
production addresses nor signs/broadcasts transactions. An execution mode is
always explicit and never changes the economic commitment. Dependent admission
requires actual sequential execution, not disconnected calls to future pools.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any, Literal, TYPE_CHECKING, TypedDict

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address
from web3 import Web3

from .addresses import ROBINHOOD_MAINNET_CHAIN_ID, ROBINHOOD_MULTICALL3
from .abyss import ABYSS_FEE_TIERS
from .auction import AUCTION_QUOTE_OPTIONS as _AUCTION_QUOTE_OPTIONS
from .launch import assert_launch_topology_coverage, get_tick_at_sqrt_ratio
from .hmm import (
    HMM_FIXED_PROFILE_ID, HmmLaunchConfig, decode_hmm_launch_tail, encode_hmm_launch_tail,
    is_hmm_profile, validate_hmm_launch_config,
)
from .ufg import (
    UFG_LAUNCH_PROXY_ADDRESS, UfgLaunchConfig, is_ufg_profile, encode_ufg_launch_config,
    decode_ufg_launch_config, hash_ufg_launch_config, validate_ufg_launch_config,
    validate_ufg_launch_plan, _ufg_principal, _validate_ufg_identity,
    build_ufg_launch_transactions, build_ufg_cancel_transaction,
)
from .lifecycle_abis import (
    ABYSS_MARKET_CONFIG_COMPONENTS_V1, LAUNCH_DIRECTORY_V1_ABI, LAUNCH_FEE_HUB_V3_ABI,
    LAUNCH_FUNDING_ESCROW_V1_ABI, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI,
    LAUNCH_LIFECYCLE_V1_ABI, LAUNCH_MARKET_ADAPTER_V1_ABI, LAUNCH_PLAN_COMPONENTS_V1,
    LAUNCH_PROGRESS_COMPONENTS_V1, LAUNCH_RECEIPT_COMPONENTS_V1,
    LAUNCH_TOKEN_FACTORY_V1_ABI, TOKEN_CONFIG_COMPONENTS_V1,
    ADAPTER_REGISTRATION_COMPONENTS_V1, MARKET_IDENTITY_COMPONENTS_V1,
    POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1, POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V2,
    POOL_HOOK_DEPLOYER_V1_ABI, POOL_HOOK_DEPLOYER_CONFIG_V6_ABI,
    FIXED_FEE_POOL_HOOK_V1_ABI, FIXED_FEE_POOL_HOOK_CONFIG_V6_ABI,
    POOL_MARKET_ADAPTER_V1_ABI, PROFILE_TOPOLOGY_COMPONENTS_V1,
    PROFILE_REGISTRATION_COMPONENTS_V1, POOL_FEE_COLLECTOR_FACTORY_V1_ABI,
    POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI,
    HMM_FIXED_HOOK_V2_ABI, HMM_DYNAMIC_HOOK_V2_ABI, HMM_FEE_COLLECTOR_FACTORY_V2_ABI,
    V4_MARKET_CONFIG_COMPONENTS_V4, V4_MARKET_CONFIG_COMPONENTS_V5, V4_MARKET_CONFIG_COMPONENTS_V6,
    LAUNCH_BOUNDS_COMPONENTS_V2, LAUNCH_GRAPH_COMPONENTS_V2,
    LAUNCH_ENVELOPE_COMPONENTS_V2, DEVELOPER_TERMS_COMPONENTS_V3,
    SOURCE_TERMS_COMPONENTS_V3, LAUNCH_FEE_HUB_FACTORY_V3_ABI,
    MARKET_CONFIG_COMPONENTS_V1,
    SHARED_HOOK_DEPLOYER_V1_ABI, LIFECYCLE_ORACLE_FACTORY_ABI,
    LIFECYCLE_V4_HOOK_ABI, V4_FEE_LIQUIDITY_LOCKER_V2_ABI,
    NITRO_ARB_SYS_ABI, NITRO_ARB_GAS_INFO_ABI,
)
from .lifecycle_abis import (
    HOOK_SALT_MINER_ABI, LAUNCH_LENS_ABI, LAUNCH_LENS_SNAPSHOT_COMPONENTS,
)
from .lifecycle_rpc import (
    ControlledLaunchFork, LaunchBlock, LaunchExecutionLimits, LaunchLimitContext, LaunchRpcError, LaunchRpcSimulation,
    LaunchStateChanged, assert_canonical, assert_chain, create_controlled_launch_fork, hex_bytes, quantity, read_block, rpc, rpc_transaction, simulate_transactions,
    LaunchSimulationCall, execution_failure, lifecycle_stage, pinned_rpc, read_invocation, async_read_invocation,
    read_chain_id, read_gas_price, check_lifecycle_source, failure_reason,
    _LifecycleReadClient, _capability_facts, _read_lifecycle_account_code,
)

if TYPE_CHECKING:
    from .lifecycle_gas_profile import _ProfiledLaunchCapacity

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
# Snapshot release identities; editing the display catalog cannot grant coverage.
_PROFILED_QUOTE_IDENTITIES = frozenset(
    quote.address.lower() for quote in _AUCTION_QUOTE_OPTIONS if quote.address != ZERO_ADDRESS)
LIFECYCLE_PLAN_DOMAIN = keccak(text="BLACK_MARKET_LAUNCH_PLAN_V1")
NITRO_ARB_SYS_ADDRESS = "0x0000000000000000000000000000000000000064"
NITRO_ARB_GAS_INFO_ADDRESS = "0x000000000000000000000000000000000000006c"
NITRO_NODE_INTERFACE_ADDRESS = "0x00000000000000000000000000000000000000c8"
LIFECYCLE_TOKEN_ONLY_CAPABILITY = 1
LIFECYCLE_EMPTY_PREPARE_CAPABILITY = 2
LIFECYCLE_PERMANENT_CUSTODY_CAPABILITY = 8
LIFECYCLE_CANONICAL_FEES_CAPABILITY = 16
LIFECYCLE_ERC404_CAPABILITY = 32
LIFECYCLE_MULTI_POSITION_CAPABILITY = 64
# TOKEN_ONLY|EMPTY_PREPARE|PERMANENT_CUSTODY|CANONICAL_FEES. Preactivation
# safety combines launch-token transfer restrictions with canonical opening-state
# verification on both venues. Execution grouping never changes eligibility.
LIFECYCLE_REQUIRED_CAPABILITIES = (
    LIFECYCLE_TOKEN_ONLY_CAPABILITY | LIFECYCLE_EMPTY_PREPARE_CAPABILITY
    | LIFECYCLE_PERMANENT_CUSTODY_CAPABILITY | LIFECYCLE_CANONICAL_FEES_CAPABILITY
)
LifecycleLimitResolver = Callable[[Web3, LaunchLimitContext], LaunchExecutionLimits]
LIFECYCLE_MAX_ERC20_SUPPLY = (1 << 256) - 1
LIFECYCLE_MAX_REWARD_ERC20_SUPPLY = 10**77
LIFECYCLE_MAX_ERC404_SUPPLY = (1 << 96) - 1
LaunchExecutionMode = Literal["atomic", "staged"]
PoolBoundLifecycleConfigVersion = Literal[5, 6]


class LifecyclePlanningError(ValueError):
    """A stable SDK guard code with optional exact execution evidence."""

    def __init__(self, code: str, message: str, simulation: LaunchSimulation | None = None) -> None:
        self.code = code
        self.simulation = simulation
        super().__init__(message)


class LifecycleMode(IntEnum):
    ATOMIC = 0
    STAGED = 1


class LifecycleVenue(IntEnum):
    UNISWAP_V4 = 0
    ABYSS = 1


class LifecycleTokenKind(IntEnum):
    ERC20 = 0
    ERC404 = 1


class LifecycleRewardMode(IntEnum):
    NONE = 0
    STAKING = 1
    DIVIDENDS = 2


class LifecycleFundingKind(IntEnum):
    ERC20 = 0
    NATIVE_WRAP = 1
    SWAP = 2


class LifecyclePhase(IntEnum):
    NONE = 0
    PREPARING = 1
    READY = 2
    ACTIVATING = 3
    ACTIVE = 4
    CANCELLED = 5


class LifecycleHookTopology(IntEnum):
    NONE = 0
    SHARED_V4 = 1
    POOL_BOUND_V4 = 2


@dataclass(frozen=True)
class LifecyclePositionIdentity:
    canonical_id: bytes | str
    market_id: bytes | str
    manager: str
    custody: str
    token_id: int
    tick_lower: int
    tick_upper: int
    salt: bytes | str
    liquidity: int


@dataclass(frozen=True)
class LaunchExecutionContextV1:
    launch_id: bytes | str
    market_index: int
    operation: int
    adapter: str
    executor: str
    token: str
    quote_asset: str
    manager: str
    custody: str
    recipient: str
    amount: int


@dataclass(frozen=True)
class LaunchReceiptV1:
    launch_id: bytes | str
    plan_hash: bytes | str
    token: str
    fee_hub: str
    rewards: str
    market_count: int
    position_count: int
    quote_spent: tuple[int, ...]
    token_out: tuple[int, ...]


@dataclass(frozen=True)
class LifecycleTokenConfig:
    kind: LifecycleTokenKind | int
    reward_mode: LifecycleRewardMode | int
    name: str
    symbol: str
    supply: int
    nft_unit: int
    metadata_uri: str
    salt: bytes | str
    inventory_recipient: str
    burn_on_cancel: bool


@dataclass(frozen=True)
class LifecycleAssetFunding:
    asset: str
    amount: int
    kind: LifecycleFundingKind | int
    input_asset: str
    input_amount: int
    target: str
    data: bytes | str


@dataclass(frozen=True)
class LifecycleFeeAssetPolicy:
    asset: str
    owner_bps: int
    rewards_bps: int
    burn_bps: int


@dataclass(frozen=True)
class LifecycleMarketConfig:
    adapter_id: bytes | str
    profile_id: bytes | str
    quote_asset: str
    token_budget: int
    config_version: int
    config: bytes | str


@dataclass(frozen=True)
class LifecycleInitialBuy:
    market_index: int
    quote_amount_in: int
    min_token_out: int
    recipient: str
    sqrt_price_limit_x96: int


@dataclass(frozen=True)
class LaunchPlanV1:
    chain_id: int
    orchestrator: str
    creator: str
    nonce: int
    token: LifecycleTokenConfig
    funding: tuple[LifecycleAssetFunding, ...]
    fee_assets: tuple[LifecycleFeeAssetPolicy, ...]
    markets: tuple[LifecycleMarketConfig, ...]
    buys: tuple[LifecycleInitialBuy, ...]
    deadline: int
    executor_fee_bps: int


@dataclass(frozen=True)
class LifecycleV4Position:
    tick_lower: int
    tick_upper: int
    liquidity: int
    salt: bytes | str
    max_token_amount: int


@dataclass(frozen=True)
class LifecycleV4MarketConfig:
    version: Literal[4]
    lp_fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    hook_fee_pips: int
    fee_mode: int
    protocol_fee_denominator: int
    treasury: str
    external_liquidity_disabled: bool
    oracle_config_id: bytes | str
    profile_id: bytes | str
    terms_digest: bytes | str
    developer_beneficiary: str
    developer_fee_bps: int
    positions: tuple[LifecycleV4Position, ...]


@dataclass(frozen=True)
class LifecyclePoolBoundV4MarketConfigV5:
    version: Literal[5]
    lp_fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    hook_fee_pips: int
    fee_mode: int
    protocol_fee_denominator: int
    treasury: str
    external_liquidity_disabled: bool
    oracle_config_id: bytes | str
    hook_salt: bytes | str
    profile_id: bytes | str
    terms_digest: bytes | str
    developer_beneficiary: str
    developer_fee_bps: int
    positions: tuple[LifecycleV4Position, ...]


@dataclass(frozen=True)
class LifecyclePoolBoundV4MarketConfigV6:
    version: Literal[6]
    lp_fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    hook_fee_pips: int
    minimum_hook_fee_pips: int
    fee_sensitivity_pips_seconds_per_tick: int
    fee_mode: int
    protocol_fee_denominator: int
    treasury: str
    external_liquidity_disabled: bool
    oracle_config_id: bytes | str
    hook_salt: bytes | str
    profile_id: bytes | str
    terms_digest: bytes | str
    developer_beneficiary: str
    developer_fee_bps: int
    positions: tuple[LifecycleV4Position, ...]
    hmm_launch_config: HmmLaunchConfig | None = None
    ufg_launch_config: UfgLaunchConfig | None = None


LifecyclePoolBoundV4MarketConfig = LifecyclePoolBoundV4MarketConfigV5 | LifecyclePoolBoundV4MarketConfigV6


@dataclass(frozen=True)
class PoolBoundHookParametersV1:
    pool_manager: str
    registrar: str
    oracle_factory: str
    core: str
    liquidity_locker: str
    token: str
    quote_currency: str
    lp_fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    hook_fee_pips: int
    fee_mode: int
    protocol_fee_denominator: int
    treasury: str
    external_liquidity_disabled: bool
    oracle_config_id: bytes | str
    market_commitment: bytes | str
    expected_position_count: int


@dataclass(frozen=True)
class PoolBoundHookParametersV2(PoolBoundHookParametersV1):
    minimum_hook_fee_pips: int
    fee_sensitivity_pips_seconds_per_tick: int


PoolBoundHookParameters = PoolBoundHookParametersV1 | PoolBoundHookParametersV2


@dataclass(frozen=True)
class ProfileTopologyV1:
    hook_topology: LifecycleHookTopology | Literal[0, 1, 2]
    config_version: int
    hook_deployer: str
    hook_creation_code_hash: str


@dataclass(frozen=True)
class LaunchBoundsV2:
    minimum_tick_spacing: int
    maximum_tick_spacing: int
    maximum_positions: int
    maximum_oracle_cardinality: int
    fee_mode_flags: int


@dataclass(frozen=True)
class LaunchGraphV2:
    manager: str
    hook_root: str
    oracle_factory: str
    locker: str
    collector_factory: str
    collector_deployer: str
    hook_deployer: str
    core_code_hash: bytes | str
    manager_code_hash: bytes | str
    hook_runtime_code_hash: bytes | str
    oracle_factory_code_hash: bytes | str
    locker_code_hash: bytes | str
    collector_factory_code_hash: bytes | str
    collector_deployer_code_hash: bytes | str
    hook_deployer_code_hash: bytes | str
    hook_creation_code_hash: bytes | str
    code_chunk0: str
    code_chunk0_hash: bytes | str
    code_chunk1: str
    code_chunk1_hash: bytes | str
    shared_hook_salt: bytes | str


@dataclass(frozen=True)
class LaunchEnvelopeV2:
    artifact_digest: bytes | str
    review_manifest_digest: bytes | str
    config_bounds_digest: bytes | str
    terms_digest: bytes | str
    topology: LifecycleHookTopology | int
    config_version: int
    economic_version: int
    capabilities: int
    flags: int
    callback_flags: int
    callback_mask: int
    protocol_treasury: str
    protocol_fee_denominator: int
    beneficiary: str
    maximum_developer_fee_bps: int
    bounds: LaunchBoundsV2
    graph: LaunchGraphV2


@dataclass(frozen=True)
class LifecycleDeveloperTerms:
    adapter: str
    beneficiary: str
    maximum_developer_fee_bps: int
    terms_digest: bytes | str
    enabled: bool


@dataclass(frozen=True)
class SourceTermsV3:
    adapter: str
    profile_id: str
    terms_digest: str
    beneficiary: str
    maximum_developer_fee_bps: int
    developer_fee_bps: int


@dataclass(frozen=True)
class LifecycleAuthor:
    registry: str
    core: str
    factory: str
    author_id: str
    payout: str
    known: bool
    hub_count: int
    chain_id: int
    block_number: int
    block_hash: str


@dataclass(frozen=True)
class AuthorHubsPage(LifecycleAuthor):
    hubs: tuple[str, ...]
    offset: int
    next_offset: int
    total: int
    cursor_complete: bool


@dataclass(frozen=True)
class DeveloperFeeBalance:
    asset: str
    supported: bool
    claimable: int
    reserved: int


@dataclass(frozen=True)
class DeveloperFeeSource:
    source: str
    assets: tuple[str, ...]
    terms: SourceTermsV3


@dataclass(frozen=True)
class DeveloperFees(LifecycleAuthor):
    hub: str
    assets: tuple[str, ...]
    balances: tuple[DeveloperFeeBalance, ...]
    sources: tuple[DeveloperFeeSource, ...]


@dataclass(frozen=True)
class DeveloperDirectClaim:
    registry: str
    factory: str
    author_id: str
    hub: str
    asset: str
    kind: Literal["direct"] = "direct"


@dataclass(frozen=True)
class DeveloperPageClaim:
    registry: str
    factory: str
    author_id: str
    offset: int
    limit: int
    assets: tuple[str, ...]
    kind: Literal["page"] = "page"


LifecycleUnsignedTransaction = TypedDict("LifecycleUnsignedTransaction",
    {"chainId": int, "from": str, "to": str, "data": str, "value": int})


class DeveloperClaimTransaction(LifecycleUnsignedTransaction):
    claim: DeveloperDirectClaim | DeveloperPageClaim


@dataclass(frozen=True)
class DeveloperClaimResult:
    hub: str
    asset: str
    amount: int
    status: Literal[0, 1, 2, 3]
    outcome: Literal["paid", "zero", "unsupported", "failed"]
    error_selector: str
    payout: str | None = None


@dataclass(frozen=True)
class DeveloperClaimReceipt:
    execution_succeeded: bool
    outcome: Literal["observed", "unobserved", "reverted"]
    results: tuple[DeveloperClaimResult, ...]
    retryable_results: tuple[DeveloperClaimResult, ...]
    cursor_complete: bool
    payments_succeeded: bool
    offset: int | None = None
    next_offset: int | None = None
    total: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LifecycleAdapterRegistration:
    implementation: str
    code_hash: str
    capabilities: int
    config_version: int
    enabled: bool


@dataclass(frozen=True)
class LifecycleProfileRegistration:
    adapter_id: str
    config_schema: str
    dependency_digest: str
    venue: str
    factory: str
    hook: str
    capabilities: int
    enabled: bool


@dataclass(frozen=True)
class LifecycleProfileMetadata:
    id: str
    registration: LifecycleProfileRegistration
    adapter: LifecycleAdapterRegistration
    topology: ProfileTopologyV1
    venue_kind: Literal["uniswap-v4", "abyss", "unknown"]
    envelope: LaunchEnvelopeV2 | None = None
    developer_terms: LifecycleDeveloperTerms | None = None
    protocol_maximum_developer_fee_bps: int | None = None


@dataclass(frozen=True)
class LifecycleConstructionProfile(LifecycleProfileMetadata):
    metadata_source: Literal["preset"] = "preset"


@dataclass(frozen=True, kw_only=True)
class LifecycleProfile(LifecycleProfileMetadata):
    admitted: bool
    reason: str | None = None


def _profile_registration(values: Sequence[Any]) -> LifecycleProfileRegistration:
    return LifecycleProfileRegistration(*(hex_bytes(value) for value in values[:3]), *values[3:])


def _adapter_registration(values: Sequence[Any]) -> LifecycleAdapterRegistration:
    return LifecycleAdapterRegistration(values[0], hex_bytes(values[1]), *values[2:])


@dataclass(frozen=True)
class PoolBoundHookDeployment:
    deployer: str
    init_code_hash: str
    salt: str
    predicted_hook: str


@dataclass(frozen=True)
class PoolBoundLifecycleDeployment(PoolBoundHookDeployment):
    market_index: int


@dataclass(frozen=True)
class PoolBoundHookMiningProgress:
    attempts: int
    salt: str
    predicted_hook: str
    market_index: int | None = None


@dataclass(frozen=True)
class PoolBoundHookSalt:
    salt: str
    predicted_hook: str




@dataclass(frozen=True)
class PoolBoundHookDeploymentTransaction:
    to: str
    data: str
    value: Literal[0]
    deployment: PoolBoundHookDeployment

    def as_transaction(self) -> dict[str, Any]:
        return {"to": self.to, "data": self.data, "value": self.value}


@dataclass(frozen=True)
class LifecycleAbyssPosition:
    tick_lower: int
    tick_upper: int
    liquidity: int
    token_amount_maximum: int


@dataclass(frozen=True)
class LifecycleAbyssMarketConfig:
    profile: int
    fee: int
    oracle_config_id: bytes | str
    opening_sqrt_price_x96: int
    positions: tuple[LifecycleAbyssPosition, ...]


def _snake(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)).lower()


def canonical_abi_type(entry: Mapping[str, Any]) -> str:
    if entry["type"].startswith("tuple"):
        return "(" + ",".join(canonical_abi_type(child) for child in entry["components"]) + ")" + entry["type"][5:]
    return entry["type"]


def _tuple_type(components: Sequence[Mapping[str, Any]]) -> str:
    return "(" + ",".join(canonical_abi_type(entry) for entry in components) + ")"


LAUNCH_PLAN_V1_ABI_TYPE = _tuple_type(LAUNCH_PLAN_COMPONENTS_V1)
V4_LIFECYCLE_CONFIG_SCHEMA = keccak(text=_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V4))
V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5 = keccak(text=_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V5))
V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6 = keccak(text=_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V6))
ABYSS_LIFECYCLE_CONFIG_SCHEMA = keccak(text=_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1))
V4_POOL_BOUND_MARKET_ECONOMICS_DOMAIN = keccak(text="black-market.pool-bound-market-economics.v1")
V4_LIFECYCLE_HOOK_PERMISSION_MASK = 0x3FFF
V4_LIFECYCLE_HOOK_PERMISSIONS = 0x1AFC


def _uint(value: Any, bits: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit uint{bits}")
    return int(value)


def _address(value: Any, name: str, *, nonzero: bool = False) -> str:
    if not isinstance(value, str) or not is_address(value):
        raise ValueError(f"{name} must be an Ethereum address")
    if nonzero and int(value, 16) == 0:
        raise ValueError(f"{name} must be nonzero")
    return to_checksum_address(value)


def _abi_value(entry: Mapping[str, Any], value: Any) -> Any:
    kind = entry["type"]
    name = entry["name"]
    if kind.endswith("[]"):
        if not isinstance(value, Sequence) or isinstance(value, (bytes, str)):
            raise ValueError(f"{name} must be an ordered sequence")
        return tuple(_abi_value({**entry, "type": kind[:-2]}, child) for child in value)
    if kind == "tuple":
        components = entry["components"]
        if isinstance(value, Mapping):
            expected = {component["name"] for component in components}
            if set(value) != expected:
                raise ValueError(f"{name} fields must exactly match the versioned Solidity tuple")
            return tuple(_abi_value(component, value[component["name"]]) for component in components)
        return tuple(_abi_value(component, getattr(value, _snake(component["name"]))) for component in components)
    if kind == "address":
        return _address(value, name)
    if kind.startswith("uint"):
        return _uint(value, int(kind[4:]), name)
    if kind.startswith("int"):
        bits = int(kind[3:])
        if isinstance(value, bool) or not isinstance(value, int) or not -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
            raise ValueError(f"{name} must fit {kind}")
        return int(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
        return value
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value
    if kind.startswith("bytes"):
        raw = bytes.fromhex(hex_bytes(value)[2:])
        if kind != "bytes" and len(raw) != int(kind[5:]):
            raise ValueError(f"{name} must be exactly {kind[5:]} bytes")
        return raw
    raise ValueError(f"unsupported ABI type {kind}")


def _struct_tuple(components: Sequence[Mapping[str, Any]], value: Any) -> tuple[Any, ...]:
    return _abi_value({"name": "struct", "type": "tuple", "components": components}, value)


def _json_tuple(components: Sequence[Mapping[str, Any]], values: Sequence[Any]) -> dict[str, Any]:
    def convert(entry: Mapping[str, Any], value: Any) -> Any:
        kind = entry["type"]
        if kind.endswith("[]"):
            return [convert({**entry, "type": kind[:-2]}, child) for child in value]
        if kind == "tuple":
            return _json_tuple(entry["components"], value)
        if kind.startswith("bytes"):
            return hex_bytes(value)
        if kind.startswith("uint") or kind.startswith("int"):
            bits = int(kind[4:] if kind.startswith("uint") else kind[3:])
            return str(value) if bits >= 64 else int(value)
        return value
    return {entry["name"]: convert(entry, value) for entry, value in zip(components, values)}


def _parse_json_tuple(components: Sequence[Mapping[str, Any]], value: Mapping[str, Any]) -> tuple[Any, ...]:
    def parse(entry: Mapping[str, Any], item: Any) -> Any:
        kind = entry["type"]
        if kind.endswith("[]"):
            if not isinstance(item, list):
                raise ValueError(f"{entry['name']} must be an array")
            return tuple(parse({**entry, "type": kind[:-2]}, child) for child in item)
        if kind == "tuple":
            return _parse_json_tuple(entry["components"], item)
        if kind.startswith("uint") or kind.startswith("int"):
            if isinstance(item, str):
                if not re.fullmatch(r"[0-9]+" if kind.startswith("uint") else r"-?[0-9]+", item):
                    raise ValueError(f"{entry['name']} must be an exact decimal integer")
                item = int(item)
            elif isinstance(item, bool) or not isinstance(item, int) or abs(item) > (1 << 53) - 1:
                raise ValueError(f"{entry['name']} needs an exact decimal string or safe JSON integer")
        if kind == "address":
            if not isinstance(item, str) or not is_address(item):
                raise ValueError(f"{entry['name']} must be an address")
            return item
        if kind.startswith("bytes") and isinstance(item, str):
            _abi_value(entry, item)
            return item
        return _abi_value(entry, item)
    if not isinstance(value, Mapping) or not {entry["name"] for entry in components}.issubset(value):
        raise ValueError("the JSON plan requires every versioned Solidity tuple field")
    return tuple(parse(entry, value[entry["name"]]) for entry in components)


def launch_plan_from_dict(value: Mapping[str, Any]) -> LaunchPlanV1:
    """Load a portable Solidity camelCase plan with exact decimal quantities."""
    values = _parse_json_tuple(LAUNCH_PLAN_COMPONENTS_V1, value)
    plan = LaunchPlanV1(
        *values[:4], LifecycleTokenConfig(*values[4]),
        tuple(LifecycleAssetFunding(*item) for item in values[5]),
        tuple(LifecycleFeeAssetPolicy(*item) for item in values[6]),
        tuple(LifecycleMarketConfig(*item) for item in values[7]),
        tuple(LifecycleInitialBuy(*item) for item in values[8]), *values[9:],
    )
    if int(plan.token.kind) not in (0, 1) or int(plan.token.reward_mode) not in (0, 1, 2) or any(int(funding.kind) not in (0, 1, 2) for funding in plan.funding):
        raise ValueError("Unsupported token, reward or funding kind")
    to_launch_plan_tuple(plan)
    return plan


def to_launch_plan_tuple(plan: LaunchPlanV1) -> tuple[Any, ...]:
    """Encode exact wire widths without claiming economic execution validity."""
    return _struct_tuple(LAUNCH_PLAN_COMPONENTS_V1, plan)


def _assert_allocation(plan: LaunchPlanV1) -> int:
    if sum(market.token_budget for market in plan.markets) != plan.token.supply:
        raise LifecyclePlanningError("INVALID_TOKEN_BUDGET", "Market token budgets must sum exactly to committed supply")
    count = 0
    for market in plan.markets:
        if market.config_version == 1:
            positions = decode_lifecycle_abyss_market_config(market.config).positions
            maxima = sum(position.token_amount_maximum for position in positions)
        elif market.config_version == 4 or is_pool_bound_v4_config_version(market.config_version):
            config = decode_lifecycle_v4_market_config(market.config) if market.config_version == 4 else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
            positions = config.positions
            maxima = sum(position.max_token_amount for position in positions)
        else:
            raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", "Allocation requires an explicit supported position schema")
        if not 1 <= len(positions) <= 32:
            raise LifecyclePlanningError("INVALID_POSITION_COUNT", "Every market requires one through 32 positions")
        if maxima != market.token_budget:
            raise LifecyclePlanningError("INVALID_TOKEN_BUDGET", "Position token maxima must sum exactly to their market budget")
        count += len(positions)
    if count > 32:
        raise LifecyclePlanningError("INVALID_POSITION_COUNT", "A launch commits at most 32 total positions across every venue")
    return count


def _validate_launch_plan(plan: LaunchPlanV1) -> None:
    to_launch_plan_tuple(plan)
    if not 1 <= len(plan.markets) <= 16 or len(plan.buys) > 64 or len(plan.funding) > 8 or not 1 <= len(plan.fee_assets) <= 8 or plan.token.supply <= 0 or int(plan.token.inventory_recipient, 16) == 0 or plan.token.inventory_recipient.lower() == plan.orchestrator.lower() or plan.executor_fee_bps > 1000:
        raise LifecyclePlanningError("INVALID_PLAN", "Plan exceeds lifecycle bounds or lacks positive token economics")
    if (int(plan.token.kind) == 1 and plan.token.supply > LIFECYCLE_MAX_ERC404_SUPPLY) or (int(plan.token.kind) == 0 and int(plan.token.reward_mode) != 0 and plan.token.supply > LIFECYCLE_MAX_REWARD_ERC20_SUPPLY):
        raise LifecyclePlanningError("INVALID_TOKEN_SUPPLY", "Reward-enabled ERC20 supply must not exceed 10^77; ERC404 supply must fit uint96")
    if not 1 <= len(plan.token.name.encode()) <= 128 or not 1 <= len(plan.token.symbol.encode()) <= 32 or len(plan.token.metadata_uri.encode()) > 2048:
        raise LifecyclePlanningError("INVALID_PLAN", "Token metadata exceeds the committed byte bounds")
    previous = 0
    for policy in plan.fee_assets:
        if int(policy.asset, 16) <= previous or policy.owner_bps + policy.rewards_bps + policy.burn_bps != 10000:
            raise LifecyclePlanningError("INVALID_FEE_POLICY", "Fee assets must be ascending with fractions summing to 10000")
        previous = int(policy.asset, 16)
    if any(policy.rewards_bps != 0 for policy in plan.fee_assets) != (int(plan.token.reward_mode) != 0):
        raise LifecyclePlanningError("INVALID_FEE_POLICY", "Reward mode and committed rewards shares must agree")
    previous = 0
    for funding in plan.funding:
        if int(funding.asset, 16) <= previous or funding.amount <= 0 or funding.input_amount <= 0:
            raise LifecyclePlanningError("INVALID_FUNDING", "Funding output assets must be positive, unique and ascending")
        if int(funding.kind) != 2 and (funding.input_asset.lower() != funding.asset.lower() or funding.input_amount != funding.amount or int(funding.target, 16) != 0 or hex_bytes(funding.data) != "0x"):
            raise LifecyclePlanningError("INVALID_FUNDING", "Direct funding requires exact asset/amount and no target call")
        if int(funding.kind) == 2 and (funding.input_asset.lower() == funding.asset.lower() or not 1 <= len(bytes.fromhex(hex_bytes(funding.data)[2:])) <= 16384):
            raise LifecyclePlanningError("INVALID_FUNDING", "Conversion funding must change assets and commit bounded nonempty calldata")
        previous = int(funding.asset, 16)
    for buy in plan.buys:
        if buy.market_index >= len(plan.markets) or buy.quote_amount_in <= 0 or buy.min_token_out <= 0 or int(buy.recipient, 16) == 0 or buy.recipient.lower() == plan.orchestrator.lower():
            raise LifecyclePlanningError("INVALID_BUY", "Each ordered buy needs a market, positive constraints and external recipient")
    v4_quotes: set[str] = set()
    for market in plan.markets:
        if market.token_budget <= 0 or not 1 <= len(bytes.fromhex(hex_bytes(market.config)[2:])) <= 16384:
            raise LifecyclePlanningError("INVALID_MARKET", "Each market needs a positive budget and bounded versioned config")
        if market.config_version == 4 or is_pool_bound_v4_config_version(market.config_version):
            config = decode_lifecycle_v4_market_config(market.config) if market.config_version == 4 else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
            if hex_bytes(config.profile_id) != hex_bytes(market.profile_id):
                raise LifecyclePlanningError("PROFILE_TERMS_MISMATCH", "Inner config profile differs from the committed market")
            if market.quote_asset.lower() in v4_quotes:
                raise LifecyclePlanningError("DUPLICATE_V4_QUOTE", "Only one V4 market per quote is allowed")
            v4_quotes.add(market.quote_asset.lower())
            if config.lp_fee_pips >= 1000000 or config.hook_fee_pips > (1000000 if config.version == 6 else 999999) or config.fee_mode > 1 or not 1 <= config.tick_spacing <= 32767 or not 4295128739 <= config.sqrt_price_x96 < 1461446703485210103287273052203988822378723970342 or (config.protocol_fee_denominator != 0 and (not 4 <= config.protocol_fee_denominator <= 10 or int(config.treasury, 16) == 0)):
                raise LifecyclePlanningError("INVALID_MARKET", "V4 market violates lifecycle fee, price or treasury bounds")
    _assert_allocation(plan)
    funding_by_asset = {funding.asset.lower(): funding.amount for funding in plan.funding}
    requirements: dict[str, int] = {}
    for buy in plan.buys:
        asset = plan.markets[buy.market_index].quote_asset.lower()
        requirements[asset] = requirements.get(asset, 0) + buy.quote_amount_in
    if any(funding_by_asset.get(asset, 0) < amount for asset, amount in requirements.items()):
        raise LifecyclePlanningError("INVALID_FUNDING", "Quote funding must cover all ordered buys")


def _assert_plan_topology(
    plan: LaunchPlanV1, token: str, *, allow_topology_gaps: bool,
    allow_missing_permanent_tail: bool,
) -> None:
    for index, market in enumerate(plan.markets):
        if market.config_version == 1:
            config = decode_lifecycle_abyss_market_config(market.config)
            tier = next((tier for tier in ABYSS_FEE_TIERS if tier.fee_pips == config.fee), None)
            if tier is None:
                raise LifecyclePlanningError("INVALID_MARKET", "Abyss fee has no canonical tick spacing")
            spacing, price = tier.tick_spacing, config.opening_sqrt_price_x96
            positions = tuple(dict(tick_lower=position.tick_lower, tick_upper=position.tick_upper,
                max_token_amount=position.token_amount_maximum) for position in config.positions)
        else:
            config = decode_lifecycle_v4_market_config(market.config) if market.config_version == 4 else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
            spacing, price, positions = config.tick_spacing, config.sqrt_price_x96, config.positions
        try:
            assert_launch_topology_coverage(positions, opening_tick=get_tick_at_sqrt_ratio(price),
                tick_spacing=spacing, launched_token_is_quote=int(token, 16) < int(market.quote_asset, 16),
                allow_interior_gaps=allow_topology_gaps)
        except ValueError as error:
            code, separator, message = str(error).partition(": ")
            if code == "MISSING_PERMANENT_TAIL" and allow_missing_permanent_tail:
                continue
            if separator and code in {"TOPOLOGY_OPENING_GAP", "TOPOLOGY_INTERIOR_GAP",
                "MISSING_PERMANENT_TAIL", "TOPOLOGY_OFF_GRID_TICK"}:
                raise LifecyclePlanningError(code, f"Market {index}: {message}") from None
            raise


def launch_plan_to_dict(plan: LaunchPlanV1) -> dict[str, Any]:
    """Preserve committed text/casing and serialize wide quantities losslessly."""
    to_launch_plan_tuple(plan)
    def convert(entry: Mapping[str, Any], value: Any) -> Any:
        kind = entry["type"]
        if kind.endswith("[]"):
            return [convert({**entry, "type": kind[:-2]}, item) for item in value]
        if kind == "tuple":
            return {child["name"]: convert(child, getattr(value, _snake(child["name"]))) for child in entry["components"]}
        if kind.startswith("uint") or kind.startswith("int"):
            bits = int(kind[4:] if kind.startswith("uint") else kind[3:])
            return str(value) if bits >= 64 else int(value)
        return hex_bytes(value) if kind.startswith("bytes") and not isinstance(value, str) else value
    return convert({"type": "tuple", "components": LAUNCH_PLAN_COMPONENTS_V1}, plan)


def serialize_launch_plan(plan: LaunchPlanV1) -> str:
    return json.dumps(launch_plan_to_dict(plan), separators=(",", ":"), ensure_ascii=False)


def parse_launch_plan(value: str) -> LaunchPlanV1:
    return launch_plan_from_dict(json.loads(value))


def encode_launch_plan(plan: LaunchPlanV1) -> bytes:
    return abi_encode([LAUNCH_PLAN_V1_ABI_TYPE], [to_launch_plan_tuple(plan)])


def hash_launch_plan(plan: LaunchPlanV1) -> str:
    return hex_bytes(keccak(abi_encode(["bytes32", LAUNCH_PLAN_V1_ABI_TYPE], [LIFECYCLE_PLAN_DOMAIN, to_launch_plan_tuple(plan)])))


def hash_launch_identity(*, chain_id: int, orchestrator: str, creator: str, nonce: int) -> str:
    return hex_bytes(keccak(abi_encode(["uint256", "address", "address", "uint256"], [
        _uint(chain_id, 256, "chainId"), _address(orchestrator, "orchestrator"),
        _address(creator, "creator"), _uint(nonce, 256, "nonce")])))


def launch_id_of(plan: LaunchPlanV1) -> str:
    return hash_launch_identity(chain_id=plan.chain_id, orchestrator=plan.orchestrator, creator=plan.creator, nonce=plan.nonce)


def encode_lifecycle_v4_market_config(config: LifecycleV4MarketConfig | Mapping[str, Any]) -> bytes:
    values = _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V4, config)
    if values[0] != 4:
        raise ValueError("V4 lifecycle market config version must be 4")
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V4)], [values])




def decode_lifecycle_v4_market_config(data: bytes | str) -> LifecycleV4MarketConfig:
    raw = bytes.fromhex(hex_bytes(data)[2:])
    values = abi_decode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V4)], raw)[0]
    config = LifecycleV4MarketConfig(*values[:-1], tuple(LifecycleV4Position(*position) for position in values[-1]))
    if encode_lifecycle_v4_market_config(config) != raw:
        raise ValueError("shared V4 config is not its exact canonical V4 encoding")
    return config


def is_pool_bound_v4_config_version(version: int) -> bool:
    return not isinstance(version, bool) and version in (5, 6)


def pool_bound_v4_lifecycle_config_schema(version: PoolBoundLifecycleConfigVersion) -> bytes:
    if version == 5:
        return V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5
    if version == 6:
        return V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6
    raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", "Pool-bound config version must be explicitly 5 or 6")


def _bound_components(version: int) -> Sequence[Mapping[str, Any]]:
    pool_bound_v4_lifecycle_config_schema(version)
    return V4_MARKET_CONFIG_COMPONENTS_V5 if version == 5 else V4_MARKET_CONFIG_COMPONENTS_V6


def _bound_parameter_components(version: int) -> Sequence[Mapping[str, Any]]:
    pool_bound_v4_lifecycle_config_schema(version)
    return POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1 if version == 5 else POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V2


def _bound_deployer_abi(version: int) -> Sequence[Mapping[str, Any]]:
    pool_bound_v4_lifecycle_config_schema(version)
    return POOL_HOOK_DEPLOYER_V1_ABI if version == 5 else POOL_HOOK_DEPLOYER_CONFIG_V6_ABI


def validate_pool_bound_hook_fees(config: LifecyclePoolBoundV4MarketConfigV6 | PoolBoundHookParametersV2 | Mapping[str, Any]) -> None:
    def field(snake: str, camel: str) -> Any:
        return config.get(camel) if isinstance(config, Mapping) else getattr(config, snake, None)
    maximum = field("hook_fee_pips", "hookFeePips")
    minimum = field("minimum_hook_fee_pips", "minimumHookFeePips")
    sensitivity = field("fee_sensitivity_pips_seconds_per_tick", "feeSensitivityPipsSecondsPerTick")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (maximum, minimum, sensitivity)) or not 0 <= minimum <= maximum <= 1_000_000 or not 0 <= sensitivity <= 0xFFFFFFFF:
        raise LifecyclePlanningError("INVALID_HOOK_FEES", "Require integer 0 <= minimum <= maximum <= 1000000 pips and uint32 fee-pips * seconds/tick sensitivity")


def _v1_fee_shape(value: Any) -> None:
    if isinstance(value, Mapping):
        has_v2 = "minimumHookFeePips" in value or "feeSensitivityPipsSecondsPerTick" in value
    else:
        has_v2 = hasattr(value, "minimum_hook_fee_pips") or hasattr(value, "fee_sensitivity_pips_seconds_per_tick")
    if has_v2:
        raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", "Config5/V1 cannot contain config6/V2 fee-policy fields")


def encode_lifecycle_pool_bound_v4_market_config(config: LifecyclePoolBoundV4MarketConfig | Mapping[str, Any]) -> bytes:
    version = config["version"] if isinstance(config, Mapping) else config.version
    components = _bound_components(version)
    if version == 6:
        validate_pool_bound_hook_fees(config)
    else:
        _v1_fee_shape(config)
    prefix = abi_encode([_tuple_type(components)], [_struct_tuple(components, config)])
    profile_id = config["profileId"] if isinstance(config, Mapping) else config.profile_id
    hmm = config.get("hmmLaunchConfig") if isinstance(config, Mapping) else getattr(config, "hmm_launch_config", None)
    ufg = config.get("ufgLaunchConfig") if isinstance(config, Mapping) else getattr(config, "ufg_launch_config", None)
    if is_ufg_profile(profile_id):
        if version != 6 or ufg is None or hmm is not None:
            raise LifecyclePlanningError("INVALID_UFG_PLAN", "UFG profiles require config6 and only an explicit UFG launch tail")
        lp_fee = config["lpFeePips"] if isinstance(config, Mapping) else config.lp_fee_pips
        fee_mode = config["feeMode"] if isinstance(config, Mapping) else config.fee_mode
        if lp_fee != 0 or fee_mode != 1:
            raise LifecyclePlanningError("INVALID_UFG_PLAN", "UFG profiles require QuoteOnly fees and zero LP fees")
        return prefix + encode_ufg_launch_config(ufg)
    if ufg is not None:
        raise LifecyclePlanningError("INVALID_UFG_PLAN", "Only UFG profiles accept a UFG launch tail")
    if is_hmm_profile(profile_id):
        if version != 6 or hmm is None:
            raise LifecyclePlanningError("INVALID_HMM_CONFIG", "HMM profiles require config6 and an explicit HMM launch config")
        lp_fee = config["lpFeePips"] if isinstance(config, Mapping) else config.lp_fee_pips
        developer_fee = config["developerFeeBps"] if isinstance(config, Mapping) else config.developer_fee_bps
        if lp_fee != 0 or developer_fee != 0:
            raise LifecyclePlanningError("INVALID_HMM_CONFIG", "HMM V2 requires zero LP and developer fees")
        return prefix + encode_hmm_launch_tail(hmm)
    if hmm is not None:
        raise LifecyclePlanningError("INVALID_HMM_CONFIG", "Only HMM profiles accept an HMM launch tail")
    return prefix


def decode_lifecycle_pool_bound_v4_market_config(data: bytes | str, *, expected_version: PoolBoundLifecycleConfigVersion | None = None) -> LifecyclePoolBoundV4MarketConfig:
    raw = bytes.fromhex(hex_bytes(data)[2:])
    offset, version = abi_decode(["uint256", "uint16"], raw[:64])
    components = _bound_components(version)
    if offset != 32 or expected_version is not None and version != expected_version:
        raise LifecyclePlanningError("INVALID_BOUND_MARKET", "Pool-bound wire config differs from its committed explicit version")
    values = abi_decode([_tuple_type(components)], raw)[0]
    cls = LifecyclePoolBoundV4MarketConfigV5 if version == 5 else LifecyclePoolBoundV4MarketConfigV6
    config = cls(*values[:-1], tuple(LifecycleV4Position(*position) for position in values[-1]))
    prefix = abi_encode([_tuple_type(components)], [_struct_tuple(components, config)])
    if is_hmm_profile(config.profile_id):
        if version != 6 or raw[:len(prefix)] != prefix:
            raise LifecyclePlanningError("INVALID_HMM_CONFIG", "HMM profiles require an exact canonical config6 prefix")
        config = replace(config, hmm_launch_config=decode_hmm_launch_tail(raw[len(prefix):]))
    elif is_ufg_profile(config.profile_id):
        if version != 6 or raw[:len(prefix)] != prefix:
            raise LifecyclePlanningError("INVALID_UFG_PLAN", "UFG profiles require an exact canonical config6 prefix")
        config = replace(config, ufg_launch_config=decode_ufg_launch_config(raw[len(prefix):]))
    if encode_lifecycle_pool_bound_v4_market_config(config) != raw:
        raise LifecyclePlanningError("INVALID_BOUND_MARKET", "Pool-bound config is not canonical for its explicit schema")
    return config


def encode_pool_bound_hook_parameters(parameters: PoolBoundHookParameters | Mapping[str, Any], *, config_version: PoolBoundLifecycleConfigVersion) -> bytes:
    components = _bound_parameter_components(config_version)
    if config_version == 6:
        validate_pool_bound_hook_fees(parameters)
    else:
        _v1_fee_shape(parameters)
    return abi_encode([_tuple_type(components)], [_struct_tuple(components, parameters)])


def pool_bound_hook_init_code_hash(creation_code: bytes | str, parameters: PoolBoundHookParameters, *, config_version: PoolBoundLifecycleConfigVersion) -> str:
    return hex_bytes(keccak(bytes.fromhex(hex_bytes(creation_code)[2:]) + encode_pool_bound_hook_parameters(parameters, config_version=config_version)))


def build_pool_bound_hook_parameters(
    *, plan: LaunchPlanV1, market_index: int, token: str, pool_manager: str,
    registrar: str, oracle_factory: str, liquidity_locker: str,
) -> PoolBoundHookParameters:
    market = plan.markets[market_index]
    config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
    common = dict(pool_manager=pool_manager, registrar=registrar, oracle_factory=oracle_factory,
                  core=plan.orchestrator, liquidity_locker=liquidity_locker, token=token,
                  quote_currency=market.quote_asset, lp_fee_pips=config.lp_fee_pips,
                  tick_spacing=config.tick_spacing, sqrt_price_x96=config.sqrt_price_x96,
                  hook_fee_pips=config.hook_fee_pips, fee_mode=config.fee_mode,
                  protocol_fee_denominator=config.protocol_fee_denominator, treasury=config.treasury,
                  external_liquidity_disabled=config.external_liquidity_disabled,
                  oracle_config_id=config.oracle_config_id,
                  market_commitment=pool_bound_market_commitment(plan, token=token, registrar=registrar, market_index=market_index),
                  expected_position_count=len(config.positions))
    if config.version == 6:
        return PoolBoundHookParametersV2(**common, minimum_hook_fee_pips=config.minimum_hook_fee_pips,
                                        fee_sensitivity_pips_seconds_per_tick=config.fee_sensitivity_pips_seconds_per_tick)
    return PoolBoundHookParametersV1(**common)


def pool_bound_market_commitment(plan: LaunchPlanV1, *, token: str, registrar: str, market_index: int) -> str:
    """Commit the canonical market prefix, normalizing salt and excluding launch tails."""
    _uint(market_index, 32, "marketIndex")
    if market_index >= len(plan.markets):
        raise ValueError("market_index is outside the economic plan")
    market = plan.markets[market_index]
    if not is_pool_bound_v4_config_version(market.config_version):
        raise LifecyclePlanningError("INVALID_BOUND_MARKET", "Market must select explicit pool-bound config5 or config6")
    config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
    if hex_bytes(config.profile_id) != hex_bytes(market.profile_id):
        raise ValueError("inner reviewed profileId differs from its outer market binding")
    normalized = replace(config, hook_salt=ZERO_HASH)
    config_hash = keccak(abi_encode([_tuple_type(_bound_components(config.version))],
                                  [_struct_tuple(_bound_components(config.version), normalized)]))
    return hex_bytes(keccak(abi_encode(
        ["bytes32", "uint256", "address", "address", "address", "bytes32", "bytes32", "address", "uint256", "uint32", "bytes32"],
        [V4_POOL_BOUND_MARKET_ECONOMICS_DOMAIN, _uint(plan.chain_id, 256, "chainId"),
         _address(plan.orchestrator, "core", nonzero=True), _address(registrar, "registrar", nonzero=True),
         _address(token, "token", nonzero=True), bytes.fromhex(hex_bytes(market.adapter_id)[2:]),
         bytes.fromhex(hex_bytes(market.profile_id)[2:]), _address(market.quote_asset, "quoteAsset", nonzero=True),
         _uint(market.token_budget, 256, "tokenBudget"), _uint(market.config_version, 32, "configVersion"), config_hash],
    )))


def predict_pool_bound_hook_address(*, deployer: str, init_code_hash: bytes | str, salt: bytes | str) -> str:
    deployer = _address(deployer, "deployer")
    init_hash = _abi_value({"name": "initCodeHash", "type": "bytes32"}, init_code_hash)
    salt_bytes = _abi_value({"name": "salt", "type": "bytes32"}, salt)
    return to_checksum_address(keccak(b"\xff" + bytes.fromhex(deployer[2:]) + salt_bytes + init_hash)[12:])


def valid_pool_bound_hook_address(hook: str) -> bool:
    return int(_address(hook, "hook"), 16) & V4_LIFECYCLE_HOOK_PERMISSION_MASK == V4_LIFECYCLE_HOOK_PERMISSIONS


def encode_lifecycle_abyss_market_config(config: LifecycleAbyssMarketConfig | Mapping[str, Any]) -> bytes:
    profile = config["profile"] if isinstance(config, Mapping) else config.profile
    if isinstance(profile, bool) or not isinstance(profile, int) or not 0 <= profile <= 3:
        raise ValueError("Unsupported Abyss lifecycle pool profile")
    return abi_encode([_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1)], [_struct_tuple(ABYSS_MARKET_CONFIG_COMPONENTS_V1, config)])


def decode_lifecycle_abyss_market_config(data: bytes | str) -> LifecycleAbyssMarketConfig:
    values = abi_decode([_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1)], bytes.fromhex(hex_bytes(data)[2:]))[0]
    return LifecycleAbyssMarketConfig(*values[:-1], tuple(LifecycleAbyssPosition(*position) for position in values[-1]))


def encode_launch_bounds(bounds: LaunchBoundsV2) -> bytes:
    return abi_encode([_tuple_type(LAUNCH_BOUNDS_COMPONENTS_V2)], [_struct_tuple(LAUNCH_BOUNDS_COMPONENTS_V2, bounds)])


def decode_launch_bounds(data: bytes | str) -> LaunchBoundsV2:
    return LaunchBoundsV2(*abi_decode([_tuple_type(LAUNCH_BOUNDS_COMPONENTS_V2)], bytes.fromhex(hex_bytes(data)[2:]))[0])


def hash_launch_bounds(bounds: LaunchBoundsV2) -> str:
    return hex_bytes(keccak(encode_launch_bounds(bounds)))


def encode_launch_envelope(envelope: LaunchEnvelopeV2) -> bytes:
    return abi_encode([_tuple_type(LAUNCH_ENVELOPE_COMPONENTS_V2)], [_struct_tuple(LAUNCH_ENVELOPE_COMPONENTS_V2, envelope)])


def decode_launch_envelope(data: bytes | str) -> LaunchEnvelopeV2:
    values = abi_decode([_tuple_type(LAUNCH_ENVELOPE_COMPONENTS_V2)], bytes.fromhex(hex_bytes(data)[2:]))[0]
    if values[4] not in (1, 2):
        raise ValueError("Unsupported reviewed hook topology")
    return LaunchEnvelopeV2(*values[:-2], LaunchBoundsV2(*values[-2]), LaunchGraphV2(*values[-1]))


def hash_lifecycle_profile(envelope: LaunchEnvelopeV2) -> str:
    return hex_bytes(_profile_id(envelope))


def hash_launch_dependencies(*, chain_id: int, core: str, registry: str, registrar: str, graph: LaunchGraphV2) -> str:
    types = ["bytes32", "uint256", "address", "bytes32", "address", "address"]
    values: list[Any] = [keccak(text="black-market.v4-dependencies.v2"), chain_id, core,
                         bytes.fromhex(hex_bytes(graph.core_code_hash)[2:]), registry, registrar]
    for address, code_hash in (
        (graph.manager, graph.manager_code_hash), (graph.hook_root, graph.hook_runtime_code_hash),
        (graph.oracle_factory, graph.oracle_factory_code_hash), (graph.locker, graph.locker_code_hash),
        (graph.collector_factory, graph.collector_factory_code_hash),
        (graph.collector_deployer, graph.collector_deployer_code_hash),
        (graph.hook_deployer, graph.hook_deployer_code_hash),
    ):
        types.extend(("address", "bytes32"))
        values.extend((address, bytes.fromhex(hex_bytes(code_hash)[2:])))
    types.append("bytes32")
    values.append(bytes.fromhex(hex_bytes(graph.hook_creation_code_hash)[2:]))
    for address, code_hash in ((graph.code_chunk0, graph.code_chunk0_hash), (graph.code_chunk1, graph.code_chunk1_hash)):
        types.extend(("address", "bytes32"))
        values.extend((address, bytes.fromhex(hex_bytes(code_hash)[2:])))
    return hex_bytes(keccak(abi_encode(types, values)))


def _entry(abi: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return next(entry for entry in abi if entry["type"] == "function" and entry["name"] == name)


def _encode_function(abi: Sequence[Mapping[str, Any]], name: str, args: Sequence[Any]) -> str:
    entry = _entry(abi, name)
    types = [canonical_abi_type(item) for item in entry["inputs"]]
    return hex_bytes(keccak(text=name + "(" + ",".join(types) + ")")[:4] + abi_encode(types, args))


def _call(client: Web3, target: str, abi: Sequence[Mapping[str, Any]], name: str, args: Sequence[Any], block: LaunchBlock, *, sender: str | None = None) -> Any:
    transaction = {"to": _address(target, name), "data": _encode_function(abi, name, args)}
    if sender is not None:
        transaction["from"] = sender
    raw = pinned_rpc(client, "eth_call", [transaction, block.tag], block)
    outputs = _entry(abi, name)["outputs"]
    values = abi_decode([canonical_abi_type(item) for item in outputs], bytes.fromhex(hex_bytes(raw)[2:]))
    return values[0] if len(values) == 1 else values


def _read_token_factory_binding(client: Web3, orchestrator: str, block: LaunchBlock, chain_id: int) -> tuple[str, str]:
    from .lifecycle_presets import get_known_lifecycle_deployment
    preset = get_known_lifecycle_deployment(chain_id=chain_id, orchestrator=orchestrator)
    if preset is not None:
        return preset.token_factory, preset.token_factory_code_hash
    factory = _address(_call(client, orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "tokenFactory", [], block), "token factory", nonzero=True)
    code = bytes.fromhex(hex_bytes(pinned_rpc(client, "eth_getCode", [factory, block.tag], block))[2:])
    if not code or _call(client, factory, LAUNCH_TOKEN_FACTORY_V1_ABI, "core", [], block).lower() != orchestrator.lower():
        raise ValueError("token factory code or core binding differs from the selected lifecycle orchestrator")
    return factory, hex_bytes(keccak(code))


_PROFILE_DEPENDENCY_ABI = [*POOL_MARKET_ADAPTER_V1_ABI,
    {"type": "function", "name": "factory", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
]

_ORACLE_FACTORY_ABI = [
    {"type": "function", "name": "oracleConfigs", "stateMutability": "view",
     "inputs": [{"name": "", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint24"}, {"name": "", "type": "uint16"}]},
]


def _profile_topology(client: Web3, registry: str, profile_id: bytes, block: LaunchBlock) -> ProfileTopologyV1:
    values = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profileTopology", [profile_id], block)
    if values[0] not in (0, 1, 2):
        raise ValueError("registry reports an unknown certified hook topology")
    return ProfileTopologyV1(LifecycleHookTopology(values[0]), values[1], to_checksum_address(values[2]), hex_bytes(values[3]))


def _profile_metadata(client: Web3, registry: str, profile_id: bytes, block: LaunchBlock) -> tuple[LaunchEnvelopeV2, LifecycleDeveloperTerms]:
    values = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profileEnvelope", [profile_id], block)
    envelope = LaunchEnvelopeV2(*values[:-2], LaunchBoundsV2(*values[-2]), LaunchGraphV2(*values[-1]))
    terms = LifecycleDeveloperTerms(*_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "developerTerms", [profile_id], block))
    return envelope, terms


def _profile_id(envelope: LaunchEnvelopeV2) -> bytes:
    return keccak(abi_encode(
        ["bytes32"] * 5 + ["uint256"] * 6,
        [keccak(text="black-market.launch-profile.v2"),
         *[_abi_value({"name": "digest", "type": "bytes32"}, value) for value in (envelope.artifact_digest, envelope.review_manifest_digest, envelope.config_bounds_digest, envelope.terms_digest)],
         int(envelope.topology), envelope.config_version, envelope.economic_version,
         int(envelope.beneficiary, 16), envelope.maximum_developer_fee_bps, envelope.capabilities],
    ))


def _verify_profile_graph(client: Web3, registry: str, orchestrator: str, implementation: str, envelope: LaunchEnvelopeV2, topology: ProfileTopologyV1, block: LaunchBlock) -> None:
    graph = envelope.graph
    for getter, expected in (("poolManager", graph.manager), ("hookRoot", graph.hook_root), ("oracleFactory", graph.oracle_factory), ("locker", graph.locker), ("collectorFactory", graph.collector_factory), ("hookDeployer", graph.hook_deployer), ("implementationRegistry", registry)):
        if _call(client, implementation, _PROFILE_DEPENDENCY_ABI, getter, [], block).lower() != expected.lower():
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Live adapter dependency differs from the frozen reviewed graph")
    for address, expected in ((orchestrator, graph.core_code_hash), (graph.manager, graph.manager_code_hash), (graph.oracle_factory, graph.oracle_factory_code_hash), (graph.locker, graph.locker_code_hash), (graph.collector_factory, graph.collector_factory_code_hash), (graph.collector_deployer, graph.collector_deployer_code_hash), (graph.hook_deployer, graph.hook_deployer_code_hash), (graph.code_chunk0, graph.code_chunk0_hash)):
        code = bytes.fromhex(hex_bytes(pinned_rpc(client, "eth_getCode", [address, block.tag], block))[2:])
        if not code or hex_bytes(keccak(code)) != hex_bytes(expected):
            raise LifecyclePlanningError("PROFILE_CODE_HASH", "Live runtime differs from its frozen reviewed code hash")
    if int(graph.code_chunk1, 16):
        code = bytes.fromhex(hex_bytes(pinned_rpc(client, "eth_getCode", [graph.code_chunk1, block.tag], block))[2:])
        if not code or hex_bytes(keccak(code)) != hex_bytes(graph.code_chunk1_hash):
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Reviewed second creation chunk differs from its exact code hash")
    elif hex_bytes(graph.code_chunk1_hash) != ZERO_HASH:
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Absent second chunk requires a zero code hash")
    collector = _call(client, graph.collector_factory, POOL_FEE_COLLECTOR_FACTORY_V1_ABI, "collectorDeployer", [], block)
    launcher = _call(client, graph.locker, V4_FEE_LIQUIDITY_LOCKER_V2_ABI, "launcher", [], block)
    manager = _call(client, graph.locker, V4_FEE_LIQUIDITY_LOCKER_V2_ABI, "poolManager", [], block)
    if collector.lower() != graph.collector_deployer.lower() or launcher.lower() != implementation.lower() or manager.lower() != graph.manager.lower() or graph.locker.lower() in (graph.manager.lower(), graph.hook_root.lower()):
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Collector or locker authority differs from the frozen graph")
    for name, expected in (("codeChunk0", graph.code_chunk0), ("codeChunk1", graph.code_chunk1)):
        if _call(client, graph.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, name, [], block).lower() != expected.lower():
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Reviewed deployer chunks differ from their frozen addresses")
    if graph.code_chunk0.lower() == graph.code_chunk1.lower() or topology.hook_deployer.lower() != graph.hook_deployer.lower() or topology.hook_creation_code_hash != hex_bytes(graph.hook_creation_code_hash):
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Certified topology differs from the exact reviewed deployer")
    if hex_bytes(_call(client, graph.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, "creationCodeHash", [], block)) != topology.hook_creation_code_hash:
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Reviewed creation bytecode differs from certification")
    creation_code = _pool_bound_creation_code(client, topology, block)
    if hash_launch_dependencies(chain_id=read_chain_id(client), core=orchestrator, registry=registry, registrar=implementation, graph=graph) != hex_bytes(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profile", [bytes.fromhex(hash_lifecycle_profile(envelope)[2:])], block)[2]):
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Exact immutable dependency digest differs from admission")
    if topology.hook_topology == LifecycleHookTopology.SHARED_V4:
        code = bytes.fromhex(hex_bytes(pinned_rpc(client, "eth_getCode", [graph.hook_root, block.tag], block))[2:])
        recorded = _call(client, graph.hook_deployer, SHARED_HOOK_DEPLOYER_V1_ABI, "deployedCodeHash", [graph.hook_root], block)
        args = abi_encode(["address", "address", "address"], [graph.manager, implementation, graph.oracle_factory])
        predicted = predict_pool_bound_hook_address(deployer=graph.hook_deployer, init_code_hash=keccak(creation_code + args), salt=graph.shared_hook_salt)
        remote = _call(client, graph.hook_deployer, SHARED_HOOK_DEPLOYER_V1_ABI, "predict", [graph.manager, implementation, graph.oracle_factory, bytes.fromhex(hex_bytes(graph.shared_hook_salt)[2:])], block)
        if not code or hex_bytes(recorded) == ZERO_HASH or hex_bytes(keccak(code)) != hex_bytes(graph.hook_runtime_code_hash) or hex_bytes(recorded) != hex_bytes(graph.hook_runtime_code_hash) or not valid_pool_bound_hook_address(graph.hook_root) or predicted.lower() != graph.hook_root.lower() or remote.lower() != predicted.lower():
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Shared root lacks exact typed-deployer runtime and constructor provenance")
        for name, expected in (("registrar", implementation), ("poolManager", graph.manager), ("oracleFactory", graph.oracle_factory)):
            if _call(client, graph.hook_root, LIFECYCLE_V4_HOOK_ABI, name, [], block).lower() != expected.lower():
                raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Shared immutable hook graph differs from admission")
        if _call(client, graph.hook_root, LIFECYCLE_V4_HOOK_ABI, "REQUIRED_HOOK_FLAGS", [], block) != V4_LIFECYCLE_HOOK_PERMISSIONS or _call(client, graph.hook_root, LIFECYCLE_V4_HOOK_ABI, "ALL_HOOK_MASK", [], block) != V4_LIFECYCLE_HOOK_PERMISSION_MASK:
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Shared hook callbacks differ from admission")
    elif int(graph.hook_root, 16) or hex_bytes(graph.hook_runtime_code_hash) != ZERO_HASH or hex_bytes(graph.shared_hook_salt) != ZERO_HASH:
        raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Bound topology cannot reuse a shared root")
    if envelope.beneficiary.lower() in {address.lower() for address in (ZERO_ADDRESS, orchestrator, registry, implementation, graph.manager, graph.hook_root, graph.locker, graph.collector_factory, graph.collector_deployer, graph.hook_deployer)}:
        raise LifecyclePlanningError("PROFILE_TERMS", "Stable author identity is an excluded reviewed graph destination")


def _certified_profile(client: Web3, registry: str, orchestrator: str, profile_id: bytes, block: LaunchBlock, required: int) -> tuple[tuple[Any, ...], tuple[Any, ...], ProfileTopologyV1]:
    profile = tuple(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profile", [profile_id], block))
    adapter = tuple(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "adapter", [profile[0]], block))
    schema = profile[1]
    if schema not in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6, ABYSS_LIFECYCLE_CONFIG_SCHEMA):
        raise LifecyclePlanningError("UNSUPPORTED_SCHEMA", "Registry admission does not imply supported SDK config schema")
    topology = _profile_topology(client, registry, profile_id, block)
    abyss = schema == ABYSS_LIFECYCLE_CONFIG_SCHEMA
    version = 1 if abyss else 4 if schema == V4_LIFECYCLE_CONFIG_SCHEMA else 5 if schema == V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5 else 6
    expected_topology = LifecycleHookTopology.NONE if abyss else LifecycleHookTopology.SHARED_V4 if version == 4 else LifecycleHookTopology.POOL_BOUND_V4
    if adapter[3] != version or topology.config_version != version or topology.hook_topology != expected_topology:
        raise LifecyclePlanningError("UNCERTIFIED_TOPOLOGY", "Registry topology/version differs from the supported config schema")
    if abyss:
        canonical = any(profile_id == keccak(abi_encode(["bytes32", "uint256", "address", "uint8"],
            [keccak(text="BLACK_MARKET_ABYSS_CANONICAL_PROFILE_V1"), read_chain_id(client), profile[4], variant])) for variant in range(4))
        if topology.hook_deployer.lower() != ZERO_ADDRESS or topology.hook_creation_code_hash != ZERO_HASH:
            raise LifecyclePlanningError("UNCERTIFIED_TOPOLOGY", "Abyss does not admit a V4 hook deployment graph")
        if not canonical or profile[3].lower() != profile[4].lower() or int(profile[5], 16):
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Abyss requires its exact canonical factory/variant binding")
    else:
        envelope, terms = _profile_metadata(client, registry, profile_id, block)
        ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], block)
        if terms.adapter.lower() != adapter[0].lower() or terms.beneficiary.lower() != envelope.beneficiary.lower() or hex_bytes(terms.terms_digest) != hex_bytes(envelope.terms_digest) or terms.maximum_developer_fee_bps != envelope.maximum_developer_fee_bps or terms.maximum_developer_fee_bps > ceiling:
            raise LifecyclePlanningError("PROFILE_TERMS", "Developer terms differ from the frozen reviewed envelope")
        allowed = LIFECYCLE_REQUIRED_CAPABILITIES | LIFECYCLE_ERC404_CAPABILITY | LIFECYCLE_MULTI_POSITION_CAPABILITY
        if _profile_id(envelope) != profile_id or envelope.config_version != version or envelope.topology != expected_topology or envelope.economic_version != 3 or envelope.flags != 0 or envelope.callback_flags != V4_LIFECYCLE_HOOK_PERMISSIONS or envelope.callback_mask != V4_LIFECYCLE_HOOK_PERMISSION_MASK or envelope.capabilities != profile[6] or envelope.capabilities != adapter[2] or envelope.capabilities & ~allowed or any(hex_bytes(value) == ZERO_HASH for value in (envelope.artifact_digest, envelope.review_manifest_digest, envelope.terms_digest)) or hash_launch_bounds(envelope.bounds) != hex_bytes(envelope.config_bounds_digest):
            raise LifecyclePlanningError("PROFILE_ENVELOPE", "Reviewed identity, bounds, capabilities or economic version differs from admission")
        bounds = envelope.bounds
        if bounds.minimum_tick_spacing < 1 or bounds.maximum_tick_spacing > 32767 or bounds.minimum_tick_spacing > bounds.maximum_tick_spacing or not 1 <= bounds.maximum_positions <= 32 or not 2 <= bounds.maximum_oracle_cardinality <= 4096 or bounds.fee_mode_flags == 0 or bounds.fee_mode_flags & ~3 or int(envelope.protocol_treasury, 16) == 0 or envelope.protocol_fee_denominator not in (0, 4, 5, 6, 7, 8, 9, 10):
            raise LifecyclePlanningError("PROFILE_BOUNDS", "Registry envelope has unsupported executable bounds")
        if int(profile[4], 16) or envelope.graph.manager.lower() != profile[3].lower() or envelope.graph.hook_root.lower() != profile[5].lower():
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Reviewed venue/topology differs from the frozen envelope")
        if _call(client, adapter[0], _PROFILE_DEPENDENCY_ABI, "PROFILE_ID", [], block) != profile_id or _call(client, adapter[0], _PROFILE_DEPENDENCY_ABI, "CONFIG_SCHEMA", [], block) != schema or _call(client, adapter[0], _PROFILE_DEPENDENCY_ABI, "CONFIG_VERSION", [], block) != version:
            raise LifecyclePlanningError("PROFILE_DEPENDENCY", "Live adapter metadata differs from registry certification")
        _verify_profile_graph(client, registry, orchestrator, adapter[0], envelope, topology, block)
        if not terms.enabled:
            raise LifecyclePlanningError("INELIGIBLE_PROFILE", "Registry refuses this profile for pending source admission")
    implementation = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "requireEligible", [profile[0], profile_id, version, required], block)
    if implementation.lower() != adapter[0].lower():
        raise LifecyclePlanningError("INELIGIBLE_PROFILE", "Registry refuses this profile for pending source admission")
    return adapter, profile, topology


@read_invocation("profile.certification")
def read_lifecycle_profiles(client: Web3, *, orchestrator: str, profile_ids: Sequence[bytes | str] | None = None, offset: int = 0, limit: int = 100, block: LaunchBlock | None = None) -> tuple[LifecycleProfile, ...]:
    """Discover and certify live profiles, never substituting construction presets."""
    orchestrator = _address(orchestrator, "orchestrator", nonzero=True)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
        raise LifecyclePlanningError("INVALID_PROFILE_PAGE", "Registry profile pages require unsigned offset and limit at most 100")
    chain_id = read_chain_id(client)
    pinned = block or read_block(client)
    registry = _address(_call(client, orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], pinned), "registry", nonzero=True)
    if _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], pinned).lower() != orchestrator.lower():
        raise LifecyclePlanningError("REGISTRY_BINDING", "Registry is not bound to the selected lifecycle core")
    ids = profile_ids if profile_ids is not None else _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profileIds", [offset, limit], pinned)
    protocol_ceiling = None
    result = []
    for item in ids:
        profile_id = _abi_value({"name": "profileId", "type": "bytes32"}, item)
        profile = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profile", [profile_id], pinned)
        adapter = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "adapter", [profile[0]], pinned)
        venue_kind = "uniswap-v4" if profile[1] in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6) else "abyss" if profile[1] == ABYSS_LIFECYCLE_CONFIG_SCHEMA else "unknown"
        topology = ProfileTopologyV1(LifecycleHookTopology.NONE, adapter[3], ZERO_ADDRESS, ZERO_HASH)
        reason = None
        envelope = terms = None
        if venue_kind == "uniswap-v4" and protocol_ceiling is None:
            protocol_ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], pinned)
        try:
            if venue_kind != "unknown":
                topology = _profile_topology(client, registry, profile_id, pinned)
                if venue_kind == "uniswap-v4":
                    envelope, terms = _profile_metadata(client, registry, profile_id, pinned)
            adapter, profile, topology = _certified_profile(client, registry, orchestrator, profile_id, pinned, LIFECYCLE_REQUIRED_CAPABILITIES)
        except (ValueError, LaunchRpcError) as error:
            reason = failure_reason(error)
        result.append(LifecycleProfile(hex_bytes(profile_id), _profile_registration(profile), _adapter_registration(adapter),
            topology, venue_kind, envelope, terms, protocol_ceiling if venue_kind == "uniswap-v4" else None,
            admitted=reason is None, reason=reason))
    check_lifecycle_source(client, chain_id, pinned)
    return tuple(result)


def _pool_bound_creation_code(client: Web3, topology: ProfileTopologyV1, block: LaunchBlock) -> bytes:
    chunks = []
    for name in ("codeChunk0", "codeChunk1"):
        address = _call(client, topology.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, name, [], block)
        if int(address, 16) == 0:
            if name == "codeChunk0":
                raise ValueError("pool-bound deployer has no immutable creation-code chunk")
            continue
        code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [address, block.tag]))[2:])
        if not 1 < len(code) <= 24_576 or code[0] != 0:
            raise ValueError("pool-bound creation-code chunk is not its bounded STOP-prefixed runtime")
        chunks.append(code[1:])
    if len(chunks) == 2 and len(chunks[0]) != 24_575:
        raise ValueError("reviewed two-chunk creation code requires a full first chunk")
    creation_code = b"".join(chunks)
    constructor_size = (20 if topology.config_version == 6 else 18 if topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4 else 3) * 32
    if len(creation_code) + constructor_size > 49_152 or hex_bytes(keccak(creation_code)) != topology.hook_creation_code_hash:
        raise ValueError("actual creation-code chunks differ from certified bytecode or exceed the initcode bound")
    return creation_code


def validate_v4_lifecycle_market(*, market: LifecycleMarketConfig, config: LifecycleV4MarketConfig | LifecyclePoolBoundV4MarketConfig, profile: LifecycleProfile | LifecycleProfileMetadata, token: str) -> None:
    """Check explicit reviewed construction economics without claiming eligibility."""
    if config.version != 4 and not is_pool_bound_v4_config_version(config.version):
        raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", "Reviewed V4 config version must be 4, 5 or 6")
    if config.version == 6:
        validate_pool_bound_hook_fees(config)
    elif config.version == 5:
        _v1_fee_shape(config)
    if is_hmm_profile(config.profile_id):
        hmm = getattr(config, "hmm_launch_config", None)
        if config.version != 6 or hmm is None or config.lp_fee_pips != 0 or config.developer_fee_bps != 0:
            raise LifecyclePlanningError("INVALID_HMM_CONFIG", "HMM V2 requires config6, an explicit launch config and zero LP/developer fees")
        validate_hmm_launch_config(hmm)
    if is_ufg_profile(config.profile_id):
        ufg = getattr(config, "ufg_launch_config", None)
        if config.version != 6 or ufg is None or config.lp_fee_pips != 0 or config.fee_mode != 1:
            raise LifecyclePlanningError("INVALID_UFG_PLAN", "UFG requires config6, an explicit treasury config, QuoteOnly and zero LP fees")
        validate_ufg_launch_config(ufg)
    envelope, terms, ceiling = profile.envelope, profile.developer_terms, profile.protocol_maximum_developer_fee_bps
    expected_schema = V4_LIFECYCLE_CONFIG_SCHEMA if config.version == 4 else pool_bound_v4_lifecycle_config_schema(config.version)
    if envelope is None or terms is None or ceiling is None or not terms.enabled or profile.topology.hook_topology != (1 if config.version == 4 else 2) or market.config_version != config.version or profile.topology.config_version != config.version or profile.adapter.config_version != config.version or envelope.topology != profile.topology.hook_topology or envelope.config_version != config.version or envelope.economic_version != 3 or profile.registration.config_schema.lower() != hex_bytes(expected_schema) or hex_bytes(market.adapter_id) != profile.registration.adapter_id.lower() or hex_bytes(market.profile_id) != profile.id.lower() or hex_bytes(config.profile_id) != profile.id.lower() or terms.adapter.lower() != profile.adapter.implementation.lower() or hex_bytes(config.terms_digest) != hex_bytes(envelope.terms_digest) or hex_bytes(config.terms_digest) != hex_bytes(terms.terms_digest) or config.developer_beneficiary.lower() != envelope.beneficiary.lower() or config.developer_beneficiary.lower() != terms.beneficiary.lower() or int(config.developer_beneficiary, 16) == 0 or config.developer_beneficiary.lower() in (token.lower(), market.quote_asset.lower()):
        raise LifecyclePlanningError("PROFILE_TERMS_MISMATCH", "Market must bind the reviewed profile, frozen terms and stable author identity")
    if isinstance(config.developer_fee_bps, bool) or not isinstance(config.developer_fee_bps, int) or config.developer_fee_bps < 0 or config.developer_fee_bps > min(envelope.maximum_developer_fee_bps, terms.maximum_developer_fee_bps, ceiling):
        raise LifecyclePlanningError("DEVELOPER_FEE_CEILING", "Explicit developer fee exceeds the reviewed or protocol ceiling")
    if hex_bytes(config.oracle_config_id) == ZERO_HASH:
        raise LifecyclePlanningError("INVALID_ORACLE_CONFIG", "Market oracle configuration must be nonzero")
    bounds = envelope.bounds
    if config.treasury.lower() != envelope.protocol_treasury.lower() or config.protocol_fee_denominator != envelope.protocol_fee_denominator or isinstance(config.hook_fee_pips, bool) or not isinstance(config.hook_fee_pips, int) or not 0 <= config.hook_fee_pips <= (1_000_000 if config.version == 6 else 999_999) or isinstance(config.lp_fee_pips, bool) or not isinstance(config.lp_fee_pips, int) or not 0 <= config.lp_fee_pips < 1_000_000 or isinstance(config.fee_mode, bool) or not isinstance(config.fee_mode, int) or config.fee_mode not in (0, 1) or not bounds.fee_mode_flags & (1 << config.fee_mode) or not bounds.minimum_tick_spacing <= config.tick_spacing <= bounds.maximum_tick_spacing or not 1 <= len(config.positions) <= bounds.maximum_positions:
        raise LifecyclePlanningError("PROFILE_BOUNDS_MISMATCH", "Market economics exceed or differ from the exact reviewed envelope")


@read_invocation("profile.certification")
def validate_v4_lifecycle_oracle(client: Web3, *, envelope: LaunchEnvelopeV2, oracle_config_id: bytes | str, block: LaunchBlock) -> None:
    oracle_id = _abi_value({"name": "oracleConfigId", "type": "bytes32"}, oracle_config_id)
    if not any(oracle_id):
        raise LifecyclePlanningError("INVALID_ORACLE_CONFIG", "Market oracle configuration must be nonzero")
    move, cardinality = _call(client, envelope.graph.oracle_factory, LIFECYCLE_ORACLE_FACTORY_ABI, "oracleConfigs", [oracle_id], block)
    if not 0 < move <= 887272 or not 2 <= cardinality <= envelope.bounds.maximum_oracle_cardinality:
        raise LifecyclePlanningError("INVALID_ORACLE_CONFIG", "Selected oracle is unregistered or exceeds reviewed cardinality bounds")


def _admit_market_config(client: Web3, registry: str, market: LifecycleMarketConfig, token: str, adapter: Sequence[Any], profile: Sequence[Any], topology: ProfileTopologyV1, block: LaunchBlock, required: int) -> tuple[LaunchEnvelopeV2 | None, LifecycleDeveloperTerms | None]:
    if profile[1] == ABYSS_LIFECYCLE_CONFIG_SCHEMA:
        positions = abi_decode([_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1)], bytes.fromhex(hex_bytes(market.config)[2:]))[0][-1]
        envelope = terms = None
    else:
        config = decode_lifecycle_pool_bound_v4_market_config(market.config) if topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4 else decode_lifecycle_v4_market_config(market.config)
        positions = config.positions
        envelope, terms = _profile_metadata(client, registry, bytes.fromhex(hex_bytes(market.profile_id)[2:]), block)
        ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], block)
        metadata = LifecycleProfileMetadata(hex_bytes(market.profile_id), _profile_registration(profile),
            _adapter_registration(adapter), topology, "uniswap-v4", envelope, terms, ceiling)
        validate_v4_lifecycle_market(market=market, config=config, token=token, profile=metadata)
        validate_v4_lifecycle_oracle(client, envelope=envelope, oracle_config_id=config.oracle_config_id, block=block)
    if len(positions) > 1:
        required |= LIFECYCLE_MULTI_POSITION_CAPABILITY
        if adapter[2] & required != required or profile[6] & required != required:
            raise ValueError("positions require the registry's admitted multi-position capability")
        _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "requireEligible", [profile[0], bytes.fromhex(hex_bytes(market.profile_id)[2:]), market.config_version, required], block)
    return envelope, terms


def _pool_bound_deployment(client: Web3, plan: LaunchPlanV1, index: int, token: str, adapter: Sequence[Any], profile: Sequence[Any], topology: ProfileTopologyV1, block: LaunchBlock) -> tuple[PoolBoundHookDeployment, PoolBoundHookParameters]:
    if topology.hook_topology != LifecycleHookTopology.POOL_BOUND_V4:
        raise ValueError("market is not a certified pool-bound V4 offering")
    market = plan.markets[index]
    config = decode_lifecycle_pool_bound_v4_market_config(market.config)
    implementation = adapter[0]
    if market.config_version != topology.config_version or bytes.fromhex(hex_bytes(market.adapter_id)[2:]) != profile[0]:
        raise ValueError("market adapter/config version differs from its certified pool-bound profile")
    registry = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block)
    required = LIFECYCLE_REQUIRED_CAPABILITIES | (LIFECYCLE_ERC404_CAPABILITY if int(plan.token.kind) == 1 else 0)
    _admit_market_config(client, registry, market, token, adapter, profile, topology, block, required)
    metadata = _call(client, implementation, POOL_MARKET_ADAPTER_V1_ABI, "hookDeploymentMetadata", [token, _market_tuple(plan, index)], block)
    deployer, init_code_hash, salt, predicted_hook = metadata
    if deployer.lower() != topology.hook_deployer.lower() or salt != bytes.fromhex(hex_bytes(config.hook_salt)[2:]) or hex_bytes(init_code_hash) == ZERO_HASH:
        raise ValueError("pool-bound deployment metadata differs from its certified deployer and exact committed salt")
    prediction = predict_pool_bound_hook_address(deployer=deployer, init_code_hash=init_code_hash, salt=salt)
    if predicted_hook.lower() != prediction.lower():
        raise ValueError("pool-bound metadata prediction is not its exact CREATE2 address")
    oracle_factory = _call(client, implementation, POOL_MARKET_ADAPTER_V1_ABI, "oracleFactory", [], block)
    locker = _call(client, implementation, POOL_MARKET_ADAPTER_V1_ABI, "locker", [], block)
    collector_factory = _call(client, implementation, POOL_MARKET_ADAPTER_V1_ABI, "collectorFactory", [], block)
    parameters = build_pool_bound_hook_parameters(plan=plan, market_index=index, token=token,
        pool_manager=profile[3], registrar=implementation, oracle_factory=oracle_factory, liquidity_locker=locker)
    encoded_parameters = encode_pool_bound_hook_parameters(parameters, config_version=market.config_version)
    parameter_components = _bound_parameter_components(market.config_version)
    deployer_abi = _bound_deployer_abi(market.config_version)
    factory_abi = HMM_FEE_COLLECTOR_FACTORY_V2_ABI if is_hmm_profile(market.profile_id) else POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI if market.config_version == 6 else POOL_FEE_COLLECTOR_FACTORY_V1_ABI
    actual_parameters, actual_salt = _call(client, collector_factory, factory_abi, "poolBoundHookParameters", [implementation, token, _market_tuple(plan, index)], block)
    if actual_salt != salt or abi_encode([_tuple_type(parameter_components)], [actual_parameters]) != encoded_parameters:
        raise ValueError("pool-bound constructor tuple differs from the exact salt-normalized market economics")
    creation_code = _pool_bound_creation_code(client, topology, block)
    if keccak(creation_code + encoded_parameters) != init_code_hash:
        raise ValueError("pool-bound initcode differs from certified bytecode plus its exact versioned constructor")
    parameter_tuple = _struct_tuple(parameter_components, parameters)
    if _call(client, deployer, deployer_abi, "initCodeHash", [parameter_tuple], block) != init_code_hash or _call(client, deployer, deployer_abi, "predict", [parameter_tuple, salt], block).lower() != prediction.lower():
        raise ValueError("typed deployer initcode/prediction differs from exact local derivation")
    hook_code = bytes.fromhex(hex_bytes(pinned_rpc(client, "eth_getCode", [prediction, block.tag], block))[2:])
    hook_abi = (HMM_FIXED_HOOK_V2_ABI if hex_bytes(market.profile_id) == HMM_FIXED_PROFILE_ID
                else HMM_DYNAMIC_HOOK_V2_ABI) if is_hmm_profile(market.profile_id) else FIXED_FEE_POOL_HOOK_V1_ABI
    if hook_code:
        recorded_hash = _call(client, deployer, POOL_HOOK_DEPLOYER_V1_ABI, "deployedCodeHash", [prediction], block)
        if len(hook_code) > 24_576 or recorded_hash == bytes(32) or recorded_hash != keccak(hook_code):
            raise ValueError("existing pool-bound hook lacks exact typed-deployer runtime provenance")
        for name, expected in (("deploymentConfigHash", keccak(encoded_parameters)), ("marketCommitment", bytes.fromhex(hex_bytes(parameters.market_commitment)[2:]))):
            if _call(client, prediction, hook_abi, name, [], block) != expected:
                raise ValueError("existing pool-bound hook differs from its exact immutable economic commitment")
        for name, expected in (("poolManager", parameters.pool_manager), ("registrar", parameters.registrar), ("oracleFactory", parameters.oracle_factory), ("core", parameters.core), ("liquidityLocker", parameters.liquidity_locker), ("token", parameters.token)):
            if _call(client, prediction, hook_abi, name, [], block).lower() != expected.lower():
                raise ValueError("existing pool-bound hook differs from its exact immutable dependency graph")
        currency0, currency1 = sorted((token, market.quote_asset), key=lambda address: int(address, 16))
        pool_id = keccak(abi_encode(["address", "address", "uint24", "int24", "address"], [currency0, currency1, config.lp_fee_pips, config.tick_spacing, prediction]))
        if _call(client, prediction, hook_abi, "boundPoolId", [], block) != pool_id or _call(client, prediction, hook_abi, "openingSqrtPriceX96", [], block) != config.sqrt_price_x96 or _call(client, prediction, hook_abi, "expectedPositionCount", [], block) != len(config.positions):
            raise ValueError("existing pool-bound hook differs from its exact key, opening price or position count")
        if _call(client, prediction, hook_abi, "REQUIRED_HOOK_FLAGS", [], block) != V4_LIFECYCLE_HOOK_PERMISSIONS or _call(client, prediction, hook_abi, "ALL_HOOK_MASK", [], block) != V4_LIFECYCLE_HOOK_PERMISSION_MASK:
            raise ValueError("existing pool-bound hook differs from its exact reviewed callback declarations")
        if config.version == 6:
            for name, expected in (("minimumHookFeePips", config.minimum_hook_fee_pips), ("feeSensitivityPipsSecondsPerTick", config.fee_sensitivity_pips_seconds_per_tick)):
                if _call(client, prediction, hook_abi if is_hmm_profile(market.profile_id) else FIXED_FEE_POOL_HOOK_CONFIG_V6_ABI, name, [], block) != expected:
                    raise LifecyclePlanningError("HOOK_DEPLOYMENT_CHANGED", "Existing config6 hook fee policy differs from its exact constructor")
    return PoolBoundHookDeployment(to_checksum_address(deployer), hex_bytes(init_code_hash), hex_bytes(salt), prediction), parameters


def _known_bound_deployment(plan: LaunchPlanV1, index: int, token: str) -> tuple[PoolBoundHookDeployment, PoolBoundHookParameters] | None:
    from .lifecycle_presets import get_known_lifecycle_profile
    market = plan.markets[index]
    preset = get_known_lifecycle_profile(chain_id=plan.chain_id, orchestrator=plan.orchestrator, profile_id=market.profile_id)
    if preset is None:
        return None
    if not preset.supported:
        raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", preset.unavailable_reason or "Historical preset does not support the current bound ABI")
    profile = preset.profile
    if preset.bound_hook is None or profile.topology.hook_topology != 2 or market.config_version != profile.topology.config_version or profile.adapter.config_version != market.config_version or profile.envelope is None or profile.envelope.config_version != market.config_version or profile.registration.config_schema.lower() != hex_bytes(pool_bound_v4_lifecycle_config_schema(market.config_version)) or hex_bytes(market.adapter_id) != profile.registration.adapter_id.lower():
        raise LifecyclePlanningError("INVALID_BOUND_MARKET", "Market does not select the preset's exact version, schema, adapter and bound topology")
    hook = preset.bound_hook
    parameters = build_pool_bound_hook_parameters(plan=plan, market_index=index, token=token,
        pool_manager=hook.pool_manager, registrar=hook.registrar, oracle_factory=hook.oracle_factory,
        liquidity_locker=hook.liquidity_locker)
    init_hash = pool_bound_hook_init_code_hash(hook.creation_code, parameters, config_version=market.config_version)
    config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
    salt = hex_bytes(config.hook_salt)
    prediction = predict_pool_bound_hook_address(deployer=hook.deployer, init_code_hash=init_hash, salt=salt)
    return PoolBoundHookDeployment(hook.deployer, init_hash, salt, prediction), parameters


def _read_pool_bound_deployment(client: Web3, plan: LaunchPlanV1, market_index: int, block: LaunchBlock) -> tuple[PoolBoundHookDeployment, PoolBoundHookParameters]:
    if isinstance(market_index, bool) or not isinstance(market_index, int) or not 0 <= market_index < len(plan.markets) or not is_pool_bound_v4_config_version(plan.markets[market_index].config_version):
        raise LifecyclePlanningError("INVALID_BOUND_MARKET", "Market index must select explicit pool-bound config version 5 or 6")
    market = plan.markets[market_index]
    token = read_lifecycle_token_at_block(client, plan, block)
    known = _known_bound_deployment(plan, market_index, token)
    if known is not None:
        return known
    registry = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block)
    if _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], block).lower() != plan.orchestrator.lower():
        raise ValueError("registry is not bound to the committed lifecycle orchestrator")
    required = LIFECYCLE_REQUIRED_CAPABILITIES | (LIFECYCLE_ERC404_CAPABILITY if int(plan.token.kind) == 1 else 0)
    adapter, profile, topology = _certified_profile(client, registry, plan.orchestrator, bytes.fromhex(hex_bytes(market.profile_id)[2:]), block, required)
    return _pool_bound_deployment(client, plan, market_index, token, adapter, profile, topology, block)


@read_invocation("plan.domain")
def read_pool_bound_hook_deployment(client: Web3, plan: LaunchPlanV1, *, market_index: int, block: LaunchBlock | None = None) -> PoolBoundHookDeployment:
    """Read exact metadata even with unmined salts or an already prepared pool."""
    _check_client_identity(client, plan)
    commitment = hash_launch_plan(plan)
    pinned = block or read_block(client)
    deployment, _ = _read_pool_bound_deployment(client, plan, market_index, pinned)
    check_lifecycle_source(client, plan.chain_id, pinned)
    if hash_launch_plan(plan) != commitment:
        raise LifecyclePlanningError("PLAN_MUTATED", "Plan changed during exact bound-hook derivation")
    return deployment


@read_invocation("plan.domain")
def build_pool_bound_hook_deployment_transaction(client: Web3, plan: LaunchPlanV1, *, market_index: int) -> PoolBoundHookDeploymentTransaction:
    """Build optional permissionless predeployment without accepting arbitrary code."""
    _check_client_identity(client, plan)
    commitment = hash_launch_plan(plan)
    block = read_block(client)
    deployment, parameters = _read_pool_bound_deployment(client, plan, market_index, block)
    if not valid_pool_bound_hook_address(deployment.predicted_hook):
        raise ValueError("mine and finalize the pool-bound hook salt before predeployment")
    version = plan.markets[market_index].config_version
    data = _encode_function(_bound_deployer_abi(version), "deploy", [_struct_tuple(_bound_parameter_components(version), parameters), bytes.fromhex(deployment.salt[2:])])
    check_lifecycle_source(client, plan.chain_id, block)
    if hash_launch_plan(plan) != commitment:
        raise LifecyclePlanningError("PLAN_MUTATED", "Plan changed during optional typed predeployment")
    return PoolBoundHookDeploymentTransaction(deployment.deployer, data, 0, deployment)


def _check_mining_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("pool-bound hook salt mining cancelled")


async def mine_pool_bound_hook_salt(*, deployer: str, init_code_hash: bytes | str, start_salt: int = 0, cancel_event: asyncio.Event | None = None, on_progress: Callable[[PoolBoundHookMiningProgress], None] | None = None) -> PoolBoundHookSalt:
    """Mine exact 0x1afc permission bits locally, yielding for cancellation/UI work."""
    deployer = _address(deployer, "deployer")
    init_hash = _abi_value({"name": "initCodeHash", "type": "bytes32"}, init_code_hash)
    try:
        counter = _uint(start_salt, 256, "startSalt")
    except ValueError:
        raise LifecyclePlanningError("INVALID_HOOK_SALT", "Starting hook salt must fit uint256") from None
    prefix = b"\xff" + bytes.fromhex(deployer[2:])
    attempts = 0
    _check_mining_cancelled(cancel_event)
    if on_progress is not None:
        initial_salt = counter.to_bytes(32, "big")
        initial_digest = keccak(prefix + initial_salt + init_hash)
        on_progress(PoolBoundHookMiningProgress(0, hex_bytes(initial_salt), to_checksum_address(initial_digest[-20:])))
    await asyncio.sleep(0)
    while counter < 1 << 256:
        _check_mining_cancelled(cancel_event)
        salt = counter.to_bytes(32, "big")
        digest = keccak(prefix + salt + init_hash)
        attempts += 1
        valid = int.from_bytes(digest[-2:], "big") & V4_LIFECYCLE_HOOK_PERMISSION_MASK == V4_LIFECYCLE_HOOK_PERMISSIONS
        if valid or attempts % 256 == 0:
            progress = PoolBoundHookMiningProgress(attempts, hex_bytes(salt), to_checksum_address(digest[-20:]))
            if on_progress is not None:
                on_progress(progress)
            _check_mining_cancelled(cancel_event)
            if valid:
                return PoolBoundHookSalt(progress.salt, progress.predicted_hook)
            await asyncio.sleep(0)
        counter += 1
    raise LifecyclePlanningError("HOOK_SALT_EXHAUSTED", "No valid hook address remains in the uint256 salt range")


def _protect_ordered_buy_minimums(planned: PlannedLaunch, bps: int) -> LaunchPlanV1:
    simulation = planned.simulation
    if simulation.deferred_transaction_ids:
        raise LifecyclePlanningError("LAUNCH_DIAGNOSTIC_FAILED", "Buy calibration requires activation outputs beyond the proven simulation prefix; use a higher aggregate-cap native RPC or omit buy_slippage_bps with committed minima", simulation)
    if simulation.confidence != "stateful" or simulation.backend is None or not simulation.evidence.complete or any(not call.success for call in simulation.evidence.calls) or len(simulation.evidence.calls) != len(planned.transactions):
        raise LifecyclePlanningError("LAUNCH_DIAGNOSTIC_FAILED", "; ".join(simulation.reasons) or "Complete diagnostic execution failed", simulation)
    index = next((index for index, transaction in enumerate(planned.transactions) if transaction.kind in {"atomic", "activate"}), None)
    if index is None:
        raise LifecyclePlanningError("MISSING_BUY_OUTPUTS", "Diagnostic omitted the actual activation receipt", simulation)
    call = simulation.evidence.calls[index]
    if simulation.backend == "controlled-fork":
        buys = [event for event in decode_lifecycle_events(planned.plan, call.logs) if event.name == "InitialBuyExecuted"]
        spent, outputs = tuple(event.args["quoteSpent"] for event in buys), tuple(event.args["tokenOut"] for event in buys)
    else:
        receipt = abi_decode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], bytes.fromhex(call.return_data[2:]))[0]
        if hex_bytes(receipt[0]) != planned.launch_id or hex_bytes(receipt[1]) != planned.plan_hash or receipt[2].lower() != planned.predicted_token.lower() or receipt[5] != len(planned.plan.markets):
            raise LifecyclePlanningError("MISSING_BUY_OUTPUTS", "Diagnostic receipt differs from the complete committed launch", simulation)
        spent, outputs = receipt[7], receipt[8]
    if len(spent) != len(planned.plan.buys) or len(outputs) != len(planned.plan.buys):
        raise LifecyclePlanningError("MISSING_BUY_OUTPUTS", "Diagnostic omitted an ordered buy output", simulation)
    protected = []
    for buy, quote, output in zip(planned.plan.buys, spent, outputs):
        minimum = output * (10000 - bps) // 10000
        if quote > buy.quote_amount_in or output < buy.min_token_out or minimum <= 0:
            raise LifecyclePlanningError("INVALID_BUY_OUTPUT", "Every actual buy needs a satisfied budget and positive protected minimum", simulation)
        protected.append(replace(buy, min_token_out=minimum))
    return replace(planned.plan, buys=tuple(protected))


async def _mine_bound_deployments(
    client: Web3, plan: LaunchPlanV1, block: LaunchBlock,
    deployments: Sequence[PoolBoundLifecycleDeployment], *,
    cancel_event: asyncio.Event | None,
    on_progress: Callable[[PoolBoundHookMiningProgress], None] | None,
) -> tuple[PoolBoundLifecycleDeployment, ...]:
    from .lifecycle_presets import get_known_lifecycle_deployment
    preset = get_known_lifecycle_deployment(chain_id=plan.chain_id, orchestrator=plan.orchestrator)
    snapshot = getattr(client, "lens_snapshots", {}).get((block.number, block.block_hash, launch_id_of(plan)))
    pending = {item.market_index: item for item in deployments}
    starts = {index: int(item.salt, 16) for index, item in pending.items()}
    mined = {}
    while pending:
        _check_mining_cancelled(cancel_event)
        for index, deployment in tuple(pending.items()):
            if valid_pool_bound_hook_address(deployment.predicted_hook):
                mined[index] = pending.pop(index)
        if not pending:
            break
        if snapshot is not None:
            rows = {row[0]: row for row in snapshot[10]}
            candidates = []
            for index, deployment in pending.items():
                row = rows[index]
                if row[2].lower() != deployment.deployer.lower() or hex_bytes(row[4]) != deployment.init_code_hash:
                    raise LifecyclePlanningError("INVALID_HOOK_SALT", "Launch lens changed the locally derived hook constructor")
                candidates.append((index, hex_bytes(row[5]), row[6], row[7]))
            snapshot = None
        elif preset is not None and preset.hook_salt_miner is not None:
            attempts = min(150_000 // len(pending), min((1 << 256) - 1 - int(item.salt, 16) for item in pending.values()))
            if attempts == 0:
                raise LifecyclePlanningError("HOOK_SALT_EXHAUSTED", "No searchable hook salt remains")
            searches = [(item.deployer, bytes.fromhex(item.init_code_hash[2:]), int(item.salt, 16),
                V4_LIFECYCLE_HOOK_PERMISSIONS) for item in pending.values()]
            salts, hooks, found = await asyncio.to_thread(_call, client, preset.hook_salt_miner,
                HOOK_SALT_MINER_ABI, "findMany", [searches, attempts], block)
            if not len(salts) == len(hooks) == len(found) == len(pending):
                raise LifecyclePlanningError("INVALID_HOOK_SALT", "Hook miner omitted a requested market")
            candidates = [(index, hex_bytes(salt), hook, success)
                for index, salt, hook, success in zip(pending, salts, hooks, found)]
            for index, salt, _, success in candidates:
                start = int(pending[index].salt, 16)
                if not (start <= int(salt, 16) < start + attempts if success else int(salt, 16) == start + attempts):
                    raise LifecyclePlanningError("INVALID_HOOK_SALT", "Hook miner returned a salt outside its search window")
        elif preset is not None and preset.launch_lens is not None:
            markets = list(plan.markets)
            for index, deployment in {**mined, **pending}.items():
                config = decode_lifecycle_pool_bound_v4_market_config(markets[index].config)
                markets[index] = replace(markets[index], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=deployment.salt)))
            account = _execution_account(plan)
            spender = UFG_LAUNCH_PROXY_ADDRESS if plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() else preset.funding_escrow
            snapshot = await asyncio.to_thread(_call, client, preset.launch_lens, LAUNCH_LENS_ABI, "snapshot",
                [to_launch_plan_tuple(replace(plan, markets=tuple(markets))), account, spender, (), 150_000], block)
            continue
        else:
            candidates = []
            for index, deployment in pending.items():
                def report(item: PoolBoundHookMiningProgress, index: int = index) -> None:
                    if on_progress is not None:
                        on_progress(replace(item, market_index=index))
                result = await mine_pool_bound_hook_salt(deployer=deployment.deployer,
                    init_code_hash=deployment.init_code_hash, start_salt=int(deployment.salt, 16),
                    cancel_event=cancel_event, on_progress=report if on_progress is not None else None)
                candidates.append((index, result.salt, result.predicted_hook, True))
        for index, salt, hook, found in candidates:
            _check_mining_cancelled(cancel_event)
            deployment = pending[index]
            if found:
                local = predict_pool_bound_hook_address(deployer=deployment.deployer,
                    init_code_hash=deployment.init_code_hash, salt=salt)
                if hook.lower() != local.lower() or not valid_pool_bound_hook_address(local):
                    raise LifecyclePlanningError("INVALID_HOOK_SALT", "Hook miner result failed local CREATE2 permission verification")
                mined[index] = replace(deployment, salt=salt, predicted_hook=local)
                del pending[index]
            else:
                if int(salt, 16) <= int(deployment.salt, 16) or int(hook, 16) != 0:
                    raise LifecyclePlanningError("INVALID_HOOK_SALT", "Hook miner did not advance its bounded search")
                pending[index] = replace(deployment, salt=salt, predicted_hook=predict_pool_bound_hook_address(
                    deployer=deployment.deployer, init_code_hash=deployment.init_code_hash, salt=salt))
            if on_progress is not None:
                on_progress(PoolBoundHookMiningProgress(int(salt, 16) - starts[index] + int(found), salt, hook, index))
        await asyncio.sleep(0)
    return tuple(mined[index] for index in sorted(mined))


@async_read_invocation("plan")
async def prepare_and_plan_lifecycle_launch(
    client: Web3, plan: LaunchPlanV1, *, account: str, mode: LaunchExecutionMode,
    buy_slippage_bps: int | None = None, cancel_event: asyncio.Event | None = None,
    on_progress: Callable[[PoolBoundHookMiningProgress], None] | None = None,
    limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    confirmations: int = 1,
    receipts: Sequence[LifecycleReceiptReference] = (), fork: ControlledLaunchFork | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    allow_topology_gaps: bool = False, allow_missing_permanent_tail: bool = False,
    ufg: UfgLaunchConfig | None = None,
) -> PlannedLaunch:
    """Mine exact salts, protect ordered buys, then admit the final commitment."""
    commitment = _validate_planning_intent(plan, account, mode, cancel_event, ufg)
    ufg_hash = None if ufg is None else hash_ufg_launch_config(ufg)
    if buy_slippage_bps is not None and (isinstance(buy_slippage_bps, bool) or not isinstance(buy_slippage_bps, int) or not 0 <= buy_slippage_bps < 10000):
        raise LifecyclePlanningError("INVALID_BUY_SLIPPAGE", "Buy slippage must be an integer between zero and 9999 basis points")
    mining = any(is_pool_bound_v4_config_version(market.config_version) for market in plan.markets)
    protection = buy_slippage_bps is not None and bool(plan.buys)
    options: dict[str, Any] = dict(account=account, mode=mode, limits=limits,
                   confirmations=confirmations, receipts=receipts, fork=fork,
                   cancel_event=cancel_event, data_fee_estimator=data_fee_estimator,
                   allow_topology_gaps=allow_topology_gaps,
                   allow_missing_permanent_tail=allow_missing_permanent_tail, ufg=ufg)
    if not mining and not protection:
        return await asyncio.to_thread(_plan_launch, client, plan, **options)
    draft = launch_plan_from_dict(launch_plan_to_dict(plan))
    def assert_draft() -> None:
        _check_mining_cancelled(cancel_event)
        if hash_launch_plan(plan) != commitment:
            raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan changed during asynchronous preparation")
        if (None if ufg is None else hash_ufg_launch_config(ufg)) != ufg_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "UFG treasury config changed during asynchronous preparation")
    await asyncio.to_thread(_check_client_identity, client, draft, account)
    initial = await asyncio.to_thread(_prime_lifecycle_reads, client, draft, mining=mining)
    markets = list(draft.markets)
    expected: list[PoolBoundLifecycleDeployment] = []
    for index, market in enumerate(draft.markets):
        if not is_pool_bound_v4_config_version(market.config_version):
            continue
        deployment, _ = await asyncio.to_thread(_read_pool_bound_deployment, client, draft, index, initial)
        assert_draft()
        expected.append(PoolBoundLifecycleDeployment(deployment.deployer, deployment.init_code_hash, deployment.salt, deployment.predicted_hook, index))
    expected = await _mine_bound_deployments(client, draft, initial, expected,
        cancel_event=cancel_event, on_progress=on_progress)
    assert_draft()
    for deployment in expected:
        index = deployment.market_index
        market = markets[index]
        config = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=market.config_version)
        markets[index] = replace(market, config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=deployment.salt)))
    execution_block = initial
    finalized = replace(draft, markets=tuple(markets))
    owned_progress = None
    opening_buys = None
    from .lifecycle_buy_math import has_profiled_opening_math
    if protection and has_profiled_opening_math(finalized):
        predicted = await asyncio.to_thread(read_lifecycle_token_at_block, client, finalized, execution_block)
        opening_buys = compute_lifecycle_opening_buys(finalized, predicted_token=predicted)
        protected = []
        for buy, quote in zip(finalized.buys, opening_buys):
            minimum = quote.token_out * (10000 - buy_slippage_bps) // 10000
            if minimum <= 0:
                raise LifecyclePlanningError("INVALID_BUY_OUTPUT", "Each opening buy must produce a positive protected minimum")
            protected.append(replace(buy, min_token_out=minimum))
        finalized = replace(finalized, buys=tuple(protected))
    elif protection:
        diagnostic_plan = replace(finalized, buys=tuple(replace(buy, min_token_out=1) for buy in finalized.buys))
        diagnostic = await asyncio.to_thread(_plan_launch, client, diagnostic_plan, **options,
                                             block=execution_block, diagnostic=True)
        assert_draft()
        if diagnostic.progress.phase != LifecyclePhase.NONE or diagnostic.progress.head.phase != LifecyclePhase.NONE:
            raise LifecyclePlanningError("LAUNCH_ALREADY_STARTED", "Buy protection cannot rewrite a committed launch")
        finalized = _protect_ordered_buy_minimums(diagnostic, buy_slippage_bps)
        owned_progress = replace(diagnostic.progress, plan_hash=hash_launch_plan(finalized))
    planned = await asyncio.to_thread(_plan_launch, client, finalized, **options,
                                      block=execution_block, owned_progress=owned_progress,
                                      opening_buys=opening_buys)
    assert_draft()
    _assert_planned_identity(planned)
    if planned.progress.phase not in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        if tuple(expected) != planned.hook_deployments:
            raise LifecyclePlanningError("HOOK_DEPLOYMENT_CHANGED", "Final exact constructor binding differs from the mined candidate")
    if protection and not planned.simulation.admitted:
        raise LifecyclePlanningError("PROTECTED_PLAN_NOT_ADMITTED", "; ".join(planned.simulation.reasons) or "Final protected execution was not proved", planned.simulation)
    if isinstance(client, _LifecycleReadClient):
        client.completion_checks.append(assert_draft)
    return planned


def build_lifecycle_calldata(plan: LaunchPlanV1, command: str, *, first_market: int = 0, count: int = 0) -> str:
    args: list[Any] = [to_launch_plan_tuple(plan)]
    if command == "beginLaunch":
        args.append(1)  # Public pending launches are always explicitly Staged.
    elif command == "prepareMarkets":
        _uint(first_market, 32, "firstMarket")
        _uint(count, 32, "count")
        if count == 0 or first_market + count > len(plan.markets):
            raise ValueError("preparation must be a nonempty contiguous committed market range")
        args.extend((first_market, count))
    elif command not in {"launchAtomic", "activateLaunch", "cancelLaunch"}:
        raise ValueError("only launch-specific lifecycle commands can be constructed")
    return _encode_function(LAUNCH_LIFECYCLE_V1_ABI, command, args)


def read_lifecycle_token_at_block(client: Web3, plan: LaunchPlanV1, block: LaunchBlock) -> str:
    """Read the token prediction at the caller's exact source block."""
    values = _struct_tuple(LAUNCH_PLAN_COMPONENTS_V1, plan)
    if plan.chain_id == 0:
        raise ValueError("prediction chain_id must be positive")
    _address(plan.creator, "creator", nonzero=True)
    _address(plan.orchestrator, "orchestrator", nonzero=True)
    pinned = block
    from .lifecycle_presets import get_known_lifecycle_deployment
    preset = get_known_lifecycle_deployment(chain_id=plan.chain_id, orchestrator=plan.orchestrator)
    key = None if preset is None else _known_prediction_key(plan, preset)
    if isinstance(client, _LifecycleReadClient) and key is not None and key in client.immutable_predictions:
        client.assert_open()
        return client.immutable_predictions[key]
    result = _address(
        _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "predictToken", [values], pinned) if preset is None else
        _call(client, preset.token_factory, _LIFECYCLE_TOKEN_PREDICTION_ABI, "predictToken", [bytes.fromhex(launch_id_of(plan)[2:]), values[4]], pinned),
        "predicted token", nonzero=True)
    if isinstance(client, _LifecycleReadClient) and key is not None:
        client.immutable_predictions[key] = result
    return result


@read_invocation("token.predict")
def predict_launch_token(client: Web3, plan: LaunchPlanV1, *, block: LaunchBlock | None = None) -> str:
    """Predict a token-only draft without claiming economic validation or admission."""
    pinned = block or read_block(client)
    result = read_lifecycle_token_at_block(client, plan, pinned)
    check_lifecycle_source(client, plan.chain_id, pinned)
    return result


@dataclass(frozen=True)
class LifecycleEvent:
    name: str
    args: Mapping[str, Any]
    log_index: int


def decode_lifecycle_events(plan: LaunchPlanV1, logs: Sequence[Mapping[str, Any]]) -> tuple[LifecycleEvent, ...]:
    result: list[LifecycleEvent] = []
    event_abis = {}
    for event in LAUNCH_LIFECYCLE_V1_ABI:
        if event["type"] == "event":
            signature = event["name"] + "(" + ",".join(canonical_abi_type(item) for item in event["inputs"]) + ")"
            event_abis[hex_bytes(keccak(text=signature))] = event
    for index, log in enumerate(logs):
        if str(log.get("address", "")).lower() != plan.orchestrator.lower() or log.get("removed"):
            continue
        topics = [hex_bytes(topic) for topic in log.get("topics", ())]
        if not topics or topics[0] not in event_abis:
            continue
        event = event_abis[topics[0]]
        indexed = [item for item in event["inputs"] if item["indexed"]]
        plain = [item for item in event["inputs"] if not item["indexed"]]
        if len(topics) != len(indexed) + 1:
            raise ValueError("malformed lifecycle receipt topics")
        args = {
            item["name"]: abi_decode([canonical_abi_type(item)], bytes.fromhex(topic[2:]))[0]
            for item, topic in zip(indexed, topics[1:])
        }
        args.update(zip((item["name"] for item in plain), abi_decode([canonical_abi_type(item) for item in plain], bytes.fromhex(hex_bytes(log.get("data", "0x"))[2:]))))
        for item in event["inputs"]:
            if item["type"] == "bytes32":
                args[item["name"]] = hex_bytes(args[item["name"]])
            elif item["type"] == "address":
                args[item["name"]] = to_checksum_address(args[item["name"]])
        if args.get("launchId") != launch_id_of(plan) or ("planHash" in args and args["planHash"] != hash_launch_plan(plan)):
            raise ValueError("receipt belongs to another launch identity or economic commitment")
        if "creator" in args and args["creator"].lower() != plan.creator.lower():
            raise ValueError("receipt creator does not match the committed creator")
        result.append(LifecycleEvent(event["name"], args, quantity(log.get("logIndex", index))))
    return tuple(result)


def decode_lifecycle_launch_receipt(data: bytes | str, plan: LaunchPlanV1) -> LaunchReceiptV1:
    values = abi_decode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], bytes.fromhex(hex_bytes(data)[2:]))[0]
    receipt = _json_tuple(LAUNCH_RECEIPT_COMPONENTS_V1, values)
    if receipt["launchId"] != launch_id_of(plan) or receipt["planHash"] != hash_launch_plan(plan):
        raise ValueError("launch receipt does not match the committed plan")
    if receipt["marketCount"] != len(plan.markets) or len(receipt["quoteSpent"]) != len(plan.buys) or len(receipt["tokenOut"]) != len(plan.buys):
        raise ValueError("launch receipt does not include every committed market and ordered buy")
    return LaunchReceiptV1(
        launch_id=receipt["launchId"], plan_hash=receipt["planHash"], token=receipt["token"],
        fee_hub=receipt["feeHub"], rewards=receipt["rewards"],
        market_count=receipt["marketCount"], position_count=receipt["positionCount"],
        quote_spent=tuple(values[7]), token_out=tuple(values[8]),
    )


@dataclass(frozen=True)
class LifecycleReceiptReference:
    transaction_hash: str
    replacement_hash: str | None = None
    confirmations: int = 1
    observed_block_number: int | None = None
    observed_block_hash: str | None = None
    nonce: int | None = None

    def __post_init__(self) -> None:
        for value in (self.transaction_hash, self.replacement_hash, self.observed_block_hash):
            if value is not None and len(hex_bytes(value)) != 66:
                raise LifecyclePlanningError("INVALID_RECEIPT", "Receipt identities must be bytes32")
        if isinstance(self.confirmations, bool) or not isinstance(self.confirmations, int) or not 1 <= self.confirmations <= (1 << 53) - 1:
            raise LifecyclePlanningError("INVALID_CONFIRMATIONS", "Receipt confirmations must be a positive exact integer")
        if self.observed_block_number is not None:
            _uint(self.observed_block_number, 256, "observed receipt block")
        if self.nonce is not None:
            _uint(self.nonce, 64, "observed transaction nonce")


@dataclass(frozen=True)
class LifecycleReceiptStatus:
    transaction_hash: str
    effective_hash: str
    status: Literal["pending", "unconfirmed", "confirmed", "reorged", "reverted", "replacement-cancelled"]
    replaced: bool
    block_number: int | None = None
    block_hash: str | None = None
    confirmations: int | None = None
    nonce: int | None = None


@dataclass(frozen=True)
class LifecycleCanonicalProgress:
    launch_id: str
    plan_hash: str
    creator: str
    nonce: int
    mode: LifecycleMode | int
    phase: LifecyclePhase | int
    token: str
    fee_hub: str
    rewards: str
    prepared_markets: int
    market_count: int
    buy_count: int
    position_count: int
    deadline: int


@dataclass(frozen=True)
class LifecycleMarketIdentity:
    venue: LifecycleVenue | int
    canonical_id: str
    manager: str
    factory: str
    pool: str
    pool_id: str
    profile_id: str
    currency0: str
    currency1: str
    fee: int
    tick_spacing: int
    hook: str
    opening_sqrt_price_x96: int


@dataclass(frozen=True)
class LifecyclePreparedMarket:
    identity: LifecycleMarketIdentity
    fee_source: str
    custody: str
    mint_executor: str
    buy_executor: str
    exclusions: tuple[str, ...]
    position_count: int


@dataclass(frozen=True)
class LifecycleMarketLiveState:
    sqrt_price_x96: int
    tick: int
    liquidity: int
    public_trading: bool
    oracle_ready_at: int


@dataclass(frozen=True)
class LifecycleMarketProgress:
    index: int
    prepared: LifecyclePreparedMarket
    live: LifecycleMarketLiveState | None = None
    error: str | None = None


@dataclass(frozen=True)
class LaunchProgress:
    launch_id: str
    plan_hash: str
    creator: str
    nonce: int
    mode: str | None
    phase: LifecyclePhase
    token: str
    fee_hub: str
    rewards: str
    prepared_markets: int
    market_count: int
    buy_count: int
    position_count: int
    deadline: int
    block: LaunchBlock
    head_block: LaunchBlock
    awaiting_confirmations: bool
    canonical: LifecycleCanonicalProgress
    head: LifecycleCanonicalProgress
    confirmation_depth: int
    confirmation_safe: bool
    receipts: tuple[LifecycleReceiptStatus, ...] = ()
    markets: tuple[LifecycleMarketProgress, ...] = ()


def _progress_values(client: Web3, plan: LaunchPlanV1, block: LaunchBlock) -> tuple[Any, ...]:
    values = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "readLaunchProgress", [bytes.fromhex(launch_id_of(plan)[2:])], block)
    phase = LifecyclePhase(values[5])
    if phase == LifecyclePhase.NONE:
        return tuple(values)
    if hex_bytes(values[0]) != launch_id_of(plan) or hex_bytes(values[1]) != hash_launch_plan(plan) or values[2].lower() != plan.creator.lower() or values[3] != plan.nonce:
        raise ValueError("canonical progress is bound to different launch economics or identity")
    if values[10] != len(plan.markets) or values[11] != len(plan.buys) or values[13] != plan.deadline or values[9] > values[10]:
        raise ValueError("canonical progress does not match the full committed plan")
    if values[4] not in (0, 1) or phase == LifecyclePhase.ACTIVATING:
        raise ValueError("invalid persisted launch mode/transaction-local phase")
    if phase == LifecyclePhase.READY and values[9] != values[10]:
        raise LifecyclePlanningError("INVALID_PROGRESS", "Canonical ready progress has incomplete markets")
    predicted = read_lifecycle_token_at_block(client, plan, block)
    if values[6].lower() != predicted.lower():
        raise ValueError("canonical token identity differs from deterministic prediction")
    return tuple(values)


def _execution_account(plan: LaunchPlanV1) -> str:
    return _ufg_principal(plan) if plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() else plan.creator


def _check_client_identity(client: Web3, plan: LaunchPlanV1, account: str | None = None) -> None:
    if read_chain_id(client) != plan.chain_id:
        raise LifecyclePlanningError("CHAIN_MISMATCH", "RPC chain ID differs from the economic plan domain")
    if account is not None and _address(account, "account", nonzero=True).lower() != _execution_account(plan).lower():
        raise LifecyclePlanningError("ACCOUNT_MISMATCH", "Execution account must be the committed creator/payer/refund account")


def _transaction_matches_receipt(client: Web3, plan: LaunchPlanV1, transaction: Mapping[str, Any], block: LaunchBlock, ufg_hash: str | None = None) -> bool:
    if str(transaction.get("from", "")).lower() != _execution_account(plan).lower():
        raise LifecyclePlanningError("RECEIPT_ACCOUNT", "Receipt transaction belongs to a different account")
    if transaction.get("chainId") is not None and quantity(transaction["chainId"]) != plan.chain_id:
        raise LifecyclePlanningError("RECEIPT_CHAIN", "Receipt transaction belongs to a different chain")
    target = str(transaction.get("to") or ZERO_ADDRESS).lower()
    calldata = bytes.fromhex(hex_bytes(transaction.get("input", transaction.get("data", "0x")))[2:])
    proxy = plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()
    if target == (UFG_LAUNCH_PROXY_ADDRESS if proxy else plan.orchestrator).lower():
        from .ufg_abis import UFG_LAUNCH_PROXY_V1_ABI
        for command in ("launchAtomic", "beginLaunch", "prepareMarkets", "activateLaunch", "cancelLaunch"):
            entry = _entry(UFG_LAUNCH_PROXY_V1_ABI if proxy else LAUNCH_LIFECYCLE_V1_ABI, command)
            types = [canonical_abi_type(item) for item in entry["inputs"]]
            if calldata[:4] != keccak(text=command + "(" + ",".join(types) + ")")[:4]:
                continue
            try:
                values = abi_decode(types, calldata[4:])
                if proxy and ufg_hash is not None and command in {"launchAtomic", "beginLaunch"}:
                    config = values[1 if command == "launchAtomic" else 2]
                    if hash_ufg_launch_config(UfgLaunchConfig(config[0], config[1], tuple(config[2]))) != ufg_hash:
                        return False
                return hex_bytes(keccak(abi_encode(["bytes32", LAUNCH_PLAN_V1_ABI_TYPE], [LIFECYCLE_PLAN_DOMAIN, values[0]]))) == hash_launch_plan(plan)
            except Exception:
                return False
        return False
    if target not in {funding.input_asset.lower() for funding in plan.funding} or quantity(transaction.get("value", "0x0")) != 0 or calldata[:4] != keccak(text="approve(address,uint256)")[:4]:
        return False
    try:
        spender, _ = abi_decode(["address", "uint256"], calldata[4:])
    except Exception:
        return False
    escrow = UFG_LAUNCH_PROXY_ADDRESS if proxy else _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "fundingEscrow", [], block)
    return spender.lower() == escrow.lower()


def _receipt_header(client: Web3, plan: LaunchPlanV1, number: int) -> LaunchBlock | None:
    headers = getattr(client, "receipt_headers", None)
    if headers is not None and number in headers:
        return headers[number]
    try:
        block = read_block(client, number)
    except LaunchStateChanged:
        block = None
    if headers is not None:
        headers[number] = block
    if block is not None:
        check_lifecycle_source(client, plan.chain_id, block)
    return block


def _receipt_status(client: Web3, plan: LaunchPlanV1, reference: LifecycleReceiptReference, head: LaunchBlock, confirmations: int, ufg_hash: str | None = None) -> LifecycleReceiptStatus:
    original = hex_bytes(reference.transaction_hash)
    effective = hex_bytes(reference.replacement_hash or original)
    replaced = effective != original
    observed_nonce = reference.nonce
    def status(value: str, number: int | None = None, block_hash: str | None = None, depth: int = 0) -> LifecycleReceiptStatus:
        return LifecycleReceiptStatus(original, effective, value, replaced, number, block_hash, depth, observed_nonce)
    if reference.observed_block_number is not None and reference.observed_block_hash is not None:
        observed = _receipt_header(client, plan, reference.observed_block_number)
        if observed is None or observed.block_hash != reference.observed_block_hash.lower():
            return status("reorged", reference.observed_block_number, reference.observed_block_hash)
    raw = rpc(client, "eth_getTransactionReceipt", [effective])
    transaction = rpc(client, "eth_getTransactionByHash", [effective])
    if transaction is not None and transaction.get("nonce") is not None:
        observed_nonce = quantity(transaction["nonce"])
        if reference.nonce is not None and observed_nonce != reference.nonce:
            raise LifecyclePlanningError("RECEIPT_NONCE",
                "Submitted or replacement transaction differs from the retained nonce")
    if raw is None:
        return status("pending", reference.observed_block_number, reference.observed_block_hash)
    if transaction is None:
        raise LifecyclePlanningError("INVALID_RPC_RESPONSE", "Receipt exists without its canonical transaction")
    number, block_hash = quantity(raw["blockNumber"]), hex_bytes(raw["blockHash"])
    receipt_block = _receipt_header(client, plan, number)
    if receipt_block is None or receipt_block.block_hash != block_hash:
        return status("reorged", number, block_hash)
    matches = _transaction_matches_receipt(client, plan, transaction, receipt_block, ufg_hash)
    if not matches and not replaced:
        raise LifecyclePlanningError("RECEIPT_IDENTITY", "Receipt calldata does not belong to the exact committed launch")
    depth = max(0, head.number - number + 1)
    if depth < max(confirmations, reference.confirmations):
        return status("unconfirmed", number, block_hash, depth)
    if not matches:
        return status("replacement-cancelled", number, block_hash, depth)
    return status("confirmed" if quantity(raw["status"]) == 1 else "reverted", number, block_hash, depth)


@read_invocation("plan.progress")
def read_launch_progress(
    client: Web3, plan: LaunchPlanV1, *, confirmations: int = 1,
    receipts: Sequence[LifecycleReceiptReference] = (), block: LaunchBlock | None = None,
    mode: LaunchExecutionMode | None = None, cancel_event: asyncio.Event | None = None,
    ufg: UfgLaunchConfig | None = None,
) -> LaunchProgress:
    """Recover confirmed/head state, submitted receipts, replacements and markets."""
    commitment = hash_launch_plan(plan)
    ufg_hash = None if ufg is None else hash_ufg_launch_config(ufg)
    _check_client_identity(client, plan)
    head = _prime_lifecycle_reads(client, plan, block)
    progress = _read_launch_progress_at_block(client, plan, confirmations=confirmations,
        receipts=receipts, block=head, mode=mode, cancel_event=cancel_event, include_markets=True, ufg_hash=ufg_hash)
    check_lifecycle_source(client, plan.chain_id, progress.block, head)
    def assert_current() -> None:
        _check_mining_cancelled(cancel_event)
        if hash_launch_plan(plan) != commitment or (None if ufg is None else hash_ufg_launch_config(ufg)) != ufg_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan or UFG config changed during canonical progress reads")
    assert_current()
    if isinstance(client, _LifecycleReadClient):
        client.completion_checks.append(assert_current)
    return progress


def _read_prepared_market_progress(client: Web3, plan: LaunchPlanV1, block: LaunchBlock, token: str, count: int) -> tuple[LifecycleMarketProgress, ...]:
    from .addresses import get_launch_addresses
    from .lifecycle_presets import get_known_lifecycle_deployment
    snapshot = getattr(client, "lens_snapshots", {}).get((block.number, block.block_hash, launch_id_of(plan)))
    if snapshot is not None:
        rows = [(index, prepared, live if success else None)
            for index, _, prepared, success, live, _ in snapshot[9]]
    else:
        preset = get_known_lifecycle_deployment(chain_id=plan.chain_id, orchestrator=plan.orchestrator)
        batched = isinstance(client, _LifecycleReadClient) and preset is not None
        directory = get_launch_addresses(plan.chain_id).directory if preset is not None else _call(
            client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "directory", [], block)
        launch_id = bytes.fromhex(launch_id_of(plan)[2:])
        calls = [{"to": _address(directory, "directory"), "data": _encode_function(
            LAUNCH_DIRECTORY_V1_ABI, "market", [launch_id, index])} for index in range(count)]
        if batched:
            results = _aggregate_lifecycle_reads(client, calls, tag=block.tag)
            outputs = [canonical_abi_type(item) for item in _entry(LAUNCH_DIRECTORY_V1_ABI, "market")["outputs"]]
            prepared = [abi_decode(outputs, data) for _, data in results]
            calls = [{"to": _address(adapter, "adapter"), "data": _encode_function(
                LAUNCH_MARKET_ADAPTER_V1_ABI, "readMarket", [launch_id, index]), "allow_failure": True}
                for index, (adapter, _) in enumerate(prepared)]
            live = _aggregate_lifecycle_reads(client, calls, tag=block.tag)
            outputs = [canonical_abi_type(item) for item in _entry(LAUNCH_MARKET_ADAPTER_V1_ABI, "readMarket")["outputs"]]
            rows = [(index, stored, abi_decode(outputs, data)[0] if success else None)
                for index, ((_, stored), (success, data)) in enumerate(zip(prepared, live))]
        else:
            rows = []
            for index in range(count):
                adapter, stored = _call(client, directory, LAUNCH_DIRECTORY_V1_ABI, "market", [launch_id, index], block)
                try:
                    live = _call(client, adapter, LAUNCH_MARKET_ADAPTER_V1_ABI, "readMarket", [launch_id, index], block)
                except (LaunchRpcError, ValueError, RuntimeError):
                    live = None
                rows.append((index, stored, live))
    markets = []
    for index, prepared, live in rows:
        validate_lifecycle_market_identity(prepared[0], plan, token, index)
        identity = LifecycleMarketIdentity(*[
            hex_bytes(value) if component["type"] == "bytes32" else value
            for component, value in zip(MARKET_IDENTITY_COMPONENTS_V1, prepared[0])])
        stored = LifecyclePreparedMarket(identity, *prepared[1:5], tuple(prepared[5]), prepared[6])
        markets.append(LifecycleMarketProgress(index, stored,
            None if live is None else LifecycleMarketLiveState(*live),
            "Market state read unavailable" if live is None else None))
    return tuple(markets)


def _read_launch_progress_at_block(
    client: Web3, plan: LaunchPlanV1, *, block: LaunchBlock, confirmations: int = 1,
    receipts: Sequence[LifecycleReceiptReference] = (), mode: LaunchExecutionMode | None = None,
    cancel_event: asyncio.Event | None = None, include_markets: bool = False,
    ufg_hash: str | None = None,
) -> LaunchProgress:
    commitment = hash_launch_plan(plan)
    _check_mining_cancelled(cancel_event)
    if isinstance(confirmations, bool) or not isinstance(confirmations, int) or not 1 <= confirmations <= (1 << 53) - 1:
        raise LifecyclePlanningError("INVALID_CONFIRMATIONS", "Confirmation depth must be a positive exact integer")
    head = block
    confirmed = head if confirmations == 1 else read_block(client, max(0, head.number - confirmations + 1))
    if confirmed.number != head.number:
        _prime_lifecycle_reads(client, plan, confirmed)
    values = _progress_values(client, plan, confirmed)
    current = values if confirmed.number == head.number else _progress_values(client, plan, head)
    for observation in (values, current):
        if observation[5] != 0 and mode is not None and observation[4] != (0 if _mode(mode) == "atomic" else 1):
            raise LifecyclePlanningError("MODE_MISMATCH", "Execution mode differs from the recorded launch")
    token = read_lifecycle_token_at_block(client, plan, head)
    statuses = tuple(_receipt_status(client, plan, reference, head, confirmations, ufg_hash) for reference in receipts)
    safe = values == current and not any(status.status in {"unconfirmed", "pending"} for status in statuses)
    markets = _read_prepared_market_progress(client, plan, confirmed, token, values[9]) if include_markets and values[9] > 0 else ()
    _check_mining_cancelled(cancel_event)
    if hash_launch_plan(plan) != commitment:
        raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan changed during canonical progress reads")
    def progress_tuple(observation: Sequence[Any]) -> LifecycleCanonicalProgress:
        return LifecycleCanonicalProgress(hex_bytes(observation[0]), hex_bytes(observation[1]), *observation[2:])
    return LaunchProgress(
        launch_id=launch_id_of(plan), plan_hash=commitment, creator=_address(plan.creator, "creator"), nonce=plan.nonce,
        mode=None if values[5] == 0 else ("atomic" if values[4] == 0 else "staged"), phase=LifecyclePhase(values[5]),
        token=token, fee_hub=to_checksum_address(values[7]), rewards=to_checksum_address(values[8]),
        prepared_markets=values[9], market_count=values[10], buy_count=values[11], position_count=values[12], deadline=values[13],
        block=confirmed, head_block=head, awaiting_confirmations=not safe,
        receipts=statuses, canonical=progress_tuple(values), head=progress_tuple(current), confirmation_depth=confirmations,
        confirmation_safe=safe, markets=tuple(markets),
    )


@dataclass(frozen=True)
class LifecycleApproval:
    asset: str
    spender: str
    required_amount: int
    allowance: int
    balance: int
    needs_approval: bool


@dataclass(frozen=True)
class LifecycleFundingPrerequisite:
    funding: LifecycleAssetFunding
    input_balance: int
    required_input: int
    spender: str
    native_value: int
    conversion: Literal["native-wrap", "allowlisted-swap", "none"]
    allowance: int | None = None


@dataclass(frozen=True)
class LifecycleLaunchPostcondition:
    phase: Literal["Preparing", "Ready", "Active", "Cancelled"]
    prepared_markets: int
    kind: Literal["launch"] = "launch"


@dataclass(frozen=True)
class LifecycleAllowancePostcondition:
    asset: str
    spender: str
    minimum: int
    exact: int | None = None
    kind: Literal["allowance"] = "allowance"


LifecyclePostcondition = LifecycleLaunchPostcondition | LifecycleAllowancePostcondition
LifecycleTransactionKind = Literal["approve-reset", "approve", "atomic", "begin", "prepare", "activate", "cancel"]
LifecycleFailureCategory = Literal["semantic", "capacity", "opaque", "source", "postcondition", "affordability"]
LifecycleCapacityConstraint = Literal["compute", "gas-envelope", "calldata", "simulation-gas", "activation"]


@dataclass(frozen=True)
class LifecycleProofOutcomes:
    execution_proof: Literal["proved", "failed", "unavailable", "not-run"]
    protocol_fit: Literal["proved", "failed", "unknown", "profiled"]


@dataclass(frozen=True)
class LifecycleTransactionEstimate:
    gas_limit: int
    gas_price: int
    execution_fee: int
    data_fee_included_in_gas: bool
    fee_confidence: Literal["execution-and-data", "execution-only", "native-ceiling-and-data"]
    gas_used: int | None = None
    data_fee: int | None = None
    total_fee: int | None = None
    poster_gas: int | None = None
    poster_fee: int | None = None


@dataclass(frozen=True)
class LifecycleOpeningBuyQuote:
    buy_index: int
    market_index: int
    quote_amount_in: int
    quote_spent: int
    token_out: int
    sqrt_price_after_x96: int


@dataclass(frozen=True)
class LifecycleLaunchGasBounds:
    """Conservative mode/capacity bounds, never signing-gas sizing."""
    atomic_compute_gas_upper_bound: int
    activation_compute_gas_upper_bound: int


@dataclass(frozen=True)
class LifecycleGasProfileEvidence:
    version: str
    bounds: LifecycleLaunchGasBounds
    initial_buys: tuple[LifecycleOpeningBuyQuote, ...]


def compute_lifecycle_opening_buys(
    plan: LaunchPlanV1, *, predicted_token: str,
) -> tuple[LifecycleOpeningBuyQuote, ...]:
    """Quote ordered unopened release curves exactly, without execution proof.

    Funding, token callbacks, current registry admission and wallet execution
    remain separate authorities. This is not a live-pool quote.
    """
    from .lifecycle_buy_math import compute_opening_buys
    return compute_opening_buys(plan, predicted_token)


def compute_lifecycle_launch_gas_bounds(
    plan: LaunchPlanV1, *, predicted_token: str, ufg: UfgLaunchConfig | None = None,
    headroom_bps: int = 1500,
) -> LifecycleLaunchGasBounds | None:
    """Return conservative compute-only mode bounds, not signing gas or proof."""
    from .lifecycle_gas_profile import compute_launch_gas_bounds
    return compute_launch_gas_bounds(plan, predicted_token=predicted_token,
        ufg=ufg, headroom_bps=headroom_bps)


@dataclass(frozen=True)
class LifecycleSimulationStep:
    transaction_id: str
    success: bool
    gas_used: int | None = None
    gas_limit: int | None = None
    gas_required: int | None = None
    return_data: str | None = None
    error: str | None = None
    estimate: LifecycleTransactionEstimate | None = None
    failure_category: LifecycleFailureCategory | None = None
    decoded_error: str | None = None
    native_error_code: int | None = None
    native_error_kind: Literal["out-of-gas", "execution-reverted", "vm-error", "other", "missing"] | None = None
    native_error_data_bytes: int | None = None
    native_gas_capped: bool = False
    # Successful suffix feasibility is not an exact future signing envelope.
    provisional: bool = False


@dataclass(frozen=True)
class LifecycleAdmission:
    """Execution evidence or explicit profile coverage, separate from submission."""

    admitted: bool
    confidence: Literal["stateful", "provisional", "profiled"]
    limits: LaunchExecutionLimits
    block_number: int
    block_hash: str
    account: str
    chain_id: int
    execution_proof: Literal["proved", "failed", "unavailable", "not-run"]
    protocol_fit: Literal["proved", "failed", "unknown", "profiled"]
    reason: str | None = None

    @property
    def outcomes(self) -> LifecycleProofOutcomes:
        return LifecycleProofOutcomes(self.execution_proof, self.protocol_fit)


@dataclass(frozen=True)
class LifecycleTransaction:
    """An unsigned envelope; the wallet assigns its EOA transaction nonce."""
    step_id: str
    kind: LifecycleTransactionKind
    chain_id: int
    from_address: str
    to: str
    data: str
    value: int
    dependencies: tuple[str, ...]
    postconditions: tuple[LifecyclePostcondition, ...]
    first_market: int | None = None
    market_count: int | None = None
    gas_estimate: int | None = None
    gas_limit: int | None = None
    gas_price: int | None = None
    execution_fee: int | None = None
    maximum_execution_fee: int | None = None
    data_fee: int | None = None
    poster_gas: int | None = None
    poster_fee: int | None = None
    data_fee_included_in_gas: bool = False
    total_fee: int | None = None
    gas_used: int | None = None
    confidence: str = "provisional"
    admission: LifecycleAdmission | None = None

    @property
    def estimate(self) -> LifecycleTransactionEstimate | None:
        if self.gas_limit is None or self.gas_price is None or self.execution_fee is None:
            return None
        confidence = "native-ceiling-and-data" if self.confidence == "profiled" else (
            "execution-only" if self.total_fee is None else "execution-and-data")
        return LifecycleTransactionEstimate(gas_limit=self.gas_limit, gas_price=self.gas_price,
            execution_fee=self.execution_fee, data_fee_included_in_gas=self.data_fee_included_in_gas,
            fee_confidence=confidence, gas_used=self.gas_used, data_fee=self.data_fee,
            total_fee=self.total_fee, poster_gas=self.poster_gas, poster_fee=self.poster_fee)

    @property
    def calldata_bytes(self) -> int:
        return (len(self.data) - 2) // 2

    def as_transaction(self) -> dict[str, Any]:
        result: dict[str, Any] = {"chainId": self.chain_id, "from": self.from_address, "to": self.to, "data": self.data, "value": self.value}
        if self.gas_limit is not None:
            result["gas"] = self.gas_limit
        if self.gas_price is not None:
            result["gasPrice"] = self.gas_price
        return result


@dataclass(frozen=True)
class LaunchSimulation:
    confidence: Literal["stateful", "provisional", "profiled"]
    admitted: bool
    block: LaunchBlock
    backend: Literal["eth_simulateV1", "controlled-fork", "profiled"] | None
    transactions: tuple[LifecycleTransaction, ...]
    reasons: tuple[str, ...]
    evidence: LaunchRpcSimulation
    unknown_constraints: tuple[str, ...] = ()
    execution_proof: Literal["proved", "failed", "unavailable", "not-run"] = "unavailable"
    protocol_fit: Literal["proved", "failed", "unknown", "profiled"] = "unknown"
    chain_id: int | None = None
    account: str | None = None
    failure_category: LifecycleFailureCategory | None = None
    capacity_constraint: LifecycleCapacityConstraint | None = None
    failed_transaction_id: str | None = None
    limits: LaunchExecutionLimits | None = None
    deferred_transaction_ids: tuple[str, ...] = ()
    gas_profile: LifecycleGasProfileEvidence | None = None

    @property
    def outcomes(self) -> LifecycleProofOutcomes:
        return LifecycleProofOutcomes(self.execution_proof, self.protocol_fit)

    @property
    def steps(self) -> tuple[LifecycleSimulationStep, ...]:
        return tuple(LifecycleSimulationStep(transaction.step_id, call.success, call.gas_used,
            call.gas_limit, call.gas_required, call.return_data, call.error,
            transaction.estimate if call.success else None, call.failure_category, call.decoded_error,
            call.native_error_code, call.native_error_kind, call.native_error_data_bytes, call.native_gas_capped,
            provisional=index > 0)
            for index, (transaction, call) in enumerate(zip(self.transactions, self.evidence.calls)))


@dataclass(frozen=True)
class PlannedLaunch:
    plan: LaunchPlanV1
    plan_hash: str
    launch_id: str
    predicted_token: str
    chain_id: int
    account: str
    mode: LaunchExecutionMode
    transactions: tuple[LifecycleTransaction, ...]
    approvals: tuple[LifecycleApproval, ...]
    progress: LaunchProgress
    simulation: LaunchSimulation
    limits: LaunchExecutionLimits
    irreversible_costs: tuple[str, ...]
    token_factory: str
    token_factory_code_hash: str
    profiles: tuple[LifecycleProfile | LifecycleConstructionProfile, ...] = ()
    hook_deployments: tuple[PoolBoundLifecycleDeployment, ...] = ()
    prerequisites: tuple[LifecycleFundingPrerequisite, ...] = ()
    total_calldata_bytes: int = 0
    limits_source: LaunchExecutionLimits | LifecycleLimitResolver | None = None
    confirmations: int = 1
    receipt_references: tuple[LifecycleReceiptReference, ...] = ()
    allow_topology_gaps: bool = False
    allow_missing_permanent_tail: bool = False
    ufg: UfgLaunchConfig | None = None
    ufg_config_hash: str | None = None

    @property
    def admitted(self) -> bool:
        return self.simulation.admitted

    @property
    def confidence(self) -> str:
        return self.simulation.confidence


_ERC20_READ_ABI = [
    {"type": "function", "name": "allowance", "stateMutability": "view", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view", "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}]},
]

_LIFECYCLE_TOKEN_PREDICTION_ABI = [{
    "type": "function", "name": "predictToken", "stateMutability": "view",
    "inputs": [
        {"name": "launchId", "type": "bytes32"},
        {"name": "config", "type": "tuple", "components": TOKEN_CONFIG_COMPONENTS_V1},
    ],
    "outputs": [{"type": "address"}],
}]

_LIFECYCLE_MULTICALL_CODE_HASH = "0xd5c15df687b16f2ff992fc8d767b4216323184a2bbc6ee2f9c398c318e770891"


def _known_prediction_key(plan: LaunchPlanV1, preset: Any) -> str:
    return json.dumps([plan.chain_id, plan.orchestrator.lower(), preset.token_factory.lower(),
        preset.token_factory_code_hash.lower(), launch_id_of(plan),
        hex_bytes(keccak(abi_encode([_tuple_type(TOKEN_CONFIG_COMPONENTS_V1)],
            [_struct_tuple(TOKEN_CONFIG_COMPONENTS_V1, plan.token)])))], separators=(",", ":"))


def _aggregate_lifecycle_reads(
    client: _LifecycleReadClient, calls: Sequence[Mapping[str, Any]], *, tag: str,
) -> tuple[tuple[bool, bytes], ...]:
    key = ("multicall", ROBINHOOD_MAINNET_CHAIN_ID, ROBINHOOD_MULTICALL3.lower())
    if key not in client.capabilities:
        code = hex_bytes(rpc(client, "eth_getCode", [ROBINHOOD_MULTICALL3, tag]))
        if hex_bytes(keccak(bytes.fromhex(code[2:]))) != _LIFECYCLE_MULTICALL_CODE_HASH:
            raise LifecyclePlanningError("MULTICALL_CODE_MISMATCH", "Multicall3 runtime differs from the reviewed Robinhood deployment")
        client.capabilities.add(key)
    data = hex_bytes(keccak(text="aggregate3((address,bool,bytes)[])")[:4] +
        abi_encode(["(address,bool,bytes)[]"], [[
            (call["to"], call.get("allow_failure", False), bytes.fromhex(call["data"][2:]))
            for call in calls]]))
    raw = rpc(client, "eth_call", [{"to": ROBINHOOD_MULTICALL3, "data": data}, tag])
    results = abi_decode(["(bool,bytes)[]"], bytes.fromhex(hex_bytes(raw)[2:]))[0]
    if len(results) != len(calls) or any(not success and not call.get("allow_failure")
            for call, (success, _) in zip(calls, results)):
        raise LifecyclePlanningError("INVALID_RPC_RESPONSE", "Multicall3 did not return every requested read")
    return results


def _seed_lens_reads(client: _LifecycleReadClient, plan: LaunchPlanV1, block: LaunchBlock, preset: Any, snapshot: tuple[Any, ...]) -> None:
    if snapshot[0] != plan.chain_id or snapshot[1] != block.number or hex_bytes(snapshot[7]) != launch_id_of(plan):
        raise LifecyclePlanningError("INVALID_RPC_RESPONSE", "Launch lens returned a different planning domain")
    client.lens_snapshots[(block.number, block.block_hash, launch_id_of(plan))] = snapshot
    def seed(target, abi, name, args, value):
        call = {"to": _address(target, name), "data": _encode_function(abi, name, args)}
        outputs = _entry(abi, name)["outputs"]
        client.seed_read("eth_call", [call, block.tag], block,
            hex_bytes(abi_encode([canonical_abi_type(item) for item in outputs], value)))
    seed(preset.token_factory, _LIFECYCLE_TOKEN_PREDICTION_ABI, "predictToken",
        [bytes.fromhex(launch_id_of(plan)[2:]), _struct_tuple(TOKEN_CONFIG_COMPONENTS_V1, plan.token)], [snapshot[6]])
    seed(plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "readLaunchProgress",
        [bytes.fromhex(launch_id_of(plan)[2:])], [snapshot[8]])
    account = _execution_account(plan)
    client.seed_read("eth_getBalance", [account, block.tag], block, hex(snapshot[4]))
    spender = UFG_LAUNCH_PROXY_ADDRESS if plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() else preset.funding_escrow
    for token, balance, allowance in snapshot[5]:
        seed(token, _ERC20_READ_ABI, "balanceOf", [account], [balance])
        seed(token, _ERC20_READ_ABI, "allowance", [account, spender], [allowance])


def _prime_lifecycle_reads(client: Web3, plan: LaunchPlanV1, block: LaunchBlock | None = None, *, mining: bool = False) -> LaunchBlock:
    """Pin and batch every independent planning read, including native balances."""
    from .lifecycle_presets import get_known_lifecycle_deployment
    preset = get_known_lifecycle_deployment(chain_id=plan.chain_id, orchestrator=plan.orchestrator)
    if not isinstance(client, _LifecycleReadClient) or plan.chain_id != ROBINHOOD_MAINNET_CHAIN_ID or preset is None:
        return block or read_block(client)
    # Nitro's EVM BLOCKNUMBER/BLOCKHASH are L1 values, not the RPC's L2 domain.
    block = block or read_block(client)
    calls = []
    def add(target, abi, name, args=()):
        call = {"to": _address(target, name), "data": _encode_function(abi, name, args)}
        if not client.has_read("eth_call", [call, block.tag], block):
            calls.append(call)
        return call
    for target, abi, name in (
        (NITRO_ARB_SYS_ADDRESS, NITRO_ARB_SYS_ABI, "arbOSVersion"),
        (NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI, "getPricesInWei"),
    ):
        add(target, abi, name)
    account = _execution_account(plan)
    tokens = tuple(dict.fromkeys(_address(item.input_asset, "input asset")
        for item in plan.funding if int(item.kind) != 1 and int(item.input_asset, 16)))
    spender = UFG_LAUNCH_PROXY_ADDRESS if plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() else preset.funding_escrow
    lens_call = None
    native_call = None
    snapshot_key = (block.number, block.block_hash, launch_id_of(plan))
    if preset.launch_lens is not None:
        if snapshot_key not in client.lens_snapshots:
            lens_call = add(preset.launch_lens, LAUNCH_LENS_ABI, "snapshot",
                [to_launch_plan_tuple(plan), account, spender, tokens, 150_000 if mining else 0])
    else:
        if _known_prediction_key(plan, preset) not in client.immutable_predictions:
            add(preset.token_factory, _LIFECYCLE_TOKEN_PREDICTION_ABI, "predictToken",
                [bytes.fromhex(launch_id_of(plan)[2:]), _struct_tuple(TOKEN_CONFIG_COMPONENTS_V1, plan.token)])
        add(plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "readLaunchProgress", [bytes.fromhex(launch_id_of(plan)[2:])])
        if not client.has_read("eth_getBalance", [account, block.tag], block):
            native_call = {"to": ROBINHOOD_MULTICALL3,
                "data": hex_bytes(keccak(text="getEthBalance(address)")[:4] + abi_encode(["address"], [account]))}
            calls.append(native_call)
        for token in tokens:
            add(token, _ERC20_READ_ABI, "balanceOf", [account])
            add(token, _ERC20_READ_ABI, "allowance", [account, spender])
    if not calls:
        return block
    results = _aggregate_lifecycle_reads(client, calls, tag=block.tag)
    for call, (_, data) in zip(calls, results):
        if call is native_call:
            client.seed_read("eth_getBalance", [account, block.tag], block, hex(abi_decode(["uint256"], data)[0]))
        else:
            client.seed_read("eth_call", [call, block.tag], block, hex_bytes(data))
        if call is lens_call:
            snapshot = abi_decode([_tuple_type(LAUNCH_LENS_SNAPSHOT_COMPONENTS)], data)[0]
            _seed_lens_reads(client, plan, block, preset, snapshot)
    return block


def _read_nitro_uint(client: Web3, block: LaunchBlock, target: str, abi: Sequence[Mapping[str, Any]], name: str, bits: int) -> int:
    data = _encode_function(abi, name, [])
    raw = hex_bytes(pinned_rpc(client, "eth_call", [{"to": _address(target, name), "data": data}, block.tag], block))
    if len(raw) != 66:
        raise ValueError(f"Nitro {name} must return exactly one ABI word")
    return _uint(abi_decode([f"uint{bits}"], bytes.fromhex(raw[2:]))[0], bits, f"Nitro {name}")


def _read_nitro_prices(client: Web3, block: LaunchBlock) -> tuple[int, ...]:
    return tuple(_call(client, NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI,
        "getPricesInWei", [], block))


def _read_lifecycle_gas_price(client: Web3, block: LaunchBlock, chain_id: int) -> int:
    if chain_id != 4663:
        return read_gas_price(client, block)
    return max(_read_nitro_prices(client, block)[5], (block.base_fee_per_gas or 0) * 2)


def _resolve_limits(client: Web3, source: LaunchExecutionLimits | LifecycleLimitResolver | None, block: LaunchBlock, account: str, chain_id: int, orchestrator: str, *, policy_guards: list[Callable[[], None]] | None = None) -> LaunchExecutionLimits:
    raw_client = getattr(client, "source", client)
    context = LaunchLimitContext(block, chain_id, _address(orchestrator, "orchestrator"),
        _address(account, "account"), getattr(getattr(raw_client, "provider", None), "endpoint_uri", None))
    try:
        limits = source(raw_client, context) if callable(source) else source if source is not None else LaunchExecutionLimits()
    except Exception as error:
        raise LifecyclePlanningError("LIMIT_SOURCE_FAILED", f"Supplied execution policy could not be resolved: {failure_reason(error)}") from None
    if not isinstance(limits, LaunchExecutionLimits):
        raise LifecyclePlanningError("INVALID_LIMITS", "A supplied execution-limit resolver must return LaunchExecutionLimits")
    supplied = limits
    fields = tuple(vars(supplied).items())
    def assert_policy() -> None:
        if tuple(vars(supplied).items()) != fields:
            raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Supplied execution policy changed after its pinned observation")
    if policy_guards is not None:
        policy_guards.append(assert_policy)
    limits = replace(limits)
    if limits.observed_block_number is not None and limits.observed_block_number != block.number:
        raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Execution limits were observed at a different block number")
    if limits.observed_block_hash is not None and limits.observed_block_hash.lower() != block.block_hash:
        raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Execution limits were observed on a different canonical block")
    if limits.chain_id is not None and limits.chain_id != chain_id:
        raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Execution limits belong to a different chain")
    if limits.account is not None and _address(limits.account, "limits account").lower() != account.lower():
        raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Execution limits belong to a different account")
    if limits.orchestrator is not None and _address(limits.orchestrator, "limits orchestrator").lower() != orchestrator.lower():
        raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Execution limits belong to a different orchestrator")
    supplied_caps = [value for value in (
        limits.chain_transaction_gas_limit, limits.rpc_transaction_gas_limit,
        limits.account_transaction_gas_limit,
    ) if value is not None]
    if chain_id != ROBINHOOD_MAINNET_CHAIN_ID:
        ceiling = min([block.gas_limit, *supplied_caps])
        return replace(limits, protocol="evm", execution_gas_ceiling=ceiling,
                       transaction_gas_ceiling=ceiling, arb_os_version=None)
    from .lifecycle_presets import ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT, get_known_lifecycle_deployment
    preset = get_known_lifecycle_deployment(chain_id=chain_id, orchestrator=orchestrator)
    native_ceiling = (preset.native_transaction_gas_limit if preset is not None
                      and preset.native_transaction_gas_limit is not None else ROBINHOOD_NATIVE_TRANSACTION_GAS_LIMIT)
    raw_version = _read_nitro_uint(client, block, NITRO_ARB_SYS_ADDRESS, NITRO_ARB_SYS_ABI, "arbOSVersion", 256)
    assert_policy()
    return replace(limits, protocol="nitro", execution_gas_ceiling=native_ceiling,
                   transaction_gas_ceiling=min(supplied_caps) if supplied_caps else None,
                   arb_os_version=raw_version - 55)



def _mode(value: str) -> str:
    if value not in {"atomic", "staged"}:
        raise LifecyclePlanningError("EXPLICIT_MODE_REQUIRED", "Choose atomic preference or staged execution explicitly")
    return value


def _native_value(plan: LaunchPlanV1) -> int:
    return sum(item.input_amount for item in plan.funding if int(item.kind) == 1 or (int(item.kind) == 2 and int(item.input_asset, 16) == 0))


def build_launch_transactions(plan: LaunchPlanV1, *, mode: LaunchExecutionMode, preparation_batches: Sequence[int] | None = None) -> tuple[LifecycleTransaction, ...]:
    """Compile complete unsigned commands without RPC, approval or gas claims."""
    _mode(mode)
    if isinstance(plan.chain_id, bool) or not 0 < plan.chain_id <= (1 << 53) - 1:
        raise LifecyclePlanningError("CHAIN_MISMATCH", "Chain ID must be a positive exact portable integer")
    if not 1 <= len(plan.markets) <= 16:
        raise LifecyclePlanningError("INVALID_MARKET_COUNT", "A launch requires one through sixteen markets")
    if mode == "atomic" and preparation_batches is not None:
        raise LifecyclePlanningError("INVALID_PREPARATION_BATCHES", "Atomic execution has no separate preparation batches")
    _assert_allocation(plan)
    result: list[LifecycleTransaction] = []
    def add(kind: str, command: str, value: int, phase: str, prepared: int, first: int | None = None, count: int | None = None) -> None:
        identity = f"prepare:{first}:{count}" if kind == "prepare" else kind
        data = build_lifecycle_calldata(plan, command, first_market=first or 0, count=count or 0)
        result.append(LifecycleTransaction(identity, kind, plan.chain_id, plan.creator, plan.orchestrator, data, value, () if not result else (result[-1].step_id,), (LifecycleLaunchPostcondition(phase, prepared),), first, count))
    if mode == "atomic":
        add("atomic", "launchAtomic", _native_value(plan), "Active", len(plan.markets))
    else:
        batches = tuple(preparation_batches) if preparation_batches is not None else (len(plan.markets),)
        if not batches or any(isinstance(count, bool) or not isinstance(count, int) or not 0 < count <= (1 << 53) - 1 for count in batches) or sum(batches) != len(plan.markets):
            raise LifecyclePlanningError("INVALID_PREPARATION_BATCHES", "Positive ordered preparation counts must cover every market exactly")
        add("begin", "beginLaunch", _native_value(plan), "Preparing", 0)
        first = 0
        for count in batches:
            completed = first + count
            add("prepare", "prepareMarkets", 0, "Ready" if completed == len(plan.markets) else "Preparing", completed, first, count)
            first = completed
        add("activate", "activateLaunch", 0, "Active", len(plan.markets))
    return tuple(result)


def _read_plan_metadata(client: Web3, plan: LaunchPlanV1, token: str, block: LaunchBlock) -> tuple[tuple[LifecycleProfile | LifecycleConstructionProfile, ...], tuple[PoolBoundLifecycleDeployment, ...], tuple[str, ...]]:
    from .lifecycle_presets import get_known_lifecycle_profile
    ids = tuple(dict.fromkeys(hex_bytes(market.profile_id) for market in plan.markets))
    known = {identity: get_known_lifecycle_profile(chain_id=plan.chain_id, orchestrator=plan.orchestrator, profile_id=identity) for identity in ids}
    unknown = tuple(identity for identity in ids if known[identity] is None)
    live = read_lifecycle_profiles(client, orchestrator=plan.orchestrator, profile_ids=unknown, block=block) if unknown else ()
    profiles = []
    for identity in ids:
        preset = known[identity]
        if preset is not None:
            if not preset.supported:
                raise LifecyclePlanningError("UNSUPPORTED_CONFIG_VERSION", preset.unavailable_reason or "Unsupported historical construction preset")
            profiles.append(LifecycleConstructionProfile(**vars(preset.profile)))
        else:
            profile = next((profile for profile in live if profile.id.lower() == identity.lower()), None)
            if profile is None:
                raise LifecyclePlanningError("PROFILE_NOT_FOUND", "Unlisted discovery omitted the committed profile")
            profiles.append(profile)
    deployments = []
    for index, market in enumerate(plan.markets):
        if not is_pool_bound_v4_config_version(market.config_version):
            continue
        known_deployment = _known_bound_deployment(plan, index, token)
        deployment = known_deployment[0] if known_deployment is not None else _read_pool_bound_deployment(client, plan, index, block)[0]
        deployments.append(PoolBoundLifecycleDeployment(deployment.deployer, deployment.init_code_hash, deployment.salt, deployment.predicted_hook, index))
    return tuple(profiles), tuple(deployments), tuple(profile.reason or "Unlisted lifecycle profile unavailable" for profile in live if not profile.admitted)


def _read_funding(client: Web3, plan: LaunchPlanV1, block: LaunchBlock) -> tuple[tuple[LifecycleFundingPrerequisite, ...], tuple[LifecycleApproval, ...]]:
    from .lifecycle_presets import get_known_lifecycle_deployment
    if not plan.funding:
        return (), ()
    preset = get_known_lifecycle_deployment(chain_id=plan.chain_id, orchestrator=plan.orchestrator)
    spender = UFG_LAUNCH_PROXY_ADDRESS if plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() else preset.funding_escrow if preset is not None else _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "fundingEscrow", [], block), "funding escrow", nonzero=True)
    account = _execution_account(plan)
    prerequisites = []
    totals: dict[str, tuple[str, int, int, int]] = {}
    for funding in plan.funding:
        native = int(funding.kind) == 1 or (int(funding.kind) == 2 and int(funding.input_asset, 16) == 0)
        balance = quantity(pinned_rpc(client, "eth_getBalance", [account, block.tag], block)) if native else _call(client, funding.input_asset, _ERC20_READ_ABI, "balanceOf", [account], block)
        allowance = None if native else _call(client, funding.input_asset, _ERC20_READ_ABI, "allowance", [account, spender], block)
        prerequisites.append(LifecycleFundingPrerequisite(funding, balance, funding.input_amount, spender,
            funding.input_amount if native else 0, "native-wrap" if int(funding.kind) == 1 else "allowlisted-swap" if int(funding.kind) == 2 else "none", allowance))
        if allowance is not None:
            key = funding.input_asset.lower()
            prior = totals.get(key)
            totals[key] = (funding.input_asset, funding.input_amount + (prior[1] if prior is not None else 0), allowance, balance)
    approvals = tuple(LifecycleApproval(asset, spender, amount, allowance, balance, allowance < amount)
                      for asset, amount, allowance, balance in totals.values())
    return tuple(prerequisites), approvals


def _market_tuple(plan: LaunchPlanV1, index: int) -> tuple[Any, ...]:
    return to_launch_plan_tuple(plan)[7][index]


def _make_transactions(plan: LaunchPlanV1, mode: str, progress: LaunchProgress, approvals: Sequence[LifecycleApproval], groups: Sequence[tuple[int, int]], *, gas_cap: int, gas_price: int, cancel: bool = False, ufg: UfgLaunchConfig | None = None) -> tuple[LifecycleTransaction, ...]:
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        return ()
    result: list[LifecycleTransaction] = []
    if cancel:
        transaction = build_ufg_cancel_transaction(plan) if ufg is not None else LifecycleTransaction("cancel", "cancel", plan.chain_id, plan.creator, plan.orchestrator, build_lifecycle_calldata(plan, "cancelLaunch"), 0, (), ())
        result.append(replace(transaction, postconditions=(LifecycleLaunchPostcondition("Cancelled", progress.prepared_markets),)))
    else:
        if progress.phase == LifecyclePhase.NONE:
            for approval in approvals:
                if not approval.needs_approval:
                    continue
                for kind, amount in (("approve-reset", 0), ("approve", approval.required_amount)) if approval.allowance else (("approve", approval.required_amount),):
                    condition = LifecycleAllowancePostcondition(approval.asset, approval.spender, amount, 0 if kind == "approve-reset" else None)
                    result.append(LifecycleTransaction(f"{kind}:{approval.asset}", kind, plan.chain_id, _execution_account(plan), approval.asset, _encode_function(_ERC20_READ_ABI, "approve", [approval.spender, amount]), 0, (), (condition,)))
        batches = ([progress.prepared_markets] if progress.prepared_markets else []) + [count for _, count in groups]
        commands = (build_launch_transactions(plan, mode=mode, preparation_batches=batches if mode == "staged" else None)
            if ufg is None else build_ufg_launch_transactions(plan, config=ufg, mode=mode,
                preparation_batches=batches if mode == "staged" else None))
        result.extend(transaction for transaction in commands
                      if (transaction.kind not in {"begin", "atomic"} or progress.phase == LifecyclePhase.NONE)
                      and (transaction.kind != "prepare" or transaction.first_market >= progress.prepared_markets))
    return tuple(replace(transaction, gas_limit=gas_cap, gas_price=gas_price,
                         dependencies=() if index == 0 else (result[index - 1].step_id,))
                 for index, transaction in enumerate(result))


def _condition_requests(plan: LaunchPlanV1, transaction: LifecycleTransaction) -> tuple[Mapping[str, str], ...]:
    requests = []
    for condition in transaction.postconditions:
        if condition.kind == "allowance":
            requests.append({"to": condition.asset, "data": _encode_function(_ERC20_READ_ABI, "allowance", [_execution_account(plan), condition.spender])})
        else:
            requests.append({"to": plan.orchestrator, "data": _encode_function(LAUNCH_LIFECYCLE_V1_ABI, "readLaunchProgress", [bytes.fromhex(launch_id_of(plan)[2:])])})
    return tuple(requests)


def _check_progress_condition(plan: LaunchPlanV1, predicted: str, condition: LifecycleLaunchPostcondition, data: str) -> tuple[Any, ...]:
    progress = abi_decode([_tuple_type(LAUNCH_PROGRESS_COMPONENTS_V1)], bytes.fromhex(data[2:]))[0]
    phase = LifecyclePhase[condition.phase.upper()]
    if progress[5] != phase or progress[9] != condition.prepared_markets or hex_bytes(progress[0]) != launch_id_of(plan) or hex_bytes(progress[1]) != hash_launch_plan(plan) or progress[6].lower() != predicted.lower():
        raise ValueError("Simulation did not establish exact identity, phase and ordered progress")
    return progress


def _check_transaction_postconditions(plan: LaunchPlanV1, transaction: LifecycleTransaction, call: LaunchSimulationCall, predicted: str, *, fork: bool) -> None:
    events = decode_lifecycle_events(plan, call.logs)
    if fork:
        if len(call.postcondition_data) != len(transaction.postconditions):
            raise ValueError("Controlled execution omitted actual postcondition reads")
        for condition, data in zip(transaction.postconditions, call.postcondition_data):
            if condition.kind == "allowance":
                allowance = abi_decode(["uint256"], bytes.fromhex(data[2:]))[0]
                if allowance < condition.minimum or (condition.exact is not None and allowance != condition.exact):
                    raise ValueError("Funding allowance was not established")
            else:
                progress = _check_progress_condition(plan, predicted, condition, data)
                if transaction.kind in {"atomic", "activate"} and (progress[10] != len(plan.markets) or progress[12] != _assert_allocation(plan)):
                    raise ValueError("Controlled activation did not establish every market and position")
    elif transaction.kind == "begin":
        _check_progress_condition(plan, predicted, LifecycleLaunchPostcondition("Preparing", 0), call.return_data)
    if transaction.kind == "prepare":
        indices = [event.args["marketIndex"] for event in events if event.name == "MarketPrepared"]
        if indices != list(range(transaction.first_market, transaction.first_market + transaction.market_count)):
            raise ValueError("Simulation did not prepare every exact ordered market")
    elif transaction.kind in {"atomic", "activate"}:
        position_count = _assert_allocation(plan)
        if not any(event.name == "LaunchActivated" and event.args["token"].lower() == predicted.lower()
                   and event.args["marketCount"] == len(plan.markets) and event.args["positionCount"] == position_count
                   for event in events):
            raise ValueError("Simulation did not prove exact activation identity and counts")
        if fork:
            buys = [event for event in events if event.name == "InitialBuyExecuted"]
            if len(buys) != len(plan.buys) or any(event.args["buyIndex"] != index or event.args["marketIndex"] != buy.market_index
                    or event.args["quoteAsset"].lower() != plan.markets[buy.market_index].quote_asset.lower()
                    or event.args["recipient"].lower() != buy.recipient.lower() for index, (event, buy) in enumerate(zip(buys, plan.buys))):
                raise ValueError("Controlled execution omitted the exact ordered buys")
            spent, outputs = tuple(event.args["quoteSpent"] for event in buys), tuple(event.args["tokenOut"] for event in buys)
        else:
            receipt = abi_decode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], bytes.fromhex(call.return_data[2:]))[0]
            if hex_bytes(receipt[0]) != launch_id_of(plan) or hex_bytes(receipt[1]) != hash_launch_plan(plan) or receipt[2].lower() != predicted.lower() or receipt[5] != len(plan.markets) or receipt[6] != position_count:
                raise ValueError("Simulation returned a different complete activation commitment")
            spent, outputs = receipt[7], receipt[8]
        if len(spent) != len(plan.buys) or len(outputs) != len(plan.buys) or any(quote > buy.quote_amount_in or output < buy.min_token_out for quote, output, buy in zip(spent, outputs, plan.buys)):
            raise ValueError("Simulation did not satisfy every ordered buy budget and minimum")
    elif transaction.kind == "cancel":
        if not any(event.name == "LaunchCancelled" and event.args["creator"].lower() == plan.creator.lower() for event in events):
            raise ValueError("Simulation did not cancel the exact committed launch")
    elif not fork and transaction.kind in {"approve", "approve-reset"} and call.return_data != "0x":
        if abi_decode(["bool"], bytes.fromhex(call.return_data[2:]))[0] is not True:
            raise ValueError("Funding approval returned failure")


def _prove_postconditions(plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], simulation: LaunchRpcSimulation, predicted: str) -> LaunchRpcSimulation:
    calls = []
    for transaction, call in zip(transactions, simulation.calls):
        call = replace(call, gas_limit=transaction.gas_limit)
        if call.success:
            try:
                _check_transaction_postconditions(plan, transaction, call, predicted, fork=simulation.backend == "controlled-fork")
                if simulation.backend == "controlled-fork":
                    if transaction.kind == "begin":
                        call = replace(call, return_data=call.postcondition_data[0])
                    elif transaction.kind in {"atomic", "activate"}:
                        progress = abi_decode([_tuple_type(LAUNCH_PROGRESS_COMPONENTS_V1)], bytes.fromhex(call.postcondition_data[0][2:]))[0]
                        buys = [event for event in decode_lifecycle_events(plan, call.logs) if event.name == "InitialBuyExecuted"]
                        receipt = (bytes.fromhex(launch_id_of(plan)[2:]), bytes.fromhex(hash_launch_plan(plan)[2:]),
                            predicted, progress[7], progress[8], len(plan.markets), _assert_allocation(plan),
                            tuple(event.args["quoteSpent"] for event in buys), tuple(event.args["tokenOut"] for event in buys))
                        call = replace(call, return_data=hex_bytes(abi_encode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], [receipt])))
            except Exception:
                call = replace(call, success=False, error="Simulation returned an invalid committed postcondition", failure_category="postcondition")
        calls.append(call)
        if not call.success:
            break
    return replace(simulation, calls=tuple(calls))



def _launch_simulation(
    plan: LaunchPlanV1, limits: LaunchExecutionLimits, evidence: LaunchRpcSimulation,
    transactions: Sequence[LifecycleTransaction], reasons: Sequence[str] = (), *,
    admitted: bool = False, execution_proof: Literal["proved", "failed", "unavailable", "not-run"] = "unavailable",
    protocol_fit: Literal["proved", "failed", "unknown", "profiled"] = "unknown", unknown: tuple[str, ...] = (),
    failure_category: str | None = None, capacity_constraint: str | None = None,
    failed_transaction_id: str | None = None,
    deferred: Sequence[LifecycleTransaction] = (),
    gas_profile: LifecycleGasProfileEvidence | None = None,
) -> LaunchSimulation:
    admission = LifecycleAdmission(
        admitted, evidence.confidence, limits, evidence.block.number, evidence.block.block_hash,
        _address(_execution_account(plan), "account"), plan.chain_id, execution_proof, protocol_fit,
        reason="; ".join(reasons) if reasons else None,
    )
    bound = tuple(replace(transaction, admission=admission if index == 0 else None)
        for index, transaction in enumerate(transactions))
    bound += tuple(replace(transaction, gas_limit=None, gas_price=None, admission=None) for transaction in deferred)
    return LaunchSimulation(
        evidence.confidence, admitted, evidence.block, evidence.backend, bound, tuple(reasons), evidence,
        unknown, execution_proof, protocol_fit, chain_id=plan.chain_id, account=admission.account,
        failure_category=failure_category, capacity_constraint=capacity_constraint,
        failed_transaction_id=failed_transaction_id, limits=limits,
        deferred_transaction_ids=tuple(transaction.step_id for transaction in deferred),
        gas_profile=gas_profile,
    )


def _verify_nitro_backend(client: Web3, plan: LaunchPlanV1, block: LaunchBlock, limits: LaunchExecutionLimits, gas_price: int) -> None:
    if limits.arb_os_version is None:
        raise ValueError("the native Nitro protocol context is incomplete")
    specifications = (
        (NITRO_ARB_SYS_ADDRESS, NITRO_ARB_SYS_ABI, "arbOSVersion", 256, limits.arb_os_version + 55),
    )
    gas = min(limits.compute_cap(block), 100_000)
    calls = [
        {"from": _execution_account(plan), "to": target, "data": _encode_function(abi, name, []),
         "value": "0x0", "gas": hex(gas), "gasPrice": hex(gas_price)}
        for target, abi, name, _, _ in specifications
    ]
    # This isolated capability probe proves native ArbOS execution only. Its
    # temporary balance is never used in lifecycle execution or funding
    # admission, so an exactly funded payer need not afford the probe.
    payload = {
        "blockStateCalls": [{
            "blockOverrides": {"number": hex(block.number + 1), "time": hex(block.timestamp + 1), "gasLimit": hex(block.gas_limit)},
            "stateOverrides": {_execution_account(plan): {"balance": hex((1 << 256) - 1)}},
            "calls": calls,
        }],
        "validation": True, "traceTransfers": False, "returnFullTransactions": False,
    }
    if limits.rpc_total_simulation_gas_limit is not None and gas * len(calls) > limits.rpc_total_simulation_gas_limit:
        raise ValueError("the RPC aggregate simulation gas cap cannot fit the native Nitro capability probe")
    envelope = {"jsonrpc": "2.0", "id": 1, "method": "eth_simulateV1", "params": [payload, block.tag]}
    if limits.rpc_max_request_bytes is not None and len(json.dumps(envelope, separators=(",", ":")).encode()) > limits.rpc_max_request_bytes:
        raise ValueError("the RPC request-size cap cannot fit the native Nitro capability probe")
    raw = rpc(client, "eth_simulateV1", [payload, block.tag])
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        raise ValueError("the native Nitro capability probe returned an incomplete block")
    results = raw[0].get("calls")
    if not isinstance(results, list) or len(results) != len(specifications):
        raise ValueError("the native Nitro capability probe returned incomplete calls")
    for result, (_, _, name, bits, expected) in zip(results, specifications):
        returned = hex_bytes(result.get("returnData", "0x"))
        if quantity(result.get("status")) != 1 or quantity(result.get("gasUsed")) > gas or len(returned) != 66:
            raise ValueError(f"the simulation backend cannot prove native Nitro {name}")
        actual = abi_decode([f"uint{bits}"], bytes.fromhex(returned[2:]))[0]
        if actual != expected:
            raise ValueError(f"the simulation backend Nitro {name} differs from the pinned protocol context")


def _nitro_poster_gas_bound(transaction: LifecycleTransaction, prices: Sequence[int]) -> tuple[int, int]:
    # Nitro's Brotli allocation bound, including an empty-access-list EOA envelope.
    serialized_bytes = transaction.calldata_bytes + 384
    compressed_bound = serialized_bytes + 8 * (serialized_bytes // 1024) + 64
    fee_bound = compressed_bound * prices[1]
    if fee_bound == 0:
        return 0, 0
    if prices[5] == 0:
        raise LifecyclePlanningError("NITRO_PRICES_UNAVAILABLE", "Positive poster fees cannot be converted at a zero L2 gas price")
    poster_gas = 2 * ((fee_bound + prices[5] - 1) // prices[5])
    return poster_gas, _uint(fee_bound, 256, "Nitro poster fee bound")


def _simulate_sequence(client: Web3, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *, block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits, fork: ControlledLaunchFork | None, data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None, reviewed_envelope: tuple[int, int] | None = None, diagnostic: bool = False, policy_guard: Callable[[], None] | None = None, opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None, force_simulation: bool = False, ufg: UfgLaunchConfig | None = None, capacity: _ProfiledLaunchCapacity | None = None) -> LaunchSimulation:
    """Admit local capacity or prove the fixed-ceiling sequence exactly once."""
    commitment = hash_launch_plan(plan)
    block_fields, limit_fields = tuple(vars(block).items()), tuple(vars(limits).items())
    fork_fields = None if fork is None else tuple(vars(fork).items())
    from .lifecycle_rpc import _transport_identity
    fork_transport = None if fork is None else _transport_identity(fork.client)
    def assert_current() -> None:
        if policy_guard is not None:
            policy_guard()
        if hash_launch_plan(plan) != commitment:
            raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan changed after binding its simulation")
        if tuple(vars(block).items()) != block_fields or tuple(vars(limits).items()) != limit_fields or (fork is not None and (tuple(vars(fork).items()) != fork_fields or _transport_identity(fork.client) != fork_transport)):
            raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Simulation header, limits or disposable transport changed")
        guard = getattr(client, "assert_open", None)
        if guard is not None:
            guard()
    assert_current()
    try:
        from .lifecycle_gas_profile import profile_lifecycle_transactions
        profile = None if diagnostic else profile_lifecycle_transactions(client, plan, transactions,
            block=block, predicted=predicted, limits=limits, reviewed_envelope=reviewed_envelope,
            opening_buys=opening_buys, ufg=ufg, capacity=capacity)
        if not force_simulation and profile is not None and profile.profile_only:
            return profile.simulation
        proof = _simulate_sequence_at_block(client, plan, transactions, block=block, predicted=predicted,
            limits=limits, fork=fork, data_fee_estimator=data_fee_estimator,
            reviewed_envelope=reviewed_envelope, diagnostic=diagnostic, assert_current=assert_current)
        return proof if profile is None else replace(proof, gas_profile=profile.simulation.gas_profile)
    finally:
        assert_current()


def _simulate_sequence_at_block(client: Web3, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *, block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits, fork: ControlledLaunchFork | None, data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None, reviewed_envelope: tuple[int, int] | None, diagnostic: bool, assert_current: Callable[[], None]) -> LaunchSimulation:
    unknown = limits.unknown_constraints()
    if limits.protocol == "evm" and data_fee_estimator is None:
        unknown = (*unknown, "data_fee")
    empty = LaunchRpcSimulation("provisional", None, block, ())
    if _read_lifecycle_account_code(client, _execution_account(plan), plan.chain_id, block) != "0x":
        return _launch_simulation(plan, limits, empty, (),
            ("Direct EOA simulation cannot prove smart-account execution",), unknown=unknown)
    if not transactions:
        return _launch_simulation(plan, limits, replace(empty, confidence="stateful"), (),
            admitted=True, execution_proof="proved", protocol_fit="proved", unknown=unknown)
    if reviewed_envelope is not None:
        transactions = transactions[:1]
    ceiling, envelope_cap = limits.compute_cap(block), limits.gas_cap(block)
    price = reviewed_envelope[1] if reviewed_envelope is not None else _uint(transactions[0].gas_price, 256, "gas price")
    exact = tuple(replace(transaction, gas_limit=ceiling, gas_price=price) for transaction in transactions)
    def finish(evidence: LaunchRpcSimulation, sequence: Sequence[LifecycleTransaction], reasons: Sequence[str] = (), **outcomes: Any) -> LaunchSimulation:
        return _launch_simulation(plan, limits, evidence, sequence, reasons, unknown=unknown, **outcomes)
    cap = limits.calldata_cap()
    for transaction in exact:
        if transaction.from_address.lower() != _execution_account(plan).lower() or transaction.chain_id != plan.chain_id:
            raise LifecyclePlanningError("ACCOUNT_MISMATCH", "Simulation transaction differs from its committed chain/account")
        if cap is not None and transaction.calldata_bytes > cap:
            return finish(empty, exact, ("Exact calldata exceeds the supplied byte cap",),
                protocol_fit="failed", failure_category="capacity", capacity_constraint="calldata",
                failed_transaction_id=transaction.step_id)
    if reviewed_envelope is not None and reviewed_envelope[0] != ceiling:
        raise LifecyclePlanningError("STALE_REVIEWED_GAS", "Reviewed gas differs from the native transaction ceiling; review the launch again")
    if ceiling > envelope_cap:
        return finish(empty, exact, ("Launch gas must equal the native ceiling and fit current caller limits",),
            protocol_fit="failed", failure_category="capacity", capacity_constraint="gas-envelope",
            failed_transaction_id=exact[0].step_id)
    nitro = limits.protocol == "nitro"
    prices = _read_nitro_prices(client, block) if nitro else None
    fee_floor = max(prices[5], block.base_fee_per_gas or 0) if prices is not None else block.base_fee_per_gas or 0
    if exact[0].value + ceiling * price >= 1 << 256:
        raise LifecyclePlanningError("INVALID_GAS_ENVELOPE", "Transaction value and native-ceiling fee must fit uint256")
    if price < fee_floor:
        return finish(empty, exact, ("Reviewed gas price is below the pinned required fee",),
            failed_transaction_id=exact[0].step_id)
    balance = quantity(pinned_rpc(client, "eth_getBalance", [_execution_account(plan), block.tag], block))
    if exact[0].value + ceiling * price > balance:
        return finish(empty, exact, ("Creator native balance cannot reserve transaction value and the native gas ceiling",),
            failure_category="affordability")
    poster_budgets = []
    if nitro:
        try:
            capabilities = client.capabilities if isinstance(client, _LifecycleReadClient) else _capability_facts(client)
            key = ("nitro", plan.chain_id, "eth_simulateV1")
            if key not in capabilities:
                with lifecycle_stage(client, "simulation.nitro"):
                    _verify_nitro_backend(client, plan, block, limits, price)
                    assert_current()
                capabilities.add(key)
            poster_budgets = [_nitro_poster_gas_bound(transaction, prices)[0] for transaction in exact]
        except LaunchStateChanged:
            raise
        except Exception as error:
            reason = f"Native Nitro execution proof unavailable: {failure_reason(error)}"
            return finish(replace(empty, reason=reason), exact, (reason,))
    def validate(index: int, call: LaunchSimulationCall) -> None:
        assert_current()
        _check_transaction_postconditions(plan, exact[index], call, predicted, fork=True)
    with lifecycle_stage(client, "simulation.execute"):
        proof = simulate_transactions(client, [transaction.as_transaction() for transaction in exact],
            block=block, limits=limits, fork=fork, validation=not diagnostic,
            postconditions=tuple(_condition_requests(plan, transaction) for transaction in exact),
            postcondition_validator=validate, source_chain=plan.chain_id,
            force_owned_fork=not nitro and fork is not None)
        assert_current()
        proof = _prove_postconditions(plan, exact, proof, predicted)
    if limits.rpc_total_simulation_gas_limit is not None and sum(call.gas_used or 0 for call in proof.calls) > limits.rpc_total_simulation_gas_limit:
        reason = "Validated execution exceeds the supplied RPC aggregate simulation gas policy"
        return finish(replace(empty, reason=reason, failure_category="source"), (), (reason,),
            failure_category="source", capacity_constraint="simulation-gas", deferred=exact)
    failed = next(((index, call) for index, call in enumerate(proof.calls) if not call.success), None)
    if proof.confidence != "stateful" or not proof.complete and failed is None:
        return finish(proof, exact, (proof.reason or "Complete exact validated execution unavailable",),
            failure_category=proof.failure_category or "opaque")
    if diagnostic:
        return finish(proof, exact, () if failed is None else (failed[1].error or "Diagnostic execution failed",),
            failure_category=None if failed is None else failed[1].failure_category,
            failed_transaction_id=None if failed is None else exact[failed[0]].step_id)
    affordability_failure = False
    observed = []
    for index, (transaction, call) in enumerate(zip(exact, proof.calls)):
        if index > 0 or transaction.kind in {"approve", "approve-reset"}:
            observed.append(replace(transaction, gas_limit=None, gas_price=None,
                gas_estimate=call.gas_required, gas_used=call.gas_used,
                confidence="provisional" if index > 0 else "stateful", admission=None))
            continue
        execution_fee = _uint(ceiling * price, 256, "execution fee envelope")
        data_fee = 0 if nitro else None if data_fee_estimator is None else _uint(data_fee_estimator(client, transaction.as_transaction(), block), 256, "data fee")
        assert_current()
        total = None if data_fee is None else _uint(execution_fee + data_fee, 256, "total fee")
        required = transaction.value + execution_fee + (data_fee or 0)
        if required >= 1 << 256:
            raise LifecyclePlanningError("INVALID_GAS_ENVELOPE", "Transaction value and fee envelope exceed uint256")
        affordability_failure = required > balance
        poster = poster_budgets[index] if nitro else None
        observed.append(replace(transaction, gas_estimate=call.gas_required, gas_used=call.gas_used,
            execution_fee=execution_fee, maximum_execution_fee=execution_fee,
            data_fee=data_fee, total_fee=total, data_fee_included_in_gas=nitro,
            poster_gas=poster, poster_fee=None if poster is None else poster * price, confidence="stateful"))
    if failed is not None:
        index, call = failed
        activation = exact[index].kind == "activate" and call.failure_category == "capacity"
        return finish(proof, observed, (call.error or "Exact execution failed",),
            execution_proof="failed", failure_category=call.failure_category or "opaque",
            capacity_constraint="activation" if activation else "compute" if call.failure_category == "capacity" else None,
            failed_transaction_id=exact[index].step_id)
    if affordability_failure:
        return finish(proof, observed, ("Creator native balance cannot reserve transaction value and the native-ceiling/data fee envelope",),
            execution_proof="proved", protocol_fit="proved", failure_category="affordability")
    return finish(proof, observed, admitted=True, execution_proof="proved", protocol_fit="proved")


def _prepare_gas_estimate(plan: LaunchPlanV1, index: int) -> int:
    from .lifecycle_presets import HMM_V2_PREPARE_GAS_ESTIMATE, OTHER_PREPARE_GAS_ESTIMATE
    return HMM_V2_PREPARE_GAS_ESTIMATE if is_hmm_profile(plan.markets[index].profile_id) else OTHER_PREPARE_GAS_ESTIMATE


def _forwarding_reserve(plan: LaunchPlanV1, kind: str) -> int:
    from .lifecycle_presets import HMM_V2_FORWARDING_RESERVE, HMM_V2_ADDITIONAL_MARKET_RESERVE
    if kind not in {"activate", "atomic"}:
        return 0
    count = sum(is_hmm_profile(market.profile_id) for market in plan.markets)
    return HMM_V2_FORWARDING_RESERVE + HMM_V2_ADDITIONAL_MARKET_RESERVE * (count - 1) if count else 0


def _groups(plan: LaunchPlanV1, first: int, compute_cap: int) -> list[tuple[int, int]]:
    groups = []
    start, cost = first, 0
    for index in range(first, len(plan.markets)):
        estimate = _prepare_gas_estimate(plan, index)
        if index > start and cost + estimate > compute_cap:
            groups.append((start, index - start))
            start, cost = index, 0
        cost += estimate
    if start < len(plan.markets):
        groups.append((start, len(plan.markets) - start))
    return groups


def _validate_planning_intent(plan: LaunchPlanV1, account: str, mode: str, cancel_event: asyncio.Event | None, ufg: UfgLaunchConfig | None = None) -> str:
    _check_mining_cancelled(cancel_event)
    _mode(mode)
    if ufg is not None:
        _validate_ufg_identity(plan)
        validate_ufg_launch_config(ufg)
    elif plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower():
        raise LifecyclePlanningError("INVALID_UFG_PLAN", "Proxy launches require an explicit ufg config")
    if _address(account, "account").lower() != _execution_account(plan).lower():
        raise LifecyclePlanningError("ACCOUNT_MISMATCH", "Execution account must be the committed creator/payer/refund account")
    if isinstance(plan.chain_id, bool) or not 0 < plan.chain_id <= (1 << 53) - 1:
        raise LifecyclePlanningError("CHAIN_MISMATCH", "Chain ID must be a positive exact portable integer")
    _validate_launch_plan(plan)
    return hash_launch_plan(plan)


@dataclass(frozen=True)
class _ReviewedTransaction:
    step_id: str
    kind: str
    chain_id: int
    from_address: str
    to: str
    data: str
    value: int
    gas_limit: int | None
    gas_price: int | None


def _read_reviewed_transaction(transaction: LifecycleTransaction | Mapping[str, Any] | None) -> _ReviewedTransaction | None:
    if transaction is None:
        return None
    def value(snake: str, wire: str):
        return transaction.get(snake, transaction.get(wire)) if isinstance(transaction, Mapping) else getattr(transaction, snake, None)
    try:
        reviewed = _ReviewedTransaction(value("step_id", "id"), value("kind", "kind"), value("chain_id", "chainId"),
            value("from_address", "from"), value("to", "to"), value("data", "data"), value("value", "value"),
            value("gas_limit", "gas"), value("gas_price", "gasPrice"))
        if not isinstance(reviewed.step_id, str) or reviewed.kind not in {"approve-reset", "approve", "atomic", "begin", "prepare", "activate", "cancel"} or isinstance(reviewed.chain_id, bool) or not isinstance(reviewed.chain_id, int) or not 0 < reviewed.chain_id <= (1 << 53) - 1:
            raise ValueError("Malformed reviewed identity")
        _address(reviewed.from_address, "reviewed from")
        _address(reviewed.to, "reviewed target")
        if not isinstance(reviewed.data, str):
            raise ValueError("Reviewed calldata must be hex")
        hex_bytes(reviewed.data)
        _uint(reviewed.value, 256, "reviewed value")
        if reviewed.kind in {"approve", "approve-reset"}:
            return replace(reviewed, gas_limit=None, gas_price=None)
        if _uint(reviewed.gas_limit, 64, "reviewed gas") == 0:
            raise ValueError("Reviewed gas must be positive")
        _uint(reviewed.gas_price, 256, "reviewed gas price")
        return reviewed
    except (ValueError, TypeError, AttributeError):
        raise LifecyclePlanningError("INVALID_REVIEWED_TRANSACTION", "Reviewed transaction requires exact chain/account/target/calldata/value; launch commands also require uint64 gas/uint256 price") from None


def _matches_reviewed(transaction: LifecycleTransaction | _ReviewedTransaction | None, reviewed: _ReviewedTransaction) -> bool:
    return transaction is not None and transaction.step_id == reviewed.step_id and transaction.kind == reviewed.kind and transaction.chain_id == reviewed.chain_id and transaction.from_address.lower() == reviewed.from_address.lower() and transaction.to.lower() == reviewed.to.lower() and transaction.data.lower() == reviewed.data.lower() and transaction.value == reviewed.value


def _assert_reviewed_unchanged(supplied: LifecycleTransaction | Mapping[str, Any] | None, snapshot: _ReviewedTransaction | None, next_transaction: LifecycleTransaction) -> None:
    current = _read_reviewed_transaction(supplied)
    if snapshot is None:
        if current is not None:
            raise LifecyclePlanningError("REVIEWED_TRANSACTION_CHANGED", "Reviewed transaction changed during fresh proof")
    elif current is None or not _matches_reviewed(current, snapshot) or not _matches_reviewed(next_transaction, snapshot) or (snapshot.kind not in {"approve", "approve-reset"} and (current.gas_limit != snapshot.gas_limit or current.gas_price != snapshot.gas_price or next_transaction.gas_limit != snapshot.gas_limit or next_transaction.gas_price != snapshot.gas_price)):
        raise LifecyclePlanningError("REVIEWED_TRANSACTION_CHANGED", "Reviewed wire transaction changed during fresh admission")


@read_invocation("plan")
def plan_launch(
    client: Web3, plan: LaunchPlanV1, *, account: str, mode: LaunchExecutionMode,
    limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    confirmations: int = 1, receipts: Sequence[LifecycleReceiptReference] = (),
    fork: ControlledLaunchFork | None = None, cancel_event: asyncio.Event | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    allow_topology_gaps: bool = False, allow_missing_permanent_tail: bool = False,
    ufg: UfgLaunchConfig | None = None,
) -> PlannedLaunch:
    """Admit the selected execution mode from fresh canonical state."""
    return _plan_launch(client, plan, account=account, mode=mode, limits=limits,
        confirmations=confirmations, receipts=receipts,
        fork=fork, cancel_event=cancel_event, data_fee_estimator=data_fee_estimator,
        allow_topology_gaps=allow_topology_gaps, allow_missing_permanent_tail=allow_missing_permanent_tail,
        ufg=ufg)


def _plan_launch(
    client: Web3, plan: LaunchPlanV1, *, account: str, mode: LaunchExecutionMode,
    limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    confirmations: int = 1, receipts: Sequence[LifecycleReceiptReference] = (),
    fork: ControlledLaunchFork | None = None, cancel_event: asyncio.Event | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    block: LaunchBlock | None = None, owned_progress: LaunchProgress | None = None,
    diagnostic: bool = False,
    allow_topology_gaps: bool = False, allow_missing_permanent_tail: bool = False,
    ufg: UfgLaunchConfig | None = None,
    opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None,
    force_simulation: bool = False,
) -> PlannedLaunch:
    commitment = _validate_planning_intent(plan, account, mode, cancel_event, ufg)
    requested_mode = mode
    ufg_hash = None if ufg is None else hash_ufg_launch_config(ufg)
    if not isinstance(allow_topology_gaps, bool) or not isinstance(allow_missing_permanent_tail, bool):
        raise ValueError("Topology gap options must be booleans")
    policy_fields = tuple(vars(limits).items()) if isinstance(limits, LaunchExecutionLimits) else None
    policy_guards: list[Callable[[], None]] = []
    with lifecycle_stage(client, "plan.context"):
        # Preparation owns a supplied pin; every other invocation pins once here.
        if block is None:
            _check_client_identity(client, plan, account)
    with lifecycle_stage(client, "plan.domain"):
        block = _prime_lifecycle_reads(client, plan, block)
        resolved_limits = _resolve_limits(client, limits, block, _address(account, "account"), plan.chain_id, plan.orchestrator, policy_guards=policy_guards)
        token_factory, token_factory_code_hash = _read_token_factory_binding(client, plan.orchestrator, block, plan.chain_id)
        predicted = read_lifecycle_token_at_block(client, plan, block)
        if ufg is not None:
            validate_ufg_launch_plan(plan, ufg, predicted)
        progress = owned_progress or _read_launch_progress_at_block(client, plan, confirmations=confirmations,
            receipts=receipts, block=block, mode=mode, cancel_event=cancel_event, ufg_hash=ufg_hash)
    if owned_progress is not None and (progress.plan_hash != commitment or progress.launch_id != launch_id_of(plan) or progress.head_block != block or progress.confirmation_depth != confirmations or progress.token.lower() != predicted.lower()):
        raise LifecyclePlanningError("INVALID_READ_CONTEXT", "Owned canonical progress differs from the active planning snapshot")
    def assert_current() -> None:
        for guard in policy_guards:
            guard()
        _check_mining_cancelled(cancel_event)
        if hash_launch_plan(plan) != commitment:
            raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan changed during planning")
        if (None if ufg is None else hash_ufg_launch_config(ufg)) != ufg_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "UFG treasury config changed during planning")
        if policy_fields is not None and tuple(vars(limits).items()) != policy_fields:
            raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Supplied policy changed during planning")
    def finish(simulation: LaunchSimulation, transactions: Sequence[LifecycleTransaction] = (), approvals: Sequence[LifecycleApproval] = (), profiles: Sequence[Any] = (), hooks: Sequence[PoolBoundLifecycleDeployment] = (), prerequisites: Sequence[LifecycleFundingPrerequisite] = ()) -> PlannedLaunch:
        assert_current()
        if isinstance(client, _LifecycleReadClient):
            client.completion_checks.append(assert_current)
        check_lifecycle_source(client, plan.chain_id, progress.block, block)
        admitted = {transaction.step_id: transaction for transaction in simulation.transactions if transaction.admission is not None}
        admission = LifecycleAdmission(simulation.admitted, simulation.confidence, resolved_limits,
            block.number, block.block_hash, account, plan.chain_id, simulation.execution_proof,
            simulation.protocol_fit, "; ".join(simulation.reasons) or None)
        attached = tuple(replace(admitted[transaction.step_id], admission=admission) if transaction.step_id in admitted
            else replace(transaction, gas_limit=None, gas_price=None,
                admission=None) for transaction in transactions)
        return PlannedLaunch(plan=plan, plan_hash=commitment, launch_id=launch_id_of(plan), predicted_token=predicted,
            chain_id=plan.chain_id, account=_address(account, "account"), mode=mode, transactions=attached,
            approvals=tuple(approvals), progress=progress, simulation=simulation, limits=resolved_limits,
            irreversible_costs=("Preparation gas and setup deployments are irreversible",
                "Cancellation refunds only unspent external funding, not launch-token inventory or gas"),
            profiles=tuple(profiles), hook_deployments=tuple(hooks), prerequisites=tuple(prerequisites),
            total_calldata_bytes=sum(transaction.calldata_bytes for transaction in transactions),
            limits_source=limits, confirmations=confirmations, receipt_references=tuple(receipts),
            token_factory=token_factory, token_factory_code_hash=token_factory_code_hash,
            allow_topology_gaps=allow_topology_gaps, allow_missing_permanent_tail=allow_missing_permanent_tail,
            ufg=ufg, ufg_config_hash=ufg_hash)
    empty = LaunchRpcSimulation("stateful", None, block, ())
    if not progress.confirmation_safe or any(receipt.status in {"pending", "unconfirmed"} for receipt in progress.receipts):
        hooks = []
        for index, market in enumerate(plan.markets):
            if is_pool_bound_v4_config_version(market.config_version):
                deployment = _read_pool_bound_deployment(client, plan, index, block)[0]
                hooks.append(PoolBoundLifecycleDeployment(deployment.deployer, deployment.init_code_hash, deployment.salt, deployment.predicted_hook, index))
        waiting = _launch_simulation(plan, resolved_limits, empty, (), ("Wait for canonical launch progress and receipt confirmations",), unknown=resolved_limits.unknown_constraints())
        return finish(waiting, hooks=hooks)
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        terminal = _launch_simulation(plan, resolved_limits, empty, (), admitted=True, execution_proof="proved", protocol_fit="proved")
        return finish(terminal)
    if progress.phase == LifecyclePhase.NONE:
        _assert_plan_topology(plan, predicted, allow_topology_gaps=allow_topology_gaps,
            allow_missing_permanent_tail=allow_missing_permanent_tail)
    with lifecycle_stage(client, "plan.inputs"):
        prerequisites, approvals = _read_funding(client, plan, block) if progress.phase == LifecyclePhase.NONE else ((), ())
        profiles, hooks, reasons = _read_plan_metadata(client, plan, predicted, block)
    assert_current()
    gas_price = _uint(_read_lifecycle_gas_price(client, block, plan.chain_id), 256, "gas price")
    cap = resolved_limits.compute_cap(block)
    from .lifecycle_gas_profile import derive_lifecycle_launch_capacity
    capacity = (derive_lifecycle_launch_capacity(plan, predicted_token=predicted, ufg=ufg,
        headroom_bps=resolved_limits.headroom_bps, opening_buys=opening_buys)
        if resolved_limits.protocol == "nitro" and resolved_limits.arb_os_version == 61 else None)
    if not force_simulation and progress.phase == LifecyclePhase.NONE and mode == "atomic" and capacity is not None:
        if capacity.bounds.atomic_compute_gas_upper_bound + _nitro_poster_gas_bound(capacity.atomic, _read_nitro_prices(client, block))[0] > cap:
            mode = "staged"
    groups = _groups(plan, progress.prepared_markets, cap)
    while True:
        transactions = _make_transactions(plan, mode, progress, approvals, groups,
            gas_cap=cap, gas_price=gas_price, ufg=ufg)
        if reasons:
            refusal = _launch_simulation(plan, resolved_limits, empty, (), reasons, failure_category="semantic")
            return finish(refusal, transactions, approvals, profiles, hooks, prerequisites)
        simulation = _simulate_sequence(client, plan, transactions, block=block, predicted=predicted, limits=resolved_limits,
            fork=fork, data_fee_estimator=data_fee_estimator, diagnostic=diagnostic,
            policy_guard=assert_current, ufg=ufg,
            opening_buys=opening_buys, capacity=capacity, force_simulation=force_simulation)
        assert_current()
        if (not diagnostic and not force_simulation and requested_mode == mode == "atomic"
                and progress.phase == LifecyclePhase.NONE and simulation.execution_proof == "failed"
                and simulation.failure_category == "capacity" and simulation.capacity_constraint == "compute"):
            mode = "staged"
            groups = _groups(plan, progress.prepared_markets, cap)
            continue
        failed = next((transaction for transaction in simulation.transactions if transaction.step_id == simulation.failed_transaction_id), None)
        from .lifecycle_presets import NEAR_GAS_CEILING_BPS
        failed_call = next((call for transaction, call in zip(simulation.transactions, simulation.evidence.calls)
            if transaction.step_id == simulation.failed_transaction_id), None)
        if mode != "staged" or failed is None or failed.kind != "prepare" or failed.market_count <= 1 or failed_call is None or failed_call.gas_limit is None or failed_call.gas_limit < cap or (failed_call.gas_used or 0) * 10_000 < cap * NEAR_GAS_CEILING_BPS:
            break
        index = groups.index((failed.first_market, failed.market_count))
        half = (failed.market_count + 1) // 2
        groups[index:index + 1] = [(failed.first_market, half),
            (failed.first_market + half, failed.market_count - half)]
    if not simulation.admitted and simulation.failed_transaction_id == "activate" and simulation.failure_category == "capacity":
        simulation = replace(simulation, capacity_constraint="activation",
            reasons=("Too many positions or pools for one activation. Reduce them to fit the native transaction ceiling.",))
    return finish(simulation, transactions, approvals, profiles, hooks, prerequisites)

def _assert_planned_identity(launch: PlannedLaunch) -> None:
    if hash_launch_plan(launch.plan) != hex_bytes(launch.plan_hash) or launch_id_of(launch.plan) != hex_bytes(launch.launch_id) or launch.chain_id != launch.plan.chain_id or launch.account.lower() != _execution_account(launch.plan).lower():
        raise LifecyclePlanningError("PLAN_MUTATED", "Reviewed economic plan or identity changed")
    if (None if launch.ufg is None else hash_ufg_launch_config(launch.ufg)) != launch.ufg_config_hash:
        raise LifecyclePlanningError("PLAN_MUTATED", "Reviewed UFG treasury config changed")
    if launch.ufg is not None:
        validate_ufg_launch_plan(launch.plan, launch.ufg, launch.predicted_token)


def _assert_hook_deployments(reviewed: PlannedLaunch, current: PlannedLaunch) -> None:
    _assert_planned_identity(reviewed)
    if current.plan_hash != reviewed.plan_hash or current.launch_id != reviewed.launch_id:
        raise LifecyclePlanningError("PLAN_MUTATED", "Stored economic commitment changed before execution")
    if current.ufg_config_hash != reviewed.ufg_config_hash:
        raise LifecyclePlanningError("PLAN_MUTATED", "Reviewed UFG treasury config changed before execution")
    if reviewed.predicted_token.lower() != current.predicted_token.lower() or reviewed.token_factory != current.token_factory or reviewed.token_factory_code_hash != current.token_factory_code_hash:
        raise LifecyclePlanningError("TOKEN_FACTORY_BINDING", "Token prediction or immutable factory binding changed before execution")
    if not current.simulation.admitted or current.progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        return
    if reviewed.hook_deployments != current.hook_deployments:
        raise LifecyclePlanningError("HOOK_DEPLOYMENT_CHANGED", "Final bound-hook deployer, initcode, salt or prediction changed")



@read_invocation("plan")
def simulate_launch_plan(
    client: Web3, launch: PlannedLaunch | LaunchPlanV1, *, account: str | None = None,
    mode: LaunchExecutionMode | None = None, limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    confirmations: int | None = None,
    receipts: Sequence[LifecycleReceiptReference] | None = None, fork: ControlledLaunchFork | None = None,
    cancel_event: asyncio.Event | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    ufg: UfgLaunchConfig | None = None,
) -> LaunchSimulation:
    """Freshly prove canonical unfinished work with inherited receipt evidence."""
    if isinstance(launch, PlannedLaunch):
        _assert_planned_identity(launch)
        plan, selected = launch.plan, launch.mode if mode is None else mode
        account = launch.account if account is None else account
        limits = launch.limits_source if limits is None else limits
        confirmations = launch.confirmations if confirmations is None else confirmations
        receipts = launch.receipt_references if receipts is None else receipts
        if ufg is not None and hash_ufg_launch_config(ufg) != launch.ufg_config_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "Cannot replace the reviewed UFG treasury config")
        ufg = launch.ufg
    else:
        plan, selected = launch, mode
        account = _execution_account(plan) if account is None else account
    if selected is None:
        raise LifecyclePlanningError("EXPLICIT_MODE_REQUIRED", "Simulation requires an explicitly selected mode")
    current = _plan_launch(client, plan, account=account, mode=selected, limits=limits,
        confirmations=1 if confirmations is None else confirmations,
        receipts=() if receipts is None else receipts, fork=fork, cancel_event=cancel_event,
        data_fee_estimator=data_fee_estimator,
        allow_topology_gaps=launch.allow_topology_gaps if isinstance(launch, PlannedLaunch) else False,
        allow_missing_permanent_tail=launch.allow_missing_permanent_tail if isinstance(launch, PlannedLaunch) else False,
        ufg=ufg, force_simulation=True)
    if isinstance(launch, PlannedLaunch):
        _assert_hook_deployments(launch, current)
    return current.simulation


@read_invocation("plan")
def build_next_transaction(
    client: Web3, launch: PlannedLaunch | LaunchPlanV1, *, account: str | None = None,
    mode: LaunchExecutionMode | None = None, action: Literal["continue", "cancel"] = "continue",
    limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    confirmations: int | None = None,
    receipts: Sequence[LifecycleReceiptReference] | None = None, fork: ControlledLaunchFork | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    cancel_event: asyncio.Event | None = None,
    reviewed_transaction: LifecycleTransaction | Mapping[str, Any] | None = None,
    ufg: UfgLaunchConfig | None = None,
) -> LifecycleTransaction | None:
    """Build canonical unfinished work and prove only its immediate transaction.

    Reviewed launch gas must match the native ceiling; its price remains exact.
    """
    reviewed = _read_reviewed_transaction(reviewed_transaction)  # Validate before any RPC.
    if action not in {"continue", "cancel"}:
        raise ValueError("action must be 'continue' or 'cancel'")
    full_review_admitted = False
    if isinstance(launch, PlannedLaunch):
        full_review_admitted = launch.simulation.admitted is True
        _assert_planned_identity(launch)
        plan = launch.plan
        if mode is not None and _mode(mode) != launch.mode:
            raise LifecyclePlanningError("PLAN_MUTATED", "Cannot replace the reviewed execution mode; request a new full review")
        selected = launch.mode
        account = launch.account if account is None else account
        limits = launch.limits_source if limits is None else limits
        confirmations = launch.confirmations if confirmations is None else confirmations
        receipts = launch.receipt_references if receipts is None else receipts
        if ufg is not None and hash_ufg_launch_config(ufg) != launch.ufg_config_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "Cannot replace the reviewed UFG treasury config")
        ufg = launch.ufg
    else:
        plan, selected = launch, mode
        account = _execution_account(plan) if account is None else account
    if selected is None:
        raise LifecyclePlanningError("EXPLICIT_MODE_REQUIRED", "Building requires an explicitly selected mode")
    selected = _mode(selected)
    confirmations = 1 if confirmations is None else confirmations
    receipts = () if receipts is None else receipts
    _check_mining_cancelled(cancel_event)
    _check_client_identity(client, plan, account)
    commitment = _validate_planning_intent(plan, account, selected, cancel_event, ufg)
    ufg_hash = None if ufg is None else hash_ufg_launch_config(ufg)
    policy_guards: list[Callable[[], None]] = []
    def assert_policy() -> None:
        for guard in policy_guards:
            guard()
        if (None if ufg is None else hash_ufg_launch_config(ufg)) != ufg_hash:
            raise LifecyclePlanningError("PLAN_MUTATED", "UFG treasury config changed during build-next proof")
    block = _prime_lifecycle_reads(client, plan)
    progress = _read_launch_progress_at_block(client, plan, confirmations=confirmations, receipts=receipts,
        block=block, mode=selected, cancel_event=cancel_event, ufg_hash=ufg_hash)
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        check_lifecycle_source(client, plan.chain_id, progress.block, block)
        return None
    if not progress.confirmation_safe:
        raise LifecyclePlanningError("UNCONFIRMED_STATE", "Wait for confirmed canonical launch progress")
    if any(receipt.status in {"pending", "unconfirmed"} for receipt in progress.receipts):
        raise LifecyclePlanningError("RECEIPT_PENDING", "Wait for canonical receipt confirmations or resolve replacement")
    if action != "cancel" and progress.phase == LifecyclePhase.NONE and not full_review_admitted:
        raise LifecyclePlanningError("PLAN_NOT_ADMITTED",
            "Complete an admitted full launch review before starting or approving funding; next-only construction does not replan the remaining sequence")
    resolved = _resolve_limits(client, limits, block, _address(account, "account"),
        plan.chain_id, plan.orchestrator, policy_guards=policy_guards)
    price = reviewed.gas_price if reviewed is not None and reviewed.gas_price is not None else _uint(_read_lifecycle_gas_price(client, block, plan.chain_id), 256, "gas price")
    cap = resolved.compute_cap(block)
    approvals = ()
    groups = ()
    if action == "cancel":
        if progress.phase == LifecyclePhase.NONE:
            raise LifecyclePlanningError("NOT_STARTED", "An unstarted plan has no launch escrow to cancel")
    else:
        if progress.phase == LifecyclePhase.NONE:
            _assert_plan_topology(plan, progress.token,
                allow_topology_gaps=launch.allow_topology_gaps if isinstance(launch, PlannedLaunch) else False,
                allow_missing_permanent_tail=launch.allow_missing_permanent_tail if isinstance(launch, PlannedLaunch) else False)
            _, approvals = _read_funding(client, plan, block)
        groups = _groups(plan, progress.prepared_markets, cap)
        if isinstance(launch, PlannedLaunch) and selected == "staged":
            stored = [(max(transaction.first_market, progress.prepared_markets),
                transaction.first_market + transaction.market_count - max(transaction.first_market, progress.prepared_markets))
                for transaction in launch.transactions if transaction.kind == "prepare"
                and transaction.first_market + transaction.market_count > progress.prepared_markets]
            if stored:
                groups = stored
        if reviewed is not None and reviewed.kind == "prepare":
            try:
                entry = _entry(LAUNCH_LIFECYCLE_V1_ABI, "prepareMarkets")
                _, first, count = abi_decode([canonical_abi_type(item) for item in entry["inputs"]], bytes.fromhex(reviewed.data[10:]))
                if first != progress.prepared_markets or count <= 0 or first + count > len(plan.markets):
                    raise ValueError("Invalid reviewed preparation span")
                groups = [(first, count), *_groups(plan, first + count, cap)]
            except Exception:
                raise LifecyclePlanningError("REVIEWED_TRANSACTION_MISMATCH", "Reviewed preparation differs from canonical unfinished markets") from None
    transactions = _make_transactions(plan, selected, progress, approvals, groups,
        gas_cap=cap, gas_price=price, cancel=action == "cancel", ufg=ufg)[:1]
    if reviewed is not None and not _matches_reviewed(transactions[0] if transactions else None, reviewed):
        raise LifecyclePlanningError("REVIEWED_TRANSACTION_MISMATCH", "Reviewed wire transaction differs from canonical unfinished work")
    proof = _simulate_sequence(client, plan, transactions, block=block, predicted=progress.token, limits=resolved,
        fork=fork, data_fee_estimator=data_fee_estimator,
        reviewed_envelope=None if reviewed is None or reviewed.kind in {"approve", "approve-reset"} else (reviewed.gas_limit, reviewed.gas_price),
        policy_guard=assert_policy, ufg=ufg)
    if not proof.admitted:
        raise LifecyclePlanningError("CANCEL_NOT_ADMITTED" if action == "cancel" else "PLAN_NOT_ADMITTED",
            "; ".join(proof.reasons) or "Immediate launch execution cannot be proved", proof)
    next_transaction = proof.transactions[0] if proof.transactions else None
    if next_transaction is None:
        return None
    admitted_outcome = ((proof.execution_proof, proof.protocol_fit) == ("proved", "proved")
        or proof.confidence == "profiled" and (proof.execution_proof, proof.protocol_fit) == ("not-run", "profiled"))
    approval = next_transaction.kind in {"approve", "approve-reset"}
    if (not approval and (next_transaction.gas_limit is None or next_transaction.gas_price is None or next_transaction.maximum_execution_fee is None)) or not admitted_outcome or next_transaction.admission is None:
        raise LifecyclePlanningError("MISSING_GAS_PROOF", "Next transaction lacks current envelope and fee admission", proof)
    _assert_reviewed_unchanged(reviewed_transaction, reviewed, next_transaction)
    _check_mining_cancelled(cancel_event)
    def assert_current() -> None:
        if hash_launch_plan(plan) != commitment:
            raise LifecyclePlanningError("PLAN_MUTATED", "Economic plan changed during execution proof")
        _check_mining_cancelled(cancel_event)
        assert_policy()
        _assert_reviewed_unchanged(reviewed_transaction, reviewed, next_transaction)
    assert_current()
    check_lifecycle_source(client, plan.chain_id, progress.block, block)
    if isinstance(client, _LifecycleReadClient):
        client.completion_checks.append(assert_current)
    return replace(next_transaction, dependencies=())


def validate_lifecycle_market_identity(identity: LifecycleMarketIdentity | Sequence[Any] | Mapping[str, Any], plan: LaunchPlanV1, token: str, market_index: int) -> None:
    if isinstance(market_index, bool) or not isinstance(market_index, int) or not 0 <= market_index < len(plan.markets):
        raise LifecyclePlanningError("MARKET_IDENTITY", "Market index is outside the economic plan")
    values = tuple(identity) if isinstance(identity, (tuple, list)) else _struct_tuple(MARKET_IDENTITY_COMPONENTS_V1, identity)
    venue, canonical_id, manager, factory, pool, pool_id, profile_id, currency0, currency1, fee, spacing, hook, opening_price = values
    market = plan.markets[market_index]
    expected = keccak(abi_encode(["uint256", "uint8", "address", "address", "address", "bytes32", "bytes32"],
        [plan.chain_id, venue, manager, factory, pool, pool_id, profile_id]))
    currencies = sorted((token.lower(), market.quote_asset.lower()), key=lambda address: int(address, 16))
    if hex_bytes(canonical_id) != hex_bytes(expected) or hex_bytes(profile_id) != hex_bytes(market.profile_id) or [currency0.lower(), currency1.lower()] != currencies or opening_price == 0 or spacing <= 0:
        raise LifecyclePlanningError("MARKET_IDENTITY", "Venue identity differs from canonical chain, plan, currencies or opening price")
    if venue == LifecycleVenue.UNISWAP_V4:
        expected_pool = keccak(abi_encode(["address", "address", "uint24", "int24", "address"], [currency0, currency1, fee, spacing, hook]))
        if int(manager, 16) == 0 or int(factory, 16) != 0 or int(pool, 16) != 0 or hex_bytes(pool_id) != hex_bytes(expected_pool) or not valid_pool_bound_hook_address(hook):
            raise LifecyclePlanningError("MARKET_IDENTITY", "V4 full permissioned PoolKey differs from the canonical pool ID")
    elif venue != LifecycleVenue.ABYSS or int(pool, 16) == 0 or int(factory, 16) == 0:
        raise LifecyclePlanningError("MARKET_IDENTITY", "Abyss canonical factory and pool identity is missing")


def read_launch_markets(client: Web3, plan: LaunchPlanV1, *, offset: int = 0, limit: int = 16, position_offset: int = 0, position_limit: int = 32) -> tuple[Mapping[str, Any], ...]:
    """Read directory identities plus actual pool/custody state with pagination."""
    from .lifecycle_abis import MARKET_IDENTITY_COMPONENTS_V1, POSITION_IDENTITY_COMPONENTS_V1
    _check_client_identity(client, plan)
    for value, name in ((offset, "offset"), (limit, "limit"), (position_offset, "positionOffset"), (position_limit, "positionLimit")):
        _uint(value, 256, name)
    if limit > 16 or position_limit > 32:
        raise ValueError("directory pages are bounded by 16 markets and 32 positions")
    progress = read_launch_progress(client, plan)
    block = progress.block
    directory = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "directory", [], block)
    result = []
    for index in range(offset, min(offset + limit, progress.prepared_markets)):
        adapter, prepared = _call(client, directory, LAUNCH_DIRECTORY_V1_ABI, "market", [bytes.fromhex(progress.launch_id[2:]), index], block)
        identity = prepared[0]
        validate_lifecycle_market_identity(identity, plan, progress.token, index)
        if identity[0] == 0 and is_pool_bound_v4_config_version(plan.markets[index].config_version):
            deployer, init_code_hash, salt, predicted_hook = _call(client, adapter, POOL_MARKET_ADAPTER_V1_ABI, "hookDeploymentMetadata", [progress.token, _market_tuple(plan, index)], block)
            config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[index].config)
            if salt != bytes.fromhex(hex_bytes(config.hook_salt)[2:]) or predicted_hook.lower() != identity[11].lower() or predict_pool_bound_hook_address(deployer=deployer, init_code_hash=init_code_hash, salt=salt).lower() != identity[11].lower():
                raise ValueError("prepared pool-bound identity differs from exact committed CREATE2 metadata")
        live = _call(client, adapter, LAUNCH_MARKET_ADAPTER_V1_ABI, "readMarket", [bytes.fromhex(progress.launch_id[2:]), index], block)
        positions = _call(client, directory, LAUNCH_DIRECTORY_V1_ABI, "positions", [bytes.fromhex(progress.launch_id[2:]), index, position_offset, position_limit], block)
        position_reads = []
        for position in positions:
            expected = keccak(abi_encode(["uint256", "bytes32", "address", "address", "uint256", "int24", "int24", "bytes32"], [plan.chain_id, position[1], *position[2:8]]))
            if position[0] != expected or position[1] != identity[1] or position[3].lower() != prepared[2].lower():
                raise ValueError("directory position does not match canonical market/custody identity")
            liquidity, owner = _call(client, adapter, LAUNCH_MARKET_ADAPTER_V1_ABI, "readPosition", [position], block)
            position_reads.append({**_json_tuple(POSITION_IDENTITY_COMPONENTS_V1, position), "liveLiquidity": liquidity, "liveOwner": to_checksum_address(owner)})
        result.append({"marketIndex": index, "adapter": to_checksum_address(adapter), "identity": _json_tuple(MARKET_IDENTITY_COMPONENTS_V1, identity), "feeSource": to_checksum_address(prepared[1]), "custody": to_checksum_address(prepared[2]), "positionCount": prepared[6], "positions": tuple(position_reads), "live": {"sqrtPriceX96": live[0], "tick": live[1], "liquidity": live[2], "publicTrading": live[3], "oracleReadyAt": live[4]}, "blockNumber": block.number, "blockHash": block.block_hash})
    assert_canonical(client, block)
    return tuple(result)


def preview_lifecycle_fees(client: Web3, hub: str, *, executor: str, block: LaunchBlock | None = None) -> tuple[Mapping[str, Any], ...]:
    """Run the exact no-argument claimAndSplit with the intended executor."""
    pinned = block or read_block(client)
    if _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "economicVersion", [], pinned) != 3:
        raise ValueError("fee previews require current V3 source-aware economics")
    payments = _call(client, _address(hub, "hub", nonzero=True), LAUNCH_FEE_HUB_V3_ABI, "claimAndSplit", [], pinned, sender=_address(executor, "executor", nonzero=True))
    result = tuple({"asset": to_checksum_address(asset), "amount": amount} for asset, amount in payments)
    assert_canonical(client, pinned)
    return result


def _author_root(client: Web3, registry: str, block: LaunchBlock) -> tuple[str, str]:
    registry = _address(registry, "registry", nonzero=True)
    core = _address(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], block), "registry core", nonzero=True)
    factory = _address(_call(client, core, LAUNCH_LIFECYCLE_V1_ABI, "feeFactory", [], block), "canonical fee factory", nonzero=True)
    if _call(client, core, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block).lower() != registry.lower() or _call(client, factory, LAUNCH_FEE_HUB_FACTORY_V3_ABI, "implementationRegistry", [], block).lower() != registry.lower() or _call(client, factory, LAUNCH_FEE_HUB_FACTORY_V3_ABI, "deploymentAuthority", [], block).lower() != core.lower():
        raise ValueError("author discovery requires the trusted registry/core canonical fee factory")
    return core, factory


def _canonical_author_hub(client: Web3, registry: str, factory: str, hub: str, block: LaunchBlock) -> str:
    hub = _address(hub, "hub", nonzero=True)
    if _call(client, factory, LAUNCH_FEE_HUB_FACTORY_V3_ABI, "isHub", [hub], block) is not True:
        raise ValueError("hub is not a member of the trusted registry core's canonical fee factory")
    if _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "economicVersion", [], block) != 3 or _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "implementationRegistry", [], block).lower() != registry.lower():
        raise ValueError("canonical author hub requires current V3 registry-bound economics")
    return hub


@read_invocation("author.discovery")
def read_lifecycle_author(client: Web3, *, registry: str, author_id: str, block: LaunchBlock | None = None) -> LifecycleAuthor:
    """Read the stable author identity's live controller and canonical hub domain."""
    registry = _address(registry, "registry", nonzero=True)
    author_id = _address(author_id, "authorId", nonzero=True)
    chain_id = read_chain_id(client)
    pinned = block or read_block(client)
    core, factory = _author_root(client, registry, pinned)
    payout = _address(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorPayout", [author_id], pinned), "payout")
    count = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorHubCount", [author_id], pinned)
    if block is None:
        assert_canonical(client, pinned)
    assert_chain(client, chain_id)
    return LifecycleAuthor(registry, core, factory, author_id, payout, int(payout, 16) != 0,
        count, chain_id, pinned.number, pinned.block_hash)


def _page_bounds(offset: int, limit: int, maximum: int) -> None:
    _uint(offset, 256, "offset")
    _uint(limit, 256, "limit")
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be 1..{maximum}")


def _claim_assets(assets: Sequence[str]) -> tuple[str, ...]:
    if isinstance(assets, (str, bytes)) or not isinstance(assets, Sequence) or len(assets) > 8:
        raise ValueError("claim assets must be a sorted unique nonzero list of at most eight addresses")
    normalized = tuple(_address(asset, "asset", nonzero=True) for asset in assets)
    if any(int(left, 16) >= int(right, 16) for left, right in zip(normalized, normalized[1:])):
        raise ValueError("claim assets must be sorted, unique and nonzero; never silently reordered")
    return normalized


@read_invocation("author.discovery")
def read_author_hubs(client: Web3, *, registry: str, author_id: str, offset: int = 0, limit: int = 100, block: LaunchBlock | None = None) -> AuthorHubsPage:
    """Read bounded append-only authenticated hubs, including retired sources."""
    _page_bounds(offset, limit, 100)
    pinned = block or read_block(client)
    author = read_lifecycle_author(client, registry=registry, author_id=author_id, block=pinned)
    if offset > author.hub_count:
        raise LifecyclePlanningError("INVALID_AUTHOR_PAGE", "Discovery offset exceeds the registered hub count")
    hubs, next_offset, total = _call(client, author.registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorHubs", [author.author_id, offset, limit], pinned)
    if total != author.hub_count or next_offset != offset + len(hubs) or next_offset > total or len(hubs) > limit:
        raise LifecyclePlanningError("INVALID_AUTHOR_PAGE", "Registry returned an inconsistent discovery cursor")
    canonical = tuple(_canonical_author_hub(client, author.registry, author.factory, hub, pinned) for hub in hubs)
    if block is None:
        assert_canonical(client, pinned)
    assert_chain(client, author.chain_id)
    return AuthorHubsPage(**vars(author), hubs=canonical, offset=offset, next_offset=next_offset,
        total=total, cursor_complete=next_offset == total)


@read_invocation("author.fees")
def read_developer_fees(client: Web3, *, registry: str, hub: str, author_id: str, assets: Sequence[str] = (), block: LaunchBlock | None = None) -> DeveloperFees:
    """Read actual supported assets, reserved credits and every frozen source."""
    requested = _claim_assets(assets)
    pinned = block or read_block(client)
    author = read_lifecycle_author(client, registry=registry, author_id=author_id, block=pinned)
    hub = _canonical_author_hub(client, author.registry, author.factory, hub, pinned)
    actual = tuple(_address(asset, "asset", nonzero=True) for asset in _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "assets", [], pinned))
    supported = {asset.lower() for asset in actual}
    balances = []
    for asset in requested or actual:
        is_supported = asset.lower() in supported
        amount = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "claimableDeveloperFees", [author.author_id, asset], pinned) if is_supported else 0
        reserved = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "reservedDeveloperFees", [asset], pinned) if is_supported else 0
        balances.append(DeveloperFeeBalance(asset, is_supported, amount, reserved))
    sources = []
    for source in _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "sources", [], pinned):
        values = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "sourceTerms", [source], pinned)
        terms = SourceTermsV3(values[0], hex_bytes(values[1]), hex_bytes(values[2]), *values[3:])
        source_assets = tuple(_call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "sourceAssets", [source], pinned))
        sources.append(DeveloperFeeSource(to_checksum_address(source), source_assets, terms))
    if block is None:
        assert_canonical(client, pinned)
    assert_chain(client, author.chain_id)
    return DeveloperFees(**vars(author), hub=hub, assets=actual, balances=tuple(balances), sources=tuple(sources))


def _unsigned_fee_transaction(client: Web3, *, chain_id: int, account: str, to: str, abi: Sequence[Mapping[str, Any]], function: str, args: Sequence[Any]) -> LifecycleUnsignedTransaction:
    _uint(chain_id, 256, "chainId")
    if not 0 < chain_id <= (1 << 53) - 1 or read_chain_id(client) != chain_id:
        raise LifecyclePlanningError("CHAIN_MISMATCH", "Unsigned transaction chain must match the selected author domain")
    return {"chainId": chain_id, "from": _address(account, "account", nonzero=True), "to": _address(to, "to", nonzero=True), "data": _encode_function(abi, function, args), "value": 0}


@read_invocation("author.payout")
def build_set_author_payout_transaction(client: Web3, *, registry: str, author_id: str, payout: str, account: str, chain_id: int) -> LifecycleUnsignedTransaction:
    payout = _address(payout, "payout", nonzero=True)
    block = read_block(client)
    state = read_lifecycle_author(client, registry=registry, author_id=author_id, block=block)
    if not state.known:
        raise LifecyclePlanningError("UNKNOWN_AUTHOR", "Cannot route an unknown stable author identity")
    account = _address(account, "account", nonzero=True)
    admin = _call(client, state.registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "admin", [], block)
    if account.lower() not in (state.payout.lower(), admin.lower()):
        raise LifecyclePlanningError("AUTHOR_AUTHORIZATION", "Only the registry admin/current controller can update routing")
    transaction = _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=registry,
        abi=LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, function="setAuthorPayout", args=[state.author_id, payout])
    assert_canonical(client, block)
    assert_chain(client, chain_id)
    return transaction


@read_invocation("author.claim")
def build_claim_developer_fees_transaction(client: Web3, *, registry: str, hub: str, author_id: str, asset: str, account: str, chain_id: int) -> DeveloperClaimTransaction:
    """Build a supported canonical direct claim with its exact receipt context."""
    asset = _address(asset, "asset", nonzero=True)
    block = read_block(client)
    author = read_lifecycle_author(client, registry=registry, author_id=author_id, block=block)
    hub = _canonical_author_hub(client, author.registry, author.factory, hub, block)
    assets = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "assets", [], block)
    if not any(actual.lower() == asset.lower() for actual in assets):
        raise LifecyclePlanningError("UNSUPPORTED_ASSET", "Direct claims require an asset supported by this canonical hub")
    transaction = _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=hub,
        abi=LAUNCH_FEE_HUB_V3_ABI, function="claimDeveloperFees", args=[author.author_id, asset])
    assert_canonical(client, block)
    assert_chain(client, chain_id)
    return {**transaction, "claim": DeveloperDirectClaim(author.registry, author.factory, author.author_id, hub, asset)}


@read_invocation("author.claim")
def build_claim_developer_fees_page_transaction(client: Web3, *, registry: str, author_id: str, offset: int, limit: int, account: str, chain_id: int, assets: Sequence[str] = ()) -> DeveloperClaimTransaction:
    """Build one 1..10-hub fixed-destination page and preserve its receipt context."""
    _page_bounds(offset, limit, 10)
    normalized = _claim_assets(assets)
    block = read_block(client)
    page = read_author_hubs(client, registry=registry, author_id=author_id, offset=offset, limit=limit, block=block)
    transaction = _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=page.factory,
        abi=LAUNCH_FEE_HUB_FACTORY_V3_ABI, function="claimDeveloperFeesPage", args=[page.author_id, offset, limit, normalized])
    assert_canonical(client, block)
    assert_chain(client, chain_id)
    return {**transaction, "claim": DeveloperPageClaim(page.registry, page.factory, page.author_id, offset, limit, normalized)}


def _claim_log(abi: Sequence[Mapping[str, Any]], name: str, log: Mapping[str, Any]) -> Mapping[str, Any] | None:
    entry = next(entry for entry in abi if entry["type"] == "event" and entry["name"] == name)
    signature = keccak(text=name + "(" + ",".join(canonical_abi_type(item) for item in entry["inputs"]) + ")")
    topics = [bytes.fromhex(hex_bytes(topic)[2:]) for topic in log.get("topics", ())]
    if not topics or topics[0] != signature:
        return None
    indexed = [item for item in entry["inputs"] if item["indexed"]]
    if len(topics) != 1 + len(indexed):
        raise ValueError("developer claim event has invalid indexed fields")
    plain = [item for item in entry["inputs"] if not item["indexed"]]
    values = abi_decode([canonical_abi_type(item) for item in plain], bytes.fromhex(hex_bytes(log["data"])[2:]))
    arguments = {item["name"]: abi_decode([canonical_abi_type(item)], topic)[0] for item, topic in zip(indexed, topics[1:])}
    arguments.update({item["name"]: value for item, value in zip(plain, values)})
    return arguments


def decode_developer_claim_receipt(receipt: Mapping[str, Any], *, transaction: DeveloperClaimTransaction) -> DeveloperClaimReceipt:
    """Receipt-proven payments only; completed cursors do not promise payment."""
    claim = transaction.get("claim")
    if not isinstance(claim, (DeveloperDirectClaim, DeveloperPageClaim)):
        raise LifecyclePlanningError("CLAIM_RECEIPT_IDENTITY", "Receipt requires the canonical unsigned builder's claim context")
    target = claim.hub if claim.kind == "direct" else claim.factory
    sender = _address(transaction["from"], "claim sender", nonzero=True)
    if transaction["to"].lower() != target.lower() or transaction["value"] != 0 or not receipt.get("to") or receipt["to"].lower() != target.lower() or not receipt.get("from") or receipt["from"].lower() != sender.lower():
        raise LifecyclePlanningError("CLAIM_RECEIPT_IDENTITY", "Receipt account/target differs from its fixed-destination unsigned context")
    expected = _encode_function(LAUNCH_FEE_HUB_V3_ABI if claim.kind == "direct" else LAUNCH_FEE_HUB_FACTORY_V3_ABI,
        "claimDeveloperFees" if claim.kind == "direct" else "claimDeveloperFeesPage",
        [claim.author_id, claim.asset] if claim.kind == "direct" else [claim.author_id, claim.offset, claim.limit, claim.assets])
    if hex_bytes(transaction["data"]) != expected:
        raise LifecyclePlanningError("CLAIM_RECEIPT_IDENTITY", "Unsigned claim context does not encode this exact destination and claim")
    raw_status = receipt["status"]
    if raw_status in ("success", "0x1", 1) and not isinstance(raw_status, bool):
        success = True
    elif raw_status in ("reverted", "0x0", 0) and not isinstance(raw_status, bool):
        success = False
    else:
        raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Receipt status must be a canonical success/revert value")
    if not success:
        return DeveloperClaimReceipt(False, "reverted", (), (), False, False,
            reason="Claim reverted; no payment or cursor advancement occurred")
    results = []
    cursor = None
    seen = set()
    for log in receipt.get("logs", ()):
        if log.get("address", "").lower() != target.lower():
            continue
        if log.get("removed"):
            raise LifecyclePlanningError("CLAIM_RECEIPT_REORGED", "Removed canonical claim logs cannot prove payment")
        if claim.kind == "direct":
            value = _claim_log(LAUNCH_FEE_HUB_V3_ABI, "DeveloperFeesClaimed", log)
            if value is not None:
                if results or value["authorId"].lower() != claim.author_id.lower() or value["asset"].lower() != claim.asset.lower() or value["amount"] == 0:
                    raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Direct payment differs from the exact author/asset or is duplicated")
                results.append(DeveloperClaimResult(claim.hub, value["asset"], value["amount"], 0, "paid",
                    "0x00000000", value["payout"]))
        else:
            value = _claim_log(LAUNCH_FEE_HUB_FACTORY_V3_ABI, "DeveloperClaimResult", log)
            if value is not None:
                status, amount, error = value["status"], value["amount"], hex_bytes(value["errorSelector"])
                key = (value["hub"].lower(), value["asset"].lower())
                if value["authorId"].lower() != claim.author_id.lower() or status > 3 or key in seen or (claim.assets and not any(asset.lower() == value["asset"].lower() for asset in claim.assets)) or (amount == 0 if status == 0 else amount != 0) or (status < 2 and error != "0x00000000"):
                    raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Factory row differs in author, asset, amount, status or uniqueness")
                seen.add(key)
                results.append(DeveloperClaimResult(value["hub"], value["asset"], amount, status,
                    ("paid", "zero", "unsupported", "failed")[status], error))
            value = _claim_log(LAUNCH_FEE_HUB_FACTORY_V3_ABI, "DeveloperClaimPage", log)
            if value is not None:
                if cursor is not None or value["authorId"].lower() != claim.author_id.lower() or value["offset"] != claim.offset or value["nextOffset"] < claim.offset or value["nextOffset"] > value["total"] or value["nextOffset"] - claim.offset > claim.limit:
                    raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Cursor differs from the bounded expected page")
                cursor = value
    if claim.kind == "page":
        if cursor is None:
            raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Successful page receipt lacks its canonical cursor")
        hubs = len({result.hub.lower() for result in results})
        if hubs != cursor["nextOffset"] - cursor["offset"] or claim.assets and len(results) != hubs * len(claim.assets):
            raise LifecyclePlanningError("INVALID_CLAIM_RECEIPT", "Receipt rows do not account for each observed hub/asset")
    observed = bool(results) or cursor is not None
    return DeveloperClaimReceipt(True, "observed" if observed else "unobserved", tuple(results),
        tuple(result for result in results if result.status == 3),
        cursor is not None and cursor["nextOffset"] == cursor["total"],
        observed and all(result.status < 2 for result in results),
        cursor["offset"] if cursor else None, cursor["nextOffset"] if cursor else None, cursor["total"] if cursor else None,
        "No direct payment event proves an amount; zero credit is possible" if not observed else None)


__all__ = [
    "ControlledLaunchFork", "LaunchBlock", "LaunchExecutionLimits", "LaunchStateChanged",
    "LifecycleLimitResolver", "LaunchLimitContext", "create_controlled_launch_fork",
    "LIFECYCLE_PLAN_DOMAIN", "LAUNCH_PLAN_V1_ABI_TYPE", "LIFECYCLE_REQUIRED_CAPABILITIES",
    "LIFECYCLE_TOKEN_ONLY_CAPABILITY", "LIFECYCLE_EMPTY_PREPARE_CAPABILITY",
    "LIFECYCLE_PERMANENT_CUSTODY_CAPABILITY", "LIFECYCLE_CANONICAL_FEES_CAPABILITY",
    "LIFECYCLE_ERC404_CAPABILITY", "LIFECYCLE_MULTI_POSITION_CAPABILITY",
    "LIFECYCLE_MAX_ERC20_SUPPLY", "LIFECYCLE_MAX_REWARD_ERC20_SUPPLY", "LIFECYCLE_MAX_ERC404_SUPPLY",
    "V4_LIFECYCLE_CONFIG_SCHEMA", "ABYSS_LIFECYCLE_CONFIG_SCHEMA",
    "V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V5", "V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6",
    "V4_POOL_BOUND_MARKET_ECONOMICS_DOMAIN", "V4_LIFECYCLE_HOOK_PERMISSION_MASK", "V4_LIFECYCLE_HOOK_PERMISSIONS",
    "NITRO_ARB_SYS_ADDRESS", "NITRO_ARB_GAS_INFO_ADDRESS", "NITRO_NODE_INTERFACE_ADDRESS",
    "LifecycleMode", "LifecycleVenue", "LaunchExecutionMode", "PoolBoundLifecycleConfigVersion",
    "LifecycleHookTopology", "ProfileTopologyV1", "LifecycleProfile", "LifecycleProfileMetadata",
    "LifecycleAdapterRegistration", "LifecycleProfileRegistration", "LifecycleConstructionProfile",
    "LaunchBoundsV2", "LaunchGraphV2", "LaunchEnvelopeV2", "LifecycleDeveloperTerms",
    "SourceTermsV3", "LifecycleAuthor", "AuthorHubsPage", "DeveloperFeeBalance", "DeveloperFeeSource",
    "DeveloperFees", "LifecycleUnsignedTransaction", "DeveloperClaimTransaction", "DeveloperDirectClaim",
    "DeveloperPageClaim", "DeveloperClaimResult", "DeveloperClaimReceipt",
    "LifecyclePoolBoundV4MarketConfig", "LifecyclePoolBoundV4MarketConfigV5", "LifecyclePoolBoundV4MarketConfigV6",
    "PoolBoundHookParameters", "PoolBoundHookParametersV1", "PoolBoundHookParametersV2",
    "PoolBoundHookDeployment", "PoolBoundLifecycleDeployment", "PoolBoundHookMiningProgress", "PoolBoundHookSalt",
    "PoolBoundHookDeploymentTransaction", "LifecyclePlanningError",
    "is_pool_bound_v4_config_version", "pool_bound_v4_lifecycle_config_schema",
    "encode_lifecycle_pool_bound_v4_market_config", "decode_lifecycle_pool_bound_v4_market_config",
    "encode_pool_bound_hook_parameters", "build_pool_bound_hook_parameters", "pool_bound_hook_init_code_hash",
    "pool_bound_market_commitment", "predict_pool_bound_hook_address", "valid_pool_bound_hook_address",
    "validate_pool_bound_hook_fees", "validate_v4_lifecycle_market", "validate_v4_lifecycle_oracle",
    "validate_lifecycle_market_identity", "read_lifecycle_profiles", "read_pool_bound_hook_deployment",
    "mine_pool_bound_hook_salt", "prepare_and_plan_lifecycle_launch", "build_pool_bound_hook_deployment_transaction",
    "LaunchPlanV1", "LaunchProgress", "LaunchSimulation", "PlannedLaunch", "LifecycleCanonicalProgress",
    "LifecycleMarketIdentity", "LifecyclePreparedMarket", "LifecycleMarketLiveState", "LifecycleMarketProgress",
    "LifecyclePositionIdentity", "LaunchExecutionContextV1", "LaunchReceiptV1",
    "LifecycleReceiptReference", "LifecycleReceiptStatus", "LifecycleFundingPrerequisite",
    "LifecycleTokenKind", "LifecycleRewardMode", "LifecycleFundingKind", "LifecyclePhase",
    "LifecycleTokenConfig", "LifecycleAssetFunding", "LifecycleFeeAssetPolicy", "LifecycleMarketConfig", "LifecycleInitialBuy",
    "LifecycleV4Position", "LifecycleV4MarketConfig", "LifecycleAbyssPosition", "LifecycleAbyssMarketConfig",
    "LifecycleApproval", "LifecycleTransaction", "LifecycleTransactionKind", "LifecycleEvent",
    "LifecyclePostcondition", "LifecycleLaunchPostcondition", "LifecycleAllowancePostcondition",
    "LifecycleAdmission", "LifecycleProofOutcomes", "LifecycleTransactionEstimate", "LifecycleSimulationStep",
    "LifecycleOpeningBuyQuote", "LifecycleLaunchGasBounds", "LifecycleGasProfileEvidence",
    "LifecycleFailureCategory", "LifecycleCapacityConstraint",
    "launch_plan_from_dict", "launch_plan_to_dict", "serialize_launch_plan", "parse_launch_plan",
    "to_launch_plan_tuple", "encode_launch_plan", "hash_launch_plan", "hash_launch_identity", "launch_id_of",
    "encode_launch_bounds", "decode_launch_bounds", "hash_launch_bounds", "encode_launch_envelope", "decode_launch_envelope",
    "hash_lifecycle_profile", "hash_launch_dependencies",
    "encode_lifecycle_v4_market_config", "decode_lifecycle_v4_market_config",
    "encode_lifecycle_abyss_market_config", "decode_lifecycle_abyss_market_config", "build_lifecycle_calldata",
    "build_launch_transactions", "predict_launch_token", "read_lifecycle_token_at_block",
    "compute_lifecycle_opening_buys", "compute_lifecycle_launch_gas_bounds",
    "decode_lifecycle_events", "decode_lifecycle_launch_receipt",
    "plan_launch", "simulate_launch_plan", "build_next_transaction",
    "read_launch_progress", "read_launch_markets", "preview_lifecycle_fees",
    "read_lifecycle_author", "build_set_author_payout_transaction", "read_author_hubs", "read_developer_fees",
    "build_claim_developer_fees_transaction", "build_claim_developer_fees_page_transaction", "decode_developer_claim_receipt",
]
