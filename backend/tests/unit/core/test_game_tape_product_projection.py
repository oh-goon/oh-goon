from __future__ import annotations

import hashlib
from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    MissingPlanArtifact,
    PlanOutputEvidence,
    PlanStepSettlementEvidence,
    PresentPlanArtifact,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def _output(value: str) -> PlanOutputEvidence:
    data = value.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(data).hexdigest(),
        byte_count=len(data),
        line_count=len(value.splitlines()),
    )


def test_game_tape_owns_step_artifacts_and_published_outputs(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = ResolvedFleetPlan.create(
        name="output-projection",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="build", argv=("echo", "build"), timeout_seconds=30),),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="1" * 64),
        identity=structural_execution_identity(plan),
    )
    now = utc_now()
    execution = CompletedPlanStepExecution(
        step_id="build",
        kind="command",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        started_at=now,
        finished_at=now,
        argv=("echo", "build"),
        stdout_evidence=_output("ok\n"),
        stderr_evidence=_output(""),
        repository_state_before="before",
        repository_state_after="after",
        artifacts=(
            PresentPlanArtifact(
                status="PRESENT", path="dist/app.whl", sha256="2" * 64, byte_count=42
            ),
            MissingPlanArtifact(status="MISSING", path="dist/sbom.json"),
        ),
        outputs=(PublishedPlanOutputEvidence(name="wheel", sha256="3" * 64, byte_count=42),),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id="run-product-projection",
        target=target,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=(execution,))
        ),
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run-product-projection",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    tape = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    ).game_tape
    step = tape.repositories[0].steps[0]
    assert step.artifacts == execution.artifacts
    assert step.outputs == execution.outputs
    assert step.output_names == ("wheel",)
    assert tape.enforcer_receipt_available is False


def test_run4_output_readback_uses_original_real_game_tape_evidence(tmp_path: Path, capsys) -> None:
    """The CLI transports real step outputs/artifacts already projected by Game Tape."""
    import json
    from types import SimpleNamespace

    from oh_goon.cli_parser import build_parser
    from oh_goon.cli_runtime_plans import run_plan_command

    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = ResolvedFleetPlan.create(
        name="run4-existing-output",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="publish", argv=("echo", "out"), timeout_seconds=30),),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="1" * 64),
        identity=structural_execution_identity(plan),
    )
    now = utc_now()
    execution = CompletedPlanStepExecution(
        step_id="publish",
        kind="command",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        started_at=now,
        finished_at=now,
        argv=("echo", "out"),
        stdout_evidence=_output("secret raw bytes must not appear\n"),
        stderr_evidence=_output(""),
        repository_state_before="before",
        repository_state_after="after",
        artifacts=(
            PresentPlanArtifact(
                status="PRESENT", path="dist/output.whl", sha256="2" * 64, byte_count=12
            ),
        ),
        outputs=(PublishedPlanOutputEvidence(name="wheel", sha256="3" * 64, byte_count=12),),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id="run4-real",
        target=target,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=(execution,))
        ),
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run4-real",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    detail = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )
    calls = []

    def get(run_id):
        calls.append(run_id)
        return detail

    args = build_parser().parse_args(["plan", "outputs", "run4-real", "--json"])
    assert run_plan_command(SimpleNamespace(runs=SimpleNamespace(get=get)), args) == 0
    printed = capsys.readouterr().out
    view = json.loads(printed)
    projected = detail.game_tape.repositories[0].steps[0]
    observed = view["repositories"][0]["steps"][0]
    assert view["repositories"][0]["repository_execution_id"] == (
        detail.game_tape.repositories[0].repository_execution_id
    )
    assert observed["step_execution_id"] == projected.step_execution_id
    assert observed["outputs"] == [item.model_dump(mode="json") for item in projected.outputs]
    assert observed["artifacts"] == [item.model_dump(mode="json") for item in projected.artifacts]
    assert observed["stdout_evidence"] == projected.stdout_evidence.model_dump(mode="json")
    assert "secret raw bytes" not in printed
    assert calls == ["run4-real"]
