from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import (
    PlanIsolationBackendEvidence,
    PlanSnapshotEvidence,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.path_layouts import RunPathLayout
from oh_goon.integrations._fleet_plan_run_resources import _FleetPlanRunInputs
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.fleet_plan_repository_scope import RepositoryScopeBinding
from tests.factories.fleet_plan_executor import strict_test_plan
from tests.factories.path_authority import TEST_PATH_AUTHORITY


class _CapturedRunRoot(RuntimeError):
    pass


def test_uv_preparation_consumes_run_root_from_canonical_run_layout_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "owned.txt").write_text("source\n", encoding="utf-8")
    run_root = tmp_path / "run"
    run_root.mkdir()
    identity = SnapshotIdentity(sha256="a" * 64, byte_count=7, entry_count=1)
    source_authority = RepositorySourceAuthority.create(
        subject_instance_id="repo-subject-instance-" + "b" * 64,
        source_snapshot=PlanSnapshotEvidence(
            sha256=identity.sha256,
            byte_count=identity.byte_count,
            entry_count=identity.entry_count,
        ),
    )
    plan = strict_test_plan(ResolvedCommandStep(timeout_seconds=60, step_id="noop", argv=("true",)))
    observed: dict[str, Path] = {}

    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_run_resources.snapshot_source_directory",
        lambda *args, **kwargs: identity,
    )
    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_run_resources.bind_repository_scope",
        lambda *args, **kwargs: RepositoryScopeBinding(
            admission=None,
            import_applicability=(),
            import_skips=MappingProxyType({}),
            active_step_ids=("noop",),
        ),
    )

    def capture_run_root(*args, run_root: Path, **kwargs):
        del args, kwargs
        observed["run_root"] = run_root
        raise _CapturedRunRoot

    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_run_resources.prepare_uv_project_runtimes",
        capture_run_root,
    )

    with pytest.raises(_CapturedRunRoot):
        _FleetPlanRunInputs(
            RepositoryIdentity(path=repository_path),
            run_root,
            plan,
            source_authority=source_authority,
            isolation_backend=PlanIsolationBackendEvidence(
                backend_id="test-isolation",
                configuration_id="test-run-root-authority",
                kernel_boundary="shared_host_kernel",
                remote_execution=False,
                experimental=False,
            ),
            path_authority=TEST_PATH_AUTHORITY,
            managed_runtimes=None,
            cancel_requested=None,
        )

    assert observed["run_root"] == RunPathLayout.from_root(run_root).root
