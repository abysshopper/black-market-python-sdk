"""Wire-level gas sizing, bounded proof coverage and automatic preparation packing."""

from dataclasses import replace

import pytest
from eth_utils import keccak

from black_market_sdk import (
    KnownLifecycleProfile, LaunchExecutionLimits, LifecyclePhase,
    build_launch_transactions, build_next_transaction,
    encode_lifecycle_pool_bound_v4_market_config, get_known_lifecycle_profile,
)
from black_market_sdk.lifecycle import _groups, _protect_ordered_buy_minimums
from test_hmm import CORE, hmm_config
from test_lifecycle import plan, topology_plan, wire_planning


def hmm_plan(count, *, dynamic=False):
    original = plan()
    markets = []
    for index in range(count):
        key = KnownLifecycleProfile.V4_HMM_DYNAMIC_POOL if dynamic else KnownLifecycleProfile.V4_HMM_FIXED_POOL
        preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE, key=key)
        config = hmm_config(preset.profile.id)
        budget = sum(position.max_token_amount for position in config.positions)
        markets.append(replace(original.markets[0], profile_id=preset.profile.id,
            adapter_id=preset.profile.registration.adapter_id, quote_asset=f"0x{512 + index:040x}",
            config_version=6, token_budget=budget,
            config=encode_lifecycle_pool_bound_v4_market_config(config)))
    return replace(original, chain_id=4663, orchestrator=CORE,
        token=replace(original.token, supply=sum(market.token_budget for market in markets)),
        markets=tuple(markets), funding=(), buys=())


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("mode", ["atomic", "activate"])
def test_hmm_reserve_is_added_once_per_atomic_or_activation(monkeypatch, dynamic, count, mode):
    request = hmm_plan(count, dynamic=dynamic)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"compute_gas": 6_000_000 * count,
            "latest_phase": 2 if mode == "activate" else 0,
            "confirmed_phase": 2 if mode == "activate" else 0})
    planned = lifecycle.plan_launch(client, request, account=request.creator,
        mode="staged" if mode == "activate" else "atomic",
        limits=LaunchExecutionLimits(headroom_bps=0, rpc_total_simulation_gas_limit=200_000_000))
    assert planned.admitted
    transaction = planned.transactions[0]
    reserve = 4_250_000 + 500_000 * (count - 1)
    assert transaction.gas_limit == provider.compute_gas + reserve
    assert [payload["validation"] for payload in provider.simulations] == [False, True]


def test_hmm_reserve_is_not_added_to_begin_or_prepare(monkeypatch):
    request = hmm_plan(1)
    lifecycle, _, _, client = wire_planning(monkeypatch, request=request,
        scenario_options={"compute_gas_by_kind": {"begin": 8_000_000, "prepare": 14_000_000, "activate": 6_000_000}})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.admitted and planned.simulation.deferred_transaction_ids == ()
    assert [transaction.gas_limit for transaction in planned.transactions] == [9_200_000, 16_100_000, 10_250_000]


@pytest.mark.parametrize("mode", ["atomic", "staged"])
def test_non_hmm_plan_keeps_percentage_only_padding(monkeypatch, mode):
    request = replace(topology_plan(4, False), chain_id=4663)
    lifecycle, _, _, client = wire_planning(monkeypatch, request=request)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode=mode)
    assert planned.admitted
    assert all(transaction.gas_limit == 115_000 for transaction in planned.transactions)


@pytest.mark.parametrize("gross", [27_360_000, 31_000_000])
def test_one_pool_hmm_atomic_uses_reserve_and_clamps_padding(monkeypatch, gross):
    request = hmm_plan(1)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"compute_gas": gross})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert planned.admitted and planned.mode == "atomic"
    expected = min(max(gross + 4_250_000, (gross * 11500 + 9999) // 10000), 32_000_000)
    assert planned.transactions[0].gas_limit == expected
    replay = provider.simulations[-1]["blockStateCalls"][0]["calls"][0]
    assert int(replay["gas"], 16) == expected


def test_true_atomic_compute_overflow_still_refuses(monkeypatch):
    request = hmm_plan(1)
    lifecycle, _, _, client = wire_planning(monkeypatch, request=request,
        scenario_options={"compute_gas": 32_000_001})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert not planned.admitted and planned.simulation.failure_category == "capacity"
    assert planned.simulation.failed_transaction_id == "atomic"


@pytest.mark.parametrize("count,batches", [(1, [1]), (2, [2]), (3, [2, 1]), (5, [2, 2, 1])])
def test_staged_preparation_is_greedily_packed_before_measurement(monkeypatch, count, batches):
    request = hmm_plan(count)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"prepare_gas_per_market": 14_000_000})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.admitted
    prepares = [transaction for transaction in planned.transactions if transaction.kind == "prepare"]
    assert [transaction.market_count for transaction in prepares] == batches
    assert [transaction.first_market for transaction in prepares] == [sum(batches[:index]) for index in range(len(batches))]
    first_measurement = provider.simulations[0]["blockStateCalls"]
    measured_batches = [provider.command(block["calls"][0])[1][2] for block in first_measurement
                        if provider.command(block["calls"][0])[0] == "prepare"]
    assert measured_batches == batches[:len(measured_batches)]


def test_mixed_profile_packing_preserves_contiguous_order_and_offline_explicit_batches():
    request = hmm_plan(5)
    request = replace(request, markets=tuple(replace(market, profile_id=bytes(32)) if index in (1, 3) else market
                                            for index, market in enumerate(request.markets)))
    assert _groups(request, 0, 32_000_000) == [(0, 2), (2, 2), (4, 1)]
    assert _groups(request, 0, 30_000_000) == [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)]
    transactions = build_launch_transactions(hmm_plan(5), mode="staged", preparation_batches=[1, 3, 1])
    assert [transaction.market_count for transaction in transactions if transaction.kind == "prepare"] == [1, 3, 1]


@pytest.mark.parametrize("error", ["DeploymentFailed", "InvalidConfiguration", None])
def test_underestimated_packed_prepare_splits_at_near_ceiling_for_any_error(monkeypatch, error):
    request = hmm_plan(2)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"prepare_gas_per_market": 18_000_000})
    original = provider.make_request
    def respond(method, params):
        response = original(method, params)
        if method == "eth_simulateV1" and not params[0]["validation"] and error is not None:
            for block, result in zip(params[0]["blockStateCalls"], response["result"]):
                kind, args = provider.command(block["calls"][0])
                if kind == "prepare" and args[2] > 1:
                    result["calls"][0].update(returnData="0x" + keccak(text=error + "()")[:4].hex(),
                        error={"code": 3, "message": "execution reverted"})
        return response
    monkeypatch.setattr(provider, "make_request", respond)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.admitted
    assert [transaction.market_count for transaction in planned.transactions if transaction.kind == "prepare"] == [1, 1]
    assert [payload["validation"] for payload in provider.simulations] == [False, False, True]


@pytest.mark.parametrize("cumulative_cap", [False, True])
def test_measurement_distinguishes_transport_exhaustion_from_semantic_failure(monkeypatch, cumulative_cap):
    request = hmm_plan(1)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"compute_gas_by_kind": {"begin": 12_000_000 if cumulative_cap else 8_000_000,
            "prepare": 20_000_000 if cumulative_cap else 14_000_000, "activate": 3_000_000}})
    original = provider.make_request
    def respond(method, params):
        response = original(method, params)
        if method == "eth_simulateV1" and not params[0]["validation"]:
            result = response["result"][-1]["calls"][0]
            result.update(status="0x0", returnData="0x" + keccak(text="SlippageExceeded()")[:4].hex(),
                          error={"code": 3, "message": "execution reverted"})
        return response
    monkeypatch.setattr(provider, "make_request", respond)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged",
        limits=LaunchExecutionLimits(rpc_total_simulation_gas_limit=35_000_000 if cumulative_cap else 50_000_000))
    if cumulative_cap:
        assert planned.admitted
        assert planned.simulation.execution_proof == planned.simulation.protocol_fit == "proved"
        assert planned.simulation.deferred_transaction_ids == ("activate",)
        assert [len(payload["blockStateCalls"]) for payload in provider.simulations] == [3, 2]
    else:
        assert not planned.admitted
        assert planned.simulation.execution_proof == "failed"
        assert planned.simulation.failed_transaction_id == "activate"
        assert planned.simulation.failure_category == "semantic"
        assert planned.simulation.evidence.calls[-1].decoded_error == "SlippageExceeded"
        assert len(provider.simulations) == 1


def test_default_aggregate_cap_proves_prefix_and_defers_unpriced_suffix_then_recovers(monkeypatch):
    request = hmm_plan(3)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"prepare_gas_per_market": 14_000_000, "compute_gas_by_kind": {"begin": 8_500_000, "activate": 18_000_000}})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert planned.admitted and planned.limits.rpc_total_simulation_gas_limit == 50_000_000
    assert planned.simulation.execution_proof == planned.simulation.protocol_fit == "proved"
    assert planned.simulation.deferred_transaction_ids == ("prepare:2:1", "activate")
    assert [len(payload["blockStateCalls"]) for payload in provider.simulations] == [2, 2]
    assert planned.simulation.evidence.complete
    for transaction in (*planned.transactions, *planned.simulation.transactions):
        if transaction.step_id in planned.simulation.deferred_transaction_ids:
            assert transaction.gas_limit is transaction.gas_price is transaction.estimate is transaction.admission is None
        else:
            assert transaction.admission.admitted and transaction.gas_limit is not None
    with pytest.raises(ValueError, match="activation outputs beyond the proven simulation prefix") as error:
        _protect_ordered_buy_minimums(planned, 500)
    assert error.value.code == "LAUNCH_DIAGNOSTIC_FAILED"
    provider.latest_phase = provider.confirmed_phase = LifecyclePhase.READY
    immediate = build_next_transaction(client, planned)
    assert immediate.kind == "activate" and immediate.admission.admitted
    assert immediate.gas_limit == 23_250_000


def test_explicit_aggregate_override_can_prove_whole_sequence(monkeypatch):
    request = hmm_plan(3)
    lifecycle, _, _, client = wire_planning(monkeypatch, request=request)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged",
        limits=LaunchExecutionLimits(rpc_total_simulation_gas_limit=100_000_000))
    assert planned.admitted and planned.simulation.deferred_transaction_ids == ()
    assert all(transaction.admission.admitted for transaction in planned.transactions)


@pytest.mark.parametrize("count,compute_cap,rpc_cap,constraint,reason", [
    (2, 32_000_000, 50_000_000, "compute",
     "Atomic launch needs about 55.3M gas, above the 32.0M per-transaction limit; use staged"),
    (1, 64_000_000, 30_000_000, "simulation-gas",
     "RPC aggregate simulation gas cap cannot fit the immediate transaction estimate"),
])
def test_no_prefix_refusal_prioritizes_compute_limit_over_rpc_budget(monkeypatch, count, compute_cap, rpc_cap, constraint, reason):
    request = hmm_plan(count)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"tx_cap": compute_cap, "block_cap": compute_cap})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic",
        limits=LaunchExecutionLimits(rpc_total_simulation_gas_limit=rpc_cap))
    assert not planned.admitted and planned.simulation.execution_proof == "unavailable"
    assert planned.simulation.deferred_transaction_ids == ("atomic",)
    assert planned.simulation.failure_category == "capacity"
    assert planned.simulation.capacity_constraint == constraint
    assert planned.simulation.reasons[0] == reason
    if constraint == "compute":
        assert planned.simulation.reasons == (reason,)
    assert provider.simulations == []


def test_owner_guard_collapses_planning_rpc_count(monkeypatch):
    request = hmm_plan(1)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="atomic")
    assert planned.admitted
    assert provider.operations.count("eth_chainId") == 2
    assert provider.operations.count("eth_getBlockByNumber") == 2
    assert len(provider.operations) == 15  # Five invariant getters share one attested aggregate.


def test_low_gas_semantic_prepare_failure_is_not_split(monkeypatch):
    request = hmm_plan(2)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request)
    original = provider.make_request
    def respond(method, params):
        response = original(method, params)
        if method == "eth_simulateV1" and not params[0]["validation"]:
            result = response["result"][-1]["calls"][0]
            result.update(status="0x0", gasUsed=hex(1_000_000), maxUsedGas=hex(1_000_000),
                returnData="0x" + keccak(text="InvalidConfiguration()")[:4].hex(),
                error={"code": 3, "message": "execution reverted"})
        return response
    monkeypatch.setattr(provider, "make_request", respond)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    assert not planned.admitted and planned.simulation.failure_category == "semantic"
    assert planned.simulation.failed_transaction_id == "prepare:0:2"
    assert len(provider.simulations) == 1
    assert [transaction.market_count for transaction in planned.transactions if transaction.kind == "prepare"] == [2]


def test_reviewed_prepare_preserves_held_span_without_batch_rediscovery(monkeypatch):
    request = hmm_plan(2)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request,
        scenario_options={"prepare_gas_per_market": 18_000_000})
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    held = planned.transactions[1]
    assert held.kind == "prepare" and held.market_count == 1
    provider.prepare_gas_per_market = 14_000_000
    provider.latest_phase = provider.confirmed_phase = LifecyclePhase.PREPARING
    original = provider.make_request
    def respond(method, params):
        return {"result": "0x1"} if method == "eth_getTransactionCount" else original(method, params)
    monkeypatch.setattr(provider, "make_request", respond)
    provider.simulations.clear()
    current = build_next_transaction(client, planned, reviewed_transaction=held)
    assert current.as_transaction() == held.as_transaction()
    assert current.admission.admitted
    assert [payload["validation"] for payload in provider.simulations] == [True]
    assert len(provider.simulations[0]["blockStateCalls"]) == 1
    _, args = provider.command(provider.simulations[0]["blockStateCalls"][0]["calls"][0])
    assert args[1:] == (0, 1)


@pytest.mark.parametrize("execution_succeeds", [False, True])
def test_reviewed_generic_owned_fork_replays_directly_without_estimation_or_backend_switch(monkeypatch, execution_succeeds):
    from types import SimpleNamespace
    from black_market_sdk import ControlledLaunchFork, build_lifecycle_calldata
    from test_lifecycle import TRANSACTION_HASH
    lifecycle, request, provider, client = wire_planning(monkeypatch)
    planned = lifecycle.plan_launch(client, request, account=request.creator, mode="staged")
    held = planned.transactions[0]
    provider.simulations.clear()
    class OwnedFork:
        endpoint_uri = "http://127.0.0.1:19993"
        def __init__(self):
            self.operations = []
            self.last_transaction = None
        def make_request(self, method, params):
            self.operations.append(method)
            if method in {"eth_chainId", "eth_getBlockByNumber", "eth_getTransactionCount"}:
                return provider.make_request(method, params)
            if method == "evm_snapshot":
                value = "0x1"
            elif method == "evm_revert":
                value = True
            elif method == "eth_sendTransaction":
                self.last_transaction = params[0]
                value = TRANSACTION_HASH
            elif method == "eth_getTransactionReceipt":
                value = {"status": hex(int(execution_succeeds)), "gasUsed": hex(100_000),
                         "logs": provider.logs(self.last_transaction) if execution_succeeds else []}
            elif method == "eth_call":
                value = provider.return_data({"data": build_lifecycle_calldata(request, "beginLaunch")})
            else:
                raise AssertionError(f"Unexpected disposable fork request: {method}")
            return {"result": value}
    fork_provider = OwnedFork()
    fork = ControlledLaunchFork(client=SimpleNamespace(provider=fork_provider), isolated=True,
        source_rpc_url=provider.endpoint_uri)
    if execution_succeeds:
        current = build_next_transaction(client, planned, reviewed_transaction=held, fork=fork)
        assert current.as_transaction() == held.as_transaction() and current.admission.admitted
    else:
        with pytest.raises(ValueError) as failure:
            build_next_transaction(client, planned, reviewed_transaction=held, fork=fork)
        assert failure.value.code == "PLAN_NOT_ADMITTED"
        assert failure.value.simulation.backend == "controlled-fork"
    assert provider.simulations == []
    assert "eth_estimateGas" not in fork_provider.operations
    assert fork_provider.operations.count("eth_sendTransaction") == 1
    assert fork_provider.operations[-1] == "evm_revert"
    assert int(fork_provider.last_transaction["gas"], 16) == held.gas_limit
    assert int(fork_provider.last_transaction["gasPrice"], 16) == held.gas_price


def test_exact_hmm_profile_estimates_are_global_but_unknown_profiles_get_no_reserve():
    from black_market_sdk.lifecycle import _prepare_gas_estimate, _forwarding_reserve
    request = replace(hmm_plan(3), chain_id=31337, orchestrator="0x" + "12" * 20)
    assert [_prepare_gas_estimate(request, index) for index in range(3)] == [14_500_000] * 3
    assert _forwarding_reserve(request, "activate") == 5_250_000
    assert _groups(request, 0, 32_000_000) == [(0, 2), (2, 1)]
    unknown = replace(request, markets=tuple(replace(market, profile_id=bytes(32)) for market in request.markets))
    assert [_prepare_gas_estimate(unknown, index) for index in range(3)] == [16_000_000] * 3
    assert _forwarding_reserve(unknown, "activate") == _forwarding_reserve(unknown, "atomic") == 0


def test_unified_preparation_retains_only_owner_source_guards(monkeypatch):
    import asyncio
    from black_market_sdk.lifecycle import _known_bound_deployment, PoolBoundLifecycleDeployment
    from test_lifecycle import ORPHAN_HASH
    request = hmm_plan(1)
    lifecycle, _, provider, client = wire_planning(monkeypatch, request=request)
    def metadata(_client, current, token, _block):
        provider.plan = current
        deployment, _ = _known_bound_deployment(current, 0, token)
        return (), (PoolBoundLifecycleDeployment(deployment.deployer, deployment.init_code_hash,
            deployment.salt, deployment.predicted_hook, 0),), ()
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", metadata)
    def advance(_progress):
        provider.block_number = 52
        provider.block_hash = ORPHAN_HASH
    planned = asyncio.run(lifecycle.prepare_and_plan_lifecycle_launch(client, request,
        account=request.creator, mode="atomic", on_progress=advance))
    assert planned.admitted and planned.progress.head_block.number == 52
    assert provider.operations.count("eth_chainId") == 2
    assert provider.operations.count("eth_getBlockByNumber") == 3
    assert len(provider.operations) == 17
