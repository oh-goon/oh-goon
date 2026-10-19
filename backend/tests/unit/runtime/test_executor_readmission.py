from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import oh_goon.services.fleet_run_control_plane as fleet_run_control_plane_module
from oh_goon.core.errors import ExecutorReadmissionNotAllowed, JournalConflict
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import ExplicitRepositoryScope, StrictFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, FleetRunStatus, OperationAction
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def _target() -> FleetPlanTarget:
    plan = ResolvedFleetPlan.create(
        name="readmission",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="noop",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    return structural_fleet_plan_target(plan)


def _wire_readmission_dependencies(
    manager: FleetRunControlPlane,
    detail: object,
    child: object | None = None,
    *,
    reads: list[str] | None = None,
) -> None:
    manager._lifecycle = SimpleNamespace(  # type: ignore[attr-defined]
        serialize_admission=lambda _identity: nullcontext(),
        require_fleet_prework_available=lambda *args, **kwargs: None,
    )

    def read_view():
        if reads is not None:
            reads.append("read")
        return SimpleNamespace(
            required_run_detail=lambda _run_id: detail,
            readmission_child=lambda _run_id: child,
        )

    manager._journal = SimpleNamespace(read_view=read_view)  # type: ignore[attr-defined]


def _detail(*, failure_code: str) -> SimpleNamespace:
    target = _target()
    return SimpleNamespace(
        run=SimpleNamespace(
            fleet_run_id="old-run",
            status=FleetRunStatus.FAILED,
            failure=SimpleNamespace(code=failure_code),
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=ExplicitRepositoryScope(repository_ids=("repo-a", "repo-b")),
            progress=SimpleNamespace(completed=0),
            source=FleetRunSource.CICD,
            action=OperationAction.EXECUTE_PLAN,
            targets=(
                RepositoryIdentity(path=Path("/repos/repo-a")),
                RepositoryIdentity(path=Path("/repos/repo-b")),
            ),
        )
    )


def _existing_child(parent: SimpleNamespace, *, fleet_run_id: str = "new-run") -> SimpleNamespace:
    """An already-projected strict child; no second admission or native issuer."""
    run = SimpleNamespace(
        fleet_run_id=fleet_run_id,
        source=parent.run.source,
        action=parent.run.action,
        capability=parent.run.capability,
        target=parent.run.target,
        repository_scope=parent.run.repository_scope,
        targets=parent.run.targets,
    )
    # Only the lineage discriminator is observed by this read-only service test.
    execution = StrictFleetExecutionAdmission.model_construct(
        readmitted_from_run_id="old-run", rerun_from_run_id=None
    )
    return SimpleNamespace(run=run, execution=execution)


def test_readmission_creates_new_intent_and_preserves_source_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object.__new__(FleetRunControlPlane)
    detail = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    _wire_readmission_dependencies(manager, detail)
    resolved = object()
    monkeypatch.setattr(
        fleet_run_control_plane_module,
        "resolve_bound_capability_intent",
        lambda _capability, _target: resolved,
    )
    captured: dict[str, object] = {}

    def _start(
        intent, *, repository_scope, fleet_run_id, source, readmitted_from_run_id, rerun_from_run_id
    ):
        captured.update(
            intent=intent,
            repository_scope=repository_scope,
            fleet_run_id=fleet_run_id,
            source=source,
            readmitted_from_run_id=readmitted_from_run_id,
            rerun_from_run_id=rerun_from_run_id,
        )
        return "new-state"

    manager._start_intent = _start  # type: ignore[method-assign]

    result = manager.readmit("old-run", new_fleet_run_id="new-run")

    assert result == "new-state"
    assert captured == {
        "intent": resolved,
        "repository_scope": detail.run.repository_scope,
        "fleet_run_id": "new-run",
        "source": FleetRunSource.CICD,
        "readmitted_from_run_id": "old-run",
        "rerun_from_run_id": None,
    }


def test_readmission_rejects_strict_target_without_cicd_provenance() -> None:
    manager = object.__new__(FleetRunControlPlane)
    detail = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    detail.run.source = FleetRunSource.MANUAL
    _wire_readmission_dependencies(manager, detail)

    with pytest.raises(ExecutorReadmissionNotAllowed, match="CICD provenance"):
        manager.readmit("old-run", new_fleet_run_id="new-run")


def test_readmission_is_rejected_for_non_placement_failure() -> None:
    manager = object.__new__(FleetRunControlPlane)
    _wire_readmission_dependencies(manager, _detail(failure_code="UNEXPECTED_RUN_FAILURE"))

    with pytest.raises(ExecutorReadmissionNotAllowed, match="EXECUTOR_PLACEMENT_LOST"):
        manager.readmit("old-run", new_fleet_run_id="new-run")


def test_readmission_replays_existing_child_for_same_new_identity() -> None:
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    child = _existing_child(parent)
    _wire_readmission_dependencies(manager, parent, child)
    manager._start_intent = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("existing re-admission child must be replayed without a second start")
    )

    assert manager.readmit("old-run", new_fleet_run_id="new-run") is child.run


def test_readmission_replays_durable_child_even_when_retry_uses_a_new_command_identity() -> None:
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    child = _existing_child(parent, fleet_run_id="first-child")
    _wire_readmission_dependencies(manager, parent, child)
    manager._start_intent = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("durable re-admission lineage must win over a fresh client command identity")
    )

    assert manager.readmit("old-run", new_fleet_run_id="second-client-id") is child.run


def test_readmission_parent_and_child_use_one_journal_view() -> None:
    manager = object.__new__(FleetRunControlPlane)
    detail = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    child = _existing_child(detail)
    reads: list[str] = []
    _wire_readmission_dependencies(manager, detail, child, reads=reads)

    assert manager.readmit("old-run", new_fleet_run_id="new-run") is child.run
    assert reads == ["read"]


@pytest.mark.parametrize(
    "mismatch",
    ["parent", "source", "capability", "plan", "repository_scope", "repositories", "rerun"],
)
def test_child_lookup_rejects_cross_run_projection_before_replay(
    mismatch: str,
) -> None:
    """Reproduce former bypass: a substituted child was returned without proof."""
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    child = _existing_child(parent)
    if mismatch == "parent":
        child.execution = StrictFleetExecutionAdmission.model_construct(
            readmitted_from_run_id="another-run", rerun_from_run_id=None
        )
    elif mismatch == "source":
        child.run.source = FleetRunSource.MANUAL
    elif mismatch == "capability":
        child.run.capability = capability_snapshot(OperationAction.STATUS)
    elif mismatch == "plan":
        child.run.target = None
    elif mismatch == "repository_scope":
        child.run.repository_scope = ExplicitRepositoryScope(repository_ids=("repo-c",))
    elif mismatch == "repositories":
        child.run.targets = (RepositoryIdentity(path=Path("/repos/other")),)
    else:
        child.execution = StrictFleetExecutionAdmission.model_construct(
            readmitted_from_run_id="old-run", rerun_from_run_id="another-run"
        )
    reads: list[str] = []
    _wire_readmission_dependencies(manager, parent, child, reads=reads)
    manager._start_intent = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("cross-Run child lookup caused new admission")
    )
    with pytest.raises(JournalConflict):
        manager.readmit("old-run", new_fleet_run_id="fresh-client-command")
    assert reads == ["read"]


def test_child_lookup_rejects_reuse_of_parent_run_identity() -> None:
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    child = _existing_child(parent, fleet_run_id="old-run")
    _wire_readmission_dependencies(manager, parent, child)
    with pytest.raises(JournalConflict, match="captured repository identities"):
        manager.readmit("old-run", new_fleet_run_id="new-run")


def test_child_lookup_rejects_wrong_parent_projection_before_readmission_work() -> None:
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    parent.run.fleet_run_id = "another-run"
    reads: list[str] = []
    _wire_readmission_dependencies(manager, parent, reads=reads)
    manager._start_intent = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("wrong parent lookup must not begin any new work")
    )
    with pytest.raises(JournalConflict, match="enrolled Run identity"):
        manager.readmit("old-run", new_fleet_run_id="new-run")
    assert reads == ["read"]


def test_readmission_rejects_parent_identity_reuse() -> None:
    manager = object.__new__(FleetRunControlPlane)

    with pytest.raises(ExecutorReadmissionNotAllowed, match="own identity"):
        manager.readmit("same-run", new_fleet_run_id="same-run")


def test_readmission_rejects_parent_with_completed_repository_effect() -> None:
    manager = object.__new__(FleetRunControlPlane)
    detail = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    detail.run.progress = SimpleNamespace(completed=1)
    _wire_readmission_dependencies(manager, detail)

    with pytest.raises(ExecutorReadmissionNotAllowed, match="durably completed"):
        manager.readmit("old-run", new_fleet_run_id="new-run")
