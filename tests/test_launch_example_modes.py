"""Offline runner modes; synthetic keys and fake RPC/API boundaries only."""

from __future__ import annotations

import base64
import json
import runpy
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from web3 import Web3

from examples import launch_examples as example
from examples._launch_support import Artifacts, LocalSigningWallet
from black_market_sdk import lifecycle
from test_launch_examples import (
    API_ORIGIN, TOKEN, TX_HASH, case_definition, construct_case, no_call, assessed_review_launch,
)

CASES = [
    "launch_erc20_v4.py", "launch_erc20_abyss.py", "launch_erc404_v4.py", "launch_erc404_abyss.py",
    "launch_erc20_staking_v4.py", "launch_erc20_dividends_abyss.py", "launch_erc20_burn_mixed.py",
    "launch_erc404_dividends_mixed.py", "launch_erc20_multi_quote.py", "launch_erc20_depth_v4.py",
    "launch_erc20_dividends_multiquote_mixed.py",
]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS0AAAAASUVORK5CYII=")


@pytest.fixture
def runner_context(construct_case, tmp_path, monkeypatch):
    """Keep real construction/admission artifacts/review; replace external reads/proof."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "_ENV_LOADED", False)
    monkeypatch.setenv("PRIVATE_KEY", "0x" + "01" * 32)
    monkeypatch.setenv("LAUNCH_API_URL", API_ORIGIN)
    monkeypatch.setenv("RPC_URL", "https://rpc.example")
    for name in (
        "EXAMPLE_KEYSTORE_PATH", "EXAMPLE_KEYSTORE_PASSWORD", "EXAMPLE_KYBER_API_URL",
        "EXAMPLE_BUY_AMOUNT_WEI", "EXAMPLE_TARGET_MARKET_CAP_USD", "NFT_BASE_URI",
        "LAUNCH_CHAIN_GAS_CAP", "LAUNCH_RPC_GAS_CAP", "LAUNCH_ACCOUNT_GAS_CAP", "LAUNCH_CALLDATA_CAP",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "token.png"
    path.write_bytes(PNG)
    monkeypatch.setenv("EXAMPLE_IMAGE_PATH", str(path))
    monkeypatch.setattr("builtins.input", no_call)
    monkeypatch.setattr(Web3.HTTPProvider, "make_request", no_call)
    deployment = example.get_launch_addresses(4663)

    class OfflineWeb3(Web3):
        def __init__(self, provider):
            super().__init__(provider)
            monkeypatch.setattr(self.eth, "get_code", lambda address:
                                b"\x60\x00" if example.address_equal(address, deployment.orchestrator) else b"")

    monkeypatch.setattr(example, "Web3", OfflineWeb3)
    quote_contract = example.contract

    def call(value):
        def read(*, block_identifier=None):
            if block_identifier is not None:
                assert block_identifier == example.read_block(None).number
            return value
        return lambda: SimpleNamespace(call=read)

    def contracts(client, address, abi):
        if example.address_equal(address, deployment.orchestrator):
            return SimpleNamespace(address=address, functions=SimpleNamespace(
                registry=call(deployment.registry), fundingEscrow=call(deployment.funding_escrow)))
        if example.address_equal(address, deployment.funding_escrow):
            return SimpleNamespace(functions=SimpleNamespace(wrappedNative=call(deployment.wrapped_native)))
        return quote_contract(client, address, abi)

    monkeypatch.setattr(example, "contract", contracts)
    preparations, admitted = [], [True]

    async def prepare(client, plan, **options):
        # Model the planner's final protected commitment rather than the draft minima.
        buys = tuple(replace(buy, min_token_out=(350 * 10**18 * buy.quote_amount_in // 10**15) * 9950 // 10000)
                     for buy in plan.buys)
        launch = assessed_review_launch(replace(plan, buys=buys))
        launch.mode = options["mode"]
        launch.predicted_token = TOKEN
        launch.launch_id = lifecycle.launch_id_of(launch.plan)
        launch.assessment.ready = admitted[0]
        launch.profiles, launch.hook_deployments = (), ()
        launch.token_factory, launch.token_factory_code_hash = None, None
        launch.assessment.code = None if admitted[0] else "PLAN_NOT_READY"
        launch.assessment.reason = None if admitted[0] else "offline fixture refused"
        preparations.append((plan, options, launch))
        return launch

    monkeypatch.setattr(example, "prepare_and_plan_lifecycle_launch", prepare)
    wallet_factory = Mock(wraps=LocalSigningWallet)
    sign, send = Mock(side_effect=no_call), Mock(side_effect=no_call)
    monkeypatch.setattr(example, "LocalSigningWallet", wallet_factory)
    monkeypatch.setattr(LocalSigningWallet, "sign_typed_data", sign)
    monkeypatch.setattr(LocalSigningWallet, "send_transaction", send)
    for name in ("api_client", "stage_metadata", "execute_plan", "verify_chain", "publish_activation"):
        monkeypatch.setattr(example, name, no_call)
    return SimpleNamespace(preparations=preparations, admitted=admitted, wallet_factory=wallet_factory,
                           sign=sign, send=send, image=example.load_example_image(path)[1])


def invoke_case(filename, argv, monkeypatch):
    script = str(Path(example.__file__).with_name(filename))
    monkeypatch.setattr(sys, "argv", [script, *argv])
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(script, run_name="__main__")
    return exit_status.value.code, script


@pytest.mark.parametrize("filename", CASES)
def test_default_case_dry_run_saves_final_review_without_wallet_or_writes(runner_context, filename, tmp_path, monkeypatch, capsys):
    status, script = invoke_case(filename, [], monkeypatch)
    assert status == 0
    runner_context.wallet_factory.assert_not_called()
    runner_context.sign.assert_not_called()
    runner_context.send.assert_not_called()
    output = next((tmp_path / "launch-results").iterdir())
    result = json.loads((output / "result.json").read_text())
    review = json.loads((output / "economic-review.json").read_text())
    run = json.loads((output / "run.json").read_text())
    assert result["status"] == "passed" and result["stage"] == "dry-run-complete"
    assert result["execution"] == "dry-run" and result["chain"]["status"] == "planned"
    assert result["chain"]["transactions"] == [] and result["api"]["status"] == "not-run"
    assert run["executionMode"] == "dry-run"
    assert json.loads((output / "receipts.json").read_text()) == []
    assert not (output / "economic-admission.json").exists()
    assert not (output / "recovery.private.json").exists()
    assert not list(output.glob("signed-*.private.json"))
    assert len(runner_context.preparations) == 1
    draft, options, launch = runner_context.preparations[0]
    assert options["mode"] == run["case"]["mode"] and options["account"] == draft.creator
    assert options["buy_slippage_bps"] == example.BUY_SLIPPAGE_BPS
    assert all(buy.min_token_out > original.min_token_out for buy, original in zip(launch.plan.buys, draft.buys))
    assert json.loads((output / "plan.json").read_text()) == lifecycle.launch_plan_to_dict(launch.plan)
    assert json.loads((output / "assessment.json").read_text())["ready"] is True
    assert review["reviewHash"] == example.economic_review_hash(review["review"])
    assert review["review"]["image"] == example.portable(runner_context.image)
    assert len(review["review"]["openingBuys"]) == len(launch.plan.buys)
    assert review["review"]["funding"] == review["review"]["plan"]["funding"]
    text = capsys.readouterr()
    assert text.err == ""
    assert text.out.splitlines()[0] == "DRY RUN — no transactions signed or broadcast."
    assert review["reviewHash"] in text.out and '"openingBuys"' in text.out
    assert text.out.splitlines()[-1] == shlex.join([sys.executable, script, "--broadcast"])


@pytest.mark.parametrize("filename,select_staged", [
    *((filename, False) for filename in CASES), ("launch_erc20_abyss.py", True),
])
def test_broadcast_case_routes_all_execution_phases_without_hash_input(runner_context, filename, select_staged, tmp_path, monkeypatch, capsys):
    phases, api, recovery, receipt, references = [], object(), {}, {"transactionHash": TX_HASH}, ()
    monkeypatch.setattr(example, "api_client", lambda url: api)
    requested_cases = []
    admit = example.assess_plan
    def admission(client, plan, case, artifacts, cancellation):
        requested_cases.append((case, case["mode"]))
        launch = admit(client, plan, case, artifacts, cancellation)
        if select_staged:
            launch.mode = "staged"
        return launch
    monkeypatch.setattr(example, "assess_plan", admission)

    def stage(client, actual_api, plan, artifacts, cancellation, **options):
        phases.append("stage")
        evidence = json.loads((artifacts.directory / "economic-admission.json").read_text())
        assert evidence["authorization"] == "--broadcast"
        assert evidence["reviewHash"] == options["economic_review_hash"]
        assert callable(options["wallet"].signature_guard)
        assert actual_api is api and options["mode"] == runner_context.preparations[0][2].mode
        assert options["image"][1] == runner_context.image
        assert options["economic_review"]["planHash"] == lifecycle.hash_launch_plan(plan)
        return recovery

    def execute(client, launch, plan, artifacts, cancellation, **options):
        phases.append("execute")
        assert options["api"] is api and options["recovery"] is recovery
        assert plan == launch.plan and callable(options["wallet"].signature_guard)
        return receipt, [TX_HASH], references

    def verify(client, plan, case, predicted, actual_receipt, hashes, actual_references, artifacts, cancellation):
        phases.append("verify")
        assert predicted == TOKEN and actual_receipt is receipt and hashes == [TX_HASH]
        assert actual_references is references
        assert case["mode"] == runner_context.preparations[0][2].mode
        artifacts.result["chain"]["status"] = "passed"

    def publish(client, actual_api, actual_recovery, plan, predicted, actual_receipt, actual_references, timeout, artifacts, cancellation):
        phases.append("publish")
        assert actual_api is api and actual_recovery is recovery and actual_receipt is receipt
        assert actual_references is references
        artifacts.result["api"]["status"] = "passed"

    monkeypatch.setattr(example, "stage_metadata", stage)
    monkeypatch.setattr(example, "execute_plan", execute)
    monkeypatch.setattr(example, "verify_chain", verify)
    monkeypatch.setattr(example, "publish_activation", publish)
    status, _ = invoke_case(filename, ["--broadcast"], monkeypatch)
    assert status == 0 and phases == ["stage", "execute", "verify", "publish"]
    runner_context.wallet_factory.assert_called_once()
    output = next((tmp_path / "launch-results").iterdir())
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "passed" and result["execution"] == "executed"
    assert result["chain"]["status"] == "passed" and result["api"]["status"] == "passed"
    assert json.loads((output / "run.json").read_text())["executionMode"] == "broadcast"
    assert requested_cases[0][0]["mode"] == requested_cases[0][1]
    if select_staged:
        assert requested_cases[0][1] == "atomic" and runner_context.preparations[0][2].mode == "staged"
    text = capsys.readouterr()
    assert text.err == "" and "DRY RUN" not in text.out
    assert '"reviewHash"' in text.out and "end-to-end" in text.out


@pytest.mark.parametrize("argv", [
    ["--unknown"], ["--dry-run"], ["--broadcast=true"], ["--broadcast", "extra"],
    ["--broadcast", "--broadcast"], ["--broadcast", "--resume", "saved"],
    ["--resume"], ["--resume", ""], ["--resume", "  "], ["--resume", "--broadcast"],
    ["--resume", "saved", "--broadcast"], ["--resume", "saved", "extra"], ["extra"],
])
def test_malformed_modes_fail_fast_with_usage(tmp_path, monkeypatch, capsys, argv):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["examples/launch_erc20_v4.py"])
    for name in ("Artifacts", "example_configuration", "LocalSigningWallet", "run", "resume_launch_example"):
        monkeypatch.setattr(example, name, no_call)
    assert example.run_launch_example(case_definition(), argv=argv) == 2
    text = capsys.readouterr()
    assert text.out == "" and "Usage:" in text.err
    assert "--broadcast" in text.err and "--resume <dir>" in text.err
    assert not (tmp_path / "launch-results").exists()


def test_resume_retains_existing_continuation_route(tmp_path, monkeypatch, capsys):
    directory, calls = str(tmp_path / "saved launch"), []

    def resume(recovery_dir, cancellation):
        calls.append(recovery_dir)
        return "Original launch resumed"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(example, "resume_launch_example", resume)
    monkeypatch.setattr(example, "run", no_call)
    monkeypatch.setattr(example, "Artifacts", no_call)
    assert example.run_launch_example(case_definition(), argv=["--resume", directory]) == 0
    assert calls == [directory] and not (tmp_path / "launch-results").exists()
    text = capsys.readouterr()
    assert text.err == "" and "Original launch resumed" in text.out
    assert "DRY RUN" not in text.out and "--broadcast" not in text.out


@pytest.mark.parametrize("argv", [[], ["--broadcast"]])
def test_modes_preserve_planner_refusal_before_wallet_creation(runner_context, tmp_path, monkeypatch, argv):
    runner_context.admitted[0] = False
    assert invoke_case(CASES[0], argv, monkeypatch)[0] == 1
    runner_context.wallet_factory.assert_not_called()
    runner_context.sign.assert_not_called()
    runner_context.send.assert_not_called()
    output = next((tmp_path / "launch-results").iterdir())
    result = json.loads((output / "result.json").read_text())
    assert result["error"]["code"] == "PLAN_NOT_READY"
    assert result["chain"]["transactions"] == []
    assert not (output / "economic-admission.json").exists()


def test_internal_runner_also_defaults_to_unsigned_planning(runner_context, tmp_path):
    case = runpy.run_path(str(Path(example.__file__).with_name(CASES[0])))["CASE"]
    artifacts = Artifacts(tmp_path / "direct", case["id"])
    assert example.run(case, artifacts, example.Cancellation()) == "Dry-run launch plan and economic review saved"
    artifacts.finish()
    assert artifacts.result["execution"] == "dry-run" and artifacts.result["chain"]["status"] == "planned"
    runner_context.wallet_factory.assert_not_called()
    runner_context.sign.assert_not_called()
    runner_context.send.assert_not_called()


@pytest.mark.parametrize("script", ["examples/launch_erc20_v4.py", "examples with spaces/launch_erc20_v4.py"])
def test_dry_run_command_preserves_case_path_and_shell_quoting(runner_context, monkeypatch, capsys, script):
    monkeypatch.setattr(sys, "argv", [script])
    assert example.run_launch_example(case_definition()) == 0
    assert capsys.readouterr().out.splitlines()[-1] == shlex.join([sys.executable, script, "--broadcast"])


def test_public_broadcast_evidence_does_not_weaken_authorization_redaction():
    redactor = example.Redactor()
    evidence = {"authorization": "--broadcast", "reviewHash": "0x" + "ab" * 32, "acceptedAt": 1}
    document = {"admission": evidence, "authorization": {"Bearer": "private-credential"},
                "signature": "0x" + "cd" * 65}
    redactor.learn(document)
    public = redactor(document)
    assert public["admission"] == evidence
    assert public["authorization"] == "[REDACTED]" and public["signature"] == "[REDACTED]"
    assert "--broadcast" not in redactor.secrets
    assert redactor({"authorization": "--broadcast"})["authorization"] == "[REDACTED]"


@pytest.mark.parametrize("changes", [
    {"authorization": "Bearer private-credential"}, {"reviewHash": "invalid"},
    {"acceptedAt": True}, {"acceptedAt": 0}, {"acceptedAt": "1"}, {"signature": "0x" + "cd" * 65},
])
def test_only_exact_broadcast_provenance_has_public_authorization(changes):
    evidence = {"authorization": "--broadcast", "reviewHash": "0x" + "ab" * 32, "acceptedAt": 1, **changes}
    public = example.Redactor()(evidence)
    assert public["authorization"] == "[REDACTED]"
    if "signature" in public:
        assert public["signature"] == "[REDACTED]"
