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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest
from requests import HTTPError
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data

from examples import launch_examples as example
from examples import _launch_support as support
from examples._launch_support import Artifacts, JournalHTTPProvider, LocalSigningWallet, Redactor, ExampleFailure, error_details, loopback_url, mainnet_url
from black_market_sdk import (
    LaunchBlock, LaunchExecutionLimits, LaunchPlanV1, LaunchPublishPending, LifecycleTokenConfig,
    LaunchApiError, KnownLifecycleProfile, get_known_lifecycle_profile, get_launch_addresses,
    predict_launch_token, to_launch_plan_tuple,
)
from black_market_sdk import lifecycle
from black_market_sdk.lifecycle_rpc import LaunchRpcError, LaunchRpcSimulation

CREATOR = "0x0000000000000000000000000000000000000001"
TOKEN = "0x0000000000000000000000000000000000000002"
TX_HASH = "0x" + "ab" * 32
BLOCK_HASH = "0x" + "cd" * 32


def case_definition():
    return {"id": "boundary-case", "description": "Unit boundary", "tokenKind": 0, "rewardMode": 0,
            "mode": "atomic", "markets": [{"venue": "v4", "positions": 1}], "buysPerMarket": 1}




def token_draft():
    return LaunchPlanV1(
        chain_id=4663, orchestrator=get_launch_addresses(4663).orchestrator, creator=CREATOR, nonce=123,
        token=LifecycleTokenConfig(0, 0, "Smoke boundary-case", "SMOKE", 1000000 * 10**18, 0, "", bytes(32), CREATOR, False),
        funding=(), fee_assets=(), markets=(), buys=(), deadline=3600, executor_fee_bps=275,
    )


@pytest.fixture
def construct_case(tmp_path, monkeypatch):
    """Build the real fixed economics using release metadata, with no RPC or signing."""
    block = LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1)
    deployment = get_launch_addresses(4663)
    configuration = {"chainId": 4663, "orchestrator": deployment.orchestrator,
                     "creator": CREATOR, "quoteAsset": deployment.wrapped_native,
                     "oracleConfigId": "0x" + "12" * 32}
    profiles = {
        venue: [get_known_lifecycle_profile(chain_id=4663, orchestrator=deployment.orchestrator, key=key).profile]
        for venue, key in (("v4", KnownLifecycleProfile.V4_FIXED_FEE_POOL), ("abyss", KnownLifecycleProfile.ABYSS_3))
    }
    monkeypatch.setattr(example, "read_block", lambda *_args: block)
    monkeypatch.setattr(example, "predict_launch_token", lambda *_args, **_kwargs: TOKEN)
    monkeypatch.setattr(example, "discover_profiles", lambda *_args: profiles)
    monkeypatch.setitem(sys.modules, "launch_examples", example)

    def construct(filename, *, markets=None):
        case = runpy.run_path(str(Path(example.__file__).with_name(filename)))["CASE"]
        if markets is not None:
            case = {**case, "markets": markets}
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
        base_maximum = market.token_budget // len(config.positions)
        assert maxima[-1] == market.token_budget - base_maximum * (len(config.positions) - 1)
        if spec["venue"] == "v4":
            assert isinstance(config, lifecycle.LifecyclePoolBoundV4MarketConfigV6)
            assert config.minimum_hook_fee_pips < config.hook_fee_pips
            assert config.fee_sensitivity_pips_seconds_per_tick > 0
            assert example.hex_bytes(config.profile_id) == market.profile_id
    assert len(plan.buys) == len(plan.markets) * case["buysPerMarket"]
    assert plan.funding[0].amount == sum(buy.quote_amount_in for buy in plan.buys)
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
    def interrupted(_args, artifacts, cancellation):
        artifacts.result.update(scope="end-to-end", execution="executed")
        artifacts.result["chain"]["status"] = "executing"
        artifacts.enter("receipt-atomic")
        artifacts.broadcast_attempt("atomic", {"from": CREATOR, "data": "0x", "gas": 100000}, TX_HASH)
        cancellation.signal(signal.SIGINT, None)
        cancellation.check()

    monkeypatch.setattr(example, "run", interrupted)
    monkeypatch.chdir(tmp_path)
    assert example.run_launch_example(case_definition()) == 1
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


def test_only_202_is_polled_and_deadline_is_bounded(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "pending", "boundary-case")
    now = [0.0]
    calls = []
    pending = {"sessionId": "session", "status": "awaiting_indexer", "token": None}

    class PendingApi:
        config = SimpleNamespace(base_url="http://127.0.0.1:18764", timeout=15)

        def publish_launch_upload_session(self, chain_id, session_id, capability, request):
            calls.append((chain_id, session_id, capability, request.transaction_hash))
            raise LaunchPublishPending(pending, 5000)

    api = PendingApi()
    monkeypatch.setattr(example.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    cancellation = SimpleNamespace(check=lambda: None, sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    recovery = {"session": {"sessionId": "session", "capability": "private-capability"}}
    with pytest.raises(ExampleFailure) as failure:
        example.publish_activation(api, recovery, token_draft(), TOKEN, {"transactionHash": TX_HASH}, 1, artifacts, cancellation)
    artifacts.finish(failure.value)
    assert failure.value.code == "API_INDEX_TIMEOUT"
    assert calls == [(4663, "session", "private-capability", TX_HASH)]
    assert now[0] == 1 and artifacts.result["api"]["lastSession"] == pending


def test_non_pending_api_failure_is_not_retried_or_relaunched(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "api-error", "boundary-case")
    calls = []

    class FailedApi:
        config = SimpleNamespace(base_url="http://127.0.0.1:18764", timeout=15)

        def publish_launch_upload_session(self, *_args):
            calls.append("publish")
            raise LaunchApiError(503, "INDEXER_UNAVAILABLE")

    api = FailedApi()
    monkeypatch.setattr(example, "api_client", lambda *_args, **_kwargs: api)
    monkeypatch.setattr(example, "execute_plan", no_call)
    cancellation = SimpleNamespace(check=lambda: None, sleep=no_call)
    with pytest.raises(LaunchApiError) as failure:
        example.publish_activation(api, {"session": {"sessionId": "session", "capability": "private-capability"}}, token_draft(), TOKEN,
                                 {"transactionHash": TX_HASH}, 90, artifacts, cancellation)
    artifacts.finish(failure.value)
    assert calls == ["publish"] and failure.value.code == "INDEXER_UNAVAILABLE"


def test_publication_success_requires_actual_matching_canonical_representation():
    plan = token_draft()
    session = {"sessionId": "session", "chainId": plan.chain_id, "wallet": CREATOR, "token": TOKEN,
               "transactionHash": TX_HASH, "status": "optimistic", "canonicalStatus": "optimistic"}
    item = {"token": TOKEN, "transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "16", "canonicalStatus": "optimistic",
            "launchedToken": {"name": plan.token.name, "symbol": plan.token.symbol},
            "metadata": {"document": {"name": plan.token.name, "symbol": plan.token.symbol}}}
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10"}
    detail = {"chainId": plan.chain_id, "indexedBlock": "16", "item": item}
    example.verify_publication(session, detail, plan, TOKEN, receipt, "session")
    wrong = {**detail, "item": {**item, "transactionHash": "0x" + "ef" * 32}}
    with pytest.raises(ExampleFailure) as failure:
        example.verify_publication(session, wrong, plan, TOKEN, receipt, "session")
    assert failure.value.code == "API_PUBLICATION_MISMATCH"




def test_burn_disposition_never_burns_quote_in_either_address_orientation():
    definition = {**case_definition(), "burnBps": 3000}
    for token, quote in ((TOKEN, CREATOR), (CREATOR, TOKEN)):
        policies = example.fee_policies(token, quote, definition)
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
        replace(config, hook_salt="0x" + "34" * 32))),))
    block = LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1)
    fields = dict(launch_id=lifecycle.launch_id_of(final), plan_hash=lifecycle.hash_launch_plan(final),
                  creator=final.creator, nonce=final.nonce, phase=lifecycle.LifecyclePhase.NONE,
                  token=predicted, fee_hub=example.ZERO_ADDRESS, rewards=example.ZERO_ADDRESS,
                  prepared_markets=0, market_count=0, buy_count=0, position_count=0, deadline=final.deadline)
    canonical = lifecycle.LifecycleCanonicalProgress(**fields, mode=lifecycle.LifecycleMode.ATOMIC)
    progress = lifecycle.LaunchProgress(
        **fields, mode=case["mode"], block=block, head_block=block, awaiting_confirmations=False,
        canonical=canonical, head=canonical, confirmation_depth=1,
        confirmed_account_nonce=0, head_account_nonce=0, pending_account_nonce=0, confirmation_safe=True,
    )
    simulation = lifecycle.LaunchSimulation(
        "stateful", True, block, "eth_simulateV1", (), (), LaunchRpcSimulation("stateful", "eth_simulateV1", block, ()),
        execution_proof="proved", protocol_fit="proved",
    )
    deployment = get_launch_addresses(final.chain_id)
    planned = lifecycle.PlannedLaunch(
        plan=final, plan_hash=fields["plan_hash"], launch_id=fields["launch_id"], predicted_token=predicted,
        chain_id=final.chain_id, account=final.creator, mode=case["mode"], transactions=(), approvals=(),
        progress=progress, simulation=simulation, limits=LaunchExecutionLimits(), prepare_batch_size=1,
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
    assert example.run_launch_example(case_definition()) == 1
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
    limits = LaunchExecutionLimits(protocol="nitro", execution_gas_ceiling=32_000_000,
                                   max_tx_compute_gas=32_000_000, max_block_compute_gas=32_000_000, arb_os_version=50)
    assert example.validate_envelope(None, transaction, request, limits) is None
    with pytest.raises(ExampleFailure) as gas_failure:
        example.validate_envelope(None, transaction, request, LaunchExecutionLimits(account_transaction_gas_limit=34_000_000))
    assert gas_failure.value.code == "TRANSACTION_LIMIT_MISMATCH"
    with pytest.raises(ExampleFailure) as calldata_failure:
        example.validate_envelope(None, transaction, request, LaunchExecutionLimits(max_calldata_bytes=1))
    assert calldata_failure.value.code == "TRANSACTION_LIMIT_MISMATCH"
