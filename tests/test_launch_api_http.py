"""Opt-in integration against the real Abyss HTTP router and session database.

Default pytest runs exclude this module's marker. Select launch_api_http to run
it; missing explicit service/key prerequisites fail rather than skip. Select
'launch_api_http and not launch_api_write' for GET-only checks without a key.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import is_address

from black_market_sdk import (
    ROBINHOOD_MAINNET_CHAIN_ID,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchApiError,
    LaunchPlanV1,
    LifecycleTokenConfig,
    LaunchAttributionAuthorization,
    LaunchSessionCreateRequest,
    LaunchSessionMetadata,
    build_launch_attribution_typed_data,
    get_launch_addresses,
)
from examples import smoke_launch as smoke
from _smoke_support import Artifacts

pytestmark = pytest.mark.launch_api_http


@dataclass(frozen=True)
class ApiSettings:
    config: LaunchApiConfig
    chain_id: int
    orchestrator: str


@pytest.fixture(scope="module")
def api_settings() -> ApiSettings:
    base_url = os.environ.get("LAUNCH_API_TEST_URL")
    if not base_url:
        pytest.fail("LAUNCH_API_TEST_URL is required for the explicitly selected HTTP suite", pytrace=False)
    try:
        config = LaunchApiConfig(base_url=base_url, allow_loopback_http=True)
        chain_id = int(os.environ.get("LAUNCH_API_TEST_CHAIN_ID", str(ROBINHOOD_MAINNET_CHAIN_ID)))
    except ValueError as error:
        pytest.fail(f"Invalid HTTP test configuration: {error}", pytrace=False)
    if chain_id <= 0:
        pytest.fail("LAUNCH_API_TEST_CHAIN_ID must be positive", pytrace=False)
    orchestrator = os.environ.get("LAUNCH_API_TEST_ORCHESTRATOR", get_launch_addresses(ROBINHOOD_MAINNET_CHAIN_ID).orchestrator)
    if not is_address(orchestrator) or int(orchestrator, 16) == 0:
        pytest.fail("LAUNCH_API_TEST_ORCHESTRATOR must be a nonzero address", pytrace=False)
    return ApiSettings(config, chain_id, orchestrator)


@pytest.fixture(scope="module")
def api_client(api_settings) -> LaunchApiClient:
    # Exercise the public production HTTP transport, not an injected transport.
    return LaunchApiClient(api_settings.config)


@pytest.fixture(scope="module")
def signed_account(api_settings):
    host = urlsplit(api_settings.config.base_url).hostname
    try:
        loopback = host == "localhost" or (host is not None and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        loopback = False
    if not loopback:
        pytest.fail("Signed HTTP tests require a loopback test service, never production", pytrace=False)
    private_key = os.environ.get("LAUNCH_API_TEST_PRIVATE_KEY")
    if not private_key:
        pytest.fail("LAUNCH_API_TEST_PRIVATE_KEY is required for explicitly selected signed HTTP tests", pytrace=False)
    try:
        return Account.from_key(private_key)
    except (ValueError, TypeError):
        pytest.fail("LAUNCH_API_TEST_PRIVATE_KEY must identify a disposable test wallet", pytrace=False)


def signed_request(settings, account, key, metadata, *, wallet=None, orchestrator=None, deadline=None):
    nonce = "0x" + secrets.token_hex(32)
    deadline = int(time.time()) + 600 if deadline is None else deadline
    wallet = account.address if wallet is None else wallet
    typed_data = build_launch_attribution_typed_data(
        chain_id=settings.chain_id,
        verifying_contract=settings.orchestrator if orchestrator is None else orchestrator,
        wallet=wallet, metadata=metadata, idempotency_key=key, nonce=nonce, deadline=deadline,
    )
    signature = "0x" + bytes(account.sign_message(encode_typed_data(full_message=typed_data)).signature).hex()
    return LaunchSessionCreateRequest(
        chain_id=settings.chain_id, wallet=wallet, metadata=metadata,
        authorization=LaunchAttributionAuthorization(nonce=nonce, deadline=deadline, signature=signature),
    )


@pytest.fixture(scope="module")
def metadata_session(api_settings, api_client, signed_account):
    key = "python-sdk-http-" + secrets.token_hex(16)
    metadata = LaunchSessionMetadata(
        name="  Cafe\u0301 SDK  ", symbol=" PYHTTP ",
        description="  Real HTTP metadata session; no on-chain launch or image upload.  ",
        website_url="https://abyss.trading/",
    )
    request = signed_request(api_settings, signed_account, key, metadata)
    created = api_client.create_launch_upload_session(request, key)
    return key, request, created


@pytest.mark.parametrize("capability,status,code", [
    ("too-short", 401, "INVALID_CAPABILITY"),
    ("x" * 48, 404, "NOT_FOUND"),
])
def test_session_get_enforces_capability_and_not_found_over_http(api_settings, api_client, capability, status, code):
    session_id = "lus_" + secrets.token_hex(16)
    with pytest.raises(LaunchApiError) as failure:
        api_client.get_launch_upload_session(api_settings.chain_id, session_id, capability)
    assert (failure.value.status, failure.value.code) == (status, code)
    assert failure.value.retryable is False


@pytest.mark.launch_api_write
def test_signed_metadata_only_session_is_ready_and_normalized(api_settings, signed_account, metadata_session):
    _, _, created = metadata_session
    assert created["chainId"] == api_settings.chain_id
    assert created["wallet"].lower() == signed_account.address.lower()
    assert created["status"] == "ready_to_launch"
    assert created["canonicalStatus"] == "pending"
    assert created["image"] is None
    assert created["transactionHash"] is None
    assert created["token"] is None
    assert created["metadata"]["name"] == "Café SDK"
    assert created["metadata"]["symbol"] == "PYHTTP"
    assert created["metadata"]["description"] == "Real HTTP metadata session; no on-chain launch or image upload."
    assert created["metadata"]["websiteUrl"] == "https://abyss.trading/"
    assert isinstance(created["capability"], str) and len(created["capability"]) >= 32
    assert "upload" not in created and "imageUrl" not in created
    assert int(created["sessionExpiresAt"]) > int(created["createdAt"])


@pytest.mark.launch_api_write
def test_session_recovery_reads_persisted_metadata_without_reissuing_secrets(api_settings, api_client, metadata_session):
    _, _, created = metadata_session
    recovered = api_client.get_launch_upload_session(api_settings.chain_id, created["sessionId"], created["capability"])
    for field in ("sessionId", "wallet", "status", "metadata", "image", "createdAt", "sessionExpiresAt"):
        assert recovered[field] == created[field]
    assert "capability" not in recovered and "upload" not in recovered


@pytest.mark.launch_api_write
def test_identical_signed_request_replays_same_session_without_capability(api_client, metadata_session):
    key, request, created = metadata_session
    # Reuse the exact signed body, including its wall-clock deadline.
    replay = api_client.create_launch_upload_session(request, key)
    assert replay["sessionId"] == created["sessionId"]
    assert replay["metadata"] == created["metadata"]
    assert "capability" not in replay and "upload" not in replay


@pytest.mark.launch_api_write
def test_same_key_with_new_valid_signed_metadata_conflicts(api_settings, api_client, signed_account, metadata_session):
    key, request, created = metadata_session
    changed = signed_request(api_settings, signed_account, key, replace(request.metadata, description="Different signed document"))
    with pytest.raises(LaunchApiError) as failure:
        api_client.create_launch_upload_session(changed, key)
    assert (failure.value.status, failure.value.code) == (409, "IDEMPOTENCY_CONFLICT")
    recovered = api_client.get_launch_upload_session(api_settings.chain_id, created["sessionId"], created["capability"])
    assert recovered["metadata"] == created["metadata"]


@pytest.mark.launch_api_write
@pytest.mark.parametrize("capability", ["malformed", "x" * 48])
def test_existing_session_rejects_malformed_and_wrong_capabilities(api_settings, api_client, metadata_session, capability):
    _, _, created = metadata_session
    with pytest.raises(LaunchApiError) as failure:
        api_client.get_launch_upload_session(api_settings.chain_id, created["sessionId"], capability)
    assert (failure.value.status, failure.value.code) == (401, "INVALID_CAPABILITY")


@pytest.mark.launch_api_write
@pytest.mark.parametrize("boundary,status,code", [
    ("wallet", 403, "AUTH_FORBIDDEN"),
    ("orchestrator", 403, "AUTH_FORBIDDEN"),
    ("expired", 401, "AUTH_INVALID"),
    ("signature", 401, "AUTH_INVALID"),
    ("metadata", 403, "AUTH_FORBIDDEN"),
])
def test_real_signature_domain_deadline_and_metadata_authorization_boundaries(api_settings, api_client, signed_account, boundary, status, code):
    key = "python-sdk-rejection-" + secrets.token_hex(16)
    metadata = LaunchSessionMetadata(name="Authorization Boundary", symbol="AUTH", description="Bound metadata document")
    options = {}
    if boundary == "wallet":
        options["wallet"] = Account.create().address
    elif boundary == "orchestrator":
        options["orchestrator"] = Account.create().address
    elif boundary == "expired":
        options["deadline"] = int(time.time()) - 1
    request = signed_request(api_settings, signed_account, key, metadata, **options)
    if boundary == "signature":
        request = replace(request, authorization=replace(request.authorization, signature="0x" + "00" * 65))
    elif boundary == "metadata":
        request = replace(request, metadata=replace(metadata, description="Unsigned substituted metadata"))
    with pytest.raises(LaunchApiError) as failure:
        api_client.create_launch_upload_session(request, key)
    assert (failure.value.status, failure.value.code) == (status, code)
    assert failure.value.retryable is False


@pytest.mark.launch_api_write
def test_smoke_metadata_authorization_is_accepted_and_recoverable(
    api_settings, api_client, signed_account, tmp_path, monkeypatch,
):
    """Exercise the smoke producer against real API authorization, not a copied deadline."""
    plan = LaunchPlanV1(
        chain_id=api_settings.chain_id, orchestrator=api_settings.orchestrator,
        creator=signed_account.address, nonce=1,
        token=LifecycleTokenConfig(
            0, 0, "Smoke authorization boundary", "SMOKE", 1000000 * 10**18,
            0, "", bytes(32), signed_account.address, False,
        ),
        funding=(), fee_assets=(), markets=(), buys=(),
        deadline=int(time.time()) + 3600, executor_fee_bps=275,
    )

    def sign_typed_data(_client, method, params):
        if method != "eth_signTypedData_v4" or params[0] != signed_account.address:
            raise AssertionError("only disposable-wallet attribution signing is supported")
        return "0x" + bytes(signed_account.sign_message(
            encode_typed_data(full_message=json.loads(params[1])),
        ).signature).hex()

    monkeypatch.setattr(smoke, "rpc", sign_typed_data)
    artifacts = Artifacts(tmp_path / "smoke-authorization", "authorization-boundary")
    recovery = smoke.stage_metadata(None, api_client, plan, artifacts, smoke.Cancellation())
    created = recovery["session"]
    recovered = api_client.get_launch_upload_session(
        plan.chain_id, created["sessionId"], created["capability"],
    )
    assert recovered["status"] == "ready_to_launch"
    assert recovered["wallet"].lower() == plan.creator.lower()
    assert recovered["metadata"]["name"] == plan.token.name
    assert recovered["metadata"]["symbol"] == plan.token.symbol
    assert recovered["token"] is None and recovered["transactionHash"] is None
    assert "capability" not in recovered
