"""Exercise the opt-in launch API lifecycle with a deterministic fake transport.

Run with ``python examples/launch_api_lifecycle.py``.  It never signs a typed
data payload or contacts the API/upload host: the injected transport returns all
responses locally.  A real caller supplies its own signature, keeps the
capability private, and decides if/when to retry a pending publish.
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    LaunchApiClient,
    LaunchApiConfig,
    LaunchApiResponse,
    LaunchAttributionAuthorization,
    LaunchPublishPending,
    LaunchSessionCreateRequest,
    LaunchSessionImageDescriptor,
    LaunchSessionMetadata,
    LaunchSessionPublishRequest,
    build_launch_attribution_typed_data,
    sha256_hex,
)

CHAIN_ID = 4663
WALLET = "0x1000000000000000000000000000000000000001"
NONCE = "0x" + "01" * 32
DEADLINE = 1_800_000_000
IDEMPOTENCY_KEY = "offline-launch-example-001"
# This only satisfies local shape validation. A real caller signs ``typed_data``
# with its wallet and never sends a placeholder signature to the live API.
PLACEHOLDER_SIGNATURE = "0x" + "11" * 65
IMAGE_BYTES = b"offline-example-image"


def _json_response(status: int, payload: dict, headers: dict[str, str] | None = None):
    return LaunchApiResponse(
        status=status,
        headers={} if headers is None else headers,
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def fake_transport(method: str, url: str, headers, body: bytes | None, timeout: float):
    """A local stand-in for both the API and its presigned upload host."""

    upload_headers = {
        "Content-Type": "image/png",
        "Content-Length": str(len(IMAGE_BYTES)),
        "if-none-match": "*",
    }
    path = urlsplit(url).path
    if url.startswith("https://uploads.example.invalid/"):
        if method != "PUT" or body != IMAGE_BYTES or dict(headers) != upload_headers:
            raise AssertionError("the example must PUT the exact bytes and issued headers")
        return LaunchApiResponse(status=200, headers={}, body=b"")

    session = {
        "sessionId": "offline-session",
        "chainId": CHAIN_ID,
    }
    if method == "POST" and path == "/api/v1/launch-upload-sessions":
        return _json_response(
            201,
            {
                **session,
                "status": "awaiting_upload",
                "upload": {
                    "url": "https://uploads.example.invalid/offline-session/image",
                    "headers": upload_headers,
                    "expiresAt": "1800000000",
                },
                # Returned exactly once; the caller persists it before any
                # subsequent response deliberately omits it.
                "capability": "offline-private-capability",
            },
        )
    if method == "POST" and path.endswith("/upload/complete"):
        return _json_response(200, {**session, "status": "ready_to_launch"})
    if method == "POST" and path.endswith("/publish"):
        return _json_response(
            202,
            {**session, "status": "awaiting_indexer", "canonicalStatus": "pending"},
            {"Retry-After": "1"},
        )
    raise AssertionError(f"unexpected fake request: {method} {path}")


metadata = LaunchSessionMetadata(
    name="Offline Example",
    symbol="OFFLINE",
    description="This lifecycle is exercised entirely against an injected transport.",
)
image = LaunchSessionImageDescriptor(
    sha256=sha256_hex(IMAGE_BYTES),
    content_type="image/png",
    content_length=len(IMAGE_BYTES),
)
typed_data = build_launch_attribution_typed_data(
    chain_id=CHAIN_ID,
    wallet=WALLET,
    metadata=metadata,
    image=image,
    idempotency_key=IDEMPOTENCY_KEY,
    nonce=NONCE,
    deadline=DEADLINE,
)
print("prepared EIP-712 domain:", typed_data["domain"])
print("prepared metadata hash:", typed_data["message"]["metadataHash"])

client = LaunchApiClient(
    LaunchApiConfig(base_url="https://api.example.invalid"),
    transport=fake_transport,
)
session = client.create_launch_upload_session(
    LaunchSessionCreateRequest(
        chain_id=CHAIN_ID,
        wallet=WALLET,
        metadata=metadata,
        image=image,
        authorization=LaunchAttributionAuthorization(
            nonce=NONCE,
            deadline=DEADLINE,
            signature=PLACEHOLDER_SIGNATURE,
        ),
    ),
    IDEMPOTENCY_KEY,
)
capability = session["capability"]  # Keep this recovery secret out of logs and URLs.
client.put_launch_image(session["upload"], IMAGE_BYTES)
ready = client.complete_launch_upload(CHAIN_ID, session["sessionId"], capability)
assert "capability" not in ready and "upload" not in ready
print("upload lifecycle status:", ready["status"])

try:
    client.publish_launch_upload_session(
        CHAIN_ID,
        session["sessionId"],
        capability,
        LaunchSessionPublishRequest(
            chain_id=CHAIN_ID,
            transaction_hash="0x" + "ab" * 32,
        ),
    )
except LaunchPublishPending as pending:
    # The client intentionally does not retry write calls. Persist the session,
    # capability, and idempotency key; callers choose whether to retry after this delay.
    assert "capability" not in pending.session and "upload" not in pending.session
    print("publish pending:", pending.session["status"], f"retry after {pending.retry_after_ms} ms")
