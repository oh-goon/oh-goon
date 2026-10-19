from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.api.schemas.fleet import FleetRunRequest
from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import FleetScope, OperationAction, UvRecipeId
from oh_goon.services.fleet_admission import FleetAdmissionPlanner


def _git_root(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def test_explicit_fleet_request_requires_instance_authority_for_every_repository() -> None:
    with pytest.raises(ValidationError, match="repository_instance_ids must exactly match"):
        FleetRunRequest(
            fleet_run_id="fleet-test",
            action=OperationAction.RECIPE,
            target=UvRecipeTarget(kind="recipe", recipe=UvRecipeId.SBOM),
            repository_ids=["repo-a"],
            repository_instance_ids=None,
        )


def test_repository_instance_fence_rejects_delete_recreate_at_same_path(tmp_path: Path) -> None:
    root = _git_root(tmp_path / "team")
    identity = RepositoryIdentity(path=root)
    old_instance = repository_subject_instance_id(root)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)

    (root / ".git").rmdir()
    root.rmdir()
    _git_root(root)

    with pytest.raises(TargetSelectionError, match="Repository instance changed"):
        FleetAdmissionPlanner._validate_repository_instances(
            (entry,),
            {identity.repository_id: old_instance},
        )


def test_non_git_repository_instance_fence_rejects_delete_recreate_at_same_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    identity = RepositoryIdentity(path=root)
    old_instance = repository_subject_instance_id(root)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)

    root.rmdir()
    root.mkdir()

    with pytest.raises(TargetSelectionError, match="Repository instance changed"):
        FleetAdmissionPlanner._validate_repository_instances(
            (entry,),
            {identity.repository_id: old_instance},
        )


def test_fleet_run_request_defaults_priority_and_accepts_canonical_priority() -> None:
    from oh_goon.core.models.scheduling import SchedulingPriority

    normal = FleetRunRequest(fleet_run_id="normal", action=OperationAction.STATUS)
    high = FleetRunRequest(
        fleet_run_id="high",
        action=OperationAction.STATUS,
        requested_priority=SchedulingPriority.HIGH,
    )

    assert normal.requested_priority is SchedulingPriority.NORMAL
    assert high.requested_priority is SchedulingPriority.HIGH
