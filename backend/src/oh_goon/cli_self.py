from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon import __version__
from oh_goon.repository_tooling.self_certification_plan import (
    ShardPlan,
    self_certification_plan,
    shard_plan,
    stage_parent_source_input,
)
from oh_goon.workflow import run_app, run_self_verify, self_plan_descriptor


def run_self_command(args: argparse.Namespace) -> int | None:
    if args.command == "self":
        if args.self_command == "version":
            print(__version__)
            return 0
        if args.self_command == "setup":
            if args.parent_zip is not None:
                staged = stage_parent_source_input(Path.cwd(), args.parent_zip)
                print(
                    "Staged verified parent source ZIP: "
                    f"{staged['artifact']} sha256={staged['sha256']}"
                )
                return 0
            setup_plan = self_certification_plan(Path.cwd())
            required = setup_plan["required_inputs"]
            if not required:
                print("Self-certification setup has no external inputs.")
                return 0
            for item in required:
                print(
                    f"{item['input']}: {item['status']} | "
                    f"{item['artifact']} sha256={item['sha256']}"
                )
            return 0
        if args.self_command in {"verify", "certify"} and args.plan:
            descriptor = self_plan_descriptor(args.self_command)
            if args.as_json:
                print(json.dumps(descriptor, sort_keys=True))
            else:
                print(f"SELF {args.self_command.upper()}")
                print(f"Plan          {descriptor['plan']}")
                print(f"Authority     {descriptor['execution_authority']}")
                print("Pipeline      " + " -> ".join(descriptor["pipeline"]))
            return 0
        if args.self_command == "verify":
            return run_self_verify()
        if args.self_command == "certify":
            return run_app()
        return 2
    if args.command == "dev" and args.dev_command == "shards":
        payload = shard_plan(Path.cwd())
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            _print_shards(payload)
        return 0
    return None


def _print_shards(payload: ShardPlan) -> None:
    print("OH-GOON VALIDATION SHARDS")
    print(f"Surface: {payload['surface']}")
    print(f"Strategy: {payload['strategy']}")
    print(
        f"Tests: {payload['test_count']} assigned={payload['assigned']} duplicates={payload['duplicates']} missing={payload['missing']}"
    )
    for shard in payload["shards"]:
        print(
            f"- {shard['id']}: {shard['test_count']} files | budget={shard['execution_budget_seconds']}s | {shard['execution_unit_id']}"
        )
        print("  Command: " + " ".join(shard["command"]))
