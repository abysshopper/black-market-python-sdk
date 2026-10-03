"""Fail-closed controlled-fork admission boundaries; real execution uses Anvil."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from black_market_sdk import lifecycle_rpc
from black_market_sdk.lifecycle_rpc import (
    ControlledLaunchFork,
    LaunchExecutionLimits,
    LaunchStateChanged,
    read_block,
    simulate_transactions,
)


CREATOR = "0x1000000000000000000000000000000000000001"
OTHER_CREATOR = "0x1000000000000000000000000000000000000002"
SOURCE_URL = "http://127.0.0.1:19571"
REFRESH_URL = "http://127.0.0.1:19572"
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
        # A later creator must be checked even when the first creator matches.
        state["nonces"][OTHER_CREATOR] += 1
    return state


class ForkStateRpc:
    def __init__(self, endpoint, state, *, reset_state=None, drift_after_unlock=None):
        self.endpoint_uri = endpoint
        self.state = deepcopy(state)
        self.reset_state = reset_state
        self.drift_after_unlock = drift_after_unlock
        self.execution_operations = []
        self.resets = 0

    def make_request(self, method, params):
        if method == "anvil_nodeInfo":
            result = {}
        elif method == "eth_chainId":
            result = hex(self.state["chain"])
        elif method == "eth_getBlockByNumber":
            result = self.state["header"]
        elif method == "eth_getTransactionCount":
            result = hex(self.state["nonces"][params[0]])
        elif method == "anvil_reset":
            self.resets += 1
            self.state = deepcopy(self.reset_state)
            result = None
        elif method == "eth_accounts":
            result = list(PINNED["nonces"])
            if self.drift_after_unlock is not None:
                self.state = deepcopy(self.drift_after_unlock)
        elif method in {"evm_snapshot", "eth_estimateGas", "eth_sendTransaction"}:
            self.execution_operations.append(method)
            raise AssertionError("mismatched fork reached admission execution")
        else:
            raise AssertionError(f"unexpected RPC call: {method}")
        return {"result": result}


def scenario(monkeypatch, *, refresh_state=PINNED, fork_state=PINNED, reset_state=PINNED, drift_after_unlock=None):
    source = SimpleNamespace(provider=ForkStateRpc(SOURCE_URL, PINNED))
    refresh = ForkStateRpc(REFRESH_URL, refresh_state)
    fork_provider = ForkStateRpc(FORK_URL, fork_state, reset_state=reset_state, drift_after_unlock=drift_after_unlock)
    client = SimpleNamespace(provider=fork_provider)

    def web3(provider):
        return SimpleNamespace(provider=provider)

    web3.HTTPProvider = lambda endpoint: refresh if endpoint == REFRESH_URL else source.provider
    monkeypatch.setattr(lifecycle_rpc, "Web3", web3)
    fork = ControlledLaunchFork(client=client, isolated=True, source_rpc_url=REFRESH_URL)
    transactions = [{"from": creator, "to": CREATOR, "gas": 21000} for creator in PINNED["nonces"]]
    return source, fork, read_block(source), transactions


@pytest.mark.parametrize("validation", [False, True])
@pytest.mark.parametrize("field", ["chain ID", "block number", "block hash", "creator nonce"])
def test_refresh_endpoint_must_match_pinned_planning_state_even_if_fork_is_current(monkeypatch, field, validation):
    source, fork, block, transactions = scenario(monkeypatch, refresh_state=mismatched(field))
    with pytest.raises(LaunchStateChanged, match=f"controlled fork source state mismatch: {field}"):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(), fork=fork, validation=validation)
    assert fork.client.provider.resets == 0
    assert fork.client.provider.execution_operations == []


@pytest.mark.parametrize("validation", [False, True])
@pytest.mark.parametrize("field", ["chain ID", "block number", "block hash", "creator nonce"])
def test_reset_result_must_match_every_pinned_state_dimension(monkeypatch, field, validation):
    source, fork, block, transactions = scenario(monkeypatch, fork_state=mismatched("block hash"), reset_state=mismatched(field))
    with pytest.raises(LaunchStateChanged, match=f"controlled fork state mismatch after reset: {field}"):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(), fork=fork, validation=validation)
    assert fork.client.provider.resets == 1
    assert fork.client.provider.execution_operations == []


@pytest.mark.parametrize("validation", [False, True])
@pytest.mark.parametrize("field", ["chain ID", "block number", "block hash", "creator nonce"])
def test_state_drift_before_snapshot_cannot_be_stamped_as_pinned_admission(monkeypatch, field, validation):
    source, fork, block, transactions = scenario(monkeypatch, fork_state=mismatched("block hash"), drift_after_unlock=mismatched(field))
    with pytest.raises(LaunchStateChanged, match=f"controlled fork state mismatch before simulation: {field}"):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(), fork=fork, validation=validation)
    assert fork.client.provider.execution_operations == []


def test_stale_fork_without_refresh_remains_refused(monkeypatch):
    source, fork, block, transactions = scenario(monkeypatch, fork_state=mismatched("creator nonce"))
    fork = ControlledLaunchFork(client=fork.client, isolated=True)
    with pytest.raises(ValueError, match="stale and has no source refresh"):
        simulate_transactions(source, transactions, block=block, limits=LaunchExecutionLimits(), fork=fork)
    assert fork.client.provider.execution_operations == []
