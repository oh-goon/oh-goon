"""CLI transport for the same stopped-campaign inspection and fresh Run operation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_run_readback import print_run_completion
from oh_goon.cli_result import CliTermination
from oh_goon.core.errors import FleetDomainError, InvalidRequest
from oh_goon.core.models.agent_continuation import (
    MAX_CONTINUATION_FEEDBACK_BYTES,
    AgentContinuationInspection,
    AgentContinuationRequest,
)
from oh_goon.core.vocabulary import FleetRunOutcome


def _read_text(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise InvalidRequest("Continuation input exceeds its byte limit")
    return payload.decode("utf-8")


def run_agent_continuation_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.runs_command == "investigate":
            inspection = runtime.plans.inspect_agent_continuation(
                args.fleet_run_id,
                implementation=args.agent,
                profile=args.profile,
                config_file=str(args.config) if args.config is not None else None,
            )
            if args.as_json:
                print(inspection.model_dump_json(by_alias=True))
            else:
                print(f"Campaign: {inspection.campaign_id}; state: {inspection.state}")
                print(f"Original Run: {inspection.previous_run_id}")
                print(
                    f"Current candidate: {inspection.source.source_authority.source_snapshot.sha256}"
                )
                print(
                    f"Native selection: {inspection.selection.implementation}/{inspection.selection.profile}"
                )
                print(inspection.verification_reason)
                if inspection.source.diff.reason:
                    print(inspection.source.diff.reason)
                if inspection.source.diff.text:
                    print(inspection.source.diff.text)
                for reason in inspection.refusal_reasons:
                    print(reason)
                print("Save --json for review before authorizing a fresh Run.")
            return 0
        inspection = AgentContinuationInspection.model_validate_json(
            _read_text(args.inspection, 1024 * 1024)
        )
        request = AgentContinuationRequest(
            new_fleet_run_id=args.new_run_id,
            review=inspection.review,
            feedback=_read_text(args.feedback_file, MAX_CONTINUATION_FEEDBACK_BYTES),
            authorize=args.authorize,
        )
        # Print the stable lookup identity before the first potentially applied effect.
        print(f"Proposed Fleet Run ID: {request.new_fleet_run_id}", file=sys.stderr)
        print(
            "If acknowledgment is lost, inspect this exact ID with runs handoff; "
            "do not submit another Run automatically.",
            file=sys.stderr,
        )
        admitted = runtime.plans.continue_agent_campaign(args.fleet_run_id, request)
        try:
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
        except KeyboardInterrupt as interruption:
            runtime.runs.cancel(admitted.fleet_run_id)
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
            print(
                "Cancellation requested; inspect the original Run for cleanup evidence.",
                file=sys.stderr,
            )
            return 143 if isinstance(interruption, CliTermination) else 130
        if args.as_json:
            print(
                json.dumps(
                    {
                        "continuation": admitted.model_dump(mode="json"),
                        "feedback": detail.feedback.model_dump(mode="json", by_alias=True),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Fleet Run ID: {admitted.fleet_run_id}; outcome: {detail.outcome.value}")
            print_run_completion(detail)
        return 0 if detail.outcome is FleetRunOutcome.SUCCESS else 1
    except (FleetDomainError, OSError, ValueError) as exc:
        # Neither an exception nor a missing acknowledgment grants replay authority.
        code = exc.code if isinstance(exc, FleetDomainError) else "CONTINUATION_UNAVAILABLE"
        if args.as_json:
            print(
                json.dumps(
                    {
                        "code": code,
                        "message": "Continuation was not confirmed. Inspect the proposed Run ID before any new submission.",
                    }
                )
            )
        else:
            print(
                f"Continuation not confirmed: {code}. Inspect the proposed Run ID before any new submission."
            )
        return 2
