"""Deterministic boundary tests for the guarded launch-submit example."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from web3 import Web3

from black_market_sdk import LaunchPublishPending, LaunchUploadError
from black_market_sdk.unified import ABYSS_POOL_TYPE
from examples import launch_submit as submit
from examples.launch_matrix import iter_launch_scenarios


CHAIN_ID = 4663
LAUNCHER = Web3.to_checksum_address("0x1000000000000000000000000000000000000001")
REGISTRY = Web3.to_checksum_address("0x2000000000000000000000000000000000000002")
ADAPTER = Web3.to_checksum_address("0x3000000000000000000000000000000000000003")
FACTORY = Web3.to_checksum_address("0x4000000000000000000000000000000000000004")
PAIRED = Web3.to_checksum_address("0x5000000000000000000000000000000000000005")
ACCOUNT = Web3.to_checksum_address("0x6000000000000000000000000000000000000006")
TOKEN = Web3.to_checksum_address("0x7000000000000000000000000000000000000007")
MARKET = Web3.to_checksum_address("0x8000000000000000000000000000000000000008")
REWARDS = Web3.to_checksum_address("0x9000000000000000000000000000000000000009")
SPLITTER = Web3.to_checksum_address("0xa00000000000000000000000000000000000000a")
FEED = Web3.to_checksum_address("0xc00000000000000000000000000000000000000c")
FEE_CLAIMER = Web3.to_checksum_address("0xb00000000000000000000000000000000000000b")
LAUNCH_HASH = "0x" + "ab" * 32

@pytest.fixture
def abyss_scenario():
    return next(
        scenario
        for scenario in iter_launch_scenarios()
        if scenario.scenario_id == "abyss.standard.standard-oracle.burnable.owner-only"
    )


class ReadCall:
    def __init__(self, value: Any) -> None:
        self.value = value

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.value


class ApprovalCall(ReadCall):
    def __init__(self, token: "TokenContract", amount: int) -> None:
        super().__init__(True)
        self.token = token
        self.amount = amount

    def build_transaction(self, fields: dict[str, Any]) -> dict[str, Any]:
        self.token.approvals.append(self.amount)
        return {
            **fields,
            "to": PAIRED,
            "data": b"approve",
            "value": 0,
            "approval_amount": self.amount,
        }


class LauncherContract:
    def __init__(self) -> None:
        self.functions = SimpleNamespace(poolRegistry=lambda: ReadCall(REGISTRY))


class RegistryContract:
    def __init__(self, *, disabled: bool) -> None:
        self.functions = SimpleNamespace(
            poolTypes=lambda _pool_type: ReadCall((ADAPTER, disabled)),
            adapter=lambda _pool_type: ReadCall(ADAPTER),
        )


class AdapterContract:
    def __init__(self) -> None:
        self.functions = SimpleNamespace(
            poolType=lambda: ReadCall(ABYSS_POOL_TYPE),
            abyssFactory=lambda: ReadCall(FACTORY),
            launchFee=lambda: ReadCall(7),
            wrappedNative=lambda: ReadCall(PAIRED),
        )


class FactoryContract:
    def __init__(self) -> None:
        self.functions = SimpleNamespace(
            feeAmountTickSpacing=lambda _fee: ReadCall(60),
            oracleConfigs=lambda _config: ReadCall((17, 4096)),
        )


class TokenContract:
    def __init__(self, allowance: int, balance: int = 10_000) -> None:
        self.allowance_value = allowance
        self.balance_value = balance
        self.approvals: list[int] = []
        self.deposits: list[int | None] = []
        self.pending_wraps: list[int] = []
        self.functions = SimpleNamespace(
            balanceOf=lambda _owner: ReadCall(self.balance_value),
            allowance=lambda _owner, _spender: ReadCall(self.allowance_value),
            approve=lambda _spender, amount: ApprovalCall(self, amount),
            deposit=lambda: DepositCall(self),
        )


class DepositCall(ReadCall):
    def __init__(self, token: "TokenContract") -> None:
        super().__init__(True)
        self.token = token

    def build_transaction(self, fields: dict[str, Any]) -> dict[str, Any]:
        self.token.deposits.append(fields.get("value"))
        self.token.pending_wraps.append(fields.get("value"))
        return {
            **fields,
            "to": PAIRED,
            "data": b"deposit",
            "value": fields.get("value", 0),
            "wrap_amount": fields.get("value"),
        }


class FeedContract:
    def __init__(self, *, answer: int = 2_500 * 10**8, decimals: int = 8) -> None:
        self.functions = SimpleNamespace(
            latestRoundData=lambda: ReadCall((0, answer, 0, 0, 0)),
            decimals=lambda: ReadCall(decimals),
        )


class FakeEth:
    def __init__(
        self,
        *,
        chain_id: int = CHAIN_ID,
        disabled: bool = False,
        allowance: int = 0,
        native_balance: int = 1_000_000,
        feed: FeedContract | None = None,
    ) -> None:
        self.chain_id = chain_id
        self.gas_price = 2
        self.native_balance = native_balance
        self.operations: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.sent: list[bytes] = []
        self.pending_wraps: list[int] = []
        self.token = TokenContract(allowance)
        self.feed = feed if feed is not None else FeedContract()
        self.contracts = {
            LAUNCHER.lower(): LauncherContract(),
            REGISTRY.lower(): RegistryContract(disabled=disabled),
            ADAPTER.lower(): AdapterContract(),
            FACTORY.lower(): FactoryContract(),
            PAIRED.lower(): self.token,
            FEED.lower(): self.feed,
        }

    def contract(self, *, address: str, abi: object) -> object:
        del abi
        return self.contracts[address.lower()]

    def get_code(self, address: str) -> bytes:
        assert address == LAUNCHER
        return b"\x01"

    def get_balance(self, address: str) -> int:
        assert address == ACCOUNT
        return self.native_balance


    def get_transaction_count(self, address: str, block_identifier: str) -> int:
        assert address == ACCOUNT
        assert block_identifier == "pending"
        return len(self.sent)

    def estimate_gas(self, transaction: dict[str, Any]) -> int:
        assert transaction["from"] == ACCOUNT
        return 100

    def call(self, transaction: dict[str, Any]) -> bytes:
        assert transaction["from"] == ACCOUNT
        self.calls.append(dict(transaction))
        self.operations.append("simulate")
        return b"quoted-launch-receipt"

    def send_raw_transaction(self, raw: bytes) -> bytes:
        self.sent.append(raw)
        self.operations.append("broadcast")
        return bytes([len(self.sent)]) * 32

    def wait_for_transaction_receipt(self, _transaction_hash: str, *, timeout: float) -> dict[str, int]:
        assert timeout == 180.0
        self.operations.append("receipt")
        # A confirmed WETH deposit credits the wrapped balance like mainnet.
        if self.token.pending_wraps:
            self.token.balance_value += self.token.pending_wraps.pop(0)
        return {"status": 1}


class FakeWeb3:
    def __init__(self, **kwargs: Any) -> None:
        self.eth = FakeEth(**kwargs)


class FakeAccount:
    address = ACCOUNT

    def __init__(self) -> None:
        self.signed_transactions: list[dict[str, Any]] = []

    def sign_message(self, _message: object) -> object:
        return SimpleNamespace(signature=b"\x11" * 65)

    def sign_transaction(self, transaction: dict[str, Any]) -> object:
        self.signed_transactions.append(dict(transaction))
        return SimpleNamespace(raw_transaction=b"signed" + bytes([len(self.signed_transactions)]))


def _wallet_factory(w3: FakeWeb3, account: FakeAccount, *, rpc_url: str | None = "https://rpc.example.invalid"):
    def factory(*, private_key: str, chain_id: int, rpc_url: str):
        assert private_key == "0x" + "11" * 32
        assert chain_id == CHAIN_ID
        assert rpc_url == expected_rpc_url
        return w3, account

    expected_rpc_url = rpc_url
    return factory


def _execute_arguments(scenario_id: str, *extra: str) -> list[str]:
    return [
        "--execute",
        "--case",
        scenario_id,
        "--private-key",
        "0x" + "11" * 32,
        "--rpc-url",
        "https://rpc.example.invalid",
        "--chain-id",
        str(CHAIN_ID),
        "--launcher",
        LAUNCHER,
        "--paired-token",
        PAIRED,
        "--paired-token-usd-price-x18",
        "2500000000000000000000",
        *extra,
    ]


def _receipt_details(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "token": TOKEN,
        "market": MARKET,
        "token_id": 1,
        "liquidity_launched_token_amount": 900,
        "liquidity_paired_token_amount": 0,
        "initial_buy_paired_token_amount": 0,
        "initial_buy_launched_token_amount": 0,
        "rewards": REWARDS,
        "splitter": SPLITTER,
        "fee_claimer": FEE_CLAIMER,
    }


def test_offline_preview_never_constructs_network_clients(abyss_scenario):
    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("offline preview must not construct a client")

    summaries = submit.run_cli(
        ["--case", abyss_scenario.scenario_id],
        wallet_web3_factory=forbidden,
        api_client_factory=forbidden,
    )

    assert summaries[0]["mode"] == "offline"
    assert summaries[0]["scenario_id"] == abyss_scenario.scenario_id


class FakeApiClient:
    """Minimal ready-to-publish API double for the always-on lifecycle."""

    def create_launch_upload_session(self, request: object, idempotency_key: str) -> dict[str, Any]:
        return {
            "sessionId": "session-1",
            "capability": "private-capability",
            "status": "ready_to_launch",
        }

    def put_launch_image(self, upload: object, body: bytes) -> None:
        raise AssertionError("no image was supplied; no upload occurs")

    def complete_launch_upload(self, chain_id: int, session_id: str, capability: str) -> dict[str, str]:
        raise AssertionError("no image was supplied; no completion occurs")

    def get_launch_upload_session(self, chain_id: int, session_id: str, capability: str) -> dict[str, str]:
        return {"sessionId": "session-1", "status": "ready_to_launch"}

    def publish_launch_upload_session(
        self, chain_id: int, session_id: str, capability: str, body: object
    ) -> dict[str, str]:
        return {"sessionId": "session-1", "status": "published"}


def test_help_exposes_current_flags_and_drops_obsolete_ones():
    help_text = submit.build_parser().format_help()
    for flag in ("--execute", "--case", "--fee-pips", "--initial-buy", "--resume-publish"):
        assert flag in help_text, flag
    for flag in ("--submit", "--scenario", "--initial-buy-amount", "--fee "):
        assert flag not in help_text, flag
    # The lifecycle is no longer opt-in: --api is gone, only tuning options remain.
    assert "--api " not in help_text


def test_execute_rejects_all_and_requires_exactly_one_case(abyss_scenario):
    with pytest.raises(SystemExit):
        submit.build_parser().parse_args(
            ["--execute", "--case", abyss_scenario.scenario_id, "--all"]
        )
    with pytest.raises(submit.LaunchSubmissionError, match="--all"):
        submit._validate_cli_args(submit.build_parser().parse_args(["--execute", "--all"]))
    with pytest.raises(submit.LaunchSubmissionError, match="--case"):
        submit._validate_cli_args(submit.build_parser().parse_args(["--execute"]))


def test_execute_reads_private_key_automatically_from_private_key_env(abyss_scenario, monkeypatch):
    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("the key gate must run before client construction")

    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    with pytest.raises(submit.LaunchSubmissionError, match="PRIVATE_KEY"):
        submit.run_cli(
            [
                "--execute",
                "--case",
                abyss_scenario.scenario_id,
                "--rpc-url",
                "https://rpc.example.invalid",
                "--paired-token-usd-price-x18",
                "2500000000000000000000",
                "--launcher",
                LAUNCHER,
                "--paired-token",
                PAIRED,
            ],
            wallet_web3_factory=forbidden,
        )

    monkeypatch.setenv("PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)
    w3 = FakeWeb3()
    account = FakeAccount()
    summaries = submit.run_cli(
        [
            "--execute",
            "--case",
            abyss_scenario.scenario_id,
            "--rpc-url",
            "https://rpc.example.invalid",
            "--paired-token-usd-price-x18",
            "2500000000000000000000",
            "--launcher",
            LAUNCHER,
            "--paired-token",
            PAIRED,
        ],
        wallet_web3_factory=_wallet_factory(w3, account),
        api_client_factory=lambda _config: FakeApiClient(),
    )

    assert summaries[0]["mode"] == "submitted"
    assert summaries[0]["api"] == {"status": "published"}


def test_live_chain_and_disabled_route_are_rejected_without_broadcast(abyss_scenario):
    account = FakeAccount()
    wrong_chain = FakeWeb3(chain_id=1)
    with pytest.raises(submit.LaunchSubmissionError, match="chain ID"):
        submit.run_cli(
            _execute_arguments(abyss_scenario.scenario_id),
            wallet_web3_factory=_wallet_factory(wrong_chain, account),
            api_client_factory=lambda _config: FakeApiClient(),
        )
    assert wrong_chain.eth.sent == []

    disabled_route = FakeWeb3(disabled=True)
    with pytest.raises(submit.LaunchSubmissionError, match="disabled"):
        submit.run_cli(
            _execute_arguments(abyss_scenario.scenario_id),
            wallet_web3_factory=_wallet_factory(disabled_route, account),
            api_client_factory=lambda _config: FakeApiClient(),
        )
    assert disabled_route.eth.sent == []


def test_execute_uses_exact_allowance_quote_then_final_simulation(abyss_scenario, monkeypatch):
    w3 = FakeWeb3()
    account = FakeAccount()
    w3.eth.token.balance_value = 30 * 10**18
    monkeypatch.setattr(
        submit,
        "decode_unified_launch_receipt",
        lambda _payload: SimpleNamespace(initial_buy_launched_token_amount=80),
    )
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)

    summaries = submit.run_cli(
        _execute_arguments(abyss_scenario.scenario_id, "--initial-buy", "25"),
        wallet_web3_factory=_wallet_factory(w3, account),
        api_client_factory=lambda _config: FakeApiClient(),
    )

    assert w3.eth.token.approvals == [25 * 10**18]
    assert [
        transaction.get("approval_amount") for transaction in account.signed_transactions
    ] == [25 * 10**18, None]
    assert len(w3.eth.calls) == 2  # quote (minimum zero) followed by final simulation
    assert summaries[0]["fees"]["adapter_launch_fee"] == 7
    assert summaries[0]["fees"]["transaction_value"] == 7


def test_decimal_initial_buy_parses_to_base_units(abyss_scenario, monkeypatch):
    w3 = FakeWeb3()
    account = FakeAccount()
    w3.eth.token.balance_value = 10 * 10**18
    monkeypatch.setattr(
        submit,
        "decode_unified_launch_receipt",
        lambda _payload: SimpleNamespace(initial_buy_launched_token_amount=80),
    )
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)

    submit.run_cli(
        _execute_arguments(abyss_scenario.scenario_id, "--initial-buy", "0.0025"),
        wallet_web3_factory=_wallet_factory(w3, account),
        api_client_factory=lambda _config: FakeApiClient(),
    )

    # '0.0025' at 18 decimals is 25 * 10**14 base units: the exact approval
    # amount observes the parsed decimal input.
    assert w3.eth.token.approvals == [25 * 10**14]


@pytest.mark.parametrize("amount", ["abc", "-1", "0.0001", "1e3", ""])
def test_invalid_decimal_initial_buy_is_rejected(abyss_scenario, amount):
    w3 = FakeWeb3()
    account = FakeAccount()

    with pytest.raises(submit.LaunchSubmissionError, match="initial-buy"):
        submit.run_cli(
            _execute_arguments(
                abyss_scenario.scenario_id,
                "--initial-buy",
                amount,
                "--paired-token-decimals",
                "3",
            ),
            wallet_web3_factory=_wallet_factory(w3, account),
            api_client_factory=lambda _config: FakeApiClient(),
        )
    assert w3.eth.sent == []


class ApiStub:
    def __init__(
        self,
        operations: list[str],
        *,
        pending: bool = False,
        upload_error: bool = False,
        publish_final: str | None = None,
    ) -> None:
        self.operations = operations
        self.pending = pending
        self.upload_error = upload_error
        self.publish_final = publish_final
        self.request = None

    def create_launch_upload_session(self, request: object, _idempotency_key: str) -> dict[str, Any]:
        self.operations.append("api-create")
        self.request = request
        return {
            "sessionId": "session-1",
            "capability": "private-capability",
            "status": "awaiting_upload",
            "upload": {
                "url": "https://uploads.example.invalid/session-1?private=query",
                "headers": {"content-type": "image/png", "if-none-match": "*"},
            },
        }

    def put_launch_image(self, upload: object, body: bytes) -> None:
        assert isinstance(upload, dict)
        assert body == b"image"
        self.operations.append("api-put")
        if self.upload_error:
            raise LaunchUploadError("safe test upload error", status=503)

    def complete_launch_upload(self, _chain_id: int, _session_id: str, _capability: str) -> dict[str, str]:
        self.operations.append("api-complete")
        return {"sessionId": "session-1", "status": "processing"}

    def get_launch_upload_session(self, _chain_id: int, _session_id: str, _capability: str) -> dict[str, str]:
        self.operations.append("api-get")
        if self.publish_final is not None and "api-publish" in self.operations:
            return {"sessionId": "session-1", "status": self.publish_final, "token": TOKEN}
        return {"sessionId": "session-1", "status": "ready_to_launch"}

    def publish_launch_upload_session(
        self, _chain_id: int, _session_id: str, _capability: str, _body: object
    ) -> dict[str, str]:
        self.operations.append("api-publish")
        if self.pending:
            raise LaunchPublishPending(
                {"sessionId": "session-1", "status": "awaiting_indexer"}, 1_000
            )
        return {"sessionId": "session-1", "status": "published"}


class ResumePublishStub:
    def __init__(self) -> None:
        self.operations: list[str] = []

    def publish_launch_upload_session(
        self, _chain_id: int, _session_id: str, _capability: str, _body: object
    ) -> dict[str, str]:
        self.operations.append("api-publish")
        raise LaunchPublishPending({"sessionId": "session-1", "status": "awaiting_indexer"}, 1_000)

    def get_launch_upload_session(
        self, _chain_id: int, _session_id: str, _capability: str
    ) -> dict[str, str]:
        self.operations.append("api-get")
        return {"sessionId": "session-1", "status": "optimistic", "token": TOKEN}


def test_api_upload_precedes_launch_and_publish_follows_confirmation(
    abyss_scenario, monkeypatch, tmp_path: Path
):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    state_path = tmp_path / "private-state.json"
    w3 = FakeWeb3()
    account = FakeAccount()
    api = ApiStub(w3.eth.operations)
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)

    summaries = submit.run_cli(
        _execute_arguments(
            abyss_scenario.scenario_id,
            "--image",
            str(image),
            "--name",
            "Exact Name",
            "--symbol",
            "EXACT",
            "--private-state-output",
            str(state_path),
        ),
        wallet_web3_factory=_wallet_factory(w3, account),
        api_client_factory=lambda _config: api,
        sleeper=lambda _delay: None,
    )

    assert api.request.metadata.name == "Exact Name"
    assert api.request.metadata.symbol == "EXACT"
    assert w3.eth.operations == [
        "api-create",
        "api-put",
        "api-complete",
        "api-get",
        "simulate",
        "broadcast",
        "receipt",
        "api-publish",
    ]
    assert summaries[0]["api"] == {"status": "published"}
    assert "capability" not in json.dumps(summaries[0])
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_api_failure_preserves_private_recovery_without_launch(abyss_scenario, tmp_path: Path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    state_path = tmp_path / "private-state.json"
    w3 = FakeWeb3()
    account = FakeAccount()
    api = ApiStub(w3.eth.operations, upload_error=True)

    with pytest.raises(LaunchUploadError):
        submit.run_cli(
            _execute_arguments(
                abyss_scenario.scenario_id,
                "--image",
                str(image),
                "--private-state-output",
                str(state_path),
            ),
            wallet_web3_factory=_wallet_factory(w3, account),
            api_client_factory=lambda _config: api,
        )

    assert w3.eth.sent == []
    assert json.loads(state_path.read_text())["api"]["capability"] == "private-capability"


def test_pending_publish_polls_until_final_and_never_exposes_capability(
    abyss_scenario, monkeypatch, tmp_path: Path
):
    state_path = tmp_path / "private-state.json"
    w3 = FakeWeb3()
    account = FakeAccount()
    api = ApiStub(w3.eth.operations, pending=True, publish_final="optimistic")
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)

    summaries = submit.run_cli(
        _execute_arguments(
            abyss_scenario.scenario_id,
            "--private-state-output",
            str(state_path),
        ),
        wallet_web3_factory=_wallet_factory(w3, account),
        api_client_factory=lambda _config: api,
        sleeper=lambda _delay: None,
    )

    # The pending publish is resumed by the completion poll, not re-published.
    assert w3.eth.operations.count("api-publish") == 1
    assert w3.eth.operations == [
        "api-create",
        "api-get",
        "simulate",
        "broadcast",
        "receipt",
        "api-publish",
        "api-get",
    ]
    assert summaries[0]["api"] == {"status": "optimistic"}
    assert "private-capability" not in json.dumps(summaries[0])
    assert json.loads(state_path.read_text())["stage"] == "api_published"


def test_chainlink_price_and_native_buy_replace_weth_wrap_and_allowance(
    abyss_scenario, monkeypatch
):
    initial_weth_balance = 10**18
    w3 = FakeWeb3(native_balance=30 * 10**18)
    account = FakeAccount()
    w3.eth.token.balance_value = initial_weth_balance
    monkeypatch.setattr(
        submit,
        "decode_unified_launch_receipt",
        lambda _payload: SimpleNamespace(initial_buy_launched_token_amount=80),
    )
    monkeypatch.setattr(submit, "_decode_launch_events", _receipt_details)

    class RecordedAddresses(SimpleNamespace):
        weth = PAIRED
        eth_usd_feed = FEED

    monkeypatch.setattr(submit, "get_addresses", lambda _chain_id: RecordedAddresses())

    summaries = submit.run_cli(
        [
            "--execute",
            "--case",
            abyss_scenario.scenario_id,
            "--private-key",
            "0x" + "11" * 32,
            "--chain-id",
            str(CHAIN_ID),
            "--launcher",
            LAUNCHER,
            "--paired-token",
            PAIRED,
            "--initial-buy",
            "25",
        ],
        wallet_web3_factory=_wallet_factory(w3, account, rpc_url=None),
        api_client_factory=lambda _config: FakeApiClient(),
    )

    # No explicit price was supplied: the Chainlink feed priced the WETH buy.
    # A live Abyss launch paired with the chain WETH maps the decimal
    # --initial-buy onto msg.value: no WETH wrap, no ERC-20 approval or pull,
    # and the single broadcast launch transaction carries the adapter launch
    # fee plus the buy amount.
    assert w3.eth.token.deposits == []
    assert w3.eth.token.approvals == []
    assert [
        (transaction.get("wrap_amount"), transaction.get("approval_amount"))
        for transaction in account.signed_transactions
    ] == [(None, None)]
    assert summaries[0]["fees"]["transaction_value"] == 7 + 25 * 10**18


def test_conflicting_explicit_native_amount_is_rejected_on_abyss_weth(
    abyss_scenario, monkeypatch
):
    w3 = FakeWeb3()
    account = FakeAccount()

    class RecordedAddresses(SimpleNamespace):
        weth = PAIRED

    monkeypatch.setattr(submit, "get_addresses", lambda _chain_id: RecordedAddresses())

    with pytest.raises(submit.LaunchSubmissionError, match="conflicts"):
        submit.run_cli(
            _execute_arguments(
                abyss_scenario.scenario_id,
                "--initial-buy",
                "0.0025",
                "--native-buy-amount",
                "1000",
            ),
            wallet_web3_factory=_wallet_factory(w3, account),
            api_client_factory=lambda _config: FakeApiClient(),
        )
    assert w3.eth.sent == []


def test_resume_publish_polls_without_web3_or_broadcast():
    api = ResumePublishStub()

    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("resume-publish must not construct a Web3 client")

    summaries = submit.run_cli(
        [
            "--resume-publish",
            "--session",
            "session-1",
            "--capability",
            "cap",
            "--transaction",
            LAUNCH_HASH,
            "--chain-id",
            str(CHAIN_ID),
        ],
        api_client_factory=lambda _config: api,
        wallet_web3_factory=forbidden,
    )

    assert summaries[0] == {
        "mode": "resume_publish",
        "transaction_hash": LAUNCH_HASH,
        "session_id": "session-1",
        "token": TOKEN,
        "status": "optimistic",
    }
    assert api.operations == ["api-publish", "api-get"]
    assert "cap" not in json.dumps(summaries[0])
