"""Offline case, funding, image, signer and original-receipt regressions.

Fake RPC/API boundaries exercise the real codecs and local signatures only. These
are not evidence of a live route, on-chain admission, upload or launch.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, replace
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from black_market_sdk import LaunchBlock, LaunchApiClient, LaunchApiConfig, LaunchApiResponse
from black_market_sdk import lifecycle
from examples import launch_examples as example
from examples import _launch_support as support
from examples import launch_submit as submit
from test_launch_examples import (
    API_ORIGIN, BLOCK_HASH, CREATOR, TOKEN, TX_HASH, Artifacts, ExampleFailure,
    clean_progress, construct_case, kyber_build_fixture, metadata_document,
    metadata_recovery, metadata_session, no_call, publication_context,
    proved_review_launch, signed_metadata, token_draft,
)

NEW_CASES = [
    ("launch_erc20_multi_quote.py", 3, 3, 0),
    ("launch_erc20_depth_v4.py", 1, 4, 0),
    ("launch_erc20_dividends_multiquote_mixed.py", 3, 7, 2),
]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS0AAAAASUVORK5CYII=")
UPLOAD_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.mark.parametrize("filename,market_count,position_count,reward_mode", NEW_CASES)
def test_new_cases_preserve_economics_and_fund_every_quote(construct_case, filename, market_count, position_count, reward_mode):
    case, plan, predicted, artifacts = construct_case(filename)
    assert case["mode"] == "staged" and plan.token.kind == 0 and plan.token.reward_mode == reward_mode
    assert plan.token.supply == 1_000_000 * 10**18 and plan.executor_fee_bps == 275
    assert len(plan.markets) == market_count and len(plan.buys) == market_count
    configs = [lifecycle.decode_lifecycle_pool_bound_v4_market_config(m.config) if m.config_version == 6
               else lifecycle.decode_lifecycle_abyss_market_config(m.config) for m in plan.markets]
    assert sum(len(c.positions) for c in configs) == position_count
    assert sum(m.token_budget for m in plan.markets) == plan.token.supply
    v4_quotes = [m.quote_asset.lower() for m in plan.markets if m.config_version == 6]
    assert len(v4_quotes) == len(set(v4_quotes))
    assert [int(f.asset, 16) for f in plan.funding] == sorted(int(f.asset, 16) for f in plan.funding)
    for funding, conversion in zip(plan.funding, artifacts.economic_pricing["conversions"]):
        indices = conversion["buyIndices"]
        assert funding.input_amount == len(indices) * 10**13
        assert sum(plan.buys[i].quote_amount_in for i in indices) == funding.amount
        if conversion.get("nativeWrap") is True:
            # Payable native-wrap escrow funding: exact 1:1, no router evidence.
            assert funding.kind == lifecycle.LifecycleFundingKind.NATIVE_WRAP
            assert funding.input_asset == funding.asset and funding.target == example.ZERO_ADDRESS and funding.data == b""
            assert funding.amount == funding.input_amount == len(indices) * 10**13
        else:
            assert funding.kind == lifecycle.LifecycleFundingKind.SWAP and funding.input_asset == example.ZERO_ADDRESS
            assert funding.amount == int(conversion["amountOutMinimum"])
            assert funding.target == support.KYBER_ROUTER and funding.data == conversion["transaction"]["data"]
            # Conversion swap expiry is decoupled from, and within, the plan deadline.
            assert conversion["expiresAt"] <= plan.deadline
    for market, pricing in zip(plan.markets, artifacts.economic_pricing["markets"]):
        usd, decimals, root = int(pricing["quote"]["priceUsdX18"]), pricing["quoteDecimals"], int(pricing["sqrtPriceX96"])
        ratio = Fraction(root * root, 1 << 192) if int(predicted, 16) < int(market.quote_asset, 16) else Fraction(1 << 192, root * root)
        valuation = ratio * plan.token.supply * usd / 10**decimals
        assert int(pricing["openingValuationUsdX18"]) == valuation.numerator // valuation.denominator
        assert 5000 * 10**18 <= int(pricing["openingValuationUsdX18"]) < 5031 * 10**18
        assert pricing["tickSpacing"] == 60 and pricing["lpFeePips"] == 3000
        assert pricing["positionGaps"] == "adjacent" and pricing["extendFinalRangeToBoundary"] is True
    assert all(policy.rewards_bps == (4000 if reward_mode else 0) for policy in plan.fee_assets)
    assert artifacts.economic_pricing["totalNativeBuyInputWei"] == str(market_count * 10**13)
    artifacts.finish()


@pytest.mark.parametrize("filename", [NEW_CASES[0][0], NEW_CASES[2][0]])
def test_multiquote_token_order_is_per_market_not_global(construct_case, filename):
    predicted = "0x3000000000000000000000000000000000000000"
    _, plan, token, artifacts = construct_case(filename, predicted_token=predicted)
    assert token == predicted
    assert [m["tokenIsCurrency0"] for m in artifacts.economic_pricing["markets"]] == [False, True, False]
    assert [m["quoteDecimals"] for m in artifacts.economic_pricing["markets"]] == [18, 6, 18]
    assert artifacts.economic_pricing["quotes"][1]["priceSource"] == "usdPeg"
    assert artifacts.economic_pricing["quotes"][1]["priceUsdX18"] == str(10**18)
    for market in plan.markets:
        config = (lifecycle.decode_lifecycle_pool_bound_v4_market_config(market.config) if market.config_version == 6
                  else lifecycle.decode_lifecycle_abyss_market_config(market.config))
        assert len(config.positions) > 0
    artifacts.finish()


@pytest.mark.parametrize("token0", [True, False])
def test_depth_uses_increasing_source_inventory_adjacent_ranges_and_tail(token0):
    pricing = example.derive_market_pricing(supply=1_000_000 * 10**18, budget=1_000_000 * 10**18,
                                          token0=token0, quote_usd_x18=2000 * 10**18, target=5000 * 10**18,
                                          count=4, venue="v4", distribution_preset="early-scarcity")
    positions = pricing["positions"]
    maxima = [int(p["maxTokenAmount"]) for p in positions]
    assert all(left < right for left, right in zip(maxima, maxima[1:]))
    for left, right in zip(positions, positions[1:]):
        assert left["tickUpper"] == right["tickLower"] if token0 else left["tickLower"] == right["tickUpper"]
    boundary = 887272 // 60 * 60
    assert positions[-1]["tickUpper" if token0 else "tickLower"] == (boundary if token0 else -boundary)
    assert sum(maxima) == 1_000_000 * 10**18
    assert sum(int(p["expectedMintDebit"]) for p in positions) + int(pricing["roundingBurn"]) == sum(maxima)


@pytest.mark.parametrize("filename", [NEW_CASES[0][0], NEW_CASES[2][0]])
def test_proved_buy_review_scales_each_quotes_decimals(construct_case, filename):
    _, plan, _, artifacts = construct_case(filename)
    rows = example.opening_buy_review(proved_review_launch(plan), artifacts.economic_pricing)
    for buy, row in zip(plan.buys, rows):
        pricing = artifacts.economic_pricing["markets"][buy.market_index]
        numerator = int(row["quoteSpent"]) * int(pricing["quote"]["priceUsdX18"]) * 10**18
        denominator = int(row["tokenOut"]) * 10**pricing["quoteDecimals"]
        assert int(row["allInExecutionUnitPriceUsdX18"]) == numerator // denominator
        assert row["quoteAsset"].lower() == plan.markets[buy.market_index].quote_asset.lower()
        assert row["quoteDecimals"] == pricing["quoteDecimals"]
    artifacts.finish()


def test_no_native_balance_stops_unsigned_and_never_requests_build(construct_case, monkeypatch):
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params: "0x0" if method == "eth_getBalance" else hex(4663))
    monkeypatch.setattr(example, "read_kyber_build", no_call)
    with pytest.raises(ExampleFailure) as failure:
        construct_case(NEW_CASES[0][0])
    assert failure.value.code == "QUOTE_UNFUNDED"


def test_abyss_only_construction_does_not_require_v4_profile(construct_case, monkeypatch):
    deployment = example.get_launch_addresses(4663)
    from black_market_sdk import get_known_lifecycle_profile, KnownLifecycleProfile
    abyss = get_known_lifecycle_profile(chain_id=4663, orchestrator=deployment.orchestrator, key=KnownLifecycleProfile.ABYSS_3).profile
    monkeypatch.setattr(example, "discover_profiles", lambda *_args, **_kwargs: {"v4": [], "abyss": [abyss]})
    _, plan, _, artifacts = construct_case("launch_erc20_abyss.py")
    assert len(plan.markets) == 1 and plan.markets[0].config_version == 1
    assert artifacts.economic_pricing["markets"][0]["oracle"]["factory"].lower() == abyss.registration.factory.lower()
    artifacts.finish()


def registered_oracle_log(factory, move, cardinality, block=321):
    config_id = example.hex_bytes(Web3.keccak(encode(["uint24", "uint16"], [move, cardinality])))
    return {"address": factory, "topics": [example.hex_bytes(Web3.keccak(text="OracleConfigRegistered(bytes32,uint24,uint16)")), config_id],
            "data": example.hex_bytes(encode(["uint24", "uint16"], [move, cardinality])),
            "blockNumber": hex(block), "blockHash": BLOCK_HASH, "transactionHash": TX_HASH,
            "logIndex": "0x0", "transactionIndex": "0x0", "removed": False}


@pytest.mark.parametrize("venue", ["v4", "abyss"])
@pytest.mark.parametrize("registered", [True, False])
def test_usdg_oracle_discovery_is_pinned_registered_and_venue_owned(monkeypatch, venue, registered):
    from black_market_sdk import get_known_lifecycle_profile, KnownLifecycleProfile
    deployment = example.get_launch_addresses(4663)
    key = KnownLifecycleProfile.V4_FIXED_FEE_POOL if venue == "v4" else KnownLifecycleProfile.ABYSS_3
    profile = get_known_lifecycle_profile(chain_id=4663, orchestrator=deployment.orchestrator, key=key).profile
    factory = profile.envelope.graph.oracle_factory if venue == "v4" else profile.registration.factory
    block = LaunchBlock(321, BLOCK_HASH, 100, 32000000, 1)
    logs = [registered_oracle_log(factory, 17, 4096), registered_oracle_log(factory, 6, 4096)] if registered else []
    configs = {log["topics"][1]: (17 if index == 0 else 6, 4096) for index, log in enumerate(logs)}
    calls = []
    def mapping(config_id):
        def read(*, block_identifier):
            assert block_identifier == 321
            calls.append(example.hex_bytes(config_id))
            return configs.get(example.hex_bytes(config_id), (0, 0))
        return SimpleNamespace(call=read)
    def selected_contract(_client, address, _abi):
        assert address.lower() == factory.lower()
        return SimpleNamespace(functions=SimpleNamespace(oracleConfigs=mapping))
    def rpc(_client, method, params):
        assert method == "eth_getLogs"
        assert params[0]["address"].lower() == factory.lower() and params[0]["toBlock"] == hex(321)
        return logs
    monkeypatch.setattr(example, "contract", selected_contract)
    monkeypatch.setattr(example, "rpc", rpc)
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    quote = {"symbol": "USDG", "asset": example.ROBINHOOD_USDG, "decimals": 6}
    client = SimpleNamespace(codec=Web3().codec)
    if not registered:
        with pytest.raises(ExampleFailure) as failure:
            example.discover_oracle_config(client, profile, quote, block, example.Cancellation())
        assert failure.value.code == "ORACLE_CONFIG_UNAVAILABLE" and "no registered" in str(failure.value)
    else:
        result = example.discover_oracle_config(client, profile, quote, block, example.Cancellation())
        assert result["maxAbsTickMove"] == 6 and result["oracleConfigId"] == logs[1]["topics"][1]
        assert result["blockNumber"] == "321" and len(calls) == 2


def test_unregistered_oracle_log_cannot_supply_config_id(monkeypatch):
    from black_market_sdk import get_known_lifecycle_profile, KnownLifecycleProfile
    deployment = example.get_launch_addresses(4663)
    profile = get_known_lifecycle_profile(chain_id=4663, orchestrator=deployment.orchestrator, key=KnownLifecycleProfile.V4_FIXED_FEE_POOL).profile
    factory = profile.envelope.graph.oracle_factory
    block = LaunchBlock(321, BLOCK_HASH, 100, 32000000, 1)
    monkeypatch.setattr(example, "rpc", lambda *_args: [registered_oracle_log(factory, 17, 4096)])
    monkeypatch.setattr(example, "contract", lambda *_args: SimpleNamespace(functions=SimpleNamespace(
        oracleConfigs=lambda _id: SimpleNamespace(call=lambda **_kwargs: (0, 0)))))
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    with pytest.raises(ExampleFailure) as failure:
        example.discover_oracle_config(SimpleNamespace(codec=Web3().codec), profile,
                                       {"symbol": "USDG"}, block, example.Cancellation())
    assert failure.value.code == "ORACLE_CONFIG_UNAVAILABLE"


@pytest.mark.parametrize("change", ["router", "value", "token", "recipient", "input", "minimum", "selector", "executor", "flags", "permit", "expiry"])
def test_kyber_swap_binding_rejects_changed_commitment(change):
    quote = example.get_addresses(4663).weth
    escrow = example.get_launch_addresses(4663).funding_escrow
    amount, deadline = 10**13, 400
    build = kyber_build_fixture(quote_asset=quote, amount_in=amount, escrow=escrow, deadline=deadline)
    if change == "router":
        build["routerAddress"] = TOKEN
    elif change == "value":
        build["transaction"]["value"] = "1"
    elif change == "expiry":
        build["expiresAt"] += 1
    elif change == "selector":
        build["transaction"]["data"] = "0xdeadbeef" + build["transaction"]["data"][10:]
    else:
        from eth_abi import decode
        raw = bytes.fromhex(build["transaction"]["data"][10:])
        execution = list(decode([support.KYBER_SWAP_EXECUTION_TYPE], raw)[0])
        desc = list(execution[3])
        if change == "executor":
            execution[0] = TOKEN
        else:
            index, value = {"token": (1, TOKEN), "recipient": (6, CREATOR), "input": (7, amount + 1),
                            "minimum": (8, 1), "flags": (9, 0x42), "permit": (10, b"permit")}[change]
            desc[index] = value
            execution[3] = tuple(desc)
        build["transaction"]["data"] = example.hex_bytes(support.KYBER_SWAP_SELECTOR + encode([support.KYBER_SWAP_EXECUTION_TYPE], [tuple(execution)]))
    with pytest.raises(ExampleFailure) as failure:
        support.validate_kyber_build(build, quote_asset=quote, amount_in=amount, escrow=escrow, deadline=deadline, now=100)
    assert failure.value.code == "INVALID_KYBER_BUILD"


@pytest.mark.parametrize("enabled,spender,code_hash", [
    (False, support.KYBER_ROUTER, None), (True, TOKEN, None), (True, support.KYBER_ROUTER, BLOCK_HASH),
])
def test_unadmitted_kyber_target_stops_unsigned_with_exact_refusal(tmp_path, monkeypatch, enabled, spender, code_hash):
    block = LaunchBlock(321, BLOCK_HASH, 100, 32000000, 1)
    deployment = example.get_launch_addresses(4663)
    code = "0x6000"
    expected_hash = example.hex_bytes(Web3.keccak(hexstr=code)) if code_hash is None else code_hash
    def call(value):
        return lambda *_args: SimpleNamespace(call=lambda **_kwargs: value)
    def contracts(_client, address, _abi):
        if address.lower() == deployment.orchestrator.lower():
            functions = SimpleNamespace(fundingEscrow=call(deployment.funding_escrow), registry=call(deployment.registry))
        elif address.lower() == deployment.funding_escrow.lower():
            functions = SimpleNamespace(core=call(deployment.orchestrator), registry=call(deployment.registry), wrappedNative=call(deployment.wrapped_native))
        else:
            functions = SimpleNamespace(core=call(deployment.orchestrator), fundingTarget=call((spender, expected_hash, enabled)))
        return SimpleNamespace(functions=functions)
    monkeypatch.setattr(example, "contract", contracts)
    monkeypatch.setattr(example, "rpc", lambda *_args: code)
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    artifacts = Artifacts(tmp_path / "admission", "funding")
    with pytest.raises(ExampleFailure) as failure:
        example.read_funding_authority(None, {"orchestrator": deployment.orchestrator, "creator": CREATOR}, block, artifacts)
    assert str(failure.value) == "Kyber conversion is not admitted by this launch deployment. No separate swap will be submitted."
    assert not artifacts.receipts
    artifacts.finish(failure.value)


@pytest.fixture
def kyber_http_server():
    calls, state = [], {"status": 200}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, payload))
            body = state["response"](payload)
            data = json.dumps(body).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_kyber_http_build_uses_native_boundary_exact_escrow_and_plan_expiry(kyber_http_server):
    url, state, calls = kyber_http_server
    escrow, quote = example.get_launch_addresses(4663).funding_escrow, example.ROBINHOOD_USDG
    state["response"] = lambda body: kyber_build_fixture(quote_asset=body["tokenOut"], amount_in=int(body["amountIn"]),
                                                      escrow=body["recipient"], deadline=int(body["deadline"]))
    build = support.read_kyber_build(url, quote_asset=quote, amount_in=2 * 10**13, escrow=escrow, deadline=400)
    support.validate_kyber_build(build, quote_asset=quote, amount_in=2 * 10**13, escrow=escrow, deadline=400, now=100)
    assert calls == [("/api/v1/quote/kyber/build?chainId=4663", {"tokenIn": example.ZERO_ADDRESS, "tokenOut": quote,
                      "amountIn": str(2 * 10**13), "sender": escrow, "recipient": escrow, "slippageBps": 50, "deadline": "400"})]


def test_kyber_http_refusal_is_verbatim_and_never_retried(kyber_http_server):
    url, state, calls = kyber_http_server
    state.update(status=503, response=lambda _body: {"code": "NO_CONNECTED_ROUTE", "error": "No native route for this quote"})
    with pytest.raises(ExampleFailure) as failure:
        support.read_kyber_build(url, quote_asset=example.ROBINHOOD_USDG, amount_in=10**13,
                                escrow=CREATOR, deadline=400)
    assert failure.value.code == "NO_CONNECTED_ROUTE" and str(failure.value) == "No native route for this quote"
    assert len(calls) == 1


@pytest.mark.parametrize("when", ["before-review", "before-sign", "terminal"])
def test_executor_reorg_barrier_never_replays_orphaned_work(tmp_path, monkeypatch, when):
    plan, client = token_draft(), object()
    artifacts = Artifacts(tmp_path / when, "reorg")
    original = lifecycle.LifecycleReceiptReference(TX_HASH, observed_block_number=16, observed_block_hash=BLOCK_HASH)
    submitted_hash = "0x" + "ef" * 32
    envelope = {"chainId": 4663, "from": CREATOR, "to": plan.orchestrator, "data": "0x01", "value": 0, "gas": 100000, "gasPrice": 1, "nonce": 0}
    held = SimpleNamespace(kind="atomic", step_id="atomic", as_transaction=lambda: envelope,
                           admission=SimpleNamespace(admitted=True, limits=None))
    launch = SimpleNamespace(transactions=(held,), receipt_references=(original,), approvals=(), mode="atomic")
    reads, sent = [], []
    done = [False]
    def progress(_client, _plan, **options):
        reads.append(options["receipts"])
        reorg = when == "before-review" or when == "before-sign" and len(reads) > 1 or when == "terminal" and done[0]
        statuses = tuple(lifecycle.LifecycleReceiptStatus(r.transaction_hash, r.transaction_hash,
                         "reorged" if reorg else "confirmed", False, r.observed_block_number, r.observed_block_hash, 1)
                         for r in options["receipts"])
        return clean_progress(statuses=statuses)
    def next_transaction(_client, _launch, **options):
        if "reviewed_transaction" in options:
            return held
        done[0] = True
        return None
    def send(kind, transaction):
        sent.append(kind)
        return artifacts.broadcast_attempt(kind, transaction, submitted_hash)
    def wait(_client, row, _artifacts, _cancel):
        receipt = {"transactionHash": submitted_hash, "blockNumber": "0x11", "blockHash": BLOCK_HASH, "status": "0x1"}
        artifacts.included(row, receipt)
        return receipt
    monkeypatch.setattr(example, "read_launch_progress", progress)
    monkeypatch.setattr(example, "build_next_transaction", next_transaction)
    monkeypatch.setattr(example, "validate_envelope", lambda *_args: None)
    monkeypatch.setattr(example, "wait_receipt", wait)
    wallet = SimpleNamespace(client=client, account=SimpleNamespace(address=CREATOR), send_transaction=send)
    api = SimpleNamespace(get_launch_upload_session=lambda *_args: metadata_session())
    with pytest.raises(ExampleFailure) as failure:
        example.execute_plan(client, launch, plan, artifacts, example.Cancellation(), wallet=wallet, api=api, recovery=metadata_recovery())
    assert failure.value.code == "RECEIPT_REORGED" and "launch_submit.py" in str(failure.value)
    assert sent == (["atomic"] if when == "terminal" else [])
    assert reads[0][0] == original
    if when == "terminal":
        assert artifacts.receipts[0]["reference"]["observed_block_number"] == 17
    artifacts.finish(failure.value)


def test_verify_chain_stops_on_reorg_even_if_phase_is_active(tmp_path, monkeypatch, publication_context):
    client, receipt, references = publication_context
    artifacts = Artifacts(tmp_path / "verify-reorg", "reorg")
    status = lifecycle.LifecycleReceiptStatus(TX_HASH, TX_HASH, "reorged", False, 16, BLOCK_HASH, 0)
    monkeypatch.setattr(example, "read_launch_progress", lambda *_args, **_kwargs: clean_progress(statuses=(status,)))
    monkeypatch.setattr(example, "contract", no_call)
    case = {"mode": "atomic", "markets": [{"positions": 1}]}
    with pytest.raises(ExampleFailure) as failure:
        example.verify_chain(client, token_draft(), case, TOKEN, receipt, [TX_HASH], references, artifacts, example.Cancellation())
    assert failure.value.code == "RECEIPT_REORGED"
    artifacts.finish(failure.value)


@pytest.mark.parametrize("when", ["before-publication", "before-mutation", "before-success"])
def test_publication_revalidates_original_receipt_at_each_boundary(tmp_path, monkeypatch, publication_context, when):
    client, receipt, references = publication_context
    plan = token_draft()
    artifacts = Artifacts(tmp_path / when, "publication-reorg")
    state, mutations = [when == "before-publication"], []
    published = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic")
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol}, "metadata": {"document": metadata_document()}}
    def rpc(_client, method, _params):
        assert method == "eth_getTransactionReceipt"
        return {**receipt, "blockHash": "0x" + "99" * 32} if state[0] else dict(receipt)
    def get_session(*_args):
        if when == "before-mutation":
            state[0] = True
        return metadata_session()
    def publish(*_args):
        mutations.append("publish")
        return published
    def detail(*_args):
        if when == "before-success":
            state[0] = True
        return {"chainId": 4663, "indexedBlock": "16", "item": item}
    api = SimpleNamespace(config=SimpleNamespace(base_url=API_ORIGIN, timeout=15),
                          get_launch_upload_session=get_session, publish_launch_upload_session=publish, get_launch=detail)
    monkeypatch.setattr(example, "rpc", rpc)
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    with pytest.raises(ExampleFailure) as failure:
        example.publish_activation(client, api, metadata_recovery(), plan, TOKEN, receipt, references, 90, artifacts, example.Cancellation())
    assert failure.value.code == "RECEIPT_REORGED" and artifacts.result["api"]["status"] != "passed"
    assert mutations == (["publish"] if when == "before-success" else [])
    artifacts.finish(failure.value)


def test_publication_checks_configured_depth_and_retains_canonical_success(tmp_path, monkeypatch, publication_context):
    client, receipt, references = publication_context
    plan, depths = token_draft(), []
    references = (replace(references[0], confirmations=3),)
    def progress(_client, _plan, **options):
        depths.append(options["confirmations"])
        status = lifecycle.LifecycleReceiptStatus(TX_HASH, TX_HASH, "confirmed", False, 16, BLOCK_HASH, 3)
        return clean_progress(statuses=(status,))
    monkeypatch.setattr(example, "read_launch_progress", progress)
    published = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic")
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol}, "metadata": {"document": metadata_document()}}
    api = SimpleNamespace(config=SimpleNamespace(base_url=API_ORIGIN, timeout=15), get_launch_upload_session=lambda *_args: metadata_session(),
                          publish_launch_upload_session=lambda *_args: published,
                          get_launch=lambda *_args: {"chainId": 4663, "indexedBlock": "16", "item": item})
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    artifacts = Artifacts(tmp_path / "success", "publication")
    example.publish_activation(client, api, {**metadata_recovery(), "confirmations": 3}, plan, TOKEN, receipt,
                               references, 90, artifacts, example.Cancellation())
    assert depths == [3, 3, 3] and artifacts.result["api"]["status"] == "passed"
    artifacts.finish()


def test_receipt_reference_json_roundtrips_wide_blocks_and_replacements():
    replacement = "0x" + "ef" * 32
    payload = [{"transaction_hash": TX_HASH, "replacement_hash": replacement, "confirmations": "7",
                "observed_block_number": str(2**80 + 1), "observed_block_hash": BLOCK_HASH}]
    references = submit.parse_receipt_references({"receipts": payload})
    assert references[0].observed_block_number == 2**80 + 1 and references[0].replacement_hash == replacement
    assert submit.receipt_reference_json(references) == payload
    known = replace(references[0], nonce=(1 << 64) - 1)
    encoded = submit.receipt_reference_json((known,))
    assert encoded[0]["nonce"] == str((1 << 64) - 1)
    assert submit.parse_receipt_references(encoded) == (known,)


@pytest.mark.parametrize("field", ["observed_block_number", "nonce"])
@pytest.mark.parametrize("number", ["01", "1e3", "-1", True, 1.5, "9" * 79])
def test_receipt_reference_json_rejects_nonexact_observations(field, number):
    with pytest.raises((ValueError, lifecycle.LifecyclePlanningError)):
        submit.parse_receipt_references([{"transaction_hash": TX_HASH, field: number}])


def test_submit_restores_wrapper_receipts(construct_case, tmp_path, monkeypatch, capsys):
    _, plan, _, artifacts = construct_case("launch_erc20_v4.py")
    reference = lifecycle.LifecycleReceiptReference(TX_HASH, observed_block_number=16, observed_block_hash=BLOCK_HASH)
    payload = {"plan": lifecycle.launch_plan_to_dict(plan), "receipts": submit.receipt_reference_json((reference,))}
    path = tmp_path / "recover.json"
    path.write_text(json.dumps(payload))
    @dataclass
    class ProgressOutput:
        receipts: tuple
    status = lifecycle.LifecycleReceiptStatus(TX_HASH, TX_HASH, "confirmed", False, 16, BLOCK_HASH, 1)
    progress = ProgressOutput((status,))
    planned = SimpleNamespace(admitted=True, confidence="stateful", predicted_token=TOKEN, progress=progress,
                              approvals=(), profiles=(), hook_deployments=(), simulation={})
    observed = []
    def plan_launch(_client, _plan, **options):
        assert options["receipts"] == (reference,)
        return planned
    def next_transaction(_client, launch, **options):
        assert launch is planned
        observed.append(options)
        return None
    monkeypatch.setattr(submit, "plan_launch", plan_launch)
    monkeypatch.setattr(submit, "build_next_transaction", next_transaction)
    assert submit.main([str(path), "--rpc-url", "https://rpc.example", "--account", plan.creator, "--mode", "staged"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert observed[0]["receipts"] == (reference,) and output["receipts"] == payload["receipts"]
    assert output["nextTransaction"] is None
    artifacts.finish()


@pytest.mark.parametrize("change", ["wrong-password", "unsafe-permissions", "address", "kdf-work", "both-signers"])
def test_keystore_failure_preserves_safe_diagnostics(tmp_path, monkeypatch, change):
    source = Path(__file__).with_name("fixtures") / "launch-keystore-v3-empty.json"
    document = json.loads(source.read_text())
    if change == "address":
        document["address"] = "02" * 20
    if change == "kdf-work":
        document["crypto"]["kdfparams"]["n"] = 2**30
    path = tmp_path / "key.json"
    path.write_text(json.dumps(document))
    path.chmod(0o644 if change == "unsafe-permissions" else 0o600)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.setenv("EXAMPLE_KEYSTORE_PATH", str(path))
    monkeypatch.setenv("LAUNCH_API_URL", "https://api.example")
    monkeypatch.setenv("EXAMPLE_KEYSTORE_PASSWORD", "not-the-password" if change == "wrong-password" else "")
    if change == "both-signers":
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "01" * 32)
    artifacts = Artifacts(tmp_path / "failure", "keystore")
    with pytest.raises(ExampleFailure) as failure:
        example.example_configuration(artifacts)
    assert "not-the-password" not in str(failure.value) and not artifacts.receipts
    artifacts.finish(failure.value)


def test_known_v3_empty_password_signer_derives_creator_and_redacts_key(tmp_path, monkeypatch):
    path = tmp_path / "key.json"
    path.write_bytes((Path(__file__).with_name("fixtures") / "launch-keystore-v3-empty.json").read_bytes())
    path.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.delenv("EXAMPLE_KEYSTORE_PASSWORD", raising=False)
    monkeypatch.setenv("EXAMPLE_KEYSTORE_PATH", str(path))
    monkeypatch.setenv("LAUNCH_API_URL", "https://api.example")
    artifacts = Artifacts(tmp_path / "keystore", "keystore")
    config, key = example.example_configuration(artifacts)
    assert bytes(key) == bytes.fromhex("01" * 32) and config["creator"] == Account.from_key(key).address
    assert config["buyAmountWei"] == 10**13 and config["kyberApiUrl"] == config["apiUrl"]
    assert artifacts.redact({"note": key.hex()})["note"] == support.REDACTED
    artifacts.finish()


@pytest.mark.parametrize("field,value", [("EXAMPLE_TOKEN_NAME", ""), ("EXAMPLE_TOKEN_NAME", "N" * 101),
    ("EXAMPLE_TOKEN_NAME", "🦑" * 33), ("EXAMPLE_TOKEN_SYMBOL", "  "), ("EXAMPLE_TOKEN_SYMBOL", "S" * 33),
    ("EXAMPLE_TOKEN_SYMBOL", "🦑" * 9), ("EXAMPLE_TOKEN_DESCRIPTION", "D" * 2001), ("EXAMPLE_TOKEN_NAME", "\udcff")])
def test_identity_override_validation_keeps_api_and_protocol_bounds(monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(ExampleFailure) as failure:
        example.example_identity({"id": "case"})
    assert failure.value.code == "INVALID_CONFIGURATION"


def test_identity_overrides_are_normalized_and_frozen_in_plan_review(construct_case, monkeypatch):
    monkeypatch.setenv("EXAMPLE_TOKEN_NAME", "  Cafe\u0301 coin  ")
    monkeypatch.setenv("EXAMPLE_TOKEN_SYMBOL", "  CAFE  ")
    monkeypatch.setenv("EXAMPLE_TOKEN_DESCRIPTION", "  Memes\nwith depth  ")
    _, plan, _, artifacts = construct_case(NEW_CASES[1][0])
    assert plan.token.name == "Café coin" and plan.token.symbol == "CAFE"
    assert artifacts.economic_pricing["metadata"]["description"] == "Memes\nwith depth"
    monkeypatch.setenv("EXAMPLE_TOKEN_NAME", "changed")
    assert artifacts.economic_pricing["metadata"]["name"] == plan.token.name
    artifacts.finish()


@pytest.mark.parametrize("kind", ["missing", "empty", "large", "directory", "symlink", "invalid-type"])
def test_image_path_validation_refuses_unreadable_or_unaccepted_input(tmp_path, kind):
    path = tmp_path / "image.png"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        actual = tmp_path / "actual.png"
        actual.write_bytes(PNG)
        path.symlink_to(actual)
    elif kind != "missing":
        path.write_bytes(b"" if kind == "empty" else b"x" * (support.MAX_LAUNCH_IMAGE_BYTES + 1) if kind == "large" else b"not an image")
    with pytest.raises(ExampleFailure) as failure:
        support.load_example_image(path)
    assert failure.value.code == "INVALID_IMAGE"


def test_image_descriptor_detects_content_not_extension(tmp_path):
    path = tmp_path / "misnamed.txt"
    path.write_bytes(PNG)
    body, descriptor = support.load_example_image(path)
    assert body == PNG and descriptor.content_type == "image/png" and descriptor.content_length == len(PNG)
    assert descriptor.sha256 == support.sha256_hex(PNG)


def test_image_stage_signs_exact_descriptor_uploads_once_and_requires_ready(construct_case, tmp_path, monkeypatch):
    _, draft, _, artifacts = construct_case(NEW_CASES[0][0])
    account = Account.from_key("0x" + "01" * 32)
    plan = replace(draft, creator=account.address)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(PNG)
    image = support.load_example_image(image_path)
    image_json = {"sha256": image[1].sha256, "contentType": image[1].content_type,
                  "contentLength": image[1].content_length, "width": None, "height": None}
    upload_url = f"https://uploads.example/bucket/launches/4663/{account.address.lower()}/{UPLOAD_ID}?signed=opaque"
    upload = {"url": upload_url, "headers": {"content-type": "image/png", "if-none-match": "*", "x-amz-meta-upload-id": UPLOAD_ID}}
    state, calls = {}, []
    def transport(method, url, headers, body, timeout):
        if method == "POST" and urlsplit(url).path.endswith("/launch-upload-sessions"):
            calls.append("create")
            state["request"] = json.loads(body)
            metadata = {**metadata_document(), **state["request"]["metadata"]}
            state["session"] = metadata_session(wallet=account.address, metadata=metadata, capability="original-capability",
                                                status="awaiting_upload", metadataStatus="queued", imageStatus="upload_pending", image=image_json, upload=upload)
            result = state["session"]
        elif method == "PUT":
            calls.append("put")
            assert url == upload_url and body == PNG and headers == upload["headers"]
            return LaunchApiResponse(200, {}, b"")
        elif method == "GET":
            calls.append("get")
            result = state["session"]
        else:
            assert method == "POST" and urlsplit(url).path.endswith("/upload/complete")
            calls.append("complete")
            result = {**state["session"], "status": "ready_to_launch", "metadataStatus": "ready", "imageStatus": "ready",
                      "image": {**image_json, "width": 1, "height": 1}}
        return LaunchApiResponse(200, {}, json.dumps(result).encode())
    monkeypatch.setattr(support, "rpc", lambda *_args: hex(4663))
    api = LaunchApiClient(LaunchApiConfig(base_url="https://api.example"), transport=transport)
    wallet = support.LocalSigningWallet(None, artifacts, private_key="0x" + "01" * 32, creator=plan.creator, chain_id=4663)
    recovery = example.stage_metadata(None, api, plan, artifacts, example.Cancellation(), wallet=wallet, mode="staged", image=image)
    typed = recovery["typedData"]
    assert typed["message"]["imageSha256"] == image[1].sha256 and typed["message"]["imageContentLength"] == len(PNG)
    assert typed["message"]["imageContentType"] == "image/png"
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=recovery["request"]["authorization"]["signature"]) == account.address
    assert calls == ["create", "put", "get", "complete"] and recovery["lastSession"]["imageStatus"] == "ready"
    assert state["request"]["image"] == {key: image_json[key] for key in ("sha256", "contentType", "contentLength")}
    assert not artifacts.receipts
    artifacts.finish()


def test_image_publication_binds_public_key_type_length_and_url(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(PNG)
    _, descriptor = support.load_example_image(path)
    plan = token_draft()
    key = f"launches/4663/{TOKEN.lower()}/{UPLOAD_ID}"
    image_url = "https://images.example/" + key
    image = {"sha256": descriptor.sha256, "contentType": "image/png", "contentLength": len(PNG), "width": 1, "height": 1}
    # Sessions carry no imageUrl; the public URL is on the DTO item, and image
    # type/length are nested under item.metadata (matching the real launch DTO).
    session = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic",
                               image=image, imageStatus="ready")
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol},
            "metadata": {"document": {**metadata_document(), "imageKey": key},
                         "imageContentType": "image/png", "imageContentLength": str(len(PNG))},
            "imageUrl": image_url}
    detail = {"chainId": 4663, "indexedBlock": "16", "item": item}
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10"}
    example.verify_publication(session, detail, plan, TOKEN, receipt, "session", signed_metadata(), image=descriptor)
    wrong = {**detail, "item": {**item, "metadata": {**item["metadata"], "imageContentLength": "1"}}}
    with pytest.raises(ExampleFailure) as failure:
        example.verify_publication(session, wrong, plan, TOKEN, receipt, "session", signed_metadata(), image=descriptor)
    assert failure.value.code == "API_PUBLICATION_MISMATCH"


@pytest.fixture
def published_image_server():
    state, calls = {"body": PNG, "contentType": "image/png", "status": 200}, []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(state["status"])
            self.send_header("Content-Type", state["contentType"])
            self.send_header("Content-Length", str(state.get("length", len(state["body"]))))
            if state["status"] == 302:
                self.send_header("Location", "/other-image")
            self.end_headers()
            self.wfile.write(state["body"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/image", state, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("transformed,content_type", [(False, "image/png"), (True, "image/webp"), (True, "image/avif")])
def test_published_image_get_records_exact_identity_or_inferred_transformation(published_image_server, transformed, content_type):
    url, state, calls = published_image_server
    descriptor = example.LaunchSessionImageDescriptor(support.sha256_hex(PNG), "image/png", len(PNG))
    if transformed:
        state.update(body=b"transcoded image fixture", contentType=content_type)
    evidence = support.read_published_image(url, descriptor)
    assert calls == ["/image"] and evidence["status"] == 200 and evidence["transformed"] is transformed
    assert evidence["matchesUploadedSha256"] is not transformed and evidence["contentLength"] == len(state["body"])
    assert bool(evidence["transformationEvidence"]) is transformed


@pytest.mark.parametrize("failure", ["missing", "redirect", "empty", "type", "oversize", "truncated"])
def test_published_image_get_failure_is_single_bounded_attempt(published_image_server, failure):
    url, state, calls = published_image_server
    descriptor = example.LaunchSessionImageDescriptor(support.sha256_hex(PNG), "image/png", len(PNG))
    changes = {"missing": {"status": 404}, "redirect": {"status": 302}, "empty": {"body": b""},
               "type": {"contentType": "text/plain"}, "oversize": {"length": support.MAX_LAUNCH_IMAGE_BYTES + 1},
               "truncated": {"length": len(PNG) + 1}}
    state.update(changes[failure])
    with pytest.raises(ExampleFailure) as failure:
        support.read_published_image(url, descriptor)
    assert failure.value.code == "IMAGE_PUBLICATION_UNAVAILABLE" and calls == ["/image"]


@pytest.mark.parametrize("available", [True, False])
def test_image_publication_cannot_pass_without_public_image_get(tmp_path, monkeypatch, publication_context, available):
    client, receipt, references = publication_context
    plan = token_draft()
    descriptor = example.LaunchSessionImageDescriptor(support.sha256_hex(PNG), "image/png", len(PNG))
    image = {"sha256": descriptor.sha256, "contentType": "image/png", "contentLength": len(PNG), "width": 1, "height": 1}
    key = f"launches/4663/{TOKEN.lower()}/{UPLOAD_ID}"
    url = "https://images.example/" + key
    staged = metadata_session(image=image, imageStatus="ready")
    published = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic",
                                 image=image, imageStatus="ready")
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol},
            "metadata": {"document": {**metadata_document(), "imageKey": key},
                         "imageContentType": "image/png", "imageContentLength": str(len(PNG))},
            "imageUrl": url}
    api = SimpleNamespace(config=SimpleNamespace(base_url=API_ORIGIN, timeout=15), get_launch_upload_session=lambda *_args: staged,
                          publish_launch_upload_session=lambda *_args: published,
                          get_launch=lambda *_args: {"chainId": 4663, "indexedBlock": "16", "item": item})
    calls = []
    def public_get(actual_url, actual_descriptor, **options):
        calls.append(actual_url)
        assert actual_descriptor == descriptor and 0 < options["timeout_seconds"] <= 90
        if not available:
            raise ExampleFailure("not readable", code="IMAGE_PUBLICATION_UNAVAILABLE")
        return {"status": 200, "contentType": "image/png", "contentLength": len(PNG), "sha256": descriptor.sha256}
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    monkeypatch.setattr(example, "read_published_image", public_get)
    artifacts = Artifacts(tmp_path / "image-publication", "image")
    recovery = {**metadata_recovery(), "image": {"sha256": descriptor.sha256, "content_type": "image/png", "content_length": len(PNG)}}
    if available:
        example.publish_activation(client, api, recovery, plan, TOKEN, receipt, references, 90, artifacts, example.Cancellation())
        assert artifacts.result["api"]["status"] == "passed" and (artifacts.directory / "published-image.json").exists()
        artifacts.finish()
    else:
        with pytest.raises(ExampleFailure) as failure:
            example.publish_activation(client, api, recovery, plan, TOKEN, receipt, references, 90, artifacts, example.Cancellation())
        assert failure.value.code == "IMAGE_PUBLICATION_UNAVAILABLE" and artifacts.result["api"]["status"] != "passed"
        artifacts.finish(failure.value)
    assert calls == [url]


@pytest.mark.parametrize("venue", ["v4", "abyss"])
def test_weth_p1_oracle_uses_selected_factory_and_pinned_mapping(monkeypatch, venue):
    from black_market_sdk import get_known_lifecycle_profile, KnownLifecycleProfile
    deployment = example.get_launch_addresses(4663)
    key = KnownLifecycleProfile.V4_FIXED_FEE_POOL if venue == "v4" else KnownLifecycleProfile.ABYSS_3
    profile = get_known_lifecycle_profile(chain_id=4663, orchestrator=deployment.orchestrator, key=key).profile
    factory = profile.envelope.graph.oracle_factory if venue == "v4" else profile.registration.factory
    block = LaunchBlock(321, BLOCK_HASH, 100, 32000000, 1)
    expected_id = example.hex_bytes(Web3.keccak(encode(["uint24", "uint16"], [1, 4096])))
    def mapping(config_id):
        assert example.hex_bytes(config_id) == expected_id
        def read(*, block_identifier):
            assert block_identifier == 321
            return 1, 4096
        return SimpleNamespace(call=read)
    def selected_contract(_client, address, _abi):
        assert address.lower() == factory.lower()
        return SimpleNamespace(functions=SimpleNamespace(oracleConfigs=mapping))
    monkeypatch.setattr(example, "contract", selected_contract)
    monkeypatch.setattr(example, "rpc", no_call)
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    result = example.discover_oracle_config(None, profile, {"symbol": "WETH"}, block, example.Cancellation())
    assert result["oracleConfigId"] == expected_id and result["factory"] == factory


def test_truncated_kyber_calldata_has_a_safe_typed_refusal():
    escrow, quote = example.get_launch_addresses(4663).funding_escrow, example.ROBINHOOD_USDG
    build = kyber_build_fixture(quote_asset=quote, amount_in=10**13, escrow=escrow, deadline=400)
    build["transaction"]["data"] = example.hex_bytes(support.KYBER_SWAP_SELECTOR + b"\x00")
    with pytest.raises(ExampleFailure) as failure:
        support.validate_kyber_build(build, quote_asset=quote, amount_in=10**13, escrow=escrow, deadline=400, now=100)
    assert failure.value.code == "INVALID_KYBER_BUILD"


@pytest.mark.parametrize("change", ["expired", "unadmitted"])
def test_signing_guard_revalidates_retained_conversion_without_rebuilding(construct_case, monkeypatch, change):
    _, plan, predicted, artifacts = construct_case(NEW_CASES[0][0])
    review = {**artifacts.economic_pricing, "planHash": lifecycle.hash_launch_plan(plan)}
    configuration = {"chainId": 4663, "orchestrator": plan.orchestrator, "creator": CREATOR,
                     "quoteAsset": example.get_addresses(4663).weth, "apiUrl": API_ORIGIN}
    client = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="https://rpc.example"))
    guard = example.economic_signature_guard(client, configuration, plan, predicted, review,
                                            example.economic_review_hash(review), artifacts)
    monkeypatch.setattr(example, "read_kyber_build", no_call)
    if change == "expired":
        monkeypatch.setattr(example.time, "time", lambda: plan.deadline)
    else:
        def refused(*_args):
            raise ExampleFailure("target was unadmitted", code="FUNDING_NOT_ADMITTED")
        monkeypatch.setattr(example, "read_funding_authority", refused)
    with pytest.raises(ExampleFailure) as failure:
        guard()
    assert failure.value.code == ("INVALID_KYBER_BUILD" if change == "expired" else "FUNDING_NOT_ADMITTED")
    assert not artifacts.receipts
    artifacts.finish(failure.value)


def test_submit_refuses_salt_finalization_with_retained_receipts(construct_case, tmp_path, monkeypatch):
    _, plan, _, artifacts = construct_case("launch_erc20_v4.py")
    path = tmp_path / "recover.json"
    path.write_text(json.dumps({"plan": lifecycle.launch_plan_to_dict(plan),
                               "receipts": [{"transaction_hash": TX_HASH}]}))
    monkeypatch.setattr(submit, "prepare_and_plan_lifecycle_launch", no_call)
    with pytest.raises(SystemExit) as failure:
        submit.main([str(path), "--rpc-url", "https://rpc.example", "--account", plan.creator,
                     "--mode", "staged", "--finalize-bound"])
    assert failure.value.code == 2
    artifacts.finish()
