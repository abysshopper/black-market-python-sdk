"""Canonical author hub authentication and truthful receipt/cursor outcomes."""

from types import SimpleNamespace

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak

from black_market_sdk import (
    DeveloperClaimReceipt, DeveloperClaimResult, DeveloperDirectClaim, DeveloperPageClaim,
    build_claim_developer_fees_page_transaction, build_set_author_payout_transaction,
    decode_developer_claim_receipt, read_author_hubs, read_developer_fees,
)

REGISTRY = "0x0000000000000000000000000000000000000010"
CORE = "0x0000000000000000000000000000000000000020"
FACTORY = "0x0000000000000000000000000000000000000030"
HUB = "0x0000000000000000000000000000000000000040"
AUTHOR = "0x0000000000000000000000000000000000000050"
ASSET = "0x0000000000000000000000000000000000000060"
PAYOUT = "0x0000000000000000000000000000000000000070"
BLOCK_HASH = "0x" + "ab" * 32


def transaction(page=False, *, offset=0, limit=1, assets=(ASSET,)):
    if page:
        signature = "claimDeveloperFeesPage(address,uint256,uint256,address[])"
        args = abi_encode(["address", "uint256", "uint256", "address[]"], [AUTHOR, offset, limit, assets])
    else:
        signature = "claimDeveloperFees(address,address)"
        args = abi_encode(["address", "address"], [AUTHOR, ASSET])
    claim = (DeveloperPageClaim(REGISTRY, FACTORY, AUTHOR, offset, limit, tuple(assets))
        if page else DeveloperDirectClaim(REGISTRY, FACTORY, AUTHOR, HUB, ASSET))
    return {"chainId": 31337, "from": PAYOUT, "to": FACTORY if page else HUB,
        "data": "0x" + (keccak(text=signature)[:4] + args).hex(), "value": 0, "claim": claim}


def log(signature, addresses, types, values, *, emitter=FACTORY):
    return {"address": emitter,
        "topics": ["0x" + keccak(text=signature).hex(), *["0x" + abi_encode(["address"], [address]).hex() for address in addresses]],
        "data": "0x" + abi_encode(types, values).hex()}


def result(status, amount=0, error=bytes(4), *, emitter=FACTORY):
    return log("DeveloperClaimResult(address,address,address,uint256,uint8,bytes4)",
        [AUTHOR, HUB, ASSET], ["uint256", "uint8", "bytes4"], [amount, status, error], emitter=emitter)


def cursor(*, offset=0, next_offset=1, total=1):
    return log("DeveloperClaimPage(address,uint256,uint256,uint256)", [AUTHOR],
        ["uint256"] * 3, [offset, next_offset, total])


@pytest.mark.parametrize("status,amount,error", [
    (0, 123, bytes(4)), (1, 0, bytes(4)),
    (2, 0, keccak(text="UnsupportedAsset()")[:4]), (3, 0, bytes(4)),
])
def test_page_completion_preserves_distinct_payment_and_retryable_failure_outcomes(status, amount, error):
    outcome = decode_developer_claim_receipt({"status": 1, "from": PAYOUT, "to": FACTORY,
        "logs": [result(status, amount, error), cursor()]}, transaction=transaction(True))
    assert isinstance(outcome, DeveloperClaimReceipt)
    assert outcome.cursor_complete is True
    assert outcome.payments_succeeded is (status in (0, 1))
    assert isinstance(outcome.results[0], DeveloperClaimResult)
    assert outcome.results[0].status == status
    assert outcome.results[0].amount == amount
    assert outcome.results[0].outcome == ("paid", "zero", "unsupported", "failed")[status]
    assert bool(outcome.retryable_results) is (status == 3)


@pytest.mark.parametrize("logs", [
    [result(0, 10)], [result(0, 10), cursor(next_offset=2)],
    [result(0, 10), cursor(offset=1)], [result(0, 10), cursor(), cursor()],
    [result(0, 10), result(0, 10), cursor()], [cursor()],
    [result(0, 10, emitter=HUB), cursor()], [result(0), cursor()],
    [result(3, 10), cursor()], [result(4), cursor()],
])
def test_invalid_cursor_emitter_duplicate_or_inconsistent_rows_never_become_success(logs):
    with pytest.raises(ValueError):
        decode_developer_claim_receipt({"status": 1, "from": PAYOUT, "to": FACTORY, "logs": logs}, transaction=transaction(True))


def test_empty_completed_page_is_distinct_from_failed_transaction():
    tx = transaction(True, offset=2)
    outcome = decode_developer_claim_receipt({"status": 1, "from": PAYOUT, "to": FACTORY, "logs": [cursor(offset=2, next_offset=2, total=2)]}, transaction=tx)
    assert outcome.cursor_complete is True and outcome.results == ()
    failed = decode_developer_claim_receipt({"status": 0, "from": PAYOUT, "to": FACTORY, "logs": []}, transaction=tx)
    assert failed.cursor_complete is False and failed.next_offset is None
    assert failed.execution_succeeded is False and failed.outcome == "reverted"


def test_direct_receipt_without_payment_event_does_not_invent_zero_or_payment():
    outcome = decode_developer_claim_receipt({"status": 1, "from": PAYOUT, "to": HUB, "logs": []}, transaction=transaction())
    assert outcome.outcome == "unobserved" and outcome.payments_succeeded is False
    assert outcome.results == ()
    paid = log("DeveloperFeesClaimed(address,address,address,uint256)", [AUTHOR, ASSET, PAYOUT], ["uint256"], [123], emitter=HUB)
    outcome = decode_developer_claim_receipt({"status": 1, "from": PAYOUT, "to": HUB, "logs": [paid]}, transaction=transaction())
    assert outcome.results[0].payout.lower() == PAYOUT.lower()
    assert outcome.results[0].amount == 123 and outcome.payments_succeeded is True


@pytest.mark.parametrize("limit", [0, 11, True, -1])
def test_claim_page_builder_rejects_out_of_range_limit_before_rpc(limit):
    with pytest.raises(ValueError):
        build_claim_developer_fees_page_transaction(SimpleNamespace(), registry=REGISTRY,
            author_id=AUTHOR, offset=0, limit=limit, account=PAYOUT, chain_id=31337)


@pytest.mark.parametrize("assets", [(ASSET, ASSET), (PAYOUT, ASSET), ("0x" + "00" * 20,), (ASSET,) * 9])
def test_claim_page_builder_rejects_unsorted_duplicate_zero_or_oversized_assets(assets):
    with pytest.raises(ValueError):
        build_claim_developer_fees_page_transaction(SimpleNamespace(), registry=REGISTRY,
            author_id=AUTHOR, offset=0, limit=1, assets=assets, account=PAYOUT, chain_id=31337)


class UntrustedHubRpc:
    """An attacker hub may self-report V3/registry; canonical factory denies it."""
    def make_request(self, method, params):
        if method == "eth_chainId":
            return {"result": "0x7a69"}
        if method == "eth_getBlockByNumber":
            return {"result": {"number": "0x7", "hash": BLOCK_HASH, "timestamp": "0x64", "gasLimit": "0x1c9c380", "baseFeePerGas": "0x1"}}
        if method != "eth_call":
            raise AssertionError(method)
        target = params[0]["to"].lower()
        selector = bytes.fromhex(params[0]["data"][2:10])
        rows = {
            (REGISTRY.lower(), "core()"): ("address", CORE),
            (CORE.lower(), "feeFactory()"): ("address", FACTORY),
            (CORE.lower(), "registry()"): ("address", REGISTRY),
            (FACTORY.lower(), "implementationRegistry()"): ("address", REGISTRY),
            (FACTORY.lower(), "deploymentAuthority()"): ("address", CORE),
            (FACTORY.lower(), "isHub(address)"): ("bool", False),
            (REGISTRY.lower(), "authorPayout(address)"): ("address", PAYOUT),
            (REGISTRY.lower(), "authorHubCount(address)"): ("uint256", 0),
        }
        if target == HUB.lower():
            raise AssertionError("untrusted hub self-report was consulted before canonical membership")
        for (address, signature), (kind, value) in rows.items():
            if target == address and selector == keccak(text=signature)[:4]:
                return {"result": "0x" + abi_encode([kind], [value]).hex()}
        raise AssertionError((target, selector))


def test_attacker_hub_self_reports_cannot_authorize_developer_fee_reads():
    with pytest.raises(ValueError):
        read_developer_fees(SimpleNamespace(provider=UntrustedHubRpc()), registry=REGISTRY, hub=HUB, author_id=AUTHOR)


@pytest.mark.parametrize("limit", [0, 101])
def test_author_discovery_bounds_are_not_factory_claim_bounds(limit):
    with pytest.raises(ValueError):
        read_author_hubs(SimpleNamespace(), registry=REGISTRY, author_id=AUTHOR, limit=limit)


@pytest.mark.parametrize("page", [False, True])
@pytest.mark.parametrize("field,value", [("from", AUTHOR), ("from", None), ("to", None)])
def test_claim_receipt_rejects_another_sender_or_missing_call_identity(page, field, value):
    tx = transaction(page)
    receipt = {"status": 1, "from": PAYOUT, "to": tx["to"],
        "logs": [result(0, 123), cursor()] if page else []}
    receipt[field] = value
    with pytest.raises(ValueError):
        decode_developer_claim_receipt(receipt, transaction=tx)


class AuthorControlRpc(UntrustedHubRpc):
    def make_request(self, method, params):
        if method == "eth_chainId":
            return {"result": "0x7a69"}
        if method == "eth_call" and params[0]["to"].lower() == REGISTRY.lower():
            selector = bytes.fromhex(params[0]["data"][2:10])
            for signature, kind, value in [
                ("authorPayout(address)", "address", PAYOUT),
                ("authorHubCount(address)", "uint256", 0),
                ("admin()", "address", CORE),
            ]:
                if selector == keccak(text=signature)[:4]:
                    return {"result": "0x" + abi_encode([kind], [value]).hex()}
        return super().make_request(method, params)


def test_retired_controller_cannot_build_a_live_payout_update():
    client = SimpleNamespace(provider=AuthorControlRpc())
    with pytest.raises(ValueError):
        build_set_author_payout_transaction(client, registry=REGISTRY,
            author_id=AUTHOR, payout=ASSET, account=AUTHOR, chain_id=31337)


@pytest.mark.parametrize("account", [PAYOUT, CORE])
def test_current_controller_and_admin_can_build_a_live_payout_update(account):
    tx = build_set_author_payout_transaction(SimpleNamespace(provider=AuthorControlRpc()),
        registry=REGISTRY, author_id=AUTHOR, payout=ASSET, account=account, chain_id=31337)
    assert tx["from"].lower() == account.lower()
    assert tx["to"].lower() == REGISTRY.lower() and tx["value"] == 0
    assert tx["data"][:10] == "0x" + keccak(text="setAuthorPayout(address,address)")[:4].hex()


@pytest.mark.parametrize("page", [False, True])
def test_receipt_requires_exact_typed_claim_context_and_calldata(page):
    tx = transaction(page)
    receipt = {"status": 1, "from": PAYOUT, "to": tx["to"],
        "logs": [result(0, 123), cursor()] if page else []}
    for changed in (
        {key: value for key, value in tx.items() if key != "claim"},
        {**tx, "claim": {"kind": "page" if page else "direct"}},
        {**tx, "data": "0x"},
        {**tx, "value": 1},
    ):
        with pytest.raises(ValueError):
            decode_developer_claim_receipt(receipt, transaction=changed)


@pytest.mark.parametrize("page", [False, True])
def test_removed_canonical_claim_logs_cannot_prove_payment(page):
    tx = transaction(page)
    paid = result(0, 123) if page else log(
        "DeveloperFeesClaimed(address,address,address,uint256)",
        [AUTHOR, ASSET, PAYOUT], ["uint256"], [123], emitter=HUB)
    with pytest.raises(ValueError):
        decode_developer_claim_receipt(
            {"status": 1, "from": PAYOUT, "to": tx["to"],
             "logs": [{**paid, "removed": True}, cursor()] if page else [{**paid, "removed": True}]},
            transaction=tx,
        )
