from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import RecipeOperationEvidence
from oh_goon.core.models.recipe import UvRecipeResult
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    FleetRunProgress,
    QueuedFleetRun,
)
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    CommandSafety,
    FleetRunStatus,
    OperationAction,
    UvRecipeId,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report
from tests.factories.schedules import schedule_definition


def test_durable_fleet_and_schedule_values_are_deeply_tuple_backed_and_frozen(
    tmp_path: Path,
) -> None:
    repository = RepositoryIdentity(name="api", path=tmp_path / "api")
    run = QueuedFleetRun(
        status=FleetRunStatus.QUEUED,
        fleet_run_id="fleet-immutable",
        capability=capability_snapshot(OperationAction.INIT),
        repository_scope=fleet_repository_scope(None),
        targets=[repository],
        accepted_at=utc_now(),
        progress=FleetRunProgress(total=1, succeeded=0, failed=0),
    )
    assert run.targets == (repository,)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        run.progress = FleetRunProgress(total=1, succeeded=1, failed=0)
    with pytest.raises(AttributeError):
        run.targets.append(repository)  # type: ignore[attr-defined]

    definition = schedule_definition(now=utc_now(), name="Immutable")
    assert isinstance(definition.target.plan.steps, tuple)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        definition.name = "mutated"
    with pytest.raises(AttributeError):
        definition.target.plan.steps.append(definition.target.plan.steps[0])  # type: ignore[attr-defined]


def test_canonical_operation_structure_is_frozen(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.INIT, fleet_run_id=None)

    with pytest.raises(ValidationError, match="Instance is frozen"):
        report.outcome.returncode = 99
    assert isinstance(report.evidence.readiness.position.branch, str)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        report.evidence.readiness.position.branch = "other"


def test_verified_journal_index_maps_are_read_only(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.INIT, fleet_run_id=None)
    journal = Journal(LocalDatabase(tmp_path / "journal.db"))
    commit_operation(journal, report)

    view = journal.read_view()
    with pytest.raises(TypeError):
        view._index.operations_by_id[report.provenance.operation_id] = report  # type: ignore[index]


def test_opaque_recipe_json_remains_isolated_by_journal_copy_boundary(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.SBOM)
    evidence = RecipeOperationEvidence(
        kind="RECIPE",
        recipe=UvRecipeResult(
            recipe=UvRecipeId.SBOM,
            safety=CommandSafety.READ_ONLY,
            argv=("uv", "export"),
            exit_code=0,
            structured_output={"components": [{"name": "demo"}]},
        ),
    )
    report = make_operation_report(
        repository,
        OperationAction.RECIPE,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id=None,
        target=target,
        evidence=evidence,
    )
    journal = Journal(LocalDatabase(tmp_path / "opaque-json.db"))
    commit_operation(journal, report)

    first = journal.require_operation(report.provenance.operation_id)
    payload = first.evidence.recipe.structured_output  # type: ignore[union-attr]
    assert isinstance(payload, dict)
    payload["components"][0]["name"] = "mutated"  # type: ignore[index]

    second = journal.require_operation(report.provenance.operation_id)
    persisted = second.evidence.recipe.structured_output  # type: ignore[union-attr]
    assert isinstance(persisted, dict)
    assert persisted["components"][0]["name"] == "demo"  # type: ignore[index]
