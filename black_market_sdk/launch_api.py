"""Opt-in client and signing helpers for the Abyss launch metadata API.

This module never creates a client or performs I/O at import time.  Callers own
wallet signatures, idempotency keys, recovery state, and retry policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence, TypeAlias, Union
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from eth_utils import is_address, keccak


LAUNCH_IMAGE_CONTENT_TYPES: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")
MAX_LAUNCH_IMAGE_BYTES = 5 * 1024 * 1024

_DEFAULT_API_URL = "https://api.abyss.trading"
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ZERO_HASH = "0x" + "00" * 32
_MAX_UINT256 = (1 << 256) - 1
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ERROR_BODY_BYTES = 64 * 1024
_MAX_RETRY_AFTER_MS = 7 * 24 * 60 * 60 * 1000

_HEX_32_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_SIGNATURE_RE = re.compile(r"0x[0-9a-fA-F]{130}\Z")
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_ERROR_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")

# ECMAScript WhiteSpace plus LineTerminator. Python's str.strip() additionally
# removes a few C0 separators that ECMAScript leaves intact, so it cannot be
# used for signature-bound canonical JSON.
_ECMASCRIPT_TRIM_CHARACTERS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


@dataclass(frozen=True)
class LaunchApiConfig:
    """Explicit transport configuration for :class:`LaunchApiClient`."""

    base_url: str = _DEFAULT_API_URL
    timeout: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _normalize_api_base_url(self.base_url))
        object.__setattr__(self, "timeout", _validate_timeout(self.timeout))


@dataclass(frozen=True)
class LaunchApiResponse:
    """A deterministic transport response used by ``LaunchApiTransport``."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if not _is_int(self.status) or self.status < 0 or self.status > 999:
            raise ValueError("response status must be an integer from 0 through 999")
        if not isinstance(self.headers, Mapping):
            raise TypeError("response headers must be a mapping")
        response_headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("response headers must contain string names and values")
            response_headers[name] = value
        if not isinstance(self.body, bytes):
            raise TypeError("response body must be bytes")
        object.__setattr__(self, "headers", MappingProxyType(response_headers))


LaunchApiTransport: TypeAlias = Callable[
    [str, str, Mapping[str, str], Optional[bytes], float], LaunchApiResponse
]


@dataclass(frozen=True)
class LaunchSessionMetadata:
    """Creator-provided metadata staged before a launch transaction."""

    name: str
    symbol: str
    description: Optional[str] = None
    website_url: Optional[str] = None
    twitter_url: Optional[str] = None
    telegram_url: Optional[str] = None
    discord_url: Optional[str] = None
    image_key: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_session_metadata(self)


@dataclass(frozen=True)
class LaunchSessionImageDescriptor:
    """Image bytes bound into an attribution signature before direct upload."""

    sha256: str
    content_type: str
    content_length: int

    def __post_init__(self) -> None:
        _validate_image_descriptor(self)


@dataclass(frozen=True)
class LaunchAttributionAuthorization:
    """Caller-managed EIP-712 authorization for a launch upload session."""

    nonce: str
    deadline: int
    signature: str

    def __post_init__(self) -> None:
        _validate_attribution_authorization(self)


@dataclass(frozen=True)
class LaunchSessionCreateRequest:
    """The signed metadata and optional image descriptor for session creation."""

    chain_id: int
    wallet: str
    metadata: LaunchSessionMetadata
    authorization: LaunchAttributionAuthorization
    image: Optional[LaunchSessionImageDescriptor] = None

    def __post_init__(self) -> None:
        _validate_session_create_request(self)


@dataclass(frozen=True)
class LaunchSessionPublishRequest:
    """A confirmed launch transaction registered against a private session."""

    chain_id: int
    transaction_hash: str

    def __post_init__(self) -> None:
        _validate_publish_request(self)


@dataclass(frozen=True)
class LaunchMetadataEditDocument:
    """The mutable metadata fields; on-chain ``name`` and ``symbol`` are absent."""

    description: str
    website_url: Optional[str] = None
    twitter_url: Optional[str] = None
    telegram_url: Optional[str] = None
    discord_url: Optional[str] = None
    image_key: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_metadata_edit_document(self)


@dataclass(frozen=True)
class LaunchMetadataAuthorization:
    """Caller-managed EIP-712 authorization for a metadata replacement."""

    expected_version: int
    nonce: str
    deadline: int
    signature: str

    def __post_init__(self) -> None:
        _validate_metadata_authorization(self)


@dataclass(frozen=True)
class LaunchImageUploadRequest:
    """Signed request for a replacement-image presigned PUT."""

    upload_id: str
    content_type: str
    content_length: int
    metadata: LaunchMetadataEditDocument
    authorization: LaunchMetadataAuthorization

    def __post_init__(self) -> None:
        _validate_image_upload_request(self)


@dataclass(frozen=True)
class LaunchSessionDirectUpload:
    """A direct create-only presigned PUT returned by the launch API."""

    url: str
    headers: Mapping[str, str]
    expires_at: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_direct_upload_url(self.url)
        object.__setattr__(self, "headers", MappingProxyType(_validate_signed_headers(self.headers)))
        if self.expires_at is not None:
            _assert_uint(self.expires_at, "expires_at")


class LaunchApiError(RuntimeError):
    """A safe, typed failure returned by the launch API.

    Error bodies are deliberately not retained or interpolated into the error
    message.  They can contain arbitrary server-provided content; callers get
    the status, a bounded safe code, and the parsed Retry-After hint instead.
    """

    def __init__(
        self,
        status: int,
        code: str,
        *,
        retry_after_ms: Optional[int] = None,
    ) -> None:
        if not _is_int(status) or status < 0 or status > 999:
            raise ValueError("status must be an integer from 0 through 999")
        safe_code = _safe_error_code(code)
        if retry_after_ms is not None:
            _assert_uint(retry_after_ms, "retry_after_ms")
            retry_after_ms = min(retry_after_ms, _MAX_RETRY_AFTER_MS)
        super().__init__(f"Launch API request failed with status {status} ({safe_code}).")
        self.status = status
        self.code = safe_code
        self.retry_after_ms = retry_after_ms

    @property
    def retryable(self) -> bool:
        """Whether a caller-controlled retry may be appropriate."""

        return self.status == 0 or self.status == 429 or self.status >= 500


class LaunchPublishPending(RuntimeError):
    """A 202 publish response: transaction is known but projection is pending."""

    def __init__(self, session: Mapping[str, Any], retry_after_ms: int) -> None:
        if not isinstance(session, Mapping):
            raise TypeError("pending launch session must be a mapping")
        _assert_uint(retry_after_ms, "retry_after_ms")
        if retry_after_ms == 0:
            raise ValueError("retry_after_ms must be positive")
        super().__init__("Launch transaction is known; waiting for the canonical indexed projection.")
        self.session = session
        self.retry_after_ms = min(retry_after_ms, _MAX_RETRY_AFTER_MS)


class LaunchUploadError(RuntimeError):
    """A safe failure while uploading bytes to a presigned URL."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        if status is not None and (not _is_int(status) or status < 0 or status > 999):
            raise ValueError("status must be an integer from 0 through 999")
        super().__init__(message)
        self.status = status


class _NoRedirectHandler(HTTPRedirectHandler):
    """Return redirects as responses rather than forwarding signed headers."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None


def _default_http_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Optional[bytes],
    timeout: float,
) -> LaunchApiResponse:
    """Stdlib transport with no proxy discovery and no redirect forwarding."""

    request = Request(url, data=body, headers=dict(headers), method=method)
    opener = build_opener(
        ProxyHandler({}),
        HTTPHandler(),
        HTTPSHandler(),
        _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return LaunchApiResponse(
                status=response.getcode(),
                headers=dict(response.headers.items()),
                body=_read_bounded_response_body(response),
            )
    except HTTPError as response:
        return LaunchApiResponse(
            status=response.code,
            headers=dict(response.headers.items()) if response.headers is not None else {},
            body=_read_bounded_response_body(response),
        )


def _read_bounded_response_body(response: Any) -> bytes:
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
        return b""
    return body


class LaunchApiClient:
    """Explicit, no-retry client for the Abyss launch API and presigned uploads."""

    def __init__(
        self,
        config: LaunchApiConfig,
        *,
        transport: Optional[LaunchApiTransport] = None,
    ) -> None:
        if not isinstance(config, LaunchApiConfig):
            raise TypeError("config must be a LaunchApiConfig")
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable")
        self._config = config
        self._transport: LaunchApiTransport = transport or _default_http_transport

    @property
    def config(self) -> LaunchApiConfig:
        """The immutable explicit configuration used by this client."""

        return self._config

    def create_launch_upload_session(
        self,
        request: LaunchSessionCreateRequest,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """POST a signed metadata session and receive its recovery capability."""

        _validate_session_create_request(request)
        _validate_header_value(idempotency_key, "idempotency_key")
        return self._request(
            "POST",
            "/api/v1/launch-upload-sessions",
            request.chain_id,
            body=_json_bytes(_session_create_payload(request)),
            idempotency_key=idempotency_key,
        )

    def get_launch_upload_session(
        self,
        chain_id: int,
        session_id: str,
        capability: str,
    ) -> dict[str, Any]:
        """GET a private session for caller-managed recovery."""

        return self._request(
            "GET",
            f"/api/v1/launch-upload-sessions/{_path_component(session_id, 'session_id')}",
            chain_id,
            capability=capability,
        )

    def renew_launch_upload_url(
        self,
        chain_id: int,
        session_id: str,
        capability: str,
    ) -> dict[str, Any]:
        """POST for a replacement direct PUT URL when a session upload expires."""

        return self._request(
            "POST",
            f"/api/v1/launch-upload-sessions/{_path_component(session_id, 'session_id')}/upload-url",
            chain_id,
            body=b"{}",
            capability=capability,
        )

    def complete_launch_upload(
        self,
        chain_id: int,
        session_id: str,
        capability: str,
    ) -> dict[str, Any]:
        """POST that staged bytes are ready for API-side image verification."""

        return self._request(
            "POST",
            f"/api/v1/launch-upload-sessions/{_path_component(session_id, 'session_id')}/upload/complete",
            chain_id,
            body=b"{}",
            capability=capability,
        )

    def publish_launch_upload_session(
        self,
        chain_id: int,
        session_id: str,
        capability: str,
        body: LaunchSessionPublishRequest,
    ) -> dict[str, Any]:
        """POST a confirmed transaction; a 202 becomes ``LaunchPublishPending``."""

        _validate_publish_request(body)
        _assert_chain_id(chain_id)
        if body.chain_id != chain_id:
            raise ValueError("publish body chain_id must match the request chain_id")
        return self._request(
            "POST",
            f"/api/v1/launch-upload-sessions/{_path_component(session_id, 'session_id')}/publish",
            chain_id,
            body=_json_bytes(_publish_payload(body)),
            capability=capability,
            expect_pending=True,
        )

    def list_launches(
        self,
        chain_id: int,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET a public, cursor-paginated launch projection page."""

        _assert_chain_id(chain_id)
        query: list[tuple[str, str]] = []
        if limit is not None:
            _assert_uint(limit, "limit")
            if limit == 0:
                raise ValueError("limit must be positive")
            query.append(("limit", str(limit)))
        if cursor is not None:
            _validate_query_value(cursor, "cursor")
            query.append(("cursor", cursor))
        return self._request("GET", "/api/v1/launches", chain_id, query=query)

    def get_launch(self, chain_id: int, token: str) -> dict[str, Any]:
        """GET one public launch projection with its companion-pool state."""

        _assert_chain_id(chain_id)
        _validate_address(token, "token")
        return self._request("GET", f"/api/v1/launches/{quote(token, safe='')}", chain_id)

    def replace_launch_metadata(
        self,
        chain_id: int,
        token: str,
        metadata: Union[LaunchMetadataEditDocument, Mapping[str, Any]],
        authorization: LaunchMetadataAuthorization,
    ) -> dict[str, Any]:
        """PUT signed mutable metadata; immutable name and symbol are rejected locally."""

        _assert_chain_id(chain_id)
        _validate_address(token, "token")
        document = _coerce_metadata_edit_document(metadata)
        _validate_metadata_authorization(authorization)
        return self._request(
            "PUT",
            f"/api/v1/launches/{quote(token, safe='')}/metadata",
            chain_id,
            body=_json_bytes(
                {
                    "metadata": _metadata_edit_payload(document),
                    "authorization": _metadata_authorization_payload(authorization),
                }
            ),
        )

    def create_launch_image_upload(
        self,
        chain_id: int,
        token: str,
        body: LaunchImageUploadRequest,
    ) -> dict[str, Any]:
        """POST signed replacement-image upload initiation for a launch token."""

        _assert_chain_id(chain_id)
        _validate_address(token, "token")
        _validate_image_upload_request(body)
        return self._request(
            "POST",
            f"/api/v1/launches/{quote(token, safe='')}/images/uploads",
            chain_id,
            body=_json_bytes(_image_upload_request_payload(body)),
        )

    def put_launch_image(
        self,
        upload: Union[LaunchSessionDirectUpload, Mapping[str, Any]],
        body: bytes,
    ) -> None:
        """PUT bytes with every API-issued presigned header and no redirects."""

        url, headers = _coerce_direct_upload(upload)
        _validate_image_bytes(body)
        content_type = _content_type_from_upload_headers(headers)
        _validate_content_type(content_type)
        _validate_content_length_header(headers, len(body))
        try:
            response = self._transport("PUT", url, headers, body, self._config.timeout)
        except LaunchUploadError:
            raise
        except Exception:
            raise LaunchUploadError("Image upload transport failed.") from None
        if not isinstance(response, LaunchApiResponse):
            raise LaunchUploadError("Image upload transport returned an invalid response.")
        if not 200 <= response.status < 300:
            raise LaunchUploadError(
                f"Image upload failed with status {response.status}.", status=response.status
            )

    def put_replacement_launch_image(
        self,
        upload: Union[LaunchSessionDirectUpload, Mapping[str, Any]],
        body: bytes,
    ) -> None:
        """PUT a replacement image using the same exact presigned-header path."""

        self.put_launch_image(upload, body)

    def _request(
        self,
        method: str,
        path: str,
        chain_id: int,
        *,
        body: Optional[bytes] = None,
        capability: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        query: Sequence[tuple[str, str]] = (),
        expect_pending: bool = False,
    ) -> dict[str, Any]:
        _assert_chain_id(chain_id)
        if body is not None and not isinstance(body, bytes):
            raise TypeError("request body must be bytes")
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if capability is not None:
            _validate_header_value(capability, "capability")
            headers["Authorization"] = f"Launch-Upload-Capability {capability}"
        if idempotency_key is not None:
            _validate_header_value(idempotency_key, "idempotency_key")
            headers["Idempotency-Key"] = idempotency_key
        url = self._api_url(path, chain_id, query)
        try:
            response = self._transport(method, url, headers, body, self._config.timeout)
        except LaunchApiError:
            raise
        except Exception:
            raise LaunchApiError(0, "RPC_UNAVAILABLE") from None
        if not isinstance(response, LaunchApiResponse):
            raise LaunchApiError(0, "INVALID_RESPONSE")
        if 200 <= response.status < 300:
            payload = _parse_success_mapping(response)
            if response.status == 202 and expect_pending:
                raise LaunchPublishPending(payload, _retry_after_ms(response.headers) or 5_000)
            return payload
        raise LaunchApiError(
            response.status,
            _response_error_code(response),
            retry_after_ms=_retry_after_ms(response.headers),
        )

    def _api_url(
        self,
        path: str,
        chain_id: int,
        query: Sequence[tuple[str, str]],
    ) -> str:
        if not path.startswith("/api/v1/"):
            raise ValueError("launch API paths must start with /api/v1/")
        parameters = list(query)
        parameters.append(("chainId", str(chain_id)))
        return f"{self._config.base_url}{path}?{urlencode(parameters)}"


def canonical_launch_metadata_hash(metadata: LaunchSessionMetadata) -> str:
    """Keccak-256 of the exact JavaScript canonical session metadata JSON."""

    _validate_session_metadata(metadata)
    canonical = _canonical_json(
        {
            "name": _canonical_text(metadata.name),
            "symbol": _canonical_text(metadata.symbol),
            "description": _canonical_text(metadata.description if metadata.description is not None else ""),
            "websiteUrl": metadata.website_url,
            "twitterUrl": metadata.twitter_url,
            "telegramUrl": metadata.telegram_url,
            "discordUrl": metadata.discord_url,
            "imageKey": metadata.image_key,
        }
    )
    return "0x" + keccak(primitive=canonical.encode("utf-8")).hex()


def canonical_launch_metadata_edit_hash(
    metadata: Union[LaunchMetadataEditDocument, Mapping[str, Any]],
) -> str:
    """Keccak-256 of the fixed-order mutable metadata JSON used by EIP-712."""

    document = _coerce_metadata_edit_document(metadata)
    canonical = _canonical_json(
        {
            "description": _canonical_text(document.description),
            "websiteUrl": document.website_url,
            "twitterUrl": document.twitter_url,
            "telegramUrl": document.telegram_url,
            "discordUrl": document.discord_url,
            "imageKey": document.image_key,
        }
    )
    return "0x" + keccak(primitive=canonical.encode("utf-8")).hex()


def sha256_hex(body: bytes) -> str:
    """Return a lowercase, 0x-prefixed SHA-256 digest without performing I/O."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    return "0x" + hashlib.sha256(body).hexdigest()


def build_launch_attribution_typed_data(
    *,
    chain_id: int,
    wallet: str,
    metadata: LaunchSessionMetadata,
    image: Optional[LaunchSessionImageDescriptor] = None,
    idempotency_key: str,
    nonce: str,
    deadline: int,
    verifying_contract: Optional[str] = None,
) -> dict[str, Any]:
    """Build a complete eth-account-compatible ``LaunchAttribution`` payload.

    The caller chooses nonce, deadline, and idempotency key, signs this returned
    dictionary, then places the signature in ``LaunchAttributionAuthorization``.
    """

    _assert_chain_id(chain_id)
    _validate_address(wallet, "wallet")
    _validate_session_metadata(metadata)
    if image is not None:
        _validate_image_descriptor(image)
    _validate_header_value(idempotency_key, "idempotency_key")
    _validate_bytes32(nonce, "nonce")
    _assert_uint(deadline, "deadline")
    contract = _resolve_verifying_contract(chain_id, verifying_contract)
    return {
        "types": {
            "EIP712Domain": _eip712_domain_types(),
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
            "chainId": chain_id,
            "verifyingContract": contract,
        },
        "message": {
            "chainId": chain_id,
            "wallet": wallet,
            "metadataHash": canonical_launch_metadata_hash(metadata),
            "imageSha256": image.sha256 if image is not None else _ZERO_HASH,
            "imageContentType": image.content_type if image is not None else "",
            "imageContentLength": image.content_length if image is not None else 0,
            "idempotencyKey": idempotency_key,
            "nonce": nonce,
            "deadline": deadline,
        },
    }


def build_launch_metadata_update_typed_data(
    *,
    chain_id: int,
    token: str,
    metadata: Union[LaunchMetadataEditDocument, Mapping[str, Any]],
    expected_version: int,
    nonce: str,
    deadline: int,
    verifying_contract: Optional[str] = None,
) -> dict[str, Any]:
    """Build a complete eth-account-compatible ``LaunchMetadataUpdate`` payload."""

    _assert_chain_id(chain_id)
    _validate_address(token, "token")
    document = _coerce_metadata_edit_document(metadata)
    _assert_uint(expected_version, "expected_version")
    _validate_bytes32(nonce, "nonce")
    _assert_uint(deadline, "deadline")
    contract = _resolve_verifying_contract(chain_id, verifying_contract)
    return {
        "types": {
            "EIP712Domain": _eip712_domain_types(),
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
            "chainId": chain_id,
            "verifyingContract": contract,
        },
        "message": {
            "chainId": chain_id,
            "token": token,
            "metadataHash": canonical_launch_metadata_edit_hash(document),
            "expectedVersion": expected_version,
            "nonce": nonce,
            "deadline": deadline,
        },
    }


def _eip712_domain_types() -> list[dict[str, str]]:
    return [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ]


def _resolve_verifying_contract(chain_id: int, verifying_contract: Optional[str]) -> str:
    if verifying_contract is None:
        raise ValueError("verifying_contract must explicitly name the reviewed lifecycle orchestrator")
    _validate_address(verifying_contract, "verifying_contract")
    return verifying_contract


def _session_create_payload(request: LaunchSessionCreateRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chainId": request.chain_id,
        "wallet": request.wallet,
        "metadata": _session_metadata_payload(request.metadata),
    }
    if request.image is not None:
        payload["image"] = {
            "sha256": request.image.sha256,
            "contentType": request.image.content_type,
            "contentLength": request.image.content_length,
        }
    payload["authorization"] = _attribution_authorization_payload(request.authorization)
    return payload


def _session_metadata_payload(metadata: LaunchSessionMetadata) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": metadata.name, "symbol": metadata.symbol}
    optional_fields = (
        ("description", metadata.description),
        ("websiteUrl", metadata.website_url),
        ("twitterUrl", metadata.twitter_url),
        ("telegramUrl", metadata.telegram_url),
        ("discordUrl", metadata.discord_url),
        ("imageKey", metadata.image_key),
    )
    for key, value in optional_fields:
        if value is not None:
            payload[key] = value
    return payload


def _attribution_authorization_payload(
    authorization: LaunchAttributionAuthorization,
) -> dict[str, str]:
    return {
        "nonce": authorization.nonce,
        "deadline": str(authorization.deadline),
        "signature": authorization.signature,
    }


def _publish_payload(body: LaunchSessionPublishRequest) -> dict[str, Any]:
    return {"chainId": body.chain_id, "transactionHash": body.transaction_hash}


def _metadata_edit_payload(metadata: LaunchMetadataEditDocument) -> dict[str, Any]:
    payload: dict[str, Any] = {"description": metadata.description}
    optional_fields = (
        ("websiteUrl", metadata.website_url),
        ("twitterUrl", metadata.twitter_url),
        ("telegramUrl", metadata.telegram_url),
        ("discordUrl", metadata.discord_url),
        ("imageKey", metadata.image_key),
    )
    for key, value in optional_fields:
        if value is not None:
            payload[key] = value
    return payload


def _metadata_authorization_payload(
    authorization: LaunchMetadataAuthorization,
) -> dict[str, str]:
    return {
        "expectedVersion": str(authorization.expected_version),
        "nonce": authorization.nonce,
        "deadline": str(authorization.deadline),
        "signature": authorization.signature,
    }


def _image_upload_request_payload(body: LaunchImageUploadRequest) -> dict[str, Any]:
    return {
        "uploadId": body.upload_id,
        "contentType": body.content_type,
        "contentLength": body.content_length,
        "metadata": _metadata_edit_payload(body.metadata),
        "authorization": _metadata_authorization_payload(body.authorization),
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip(_ECMASCRIPT_TRIM_CHARACTERS)


def _parse_success_mapping(response: LaunchApiResponse) -> dict[str, Any]:
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise LaunchApiError(response.status, "INVALID_RESPONSE")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LaunchApiError(response.status, "INVALID_RESPONSE") from None
    if not isinstance(payload, dict):
        raise LaunchApiError(response.status, "INVALID_RESPONSE")
    return payload


def _response_error_code(response: LaunchApiResponse) -> str:
    if len(response.body) > _MAX_ERROR_BODY_BYTES:
        return "INVALID_REQUEST"
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "INVALID_REQUEST"
    if not isinstance(payload, dict):
        return "INVALID_REQUEST"
    code = payload.get("code")
    return _safe_error_code(code) if isinstance(code, str) else "INVALID_REQUEST"


def _retry_after_ms(headers: Mapping[str, str]) -> Optional[int]:
    value = next((item for name, item in headers.items() if name.lower() == "retry-after"), None)
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    if seconds >= _MAX_RETRY_AFTER_MS / 1000:
        return _MAX_RETRY_AFTER_MS
    # JavaScript's Math.round is half-up for nonnegative values; Python's
    # round is banker's rounding. Clamp before multiplication to avoid an
    # overflow from an untrusted but finite Retry-After value.
    milliseconds = math.floor(seconds * 1000 + 0.5)
    return max(250, milliseconds)


def _coerce_direct_upload(
    upload: Union[LaunchSessionDirectUpload, Mapping[str, Any]],
) -> tuple[str, dict[str, str]]:
    if isinstance(upload, LaunchSessionDirectUpload):
        return upload.url, dict(upload.headers)
    if not isinstance(upload, Mapping):
        raise TypeError("upload must be a LaunchSessionDirectUpload or mapping")
    url = upload.get("url")
    headers = upload.get("headers")
    if not isinstance(url, str):
        raise TypeError("upload.url must be a string")
    if "method" in upload and upload["method"] != "PUT":
        raise ValueError("upload.method must be PUT")
    _validate_direct_upload_url(url)
    return url, _validate_signed_headers(headers)


def _content_type_from_upload_headers(headers: Mapping[str, str]) -> str:
    content_types = [value for name, value in headers.items() if name.lower() == "content-type"]
    if len(content_types) != 1:
        raise ValueError("upload headers must include exactly one Content-Type")
    return content_types[0]


def _validate_content_length_header(headers: Mapping[str, str], length: int) -> None:
    content_lengths = [value for name, value in headers.items() if name.lower() == "content-length"]
    if len(content_lengths) > 1:
        raise ValueError("upload headers cannot contain more than one Content-Length")
    if not content_lengths:
        return
    raw_length = content_lengths[0]
    if not raw_length.isascii() or not raw_length.isdecimal():
        raise ValueError("upload Content-Length must be a decimal integer")
    if int(raw_length) != length:
        raise ValueError("upload Content-Length does not match the image body")


def _coerce_metadata_edit_document(
    metadata: Union[LaunchMetadataEditDocument, Mapping[str, Any]],
) -> LaunchMetadataEditDocument:
    if isinstance(metadata, LaunchMetadataEditDocument):
        _validate_metadata_edit_document(metadata)
        if hasattr(metadata, "name") or hasattr(metadata, "symbol"):
            raise ValueError("metadata edits cannot include immutable name or symbol")
        return metadata
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a LaunchMetadataEditDocument")
    if "name" in metadata or "symbol" in metadata:
        raise ValueError("metadata edits cannot include immutable name or symbol")
    allowed = {
        "description",
        "websiteUrl",
        "twitterUrl",
        "telegramUrl",
        "discordUrl",
        "imageKey",
        "website_url",
        "twitter_url",
        "telegram_url",
        "discord_url",
        "image_key",
    }
    unknown = set(metadata) - allowed
    if unknown:
        raise ValueError("metadata edits contain unsupported fields")
    if "description" not in metadata:
        raise ValueError("metadata edits require description")
    return LaunchMetadataEditDocument(
        description=metadata["description"],
        website_url=_mapping_field(metadata, "website_url", "websiteUrl"),
        twitter_url=_mapping_field(metadata, "twitter_url", "twitterUrl"),
        telegram_url=_mapping_field(metadata, "telegram_url", "telegramUrl"),
        discord_url=_mapping_field(metadata, "discord_url", "discordUrl"),
        image_key=_mapping_field(metadata, "image_key", "imageKey"),
    )


def _mapping_field(mapping: Mapping[str, Any], snake_case: str, camel_case: str) -> Any:
    if snake_case in mapping and camel_case in mapping:
        raise ValueError(f"metadata cannot include both {snake_case} and {camel_case}")
    if snake_case in mapping:
        return mapping[snake_case]
    return mapping.get(camel_case)


def _validate_session_metadata(metadata: LaunchSessionMetadata) -> None:
    if not isinstance(metadata, LaunchSessionMetadata):
        raise TypeError("metadata must be a LaunchSessionMetadata")
    _validate_required_metadata_text(metadata.name, "metadata.name")
    _validate_required_metadata_text(metadata.symbol, "metadata.symbol")
    _validate_optional_text(metadata.description, "metadata.description")
    _validate_optional_text(metadata.website_url, "metadata.website_url")
    _validate_optional_text(metadata.twitter_url, "metadata.twitter_url")
    _validate_optional_text(metadata.telegram_url, "metadata.telegram_url")
    _validate_optional_text(metadata.discord_url, "metadata.discord_url")
    _validate_optional_text(metadata.image_key, "metadata.image_key")


def _validate_metadata_edit_document(metadata: LaunchMetadataEditDocument) -> None:
    if not isinstance(metadata, LaunchMetadataEditDocument):
        raise TypeError("metadata must be a LaunchMetadataEditDocument")
    if not isinstance(metadata.description, str):
        raise TypeError("metadata.description must be a string")
    _validate_optional_text(metadata.website_url, "metadata.website_url")
    _validate_optional_text(metadata.twitter_url, "metadata.twitter_url")
    _validate_optional_text(metadata.telegram_url, "metadata.telegram_url")
    _validate_optional_text(metadata.discord_url, "metadata.discord_url")
    _validate_optional_text(metadata.image_key, "metadata.image_key")


def _validate_required_metadata_text(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _canonical_text(value) == "":
        raise ValueError(f"{name} must not be empty")


def _validate_optional_text(value: Any, name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")


def _validate_image_descriptor(image: LaunchSessionImageDescriptor) -> None:
    if not isinstance(image, LaunchSessionImageDescriptor):
        raise TypeError("image must be a LaunchSessionImageDescriptor")
    _validate_bytes32(image.sha256, "image.sha256")
    _validate_content_type(image.content_type)
    _validate_image_length(image.content_length, "image.content_length")


def _validate_attribution_authorization(authorization: LaunchAttributionAuthorization) -> None:
    if not isinstance(authorization, LaunchAttributionAuthorization):
        raise TypeError("authorization must be a LaunchAttributionAuthorization")
    _validate_bytes32(authorization.nonce, "authorization.nonce")
    _assert_uint(authorization.deadline, "authorization.deadline")
    _validate_signature(authorization.signature, "authorization.signature")


def _validate_session_create_request(request: LaunchSessionCreateRequest) -> None:
    if not isinstance(request, LaunchSessionCreateRequest):
        raise TypeError("request must be a LaunchSessionCreateRequest")
    _assert_chain_id(request.chain_id)
    _validate_address(request.wallet, "wallet")
    _validate_session_metadata(request.metadata)
    _validate_attribution_authorization(request.authorization)
    if request.image is not None:
        _validate_image_descriptor(request.image)


def _validate_publish_request(body: LaunchSessionPublishRequest) -> None:
    if not isinstance(body, LaunchSessionPublishRequest):
        raise TypeError("body must be a LaunchSessionPublishRequest")
    _assert_chain_id(body.chain_id)
    _validate_bytes32(body.transaction_hash, "transaction_hash")


def _validate_metadata_authorization(authorization: LaunchMetadataAuthorization) -> None:
    if not isinstance(authorization, LaunchMetadataAuthorization):
        raise TypeError("authorization must be a LaunchMetadataAuthorization")
    _assert_uint(authorization.expected_version, "authorization.expected_version")
    _validate_bytes32(authorization.nonce, "authorization.nonce")
    _assert_uint(authorization.deadline, "authorization.deadline")
    _validate_signature(authorization.signature, "authorization.signature")


def _validate_image_upload_request(body: LaunchImageUploadRequest) -> None:
    if not isinstance(body, LaunchImageUploadRequest):
        raise TypeError("body must be a LaunchImageUploadRequest")
    _path_component(body.upload_id, "upload_id")
    _validate_content_type(body.content_type)
    _validate_image_length(body.content_length, "content_length")
    _validate_metadata_edit_document(body.metadata)
    if body.metadata.image_key is None or body.metadata.image_key == "":
        raise ValueError("image upload metadata requires image_key")
    _validate_metadata_authorization(body.authorization)


def _validate_image_bytes(body: bytes) -> None:
    if not isinstance(body, bytes):
        raise TypeError("image body must be bytes")
    _validate_image_length(len(body), "image body length")


def _validate_image_length(value: int, name: str) -> None:
    _assert_uint(value, name)
    if value < 1 or value > MAX_LAUNCH_IMAGE_BYTES:
        raise ValueError(f"{name} must be between 1 byte and {MAX_LAUNCH_IMAGE_BYTES} bytes")


def _validate_content_type(value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError("content_type must be a string")
    if value not in LAUNCH_IMAGE_CONTENT_TYPES:
        raise ValueError("content_type must be image/png, image/jpeg, or image/webp")


def _validate_address(value: Any, name: str) -> None:
    if not isinstance(value, str) or not is_address(value) or value.lower() == _ZERO_ADDRESS:
        raise ValueError(f"{name} must be a nonzero address")


def _validate_bytes32(value: Any, name: str) -> None:
    if not isinstance(value, str) or _HEX_32_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 0x-prefixed bytes32 hex string")


def _validate_signature(value: Any, name: str) -> None:
    if not isinstance(value, str) or _SIGNATURE_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 65-byte 0x-prefixed signature")


def _assert_chain_id(value: Any) -> None:
    _assert_uint(value, "chain_id")
    if value == 0:
        raise ValueError("chain_id must be positive")


def _assert_uint(value: Any, name: str) -> None:
    if not _is_int(value) or value < 0 or value > _MAX_UINT256:
        raise ValueError(f"{name} must be a uint256 integer")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_header_value(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > 4096 or _contains_header_control(value):
        raise ValueError(f"{name} must be a nonempty safe header value")


def _validate_signed_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise TypeError("upload.headers must be a mapping")
    validated: dict[str, str] = {}
    seen: set[str] = set()
    for name, value in headers.items():
        if not isinstance(name, str) or _HEADER_NAME_RE.fullmatch(name) is None:
            raise ValueError("upload header name is invalid")
        if not isinstance(value, str) or _contains_header_control(value):
            raise ValueError("upload header value is invalid")
        lowered = name.lower()
        if lowered in seen:
            raise ValueError("upload headers cannot repeat a case-insensitive name")
        seen.add(lowered)
        validated[name] = value
    return validated


def _contains_header_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _path_component(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > 256 or _contains_header_control(value):
        raise ValueError(f"{name} must be a nonempty safe path component")
    if any(character in value for character in "/\\?#%"):
        raise ValueError(f"{name} contains an unsafe path character")
    return quote(value, safe="-._~")


def _validate_query_value(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > 4096 or _contains_header_control(value):
        raise ValueError(f"{name} must be a nonempty safe query value")


def _validate_direct_upload_url(value: Any) -> None:
    if not isinstance(value, str) or not value or _contains_header_control(value) or any(
        character.isspace() for character in value
    ):
        raise ValueError("upload.url must be a safe HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("upload.url must be a safe HTTPS URL") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise ValueError("upload.url must be a safe HTTPS URL")


def _normalize_api_base_url(value: Any) -> str:
    if not isinstance(value, str) or not value or _contains_header_control(value) or any(
        character.isspace() for character in value
    ):
        raise ValueError("base_url must be a safe HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("base_url must be a safe HTTPS URL") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise ValueError("base_url must be a safe HTTPS URL")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a positive finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    return timeout


def _safe_error_code(value: str) -> str:
    return value if _ERROR_CODE_RE.fullmatch(value) is not None else "INVALID_REQUEST"
