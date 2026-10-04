"""Validate and hash a caller-supplied current lifecycle plan entirely offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    encode_launch_plan, hash_launch_plan, launch_id_of,
    launch_plan_from_dict, launch_plan_to_dict,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Complete portable LaunchPlanV1 JSON, or an object with a plan field")
    args = parser.parse_args(argv)
    payload = json.loads(args.plan.read_text())
    plan = launch_plan_from_dict(payload.get("plan", payload))
    print(json.dumps({"plan": launch_plan_to_dict(plan), "planHash": hash_launch_plan(plan),
        "launchId": launch_id_of(plan), "encodedPlan": "0x" + encode_launch_plan(plan).hex(),
        "admitted": False, "note": "Offline commitment only: no eligibility, funds or stateful execution proof"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
