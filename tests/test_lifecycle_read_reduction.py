"""Planning-stage batching, dependent snapshots and immediate-only execution proof."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak

from black_market_sdk import (
    HOOK_SALT_MINER_ABI, LAUNCH_LENS_ABI, LAUNCH_LENS_SNAPSHOT_COMPONENTS, LAUNCH_PLAN_COMPONENTS_V1,
    KnownLifecycleProfile, LifecycleAssetFunding, LifecyclePlanningError,
    LaunchExecutionLimits, ROBINHOOD_MULTICALL3,
    ControlledLaunchFork,
    build_next_transaction, get_known_lifecycle_deployment, get_known_lifecycle_profile,
    decode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_pool_bound_v4_market_config,
    launch_id_of, prepare_and_plan_lifecycle_launch, read_launch_progress,
    LifecycleReceiptReference, UFG_LAUNCH_PROXY_ADDRESS, build_ufg_launch_transactions,
)
from black_market_sdk.lifecycle import (
    _aggregate_lifecycle_reads, _encode_function, _execution_account, _known_bound_deployment,
    _prime_lifecycle_reads, _read_funding, _tuple_type, _json_tuple, launch_plan_from_dict,
    encode_pool_bound_hook_parameters, predict_pool_bound_hook_address,
)
from black_market_sdk.lifecycle_abis import NITRO_ARB_SYS_ABI
from black_market_sdk.lifecycle_rpc import read_invocation
from test_lifecycle_read_counts import CountingTransport, NativeBuyScenario, draft_plan
from test_lifecycle import AdmissionRpcScenario, BLOCK_HASH, admission_fixture, simulate_fixture
from test_ufg import CORE, PRINCIPAL, QUOTE, TOKEN, ZERO, terms

LENS = "0x7777777777777777777777777777777777777777"


def ordinary_bound_draft(count=2):
    draft = draft_plan()
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=CORE,
        key=KnownLifecycleProfile.V4_DYNAMIC_FEE_POOL)
    config = decode_lifecycle_pool_bound_v4_market_config(draft.markets[0].config)
    envelope = preset.profile.envelope
    config = replace(config, profile_id=preset.profile.id, terms_digest=envelope.terms_digest,
        developer_beneficiary=envelope.beneficiary, treasury=envelope.protocol_treasury,
        protocol_fee_denominator=envelope.protocol_fee_denominator, ufg_launch_config=None)
    first = replace(draft.markets[0], profile_id=preset.profile.id,
        adapter_id=preset.profile.registration.adapter_id,
        config=encode_lifecycle_pool_bound_v4_market_config(config))
    markets = tuple(replace(first, quote_asset=f"0x{index + 1:040x}") for index in range(count))
    return replace(draft, creator=PRINCIPAL, token=replace(draft.token, supply=draft.token.supply * count),
        markets=markets, funding=(), buys=())

class OrdinaryScenario(AdmissionRpcScenario):
    def command(self, transaction):
        kind, args = super().command(transaction)
        if args:
            self.plan = launch_plan_from_dict(_json_tuple(LAUNCH_PLAN_COMPONENTS_V1, args[0]))
        return kind, args



def prepare(client, draft, **kwargs):
    return asyncio.run(prepare_and_plan_lifecycle_launch(client, draft, account=PRINCIPAL,
        mode="staged", limits=LaunchExecutionLimits(rpc_total_simulation_gas_limit=200_000_000), **kwargs))


def aggregate_calls(client):
    return [call for call in client.calls if call["method"] == "eth_call"
        and call["params"][0]["to"].lower() == ROBINHOOD_MULTICALL3.lower()]


def test_same_stage_funding_reads_share_pin_and_aggregate_duplicate_inputs():
    draft = draft_plan()
    draft = replace(draft, funding=(
        LifecycleAssetFunding(QUOTE, 11, 0, QUOTE, 11, ZERO, b""),
        LifecycleAssetFunding(TOKEN, 7, 2, QUOTE, 13, PRINCIPAL, b"conversion"),
    ))
    client = CountingTransport(NativeBuyScenario(draft))

    @read_invocation("plan")
    def read(scoped):
        block = _prime_lifecycle_reads(scoped, draft)
        prerequisites, approvals = _read_funding(scoped, draft, block)
        return block, prerequisites, approvals

    block, prerequisites, approvals = read(client)
    assert block.number == client.provider.block_number and block.block_hash == BLOCK_HASH
    assert len(prerequisites) == 2 and approvals[0].required_amount == 24
    assert len(aggregate_calls(client)) == 1
    assert not client.counts["eth_getBalance"] and client.counts["eth_getBlockByNumber"] == 1
    assert aggregate_calls(client)[0]["params"][1] == block.tag
    calls = abi_decode(["(address,bool,bytes)[]"], bytes.fromhex(aggregate_calls(client)[0]["params"][0]["data"][10:]))[0]
    assert [data[:4] for target, _, data in calls if target.lower() == ROBINHOOD_MULTICALL3.lower()] == [keccak(text="getEthBalance(address)")[:4]]
    token_reads = [data[:4] for target, _, data in calls if target.lower() == QUOTE.lower()]
    assert token_reads.count(keccak(text="balanceOf(address)")[:4]) == 1
    assert token_reads.count(keccak(text="allowance(address,address)")[:4]) == 1


def test_single_read_batches_without_threshold_and_attestation_is_session_scoped():
    client = CountingTransport(NativeBuyScenario(draft_plan()))
    calls = [{"to": "0x0000000000000000000000000000000000000064",
        "data": _encode_function(NITRO_ARB_SYS_ABI, "arbOSVersion", [])}]

    @read_invocation("plan")
    def read(scoped):
        return _aggregate_lifecycle_reads(scoped, calls, tag="latest")

    assert abi_decode(["uint256"], read(client)[0][1])[0] == 105
    client.provider.raw_version = 106
    assert abi_decode(["uint256"], read(client)[0][1])[0] == 106
    assert client.counts["eth_getCode"] == 1 and len(aggregate_calls(client)) == 2


def test_deployed_miner_batches_markets_and_verifies_create2_locally(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft = ordinary_bound_draft()
    client = CountingTransport(OrdinaryScenario(draft))
    def forbidden(**_):
        raise AssertionError("Known deployments must use the deployed miner")
    monkeypatch.setattr(lifecycle, "mine_pool_bound_hook_salt", forbidden)
    launch = prepare(client, draft)
    assert launch.admitted and len(launch.hook_deployments) == 2
    miner = get_known_lifecycle_deployment(chain_id=4663, orchestrator=CORE).hook_salt_miner
    calls = [call for call in client.calls if call["method"] == "eth_call"
        and call["params"][0]["to"].lower() == miner.lower()]
    assert len(calls) == 1
    searches, attempts = abi_decode(["(address,bytes32,uint256,uint160)[]", "uint256"],
        bytes.fromhex(calls[0]["params"][0]["data"][10:]))
    assert len(searches) == 2 and attempts == 75_000
    for deployment in launch.hook_deployments:
        assert deployment.predicted_hook == predict_pool_bound_hook_address(
            deployer=deployment.deployer, init_code_hash=deployment.init_code_hash, salt=deployment.salt)
        assert int(deployment.predicted_hook, 16) & 0x3fff == 0x1afc


@pytest.mark.parametrize("fault", ["wrong-hook", "outside-window", "missing"])
def test_deployed_miner_rejects_invalid_results_before_simulation(fault):
    draft = ordinary_bound_draft(1)
    client = CountingTransport(OrdinaryScenario(draft))
    selector = _encode_function(HOOK_SALT_MINER_ABI, "findMany", [[], 1])[:10]
    def corrupt(args, response):
        if args["method"] != "eth_call" or args["params"][0]["data"][:10] != selector:
            return
        salts, hooks, found = [list(values) for values in abi_decode(
            ["bytes32[]", "address[]", "bool[]"], bytes.fromhex(response["result"][2:]))]
        if fault == "wrong-hook":
            hooks[0] = ZERO
        elif fault == "outside-window":
            salts[0] = (150_000).to_bytes(32, "big")
        else:
            found.pop()
        response["result"] = "0x" + abi_encode(["bytes32[]", "address[]", "bool[]"], [salts, hooks, found]).hex()
    client.after_response = corrupt
    with pytest.raises(LifecyclePlanningError) as failure:
        prepare(client, draft)
    assert failure.value.code == "INVALID_HOOK_SALT" and not client.counts["eth_simulateV1"]


def test_miner_continues_only_unfound_markets_from_next_unsearched_salt():
    draft = ordinary_bound_draft(1)
    client = CountingTransport(OrdinaryScenario(draft))
    selector = _encode_function(HOOK_SALT_MINER_ABI, "findMany", [[], 1])[:10]
    starts = []
    def exhausted(args, response):
        if args["method"] != "eth_call" or args["params"][0]["data"][:10] != selector:
            return
        searches, attempts = abi_decode(["(address,bytes32,uint256,uint160)[]", "uint256"],
            bytes.fromhex(args["params"][0]["data"][10:]))
        starts.append(searches[0][2])
        if len(starts) == 1:
            response["result"] = "0x" + abi_encode(["bytes32[]", "address[]", "bool[]"],
                [[(searches[0][2] + attempts).to_bytes(32, "big")], [ZERO], [False]]).hex()
    client.after_response = exhausted
    launch = prepare(client, draft)
    assert launch.admitted and starts == [0, 150_000]


class LensScenario(NativeBuyScenario):
    def make_request(self, method, params):
        if method == "eth_call" and params[0]["to"].lower() == LENS.lower():
            inputs = [item["type"] if item["type"] != "tuple" else _tuple_type(item["components"])
                for item in LAUNCH_LENS_ABI[0]["inputs"]]
            _, owner, spender, tokens, attempts = abi_decode(inputs, bytes.fromhex(params[0]["data"][10:]))
            assert owner.lower() == _execution_account(self.plan).lower()
            hooks = []
            for index, market in enumerate(self.plan.markets):
                if market.config_version not in (5, 6):
                    continue
                deployment, parameters = _known_bound_deployment(self.plan, index, TOKEN)
                salt = int(deployment.salt, 16)
                hook = deployment.predicted_hook
                if attempts:
                    while int(hook, 16) & 0x3fff != 0x1afc:
                        salt += 1
                        hook = predict_pool_bound_hook_address(deployer=deployment.deployer,
                            init_code_hash=deployment.init_code_hash, salt=salt.to_bytes(32, "big"))
                hooks.append((index, parameters.registrar, deployment.deployer,
                    encode_pool_bound_hook_parameters(parameters, config_version=market.config_version),
                    bytes.fromhex(deployment.init_code_hash[2:]), salt.to_bytes(32, "big"), hook,
                    bool(attempts)))
            prepared = []
            if self.latest_phase:
                for index in range(len(self.plan.markets)):
                    adapter, market = self.prepared_market(index)
                    success = not getattr(self, "live_failure", False)
                    prepared.append((index, adapter, market, success, (1 << 96, 0, 0, False, 0),
                        b"" if success else b"unavailable"))
            snapshot = (self.plan.chain_id, self.block_number, bytes.fromhex(self.block_hash[2:]),
                self.plan.deadline - 100, self.balance,
                [(token, self.balance, self.allowance) for token in tokens], TOKEN,
                bytes.fromhex(launch_id_of(self.plan)[2:]), self.progress(self.latest_phase), prepared, hooks)
            return {"result": "0x" + abi_encode([_tuple_type(LAUNCH_LENS_SNAPSHOT_COMPONENTS)], [snapshot]).hex()}
        return super().make_request(method, params)


@pytest.fixture
def lens_preset(monkeypatch):
    import black_market_sdk.lifecycle_presets as presets
    monkeypatch.setattr(presets, "_DEPLOYMENT", replace(presets._DEPLOYMENT, launch_lens=LENS))


def test_lens_path_seeds_progress_balances_and_mining_in_first_aggregate(lens_preset):
    draft = draft_plan()
    client = CountingTransport(LensScenario(draft))
    launch = prepare(client, draft, ufg=terms(), buy_slippage_bps=50)
    assert launch.admitted and launch.plan.buys[0].min_token_out == 995
    assert len(aggregate_calls(client)) == 1
    calls = abi_decode(["(address,bool,bytes)[]"], bytes.fromhex(aggregate_calls(client)[0]["params"][0]["data"][10:]))[0]
    assert sum(target.lower() == LENS.lower() for target, _, _ in calls) == 1
    miner = get_known_lifecycle_deployment(chain_id=4663, orchestrator=CORE).hook_salt_miner
    assert not any(call["params"][0]["to"].lower() == miner.lower()
        for call in client.calls if call["method"] == "eth_call")
    assert client.counts["eth_getBlockByNumber"] == 1 and not client.counts["eth_getBalance"]


def test_lens_prepared_markets_retain_partial_live_read_failure(lens_preset):
    draft = draft_plan()
    provider = LensScenario(draft, latest_phase=2, confirmed_phase=2)
    provider.live_failure = True
    client = CountingTransport(provider)
    progress = read_launch_progress(client, draft)
    assert progress.prepared_markets == 1 and len(progress.markets) == 1
    assert progress.markets[0].live is None
    assert progress.markets[0].error == "Market state read unavailable"
    assert len(aggregate_calls(client)) == 1


def test_next_builder_avoids_plan_metadata_and_proves_only_immediate_command(monkeypatch):
    import black_market_sdk.lifecycle as lifecycle
    draft = ordinary_bound_draft()
    client = CountingTransport(OrdinaryScenario(draft))
    launch = prepare(client, draft)
    client.provider.simulations.clear()
    client.calls.clear()
    def forbidden(*_, **__):
        raise AssertionError("Build-next must not replan or refetch profile metadata")
    monkeypatch.setattr(lifecycle, "_plan_launch", forbidden)
    monkeypatch.setattr(lifecycle, "_read_plan_metadata", forbidden)
    transaction = build_next_transaction(client, launch)
    assert transaction.kind == "begin" and transaction.admission.admitted
    assert len(client.provider.simulations) == 1
    assert client.provider.simulations[0]["validation"] is True
    assert len(client.provider.simulations[0]["blockStateCalls"]) == 1
    assert client.counts["eth_getCode"] == 0  # Both successful capabilities survive the new pin.
    assert len(client.provider.probes) == 1


def test_receipt_at_head_reuses_authoritative_l2_header():
    draft = draft_plan()
    client = CountingTransport(NativeBuyScenario(draft))
    original = client.provider.make_request
    def receipts(method, params):
        if method == "eth_getTransactionReceipt":
            return {"result": {"blockNumber": "0x33", "blockHash": BLOCK_HASH, "status": "0x1"}}
        if method == "eth_getTransactionByHash":
            return {"result": {"from": PRINCIPAL, "to": UFG_LAUNCH_PROXY_ADDRESS,
                "input": build_ufg_launch_transactions(draft, config=terms(), mode="staged")[0].data,
                "value": hex(10**14)}}
        return original(method, params)
    client.provider.make_request = receipts
    reference = LifecycleReceiptReference("0x" + "cc" * 32,
        observed_block_number=51, observed_block_hash=BLOCK_HASH)
    progress = read_launch_progress(client, draft, receipts=(reference,))
    assert progress.head_block.block_hash == BLOCK_HASH
    assert client.counts["eth_getBlockByNumber"] == 1
    assert progress.receipts[0].status == "confirmed"
    assert progress.receipts[0].block_hash == BLOCK_HASH


@pytest.mark.parametrize("code,message,category", [
    (3, "execution reverted", "opaque"), (-32000, "out of gas", "capacity"),
])
def test_final_measurement_never_masks_execution_refusal_with_a_fork(monkeypatch, code, message, category):
    fixture = admission_fixture()
    provider = fixture[1]
    original = provider.make_request
    def fail(method, params):
        if method == "eth_simulateV1":
            return {"error": {"code": code, "message": message}}
        return original(method, params)
    monkeypatch.setattr(provider, "make_request", fail)
    operations = []
    def forbidden(method, _params):
        operations.append(method)
        raise AssertionError("Execution refusals must not select a different backend")
    fork = ControlledLaunchFork(client=SimpleNamespace(provider=SimpleNamespace(
        endpoint_uri="http://127.0.0.1:19993", make_request=forbidden)),
        isolated=True, source_rpc_url=provider.endpoint_uri)
    result = simulate_fixture(fixture, fork=fork)
    assert not result.admitted and result.failure_category == category
    assert operations == []


@pytest.mark.parametrize("field", [0, 1])
def test_lens_rejects_a_different_chain_or_l1_height(lens_preset, field):
    draft = draft_plan()
    provider = LensScenario(draft)
    provider.block_number = 99_875_269
    client = CountingTransport(provider)
    def corrupt(args, response):
        if args["method"] != "eth_call" or args["params"][0]["to"].lower() != ROBINHOOD_MULTICALL3.lower():
            return
        calls = abi_decode(["(address,bool,bytes)[]"], bytes.fromhex(args["params"][0]["data"][10:]))[0]
        values = list(abi_decode(["(bool,bytes)[]"], bytes.fromhex(response["result"][2:]))[0])
        index = next(index for index, (target, _, _) in enumerate(calls) if target.lower() == LENS.lower())
        snapshot_type = _tuple_type(LAUNCH_LENS_SNAPSHOT_COMPONENTS)
        snapshot = list(abi_decode([snapshot_type], values[index][1])[0])
        snapshot[field] = 4664 if field == 0 else 26_289_498
        values[index] = (True, abi_encode([snapshot_type], [tuple(snapshot)]))
        response["result"] = "0x" + abi_encode(["(bool,bytes)[]"], [values]).hex()
    client.after_response = corrupt
    with pytest.raises(LifecyclePlanningError) as failure:
        read_launch_progress(client, draft)
    assert failure.value.code == "INVALID_RPC_RESPONSE"
    assert not client.counts["eth_simulateV1"]


def test_prepared_directory_and_live_reads_batch_in_separate_dependency_stages():
    draft = ordinary_bound_draft()
    provider = OrdinaryScenario(draft, latest_phase=2, confirmed_phase=2)
    original = provider.make_request
    def fail_second_live(method, params):
        if method == "eth_call" and params[0]["data"][:10] == "0x" + keccak(text="readMarket(bytes32,uint32)")[:4].hex():
            _, index = abi_decode(["bytes32", "uint32"], bytes.fromhex(params[0]["data"][10:]))
            if index == 1:
                return {"error": {"code": 3, "message": "execution reverted"}}
        return original(method, params)
    provider.make_request = fail_second_live
    client = CountingTransport(provider)
    progress = read_launch_progress(client, draft)
    assert len(progress.markets) == 2
    assert progress.markets[0].live is not None and progress.markets[1].live is None
    assert progress.markets[1].error == "Market state read unavailable"
    assert len(aggregate_calls(client)) == 3
    assert {call["params"][1] for call in aggregate_calls(client)} == {"0x33"}
