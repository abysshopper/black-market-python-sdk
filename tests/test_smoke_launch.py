"""Diagnostic, write-consent, interruption and publication phase boundaries.

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

from examples import smoke_launch as smoke
from _smoke_support import Artifacts, JournalHTTPProvider, Redactor, SmokeFailure, error_details, loopback_url
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


def fixture_document():
    deployment = get_launch_addresses(4663)
    return {"schema": "black-market.launch-smoke-fixture.v1", "fixtureOnly": True, "chainId": "4663",
            "rpcUrl": "http://127.0.0.1:19863", "forkRpcUrl": "http://127.0.0.1:19864",
            "orchestrator": deployment.orchestrator, "creator": CREATOR, "quoteAsset": deployment.wrapped_native,
            "quoteDecimals": 18, "oracleConfigId": "0x" + "12" * 32,
            "executionLimits": {"chainTransactionGasLimit": "32000000", "rpcTransactionGasLimit": "32000000",
                                "accountTransactionGasLimit": "32000000", "maxCalldataBytes": 131072,
                                "headroomBps": 1000, "provenance": {"scope": "controlled-local-measurement"}},
            "cases": [case_definition()]}


def fixture_path(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture_document()))
    return path


def token_draft():
    return LaunchPlanV1(
        chain_id=4663, orchestrator=get_launch_addresses(4663).orchestrator, creator=CREATOR, nonce=123,
        token=LifecycleTokenConfig(0, 0, "Smoke boundary-case", "SMOKE", 1000000 * 10**18, 0, "", bytes(32), CREATOR, False),
        funding=(), fee_assets=(), markets=(), buys=(), deadline=3600, executor_fee_bps=275,
    )


def no_call(*_args, **_kwargs):
    raise AssertionError("unexpected RPC, signing, simulation or API operation")


def test_list_uses_fixture_without_constructing_rpc_or_api(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "JournalHTTPProvider", no_call)
    monkeypatch.setattr(smoke, "api_client", no_call)
    assert smoke.main(["--fixture", str(fixture_path(tmp_path)), "--list"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["signed"] is False and output["rpcContacted"] is False
    assert output["cases"][0]["id"] == "boundary-case"


def test_plan_only_never_enters_simulation_signing_or_api(tmp_path, monkeypatch):
    fixture = fixture_path(tmp_path)
    artifacts = Artifacts(tmp_path / "plan", "boundary-case")

    def provider(url, _artifacts, *, node, execute):
        return object()

    class ReadOnlyClient:
        to_checksum_address = staticmethod(Web3.to_checksum_address)
        is_address = staticmethod(Web3.is_address)

        def __init__(self, _provider):
            self.eth = SimpleNamespace(get_code=lambda _address: b"\x01")

    def read_rpc(_client, method, _params):
        return {"anvil_nodeInfo": {}, "eth_chainId": hex(4663), "eth_accounts": [CREATOR]}[method]

    monkeypatch.setattr(smoke, "Web3", ReadOnlyClient)
    monkeypatch.setattr(smoke, "JournalHTTPProvider", provider)
    monkeypatch.setattr(smoke, "rpc", read_rpc)
    monkeypatch.setattr(smoke, "read_block", lambda _client: LaunchBlock(1, BLOCK_HASH, 100, 32000000, 1))
    monkeypatch.setattr(smoke, "contract", lambda *_args: SimpleNamespace(functions=SimpleNamespace(decimals=lambda: SimpleNamespace(call=lambda: 18))))
    monkeypatch.setattr(smoke, "construct_plan", lambda *_args: (token_draft(), TOKEN))
    for name in ("plan_launch", "build_next_transaction", "create_controlled_launch_fork", "stage_metadata", "api_client"):
        monkeypatch.setattr(smoke, name, no_call)
    args = SimpleNamespace(fixture=fixture, case="boundary-case", api_url=None, execute=False,
                           chain_only=False, publish_timeout_seconds=90)
    message = smoke.run(args, artifacts, smoke.Cancellation())
    artifacts.finish()
    assert message == "Plan prepared; launch not executed"
    result = json.loads((artifacts.directory / "result.json").read_text())
    assert result["scope"] == "plan-only" and result["execution"] == "not-run"
    assert result["chain"]["status"] == "not-run" and result["api"]["status"] == "not-run"


@pytest.mark.parametrize("method", ["eth_sendTransaction", "eth_signTypedData_v4", "evm_snapshot", "anvil_reset"])
def test_provider_refuses_writes_without_consent_before_transport(tmp_path, monkeypatch, method):
    artifacts = Artifacts(tmp_path / "run", "boundary-case")
    monkeypatch.setattr(Web3.HTTPProvider, "make_request", no_call)
    provider = JournalHTTPProvider("http://127.0.0.1:19863", artifacts, node="execution", execute=False)
    with pytest.raises(SmokeFailure) as failure:
        provider.make_request(method, [])
    artifacts.finish(failure.value)
    assert failure.value.code == "EXECUTION_NOT_AUTHORIZED"


def test_source_snapshot_or_reset_is_forbidden_even_with_execute(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "run", "boundary-case")
    monkeypatch.setattr(Web3.HTTPProvider, "make_request", no_call)
    provider = JournalHTTPProvider("http://127.0.0.1:19863", artifacts, node="execution", execute=True)
    with pytest.raises(SmokeFailure) as failure:
        provider.make_request("evm_revert", ["0x1"])
    artifacts.finish(failure.value)
    assert failure.value.code == "SOURCE_STATE_MUTATION_FORBIDDEN"


@pytest.mark.parametrize("url", [
    "https://api.abyss.trading", "http://127.0.0.1.example:1234", "http://user:secret@127.0.0.1:1234",
    "http://127.0.0.1:1234?api_key=secret", "http://127.0.0.1:1234#fragment", "http://127.0.0.1",
])
def test_loopback_guard_rejects_live_credentialed_or_ambiguous_endpoints(url):
    with pytest.raises(ValueError):
        loopback_url(url)


def test_simulation_endpoint_alias_cannot_resolve_to_execution_endpoint():
    fixture = fixture_document()
    fixture["forkRpcUrl"] = "http://localhost:19863/"
    with pytest.raises(SmokeFailure) as failure:
        smoke.validate_fixture(fixture)
    assert failure.value.code == "INVALID_CONFIGURATION"


def test_early_configuration_error_leaves_final_artifacts(tmp_path, monkeypatch):
    output = tmp_path / "early"
    monkeypatch.setattr(smoke, "JournalHTTPProvider", no_call)
    assert smoke.main(["--fixture", str(tmp_path / "missing.json"), "--case", "boundary-case", "--output", str(output)]) == 1
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "failed" and result["stage"] == "configuration"
    assert result["error"]["name"] == "FileNotFoundError"
    assert result["error"]["stack"] and (output / "events.jsonl").read_text()


def test_existing_output_is_never_replaced(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "result.json"
    sentinel.write_text("keep original")
    assert smoke.main(["--fixture", "missing.json", "--case", "boundary-case", "--output", str(output)]) == 1
    assert sentinel.read_text() == "keep original"


def test_interrupt_preserves_submitted_hash_and_meaningful_result(tmp_path, monkeypatch):
    def interrupted(_args, artifacts, cancellation):
        artifacts.result.update(scope="chain-only", execution="executed")
        artifacts.result["chain"]["status"] = "executing"
        artifacts.enter("receipt-atomic")
        artifacts.submitted("atomic", {"from": CREATOR, "data": "0x", "gas": 100000}, TX_HASH)
        cancellation.signal(signal.SIGINT, None)
        cancellation.check()

    monkeypatch.setattr(smoke, "run", interrupted)
    output = tmp_path / "interrupt"
    assert smoke.main(["--fixture", "unused.json", "--case", "boundary-case", "--execute", "--chain-only", "--output", str(output)]) == 1
    receipts = json.loads((output / "receipts.json").read_text())
    result = json.loads((output / "result.json").read_text())
    assert receipts[0]["transactionHash"] == TX_HASH and receipts[0]["receipt"] is None
    assert result["status"] == "failed" and result["stage"] == "receipt-atomic"
    assert result["error"]["code"] == "INTERRUPTED" and result["chain"]["transactions"] == [TX_HASH]


def test_reverted_receipt_is_saved_before_status_failure(tmp_path, monkeypatch):
    artifacts = Artifacts(tmp_path / "reverted", "boundary-case")
    row = artifacts.submitted("atomic", {"from": CREATOR, "data": "0x", "gas": 100000}, TX_HASH)
    receipt = {"transactionHash": TX_HASH, "blockHash": BLOCK_HASH, "blockNumber": "0x10", "status": "0x0", "gasUsed": "0x100"}
    trace = {"error": "execution reverted", "output": "0xdeadbeef00000001"}
    monkeypatch.setattr(smoke, "rpc", lambda _client, method, _params: receipt if method == "eth_getTransactionReceipt" else trace)
    with pytest.raises(SmokeFailure) as failure:
        smoke.wait_receipt(None, row, artifacts, smoke.Cancellation())
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
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke, "api_client", lambda *_args, **_kwargs: api)
    cancellation = SimpleNamespace(check=lambda: None, sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    recovery = {"session": {"sessionId": "session", "capability": "private-capability"}}
    with pytest.raises(SmokeFailure) as failure:
        smoke.publish_activation(api, recovery, token_draft(), TOKEN, {"transactionHash": TX_HASH}, 1, artifacts, cancellation)
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
    monkeypatch.setattr(smoke, "api_client", lambda *_args, **_kwargs: api)
    monkeypatch.setattr(smoke, "execute_plan", no_call)
    cancellation = SimpleNamespace(check=lambda: None, sleep=no_call)
    with pytest.raises(LaunchApiError) as failure:
        smoke.publish_activation(api, {"session": {"sessionId": "session", "capability": "private-capability"}}, token_draft(), TOKEN,
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
    smoke.verify_publication(session, detail, plan, TOKEN, receipt, "session")
    wrong = {**detail, "item": {**item, "transactionHash": "0x" + "ef" * 32}}
    with pytest.raises(SmokeFailure) as failure:
        smoke.verify_publication(session, wrong, plan, TOKEN, receipt, "session")
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
        policies = smoke.fee_policies(token, quote, definition)
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
        f"http://127.0.0.1:{server.server_port}", artifacts, node="execution", execute=False,
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
