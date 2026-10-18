"""M213: a history SUCCESS row is not proof of a different/detail-changed Run."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.services import plan_success_evidence


class _PlanTarget:
    def __init__(self, digest: str = "same-plan", source: str = "same-source"):
        self.identity = SimpleNamespace(digest=digest)
        self.source = source


@pytest.mark.parametrize(
    ("detail_run_id", "detail_outcome", "detail_digest", "is_valid"),
    [
        ("enrolled", FleetRunOutcome.SUCCESS, "same-plan", True),
        ("prior", FleetRunOutcome.SUCCESS, "same-plan", False),
        ("enrolled", FleetRunOutcome.FAILED, "same-plan", False),
        ("enrolled", FleetRunOutcome.SUCCESS, "different-plan", False),
    ],
)
def test_deployment_requires_current_detail_to_agree_with_success_history(
    tmp_path: Path, monkeypatch, detail_run_id, detail_outcome, detail_digest, is_valid,
) -> None:
    repository_id = RepositoryIdentity(path=tmp_path).repository_id
    admission = object()
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _PlanTarget)
    monkeypatch.setattr(
        plan_success_evidence, "repository_plan_admission_evidence", lambda *_args: admission,
    )
    history = SimpleNamespace(items=(SimpleNamespace(run=SimpleNamespace(
        fleet_run_id="enrolled", target=_PlanTarget(),
    )),), next_before_revision=None)
    detail = SimpleNamespace(
        run=SimpleNamespace(fleet_run_id=detail_run_id, target=_PlanTarget(digest=detail_digest)),
        outcome=detail_outcome,
        repository_admissions=(admission,),
    )
    lookups = []
    runtime = SimpleNamespace(
        plans=SimpleNamespace(prepare_execution_target=lambda *_a, **_kw: SimpleNamespace(
            identity=SimpleNamespace(digest="same-plan"), source="same-source", plan=object(),
        )),
        runs=SimpleNamespace(
            history=lambda **_kw: history,
            get=lambda run_id: lookups.append(run_id) or detail,
        ),
    )
    result = plan_success_evidence.has_exact_plan_success(
        runtime, plan=Path("app.yml"), repository_id=repository_id,
        source_root=tmp_path, lock_path=tmp_path / "app.goon.lock",
        fleet_run_id="enrolled",
    )
    assert result is is_valid
    assert lookups == ["enrolled"]
