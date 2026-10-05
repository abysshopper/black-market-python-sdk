"""Local launch-example wallet, diagnostic, interruption and publication boundaries.

These are local unit tests, not evidence that any API/chain launch was executed.
"""

from __future__ import annotations

import json
import signal
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from requests import HTTPError
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data

from examples import launch_examples as example
from examples import _launch_support as support
from examples._launch_support import Artifacts, JournalHTTPProvider, LocalSigningWallet, Redactor, ExampleFailure, error_details, loopback_url, mainnet_url
from black_market_sdk import (
    LaunchBlock, LaunchPlanV1, LaunchPublishPending, LifecycleTokenConfig,
    LaunchApiError, get_launch_addresses, predict_launch_token, to_launch_plan_tuple,
)
from black_market_sdk import lifecycle
from black_market_sdk.lifecycle_rpc import LaunchRpcError

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
    assert result["error"]["cause"]["revertData"] == "0xdeadbeef00000001"
    assert "LaunchRpcError" in result["error"]["stack"]
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


def test_token_only_prediction_does_not_weaken_execution_validation(monkeypatch):
    draft = token_draft()
    calls = []
    block = LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1)
    monkeypatch.setattr(lifecycle, "_call", lambda _client, _target, _abi, name, _args, _block: calls.append(name) or TOKEN)
    monkeypatch.setattr(lifecycle, "assert_canonical", lambda *_args: None)
    assert predict_launch_token(None, draft, block=block) == Web3.to_checksum_address(TOKEN)
    assert calls == ["predictToken"]
    with pytest.raises(ValueError, match="markets"):
        to_launch_plan_tuple(draft)
    with pytest.raises(ValueError, match="uint256"):
        predict_launch_token(None, replace(draft, nonce=True), block=block)
    assert calls == ["predictToken"]


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
        f"http://127.0.0.1:{server.server_port}", artifacts, node="execution",
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
        metadata=example.LaunchSessionMetadata(name="Smoke", symbol="SMOKE"),
        idempotency_key="smoke-wallet-regression", nonce="0x" + "12" * 32, deadline=2000000000)
    signature = wallet.sign_typed_data(typed, verifying_contract=orchestrator)
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=signature) == account.address
    typed["domain"]["chainId"] = 1
    with pytest.raises(ExampleFailure):
        wallet.sign_typed_data(typed, verifying_contract=orchestrator)
    artifacts.finish()


@pytest.mark.parametrize("method", ["eth_sendTransaction", "eth_signTypedData_v4", "personal_sign", "hardhat_setBalance"])
def test_mainnet_provider_requires_local_signing_and_preserves_source(tmp_path, monkeypatch, method):
    artifacts = Artifacts(tmp_path / "guard", "boundary-case")
    monkeypatch.setattr(Web3.HTTPProvider, "make_request", no_call)
    provider = JournalHTTPProvider("https://rpc.example", artifacts, node="execution")
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
    provider = JournalHTTPProvider("https://rpc.example", artifacts, node="execution")
    assert provider.make_request("eth_simulateV1", [{}]) == {"result": []}
    assert calls == [("eth_simulateV1", [{}])]
    artifacts.finish()


def test_controlled_fork_reimpersonates_only_simulation_creator_after_reset(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "controlled-unlock", "boundary-case")
    calls = []

    def request(_self, method, params):
        calls.append((method, params))
        return {"result": True}

    monkeypatch.setattr(Web3.HTTPProvider, "make_request", request)
    provider = JournalHTTPProvider("http://127.0.0.1:19864", artifacts, node="simulation",
                                   simulation_creator=CREATOR)
    provider.make_request("anvil_reset", [{"forking": {"jsonRpcUrl": "https://rpc.example", "blockNumber": 17}}])
    assert [method for method, _ in calls] == ["anvil_reset", "anvil_impersonateAccount"]
    assert calls[-1][1] == [CREATOR]
    artifacts.finish()



def test_unavailable_native_admission_retains_failure_without_sign_or_api(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "refused-native", "boundary-case")
    refused = SimpleNamespace(admitted=False, confidence="provisional",
                              simulation=SimpleNamespace(backend=None, reasons=("eth_simulateV1 unavailable",)),
                              market_admissions=(), limits=None, atomic_simulation=None,
                              token_factory=None, token_factory_code_hash=None)
    calls = []

    def refuse(_client, plan, **kwargs):
        calls.append((plan, kwargs))
        return refused

    monkeypatch.setattr(example, "plan_launch", refuse)
    for name in ("create_controlled_launch_fork", "stage_metadata", "build_next_transaction", "api_client"):
        monkeypatch.setattr(example, name, no_call)
    with pytest.raises(ExampleFailure) as failure:
        example.admit_plan(None, None, token_draft(), case_definition(), artifacts, example.Cancellation())
    artifacts.finish(failure.value)
    assert failure.value.code == "LAUNCH_NOT_ADMITTED"
    assert len(calls) == 1 and calls[0][1]["fork"] is None and calls[0][1]["mode"] == "atomic"
    assert artifacts.result["chain"]["transactions"] == []
    assert "eth_simulateV1 unavailable" in (artifacts.directory / "admission.json").read_text()


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
    monkeypatch.delenv("SIMULATION_RPC_URL", raising=False)
    artifacts = Artifacts(tmp_path / "config", "boundary-case")
    configuration, actual_key = example.example_configuration(artifacts)
    artifacts.finish()
    assert actual_key == key
    assert configuration["creator"] == Account.from_key(key).address
    assert configuration["rpcUrl"] == example.ROBINHOOD_MAINNET_RPC.rstrip("/")
    assert configuration["orchestrator"] == get_launch_addresses(4663).orchestrator
    assert configuration["quoteAsset"] == get_launch_addresses(4663).wrapped_native


def test_example_ceiling_is_bounded_by_observed_block(tmp_path, monkeypatch):
    for name in ("LAUNCH_CHAIN_GAS_CAP", "LAUNCH_RPC_GAS_CAP", "LAUNCH_ACCOUNT_GAS_CAP", "LAUNCH_CALLDATA_CAP"):
        monkeypatch.delenv(name, raising=False)
    block = LaunchBlock(1, BLOCK_HASH, 100, 12_000_000, 1)
    context = SimpleNamespace(block=block, chain_id=4663, account=CREATOR, orchestrator=TOKEN)
    limits = example.limit_resolver()(None, context)
    assert limits.gas_cap(block) == 12_000_000
    assert limits.max_calldata_bytes == 131072 and limits.headroom_bps == 1000
    assert "EXAMPLE ceilings" in limits.source
