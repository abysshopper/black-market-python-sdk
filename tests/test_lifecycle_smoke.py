"""Expected-negative smoke rows must never hide unrelated execution failures."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from black_market_sdk.lifecycle_rpc import LaunchBlock, LaunchExecutionLimits, LaunchRpcSimulation, LaunchSimulationCall

_spec = spec_from_file_location("lifecycle_smoke", Path(__file__).parents[1] / "examples/launch_lifecycle_smoke.py")
_smoke = module_from_spec(_spec)
_spec.loader.exec_module(_smoke)

CAP = 16_000_000
BLOCK = LaunchBlock(7, "0x" + "ab" * 32, 100, 30_000_000, 1)
LIMITS = LaunchExecutionLimits(headroom_bps=2000, account_transaction_gas_limit=CAP)
PREFIX = LaunchSimulationCall(True, 50_000, "0x", ())


def launch(kind, reasons, calls, *, backend="controlled-fork"):
    transactions = (SimpleNamespace(kind="approval", gas_limit=CAP), SimpleNamespace(kind=kind, gas_limit=CAP))
    evidence = LaunchRpcSimulation("stateful", backend, BLOCK, tuple(calls), None, len(transactions))
    return SimpleNamespace(limits=LIMITS, simulation=SimpleNamespace(block=BLOCK, reasons=tuple(reasons), evidence=evidence, transactions=transactions))


@pytest.mark.parametrize("kind", ["atomic", "activate"])
@pytest.mark.parametrize("reason", [
    "eth_estimateGas: execution reverted",
    "execution failed; choose staged explicitly only if empty preparation can be partitioned",
    "Validated execution at the exact current gas ceiling still failed: execution failed",
])
def test_successful_prefix_does_not_authenticate_an_unmeasured_failure(kind, reason):
    assert _smoke.refusal_outcome(launch(kind, (reason,), (PREFIX,))).startswith("failure:")


@pytest.mark.parametrize("kind", ["atomic", "activate"])
def test_ceiling_estimate_does_not_excuse_a_below_cap_business_revert(kind):
    reverted = LaunchSimulationCall(False, 3_000_000, "0x", (), {"message": "the measured transaction exceeds the current execution ceiling"}, CAP)
    assert _smoke.refusal_outcome(launch(kind, ("the measured transaction exceeds the current execution ceiling",), (PREFIX, reverted))).startswith("failure:")


def test_failed_prefix_cannot_be_hidden_by_later_cap_consumption():
    reverted_prefix = LaunchSimulationCall(False, 50_000, "0x", (), {"message": "approval failed"})
    exhausted = LaunchSimulationCall(False, CAP, "0x", ())
    assert _smoke.refusal_outcome(launch("activate", ("execution reverted",), (reverted_prefix, exhausted))).startswith("failure:")


def test_numeric_headroom_and_authentic_terminal_exhaustion_are_supported():
    measured = LaunchSimulationCall(True, 14_000_000, "0x", (), None, 14_000_000)
    assert _smoke.refusal_outcome(launch("atomic", ("a measured transaction does not fit current limits with conservative headroom",), (PREFIX, measured))) == "gas-cap-refusal"
    exhausted = LaunchSimulationCall(False, CAP - 10_000, "0x", ())
    assert _smoke.refusal_outcome(launch("activate", ("transaction reverted",), (PREFIX, exhausted))) == "gas-cap-refusal"
    nested = LaunchSimulationCall(False, 15_000_000, "0x", (), {"outOfGas": True})
    assert _smoke.refusal_outcome(launch("atomic", ("execution reverted",), (PREFIX, nested), backend="controlled-fork-ceiling-receipts")) == "gas-cap-refusal"
    assert _smoke.refusal_outcome(launch("atomic", ("execution reverted",), (PREFIX, nested))).startswith("failure:")
