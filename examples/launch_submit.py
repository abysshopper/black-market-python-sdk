"""Plan, simulate, recover and emit a current unsigned lifecycle transaction.

This command never loads private keys, signs, source-broadcasts or publishes.
Stateful simulation may execute only on an explicitly owned isolated local fork;
normal RPCs use non-persisting eth_simulateV1. An unadmitted plan emits no next
transaction. Cancellation uses canonical pending state, not disabled admission.
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
    LaunchExecutionLimits, build_next_transaction, create_controlled_launch_fork,
    hash_launch_plan, launch_id_of, launch_plan_from_dict, launch_plan_to_dict,
    plan_launch, prepare_pool_bound_lifecycle_plan, read_launch_progress,
)


def json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    raise TypeError(f"Unsupported planner output: {type(value).__name__}")


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
    parser.add_argument("--prepare-batch-size", type=int)
    parser.add_argument("--confirmations", type=int, default=1)
    parser.add_argument("--transaction-hash", action="append", default=[])
    parser.add_argument("--fork-rpc-url", help="Separate owned local Anvil fork; never the source execution node")
    parser.add_argument("--submission-rpc-url", help="Optional read-only immediate-next submission RPC preflight")
    parser.add_argument("--isolated-fork", action="store_true", help="Assert exclusive ownership of the local fork")
    parser.add_argument("--finalize-bound", action="store_true", help="Explicitly mine/finalize draft bound5 salts before reviewing its final planHash")
    args = parser.parse_args(argv)
    if bool(args.fork_rpc_url) != args.isolated_fork:
        parser.error("--fork-rpc-url and --isolated-fork must be provided together")
    if args.finalize_bound and args.transaction_hash:
        parser.error("do not mutate deployment salts after submitting transaction evidence")
    payload = json.loads(args.plan.read_text())
    plan = launch_plan_from_dict(payload.get("plan", payload))
    client = Web3(Web3.HTTPProvider(args.rpc_url))
    submission_client = Web3(Web3.HTTPProvider(args.submission_rpc_url)) if args.submission_rpc_url else None
    fork = create_controlled_launch_fork(client, Web3(Web3.HTTPProvider(args.fork_rpc_url)), isolated=True) if args.fork_rpc_url else None
    if args.finalize_bound:
        plan = asyncio.run(prepare_pool_bound_lifecycle_plan(client, plan)).plan

    def limits(_, context):
        return LaunchExecutionLimits(
            headroom_bps=args.headroom_bps, chain_transaction_gas_limit=args.chain_gas_cap,
            rpc_transaction_gas_limit=args.rpc_gas_cap, account_transaction_gas_limit=args.account_gas_cap,
            max_calldata_bytes=args.calldata_cap, observed_block_number=context.block.number,
            observed_block_hash=context.block.block_hash, chain_id=context.chain_id,
            account=context.account, orchestrator=context.orchestrator, source="explicit CLI caller policy",
        )

    options = {"account": args.account, "mode": args.mode, "limits": limits,
        "prepare_batch_size": args.prepare_batch_size, "confirmations": args.confirmations,
        "transaction_hashes": tuple(args.transaction_hash), "fork": fork}
    output = {"plan": launch_plan_to_dict(plan), "planHash": hash_launch_plan(plan),
        "launchId": launch_id_of(plan), "mode": args.mode, "action": args.action,
        "note": "Unsigned output only; each wallet submission must revalidate live canonical state"}
    if args.action == "cancel":
        next_transaction = build_next_transaction(client, plan, action="cancel", submission_client=submission_client, **options)
        output["progress"] = read_launch_progress(client, plan, confirmations=args.confirmations, transaction_hashes=args.transaction_hash)
        output["nextTransaction"] = next_transaction.as_transaction() if next_transaction else None
        output["nextAdmission"] = next_transaction.admission if next_transaction else None
    else:
        planned = plan_launch(client, plan, **options)
        next_transaction = build_next_transaction(client, planned, account=args.account, fork=fork, submission_client=submission_client) if planned.admitted else None
        output.update({"admitted": planned.admitted, "confidence": planned.confidence,
            "predictedToken": planned.predicted_token, "progress": planned.progress,
            "approvals": planned.approvals, "marketAdmissions": planned.market_admissions,
            "simulation": planned.simulation, "atomicSimulation": planned.atomic_simulation,
            "nextTransaction": next_transaction.as_transaction() if next_transaction else None,
            "nextAdmission": next_transaction.admission if next_transaction else None})
    print(json.dumps(output, default=json_value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
