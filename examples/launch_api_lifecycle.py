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
if __package__:
    from ._launch_support import save_private_state, validate_metadata_session
else:
    from _launch_support import save_private_state, validate_metadata_session


def recovery_metadata(recovery):
    document = recovery["request"]["metadata"]
    return LaunchSessionCreateMetadata(
        name=document["name"], symbol=document["symbol"], description=document["description"],
        website_url=document.get("websiteUrl"), twitter_url=document.get("twitterUrl"),
        telegram_url=document.get("telegramUrl"), discord_url=document.get("discordUrl"),
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
    client = LaunchApiClient(LaunchApiConfig(base_url=base_url, allow_loopback_http=True, retries=1))

    if not args.signed:
        if args.state_file is not None:
            recovery = json.loads(args.state_file.read_text())
            if recovery["chainId"] != chain_id or recovery["apiUrl"] != client.config.base_url:
                parser.error("recovery state belongs to a different chain or API")
            if not recovery.get("sessionId") or not recovery.get("capability"):
                parser.error("original session issuance is unknown; reconcile the saved request with the service, never replay or create a new session")
            session = client.get_launch_upload_session(chain_id, recovery["sessionId"], recovery["capability"])
            if "request" in recovery:
                validate_metadata_session(session, recovery_metadata(recovery), chain_id=chain_id,
                                          wallet=recovery["request"]["wallet"], session_id=recovery["sessionId"])
            else:
                # Legacy capability-only files retain GET recovery; no intent is invented.
                if session.get("sessionId") != recovery["sessionId"] or session.get("chainId") != chain_id:
                    raise RuntimeError("API changed the original recovery session")
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
    # Store the exact wire request and key before the first POST can issue a capability.
    recovery = {
        "schema": "black-market.launch-api-example.v2",
        "apiUrl": client.config.base_url, "chainId": chain_id, "orchestrator": orchestrator,
        "idempotencyKey": key, "issuance": "unknown",
        "request": {
            "chainId": chain_id, "wallet": account.address,
            "metadata": {"name": metadata.name, "symbol": metadata.symbol, "description": metadata.description},
            "authorization": {"nonce": nonce, "deadline": str(deadline), "signature": signature},
        },
    }
    save_private_state(args.state_file, recovery, create=True)
    session = client.create_launch_upload_session(request, key)
    # Retain the once-returned credentials even if response validation later refuses.
    recovery.update(sessionId=session.get("sessionId"), capability=session.get("capability"),
                    issuance="returned", session=session)
    save_private_state(args.state_file, recovery)
    validate_metadata_session(session, metadata, chain_id=chain_id, wallet=account.address, staging=True)
    if not isinstance(session.get("capability"), str) or not session["capability"]:
        raise RuntimeError("API did not return the original private capability")
    recovered = client.get_launch_upload_session(chain_id, session["sessionId"], session["capability"])
    validate_metadata_session(recovered, metadata, chain_id=chain_id, wallet=account.address,
                              session_id=session["sessionId"], staging=True)
    replay = client.create_launch_upload_session(request, key)
    validate_metadata_session(replay, metadata, chain_id=chain_id, wallet=account.address,
                              session_id=session["sessionId"], staging=True)
    if replay["sessionId"] != session["sessionId"] or "capability" in replay or "upload" in replay:
        raise RuntimeError("API idempotency or capability privacy contract was violated")
    print(json.dumps({"sessionId": recovered["sessionId"], "status": recovered["status"], "recoveryFile": str(args.state_file), "chainTransactionSent": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
