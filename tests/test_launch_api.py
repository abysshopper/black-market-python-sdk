"""Offline contract tests for the opt-in Abyss launch metadata API client."""

from __future__ import annotations

import json
import black_market_sdk.launch_api as launch_api_module


import pytest
from eth_account.messages import encode_typed_data
from eth_utils import keccak

from black_market_sdk.launch_api import (
    LAUNCH_IMAGE_CONTENT_TYPES,
    MAX_LAUNCH_IMAGE_BYTES,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchApiError,
    LaunchApiResponse,
    LaunchAttributionAuthorization,
    LaunchImageUploadRequest,
    LaunchMetadataAuthorization,
    LaunchMetadataEditDocument,
    LaunchPublishPending,
    LaunchSessionCreateRequest,
    LaunchSessionCreateMetadata,
    LaunchSessionDirectUpload,
    LaunchUploadError,
    LaunchSessionImageDescriptor,
    LaunchSessionMetadata,
    LaunchSessionPublishRequest,
    build_launch_attribution_typed_data,
    build_launch_metadata_update_typed_data,
    canonical_launch_metadata_edit_hash,
    canonical_launch_metadata_hash,
    sha256_hex,
)

CHAIN_ID = 4663
WALLET = "0x1000000000000000000000000000000000000001"
TOKEN = "0xAABBccDDeeFF0011223344556677889900AaBbCc"
ORCHESTRATOR = "0x4000000000000000000000000000000000000004"
NONCE = "0x" + "aa" * 32
SIGNATURE = "0x" + "bb" * 65
IMAGE_SHA256 = "0x" + "cc" * 32
TRANSACTION_HASH = "0x" + "dd" * 32


class TransportStub:
    """A deterministic, injectable transport that never opens a socket."""

    def __init__(self, *responses: LaunchApiResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> LaunchApiResponse:
        self.calls.append((method, url, dict(headers), body, timeout))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        return self.responses.pop(0)


def _json_response(
    payload: dict,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> LaunchApiResponse:
    return LaunchApiResponse(
        status=status,
        headers=headers or {"Content-Type": "application/json"},
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def _attribution_authorization() -> LaunchAttributionAuthorization:
    return LaunchAttributionAuthorization(
        nonce=NONCE,
        deadline=1_800_000_000,
        signature=SIGNATURE,
    )


def _metadata_authorization() -> LaunchMetadataAuthorization:
    return LaunchMetadataAuthorization(
        expected_version=7,
        nonce=NONCE,
        deadline=1_800_000_000,
        signature=SIGNATURE,
    )


def test_client_construction_is_explicit_and_makes_no_network_request():
    transport = TransportStub()

    client = LaunchApiClient(LaunchApiConfig(), transport=transport)

    assert client.config.base_url == "https://api.abyss.trading"
    assert client.config.timeout == 15.0
    assert transport.calls == []
    assert LAUNCH_IMAGE_CONTENT_TYPES == ("image/png", "image/jpeg", "image/webp")
    assert MAX_LAUNCH_IMAGE_BYTES == 5 * 1024 * 1024


def test_upload_session_lifecycle_serializes_paths_headers_and_pending_recovery():
    created = {
        "sessionId": "session-1",
        "status": "awaiting_upload",
        "capability": "created-only-capability",
        "upload": {
            "url": "https://uploads.example/session-1?X-Amz-Credential=private",
            "headers": {"content-type": "image/png", "if-none-match": "*"},
            "expiresInSeconds": 300,
        },
        "serverOnly": {"kept": True},
    }
    recovered = {"sessionId": "session-1", "status": "awaiting_upload"}
    renewed = {"sessionId": "session-1", "status": "awaiting_upload", "upload": created["upload"]}
    completed = {"sessionId": "session-1", "status": "ready_to_launch"}
    pending = {"sessionId": "session-1", "status": "awaiting_indexer", "token": None}
    transport = TransportStub(
        _json_response(created, status=201),
        _json_response(recovered),
        _json_response(renewed),
        _json_response(completed),
        _json_response(pending, status=202, headers={"Retry-After": "1.5"}),
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example/"), transport=transport)
    metadata = LaunchSessionCreateMetadata(
        name="Abyss",
        symbol="ABYSS",
        description="Launched through the Abyss protocol.",
        website_url="https://abyss.trading/",
    )
    request = LaunchSessionCreateRequest(
        chain_id=CHAIN_ID,
        wallet=WALLET,
        metadata=metadata,
        image=LaunchSessionImageDescriptor(
            sha256=IMAGE_SHA256,
            content_type="image/png",
            content_length=3,
        ),
        authorization=_attribution_authorization(),
    )

    assert client.create_launch_upload_session(request, "launch-idempotency-key") == created
    assert client.get_launch_upload_session(CHAIN_ID, "session-1", "private-capability") == recovered
    assert client.renew_launch_upload_url(CHAIN_ID, "session-1", "private-capability") == renewed
    assert client.complete_launch_upload(CHAIN_ID, "session-1", "private-capability") == completed
    with pytest.raises(LaunchPublishPending) as pending_error:
        client.publish_launch_upload_session(
            CHAIN_ID,
            "session-1",
            "private-capability",
            LaunchSessionPublishRequest(
                chain_id=CHAIN_ID,
                transaction_hash=TRANSACTION_HASH,
            ),
        )

    assert pending_error.value.session == pending
    assert pending_error.value.retry_after_ms == 1_500
    assert len(transport.calls) == 5

    create_method, create_url, create_headers, create_body, create_timeout = transport.calls[0]
    assert create_method == "POST"
    assert create_url == "https://api.example/api/v1/launch-upload-sessions?chainId=4663"
    assert create_headers == {
        "Content-Type": "application/json",
        "Idempotency-Key": "launch-idempotency-key",
    }
    assert create_timeout == 15.0
    assert json.loads(create_body or b"") == {
        "chainId": CHAIN_ID,
        "wallet": WALLET,
        "metadata": {
            "name": "Abyss",
            "symbol": "ABYSS",
            "description": "Launched through the Abyss protocol.",
            "websiteUrl": "https://abyss.trading/",
        },
        "image": {
            "sha256": IMAGE_SHA256,
            "contentType": "image/png",
            "contentLength": 3,
        },
        "authorization": {
            "nonce": NONCE,
            "deadline": "1800000000",
            "signature": SIGNATURE,
        },
    }

    assert transport.calls[1] == (
        "GET",
        "https://api.example/api/v1/launch-upload-sessions/session-1?chainId=4663",
        {"Authorization": "Launch-Upload-Capability private-capability"},
        None,
        15.0,
    )
    assert transport.calls[2][0:4] == (
        "POST",
        "https://api.example/api/v1/launch-upload-sessions/session-1/upload-url?chainId=4663",
        {
            "Content-Type": "application/json",
            "Authorization": "Launch-Upload-Capability private-capability",
        },
        b"{}",
    )
    assert transport.calls[3][0:4] == (
        "POST",
        "https://api.example/api/v1/launch-upload-sessions/session-1/upload/complete?chainId=4663",
        {
            "Content-Type": "application/json",
            "Authorization": "Launch-Upload-Capability private-capability",
        },
        b"{}",
    )
    assert transport.calls[4][0:3] == (
        "POST",
        "https://api.example/api/v1/launch-upload-sessions/session-1/publish?chainId=4663",
        {
            "Content-Type": "application/json",
            "Authorization": "Launch-Upload-Capability private-capability",
        },
    )
    assert json.loads(transport.calls[4][3] or b"") == {
        "chainId": CHAIN_ID,
        "transactionHash": TRANSACTION_HASH,
    }


def test_public_launch_reads_preserve_server_fields_and_query_contract():
    list_response = {"chainId": CHAIN_ID, "indexedBlock": "1300", "items": [{"unknown": "kept"}]}
    detail_response = {
        "chainId": CHAIN_ID,
        "indexedBlock": "1300",
        "item": {"token": TOKEN, "unknown": "kept"},
        "poolState": {"unlocked": True},
        "poolChartUrl": "/api/v1/metrics/pools/example/chart?chainId=4663",
    }
    transport = TransportStub(_json_response(list_response), _json_response(detail_response))
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example"), transport=transport)

    assert client.list_launches(CHAIN_ID, limit=30, cursor="1200:0:0xabc") == list_response
    assert client.get_launch(CHAIN_ID, TOKEN) == detail_response

    assert transport.calls[0][0:4] == (
        "GET",
        "https://api.example/api/v1/launches?limit=30&cursor=1200%3A0%3A0xabc&chainId=4663",
        {},
        None,
    )
    assert transport.calls[1][0:4] == (
        "GET",
        f"https://api.example/api/v1/launches/{TOKEN}?chainId=4663",
        {},
        None,
    )


def test_canonical_hash_and_eip712_helpers_match_fixed_vectors_and_eth_account_shape():
    edit = LaunchMetadataEditDocument(
        description="\n  Line one\n\nLine  two   with   spaces \t\n"
    )
    assert canonical_launch_metadata_edit_hash(edit) == (
        "0xa83740a250b920e02e9d23e45a9afbd4c3c7968f048d557de3e769248781c0a6"
    )
    assert sha256_hex(b"abc") == (
        "0xba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )

    metadata = LaunchSessionMetadata(
        name="\ufeff Cafe\u0301 ",
        symbol=" ABYSS\u00a0",
        description="\u2009 Rocket 🚀 \u2009",
    )
    canonical_json = (
        '{"name":"Café","symbol":"ABYSS","description":"Rocket 🚀",'
        '"websiteUrl":null,"twitterUrl":null,"telegramUrl":null,'
        '"discordUrl":null,"imageKey":null}'
    )
    expected_metadata_hash = "0x" + keccak(primitive=canonical_json.encode("utf-8")).hex()
    assert canonical_launch_metadata_hash(metadata) == expected_metadata_hash

    attribution = build_launch_attribution_typed_data(
        chain_id=CHAIN_ID,
        wallet=WALLET,
        metadata=metadata,
        idempotency_key="launch-idempotency-key",
        nonce=NONCE,
        deadline=1_800_000_000,
        verifying_contract=ORCHESTRATOR,
    )
    assert attribution == {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "LaunchAttribution": [
                {"name": "chainId", "type": "uint256"},
                {"name": "wallet", "type": "address"},
                {"name": "metadataHash", "type": "bytes32"},
                {"name": "imageSha256", "type": "bytes32"},
                {"name": "imageContentType", "type": "string"},
                {"name": "imageContentLength", "type": "uint256"},
                {"name": "idempotencyKey", "type": "string"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "LaunchAttribution",
        "domain": {
            "name": "Abyss Launch Attribution",
            "version": "1",
            "chainId": CHAIN_ID,
            "verifyingContract": ORCHESTRATOR,
        },
        "message": {
            "chainId": CHAIN_ID,
            "wallet": WALLET,
            "metadataHash": expected_metadata_hash,
            "imageSha256": "0x" + "00" * 32,
            "imageContentType": "",
            "imageContentLength": 0,
            "idempotencyKey": "launch-idempotency-key",
            "nonce": NONCE,
            "deadline": 1_800_000_000,
        },
    }
    assert encode_typed_data(full_message=attribution).version == b"\x01"

    update = build_launch_metadata_update_typed_data(
        chain_id=CHAIN_ID,
        token=TOKEN,
        metadata=edit,
        expected_version=7,
        nonce=NONCE,
        deadline=1_800_000_000,
        verifying_contract=ORCHESTRATOR,
    )
    assert update == {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "LaunchMetadataUpdate": [
                {"name": "chainId", "type": "uint256"},
                {"name": "token", "type": "address"},
                {"name": "metadataHash", "type": "bytes32"},
                {"name": "expectedVersion", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "LaunchMetadataUpdate",
        "domain": {
            "name": "Abyss Launch Metadata",
            "version": "1",
            "chainId": CHAIN_ID,
            "verifyingContract": ORCHESTRATOR,
        },
        "message": {
            "chainId": CHAIN_ID,
            "token": TOKEN,
            "metadataHash": "0xa83740a250b920e02e9d23e45a9afbd4c3c7968f048d557de3e769248781c0a6",
            "expectedVersion": 7,
            "nonce": NONCE,
            "deadline": 1_800_000_000,
        },
    }
    assert encode_typed_data(full_message=update).version == b"\x01"


def test_metadata_authorization_requires_an_explicit_orchestrator_domain():
    with pytest.raises(ValueError):
        build_launch_attribution_typed_data(
            chain_id=CHAIN_ID,
            wallet=WALLET,
            metadata=LaunchSessionMetadata(name="Abyss", symbol="ABYSS"),
            idempotency_key="launch-idempotency-key",
            nonce=NONCE,
            deadline=1_800_000_000,
        )


def test_signed_metadata_replacement_image_flow_reuses_exact_document_and_authorization():
    upload_id = "123e4567-e89b-42d3-a456-426614174000"
    image_key = f"launches/{CHAIN_ID}/{TOKEN.lower()}/{upload_id}"
    metadata = LaunchMetadataEditDocument(
        description="A description with  internal\tspaces\nand newlines",
        website_url="https://abyss.trading/",
        image_key=image_key,
    )
    authorization = _metadata_authorization()
    pending_upload = {
        "chainId": CHAIN_ID,
        "token": TOKEN,
        "uploadId": upload_id,
        "key": image_key,
        "upload": {
            "url": "https://uploads.example/replacement?X-Amz-Credential=private",
            "method": "PUT",
            "headers": {"content-type": "image/png", "if-none-match": "*"},
            "expiresInSeconds": 300,
        },
        "putExpiresAt": "1800000300",
    }
    transport = TransportStub(
        _json_response(pending_upload),
        LaunchApiResponse(status=200, headers={}, body=b""),
        _json_response(
            {
                "chainId": CHAIN_ID,
                "token": TOKEN,
                "metadata": {"version": "8", "document": {"name": "Abyss", "symbol": "ABYSS"}},
            }
        ),
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example"), transport=transport)
    initiation = LaunchImageUploadRequest(
        upload_id=upload_id,
        content_type="image/png",
        content_length=3,
        metadata=metadata,
        authorization=authorization,
    )

    response = client.create_launch_image_upload(CHAIN_ID, TOKEN, initiation)
    client.put_replacement_launch_image(response["upload"], b"png")
    client.replace_launch_metadata(CHAIN_ID, TOKEN, metadata, authorization)

    assert len(transport.calls) == 3
    assert transport.calls[0][0:3] == (
        "POST",
        f"https://api.example/api/v1/launches/{TOKEN}/images/uploads?chainId=4663",
        {"Content-Type": "application/json"},
    )
    initiation_payload = json.loads(transport.calls[0][3] or b"")
    assert initiation_payload == {
        "uploadId": upload_id,
        "contentType": "image/png",
        "contentLength": 3,
        "metadata": {
            "description": metadata.description,
            "websiteUrl": metadata.website_url,
            "imageKey": image_key,
        },
        "authorization": {
            "expectedVersion": "7",
            "nonce": NONCE,
            "deadline": "1800000000",
            "signature": SIGNATURE,
        },
    }
    assert transport.calls[1] == (
        "PUT",
        "https://uploads.example/replacement?X-Amz-Credential=private",
        {"content-type": "image/png", "if-none-match": "*"},
        b"png",
        15.0,
    )
    assert transport.calls[2][0:3] == (
        "PUT",
        f"https://api.example/api/v1/launches/{TOKEN}/metadata?chainId=4663",
        {"Content-Type": "application/json"},
    )
    promotion_payload = json.loads(transport.calls[2][3] or b"")
    assert promotion_payload == {"metadata": initiation_payload["metadata"], "authorization": initiation_payload["authorization"]}


def test_errors_and_local_validation_do_not_retry_or_send_injected_values():
    secret = "untrusted server body with a capability-like secret"
    transport = TransportStub(
        _json_response(
            {"code": "RATE_LIMITED", "error": secret},
            status=429,
            headers={"Retry-After": "2"},
        )
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=1), transport=transport)

    with pytest.raises(LaunchApiError) as error:
        client.get_launch(CHAIN_ID, TOKEN)
    assert error.value.status == 429
    assert error.value.code == "RATE_LIMITED"
    assert error.value.retry_after_ms == 2_000
    assert error.value.retryable is True
    assert secret not in str(error.value)
    assert len(transport.calls) == 1

    with pytest.raises(ValueError):
        client.replace_launch_metadata(
            CHAIN_ID,
            TOKEN,
            {"name": "not editable", "description": "description"},
            _metadata_authorization(),
        )
    with pytest.raises(ValueError):
        client.get_launch_upload_session(CHAIN_ID, "session-1", "capability\r\nInjected: value")
    with pytest.raises(ValueError):
        client.get_launch_upload_session(CHAIN_ID, "../session-1", "capability")
    with pytest.raises(ValueError):
        LaunchSessionImageDescriptor(
            sha256=IMAGE_SHA256,
            content_type="image/gif",
            content_length=3,
        )
    with pytest.raises(ValueError):
        client.put_launch_image(
            {"url": "https://uploads.example/object", "headers": {"content-type": "image/png"}},
            b"",
        )
    assert len(transport.calls) == 1

def test_retry_after_uses_javascript_rounding_and_clamps_finite_extremes():
    transport = TransportStub(
        _json_response({"code": "RATE_LIMITED"}, status=429, headers={"Retry-After": "0.3125"}),
        _json_response({"code": "RATE_LIMITED"}, status=429, headers={"Retry-After": "1e308"}),
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=1), transport=transport)

    with pytest.raises(LaunchApiError) as half_up:
        client.get_launch(CHAIN_ID, TOKEN)
    with pytest.raises(LaunchApiError) as extreme:
        client.get_launch(CHAIN_ID, TOKEN)

    assert half_up.value.retry_after_ms == 313
    assert extreme.value.retry_after_ms == 7 * 24 * 60 * 60 * 1000
    assert len(transport.calls) == 2


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:18763",
    "http://localhost:18763",
    "http://[::1]:18763",
    "http://127.0.0.2:18763/api/",
])
def test_http_api_configuration_requires_explicit_loopback_opt_in(base_url):
    with pytest.raises(ValueError):
        LaunchApiConfig(base_url=base_url)
    config = LaunchApiConfig(base_url=base_url, allow_loopback_http=True)
    assert config.base_url == base_url.rstrip("/")


@pytest.mark.parametrize("base_url", [
    "http://api.abyss.trading",
    "http://192.168.1.1:18763",
    "http://[2001:db8::1]:18763",
    "http://localhost.attacker.invalid:18763",
    "http://127.0.0.1.attacker.invalid:18763",
    "http://2130706433:18763",
    "http://user:password@localhost:18763",
    "http://127.0.0.1@api.abyss.trading",
    "http://127.0.0.1:18763?secret=value",
    "http://localhost:18763#fragment",
    "http://localhost:18763?",
    "http://localhost:18763#",
    "http://localhost:0",
    "http://[::1%25eth0]:18763",
    "ftp://localhost:18763",
    "https://api.abyss.trading?secret=value",
    "https://user:password@api.abyss.trading",
])
def test_loopback_http_opt_in_still_rejects_remote_and_unsafe_api_urls(base_url):
    with pytest.raises(ValueError):
        LaunchApiConfig(base_url=base_url, allow_loopback_http=True)


@pytest.mark.parametrize("opt_in", [None, 0, 1, "false", "true"])
def test_loopback_http_opt_in_must_be_an_explicit_boolean(opt_in):
    with pytest.raises(TypeError):
        LaunchApiConfig(base_url="http://127.0.0.1:18763", allow_loopback_http=opt_in)


def test_local_http_api_opt_in_does_not_permit_http_image_uploads():
    client = LaunchApiClient(LaunchApiConfig(
        base_url="http://127.0.0.1:18763", allow_loopback_http=True,
    ))
    with pytest.raises(ValueError):
        client.put_launch_image(
            {"url": "http://127.0.0.1:18763/upload", "headers": {"content-type": "image/png"}},
            b"png",
        )


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_transient_api_retries_count_attempts_and_retain_request_identity(monkeypatch, status):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    transport = TransportStub(*[
        _json_response({"code": "RPC_UNAVAILABLE"}, status=status) for _ in range(3)
    ])
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=3), transport=transport)
    with pytest.raises(LaunchApiError) as failure:
        client.get_launch_upload_session(CHAIN_ID, "session-1", "private-capability")
    assert failure.value.status == status
    assert len(transport.calls) == 3
    assert transport.calls == [transport.calls[0]] * 3


def test_session_creation_retry_reuses_exact_signed_payload_and_key(monkeypatch):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    created = {"sessionId": "session-1", "capability": "created-only-capability"}
    transport = TransportStub(
        _json_response({"code": "RPC_UNAVAILABLE"}, status=503),
        _json_response(created, status=201),
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example"), transport=transport)
    request = LaunchSessionCreateRequest(
        chain_id=CHAIN_ID, wallet=WALLET,
        metadata=LaunchSessionCreateMetadata(name="Abyss", symbol="ABYSS", description="Description"),
        authorization=_attribution_authorization(),
    )
    assert client.create_launch_upload_session(request, "launch-idempotency-key") == created
    assert transport.calls == [transport.calls[0]] * 2


def test_publish_pending_is_returned_once_without_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    pending = {"sessionId": "session-1", "status": "awaiting_indexer"}
    transport = TransportStub(_json_response(pending, status=202))
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=5), transport=transport)
    with pytest.raises(LaunchPublishPending) as failure:
        client.publish_launch_upload_session(CHAIN_ID, "session-1", "private-capability",
            LaunchSessionPublishRequest(chain_id=CHAIN_ID, transaction_hash=TRANSACTION_HASH))
    assert failure.value.session == pending
    assert failure.value.retry_after_ms == 250
    assert len(transport.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("replacement", [False, True])
def test_direct_image_upload_never_retries_or_sleeps(monkeypatch, status, replacement):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    transport = TransportStub(LaunchApiResponse(status=status, headers={}, body=b"private response"))
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=5), transport=transport)
    upload = LaunchSessionDirectUpload(
        url="https://uploads.example/object?signature=private",
        headers={"content-type": "image/png", "if-none-match": "*"},
        expires_in_seconds=300,
    )
    put = client.put_replacement_launch_image if replacement else client.put_launch_image
    with pytest.raises(LaunchUploadError) as failure:
        put(upload, b"png")
    assert failure.value.status == status
    assert "private" not in str(failure.value)
    assert upload.expires_in_seconds == 300
    assert len(transport.calls) == 1
    assert transport.calls[0][2] == dict(upload.headers)
    assert sleeps == []


def test_new_session_rejects_caller_assigned_image_key_before_transport():
    transport = TransportStub()
    client = LaunchApiClient(transport=transport)
    with pytest.raises(TypeError):
        request = LaunchSessionCreateRequest(
            chain_id=CHAIN_ID, wallet=WALLET,
            metadata=LaunchSessionMetadata(name="Abyss", symbol="ABYSS", image_key="private-key"),
            authorization=_attribution_authorization(),
        )
        client.create_launch_upload_session(request, "launch-idempotency-key")
    assert transport.calls == []


def test_malformed_generic_success_json_can_recover_within_attempt_budget(monkeypatch):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    transport = TransportStub(
        LaunchApiResponse(status=200, headers={}, body=b"{"),
        _json_response({"chainId": CHAIN_ID, "item": {"token": TOKEN}}),
    )
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=2), transport=transport)
    assert client.get_launch(CHAIN_ID, TOKEN)["item"]["token"] == TOKEN
    assert len(transport.calls) == 2
    assert transport.calls[0] == transport.calls[1]


def test_malformed_generic_success_json_exhausts_attempt_budget(monkeypatch):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    transport = TransportStub(*[
        LaunchApiResponse(status=200, headers={}, body=b"{") for _ in range(2)
    ])
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=2), transport=transport)
    with pytest.raises(LaunchApiError):
        client.get_launch(CHAIN_ID, TOKEN)
    assert len(transport.calls) == 2


def test_accepted_publish_with_malformed_json_is_never_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(launch_api_module.time, "sleep", sleeps.append)
    transport = TransportStub(LaunchApiResponse(status=202, headers={}, body=b"{"))
    client = LaunchApiClient(LaunchApiConfig(base_url="https://api.example", retries=3), transport=transport)
    with pytest.raises(LaunchApiError):
        client.publish_launch_upload_session(CHAIN_ID, "session-1", "private-capability",
            LaunchSessionPublishRequest(chain_id=CHAIN_ID, transaction_hash=TRANSACTION_HASH))
    assert len(transport.calls) == 1
    assert sleeps == []


def test_api_routes_resolve_from_origin_not_base_url_path_prefix():
    transport = TransportStub(_json_response({"item": {"token": TOKEN}}))
    client = LaunchApiClient(LaunchApiConfig(
        base_url="https://api.example/nested/prefix/",
    ), transport=transport)
    assert client.get_launch(CHAIN_ID, TOKEN)["item"]["token"] == TOKEN
    assert transport.calls[0][1] == f"https://api.example/api/v1/launches/{TOKEN}?chainId={CHAIN_ID}"
