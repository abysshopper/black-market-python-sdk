"""Prepare or execute ONE token-launch case on explicitly owned local Anvil nodes.

The shared fixture supplies cases and endpoints. Without --execute this only reads
chain provenance and finalizes a plan offchain: no signing, simulation, or writes.
--execute stages signed metadata, launches the real token, verifies chain state,
and publishes its activation hash to the real configured API. --chain-only is an
explicit partial proof; it never reports full end-to-end success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web3 import Web3
from web3._utils.events import get_event_data

from black_market_sdk import (
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
    V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchAttributionAuthorization,
    LaunchExecutionLimits,
    LaunchPlanV1,
    LaunchPublishPending,
    LaunchSessionCreateRequest,
    LaunchSessionMetadata,
    LaunchSessionPublishRequest,
    LifecycleAbyssMarketConfig,
    LifecycleAbyssPosition,
    LifecycleAssetFunding,
    LifecycleFeeAssetPolicy,
    LifecycleInitialBuy,
    LifecycleMarketConfig,
    LifecyclePhase,
    LifecyclePoolBoundV4MarketConfig,
    LifecycleTokenConfig,
    LifecycleV4Position,
    build_launch_attribution_typed_data,
    build_next_transaction,
    create_controlled_launch_fork,
    decode_lifecycle_events,
    decode_lifecycle_pool_bound_v4_market_config,
    encode_lifecycle_abyss_market_config,
    encode_lifecycle_pool_bound_v4_market_config,
    get_launch_addresses,
    get_sqrt_ratio_at_tick,
    hash_launch_plan,
    launch_id_of,
    launch_plan_to_dict,
    plan_launch,
    predict_launch_token,
    prepare_pool_bound_lifecycle_plan,
    read_launch_markets,
    read_launch_progress,
    read_lifecycle_profiles,
)
from black_market_sdk.lifecycle_rpc import hex_bytes, quantity, read_block, rpc, rpc_transaction
from _smoke_support import (
    Artifacts, JournalHTTPProvider, Redactor, SmokeFailure, error_details,
    loopback_url, portable, positive_seconds, rpc_identity,
)

ZERO_ADDRESS = "0x" + "00" * 20
UNIT = 10**18
MARKET_BUDGET = 1100 * UNIT
LIQUIDITY = 1000 * UNIT
BUY_INPUT = 10**15
CONFIRMATIONS = 1
CHAIN_TIMEOUT_SECONDS = 90
_LIMIT_FIELDS = {
    "chainTransactionGasLimit": "chain_transaction_gas_limit",
    "rpcTransactionGasLimit": "rpc_transaction_gas_limit",
    "accountTransactionGasLimit": "account_transaction_gas_limit",
    "maxCalldataBytes": "max_calldata_bytes",
    "headroomBps": "headroom_bps",
}


class Cancellation:
    def __init__(self):
        self.signum = None

    def signal(self, signum, _frame):
        # Let in-flight submissions return so their hashes can be durably saved.
        self.signum = signum

    def check(self):
        if self.signum is not None:
            raise SmokeFailure("launch smoke interrupted; submitted chain state is preserved", code="INTERRUPTED", diagnostic={"signal": self.signum})

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
        raise SmokeFailure(message, code=code, diagnostic=diagnostic)


def address_equal(left, right) -> bool:
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def contract(client, address, abi):
    return client.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def validate_cases(cases):
    require(isinstance(cases, list) and bool(cases), "fixture cases must be a nonempty catalogue", code="INVALID_CONFIGURATION")
    ids = set()
    for case in cases:
        require(isinstance(case, dict), "each catalogue case must be an object", code="INVALID_CONFIGURATION")
        case_id = case.get("id")
        require(isinstance(case_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", case_id) and case_id not in ids,
                "catalogue case IDs must be safe and unique", code="INVALID_CONFIGURATION")
        ids.add(case_id)
        require(type(case.get("tokenKind")) is int and case["tokenKind"] in (0, 1) and type(case.get("rewardMode")) is int and case["rewardMode"] in (0, 1, 2),
                "unsupported token kind or reward mode", code="INVALID_CONFIGURATION")
        require(case.get("mode") in ("atomic", "staged"), "case mode must be atomic or staged", code="INVALID_CONFIGURATION")
        markets = case.get("markets")
        require(isinstance(markets, list) and 1 <= len(markets) <= 2, "cases require one or two complete markets", code="INVALID_CONFIGURATION")
        require(type(case.get("buysPerMarket")) is int and 1 <= case["buysPerMarket"] <= 2, "unsupported opening buy count", code="INVALID_CONFIGURATION")
        venues = set()
        for market in markets:
            require(isinstance(market, dict) and market.get("venue") in ("v4", "abyss"), "unsupported market venue", code="INVALID_CONFIGURATION")
            require(market["venue"] not in venues, "each venue appears at most once per quote", code="INVALID_CONFIGURATION")
            venues.add(market["venue"])
            require(type(market.get("positions")) is int and 1 <= market["positions"] <= 3, "unsupported position count", code="INVALID_CONFIGURATION")
            require(type(market.get("feeMode", 0)) is int and market.get("feeMode", 0) in (0, 1), "unsupported V4 fee mode", code="INVALID_CONFIGURATION")
            require(market["venue"] == "v4" or "feeMode" not in market, "Abyss cases cannot set V4 fee mode", code="INVALID_CONFIGURATION")
            require(not set(market) - {"venue", "positions", "feeMode"}, "unknown market variation", code="INVALID_CONFIGURATION")
        burn = case.get("burnBps", 0)
        require(type(burn) is int and 0 <= burn < 10000 and (not burn or case["rewardMode"] == 0), "unsupported burn/reward combination", code="INVALID_CONFIGURATION")
        require(not set(case) - {"id", "description", "tokenKind", "rewardMode", "mode", "markets", "buysPerMarket", "burnBps"},
                "unknown case variation", code="INVALID_CONFIGURATION")
    return cases


def load_fixture(path: Path):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(fixture, dict) and fixture.get("schema") == "black-market.launch-smoke-fixture.v1" and fixture.get("fixtureOnly") is True,
            "an explicit fixtureOnly local launch-smoke fixture is required", code="INVALID_CONFIGURATION")
    validate_cases(fixture.get("cases"))
    return fixture


def validate_fixture(fixture, api_override=None):
    require(fixture.get("chainId") == "4663", "fixture must fork the current chain4663 deployment", code="INVALID_CONFIGURATION")
    deployment = get_launch_addresses(4663)
    require(address_equal(fixture.get("orchestrator"), deployment.orchestrator), "fixture orchestrator is not the current SDK deployment", code="INVALID_CONFIGURATION")
    require(address_equal(fixture.get("quoteAsset"), deployment.wrapped_native) and fixture.get("quoteDecimals") == 18,
            "fixture quote must be canonical 18-decimal wrapped native", code="INVALID_CONFIGURATION")
    creator = fixture.get("creator")
    require(isinstance(creator, str) and Web3.is_address(creator) and int(creator, 16) != 0, "fixture creator must be a nonzero unlocked dev account", code="INVALID_CONFIGURATION")
    oracle = fixture.get("oracleConfigId")
    require(isinstance(oracle, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", oracle) and int(oracle, 16) != 0,
            "fixture requires a nonzero registered oracleConfigId", code="INVALID_CONFIGURATION")
    source = loopback_url(fixture.get("rpcUrl"), rpc=True)
    fork = loopback_url(fixture.get("forkRpcUrl"), rpc=True)
    require(rpc_identity(source) != rpc_identity(fork), "execution and simulation Anvil endpoints must be separate", code="INVALID_CONFIGURATION")
    limits = fixture.get("executionLimits")
    require(isinstance(limits, dict) and set(limits) == {*_LIMIT_FIELDS, "provenance"}, "fixture must supply every supported execution limit without unknown caps", code="INVALID_CONFIGURATION")
    require(isinstance(limits["provenance"], dict) and limits["provenance"].get("scope") == "controlled-local-measurement",
            "execution limits require controlled-local-measurement provenance", code="INVALID_CONFIGURATION")
    for name in _LIMIT_FIELDS:
        value = limits[name]
        require((type(value) is int or isinstance(value, str) and re.fullmatch(r"[0-9]+", value)) and int(value) >= (0 if name == "headroomBps" else 1),
                f"invalid execution limit {name}", code="INVALID_CONFIGURATION")
    api = api_override if api_override is not None else fixture.get("apiUrl")
    if api is not None:
        api = loopback_url(api)
    return source, fork, api


def limit_resolver(fixture):
    def resolve(_client, context):
        return LaunchExecutionLimits(
            **{target: int(fixture["executionLimits"][source]) for source, target in _LIMIT_FIELDS.items()},
            observed_block_number=context.block.number, observed_block_hash=context.block.block_hash,
            chain_id=context.chain_id, account=context.account, orchestrator=context.orchestrator,
            source="owned local fixture; controlled-local-measurement",
        )
    return resolve


def oriented_bands(count: int, token_is_currency0: bool):
    return tuple(
        (index * 120, 887220 if index == count - 1 else (index + 1) * 120) if token_is_currency0 else
        (-887220 if index == count - 1 else -(index + 1) * 120, -index * 120)
        for index in range(count)
    )


def fee_policies(token, quote, case):
    def policy(asset):
        if case["rewardMode"]:
            return LifecycleFeeAssetPolicy(asset, 6000, 4000, 0)
        burn = case.get("burnBps", 0) if address_equal(asset, token) else 0
        return LifecycleFeeAssetPolicy(asset, 10000 - burn, 0, burn)
    return tuple(policy(asset) for asset in sorted((token, quote), key=lambda item: int(item, 16)))


def discover_profiles(client, fixture, artifacts, cancellation):
    orchestrator = contract(client, fixture["orchestrator"], LAUNCH_LIFECYCLE_V1_ABI)
    registry_address = orchestrator.functions.registry().call()
    registry = contract(client, registry_address, LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI)
    count = registry.functions.profileCount().call()
    require(0 < count <= 1000, "registry discovery exceeds this bounded smoke catalogue", code="UNSUPPORTED_PROFILE_CATALOGUE")
    profiles = []
    for offset in range(0, count, 100):
        cancellation.check()
        profiles.extend(read_lifecycle_profiles(client, orchestrator=fixture["orchestrator"], offset=offset, limit=100))
    artifacts.event("profiles-discovered", registry=registry_address, profiles=profiles)
    v4 = [profile for profile in profiles if profile.admitted and profile.venue_kind == "uniswap-v4"
          and profile.topology.hook_topology == 2 and profile.topology.config_version == 5
          and profile.registration["configSchema"].lower() == hex_bytes(V4_POOL_BOUND_LIFECYCLE_CONFIG_SCHEMA)]
    canonical_ids = {}
    for profile in profiles:
        if profile.venue_kind == "abyss":
            implementation = profile.adapter["implementation"]
            if implementation.lower() not in canonical_ids:
                cancellation.check()
                canonical_ids[implementation.lower()] = hex_bytes(contract(client, implementation, ABYSS_MARKET_ADAPTER_V1_ABI).functions.profileId(3).call())
    abyss = [profile for profile in profiles if profile.admitted and profile.venue_kind == "abyss"
             and profile.topology.config_version == 1
             and profile.registration["configSchema"].lower() == hex_bytes(ABYSS_LIFECYCLE_CONFIG_SCHEMA)
             and profile.id.lower() == canonical_ids[profile.adapter["implementation"].lower()]]
    return {"v4": v4, "abyss": abyss}


def construct_plan(client, fixture, case, nonce, token_salt, artifacts, cancellation):
    block = read_block(client)
    creator = Web3.to_checksum_address(fixture["creator"])
    quote = Web3.to_checksum_address(fixture["quoteAsset"])
    draft = LaunchPlanV1(
        chain_id=int(fixture["chainId"]), orchestrator=Web3.to_checksum_address(fixture["orchestrator"]),
        creator=creator, nonce=nonce,
        token=LifecycleTokenConfig(
            kind=case["tokenKind"], reward_mode=case["rewardMode"], name="Smoke " + case["id"], symbol="SMOKE",
            supply=(10000 if case["tokenKind"] == 1 else 1000000) * UNIT,
            nft_unit=100 * UNIT if case["tokenKind"] == 1 else 0,
            metadata_uri="ipfs://sdk-smoke/" if case["tokenKind"] == 1 else "",
            salt=token_salt, inventory_recipient=creator, burn_on_cancel=False,
        ),
        funding=(), fee_assets=(), markets=(), buys=(), deadline=block.timestamp + 3600, executor_fee_bps=275,
    )
    predicted = predict_launch_token(client, draft, block=block)
    artifacts.result["chain"].update(chainId=fixture["chainId"], orchestrator=draft.orchestrator, creator=creator, predictedToken=predicted)
    artifacts.event("token-predicted", predictedToken=predicted, block=block)
    offerings = discover_profiles(client, fixture, artifacts, cancellation)
    token0 = int(predicted, 16) < int(quote, 16)
    markets = []
    buys = []
    for market_index, spec in enumerate(case["markets"]):
        candidates = offerings[spec["venue"]]
        require(len(candidates) == 1, f"expected one admitted current {spec['venue']} offering, found {len(candidates)}", code="PROFILE_UNAVAILABLE")
        profile = candidates[0]
        bands = oriented_bands(spec["positions"], token0)
        if spec["venue"] == "v4":
            envelope = profile.envelope
            require(envelope is not None, "bound V4 profile lacks its actual envelope", code="PROFILE_UNAVAILABLE")
            positions = tuple(LifecycleV4Position(lower, upper, LIQUIDITY, ((market_index + 1) * 100 + index + 1).to_bytes(32, "big"), MARKET_BUDGET)
                              for index, (lower, upper) in enumerate(bands))
            config = LifecyclePoolBoundV4MarketConfig(
                version=5, lp_fee_pips=3000, tick_spacing=60, sqrt_price_x96=2**96, hook_fee_pips=10000,
                fee_mode=spec.get("feeMode", 0), protocol_fee_denominator=envelope.protocol_fee_denominator,
                treasury=envelope.protocol_treasury, external_liquidity_disabled=True, oracle_config_id=fixture["oracleConfigId"],
                hook_salt=bytes(32), profile_id=profile.id, terms_digest=envelope.terms_digest,
                developer_beneficiary=envelope.beneficiary, developer_fee_bps=0, positions=positions,
            )
            encoded = encode_lifecycle_pool_bound_v4_market_config(config)
            version = 5
        else:
            positions = tuple(LifecycleAbyssPosition(lower, upper, LIQUIDITY, MARKET_BUDGET) for lower, upper in bands)
            encoded = encode_lifecycle_abyss_market_config(LifecycleAbyssMarketConfig(3, 3000, fixture["oracleConfigId"], 2**96, positions))
            version = 1
        markets.append(LifecycleMarketConfig(profile.registration["adapterId"], profile.id, quote, MARKET_BUDGET, version, encoded))
        for _ in range(case["buysPerMarket"]):
            buys.append(LifecycleInitialBuy(market_index, BUY_INPUT, 1, creator,
                                           get_sqrt_ratio_at_tick(887272) - 1 if token0 else get_sqrt_ratio_at_tick(-887272) + 1))
    amount = len(buys) * BUY_INPUT
    plan = replace(draft, markets=tuple(markets), buys=tuple(buys), fee_assets=fee_policies(predicted, quote, case),
                   funding=(LifecycleAssetFunding(quote, amount, 1, quote, amount, ZERO_ADDRESS, "0x"),))
    launch_plan_to_dict(plan)  # Validate the complete economics, not only the draft prediction.
    if any(market.config_version == 5 for market in plan.markets):
        def progress(observation):
            cancellation.check()
            artifacts.event("hook-salt-mining", observation=observation)
        finalized = asyncio.run(prepare_pool_bound_lifecycle_plan(client, plan, on_progress=progress))
        plan = finalized.plan
        artifacts.event("hook-salts-finalized", deployments=finalized.deployments)
    cancellation.check()
    require(address_equal(predict_launch_token(client, plan), predicted), "finalization changed the predicted token")
    artifacts.save("plan.json", launch_plan_to_dict(plan))
    artifacts.result["chain"].update(planHash=hash_launch_plan(plan), launchId=launch_id_of(plan), mode=case["mode"])
    artifacts.event("plan-prepared", planHash=hash_launch_plan(plan), launchId=launch_id_of(plan), predictedToken=predicted)
    return plan, predicted


def api_client(url, *, timeout=15):
    return LaunchApiClient(LaunchApiConfig(base_url=url, allow_loopback_http=True, timeout=timeout))


def stage_metadata(client, api, plan, artifacts, cancellation):
    artifacts.enter("api-stage")
    metadata = LaunchSessionMetadata(name=plan.token.name, symbol=plan.token.symbol, description="Local Python SDK token-creation smoke: " + artifacts.case_id)
    key = "python-smoke-" + secrets.token_hex(16)
    nonce = "0x" + secrets.token_hex(32)
    deadline = int(time.time()) + 600
    typed_data = build_launch_attribution_typed_data(chain_id=plan.chain_id, verifying_contract=plan.orchestrator, wallet=plan.creator,
                                                   metadata=metadata, idempotency_key=key, nonce=nonce, deadline=deadline)
    recovery = {"apiUrl": api.config.base_url, "chainId": plan.chain_id, "orchestrator": plan.orchestrator,
                "idempotencyKey": key, "typedData": typed_data, "metadata": portable(metadata), "nonce": nonce, "deadline": deadline}
    artifacts.redact.remember(key)
    artifacts.save("recovery.private.json", recovery, private=True)
    cancellation.check()
    signature = rpc(client, "eth_signTypedData_v4", [plan.creator, json.dumps(typed_data, separators=(",", ":"))])
    artifacts.redact.remember(signature)
    authorization = LaunchAttributionAuthorization(nonce=nonce, deadline=deadline, signature=signature)
    request = LaunchSessionCreateRequest(chain_id=plan.chain_id, wallet=plan.creator, metadata=metadata, authorization=authorization)
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
    require(session.get("status") == "ready_to_launch" and session.get("token") is None and session.get("transactionHash") is None,
            "metadata-only session is not ready for a new real launch", code="API_SESSION_MISMATCH", diagnostic=session)
    require(session.get("chainId") == plan.chain_id and address_equal(session.get("wallet"), plan.creator), "metadata session belongs to another creator or chain", code="API_SESSION_MISMATCH")
    require(session.get("metadata", {}).get("name") == plan.token.name and session.get("metadata", {}).get("symbol") == plan.token.symbol,
            "API metadata identity differs from the committed token", code="API_SESSION_MISMATCH")
    artifacts.result["api"].update(status="staged", sessionId=session["sessionId"], recoveryFile="recovery.private.json")
    cancellation.check()
    return recovery


def simulation_observation(launch):
    return {"admitted": launch.admitted, "confidence": launch.confidence, "backend": launch.simulation.backend,
            "marketAdmissions": launch.market_admissions, "limits": launch.limits,
            "simulation": launch.simulation, "atomicSimulation": launch.atomic_simulation,
            "tokenFactory": launch.token_factory, "tokenFactoryCodeHash": launch.token_factory_code_hash}


def validate_envelope(client, transaction, plan, limits):
    require(quantity(rpc(client, "eth_chainId", [])) == plan.chain_id and transaction["chainId"] == plan.chain_id,
            "transaction chain differs from the owned fixture", code="TRANSACTION_IDENTITY_MISMATCH")
    require(address_equal(transaction["from"], plan.creator), "transaction sender differs from committed creator", code="TRANSACTION_IDENTITY_MISMATCH")
    require(quantity(rpc(client, "eth_getTransactionCount", [plan.creator, "pending"])) == transaction["nonce"],
            "transaction nonce differs from settled account state", code="TRANSACTION_IDENTITY_MISMATCH")
    require(0 < transaction.get("gas", 0) <= limits.gas_cap(read_block(client)), "proven envelope exceeds actual gas constraints", code="TRANSACTION_LIMIT_MISMATCH")
    cap = limits.calldata_cap()
    require(cap is not None and len(bytes.fromhex(transaction["data"][2:])) <= cap,
            "proven envelope exceeds calldata constraints", code="TRANSACTION_LIMIT_MISMATCH")


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
        raise SmokeFailure("actual submitted transaction reverted", code="TRANSACTION_REVERTED", diagnostic={"receipt": receipt, "trace": trace})
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


def execute_plan(client, fork_client, plan, case, fixture, artifacts, cancellation):
    artifacts.enter("admission")
    fork = create_controlled_launch_fork(client, fork_client, isolated=True)
    limits = limit_resolver(fixture)
    cancellation.check()
    launch = plan_launch(client, plan, account=plan.creator, mode=case["mode"], limits=limits,
                         prepare_batch_size=1, confirmations=CONFIRMATIONS, fork=fork)
    observation = simulation_observation(launch)
    artifacts.save("admission.json", observation)
    artifacts.event("launch-admission", **observation)
    require(launch.admitted, "SDK refused this exact launch: " + "; ".join(launch.simulation.reasons), code="LAUNCH_NOT_ADMITTED", diagnostic=observation)
    hashes = []
    activation_receipt = None
    maximum_steps = len(plan.markets) + len(launch.approvals) + 2
    for index in range(maximum_steps + 1):
        artifacts.enter("build-next")
        cancellation.check()
        step = build_next_transaction(client, launch, account=plan.creator, transaction_hashes=hashes, confirmations=CONFIRMATIONS, fork=fork)
        if step is None:
            break
        require(index < maximum_steps, "SDK sequence exceeded the complete bounded lifecycle", code="LIFECYCLE_SEQUENCE_MISMATCH")
        artifacts.event("next-transaction-proven", transaction=step)
        transaction = step.as_transaction()
        validate_envelope(client, transaction, plan, launch.limits)
        artifacts.enter("submit-" + step.kind)
        cancellation.check()
        transaction_hash = hex_bytes(rpc(client, "eth_sendTransaction", [rpc_transaction(transaction)]))
        row = artifacts.submitted(step.kind, transaction, transaction_hash)
        hashes.append(transaction_hash)
        cancellation.check()
        artifacts.enter("receipt-" + step.kind)
        receipt = wait_receipt(client, row, artifacts, cancellation)
        progress = read_launch_progress(client, plan, confirmations=CONFIRMATIONS, transaction_hashes=hashes)
        artifacts.event("canonical-progress", progress=progress)
        if step.kind in {"atomic", "activate"}:
            require(activation_receipt is None, "more than one activation was submitted", code="LIFECYCLE_SEQUENCE_MISMATCH")
            activation_receipt = receipt
            artifacts.result["chain"]["activationTransactionHash"] = transaction_hash
    require(activation_receipt is not None, "lifecycle ended without a real activation receipt", code="LIFECYCLE_SEQUENCE_MISMATCH")
    return activation_receipt, hashes


def verify_chain(client, plan, case, predicted, receipt, hashes, artifacts, cancellation):
    artifacts.enter("verify-chain")
    progress = read_launch_progress(client, plan, confirmations=CONFIRMATIONS, transaction_hashes=hashes)
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
    require(token_observation["name"] == plan.token.name and token_observation["symbol"] == plan.token.symbol and token_observation["decimals"] == 18
            and token_observation["totalSupply"] == plan.token.supply == token_observation["initialSupply"], "actual token metadata or fixed supply differs from plan")
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
        require(identity["venue"] == (0 if spec["venue"] == "v4" else 1) and int(identity["openingSqrtPriceX96"]) == 2**96 and identity["fee"] == 3000,
                "actual market venue or opening economics differ from case")
        require(market["live"]["publicTrading"] is True and 0 < market["live"]["oracleReadyAt"] <= activation_timestamp, "actual market did not open with a real oracle genesis")
        require(market["positionCount"] == spec["positions"] == len(market["positions"]) and bool(client.eth.get_code(market["custody"])) and bool(client.eth.get_code(market["feeSource"])), "actual position count or custody runtime differs from case")
        expected_bands = oriented_bands(spec["positions"], int(predicted, 16) < int(plan.markets[market["marketIndex"]].quote_asset, 16))
        if spec["venue"] == "v4":
            locker = contract(client, market["custody"], V4_FEE_LIQUIDITY_LOCKER_V2_ABI)
            pool_id = bytes.fromhex(identity["poolId"][2:])
            custody = {"isSealed": locker.functions.isSealed(pool_id).call(), "positionCount": locker.functions.positionCount(pool_id).call(), "feeRecipient": locker.functions.feeRecipient(pool_id).call()}
            require(custody["isSealed"] is True and custody["positionCount"] == spec["positions"] and address_equal(custody["feeRecipient"], market["feeSource"]), "V4 permanent sealed custody differs from canonical market")
            config = decode_lifecycle_pool_bound_v4_market_config(plan.markets[market["marketIndex"]].config)
        else:
            locker = contract(client, market["custody"], ABYSS_POSITION_LOCKER_ABI)
            custody = {"locks": []}
            require(bool(client.eth.get_code(identity["pool"])), "canonical Abyss pool has no runtime")
        for position_index, (position, band) in enumerate(zip(market["positions"], expected_bands)):
            require(position["tickLower"] == band[0] and position["tickUpper"] == band[1]
                    and int(position["liquidity"]) == LIQUIDITY == position["liveLiquidity"] and address_equal(position["liveOwner"], market["custody"]), "actual canonical position economics/liquidity/custody differ from plan")
            if spec["venue"] == "v4":
                require(position["salt"] == hex_bytes(config.positions[position_index].salt), "actual V4 permanent position salt differs from plan")
            else:
                lock = locker.functions.locks(int(position["tokenId"])).call()
                custody["locks"].append({"tokenId": position["tokenId"], "owner": lock[0], "claimAuthority": lock[1], "feeRecipient": lock[2], "unlockTime": lock[3], "permissionlessClaim": lock[4]})
                require(all(address_equal(item, market["feeSource"]) for item in lock[:3]) and lock[3] == 0 and lock[4] is False, "Abyss NFT lock is not permanent collector-owned custody")
        market["custodyEvidence"] = custody
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
    observation = {"status": "passed", "phase": progress.phase.name, "token": token_observation, "marketCount": len(markets),
                   "positionCount": expected_positions, "markets": markets, "orderedBuys": buys, "confirmations": CONFIRMATIONS,
                   "activationTransactionHash": hex_bytes(receipt["transactionHash"]), "activationBlockNumber": quantity(receipt["blockNumber"]),
                   "activationBlockHash": receipt["blockHash"]}
    artifacts.result["chain"].update(observation)
    artifacts.save("chain.json", observation)
    artifacts.event("chain-verified", **observation)


def verify_publication(session, detail, plan, predicted, receipt, session_id):
    require(session.get("sessionId") == session_id and session.get("chainId") == plan.chain_id and address_equal(session.get("wallet"), plan.creator)
            and address_equal(session.get("token"), predicted) and session.get("transactionHash", "").lower() == hex_bytes(receipt["transactionHash"]), "published session does not bind the actual token and activation", code="API_PUBLICATION_MISMATCH")
    require(session.get("status") in {"optimistic", "final"} and session.get("canonicalStatus") in {"optimistic", "final"}, "API session is not canonically published", code="API_PUBLICATION_MISMATCH")
    item = detail.get("item")
    require(isinstance(item, dict) and detail.get("chainId") == plan.chain_id and int(detail.get("indexedBlock", "-1")) >= quantity(receipt["blockNumber"]), "public API launch is missing or predates activation", code="API_PUBLICATION_MISMATCH")
    require(address_equal(item.get("token"), predicted) and item.get("transactionHash", "").lower() == hex_bytes(receipt["transactionHash"])
            and item.get("blockHash", "").lower() == hex_bytes(receipt["blockHash"]) and int(item.get("blockNumber", "-1")) == quantity(receipt["blockNumber"])
            and item.get("canonicalStatus") in {"optimistic", "final"}, "public API representation does not match actual canonical activation", code="API_PUBLICATION_MISMATCH")
    metadata = item.get("metadata", {}).get("document", {})
    launched_token = item.get("launchedToken", {})
    require(metadata.get("name") == plan.token.name and metadata.get("symbol") == plan.token.symbol
            and launched_token.get("name") == plan.token.name and launched_token.get("symbol") == plan.token.symbol,
            "public API did not publish the staged and actual token identity", code="API_PUBLICATION_MISMATCH")


def publish_activation(api, recovery, plan, predicted, receipt, timeout_seconds, artifacts, cancellation):
    artifacts.enter("api-publish")
    deadline = time.monotonic() + timeout_seconds
    transaction_hash = hex_bytes(receipt["transactionHash"])
    request = LaunchSessionPublishRequest(plan.chain_id, transaction_hash)
    session_id = recovery["session"]["sessionId"]
    capability = recovery["session"]["capability"]
    recovery["activationTransactionHash"] = transaction_hash
    artifacts.save("recovery.private.json", recovery, private=True)
    last_pending = None
    while time.monotonic() < deadline:
        cancellation.check()
        remaining = deadline - time.monotonic()
        require(remaining > 0, "publication exhausted its bounded indexing deadline", code="API_INDEX_TIMEOUT")
        active_client = api_client(api.config.base_url, timeout=min(api.config.timeout, remaining))
        try:
            session = active_client.publish_launch_upload_session(plan.chain_id, session_id, capability, request)
        except LaunchPublishPending as pending:
            last_pending = pending.session
            artifacts.redact.learn(last_pending)
            artifacts.result["api"].update(status="pending", lastSession=last_pending)
            recovery["lastPublishResponse"] = last_pending
            artifacts.save("recovery.private.json", recovery, private=True)
            artifacts.event("publish-pending", session=last_pending, retryAfterMs=pending.retry_after_ms, remainingSeconds=max(0, deadline - time.monotonic()))
            require(last_pending.get("status") in {"transaction_submitted", "awaiting_indexer"}, "202 returned a terminal or unknown session state", code="API_PUBLICATION_MISMATCH")
            require(last_pending.get("token") is None or address_equal(last_pending.get("token"), predicted), "pending session names a different token", code="API_PUBLICATION_MISMATCH")
            cancellation.sleep(min(pending.retry_after_ms / 1000, max(0, deadline - time.monotonic())))
            continue
        artifacts.redact.learn(session)
        artifacts.event("publish-returned", session=session)
        recovery["lastPublishResponse"] = session
        artifacts.save("recovery.private.json", recovery, private=True)
        artifacts.result["api"]["publishedSession"] = session
        require(address_equal(session.get("token"), predicted) and session.get("status") in {"optimistic", "final"}
                and session.get("canonicalStatus") in {"optimistic", "final"}, "API returned success without actual canonical publication", code="API_PUBLICATION_MISMATCH", diagnostic=session)
        artifacts.enter("api-verify")
        cancellation.check()
        remaining = deadline - time.monotonic()
        require(remaining > 0, "publication exhausted its bounded verification deadline", code="API_INDEX_TIMEOUT")
        detail = api_client(api.config.base_url, timeout=min(api.config.timeout, remaining)).get_launch(plan.chain_id, predicted)
        artifacts.event("public-launch-returned", representation=detail)
        artifacts.save("api.json", {"session": session, "launch": detail})
        verify_publication(session, detail, plan, predicted, receipt, session_id)
        artifacts.result["api"].update(status="passed", canonicalStatus=session["canonicalStatus"], token=predicted, transactionHash=transaction_hash, representation=detail)
        artifacts.event("publication-verified", token=predicted, transactionHash=transaction_hash)
        return
    raise SmokeFailure("real API did not index and publish the activation before the bounded deadline", code="API_INDEX_TIMEOUT", diagnostic={"lastSession": last_pending, "transactionHash": transaction_hash, "timeoutSeconds": timeout_seconds})


def run(args, artifacts, cancellation):
    require(args.fixture is not None and args.case is not None, "--fixture and exactly one --case are required", code="INVALID_CONFIGURATION")
    fixture = load_fixture(args.fixture)
    case = next((case for case in fixture["cases"] if case["id"] == args.case), None)
    require(case is not None, "--case must select a fixture catalogue ID; use --list", code="INVALID_CONFIGURATION")
    source_url, fork_url, api_url = validate_fixture(fixture, args.api_url)
    require(not args.execute or args.chain_only or api_url is not None, "--execute requires an explicit real loopback API; use --chain-only to omit API", code="INVALID_CONFIGURATION")
    artifacts.result.update(scope="chain-only" if args.execute and args.chain_only else "end-to-end" if args.execute else "plan-only", execution="requested" if args.execute else "not-run")
    artifacts.result["api"].update(status="skipped" if args.execute and args.chain_only else "not-run", reason="explicit --chain-only" if args.chain_only and args.execute else "--execute required" if not args.execute else None, url=api_url)
    nonce = secrets.randbits(256)
    token_salt = "0x" + secrets.token_hex(32)
    artifacts.save("run.json", {"fixture": fixture, "case": case, "nonce": str(nonce), "tokenSalt": token_salt,
                                "execute": args.execute, "chainOnly": args.chain_only, "apiUrl": api_url, "publishTimeoutSeconds": args.publish_timeout_seconds})
    client = Web3(JournalHTTPProvider(source_url, artifacts, node="execution", execute=args.execute))
    artifacts.enter("provenance")
    cancellation.check()
    node = rpc(client, "anvil_nodeInfo", [])
    require(quantity(rpc(client, "eth_chainId", [])) == int(fixture["chainId"]), "actual RPC chain differs from fixture", code="CHAIN_IDENTITY_MISMATCH")
    require(fixture["creator"].lower() in {account.lower() for account in rpc(client, "eth_accounts", [])}, "creator is not unlocked on the owned execution Anvil", code="CHAIN_IDENTITY_MISMATCH")
    require(bool(client.eth.get_code(Web3.to_checksum_address(fixture["orchestrator"]))), "current orchestrator has no actual runtime", code="CHAIN_IDENTITY_MISMATCH")
    quote = contract(client, fixture["quoteAsset"], ERC20_ABI)
    require(quote.functions.decimals().call() == fixture["quoteDecimals"], "actual wrapped-native decimals differ from fixture", code="CHAIN_IDENTITY_MISMATCH")
    artifacts.event("execution-node-verified", node=node, block=read_block(client), executionLimits=fixture["executionLimits"])
    artifacts.enter("plan")
    plan, predicted = construct_plan(client, fixture, case, nonce, token_salt, artifacts, cancellation)
    if not args.execute:
        artifacts.enter("plan-only")
        return "Plan prepared; launch not executed"
    fork_client = Web3(JournalHTTPProvider(fork_url, artifacts, node="simulation", execute=True))
    rpc(fork_client, "anvil_nodeInfo", [])
    require(quantity(rpc(fork_client, "eth_chainId", [])) == plan.chain_id
            and plan.creator.lower() in {account.lower() for account in rpc(fork_client, "eth_accounts", [])}, "simulation Anvil must mirror the chain and unlocked creator without impersonation", code="CHAIN_IDENTITY_MISMATCH")
    api = api_client(api_url) if not args.chain_only else None
    recovery = stage_metadata(client, api, plan, artifacts, cancellation) if api is not None else None
    artifacts.result["execution"] = "executed"
    artifacts.result["chain"]["status"] = "executing"
    receipt, hashes = execute_plan(client, fork_client, plan, case, fixture, artifacts, cancellation)
    verify_chain(client, plan, case, predicted, receipt, hashes, artifacts, cancellation)
    if args.chain_only:
        artifacts.enter("chain-only-complete")
        return "Chain-only launch verified; API skipped (not full end-to-end)"
    publish_activation(api, recovery, plan, predicted, receipt, args.publish_timeout_seconds, artifacts, cancellation)
    artifacts.enter("end-to-end-complete")
    return "Token launch and real API publication verified end-to-end"


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SmokeFailure(message, code="INVALID_CONFIGURATION")


def parser():
    argument_parser = Parser(description=__doc__)
    argument_parser.add_argument("--fixture", type=Path, help="Required shared fixtureOnly local JSON from the owned fork setup")
    argument_parser.add_argument("--case", help="Exactly one catalogue ID, never an implicit all-case run")
    argument_parser.add_argument("--output", type=Path, help="Required fresh exclusive run directory; never overwrites a run")
    argument_parser.add_argument("--list", action="store_true", help="List the fixture catalogue unsigned, without contacting any RPC/API")
    argument_parser.add_argument("--execute", action="store_true", help="Explicitly allow signing, fork simulations, source chain transactions and API writes")
    argument_parser.add_argument("--chain-only", action="store_true", help="Explicitly omit API work; success is only a chain proof")
    argument_parser.add_argument("--api-url", help="Override fixture apiUrl with an actual owned loopback API; no default endpoint")
    argument_parser.add_argument("--publish-timeout-seconds", type=positive_seconds, default=90, help="Bounded API indexing/publication deadline (default90)")
    return argument_parser


def argument_value(argv, name):
    for index, value in enumerate(argv):
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
        if value == name and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            return argv[index + 1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    artifacts = None
    output = None
    cancellation = Cancellation()
    original_handlers = {}
    try:
        # Reserve output before parsing/reading configuration so early failures are durable.
        output = argument_value(argv, "--output")
        if output is not None and not any(flag in argv for flag in ("--help", "-h", "--list")):
            artifacts = Artifacts(Path(output), argument_value(argv, "--case") or "unknown")
        args = parser().parse_args(argv)
        require(args.fixture is not None, "--fixture is required; no live endpoint is selected", code="INVALID_CONFIGURATION")
        if args.list:
            fixture = load_fixture(args.fixture)
            print(json.dumps({"sdk": "python", "signed": False, "rpcContacted": False, "cases": fixture["cases"]}, indent=2))
            return 0
        require(args.output is not None, "--output must name a fresh exclusive run directory", code="INVALID_CONFIGURATION")
        if artifacts is None:
            artifacts = Artifacts(args.output, args.case or "unknown")
        for signum in (signal.SIGINT, signal.SIGTERM):
            original_handlers[signum] = signal.signal(signum, cancellation.signal)
        message = run(args, artifacts, cancellation)
        cancellation.check()
        artifacts.finish()
        print(message + "; artifacts: " + str(artifacts.directory))
        return 0
    except SystemExit as error:
        if error.code == 0:
            return 0
        raise
    except BaseException as error:
        if artifacts is None and output is not None and not Path(output).exists():
            try:
                artifacts = Artifacts(Path(output), argument_value(argv, "--case") or "unknown")
            except OSError:
                pass  # No diagnostic destination can be created; still report the original error.
        if artifacts is not None:
            if artifacts.stage.startswith("api-"):
                artifacts.result["api"]["status"] = "failed"
            elif artifacts.result["chain"].get("status") == "executing":
                artifacts.result["chain"]["status"] = "failed"
            artifacts.finish(error)
            safe = artifacts.redact(error_details(error))
            print(json.dumps({"status": "failed", "stage": artifacts.stage, "error": safe, "artifacts": str(artifacts.directory)}, indent=2), file=sys.stderr)
        else:
            print(json.dumps({"status": "failed", "error": Redactor()(error_details(error))}, indent=2), file=sys.stderr)
        return 1
    finally:
        for signum, handler in original_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
