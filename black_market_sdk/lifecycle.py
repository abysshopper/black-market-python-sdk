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
from typing import Any, Literal

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address
from web3 import Web3

from .addresses import ROBINHOOD_MAINNET_CHAIN_ID
from .lifecycle_abis import (
    ABYSS_MARKET_CONFIG_COMPONENTS_V1, LAUNCH_DIRECTORY_V1_ABI, LAUNCH_FEE_HUB_V3_ABI,
    LAUNCH_FUNDING_ESCROW_V1_ABI, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI,
    LAUNCH_LIFECYCLE_V1_ABI, LAUNCH_MARKET_ADAPTER_V1_ABI, LAUNCH_PLAN_COMPONENTS_V1,
    LAUNCH_PROGRESS_COMPONENTS_V1, LAUNCH_RECEIPT_COMPONENTS_V1,
    LAUNCH_TOKEN_FACTORY_V1_ABI,
    ADAPTER_REGISTRATION_COMPONENTS_V1, MARKET_IDENTITY_COMPONENTS_V1,
    POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1, POOL_HOOK_DEPLOYER_V1_ABI,
    FIXED_FEE_POOL_HOOK_V1_ABI,
    POOL_MARKET_ADAPTER_V1_ABI, PROFILE_TOPOLOGY_COMPONENTS_V1,
    PROFILE_REGISTRATION_COMPONENTS_V1, POOL_FEE_COLLECTOR_FACTORY_V1_ABI,
    V4_MARKET_CONFIG_COMPONENTS_V4, V4_MARKET_CONFIG_COMPONENTS_V5,
    LAUNCH_BOUNDS_COMPONENTS_V2, LAUNCH_GRAPH_COMPONENTS_V2,
    LAUNCH_ENVELOPE_COMPONENTS_V2, DEVELOPER_TERMS_COMPONENTS_V3,
    SOURCE_TERMS_COMPONENTS_V3, LAUNCH_FEE_HUB_FACTORY_V3_ABI,
    MARKET_CONFIG_COMPONENTS_V1,
    NITRO_ARB_SYS_ABI, NITRO_ARB_GAS_INFO_ABI, NITRO_NODE_INTERFACE_ABI,
)
from .lifecycle_rpc import (
    ControlledLaunchFork, LaunchBlock, LaunchExecutionLimits, LaunchLimitContext, LaunchRpcError, LaunchRpcSimulation,
    LaunchStateChanged, assert_canonical, assert_chain, create_controlled_launch_fork, hex_bytes, quantity, read_block, rpc, rpc_transaction, simulate_transactions,
)

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
LAUNCH_PLAN_DOMAIN_V1 = keccak(text="BLACK_MARKET_LAUNCH_PLAN_V1")
NITRO_ARB_SYS_ADDRESS = "0x0000000000000000000000000000000000000064"
NITRO_ARB_GAS_INFO_ADDRESS = "0x000000000000000000000000000000000000006c"
NITRO_NODE_INTERFACE_ADDRESS = "0x00000000000000000000000000000000000000c8"
LAUNCH_TOKEN_ONLY_CAPABILITY_V1 = 1
LAUNCH_EMPTY_PREPARE_CAPABILITY_V1 = 2
LAUNCH_PERMANENT_CUSTODY_CAPABILITY_V1 = 8
LAUNCH_CANONICAL_FEES_CAPABILITY_V1 = 16
LAUNCH_ERC404_CAPABILITY_V1 = 32
LAUNCH_MULTI_POSITION_CAPABILITY_V1 = 64
# TOKEN_ONLY|EMPTY_PREPARE|PERMANENT_CUSTODY|CANONICAL_FEES. Preactivation
# safety combines launch-token transfer restrictions with canonical opening-state
# verification on both venues. Execution grouping never changes eligibility.
LAUNCH_REQUIRED_CAPABILITIES_V1 = (
    LAUNCH_TOKEN_ONLY_CAPABILITY_V1 | LAUNCH_EMPTY_PREPARE_CAPABILITY_V1
    | LAUNCH_PERMANENT_CUSTODY_CAPABILITY_V1 | LAUNCH_CANONICAL_FEES_CAPABILITY_V1
)
LifecycleLimitResolver = Callable[[Web3, LaunchLimitContext], LaunchExecutionLimits]


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
class LifecyclePoolBoundV4MarketConfig:
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
class LifecycleProfile:
    id: str
    registration: Mapping[str, Any]
    adapter: Mapping[str, Any]
    topology: ProfileTopologyV1
    venue_kind: Literal["uniswap-v4", "abyss", "unknown"]
    admitted: bool
    reason: str | None = None
    envelope: LaunchEnvelopeV2 | None = None
    developer_terms: LifecycleDeveloperTerms | None = None
    protocol_maximum_developer_fee_bps: int | None = None


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
class PreparedPoolBoundLifecyclePlan:
    plan: LaunchPlanV1
    deployments: tuple[PoolBoundLifecycleDeployment, ...]


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
V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA = keccak(text=_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V5))
ABYSS_LIFECYCLE_CONFIG_SCHEMA = keccak(text=_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1))
POOL_BOUND_MARKET_ECONOMICS_DOMAIN_V1 = keccak(text="black-market.pool-bound-market-economics.v1")
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
        if (kind.startswith("uint") or kind.startswith("int")) and isinstance(item, str):
            if not re.fullmatch(r"-?(0|[1-9][0-9]*)", item):
                raise ValueError(f"{entry['name']} must be a canonical decimal quantity")
            item = int(item)
        return _abi_value(entry, item)
    if not isinstance(value, Mapping) or set(value) != {entry["name"] for entry in components}:
        raise ValueError("the JSON plan must exactly match the versioned Solidity tuple fields")
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
    to_launch_plan_tuple(plan)
    return plan


def to_launch_plan_tuple(plan: LaunchPlanV1) -> tuple[Any, ...]:
    """Validate economic invariants and return the exact LaunchPlanV1 tuple."""
    values = _struct_tuple(LAUNCH_PLAN_COMPONENTS_V1, plan)
    _address(plan.orchestrator, "orchestrator", nonzero=True)
    _address(plan.creator, "creator", nonzero=True)
    _address(plan.token.inventory_recipient, "inventoryRecipient", nonzero=True)
    if plan.chain_id == 0 or plan.token.supply == 0 or not plan.token.name or not plan.token.symbol:
        raise ValueError("chain, token supply, name and symbol must be nonempty")
    if len(plan.token.name.encode()) > 128 or len(plan.token.symbol.encode()) > 32 or len(plan.token.metadata_uri.encode()) > 2048:
        raise ValueError("token name/symbol/metadata exceed the committed UTF-8 byte bounds")
    if len(plan.funding) > 8:
        raise ValueError("funding supports at most eight distinct output assets")
    if int(plan.token.kind) not in (0, 1) or int(plan.token.reward_mode) not in (0, 1, 2):
        raise ValueError("unsupported token or reward kind")
    if int(plan.token.kind) == 0 and int(plan.token.reward_mode) != 0 and plan.token.supply > 10**77:
        raise ValueError("reward-enabled lifecycle ERC20 token supply must not exceed 10**77")
    if int(plan.token.kind) == 1 and plan.token.nft_unit == 0:
        raise ValueError("ERC404 nftUnit must be positive")
    if not 1 <= len(plan.markets) <= 16 or len(plan.buys) > 64:
        raise ValueError("a lifecycle launch requires 1..16 markets and at most 64 buys")
    if not 1 <= len(plan.fee_assets) <= 8 or plan.executor_fee_bps > 1_000:
        raise ValueError("fee assets must number 1..8 and executorFeeBps must not exceed 1000")
    for sequence, name in ((plan.fee_assets, "feeAssets"), (plan.funding, "funding")):
        addresses = [int(item.asset, 16) for item in sequence]
        if any(address == 0 for address in addresses) or any(left >= right for left, right in zip(addresses, addresses[1:])):
            raise ValueError(f"{name} assets must be nonzero, unique and ascending; never silently sorted")
    for policy in plan.fee_assets:
        if policy.owner_bps + policy.rewards_bps + policy.burn_bps != 10_000:
            raise ValueError("each fee disposition must sum to 10000")
        if int(plan.token.reward_mode) == 0 and policy.rewards_bps:
            raise ValueError("rewardMode None cannot commit a rewards disposition")
    if int(plan.token.reward_mode) != 0 and not any(policy.rewards_bps for policy in plan.fee_assets):
        raise ValueError("a staking/dividends mode requires an actual reward disposition")
    for funding in plan.funding:
        if int(funding.kind) not in (0, 1, 2) or funding.amount == 0 or funding.input_amount == 0:
            raise ValueError("funding kind and positive amounts must be valid")
        if int(funding.kind) != 2:
            if funding.asset.lower() != funding.input_asset.lower() or funding.input_amount != funding.amount or int(funding.target, 16) != 0 or hex_bytes(funding.data) != "0x":
                raise ValueError("direct ERC20/native-wrap funding cannot contain swap economics")
        elif funding.asset.lower() == funding.input_asset.lower() or int(funding.target, 16) == 0 or hex_bytes(funding.data) == "0x":
            raise ValueError("swap funding requires distinct assets and a committed target/calldata")
        if len(bytes.fromhex(hex_bytes(funding.data)[2:])) > 16_384:
            raise ValueError("committed conversion calldata exceeds its on-chain bound")
    if sum(market.token_budget for market in plan.markets) > plan.token.supply:
        raise ValueError("market token budgets exceed the one committed supply")
    fee_assets = {policy.asset.lower() for policy in plan.fee_assets}
    requirements: dict[str, int] = {}
    v4_quotes: set[str] = set()
    for market in plan.markets:
        _address(market.quote_asset, "quoteAsset", nonzero=True)
        if market.token_budget == 0 or market.config_version == 0 or hex_bytes(market.adapter_id) == ZERO_HASH or hex_bytes(market.profile_id) == ZERO_HASH:
            raise ValueError("market budget, immutable IDs and config version must be nonzero")
        if not 1 <= len(bytes.fromhex(hex_bytes(market.config)[2:])) <= 16_384:
            raise ValueError("market config must fit its nonempty bounded versioned schema")
        if market.quote_asset.lower() not in fee_assets:
            raise ValueError("every market quote must belong to the committed fee-asset set")
        if market.config_version not in (1, 4, 5):
            raise ValueError("unsupported config version; supported market schemas are Abyss1, shared4 and bound5")
        if market.config_version in (4, 5):
            config = decode_lifecycle_v4_market_config(market.config) if market.config_version == 4 else decode_lifecycle_pool_bound_v4_market_config(market.config)
            if config.lp_fee_pips >= 1_000_000:
                raise ValueError("lpFeePips must be below 1000000")
            if config.hook_fee_pips >= 1_000_000:
                raise ValueError("hookFeePips must be below 1000000")
            if hex_bytes(config.profile_id) != hex_bytes(market.profile_id):
                raise ValueError("inner reviewed profileId differs from its outer market binding")
            quote = market.quote_asset.lower()
            if quote in v4_quotes:
                raise ValueError("one V4 market per quote is allowed across shared and pool-bound offerings")
            v4_quotes.add(quote)
    for buy in plan.buys:
        _address(buy.recipient, "buy recipient", nonzero=True)
        if buy.market_index >= len(plan.markets) or buy.quote_amount_in == 0 or buy.min_token_out == 0 or buy.recipient.lower() == plan.orchestrator.lower():
            raise ValueError("each buy requires an existing market, positive quote/output constraints and a non-core recipient")
        asset = plan.markets[buy.market_index].quote_asset.lower()
        requirements[asset] = requirements.get(asset, 0) + buy.quote_amount_in
    funded = {funding.asset.lower(): funding.amount for funding in plan.funding}
    if any(funded.get(asset, 0) < amount for asset, amount in requirements.items()):
        raise ValueError("per-asset funding is below the committed ordered buy budgets")
    return values


def launch_plan_to_dict(plan: LaunchPlanV1) -> dict[str, Any]:
    return _json_tuple(LAUNCH_PLAN_COMPONENTS_V1, to_launch_plan_tuple(plan))


def encode_launch_plan(plan: LaunchPlanV1) -> bytes:
    return abi_encode([LAUNCH_PLAN_V1_ABI_TYPE], [to_launch_plan_tuple(plan)])


def hash_launch_plan(plan: LaunchPlanV1) -> str:
    return hex_bytes(keccak(abi_encode(["bytes32", LAUNCH_PLAN_V1_ABI_TYPE], [LAUNCH_PLAN_DOMAIN_V1, to_launch_plan_tuple(plan)])))


def launch_id_of(plan: LaunchPlanV1) -> str:
    return hex_bytes(keccak(abi_encode(["uint256", "address", "address", "uint256"], [plan.chain_id, _address(plan.orchestrator, "orchestrator", nonzero=True), _address(plan.creator, "creator", nonzero=True), _uint(plan.nonce, 256, "nonce")])))


def encode_lifecycle_v4_market_config(config: LifecycleV4MarketConfig | Mapping[str, Any]) -> bytes:
    values = _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V4, config)
    if values[0] != 4:
        raise ValueError("V4 lifecycle market config version must be 4")
    _validate_developer_fields(values[10], values[11], values[12], values[13])
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V4)], [values])


def _validate_developer_fields(profile_id: bytes, terms_digest: bytes, author_id: str, rate: int) -> None:
    if profile_id == bytes(32) or terms_digest == bytes(32) or int(author_id, 16) == 0:
        raise ValueError("reviewed config requires nonzero profileId, termsDigest and stable developerBeneficiary")
    if rate >= 10_000:
        raise ValueError("developerFeeBps must be below 10000 and within the reviewed protocol ceiling")


def decode_lifecycle_v4_market_config(data: bytes | str) -> LifecycleV4MarketConfig:
    raw = bytes.fromhex(hex_bytes(data)[2:])
    values = abi_decode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V4)], raw)[0]
    config = LifecycleV4MarketConfig(*values[:-1], tuple(LifecycleV4Position(*position) for position in values[-1]))
    if encode_lifecycle_v4_market_config(config) != raw:
        raise ValueError("shared V4 config is not its exact canonical V4 encoding")
    return config


def encode_lifecycle_pool_bound_v4_market_config(config: LifecyclePoolBoundV4MarketConfig | Mapping[str, Any]) -> bytes:
    values = _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V5, config)
    if values[0] != 5:
        raise ValueError("pool-bound V4 lifecycle market config version must be 5")
    _validate_developer_fields(values[11], values[12], values[13], values[14])
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V5)], [values])


def decode_lifecycle_pool_bound_v4_market_config(data: bytes | str) -> LifecyclePoolBoundV4MarketConfig:
    values = abi_decode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V5)], bytes.fromhex(hex_bytes(data)[2:]))[0]
    config = LifecyclePoolBoundV4MarketConfig(*values[:-1], tuple(LifecycleV4Position(*position) for position in values[-1]))
    if encode_lifecycle_pool_bound_v4_market_config(config) != bytes.fromhex(hex_bytes(data)[2:]):
        raise ValueError("pool-bound V4 config is not its exact canonical V5 encoding")
    return config


def encode_pool_bound_hook_parameters(parameters: PoolBoundHookParametersV1 | Mapping[str, Any]) -> bytes:
    return abi_encode([_tuple_type(POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1)], [_struct_tuple(POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1, parameters)])


def pool_bound_market_commitment(plan: LaunchPlanV1, *, token: str, registrar: str, market_index: int) -> str:
    """Commit every market economic field, normalizing only the mined hook salt."""
    _uint(market_index, 32, "marketIndex")
    if market_index >= len(plan.markets):
        raise ValueError("market_index is outside the economic plan")
    market = plan.markets[market_index]
    if market.config_version != 5:
        raise ValueError("market is not a current pool-bound V4 V5 config")
    config = decode_lifecycle_pool_bound_v4_market_config(market.config)
    if hex_bytes(config.profile_id) != hex_bytes(market.profile_id):
        raise ValueError("inner reviewed profileId differs from its outer market binding")
    config_hash = keccak(encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=ZERO_HASH)))
    return hex_bytes(keccak(abi_encode(
        ["bytes32", "uint256", "address", "address", "address", "bytes32", "bytes32", "address", "uint256", "uint32", "bytes32"],
        [POOL_BOUND_MARKET_ECONOMICS_DOMAIN_V1, _uint(plan.chain_id, 256, "chainId"),
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
    return abi_encode([_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1)], [_struct_tuple(ABYSS_MARKET_CONFIG_COMPONENTS_V1, config)])


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
    raw = rpc(client, "eth_call", [transaction, block.tag])
    outputs = _entry(abi, name)["outputs"]
    values = abi_decode([canonical_abi_type(item) for item in outputs], bytes.fromhex(hex_bytes(raw)[2:]))
    return values[0] if len(values) == 1 else values


def _read_token_factory_binding(client: Web3, orchestrator: str, block: LaunchBlock) -> tuple[str, str]:
    factory = _address(_call(client, orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "tokenFactory", [], block), "token factory", nonzero=True)
    code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [factory, block.tag]))[2:])
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
            raise ValueError("reviewed adapter dependency differs from its frozen deployment graph")
    if _call(client, graph.collector_factory, POOL_FEE_COLLECTOR_FACTORY_V1_ABI, "collectorDeployer", [], block).lower() != graph.collector_deployer.lower():
        raise ValueError("reviewed collector deployer differs from its frozen deployment graph")
    for address, expected in ((orchestrator, graph.core_code_hash), (graph.manager, graph.manager_code_hash), (graph.oracle_factory, graph.oracle_factory_code_hash), (graph.locker, graph.locker_code_hash), (graph.collector_factory, graph.collector_factory_code_hash), (graph.collector_deployer, graph.collector_deployer_code_hash), (graph.hook_deployer, graph.hook_deployer_code_hash), (graph.code_chunk0, graph.code_chunk0_hash)):
        code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [address, block.tag]))[2:])
        if not code or hex_bytes(keccak(code)) != hex_bytes(expected):
            raise ValueError("reviewed dependency runtime differs from its frozen code hash")
    if int(graph.code_chunk1, 16):
        code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [graph.code_chunk1, block.tag]))[2:])
        if not code or hex_bytes(keccak(code)) != hex_bytes(graph.code_chunk1_hash):
            raise ValueError("reviewed second creation chunk differs from its frozen code hash")
    elif hex_bytes(graph.code_chunk1_hash) != ZERO_HASH:
        raise ValueError("absent reviewed second chunk must have a zero code hash")
    for name, expected in (("codeChunk0", graph.code_chunk0), ("codeChunk1", graph.code_chunk1)):
        if _call(client, graph.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, name, [], block).lower() != expected.lower():
            raise ValueError("reviewed deployer chunks differ from their frozen addresses")
    if topology.hook_deployer.lower() != graph.hook_deployer.lower() or topology.hook_creation_code_hash != hex_bytes(graph.hook_creation_code_hash):
        raise ValueError("certified topology differs from its exact reviewed deployer and creation code")
    if hex_bytes(_call(client, graph.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, "creationCodeHash", [], block)) != topology.hook_creation_code_hash:
        raise ValueError("reviewed deployer creation bytecode differs from its certification")
    creation_code = _pool_bound_creation_code(client, topology, block)
    if topology.hook_topology == LifecycleHookTopology.SHARED_V4:
        code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [graph.hook_root, block.tag]))[2:])
        if not code or hex_bytes(keccak(code)) != hex_bytes(graph.hook_runtime_code_hash) or not valid_pool_bound_hook_address(graph.hook_root):
            raise ValueError("reviewed shared root differs from its exact runtime and hook permissions")
        recorded = _call(client, graph.hook_deployer, POOL_HOOK_DEPLOYER_V1_ABI, "deployedCodeHash", [graph.hook_root], block)
        args = abi_encode(["address", "address", "address"], [graph.manager, implementation, graph.oracle_factory])
        predicted = predict_pool_bound_hook_address(deployer=graph.hook_deployer, init_code_hash=keccak(creation_code + args), salt=graph.shared_hook_salt)
        if hex_bytes(recorded) != hex_bytes(graph.hook_runtime_code_hash) or predicted.lower() != graph.hook_root.lower():
            raise ValueError("reviewed shared root lacks exact typed-deployer constructor provenance")
    elif int(graph.hook_root, 16) or hex_bytes(graph.hook_runtime_code_hash) != ZERO_HASH or hex_bytes(graph.shared_hook_salt) != ZERO_HASH:
        raise ValueError("pool-bound reviewed graph cannot contain a shared hook root or salt")


def _certified_profile(client: Web3, registry: str, orchestrator: str, profile_id: bytes, block: LaunchBlock, required: int) -> tuple[tuple[Any, ...], tuple[Any, ...], ProfileTopologyV1]:
    profile = tuple(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profile", [profile_id], block))
    adapter = tuple(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "adapter", [profile[0]], block))
    schema = profile[1]
    if schema not in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA, ABYSS_LIFECYCLE_CONFIG_SCHEMA):
        raise ValueError("unsupported config schema; registry admission does not imply SDK codec support")
    implementation = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "requireEligible", [profile[0], profile_id, adapter[3], required], block)
    code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [adapter[0], block.tag]))[2:])
    if not profile[7] or not adapter[4] or profile[6] & required != required or adapter[2] & required != required or implementation.lower() != adapter[0].lower() or not code or keccak(code) != adapter[1]:
        raise ValueError("profile or adapter no longer has its exact immutable registry approval")
    if _call(client, implementation, LAUNCH_MARKET_ADAPTER_V1_ABI, "core", [], block).lower() != orchestrator.lower() or _call(client, implementation, LAUNCH_MARKET_ADAPTER_V1_ABI, "dependencyDigest", [], block) != profile[2]:
        raise ValueError("adapter authority or dependency graph differs from its approved profile")
    topology = _profile_topology(client, registry, profile_id, block)
    if topology.config_version != adapter[3]:
        raise ValueError("certified topology config version differs from its immutable adapter")
    if schema in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA):
        bound = schema == V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA
        version = 5 if bound else 4
        expected_topology = LifecycleHookTopology.POOL_BOUND_V4 if bound else LifecycleHookTopology.SHARED_V4
        if adapter[3] != version or topology.hook_topology != expected_topology:
            raise ValueError("reviewed V4 schema, version and certified topology must match exactly")
        if int(profile[4], 16) or (bound and int(profile[5], 16)) or (not bound and not int(profile[5], 16)):
            raise ValueError("reviewed V4 profile must retain its exact venue and hook topology")
        if _call(client, implementation, _PROFILE_DEPENDENCY_ABI, "PROFILE_ID", [], block) != profile_id or _call(client, implementation, _PROFILE_DEPENDENCY_ABI, "CONFIG_SCHEMA", [], block) != schema or _call(client, implementation, _PROFILE_DEPENDENCY_ABI, "CONFIG_VERSION", [], block) != version:
            raise ValueError("V4 adapter does not implement the approved profile, schema and version")
        envelope, terms = _profile_metadata(client, registry, profile_id, block)
        ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], block)
        if _profile_id(envelope) != profile_id or envelope.config_version != version or envelope.topology != expected_topology or envelope.economic_version != 3 or envelope.flags != 0 or envelope.callback_flags != V4_LIFECYCLE_HOOK_PERMISSIONS or envelope.callback_mask != V4_LIFECYCLE_HOOK_PERMISSION_MASK or envelope.capabilities != profile[6] or envelope.capabilities != adapter[2]:
            raise ValueError("reviewed envelope identity, version, capabilities or callbacks differ from admission")
        if hex_bytes(keccak(abi_encode([_tuple_type(LAUNCH_BOUNDS_COMPONENTS_V2)], [_struct_tuple(LAUNCH_BOUNDS_COMPONENTS_V2, envelope.bounds)]))) != hex_bytes(envelope.config_bounds_digest):
            raise ValueError("reviewed configuration bounds do not match their admitted digest")
        if not terms.enabled or terms.adapter.lower() != implementation.lower() or terms.beneficiary.lower() != envelope.beneficiary.lower() or hex_bytes(terms.terms_digest) != hex_bytes(envelope.terms_digest) or terms.maximum_developer_fee_bps != envelope.maximum_developer_fee_bps or terms.maximum_developer_fee_bps > ceiling or ceiling >= 10_000:
            raise ValueError("reviewed developer terms are disabled or differ from their exact admitted envelope")
        if envelope.graph.manager.lower() != profile[3].lower() or envelope.graph.hook_root.lower() != profile[5].lower():
            raise ValueError("reviewed venue/root differ from the registered deployment graph")
        _verify_profile_graph(client, registry, orchestrator, implementation, envelope, topology, block)
    else:
        if topology.hook_topology != LifecycleHookTopology.NONE or topology.hook_deployer.lower() != ZERO_ADDRESS or topology.hook_creation_code_hash != ZERO_HASH:
            raise ValueError("Abyss profile cannot claim a V4 hook topology")
        if adapter[3] != 1 or profile[3].lower() != profile[4].lower() or int(profile[4], 16) == 0 or int(profile[5], 16) != 0 or _call(client, implementation, _PROFILE_DEPENDENCY_ABI, "factory", [], block).lower() != profile[4].lower():
            raise ValueError("Abyss profile must retain its exact canonical factory and zero hook")
    return adapter, profile, topology


def read_lifecycle_profiles(client: Web3, *, orchestrator: str, profile_ids: Sequence[bytes | str] | None = None, offset: int = 0, limit: int = 100, block: LaunchBlock | None = None) -> tuple[LifecycleProfile, ...]:
    """Discover current eligible offerings without inferring topology from a hook."""
    orchestrator = _address(orchestrator, "orchestrator", nonzero=True)
    _uint(offset, 256, "offset")
    _uint(limit, 256, "limit")
    if limit > 100:
        raise ValueError("registry profile enumeration is bounded by 100")
    pinned = block or read_block(client)
    registry = _address(_call(client, orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], pinned), "registry", nonzero=True)
    if _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], pinned).lower() != orchestrator.lower():
        raise ValueError("registry is not bound to the selected lifecycle orchestrator")
    ids = profile_ids if profile_ids is not None else _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profileIds", [offset, limit], pinned)
    protocol_ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], pinned)
    result = []
    for item in ids:
        profile_id = _abi_value({"name": "profileId", "type": "bytes32"}, item)
        profile = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "profile", [profile_id], pinned)
        adapter = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "adapter", [profile[0]], pinned)
        topology = _profile_topology(client, registry, profile_id, pinned)
        reason = None
        envelope = terms = None
        if profile[1] in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA):
            envelope, terms = _profile_metadata(client, registry, profile_id, pinned)
        try:
            adapter, profile, topology = _certified_profile(client, registry, orchestrator, profile_id, pinned, LAUNCH_REQUIRED_CAPABILITIES_V1)
        except (ValueError, LaunchRpcError) as error:
            reason = str(error)
        venue_kind = "uniswap-v4" if profile[1] in (V4_LIFECYCLE_CONFIG_SCHEMA, V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA) else "abyss" if profile[1] == ABYSS_LIFECYCLE_CONFIG_SCHEMA else "unknown"
        result.append(LifecycleProfile(hex_bytes(profile_id), _json_tuple(PROFILE_REGISTRATION_COMPONENTS_V1, profile), _json_tuple(ADAPTER_REGISTRATION_COMPONENTS_V1, adapter), topology, venue_kind, reason is None, reason, envelope, terms, protocol_ceiling))
    assert_canonical(client, pinned)
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
    constructor_size = (18 if topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4 else 3) * 32
    if len(creation_code) + constructor_size > 49_152 or hex_bytes(keccak(creation_code)) != topology.hook_creation_code_hash:
        raise ValueError("actual creation-code chunks differ from certified bytecode or exceed the initcode bound")
    return creation_code


def _validate_v4_market(config: LifecycleV4MarketConfig | LifecyclePoolBoundV4MarketConfig, market: LifecycleMarketConfig, token: str, envelope: LaunchEnvelopeV2, ceiling: int) -> None:
    bounds = envelope.bounds
    if hex_bytes(config.oracle_config_id) == ZERO_HASH:
        raise ValueError("market oracle configuration must be nonzero")
    if config.version != market.config_version or hex_bytes(config.profile_id) != hex_bytes(market.profile_id) or hex_bytes(config.terms_digest) != hex_bytes(envelope.terms_digest) or config.developer_beneficiary.lower() != envelope.beneficiary.lower() or config.developer_beneficiary.lower() in (token.lower(), market.quote_asset.lower()) or config.developer_fee_bps > envelope.maximum_developer_fee_bps or config.developer_fee_bps > ceiling:
        raise ValueError("reviewed market profile, stable author, terms digest or explicit developer rate differs from admission")
    if config.treasury.lower() != envelope.protocol_treasury.lower() or config.protocol_fee_denominator != envelope.protocol_fee_denominator or isinstance(config.hook_fee_pips, bool) or not isinstance(config.hook_fee_pips, int) or not 0 <= config.hook_fee_pips < 1_000_000 or isinstance(config.lp_fee_pips, bool) or not isinstance(config.lp_fee_pips, int) or not 0 <= config.lp_fee_pips < 1_000_000 or not bounds.minimum_tick_spacing <= config.tick_spacing <= bounds.maximum_tick_spacing or config.fee_mode not in (0, 1) or not bounds.fee_mode_flags & (1 << config.fee_mode):
        raise ValueError("reviewed market economics exceed or differ from the exact admitted bounds")
    if not 1 <= len(config.positions) <= bounds.maximum_positions:
        raise ValueError("positions exceed the reviewed offering bounds")


def _validate_v4_oracle(client: Web3, config: LifecycleV4MarketConfig | LifecyclePoolBoundV4MarketConfig, envelope: LaunchEnvelopeV2, block: LaunchBlock) -> None:
    oracle_id = _abi_value({"name": "oracleConfigId", "type": "bytes32"}, config.oracle_config_id)
    if not any(oracle_id):
        raise ValueError("market oracle configuration must be nonzero")
    move, cardinality = _call(client, envelope.graph.oracle_factory, _ORACLE_FACTORY_ABI, "oracleConfigs", [oracle_id], block)
    if not 0 < move <= 887272 or not 2 <= cardinality <= envelope.bounds.maximum_oracle_cardinality:
        raise ValueError("selected market oracle is unregistered or exceeds the admitted cardinality bounds")


def _admit_market_config(client: Web3, registry: str, market: LifecycleMarketConfig, token: str, adapter: Sequence[Any], profile: Sequence[Any], topology: ProfileTopologyV1, block: LaunchBlock, required: int) -> tuple[LaunchEnvelopeV2 | None, LifecycleDeveloperTerms | None]:
    if profile[1] == ABYSS_LIFECYCLE_CONFIG_SCHEMA:
        positions = abi_decode([_tuple_type(ABYSS_MARKET_CONFIG_COMPONENTS_V1)], bytes.fromhex(hex_bytes(market.config)[2:]))[0][-1]
        envelope = terms = None
    else:
        config = decode_lifecycle_pool_bound_v4_market_config(market.config) if topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4 else decode_lifecycle_v4_market_config(market.config)
        positions = config.positions
        envelope, terms = _profile_metadata(client, registry, bytes.fromhex(hex_bytes(market.profile_id)[2:]), block)
        ceiling = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "protocolMaximumDeveloperFeeBps", [], block)
        _validate_v4_market(config, market, token, envelope, ceiling)
        _validate_v4_oracle(client, config, envelope, block)
    if len(positions) > 1:
        required |= LAUNCH_MULTI_POSITION_CAPABILITY_V1
        if adapter[2] & required != required or profile[6] & required != required:
            raise ValueError("positions require the registry's admitted multi-position capability")
        _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "requireEligible", [profile[0], bytes.fromhex(hex_bytes(market.profile_id)[2:]), market.config_version, required], block)
    if envelope is not None:
        _call(client, envelope.graph.collector_factory, POOL_FEE_COLLECTOR_FACTORY_V1_ABI, "decodeAndValidate", [adapter[0], token, _struct_tuple(MARKET_CONFIG_COMPONENTS_V1, market)], block)
    return envelope, terms


def _pool_bound_deployment(client: Web3, plan: LaunchPlanV1, index: int, token: str, adapter: Sequence[Any], profile: Sequence[Any], topology: ProfileTopologyV1, block: LaunchBlock) -> tuple[PoolBoundHookDeployment, PoolBoundHookParametersV1]:
    if topology.hook_topology != LifecycleHookTopology.POOL_BOUND_V4:
        raise ValueError("market is not a certified pool-bound V4 offering")
    market = plan.markets[index]
    config = decode_lifecycle_pool_bound_v4_market_config(market.config)
    implementation = adapter[0]
    if market.config_version != topology.config_version or bytes.fromhex(hex_bytes(market.adapter_id)[2:]) != profile[0]:
        raise ValueError("market adapter/config version differs from its certified pool-bound profile")
    registry = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block)
    required = LAUNCH_REQUIRED_CAPABILITIES_V1 | (LAUNCH_ERC404_CAPABILITY_V1 if int(plan.token.kind) == 1 else 0)
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
    parameters = PoolBoundHookParametersV1(
        profile[3], implementation, oracle_factory, plan.orchestrator, locker, token, market.quote_asset,
        config.lp_fee_pips, config.tick_spacing, config.sqrt_price_x96, config.hook_fee_pips, config.fee_mode,
        config.protocol_fee_denominator, config.treasury, config.external_liquidity_disabled, config.oracle_config_id,
        pool_bound_market_commitment(plan, token=token, registrar=implementation, market_index=index), len(config.positions),
    )
    encoded_parameters = encode_pool_bound_hook_parameters(parameters)
    actual_parameters, actual_salt = _call(client, collector_factory, POOL_FEE_COLLECTOR_FACTORY_V1_ABI, "poolBoundHookParameters", [implementation, token, _market_tuple(plan, index)], block)
    if actual_salt != salt or abi_encode([_tuple_type(POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1)], [actual_parameters]) != encoded_parameters:
        raise ValueError("pool-bound constructor tuple differs from the exact salt-normalized market economics")
    creation_code = _pool_bound_creation_code(client, topology, block)
    if keccak(creation_code + encoded_parameters) != init_code_hash:
        raise ValueError("pool-bound initcode hash differs from certified bytecode plus all 18 constructor fields")
    parameter_tuple = _struct_tuple(POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1, parameters)
    if _call(client, deployer, POOL_HOOK_DEPLOYER_V1_ABI, "initCodeHash", [parameter_tuple], block) != init_code_hash or _call(client, deployer, POOL_HOOK_DEPLOYER_V1_ABI, "predict", [parameter_tuple, salt], block).lower() != prediction.lower():
        raise ValueError("typed deployer initcode/prediction differs from exact local derivation")
    hook_code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [prediction, block.tag]))[2:])
    if hook_code:
        recorded_hash = _call(client, deployer, POOL_HOOK_DEPLOYER_V1_ABI, "deployedCodeHash", [prediction], block)
        if recorded_hash == bytes(32) or recorded_hash != keccak(hook_code):
            raise ValueError("existing pool-bound hook lacks exact typed-deployer runtime provenance")
        for name, expected in (("deploymentConfigHash", keccak(encoded_parameters)), ("marketCommitment", bytes.fromhex(hex_bytes(parameters.market_commitment)[2:]))):
            if _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, name, [], block) != expected:
                raise ValueError("existing pool-bound hook differs from its exact immutable economic commitment")
        for name, expected in (("poolManager", parameters.pool_manager), ("registrar", parameters.registrar), ("oracleFactory", parameters.oracle_factory), ("core", parameters.core), ("liquidityLocker", parameters.liquidity_locker), ("token", parameters.token)):
            if _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, name, [], block).lower() != expected.lower():
                raise ValueError("existing pool-bound hook differs from its exact immutable dependency graph")
        currency0, currency1 = sorted((token, market.quote_asset), key=lambda address: int(address, 16))
        pool_id = keccak(abi_encode(["address", "address", "uint24", "int24", "address"], [currency0, currency1, config.lp_fee_pips, config.tick_spacing, prediction]))
        if _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, "boundPoolId", [], block) != pool_id or _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, "openingSqrtPriceX96", [], block) != config.sqrt_price_x96 or _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, "expectedPositionCount", [], block) != len(config.positions):
            raise ValueError("existing pool-bound hook differs from its exact key, opening price or position count")
        if _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, "REQUIRED_HOOK_FLAGS", [], block) != V4_LIFECYCLE_HOOK_PERMISSIONS or _call(client, prediction, FIXED_FEE_POOL_HOOK_V1_ABI, "ALL_HOOK_MASK", [], block) != V4_LIFECYCLE_HOOK_PERMISSION_MASK:
            raise ValueError("existing pool-bound hook differs from its exact reviewed callback declarations")
    return PoolBoundHookDeployment(to_checksum_address(deployer), hex_bytes(init_code_hash), hex_bytes(salt), prediction), parameters


def _read_pool_bound_deployment(client: Web3, plan: LaunchPlanV1, market_index: int, block: LaunchBlock) -> tuple[PoolBoundHookDeployment, PoolBoundHookParametersV1]:
    _uint(market_index, 32, "marketIndex")
    if market_index >= len(plan.markets):
        raise ValueError("market_index is outside the economic plan")
    market = plan.markets[market_index]
    token = predict_launch_token(client, plan, block=block)
    registry = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block)
    if _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], block).lower() != plan.orchestrator.lower():
        raise ValueError("registry is not bound to the committed lifecycle orchestrator")
    required = LAUNCH_REQUIRED_CAPABILITIES_V1 | (LAUNCH_ERC404_CAPABILITY_V1 if int(plan.token.kind) == 1 else 0)
    adapter, profile, topology = _certified_profile(client, registry, plan.orchestrator, bytes.fromhex(hex_bytes(market.profile_id)[2:]), block, required)
    return _pool_bound_deployment(client, plan, market_index, token, adapter, profile, topology, block)


def read_pool_bound_hook_deployment(client: Web3, plan: LaunchPlanV1, *, market_index: int, block: LaunchBlock | None = None) -> PoolBoundHookDeployment:
    """Read exact metadata even with unmined salts or an already prepared pool."""
    _check_client_identity(client, plan)
    pinned = block or read_block(client)
    deployment, _ = _read_pool_bound_deployment(client, plan, market_index, pinned)
    assert_canonical(client, pinned)
    return deployment


def build_pool_bound_hook_deployment_transaction(client: Web3, plan: LaunchPlanV1, *, market_index: int) -> PoolBoundHookDeploymentTransaction:
    """Build optional permissionless predeployment without accepting arbitrary code."""
    _check_client_identity(client, plan)
    block = read_block(client)
    deployment, parameters = _read_pool_bound_deployment(client, plan, market_index, block)
    if not valid_pool_bound_hook_address(deployment.predicted_hook):
        raise ValueError("mine and finalize the pool-bound hook salt before predeployment")
    if hex_bytes(rpc(client, "eth_getCode", [deployment.predicted_hook, block.tag])) != "0x":
        raise ValueError("the exact pool-bound hook is already deployed")
    data = _encode_function(POOL_HOOK_DEPLOYER_V1_ABI, "deploy", [_struct_tuple(POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1, parameters), bytes.fromhex(deployment.salt[2:])])
    assert_canonical(client, block)
    return PoolBoundHookDeploymentTransaction(deployment.deployer, data, 0, deployment)


def _check_mining_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("pool-bound hook salt mining cancelled")


async def mine_pool_bound_hook_salt(*, deployer: str, init_code_hash: bytes | str, start_salt: int = 0, cancel_event: asyncio.Event | None = None, on_progress: Callable[[PoolBoundHookMiningProgress], None] | None = None) -> PoolBoundHookSalt:
    """Mine exact 0x1afc permission bits locally, yielding for cancellation/UI work."""
    deployer = _address(deployer, "deployer")
    init_hash = _abi_value({"name": "initCodeHash", "type": "bytes32"}, init_code_hash)
    counter = _uint(start_salt, 256, "startSalt")
    prefix = b"\xff" + bytes.fromhex(deployer[2:])
    attempts = 0
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
    raise ValueError("pool-bound hook salt search exhausted uint256 without a permissioned address")


async def prepare_pool_bound_lifecycle_plan(client: Web3, plan: LaunchPlanV1, *, cancel_event: asyncio.Event | None = None, on_progress: Callable[[PoolBoundHookMiningProgress], None] | None = None) -> PreparedPoolBoundLifecyclePlan:
    """Finalize only bound salts, then re-read every exact final deployment tuple."""
    to_launch_plan_tuple(plan)
    offerings = await asyncio.to_thread(read_lifecycle_profiles, client, orchestrator=plan.orchestrator, profile_ids=[market.profile_id for market in plan.markets])
    indices = [index for index, offering in enumerate(offerings) if offering.topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4]
    if not indices:
        return PreparedPoolBoundLifecyclePlan(plan, ())
    _check_mining_cancelled(cancel_event)
    await asyncio.to_thread(_check_client_identity, client, plan)
    progress = await asyncio.to_thread(read_launch_progress, client, plan)
    if progress.phase != LifecyclePhase.NONE:
        raise ValueError("a begun launch is already committed; pool-bound salt finalization is draft-only")
    initial_block = progress.head_block
    token_factory_binding = await asyncio.to_thread(_read_token_factory_binding, client, plan.orchestrator, initial_block)
    token = await asyncio.to_thread(predict_launch_token, client, plan, block=initial_block)
    markets = list(plan.markets)
    expected: dict[int, PoolBoundHookDeployment] = {}
    for index in indices:
        _check_mining_cancelled(cancel_event)
        draft = replace(plan, markets=tuple(markets))
        deployment = await asyncio.to_thread(read_pool_bound_hook_deployment, client, draft, market_index=index)
        if not valid_pool_bound_hook_address(deployment.predicted_hook):
            def report(item: PoolBoundHookMiningProgress) -> None:
                if on_progress is not None:
                    on_progress(replace(item, market_index=index))
            mined = await mine_pool_bound_hook_salt(deployer=deployment.deployer, init_code_hash=deployment.init_code_hash, start_salt=int(deployment.salt, 16), cancel_event=cancel_event, on_progress=report)
            config = decode_lifecycle_pool_bound_v4_market_config(markets[index].config)
            markets[index] = replace(markets[index], config=encode_lifecycle_pool_bound_v4_market_config(replace(config, hook_salt=mined.salt)))
            deployment = replace(deployment, salt=mined.salt, predicted_hook=mined.predicted_hook)
        expected[index] = deployment
    finalized = replace(plan, markets=tuple(markets))
    _check_mining_cancelled(cancel_event)
    block = await asyncio.to_thread(read_block, client)
    if await asyncio.to_thread(_read_token_factory_binding, client, finalized.orchestrator, block) != token_factory_binding:
        raise ValueError("token factory address or runtime code changed during pool-bound salt mining")
    if await asyncio.to_thread(predict_launch_token, client, finalized, block=block) != token:
        raise ValueError("hook salt finalization changed the deterministic launch-token identity")
    if (await asyncio.to_thread(_progress_values, client, finalized, block))[5] != int(LifecyclePhase.NONE):
        raise ValueError("the launch began while mining; its committed economic plan cannot be rewritten")
    deployments = []
    for index in indices:
        _check_mining_cancelled(cancel_event)
        actual = await asyncio.to_thread(read_pool_bound_hook_deployment, client, finalized, market_index=index, block=block)
        if actual != expected[index] or not valid_pool_bound_hook_address(actual.predicted_hook):
            raise ValueError("final pool-bound initcode, salt or CREATE2 prediction changed during mining")
        deployments.append(PoolBoundLifecycleDeployment(actual.deployer, actual.init_code_hash, actual.salt, actual.predicted_hook, index))
    await asyncio.to_thread(assert_canonical, client, block)
    _check_mining_cancelled(cancel_event)
    return PreparedPoolBoundLifecyclePlan(finalized, tuple(deployments))


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


def predict_launch_token(client: Web3, plan: LaunchPlanV1, *, block: LaunchBlock | None = None) -> str:
    """Predict a typed token-only draft, without requiring completed launch markets.

    Prediction is not economic validation or admission. Execution builders still
    require the complete plan through ``to_launch_plan_tuple``.
    """
    values = _struct_tuple(LAUNCH_PLAN_COMPONENTS_V1, plan)
    if plan.chain_id == 0:
        raise ValueError("prediction chain_id must be positive")
    _address(plan.creator, "creator", nonzero=True)
    _address(plan.orchestrator, "orchestrator", nonzero=True)
    pinned = block or read_block(client)
    result = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "predictToken", [values], pinned), "predicted token", nonzero=True)
    assert_canonical(client, pinned)
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


def decode_lifecycle_launch_receipt(data: bytes | str, plan: LaunchPlanV1) -> Mapping[str, Any]:
    values = abi_decode([_tuple_type(LAUNCH_RECEIPT_COMPONENTS_V1)], bytes.fromhex(hex_bytes(data)[2:]))[0]
    receipt = _json_tuple(LAUNCH_RECEIPT_COMPONENTS_V1, values)
    if receipt["launchId"] != launch_id_of(plan) or receipt["planHash"] != hash_launch_plan(plan):
        raise ValueError("launch receipt does not match the committed plan")
    if receipt["marketCount"] != len(plan.markets) or len(receipt["quoteSpent"]) != len(plan.buys) or len(receipt["tokenOut"]) != len(plan.buys):
        raise ValueError("launch receipt does not include every committed market and ordered buy")
    return receipt


@dataclass(frozen=True)
class LifecycleReceiptStatus:
    transaction_hash: str
    status: str
    block_number: int | None = None
    block_hash: str | None = None
    confirmations: int = 0
    events: tuple[LifecycleEvent, ...] = ()
    kind: str | None = None


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
    receipts: tuple[LifecycleReceiptStatus, ...] = ()


def _progress_values(client: Web3, plan: LaunchPlanV1, block: LaunchBlock) -> tuple[Any, ...]:
    values = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "readLaunchProgress", [bytes.fromhex(launch_id_of(plan)[2:])], block)
    phase = LifecyclePhase(values[5])
    if phase == LifecyclePhase.NONE:
        # An absent record is all zeros; a nonzero identity with phase None is
        # not accepted as an undeployed launch.
        empty = (b"\0" * 32, b"\0" * 32, ZERO_ADDRESS, 0, 0, 0, ZERO_ADDRESS, ZERO_ADDRESS, ZERO_ADDRESS, 0, 0, 0, 0, 0)
        if values != empty:
            raise ValueError("invalid absent launch progress")
        return tuple(values)
    if hex_bytes(values[0]) != launch_id_of(plan) or hex_bytes(values[1]) != hash_launch_plan(plan) or values[2].lower() != plan.creator.lower() or values[3] != plan.nonce:
        raise ValueError("canonical progress is bound to different launch economics or identity")
    if values[10] != len(plan.markets) or values[11] != len(plan.buys) or values[13] != plan.deadline or values[9] > values[10]:
        raise ValueError("canonical progress does not match the full committed plan")
    if values[4] not in (0, 1) or phase == LifecyclePhase.ACTIVATING:
        raise ValueError("invalid persisted launch mode/transaction-local phase")
    if phase in {LifecyclePhase.READY, LifecyclePhase.ACTIVE} and values[9] != values[10]:
        raise ValueError("canonical ready/active progress has incomplete markets")
    predicted = predict_launch_token(client, plan, block=block)
    if values[6].lower() != predicted.lower():
        raise ValueError("canonical token identity differs from deterministic prediction")
    return tuple(values)


def _check_client_identity(client: Web3, plan: LaunchPlanV1, account: str | None = None) -> None:
    if quantity(rpc(client, "eth_chainId", [])) != plan.chain_id:
        raise ValueError("RPC chain ID differs from the economic plan domain")
    if account is not None and _address(account, "account", nonzero=True).lower() != plan.creator.lower():
        raise ValueError("the current wallet account is not the committed creator/payer/refund recipient")

def _assert_confirmed_account_state(client: Web3, plan: LaunchPlanV1, progress: LaunchProgress) -> None:
    if progress.block.number != progress.head_block.number:
        confirmed = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, progress.block.tag]))
        current = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, progress.head_block.tag]))
        if confirmed != current:
            raise LaunchStateChanged("creator approvals/funding/commands are mined but not sufficiently confirmed")



def _receipt_status(client: Web3, plan: LaunchPlanV1, transaction_hash: str, head: LaunchBlock, confirmations: int) -> LifecycleReceiptStatus:
    transaction_hash = hex_bytes(transaction_hash)
    if len(transaction_hash) != 66:
        raise ValueError("transaction hash must be bytes32")
    raw = rpc(client, "eth_getTransactionReceipt", [transaction_hash])
    if raw is None:
        return LifecycleReceiptStatus(transaction_hash, "pending-or-replaced")
    number, block_hash = quantity(raw["blockNumber"]), hex_bytes(raw["blockHash"])
    canonical = rpc(client, "eth_getBlockByNumber", [hex(number), False])
    if canonical is None or hex_bytes(canonical["hash"]) != block_hash:
        return LifecycleReceiptStatus(transaction_hash, "reorged", number, block_hash)
    depth = max(0, head.number - number + 1)
    transaction = rpc(client, "eth_getTransactionByHash", [transaction_hash])
    if transaction is None or str(transaction.get("from", "")).lower() != plan.creator.lower():
        raise ValueError("receipt transaction is not from this committed creator")
    calldata = bytes.fromhex(hex_bytes(transaction.get("input", transaction.get("data", "0x")))[2:])
    target = str(transaction.get("to", "")).lower()
    if target != plan.orchestrator.lower():
        if quantity(transaction.get("value", "0x0")) != 0:
            raise ValueError("funding approval receipt must not transfer native value")
        inputs = {item.input_asset.lower() for item in plan.funding if int(item.kind) != 1 and int(item.input_asset, 16) != 0}
        if target not in inputs or calldata[:4] != keccak(text="approve(address,uint256)")[:4]:
            raise ValueError("receipt is neither this lifecycle command nor its funding approval")
        spender, amount = abi_decode(["address", "uint256"], calldata[4:])
        receipt_block = read_block(client, number)
        funding_escrow = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "fundingEscrow", [], receipt_block)
        if spender.lower() != funding_escrow.lower():
            raise ValueError("approval receipt is not bound to this core's funding escrow")
        # ERC20 approvals have no launchId: they are prerequisites only, never
        # evidence of an economic command or completed preparation step.
        status = "unconfirmed" if depth < confirmations else ("reverted" if quantity(raw["status"]) == 0 else "confirmed")
        return LifecycleReceiptStatus(transaction_hash, status, number, block_hash, depth, (), "approval-reset" if amount == 0 else "approval")
    commands = ("launchAtomic", "beginLaunch", "prepareMarkets", "activateLaunch", "cancelLaunch")
    matched = False
    for command in commands:
        entry = _entry(LAUNCH_LIFECYCLE_V1_ABI, command)
        types = [canonical_abi_type(item) for item in entry["inputs"]]
        if calldata[:4] != keccak(text=command + "(" + ",".join(types) + ")")[:4]:
            continue
        values = abi_decode(types, calldata[4:])
        if hex_bytes(keccak(abi_encode(["bytes32", LAUNCH_PLAN_V1_ABI_TYPE], [LAUNCH_PLAN_DOMAIN_V1, values[0]]))) != hash_launch_plan(plan):
            raise ValueError("receipt transaction encodes a different economic plan")
        matched = True
        break
    if not matched:
        raise ValueError("receipt is not a launch-specific lifecycle transaction")
    status = "unconfirmed" if depth < confirmations else ("reverted" if quantity(raw["status"]) == 0 else "confirmed")
    events = decode_lifecycle_events(plan, raw.get("logs", ())) if status == "confirmed" else ()
    return LifecycleReceiptStatus(transaction_hash, status, number, block_hash, depth, events, command)


def read_launch_progress(
    client: Web3, plan: LaunchPlanV1, *, confirmations: int = 1,
    transaction_hashes: Sequence[str] = (),
) -> LaunchProgress:
    """Recover canonical progress after reload/replacement/reorg; ignore counters."""
    to_launch_plan_tuple(plan)
    _check_client_identity(client, plan)
    if isinstance(confirmations, bool) or not isinstance(confirmations, int) or confirmations < 1:
        raise ValueError("confirmations must be a positive integer")
    head = read_block(client)
    block = head if confirmations == 1 else read_block(client, max(0, head.number - confirmations + 1))
    values = _progress_values(client, plan, block)
    current = values if block.number == head.number else _progress_values(client, plan, head)
    receipts = tuple(_receipt_status(client, plan, transaction_hash, head, confirmations) for transaction_hash in transaction_hashes)
    assert_canonical(client, block)
    assert_canonical(client, head)
    return LaunchProgress(
        launch_id=launch_id_of(plan), plan_hash=hash_launch_plan(plan), creator=_address(plan.creator, "creator"), nonce=plan.nonce,
        mode=None if values[5] == 0 else ("atomic" if values[4] == 0 else "staged"), phase=LifecyclePhase(values[5]),
        token=to_checksum_address(values[6]), fee_hub=to_checksum_address(values[7]), rewards=to_checksum_address(values[8]),
        prepared_markets=values[9], market_count=values[10], buy_count=values[11], position_count=values[12], deadline=values[13],
        block=block, head_block=head, awaiting_confirmations=current != values or any(receipt.status in {"unconfirmed", "pending-or-replaced"} for receipt in receipts), receipts=receipts,
    )


@dataclass(frozen=True)
class LifecycleApproval:
    asset: str
    spender: str
    required_amount: int
    allowance: int
    balance: int
    needs_approval: bool
    permit: str | None = None


@dataclass(frozen=True)
class LifecycleAdmission:
    """Exact execution admission, distinct from wallet transport acceptance."""

    admitted: bool
    confidence: str
    limits: LaunchExecutionLimits
    block_number: int
    block_hash: str
    account: str
    chain_id: int
    execution_proof: Literal["proved", "failed", "unavailable"]
    protocol_fit: Literal["proved", "failed", "unknown"]
    transport_preflight: Literal["not-requested", "passed", "failed"] = "not-requested"
    reason: str | None = None


@dataclass(frozen=True)
class LifecycleTransaction:
    step_id: str
    kind: str
    chain_id: int
    from_address: str
    to: str
    data: str
    value: int
    nonce: int
    dependencies: tuple[str, ...]
    postconditions: tuple[str, ...]
    first_market: int | None = None
    market_count: int | None = None
    gas_estimate: int | None = None
    gas_limit: int | None = None
    gas_price: int | None = None
    execution_fee: int | None = None
    maximum_execution_fee: int | None = None
    data_fee: int | None = None
    compute_gas_estimate: int | None = None
    poster_gas: int | None = None
    poster_fee: int | None = None
    data_fee_included_in_gas: bool = False
    total_fee: int | None = None
    gas_used: int | None = None
    confidence: str = "provisional"
    admission: LifecycleAdmission | None = None

    def as_transaction(self) -> dict[str, Any]:
        result: dict[str, Any] = {"chainId": self.chain_id, "from": self.from_address, "to": self.to, "data": self.data, "value": self.value, "nonce": self.nonce}
        if self.gas_limit is not None:
            result["gas"] = self.gas_limit
        if self.gas_price is not None:
            result["gasPrice"] = self.gas_price
        return result


@dataclass(frozen=True)
class LaunchSimulation:
    confidence: str
    admitted: bool
    block: LaunchBlock
    backend: str | None
    transactions: tuple[LifecycleTransaction, ...]
    reasons: tuple[str, ...]
    evidence: LaunchRpcSimulation
    unknown_constraints: tuple[str, ...] = ()
    execution_proof: Literal["proved", "failed", "unavailable"] = "unavailable"
    protocol_fit: Literal["proved", "failed", "unknown"] = "unknown"
    transport_preflight: Literal["not-requested", "passed", "failed"] = "not-requested"
    chain_id: int | None = None
    account: str | None = None


class LaunchSubmissionPreflightError(ValueError):
    """Read-only submission RPC refusal; no signature or write was requested."""

    code = "SUBMISSION_PREFLIGHT_FAILED"

    def __init__(self, reason: str, simulation: LaunchSimulation) -> None:
        self.simulation = replace(simulation, transport_preflight="failed", reasons=(*simulation.reasons, reason))
        super().__init__(reason)


@dataclass(frozen=True)
class PlannedLaunch:
    plan: LaunchPlanV1
    plan_hash: str
    launch_id: str
    predicted_token: str
    chain_id: int
    account: str
    mode: str
    transactions: tuple[LifecycleTransaction, ...]
    approvals: tuple[LifecycleApproval, ...]
    progress: LaunchProgress
    simulation: LaunchSimulation
    atomic_simulation: LaunchSimulation | None
    limits: LaunchExecutionLimits
    prepare_batch_size: int
    irreversible_costs: tuple[str, ...]
    market_admissions: tuple[Mapping[str, Any], ...] = ()
    limits_source: LaunchExecutionLimits | LifecycleLimitResolver | None = None
    confirmations: int = 1
    transaction_hashes: tuple[str, ...] = ()
    token_factory: str | None = None
    token_factory_code_hash: str | None = None

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

def _read_nitro_uint(client: Web3, block: LaunchBlock, target: str, abi: Sequence[Mapping[str, Any]], name: str, bits: int) -> int:
    data = _encode_function(abi, name, [])
    raw = hex_bytes(rpc(client, "eth_call", [{"to": target, "data": data}, block.tag]))
    if len(raw) != 66:
        raise ValueError(f"Nitro {name} must return exactly one ABI word")
    return _uint(abi_decode([f"uint{bits}"], bytes.fromhex(raw[2:]))[0], bits, f"Nitro {name}")


def _resolve_limits(client: Web3, source: LaunchExecutionLimits | LifecycleLimitResolver | None, block: LaunchBlock, account: str, chain_id: int, orchestrator: str) -> LaunchExecutionLimits:
    context = LaunchLimitContext(block, chain_id, _address(orchestrator, "orchestrator"), _address(account, "account"), getattr(client.provider, "endpoint_uri", None))
    limits = source(client, context) if callable(source) else source if source is not None else LaunchExecutionLimits()
    if not isinstance(limits, LaunchExecutionLimits):
        raise TypeError("a supplied execution-limit resolver must return LaunchExecutionLimits")
    if limits.observed_block_number is not None and limits.observed_block_number != block.number:
        raise ValueError("execution limits were observed at a different block number")
    if limits.observed_block_hash is not None and limits.observed_block_hash.lower() != block.block_hash:
        raise ValueError("execution limits were observed on a different canonical block")
    if limits.chain_id is not None and limits.chain_id != chain_id:
        raise ValueError("execution limits belong to a different chain")
    if limits.account is not None and _address(limits.account, "limits account").lower() != account.lower():
        raise ValueError("execution limits belong to a different account")
    if limits.orchestrator is not None and _address(limits.orchestrator, "limits orchestrator").lower() != orchestrator.lower():
        raise ValueError("execution limits belong to a different orchestrator")
    supplied_caps = [value for value in (
        limits.chain_transaction_gas_limit, limits.rpc_transaction_gas_limit,
        limits.account_transaction_gas_limit,
    ) if value is not None]
    if chain_id != ROBINHOOD_MAINNET_CHAIN_ID:
        ceiling = min([block.gas_limit, *supplied_caps])
        return replace(limits, protocol="evm", execution_gas_ceiling=ceiling, transaction_gas_ceiling=ceiling,
                       arb_os_version=None, max_tx_compute_gas=None, max_block_compute_gas=None)
    raw_version = _read_nitro_uint(client, block, NITRO_ARB_SYS_ADDRESS, NITRO_ARB_SYS_ABI, "arbOSVersion", 256)
    if raw_version < 105:
        raise ValueError("Nitro compute ceilings require actual ArbOS version 50 or above (ArbSys offset 55)")
    tx_cap = _read_nitro_uint(client, block, NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI, "getMaxTxGasLimit", 256)
    block_cap = _read_nitro_uint(client, block, NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI, "getMaxBlockGasLimit", 64)
    if tx_cap == 0 or block_cap == 0:
        raise ValueError("Nitro compute ceilings must be positive")
    assert_canonical(client, block)
    assert_chain(client, chain_id)
    return replace(limits, protocol="nitro", execution_gas_ceiling=min(tx_cap, block_cap),
                   transaction_gas_ceiling=min(supplied_caps) if supplied_caps else None,
                   arb_os_version=raw_version - 55, max_tx_compute_gas=tx_cap, max_block_compute_gas=block_cap)



def _mode(value: str) -> str:
    if value not in {"atomic", "staged"}:
        raise ValueError("mode must be explicitly 'atomic' or 'staged'; it is never selected silently")
    return value


def _native_value(plan: LaunchPlanV1) -> int:
    return sum(item.input_amount for item in plan.funding if int(item.kind) == 1 or (int(item.kind) == 2 and int(item.input_asset, 16) == 0))


def _live_admission(client: Web3, plan: LaunchPlanV1, progress: LaunchProgress, block: LaunchBlock) -> tuple[str, str, tuple[LifecycleApproval, ...], tuple[Mapping[str, Any], ...]]:
    if hex_bytes(rpc(client, "eth_getCode", [plan.orchestrator, block.tag])) == "0x":
        raise ValueError("the explicit lifecycle orchestrator has no deployed code")
    values = to_launch_plan_tuple(plan)
    if hex_bytes(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "hashPlan", [values], block)) != hash_launch_plan(plan):
        raise ValueError("the orchestrator does not implement the exact LaunchPlanV1 commitment")
    if hex_bytes(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "launchIdOf", [values], block)) != launch_id_of(plan):
        raise ValueError("the orchestrator launch domain differs from the plan")
    predicted = predict_launch_token(client, plan, block=block)
    for policy in plan.fee_assets:
        if policy.burn_bps and policy.asset.lower() != predicted.lower():
            raise ValueError("only the deterministic launch token can have a burn disposition")
        if policy.asset.lower() != predicted.lower():
            if hex_bytes(rpc(client, "eth_getCode", [policy.asset, block.tag])) == "0x":
                raise ValueError("a committed external fee asset has no contract code")
    if any(market.quote_asset.lower() == predicted.lower() for market in plan.markets):
        raise ValueError("a market cannot pair the launch token with itself")
    if predicted.lower() not in {policy.asset.lower() for policy in plan.fee_assets}:
        raise ValueError("feeAssets must include the exact deterministic launch token, not a sentinel")
    registry = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block), "registry", nonzero=True)
    if _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "core", [], block).lower() != plan.orchestrator.lower():
        raise ValueError("registry is not bound to the committed lifecycle orchestrator")
    spender = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "fundingEscrow", [], block), "funding escrow", nonzero=True)
    if block.timestamp > plan.deadline:
        raise ValueError("the committed launch deadline has expired; only cancellation is available")
    required = LAUNCH_REQUIRED_CAPABILITIES_V1 | (LAUNCH_ERC404_CAPABILITY_V1 if int(plan.token.kind) == 1 else 0)
    admissions: list[Mapping[str, Any]] = []
    resolved: list[tuple[Any, ...]] = []
    for index, market in enumerate(plan.markets):
        adapter_id = bytes.fromhex(hex_bytes(market.adapter_id)[2:])
        profile_id = bytes.fromhex(hex_bytes(market.profile_id)[2:])
        adapter, profile, topology = _certified_profile(client, registry, plan.orchestrator, profile_id, block, required)
        if profile[0] != adapter_id or adapter[3] != market.config_version:
            raise ValueError("market adapter/config version differs from its immutable approved profile")
        implementation = adapter[0]
        envelope, terms = _admit_market_config(client, registry, market, predicted, adapter, profile, topology, block, required)
        identity = _call(client, implementation, LAUNCH_MARKET_ADAPTER_V1_ABI, "resolve", [bytes.fromhex(launch_id_of(plan)[2:]), predicted, _market_tuple(plan, index)], block)
        _verify_market_identity(plan, predicted, index, identity)
        if identity[3].lower() != profile[4].lower() or (identity[2] if identity[0] == 0 else identity[3]).lower() != profile[3].lower():
            raise ValueError("resolved venue/factory differs from its immutable approved profile")
        deployment = None
        if topology.hook_topology == LifecycleHookTopology.POOL_BOUND_V4:
            deployment, _ = _pool_bound_deployment(client, plan, index, predicted, adapter, profile, topology, block)
            if identity[0] != 0 or identity[11].lower() != deployment.predicted_hook.lower() or not valid_pool_bound_hook_address(identity[11]):
                raise ValueError("resolved pool-bound hook differs from its exact certified deployment metadata")
        elif identity[11].lower() != profile[5].lower() or (identity[0] == 0 and topology.hook_topology != LifecycleHookTopology.SHARED_V4):
            raise ValueError("resolved hook differs from its immutable approved profile topology")
        if any(previous[1] == identity[1] for previous in resolved):
            raise ValueError("the plan repeats one canonical market")
        if identity[0] == 0 and any(previous[0] == 0 and plan.markets[prior_index].quote_asset.lower() == market.quote_asset.lower() for prior_index, previous in enumerate(resolved)):
            raise ValueError("one V4 market per quote is allowed across all offering IDs")
        resolved.append(tuple(identity))
        admissions.append({"marketIndex": index, "implementation": to_checksum_address(implementation), "adapter": _json_tuple(ADAPTER_REGISTRATION_COMPONENTS_V1, adapter), "profile": _json_tuple(PROFILE_REGISTRATION_COMPONENTS_V1, profile), "topology": _json_tuple(PROFILE_TOPOLOGY_COMPONENTS_V1, _struct_tuple(PROFILE_TOPOLOGY_COMPONENTS_V1, topology)), "identity": _json_tuple(MARKET_IDENTITY_COMPONENTS_V1, identity), "hookDeployment": deployment, "envelope": envelope, "developerTerms": terms})
        if hex_bytes(rpc(client, "eth_getCode", [market.quote_asset, block.tag])) == "0x":
            raise ValueError("a committed quote asset has no contract code")
    for item in plan.funding:
        if item.asset.lower() == predicted.lower() or item.input_asset.lower() == predicted.lower():
            raise ValueError("launch-token inventory cannot be counted as external funding")
        if int(item.kind) == 2 and int(item.input_asset, 16) != 0 and not _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "fundingInputAllowed", [item.input_asset], block):
            raise ValueError("a committed funding conversion input asset has been disabled")
        if hex_bytes(rpc(client, "eth_getCode", [item.asset, block.tag])) == "0x":
            raise ValueError("a funding output asset has no contract code")
        if int(item.kind) == 1:
            wrapped = _call(client, spender, LAUNCH_FUNDING_ESCROW_V1_ABI, "wrappedNative", [], block)
            if wrapped.lower() != item.asset.lower():
                raise ValueError("native-wrap funding does not use the bound wrapped native asset")
        elif int(item.kind) == 2:
            target_spender, code_hash, enabled = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "fundingTarget", [item.target], block)
            code = bytes.fromhex(hex_bytes(rpc(client, "eth_getCode", [item.target, block.tag]))[2:])
            if not enabled or int(target_spender, 16) == 0 or keccak(code) != code_hash:
                raise ValueError("the funding conversion target is no longer eligible")
    if progress.phase != LifecyclePhase.NONE:
        directory = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "directory", [], block)
        for index in range(progress.prepared_markets):
            adapter, prepared = _call(client, directory, LAUNCH_DIRECTORY_V1_ABI, "market", [bytes.fromhex(launch_id_of(plan)[2:]), index], block)
            _verify_market_identity(plan, predicted, index, prepared[0])
            if tuple(prepared[0]) != resolved[index]:
                raise ValueError("prepared canonical identity changed from the immutable resolved market")
            _call(client, adapter, LAUNCH_MARKET_ADAPTER_V1_ABI, "validatePrepared", [bytes.fromhex(launch_id_of(plan)[2:]), index, predicted, _market_tuple(plan, index), prepared[0]], block)
            state = _call(client, adapter, LAUNCH_MARKET_ADAPTER_V1_ABI, "readMarket", [bytes.fromhex(launch_id_of(plan)[2:]), index], block)
            if state[0] != prepared[0][12] or state[2] != 0 or state[3]:
                raise ValueError("a prepared market is no longer empty and at its committed opening state")
        needed: dict[str, int] = {}
        for buy in plan.buys:
            asset = plan.markets[buy.market_index].quote_asset.lower()
            needed[asset] = needed.get(asset, 0) + buy.quote_amount_in
        for asset, amount in needed.items():
            if _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "escrowBalance", [bytes.fromhex(launch_id_of(plan)[2:]), to_checksum_address(asset)], block) < amount:
                raise ValueError("launch-isolated escrow is below the remaining ordered buy budgets")
        return predicted, spender, (), tuple(admissions)
    requirements: dict[str, int] = {}
    for item in plan.funding:
        if int(item.kind) != 1 and int(item.input_asset, 16) != 0:
            asset = item.input_asset.lower()
            requirements[asset] = requirements.get(asset, 0) + item.input_amount
    approvals: list[LifecycleApproval] = []
    for asset, amount in sorted(requirements.items(), key=lambda pair: int(pair[0], 16)):
        address = to_checksum_address(asset)
        allowance = _call(client, address, _ERC20_READ_ABI, "allowance", [plan.creator, spender], block)
        balance = _call(client, address, _ERC20_READ_ABI, "balanceOf", [plan.creator], block)
        if balance < amount:
            raise ValueError("the creator lacks the committed per-asset funding input")
        approvals.append(LifecycleApproval(address, spender, amount, allowance, balance, allowance < amount))
    if quantity(rpc(client, "eth_getBalance", [plan.creator, block.tag])) < _native_value(plan):
        raise ValueError("the creator lacks the committed native funding value")
    return predicted, spender, tuple(approvals), tuple(admissions)


def _market_tuple(plan: LaunchPlanV1, index: int) -> tuple[Any, ...]:
    return to_launch_plan_tuple(plan)[7][index]


def _make_transactions(plan: LaunchPlanV1, mode: str, progress: LaunchProgress, approvals: Sequence[LifecycleApproval], groups: Sequence[tuple[int, int]], *, nonce: int, gas_cap: int, gas_price: int, cancel: bool = False) -> tuple[LifecycleTransaction, ...]:
    result: list[LifecycleTransaction] = []
    def add(kind: str, target: str, data: str, value: int, postconditions: tuple[str, ...], first: int | None = None, count: int | None = None) -> None:
        step_id = f"{len(result)}:{kind}" + (f":{first}:{count}" if first is not None else "")
        result.append(LifecycleTransaction(
            step_id, kind, plan.chain_id, _address(plan.creator, "creator"), _address(target, "target"), data, value, nonce + len(result),
            (result[-1].step_id,) if result else (), postconditions, first, count,
            gas_limit=gas_cap, gas_price=gas_price,
        ))
    if cancel:
        add("cancel", plan.orchestrator, build_lifecycle_calldata(plan, "cancelLaunch"), 0, ("Cancelled; only launch-isolated external funding refunded to creator", "Launch token stays inactive; committed burnOnCancel applies"))
        return tuple(result)
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        return ()
    if progress.phase == LifecyclePhase.NONE:
        for approval in approvals:
            if approval.needs_approval:
                if approval.allowance:
                    add("approval-reset", approval.asset, _encode_function(_ERC20_READ_ABI, "approve", [approval.spender, 0]), 0, ("Funding escrow allowance reset to zero",))
                add("approval", approval.asset, _encode_function(_ERC20_READ_ABI, "approve", [approval.spender, approval.required_amount]), 0, ("Exact aggregate committed funding input approved to funding escrow",))
        if mode == "atomic":
            add("atomic", plan.orchestrator, build_lifecycle_calldata(plan, "launchAtomic"), _native_value(plan), ("All positions permanently locked", "All committed buys execute in array order before public activation", "Active; launch-isolated refunds settled"))
            return tuple(result)
        add("begin", plan.orchestrator, build_lifecycle_calldata(plan, "beginLaunch"), _native_value(plan), ("Preparing; deterministic token/modules deployed and supply inactive", "Funding isolated; no positions seeded and no public pool open"))
    for first, count in groups:
        add("prepare", plan.orchestrator, build_lifecycle_calldata(plan, "prepareMarkets", first_market=first, count=count), 0, (f"Prepared contiguous markets [{first},{first + count}); empty at the committed opening state", "No duplicate steps or liquidity locked before activation"), first, count)
    add("activate", plan.orchestrator, build_lifecycle_calldata(plan, "activateLaunch"), 0, ("All positions minted and permanently locked before every buy", "Every buy succeeds in committed order before any public trading", "Active; fee sources frozen, refunds and inventory disposition settled"))
    return tuple(result)


def _postcondition_failure(plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], simulation: LaunchRpcSimulation, predicted: str) -> str | None:
    if len(simulation.calls) != len(transactions):
        return "the sequential simulation did not execute the complete transaction sequence"
    for transaction, call in zip(transactions, simulation.calls):
        if not call.success:
            return f"{transaction.kind} reverted: {call.error or call.return_data}"
        if transaction.kind.startswith("approval"):
            if call.return_data not in {"0x", "0x" + "00" * 31 + "01"}:
                return "the funding token approval did not report success"
            continue
        events = decode_lifecycle_events(plan, call.logs)
        if transaction.kind in {"begin", "atomic"}:
            begun = [event for event in events if event.name == "LaunchBegun"]
            if len(begun) != 1 or begun[0].args["token"].lower() != predicted.lower() or begun[0].args["mode"] != (0 if transaction.kind == "atomic" else 1):
                return "simulation did not prove the exact token/plan/mode begin postcondition"
        if transaction.kind in {"prepare", "atomic"}:
            prepared = [event.args["marketIndex"] for event in events if event.name == "MarketPrepared"]
            expected = list(range(len(plan.markets))) if transaction.kind == "atomic" else list(range(transaction.first_market, transaction.first_market + transaction.market_count))
            if prepared != expected:
                return "simulation did not prove every ordered preparation step exactly once"
        if transaction.kind in {"activate", "atomic"}:
            activated = [event for event in events if event.name == "LaunchActivated"]
            buys = [event for event in events if event.name == "InitialBuyExecuted"]
            if len(activated) != 1 or activated[0].args["token"].lower() != predicted.lower() or activated[0].args["marketCount"] != len(plan.markets) or len(buys) != len(plan.buys):
                return "simulation did not prove the whole launch activation postcondition"
            for index, (event, buy) in enumerate(zip(buys, plan.buys)):
                args = event.args
                if args["buyIndex"] != index or args["marketIndex"] != buy.market_index or args["quoteAsset"].lower() != plan.markets[buy.market_index].quote_asset.lower() or args["recipient"].lower() != buy.recipient.lower() or args["quoteSpent"] > buy.quote_amount_in or args["tokenOut"] < buy.min_token_out or event.log_index >= activated[0].log_index:
                    return "simulation did not prove each constrained buy before public activation"
        if transaction.kind == "cancel" and sum(event.name == "LaunchCancelled" for event in events) != 1:
            return "simulation did not prove pending-launch cancellation"
    return None


def _gas_starvation(transaction: LifecycleTransaction, call: LaunchSimulationCall, gas_limit: int) -> bool:
    """EIP-150 forwarded-gas starvation, not an economic revert.

    The transaction consumed essentially its entire forwarded-gas envelope
    (retaining the 1/64 EIP-150 remainder) without producing revert data.
    """
    if call.success or call.return_data != "0x" or call.gas_used < gas_limit - gas_limit // 64:
        return False
    message = str((call.error or {}).get("message", "")).lower() if call.error else ""
    return "revert" not in message


def _refused_overlimit(kind: str) -> tuple[str, ...]:
    indivisible = kind in {"activate", "atomic"}
    reason = "a measured transaction exceeds the current execution ceiling and cannot be split"
    return (reason + "; the final activation/launch is indivisible" if indivisible else (reason + "; only preparation groups may be partitioned"),)


def _launch_simulation(
    plan: LaunchPlanV1, limits: LaunchExecutionLimits, evidence: LaunchRpcSimulation,
    transactions: Sequence[LifecycleTransaction], reasons: Sequence[str] = (), *,
    admitted: bool = False, execution_proof: Literal["proved", "failed", "unavailable"] = "unavailable",
    protocol_fit: Literal["proved", "failed", "unknown"] = "unknown", unknown: tuple[str, ...] = (),
) -> LaunchSimulation:
    admission = LifecycleAdmission(
        admitted, evidence.confidence, limits, evidence.block.number, evidence.block.block_hash,
        _address(plan.creator, "creator"), plan.chain_id, execution_proof, protocol_fit,
        reason="; ".join(reasons) if reasons else None,
    )
    bound = tuple(replace(transaction, admission=admission) for transaction in transactions)
    return LaunchSimulation(
        evidence.confidence, admitted, evidence.block, evidence.backend, bound, tuple(reasons), evidence,
        unknown, execution_proof, protocol_fit, chain_id=plan.chain_id, account=admission.account,
    )


def _verify_nitro_backend(client: Web3, plan: LaunchPlanV1, block: LaunchBlock, limits: LaunchExecutionLimits, gas_price: int) -> None:
    if limits.arb_os_version is None or limits.max_tx_compute_gas is None or limits.max_block_compute_gas is None:
        raise ValueError("the native Nitro protocol context is incomplete")
    specifications = (
        (NITRO_ARB_SYS_ADDRESS, NITRO_ARB_SYS_ABI, "arbOSVersion", 256, limits.arb_os_version + 55),
        (NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI, "getMaxTxGasLimit", 256, limits.max_tx_compute_gas),
        (NITRO_ARB_GAS_INFO_ADDRESS, NITRO_ARB_GAS_INFO_ABI, "getMaxBlockGasLimit", 64, limits.max_block_compute_gas),
    )
    gas = min(limits.compute_cap(block), 100_000)
    calls = [
        {"from": plan.creator, "to": target, "data": _encode_function(abi, name, []),
         "value": "0x0", "gas": hex(gas), "gasPrice": hex(gas_price)}
        for target, abi, name, _, _ in specifications
    ]
    # This isolated capability probe proves native ArbOS execution only. Its
    # temporary balance is never used in lifecycle measurement/replay or funding
    # admission, so an exactly funded payer need not afford three probe calls.
    payload = {
        "blockStateCalls": [{
            "blockOverrides": {"number": hex(block.number + 1), "time": hex(block.timestamp + 1)},
            "stateOverrides": {plan.creator: {"balance": hex((1 << 256) - 1)}},
            "calls": calls,
        }],
        "validation": True, "traceTransfers": False,
    }
    if limits.rpc_total_simulation_gas_limit is not None and gas * len(calls) > limits.rpc_total_simulation_gas_limit:
        raise ValueError("the RPC aggregate simulation gas cap cannot fit the native Nitro capability probe")
    envelope = {"jsonrpc": "2.0", "id": 1, "method": "eth_simulateV1", "params": [payload, block.tag]}
    if limits.rpc_max_request_bytes is not None and len(json.dumps(envelope, separators=(",", ":")).encode()) > limits.rpc_max_request_bytes:
        raise ValueError("the RPC request-size cap cannot fit the native Nitro capability probe")
    assert_canonical(client, block)
    assert_chain(client, plan.chain_id)
    try:
        raw = rpc(client, "eth_simulateV1", [payload, block.tag])
    finally:
        assert_chain(client, plan.chain_id)
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        raise ValueError("the native Nitro capability probe returned an incomplete block")
    results = raw[0].get("calls")
    if not isinstance(results, list) or len(results) != len(specifications):
        raise ValueError("the native Nitro capability probe returned incomplete calls")
    for result, (_, _, name, bits, expected) in zip(results, specifications):
        returned = hex_bytes(result.get("returnData", "0x"))
        if quantity(result.get("status")) != 1 or len(returned) != 66:
            raise ValueError(f"the simulation backend cannot prove native Nitro {name}")
        actual = abi_decode([f"uint{bits}"], bytes.fromhex(returned[2:]))[0]
        if actual != expected:
            raise ValueError(f"the simulation backend Nitro {name} differs from the pinned protocol context")
    assert_canonical(client, block)
    assert_chain(client, plan.chain_id)


def _nitro_poster_budget(client: Web3, transaction: LifecycleTransaction, block: LaunchBlock) -> tuple[int, int]:
    request = rpc_transaction(transaction.as_transaction())
    request["to"] = NITRO_NODE_INTERFACE_ADDRESS
    request["data"] = _encode_function(
        NITRO_NODE_INTERFACE_ABI, "gasEstimateL1Component",
        [transaction.to, False, bytes.fromhex(hex_bytes(transaction.data)[2:])],
    )
    raw = hex_bytes(rpc(client, "eth_call", [request, block.tag]))
    if len(raw) != 194:
        raise ValueError("Nitro poster estimation must return exactly three ABI words")
    poster_gas, base_fee, _ = abi_decode(["uint64", "uint256", "uint256"], bytes.fromhex(raw[2:]))
    if base_fee == 0:
        raise ValueError("Nitro poster estimation returned a zero L2 base fee")
    return poster_gas, _uint(poster_gas * base_fee, 256, "Nitro poster fee")


def _simulate_sequence(client: Web3, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *, block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits, fork: ControlledLaunchFork | None, data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None) -> LaunchSimulation:
    assert_chain(client, plan.chain_id)
    unknown = limits.unknown_constraints()
    if limits.protocol == "evm" and data_fee_estimator is None:
        unknown = (*unknown, "data_fee")
    if not transactions:
        evidence = LaunchRpcSimulation("stateful", "none", block, ())
        return _launch_simulation(plan, limits, evidence, (), admitted=True, execution_proof="proved", protocol_fit="proved", unknown=unknown)
    nitro = limits.protocol == "nitro"
    if nitro:
        if fork is not None:
            reason = "a generic controlled fork cannot prove native Nitro ArbOS compute/poster metering"
            evidence = LaunchRpcSimulation("provisional", None, block, (), reason)
            return _launch_simulation(plan, limits, evidence, transactions, (reason,), unknown=unknown)
        try:
            _verify_nitro_backend(client, plan, block, limits, _uint(transactions[0].gas_price, 256, "gas price"))
        except LaunchStateChanged:
            raise
        except Exception as error:
            reason = f"native Nitro simulation capability is unavailable: {error}"
            evidence = LaunchRpcSimulation("provisional", None, block, (), reason)
            return _launch_simulation(plan, limits, evidence, transactions, (reason,), unknown=unknown)
    measured = simulate_transactions(client, [transaction.as_transaction() for transaction in transactions], block=block, limits=limits, fork=fork, validation=False)
    compute_cap = limits.compute_cap(block)
    envelope_cap = limits.gas_cap(block)
    if measured.confidence != "stateful" or not measured.successful:
        reasons = (measured.reason,) if measured.reason else tuple(f"{transaction.kind} reverted: {call.error or call.return_data}" for transaction, call in zip(transactions, measured.calls) if not call.success)
        execution = "failed" if any(not call.success for call in measured.calls) else "unavailable"
        return _launch_simulation(plan, limits, measured, transactions, reasons or ("the exact sequence could not be measured",), execution_proof=execution, unknown=unknown)
    buffered: list[LifecycleTransaction] = []
    compute_budgets: list[int] = []
    for transaction, call in zip(transactions, measured.calls):
        compute = call.gas_required if call.gas_required is not None else call.gas_used
        compute_budget = limits.buffered_gas(compute)
        compute_budgets.append(compute_budget)
        poster_gas, poster_fee = _nitro_poster_budget(client, transaction, block) if nitro else (0, 0)
        gas_limit = compute_budget + (limits.buffered_gas(poster_gas) if nitro else 0)
        gas_price = _uint(transaction.gas_price, 256, "gas price")
        maximum_fee = _uint(gas_limit * gas_price, 256, "maximum execution fee")
        buffered.append(replace(
            transaction, gas_estimate=_uint(compute + poster_gas, 256, "gas estimate"),
            compute_gas_estimate=compute if nitro else None, gas_used=call.gas_used,
            gas_limit=gas_limit, confidence="provisional", execution_fee=_uint(call.gas_used * gas_price, 256, "execution fee"),
            maximum_execution_fee=maximum_fee, poster_gas=poster_gas if nitro else None,
            poster_fee=poster_fee if nitro else None, data_fee=0 if nitro else None,
            data_fee_included_in_gas=nitro, total_fee=maximum_fee,
        ))
    if any(budget > compute_cap for budget in compute_budgets) or any(
        not 0 < transaction.gas_limit < (1 << 64) or transaction.gas_limit > envelope_cap
        for transaction in buffered
    ):
        return _launch_simulation(plan, limits, measured, buffered, ("a measured transaction does not fit current protocol/known policy with conservative headroom",), protocol_fit="failed", unknown=unknown)
    # Nitro discovery has zero child base fee and measures compute only. The
    # poster quote supplies a budget, never a subtraction-based compute proof.
    # Native validation replay charges actual poster gas and enforces ArbOS's
    # compute hold on the exact total envelope and actual sequential state.
    proof = simulate_transactions(client, [transaction.as_transaction() for transaction in buffered], block=block, limits=limits, fork=fork)
    failure = proof.reason if proof.confidence != "stateful" else _postcondition_failure(plan, buffered, proof, predicted)
    if failure is not None and proof.confidence == "stateful":
        failed_index = next((index for index, call in enumerate(proof.calls) if not call.success), None)
        if failed_index is not None and _gas_starvation(buffered[failed_index], proof.calls[failed_index], buffered[failed_index].gas_limit or 0):
            poster_budget = limits.buffered_gas(buffered[failed_index].poster_gas or 0) if nitro else 0
            ceiling = _uint(min(envelope_cap, compute_cap + poster_budget), 64, "replay gas ceiling")
            if ceiling > buffered[failed_index].gas_limit:
                replay = list(buffered)
                maximum_fee = _uint(ceiling * replay[failed_index].gas_price, 256, "maximum execution fee")
                replay[failed_index] = replace(replay[failed_index], gas_limit=ceiling, maximum_execution_fee=maximum_fee, total_fee=maximum_fee)
                retry = simulate_transactions(client, [item.as_transaction() for item in replay], block=block, limits=limits, fork=fork)
                if retry.confidence == "stateful":
                    retry_failure = _postcondition_failure(plan, replay, retry, predicted)
                    if retry_failure is None and retry.complete:
                        buffered, failure, proof = replay, None, retry
                    else:
                        failure = _refused_overlimit(replay[failed_index].kind)[0] + (f"; {retry_failure}" if retry_failure else "")
    if failure is not None or proof.confidence != "stateful":
        return _launch_simulation(plan, limits, proof, buffered, (failure or "the exact buffered sequence is unsupported",),
                                  execution_proof="failed" if proof.confidence == "stateful" else "unavailable",
                                  protocol_fit="unknown", unknown=unknown)
    buffered = [replace(transaction, gas_used=call.gas_used, execution_fee=_uint(call.gas_used * transaction.gas_price, 256, "execution fee"), confidence="stateful") for transaction, call in zip(buffered, proof.calls)]
    if not nitro and data_fee_estimator is not None:
        fees = [_uint(data_fee_estimator(client, transaction.as_transaction(), block), 256, "data fee") for transaction in buffered]
        buffered = [replace(transaction, data_fee=fee, total_fee=_uint((transaction.maximum_execution_fee or 0) + fee, 256, "total fee")) for transaction, fee in zip(buffered, fees)]
    balance = quantity(rpc(client, "eth_getBalance", [plan.creator, block.tag]))
    required = sum(transaction.value + (transaction.total_fee or 0) for transaction in buffered)
    assert_canonical(client, block)
    assert_chain(client, plan.chain_id)
    if required > balance:
        return _launch_simulation(plan, limits, proof, buffered, ("creator native balance cannot fund remaining value and execution/data fee envelopes",),
                                  execution_proof="proved", protocol_fit="proved", unknown=unknown)
    return _launch_simulation(plan, limits, proof, buffered, admitted=True, execution_proof="proved", protocol_fit="proved", unknown=unknown)


def _groups(first: int, total: int, batch_size: int) -> list[tuple[int, int]]:
    return [(index, min(batch_size, total - index)) for index in range(first, total, batch_size)]


def plan_launch(
    client: Web3, plan: LaunchPlanV1, *, account: str, mode: str,
    limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None, prepare_batch_size: int | None = None,
    confirmations: int = 1, transaction_hashes: Sequence[str] = (),
    fork: ControlledLaunchFork | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
) -> PlannedLaunch:
    """Resolve current eligibility, approvals and exact atomic/staged admission.

    Atomic is tried first for a new launch even when staged was explicitly
    chosen. Only preparation groups may be split; activation and its ordered
    buys remain one transaction. A provisional result is never admitted.
    """
    _mode(mode)
    to_launch_plan_tuple(plan)
    _check_client_identity(client, plan, account)
    limits_source = limits
    batch_size = len(plan.markets) if prepare_batch_size is None else _uint(prepare_batch_size, 32, "prepareBatchSize")
    if batch_size == 0:
        raise ValueError("prepare_batch_size must be positive")
    progress = read_launch_progress(client, plan, confirmations=confirmations, transaction_hashes=transaction_hashes)
    if progress.awaiting_confirmations:
        raise LaunchStateChanged("a lifecycle transition is mined but not sufficiently confirmed; do not duplicate it")
    if progress.mode is not None and progress.mode != mode:
        raise ValueError("the selected mode differs from canonical pending-launch execution metadata")
    block = progress.head_block
    limits = _resolve_limits(client, limits_source, block, _address(account, "account"), plan.chain_id, plan.orchestrator)
    predicted = predict_launch_token(client, plan, block=block)
    # Terminal launches are immutable; a head-only Active observation is not
    # confirmation-bound and never reaches this canonical branch.
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        empty = _simulate_sequence(client, plan, (), block=block, predicted=predicted, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
        return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, (), (), progress, empty, None, limits, batch_size, (), (), limits_source=limits_source, confirmations=confirmations, transaction_hashes=tuple(transaction_hashes))
    _assert_confirmed_account_state(client, plan, progress)
    predicted, _, approvals, admissions = _live_admission(client, plan, progress, block)
    token_factory, token_factory_code_hash = _read_token_factory_binding(client, plan.orchestrator, block)
    nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, block.tag]))
    pending_nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, "pending"]))
    gas_price = quantity(rpc(client, "eth_gasPrice", []))
    cap = min(limits.compute_cap(block), limits.gas_cap(block))
    groups = _groups(progress.prepared_markets, len(plan.markets), batch_size)
    transactions = _make_transactions(plan, mode, progress, approvals, groups, nonce=nonce, gas_cap=cap, gas_price=gas_price)
    if hex_bytes(rpc(client, "eth_getCode", [plan.creator, block.tag])) != "0x" or pending_nonce != nonce:
        reason = "direct contract-account execution is not a verified EOA route" if pending_nonce == nonce else "the account has unresolved pending/replaced transactions; wait and recover canonical state"
        evidence = LaunchRpcSimulation("provisional", None, block, (), reason)
        simulation = _launch_simulation(plan, limits, evidence, transactions, (reason,), unknown=limits.unknown_constraints())
        return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, transactions, approvals, progress, simulation, None, limits, batch_size, ("Preparation gas and deployed infrastructure cannot be recovered",), admissions, limits_source, token_factory=token_factory, token_factory_code_hash=token_factory_code_hash)
    atomic: LaunchSimulation | None = None
    if progress.phase == LifecyclePhase.NONE:
        atomic_transactions = _make_transactions(plan, "atomic", progress, approvals, (), nonce=nonce, gas_cap=cap, gas_price=gas_price)
        atomic = _simulate_sequence(client, plan, atomic_transactions, block=block, predicted=predicted, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
    if mode == "atomic":
        simulation = atomic
        if simulation is None:
            raise ValueError("atomic execution cannot resume an existing pending launch")
    else:
        while True:
            transactions = _make_transactions(plan, mode, progress, approvals, groups, nonce=nonce, gas_cap=cap, gas_price=gas_price)
            simulation = _simulate_sequence(client, plan, transactions, block=block, predicted=predicted, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
            if simulation.admitted or simulation.confidence != "stateful":
                break
            split_index = None
            for transaction_index, (transaction, call) in enumerate(zip(transactions, simulation.evidence.calls)):
                if transaction.kind != "prepare" or transaction.market_count <= 1:
                    continue
                reviewed = simulation.transactions[transaction_index]
                if limits.protocol == "nitro" and reviewed.compute_gas_estimate is not None:
                    required_gas = reviewed.compute_gas_estimate
                else:
                    required_gas = call.gas_required if call.gas_required is not None else call.gas_used
                gas_failure = limits.buffered_gas(required_gas) > limits.compute_cap(block) or (
                    reviewed.gas_limit is not None and reviewed.gas_limit > limits.gas_cap(block)
                ) or (not call.success and (required_gas >= cap or "out of gas" in str(call.error).lower()))
                if gas_failure:
                    split_index = next(index for index, group in enumerate(groups) if group[0] == transaction.first_market)
                    break
            if split_index is None and simulation.evidence.reason and len(simulation.evidence.calls) < len(transactions):
                failed = transactions[len(simulation.evidence.calls)]
                gas_error = any(marker in simulation.evidence.reason.lower() for marker in ("out of gas", "gas required exceeds", "exceeds the block gas", "intrinsic gas too high"))
                if gas_error and failed.kind == "prepare" and failed.market_count > 1:
                    split_index = next(index for index, group in enumerate(groups) if group[0] == failed.first_market)
            if split_index is None:
                break  # Never partition final activation/buys or hide economic failures.
            first, count = groups[split_index]
            left = count // 2
            groups[split_index:split_index + 1] = [(first, left), (first + left, count - left)]
    assert_canonical(client, block)
    assert_chain(client, plan.chain_id)
    return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, simulation.transactions, approvals, progress, simulation, atomic, limits, batch_size, ("Preparation gas and setup deployments are irreversible", "Cancellation refunds only unspent launch-isolated external funding, not launch-token inventory or gas"), admissions, limits_source, confirmations=confirmations, transaction_hashes=tuple(transaction_hashes), token_factory=token_factory, token_factory_code_hash=token_factory_code_hash)

def _assert_planned_identity(launch: PlannedLaunch) -> None:
    if hash_launch_plan(launch.plan) != hex_bytes(launch.plan_hash) or launch_id_of(launch.plan) != hex_bytes(launch.launch_id) or launch.chain_id != launch.plan.chain_id or launch.account.lower() != launch.plan.creator.lower():
        raise ValueError("the reviewed economic plan/identity changed; create a new explicit plan")


def _assert_hook_deployments(reviewed: PlannedLaunch, current: PlannedLaunch) -> None:
    if not current.simulation.transactions:
        return
    if reviewed.predicted_token.lower() != current.predicted_token.lower():
        raise ValueError("the reviewed deterministic token changed; create a new explicit plan")
    if reviewed.token_factory is None or reviewed.token_factory_code_hash is None or reviewed.token_factory != current.token_factory or reviewed.token_factory_code_hash != current.token_factory_code_hash:
        raise ValueError("the reviewed token factory address or runtime code changed; create a new explicit plan")
    previous = {item["marketIndex"]: item.get("hookDeployment") for item in reviewed.market_admissions}
    latest = {item["marketIndex"]: item.get("hookDeployment") for item in current.market_admissions}
    for index, market in enumerate(reviewed.plan.markets):
        if previous.get(index) is not None:
            if not isinstance(previous.get(index), PoolBoundHookDeployment) or previous[index] != latest.get(index):
                raise ValueError("the reviewed pool-bound deployer, initcode, salt or prediction changed; create a new explicit plan")



def simulate_launch_plan(client: Web3, launch: PlannedLaunch | LaunchPlanV1, *, account: str, mode: str | None = None, **options: Any) -> LaunchSimulation:
    """Resimulate from actual canonical state, never a stale/local step counter.

    A stored plan's submitted receipt evidence and confirmation depth are
    inherited exactly as build_next_transaction consumes them; resimulation
    never silently drops submitted-transaction context, so a missing receipt
    or unconfirmed/reorged submitted evidence keeps the result unadmitted.
    """
    if isinstance(launch, PlannedLaunch):
        _assert_planned_identity(launch)
        selected = launch.mode if mode is None else mode
        options.setdefault("limits", launch.limits_source if launch.limits_source is not None else launch.limits)
        options.setdefault("prepare_batch_size", launch.prepare_batch_size)
        options.setdefault("confirmations", launch.confirmations)
        options.setdefault("transaction_hashes", launch.transaction_hashes)
        plan = launch.plan
    else:
        if mode is None:
            raise ValueError("simulation requires an explicitly selected execution mode")
        selected, plan = mode, launch
    current = plan_launch(client, plan, account=account, mode=selected, **options)
    if isinstance(launch, PlannedLaunch):
        _assert_hook_deployments(launch, current)
    return current.simulation


def _submission_preflight(submission_client: Web3, transaction: LifecycleTransaction, simulation: LaunchSimulation) -> LifecycleTransaction:
    try:
        if quantity(rpc(submission_client, "eth_chainId", [])) != transaction.chain_id:
            raise ValueError("the submission RPC is connected to a different chain")
        if transaction.gas_limit is None:
            raise ValueError("the reviewed next transaction has no exact gas envelope")
        if _uint(transaction.gas_limit, 64, "reviewed gas envelope") == 0:
            raise ValueError("the reviewed next transaction gas must be a positive uint64")
        envelope = rpc_transaction(transaction.as_transaction())
        request = {key: envelope[key] for key in ("from", "to", "data", "value", "gas", "gasPrice") if key in envelope}
        estimate = quantity(rpc(submission_client, "eth_estimateGas", [request, "latest"]))
        if quantity(rpc(submission_client, "eth_chainId", [])) != transaction.chain_id:
            raise ValueError("the submission RPC chain changed during preflight")
        if not 0 < estimate <= transaction.gas_limit:
            raise ValueError("the submission RPC estimate exceeds the exact reviewed gas envelope")
    except Exception as error:
        raise LaunchSubmissionPreflightError(f"submission RPC preflight failed: {error}", simulation) from error
    if transaction.admission is None:
        raise LaunchSubmissionPreflightError("the next transaction has no bound execution admission", simulation)
    return replace(transaction, admission=replace(transaction.admission, transport_preflight="passed"))


def build_next_transaction(
    client: Web3, launch: PlannedLaunch | LaunchPlanV1, *, account: str,
    mode: str | None = None, action: str = "continue", limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    prepare_batch_size: int | None = None, confirmations: int | None = None,
    transaction_hashes: Sequence[str] | None = None, fork: ControlledLaunchFork | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
    submission_client: Web3 | None = None,
) -> LifecycleTransaction | None:
    """Read confirmed progress, revalidate and return only the next proven call.

    Replacements/reorgs are reconciled from canonical state. Terminal launches
    return None. Cancellation deliberately avoids disabled registry/adapters.
    An optional submission_client performs only a read-only latest-state
    estimate for this immediate call within its exact reviewed gas envelope.
    Execution admission does not guarantee wallet transport acceptance.
    """
    if action not in {"continue", "cancel"}:
        raise ValueError("action must be 'continue' or 'cancel'")
    if isinstance(launch, PlannedLaunch):
        _assert_planned_identity(launch)
        plan = launch.plan
        selected = launch.mode if mode is None else _mode(mode)
        if limits is None:
            limits = launch.limits_source if launch.limits_source is not None else launch.limits
        prepare_batch_size = launch.prepare_batch_size if prepare_batch_size is None else prepare_batch_size
        confirmations = launch.confirmations if confirmations is None else confirmations
        transaction_hashes = launch.transaction_hashes if transaction_hashes is None else transaction_hashes
    else:
        plan = launch
        if mode is None:
            raise ValueError("transaction building requires explicitly selected execution metadata")
        selected = _mode(mode)
    confirmations = 1 if confirmations is None else confirmations
    transaction_hashes = () if transaction_hashes is None else transaction_hashes
    _check_client_identity(client, plan, account)
    if action == "cancel":
        progress = read_launch_progress(client, plan, confirmations=confirmations, transaction_hashes=transaction_hashes)
        if progress.awaiting_confirmations:
            raise LaunchStateChanged("wait for the mined lifecycle transition before cancellation")
        if progress.phase not in {LifecyclePhase.PREPARING, LifecyclePhase.READY}:
            raise ValueError("only pending launches can be cancelled")
        if progress.mode != selected:
            raise ValueError("cancellation execution metadata differs from the pending launch")
        _assert_confirmed_account_state(client, plan, progress)
        block = progress.head_block
        limits = _resolve_limits(client, limits, block, _address(account, "account"), plan.chain_id, plan.orchestrator)
        nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, block.tag]))
        if quantity(rpc(client, "eth_getTransactionCount", [plan.creator, "pending"])) != nonce or hex_bytes(rpc(client, "eth_getCode", [plan.creator, block.tag])) != "0x":
            raise LaunchStateChanged("cancellation requires a settled direct EOA transaction context")
        transactions = _make_transactions(plan, selected, progress, (), (), nonce=nonce, gas_cap=min(limits.compute_cap(block), limits.gas_cap(block)), gas_price=quantity(rpc(client, "eth_gasPrice", [])), cancel=True)
        proof = _simulate_sequence(client, plan, transactions, block=block, predicted=progress.token, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
    else:
        planned = plan_launch(client, plan, account=account, mode=selected, limits=limits, prepare_batch_size=prepare_batch_size, confirmations=confirmations, transaction_hashes=transaction_hashes, fork=fork, data_fee_estimator=data_fee_estimator)
        if isinstance(launch, PlannedLaunch):
            _assert_hook_deployments(launch, planned)
        proof = planned.simulation
    if not proof.admitted:
        raise ValueError("the next transaction has no verified admission: " + "; ".join(proof.reasons))
    next_transaction = proof.transactions[0] if proof.transactions else None
    if next_transaction is not None and submission_client is not None:
        next_transaction = _submission_preflight(submission_client, next_transaction, proof)
        _check_client_identity(client, plan, account)
        assert_canonical(client, proof.block)
    return next_transaction


def _verify_market_identity(plan: LaunchPlanV1, token: str, index: int, identity: Sequence[Any]) -> None:
    venue, canonical_id, manager, factory, pool, pool_id, profile_id, currency0, currency1, fee, spacing, hook, opening_price = identity
    market = plan.markets[index]
    if venue not in (0, 1) or int(manager, 16) == 0 or opening_price <= 0 or spacing <= 0 or int(currency0, 16) >= int(currency1, 16) or {currency0.lower(), currency1.lower()} != {token.lower(), market.quote_asset.lower()} or hex_bytes(profile_id) != hex_bytes(market.profile_id):
        raise ValueError("directory market does not have the exact canonical currency/profile identity")
    expected = keccak(abi_encode(["uint256", "uint8", "address", "address", "address", "bytes32", "bytes32"], [plan.chain_id, venue, manager, factory, pool, pool_id, profile_id]))
    if canonical_id != expected:
        raise ValueError("directory canonical market ID is not chain/venue/manager/factory bound")
    if venue == 0:
        if int(factory, 16) != 0 or int(pool, 16) != 0 or int(hook, 16) == 0 or pool_id != keccak(abi_encode(["address", "address", "uint24", "int24", "address"], [currency0, currency1, fee, spacing, hook])):
            raise ValueError("V4 identity is not the full PoolKey at its authoritative singleton manager")
        if market.config_version not in (4, 5):
            raise ValueError("V4 identity requires a current reviewed config version")
        config = decode_lifecycle_pool_bound_v4_market_config(market.config) if market.config_version == 5 else decode_lifecycle_v4_market_config(market.config)
        if hex_bytes(config.profile_id) != hex_bytes(market.profile_id) or fee != config.lp_fee_pips or spacing != config.tick_spacing or opening_price != config.sqrt_price_x96 or not valid_pool_bound_hook_address(hook):
            raise ValueError("V4 identity differs from its committed reviewed economics or exact hook permissions")
    elif int(pool, 16) == 0 or int(factory, 16) == 0:
        raise ValueError("Abyss identity requires its actual pool and factory")


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
        _verify_market_identity(plan, progress.token, index, identity)
        if identity[0] == 0 and plan.markets[index].config_version == 5:
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


def read_lifecycle_author(client: Web3, *, registry: str, author_id: str, block: LaunchBlock | None = None) -> Mapping[str, Any]:
    """Read a stable author identity's live payout, not an envelope-frozen route."""
    registry = _address(registry, "registry", nonzero=True)
    author_id = _address(author_id, "authorId", nonzero=True)
    pinned = block or read_block(client)
    core, factory = _author_root(client, registry, pinned)
    payout = _address(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorPayout", [author_id], pinned), "payout")
    count = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorHubCount", [author_id], pinned)
    assert_canonical(client, pinned)
    return {"registry": registry, "core": core, "factory": factory, "author_id": author_id, "payout": payout, "known": int(payout, 16) != 0, "hub_count": count, "block": pinned}


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


def read_author_hubs(client: Web3, *, registry: str, author_id: str, offset: int = 0, limit: int = 100, block: LaunchBlock | None = None) -> Mapping[str, Any]:
    """Read bounded append-only canonical hubs, including retired source terms."""
    _page_bounds(offset, limit, 100)
    registry = _address(registry, "registry", nonzero=True)
    author_id = _address(author_id, "authorId", nonzero=True)
    pinned = block or read_block(client)
    _, factory = _author_root(client, registry, pinned)
    hubs, next_offset, total = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorHubs", [author_id, offset, limit], pinned)
    if offset > total or next_offset != offset + len(hubs) or next_offset != min(offset + limit, total) or len({hub.lower() for hub in hubs}) != len(hubs):
        raise ValueError("author discovery returned an invalid cursor or duplicate hub page")
    canonical = tuple(_canonical_author_hub(client, registry, factory, hub, pinned) for hub in hubs)
    assert_canonical(client, pinned)
    return {"registry": registry, "factory": factory, "author_id": author_id, "hubs": canonical, "offset": offset, "next_offset": next_offset, "total": total, "complete": next_offset == total, "block": pinned}


def read_developer_fees(client: Web3, *, registry: str, hub: str, author_id: str, assets: Sequence[str] = (), block: LaunchBlock | None = None) -> Mapping[str, Any]:
    """Read reserved credits and frozen source terms without harvest or admission."""
    registry = _address(registry, "registry", nonzero=True)
    author_id = _address(author_id, "authorId", nonzero=True)
    requested = _claim_assets(assets)
    pinned = block or read_block(client)
    _, factory = _author_root(client, registry, pinned)
    hub = _canonical_author_hub(client, registry, factory, hub, pinned)
    actual = tuple(_address(asset, "asset", nonzero=True) for asset in _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "assets", [], pinned))
    payout = _address(_call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, "authorPayout", [author_id], pinned), "payout")
    supported = {asset.lower() for asset in actual}
    rows = []
    for asset in requested or actual:
        is_supported = asset.lower() in supported
        amount = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "claimableDeveloperFees", [author_id, asset], pinned) if is_supported else None
        reserved = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "reservedDeveloperFees", [asset], pinned) if is_supported else None
        rows.append({"asset": asset, "supported": is_supported, "claimable": amount, "reserved": reserved})
    sources = []
    for source in _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "sources", [], pinned):
        values = _call(client, hub, LAUNCH_FEE_HUB_V3_ABI, "sourceTerms", [source], pinned)
        if values[3].lower() == author_id.lower():
            sources.append({"source": to_checksum_address(source), "terms": _json_tuple(SOURCE_TERMS_COMPONENTS_V3, values)})
    assert_canonical(client, pinned)
    return {"registry": registry, "factory": factory, "hub": hub, "author_id": author_id, "payout": payout, "assets": tuple(rows), "sources": tuple(sources), "block": pinned}


def _unsigned_fee_transaction(client: Web3, *, chain_id: int, account: str, to: str, abi: Sequence[Mapping[str, Any]], function: str, args: Sequence[Any]) -> dict[str, Any]:
    _uint(chain_id, 256, "chainId")
    if chain_id == 0 or quantity(rpc(client, "eth_chainId", [])) != chain_id:
        raise ValueError("unsigned fee transaction chainId differs from the connected chain")
    return {"chainId": chain_id, "from": _address(account, "account", nonzero=True), "to": _address(to, "to", nonzero=True), "data": _encode_function(abi, function, args), "value": 0}


def build_set_author_payout_transaction(client: Web3, *, registry: str, author_id: str, payout: str, account: str, chain_id: int) -> dict[str, Any]:
    """Build an unsigned admin/current-payout update; never freeze a new authorId."""
    state = read_lifecycle_author(client, registry=registry, author_id=author_id)
    if not state["known"]:
        raise ValueError("authorId has no registered payout/controller")
    account = _address(account, "account", nonzero=True)
    admin = _call(client, state["registry"], LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI,
        "admin", [], state["block"])
    if account.lower() not in (state["payout"].lower(), admin.lower()):
        raise ValueError("only the current payout/controller or registry admin can update author payout")
    assert_canonical(client, state["block"])
    payout = _address(payout, "payout", nonzero=True)
    return _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=registry, abi=LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI, function="setAuthorPayout", args=[state["author_id"], payout])


def build_claim_developer_fees_transaction(client: Web3, *, registry: str, hub: str, author_id: str, asset: str, account: str, chain_id: int) -> dict[str, Any]:
    """Build a permissionless direct claim; payout is always the live registry route."""
    registry = _address(registry, "registry", nonzero=True)
    author_id = _address(author_id, "authorId", nonzero=True)
    asset = _address(asset, "asset", nonzero=True)
    block = read_block(client)
    _, factory = _author_root(client, registry, block)
    hub = _canonical_author_hub(client, registry, factory, hub, block)
    transaction = _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=hub, abi=LAUNCH_FEE_HUB_V3_ABI, function="claimDeveloperFees", args=[author_id, asset])
    assert_canonical(client, block)
    return transaction


def build_claim_developer_fees_page_transaction(client: Web3, *, registry: str, author_id: str, offset: int, limit: int, account: str, chain_id: int, assets: Sequence[str] = ()) -> dict[str, Any]:
    """Build one 1..10-hub claim page; an empty asset list uses actual hub assets."""
    _page_bounds(offset, limit, 10)
    normalized = _claim_assets(assets)
    page = read_author_hubs(client, registry=registry, author_id=author_id, offset=offset, limit=limit)
    return _unsigned_fee_transaction(client, chain_id=chain_id, account=account, to=page["factory"], abi=LAUNCH_FEE_HUB_FACTORY_V3_ABI, function="claimDeveloperFeesPage", args=[page["author_id"], offset, limit, normalized])


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


def decode_developer_claim_receipt(receipt: Mapping[str, Any], *, transaction: Mapping[str, Any]) -> Mapping[str, Any]:
    """Decode a trusted builder's direct/page receipt; cursor completion is not payment."""
    target = _address(transaction["to"], "claim target", nonzero=True)
    raw = bytes.fromhex(hex_bytes(transaction["data"])[2:])
    entry = None
    for abi, name in ((LAUNCH_FEE_HUB_V3_ABI, "claimDeveloperFees"), (LAUNCH_FEE_HUB_FACTORY_V3_ABI, "claimDeveloperFeesPage")):
        candidate = _entry(abi, name)
        types = [canonical_abi_type(item) for item in candidate["inputs"]]
        if raw[:4] == keccak(text=name + "(" + ",".join(types) + ")")[:4]:
            entry = candidate
            arguments = abi_decode(types, raw[4:])
            if abi_encode(types, arguments) != raw[4:]:
                raise ValueError("claim transaction calldata is not canonical")
            break
    if entry is None or transaction.get("value") != 0:
        raise ValueError("receipt context must be an exact unsigned direct or page developer claim")
    sender = _address(transaction["from"], "claim sender", nonzero=True)
    if (not receipt.get("to") or receipt["to"].lower() != target.lower()
        or not receipt.get("from") or receipt["from"].lower() != sender.lower()):
        raise ValueError("developer receipt is from another transaction sender or target")
    status = quantity(receipt["status"])
    if status not in (0, 1):
        raise ValueError("developer claim receipt status must be 0 or 1")
    author = _address(arguments[0], "authorId", nonzero=True)
    page = entry["name"] == "claimDeveloperFeesPage"
    rows: list[Mapping[str, Any]] = []
    cursor = None
    if not status:
        if not page:
            rows.append({"hub": target, "asset": to_checksum_address(arguments[1]), "amount": 0, "status": 3, "outcome": "failed", "error_selector": "0x00000000", "retryable": True})
        return {"author_id": author, "kind": "page" if page else "direct", "transaction_succeeded": False, "rows": tuple(rows), "next_offset": None, "total": None, "cursor_complete": False, "payments_succeeded": False, "retryable_rows": tuple(rows), "retry_transaction": True}
    for log in receipt.get("logs", ()):
        if log.get("removed") or log.get("address", "").lower() != target.lower():
            continue
        if page:
            result = _claim_log(LAUNCH_FEE_HUB_FACTORY_V3_ABI, "DeveloperClaimResult", log)
            if result is not None:
                if result["authorId"].lower() != author.lower():
                    raise ValueError("developer result belongs to another stable author")
                row_status = result["status"]
                amount = result["amount"]
                error = hex_bytes(result["errorSelector"])
                if row_status not in (0, 1, 2, 3) or (row_status == 0) != (amount > 0) or (row_status in (0, 1) and error != "0x00000000") or (row_status == 2 and error != hex_bytes(keccak(text="UnsupportedAsset()")[:4])):
                    raise ValueError("developer result has inconsistent status, amount or error selector")
                rows.append({"hub": _address(result["hub"], "hub", nonzero=True), "asset": _address(result["asset"], "asset", nonzero=True), "amount": amount, "status": row_status, "outcome": ("paid", "zero", "unsupported", "failed")[row_status], "error_selector": error, "retryable": row_status == 3})
            decoded_cursor = _claim_log(LAUNCH_FEE_HUB_FACTORY_V3_ABI, "DeveloperClaimPage", log)
            if decoded_cursor is not None:
                if cursor is not None or decoded_cursor["authorId"].lower() != author.lower():
                    raise ValueError("developer page receipt has ambiguous or mismatched cursor")
                cursor = decoded_cursor
        else:
            result = _claim_log(LAUNCH_FEE_HUB_V3_ABI, "DeveloperFeesClaimed", log)
            if result is not None:
                if rows or result["authorId"].lower() != author.lower() or result["asset"].lower() != arguments[1].lower() or result["amount"] == 0:
                    raise ValueError("direct developer receipt differs from its exact author and asset")
                rows.append({"hub": target, "asset": to_checksum_address(result["asset"]), "payout": _address(result["payout"], "payout", nonzero=True), "amount": result["amount"], "status": 0, "outcome": "paid", "error_selector": "0x00000000", "retryable": False})
    if page:
        offset, limit = arguments[1:3]
        _page_bounds(offset, limit, 10)
        explicit = _claim_assets(arguments[3])
        if cursor is None or cursor["offset"] != offset or offset > cursor["total"] or cursor["nextOffset"] != min(offset + limit, cursor["total"]):
            raise ValueError("developer page receipt has an invalid or missing bounded cursor")
        if len({(row["hub"].lower(), row["asset"].lower()) for row in rows}) != len(rows):
            raise ValueError("developer page receipt repeats one hub/asset outcome")
        hubs = {row["hub"].lower() for row in rows}
        if len(rows) > 80 or len(hubs) != cursor["nextOffset"] - offset:
            raise ValueError("developer page outcomes do not cover their advanced hub cursor")
        if explicit:
            for hub in hubs:
                if {row["asset"].lower() for row in rows if row["hub"].lower() == hub} != {asset.lower() for asset in explicit}:
                    raise ValueError("developer page outcomes differ from the explicit asset list")
    return {"author_id": author, "kind": "page" if page else "direct", "transaction_succeeded": True, "outcome": "page" if page else "paid" if rows else "unobserved", "rows": tuple(rows), "next_offset": cursor["nextOffset"] if page else None, "total": cursor["total"] if page else None, "cursor_complete": cursor["nextOffset"] == cursor["total"] if page else None, "payments_succeeded": all(row["status"] in (0, 1) for row in rows) if page else bool(rows), "retryable_rows": tuple(row for row in rows if row["retryable"]), "retry_transaction": False}


__all__ = [
    "ControlledLaunchFork", "LaunchBlock", "LaunchExecutionLimits", "LaunchStateChanged",
    "LifecycleLimitResolver",
    "LaunchLimitContext", "create_controlled_launch_fork",
    "LAUNCH_PLAN_DOMAIN_V1", "LAUNCH_PLAN_V1_ABI_TYPE", "LAUNCH_REQUIRED_CAPABILITIES_V1",
    "LAUNCH_TOKEN_ONLY_CAPABILITY_V1", "LAUNCH_EMPTY_PREPARE_CAPABILITY_V1",
    "LAUNCH_PERMANENT_CUSTODY_CAPABILITY_V1", "LAUNCH_CANONICAL_FEES_CAPABILITY_V1",
    "LAUNCH_ERC404_CAPABILITY_V1", "LAUNCH_MULTI_POSITION_CAPABILITY_V1",
    "V4_LIFECYCLE_CONFIG_SCHEMA", "ABYSS_LIFECYCLE_CONFIG_SCHEMA", "V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA",
    "POOL_BOUND_MARKET_ECONOMICS_DOMAIN_V1", "V4_LIFECYCLE_HOOK_PERMISSION_MASK", "V4_LIFECYCLE_HOOK_PERMISSIONS",
    "NITRO_ARB_SYS_ADDRESS", "NITRO_ARB_GAS_INFO_ADDRESS", "NITRO_NODE_INTERFACE_ADDRESS",
    "LifecycleHookTopology", "ProfileTopologyV1", "LifecycleProfile",
    "LaunchBoundsV2", "LaunchGraphV2", "LaunchEnvelopeV2", "LifecycleDeveloperTerms",
    "LifecyclePoolBoundV4MarketConfig", "PoolBoundHookParametersV1",
    "PoolBoundHookDeployment", "PoolBoundLifecycleDeployment", "PoolBoundHookMiningProgress", "PoolBoundHookSalt",
    "PreparedPoolBoundLifecyclePlan", "PoolBoundHookDeploymentTransaction",
    "encode_lifecycle_pool_bound_v4_market_config", "decode_lifecycle_pool_bound_v4_market_config",
    "encode_pool_bound_hook_parameters", "pool_bound_market_commitment", "predict_pool_bound_hook_address", "valid_pool_bound_hook_address",
    "read_lifecycle_profiles", "read_pool_bound_hook_deployment", "mine_pool_bound_hook_salt",
    "prepare_pool_bound_lifecycle_plan", "build_pool_bound_hook_deployment_transaction",
    "LaunchPlanV1", "LaunchProgress", "LaunchSimulation", "PlannedLaunch",
    "LifecycleTokenKind", "LifecycleRewardMode", "LifecycleFundingKind", "LifecyclePhase",
    "LifecycleTokenConfig", "LifecycleAssetFunding", "LifecycleFeeAssetPolicy", "LifecycleMarketConfig", "LifecycleInitialBuy",
    "LifecycleV4Position", "LifecycleV4MarketConfig", "LifecycleAbyssPosition", "LifecycleAbyssMarketConfig",
    "LifecycleApproval", "LifecycleTransaction", "LifecycleEvent", "LifecycleReceiptStatus",
    "LifecycleAdmission", "LaunchSubmissionPreflightError",
    "launch_plan_from_dict", "launch_plan_to_dict", "to_launch_plan_tuple", "encode_launch_plan", "hash_launch_plan", "launch_id_of",
    "encode_lifecycle_v4_market_config", "decode_lifecycle_v4_market_config", "encode_lifecycle_abyss_market_config", "build_lifecycle_calldata",
    "predict_launch_token", "decode_lifecycle_events", "decode_lifecycle_launch_receipt",
    "plan_launch", "simulate_launch_plan", "build_next_transaction", "read_launch_progress", "read_launch_markets", "preview_lifecycle_fees",
    "read_lifecycle_author", "build_set_author_payout_transaction", "read_author_hubs", "read_developer_fees",
    "build_claim_developer_fees_transaction", "build_claim_developer_fees_page_transaction", "decode_developer_claim_receipt",
]
