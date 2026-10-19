"""WF-03C2B: transport only the original journal's admitted Plan authority.

No test in this module provisions native effect rights or simulates TL-1 admission.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import FleetScope, OperationAction
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_lifecycle import FleetExecutionAuthority, _execution_authority_in
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from tests.factories.capabilities import capability_request
from tests.unit.runtime.test_lace_wf03c_durable_consent import RUN, _admission, _admitted


def admitted_detail(tmp_path: Path) -> FleetRunDetail:
    return project_fleet_run_detail((_admitted(tmp_path),), lambda _operation_id: None)


class _Session:
    def execution_connection_scope(self):
        from contextlib import nullcontext

        return nullcontext()


def test_original_lifecycle_authority_carries_same_sqlite_run_detail(tmp_path: Path) -> None:
    repository, _target, intent = _admission(tmp_path)
    database = LocalDatabase(tmp_path / "original.db")
    journal = Journal(database)
    with database.transaction() as connection:
        journal.admit_run_in(
            connection,
            RUN,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
    # Executor inventory remains the original placement-validation owner.
    # This test does not fake a capacity PASS; it only observes which binding
    # the existing lifecycle forwards into its original inventory port.
    observed = []

    class Pool:
        def require_bound_in(self, connection, placement, plan):
            observed.append((placement, plan))

    with database.admitted_observer_connection() as connection:
        original = _execution_authority_in(connection, journal, Pool(), RUN)
        durable = journal.read_view_from_connection(connection).required_run_detail(RUN)
    assert original.admitted_run == durable
    assert original.execution == durable.execution
    assert observed == [(durable.execution.placement, durable.run.target.plan)]


def test_scheduler_forwards_one_original_detail_and_single_repository(tmp_path: Path) -> None:
    detail = admitted_detail(tmp_path)
    seen = []

    class Effects:
        def execute_fleet_target(
            self,
            _intent,
            _capability,
            entry,
            _session,
            _isolation_backend=None,
            *,
            repository_admission=None,
            admitted_run=None,
        ):
            seen.append((entry.repository.repository_id, admitted_run, repository_admission))

    FleetTargetScheduler(Effects()).execute(
        SimpleNamespace(),
        SimpleNamespace(),
        [
            IncludedRepositoryCatalogEntry(
                scope=FleetScope.INCLUDED, repository=detail.run.targets[0]
            )
        ],
        _Session(),
        parallel_safe=False,
        isolation_backend=detail.execution.placement.isolation_backend,
        repository_admissions=detail.repository_admissions,
        admitted_run=detail,
    )
    assert seen == [
        (
            detail.run.targets[0].repository_id,
            detail,
            detail.repository_admissions[0],
        )
    ]
    assert seen[0][1] is detail  # no independently reconstructed source/consent


def test_scheduler_does_not_invent_admitted_run_when_absent(tmp_path: Path) -> None:
    keys = []

    class Effects:
        def execute_fleet_target(self, *_args, **kwargs):
            keys.extend(kwargs.keys())

    from oh_goon.core.models.repository import RepositoryIdentity

    FleetTargetScheduler(Effects()).execute(
        SimpleNamespace(),
        SimpleNamespace(),
        [
            IncludedRepositoryCatalogEntry(
                scope=FleetScope.INCLUDED,
                repository=RepositoryIdentity(name="direct", path=tmp_path / "repo"),
            )
        ],
        _Session(),
        parallel_safe=False,
        repository_admissions=(),
    )
    assert "admitted_run" not in keys


def test_execution_request_requires_exact_original_run_and_target(tmp_path: Path) -> None:
    detail = admitted_detail(tmp_path)
    repository = detail.run.targets[0]
    repository.path.mkdir()
    original = capability_request(
        repository.path,
        OperationAction.EXECUTE_PLAN,
        detail.run.target,
        fleet_run_id=RUN,
    )
    bound = replace(original, admitted_run=detail)
    assert bound.admitted_run is detail

    wrong_run = capability_request(
        repository.path,
        OperationAction.EXECUTE_PLAN,
        detail.run.target,
        fleet_run_id="other-run",
    )
    with pytest.raises(ValueError, match="Run identity"):
        replace(wrong_run, admitted_run=detail)

    alternative = detail.run.target.model_copy(update={"identity": object()})
    # A stale target is impossible to construct through normal Plan validation;
    # inject one at the claimed-effect boundary to prove the transport guard.
    wrong_target = replace(
        original, attempt=original.attempt.model_copy(update={"target": alternative})
    )
    with pytest.raises(ValueError, match="Plan target"):
        replace(wrong_target, admitted_run=detail)


def test_run_detail_is_not_a_mutable_lease_equality_dimension(tmp_path: Path) -> None:
    detail = admitted_detail(tmp_path)
    assert isinstance(detail.execution, StrictFleetExecutionAdmission)
    owner = FleetExecutionAuthority(
        capability=detail.run.capability,
        target=detail.run.target,
        repository_scope=detail.run.repository_scope,
        targets=detail.run.targets,
        execution=detail.execution,
        repository_admissions=detail.repository_admissions,
        admitted_run=detail,
    )
    # Progress changes on RUN_STARTED. It must not accidentally revoke the
    # original immutable execution admission when the worker rechecks authority.
    assert owner == replace(owner, admitted_run=None)
    assert owner.admitted_run is detail


def test_original_preparation_selector_receives_admitted_run_without_native_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = admitted_detail(tmp_path)
    admitted: PlanIsolationBackendEvidence = detail.execution.placement.isolation_backend
    marker = RuntimeError("selection-cut-observed")
    seen = []

    def selection(plan, selected, backends, *, admitted_run=None):
        seen.append((plan, selected, backends, admitted_run))
        raise marker

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_run_execution.select_bound_process_isolation_backend",
        selection,
    )
    worker = FleetPlanRunExecutor(
        None,
        process_isolation_backends=(),
        path_authority=None,
    )
    # The strict preparation path requires original path authority prior to
    # selection. A non-None sentinel is sufficient: selection aborts first.
    worker._path_authority = object()
    with pytest.raises(RuntimeError, match="selection-cut-observed"):
        worker.prepare_strict_run(
            detail.run.targets[0],
            detail.run.target.plan,
            cancel_requested=None,
            admitted_source_authority=detail.repository_admissions[0].source_authority,
            admitted_isolation_backend=admitted,
            admitted_run=detail,
        )
    assert seen == [(detail.run.target.plan, admitted, (), detail)]
