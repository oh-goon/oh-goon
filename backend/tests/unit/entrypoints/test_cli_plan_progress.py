from __future__ import annotations

import os
import tty
from types import SimpleNamespace

import oh_goon.cli_runtime_plans as plans
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan, ResolvedImport
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.run import FleetRunProgress, QueuedFleetRun
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def _target(authored):
    imports = tuple(
        ResolvedImport(
            import_id=f"goon-{index}",
            name=item.name,
            source_sha256="a" * 64,
            step_ids=item.step_ids,
        )
        for index, item in enumerate(authored.imports)
    )
    step_ids = tuple(dict.fromkeys(step for item in imports for step in item.step_ids))
    resolved = ResolvedFleetPlan.create(
        name="activity",
        description="",
        execution=VerificationExecutionPolicy(),
        imports=imports,
        steps=tuple(
            ResolvedCommandStep(step_id=step, argv=("true",), timeout_seconds=30)
            for step in step_ids or ("placeholder",)
        ),
    )
    return structural_fleet_plan_target(resolved)


def _run_state(target):
    return QueuedFleetRun(
        fleet_run_id="fleet-a",
        status=FleetRunStatus.QUEUED,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(),
        accepted_at=utc_now(),
        progress=FleetRunProgress(total=0, succeeded=0, failed=0),
    )


class _Runs:
    def __init__(self, terminal_detail, batches=()):
        self._terminal_detail = terminal_detail
        self._batches = list(batches)
        self.calls = 0

    def wait_for_terminal(self, fleet_run_id: str, *, timeout: float):
        assert fleet_run_id == "fleet-a"
        assert timeout == plans._PROGRESS_WAIT_SLICE_SECONDS
        self.calls += 1
        if self.calls <= 2:
            raise TimeoutError
        return self._terminal_detail

    def game_tape_live_output(self, fleet_run_id: str, *, after: int = 0):
        assert fleet_run_id == "fleet-a"
        if self._batches:
            return self._batches.pop(0)
        return SimpleNamespace(available=True, chunks=(), next_sequence=after)

    def get(self, fleet_run_id: str):
        assert fleet_run_id == "fleet-a"
        return SimpleNamespace(run=SimpleNamespace(target=None))


def test_non_tty_wait_suppresses_time_only_heartbeat_noise(monkeypatch, capsys) -> None:
    detail = object()
    runs = _Runs(detail)
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 5.0, 10.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    observed = plans._wait_for_terminal_with_progress(runtime, "fleet-a")

    assert observed is detail
    assert capsys.readouterr().out == ""


def test_wait_progress_does_not_claim_step_state_without_live_output(monkeypatch, capsys) -> None:
    detail = object()
    runs = _Runs(detail)
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    plans._wait_for_terminal_with_progress(runtime, "fleet-a")

    output = capsys.readouterr().out
    assert output == ""
    assert "Observed activity" not in output
    assert "Goon" not in output


def test_progress_descriptor_bypasses_ordinary_stdout(monkeypatch, capsys) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("OH_GOON_PROGRESS_FD", str(write_fd))
        plans._progress_print("[/] Certification working · 00:00:05 · Goon 1/14")
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 4096) == (
            b"[/] Certification working \xc2\xb7 00:00:05 \xc2\xb7 Goon 1/14\n"
        )
        assert capsys.readouterr().out == ""
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


def test_progress_heartbeat_is_suppressed_on_pipe_but_milestone_is_preserved(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("OH_GOON_PROGRESS_FD", str(write_fd))
        plans._progress_heartbeat("[/] Certification working · 00:00:05 · Goon 1/14")
        plans._progress_print("Observed activity: Goon 1/14 — backend-tests")
        os.close(write_fd)
        write_fd = -1
        output = os.read(read_fd, 4096).decode()
        assert output == "Observed activity: Goon 1/14 — backend-tests\n"
        assert "Certification working" not in output
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


def test_progress_heartbeat_overwrites_one_interactive_terminal_line(monkeypatch) -> None:
    master_fd, slave_fd = os.openpty()
    try:
        tty.setraw(slave_fd)
        monkeypatch.setenv("OH_GOON_PROGRESS_FD", str(slave_fd))
        plans._progress_heartbeat("[|] Certification working · 00:00:00 · waiting")
        plans._progress_heartbeat("[/] Certification working · 00:00:05 · preparing")
        plans._progress_print("Activity: Execution inputs ready")
        plans._progress_clear()
        os.set_blocking(master_fd, False)
        chunks = []
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        output = b"".join(chunks).decode()
        assert output == (
            "\r\x1b[2K[|] Certification working · 00:00:00 · waiting"
            "\r\x1b[2K[/] Certification working · 00:00:05 · preparing"
            "\r\x1b[2KActivity: Execution inputs ready\n"
            "\r\x1b[2K"
        )
        assert output.count("\n") == 1
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_wait_progress_surfaces_distinct_live_goon_step_activity(monkeypatch, capsys) -> None:
    detail = object()
    chunk_a = SimpleNamespace(step_id="goon-a.step", attempt=1)
    chunk_b = SimpleNamespace(step_id="goon-b.step", attempt=2)
    batches = (
        SimpleNamespace(available=True, chunks=(chunk_a, chunk_a), next_sequence=2),
        SimpleNamespace(available=True, chunks=(chunk_b,), next_sequence=3),
    )
    runs = _Runs(detail, batches=batches)
    plan = SimpleNamespace(
        imports=(
            SimpleNamespace(name="oh-goon/a", step_ids=("goon-a.step",)),
            SimpleNamespace(name="oh-goon/b", step_ids=("goon-b.step",)),
        )
    )
    runs.get = lambda _fleet_run_id: _run_state(_target(plan))
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    plans._wait_for_terminal_with_progress(runtime, "fleet-a")

    output = capsys.readouterr().out
    assert output.count("Goon 1/2 — oh-goon/a | Step: goon-a.step") == 1
    assert "Goon 2/2 — oh-goon/b | Step: goon-b.step | attempt 2" in output
    assert "Status:" not in output


def test_wait_progress_surfaces_bounded_activity_markers(monkeypatch, capsys) -> None:
    detail = object()
    chunk_a = SimpleNamespace(
        step_id="goon-a.step",
        attempt=1,
        text="OH-GOON ACTIVITY: import Python environment\nnoise that must not stream",
    )
    chunk_b = SimpleNamespace(
        step_id="goon-a.step",
        attempt=1,
        text="OH-GOON ACTIVITY: import frontend node_modules\n",
    )
    batches = (
        SimpleNamespace(available=True, chunks=(chunk_a,), next_sequence=1),
        SimpleNamespace(available=True, chunks=(chunk_b,), next_sequence=2),
    )
    runs = _Runs(detail, batches=batches)
    plan = SimpleNamespace(
        imports=(SimpleNamespace(name="oh-goon/dependency-materialize", step_ids=("goon-a.step",)),)
    )
    runs.get = lambda _fleet_run_id: _run_state(_target(plan))
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    plans._wait_for_terminal_with_progress(runtime, "fleet-a")

    output = capsys.readouterr().out
    assert output.count("Observed activity: Goon 1/1") == 1
    assert (
        "Activity detail: Goon 1/1 — oh-goon/dependency-materialize | import Python environment"
        in output
    )
    assert (
        "Activity detail: Goon 1/1 — oh-goon/dependency-materialize | import frontend node_modules"
        in output
    )
    assert "noise that must not stream" not in output


def test_repair_handoff_falls_back_to_durable_game_tape_step(capsys) -> None:
    plan = SimpleNamespace(
        plan_id="fleet-plan-a",
        imports=(
            SimpleNamespace(
                name="oh-goon/dependency-materialize",
                step_ids=("oh-goon-dependency-materialize.make",),
            ),
        ),
    )
    run = SimpleNamespace(
        fleet_run_id="fleet-a",
        target=_target(plan),
    )
    failed = SimpleNamespace(
        status=plans.GameTapeStatus.FAILED,
        step_id="oh-goon-dependency-materialize.make",
        command=("make", "certification-workspace-setup"),
        exit_code=2,
    )
    detail = SimpleNamespace(
        run=run,
        operations=(),
        game_tape=SimpleNamespace(repositories=(SimpleNamespace(steps=(failed,)),)),
    )
    runtime = SimpleNamespace(
        runs=SimpleNamespace(game_tape_live_output=lambda _run_id: SimpleNamespace(available=False))
    )

    plans._print_repair_handoff(runtime, detail)

    output = capsys.readouterr().out
    assert "Goon: 1/1 — oh-goon/dependency-materialize" in output
    assert "Step: oh-goon-dependency-materialize.make" in output
    assert "Exit code: 2" in output
    assert "Canonical repair command: make certification-workspace-setup" in output
    assert "Failure evidence: INCOMPLETE" not in output


def test_repair_handoff_guidance_is_invariant_to_goon_rename(capsys) -> None:
    def render(goon_name: str) -> str:
        plan = SimpleNamespace(
            plan_id="fleet-plan-a",
            imports=(
                SimpleNamespace(
                    name=goon_name,
                    step_ids=("backend-tests.make",),
                ),
            ),
        )
        failed = SimpleNamespace(
            status=plans.GameTapeStatus.FAILED,
            step_id="backend-tests.make",
            command=("make", "test-python-complete"),
            exit_code=2,
        )
        detail = SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id="fleet-a",
                target=_target(plan),
            ),
            operations=(),
            game_tape=SimpleNamespace(repositories=(SimpleNamespace(steps=(failed,)),)),
        )
        runtime = SimpleNamespace(
            runs=SimpleNamespace(
                game_tape_live_output=lambda _run_id: SimpleNamespace(available=False)
            )
        )
        plans._print_repair_handoff(runtime, detail)
        return capsys.readouterr().out

    canonical = render("oh-goon/backend-tests")
    renamed = render("oh-goon/backend-tests-renamed")

    assert "Goon: 1/1 — oh-goon/backend-tests\n" in canonical
    assert "Goon: 1/1 — oh-goon/backend-tests-renamed\n" in renamed
    canonical_workflow = canonical.split("\nRepair workflow:\n", 1)[1]
    renamed_workflow = renamed.split("\nRepair workflow:\n", 1)[1]
    assert canonical_workflow == renamed_workflow
    assert "Focused diagnostic command: make test-python-complete" in canonical_workflow
    assert "make verify-p0" not in canonical_workflow


def test_repair_handoff_declares_incomplete_evidence_instead_of_claiming_complete(capsys) -> None:
    plan = SimpleNamespace(plan_id="fleet-plan-a", imports=())
    detail = SimpleNamespace(
        run=SimpleNamespace(
            fleet_run_id="fleet-a",
            target=_target(plan),
        ),
        operations=(),
        game_tape=SimpleNamespace(repositories=()),
    )
    runtime = SimpleNamespace(runs=SimpleNamespace())

    plans._print_repair_handoff(runtime, detail)

    output = capsys.readouterr().out
    assert "Failure evidence: INCOMPLETE" in output
    assert "Do not treat this as a complete repair handoff" in output
    assert "make certification-explain OH_GOON_RUN_ID=fleet-a" in output


def test_repair_handoff_projects_durable_step_teardown_failure(capsys) -> None:
    import hashlib

    from oh_goon.core.models.fleet_plan_evidence import (
        CompletedPlanStepExecution,
        FailedPlanStepTeardownEvidence,
        FleetPlanResult,
        PlanOutputEvidence,
        PlanStepSettlementEvidence,
    )
    from oh_goon.core.models.operation import FleetPlanOperationEvidence

    def output() -> PlanOutputEvidence:
        return PlanOutputEvidence(
            sha256=hashlib.sha256(b"").hexdigest(), byte_count=0, line_count=0
        )

    plan = SimpleNamespace(
        plan_id="fleet-plan-a",
        imports=(
            SimpleNamespace(
                name="oh-goon/dependency-materialize",
                step_ids=("oh-goon-dependency-materialize.make",),
            ),
        ),
    )
    run = SimpleNamespace(fleet_run_id="fleet-a", target=_target(plan))
    execution = CompletedPlanStepExecution(
        step_id="oh-goon-dependency-materialize.make",
        kind="command",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        argv=("make", "certification-workspace-setup"),
        stdout_evidence=output(),
        stderr_evidence=output(),
        repository_state_before="before",
        repository_state_after="after",
        teardown=FailedPlanStepTeardownEvidence(
            attempt_roots_observed=1, reason_code="ROOT_REMAINS"
        ),
    )
    plan_result = FleetPlanResult.model_construct(
        plan_id="fleet-plan-a", steps=(execution,), execution_inputs=None
    )
    evidence = FleetPlanOperationEvidence.model_construct(kind="FLEET_PLAN", plan=plan_result)
    detail = SimpleNamespace(
        run=run,
        operations=(SimpleNamespace(evidence=evidence),),
        game_tape=SimpleNamespace(repositories=()),
    )
    runtime = SimpleNamespace(
        runs=SimpleNamespace(game_tape_live_output=lambda _run_id: SimpleNamespace(available=False))
    )

    plans._print_repair_handoff(runtime, detail)

    output_text = capsys.readouterr().out
    assert "Failure evidence: COMPLETE (durable step teardown)" in output_text
    assert "Failure boundary: step_teardown" in output_text
    assert "Teardown reason: ROOT_REMAINS" in output_text
    assert "Command exit code: 0" in output_text
    assert "Canonical step command: make certification-workspace-setup" in output_text
    assert "Failure evidence: INCOMPLETE" not in output_text
    assert "Inspect the durable teardown reason above" in output_text
    assert "Focused diagnostic command:" not in output_text


def test_wait_progress_surfaces_pre_step_run_activity(monkeypatch, capsys) -> None:
    detail = object()
    runs = _Runs(detail)
    activities = [
        (1, ((1, "Run started; preparing repository execution"),)),
        (2, ((2, "Preparing immutable execution inputs for repo"),)),
    ]

    def transient(_fleet_run_id: str, *, after: int = 0):
        if activities:
            return activities.pop(0)
        return after, ()

    runs.transient_run_activity = transient  # type: ignore[attr-defined]
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    plans._wait_for_terminal_with_progress(runtime, "fleet-a")

    output = capsys.readouterr().out
    assert output.splitlines() == [
        "Activity: Run started; preparing repository execution",
        "Activity: Preparing immutable execution inputs for repo",
    ]


def test_wait_progress_uses_admitted_plan_without_durable_owner_lookup(monkeypatch, capsys) -> None:
    detail = object()
    runs = _Runs(detail)
    runs.get = lambda _run_id: (_ for _ in ()).throw(
        AssertionError("active progress must not read durable Run detail")
    )
    plan = SimpleNamespace(
        imports=(SimpleNamespace(name="oh-goon/dependency-materialize", step_ids=("goon-a.step",)),)
    )
    admitted = SimpleNamespace(target=_target(plan))
    runtime = SimpleNamespace(runs=runs)
    times = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(plans.time, "monotonic", lambda: next(times))

    observed = plans._wait_for_terminal_with_progress(
        runtime,
        "fleet-a",
        admitted_run=admitted,
    )

    assert observed is detail
    assert capsys.readouterr().out == ""
