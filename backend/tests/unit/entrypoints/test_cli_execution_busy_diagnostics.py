"""Contention retains the execution owner's safe code across CLI presentation."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import DurableStateBusy, ExecutionBusy, StateConflict
from oh_goon.core.models.run import FleetRunDetail, FleetRunState
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic


@pytest.mark.parametrize(
    "rejection,code,stage",
    (
        (ExecutionBusy, "EXECUTION_BUSY", "execution_admission"),
        (DurableStateBusy, "PLAN_RESOLUTION_REJECTED", "plan_resolution"),
    ),
)
def test_execution_contention_keeps_owner_code_without_private_details(
    rejection: type[ExecutionBusy] | type[DurableStateBusy], code: str, stage: str
) -> None:
    diagnostic = safe_plan_diagnostic(
        rejection("PRIVATE_RUN_TOKEN /private/state/db.sqlite already active")
    )
    assert diagnostic["code"] == code
    assert diagnostic["stage"] == stage
    assert diagnostic["inputs"] == []
    assert diagnostic["execution"] == "not_started_by_diagnostic"
    assert "Wait for active execution to settle" in diagnostic["safe_next_action"]
    encoded = json.dumps(diagnostic)
    assert "PRIVATE_RUN_TOKEN" not in encoded and "/private/state" not in encoded


def test_text_execution_contention_does_not_describe_an_invalid_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_safe_plan_diagnostic(ExecutionBusy("PRIVATE_RUN_TOKEN /private/state"), as_json=False)
    text = capsys.readouterr().out
    assert text.startswith("Execution busy: EXECUTION_BUSY\n")
    assert "Wait for active execution to settle" in text
    assert "Plan rejected" not in text and "PRIVATE_RUN_TOKEN" not in text


class _SpecificObservationConflict(StateConflict):
    code = "CONTROL_OBSERVATION_UNAVAILABLE"


@pytest.mark.parametrize("rejection", (StateConflict, _SpecificObservationConflict))
def test_post_admission_conflict_keeps_original_run_and_safe_guidance(
    capsys: pytest.CaptureFixture[str], rejection: type[StateConflict]
) -> None:
    error = rejection("PRIVATE_WORKER_DETAIL /private/state RUN_TOKEN")
    admitted = cast(FleetRunState, SimpleNamespace(fleet_run_id="original-run", target=None))

    class Plans:
        def require_cli_plan_preparation(self, run_id: str | None) -> None:
            assert run_id is None

        def run(self, *_args: object, **_options: object) -> FleetRunState:
            return admitted

    class Runs:
        def wait_for_terminal(self, run_id: str, *, timeout: float | None = None) -> FleetRunDetail:
            assert run_id == admitted.fleet_run_id
            raise error

    runtime = cast(ApplicationRuntime, SimpleNamespace(plans=Plans(), runs=Runs()))
    args = Namespace(
        command="plan",
        plan_command="run",
        plan="plan.yml",
        as_json=True,
        repair_handoff=False,
        fleet_run_id=None,
        extensions=None,
        repository_ids=None,
        all_repositories=False,
        priority=None,
        lock_file=None,
        agent_repository_root=None,
        agent_skill_root=None,
        allow_agent_acceptance=(),
        native_workspace=None,
        wait=True,
    )
    assert run_plan_command(runtime, args) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "error"
    assert result["fleet_run_id"] == admitted.fleet_run_id
    assert result["code"] == error.code and result["stage"] == "run_observation"
    action = result["safe_next_action"]
    assert "runs handoff" in action and "do not rerun unresolved effects" in action
    assert "terminal outcome" in action and "physical cleanup" in action
    assert "retry validation" not in action
    assert "PRIVATE_WORKER_DETAIL" not in captured.out + captured.err
    assert "RUN_TOKEN" not in captured.out + captured.err
