"""RUN-5: a compact handoff transporting only existing Fleet/Game Tape facts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.game_tape import GameTapeIssueFocus, GameTapeWhyNotDiagnostic
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus, GameTapeStatus


class _Model:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.value


class _Tape:
    def __init__(self, status=GameTapeStatus.FAILED, *, focus=(), why_not=None, count=0):
        self.status = status
        self.plan_id = "canonical-plan"
        self.summary = _Model({"failed_steps": 1, "executed_steps": 2})
        self.receipt = _Model({"validation": "PASSED", "finished_at": "canonical-time"})
        self.observability = _Model(
            {
                "failure_navigation_source": "durable_failure_evidence",
                "raw_output_survives_process_restart": False,
            }
        )
        self.issue_count = count
        self.issue_focus = focus
        self.hidden_issue_count = count - len(focus)
        self.why_not = why_not
        self.enforcer_receipt_available = False
        self.repositories = ()


class _RunOwner:
    def __init__(self, *, status=FleetRunStatus.FAILED, outcome=FleetRunOutcome.FAILED, tape=None):
        self.calls = []
        self.status = status
        self.outcome = outcome
        self.tape = tape if tape is not None else _Tape()
        self.reject = False

    def get(self, run_id):
        self.calls.append(("get", run_id))
        if self.reject:
            raise InvalidRequest("unknown Fleet Run")
        return SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id=run_id,
                status=self.status,
                progress=_Model({"total": 2, "failed": 1}),
            ),
            outcome=self.outcome,
            game_tape=self.tape,
            repository_admissions=(),
            journal_revision=42,
        )

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected parallel Run authority: {name}")


def _invoke(owner, *args):
    parsed = build_parser().parse_args(["plan", "handoff", "run-5", *args])
    return run_plan_command(SimpleNamespace(runs=owner), parsed)


def test_handoff_preserves_exact_original_failure_guidance_and_focus(capsys):
    focus = GameTapeIssueFocus(
        scope="STEP",
        repository_execution_id="repo-exec",
        repository_id="repo-1",
        repository_name="one",
        step_execution_id="step-exec",
        step_id="verify",
        code="STEP_FAIL",
        message="canonical explanation",
        signature_id="sig-one",
    )
    why = GameTapeWhyNotDiagnostic(
        code="DENIED",
        stage="ADMISSION",
        retryable=False,
        authority_owner="execution_authority",
        message="Original rejection",
        safe_next_action="Inspect the original authenticated admission receipt",
    )
    # A genuine Game Tape exposes all five focus slots when other issues are hidden.
    visible_focus = (focus,) + tuple(
        GameTapeIssueFocus(scope="RUN", code=f"RUN_ISSUE_{i}") for i in range(4)
    )
    tape = _Tape(focus=visible_focus, why_not=why, count=7)
    owner = _RunOwner(tape=tape)
    assert _invoke(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "oh-goon.plan-run-handoff/v1"
    assert result["journal_revision"] == 42
    assert result["status"] == "FAILED" and result["outcome"] == "FAILED"
    assert result["game_tape_status"] == "FAILED"
    assert result["issue_focus"] == [item.model_dump(mode="json") for item in visible_focus]
    assert result["why_not"] == why.model_dump(mode="json")
    assert result["issue_count"] == 7 and result["hidden_issue_count"] == 2
    assert result["summary"] == tape.summary.model_dump(mode="json")
    assert result["receipt"] == tape.receipt.model_dump(mode="json")
    assert result["observability"] == tape.observability.model_dump(mode="json")
    assert owner.calls == [("get", "run-5")]
    assert "raw_stdout" not in json.dumps(result)


@pytest.mark.parametrize(
    "status,outcome",
    [
        (FleetRunStatus.COMPLETED, FleetRunOutcome.SUCCESS),
        (FleetRunStatus.COMPLETED, FleetRunOutcome.ATTENTION_REQUIRED),
        (FleetRunStatus.CANCELLED, FleetRunOutcome.CANCELLED),
        (FleetRunStatus.RUNNING, FleetRunOutcome.IN_PROGRESS),
    ],
)
def test_handoff_does_not_derive_outcome_or_invent_missing_failure_facts(
    capsys,
    status,
    outcome,
):
    tape = _Tape(status=GameTapeStatus.RUNNING, count=0)
    tape.plan_id = None  # Non-Plan Fleet Run has no Plan identity.
    owner = _RunOwner(status=status, outcome=outcome, tape=tape)
    assert _invoke(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status.value
    assert result["outcome"] == outcome.value
    assert result["game_tape_status"] == tape.status.value
    assert result["plan_id"] is None
    assert result["why_not"] is None
    assert "unavailable journal payload" in result["native_payload_retention"]
    assert result["supporting_diagnostics"]["path_readback_argv"] == [
        "oh-goon",
        "local-state",
        "diagnostics-path",
    ]
    assert result["issue_focus"] == []
    assert result["issue_count"] == result["hidden_issue_count"] == 0
    assert owner.calls == [("get", "run-5")]


def test_text_handoff_preserves_execution_anchor_and_original_guidance(capsys):
    focus = GameTapeIssueFocus(
        scope="STEP",
        repository_execution_id="repo-exec",
        repository_id="repo-1",
        repository_name="repo",
        step_execution_id="step-exec",
        step_id="build",
        code="BUILD_FAILED",
    )
    why = GameTapeWhyNotDiagnostic(
        code="NO_RETRY",
        stage="EXECUTION",
        retryable=False,
        authority_owner="fleet_run",
        message="No retry",
        safe_next_action="Original safe action",
    )
    owner = _RunOwner(tape=_Tape(focus=(focus,), why_not=why, count=1))
    assert _invoke(owner) == 0
    output = capsys.readouterr().out
    assert "step-exec" in output and "BUILD_FAILED" in output
    assert "Original safe action" in output
    assert "raw logs may be unavailable" in output
    assert owner.calls == [("get", "run-5")]


def test_unknown_run_preserves_original_readback_error_contract(capsys):
    owner = _RunOwner()
    owner.reject = True
    assert _invoke(owner, "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "oh-goon.plan-run-read-error/v1"
    assert result["fleet_run_id"] == "run-5"
    assert result["status"] == "error"
    assert owner.calls == [("get", "run-5")]


def test_handoff_transports_actual_fleet_detail_game_tape(tmp_path, capsys):
    """The original computed Game Tape is the only source of diagnostic fields."""
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
    from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        fleet_repository_scope,
    )
    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.core.time import utc_now
    from oh_goon.core.vocabulary import OperationAction
    from tests.factories.capabilities import capability_snapshot
    from tests.factories.fleet_plan_targets import structural_execution_identity
    from tests.factories.run_admission import repository_admissions_for

    plan = ResolvedFleetPlan.create(
        name="run5-handoff",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="check", argv=("true",), timeout_seconds=30),),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="1" * 64),
        identity=structural_execution_identity(plan),
    )
    repository = RepositoryIdentity(path=tmp_path / "repo")
    now = utc_now()
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run-5",
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
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(run.target, run.targets),
    )
    owner = SimpleNamespace(get=lambda run_id: detail)
    assert _invoke(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    tape = detail.game_tape
    assert result["fleet_run_id"] == tape.fleet_run_id
    assert result["plan_id"] == plan.plan_id
    assert result["outcome"] == detail.outcome.value
    source = detail.repository_admissions[0]
    assert result["source_admissions"][0]["admission_id"] == source.admission_id
    assert (
        result["source_admissions"][0]["source_authority_id"]
        == source.source_authority.authority_id
    )
    assert result["receipt"] == tape.receipt.model_dump(mode="json")
    assert result["summary"] == tape.summary.model_dump(mode="json")
    assert result["issue_focus"] == [item.model_dump(mode="json") for item in tape.issue_focus]
    assert result["observability"] == tape.observability.model_dump(mode="json")


def test_handoff_preserves_failed_verification_beside_successful_untrusted_claim(capsys):
    from oh_goon.core.models.fleet_observation import (
        AgentHarnessObservation,
        NativeAgentObservation,
    )

    claim = NativeAgentObservation(
        agent="codex",
        implementation_identity="native",
        profile="profile",
        input_identity="input",
        occurrence="native",
        source_identity="source",
        role="report",
        settled=True,
        failed=False,
        native_event_count=1,
        events=(
            AgentHarnessObservation(
                kind="agent",
                summary="Untrusted native final summary: fixed everything " + "x" * 800,
            ),
        ),
    )
    step = SimpleNamespace(
        step_execution_id="verify-exec",
        step_id="verify",
        kind="command",
        status=GameTapeStatus.FAILED,
        exit_code=7,
        reason="Independent verifier failed",
        native_agent_observations=(claim,),
    )
    tape = _Tape()
    tape.repositories = (SimpleNamespace(repository_execution_id="repository-exec", steps=(step,)),)
    owner = _RunOwner(tape=tape)
    assert _invoke(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    retained = result["recorded_steps"]["steps"][0]
    assert retained["status"] == "FAILED" and retained["exit_code"] == 7
    assert retained["native_claims"][0]["settled_claim"] is True
    assert retained["native_claims"][0]["authority"] == "UNTRUSTED_HARNESS_CLAIM"
    assert retained["native_claims"][0]["events"][0]["omitted_summary_characters"] > 0
    assert owner.calls == [("get", "run-5")]


def test_handoff_bounds_steps_and_qualifies_absent_native_claims(capsys):
    step = SimpleNamespace(
        step_execution_id="exec",
        step_id="check",
        kind="command",
        status=GameTapeStatus.RUNNING,
        exit_code=None,
        reason=None,
        native_agent_observations=(),
    )
    tape = _Tape()
    tape.repositories = (
        SimpleNamespace(repository_execution_id="repository-exec", steps=(step,) * 33),
    )
    assert _invoke(_RunOwner(tape=tape), "--json") == 0
    retained = json.loads(capsys.readouterr().out)["recorded_steps"]
    assert len(retained["steps"]) == 32 and retained["omitted_steps"] == 1
    assert retained["steps"][0]["status"] == "RUNNING"
    assert retained["steps"][0]["exit_code"] is None
    assert retained["steps"][0]["native_claim_availability"] == "unavailable"


def test_handoff_claim_excerpt_retains_origin_tail_and_exact_omission_accounting(capsys):
    from oh_goon.core.models.fleet_observation import (
        AgentHarnessObservation,
        NativeAgentObservation,
    )

    summary = (
        "Controller wrapper: original attempt\n"
        + "x" * 1000
        + "\nOrigin: require_no_mcp_servers\nRemaining: qualify an empty MCP catalog"
    )
    observation = NativeAgentObservation(
        agent="codex",
        implementation_identity="native",
        profile="profile",
        input_identity="input",
        occurrence="native",
        source_identity="source",
        role="report",
        settled=False,
        failed=True,
        native_event_count=0,
        events=(AgentHarnessObservation(kind="context", summary=summary),),
    )
    step = SimpleNamespace(
        step_execution_id="native-exec",
        step_id="native-agent",
        kind="agent",
        status=GameTapeStatus.FAILED,
        exit_code=125,
        reason="Native preflight rejected",
        native_agent_observations=(observation,),
    )
    tape = _Tape()
    tape.repositories = (SimpleNamespace(repository_execution_id="repository-exec", steps=(step,)),)
    owner = _RunOwner(
        status=FleetRunStatus.COMPLETED, outcome=FleetRunOutcome.ATTENTION_REQUIRED, tape=tape
    )

    assert _invoke(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    recorded = result["recorded_steps"]["steps"][0]
    event = recorded["native_claims"][0]["events"][0]
    excerpt = event["summary"]
    marker = "\n[... summary characters omitted ...]\n"
    assert len(excerpt) == 512 and excerpt.count(marker) == 1
    assert excerpt.startswith("Controller wrapper:")
    assert "Origin: require_no_mcp_servers" in excerpt
    assert excerpt.endswith("Remaining: qualify an empty MCP catalog")
    assert len(excerpt) - len(marker) + event["omitted_summary_characters"] == len(summary)
    assert result["status"] == "COMPLETED" and result["outcome"] == "ATTENTION_REQUIRED"
    assert recorded["status"] == "FAILED" and recorded["exit_code"] == 125
    assert owner.calls == [("get", "run-5")]
