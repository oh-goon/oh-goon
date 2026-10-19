from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, WorkspaceStateStale, WorkspaceTransitionInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import bind_atomic_runtime_authority
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    verify_selected_backend,
)
from oh_goon.integrations.source_snapshot import source_snapshot_identity
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import (
    _TEST_PROCESS_ISOLATION,
    prepare_physical_test_run,
    strict_test_plan,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _manager(tmp_path: Path) -> RepositoryWorkspaceAuthority:
    return RepositoryWorkspaceAuthority(
        LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")
    )


def _allocated_read_snapshot(manager: RepositoryWorkspaceAuthority):
    workspace = manager.request(
        repository_id="repo-read",
        base_revision="base-revision",
        kind=WorkspaceKind.READ_SNAPSHOT,
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    return workspace, allocated


def _source(root: Path) -> Path:
    source = root / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("secret git admin\n", encoding="utf-8")
    (source / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (source / "frontend" / "node_modules" / "pkg" / "index.js").write_text(
        "generated\n", encoding="utf-8"
    )
    return source


def test_read_snapshot_materialization_is_clean_identity_bound_and_durable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    source = _source(tmp_path)

    ready, binding = manager.materialize_read_snapshot(
        workspace,
        source,
        expected_state_version=allocated.state_version,
    )

    assert ready.lifecycle_state is WorkspaceLifecycleState.READY
    assert ready.state_version == allocated.state_version + 1
    assert binding.snapshot_path == manager.physical_path(workspace)
    assert binding.identity == source_snapshot_identity(source)
    assert binding.identity == source_snapshot_identity(binding.snapshot_path)
    assert (binding.snapshot_path / "src" / "app.py").is_file()
    assert not (binding.snapshot_path / ".git").exists()
    assert not (binding.snapshot_path / "frontend" / "node_modules").exists()

    reopened = RepositoryWorkspaceAuthority(
        LocalDatabase(manager._database.path, journal_mode="DELETE")
    )
    rebound = reopened.read_snapshot_binding(workspace)
    assert rebound.identity == binding.identity
    assert rebound.snapshot_path == binding.snapshot_path


def test_read_snapshot_reproof_rejects_source_mutation_and_transient_injection(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    _ready, binding = manager.materialize_read_snapshot(
        workspace,
        _source(tmp_path),
        expected_state_version=allocated.state_version,
    )

    (binding.snapshot_path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="identity is no longer current"):
        manager.read_snapshot_binding(workspace)

    # Restore exact source bytes, then inject an operational path that canonical source
    # identity intentionally excludes. Clean-snapshot verification must still reject it.
    (binding.snapshot_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (binding.snapshot_path / ".git").mkdir()
    (binding.snapshot_path / ".git" / "config").write_text("injected\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="identity is no longer current"):
        manager.read_snapshot_binding(workspace)


def test_read_snapshot_materialization_refuses_worktree_workspace(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo-edit",
        base_revision="base-revision",
        kind=WorkspaceKind.WORKTREE,
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)

    with pytest.raises(WorkspaceTransitionInvalid, match="requires a READ_SNAPSHOT"):
        manager.materialize_read_snapshot(
            workspace,
            _source(tmp_path),
            expected_state_version=allocated.state_version,
        )


def test_bubblewrap_physically_ro_binds_managed_read_snapshot_as_workspace(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    _ready, read_binding = manager.materialize_read_snapshot(
        workspace,
        _source(tmp_path),
        expected_state_version=allocated.state_version,
    )

    repository = tmp_path / "fleet-repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=30, step_id="read", argv=("true",))
    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true", str(read_binding.snapshot_path / "src" / "app.py")),
            cwd=read_binding.snapshot_path,
            env={"SOURCE_FILE": str(read_binding.snapshot_path / "src" / "app.py")},
            read_only_workspace=read_binding,
        )
        launch = sandbox_process_launch(request)
        with bind_atomic_runtime_authority(request, launch) as atomic:
            argv, env = bubblewrap._command(
                request, launch=launch, atomic_authority=atomic, network_prefix=()
            )

    pairs = list(zip(argv, argv[1:], strict=False))
    assert (str(read_binding.snapshot_path), "/workspace") in pairs
    index = argv.index(str(read_binding.snapshot_path))
    assert argv[index - 1] == "--ro-bind"
    assert (str(attempt.paths.workspace), "/workspace") not in pairs
    separator = argv.index("--")
    assert argv[separator + 1 :] == ("/toolchain/bin/true", "/workspace/src/app.py")
    assert env["SOURCE_FILE"] == "/workspace/src/app.py"
    assert (
        str(source_snapshot_identity(read_binding.snapshot_path).sha256)
        == read_binding.identity.sha256
    )


def test_read_snapshot_execution_fails_closed_without_readonly_workspace_capability(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    _ready, read_binding = manager.materialize_read_snapshot(
        workspace,
        _source(tmp_path),
        expected_state_version=allocated.state_version,
    )
    repository = tmp_path / "fleet-repo-no-ro"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=30, step_id="read", argv=("true",))
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=read_binding.snapshot_path,
            env={},
            read_only_workspace=read_binding,
        )
        with pytest.raises(FleetPlanInvalid, match="physically enforces"):
            verify_selected_backend(_TEST_PROCESS_ISOLATION, request)


def test_read_snapshot_cannot_be_marked_ready_without_materialization(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)

    with pytest.raises(WorkspaceTransitionInvalid, match="materialize_read_snapshot"):
        manager.transition(
            workspace.workspace_id,
            expected_state_version=allocated.state_version,
            next_state=WorkspaceLifecycleState.READY,
        )


def test_read_snapshot_execution_cwd_cannot_escape_snapshot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    _ready, read_binding = manager.materialize_read_snapshot(
        workspace,
        _source(tmp_path),
        expected_state_version=allocated.state_version,
    )
    repository = tmp_path / "fleet-repo-escape"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=30, step_id="read", argv=("true",))
    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=tmp_path,
            env={},
            read_only_workspace=read_binding,
        )
        with pytest.raises(FleetPlanInvalid, match="outside repository boundary"):
            launch = sandbox_process_launch(request)
            with bind_atomic_runtime_authority(request, launch) as atomic:
                bubblewrap._command(
                    request, launch=launch, atomic_authority=atomic, network_prefix=()
                )


def test_only_bubblewrap_currently_claims_physical_readonly_workspace_support() -> None:
    from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
        OpenSandboxProcessIsolation,
        OpenSandboxRuntimeKind,
        OpenSandboxSettings,
    )

    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")
    assert bubblewrap.capabilities.private_filesystem is True
    assert bubblewrap.capabilities.read_only_workspace is True

    settings = OpenSandboxSettings(
        domain="sandbox.internal",
        api_key="secret",
        image="example/image@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        cli_executable=sys.executable,
        runtime_attestation_file=str(
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "opensandbox"
            / "kata-runtime-attestation.json"
        ),
        runtime_attestation_public_key_file=str(
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "opensandbox"
            / "runtime-attestation-ed25519.pub.pem"
        ),
        runtime=OpenSandboxRuntimeKind.KATA,
        protocol="https",
    )
    remote = OpenSandboxProcessIsolation(settings, transport=object())
    assert remote.capabilities.private_filesystem is True
    assert remote.capabilities.read_only_workspace is False


def test_read_snapshot_table_is_evidence_not_parallel_path_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace, allocated = _allocated_read_snapshot(manager)
    manager.materialize_read_snapshot(
        workspace,
        _source(tmp_path),
        expected_state_version=allocated.state_version,
    )
    with manager._database.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM workspace_read_snapshots WHERE workspace_id=?",
            (workspace.workspace_id,),
        ).fetchone()
        columns = {
            item["name"]
            for item in connection.execute("PRAGMA table_info(workspace_read_snapshots)").fetchall()
        }
    assert row is not None
    assert columns == {"workspace_id", "sha256", "byte_count", "entry_count", "materialized_at"}
    assert "path" not in columns
    assert "workspace_path" not in columns
