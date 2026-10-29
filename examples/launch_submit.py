"""Plan, simulate, recover and emit a current unsigned lifecycle transaction.

This command never loads private keys, signs, source-broadcasts or publishes.
Stateful simulation may execute only on an explicitly owned isolated local fork;
normal RPCs use non-persisting eth_simulateV1. An unadmitted plan emits no next
transaction. Cancellation uses canonical pending state, not disabled admission.

Recovery accepts --receipts with typed snake_case reference JSON, or a plan
wrapper containing "receipts". Preserve original observed block number/hash and
replacement_hash; --transaction-hash alone cannot detect an orphaned original
inclusion once that same transaction is mined in another block. Numeric reference
fields are emitted as decimal strings for reuse. Reconcile read-only output
explicitly before authorizing any remaining original work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from black_market_sdk import (
    LaunchExecutionLimits, LifecycleReceiptReference, build_next_transaction,
    hash_launch_plan, launch_id_of, launch_plan_from_dict, launch_plan_to_dict,
    plan_launch, prepare_and_plan_lifecycle_launch, read_launch_progress,
)


def json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    raise TypeError(f"Unsupported planner output: {type(value).__name__}")


def parse_receipt_references(value):
    """Rehydrate exact SDK references without discarding original block evidence."""
    if isinstance(value, dict):
        value = value["receipts"]
    if not isinstance(value, list):
        raise ValueError("receipts must be a JSON array of typed receipt references")
    fields = {"transaction_hash", "replacement_hash", "confirmations",
              "observed_block_number", "observed_block_hash", "nonce"}

    def integer(raw, name):
        if isinstance(raw, str):
            if not (raw.isascii() and raw.isdecimal() and len(raw) <= 78
                    and (raw == "0" or not raw.startswith("0"))):
                raise ValueError(f"{name} must be an exact canonical decimal integer")
            return int(raw)
        if type(raw) is not int:
            raise ValueError(f"{name} must be an exact integer or decimal string")
        return raw

    references = []
    for row in value:
        if not isinstance(row, dict) or not row.keys() <= fields or "transaction_hash" not in row:
            raise ValueError("each receipt must contain SDK snake_case receipt-reference fields")
        references.append(LifecycleReceiptReference(
            transaction_hash=row["transaction_hash"], replacement_hash=row.get("replacement_hash"),
            confirmations=integer(row.get("confirmations", 1), "confirmations"),
            observed_block_number=(integer(row["observed_block_number"], "observed_block_number")
                                   if row.get("observed_block_number") is not None else None),
            observed_block_hash=row.get("observed_block_hash"),
            nonce=integer(row["nonce"], "nonce") if row.get("nonce") is not None else None,
        ))
    return tuple(references)


def receipt_reference_json(references, statuses=()):
    """Retain original evidence; enrich hash-only references with observed inclusion."""
    by_hash = {status.transaction_hash.lower(): status for status in statuses}
    result = []
    for reference in references:
        row = asdict(reference)
        status = by_hash.get(reference.transaction_hash.lower())
        if status is not None and status.status == "confirmed":
            if row["observed_block_number"] is None:
                row["observed_block_number"] = status.block_number
            if row["observed_block_hash"] is None:
                row["observed_block_hash"] = status.block_hash
        nonce = row.pop("nonce")
        if nonce is None and status is not None:
            nonce = status.nonce
        if nonce is not None:
            row["nonce"] = str(nonce)
        row["confirmations"] = str(row["confirmations"])
        if row["observed_block_number"] is not None:
            row["observed_block_number"] = str(row["observed_block_number"])
        result.append(row)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--mode", choices=("atomic", "staged"), required=True)
    parser.add_argument("--action", choices=("continue", "cancel"), default="continue")
    parser.add_argument("--chain-gas-cap", type=int, help="Optional tightening transaction policy")
    parser.add_argument("--rpc-gas-cap", type=int, help="Optional known RPC transaction envelope cap")
    parser.add_argument("--account-gas-cap", type=int, help="Optional known direct-EOA transaction envelope cap")
    parser.add_argument("--calldata-cap", type=int, help="Optional known calldata byte cap")
    parser.add_argument("--headroom-bps", type=int, default=1500)
    parser.add_argument("--confirmations", type=int, default=1)
    receipts_group = parser.add_mutually_exclusive_group()
    receipts_group.add_argument("--receipts", type=Path, help="Typed receipt-reference JSON; retains original blocks and replacements")
    receipts_group.add_argument("--transaction-hash", action="append", default=[],
                                help="Hash-only convenience alias; cannot retain original inclusion/replacement evidence")
    parser.add_argument("--finalize-bound", action="store_true", help="Mine exact draft bound5/bound6 salts and assess the final plan")
    args = parser.parse_args(argv)
    if args.finalize_bound and args.action == "cancel":
        parser.error("cancellation recovers committed economics; it cannot finalize deployment salts")
    payload = json.loads(args.plan.read_text())
    plan = launch_plan_from_dict(payload.get("plan", payload))
    if args.receipts:
        references = parse_receipt_references(json.loads(args.receipts.read_text()))
    elif args.transaction_hash:
        references = tuple(LifecycleReceiptReference(value, confirmations=args.confirmations)
                           for value in args.transaction_hash)
    else:
        references = parse_receipt_references(payload.get("receipts", []))
    if args.finalize_bound and references:
        parser.error("do not mutate deployment salts after submitting transaction evidence")
    client = Web3(Web3.HTTPProvider(args.rpc_url))

    def limits(_, context):
        return LaunchExecutionLimits(
            headroom_bps=args.headroom_bps, chain_transaction_gas_limit=args.chain_gas_cap,
            rpc_transaction_gas_limit=args.rpc_gas_cap, account_transaction_gas_limit=args.account_gas_cap,
            max_calldata_bytes=args.calldata_cap, observed_block_number=context.block.number,
            observed_block_hash=context.block.block_hash, chain_id=context.chain_id,
            account=context.account, orchestrator=context.orchestrator, source="explicit CLI caller policy",
        )

    options = {"account": args.account, "mode": args.mode, "limits": limits,
        "confirmations": args.confirmations,
        "receipts": references}
    planned = None
    if args.finalize_bound:
        planned = asyncio.run(prepare_and_plan_lifecycle_launch(client, plan, **options))
        plan = planned.plan
    output = {"plan": launch_plan_to_dict(plan), "planHash": hash_launch_plan(plan),
        "launchId": launch_id_of(plan), "mode": args.mode, "action": args.action,
        "note": "Unsigned output only; each wallet submission must revalidate live canonical state"}
    if args.action == "cancel":
        next_transaction = build_next_transaction(client, plan, action="cancel", **options)
        output["progress"] = read_launch_progress(client, plan, confirmations=args.confirmations,
                                                receipts=options["receipts"], mode=args.mode)
        output["nextTransaction"] = next_transaction.as_transaction() if next_transaction else None
        output["nextAssessment"] = next_transaction.assessment if next_transaction else None
    else:
        if planned is None:
            planned = plan_launch(client, plan, **options)
        next_transaction = build_next_transaction(client, planned, account=args.account,
            receipts=options["receipts"]) if planned.assessment.ready else None
        output.update({"ready": planned.assessment.ready, "mode": planned.mode,
            "predictedToken": planned.predicted_token, "progress": planned.progress,
            "approvals": planned.approvals, "profiles": planned.profiles,
            "hookDeployments": planned.hook_deployments, "assessment": planned.assessment,
            "nextTransaction": next_transaction.as_transaction() if next_transaction else None,
            "nextAssessment": next_transaction.assessment if next_transaction else None})
    output["receipts"] = receipt_reference_json(references, output["progress"].receipts)
    print(json.dumps(output, default=json_value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
