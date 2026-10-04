"""Expected-negative smoke rows must never hide unrelated execution failures."""
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from black_market_sdk.lifecycle_rpc import LaunchBlock, LaunchExecutionLimits, LaunchRpcSimulation, LaunchSimulationCall
from black_market_sdk.lifecycle import LaunchSimulation, LifecycleTransaction

_spec = spec_from_file_location("lifecycle_smoke", Path(__file__).parents[1] / "examples/launch_lifecycle_smoke.py")
_smoke = module_from_spec(_spec)
_spec.loader.exec_module(_smoke)

CAP = 16_000_000
BLOCK = LaunchBlock(7, "0x" + "ab" * 32, 100, 30_000_000, 1)
LIMITS = LaunchExecutionLimits(headroom_bps=2000, account_transaction_gas_limit=CAP)
PREFIX = LaunchSimulationCall(True, 50_000, "0x", ())


def launch(kind, reasons, calls, *, backend="controlled-fork-ceiling-receipts"):
    transactions = (SimpleNamespace(kind="approval", gas_limit=CAP), SimpleNamespace(kind=kind, gas_limit=CAP))
    evidence = LaunchRpcSimulation("stateful", backend, BLOCK, tuple(calls), None, len(transactions))
    return SimpleNamespace(limits=LIMITS, simulation=SimpleNamespace(block=BLOCK, reasons=tuple(reasons), evidence=evidence, transactions=transactions))


@pytest.mark.parametrize("kind", ["atomic", "activate"])
def test_successful_prefix_does_not_authenticate_an_unmeasured_failure(kind):
    assert _smoke.refusal_outcome(launch(kind, ("estimator failed",), (PREFIX,))).startswith("failure:")


@pytest.mark.parametrize("kind", ["atomic", "activate"])
def test_ceiling_estimate_does_not_excuse_a_below_cap_business_revert(kind):
    reverted = LaunchSimulationCall(False, 3_000_000, "0x", (), {"outOfGas": False}, CAP)
    assert _smoke.refusal_outcome(launch(kind, ("business revert",), (PREFIX, reverted))).startswith("failure:")


def test_failed_prefix_cannot_be_hidden_by_later_cap_consumption():
    reverted_prefix = LaunchSimulationCall(False, 50_000, "0x", (), {"message": "approval failed"})
    exhausted = LaunchSimulationCall(False, CAP, "0x", ())
    assert _smoke.refusal_outcome(launch("activate", ("execution reverted",), (reverted_prefix, exhausted))).startswith("failure:")


def test_only_successful_complete_receipts_authenticate_headroom_failure():
    measured = LaunchSimulationCall(True, 14_000_000, "0x", ())
    complete = launch("atomic", ("unclassified estimator refusal",), (PREFIX, measured))
    assert _smoke.refusal_outcome(complete) == "gas-cap-refusal"
    assert _smoke._receipt_refusal_kind(complete) == "headroom-only"
    assert _smoke.refusal_outcome(launch("atomic", (), (PREFIX, measured), backend="controlled-fork")).startswith("failure:")


def test_near_cap_consumption_cannot_replace_terminal_call_chain_oog():
    expensive_business_revert = LaunchSimulationCall(False, CAP - 10_000, "0x", (), {"outOfGas": False})
    assert _smoke.refusal_outcome(launch("activate", (), (PREFIX, expensive_business_revert))).startswith("failure:")
    nested = LaunchSimulationCall(False, 15_000_000, "0x", (), {"outOfGas": True})
    receipt = launch("atomic", (), (PREFIX, nested))
    assert _smoke.refusal_outcome(receipt) == "gas-cap-refusal"
    assert _smoke._receipt_refusal_kind(receipt) == "terminal-oog"
    assert _smoke.refusal_outcome(launch("atomic", (), (PREFIX, nested), backend="controlled-fork")).startswith("failure:")


def test_complete_receipt_headroom_uses_the_configured_ceiling_without_raising_block_limit():
    cap = 32_000_000
    block = LaunchBlock(7, BLOCK.block_hash, 100, cap, 1)
    limits = LaunchExecutionLimits(headroom_bps=1000, chain_transaction_gas_limit=cap, rpc_transaction_gas_limit=cap, account_transaction_gas_limit=cap)
    transactions = (SimpleNamespace(kind="approval", gas_limit=cap), SimpleNamespace(kind="atomic", gas_limit=cap))
    measured = LaunchSimulationCall(True, 30_000_000, "0x", ())
    proof = LaunchRpcSimulation("stateful", "controlled-fork-ceiling-receipts", block, (PREFIX, measured), None, len(transactions))
    complete = SimpleNamespace(limits=limits, simulation=SimpleNamespace(block=block, reasons=(), evidence=proof, transactions=transactions))
    assert limits.gas_cap(block) == cap
    assert _smoke._receipt_refusal_kind(complete) == "headroom-only"


def policy_pair():
    cap = 32_000_000
    block = LaunchBlock(7, BLOCK.block_hash, 100, cap, 1)
    creator, core = "0x" + "12" * 20, "0x" + "34" * 20
    limits = LaunchExecutionLimits(
        headroom_bps=1000, chain_transaction_gas_limit=cap,
        rpc_transaction_gas_limit=cap, account_transaction_gas_limit=cap,
        max_calldata_bytes=131072, observed_block_number=block.number,
        observed_block_hash=block.block_hash, chain_id=31337, account=creator, orchestrator=core,
    )
    transactions = tuple(LifecycleTransaction(
        step_id=kind, kind=kind, chain_id=31337, from_address=creator, to=core,
        data="0x0102", value=0, nonce=index, dependencies=(), postconditions=(), gas_limit=cap,
    ) for index, kind in enumerate(("approval", "atomic")))
    sdk_calls = (PREFIX, LaunchSimulationCall(True, 27_253_523, "0x", (), gas_required=30_000_000))
    sdk = LaunchRpcSimulation("stateful", "controlled-fork", block, sdk_calls, expected=2)
    simulation = LaunchSimulation("stateful", False, block, sdk.backend, transactions, (), sdk)
    probe = LaunchRpcSimulation("stateful", "controlled-fork-ceiling-receipts", block,
        (PREFIX, LaunchSimulationCall(True, 27_253_523, "0x", ())), expected=2)
    original = SimpleNamespace(limits=limits, simulation=simulation)
    receipts = SimpleNamespace(limits=limits, simulation=replace(simulation, evidence=probe))
    return original, receipts


def test_successful_receipts_keep_an_overlimit_sdk_envelope_separate_from_execution_refusal():
    original, receipts = policy_pair()
    assert _smoke.refusal_outcome(receipts).startswith("failure:")
    policy = _smoke.sdk_policy_refusal_evidence(original, receipts)
    assert policy is not None
    assert policy["bufferedRequiredEnvelopeGas"] > policy["executionGasCeiling"]
    assert policy["receiptComparison"]["bufferedReceiptGas"] < policy["executionGasCeiling"]
    assert policy["receiptComparison"]["sourceSubmissionAdmitted"] is False


@pytest.mark.parametrize("failure", ["business-prefix", "missing-envelope", "unknown-cap", "different-command"])
def test_sdk_policy_evidence_cannot_excuse_unproven_or_unmatched_refusals(failure):
    original, receipts = policy_pair()
    if failure == "business-prefix":
        evidence = replace(original.simulation.evidence, calls=(
            LaunchSimulationCall(False, 50_000, "0x", (), {"message": "approval failed"}),
            original.simulation.evidence.calls[1],
        ))
        original.simulation = replace(original.simulation, evidence=evidence)
    elif failure == "missing-envelope":
        evidence = replace(original.simulation.evidence, calls=(
            PREFIX, replace(original.simulation.evidence.calls[1], gas_required=None),
        ))
        original.simulation = replace(original.simulation, evidence=evidence)
    elif failure == "unknown-cap":
        original.limits = replace(original.limits, rpc_transaction_gas_limit=None)
    else:
        transactions = receipts.simulation.transactions
        receipts.simulation = replace(receipts.simulation,
            transactions=(transactions[0], replace(transactions[1], data="0x0304")))
    assert _smoke.sdk_policy_refusal_evidence(original, receipts) is None
