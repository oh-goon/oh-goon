"""The original executor transports the full admitted identity into native Steps."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def test_direct_step_runtime_uses_full_admitted_lock_identity(tmp_path: Path, monkeypatch):
    source = tmp_path / "goon.yml"
    source.write_text("version: 1\nsteps: [{id: check, run: 'true'}]\n")
    plan, sha = FleetPlanResolver().resolve_with_source(source)
    lock = build_execution_lock(plan, external_artifacts=(("run.yml", b"selected configuration"),))
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256=sha),
        identity=ExecutionIdentity(digest=lock.digest),
        lock_artifacts=lock.external,
    )
    seen = []

    class Sequence:
        results = ()
        stdout = ""
        stderr = ""

        def __init__(self, repository, runtime, observer):
            self.runtime = runtime

        def run_direct(self, step):
            seen.append(self.runtime.execution_identity)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_run_execution.FleetPlanStepSequence", Sequence
    )
    executor = FleetPlanRunExecutor(GitService(), process_isolation_backends=())
    executor.execute_direct_run(tmp_path, plan, cancel_requested=None, execution_target=target)
    assert seen == [target.identity]
    assert seen[0].digest == lock.digest
    assert seen[0].digest != build_execution_lock(plan).digest
    seen.clear()
    changed = plan.model_copy(
        update={"steps": (plan.steps[0].model_copy(update={"argv": ("false",)}),)}
    )
    with pytest.raises(FleetPlanInvalid, match="differs from the selected Plan"):
        executor.execute_direct_run(
            tmp_path, changed, cancel_requested=None, execution_target=target
        )
    assert seen == []
