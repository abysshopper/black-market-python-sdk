"""Invocation-owned pinned reads, transport identity and read-failure boundaries."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from black_market_sdk import LifecyclePlanningError
from black_market_sdk.lifecycle_rpc import (
    LaunchBlock, LaunchRpcError, pinned_rpc, read_chain_id, read_invocation, rpc,
)

CREATOR = "0x1000000000000000000000000000000000000001"
OTHER_CREATOR = "0x1000000000000000000000000000000000000002"
PIN = LaunchBlock(7, "0x" + "aa" * 32, 100, 30_000_000, 1)


class ReadRpc:
    def __init__(self):
        self.calls = []
        self.on_diagnostic = None
        self.failure = None

    def request(self, args):
        self.calls.append(deepcopy(args))
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        return "0x1234"


@read_invocation("author.discovery")
def repeated_reads(client, block):
    params = [{"to": CREATOR, "from": OTHER_CREATOR, "data": "0xabcd", "gas": "0x5208"}, block.tag]
    first = pinned_rpc(client, "eth_call", params, block)
    assert pinned_rpc(client, "eth_call", params, block) == first
    pinned_rpc(client, "eth_call", [{**params[0], "from": CREATOR}, block.tag], block)
    pinned_rpc(client, "eth_call", [{**params[0], "gas": "0x5209"}, block.tag], block)
    pinned_rpc(client, "eth_call", params, replace(block, block_hash="0x" + "bb" * 32))
    rpc(client, "eth_chainId", [])
    rpc(client, "eth_chainId", [])
    return client


def test_exact_pinned_wire_reads_reuse_only_within_one_invocation():
    client = ReadRpc()
    escaped = repeated_reads(client, PIN)
    assert len(client.calls) == 6
    with pytest.raises(LifecyclePlanningError) as failure:
        rpc(escaped, "eth_chainId", [])
    assert failure.value.code == "READ_SCOPE_CLOSED"
    repeated_reads(client, PIN)
    assert len(client.calls) == 12


@pytest.mark.parametrize("wrapped", [False, True])
def test_chain_identity_is_observed_once_per_client_across_invocations(wrapped):
    transport = ReadRpc()
    client = SimpleNamespace(request=transport.request) if wrapped else transport

    @read_invocation("plan")
    def observe(scoped):
        return read_chain_id(scoped)

    assert observe(client) == observe(client) == read_chain_id(client) == 0x1234
    assert transport.calls == [{"method": "eth_chainId", "params": []}]
    other = ReadRpc()
    assert observe(other) == 0x1234
    assert other.calls == [{"method": "eth_chainId", "params": []}]


def test_failed_session_chain_observation_can_recover():
    client = ReadRpc()
    client.failure = RuntimeError("chain observation unavailable")
    with pytest.raises(LaunchRpcError, match="eth_chainId failed"):
        read_chain_id(client)
    assert read_chain_id(client) == read_chain_id(client) == 0x1234
    assert client.calls == [{"method": "eth_chainId", "params": []}] * 2


@pytest.mark.parametrize("method,params", [
    ("eth_call", [{"to": CREATOR}, "latest"]),
    ("eth_call", [{"to": CREATOR}, "0x7", {}]),
    ("eth_sendTransaction", [{"from": CREATOR}, "0x7"]),
])
def test_cache_refuses_unpinned_or_mutating_or_override_requests(method, params):
    client = ReadRpc()
    with pytest.raises(ValueError):
        pinned_rpc(client, method, params, PIN)
    assert client.calls == []


def test_failed_pinned_read_is_evicted_and_can_recover():
    client = ReadRpc()
    client.failure = RuntimeError({"code": -32000, "message": "private provider data"})

    @read_invocation("author.discovery")
    def recover(scoped):
        params = [{"to": CREATOR}, PIN.tag]
        with pytest.raises(LaunchRpcError):
            pinned_rpc(scoped, "eth_call", params, PIN)
        assert pinned_rpc(scoped, "eth_call", params, PIN) == "0x1234"
        assert pinned_rpc(scoped, "eth_call", params, PIN) == "0x1234"

    recover(client)
    assert len(client.calls) == 2


def test_diagnostics_redact_wire_fields_and_observer_failure_cannot_change_reads():
    client = ReadRpc()
    events = []
    secret = "credential-private-value"

    def observe(event):
        events.append(event)
        raise RuntimeError(secret)

    client.on_diagnostic = observe

    @read_invocation("author.discovery")
    def operation(scoped):
        return rpc(scoped, secret, [{"from": CREATOR, "data": secret}])

    assert operation(client) == "0x1234"
    assert [event.phase for event in events] == ["start", "queued", "start", "success", "success"]
    assert [event.method for event in events if event.stage == "rpc"] == ["other"] * 3
    for event in events:
        assert set(vars(event)) == {"stage", "phase", "duration_ms", "queue_ms", "request_id", "method"}
        assert secret not in repr(event)
        assert CREATOR not in repr(event)


class ScriptedProvider:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def make_request(self, method, params):
        self.calls += 1
        return self.responses.pop(0)


def test_throttled_rpc_is_retried_until_it_answers(monkeypatch):
    monkeypatch.setattr("black_market_sdk.lifecycle_rpc.time.sleep", lambda _seconds: None)
    throttled = {"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "Too Many Requests"}}
    provider = ScriptedProvider([throttled, throttled, {"jsonrpc": "2.0", "id": 1, "result": "0x1"}])
    assert rpc(SimpleNamespace(provider=provider), "eth_blockNumber", []) == "0x1"
    assert provider.calls == 3


def test_execution_errors_and_exhausted_throttling_are_not_retried_forever(monkeypatch):
    monkeypatch.setattr("black_market_sdk.lifecycle_rpc.time.sleep", lambda _seconds: None)
    reverted = ScriptedProvider([{"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}}])
    with pytest.raises(LaunchRpcError):
        rpc(SimpleNamespace(provider=reverted), "eth_call", [])
    assert reverted.calls == 1
    throttled = ScriptedProvider([{"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "Too Many Requests"}}] * 4)
    with pytest.raises(LaunchRpcError):
        rpc(SimpleNamespace(provider=throttled), "eth_blockNumber", [])
    assert throttled.calls == 4


def test_value_equal_clients_do_not_share_a_chain_identity():
    class EqualRpc(ReadRpc):
        def __init__(self, chain):
            super().__init__()
            self.chain = chain
        def __eq__(self, other):
            return isinstance(other, EqualRpc)
        def __hash__(self):
            return 1
        def request(self, args):
            self.calls.append(deepcopy(args))
            return hex(self.chain)

    first, second = EqualRpc(4663), EqualRpc(31337)
    assert first == second
    assert read_chain_id(first) == read_chain_id(first) == 4663
    assert read_chain_id(second) == read_chain_id(second) == 31337
    assert len(first.calls) == len(second.calls) == 1
