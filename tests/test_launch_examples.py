"""Local launch-example wallet, diagnostic, interruption and publication boundaries.

These are local unit tests, not evidence that any API/chain launch was executed.
"""

from __future__ import annotations

import json
import runpy
import sys
import signal
import threading
from dataclasses import replace
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest
from requests import HTTPError
from web3 import Web3
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data

from examples import launch_examples as example
from examples import _launch_support as support
from examples._launch_support import Artifacts, JournalHTTPProvider, LocalSigningWallet, Redactor, ExampleFailure, error_details, loopback_url, mainnet_url
from black_market_sdk import (
    LaunchBlock, LaunchExecutionLimits, LaunchPlanV1, LaunchPublishPending, LifecycleTokenConfig,
    LaunchApiError, KnownLifecycleProfile, get_known_lifecycle_profile, get_launch_addresses,
    predict_launch_token, to_launch_plan_tuple,
    LAUNCH_RECEIPT_COMPONENTS_V1,
)
from black_market_sdk import lifecycle
from examples import launch_api_lifecycle as api_example
from black_market_sdk import LaunchSessionCreateMetadata, LaunchApiClient, LaunchApiConfig, LaunchApiResponse
from black_market_sdk.lifecycle_rpc import LaunchRpcError, LaunchRpcSimulation

CREATOR = "0x0000000000000000000000000000000000000001"
TOKEN = "0x0000000000000000000000000000000000000002"
TX_HASH = "0x" + "ab" * 32
BLOCK_HASH = "0x" + "cd" * 32
API_ORIGIN = "https://api.abyss.trading"
PRICE_RETRIEVED_AT = 1791560098


def price_snapshot():
    """Reviewed API DTO, including its old upstream observation and exact price."""
    addresses = example.get_addresses(4663)
    return {
        "chainId": 4663, "indexedBlock": "84269486", "blockNumber": "84269370",
        "items": [{
            "chainId": 4663, "address": addresses.weth, "decimals": 18,
            "metadataStatus": "ready", "priced": True, "source": "anchor",
            "priceUsdX18": "2486280000000000000000",
            "provenance": {
                "anchorToken": addresses.weth,
                "anchor": {"kind": "chainlink", "aggregator": addresses.eth_usd_feed},
                "path": [], "hopCount": 0, "sourceTimestamp": "1791552898",
                "graphBlockNumber": "84195615",
            },
        }],
    }


def case_definition():
    return {"id": "boundary-case", "description": "Unit boundary", "tokenKind": 0, "rewardMode": 0,
            "mode": "atomic", "markets": [{"venue": "v4", "positions": 1}], "buysPerMarket": 1}




def token_draft():
    return LaunchPlanV1(
        chain_id=4663, orchestrator=get_launch_addresses(4663).orchestrator, creator=CREATOR, nonce=123,
        token=LifecycleTokenConfig(0, 0, "Smoke boundary-case", "SMOKE", 1000000 * 10**18, 0, "", bytes(32), CREATOR, False),
        funding=(), fee_assets=(), markets=(), buys=(), deadline=3600, executor_fee_bps=275,
    )


def signed_metadata():
    plan = token_draft()
    return LaunchSessionCreateMetadata(name=plan.token.name, symbol=plan.token.symbol, description="Original description")


def metadata_document():
    metadata = signed_metadata()
    return {"name": metadata.name, "symbol": metadata.symbol, "description": metadata.description,
            "websiteUrl": None, "twitterUrl": None, "telegramUrl": None, "discordUrl": None}


def metadata_session(**changes):
    return {"sessionId": "session", "chainId": 4663, "wallet": CREATOR, "status": "ready_to_launch",
            "metadata": metadata_document(), "image": None, "canonicalStatus": "pending", "metadataStatus": "ready",
            "imageStatus": "none", "retryable": True, "createdAt": "1", "updatedAt": "2",
            "sessionExpiresAt": "9999999999", "transactionHash": None, "token": None, **changes}


def metadata_recovery():
    return {"metadata": support.portable(signed_metadata()), "mode": "atomic", "confirmations": 1,
            "session": {"sessionId": "session", "capability": "private-capability"}}



def kyber_build_fixture(*, quote_asset, amount_in, escrow, deadline, **changes):
    """Real router swap ABI, not an executable live route or network observation."""
    out = amount_in if example.address_equal(quote_asset, example.get_addresses(4663).weth) else amount_in * 2000 * 10**6 // 10**18
    minimum = out * 9950 // 10000
    desc = (support.KYBER_NATIVE_TOKEN, quote_asset, (), (), (), (), escrow, amount_in, minimum, 0, b"")
    target_data = bytes.fromhex(sorted(support.KYBER_EXECUTOR_IMPLEMENTATIONS)[0][2:]) + bytes(32)
    execution = (support.KYBER_EXECUTOR, example.ZERO_ADDRESS, target_data, desc, b"")
    data = example.hex_bytes(support.KYBER_SWAP_SELECTOR + encode([support.KYBER_SWAP_EXECUTION_TYPE], [execution]))
    return {"chainId": 4663, "source": "kyberswap", "tokenIn": support.KYBER_NATIVE_TOKEN,
            "tokenOut": quote_asset, "amountIn": str(amount_in), "amountOut": str(out),
            "amountOutMinimum": str(minimum), "routerAddress": support.KYBER_ROUTER, "expiresAt": deadline,
            "transaction": {"to": support.KYBER_ROUTER, "data": data, "value": str(amount_in)}, **changes}


def clean_progress(*, statuses=(), phase=lifecycle.LifecyclePhase.ACTIVE, token=TOKEN, awaiting=False):
    return SimpleNamespace(receipts=statuses, awaiting_confirmations=awaiting, phase=phase, token=token,
                           canonical=SimpleNamespace(phase=phase, token=token))


@pytest.fixture
def publication_context(monkeypatch):
    plan = token_draft()
    receipt = {"transactionHash": TX_HASH, "blockNumber": "0x10", "blockHash": BLOCK_HASH,
               "from": plan.creator, "to": plan.orchestrator, "status": "0x1"}
    references = (lifecycle.LifecycleReceiptReference(TX_HASH, observed_block_number=16, observed_block_hash=BLOCK_HASH),)
    status = lifecycle.LifecycleReceiptStatus(TX_HASH, TX_HASH, "confirmed", False, 16, BLOCK_HASH, 1)
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params: dict(receipt) if method == "eth_getTransactionReceipt" else no_call())
    monkeypatch.setattr(example, "read_launch_progress", lambda *_args, **_kwargs: clean_progress(statuses=(status,)))
    return object(), receipt, references

@pytest.fixture
def construct_case(tmp_path, monkeypatch):
    """Build the real fixed economics using release metadata, with no RPC or signing."""
    block = LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1)
    deployment = get_launch_addresses(4663)
    configuration = {"chainId": 4663, "orchestrator": deployment.orchestrator,
                     "creator": CREATOR, "quoteAsset": deployment.wrapped_native,
                     "apiUrl": API_ORIGIN}
    profiles = [SimpleNamespace(**vars(get_known_lifecycle_profile(chain_id=4663,
        orchestrator=deployment.orchestrator, key=key).profile), admitted=True)
        for key in (KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL,
                    KnownLifecycleProfile.V4_FIXED_FEE_POOL, KnownLifecycleProfile.ABYSS_3)]
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    monkeypatch.setattr(example, "predict_launch_token", lambda *_args, **_kwargs: TOKEN)
    monkeypatch.setattr(example, "read_lifecycle_profiles", lambda *_args, **_kwargs: profiles)
    monkeypatch.setattr(example, "discover_oracle_config", lambda _client, profile, quote, block, _cancel:
                        {"oracleConfigId": "0x" + "12" * 32, "factory": profile.registration.factory
                         if profile.venue_kind == "abyss" else profile.envelope.graph.oracle_factory})
    monkeypatch.setattr(example, "contract", lambda _client, address, _abi: SimpleNamespace(
        functions=SimpleNamespace(
            decimals=lambda: SimpleNamespace(call=lambda **_: 6 if example.address_equal(address, example.ROBINHOOD_USDG) else 18),
            registry=lambda: SimpleNamespace(call=lambda **_: deployment.registry),
            profileCount=lambda: SimpleNamespace(call=lambda **_: len(profiles)),
            profileId=lambda _variant: SimpleNamespace(call=lambda **_: profiles[-1].id))))
    monkeypatch.setattr(example, "read_funding_authority", lambda *_args: get_launch_addresses(4663).funding_escrow)
    monkeypatch.setattr(example, "read_kyber_build", lambda _url, **options: kyber_build_fixture(**options))
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params: hex(10**18) if method == "eth_getBalance" else hex(4663))
    for name in ("EXAMPLE_TOKEN_NAME", "EXAMPLE_TOKEN_SYMBOL", "EXAMPLE_TOKEN_DESCRIPTION", "EXAMPLE_IMAGE_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(example, "read_price_snapshot",
                        lambda *_args: (price_snapshot(), API_ORIGIN, PRICE_RETRIEVED_AT))
    monkeypatch.setitem(sys.modules, "launch_examples", example)

    def construct(filename, *, markets=None, predicted_token=TOKEN):
        case = runpy.run_path(str(Path(example.__file__).with_name(filename)))["CASE"]
        if markets is not None:
            case = {**case, "markets": markets}
        monkeypatch.setattr(example, "predict_launch_token", lambda *_args, **_kwargs: predicted_token)
        artifacts = Artifacts(tmp_path / case["id"], case["id"])
        plan, predicted = example.construct_plan(None, configuration, case, 123, bytes(32), artifacts, example.Cancellation())
        return case, plan, predicted, artifacts
    return construct


@pytest.mark.parametrize("filename", [
    "launch_erc20_v4.py", "launch_erc20_abyss.py", "launch_erc404_v4.py", "launch_erc404_abyss.py",
    "launch_erc20_staking_v4.py", "launch_erc20_dividends_abyss.py",
    "launch_erc20_burn_mixed.py", "launch_erc404_dividends_mixed.py",
])
def test_fixed_cases_allocate_complete_supply_and_position_maxima(construct_case, filename):
    case, plan, predicted, artifacts = construct_case(filename)
    assert predicted == TOKEN and sum(market.token_budget for market in plan.markets) == plan.token.supply
    base_budget = plan.token.supply // len(plan.markets)
    for index, (market, spec) in enumerate(zip(plan.markets, case["markets"])):
        assert market.token_budget == (plan.token.supply - base_budget * index if index == len(plan.markets) - 1 else base_budget)
        config = (lifecycle.decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
                  if spec["venue"] == "v4" else lifecycle.decode_lifecycle_abyss_market_config(market.config))
        assert len(config.positions) == spec["positions"]
        maxima = tuple(position.max_token_amount if spec["venue"] == "v4"
                       else position.token_amount_maximum for position in config.positions)
        assert sum(maxima) == market.token_budget
        assert all(maximum > 0 for maximum in maxima)
        review = artifacts.economic_pricing["markets"][index]
        assert sum(int(p["expectedMintDebit"]) for p in review["positions"]) + int(review["roundingBurn"]) == market.token_budget
        assert 5000 * 10**18 <= int(review["openingValuationUsdX18"]) < 5031 * 10**18
        retained = int(artifacts.economic_pricing["expectedRetainedSupply"])
        assert retained == plan.token.supply - int(artifacts.economic_pricing["roundingBurn"])
        assert plan.token.supply - retained < 10**12
        assert 4999 * 10**18 < int(review["retainedSupplyOpeningValuationUsdX18"]) < 5031 * 10**18
        if spec["venue"] == "v4":
            assert isinstance(config, lifecycle.LifecyclePoolBoundV4MarketConfigV6)
            assert config.minimum_hook_fee_pips < config.hook_fee_pips
            assert config.fee_sensitivity_pips_seconds_per_tick > 0
            assert example.hex_bytes(config.profile_id) == market.profile_id
    assert len(plan.buys) == len(plan.markets) * case["buysPerMarket"]
    assert plan.funding[0].amount == sum(buy.quote_amount_in for buy in plan.buys)
    assert artifacts.economic_pricing["quotes"][0]["source"] == "abyss-api"
    assert artifacts.economic_pricing["quotes"][0]["priceUsdX18"] == "2486280000000000000000"
    assert artifacts.economic_pricing["quotes"][0]["upstreamAgeSeconds"] == "7200"
    artifacts.finish()


def test_final_market_receives_supply_division_remainder(construct_case):
    _, plan, _, artifacts = construct_case(
        "launch_erc20_abyss.py", markets=[{"venue": "abyss", "positions": 3}] * 3,
    )
    base = plan.token.supply // 3
    assert plan.token.supply % 3 != 0
    assert tuple(market.token_budget for market in plan.markets) == (base, base, plan.token.supply - 2 * base)
    for market in plan.markets:
        config = lifecycle.decode_lifecycle_abyss_market_config(market.config)
        assert sum(position.token_amount_maximum for position in config.positions) == market.token_budget
    artifacts.finish()


def no_call(*_args, **_kwargs):
    raise AssertionError("unexpected RPC, signing, simulation or API operation")


def test_existing_output_is_never_replaced(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "result.json"
    sentinel.write_text("keep original")
    with pytest.raises(FileExistsError):
        Artifacts(output, "boundary-case")
    assert sentinel.read_text() == "keep original"


def test_interrupt_preserves_submitted_hash_and_meaningful_result(tmp_path, monkeypatch):
    def interrupted(_args, artifacts, cancellation, *, broadcast):
        assert broadcast is True
        artifacts.result.update(scope="end-to-end", execution="executed")
        artifacts.result["chain"]["status"] = "executing"
        artifacts.enter("receipt-atomic")
        artifacts.broadcast_attempt("atomic", {"from": CREATOR, "data": "0x", "gas": 100000}, TX_HASH)
        cancellation.signal(signal.SIGINT, None)
        cancellation.check()

    monkeypatch.setattr(example, "run", interrupted)
    monkeypatch.chdir(tmp_path)
    assert example.run_launch_example(case_definition(), argv=["--broadcast"]) == 1
    output = next((tmp_path / "launch-results").iterdir())
    receipts = json.loads((output / "receipts.json").read_text())
    result = json.loads((output / "result.json").read_text())
    assert receipts[0]["transactionHash"] == TX_HASH and receipts[0]["receipt"] is None
    assert result["status"] == "failed" and result["stage"] == "receipt-atomic"
    assert result["error"]["code"] == "INTERRUPTED" and result["chain"]["transactions"] == [TX_HASH]


def test_reverted_receipt_is_saved_before_status_failure(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "reverted", "boundary-case")
    row = artifacts.broadcast_attempt("atomic", {"from": CREATOR, "data": "0x", "gas": 100000}, TX_HASH)
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10", "status": "0x0", "gasUsed": "0x100"}
    trace = {"error": "execution reverted", "output": "0xdeadbeef00000001"}
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params: receipt if method == "eth_getTransactionReceipt" else trace)
    with pytest.raises(ExampleFailure) as failure:
        example.wait_receipt(None, row, artifacts, example.Cancellation())
    stored = json.loads((artifacts.directory / "receipts.json").read_text())
    artifacts.finish(failure.value)
    assert stored[0]["receipt"] == receipt and stored[0]["failureTrace"] == trace
    assert failure.value.code == "TRANSACTION_REVERTED"


def test_recursive_redaction_preserves_revert_cause_and_private_recovery(tmp_path, monkeypatch):
    secret_key = "0x" + "11" * 32
    signature = "0x" + "22" * 65
    capability = "private-session-capability"
    monkeypatch.setenv("LAUNCH_TEST_PRIVATE_KEY", secret_key)
    artifacts = Artifacts(tmp_path / "redacted", "boundary-case")
    recovery = {"capability": capability, "signature": signature, "authorization": {"Bearer": "header-secret"}}
    artifacts.redact.learn(recovery)
    artifacts.save("recovery.private.json", recovery, private=True)
    try:
        try:
            raise LaunchRpcError("eth_call", {"code": -32000, "message": "execution reverted", "data": "0xdeadbeef00000001"})
        except LaunchRpcError as cause:
            raise RuntimeError(f"failed with {capability}; key={secret_key}; signature={signature}; https://user:password@provider.example/rpc-path-key?token=presigned-secret") from cause
    except RuntimeError as error:
        artifacts.event("unsafe-input", nested=[recovery], text="Authorization: Bearer unregistered-header-secret", error=error_details(error))
        artifacts.finish(error)
    public = (artifacts.directory / "events.jsonl").read_text() + (artifacts.directory / "result.json").read_text()
    for secret in (secret_key, signature, capability, "header-secret", "unregistered-header-secret", "presigned-secret", "rpc-path-key", "user:password"):
        assert secret not in public
    result = json.loads((artifacts.directory / "result.json").read_text())
    assert result["error"]["cause"]["code"] == -32000
    private = artifacts.directory / "recovery.private.json"
    assert private.stat().st_mode & 0o777 == 0o600
    assert json.loads(private.read_text())["capability"] == capability


def test_only_202_is_polled_and_deadline_is_bounded(tmp_path, monkeypatch, publication_context):
    artifacts = Artifacts(tmp_path / "pending", "boundary-case")
    now = [0.0]
    calls = []
    pending = metadata_session(status="awaiting_indexer", metadataStatus="queued", transactionHash=TX_HASH)

    class PendingApi:
        config = SimpleNamespace(base_url="http://127.0.0.1:18764", timeout=15)

        def get_launch_upload_session(self, *_args):
            return metadata_session()

        def publish_launch_upload_session(self, chain_id, session_id, capability, request):
            calls.append((chain_id, session_id, capability, request.transaction_hash))
            raise LaunchPublishPending(pending, 5000)

    api = PendingApi()
    monkeypatch.setattr(example.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    cancellation = SimpleNamespace(check=lambda: None, sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    recovery = metadata_recovery()
    with pytest.raises(ExampleFailure) as failure:
        execution, receipt, references = publication_context
        example.publish_activation(execution, api, recovery, token_draft(), TOKEN, receipt, references, 1, artifacts, cancellation)
    artifacts.finish(failure.value)
    assert failure.value.code == "API_INDEX_TIMEOUT"
    assert calls == [(4663, "session", "private-capability", TX_HASH)]
    assert now[0] == 1 and artifacts.result["api"]["lastSession"] == pending


def test_non_pending_api_failure_is_not_retried_or_relaunched(tmp_path, monkeypatch, publication_context):
    artifacts = Artifacts(tmp_path / "api-error", "boundary-case")
    calls = []

    class FailedApi:
        config = SimpleNamespace(base_url="http://127.0.0.1:18764", timeout=15)

        def get_launch_upload_session(self, *_args):
            return metadata_session()

        def publish_launch_upload_session(self, *_args):
            calls.append("publish")
            raise LaunchApiError(503, "INDEXER_UNAVAILABLE")

    api = FailedApi()
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    monkeypatch.setattr(example, "execute_plan", no_call)
    cancellation = SimpleNamespace(check=lambda: None, sleep=no_call)
    with pytest.raises(LaunchApiError) as failure:
        execution, receipt, references = publication_context
        example.publish_activation(execution, api, metadata_recovery(), token_draft(), TOKEN,
                                   receipt, references, 90, artifacts, cancellation)
    artifacts.finish(failure.value)
    assert calls == ["publish"] and failure.value.code == "INDEXER_UNAVAILABLE"


def test_publication_success_requires_actual_matching_canonical_representation():
    plan = token_draft()
    session = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic")
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol},
            "metadata": {"document": metadata_document()}}
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10"}
    detail = {"chainId": plan.chain_id, "indexedBlock": "16", "item": item}
    example.verify_publication(session, detail, plan, TOKEN, receipt, "session", signed_metadata())
    wrong = {**detail, "item": {**item, "transactionHash": "0x" + "ef" * 32}}
    with pytest.raises(ExampleFailure) as failure:
        example.verify_publication(session, wrong, plan, TOKEN, receipt, "session", signed_metadata())
    assert failure.value.code == "API_PUBLICATION_MISMATCH"




def test_burn_disposition_never_burns_quote_in_either_address_orientation():
    definition = {**case_definition(), "burnBps": 3000}
    for token, quote in ((TOKEN, CREATOR), (CREATOR, TOKEN)):
        policies = example.fee_policies(token, (quote,), definition)
        token_policy = next(policy for policy in policies if policy.asset == token)
        quote_policy = next(policy for policy in policies if policy.asset == quote)
        assert token_policy.burn_bps == 3000 and token_policy.owner_bps == 7000
        assert quote_policy.burn_bps == 0 and quote_policy.owner_bps == 10000


def test_failed_http_request_is_logged_once_without_hidden_transport_retries(tmp_path):
    attempts = []

    class UnavailableRpc(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts.append(self.path)
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnavailableRpc)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    artifacts = Artifacts(tmp_path / "http-failure", "boundary-case")
    provider = JournalHTTPProvider(
        f"http://127.0.0.1:{server.server_port}", artifacts,
    )
    try:
        with pytest.raises(HTTPError) as failure:
            provider.make_request("eth_chainId", [])
        artifacts.finish(failure.value)
        assert len(attempts) == 1
        events = [json.loads(line) for line in (artifacts.directory / "events.jsonl").read_text().splitlines()]
        failures = [event for event in events if event["event"] == "rpc-error"]
        assert len(failures) == 1 and failures[0]["method"] == "eth_chainId"
        assert "503" in failures[0]["error"]["message"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("url", ["https://user:password@api.example", "https://api.example?key=secret"])
def test_mainnet_api_rejects_credentialed_urls(url):
    with pytest.raises(ValueError):
        mainnet_url(url, api=True)




def wallet_envelope(account, **fields):
    return {"chainId": 4663, "from": account.address, "to": TOKEN, "gas": 21000,
            "value": 123, "data": "0x", "nonce": 7, "gasPrice": 100, **fields}


def test_wallet_rejects_wrong_creator_before_any_rpc(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "wrong-key", "boundary-case")
    monkeypatch.setattr(support, "rpc", no_call)
    with pytest.raises(ExampleFailure) as failure:
        LocalSigningWallet(None, artifacts, private_key="0x" + "01" * 32, creator=CREATOR, chain_id=4663)
    artifacts.finish(failure.value)
    assert failure.value.code == "WALLET_IDENTITY_MISMATCH"


@pytest.mark.parametrize("dynamic", [False, True])
def test_wallet_preserves_sdk_envelope_and_prejournals_before_ambiguous_send(tmp_path, monkeypatch, dynamic):
    key = "0x" + "01" * 32
    account = Account.from_key(key)
    artifacts = Artifacts(tmp_path / "ambiguous", "boundary-case")
    envelope = wallet_envelope(account)
    if dynamic:
        envelope.pop("gasPrice")
        envelope.update(maxFeePerGas=100, maxPriorityFeePerGas=2)
    seen = []

    def request(_client, method, params):
        seen.append(method)
        if method == "eth_chainId":
            return hex(4663)
        assert method == "eth_sendRawTransaction"
        retained = json.loads((artifacts.directory / "receipts.json").read_text())
        assert retained[0]["broadcastStatus"] == "attempted" and retained[0]["transaction"] == envelope
        assert retained[0]["transactionHash"] == "0x" + Web3.keccak(hexstr=params[0]).hex().removeprefix("0x")
        raise TimeoutError("ambiguous broadcast containing " + params[0])

    monkeypatch.setattr(support, "rpc", request)
    wallet = LocalSigningWallet(None, artifacts, private_key=key, creator=account.address, chain_id=4663)
    with pytest.raises(TimeoutError) as failure:
        wallet.send_transaction("atomic", envelope)
    artifacts.finish(failure.value)
    assert seen == ["eth_chainId", "eth_sendRawTransaction"]
    row = artifacts.receipts[0]
    assert artifacts.result["chain"]["transactions"] == [row["transactionHash"]]
    private = artifacts.directory / ("signed-" + row["transactionHash"][2:] + ".private.json")
    assert private.stat().st_mode & 0o777 == 0o600
    raw = json.loads(private.read_text())["rawTransaction"]
    public = (artifacts.directory / "events.jsonl").read_text() + (artifacts.directory / "result.json").read_text()
    assert raw not in public and key not in public and key[2:] not in public


def test_wallet_rejects_wrong_returned_hash_and_preserves_attempt(tmp_path, monkeypatch):
    account = Account.from_key("0x" + "01" * 32)
    artifacts = Artifacts(tmp_path / "wrong-hash", "boundary-case")
    monkeypatch.setattr(support, "rpc", lambda _client, method, _params: hex(4663) if method == "eth_chainId" else TX_HASH)
    wallet = LocalSigningWallet(None, artifacts, private_key=account.key, creator=account.address, chain_id=4663)
    with pytest.raises(ExampleFailure) as failure:
        wallet.send_transaction("atomic", wallet_envelope(account))
    artifacts.finish(failure.value)
    assert failure.value.code == "BROADCAST_HASH_MISMATCH"
    assert len(artifacts.receipts) == 1 and artifacts.receipts[0]["broadcastStatus"] == "attempted"


def test_wallet_only_prepares_absent_nonce_and_fees(tmp_path, monkeypatch):
    account = Account.from_key("0x" + "01" * 32)
    artifacts = Artifacts(tmp_path / "prepare", "boundary-case")
    methods = []

    def request(_client, method, _params):
        methods.append(method)
        return {"eth_chainId": hex(4663), "eth_getTransactionCount": "0x9", "eth_gasPrice": "0x64"}[method]

    monkeypatch.setattr(support, "rpc", request)
    wallet = LocalSigningWallet(None, artifacts, private_key=account.key, creator=account.address, chain_id=4663)
    envelope = wallet_envelope(account)
    envelope.pop("nonce")
    envelope.pop("gasPrice")
    assert wallet.prepare_transaction(envelope) == {**envelope, "nonce": 9, "gasPrice": 100}
    assert methods == ["eth_chainId", "eth_getTransactionCount", "eth_gasPrice"]
    artifacts.finish()


@pytest.mark.parametrize("kind,amount", [("approve", 123), ("approve-reset", 0)])
def test_wallet_prepares_missing_approval_gas_without_relaxing_launch_envelopes(monkeypatch, kind, amount):
    request = token_draft()
    wallet = object.__new__(LocalSigningWallet)
    wallet.client, wallet.chain_id = None, request.chain_id
    wallet.account = SimpleNamespace(address=request.creator)
    transaction = {"chainId": request.chain_id, "from": request.creator,
        "to": "0x" + "ab" * 20, "value": 0,
        "data": "0x095ea7b3" + encode(["address", "uint256"], [request.orchestrator, amount]).hex()}
    methods = []
    def rpc(_client, method, params):
        methods.append(method)
        if method == "eth_estimateGas":
            assert params[0]["data"] == transaction["data"] and params[0]["to"] == transaction["to"]
            assert params[0]["gasPrice"] == "0x64" and params[0]["nonce"] == "0x9"
            return hex(26_024)
        return {"eth_chainId": hex(request.chain_id), "eth_getTransactionCount": "0x9", "eth_gasPrice": "0x64"}[method]
    monkeypatch.setattr(support, "rpc", rpc)
    prepared = wallet.prepare_transaction(transaction, kind=kind)
    assert prepared == {**transaction, "nonce": 9, "gasPrice": 100, "gas": 26_024}
    assert methods == ["eth_chainId", "eth_getTransactionCount", "eth_gasPrice", "eth_estimateGas"]
    methods.clear()
    with pytest.raises(ExampleFailure) as failure:
        wallet.prepare_transaction(transaction, kind="begin")
    assert failure.value.code == "TRANSACTION_IDENTITY_MISMATCH"
    assert methods == ["eth_chainId"]


def test_wallet_signs_real_attribution_and_rejects_wrong_domain(tmp_path, monkeypatch):
    account = Account.from_key("0x" + "01" * 32)
    artifacts = Artifacts(tmp_path / "attribution", "boundary-case")
    monkeypatch.setattr(support, "rpc", lambda _client, method, _params: hex(4663) if method == "eth_chainId" else no_call())
    wallet = LocalSigningWallet(None, artifacts, private_key=account.key, creator=account.address, chain_id=4663)
    orchestrator = get_launch_addresses(4663).orchestrator
    typed = example.build_launch_attribution_typed_data(
        chain_id=4663, verifying_contract=orchestrator, wallet=account.address,
        metadata=example.LaunchSessionCreateMetadata(name="Smoke", symbol="SMOKE", description="Signed wallet boundary"),
        idempotency_key="smoke-wallet-regression", nonce="0x" + "12" * 32, deadline=2000000000)
    signature = wallet.sign_typed_data(typed, verifying_contract=orchestrator)
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=signature) == account.address
    typed["domain"]["chainId"] = 1
    with pytest.raises(ExampleFailure):
        wallet.sign_typed_data(typed, verifying_contract=orchestrator)
    artifacts.finish()


@pytest.mark.parametrize("method", ["eth_sendTransaction", "eth_signTypedData_v4", "personal_sign", "hardhat_setBalance", "anvil_reset", "anvil_impersonateAccount"])
def test_mainnet_provider_requires_local_signing_and_preserves_source(tmp_path, monkeypatch, method):
    artifacts = Artifacts(tmp_path / "guard", "boundary-case")
    monkeypatch.setattr(Web3.HTTPProvider, "make_request", no_call)
    provider = JournalHTTPProvider("https://rpc.example", artifacts)
    with pytest.raises(ExampleFailure):
        provider.make_request(method, [])
    artifacts.finish()


def test_native_simulation_is_allowed_without_source_broadcast(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "native", "boundary-case")
    calls = []

    def request(_self, method, params):
        calls.append((method, params))
        return {"result": []}

    monkeypatch.setattr(Web3.HTTPProvider, "make_request", request)
    provider = JournalHTTPProvider("https://rpc.example", artifacts)
    assert provider.make_request("eth_simulateV1", [{}]) == {"result": []}
    assert calls == [("eth_simulateV1", [{}])]
    artifacts.finish()





def test_unavailable_native_admission_retains_failure_without_sign_or_api(construct_case, monkeypatch):
    case, plan, predicted, artifacts = construct_case("launch_erc20_v4.py")
    refused = SimpleNamespace(admitted=False, confidence="provisional", plan=plan,
                              plan_hash=lifecycle.hash_launch_plan(plan), launch_id=lifecycle.launch_id_of(plan),
                              mode=case["mode"], predicted_token=predicted,
                              simulation=SimpleNamespace(backend=None, reasons=("eth_simulateV1 unavailable",)),
                              profiles=(), hook_deployments=(), limits=None,
                              token_factory=None, token_factory_code_hash=None)
    calls = []

    async def refuse(_client, plan, **kwargs):
        calls.append((plan, kwargs))
        return refused

    monkeypatch.setattr(example, "prepare_and_plan_lifecycle_launch", refuse)
    for name in ("stage_metadata", "build_next_transaction", "api_client"):
        monkeypatch.setattr(example, name, no_call)
    with pytest.raises(ExampleFailure) as failure:
        example.admit_plan(None, plan, case, artifacts, example.Cancellation())
    artifacts.finish(failure.value)
    assert failure.value.code == "LAUNCH_NOT_ADMITTED"
    assert len(calls) == 1 and "fork" not in calls[0][1] and calls[0][1]["mode"] == "atomic"
    assert artifacts.result["chain"]["transactions"] == []
    assert "eth_simulateV1 unavailable" in (artifacts.directory / "admission.json").read_text()


def test_unified_admission_retains_actual_final_planned_launch(construct_case, monkeypatch):
    case, draft, predicted, artifacts = construct_case("launch_erc20_v4.py")
    config = lifecycle.decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config, expected_version=6)
    final = replace(draft, markets=(replace(draft.markets[0], config=lifecycle.encode_lifecycle_pool_bound_v4_market_config(
        replace(config, hook_salt="0x" + "34" * 32))),),
        buys=tuple(replace(buy, min_token_out=9950) for buy in draft.buys))
    block = LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1)
    fields = dict(launch_id=lifecycle.launch_id_of(final), plan_hash=lifecycle.hash_launch_plan(final),
                  creator=final.creator, nonce=final.nonce, phase=lifecycle.LifecyclePhase.NONE,
                  token=predicted, fee_hub=example.ZERO_ADDRESS, rewards=example.ZERO_ADDRESS,
                  prepared_markets=0, market_count=0, buy_count=0, position_count=0, deadline=final.deadline)
    canonical = lifecycle.LifecycleCanonicalProgress(**fields, mode=lifecycle.LifecycleMode.ATOMIC)
    progress = lifecycle.LaunchProgress(
        **fields, mode=case["mode"], block=block, head_block=block, awaiting_confirmations=False,
        canonical=canonical, head=canonical, confirmation_depth=1,
        confirmation_safe=True,
    )
    simulation = lifecycle.LaunchSimulation(
        "stateful", True, block, "eth_simulateV1", (), (), LaunchRpcSimulation("stateful", "eth_simulateV1", block, ()),
        execution_proof="proved", protocol_fit="proved",
    )
    deployment = get_launch_addresses(final.chain_id)
    planned = lifecycle.PlannedLaunch(
        plan=final, plan_hash=fields["plan_hash"], launch_id=fields["launch_id"], predicted_token=predicted,
        chain_id=final.chain_id, account=final.creator, mode=case["mode"], transactions=(), approvals=(),
        progress=progress, simulation=simulation, limits=LaunchExecutionLimits(),
        irreversible_costs=(), token_factory=deployment.token_factory, token_factory_code_hash="0x" + "56" * 32,
    )
    calls = []

    async def prepare(_client, plan, **options):
        calls.append((plan, options))
        return planned

    monkeypatch.setattr(example, "prepare_and_plan_lifecycle_launch", prepare)
    result = example.admit_plan(None, draft, case, artifacts, example.Cancellation())
    assert result is planned and result.plan != draft
    assert calls[0][0] is draft
    assert calls[0][1]["mode"] == case["mode"] and calls[0][1]["account"] == draft.creator
    assert all(buy.min_token_out == 9950 for buy in result.plan.buys)
    assert json.loads((artifacts.directory / "plan.json").read_text()) == lifecycle.launch_plan_to_dict(final)
    assert artifacts.result["chain"]["planHash"] == planned.plan_hash
    artifacts.finish()


def test_redactor_loads_configured_launch_key(monkeypatch):
    monkeypatch.setenv("PRIVATE_KEY", "configured-launch-secret")
    redactor = Redactor()
    assert "configured-launch-secret" in redactor.secrets


@pytest.mark.parametrize("missing", ["PRIVATE_KEY", "LAUNCH_API_URL"])
def test_missing_configuration_fails_before_transport_with_durable_result(tmp_path, monkeypatch, missing):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    monkeypatch.setenv("PRIVATE_KEY", "0x" + "01" * 32)
    monkeypatch.setenv("LAUNCH_API_URL", "https://api.example")
    monkeypatch.delenv(missing)
    monkeypatch.setattr(example, "JournalHTTPProvider", no_call)
    assert example.run_launch_example(case_definition(), argv=[]) == 1
    output = next((tmp_path / "launch-results").iterdir())
    result = json.loads((output / "result.json").read_text())
    assert result["error"]["code"] == "INVALID_CONFIGURATION"
    assert missing in result["error"]["message"]
    assert result["chain"]["transactions"] == []
    assert (output / "events.jsonl").read_text()


def test_dotenv_is_loaded_once_with_quotes_comments_and_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    monkeypatch.setenv("PRIVATE_KEY", "process-key")
    monkeypatch.delenv("LAUNCH_API_URL", raising=False)
    monkeypatch.delenv("LAUNCH_TEST_VALUE", raising=False)
    (tmp_path / ".env").write_text(
        'PRIVATE_KEY="file-key" # existing env wins\n'
        'LAUNCH_API_URL="https://api.example" # real service supplied by user\n'
        "LAUNCH_TEST_VALUE='quoted value # literal'\n"
    )
    redactor = Redactor()
    example.load_environment(redactor)
    assert example.os.environ["PRIVATE_KEY"] == "process-key"
    assert example.os.environ["LAUNCH_API_URL"] == "https://api.example"
    assert example.os.environ["LAUNCH_TEST_VALUE"] == "quoted value # literal"
    assert "file-key" in redactor.secrets
    (tmp_path / ".env").write_text("LAUNCH_TEST_VALUE=replaced\n")
    example.load_environment(redactor)
    assert example.os.environ["LAUNCH_TEST_VALUE"] == "quoted value # literal"


def test_configuration_derives_canonical_deployment_and_creator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    key = "0x" + "01" * 32
    monkeypatch.setenv("PRIVATE_KEY", key)
    monkeypatch.setenv("LAUNCH_API_URL", "https://api.example")
    monkeypatch.delenv("RPC_URL", raising=False)
    artifacts = Artifacts(tmp_path / "config", "boundary-case")
    configuration, actual_key = example.example_configuration(artifacts)
    artifacts.finish()
    assert actual_key == key
    assert configuration["creator"] == Account.from_key(key).address
    assert configuration["rpcUrl"] == example.ROBINHOOD_MAINNET_RPC.rstrip("/")
    assert configuration["orchestrator"] == get_launch_addresses(4663).orchestrator
    assert configuration["quoteAsset"] == get_launch_addresses(4663).wrapped_native
    assert "forkRpcUrl" not in configuration



def test_example_supplied_policy_only_tightens_known_limits(monkeypatch):
    for name in ("LAUNCH_CHAIN_GAS_CAP", "LAUNCH_RPC_GAS_CAP", "LAUNCH_ACCOUNT_GAS_CAP", "LAUNCH_CALLDATA_CAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LAUNCH_ACCOUNT_GAS_CAP", "12000000")
    block = LaunchBlock(1, BLOCK_HASH, 100, 30_000_000, 1)
    context = SimpleNamespace(block=block, chain_id=4663, account=CREATOR, orchestrator=TOKEN)
    limits = example.limit_resolver()(None, context)
    assert limits.account_transaction_gas_limit == 12_000_000
    assert limits.chain_transaction_gas_limit is None and limits.rpc_transaction_gas_limit is None
    assert limits.max_calldata_bytes is None


def test_fixed_example_guard_preserves_unknown_policy_and_nitro_total_envelope(monkeypatch):
    request = token_draft()
    block = LaunchBlock(1, BLOCK_HASH, 100, 1 << 50, 1)
    monkeypatch.setattr(example, "read_block", lambda *_: block)
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params:
                        hex(request.chain_id) if method == "eth_chainId" else "0x0" if method == "eth_getTransactionCount" else no_call())
    transaction = {"chainId": request.chain_id, "from": request.creator, "to": request.orchestrator,
                   "nonce": 0, "value": 0, "data": "0x0102", "gas": 34_500_000, "gasPrice": 1}
    limits = LaunchExecutionLimits(protocol="nitro", execution_gas_ceiling=32_000_000, arb_os_version=50)
    with pytest.raises(ExampleFailure) as native_failure:
        example.validate_envelope(None, transaction, request, limits)
    assert native_failure.value.code == "TRANSACTION_LIMIT_MISMATCH"
    transaction["gas"] = 32_000_000
    assert example.validate_envelope(None, transaction, request, limits) is None
    with pytest.raises(ExampleFailure) as gas_failure:
        example.validate_envelope(None, transaction, request, LaunchExecutionLimits(account_transaction_gas_limit=31_000_000))
    assert gas_failure.value.code == "TRANSACTION_LIMIT_MISMATCH"
    with pytest.raises(ExampleFailure) as calldata_failure:
        example.validate_envelope(None, transaction, request, LaunchExecutionLimits(max_calldata_bytes=1))
    assert calldata_failure.value.code == "TRANSACTION_LIMIT_MISMATCH"


@pytest.mark.parametrize("field,value", [
    ("description", "Changed"), ("websiteUrl", "https://changed.example"),
    ("twitterUrl", "https://changed.example"), ("telegramUrl", "https://changed.example"),
    ("discordUrl", "https://changed.example"), ("imageKey", "unexpected"),
])
def test_complete_signed_metadata_binding(field, value):
    session = metadata_session(metadata={**metadata_document(), field: value})
    with pytest.raises(ExampleFailure):
        support.validate_metadata_session(session, signed_metadata(), chain_id=4663, wallet=CREATOR, staging=True)


@pytest.mark.parametrize("changes", [
    {"image": {}}, {"imageStatus": "ready"}, {"status": "invented"}, {"retryable": False},
    {"canonicalStatus": "final"}, {"metadataStatus": "queued"}, {"sessionExpiresAt": "1"},
    {"updatedAt": "0"}, {"createdAt": "invalid"}, {"transactionHash": TX_HASH},
    {"token": TOKEN}, {"chainId": True},
])
def test_metadata_session_schema_expiry_and_cross_fields(changes):
    with pytest.raises(ExampleFailure):
        support.validate_metadata_session(metadata_session(**changes), signed_metadata(),
                                          chain_id=4663, wallet=CREATOR, staging=True)


def test_metadata_session_refresh_requires_all_fields():
    session = metadata_session()
    del session["metadata"]["discordUrl"]
    with pytest.raises(ExampleFailure):
        support.validate_metadata_session(session, signed_metadata(), chain_id=4663, wallet=CREATOR, session_id="session")


@pytest.mark.parametrize("field,value", [("gas", 100001), ("gasPrice", 2), ("nonce", 1), ("value", 1), ("data", "0x02")])
def test_executor_refuses_changed_held_envelope_before_signing(tmp_path, monkeypatch, field, value):
    plan = token_draft()
    client = object()
    artifacts = Artifacts(tmp_path / "held", "boundary-case")
    held = lifecycle.LifecycleTransaction("atomic", "atomic", 4663, CREATOR, plan.orchestrator, "0x01", 0, (), (), gas_limit=100000, gas_price=1)
    changed = held.as_transaction()
    changed[field] = value
    calls = []
    def prove(actual_client, launch, **options):
        calls.append("proof")
        return SimpleNamespace(kind=held.kind, step_id=held.step_id, as_transaction=lambda: changed)
    monkeypatch.setattr(example, "build_next_transaction", prove)
    monkeypatch.setattr(example, "read_launch_progress", lambda *_args, **_kwargs: clean_progress(phase=lifecycle.LifecyclePhase.NONE))
    launch = SimpleNamespace(transactions=(held,), receipt_references=(), approvals=(), mode="atomic")
    api = SimpleNamespace(get_launch_upload_session=lambda *_: metadata_session())
    wallet = SimpleNamespace(send_transaction=no_call, client=client, account=SimpleNamespace(address=CREATOR))
    with pytest.raises(ExampleFailure) as failure:
        example.execute_plan(client, launch, plan, artifacts, example.Cancellation(),
                             wallet=wallet, api=api, recovery=metadata_recovery())
    assert failure.value.code == "TRANSACTION_IDENTITY_MISMATCH" and len(calls) == 1
    artifacts.finish(failure.value)


def test_expired_launch_is_refused_before_signing(monkeypatch):
    monkeypatch.setattr(example, "read_block", lambda *_: LaunchBlock(1, BLOCK_HASH, 3600, 32000000, 1))
    monkeypatch.setattr(example, "rpc", no_call)
    with pytest.raises(ExampleFailure):
        example.validate_envelope(None, {}, token_draft(), LaunchExecutionLimits())


def test_api_only_exact_request_is_durable_before_post_and_unknown_issuance_is_not_replayed(tmp_path, monkeypatch):
    path = tmp_path / "session.private.json"
    monkeypatch.setenv("LAUNCH_API_TEST_URL", "http://127.0.0.1:18763")
    monkeypatch.setenv("LAUNCH_API_TEST_PRIVATE_KEY", "0x" + "01" * 32)
    attempts = []
    def transport(method, url, headers, body, timeout):
        attempts.append(method)
        recovery = json.loads(path.read_text())
        assert recovery["request"] == json.loads(body)
        assert recovery["idempotencyKey"] == headers["Idempotency-Key"]
        assert path.stat().st_mode & 0o777 == 0o600
        raise RuntimeError("unknown issuance")
    monkeypatch.setattr(api_example, "LaunchApiClient", lambda config:
                        LaunchApiClient(replace(config, retries=1), transport=transport))
    with pytest.raises(LaunchApiError):
        api_example.main(["--signed", "--state-file", str(path)])
    assert attempts == ["POST"]
    with pytest.raises(SystemExit):
        api_example.main(["--state-file", str(path)])
    assert attempts == ["POST"]


def test_private_state_update_is_atomic_and_preserves_original_intent(tmp_path, monkeypatch):
    path = tmp_path / "state.private.json"
    initial = {"request": {"authorization": {"signature": "original"}}, "idempotencyKey": "original"}
    support.save_private_state(path, initial, create=True)
    observed = []
    original_replace = support.os.replace
    def replace_state(source, target):
        assert json.loads(path.read_text()) == initial
        observed.append(json.loads(Path(source).read_text()))
        original_replace(source, target)
    monkeypatch.setattr(support.os, "replace", replace_state)
    final = {**initial, "capability": "original-capability", "sessionId": "session"}
    support.save_private_state(path, final)
    assert observed == [final] and json.loads(path.read_text()) == final
    assert path.stat().st_mode & 0o777 == 0o600


def test_publication_http_attempt_reserves_backoff_in_absolute_budget(tmp_path, monkeypatch, publication_context):
    artifacts = Artifacts(tmp_path / "budget", "boundary-case")
    now = [0.0]
    observed = []
    def transport(method, url, headers, body, timeout):
        observed.append((method, timeout))
        if method == "GET":
            return LaunchApiResponse(200, {}, json.dumps(metadata_session()).encode())
        now[0] += timeout
        return LaunchApiResponse(503, {}, b'{"code":"INDEXER_UNAVAILABLE"}')
    def client(url, **options):
        return LaunchApiClient(LaunchApiConfig(base_url=url, allow_loopback_http=True, **options), transport=transport)
    monkeypatch.setattr(example, "api_client", client)
    monkeypatch.setattr(example.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(example.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    api = client("http://127.0.0.1:18763", timeout=15)
    with pytest.raises(LaunchApiError):
        execution, receipt, references = publication_context
        example.publish_activation(execution, api, metadata_recovery(), token_draft(), TOKEN, receipt, references,
                                   1, artifacts, example.Cancellation())
    assert observed == [("GET", 0.25), ("POST", 0.25)]
    assert now[0] == 1
    artifacts.finish()


def test_api_only_original_capability_survives_validation_and_read_replay(tmp_path, monkeypatch, capsys):
    path = tmp_path / "session.private.json"
    monkeypatch.setenv("LAUNCH_API_TEST_URL", "http://127.0.0.1:18763")
    monkeypatch.setenv("LAUNCH_API_TEST_PRIVATE_KEY", "0x" + "01" * 32)
    calls = []
    original_capability = "private-once-returned"
    def transport(method, url, headers, body, timeout):
        calls.append(method)
        recovery = json.loads(path.read_text())
        document = recovery["request"]["metadata"]
        session = metadata_session(wallet=recovery["request"]["wallet"],
                                   metadata={**metadata_document(), **document})
        if len(calls) == 1:
            assert recovery["request"] == json.loads(body)
            session["capability"] = original_capability
        else:
            assert recovery["capability"] == original_capability
        return LaunchApiResponse(200, {}, json.dumps(session).encode())
    monkeypatch.setattr(api_example, "LaunchApiClient", lambda config: LaunchApiClient(config, transport=transport))
    assert api_example.main(["--signed", "--state-file", str(path)]) == 0
    assert calls == ["POST", "GET", "POST"]
    assert json.loads(path.read_text())["capability"] == original_capability
    assert original_capability not in capsys.readouterr().out


def test_artifacts_preserve_structured_keys_and_redact_only_values(tmp_path):
    artifacts = Artifacts(tmp_path / "structured-redaction", "boundary-case")
    artifacts.redact.remember_url("https://example.invalid/token/")
    artifacts.redact.remember("registered-secret")
    artifacts.save("plan.json", {
        "token": {"nftUnit": "100000000000000000000", "metadataURI": "https://example.invalid/token/"},
        "capability": "private-capability", "note": "registered-secret",
    })
    document = json.loads((artifacts.directory / "plan.json").read_text())
    assert document["token"]["nftUnit"] == "100000000000000000000"
    assert document["capability"] == support.REDACTED and document["note"] == support.REDACTED
    assert "example.invalid/token/" not in json.dumps(document)
    artifacts.finish()


def test_original_finalized_plan_is_private_before_signature_and_survives_session_updates(construct_case, monkeypatch):
    _, draft, _, artifacts = construct_case("launch_erc20_v4.py")
    key = "0x" + "01" * 32
    account = Account.from_key(key)
    plan = replace(draft, creator=account.address,
                   buys=tuple(replace(buy, min_token_out=9950) for buy in draft.buys))
    expected_plan = lifecycle.launch_plan_to_dict(plan)
    artifacts.redact.remember_url("https://example.invalid/token/")
    monkeypatch.setattr(support, "rpc", lambda *_: hex(plan.chain_id))
    class CheckingWallet(LocalSigningWallet):
        def sign_typed_data(self, typed_data, **options):
            saved = json.loads((artifacts.directory / "recovery.private.json").read_text())
            assert saved["plan"] == expected_plan and saved["mode"] == "staged" and saved["confirmations"] == 2
            return super().sign_typed_data(typed_data, **options)
    def transport(method, url, headers, body, timeout):
        request = json.loads(body)
        document = {**metadata_document(), **request["metadata"]}
        session = metadata_session(wallet=plan.creator, metadata=document, capability="original-capability")
        return LaunchApiResponse(200, {}, json.dumps(session).encode())
    api = LaunchApiClient(LaunchApiConfig(base_url="http://127.0.0.1:18763", allow_loopback_http=True), transport=transport)
    wallet = CheckingWallet(None, artifacts, private_key=key, creator=plan.creator, chain_id=plan.chain_id)
    recovery = example.stage_metadata(None, api, plan, artifacts, example.Cancellation(),
                                      wallet=wallet, mode="staged", confirmations=2)
    saved = json.loads((artifacts.directory / "recovery.private.json").read_text())
    assert saved["plan"] == recovery["plan"] == expected_plan
    assert saved["mode"] == "staged" and saved["confirmations"] == 2
    assert saved["session"]["capability"] == "original-capability"
    assert (artifacts.directory / "recovery.private.json").stat().st_mode & 0o777 == 0o600
    artifacts.finish()


def test_public_dto_optional_social_fields_still_bind_signed_metadata():
    session = metadata_session(token=TOKEN, transactionHash=TX_HASH, status="optimistic", canonicalStatus="optimistic")
    document = {key: value for key, value in metadata_document().items() if value is not None}
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16",
            "canonicalStatus": "optimistic", "launchedToken": {"name": signed_metadata().name, "symbol": signed_metadata().symbol},
            "metadata": {"document": document}}
    detail = {"chainId": 4663, "indexedBlock": "16", "item": item}
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10"}
    example.verify_publication(session, detail, token_draft(), TOKEN, receipt, "session", signed_metadata())
    for field in ("description", "websiteUrl", "twitterUrl", "telegramUrl", "discordUrl", "imageKey"):
        changed = {**document, field: "https://changed.example" if field.endswith("Url") else "changed"}
        with pytest.raises(ExampleFailure):
            example.verify_publication(session, {**detail, "item": {**item, "metadata": {"document": changed}}},
                                       token_draft(), TOKEN, receipt, "session", signed_metadata())


@pytest.mark.parametrize("supply_tokens", [1000000, 10000])
@pytest.mark.parametrize("token0", [True, False])
@pytest.mark.parametrize("count", [1, 2, 3])
def test_actual_supply_pricing_has_independent_spacing_valuation_and_fundable_liquidity(supply_tokens, token0, count):
    supply = supply_tokens * 10**18
    results = [example.derive_market_pricing(
        supply=supply, budget=supply // 2, token0=token0, quote_usd_x18=2000 * 10**18,
        target=5000 * 10**18, count=count, venue=venue) for venue in ("v4", "abyss")]
    assert results[0] == results[1]
    review = results[0]
    # Independent Decimal logarithm, not the SDK's integer sqrt / TickMath inverse.
    with localcontext() as context:
        context.prec = 100
        raw_ratio = Decimal(5000) / Decimal(2000 * supply_tokens)
        ratio = raw_ratio if token0 else 1 / raw_ratio
        raw_tick = (ratio.ln() / Decimal("1.0001").ln()).to_integral_value(rounding=ROUND_FLOOR)
        expected_tick = int((raw_tick / 60).to_integral_value(
            rounding=ROUND_CEILING if token0 else ROUND_FLOOR)) * 60
    assert review["openingTick"] == expected_tick
    assert review["sqrtPriceX96"] != str(1 << 96)
    root = int(review["sqrtPriceX96"])
    ratio = Fraction(root * root, 1 << 192) if token0 else Fraction(1 << 192, root * root)
    valuation = ratio * 2000 * supply_tokens
    assert 5000 <= valuation < Fraction(5031)
    assert int(review["openingValuationUsdX18"]) == valuation * 10**18 // 1
    positions = review["positions"]
    assert len(positions) == count and sum(int(p["maxTokenAmount"]) for p in positions) == supply // 2
    for position in positions:
        lower = example.get_sqrt_ratio_at_tick(position["tickLower"])
        upper = example.get_sqrt_ratio_at_tick(position["tickUpper"])
        required_per_liquidity = (Fraction((1 << 96) * (upper - lower), lower * upper) if token0
                                  else Fraction(upper - lower, 1 << 96))
        amount = required_per_liquidity * int(position["liquidity"])
        debit = -(-amount.numerator // amount.denominator)
        assert debit == int(position["expectedMintDebit"]) <= int(position["maxTokenAmount"])
        if count > 1:
            assert required_per_liquidity * (int(position["liquidity"]) + 1) > int(position["maxTokenAmount"])
    assert int(review["roundingBurn"]) < 10**12


@pytest.mark.parametrize("value", ["0", "-1", "1e3", "01", "NaN", "1.0000000000000000001", "1.", ".5"])
def test_invalid_exact_target_is_rejected(value):
    with pytest.raises(ExampleFailure):
        example.parse_target_market_cap(value)


def test_fractional_target_and_api_price_remain_exact_without_round_age_gate():
    assert example.parse_target_market_cap("5000.000000000000000001") == 5000 * 10**18 + 1
    snapshot = price_snapshot()
    snapshot["items"][0]["priceUsdX18"] = "2000123456780000000001"
    quote = example.validate_price_snapshot(snapshot, api_origin=API_ORIGIN, retrieved_at=PRICE_RETRIEVED_AT)
    assert quote["priceUsdX18"] == "2000123456780000000001"
    assert quote["sourceTimestamp"] == "1791552898" and quote["upstreamAgeSeconds"] == "7200"
    assert quote["retrievedAt"] == str(PRICE_RETRIEVED_AT) and quote["source"] == "abyss-api"


@pytest.mark.parametrize("price", ["0", "-1", "01", "1e18", "1.2", True, 1, str(2**256), "9" * 79])
def test_api_price_requires_exact_positive_uint256(price):
    snapshot = price_snapshot()
    snapshot["items"][0]["priceUsdX18"] = price
    with pytest.raises(ExampleFailure) as failure:
        example.validate_price_snapshot(snapshot, api_origin=API_ORIGIN, retrieved_at=PRICE_RETRIEVED_AT)
    assert failure.value.code == "INVALID_PRICE_QUOTE"


@pytest.mark.parametrize("field,value", [
    ("chainId", 1), ("chainId", True), ("address", TOKEN), ("decimals", 8), ("decimals", True),
    ("metadataStatus", "pending"), ("priced", False), ("priced", 1), ("source", "pool"),
    ("provenance", None),
])
def test_api_wrong_or_unpriced_weth_rejects(field, value):
    snapshot = price_snapshot()
    snapshot["items"][0][field] = value
    with pytest.raises(ExampleFailure):
        example.validate_price_snapshot(snapshot, api_origin=API_ORIGIN, retrieved_at=PRICE_RETRIEVED_AT)


@pytest.mark.parametrize("field,value", [
    ("anchorToken", TOKEN), ("anchor", {"kind": "manual"}),
    ("anchor", {"kind": "chainlink", "aggregator": TOKEN}),
    ("path", [TOKEN]), ("hopCount", 1), ("hopCount", False),
    ("sourceTimestamp", None), ("sourceTimestamp", "1791560099"), ("graphBlockNumber", None),
])
def test_api_direct_anchor_and_observation_binding_rejects(field, value):
    snapshot = price_snapshot()
    snapshot["items"][0]["provenance"][field] = value
    with pytest.raises(ExampleFailure):
        example.validate_price_snapshot(snapshot, api_origin=API_ORIGIN, retrieved_at=PRICE_RETRIEVED_AT)


@pytest.mark.parametrize("changed", ["chain", "missing", "duplicate", "oversized", "index", "block"])
def test_api_snapshot_identity_and_bounded_selection_rejects(changed):
    snapshot = price_snapshot()
    if changed == "chain":
        snapshot["chainId"] = 1
    elif changed == "missing":
        snapshot["items"] = []
        snapshot["nextCursor"] = "another-page-must-not-be-fetched"
    elif changed in {"duplicate", "oversized"}:
        snapshot["items"] *= 2 if changed == "duplicate" else 101
    else:
        snapshot["indexedBlock" if changed == "index" else "blockNumber"] = "01"
    with pytest.raises(ExampleFailure):
        example.validate_price_snapshot(snapshot, api_origin=API_ORIGIN, retrieved_at=PRICE_RETRIEVED_AT)


def proved_review_launch(plan):
    # Real receipt wire shape exercises the public decoder and intent binding.
    abi_type = "(" + ",".join(component["type"] for component in LAUNCH_RECEIPT_COMPONENTS_V1) + ")"
    values = (bytes.fromhex(lifecycle.launch_id_of(plan)[2:]), bytes.fromhex(example.hash_launch_plan(plan)[2:]),
              TOKEN, plan.creator, example.ZERO_ADDRESS, len(plan.markets),
              sum(len(lifecycle.decode_lifecycle_pool_bound_v4_market_config(m.config).positions)
                  if m.config_version == 6 else len(lifecycle.decode_lifecycle_abyss_market_config(m.config).positions)
                  for m in plan.markets),
              tuple(b.quote_amount_in for b in plan.buys),
              tuple(350 * 10**18 * b.quote_amount_in // 10**15 for b in plan.buys))
    call = SimpleNamespace(success=True, return_data="0x" + encode([abi_type], [values]).hex())
    return SimpleNamespace(plan=plan, plan_hash=example.hash_launch_plan(plan), mode="atomic",
                           simulation=SimpleNamespace(confidence="stateful", transactions=[SimpleNamespace(kind="atomic")],
                                                      evidence=SimpleNamespace(calls=[call])))


def test_economic_review_retains_hash_as_provenance_and_reports_proved_all_in_price(construct_case, monkeypatch, capsys):
    _, plan, _, artifacts = construct_case("launch_erc20_v4.py")
    artifacts.redact.remember_url(API_ORIGIN)
    launch = proved_review_launch(plan)
    monkeypatch.setattr("builtins.input", no_call)
    monkeypatch.setattr(example.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    review, digest = example.review_economics(launch, artifacts, example.Cancellation())
    saved = json.loads((artifacts.directory / "economic-review.json").read_text())
    assert not (artifacts.directory / "economic-admission.json").exists()
    output = int(saved["review"]["openingBuys"][0]["tokenOut"])
    assert saved["review"]["openingBuys"][0]["allInExecutionUnitPriceUsdX18"] == str(
        plan.buys[0].quote_amount_in * 2486280000000000000000 // output)
    assert int(saved["review"]["openingBuys"][0]["feesAndPriceImpactPremiumBps"]) > 0
    assert saved["review"]["quotes"][0]["apiOrigin"] == API_ORIGIN
    assert saved["review"]["quotes"][0]["sourceTimestamp"] == "1791552898"
    assert saved["review"]["quotes"][0]["retrievedAt"] == str(PRICE_RETRIEVED_AT)
    assert saved["review"]["quotes"][0]["upstreamAgeSeconds"] == "7200"
    assert digest == saved["reviewHash"] == example.economic_review_hash(review)
    assert review["expectedRetainedSupply"] == artifacts.economic_pricing["expectedRetainedSupply"]
    assert digest in capsys.readouterr().out
    artifacts.finish()


@pytest.mark.parametrize("changed", ["price", "apiUrl", "provider", "endpoint", "target", "review"])
def test_price_or_bound_intent_change_refuses_signature_without_rewriting(construct_case, monkeypatch, changed):
    _, plan, predicted, artifacts = construct_case("launch_erc20_v4.py")
    configuration = {"chainId": 4663, "quoteAsset": plan.markets[0].quote_asset, "apiUrl": API_ORIGIN}
    provider = SimpleNamespace(endpoint_uri="https://reviewed.invalid")
    client = SimpleNamespace(provider=provider)
    review = {**artifacts.economic_pricing, "planHash": example.hash_launch_plan(plan)}
    snapshot = price_snapshot()
    monkeypatch.setattr(example, "read_price_snapshot", lambda *_: (snapshot, API_ORIGIN, PRICE_RETRIEVED_AT))
    guard = example.economic_signature_guard(client, configuration, plan, predicted, review,
                                            example.economic_review_hash(review), artifacts)
    if changed == "price":
        snapshot["items"][0]["priceUsdX18"] = "2486280000000000000001"
    elif changed == "apiUrl":
        configuration["apiUrl"] = "https://other.invalid"
    elif changed == "provider":
        client.provider = SimpleNamespace(endpoint_uri=provider.endpoint_uri)
    elif changed == "endpoint":
        provider.endpoint_uri = "https://other.invalid"
    elif changed == "target":
        configuration["targetMarketCapUsdX18"] = 6000 * 10**18
    else:
        review["actualMintedSupply"] = "1"
    with pytest.raises(ExampleFailure) as failure:
        guard()
    assert failure.value.code == "ECONOMIC_REVIEW_CHANGED"
    assert not artifacts.receipts
    artifacts.finish(failure.value)


@pytest.mark.parametrize("advance_upstream", [False, True])
def test_unchanged_api_price_preserves_explicit_review_and_allows_signer(construct_case, monkeypatch, advance_upstream):
    _, plan, predicted, artifacts = construct_case("launch_erc20_v4.py")
    key = "0x" + "01" * 32
    account = Account.from_key(key)
    plan = replace(plan, creator=account.address)
    configuration = {"chainId": 4663, "quoteAsset": plan.markets[0].quote_asset, "apiUrl": API_ORIGIN}
    client = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="https://reviewed.invalid"))
    launch = proved_review_launch(plan)
    monkeypatch.setattr("builtins.input", no_call)
    review, digest = example.review_economics(launch, artifacts, example.Cancellation())
    original_quote = dict(review["quotes"][0])
    snapshot = price_snapshot()
    snapshot["indexedBlock"] = "84269487"
    snapshot["blockNumber"] = "84269371"
    snapshot["items"][0]["provenance"]["graphBlockNumber"] = "84269371"
    if advance_upstream:
        snapshot["items"][0]["provenance"]["sourceTimestamp"] = "1791552899"
    monkeypatch.setattr(example, "read_price_snapshot",
                        lambda *_: (snapshot, API_ORIGIN, PRICE_RETRIEVED_AT + 1))
    monkeypatch.setattr(support, "rpc", lambda *_: hex(4663))
    monkeypatch.setattr(example, "contract", no_call)
    wallet = LocalSigningWallet(client, artifacts, private_key=key, creator=account.address, chain_id=4663)
    wallet.signature_guard = example.economic_signature_guard(client, configuration, plan, predicted, review, digest, artifacts)
    typed = example.build_launch_attribution_typed_data(
        chain_id=4663, verifying_contract=plan.orchestrator, wallet=account.address,
        metadata=signed_metadata(), idempotency_key="api-review-boundary",
        nonce="0x" + "12" * 32, deadline=2000000000)
    signature = wallet.sign_typed_data(typed, verifying_contract=plan.orchestrator)
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=signature) == account.address
    assert review["quotes"][0] == original_quote and review["quotes"][0]["upstreamAgeSeconds"] == "7200"
    assert example.economic_review_hash(review) == digest and not artifacts.receipts
    artifacts.finish()


@pytest.mark.parametrize("kind", ["transaction", "attribution"])
def test_wallet_economic_guard_blocks_before_signing_or_private_raw_artifacts(construct_case, monkeypatch, kind):
    key = "0x" + "01" * 32
    account = Account.from_key(key)
    _, plan, predicted, artifacts = construct_case("launch_erc20_v4.py")
    wallet = LocalSigningWallet(None, artifacts, private_key=key, creator=account.address, chain_id=4663)
    monkeypatch.setattr(support, "rpc", lambda *_: hex(4663))
    configuration = {"chainId": 4663, "quoteAsset": plan.markets[0].quote_asset, "apiUrl": API_ORIGIN}
    client = SimpleNamespace(provider=SimpleNamespace(endpoint_uri="https://reviewed.invalid"))
    review = {**artifacts.economic_pricing, "planHash": example.hash_launch_plan(plan)}
    snapshot = price_snapshot()
    snapshot["items"][0]["priceUsdX18"] = "2486280000000000000001"
    monkeypatch.setattr(example, "read_price_snapshot", lambda *_: (snapshot, API_ORIGIN, PRICE_RETRIEVED_AT))
    monkeypatch.setattr(type(wallet.account), "sign_transaction", no_call)
    monkeypatch.setattr(type(wallet.account), "sign_message", no_call)
    wallet.signature_guard = example.economic_signature_guard(
        client, configuration, plan, predicted, review, example.economic_review_hash(review), artifacts)
    with pytest.raises(ExampleFailure) as failure:
        if kind == "transaction":
            wallet.send_transaction("atomic", wallet_envelope(account))
        else:
            orchestrator = get_launch_addresses(4663).orchestrator
            typed = example.build_launch_attribution_typed_data(
                chain_id=4663, verifying_contract=orchestrator, wallet=account.address,
                metadata=signed_metadata(), idempotency_key="pricing-boundary",
                nonce="0x" + "12" * 32, deadline=2000000000)
            wallet.sign_typed_data(typed, verifying_contract=orchestrator)
    assert failure.value.code == "ECONOMIC_REVIEW_CHANGED" and not artifacts.receipts
    assert not tuple(artifacts.directory.glob("signed-*.private.json"))
    artifacts.finish(failure.value)


@pytest.fixture
def price_api_server():
    """Exercise the actual HTTP/parser boundary using only an owned loopback service."""
    state = {"body": json.dumps(price_snapshot()).encode(), "status": 200, "length": True}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            if state["status"] == 302:
                self.send_header("Location", "/redirect-must-not-be-followed")
            if state["length"]:
                self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            try:
                self.wfile.write(state["body"])
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state, calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_price_reader_uses_one_bounded_api_snapshot_not_contract_rounds(price_api_server, monkeypatch):
    url, state, calls = price_api_server
    snapshot = price_snapshot()
    snapshot["nextCursor"] = "do-not-follow"
    state["body"] = json.dumps(snapshot).encode()
    block = LaunchBlock(42, BLOCK_HASH, 100, 32000000, 1)
    monkeypatch.setattr(example, "rpc", lambda _client, method, _params: hex(4663) if method == "eth_chainId" else no_call())
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    monkeypatch.setattr(example, "contract", no_call)
    monkeypatch.setattr(support.time, "time", lambda: PRICE_RETRIEVED_AT)
    quote = example.read_price_quote(None, {
        "chainId": 4663, "quoteAsset": example.get_addresses(4663).weth, "apiUrl": url,
    })
    assert calls == ["/api/v1/token-prices?chainId=4663&limit=100"]
    assert quote["apiOrigin"] == url and quote["priceUsdX18"] == "2486280000000000000000"
    assert quote["sourceTimestamp"] == "1791552898" and quote["upstreamAgeSeconds"] == "7200"
    assert quote["blockNumber"] == "84269370" and quote["graphBlockNumber"] == "84195615"
    assert quote["rpcBlockNumber"] == "42" and quote["rpcBlockHash"] == BLOCK_HASH


@pytest.mark.parametrize("failure", ["status", "redirect", "json", "declared-size", "streamed-size"])
def test_price_api_transport_failure_stops_without_retry_redirect_or_fallback(price_api_server, monkeypatch, failure):
    url, state, calls = price_api_server
    if failure in {"status", "redirect"}:
        state["status"] = 503 if failure == "status" else 302
    elif failure == "json":
        state["body"] = b'{"items":'
    else:
        monkeypatch.setattr(support, "PRICE_RESPONSE_MAX_BYTES", 128)
        state["length"] = failure == "declared-size"
    with pytest.raises(ExampleFailure) as rejected:
        support.read_price_snapshot(url, 4663)
    assert rejected.value.code == "PRICE_QUOTE_UNAVAILABLE"
    assert calls == ["/api/v1/token-prices?chainId=4663&limit=100"]


@pytest.mark.parametrize("unsafe", [
    "https://user:secret@api.example", "https://api.example/private-secret",
    "https://api.example?key=secret", "https://api.example#secret", "http://remote.example",
])
def test_visible_api_origin_never_exposes_endpoint_credentials_paths_or_queries(unsafe):
    redactor = Redactor()
    redactor.remember_url(API_ORIGIN)
    assert redactor({"apiOrigin": API_ORIGIN}) == {"apiOrigin": API_ORIGIN}
    assert redactor({"apiOrigin": unsafe}) == {"apiOrigin": support.REDACTED}


@pytest.mark.parametrize("complete", [False, True])
def test_profiled_economic_review_uses_real_curve_quotes_without_a_fake_receipt(construct_case, complete):
    _, plan, predicted, artifacts = construct_case("launch_erc20_abyss.py")
    quotes = tuple(lifecycle.LifecycleOpeningBuyQuote(index, buy.market_index,
        buy.quote_amount_in, buy.quote_amount_in, 10**18, 1 << 96)
        for index, buy in enumerate(plan.buys)) if complete else ()
    launch = SimpleNamespace(plan=plan, predicted_token=predicted,
        simulation=SimpleNamespace(confidence="profiled",
            gas_profile=lifecycle.LifecycleGasProfileEvidence("test-profile", lifecycle.LifecycleLaunchGasBounds(0, 0), quotes),
            transactions=(), evidence=SimpleNamespace(calls=())))
    if complete:
        rows = example.opening_buy_review(launch, artifacts.economic_pricing)
        assert len(rows) == len(plan.buys)
        assert all(row["quoteEvidence"] == "local-exact-curve" and row["tokenOut"] == str(10**18)
            for row in rows)
    else:
        with pytest.raises(ExampleFailure) as failure:
            example.opening_buy_review(launch, artifacts.economic_pricing)
        assert failure.value.code == "INVALID_ECONOMIC_PROOF"
    artifacts.finish()
