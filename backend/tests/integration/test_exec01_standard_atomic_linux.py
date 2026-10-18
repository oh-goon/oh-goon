"""EXEC-01A: real standard Atomic Goon processes through canonical production owners.

This is *not* the FleetRun/Enforcer/Game Tape admission or a sandbox test.
It proves the source -> locked target -> existing Step executor physical cut.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import prepare_execution_target


EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "standard-atomic"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/WSL host process integration")


def _ready(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLE, repo)
    source = repo / "goon.yml"
    target = prepare_execution_target(
        FleetPlanResolver(), source, lock_path=repo / "goon.lock"
    )
    executor = FleetPlanRunExecutor(GitService(), process_isolation_backends=())
    executor.prepare_direct_run(
        RepositoryIdentity(path=repo), target.plan, cancel_requested=None
    )
    return repo, target, executor


def test_real_locked_atomic_goon_runs_original_step_and_observes_output(tmp_path: Path) -> None:
    repo, target, executor = _ready(tmp_path)
    events = []

    def observe(step_id, order, event_type, **detail):
        events.append((step_id, order, event_type, detail))

    outcome = executor.execute_direct_run(
        repo, target.plan, cancel_requested=None, lifecycle_observer=observe
    )
    assert len(outcome.steps) == 1
    step = outcome.steps[0]
    assert step.step_id == "hello"
    assert step.status == "COMPLETED"
    assert step.returncode == 0
    assert outcome.stdout == "oh-goon-atomic-ok"
    assert outcome.stderr == ""
    assert step.stdout_evidence.sha256 == hashlib.sha256(b"oh-goon-atomic-ok").hexdigest()
    assert step.attempts[0].tool.name == "sh"
    assert step.attempts[0].tool.origin == "host"  # deliberately NOT sandboxed
    assert any(event[2] == "PROCESS_STARTED" for event in events)
    assert any(event[2] == "PROCESS_EXITED" for event in events)


def test_real_locked_atomic_goon_reports_process_failure(tmp_path: Path) -> None:
    repo, target, executor = _ready(tmp_path)
    (repo / "scripts" / "hello.sh").write_text(
        "#!/bin/sh\nprintf 'atomic-failed\\n'\nexit 17\n", encoding="utf-8"
    )
    outcome = executor.execute_direct_run(repo, target.plan, cancel_requested=None)
    assert outcome.steps[0].status == "FAILED"
    assert outcome.steps[0].returncode == 17
    assert outcome.steps[0].attempts[0].returncode == 17
    assert outcome.stdout == "atomic-failed"


def test_real_locked_atomic_goon_cancels_owned_process(tmp_path: Path) -> None:
    repo, target, executor = _ready(tmp_path)
    (repo / "scripts" / "hello.sh").write_text(
        "#!/bin/sh\nsleep 30\n", encoding="utf-8"
    )
    since = time.monotonic()
    with pytest.raises(OperationCancelledError):
        executor.execute_direct_run(
            repo, target.plan,
            cancel_requested=lambda: time.monotonic() - since > 0.3,
        )


def test_locked_atomic_goon_refuses_changed_definition_before_execution(
    tmp_path: Path,
) -> None:
    repo, target, executor = _ready(tmp_path)
    (repo / "goon.yml").write_text(
        "version: 1\nexecution:\n  purpose: maintenance\nsteps:\n"
        "  - id: other\n    run: sh scripts/hello.sh\n",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid):
        prepare_execution_target(
            FleetPlanResolver(), repo / "goon.yml", lock_path=repo / "goon.lock"
        )
    # The previously prepared target is not used for execution after source drift.
    assert target.plan.steps[0].step_id == "hello"


def test_original_supervisor_enforces_typed_step_deadline_on_real_process(
    tmp_path: Path,
) -> None:
    """Deadline semantics at the real cut; command YAML still owns backend default."""
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep, PLAN_TIMEOUT_RETURN_CODE
    from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step

    repo, _target, _executor = _ready(tmp_path)
    (repo / "scripts" / "hello.sh").write_text(
        "#!/bin/sh\nsleep 30\n", encoding="utf-8"
    )
    # The original resolved Step model permits backend-supplied deadlines.
    # Authoring does not support overriding normal run timeout_seconds.
    step = ResolvedCommandStep(
        step_id="deadline", argv=("sh", "scripts/hello.sh"), timeout_seconds=0.25
    )
    result = execute_direct_step(
        repo, step,
        StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
        step_order=1,
    )
    assert result.returncode == PLAN_TIMEOUT_RETURN_CODE
    assert "timed out" in result.stderr
