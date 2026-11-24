"""Inspect or explicitly retire a candidate through its original Run/workspace owners."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError


def run_candidate_workspace_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.runs_command == "candidate":
            result = runtime.plans.inspect_candidate_run(args.fleet_run_id)
        else:
            result = runtime.plans.retire_candidate_run(
                args.fleet_run_id,
                expected_state_version=args.expected_state_version,
                authorize=args.authorize,
                reconcile=args.reconcile,
            )
        if args.as_json:
            print(result.model_dump_json())
        else:
            print(f"Fleet Run ID: {result.fleet_run_id}")
            print(f"Candidate workspace: {result.admission.workspace.workspace_id}")
            print(
                f"State: {result.state.lifecycle_state.value}; version {result.state.state_version}"
            )
            print(f"Owner-resolved path: {result.path or 'unavailable'}")
            print(f"Source snapshot state: {result.source_state.lifecycle_state.value}")
            if result.checkpoint is not None:
                print(f"Recorded checkpoint: {result.checkpoint.checkpoint_id}")
                print(f"Recorded source: sha256:{result.checkpoint.source_sha256}")
            print("Checkpoint metadata is recorded; current bytes have not been re-proved.")
            if result.receipt is not None:
                print(f"Original execution settlement: {result.receipt.settlement}")
        return 0
    except (FleetDomainError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, FleetDomainError) else "CANDIDATE_OPERATION_UNCONFIRMED"
        action = shlex.join(("oh-goon", "runs", "candidate", args.fleet_run_id, "--json"))
        message = (
            "Candidate operation was not confirmed. Inspect current workspace state before "
            "explicit reconciliation; preserve dirty or uncertain output."
        )
        if args.as_json:
            print(json.dumps({"code": code, "message": message, "readback": action}))
        else:
            print(f"{code}: {message}")
            print(action)
        return 2


def run_parallel_candidate_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.runs_command == "verify-candidate":
            verification = runtime.plans.verify_parallel_candidate(args.fleet_run_id)
            if args.as_json:
                print(verification.model_dump_json())
            else:
                print(f"Verification for {verification.fleet_run_id}: {verification.outcome}")
                print(verification.reason)
                if verification.checkpoint_id is not None:
                    print(f"Verified checkpoint: {verification.checkpoint_id}")
            return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[verification.outcome]
        export = runtime.plans.export_parallel_candidate(
            args.fleet_run_id,
            checkpoint_id=args.checkpoint_id,
            destination=Path(args.output),
        )
        if args.as_json:
            print(export.model_dump_json())
        else:
            print(f"Verified candidate patch: {export.destination}")
            print(f"SHA-256: {export.sha256} ({export.byte_count} bytes)")
            if export.cleanup != "VERIFIED":
                print("Export is published; cleanup is unverified.")
        return 0
    except (FleetDomainError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, FleetDomainError) else "CANDIDATE_OPERATION_UNCONFIRMED"
        message = str(exc)
        if args.as_json:
            print(json.dumps({"code": code, "message": message}))
        else:
            print(f"{code}: {message}")
        return 2
