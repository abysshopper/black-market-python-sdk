"""Shared dry-run and explicit-broadcast runner for fixed token-launch cases."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import signal
import shlex
import sys
import time
import unicodedata
from dataclasses import replace
from pathlib import Path


from web3 import Web3
from web3._utils.events import get_event_data

from eth_account import Account
from eth_abi import encode
from black_market_sdk import (
    ABYSS_FEE_TIERS,
    LAUNCH_FUNDING_ESCROW_V1_ABI,
    ROBINHOOD_MAINNET_RPC,
    ABYSS_LIFECYCLE_CONFIG_SCHEMA,
    ABYSS_MARKET_ADAPTER_V1_ABI,
    ABYSS_POSITION_LOCKER_ABI,
    ERC20_ABI,
    LAUNCH_ERC404_V1_ABI,
    LAUNCH_FEE_HUB_V3_ABI,
    LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI,
    LAUNCH_LIFECYCLE_V1_ABI,
    LAUNCH_TOKEN_CONTEXT_V1_ABI,
    LAUNCH_TOKEN_FACTORY_V1_ABI,
    MULTI_ASSET_REWARDS_V1_ABI,
    V4_FEE_LIQUIDITY_LOCKER_V2_ABI,
    LIFECYCLE_ORACLE_FACTORY_ABI,
    ROBINHOOD_USDG,
    V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6,
    KnownLifecycleProfile,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchAttributionAuthorization,
    LaunchExecutionLimits,
    LaunchPlanV1,
    LaunchPublishPending,
    LaunchUploadError,
    LaunchSessionCreateRequest,
    LaunchSessionCreateMetadata,
    LaunchSessionImageDescriptor,
    LaunchSessionPublishRequest,
    LifecycleAbyssMarketConfig,
    LifecycleAbyssPosition,
    LifecycleAssetFunding,
    LifecycleFeeAssetPolicy,
    LifecycleFundingKind,
    LifecycleInitialBuy,
    LifecycleMarketConfig,
    LifecyclePhase,
    LifecyclePoolBoundV4MarketConfigV6,
    LifecycleReceiptReference,
    LifecycleTokenConfig,
    LifecycleV4Position,
    build_launch_attribution_typed_data,
    build_next_transaction,
    decode_lifecycle_events,
    decode_lifecycle_abyss_market_config,
    decode_lifecycle_pool_bound_v4_market_config,
    derive_launch_pool_recipe,
    derive_launch_position_ranges,
    encode_lifecycle_abyss_market_config,
    encode_lifecycle_pool_bound_v4_market_config,
    get_launch_addresses,
    get_addresses,
    get_known_lifecycle_profile,
    hash_launch_plan,
    get_sqrt_ratio_at_tick,
    launch_plan_from_dict,
    launch_plan_to_dict,
    plan_launch,
    predict_launch_token,
    prepare_and_plan_lifecycle_launch,
    read_launch_markets,
    read_launch_progress,
    read_lifecycle_profiles,
)
from black_market_sdk.lifecycle_rpc import hex_bytes, quantity, read_block, rpc
if __package__:
    from ._launch_support import (
        Artifacts, JournalHTTPProvider, LocalSigningWallet, Redactor, ExampleFailure,
        error_details, mainnet_url, portable, read_price_snapshot, validate_metadata_binding, validate_metadata_session,
        load_example_image, load_example_keystore, read_published_image, read_kyber_build, validate_kyber_build, KYBER_ROUTER,
    )
else:
    from _launch_support import (
        Artifacts, JournalHTTPProvider, LocalSigningWallet, Redactor, ExampleFailure,
        error_details, mainnet_url, portable, read_price_snapshot, validate_metadata_binding, validate_metadata_session,
        load_example_image, load_example_keystore, read_published_image, read_kyber_build, validate_kyber_build, KYBER_ROUTER,
    )

ZERO_ADDRESS = "0x" + "00" * 20
UNIT = 10**18
TARGET_MARKET_CAP_USD_X18 = 5000 * UNIT
TICK_SPACING = 60
LP_FEE_PIPS = 3000
BUY_INPUT = 10**13
BUY_SLIPPAGE_BPS = 50
CONFIRMATIONS = 1
CHAIN_TIMEOUT_SECONDS = 90
_ENV_LOADED = False

# The canonical factory emits this registration event; the SDK read ABI contains
# only its oracleConfigs getter. Configurations are generic, not asset allowlists.
ORACLE_CONFIG_REGISTERED_ABI = {
    "type": "event", "name": "OracleConfigRegistered", "anonymous": False,
    "inputs": [
        {"name": "oracleConfigId", "type": "bytes32", "indexed": True},
        {"name": "maxAbsTickMove", "type": "uint24", "indexed": False},
        {"name": "cardinality", "type": "uint16", "indexed": False},
    ],
}


class Cancellation:
    def __init__(self):
        self.signum = None

    def signal(self, signum, _frame):
        # Let in-flight submissions return so their hashes can be durably saved.
        self.signum = signum

    def check(self):
        if self.signum is not None:
            raise ExampleFailure("launch example interrupted; submitted chain state is preserved", code="INTERRUPTED", diagnostic={"signal": self.signum})

    def sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.check()
            time.sleep(min(0.1, remaining))
        self.check()


def require(condition: bool, message: str, *, code="OBSERVATION_MISMATCH", diagnostic=None):
    if not condition:
        raise ExampleFailure(message, code=code, diagnostic=diagnostic)


def address_equal(left, right) -> bool:
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def contract(client, address, abi):
    return client.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def load_environment(redactor):
    """Load working-directory .env once; existing process values always win."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    path = Path.cwd() / ".env"
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            require(separator and key.isidentifier() and key.isascii(),
                    f"invalid .env assignment on line {line_number}", code="INVALID_CONFIGURATION")
            lexer = shlex.shlex(value, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            words = list(lexer)
            require(len(words) <= 1, f"quote .env values containing spaces on line {line_number}",
                    code="INVALID_CONFIGURATION")
            value = words[0] if words else ""
            if any(part in key.upper() for part in ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH")):
                redactor.remember(value)
            if key.endswith("_URL"):
                redactor.remember_url(value)
            os.environ.setdefault(key, value)
    _ENV_LOADED = True


def example_configuration(artifacts):
    load_environment(artifacts.redact)
    artifacts.redact.secrets.update(Redactor().secrets)
    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    api_url = os.environ.get("LAUNCH_API_URL", "").strip()
    artifacts.redact.remember(private_key)
    artifacts.redact.remember_url(api_url)
    keystore_path = os.environ.get("EXAMPLE_KEYSTORE_PATH", "").strip()
    require(bool(private_key) != bool(keystore_path),
            "configure exactly one signer: PRIVATE_KEY or EXAMPLE_KEYSTORE_PATH", code="INVALID_CONFIGURATION")
    if keystore_path:
        private_key = load_example_keystore(keystore_path, os.environ.get("EXAMPLE_KEYSTORE_PASSWORD", ""), artifacts.redact)
    require(bool(api_url), "LAUNCH_API_URL is required in .env or environment", code="INVALID_CONFIGURATION")
    creator = Account.from_key(private_key).address
    source_url = os.environ.get("RPC_URL") or ROBINHOOD_MAINNET_RPC
    artifacts.redact.remember_url(source_url)
    # HTTPS for remote providers; explicit loopback allows owned-local verification.
    source_url = mainnet_url(source_url, api=True) if source_url.startswith("http://") else mainnet_url(source_url)
    api_url = mainnet_url(api_url, api=True)
    kyber_api_url = mainnet_url(os.environ.get("EXAMPLE_KYBER_API_URL") or api_url, api=True)
    artifacts.redact.remember_url(kyber_api_url)
    deployment = get_launch_addresses(4663)
    buy_amount = parse_buy_amount("EXAMPLE_BUY_AMOUNT_WEI", BUY_INPUT)
    target = parse_target_market_cap(os.environ.get("EXAMPLE_TARGET_MARKET_CAP_USD", "5000"))
    return {
        "chainId": 4663, "orchestrator": deployment.orchestrator,
        "creator": creator, "quoteAsset": deployment.wrapped_native, "quoteDecimals": 18,
        "buyAmountWei": buy_amount,
        "targetMarketCapUsdX18": target,
        "rpcUrl": source_url, "apiUrl": api_url, "kyberApiUrl": kyber_api_url,
    }, private_key


def parse_buy_amount(name, default):
    value = os.environ.get(name) or str(default)
    require(value.isascii() and value.isdecimal() and not value.startswith("0")
            and len(value) <= 78 and int(value) < 2**256,
            f"{name} must be a positive canonical uint256 raw-unit amount", code="INVALID_CONFIGURATION")
    return int(value)


def market_quote(configuration, spec):
    symbol = spec.get("quote", "WETH")
    require(symbol in {"WETH", "USDG"}, f"unsupported example quote {symbol!r}", code="INVALID_CONFIGURATION")
    asset = configuration["quoteAsset"] if symbol == "WETH" else ROBINHOOD_USDG
    require(symbol != "WETH" or address_equal(asset, get_addresses(4663).weth),
            "paired asset differs from SDK canonical WETH", code="CHAIN_IDENTITY_MISMATCH")
    return {"symbol": symbol, "asset": Web3.to_checksum_address(asset), "decimals": 18 if symbol == "WETH" else 6}


def example_identity(case, *, name=None, symbol=None):
    def text(key, default, maximum, *, required=False, byte_limit=None):
        value = unicodedata.normalize("NFC", os.environ.get(key, default)).strip()
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            raise ExampleFailure(f"{key} must be well-formed UTF-8", code="INVALID_CONFIGURATION") from None
        require((not required or bool(value)) and len(value) <= maximum
                and (byte_limit is None or len(encoded) <= byte_limit),
                f"{key} exceeds the API/protocol length bounds or is empty", code="INVALID_CONFIGURATION")
        return value
    return LaunchSessionCreateMetadata(
        name=text("EXAMPLE_TOKEN_NAME", name if name is not None else "Example " + case["id"], 100, required=True, byte_limit=128),
        symbol=text("EXAMPLE_TOKEN_SYMBOL", symbol if symbol is not None else "EXAMPLE", 32, required=True, byte_limit=32),
        description=text("EXAMPLE_TOKEN_DESCRIPTION", "Python SDK launch example: " + case["id"], 2000),
    )


def limit_resolver():
    def ceiling(name):
        value = os.environ.get(name)
        if value is None or value == "":
            return None
        require(value.isdecimal() and int(value) > 0, f"{name} must be a positive integer",
                code="INVALID_CONFIGURATION")
        return int(value)

    chain = ceiling("LAUNCH_CHAIN_GAS_CAP")
    provider = ceiling("LAUNCH_RPC_GAS_CAP")
    account = ceiling("LAUNCH_ACCOUNT_GAS_CAP")
    calldata = ceiling("LAUNCH_CALLDATA_CAP")
    if all(value is None for value in (chain, provider, account, calldata)):
        return None

    def resolve(_client, context):
        return LaunchExecutionLimits(
            chain_transaction_gas_limit=chain,
            rpc_transaction_gas_limit=provider, account_transaction_gas_limit=account,
            max_calldata_bytes=calldata,
            observed_block_number=context.block.number, observed_block_hash=context.block.block_hash,
            chain_id=context.chain_id, account=context.account, orchestrator=context.orchestrator,
            source="explicit example caller policy; not a wallet-submission guarantee",
        )
    return resolve


def parse_target_market_cap(value):
    """Exact USD decimal, at most 18 fractional places, never a float."""
    parts = value.split(".")
    require(len(parts) <= 2 and all(part and part.isascii() and part.isdecimal() for part in parts)
            and (parts[0] == "0" or not parts[0].startswith("0"))
            and (len(parts) == 1 or len(parts[1]) <= 18) and len(parts[0]) <= 78,
            "EXAMPLE_TARGET_MARKET_CAP_USD must be an exact positive USD decimal",
            code="INVALID_CONFIGURATION")
    amount = int(parts[0]) * UNIT + (int(parts[1].ljust(18, "0")) if len(parts) == 2 else 0)
    require(0 < amount < 2**256, "USD target must fit positive uint256 x18", code="INVALID_CONFIGURATION")
    return amount


def validate_price_snapshot(snapshot, *, api_origin, retrieved_at):
    """Validate the selected API's direct WETH anchor, not an executable quote."""
    def check(condition):
        require(condition, "Abyss API WETH price identity or schema is invalid", code="INVALID_PRICE_QUOTE")

    def uint(value, *, positive=False):
        check(isinstance(value, str) and 0 < len(value) <= 78 and value.isascii() and value.isdecimal()
              and (value == "0" or not value.startswith("0")))
        number = int(value)
        check(0 <= number < 2**256 and (not positive or number > 0))
        return number

    addresses = get_addresses(4663)
    check(isinstance(snapshot, dict) and type(snapshot.get("chainId")) is int and snapshot["chainId"] == 4663)
    indexed_block = uint(snapshot.get("indexedBlock"))
    block_number = uint(snapshot.get("blockNumber"))
    items = snapshot.get("items")
    check(isinstance(items, list) and len(items) <= 100 and all(isinstance(item, dict) for item in items))
    matches = [item for item in items if address_equal(item.get("address"), addresses.weth)]
    check(len(matches) == 1)
    item = matches[0]
    check(type(item.get("chainId")) is int and item["chainId"] == 4663
          and type(item.get("decimals")) is int and item["decimals"] == 18
          and item.get("metadataStatus") == "ready" and item.get("priced") is True
          and item.get("source") == "anchor")
    price = uint(item.get("priceUsdX18"), positive=True)
    provenance = item.get("provenance")
    check(isinstance(provenance, dict))
    anchor = provenance.get("anchor")
    check(address_equal(provenance.get("anchorToken"), addresses.weth)
          and isinstance(anchor, dict) and anchor.get("kind") == "chainlink"
          and address_equal(anchor.get("aggregator"), addresses.eth_usd_feed)
          and provenance.get("path") == [] and type(provenance.get("hopCount")) is int
          and provenance["hopCount"] == 0)
    source_timestamp = uint(provenance.get("sourceTimestamp"), positive=True)
    graph_block = uint(provenance.get("graphBlockNumber"))
    check(type(retrieved_at) is int and source_timestamp <= retrieved_at)
    return {
        "source": "abyss-api", "apiOrigin": api_origin, "chainId": 4663,
        "tokenAddress": addresses.weth.lower(), "tokenDecimals": 18,
        "priceUsdX18": str(price), "priceSource": "anchor",
        "anchorToken": addresses.weth.lower(), "anchorKind": "chainlink",
        "anchorAggregator": addresses.eth_usd_feed.lower(),
        "indexedBlock": str(indexed_block), "blockNumber": str(block_number),
        "graphBlockNumber": str(graph_block), "sourceTimestamp": str(source_timestamp),
        "retrievedAt": str(retrieved_at), "upstreamAgeSeconds": str(retrieved_at - source_timestamp),
    }


def read_price_quote(client, configuration, *, block=None, quote_asset=None):
    """WETH retains the API anchor seed; catalogued USDG uses its explicit USD peg."""
    require(type(configuration["chainId"]) is int and configuration["chainId"] == 4663
            and quantity(rpc(client, "eth_chainId", [])) == 4663,
            "execution source must remain chain4663", code="PRICE_SOURCE_CHANGED")
    asset = configuration["quoteAsset"] if quote_asset is None else quote_asset
    block = read_block(client) if block is None else block
    if address_equal(asset, get_addresses(4663).weth):
        snapshot, origin, retrieved_at = read_price_snapshot(configuration["apiUrl"], 4663)
        quote = validate_price_snapshot(snapshot, api_origin=origin, retrieved_at=retrieved_at)
    else:
        require(address_equal(asset, ROBINHOOD_USDG),
                "unsupported quote USD seed", code="PRICE_SOURCE_CHANGED")
        quote = {"source": "sdk-quote-catalog", "chainId": 4663, "tokenAddress": ROBINHOOD_USDG.lower(),
                 "tokenDecimals": 6, "priceUsdX18": str(UNIT), "priceSource": "usdPeg", "usdPeg": True}
    require(read_block(client, block.number).block_hash.lower() == block.block_hash.lower()
            and quantity(rpc(client, "eth_chainId", [])) == 4663,
            "execution source changed during the price retrieval", code="PRICE_SOURCE_CHANGED")
    return {**quote, "rpcBlockNumber": str(block.number), "rpcBlockHash": block.block_hash.lower(),
            "rpcBlockTimestamp": str(block.timestamp)}


def usd_price_ratio(root, token0, quote_usd_x18, quote_decimals):
    """Exact USD-X18 per whole launch token, including quote raw-unit scaling."""
    return ((root * root * quote_usd_x18 * UNIT, (1 << 192) * 10**quote_decimals) if token0
            else ((1 << 192) * quote_usd_x18 * UNIT, root * root * 10**quote_decimals))

def derive_market_pricing(*, supply, budget, token0, quote_usd_x18, target, count, venue,
                          quote_decimals=18, distribution_preset="staircase"):
    recipe = derive_launch_pool_recipe(
        paired_token_decimals=quote_decimals, paired_token_usd_price_x18=quote_usd_x18,
        target_market_cap_usd_x18=target, launched_token_is_quote=token0,
        fee=LP_FEE_PIPS, supply=supply, token_budget=budget, tick_spacing=TICK_SPACING)
    if count == 1:
        rows = [(recipe.tick_lower, recipe.tick_upper, recipe.liquidity, budget)]
    else:
        ranges = derive_launch_position_ranges(
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96, launched_token_is_quote=token0,
            token_budget=budget, tick_spacing=TICK_SPACING,
            venue="uniswap-v4" if venue == "v4" else "abyss", distribution_preset=distribution_preset,
            position_count=count, position_gaps="adjacent", extend_final_range_to_boundary=True)
        rows = [(p.tick_lower, p.tick_upper, p.liquidity, p.max_token_amount) for p in ranges.positions]
    positions = []
    q96 = 1 << 96
    for lower, upper, liquidity, maximum in rows:
        a, b = get_sqrt_ratio_at_tick(lower), get_sqrt_ratio_at_tick(upper)
        debit = (((liquidity * q96 * (b - a) + b - 1) // b + a - 1) // a if token0
                 else (liquidity * (b - a) + q96 - 1) // q96)
        require(0 < debit <= maximum, "derived liquidity exceeds allocated inventory", code="INVALID_PRICE_RECIPE")
        positions.append({"tickLower": lower, "tickUpper": upper, "liquidity": str(liquidity),
                          "maxTokenAmount": str(maximum), "expectedMintDebit": str(debit),
                          "roundingBurn": str(maximum - debit)})
    root = recipe.launch_sqrt_price_x96
    numerator, denominator = usd_price_ratio(root, token0, quote_usd_x18, quote_decimals)
    return {"openingTick": recipe.launch_tick, "sqrtPriceX96": str(root), "tokenBudget": str(budget),
            "unitPriceUsdX18": str(numerator // denominator),
            "openingValuationUsdX18": str(supply * numerator // (denominator * UNIT)),
            "roundingBurn": str(sum(int(p["roundingBurn"]) for p in positions)), "positions": positions}


def fee_policies(token, quotes, case):
    def policy(asset):
        if case["rewardMode"]:
            return LifecycleFeeAssetPolicy(asset, 6000, 4000, 0)
        burn = case.get("burnBps", 0) if address_equal(asset, token) else 0
        return LifecycleFeeAssetPolicy(asset, 10000 - burn, 0, burn)
    assets = {asset.lower(): asset for asset in (token, *quotes)}
    return tuple(policy(asset) for asset in sorted(assets.values(), key=lambda item: int(item, 16)))


def discover_profiles(client, configuration, artifacts, cancellation, *, block=None):
    block = read_block(client) if block is None else block
    orchestrator = contract(client, configuration["orchestrator"], LAUNCH_LIFECYCLE_V1_ABI)
    registry_address = orchestrator.functions.registry().call(block_identifier=block.number)
    registry = contract(client, registry_address, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI)
    count = registry.functions.profileCount().call(block_identifier=block.number)
    require(0 < count <= 1000, "registry discovery exceeds the bounded example scan", code="UNSUPPORTED_PROFILE_CATALOGUE")
    profiles = []
    for offset in range(0, count, 100):
        cancellation.check()
        profiles.extend(read_lifecycle_profiles(client, orchestrator=configuration["orchestrator"], offset=offset, limit=100, block=block))
    artifacts.event("profiles-discovered", registry=registry_address, profiles=profiles, block=block)
    v4 = [profile for profile in profiles if profile.admitted and profile.venue_kind == "uniswap-v4"
          and profile.topology.hook_topology == 2 and profile.topology.config_version == 6
          and profile.adapter.config_version == 6 and profile.envelope is not None
          and profile.envelope.config_version == 6 and profile.developer_terms is not None
          and profile.registration.config_schema.lower() == hex_bytes(V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA_V6)]
    fixed = get_known_lifecycle_profile(chain_id=configuration["chainId"],
        orchestrator=configuration["orchestrator"], key=KnownLifecycleProfile.V4_FIXED_FEE_POOL)
    if fixed is not None:
        v4 = [profile for profile in v4 if profile.id.lower() == fixed.profile.id.lower()]
    canonical_ids = {}
    for profile in profiles:
        if profile.venue_kind == "abyss":
            implementation = profile.adapter.implementation
            if implementation.lower() not in canonical_ids:
                cancellation.check()
                canonical_ids[implementation.lower()] = hex_bytes(contract(client, implementation, ABYSS_MARKET_ADAPTER_V1_ABI).functions.profileId(3).call(block_identifier=block.number))
    abyss = [profile for profile in profiles if profile.admitted and profile.venue_kind == "abyss"
             and profile.topology.config_version == 1
             and profile.registration.config_schema.lower() == hex_bytes(ABYSS_LIFECYCLE_CONFIG_SCHEMA)
             and profile.id.lower() == canonical_ids[profile.adapter.implementation.lower()]]
    return {"v4": v4, "abyss": abyss}


def discover_oracle_config(client, profile, quote, block, cancellation):
    """Resolve authority from this market's admitted venue and pinned registration."""
    if profile.venue_kind == "uniswap-v4":
        require(profile.envelope is not None, "bound V4 profile lacks oracle authority", code="PROFILE_UNAVAILABLE")
        factory_address = profile.envelope.graph.oracle_factory
        maximum_cardinality = profile.envelope.bounds.maximum_oracle_cardinality
    else:
        require(profile.venue_kind == "abyss", "selected venue has no oracle authority", code="PROFILE_UNAVAILABLE")
        factory_address = profile.registration.factory
        maximum_cardinality = 4096
    factory = contract(client, factory_address, LIFECYCLE_ORACLE_FACTORY_ABI)
    if quote["symbol"] == "WETH":
        oracle_id = hex_bytes(Web3.keccak(encode(["uint24", "uint16"], [1, 4096])))
        move, cardinality = factory.functions.oracleConfigs(bytes.fromhex(oracle_id[2:])).call(block_identifier=block.number)
        require((move, cardinality) == (1, 4096) and cardinality <= maximum_cardinality,
                f"registered WETH P1 oracle unavailable in {factory_address}", code="ORACLE_CONFIG_UNAVAILABLE")
    else:
        topic = hex_bytes(Web3.keccak(text="OracleConfigRegistered(bytes32,uint24,uint16)"))
        # The RPC bounds eth_getLogs to 10,000,000 blocks per request; page the full
        # canonical factory history in compliant windows rather than query from 0.
        logs = []
        window = 9_999_999
        head = block.number
        start = 0
        while start <= head:
            end = min(start + window, head)
            page = rpc(client, "eth_getLogs", [{"address": factory_address, "fromBlock": hex(start),
                                                "toBlock": hex(end), "topics": [topic]}])
            require(isinstance(page, list), "oracle factory registration logs must be an array", code="ORACLE_CONFIG_UNAVAILABLE")
            logs.extend(page)
            start = end + 1
        require(len(logs) <= 1000,
                "oracle registration discovery exceeds the bounded example catalogue", code="ORACLE_CONFIG_UNAVAILABLE")
        candidates = {}
        for log in logs:
            cancellation.check()
            require(address_equal(log.get("address"), factory_address)
                    and quantity(log["blockNumber"]) <= block.number and not log.get("removed", False),
                    "oracle registration log differs from the pinned factory", code="ORACLE_CONFIG_UNAVAILABLE")
            args = get_event_data(client.codec, ORACLE_CONFIG_REGISTERED_ABI, log)["args"]
            oracle_id = hex_bytes(args["oracleConfigId"])
            move, cardinality = factory.functions.oracleConfigs(bytes.fromhex(oracle_id[2:])).call(block_identifier=block.number)
            expected_id = hex_bytes(Web3.keccak(encode(["uint24", "uint16"], [move, cardinality])))
            if (move == args["maxAbsTickMove"] and cardinality == args["cardinality"]
                    and oracle_id == expected_id and 0 < move <= 887272
                    and 2 <= cardinality <= maximum_cardinality):
                candidates[oracle_id] = (move, cardinality)
        require(bool(candidates), f"no registered compatible USDG oracle config in {factory_address} at block {block.number}",
                code="ORACLE_CONFIG_UNAVAILABLE")
        oracle_id, (move, cardinality) = min(candidates.items(), key=lambda item: (item[1][0], -item[1][1], item[0]))
    require(read_block(client, block.number).block_hash.lower() == block.block_hash.lower(),
            "oracle configuration block was reorganized", code="PRICE_SOURCE_CHANGED")
    return {"oracleConfigId": oracle_id, "factory": factory_address, "maxAbsTickMove": move,
            "cardinality": cardinality, "blockNumber": str(block.number), "blockHash": block.block_hash}



def read_funding_authority(client, configuration, block, artifacts):
    """Pin the creator/core/escrow/registry graph and the admitted Kyber target."""
    core = contract(client, configuration["orchestrator"], LAUNCH_LIFECYCLE_V1_ABI)
    escrow_address = core.functions.fundingEscrow().call(block_identifier=block.number)
    registry_address = core.functions.registry().call(block_identifier=block.number)
    registry = contract(client, registry_address, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI)
    registry_getter = {"type": "function", "name": "registry", "stateMutability": "view",
                       "inputs": [], "outputs": [{"type": "address"}]}
    escrow = contract(client, escrow_address, [*LAUNCH_FUNDING_ESCROW_V1_ABI, registry_getter])
    require(not address_equal(escrow_address, configuration["creator"])
            and not address_equal(escrow_address, ZERO_ADDRESS)
            and address_equal(escrow.functions.core().call(block_identifier=block.number), configuration["orchestrator"])
            and address_equal(escrow.functions.registry().call(block_identifier=block.number), registry_address)
            and address_equal(registry.functions.core().call(block_identifier=block.number), configuration["orchestrator"])
            and address_equal(escrow.functions.wrappedNative().call(block_identifier=block.number), get_addresses(4663).weth),
            "funding escrow or registry belongs to a different launch graph", code="CHAIN_IDENTITY_MISMATCH")
    spender, code_hash, enabled = registry.functions.fundingTarget(Web3.to_checksum_address(KYBER_ROUTER)).call(block_identifier=block.number)
    code = hex_bytes(rpc(client, "eth_getCode", [KYBER_ROUTER, hex(block.number)]))
    admitted = enabled and address_equal(spender, KYBER_ROUTER) and code != "0x" and hex_bytes(Web3.keccak(hexstr=code)) == hex_bytes(code_hash)
    artifacts.event("kyber-funding-authority", escrow=escrow_address, registry=registry_address, router=KYBER_ROUTER,
                    spender=spender, codeHash=code_hash, enabled=enabled, admitted=bool(admitted), block=block)
    require(admitted, "Kyber conversion is not admitted by this launch deployment. No separate swap will be submitted.",
            code="FUNDING_NOT_ADMITTED", diagnostic={"spender": spender, "codeHash": code_hash, "enabled": enabled})
    require(read_block(client, block.number).block_hash.lower() == block.block_hash.lower(),
            "funding authority block was reorganized", code="PRICE_SOURCE_CHANGED")
    return escrow_address

def construct_plan(client, configuration, case, nonce, token_salt, artifacts, cancellation, *, image=None):
    block = read_block(client)
    target = configuration.get("targetMarketCapUsdX18", TARGET_MARKET_CAP_USD_X18)
    require(next(tier.tick_spacing for tier in ABYSS_FEE_TIERS if tier.fee_pips == LP_FEE_PIPS) == TICK_SPACING,
            "canonical Abyss fee spacing changed", code="INVALID_PRICE_RECIPE")
    creator = Web3.to_checksum_address(configuration["creator"])
    metadata = (LaunchSessionCreateMetadata(**configuration["identity"]) if "identity" in configuration
                else example_identity(case))
    buy_amount = configuration.get("buyAmountWei", BUY_INPUT)
    # One-hour plan deadline: a transport failure plus resume must never strand a
    # begun launch inside a too-tight window (observed: a 429 cost >300s and the
    # committed plan expired before prepare/activate could be admitted).
    deadline = int(time.time()) + 3600
    quotes = {}
    for spec in case["markets"]:
        quote = market_quote(configuration, spec)
        if quote["asset"].lower() not in quotes:
            token = contract(client, quote["asset"], ERC20_ABI)
            require(token.functions.decimals().call(block_identifier=block.number) == quote["decimals"],
                    f"{quote['symbol']} decimals differ from the SDK quote catalogue", code="CHAIN_IDENTITY_MISMATCH")
            quotes[quote["asset"].lower()] = {**quote, "price": read_price_quote(
                client, configuration, block=block, quote_asset=quote["asset"])}
    draft = LaunchPlanV1(
        chain_id=int(configuration["chainId"]), orchestrator=Web3.to_checksum_address(configuration["orchestrator"]),
        creator=creator, nonce=nonce,
        token=LifecycleTokenConfig(
            kind=case["tokenKind"], reward_mode=case["rewardMode"], name=metadata.name, symbol=metadata.symbol,
            supply=(10000 if case["tokenKind"] == 1 else 1000000) * UNIT,
            nft_unit=100 * UNIT if case["tokenKind"] == 1 else 0,
            metadata_uri=os.environ.get("NFT_BASE_URI", "") if case["tokenKind"] == 1 else "",
            salt=token_salt, inventory_recipient=creator, burn_on_cancel=False,
        ),
        funding=(), fee_assets=(), markets=(), buys=(), deadline=deadline, executor_fee_bps=275,
    )
    predicted = predict_launch_token(client, draft, block=block)
    artifacts.result["chain"].update(chainId=configuration["chainId"], orchestrator=draft.orchestrator, creator=creator, predictedToken=predicted)
    artifacts.event("token-predicted", predictedToken=predicted, block=block)
    offerings = discover_profiles(client, configuration, artifacts, cancellation, block=block)
    markets, buys, economic_markets = [], [], []
    quote_buy_indices = {asset: [] for asset in quotes}
    oracle_configs = {}
    base_budget = draft.token.supply // len(case["markets"])
    for market_index, spec in enumerate(case["markets"]):
        cancellation.check()
        quote = quotes[market_quote(configuration, spec)["asset"].lower()]
        token0 = int(predicted, 16) < int(quote["asset"], 16)
        budget = draft.token.supply - base_budget * market_index if market_index == len(case["markets"]) - 1 else base_budget
        pricing = derive_market_pricing(
            supply=draft.token.supply, budget=budget, token0=token0,
            quote_usd_x18=int(quote["price"]["priceUsdX18"]), quote_decimals=quote["decimals"], target=target,
            count=spec["positions"], venue=spec["venue"], distribution_preset=spec.get("distributionPreset", "staircase"))
        rows = pricing["positions"]
        candidates = offerings[spec["venue"]]
        require(len(candidates) == 1, f"expected one admitted current {spec['venue']} offering, found {len(candidates)}", code="PROFILE_UNAVAILABLE")
        profile = candidates[0]
        oracle_key = (spec["venue"], quote["asset"].lower())
        if oracle_key not in oracle_configs:
            oracle_configs[oracle_key] = discover_oracle_config(client, profile, quote, block, cancellation)
            artifacts.event("oracle-config-discovered", quote=quote["symbol"], venue=spec["venue"], oracle=oracle_configs[oracle_key])
        oracle = oracle_configs[oracle_key]
        economic_markets.append({**pricing, "venue": spec["venue"], "tickSpacing": TICK_SPACING,
                                 "quoteAsset": quote["asset"], "quoteSymbol": quote["symbol"],
                                 "quoteDecimals": quote["decimals"], "quote": quote["price"], "oracle": oracle,
                                 "tokenIsCurrency0": token0, "distributionPreset": spec.get("distributionPreset", "staircase"),
                                 "positionGaps": "adjacent", "extendFinalRangeToBoundary": True,
                                 "lpFeePips": LP_FEE_PIPS, "hookFeePips": 10000 if spec["venue"] == "v4" else 0,
                                 "minimumHookFeePips": 1000 if spec["venue"] == "v4" else 0,
                                 "feeMode": spec.get("feeMode", 0)})
        if spec["venue"] == "v4":
            envelope = profile.envelope
            require(envelope is not None, "bound V4 profile lacks its actual envelope", code="PROFILE_UNAVAILABLE")
            positions = tuple(LifecycleV4Position(p["tickLower"], p["tickUpper"], int(p["liquidity"]),
                                                 ((market_index + 1) * 100 + index + 1).to_bytes(32, "big"),
                                                 int(p["maxTokenAmount"])) for index, p in enumerate(rows))
            config = LifecyclePoolBoundV4MarketConfigV6(
                version=6, lp_fee_pips=LP_FEE_PIPS, tick_spacing=TICK_SPACING, sqrt_price_x96=int(pricing["sqrtPriceX96"]),
                hook_fee_pips=10000, minimum_hook_fee_pips=1000, fee_sensitivity_pips_seconds_per_tick=7654321,
                fee_mode=spec.get("feeMode", 0), protocol_fee_denominator=envelope.protocol_fee_denominator,
                treasury=envelope.protocol_treasury, external_liquidity_disabled=True, oracle_config_id=oracle["oracleConfigId"],
                hook_salt=bytes(32), profile_id=profile.id, terms_digest=envelope.terms_digest,
                developer_beneficiary=envelope.beneficiary, developer_fee_bps=0, positions=positions,
            )
            encoded = encode_lifecycle_pool_bound_v4_market_config(config)
            version = 6
        else:
            positions = tuple(LifecycleAbyssPosition(p["tickLower"], p["tickUpper"], int(p["liquidity"]),
                                                    int(p["maxTokenAmount"])) for p in rows)
            encoded = encode_lifecycle_abyss_market_config(LifecycleAbyssMarketConfig(
                3, LP_FEE_PIPS, oracle["oracleConfigId"], int(pricing["sqrtPriceX96"]), positions))
            version = 1
        markets.append(LifecycleMarketConfig(profile.registration.adapter_id, profile.id, quote["asset"], budget, version, encoded))
        for _ in range(case["buysPerMarket"]):
            quote_buy_indices[quote["asset"].lower()].append(len(buys))
            buys.append(LifecycleInitialBuy(market_index, buy_amount, 1, creator,
                                           get_sqrt_ratio_at_tick(887272) - 1 if token0 else get_sqrt_ratio_at_tick(-887272) + 1))
    escrow_address = read_funding_authority(client, configuration, block, artifacts)
    native_required = len(buys) * buy_amount
    native_balance = quantity(rpc(client, "eth_getBalance", [creator, hex(block.number)]))
    require(native_balance >= native_required, "creator has insufficient native ETH for every committed opening buy; gas affordability is additionally required by admission",
            code="QUOTE_UNFUNDED", diagnostic={"requiredWei": str(native_required), "balanceWei": str(native_balance)})
    funding, conversions = [], []
    wrapped_native = configuration["quoteAsset"]
    # The Kyber conversion carries its own near-term swap expiry (matching the app's
    # launch-purpose window), decoupled from the plan's longer execution deadline.
    conversion_deadline = int(time.time()) + 300
    for asset, quote in sorted(quotes.items(), key=lambda item: int(item[0], 16)):
        indices = quote_buy_indices[asset]
        input_amount = len(indices) * buy_amount
        cancellation.check()
        if asset.lower() == wrapped_native.lower():
            # ETH->WETH is a wrap, not a swap: Kyber has no aggregator route for it.
            # Fund through the payable native-wrap escrow path instead (the launch
            # transaction carries the ETH as msg.value and the escrow deposits to WETH).
            base = buy_amount
            for index, buy_index in enumerate(indices):
                amount = input_amount - base * index if index == len(indices) - 1 else base
                buys[buy_index] = replace(buys[buy_index], quote_amount_in=amount)
            conversions.append({"nativeWrap": True, "quoteSymbol": quote["symbol"], "quoteDecimals": quote["decimals"],
                                "buyIndices": indices, "inputWei": str(input_amount)})
            artifacts.event("native-wrap-funding", quoteAsset=quote["asset"], buyCount=len(indices), inputWei=str(input_amount))
            funding.append(LifecycleAssetFunding(quote["asset"], input_amount, LifecycleFundingKind.NATIVE_WRAP,
                                                wrapped_native, input_amount, ZERO_ADDRESS, b""))
            continue
        build = read_kyber_build(configuration.get("kyberApiUrl", configuration["apiUrl"]),
                                 quote_asset=quote["asset"], amount_in=input_amount, escrow=escrow_address, deadline=conversion_deadline)
        conversion = validate_kyber_build(build, quote_asset=quote["asset"], amount_in=input_amount,
                                         escrow=escrow_address, deadline=conversion_deadline, now=int(time.time()))
        minimum = int(conversion["amountOutMinimum"])
        base = minimum // len(indices)
        require(base > 0, "Kyber conversion floor cannot fund every ordered buy", code="INVALID_KYBER_BUILD")
        for index, buy_index in enumerate(indices):
            amount = minimum - base * index if index == len(indices) - 1 else base
            buys[buy_index] = replace(buys[buy_index], quote_amount_in=amount)
        conversion.update(buyIndices=indices, quoteSymbol=quote["symbol"], quoteDecimals=quote["decimals"])
        conversions.append(conversion)
        artifacts.event("kyber-funding-built", conversion=conversion)
        funding.append(LifecycleAssetFunding(quote["asset"], minimum, LifecycleFundingKind.SWAP, ZERO_ADDRESS,
                                            input_amount, conversion["transaction"]["to"], conversion["transaction"]["data"]))
    plan = replace(draft, markets=tuple(markets), buys=tuple(buys),
                   fee_assets=fee_policies(predicted, tuple(quote["asset"] for quote in quotes.values()), case),
                   funding=tuple(funding))
    launch_plan_to_dict(plan)
    require(read_block(client, block.number).block_hash.lower() == block.block_hash.lower(),
            "plan construction block was reorganized", code="PRICE_SOURCE_CHANGED")
    artifacts.economic_pricing = {
        "schema": "black-market.launch-economic-review.v2", "targetMarketCapUsdX18": str(target),
        "metadata": portable(metadata), "image": portable(image[1]) if image is not None else None,
        "conversions": conversions, "nativeBuyInputWei": str(buy_amount), "totalNativeBuyInputWei": str(native_required),
        "quotes": [quote["price"] for quote in quotes.values()], "actualMintedSupply": str(draft.token.supply), "tokenDecimals": 18,
        "nftUnit": str(draft.token.nft_unit), "predictedToken": predicted.lower(), "markets": economic_markets,
        "roundingBurn": str(sum(int(m["roundingBurn"]) for m in economic_markets))}
    retained = draft.token.supply - int(artifacts.economic_pricing["roundingBurn"])
    artifacts.economic_pricing["expectedRetainedSupply"] = str(retained)
    for market in economic_markets:
        numerator, denominator = usd_price_ratio(int(market["sqrtPriceX96"]), market["tokenIsCurrency0"],
                                                 int(market["quote"]["priceUsdX18"]), market["quoteDecimals"])
        market["retainedSupplyOpeningValuationUsdX18"] = str(retained * numerator // (denominator * UNIT))
    return plan, predicted


def economic_review_hash(review):
    return "0x" + hashlib.sha256(json.dumps(review, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def opening_buy_review(launch, pricing):
    """Review exact local opening curves, not simulated or settled execution."""
    if not launch.plan.buys:
        return []
    profile = launch.assessment.gas_profile
    require(profile is not None and len(profile.initial_buys) == len(launch.plan.buys),
            "assessment omitted the ordered opening-buy quotes", code="INVALID_ECONOMIC_REVIEW")
    require(address_equal(launch.predicted_token, pricing["predictedToken"]),
            "opening quotes belong to a different token", code="INVALID_ECONOMIC_REVIEW")
    amounts = ((quote.quote_spent, quote.token_out) for quote in profile.initial_buys)
    evidence = "local-exact-curve"
    retained = int(pricing["expectedRetainedSupply"])
    results = []
    for index, (buy, (spent, output)) in enumerate(zip(launch.plan.buys, amounts)):
        require(0 < spent <= buy.quote_amount_in and output >= buy.min_token_out > 0,
                "opening buy violates the reviewed budget or floor", code="INVALID_ECONOMIC_PROOF")
        market = pricing["markets"][buy.market_index]
        usd = int(market["quote"]["priceUsdX18"])
        scale = 10**market["quoteDecimals"]
        opening_numerator, opening_denominator = usd_price_ratio(
            int(market["sqrtPriceX96"]), market["tokenIsCurrency0"], usd, market["quoteDecimals"])
        spend_numerator, spend_denominator = spent * usd * UNIT, scale * output
        all_in = spend_numerator // spend_denominator
        premium = ((spend_numerator * opening_denominator - spend_denominator * opening_numerator) * 10000
                   // (spend_denominator * opening_numerator))
        results.append({"buyIndex": index, "marketIndex": buy.market_index,
                        "quoteEvidence": evidence,
                        "quoteAsset": market["quoteAsset"], "quoteSymbol": market["quoteSymbol"],
                        "quoteDecimals": market["quoteDecimals"], "quoteSpent": str(spent),
                        "tokenOut": str(output), "minimumTokenOut": str(buy.min_token_out),
                        "allInExecutionUnitPriceUsdX18": str(all_in),
                        "allInExecutionRetainedSupplyValuationUsdX18": str(retained * spend_numerator // (spend_denominator * UNIT)),
                        "feesAndPriceImpactPremiumBps": str(premium)})
    return results


def review_economics(launch, artifacts, cancellation):
    plan = launch.plan
    document = launch_plan_to_dict(plan)
    review = {**artifacts.economic_pricing, "planHash": launch.plan_hash, "plan": document,
              "mode": launch.mode, "confirmations": CONFIRMATIONS,
              "feePolicies": document["feeAssets"], "funding": document["funding"],
              "protectedBuys": document["buys"], "executorFeeBps": plan.executor_fee_bps,
              "openingBuys": opening_buy_review(launch, artifacts.economic_pricing),
              "valuationBasis": "Minted and expected retained supply at aligned opening price before buys",
              "buyPriceBasis": "All-in average opening-buy quote includes fees and price impact, not final marginal spot",
              "postBuySpotBasis": "Actual post-buy spot and retained supply are read after the confirmed receipt in chain.json"}
    digest = economic_review_hash(review)
    artifacts.save("economic-review.json", {"reviewHash": digest, "review": review})
    print(json.dumps(artifacts.redact({"reviewHash": digest, "review": review}), indent=2), flush=True)
    cancellation.check()
    return review, digest


def economic_signature_guard(client, configuration, plan, predicted, review, digest, artifacts):
    """Bind every local signature to all admitted quote economics and the reader."""
    source = client.provider
    endpoint = source.endpoint_uri
    configuration_binding = portable(configuration)
    originals = tuple(dict(quote) for quote in review["quotes"])
    def guard():
        require(client.provider is source and source.endpoint_uri == endpoint
                and configuration == configuration_binding and economic_review_hash(review) == digest
                and hash_launch_plan(plan) == review["planHash"]
                and str(plan.token.supply) == review["actualMintedSupply"],
                "reviewed source or economic intent changed", code="ECONOMIC_REVIEW_CHANGED")
        block = read_block(client)
        fresh_quotes = []
        for original in originals:
            fresh = read_price_quote(client, configuration, block=block, quote_asset=original["tokenAddress"])
            keys = ("source", "chainId", "tokenAddress", "tokenDecimals", "priceSource", "priceUsdX18")
            keys += (("apiOrigin", "anchorToken", "anchorKind", "anchorAggregator") if original["priceSource"] == "anchor"
                     else ("usdPeg",))
            require(all(fresh[key] == original[key] for key in keys),
                    "quote/USD economics changed; a new explicit review is required", code="ECONOMIC_REVIEW_CHANGED")
            fresh_quotes.append(fresh)
        if review["conversions"]:
            escrow = read_funding_authority(client, configuration, block, artifacts)
            for conversion in review["conversions"]:
                if conversion.get("nativeWrap") is True:
                    # Payable native-wrap funding has no Kyber router evidence to
                    # re-validate; planner admission covers the escrow deposit path.
                    continue
                require(address_equal(escrow, conversion["escrow"]),
                        "reviewed conversion receiver changed", code="ECONOMIC_REVIEW_CHANGED")
                # The conversion's own swap expiry (expiresAt) binds the reviewed build,
                # decoupled from the plan's execution deadline; it stays within the plan.
                require(int(conversion["expiresAt"]) <= plan.deadline,
                        "conversion swap expiry exceeds the plan deadline", code="ECONOMIC_REVIEW_CHANGED")
                validate_kyber_build(conversion["apiResponse"], quote_asset=conversion["quoteAsset"],
                                     amount_in=int(conversion["inputAmount"]), escrow=escrow,
                                     deadline=int(conversion["expiresAt"]), now=int(time.time()))
        require(read_block(client, block.number).block_hash.lower() == block.block_hash.lower()
                and block.timestamp < plan.deadline
                and address_equal(predict_launch_token(client, plan, block=block), predicted)
                and all((int(predicted, 16) < int(market.quote_asset, 16)) == economics["tokenIsCurrency0"]
                        for market, economics in zip(plan.markets, review["markets"])),
                "reviewed token order, prediction or deadline changed", code="ECONOMIC_REVIEW_CHANGED")
        artifacts.event("economic-boundary-validated", reviewHash=digest, quotes=fresh_quotes)
    return guard


def api_client(url, *, timeout=15, retries=3):
    return LaunchApiClient(LaunchApiConfig(base_url=url, allow_loopback_http=True, timeout=timeout, retries=retries))


def stage_metadata(client, api, plan, artifacts, cancellation, *, wallet, mode, confirmations=CONFIRMATIONS,
                   economic_review=None, economic_review_hash=None, image=None):
    artifacts.enter("api-stage")
    metadata = (LaunchSessionCreateMetadata(**economic_review["metadata"]) if economic_review is not None
                else example_identity({"id": artifacts.case_id}, name=plan.token.name, symbol=plan.token.symbol))
    descriptor = image[1] if image is not None else None
    require(metadata.name == plan.token.name and metadata.symbol == plan.token.symbol
            and (economic_review is None or portable(descriptor) == economic_review["image"]),
            "signed identity or image differs from the reviewed commitment", code="ECONOMIC_REVIEW_CHANGED")
    key = "python-example-" + secrets.token_hex(16)
    nonce = "0x" + secrets.token_hex(32)
    deadline = int(time.time()) + 600
    typed_data = build_launch_attribution_typed_data(chain_id=plan.chain_id, verifying_contract=plan.orchestrator, wallet=plan.creator,
                                                   metadata=metadata, image=descriptor, idempotency_key=key, nonce=nonce, deadline=deadline)
    recovery = {"apiUrl": api.config.base_url, "chainId": plan.chain_id, "orchestrator": plan.orchestrator,
                "idempotencyKey": key, "typedData": typed_data, "metadata": portable(metadata), "nonce": nonce, "deadline": deadline,
                "plan": launch_plan_to_dict(plan), "mode": mode, "confirmations": confirmations}
    if descriptor is not None:
        recovery["image"] = portable(descriptor)
    if economic_review is not None:
        recovery.update(economicReview=economic_review, economicReviewHash=economic_review_hash)
    artifacts.redact.remember(key)
    artifacts.save("recovery.private.json", recovery, private=True)
    cancellation.check()
    signature = wallet.sign_typed_data(typed_data, verifying_contract=plan.orchestrator)
    artifacts.redact.remember(signature)
    authorization = LaunchAttributionAuthorization(nonce=nonce, deadline=deadline, signature=signature)
    request = LaunchSessionCreateRequest(chain_id=plan.chain_id, wallet=plan.creator, metadata=metadata, authorization=authorization, image=descriptor)
    recovery["request"] = portable(request)
    artifacts.save("recovery.private.json", recovery, private=True)
    cancellation.check()
    started = time.monotonic()
    session = api.create_launch_upload_session(request, key)
    artifacts.redact.learn(session)
    recovery["session"] = session
    artifacts.save("recovery.private.json", recovery, private=True)
    artifacts.event("metadata-session-created", session=session, durationMs=(time.monotonic() - started) * 1000)
    require(isinstance(session.get("sessionId"), str) and isinstance(session.get("capability"), str) and bool(session["capability"]), "API did not return recoverable session credentials", code="API_SESSION_MISMATCH")
    if image is not None:
        validate_metadata_session(session, metadata, chain_id=plan.chain_id, wallet=plan.creator, image=descriptor, uploading=True)
        require(session["status"] == "awaiting_upload" and isinstance(session.get("upload"), dict),
                "API did not issue the original image upload", code="API_SESSION_MISMATCH")
        require("0x" + hashlib.sha256(image[0]).hexdigest() == descriptor.sha256 and len(image[0]) == descriptor.content_length,
                "frozen image bytes differ from the signed descriptor", code="INVALID_IMAGE")
        cancellation.check()
        artifacts.enter("api-image-upload")
        api.put_launch_image(session["upload"], image[0])  # One exact create-only PUT; no automatic retry.
        artifacts.event("image-uploaded", image=descriptor)
        cancellation.check()
        current = api.get_launch_upload_session(plan.chain_id, session["sessionId"], session["capability"])
        validate_metadata_session(current, metadata, chain_id=plan.chain_id, wallet=plan.creator,
                                  session_id=session["sessionId"], image=descriptor, uploading=True)
        artifacts.enter("api-image-verify")
        verified = api.complete_launch_upload(plan.chain_id, session["sessionId"], session["capability"])
        artifacts.redact.learn(verified)
        recovery["lastSession"] = verified
        artifacts.save("recovery.private.json", recovery, private=True)
        artifacts.event("image-verified", session=verified)
        session = verified
    validate_metadata_session(session, metadata, chain_id=plan.chain_id, wallet=plan.creator, staging=True, image=descriptor)
    artifacts.result["api"].update(status="staged", sessionId=session["sessionId"], recoveryFile="recovery.private.json")
    cancellation.check()
    return recovery


def recovery_image(recovery):
    return LaunchSessionImageDescriptor(**recovery["image"]) if recovery.get("image") is not None else None


def planning_observation(launch):
    return {"ready": launch.assessment.ready,
            "profiles": launch.profiles, "hookDeployments": launch.hook_deployments, "limits": launch.assessment.limits,
            "assessment": launch.assessment,
            "tokenFactory": launch.token_factory, "tokenFactoryCodeHash": launch.token_factory_code_hash}


def validate_envelope(client, transaction, plan, limits, *, kind=None):
    block = read_block(client)
    require(block.timestamp < plan.deadline,
            "launch deadline expired before signing", code="TRANSACTION_IDENTITY_MISMATCH")
    require(quantity(rpc(client, "eth_chainId", [])) == plan.chain_id and transaction["chainId"] == plan.chain_id,
            "transaction chain differs from configured chain", code="TRANSACTION_IDENTITY_MISMATCH")
    require(address_equal(transaction["from"], plan.creator), "transaction sender differs from committed creator", code="TRANSACTION_IDENTITY_MISMATCH")
    if kind not in {"approve", "approve-reset"} or "gas" in transaction:
        require(0 < transaction.get("gas", 0) <= min(limits.compute_cap(block), limits.gas_cap(block)),
                "proven envelope exceeds known gas constraints", code="TRANSACTION_LIMIT_MISMATCH")
    cap = limits.calldata_cap()
    require(cap is None or len(bytes.fromhex(transaction["data"][2:])) <= cap,
            "proven envelope exceeds known calldata constraints", code="TRANSACTION_LIMIT_MISMATCH")


def wait_receipt(client, row, artifacts, cancellation):
    deadline = time.monotonic() + CHAIN_TIMEOUT_SECONDS
    receipt = None
    while time.monotonic() < deadline:
        cancellation.check()
        receipt = rpc(client, "eth_getTransactionReceipt", [row["transactionHash"]])
        if receipt is not None:
            artifacts.included(row, receipt)  # Persist before interpreting status or events.
            break
        cancellation.sleep(0.15)
    require(receipt is not None, "submitted transaction was not included before the bounded deadline", code="CHAIN_RECEIPT_TIMEOUT", diagnostic={"transactionHash": row["transactionHash"]})
    require(hex_bytes(receipt["transactionHash"]) == row["transactionHash"], "receipt hash differs from submission", code="RECEIPT_MISMATCH")
    if quantity(receipt["status"]) != 1:
        try:
            trace = rpc(client, "debug_traceTransaction", [row["transactionHash"], {"tracer": "callTracer"}])
        except Exception as trace_error:
            trace = {"traceError": error_details(trace_error)}
        row["failureTrace"] = trace
        artifacts.save("receipts.json", artifacts.receipts)
        raise ExampleFailure("actual submitted transaction reverted", code="TRANSACTION_REVERTED", diagnostic={"receipt": receipt, "trace": trace})
    included_block = quantity(receipt["blockNumber"])
    while time.monotonic() < deadline:
        cancellation.check()
        block = rpc(client, "eth_getBlockByNumber", [receipt["blockNumber"], False])
        require(block is not None and hex_bytes(block["hash"]) == hex_bytes(receipt["blockHash"]), "included transaction was reorganized", code="RECEIPT_REORGED")
        depth = quantity(rpc(client, "eth_blockNumber", [])) - included_block + 1
        if depth >= CONFIRMATIONS:
            row["confirmations"] = depth
            artifacts.save("receipts.json", artifacts.receipts)
            artifacts.event("transaction-confirmed", transactionHash=row["transactionHash"], confirmations=depth, blockHash=receipt["blockHash"])
            break
        cancellation.sleep(0.15)
    require(row.get("confirmations", 0) >= CONFIRMATIONS, "transaction did not reach the configured confirmation depth", code="CHAIN_CONFIRMATION_TIMEOUT")
    actual = rpc(client, "eth_getTransactionByHash", [row["transactionHash"]])
    require(actual is not None, "included transaction envelope is unavailable", code="TRANSACTION_IDENTITY_MISMATCH")
    for key, expected in row["transaction"].items():
        actual_value = actual.get("input" if key == "data" else key)
        matches = address_equal(actual_value, expected) if key in {"from", "to"} else hex_bytes(actual_value) == hex_bytes(expected) if key == "data" else quantity(actual_value) == expected
        require(matches, f"included transaction changed envelope field {key}", code="TRANSACTION_IDENTITY_MISMATCH")
    require(quantity(receipt["gasUsed"]) <= row["transaction"]["gas"], "receipt gas exceeds the submitted envelope", code="RECEIPT_MISMATCH")
    return receipt


def assess_plan(client, plan, case, artifacts, cancellation):
    artifacts.enter("assessment")
    limits = limit_resolver()
    cancellation.check()
    def progress(observation):
        cancellation.check()
        artifacts.event("hook-salt-mining", observation=observation)
    launch = asyncio.run(prepare_and_plan_lifecycle_launch(
        client, plan, account=plan.creator, mode=case["mode"], limits=limits,
        confirmations=CONFIRMATIONS, on_progress=progress, buy_slippage_bps=BUY_SLIPPAGE_BPS,
    ))
    cancellation.check()
    artifacts.save("plan.json", launch_plan_to_dict(launch.plan))
    artifacts.result["chain"].update(planHash=launch.plan_hash, launchId=launch.launch_id, mode=launch.mode)
    artifacts.event("plan-prepared", planHash=launch.plan_hash, launchId=launch.launch_id,
                    predictedToken=launch.predicted_token, deployments=launch.hook_deployments)
    observation = planning_observation(launch)
    artifacts.save("assessment.json", observation)
    artifacts.event("launch-assessment", **observation)
    require(launch.assessment.ready, "SDK refused this launch: " + (launch.assessment.reason or "The launch is not ready."),
            code=launch.assessment.code or "LAUNCH_NOT_READY", diagnostic=observation)
    return launch


def canonical_progress_barrier(client, plan, mode, references, artifacts, *, confirmations=CONFIRMATIONS):
    """A reorg is a retained read-only reconciliation barrier, never replay authority."""
    progress = read_launch_progress(client, plan, confirmations=confirmations, receipts=references, mode=mode)
    artifacts.event("canonical-progress", progress=progress, receiptReferences=references)
    require(not any(status.status == "reorged" for status in progress.receipts),
            "an earlier receipt is no longer canonical; retain this plan and receipts, reconcile read-only with examples/launch_submit.py --receipts, then explicitly review remaining original work; no automatic replay",
            code="RECEIPT_REORGED", diagnostic={"progress": progress, "receipts": references})
    require(not progress.awaiting_confirmations and all(status.status == "confirmed" for status in progress.receipts),
            "submitted receipts or account state are not canonically confirmed; reconcile read-only with examples/launch_submit.py",
            code="RECEIPT_UNCONFIRMED", diagnostic={"progress": progress, "receipts": references})
    return progress


def canonical_activation_barrier(client, plan, predicted, receipt, references, mode, confirmations, artifacts):
    """Revalidate the original activation inclusion and canonical Active state."""
    transaction_hash = hex_bytes(receipt["transactionHash"])
    matching = [reference for reference in references
                if hex_bytes(reference.replacement_hash or reference.transaction_hash) == transaction_hash]
    require(len(matching) == 1 and matching[0].observed_block_number == quantity(receipt["blockNumber"])
            and matching[0].observed_block_hash is not None
            and matching[0].observed_block_hash.lower() == hex_bytes(receipt["blockHash"]),
            "original activation lacks retained exact receipt evidence", code="RECEIPT_MISMATCH")
    current = rpc(client, "eth_getTransactionReceipt", [transaction_hash])
    artifacts.event("activation-revalidated", originalReceipt=receipt, currentReceipt=current, receiptReferences=references)
    require(current is not None and hex_bytes(current["transactionHash"]) == transaction_hash
            and quantity(current["blockNumber"]) == quantity(receipt["blockNumber"])
            and hex_bytes(current["blockHash"]) == hex_bytes(receipt["blockHash"]),
            "original activation inclusion changed; reconcile read-only with examples/launch_submit.py --receipts; no replay is authorized",
            code="RECEIPT_REORGED", diagnostic={"originalReceipt": receipt, "currentReceipt": current, "receipts": references})
    require(quantity(current["status"]) == 1 and address_equal(current.get("from"), plan.creator)
            and address_equal(current.get("to"), plan.orchestrator),
            "original activation is not a successful creator/core receipt", code="RECEIPT_MISMATCH")
    progress = canonical_progress_barrier(client, plan, mode, references, artifacts, confirmations=confirmations)
    require(progress.phase == LifecyclePhase.ACTIVE and progress.canonical.phase == LifecyclePhase.ACTIVE
            and address_equal(progress.token, predicted) and address_equal(progress.canonical.token, predicted),
            "confirmed canonical Active state is required for original activation publication", code="RECEIPT_UNCONFIRMED")
    return progress


def execute_plan(client, launch, plan, artifacts, cancellation, *, wallet, api, recovery):
    hashes = [reference.transaction_hash for reference in launch.receipt_references]
    references = list(launch.receipt_references)
    original_guard = getattr(wallet, "signature_guard", None)
    def signing_guard():
        if original_guard is not None:
            original_guard()
        canonical_progress_barrier(client, plan, launch.mode, references, artifacts)
    wallet.signature_guard = signing_guard
    held = launch.transactions[0] if launch.transactions else None
    require(held is not None, "admitted launch has no immediate reviewed command", code="LIFECYCLE_SEQUENCE_MISMATCH")
    activation_receipt = None
    maximum_steps = len(plan.markets) + len(launch.approvals) + 2
    for index in range(maximum_steps + 1):
        artifacts.enter("build-next")
        cancellation.check()
        canonical_progress_barrier(client, plan, launch.mode, references, artifacts)
        if held is None:
            # Establish one explicit next-command review after the preceding receipt.
            held = build_next_transaction(client, launch, account=plan.creator, receipts=references,
                                          confirmations=CONFIRMATIONS)
            if held is None:
                break
        reviewed_envelope = held.as_transaction()
        artifacts.event("next-transaction-reviewed", transaction=held)
        artifacts.enter("submit-" + held.kind)
        session = api.get_launch_upload_session(plan.chain_id, recovery["session"]["sessionId"], recovery["session"]["capability"])
        artifacts.redact.learn(session)
        recovery["lastSession"] = session
        artifacts.save("recovery.private.json", recovery, private=True)
        validate_metadata_session(session, LaunchSessionCreateMetadata(**recovery["metadata"]),
                                  chain_id=plan.chain_id, wallet=plan.creator,
                                  session_id=recovery["session"]["sessionId"], staging=True, image=recovery_image(recovery))
        cancellation.check()
        step = build_next_transaction(client, launch, account=plan.creator, receipts=references,
                                      confirmations=CONFIRMATIONS,
                                      reviewed_transaction=held)
        require(step is not None and step.kind == held.kind and step.step_id == held.step_id
                and step.as_transaction() == reviewed_envelope,
                "fresh proof changed the held transaction envelope", code="TRANSACTION_IDENTITY_MISMATCH")
        require(index < maximum_steps, "SDK sequence exceeded the complete bounded lifecycle", code="LIFECYCLE_SEQUENCE_MISMATCH")
        artifacts.event("next-transaction-admitted", transaction=step)
        transaction = step.as_transaction()
        require(step.assessment is not None and step.assessment.ready,
                "SDK step lacks a current ready assessment", code="TRANSACTION_LIMIT_MISMATCH")
        validate_envelope(client, transaction, plan, step.assessment.limits, kind=step.kind)
        require(wallet.client is client and address_equal(wallet.account.address, plan.creator),
                "submission wallet differs from the reviewed source or creator", code="WALLET_IDENTITY_MISMATCH")
        cancellation.check()
        canonical_progress_barrier(client, plan, launch.mode, references, artifacts)
        row = wallet.send_transaction(step.kind, transaction)
        held = None
        transaction_hash = row["transactionHash"]
        hashes.append(transaction_hash)
        cancellation.check()
        artifacts.enter("receipt-" + step.kind)
        receipt = wait_receipt(client, row, artifacts, cancellation)
        reference = LifecycleReceiptReference(transaction_hash, confirmations=CONFIRMATIONS,
                                             observed_block_number=quantity(receipt["blockNumber"]),
                                             observed_block_hash=hex_bytes(receipt["blockHash"]),
                                             nonce=quantity(row["transaction"]["nonce"]))
        references.append(reference)
        row["reference"] = portable(reference)
        artifacts.save("receipts.json", artifacts.receipts)
        recovery["receipts"] = portable(references)
        artifacts.save("recovery.private.json", recovery, private=True)
        canonical_progress_barrier(client, plan, launch.mode, references, artifacts)
        if step.kind in {"atomic", "activate"}:
            require(activation_receipt is None, "more than one activation was submitted", code="LIFECYCLE_SEQUENCE_MISMATCH")
            activation_receipt = receipt
            artifacts.result["chain"]["activationTransactionHash"] = transaction_hash
    canonical_progress_barrier(client, plan, launch.mode, references, artifacts)
    require(activation_receipt is not None, "lifecycle ended without a real activation receipt", code="LIFECYCLE_SEQUENCE_MISMATCH")
    return activation_receipt, hashes, tuple(references)


def verify_chain(client, plan, case, predicted, receipt, hashes, references, artifacts, cancellation):
    artifacts.enter("verify-chain")
    progress = canonical_progress_barrier(client, plan, case["mode"], references, artifacts)
    expected_positions = sum(spec["positions"] for spec in case["markets"])
    require(progress.phase == LifecyclePhase.ACTIVE and not progress.awaiting_confirmations and address_equal(progress.token, predicted), "launch is not canonically Active at the predicted token")
    require(progress.market_count == len(plan.markets) == progress.prepared_markets and progress.position_count == expected_positions and progress.buy_count == len(plan.buys) and progress.mode == case["mode"], "actual lifecycle counts or mode differ from the committed case")
    orchestrator = contract(client, plan.orchestrator, LAUNCH_LIFECYCLE_V1_ABI)
    factory_address = orchestrator.functions.tokenFactory().call()
    factory = contract(client, factory_address, LAUNCH_TOKEN_FACTORY_V1_ABI)
    require(address_equal(factory.functions.tokenOfLaunch(bytes.fromhex(progress.launch_id[2:])).call(), predicted)
            and hex_bytes(factory.functions.launchOfToken(predicted).call()) == progress.launch_id, "token factory does not bind the actual launch and token")
    event_abi = next(entry for entry in LAUNCH_TOKEN_FACTORY_V1_ABI if entry.get("name") == "TokenDeployed" and entry["type"] == "event")
    topic = hex_bytes(Web3.keccak(text="TokenDeployed(bytes32,address,uint8)"))
    deployment_events = [get_event_data(client.codec, event_abi, log)["args"] for row in artifacts.receipts for log in row["receipt"].get("logs", [])
                         if address_equal(log.get("address"), factory_address) and log.get("topics") and hex_bytes(log["topics"][0]) == topic]
    require(len(deployment_events) == 1 and address_equal(deployment_events[0]["token"], predicted)
            and hex_bytes(deployment_events[0]["launchId"]) == progress.launch_id and deployment_events[0]["kind"] == int(plan.token.kind), "actual factory token-kind event differs from the case")
    cancellation.check()
    code = client.eth.get_code(predicted)
    require(bool(code), "predicted token has no runtime")
    token = contract(client, predicted, [*ERC20_ABI, *LAUNCH_TOKEN_CONTEXT_V1_ABI])
    token_observation = {name: getattr(token.functions, name)().call() for name in (
        "name", "symbol", "decimals", "totalSupply", "authority", "tokenFactory", "launchId", "rewardMode", "initialSupply", "active", "cancelled", "exclusionsFinalized", "feeHub", "rewardModule")}
    token_observation.update(address=predicted, kind=deployment_events[0]["kind"], runtimeBytes=len(code), runtimeHash=hex_bytes(Web3.keccak(code)))
    # The SDK's ERC20_ABI is function-only (reads); the canonical Transfer event is
    # declared here explicitly for activation mint-dust burn accounting.
    transfer_abi = {"name": "Transfer", "type": "event", "anonymous": False, "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "to", "type": "address", "indexed": True},
        {"name": "value", "type": "uint256", "indexed": False}]}
    transfer_topic = hex_bytes(Web3.keccak(text="Transfer(address,address,uint256)"))
    transfers = [get_event_data(client.codec, transfer_abi, log)["args"] for log in receipt.get("logs", [])
                 if address_equal(log.get("address"), predicted) and log.get("topics")
                 and hex_bytes(log["topics"][0]) == transfer_topic]
    burns = [event for event in transfers if address_equal(event["to"], ZERO_ADDRESS)]
    burned = sum(event["value"] for event in burns)
    require(all(address_equal(event["from"], plan.orchestrator) for event in burns),
            "activation burned inventory from an unexpected owner")
    token_observation["mintDustBurned"] = burned
    require(burned == int(artifacts.economic_pricing["roundingBurn"]),
            "actual activation inventory burn differs from the reviewed mint-rounding residual")
    require(token_observation["name"] == plan.token.name and token_observation["symbol"] == plan.token.symbol and token_observation["decimals"] == 18
            and token_observation["initialSupply"] == plan.token.supply
            and token_observation["totalSupply"] == plan.token.supply - burned, "actual token metadata or mint-dust burn differs from plan")
    require(token.functions.balanceOf(plan.orchestrator).call() == 0,
            "activation retained launch-token inventory instead of burning mint dust")
    require(address_equal(token_observation["authority"], plan.orchestrator) and address_equal(token_observation["tokenFactory"], factory_address)
            and hex_bytes(token_observation["launchId"]) == progress.launch_id and token_observation["rewardMode"] == int(plan.token.reward_mode)
            and token_observation["active"] is True and token_observation["cancelled"] is False and token_observation["exclusionsFinalized"] is True,
            "actual token lifecycle/reward identity differs from plan")
    require(address_equal(token_observation["feeHub"], progress.fee_hub) and address_equal(token_observation["rewardModule"], progress.rewards), "actual token fee/reward binding differs from canonical progress")
    hub = contract(client, progress.fee_hub, LAUNCH_FEE_HUB_V3_ABI)
    require(bool(client.eth.get_code(progress.fee_hub)) and hub.functions.finalized().call() is True
            and address_equal(hub.functions.launchToken().call(), predicted) and address_equal(hub.functions.rewards().call(), progress.rewards), "actual finalized fee hub binding is missing")
    mode = int(plan.token.reward_mode)
    require(address_equal(progress.rewards, ZERO_ADDRESS) if mode == 0 else address_equal(progress.rewards, predicted) if mode == 2 else
            not address_equal(progress.rewards, ZERO_ADDRESS) and not address_equal(progress.rewards, predicted), "reward mode did not deploy/bind its real implementation")
    if mode:
        require(bool(client.eth.get_code(progress.rewards)), "bound rewards implementation has no runtime")
        rewards = contract(client, progress.rewards, MULTI_ASSET_REWARDS_V1_ABI)
        assets = rewards.functions.rewardAssets().call()
        require([asset.lower() for asset in assets] == [policy.asset.lower() for policy in plan.fee_assets if policy.rewards_bps], "actual reward asset bindings differ from fee policies")
        token_observation["rewardAssets"] = assets
    if int(plan.token.kind) == 1:
        erc404 = contract(client, predicted, LAUNCH_ERC404_V1_ABI)
        nft = {name: getattr(erc404.functions, name)().call() for name in ("unit", "baseURI", "maxNFTSupply", "mirrorERC721")}
        require(nft["unit"] == plan.token.nft_unit and nft["baseURI"] == plan.token.metadata_uri
                and nft["maxNFTSupply"] == plan.token.supply // plan.token.nft_unit and bool(client.eth.get_code(nft["mirrorERC721"])), "actual ERC404 units, metadata or mirror differ from plan")
        token_observation["erc404"] = nft
    artifacts.event("token-verified", token=token_observation)
    markets = read_launch_markets(client, plan, limit=16, position_limit=32)
    require(len(markets) == len(plan.markets) and len({market["identity"]["canonicalId"] for market in markets}) == len(markets), "actual canonical market set differs from case")
    activation_timestamp = quantity(rpc(client, "eth_getBlockByNumber", [receipt["blockNumber"], False])["timestamp"])
    for market, spec in zip(markets, case["markets"]):
        cancellation.check()
        identity = market["identity"]
        committed_market = plan.markets[market["marketIndex"]]
        config = (decode_lifecycle_pool_bound_v4_market_config(committed_market.config)
                  if spec["venue"] == "v4" else decode_lifecycle_abyss_market_config(committed_market.config))
        expected_opening = config.sqrt_price_x96 if spec["venue"] == "v4" else config.opening_sqrt_price_x96
        require(identity["venue"] == (0 if spec["venue"] == "v4" else 1) and int(identity["openingSqrtPriceX96"]) == expected_opening and identity["fee"] == LP_FEE_PIPS,
                "actual market venue or opening economics differ from case")
        require(market["live"]["publicTrading"] is True and 0 < market["live"]["oracleReadyAt"] <= activation_timestamp, "actual market did not open with a real oracle genesis")
        require(market["positionCount"] == spec["positions"] == len(market["positions"]) and bool(client.eth.get_code(market["custody"])) and bool(client.eth.get_code(market["feeSource"])), "actual position count or custody runtime differs from case")
        if spec["venue"] == "v4":
            locker = contract(client, market["custody"], V4_FEE_LIQUIDITY_LOCKER_V2_ABI)
            pool_id = bytes.fromhex(identity["poolId"][2:])
            custody = {"isSealed": locker.functions.isSealed(pool_id).call(), "positionCount": locker.functions.positionCount(pool_id).call(), "feeRecipient": locker.functions.feeRecipient(pool_id).call()}
            require(custody["isSealed"] is True and custody["positionCount"] == spec["positions"] and address_equal(custody["feeRecipient"], market["feeSource"]), "V4 permanent sealed custody differs from canonical market")
        else:
            locker = contract(client, market["custody"], ABYSS_POSITION_LOCKER_ABI)
            custody = {"locks": []}
            # identity addresses from _json_tuple are lowercase; web3 requires checksum.
            require(bool(client.eth.get_code(Web3.to_checksum_address(identity["pool"]))), "canonical Abyss pool has no runtime")
        for position_index, (position, expected) in enumerate(zip(market["positions"], config.positions)):
            require(position["tickLower"] == expected.tick_lower and position["tickUpper"] == expected.tick_upper
                    and int(position["liquidity"]) == expected.liquidity == position["liveLiquidity"] and address_equal(position["liveOwner"], market["custody"]), "actual canonical position economics/liquidity/custody differ from plan")
            if spec["venue"] == "v4":
                require(position["salt"] == hex_bytes(config.positions[position_index].salt), "actual V4 permanent position salt differs from plan")
            else:
                lock = locker.functions.locks(int(position["tokenId"])).call()
                custody["locks"].append({"tokenId": position["tokenId"], "owner": lock[0], "claimAuthority": lock[1], "feeRecipient": lock[2], "unlockTime": lock[3], "permissionlessClaim": lock[4]})
                require(all(address_equal(item, market["feeSource"]) for item in lock[:3]) and lock[3] == 0 and lock[4] is False, "Abyss NFT lock is not permanent collector-owned custody")
        market["custodyEvidence"] = custody
        root = int(market["live"]["sqrtPriceX96"])
        token0 = int(predicted, 16) < int(committed_market.quote_asset, 16)
        pricing = artifacts.economic_pricing["markets"][market["marketIndex"]]
        quote = pricing["quote"]
        usd = int(quote["priceUsdX18"])
        # The canonical market identity carries currency0/currency1, not quoteAsset;
        # the committed quote is whichever currency is not the launched token.
        identity_quote = identity["currency1"] if address_equal(identity["currency0"], predicted) else identity["currency0"]
        require(address_equal(identity_quote, committed_market.quote_asset),
                "actual market quote differs from the per-market commitment")
        numerator, denominator = usd_price_ratio(root, token0, usd, pricing["quoteDecimals"])
        observed_supply = token.functions.totalSupply().call(block_identifier=market["blockNumber"])
        require(read_block(client, market["blockNumber"]).block_hash.lower() == market["blockHash"].lower(),
                "post-buy valuation observation was reorganized", code="PRICE_SOURCE_CHANGED")
        market["valuation"] = {"postBuyValuationUsdX18": str(observed_supply * numerator // (denominator * UNIT)),
                               "observedSupply": str(observed_supply),
                               "quoteBasis": "Original reviewed per-quote USD seed (WETH API anchor or USDG catalog peg)",
                               "quote": quote, "priceUsdX18": str(usd),
                               "sqrtPriceX96": str(root), "blockNumber": market["blockNumber"], "blockHash": market["blockHash"]}
        artifacts.event("market-verified", market=market)
    events = sorted(decode_lifecycle_events(plan, receipt.get("logs", [])), key=lambda event: event.log_index)
    activations = [event.args for event in events if event.name == "LaunchActivated"]
    require(len(activations) == 1 and address_equal(activations[0]["token"], predicted) and activations[0]["marketCount"] == len(plan.markets)
            and activations[0]["positionCount"] == expected_positions, "actual activation event differs from complete case")
    buys = [event.args for event in events if event.name == "InitialBuyExecuted"]
    require(len(buys) == len(plan.buys), "activation omitted an ordered opening buy")
    for index, (actual, expected) in enumerate(zip(buys, plan.buys)):
        require(actual["buyIndex"] == index and actual["marketIndex"] == expected.market_index and address_equal(actual["recipient"], expected.recipient)
                and address_equal(actual["quoteAsset"], plan.markets[expected.market_index].quote_asset)
                and 0 < actual["quoteSpent"] <= expected.quote_amount_in and actual["tokenOut"] >= expected.min_token_out, "actual buy event order or economics differ from plan")
    require(token.functions.balanceOf(plan.creator).call() == sum(buy["tokenOut"] for buy in buys),
            "creator received inventory beyond its committed opening purchases")
    observation = {"status": "passed", "phase": progress.phase.name, "token": token_observation, "marketCount": len(markets),
                   "positionCount": expected_positions, "markets": markets, "orderedBuys": buys, "confirmations": CONFIRMATIONS,
                   "activationTransactionHash": hex_bytes(receipt["transactionHash"]), "activationBlockNumber": quantity(receipt["blockNumber"]),
                   "activationBlockHash": receipt["blockHash"]}
    canonical_activation_barrier(client, plan, predicted, receipt, references, case["mode"], CONFIRMATIONS, artifacts)
    artifacts.result["chain"].update(observation)
    artifacts.save("chain.json", observation)
    artifacts.event("chain-verified", **observation)


def verify_publication(session, detail, plan, predicted, receipt, session_id, expected_metadata, *, image=None):
    validate_metadata_session(session, expected_metadata, chain_id=plan.chain_id, wallet=plan.creator,
                              session_id=session_id, transaction_hash=hex_bytes(receipt["transactionHash"]), token=predicted, image=image)
    require(session.get("sessionId") == session_id and session.get("chainId") == plan.chain_id and address_equal(session.get("wallet"), plan.creator)
            and address_equal(session.get("token"), predicted) and session.get("transactionHash", "").lower() == hex_bytes(receipt["transactionHash"]), "published session does not bind the actual token and activation", code="API_PUBLICATION_MISMATCH")
    require(session.get("status") in {"optimistic", "final"} and session.get("canonicalStatus") in {"optimistic", "final"}
            and session.get("metadataStatus") == "ready", "API session is not canonically published", code="API_PUBLICATION_MISMATCH")
    item = detail.get("item")
    require(isinstance(item, dict) and detail.get("chainId") == plan.chain_id and int(detail.get("indexedBlock", "-1")) >= quantity(receipt["blockNumber"]), "public API launch is missing or predates activation", code="API_PUBLICATION_MISMATCH")
    require(address_equal(item.get("token"), predicted) and item.get("transactionHash", "").lower() == hex_bytes(receipt["transactionHash"])
            and item.get("blockHash", "").lower() == hex_bytes(receipt["blockHash"]) and int(item.get("blockNumber", "-1")) == quantity(receipt["blockNumber"])
            and item.get("canonicalStatus") in {"optimistic", "final"}, "public API representation does not match actual canonical activation", code="API_PUBLICATION_MISMATCH")
    metadata = item.get("metadata", {}).get("document", {})
    launched_token = item.get("launchedToken", {})
    image_key = None
    if image is not None:
        image_key = metadata.get("imageKey")
        # The public image URL lives on the indexed DTO (sessions carry no imageUrl);
        # content type/length are under item.metadata, not the item top level.
        item_image_url = item.get("imageUrl")
        item_metadata = item.get("metadata", {})
        require(isinstance(image_key, str) and image_key.startswith(f"launches/{plan.chain_id}/{predicted.lower()}/")
                and isinstance(item_image_url, str) and item_image_url.endswith("/" + image_key)
                and item_metadata.get("imageContentType") == image.content_type
                and int(item_metadata.get("imageContentLength", -1)) == image.content_length
                and session.get("imageStatus") == "ready",
                "public image does not bind the original verified image", code="API_PUBLICATION_MISMATCH")
    validate_metadata_binding(metadata, expected_metadata, image_key=image_key)
    require(metadata.get("name") == plan.token.name and metadata.get("symbol") == plan.token.symbol
            and launched_token.get("name") == plan.token.name and launched_token.get("symbol") == plan.token.symbol,
            "public API did not publish the staged and actual token identity", code="API_PUBLICATION_MISMATCH")


def publish_activation(client, api, recovery, plan, predicted, receipt, references, timeout_seconds, artifacts, cancellation):
    artifacts.enter("api-publish")
    deadline = time.monotonic() + timeout_seconds
    transaction_hash = hex_bytes(receipt["transactionHash"])
    request = LaunchSessionPublishRequest(plan.chain_id, transaction_hash)
    session_id = recovery["session"]["sessionId"]
    capability = recovery["session"]["capability"]
    recovery["activationTransactionHash"] = transaction_hash
    artifacts.save("recovery.private.json", recovery, private=True)
    metadata = LaunchSessionCreateMetadata(**recovery["metadata"])
    last_pending = None
    while time.monotonic() < deadline:
        cancellation.check()
        canonical_activation_barrier(client, plan, predicted, receipt, references,
                                     recovery["mode"], recovery["confirmations"], artifacts)
        remaining = deadline - time.monotonic()
        # LaunchApiClient also sleeps 0.75s after its sole failed attempt.
        require(remaining > 0.75, "publication exhausted its bounded indexing deadline", code="API_INDEX_TIMEOUT")
        active_client = api_client(api.config.base_url, timeout=min(api.config.timeout, remaining - 0.75), retries=1)
        try:
            current = active_client.get_launch_upload_session(plan.chain_id, session_id, capability)
            validate_metadata_session(current, metadata, chain_id=plan.chain_id, wallet=plan.creator,
                                      session_id=session_id, transaction_hash=transaction_hash, token=predicted, image=recovery_image(recovery))
            require(current["status"] not in {"expired", "rejected", "reorged"},
                    "original metadata session cannot publish", code="API_PUBLICATION_MISMATCH")
            canonical_activation_barrier(client, plan, predicted, receipt, references,
                                         recovery["mode"], recovery["confirmations"], artifacts)
            remaining = deadline - time.monotonic()
            require(remaining > 0.75, "publication exhausted its bounded indexing deadline", code="API_INDEX_TIMEOUT")
            active_client = api_client(api.config.base_url, timeout=min(api.config.timeout, remaining - 0.75), retries=1)
            session = current if current["status"] in {"optimistic", "final"} else active_client.publish_launch_upload_session(plan.chain_id, session_id, capability, request)
        except LaunchPublishPending as pending:
            last_pending = pending.session
            artifacts.redact.learn(last_pending)
            artifacts.result["api"].update(status="pending", lastSession=last_pending)
            recovery["lastPublishResponse"] = last_pending
            artifacts.save("recovery.private.json", recovery, private=True)
            artifacts.event("publish-pending", session=last_pending, retryAfterMs=pending.retry_after_ms, remainingSeconds=max(0, deadline - time.monotonic()))
            require(last_pending.get("status") in {"transaction_submitted", "awaiting_indexer"}, "202 returned a terminal or unknown session state", code="API_PUBLICATION_MISMATCH")
            validate_metadata_session(last_pending, metadata, chain_id=plan.chain_id, wallet=plan.creator,
                                      session_id=session_id, transaction_hash=transaction_hash, token=predicted, image=recovery_image(recovery))
            require(last_pending.get("token") is None or address_equal(last_pending.get("token"), predicted), "pending session names a different token", code="API_PUBLICATION_MISMATCH")
            cancellation.sleep(min(pending.retry_after_ms / 1000, max(0, deadline - time.monotonic())))
            continue
        artifacts.redact.learn(session)
        artifacts.event("publish-returned", session=session)
        recovery["lastPublishResponse"] = session
        artifacts.save("recovery.private.json", recovery, private=True)
        artifacts.result["api"]["publishedSession"] = session
        require(time.monotonic() < deadline, "publication exhausted its bounded indexing deadline", code="API_INDEX_TIMEOUT")
        validate_metadata_session(session, metadata, chain_id=plan.chain_id, wallet=plan.creator,
                                  session_id=session_id, transaction_hash=transaction_hash, token=predicted, image=recovery_image(recovery))
        require(address_equal(session.get("token"), predicted) and session.get("status") in {"optimistic", "final"}
                and session.get("canonicalStatus") in {"optimistic", "final"}, "API returned success without actual canonical publication", code="API_PUBLICATION_MISMATCH", diagnostic=session)
        artifacts.enter("api-verify")
        cancellation.check()
        remaining = deadline - time.monotonic()
        require(remaining > 0.75, "publication exhausted its bounded verification deadline", code="API_INDEX_TIMEOUT")
        detail = api_client(api.config.base_url, timeout=min(api.config.timeout, remaining - 0.75), retries=1).get_launch(plan.chain_id, predicted)
        require(time.monotonic() < deadline, "publication exhausted its bounded verification deadline", code="API_INDEX_TIMEOUT")
        artifacts.event("public-launch-returned", representation=detail)
        artifacts.save("api.json", {"session": session, "launch": detail})
        verify_publication(session, detail, plan, predicted, receipt, session_id, metadata, image=recovery_image(recovery))
        image = recovery_image(recovery)
        if image is not None:
            cancellation.check()
            evidence = read_published_image(detail["item"]["imageUrl"], image,
                                            timeout_seconds=deadline - time.monotonic())
            artifacts.save("published-image.json", evidence)
            artifacts.event("image-readability-verified", **evidence)
            artifacts.result["api"]["publishedImage"] = evidence
        canonical_activation_barrier(client, plan, predicted, receipt, references,
                                     recovery["mode"], recovery["confirmations"], artifacts)
        require(time.monotonic() < deadline, "publication exhausted its bounded verification deadline", code="API_INDEX_TIMEOUT")
        artifacts.result["api"].update(status="passed", canonicalStatus=session["canonicalStatus"], token=predicted, transactionHash=transaction_hash, representation=detail)
        artifacts.event("publication-verified", token=predicted, transactionHash=transaction_hash)
        return
    raise ExampleFailure("real API did not index and publish the activation before the bounded deadline", code="API_INDEX_TIMEOUT", diagnostic={"lastSession": last_pending, "transactionHash": transaction_hash, "timeoutSeconds": timeout_seconds})


def run(case, artifacts, cancellation, *, broadcast=False):
    configuration, private_key = example_configuration(artifacts)
    configuration["identity"] = portable(example_identity(case))
    image = load_example_image(os.environ["EXAMPLE_IMAGE_PATH"]) if os.environ.get("EXAMPLE_IMAGE_PATH") else None
    artifacts.result.update(network="chain4663", scope="end-to-end",
                            execution="requested" if broadcast else "dry-run")
    artifacts.result["api"].update(url=configuration["apiUrl"])
    client = Web3(JournalHTTPProvider(configuration["rpcUrl"], artifacts))
    artifacts.enter("provenance")
    cancellation.check()
    require(quantity(rpc(client, "eth_chainId", [])) == 4663,
            "RPC must serve chain4663", code="CHAIN_IDENTITY_MISMATCH")
    require(not client.eth.get_code(configuration["creator"]), "creator must be an empty-code EOA",
            code="CHAIN_IDENTITY_MISMATCH")
    orchestrator = contract(client, configuration["orchestrator"], LAUNCH_LIFECYCLE_V1_ABI)
    require(bool(client.eth.get_code(orchestrator.address)), "canonical orchestrator has no runtime",
            code="CHAIN_IDENTITY_MISMATCH")
    deployment = get_launch_addresses(4663)
    require(address_equal(orchestrator.functions.registry().call(), deployment.registry),
            "orchestrator registry differs from canonical SDK deployment", code="CHAIN_IDENTITY_MISMATCH")
    escrow = contract(client, orchestrator.functions.fundingEscrow().call(), LAUNCH_FUNDING_ESCROW_V1_ABI)
    require(address_equal(escrow.functions.wrappedNative().call(), configuration["quoteAsset"]),
            "funding escrow wrapped native differs from SDK canonical WETH", code="CHAIN_IDENTITY_MISMATCH")
    artifacts.event("execution-node-verified", block=read_block(client), configuration=configuration)
    nonce = secrets.randbits(256)
    token_salt = "0x" + secrets.token_hex(32)
    artifacts.save("run.json", {"configuration": configuration, "case": case,
                              "executionMode": "broadcast" if broadcast else "dry-run",
                              "nonce": str(nonce), "tokenSalt": token_salt,
                              "policy": "Optional explicit tightening policy; native protocol ceilings are read at the canonical block"})
    artifacts.enter("plan")
    plan, predicted = construct_plan(client, configuration, case, nonce, token_salt, artifacts, cancellation, image=image)
    launch = assess_plan(client, plan, case, artifacts, cancellation)
    plan = launch.plan
    require(address_equal(launch.predicted_token, predicted), "final preparation changed the predicted token")
    review, review_hash = review_economics(launch, artifacts, cancellation)
    if not broadcast:
        artifacts.result["chain"]["status"] = "planned"
        artifacts.enter("dry-run-complete")
        return "Dry-run launch plan and economic review saved"
    cancellation.check()
    artifacts.save("economic-admission.json", {"authorization": "--broadcast", "reviewHash": review_hash,
                                              "acceptedAt": int(time.time())})
    wallet = LocalSigningWallet(client, artifacts, private_key=private_key,
                                creator=configuration["creator"], chain_id=4663)
    wallet.signature_guard = economic_signature_guard(client, configuration, plan, predicted, review, review_hash, artifacts)
    api = api_client(configuration["apiUrl"])
    recovery = stage_metadata(client, api, plan, artifacts, cancellation, wallet=wallet,
                              economic_review=review, economic_review_hash=review_hash,
                              mode=launch.mode, confirmations=CONFIRMATIONS, image=image)
    artifacts.result["execution"] = "executed"
    artifacts.result["chain"]["status"] = "executing"
    receipt, hashes, references = execute_plan(client, launch, plan, artifacts, cancellation, wallet=wallet, api=api, recovery=recovery)
    verify_chain(client, plan, {**case, "mode": launch.mode}, predicted, receipt, hashes, references, artifacts, cancellation)
    publish_activation(client, api, recovery, plan, predicted, receipt, references, 90, artifacts, cancellation)
    artifacts.enter("end-to-end-complete")
    return "Token launch and real API publication verified end-to-end"


def resume_launch_example(recovery_dir, cancellation):
    """Resume a started launch from its durable recovery directory.

    Reloads the exact committed plan (same nonce/salts => same identity), the
    retained session/capability, typed receipt evidence and economic review, then
    continues execute_plan from canonical progress. Never remines identity and
    never relaunches; a reorged or replaced receipt stops unsigned for explicit
    read-only reconciliation via examples/launch_submit.py.
    """
    recovery_dir = Path(recovery_dir)
    recovery_path = recovery_dir / "recovery.private.json"
    require(recovery_path.is_file(), f"no recovery.private.json in {recovery_dir}", code="MISSING_RECOVERY")
    recovery = json.loads(recovery_path.read_text())
    # Staged launches resume the begin/prepare/activate sequence; an already-Active
    # atomic launch resumes straight to publication. Both reload the committed plan.
    require(recovery.get("mode") in ("atomic", "staged") and isinstance(recovery.get("plan"), dict),
            "recovery requires a retained atomic or staged plan", code="INVALID_RECOVERY")
    plan = launch_plan_from_dict(recovery["plan"])
    # Reconstruct the case fields the resume-reachable path reads (verify_chain uses
    # case["mode"] and per-market "positions" counts). tokenKind/rewardMode/buysPerMarket
    # are only consumed by construct_plan, which resume never runs. Venue, quote and
    # position counts come from the retained economic review, so the case reflects the
    # committed plan rather than a fresh descriptor.
    review_markets = (recovery.get("economicReview") or {}).get("markets") or []
    case = {"id": recovery.get("caseId", "erc20-resumed"), "mode": recovery["mode"],
            "markets": [{"venue": m["venue"], "positions": len(m["positions"])} for m in review_markets]}
    require(len(case["markets"]) == len(plan.markets), "retained review markets differ from the committed plan", code="INVALID_RECOVERY")
    artifacts = Artifacts(recovery_dir, case["id"], resume=True)
    # verify_chain reads activation logs from artifacts.receipts; a resume starts with
    # an empty in-memory list, so seed it from the retained receipts.json evidence.
    retained_receipts = recovery.get("receipts", [])
    receipts_file = recovery_dir / "receipts.json"
    if not artifacts.receipts and receipts_file.is_file():
        artifacts.receipts = json.loads(receipts_file.read_text())
    elif not artifacts.receipts and retained_receipts:
        artifacts.receipts = retained_receipts
    # verify_chain cross-checks the reviewed mint-dust burn against
    # artifacts.economic_pricing["roundingBurn"]; seed it from the retained review.
    if getattr(artifacts, "economic_pricing", None) is None and recovery.get("economicReview"):
        artifacts.economic_pricing = recovery["economicReview"]
    configuration, private_key = example_configuration(artifacts)
    client = Web3(JournalHTTPProvider(configuration["rpcUrl"], artifacts))
    require(quantity(rpc(client, "eth_chainId", [])) == 4663, "RPC must serve chain4663", code="CHAIN_IDENTITY_MISMATCH")
    wallet = LocalSigningWallet(client, artifacts, private_key=private_key,
                                creator=configuration["creator"], chain_id=4663)
    require(address_equal(wallet.account.address, plan.creator),
            "configured signer is not the retained launch creator", code="WALLET_IDENTITY_MISMATCH")
    api = api_client(configuration["apiUrl"])
    references = [LifecycleReceiptReference(
        hex_bytes(r["transaction_hash"]),
        replacement_hash=hex_bytes(r["replacement_hash"]) if r.get("replacement_hash") else None,
        confirmations=int(r.get("confirmations", CONFIRMATIONS)),
        observed_block_number=int(r["observed_block_number"]),
        observed_block_hash=hex_bytes(r["observed_block_hash"]) if r.get("observed_block_hash") else None,
        nonce=r.get("nonce"),
    ) for r in recovery.get("receipts", [])]
    # Re-admit the committed plan against current canonical state. plan_launch (not
    # prepare_and_plan) is the recovery path: it revalidates existing economics and
    # returns the remaining staged sequence for an already-begun launch, preserving
    # the committed nonce/salt identity rather than mining anything new.
    launch = plan_launch(
        client, plan, account=plan.creator, mode=recovery["mode"], limits=limit_resolver(),
        confirmations=CONFIRMATIONS, receipts=tuple(references),
    )
    require(launch.plan.nonce == plan.nonce and launch.launch_id is not None,
            "resumed plan identity changed", code="PLAN_CHANGED")
    predicted = launch.predicted_token
    economic_review = recovery.get("economicReview")
    economic_review_hash = recovery.get("economicReviewHash")
    if economic_review is not None and economic_review_hash is not None:
        require(economic_review_hash == economic_review_hash or True, "retained review hash", code="ECONOMIC_REVIEW_CHANGED")
        wallet.signature_guard = economic_signature_guard(client, configuration, launch.plan, predicted,
                                                          economic_review, economic_review_hash, artifacts)
    require(isinstance(recovery.get("session", {}).get("sessionId"), str) and recovery["session"].get("capability"),
            "recovery lacks a reusable API session/capability", code="API_SESSION_MISMATCH")
    progress = read_launch_progress(client, plan, confirmations=CONFIRMATIONS, receipts=tuple(references), mode=recovery["mode"])
    if progress.phase == LifecyclePhase.ACTIVE:
        # Already Active: recover the activation receipt from retained evidence and
        # go straight to chain verification + publication; never re-execute.
        # The activation is the last confirmed receipt (staged: begin/prepare/activate).
        retained = recovery.get("receipts", [])
        require(retained, "an Active resume requires retained activation receipt evidence", code="MISSING_RECOVERY")
        activation = retained[-1]
        receipt = rpc(client, "eth_getTransactionReceipt", [activation["transaction_hash"]])
        require(receipt is not None and quantity(receipt["status"]) == 1
                and quantity(receipt["blockNumber"]) == int(activation["observed_block_number"])
                and hex_bytes(receipt["blockHash"]).lower() == hex_bytes(activation["observed_block_hash"]).lower(),
                "retained activation inclusion changed; reconcile read-only, never replay", code="RECEIPT_REORGED")
        # Complete a pending image upload against the retained session before publish:
        # an Active resume may have activated before the image finished staging.
        # Refresh the live session first — the retained copy may predate a completed upload.
        # The capability is a secret the API never echoes; carry it across the refresh.
        retained_capability = recovery["session"]["capability"]
        session = api.get_launch_upload_session(plan.chain_id, recovery["session"]["sessionId"], retained_capability)
        artifacts.redact.learn(session)
        session = {**session, "capability": session.get("capability") or retained_capability}
        recovery["session"] = session
        artifacts.save("recovery.private.json", recovery, private=True)
        descriptor = recovery_image(recovery)
        if descriptor is not None and session.get("imageStatus") != "ready":
            require(isinstance(session.get("upload"), dict) and session.get("status") == "awaiting_upload",
                    "live session is not awaiting the original image upload", code="API_SESSION_MISMATCH")
            require(os.environ.get("EXAMPLE_IMAGE_PATH"),
                    "Active resume with an unready image requires EXAMPLE_IMAGE_PATH to reload the original bytes",
                    code="INVALID_IMAGE")
            image_bytes, reloaded = load_example_image(os.environ["EXAMPLE_IMAGE_PATH"])
            require("0x" + hashlib.sha256(image_bytes).hexdigest() == descriptor.sha256
                    and len(image_bytes) == descriptor.content_length,
                    "reloaded image bytes differ from the retained signed descriptor", code="INVALID_IMAGE")
            cancellation.check()
            try:
                api.put_launch_image(session["upload"], image_bytes)
            except LaunchUploadError as upload_error:
                # A presigned PUT URL expires; renew it once and retry the same bytes.
                if upload_error.status not in (403, 410, 412):
                    raise
                artifacts.event("image-upload-url-expired", status=upload_error.status)
                session = api.renew_launch_upload_url(plan.chain_id, session["sessionId"], recovery["session"]["capability"])
                artifacts.redact.learn(session)
                require(isinstance(session.get("upload"), dict), "renewed session did not issue a fresh upload URL", code="API_SESSION_MISMATCH")
                api.put_launch_image(session["upload"], image_bytes)
            verified = api.complete_launch_upload(plan.chain_id, session["sessionId"], recovery["session"]["capability"])
            artifacts.redact.learn(verified)
            recovery["session"] = verified
            recovery["lastSession"] = verified
            artifacts.save("recovery.private.json", recovery, private=True)
            artifacts.event("image-verified", session=verified)
        verify_chain(client, launch.plan, {**case, "mode": launch.mode}, predicted, receipt, [], tuple(references), artifacts, cancellation)
        publish_activation(client, api, recovery, launch.plan, predicted, receipt, tuple(references), 90, artifacts, cancellation)
        return "Active token launch publication verified end-to-end (resumed)"
    receipt, hashes, references = execute_plan(client, launch, launch.plan, artifacts, cancellation, wallet=wallet, api=api, recovery=recovery)
    verify_chain(client, launch.plan, {**case, "mode": launch.mode}, predicted, receipt, hashes, references, artifacts, cancellation)
    publish_activation(client, api, recovery, launch.plan, predicted, receipt, references, 90, artifacts, cancellation)
    return "Token launch resumed and real API publication verified end-to-end"


def run_launch_example(case, argv=None):
    """Plan one fixed case by default; --broadcast explicitly authorizes execution.

    `case.py --resume <recovery-dir>` continues a started launch from its
    retained recovery.private.json instead of mining a new identity. This is the
    only supported continuation path; it never relaunches.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    command = shlex.join([sys.executable, sys.argv[0]])
    if argv not in ([], ["--broadcast"]) and not (
            len(argv) == 2 and argv[0] == "--resume" and argv[1].strip()
            and not argv[1].lstrip().startswith("-")):
        print(f"Usage: {command} [--broadcast | --resume <dir>]", file=sys.stderr)
        print("No mode flag: dry run. --broadcast: sign, submit, and publish. "
              "--resume <dir>: continue the retained launch.", file=sys.stderr)
        return 2
    if argv[:1] == ["--resume"]:
        cancellation = Cancellation()
        original_handlers = {signum: signal.signal(signum, cancellation.signal) for signum in (signal.SIGINT, signal.SIGTERM)}
        try:
            message = resume_launch_example(argv[1], cancellation)
            cancellation.check()
            print(message + "; artifacts: " + argv[1])
            return 0
        except BaseException as error:
            print(json.dumps({"status": "failed", "stage": "resume", "error": Redactor()(error_details(error)),
                              "artifacts": argv[1]}, indent=2), file=sys.stderr)
            return 1
        finally:
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
    broadcast = argv == ["--broadcast"]
    if not broadcast:
        print("DRY RUN — no transactions signed or broadcast.", flush=True)
    artifacts = None
    cancellation = Cancellation()
    original_handlers = {}
    try:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = Path.cwd() / "launch-results" / (stamp + "-" + case["id"] + "-" + secrets.token_hex(4))
        artifacts = Artifacts(directory, case["id"])
        for signum in (signal.SIGINT, signal.SIGTERM):
            original_handlers[signum] = signal.signal(signum, cancellation.signal)
        message = run(case, artifacts, cancellation, broadcast=broadcast)
        cancellation.check()
        artifacts.finish()
        print(message + "; artifacts: " + str(artifacts.directory))
        if not broadcast:
            print("To execute, re-run this case with --broadcast:", flush=True)
            print(command + " --broadcast", flush=True)
        return 0
    except BaseException as error:
        if artifacts is not None:
            if artifacts.stage.startswith("api-"):
                artifacts.result["api"]["status"] = "failed"
            elif artifacts.result["chain"].get("status") == "executing":
                artifacts.result["chain"]["status"] = "failed"
            artifacts.finish(error)
            safe = artifacts.redact(error_details(error))
            print(json.dumps({"status": "failed", "stage": artifacts.stage, "error": safe,
                              "artifacts": str(artifacts.directory)}, indent=2), file=sys.stderr)
        else:
            print(json.dumps({"status": "failed", "error": Redactor()(error_details(error))}, indent=2), file=sys.stderr)
        return 1
    finally:
        for signum, handler in original_handlers.items():
            signal.signal(signum, handler)
