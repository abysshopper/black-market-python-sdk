"""Use the real launch metadata API, without signing or writing by default.

LAUNCH_API_TEST_URL must name your running test API. The default command checks
private-session read boundaries. --signed creates a metadata-only session on a
loopback test service using LAUNCH_API_TEST_PRIVATE_KEY; it never sends a chain
transaction, uploads an image, or publishes a launch.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.messages import encode_typed_data

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    ROBINHOOD_MAINNET_CHAIN_ID,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchApiError,
    LaunchAttributionAuthorization,
    LaunchSessionCreateRequest,
    LaunchSessionCreateMetadata,
    build_launch_attribution_typed_data,
    get_launch_addresses,
)


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def expect_api_error(call, *, status: int, code: str) -> None:
    try:
        call()
    except LaunchApiError as error:
        if (error.status, error.code) != (status, code):
            raise
        print(f"HTTP authorization boundary: {status} {code}")
    else:
        raise RuntimeError(f"API did not enforce expected {status} {code} boundary")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signed", action="store_true", help="Explicitly sign and write metadata to a loopback test API")
    parser.add_argument("--state-file", type=Path, help="New private recovery file, required with --signed")
    args = parser.parse_args(argv)
    base_url = os.environ.get("LAUNCH_API_TEST_URL")
    if not base_url:
        parser.error("LAUNCH_API_TEST_URL is required; start the real test API first")
    chain_id = int(os.environ.get("LAUNCH_API_TEST_CHAIN_ID", str(ROBINHOOD_MAINNET_CHAIN_ID)))
    client = LaunchApiClient(LaunchApiConfig(base_url=base_url, allow_loopback_http=True))

    if not args.signed:
        if args.state_file is not None:
            recovery = json.loads(args.state_file.read_text())
            if recovery["chainId"] != chain_id or recovery["apiUrl"] != client.config.base_url:
                parser.error("recovery state belongs to a different chain or API")
            session = client.get_launch_upload_session(chain_id, recovery["sessionId"], recovery["capability"])
            print(json.dumps({"sessionId": session["sessionId"], "status": session["status"], "metadata": session["metadata"]}, indent=2))
        else:
            # A random nonexistent ID needs no seeded launch, image store or indexer.
            session_id = "lus_" + secrets.token_hex(16)
            expect_api_error(lambda: client.get_launch_upload_session(chain_id, session_id, "invalid"), status=401, code="INVALID_CAPABILITY")
            expect_api_error(lambda: client.get_launch_upload_session(chain_id, session_id, secrets.token_urlsafe(32)), status=404, code="NOT_FOUND")
        return 0

    if not is_loopback(client.config.base_url):
        parser.error("--signed is restricted to a loopback test API, never production")
    private_key = os.environ.get("LAUNCH_API_TEST_PRIVATE_KEY")
    if not private_key:
        parser.error("--signed requires LAUNCH_API_TEST_PRIVATE_KEY for a disposable test wallet")
    if args.state_file is None:
        parser.error("--signed requires --state-file to protect the once-returned capability")
    if args.state_file.exists():
        parser.error("state file already exists; use the default read-only command to recover it")
    orchestrator = os.environ.get("LAUNCH_API_TEST_ORCHESTRATOR")
    if orchestrator is None:
        if chain_id != ROBINHOOD_MAINNET_CHAIN_ID:
            parser.error("set LAUNCH_API_TEST_ORCHESTRATOR explicitly for a non-mainnet test chain")
        orchestrator = get_launch_addresses(chain_id).orchestrator
    account = Account.from_key(private_key)
    metadata = LaunchSessionCreateMetadata(name="Python API Example", symbol="PYAPI", description="Metadata staged through the real SDK HTTP client.")
    nonce = "0x" + secrets.token_hex(32)
    deadline = int(time.time()) + 600
    key = "python-example-" + secrets.token_hex(16)
    typed_data = build_launch_attribution_typed_data(
        chain_id=chain_id, verifying_contract=orchestrator, wallet=account.address,
        metadata=metadata, idempotency_key=key, nonce=nonce, deadline=deadline,
    )
    signature = "0x" + bytes(account.sign_message(encode_typed_data(full_message=typed_data)).signature).hex()
    request = LaunchSessionCreateRequest(
        chain_id=chain_id, wallet=account.address, metadata=metadata,
        authorization=LaunchAttributionAuthorization(nonce=nonce, deadline=deadline, signature=signature),
    )
    # Reserve the private destination before writing to the API; never log the secret.
    descriptor = os.open(args.state_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as state:
        session = client.create_launch_upload_session(request, key)
        recovery = {
            "apiUrl": client.config.base_url, "chainId": chain_id, "orchestrator": orchestrator,
            "sessionId": session["sessionId"], "capability": session["capability"], "idempotencyKey": key,
        }
        json.dump(recovery, state, indent=2)
        state.write("\n")
    recovered = client.get_launch_upload_session(chain_id, session["sessionId"], session["capability"])
    replay = client.create_launch_upload_session(request, key)
    if replay["sessionId"] != session["sessionId"] or "capability" in replay or "upload" in replay:
        raise RuntimeError("API idempotency or capability privacy contract was violated")
    print(json.dumps({"sessionId": recovered["sessionId"], "status": recovered["status"], "recoveryFile": str(args.state_file), "chainTransactionSent": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
