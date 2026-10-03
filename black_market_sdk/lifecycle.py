"""Explicit opt-in, receipt-driven LaunchPlanV1 planning and execution builders.

Callers provide the new orchestrator and creator. This module neither selects
production addresses nor signs/broadcasts transactions. An execution mode is
always explicit and never changes the economic commitment. Dependent admission
requires actual sequential execution, not disconnected calls to future pools.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any, Literal

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address
from web3 import Web3

from .lifecycle_abis import (
    ABYSS_MARKET_CONFIG_COMPONENTS_V1, LAUNCH_DIRECTORY_V1_ABI, LAUNCH_FEE_HUB_V2_ABI,
    LAUNCH_FUNDING_ESCROW_V1_ABI, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI,
    LAUNCH_LIFECYCLE_V1_ABI, LAUNCH_MARKET_ADAPTER_V1_ABI, LAUNCH_PLAN_COMPONENTS_V1,
    LAUNCH_PROGRESS_COMPONENTS_V1, LAUNCH_RECEIPT_COMPONENTS_V1,
    ADAPTER_REGISTRATION_COMPONENTS_V1, MARKET_IDENTITY_COMPONENTS_V1,
    PROFILE_REGISTRATION_COMPONENTS_V1, V4_MARKET_CONFIG_COMPONENTS_V2,
)
from .lifecycle_rpc import (
    ControlledLaunchFork, LaunchBlock, LaunchExecutionLimits, LaunchLimitContext, LaunchRpcSimulation,
    LaunchStateChanged, assert_canonical, create_controlled_launch_fork, hex_bytes, quantity, read_block, rpc, simulate_transactions,
)

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
LAUNCH_PLAN_DOMAIN_V1 = keccak(text="BLACK_MARKET_LAUNCH_PLAN_V1")
LAUNCH_TOKEN_ONLY_CAPABILITY_V1 = 1
LAUNCH_EMPTY_PREPARE_CAPABILITY_V1 = 2
LAUNCH_PERMANENT_CUSTODY_CAPABILITY_V1 = 8
LAUNCH_CANONICAL_FEES_CAPABILITY_V1 = 16
LAUNCH_ERC404_CAPABILITY_V1 = 32
LAUNCH_MULTI_POSITION_CAPABILITY_V1 = 64
# TOKEN_ONLY|EMPTY_PREPARE|PERMANENT_CUSTODY|CANONICAL_FEES. The retired POOL_GATE
# bit is never requested: preactivation safety is the launch token's transfer
# restrictions plus the activation-time canonical opening-state verification on
# both canonical venues. Atomic and staged share this mask; mode changes
# execution grouping, never venue eligibility.
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
    version: Literal[2]
    lp_fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    hook_fee_pips: int
    fee_mode: int
    protocol_fee_denominator: int
    treasury: str
    external_liquidity_disabled: bool
    oracle_config_id: bytes | str
    positions: tuple[LifecycleV4Position, ...]


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
V4_LIFECYCLE_PROFILE_ID = keccak(text="black-market.v4-lifecycle-market.v2")
V4_LIFECYCLE_CONFIG_SCHEMA = keccak(text=_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V2))


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
    if not 1 <= len(plan.fee_assets) <= 8 or plan.executor_fee_bps >= 10_000:
        raise ValueError("fee assets must number 1..8 and executorFeeBps must be below 10000")
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
    for market in plan.markets:
        _address(market.quote_asset, "quoteAsset", nonzero=True)
        if market.token_budget == 0 or market.config_version == 0 or hex_bytes(market.adapter_id) == ZERO_HASH or hex_bytes(market.profile_id) == ZERO_HASH:
            raise ValueError("market budget, immutable IDs and config version must be nonzero")
        if not 1 <= len(bytes.fromhex(hex_bytes(market.config)[2:])) <= 16_384:
            raise ValueError("market config must fit its nonempty bounded versioned schema")
        if market.quote_asset.lower() not in fee_assets:
            raise ValueError("every market quote must belong to the committed fee-asset set")
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
    values = _struct_tuple(V4_MARKET_CONFIG_COMPONENTS_V2, config)
    if values[0] != 2:
        raise ValueError("V4 lifecycle market config version must be 2")
    return abi_encode([_tuple_type(V4_MARKET_CONFIG_COMPONENTS_V2)], [values])


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
    """Predict from domain+token economics; draft fee assets need not include it yet."""
    pinned = block or read_block(client)
    result = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "predictToken", [to_launch_plan_tuple(plan)], pinned), "predicted token", nonzero=True)
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
    gas_used: int | None = None
    confidence: str = "provisional"

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

def _resolve_limits(client: Web3, source: LaunchExecutionLimits | LifecycleLimitResolver | None, block: LaunchBlock, account: str, chain_id: int, orchestrator: str) -> tuple[LaunchExecutionLimits, str | None]:
    try:
        context = LaunchLimitContext(block, chain_id, _address(orchestrator, "orchestrator"), _address(account, "account"), getattr(client.provider, "endpoint_uri", None))
        limits = source(client, context) if callable(source) else source
        if limits is None:
            return LaunchExecutionLimits(), None
        if not isinstance(limits, LaunchExecutionLimits):
            raise TypeError("the execution-limit resolver must return LaunchExecutionLimits")
        if limits.observed_block_number is not None and limits.observed_block_number != block.number:
            raise ValueError("execution limits were observed at a different block number")
        if limits.observed_block_hash is not None and limits.observed_block_hash.lower() != block.block_hash:
            raise ValueError("execution limits were observed on a different canonical block")
        if limits.chain_id is not None and limits.chain_id != chain_id:
            raise ValueError("execution limits belong to a different chain")
        if limits.account is not None and limits.account.lower() != account.lower():
            raise ValueError("execution limits belong to a different account")
        if limits.orchestrator is not None and limits.orchestrator.lower() != orchestrator.lower():
            raise ValueError("execution limits belong to a different orchestrator")
        return limits, None
    except Exception as error:
        # Keep progress/cancellation discovery usable, but never turn missing
        # limit authority into successful submission admission.
        return LaunchExecutionLimits(), f"execution limits could not be resolved: {type(error).__name__}"



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
            registry = _call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block)
            if not _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "assetAllowed", [policy.asset], block):
                raise ValueError("a committed external fee asset has been disabled")
    if any(market.quote_asset.lower() == predicted.lower() for market in plan.markets):
        raise ValueError("a market cannot pair the launch token with itself")
    if predicted.lower() not in {policy.asset.lower() for policy in plan.fee_assets}:
        raise ValueError("feeAssets must include the exact deterministic launch token, not a sentinel")
    registry = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "registry", [], block), "registry", nonzero=True)
    spender = _address(_call(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI, "fundingEscrow", [], block), "funding escrow", nonzero=True)
    if block.timestamp > plan.deadline:
        raise ValueError("the committed launch deadline has expired; only cancellation is available")
    required = LAUNCH_REQUIRED_CAPABILITIES_V1 | (LAUNCH_ERC404_CAPABILITY_V1 if int(plan.token.kind) == 1 else 0)
    admissions: list[Mapping[str, Any]] = []
    resolved: list[tuple[Any, ...]] = []
    for index, market in enumerate(plan.markets):
        adapter_id = bytes.fromhex(hex_bytes(market.adapter_id)[2:])
        profile_id = bytes.fromhex(hex_bytes(market.profile_id)[2:])
        implementation = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "requireEligible", [adapter_id, profile_id, market.config_version, required], block)
        adapter = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "adapter", [adapter_id], block)
        profile = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "profile", [profile_id], block)
        identity = _call(client, implementation, LAUNCH_MARKET_ADAPTER_V1_ABI, "resolve", [bytes.fromhex(launch_id_of(plan)[2:]), predicted, _market_tuple(plan, index)], block)
        _verify_market_identity(plan, predicted, index, identity)
        if identity[3].lower() != profile[4].lower() or identity[11].lower() != profile[5].lower() or (identity[2] if identity[0] == 0 else identity[3]).lower() != profile[3].lower():
            raise ValueError("resolved venue/factory/hook differs from its immutable approved profile")
        if any(previous[1] == identity[1] for previous in resolved):
            raise ValueError("the plan repeats one canonical market")
        resolved.append(tuple(identity))
        admissions.append({"marketIndex": index, "implementation": to_checksum_address(implementation), "adapter": _json_tuple(ADAPTER_REGISTRATION_COMPONENTS_V1, adapter), "profile": _json_tuple(PROFILE_REGISTRATION_COMPONENTS_V1, profile), "identity": _json_tuple(MARKET_IDENTITY_COMPONENTS_V1, identity)})
        if not _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "assetAllowed", [market.quote_asset], block):
            raise ValueError("a committed quote asset has been disabled")
    for item in plan.funding:
        if item.asset.lower() == predicted.lower() or item.input_asset.lower() == predicted.lower():
            raise ValueError("launch-token inventory cannot be counted as external funding")
        if int(item.kind) == 2 and int(item.input_asset, 16) != 0 and not _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "assetAllowed", [item.input_asset], block):
            raise ValueError("a committed funding conversion input asset has been disabled")
        if not _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "assetAllowed", [item.asset], block):
            raise ValueError("a funding output asset has been disabled")
        if int(item.kind) == 1:
            wrapped = _call(client, spender, LAUNCH_FUNDING_ESCROW_V1_ABI, "wrappedNative", [], block)
            if wrapped.lower() != item.asset.lower():
                raise ValueError("native-wrap funding does not use the bound wrapped native asset")
        elif int(item.kind) == 2:
            target_spender, code_hash, enabled = _call(client, registry, LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI, "fundingTarget", [item.target], block)
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


def _simulate_sequence(client: Web3, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *, block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits, fork: ControlledLaunchFork | None, data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None) -> LaunchSimulation:
    if not transactions:
        evidence = LaunchRpcSimulation("stateful", "none", block, ())
        return LaunchSimulation("stateful", True, block, "none", (), (), evidence)
    measured = simulate_transactions(client, [transaction.as_transaction() for transaction in transactions], block=block, limits=limits, fork=fork, validation=False)
    cap = limits.gas_cap(block)
    if measured.confidence != "stateful" or not measured.successful:
        reasons = (measured.reason,) if measured.reason else tuple(f"{transaction.kind} reverted: {call.error or call.return_data}" for transaction, call in zip(transactions, measured.calls) if not call.success)
        return LaunchSimulation(measured.confidence, False, block, measured.backend, tuple(transactions), reasons or ("the exact sequence could not be measured",), measured)
    if not measured.complete:
        missing = transactions[len(measured.calls):]
        return LaunchSimulation(measured.confidence, False, block, measured.backend, tuple(transactions), tuple(f"{transaction.kind} could not be measured against actual preceding state" for transaction in missing), measured)
    estimates = [limits.buffered_gas(call.gas_required if call.gas_required is not None else call.gas_used) for call in measured.calls]
    if any(estimate > cap for estimate in estimates):
        return LaunchSimulation("stateful", False, block, measured.backend, tuple(transactions), ("a measured transaction does not fit current limits with conservative headroom",), measured)
    buffered = tuple(replace(transaction, gas_estimate=call.gas_required if call.gas_required is not None else call.gas_used, gas_used=call.gas_used, gas_limit=estimate, confidence="provisional", execution_fee=call.gas_used * transaction.gas_price, maximum_execution_fee=estimate * transaction.gas_price) for transaction, call, estimate in zip(transactions, measured.calls, estimates))
    # Re-execute the exact buffered gas limits. gasUsed alone is not a proof
    # of sufficient forwarded gas (EIP-150) or the final transaction envelope.
    proof = simulate_transactions(client, [transaction.as_transaction() for transaction in buffered], block=block, limits=limits, fork=fork)
    failure = proof.reason if proof.confidence != "stateful" else _postcondition_failure(plan, buffered, proof, predicted)
    if failure is not None and proof.confidence == "stateful":
        failed_index = next((index for index, call in enumerate(proof.calls) if not call.success), None)
        if failed_index is not None and _gas_starvation(buffered[failed_index], proof.calls[failed_index], buffered[failed_index].gas_limit or 0):
            ceiling = cap if cap > buffered[failed_index].gas_limit else None
            if ceiling is not None and ceiling > buffered[failed_index].gas_limit:
                replay = list(buffered)
                replay[failed_index] = replace(replay[failed_index], gas_limit=ceiling, maximum_execution_fee=ceiling * replay[failed_index].gas_price)
                retry = simulate_transactions(client, [item.as_transaction() for item in replay], block=block, limits=limits, fork=fork)
                if retry.confidence == "stateful":
                    retry_failure = _postcondition_failure(plan, replay, retry, predicted)
                    if retry_failure is None and len(retry.calls) == len(replay) and retry.calls[failed_index].success:
                        buffered = tuple(replace(item, gas_used=call.gas_used, execution_fee=call.gas_used * item.gas_price, confidence="stateful", gas_estimate=call.gas_required if call.gas_required is not None else item.gas_estimate) for item, call in zip(replay, retry.calls))
                        failure, proof = None, retry
                    else:
                        failure = _refused_overlimit(replay[failed_index].kind)[0] + (f"; {retry_failure}" if retry_failure else "")
                # A discovery RPC error keeps the original failure unchanged.
    if failure is not None or proof.confidence != "stateful":
        return LaunchSimulation(proof.confidence, False, block, proof.backend, buffered, (failure or "the exact buffered sequence is unsupported",), proof)
    buffered = tuple(replace(transaction, gas_used=call.gas_used, execution_fee=call.gas_used * transaction.gas_price, confidence="stateful") for transaction, call in zip(buffered, proof.calls))
    if data_fee_estimator is not None:
        fees = [_uint(data_fee_estimator(client, transaction.as_transaction(), block), 256, "data fee") for transaction in buffered]
        buffered = tuple(replace(transaction, data_fee=fee) for transaction, fee in zip(buffered, fees))
    balance = quantity(rpc(client, "eth_getBalance", [plan.creator, block.tag]))
    required = sum(transaction.value + (transaction.maximum_execution_fee or 0) + (transaction.data_fee or 0) for transaction in buffered)
    if required > balance:
        assert_canonical(client, block)
        return LaunchSimulation("stateful", False, block, proof.backend, buffered, ("creator native balance cannot fund remaining value and execution/data fee envelopes",), proof)
    assert_canonical(client, block)
    unknown = limits.unknown_constraints()
    reasons = ("applicable execution/calldata constraints are unknown: " + ", ".join(unknown),) if unknown else ()
    return LaunchSimulation("stateful", not unknown, block, proof.backend, buffered, reasons, proof, unknown)


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
    limits, limits_failure = _resolve_limits(client, limits_source, block, _address(account, "account"), plan.chain_id, plan.orchestrator)
    predicted = predict_launch_token(client, plan, block=block)
    # Terminal launches are immutable; a head-only Active observation is not
    # confirmation-bound and never reaches this canonical branch.
    if progress.phase in {LifecyclePhase.ACTIVE, LifecyclePhase.CANCELLED}:
        empty = _simulate_sequence(client, plan, (), block=block, predicted=predicted, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
        return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, (), (), progress, empty, None, limits, batch_size, (), (), limits_source=limits_source, confirmations=confirmations, transaction_hashes=tuple(transaction_hashes))
    _assert_confirmed_account_state(client, plan, progress)
    predicted, _, approvals, admissions = _live_admission(client, plan, progress, block)
    nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, block.tag]))
    pending_nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, "pending"]))
    gas_price = quantity(rpc(client, "eth_gasPrice", []))
    cap = limits.gas_cap(block)
    groups = _groups(progress.prepared_markets, len(plan.markets), batch_size)
    transactions = _make_transactions(plan, mode, progress, approvals, groups, nonce=nonce, gas_cap=cap, gas_price=gas_price)
    if hex_bytes(rpc(client, "eth_getCode", [plan.creator, block.tag])) != "0x" or pending_nonce != nonce:
        reason = "direct contract-account execution is not a verified EOA route" if pending_nonce == nonce else "the account has unresolved pending/replaced transactions; wait and recover canonical state"
        evidence = LaunchRpcSimulation("provisional", None, block, (), reason)
        simulation = LaunchSimulation("provisional", False, block, None, transactions, (reason,), evidence)
        return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, transactions, approvals, progress, simulation, None, limits, batch_size, ("Preparation gas and deployed infrastructure cannot be recovered",), admissions, limits_source)
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
            for transaction, call in zip(transactions, simulation.evidence.calls):
                if transaction.kind != "prepare" or transaction.market_count <= 1:
                    continue
                required_gas = call.gas_required if call.gas_required is not None else call.gas_used
                gas_failure = limits.buffered_gas(required_gas) > cap or (not call.success and (required_gas >= cap or "out of gas" in str(call.error).lower()))
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
    if limits_failure is not None:
        simulation = replace(simulation, admitted=False, reasons=(*simulation.reasons, limits_failure))
        if atomic is not None:
            atomic = replace(atomic, admitted=False, reasons=(*atomic.reasons, limits_failure))
    assert_canonical(client, block)
    return PlannedLaunch(plan, hash_launch_plan(plan), launch_id_of(plan), predicted, plan.chain_id, _address(account, "account"), mode, simulation.transactions, approvals, progress, simulation, atomic, limits, batch_size, ("Preparation gas and setup deployments are irreversible", "Cancellation refunds only unspent launch-isolated external funding, not launch-token inventory or gas"), admissions, limits_source, confirmations=confirmations, transaction_hashes=tuple(transaction_hashes))

def _assert_planned_identity(launch: PlannedLaunch) -> None:
    if hash_launch_plan(launch.plan) != hex_bytes(launch.plan_hash) or launch_id_of(launch.plan) != hex_bytes(launch.launch_id) or launch.chain_id != launch.plan.chain_id or launch.account.lower() != launch.plan.creator.lower():
        raise ValueError("the reviewed economic plan/identity changed; create a new explicit plan")



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
    return plan_launch(client, plan, account=account, mode=selected, **options).simulation


def build_next_transaction(
    client: Web3, launch: PlannedLaunch | LaunchPlanV1, *, account: str,
    mode: str | None = None, action: str = "continue", limits: LaunchExecutionLimits | LifecycleLimitResolver | None = None,
    prepare_batch_size: int | None = None, confirmations: int | None = None,
    transaction_hashes: Sequence[str] | None = None, fork: ControlledLaunchFork | None = None,
    data_fee_estimator: Callable[[Web3, Mapping[str, Any], LaunchBlock], int] | None = None,
) -> LifecycleTransaction | None:
    """Read confirmed progress, revalidate and return only the next proven call.

    Replacements/reorgs are reconciled from canonical state. Terminal launches
    return None. Cancellation deliberately avoids disabled registry/adapters.
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
        limits, limits_failure = _resolve_limits(client, limits, block, _address(account, "account"), plan.chain_id, plan.orchestrator)
        nonce = quantity(rpc(client, "eth_getTransactionCount", [plan.creator, block.tag]))
        if quantity(rpc(client, "eth_getTransactionCount", [plan.creator, "pending"])) != nonce or hex_bytes(rpc(client, "eth_getCode", [plan.creator, block.tag])) != "0x":
            raise LaunchStateChanged("cancellation requires a settled direct EOA transaction context")
        transactions = _make_transactions(plan, selected, progress, (), (), nonce=nonce, gas_cap=limits.gas_cap(block), gas_price=quantity(rpc(client, "eth_gasPrice", [])), cancel=True)
        proof = _simulate_sequence(client, plan, transactions, block=block, predicted=progress.token, limits=limits, fork=fork, data_fee_estimator=data_fee_estimator)
        if limits_failure is not None:
            proof = replace(proof, admitted=False, reasons=(*proof.reasons, limits_failure))
    else:
        planned = plan_launch(client, plan, account=account, mode=selected, limits=limits, prepare_batch_size=prepare_batch_size, confirmations=confirmations, transaction_hashes=transaction_hashes, fork=fork, data_fee_estimator=data_fee_estimator)
        proof = planned.simulation
    if not proof.admitted:
        raise ValueError("the next transaction has no verified admission: " + "; ".join(proof.reasons))
    return proof.transactions[0] if proof.transactions else None


def _verify_market_identity(plan: LaunchPlanV1, token: str, index: int, identity: Sequence[Any]) -> None:
    venue, canonical_id, manager, factory, pool, pool_id, profile_id, currency0, currency1, fee, spacing, hook, _ = identity
    market = plan.markets[index]
    if venue not in (0, 1) or int(currency0, 16) >= int(currency1, 16) or {currency0.lower(), currency1.lower()} != {token.lower(), market.quote_asset.lower()} or hex_bytes(profile_id) != hex_bytes(market.profile_id):
        raise ValueError("directory market does not have the exact canonical currency/profile identity")
    expected = keccak(abi_encode(["uint256", "uint8", "address", "address", "address", "bytes32", "bytes32"], [plan.chain_id, venue, manager, factory, pool, pool_id, profile_id]))
    if canonical_id != expected:
        raise ValueError("directory canonical market ID is not chain/venue/manager/factory bound")
    if venue == 0:
        if int(pool, 16) != 0 or pool_id != keccak(abi_encode(["address", "address", "uint24", "int24", "address"], [currency0, currency1, fee, spacing, hook])):
            raise ValueError("V4 identity is not the full PoolKey at its authoritative singleton manager")
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
    payments = _call(client, _address(hub, "hub", nonzero=True), LAUNCH_FEE_HUB_V2_ABI, "claimAndSplit", [], pinned, sender=_address(executor, "executor", nonzero=True))
    result = tuple({"asset": to_checksum_address(asset), "amount": amount} for asset, amount in payments)
    assert_canonical(client, pinned)
    return result


__all__ = [
    "ControlledLaunchFork", "LaunchBlock", "LaunchExecutionLimits", "LaunchStateChanged",
    "LifecycleLimitResolver",
    "LaunchLimitContext", "create_controlled_launch_fork",
    "LAUNCH_PLAN_DOMAIN_V1", "LAUNCH_PLAN_V1_ABI_TYPE", "LAUNCH_REQUIRED_CAPABILITIES_V1",
    "LAUNCH_TOKEN_ONLY_CAPABILITY_V1", "LAUNCH_EMPTY_PREPARE_CAPABILITY_V1",
    "LAUNCH_PERMANENT_CUSTODY_CAPABILITY_V1", "LAUNCH_CANONICAL_FEES_CAPABILITY_V1",
    "LAUNCH_ERC404_CAPABILITY_V1", "LAUNCH_MULTI_POSITION_CAPABILITY_V1",
    "LaunchPlanV1", "LaunchProgress", "LaunchSimulation", "PlannedLaunch",
    "LifecycleTokenKind", "LifecycleRewardMode", "LifecycleFundingKind", "LifecyclePhase",
    "LifecycleTokenConfig", "LifecycleAssetFunding", "LifecycleFeeAssetPolicy", "LifecycleMarketConfig", "LifecycleInitialBuy",
    "LifecycleV4Position", "LifecycleV4MarketConfig", "LifecycleAbyssPosition", "LifecycleAbyssMarketConfig",
    "LifecycleApproval", "LifecycleTransaction", "LifecycleEvent", "LifecycleReceiptStatus",
    "launch_plan_from_dict", "launch_plan_to_dict", "to_launch_plan_tuple", "encode_launch_plan", "hash_launch_plan", "launch_id_of",
    "encode_lifecycle_v4_market_config", "encode_lifecycle_abyss_market_config", "build_lifecycle_calldata",
    "predict_launch_token", "decode_lifecycle_events", "decode_lifecycle_launch_receipt",
    "plan_launch", "simulate_launch_plan", "build_next_transaction", "read_launch_progress", "read_launch_markets", "preview_lifecycle_fees",
]
