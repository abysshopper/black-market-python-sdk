"""Invocation-owned reads and explicit disposable-fork safety boundaries."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from black_market_sdk.lifecycle import LifecyclePlanningError
from black_market_sdk.lifecycle_rpc import (
    ControlledLaunchFork,
    LaunchExecutionLimits,
    LaunchRpcError,
    LaunchStateChanged,
    create_controlled_launch_fork,
    pinned_rpc,
    read_block,
    read_invocation,
    rpc,
    simulate_transactions,
)

CREATOR = "0x1000000000000000000000000000000000000001"
OTHER_CREATOR = "0x1000000000000000000000000000000000000002"
SOURCE_URL = "http://127.0.0.1:19571"
FORK_URL = "http://127.0.0.1:19573"
PINNED = {
    "chain": 31337,
    "header": {
        "number": "0x7", "hash": "0x" + "aa" * 32,
        "timestamp": "0x100", "gasLimit": "0x1c9c380", "baseFeePerGas": "0x1",
    },
    "nonces": {CREATOR: 2, OTHER_CREATOR: 3},
}


def mismatched(field):
    state = deepcopy(PINNED)
    if field == "chain ID":
        state["chain"] += 1
    elif field == "block number":
        state["header"]["number"] = "0x8"
    elif field == "block hash":
        state["header"]["hash"] = "0x" + "bb" * 32
    elif field == "creator nonce":
        state["nonces"][OTHER_CREATOR] += 1
    return state


class ForkStateRpc:
    def __init__(self, endpoint, state, *, reset_state=PINNED, cleanup=True):
        self.endpoint_uri = endpoint
        self.state = deepcopy(state)
        self.reset_state = reset_state
        self.cleanup = cleanup
        self.calls = []
        self.resets = 0

    def make_request(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == "eth_chainId":
            result = hex(self.state["chain"])
        elif method == "eth_getBlockByNumber":
            result = self.state["header"]
        elif method == "eth_getTransactionCount":
            result = hex(self.state["nonces"][params[0]])
        elif method in {"anvil_reset", "hardhat_reset"}:
            self.resets += 1
            self.state = deepcopy(self.reset_state)
            result = None
        elif method == "evm_snapshot":
            result = "0x1"
        elif method == "evm_revert":
            result = self.cleanup
        elif method == "eth_estimateGas":
            result = "0x5208"
        elif method == "eth_sendTransaction":
            result = "0x" + "cc" * 32
        elif method == "eth_getTransactionReceipt":
            result = {"status": "0x1", "gasUsed": "0x5208", "logs": []}
        elif method.endswith("_impersonateAccount") or method.endswith("_stopImpersonatingAccount"):
            result = True
        else:
            raise AssertionError(f"unexpected RPC call: {method}")
        return {"result": result}

    @property
    def execution_operations(self):
        return [method for method, _ in self.calls if method in {
            "evm_snapshot", "eth_estimateGas", "eth_sendTransaction",
        }]


def scenario(*, fork_state=PINNED, reset_state=PINNED, cleanup=True, **fork_options):
    source = SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED))
    provider = ForkStateRpc(FORK_URL, fork_state, reset_state=reset_state, cleanup=cleanup)
    fork = ControlledLaunchFork(
        client=SimpleNamespace(provider=provider), isolated=True,
        source_rpc_url=SOURCE_URL, **fork_options,
    )
    transactions = [{"from": creator, "to": CREATOR, "gas": 30000} for creator in PINNED["nonces"]]
    return source, fork, read_block(source), transactions


@pytest.mark.parametrize("validation", [False, True])
@pytest.mark.parametrize("field", ["chain ID", "block number", "block hash", "creator nonce"])
def test_reset_result_must_match_every_pinned_state_dimension(field, validation):
    source, fork, block, transactions = scenario(
        fork_state=mismatched("block hash"), reset_state=mismatched(field),
    )
    with pytest.raises(LaunchStateChanged):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
            fork=fork, validation=validation, force_owned_fork=True)
    assert fork.client.provider.resets == 1
    assert fork.client.provider.execution_operations == []
    reset = next(params for method, params in fork.client.provider.calls if method == "anvil_reset")
    assert reset == [{"forking": {"jsonRpcUrl": SOURCE_URL, "blockNumber": block.number}}]


@pytest.mark.parametrize("validation", [False, True])
def test_later_creator_nonce_mismatch_is_refused_before_snapshot(validation):
    source, fork, block, transactions = scenario(fork_state=mismatched("creator nonce"))
    with pytest.raises(LaunchStateChanged):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
            fork=fork, validation=validation, force_owned_fork=True)
    assert fork.client.provider.resets == 0
    assert fork.client.provider.execution_operations == []


@pytest.mark.parametrize("options", [
    {"expected_chain_id": 1}, {"expected_block_hash": "0x" + "bb" * 32},
])
def test_configured_fork_identity_is_enforced_before_snapshot(options):
    source, fork, block, transactions = scenario(**options)
    with pytest.raises(LaunchStateChanged):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
            fork=fork, force_owned_fork=True)
    assert fork.client.provider.execution_operations == []


@pytest.mark.parametrize("fork_url,isolated", [
    ("https://remote.example/rpc", True),
    ("http://localhost:19571", True),
    ("http://[::1]:19571", True),
    (FORK_URL, False),
])
def test_fork_requires_distinct_explicit_disposable_loopback(fork_url, isolated):
    with pytest.raises(ValueError):
        ControlledLaunchFork(client=SimpleNamespace(), isolated=isolated,
            source_rpc_url=SOURCE_URL, fork_rpc_url=fork_url)


def test_fork_refresh_source_is_required():
    with pytest.raises(ValueError):
        ControlledLaunchFork(client=SimpleNamespace(), isolated=True,
            source_rpc_url=None, fork_rpc_url=FORK_URL)


def test_source_client_cannot_be_reused_as_execution_fork():
    source = SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED))
    with pytest.raises(ValueError):
        create_controlled_launch_fork(source, source, isolated=True, fork_rpc_url=FORK_URL)
    assert source.provider.execution_operations == []


@pytest.mark.parametrize("reset_method,impersonation", [
    ("anvil_reset", "anvil"), ("hardhat_reset", "hardhat"),
])
def test_explicit_fork_backend_resets_executes_and_cleans_up(reset_method, impersonation):
    source, fork, block, transactions = scenario(fork_state=mismatched("block hash"),
        reset_method=reset_method, impersonation=impersonation)
    outcome = simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
        fork=fork, force_owned_fork=True)
    assert outcome.successful is True
    assert outcome.backend == "controlled-fork"
    methods = [method for method, _ in fork.client.provider.calls]
    assert reset_method in methods
    assert methods.count(f"{impersonation}_impersonateAccount") == 2
    assert methods.count(f"{impersonation}_stopImpersonatingAccount") == 2
    assert methods[-1] == "evm_revert"
    assert source.provider.execution_operations == []


def test_snapshot_cleanup_failure_cannot_be_admitted():
    source, fork, block, transactions = scenario(cleanup=False)
    with pytest.raises(RuntimeError):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
            fork=fork, force_owned_fork=True)
    assert fork.client.provider.calls[-1] == ("evm_revert", ["0x1"])
    assert source.provider.execution_operations == []


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
    block = read_block(SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED)))
    escaped = repeated_reads(client, block)
    assert len(client.calls) == 6
    with pytest.raises(LifecyclePlanningError) as failure:
        rpc(escaped, "eth_chainId", [])
    assert failure.value.code == "READ_SCOPE_CLOSED"
    repeated_reads(client, block)
    assert len(client.calls) == 12


@pytest.mark.parametrize("method,params", [
    ("eth_call", [{"to": CREATOR}, "latest"]),
    ("eth_call", [{"to": CREATOR}, "0x7", {}]),
    ("eth_sendTransaction", [{"from": CREATOR}, "0x7"]),
])
def test_cache_refuses_unpinned_or_mutating_or_override_requests(method, params):
    client = ReadRpc()
    block = read_block(SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED)))
    with pytest.raises(ValueError):
        pinned_rpc(client, method, params, block)
    assert client.calls == []


def test_failed_pinned_read_is_evicted_and_can_recover():
    client = ReadRpc()
    client.failure = RuntimeError({"code": -32000, "message": "private provider data"})
    block = read_block(SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED)))

    @read_invocation("author.discovery")
    def recover(scoped):
        params = [{"to": CREATOR}, block.tag]
        with pytest.raises(LaunchRpcError):
            pinned_rpc(scoped, "eth_call", params, block)
        assert pinned_rpc(scoped, "eth_call", params, block) == "0x1234"
        assert pinned_rpc(scoped, "eth_call", params, block) == "0x1234"

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


@pytest.mark.parametrize("code,message,category", [
    (-32000, "credential-private-value", "source"),
    (3, "execution reverted credential-private-value", "opaque"),
])
def test_rpc_replay_refusal_never_selects_a_different_execution_backend(code, message, category):
    source, fork, block, transactions = scenario()
    original = source.provider.make_request

    def fail_simulation(method, params):
        if method == "eth_simulateV1":
            return {"error": {"code": code, "message": message, "data": "opaque-secret"}}
        return original(method, params)

    source.provider.make_request = fail_simulation
    outcome = simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
        fork=fork, validation=True)
    assert outcome.successful is False
    assert outcome.confidence == "provisional"
    assert outcome.failure_category == category
    assert outcome.native_error_code == code
    assert fork.client.provider.calls == []
    assert "credential-private-value" not in repr(outcome)
    assert "opaque-secret" not in repr(outcome)


def test_initial_measurement_can_use_explicit_fork_after_rpc_request_refusal():
    source, fork, block, transactions = scenario()
    original = source.provider.make_request

    def fail_simulation(method, params):
        if method == "eth_simulateV1":
            return {"error": {"code": -32000, "message": "opaque request refusal"}}
        return original(method, params)

    source.provider.make_request = fail_simulation
    outcome = simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
        fork=fork, validation=False)
    assert outcome.successful is True
    assert outcome.backend == "controlled-fork"
    assert fork.client.provider.calls[-1] == ("evm_revert", ["0x1"])
    assert source.provider.execution_operations == []


@pytest.mark.parametrize("validation", [False, True])
def test_returned_execution_failure_never_falls_back_to_fork(validation):
    source, fork, block, transactions = scenario()
    original = source.provider.make_request

    def failed_execution(method, params):
        if method == "eth_simulateV1":
            return {"result": [{"calls": [{
                "status": "0x0", "gasUsed": "0x5208", "returnData": "0x",
                "error": {"code": -32000, "message": "private-provider-text"},
            }]} for _ in transactions]}
        return original(method, params)

    source.provider.make_request = failed_execution
    outcome = simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(),
        fork=fork, validation=validation)
    assert outcome.successful is False
    assert outcome.backend == "eth_simulateV1"
    assert outcome.confidence == "stateful"
    assert outcome.calls[0].failure_category == "opaque"
    assert fork.client.provider.calls == []
    assert "private-provider-text" not in repr(outcome)
