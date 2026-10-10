"""Launch runner wallet boundaries and durable, redacted diagnostic artifacts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
import re
import time
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from web3 import Web3
from eth_account import Account
from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_account.messages import encode_typed_data

from black_market_sdk.lifecycle_rpc import hex_bytes, quantity, rpc
from black_market_sdk import (
    LAUNCH_IMAGE_CONTENT_TYPES, MAX_LAUNCH_IMAGE_BYTES, LaunchSessionImageDescriptor,
    LaunchSessionMetadata, canonical_launch_metadata_hash, sha256_hex,
)


def read_example_file(path, *, limit, code, private=False):
    """Bound a regular local file read without following a final symlink or FIFO."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit
                    or private and (before.st_uid != os.getuid() or before.st_mode & 0o077)):
                raise ExampleFailure("local input must be a bounded regular file; keystores must be owner-only", code=code)
            body = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if (len(body) != before.st_size or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    != (before.st_size, before.st_mtime_ns, before.st_ctime_ns)):
                raise ExampleFailure("local input changed while being read", code=code)
            return body
    except (OSError, ValueError):
        raise ExampleFailure("local input could not be read safely", code=code) from None


def load_example_keystore(path, password, redactor):
    """Use eth-account's authenticated V3 decryption; never expose decrypt errors."""
    redactor.remember(password)
    body = read_example_file(path, limit=64 * 1024, code="KEYSTORE_FILE_INVALID", private=True)
    try:
        document = json.loads(body.decode("utf-8"))
        crypto = document["crypto"]
        params = crypto["kdfparams"]
        if (document["version"] != 3 or crypto["cipher"] != "aes-128-ctr"
                or params["dklen"] != 32 or len(password.encode("utf-8")) > 4096):
            raise ValueError("unsupported V3 format")
        if crypto["kdf"] == "scrypt":
            n, r, p = params["n"], params["r"], params["p"]
            if (any(type(value) is not int or value <= 0 for value in (n, r, p))
                    or n < 2 or n & (n - 1) or n > 2**18 or r > 32 or p > 16
                    or n * r * p > 2**21
                    or 128 * n * r + 128 * r * p + 256 * r > 320 * 1024 * 1024):
                raise ValueError("unsupported scrypt resource limits")
        elif crypto["kdf"] == "pbkdf2":
            if params["prf"] != "hmac-sha256" or type(params["c"]) is not int or not 1 <= params["c"] <= 1_000_000:
                raise ValueError("unsupported PBKDF2 resource limits")
        else:
            raise ValueError("unsupported V3 KDF")
        key = Account.decrypt(document, password)
        redactor.remember(key)
        redactor.remember(key.hex())
        account = Account.from_key(key)
        declared = document.get("address")
        if declared is not None and account.address.lower() != ("0x" + declared).lower():
            raise ValueError("keystore address mismatch")
        return key
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ExampleFailure("V3 keystore format, resource limits or authentication is invalid", code="KEYSTORE_DECRYPT_FAILED") from None


def load_example_image(path):
    """Detect accepted image types from frozen bytes, not a filename extension."""
    body = read_example_file(path, limit=MAX_LAUNCH_IMAGE_BYTES, code="INVALID_IMAGE")
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif body.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        raise ExampleFailure("EXAMPLE_IMAGE_PATH must contain PNG, JPEG or WebP bytes", code="INVALID_IMAGE")
    if content_type not in LAUNCH_IMAGE_CONTENT_TYPES:
        raise ExampleFailure("image content type is not accepted by the launch API", code="INVALID_IMAGE")
    return body, LaunchSessionImageDescriptor(sha256_hex(body), content_type, len(body))


def read_published_image(url, descriptor, *, timeout_seconds=15):
    """One bounded public GET, retaining identity or inferred transformation evidence."""
    source = mainnet_url(url, api=True)
    timeout = min(15, timeout_seconds)
    if timeout <= 0:
        raise ExampleFailure("published image verification deadline expired", code="IMAGE_PUBLICATION_UNAVAILABLE")

    class NoRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = build_opener(ProxyHandler({}), HTTPHandler(), HTTPSHandler(), NoRedirectHandler())
    request = Request(source, method="GET", headers={"Accept": "image/*",
                                                   "Accept-Encoding": "identity"})
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            length = response.headers.get("Content-Length")
            if (response.getcode() != 200 or re.fullmatch(r"image/[a-z0-9!#$&^_.+-]+", content_type) is None
                    or length is not None and (not length.isascii() or not length.isdecimal()
                                               or not 0 < int(length) <= MAX_LAUNCH_IMAGE_BYTES)):
                raise ValueError("published image has an invalid status, type or bounded length")
            body = bytearray()
            while True:
                chunk = response.read1(min(8192, MAX_LAUNCH_IMAGE_BYTES - len(body) + 1))
                if time.monotonic() - started > timeout:
                    raise ValueError("published image retrieval deadline exceeded")
                if not chunk:
                    break
                if len(body) + len(chunk) > MAX_LAUNCH_IMAGE_BYTES:
                    raise ValueError("published image exceeds body limit")
                body.extend(chunk)
            if not body or length is not None and int(length) != len(body):
                raise ValueError("published image body is empty or truncated")
            digest = "0x" + hashlib.sha256(body).hexdigest()
            identical = digest.lower() == descriptor.sha256.lower()
            if identical and (content_type != descriptor.content_type or len(body) != descriptor.content_length):
                raise ValueError("identical published image changes its descriptor")
            return {"url": source, "status": 200, "contentType": content_type, "contentLength": len(body),
                    "sha256": digest, "matchesUploadedSha256": identical,
                    "matchesUploadedLength": len(body) == descriptor.content_length, "transformed": not identical,
                    "transformationEvidence": None if identical else
                    "[INFERENCE] Served image bytes differ from the signed upload; API transformation within the image family.",
                    "originalImage": dataclasses.asdict(descriptor),
                    "durationMs": int((time.monotonic() - started) * 1000)}
    except (OSError, ValueError):
        raise ExampleFailure("published image GET did not return a readable bounded image",
                             code="IMAGE_PUBLICATION_UNAVAILABLE", diagnostic={"url": source}) from None


def validate_metadata_binding(document, expected, *, image_key=None):
    """Bind canonical signed fields; only a verified API image may add its imageKey."""
    fields = ("name", "symbol", "description")
    if not isinstance(document, dict) or not all(field in document for field in fields):
        raise ExampleFailure("API metadata schema is incomplete", code="API_SESSION_MISMATCH")
    if image_key is not None:
        expected = LaunchSessionMetadata(**{**dataclasses.asdict(expected), "image_key": image_key})
    try:
        observed = LaunchSessionMetadata(
            name=document["name"], symbol=document["symbol"], description=document["description"],
            website_url=document.get("websiteUrl"), twitter_url=document.get("twitterUrl"),
            telegram_url=document.get("telegramUrl"), discord_url=document.get("discordUrl"),
            image_key=document.get("imageKey"),
        )
        matches = canonical_launch_metadata_hash(observed) == canonical_launch_metadata_hash(expected)
    except (ValueError, TypeError):
        matches = False
    if not matches:
        raise ExampleFailure("API changed the original signed metadata", code="API_SESSION_MISMATCH")


def validate_metadata_session(session, expected, *, chain_id, wallet, session_id=None,
                              transaction_hash=None, token=None, staging=False, image=None, uploading=False):
    """Validate the API schema, original signed metadata and optional image binding."""
    def check(condition):
        if not condition:
            raise ExampleFailure("API session schema or original intent differs", code="API_SESSION_MISMATCH")
    check(isinstance(session, dict))
    identifier = session.get("sessionId")
    check(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier) is not None)
    check(session_id is None or identifier == session_id)
    check(type(session.get("chainId")) is int and session["chainId"] == chain_id)
    check(isinstance(session.get("wallet"), str) and session["wallet"].lower() == wallet.lower())
    status = session.get("status")
    statuses = {"ready_to_launch", "transaction_submitted", "awaiting_indexer",
                "optimistic", "final", "reorged", "expired", "rejected"}
    if image is not None:
        statuses.update({"awaiting_upload", "verifying_image"})
    check(status in statuses)
    canonical = status if status in {"optimistic", "final", "reorged"} else "pending"
    check(session.get("canonicalStatus") == canonical)
    metadata_states = {"ready", "needs_owner_action"} if status in {"optimistic", "final"} else {
        "none" if status == "reorged" else "ready" if status == "ready_to_launch" else "queued"}
    check(session.get("metadataStatus") in metadata_states)
    check("image" in session)
    if image is None:
        check(session["image"] is None and session.get("imageStatus") == "none")
        check("upload" not in session and "imageUrl" not in session)
    else:
        observed = session["image"]
        check(isinstance(observed, dict) and observed.get("sha256", "").lower() == image.sha256.lower()
              and observed.get("contentType") == image.content_type
              and type(observed.get("contentLength")) is int and observed["contentLength"] == image.content_length)
        width, height = observed.get("width"), observed.get("height")
        check("width" in observed and "height" in observed and (width is None) == (height is None))
        check(width is None or all(type(value) is int and 1 <= value <= 4096 for value in (width, height)))
        image_state = ("verifying" if status == "verifying_image" else "upload_pending") if width is None else (
            "rejected" if status == "rejected" else "ready")
        check(session.get("imageStatus") == image_state)
        if staging:
            check(image_state == "ready")
        if "upload" in session:
            upload = session["upload"]
            check(isinstance(upload, dict) and isinstance(upload.get("headers"), dict))
            headers = {key.lower(): value for key, value in upload["headers"].items()}
            upload_id = headers.get("x-amz-meta-upload-id")
            check(headers.get("content-type") == image.content_type and headers.get("if-none-match") == "*"
                  and isinstance(upload_id, str) and re.fullmatch(
                      r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", upload_id) is not None)
            parts = urlsplit(upload.get("url", ""))
            check(parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                  and not parts.fragment and unquote(parts.path).endswith(f"/launches/{chain_id}/{wallet.lower()}/{upload_id}"))
        if "imageUrl" in session:
            parts = urlsplit(session["imageUrl"])
            check(session.get("token") is not None and parts.scheme == "https" and parts.hostname
                  and not parts.username and not parts.password and not parts.query and not parts.fragment
                  and re.fullmatch(r"/launches/" + str(chain_id) + "/" + re.escape(session["token"].lower())
                                   + r"/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", parts.path) is not None)
    check(type(session.get("retryable")) is bool and session["retryable"] == (status not in {"expired", "rejected"}))
    def timestamp(field):
        value = session.get(field)
        check(isinstance(value, str) and re.fullmatch(r"(0|[1-9][0-9]*)", value) is not None)
        number = int(value)
        check(number < 2**256)
        return number
    created = timestamp("createdAt")
    check(timestamp("updatedAt") >= created)
    expiry = timestamp("sessionExpiresAt")
    check(expiry > created)
    if "publishedAt" in session:
        check(timestamp("publishedAt") >= created)
    for field, pattern in (("transactionHash", r"0x[0-9a-fA-F]{64}"), ("token", r"0x[0-9a-fA-F]{40}")):
        check(field in session)
        value = session[field]
        check(value is None or isinstance(value, str) and re.fullmatch(pattern, value) is not None)
    bound = session["transactionHash"]
    check(bound is None or transaction_hash is not None and bound.lower() == transaction_hash.lower())
    check(session["token"] is None or token is not None and session["token"].lower() == token.lower())
    check(canonical == "pending" or bool(bound and session["token"]))
    check(status not in {"transaction_submitted", "awaiting_indexer"} or bound is not None)
    if staging:
        check(status == "ready_to_launch" and bound is None and session["token"] is None)
    if uploading:
        check(image is not None and status in {"awaiting_upload", "verifying_image", "ready_to_launch"}
              and bound is None and session["token"] is None)
    if bound is None:
        check(expiry > time.time())
    check(isinstance(session.get("metadata"), dict) and all(field in session["metadata"] for field in
          ("name", "symbol", "description", "websiteUrl", "twitterUrl", "telegramUrl", "discordUrl")))
    validate_metadata_binding(session.get("metadata"), expected)


def save_private_state(path, value, *, create=False):
    """Fsync a private JSON record and its directory before returning."""
    path = Path(path)
    temporary = path.with_name(path.name + "." + os.urandom(8).hex() + ".tmp")
    destination = path if create else temporary
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not create:
            os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"signature|privatekey|secret|capability|authorization|signedheaders|password|"
    r"apikey|accesskey|accesstoken|bearer|cookie|mnemonic|idempotencykey|credential|rawtransaction",
    re.IGNORECASE,
)
_SECRET_ENV_KEY = re.compile(r"privatekey|secret|password|passphrase|apikey|accesskey|token|auth|credential|mnemonic|cookie", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SIGNATURE = re.compile(r"(?<![\w])0x[0-9a-fA-F]{130}(?![0-9a-fA-F])")
_LABELLED_SECRET = re.compile(
    r"([\"']?(?:signature|private[_-]?key|capability|authorization|password|"
    r"api[_-]?key|secret|access[_-]?token)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"'\r\n]*[\"']|[^\s,;}]+)", re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s\"',;}>]+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def portable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: portable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): portable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class Redactor:
    def __init__(self):
        self.secrets: set[str] = set()
        for name in os.environ:
            if _SECRET_ENV_KEY.search(re.sub(r"[^a-z0-9]", "", name.lower())):
                self.remember(os.environ[name])

    def remember(self, value):
        if isinstance(value, str) and len(value) >= 4:
            self.secrets.add(value)
            if re.fullmatch(r"0x[0-9a-fA-F]{64,}", value):
                self.secrets.add(value[2:])
        elif isinstance(value, (bytes, bytearray)):
            self.secrets.add("0x" + bytes(value).hex())
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.remember(item)
        elif isinstance(value, Mapping):
            for item in value.values():
                self.remember(item)

    def remember_url(self, value):
        if not isinstance(value, str):
            return
        self.remember(value)
        try:
            parts = urlsplit(value)
            for item in (parts.username, parts.password, parts.path, *parts.path.split("/"),
                         *(item for _, item in parse_qsl(parts.query))):
                if item and item != "/":
                    self.secrets.add(item)
                    self.secrets.add(unquote(item))
        except ValueError:
            pass

    def learn(self, value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if _SECRET_KEY.search(normalized):
                    self.remember(item)
                else:
                    self.learn(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.learn(item)

    def text(self, value: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, REDACTED) if len(secret) >= 4 else re.sub(r"(?<!\w)" + re.escape(secret) + r"(?!\w)", REDACTED, value)
        value = _SIGNATURE.sub(REDACTED, value)
        value = _BEARER.sub("Bearer " + REDACTED, value)
        value = _LABELLED_SECRET.sub(lambda match: match.group(1) + REDACTED, value)

        def scrub_url(match):
            try:
                parts = urlsplit(match.group())
                host = parts.hostname or "[invalid-host]"
                if ":" in host:
                    host = "[" + host + "]"
                if parts.port is not None:
                    host += ":" + str(parts.port)
                # RPC credentials can live in a remote provider's path, not only its query.
                path = parts.path if parts.hostname in {"127.0.0.1", "localhost", "::1"} else "/" + REDACTED
                return urlunsplit((parts.scheme, host, path, REDACTED if parts.query else "", ""))
            except ValueError:
                return "[REDACTED-URL]"

        return _URL.sub(scrub_url, value)

    def public_api_origin(self, value):
        """The reviewed API origin is public evidence; URL credentials and paths are not."""
        try:
            origin = mainnet_url(value, api=True)
            if urlsplit(origin).path:
                return REDACTED
            return origin
        except (TypeError, ValueError):
            return REDACTED

    def __call__(self, value):
        value = portable(value)
        self.learn(value)
        if isinstance(value, Mapping):
            return {
                str(key): REDACTED if _SECRET_KEY.search(re.sub(r"[^a-z0-9]", "", str(key).lower()))
                else self.public_api_origin(item) if str(key) == "apiOrigin" else self(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def error_details(error: BaseException, seen=None) -> dict:
    seen = set() if seen is None else seen
    if id(error) in seen:
        return {"name": type(error).__name__, "message": "cyclic exception cause"}
    seen.add(id(error))
    detail = {
        "name": type(error).__name__,
        "code": getattr(error, "code", None),
        "message": str(error),
        "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    }
    for attr, key in (("data", "revertData"), ("status", "httpStatus"), ("retry_after_ms", "retryAfterMs"), ("method", "rpcMethod"), ("diagnostic", "diagnostic")):
        if hasattr(error, attr):
            detail[key] = portable(getattr(error, attr))
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if cause is not None:
        detail["cause"] = error_details(cause, seen)
    return detail


def loopback_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("a safe explicit loopback URL is required")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("owned local endpoints must use literal loopback HTTP(S)")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("loopback endpoints cannot contain credentials, queries, or fragments")
    if parts.port is None:
        raise ValueError("loopback endpoints require an explicit owned service port")
    return value.rstrip("/")


def mainnet_url(value: str, *, api: bool = False) -> str:
    """Mainnet source is HTTPS; only API integration may use owned loopback HTTP."""
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("an explicit safe HTTPS URL is required")
    parts = urlsplit(value)
    if parts.fragment or not parts.hostname:
        raise ValueError("endpoint must have a hostname and no fragment")
    if api and (parts.username is not None or parts.password is not None or parts.query):
        raise ValueError("API endpoints cannot contain credentials or queries")
    if api and parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        return loopback_url(value)
    if parts.scheme != "https":
        raise ValueError("mainnet RPC and nonlocal API endpoints require HTTPS")
    _ = parts.port  # Reject malformed ports before constructing transport.
    return value.rstrip("/")




PRICE_RESPONSE_MAX_BYTES = 1024 * 1024
PRICE_REQUEST_TIMEOUT_SECONDS = 15


def read_price_snapshot(api_url, chain_id):
    """One bounded, uncached GET to the explicitly selected API, without redirects."""
    base = mainnet_url(api_url, api=True)
    parts = urlsplit(base)
    origin = urlunsplit((parts.scheme, parts.netloc.lower(), "", "", ""))
    url = base + "/api/v1/token-prices?" + urlencode({"chainId": chain_id, "limit": 100})

    class NoRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    # Match the SDK transport: no proxy discovery or redirect forwarding.
    opener = build_opener(ProxyHandler({}), HTTPHandler(), HTTPSHandler(), NoRedirectHandler())
    request = Request(url, method="GET", headers={
        "Accept": "application/json", "Accept-Encoding": "identity", "Cache-Control": "no-cache",
    })
    started = time.monotonic()
    try:
        with opener.open(request, timeout=PRICE_REQUEST_TIMEOUT_SECONDS) as response:
            if response.getcode() != 200:
                raise ValueError("price API did not return HTTP 200")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isascii() or not length.isdecimal()
                                       or int(length) > PRICE_RESPONSE_MAX_BYTES):
                raise ValueError("price API response exceeds the body limit")
            body = bytearray()
            while True:
                chunk = response.read1(min(8192, PRICE_RESPONSE_MAX_BYTES - len(body) + 1))
                if time.monotonic() - started > PRICE_REQUEST_TIMEOUT_SECONDS:
                    raise ValueError("price API response exceeded the retrieval deadline")
                if not chunk:
                    break
                if len(body) + len(chunk) > PRICE_RESPONSE_MAX_BYTES:
                    raise ValueError("price API response exceeds the body limit")
                body.extend(chunk)
            snapshot = json.loads(body)
    except (OSError, ValueError, UnicodeError) as error:
        raise ExampleFailure("selected Abyss API price snapshot is unavailable or invalid",
                             code="PRICE_QUOTE_UNAVAILABLE") from error
    return snapshot, origin, int(time.time())


# Reviewed chain4663 router/executor from the reference app's trading/deployments.
# Neither deployment nor the router's swap ABI is exported by the Python SDK.
KYBER_ROUTER = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"
KYBER_EXECUTOR = "0x8F10B468b06c6FD214B65F87778827F7D113f996"
KYBER_NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
KYBER_EXECUTOR_IMPLEMENTATIONS = {
    "0x4f1423cce26c09fb31d96eafe702fc5e22421f6a", "0xd7a5c6b52756e795f680b0f7e0c2f21281dde6ef",
}
KYBER_SWAP_EXECUTION_TYPE = "(address,address,bytes,(address,address,address[],uint256[],address[],uint256[],address,uint256,uint256,uint256,bytes),bytes)"
KYBER_SWAP_SELECTOR = Web3.keccak(text="swap(" + KYBER_SWAP_EXECUTION_TYPE + ")")[:4]
CONVERSION_SLIPPAGE_BPS = 50


def read_kyber_build(api_url, *, quote_asset, amount_in, escrow, deadline):
    """One bounded fresh API build; never send conversion calldata to the chain."""
    base = mainnet_url(api_url, api=True)
    request_body = {"tokenIn": "0x" + "00" * 20, "tokenOut": quote_asset,
                    "amountIn": str(amount_in), "sender": escrow, "recipient": escrow,
                    "slippageBps": CONVERSION_SLIPPAGE_BPS, "deadline": str(deadline)}

    class NoRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = build_opener(ProxyHandler({}), HTTPHandler(), HTTPSHandler(), NoRedirectHandler())
    request = Request(base + "/api/v1/quote/kyber/build?chainId=4663", method="POST",
                      data=json.dumps(request_body).encode("utf-8"),
                      headers={"Accept": "application/json", "Accept-Encoding": "identity", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with opener.open(request, timeout=PRICE_REQUEST_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            if response.getcode() != 200 or length is not None and (
                    not length.isascii() or not length.isdecimal() or int(length) > PRICE_RESPONSE_MAX_BYTES):
                raise ValueError("invalid bounded Kyber response")
            body = bytearray()
            while True:
                chunk = response.read1(min(8192, PRICE_RESPONSE_MAX_BYTES - len(body) + 1))
                if time.monotonic() - started > PRICE_REQUEST_TIMEOUT_SECONDS:
                    raise ValueError("Kyber retrieval deadline exceeded")
                if not chunk:
                    break
                if len(body) + len(chunk) > PRICE_RESPONSE_MAX_BYTES:
                    raise ValueError("Kyber response exceeds body limit")
                body.extend(chunk)
            return json.loads(body)
    except HTTPError as error:
        try:
            raw = error.read(PRICE_RESPONSE_MAX_BYTES + 1)
            failure = json.loads(raw) if len(raw) <= PRICE_RESPONSE_MAX_BYTES else {}
        except (OSError, ValueError):
            failure = {}
        if not isinstance(failure, dict):
            failure = {}
        raise ExampleFailure(failure.get("error") if isinstance(failure.get("error"), str) else f"Kyber API returned HTTP {error.code}",
                             code=failure.get("code") if isinstance(failure.get("code"), str) else "KYBER_QUOTE_UNAVAILABLE",
                             diagnostic=failure) from None
    except (OSError, ValueError, UnicodeError):
        raise ExampleFailure("selected Abyss API Kyber build is unavailable or invalid", code="KYBER_QUOTE_UNAVAILABLE") from None


def validate_kyber_build(build, *, quote_asset, amount_in, escrow, deadline, now):
    """Decode the reviewed swap entry point and bind every native conversion term."""
    def check(condition):
        if not condition:
            raise ExampleFailure("KyberSwap build changes a reviewed currency, target, recipient, amount, deadline or protection",
                                 code="INVALID_KYBER_BUILD", diagnostic=build)

    def same(left, right):
        return isinstance(left, str) and left.lower() == right.lower()

    def uint(value):
        check(isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 78
              and (value == "0" or not value.startswith("0")) and int(value) < 2**256)
        return int(value)

    check(isinstance(build, dict) and type(build.get("chainId")) is int and build["chainId"] == 4663
          and build.get("source") == "kyberswap" and same(build.get("tokenIn"), KYBER_NATIVE_TOKEN)
          and same(build.get("tokenOut"), quote_asset) and same(build.get("routerAddress"), KYBER_ROUTER))
    check(uint(build.get("amountIn")) == amount_in and 0 < amount_in < 2**255)
    amount_out, minimum = uint(build.get("amountOut")), uint(build.get("amountOutMinimum"))
    check(amount_out > 0 and minimum > 0 and minimum == amount_out * (10000 - CONVERSION_SLIPPAGE_BPS) // 10000)
    check(type(build.get("expiresAt")) is int and build["expiresAt"] == deadline and now < deadline <= now + 1200)
    transaction = build.get("transaction")
    check(isinstance(transaction, dict) and same(transaction.get("to"), KYBER_ROUTER)
          and uint(transaction.get("value")) == amount_in)
    data = transaction.get("data")
    check(isinstance(data, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", data) is not None
          and len(data) <= 131072 * 2 + 2)
    raw = bytes.fromhex(data[2:])
    check(raw[:4] == KYBER_SWAP_SELECTOR)
    try:
        call_target, approve_target, target_data, desc, _client_data = decode([KYBER_SWAP_EXECUTION_TYPE], raw[4:])[0]
    except (DecodingError, ValueError, TypeError, OverflowError):
        raise ExampleFailure("KyberSwap calldata is not a decodable reviewed swap", code="INVALID_KYBER_BUILD") from None
    src, dst, src_receivers, src_amounts, fee_receivers, fee_amounts, recipient, exact, floor, flags, permit = desc
    check(same(src, KYBER_NATIVE_TOKEN) and same(dst, quote_asset) and same(recipient, escrow)
          and exact == amount_in and floor == minimum and same(call_target, KYBER_EXECUTOR)
          and same(approve_target, "0x" + "00" * 20) and not src_receivers and not src_amounts
          and not fee_receivers and not fee_amounts and flags & 0x42 == 0 and not permit
          and len(target_data) >= 52 and (len(target_data) - 20) % 32 == 0
          and hex_bytes(target_data[:20]) in KYBER_EXECUTOR_IMPLEMENTATIONS)
    impact = build.get("priceImpactBps")
    check(impact is None or type(impact) is int and -10000 <= impact <= 10000)
    return {"source": "kyberswap", "apiResponse": build, "quoteAsset": quote_asset, "escrow": escrow,
            "inputAsset": "0x" + "00" * 20, "inputAmount": str(amount_in),
            "amountOut": str(amount_out), "amountOutMinimum": str(minimum), "slippageBps": CONVERSION_SLIPPAGE_BPS,
            "expiresAt": deadline, "priceImpactBps": impact, "transaction": transaction}


class ExampleFailure(RuntimeError):
    def __init__(self, message: str, *, code: str, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic



class LocalSigningWallet:
    """Exact local signer, also usable against an owned chain4663 Anvil."""

    def __init__(self, client, artifacts, *, private_key: str, creator: str, chain_id: int):
        artifacts.redact.remember(private_key)
        self.account = Account.from_key(private_key)
        artifacts.redact.remember(bytes(self.account.key))
        artifacts.redact.remember(bytes(self.account.key).hex())
        if self.account.address.lower() != creator.lower():
            raise ExampleFailure("signing key does not match launch creator", code="WALLET_IDENTITY_MISMATCH")
        self.client = client
        self.artifacts = artifacts
        self.chain_id = chain_id
        self.signature_guard = None

    def check_chain(self):
        if quantity(rpc(self.client, "eth_chainId", [])) != self.chain_id:
            raise ExampleFailure("wallet RPC chain differs from configured chain", code="WALLET_IDENTITY_MISMATCH")

    def sign_typed_data(self, typed_data, *, verifying_contract: str):
        self.check_chain()
        domain = typed_data.get("domain", {})
        if (quantity(domain.get("chainId", 0)) != self.chain_id
                or domain.get("name") != "Abyss Launch Attribution" or domain.get("version") != "1"
                or typed_data.get("primaryType") != "LaunchAttribution"
                or quantity(typed_data.get("message", {}).get("chainId", 0)) != self.chain_id
                or str(domain.get("verifyingContract", "")).lower() != verifying_contract.lower()
                or str(typed_data.get("message", {}).get("wallet", "")).lower() != self.account.address.lower()):
            raise ExampleFailure("attribution domain or wallet differs from launch", code="WALLET_IDENTITY_MISMATCH")
        message = encode_typed_data(full_message=typed_data)
        if self.signature_guard is not None:
            self.signature_guard()
            self.check_chain()
        signed = self.account.sign_message(message)
        signature = hex_bytes(signed.signature)
        self.artifacts.redact.remember(signature)
        self.artifacts.redact.remember(signature[2:])
        if Account.recover_message(message, signature=signature).lower() != self.account.address.lower():
            raise ExampleFailure("typed data signature recovered another creator", code="WALLET_IDENTITY_MISMATCH")
        return signature

    def prepare_transaction(self, transaction):
        self.check_chain()
        envelope = dict(transaction)
        if (quantity(envelope.get("chainId", 0)) != self.chain_id
                or str(envelope.get("from", "")).lower() != self.account.address.lower()):
            raise ExampleFailure("transaction sender or chain differs from wallet", code="WALLET_IDENTITY_MISMATCH")
        # SDK gas/value/calldata are authoritative. Never estimate or rewrite them.
        if not {"gas", "value", "data", "to"} <= envelope.keys():
            raise ExampleFailure("SDK transaction lacks an exact execution envelope", code="TRANSACTION_IDENTITY_MISMATCH")
        if "nonce" not in envelope:
            envelope["nonce"] = quantity(rpc(self.client, "eth_getTransactionCount", [self.account.address, "pending"]))
        if "gasPrice" not in envelope:
            if "maxFeePerGas" not in envelope and "maxPriorityFeePerGas" not in envelope:
                envelope["gasPrice"] = quantity(rpc(self.client, "eth_gasPrice", []))
            elif "maxPriorityFeePerGas" not in envelope:
                envelope["maxPriorityFeePerGas"] = quantity(rpc(self.client, "eth_maxPriorityFeePerGas", []))
            elif "maxFeePerGas" not in envelope:
                block = rpc(self.client, "eth_getBlockByNumber", ["latest", False])
                envelope["maxFeePerGas"] = 2 * quantity(block["baseFeePerGas"]) + quantity(envelope["maxPriorityFeePerGas"])
        return envelope

    def send_transaction(self, kind: str, transaction):
        envelope = self.prepare_transaction(transaction)
        signing = {key: value for key, value in envelope.items() if key != "from"}
        signing["to"] = Web3.to_checksum_address(signing["to"])
        if self.signature_guard is not None:
            self.signature_guard()
            self.check_chain()
        signed = self.account.sign_transaction(signing)
        raw = hex_bytes(signed.raw_transaction)
        transaction_hash = hex_bytes(Web3.keccak(signed.raw_transaction))
        self.artifacts.redact.remember(raw)
        self.artifacts.redact.remember(raw[2:])
        self.artifacts.save("signed-" + transaction_hash[2:] + ".private.json",
                            {"rawTransaction": raw, "transactionHash": transaction_hash, "transaction": envelope}, private=True)
        # Hash + exact unsigned envelope are fsynced BEFORE even attempting transport.
        row = self.artifacts.broadcast_attempt(kind, envelope, transaction_hash)
        returned_hash = hex_bytes(rpc(self.client, "eth_sendRawTransaction", [raw]))
        if returned_hash.lower() != transaction_hash.lower():
            raise ExampleFailure("RPC returned a different signed transaction hash", code="BROADCAST_HASH_MISMATCH",
                               diagnostic={"computedHash": transaction_hash, "returnedHash": returned_hash})
        row.update(broadcastStatus="submitted", submittedAt=utc_now())
        self.artifacts.save("receipts.json", self.artifacts.receipts)
        self.artifacts.event("transaction-submitted", **row)
        return row

class Artifacts:
    def __init__(self, directory: Path, case_id: str, *, resume: bool = False):
        if resume:
            # Resume appends to an existing run's evidence; never reinitialize.
            if not directory.is_dir():
                raise ExampleFailure(f"resume requires an existing run directory: {directory}", code="MISSING_RECOVERY")
        else:
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.directory = directory
        self.redact = Redactor()
        self.case_id = case_id
        self.stage = "configuration"
        self.started_at = utc_now()
        self.started = time.monotonic()
        self.receipts: list[dict] = []
        self.result = {
            "schema": "black-market.launch-example-result.v1", "sdk": "python", "caseId": case_id,
            "status": "failed", "stage": self.stage, "scope": "end-to-end", "execution": "not-run",
            "chain": {"status": "not-run", "transactions": []}, "api": {"status": "not-run"},
            "startedAt": self.started_at, "completedAt": None,
        }
        if resume:
            descriptor = os.open(directory / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        else:
            descriptor = os.open(directory / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        self.journal = os.fdopen(descriptor, "a" if resume else "w", encoding="utf-8")
        if not resume:
            self.save("receipts.json", self.receipts)
        self.event("resumed" if resume else "started")

    def save(self, name: str, value, *, private: bool = False):
        payload = portable(value) if private else self.redact(value)
        temporary = self.directory / (name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / name)
            directory_descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def event(self, event: str, **observations):
        row = self.redact({"timestamp": utc_now(), "sdk": "python", "caseId": self.case_id, "stage": self.stage,
                           "event": event, "elapsedMs": round((time.monotonic() - self.started) * 1000, 3), **observations})
        self.journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def enter(self, stage: str):
        self.stage = stage
        self.event("stage-started")


    def broadcast_attempt(self, kind: str, transaction: dict, transaction_hash: str):
        row = {"kind": kind, "transaction": transaction, "transactionHash": transaction_hash,
               "broadcastStatus": "attempted", "attemptedAt": utc_now(), "receipt": None}
        self.receipts.append(row)
        self.result["chain"]["transactions"].append(transaction_hash)
        self.save("receipts.json", self.receipts)
        self.event("broadcast-attempt", **row)
        return row

    def included(self, row: dict, receipt: dict):
        row["receipt"] = receipt
        row["includedAt"] = utc_now()
        self.save("receipts.json", self.receipts)
        self.event("transaction-included", kind=row["kind"], transactionHash=row["transactionHash"], receipt=receipt)

    def finish(self, error: BaseException | None = None):
        self.result.update(status="failed" if error else "passed", stage=self.stage, completedAt=utc_now())
        if error is not None:
            self.result["error"] = error_details(error)
        self.event("failed" if error else "completed", result=self.result)
        self.save("result.json", self.result)
        self.journal.close()


class JournalHTTPProvider(Web3.HTTPProvider):
    """Observe real RPC failures, including errors swallowed into SDK admission reasons."""

    def __init__(self, url: str, artifacts: Artifacts):
        artifacts.redact.remember_url(url)
        from urllib.parse import urlsplit
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # The official chain-4663 endpoint filters bare non-browser requests at the
        # transport (403/429 without an Origin). Mirror the launch app's public origin
        # so the example RPC client is admitted identically to the UI it validates.
        if urlsplit(url).hostname == "rpc.mainnet.chain.robinhood.com":
            headers["Origin"] = "https://abyss.trading"
        super().__init__(
            url, request_kwargs={"timeout": 120, "headers": headers}, exception_retry_configuration=None,
        )
        self.artifacts = artifacts
        self.node = "execution"


    def make_request(self, method, params):
        method = str(method)
        if method.startswith(("evm_", "anvil_", "hardhat_")) and method != "anvil_nodeInfo":
            raise ExampleFailure("the execution chain must be preserved for debugging", code="SOURCE_STATE_MUTATION_FORBIDDEN")
        if method.startswith(("eth_sign", "personal_")) or method == "eth_sendTransaction":
            raise ExampleFailure("execution requires local wallet signing", code="REMOTE_SIGNING_FORBIDDEN")
        started = time.monotonic()
        try:
            response = super().make_request(method, params)
        except Exception as error:
            self.artifacts.event("rpc-error", node=self.node, method=method, error=error_details(error), durationMs=(time.monotonic() - started) * 1000)
            raise
        duration = (time.monotonic() - started) * 1000
        if "error" in response:
            safe_params = "[RAW SIGNED TRANSACTION OMITTED]" if method == "eth_sendRawTransaction" else params
            self.artifacts.event("rpc-error", node=self.node, method=method, params=safe_params, error=response["error"], durationMs=duration)
        elif method == "eth_getTransactionReceipt" and response.get("result") is not None:
            self.artifacts.event("rpc-receipt", node=self.node, transactionHash=params[0], receipt=response["result"], durationMs=duration)
        return response
