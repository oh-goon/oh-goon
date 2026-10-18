from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.services import plan_success_evidence


class _Runs:
    def __init__(self, pages, details):
        self._pages = iter(pages)
        self._details = details

    def history(self, **_kwargs):
        return next(self._pages)

    def get(self, run_id):
        return self._details[run_id]


class _Target:
    def __init__(self, *, purpose, digest="plan-digest", source="plan-source"):
        self.identity = SimpleNamespace(digest=digest)
        self.source = source
        self.plan = SimpleNamespace(execution=SimpleNamespace(purpose=purpose))


def _target(*, purpose, digest="plan-digest", source="plan-source"):
    return _Target(purpose=purpose, digest=digest, source=source)


def test_exact_plan_success_rejects_when_history_has_no_match(tmp_path: Path, monkeypatch) -> None:
    expected = _target(purpose=ExecutionPurpose.CICD)
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _Target)
    repository_id = RepositoryIdentity(path=tmp_path).repository_id
    current_admission = object()
    monkeypatch.setattr(
        plan_success_evidence,
        "repository_plan_admission_evidence",
        lambda repository, _plan: (
            current_admission if repository.repository_id == repository_id else None
        ),
    )
    calls = []
    runtime = SimpleNamespace(
        plans=SimpleNamespace(
            prepare_execution_target=lambda _plan, *, lock_path: calls.append(lock_path) or expected
        ),
        runs=_Runs([SimpleNamespace(items=(), next_before_revision=None)], {}),
    )
    lock_path = tmp_path / "app.goon.lock"
    assert (
        plan_success_evidence.has_exact_plan_success(
            runtime,
            plan=Path("plan.yml"),
            repository_id=repository_id,
            source_root=tmp_path,
            lock_path=lock_path,
        )
        is False
    )
    assert calls == [lock_path]


@pytest.mark.parametrize("purpose", (ExecutionPurpose.VERIFICATION, ExecutionPurpose.CICD))
def test_exact_plan_success_matches_same_repository_admission_for_every_execution_mode(
    tmp_path: Path, monkeypatch, purpose: ExecutionPurpose
) -> None:
    expected = _target(purpose=purpose)
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _Target)
    repository_id = RepositoryIdentity(path=tmp_path).repository_id
    current_admission = object()
    monkeypatch.setattr(
        plan_success_evidence,
        "repository_plan_admission_evidence",
        lambda repository, _plan: (
            current_admission if repository.repository_id == repository_id else None
        ),
    )
    run_item = SimpleNamespace(run=SimpleNamespace(target=expected, fleet_run_id="run-1"))
    detail = SimpleNamespace(
        repository_admissions=(current_admission,),
        operations=(SimpleNamespace(should_not_be_inspected=True),),
    )
    runtime = SimpleNamespace(
        plans=SimpleNamespace(prepare_execution_target=lambda _plan, *, lock_path: expected),
        runs=_Runs(
            [SimpleNamespace(items=(run_item,), next_before_revision=None)], {"run-1": detail}
        ),
    )
    assert (
        plan_success_evidence.has_exact_plan_success(
            runtime,
            plan=Path("plan.yml"),
            repository_id=repository_id,
            source_root=tmp_path,
            lock_path=tmp_path / "app.goon.lock",
        )
        is True
    )


def test_exact_plan_success_rejects_stale_repository_admission(tmp_path: Path, monkeypatch) -> None:
    expected = _target(purpose=ExecutionPurpose.VERIFICATION)
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _Target)
    repository_id = RepositoryIdentity(path=tmp_path).repository_id
    current_admission = object()
    stale_admission = object()
    monkeypatch.setattr(
        plan_success_evidence,
        "repository_plan_admission_evidence",
        lambda repository, _plan: (
            current_admission if repository.repository_id == repository_id else None
        ),
    )
    run_item = SimpleNamespace(run=SimpleNamespace(target=expected, fleet_run_id="run-1"))
    detail = SimpleNamespace(repository_admissions=(stale_admission,), operations=())
    runtime = SimpleNamespace(
        plans=SimpleNamespace(prepare_execution_target=lambda _plan, *, lock_path: expected),
        runs=_Runs(
            [SimpleNamespace(items=(run_item,), next_before_revision=None)], {"run-1": detail}
        ),
    )
    assert (
        plan_success_evidence.has_exact_plan_success(
            runtime,
            plan=Path("plan.yml"),
            repository_id=repository_id,
            source_root=tmp_path,
            lock_path=tmp_path / "app.goon.lock",
        )
        is False
    )
