from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, TypedDict, cast

from oh_goon.core.errors import (
    CandidateCheckpointNotFound,
    CandidateCheckpointStale,
    FleetPlanInvalid,
    WorkspaceNotFound,
    WorkspaceStateStale,
    WorkspaceTransitionInvalid,
)
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef, WorkspaceState
from oh_goon.core.path_authority import canonical_path, ensure_private_directory, storage_component
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import new_checkpoint_id, new_workspace_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations._copy_transport import copy_regular_file
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.source_snapshot import (
    SourceSnapshotBinding,
    snapshot_source_directory,
    source_snapshot_identity,
    verify_clean_source_snapshot,
)
from oh_goon.runtime.database import LocalDatabase


def _worktree_git_environment() -> dict[str, str]:
    """Return the minimal Git environment while preserving admitted executable lookup.

    Worktree metadata commands intentionally avoid host/system Git configuration, but
    they still execute inside the current admitted process environment. In strict APP
    certification that environment exposes frozen tools only through PATH=/toolchain.
    Passing a partial env without PATH made subprocess fall back to os.defpath and lose
    the frozen Git capability even though startup/admission had proved it.
    """

    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


_ALLOWED_TRANSITIONS: dict[WorkspaceLifecycleState, frozenset[WorkspaceLifecycleState]] = {
    WorkspaceLifecycleState.REQUESTED: frozenset(
        {
            WorkspaceLifecycleState.ALLOCATED,
            WorkspaceLifecycleState.FAILED,
            WorkspaceLifecycleState.RELEASED,
        }
    ),
    WorkspaceLifecycleState.ALLOCATED: frozenset(
        {
            WorkspaceLifecycleState.READY,
            WorkspaceLifecycleState.FAILED,
            WorkspaceLifecycleState.RELEASED,
        }
    ),
    WorkspaceLifecycleState.READY: frozenset(
        {
            WorkspaceLifecycleState.IN_USE,
            WorkspaceLifecycleState.FINALIZED,
            WorkspaceLifecycleState.FAILED,
            WorkspaceLifecycleState.RELEASED,
        }
    ),
    WorkspaceLifecycleState.IN_USE: frozenset(
        {
            WorkspaceLifecycleState.READY,
            WorkspaceLifecycleState.FINALIZED,
            WorkspaceLifecycleState.FAILED,
        }
    ),
    WorkspaceLifecycleState.FINALIZED: frozenset({WorkspaceLifecycleState.RELEASED}),
    WorkspaceLifecycleState.FAILED: frozenset({WorkspaceLifecycleState.RELEASED}),
    WorkspaceLifecycleState.RELEASED: frozenset(),
}


class _WorktreeReceipt(TypedDict):
    owner: str
    kind: str
    workspace_id: str
    base_revision: str | None
    state_version: int
    source_repository: str
    source_device: int
    source_inode: int
    sha256: str
    byte_count: int
    entry_count: int


class _RetirementReceipt(TypedDict):
    owner: str
    kind: str
    workspace_id: str
    state_version: int
    base_revision: str | None
    common_path: str
    common_device: int
    common_inode: int
    destination_device: int
    destination_inode: int
    git_marker_sha256: str
    checkpoint_sha256: str
    checkpoint_byte_count: int
    checkpoint_entry_count: int


class RepositoryWorkspaceAuthority:
    """Single durable owner of workspace identity, lifecycle state, and host path layout.

    This is deliberately separate from ``RunPathLayout`` / ``AttemptPathLayout``: those
    types own disposable execution scratch.  This owner names a durable candidate/source
    workspace that later execution attempts may receive through explicit assignment.
    """

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database
        layout = ApplicationStatePathLayout.from_database(database.path)
        self._root = ensure_private_directory(layout.managed_workspaces)

    @staticmethod
    @contextmanager
    def pin_repository_subject(repository: Path) -> Iterator[Callable[[], None]]:
        """Hold native root/Git handles across authorized edits without freezing directory ctime.

        Directory entries may change during an apply. Open handles prevent inode reuse
        from impersonating the original subject; per-Run admission still captures its
        fresh ordinary repository-instance authority.
        """
        root = canonical_path(repository)
        with ExitStack() as owned:
            pinned = []
            absent = []
            for path in (root, root / ".git"):
                try:
                    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                except FileNotFoundError:
                    if path == root:
                        raise
                    absent.append(path)
                    continue
                owned.callback(os.close, fd)
                initial = os.fstat(fd)
                pinned.append((path, fd, initial))

            def require_current() -> None:
                for path, fd, initial in pinned:
                    current = path.lstat()
                    held = os.fstat(fd)
                    if (
                        (current.st_dev, current.st_ino, current.st_mode)
                        != (held.st_dev, held.st_ino, held.st_mode)
                        or current.st_mode != initial.st_mode
                        or (
                            not stat.S_ISDIR(initial.st_mode)
                            and current.st_ctime_ns != initial.st_ctime_ns
                        )
                    ):
                        raise FleetPlanInvalid("repair repository or Git authority was replaced")
                if any(path.exists() or path.is_symlink() for path in absent):
                    raise FleetPlanInvalid("repair repository acquired a different Git authority")

            require_current()
            yield require_current

    def request(
        self,
        *,
        repository_id: str,
        base_revision: str,
        kind: WorkspaceKind,
        workspace_id: str | None = None,
    ) -> WorkspaceRef:
        """Create durable workspace identity/state without materializing repository bytes."""

        reference = WorkspaceRef(
            workspace_id=workspace_id or new_workspace_id(),
            repository_id=repository_id,
            base_revision=base_revision,
            kind=kind,
        )
        now = utc_now().isoformat()
        try:
            with self._database.transaction(write=True) as connection:
                connection.execute(
                    """
                    INSERT INTO workspaces(
                        workspace_id, repository_id, base_revision, kind, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        reference.workspace_id,
                        reference.repository_id,
                        reference.base_revision,
                        reference.kind.value,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workspace_state(
                        workspace_id, lifecycle_state, state_version,
                        current_lease_id, current_checkpoint_id, updated_at
                    ) VALUES (?, 'REQUESTED', 0, NULL, NULL, ?)
                    """,
                    (reference.workspace_id, now),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkspaceStateStale(
                f"Workspace identity is already allocated: {reference.workspace_id}"
            ) from exc
        return reference

    def reference(self, workspace_id: str) -> WorkspaceRef:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT workspace_id, repository_id, base_revision, kind "
                "FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"Workspace not found: {workspace_id}")
        return _workspace_ref(row)

    def state(self, workspace_id: str) -> WorkspaceState:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_state WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"Workspace state not found: {workspace_id}")
        return _workspace_state(row)

    def physical_path(self, workspace: WorkspaceRef) -> Path:
        """Resolve the one canonical host path; callers must not persist it as identity."""

        # Bind the supplied reference to durable identity so a forged WorkspaceRef cannot
        # redirect execution to another repository/base while reusing a workspace_id.
        durable = self.reference(workspace.workspace_id)
        if durable != workspace:
            raise WorkspaceStateStale(
                f"Workspace reference does not match durable identity: {workspace.workspace_id}"
            )
        return self._physical_path_for_id(workspace.workspace_id)

    def allocate(self, workspace_id: str, *, expected_state_version: int) -> WorkspaceState:
        """Create with a durable, workspace-scoped filesystem preparation receipt.

        An interruption after mkdir but before SQLite COMMIT leaves a named
        prepare marker. Only this same owner may reconcile it. A foreign
        preexisting path without that receipt is never adopted or removed.
        """

        marker = self._allocation_marker_for_id(workspace_id)
        prepared = False
        try:
            with self._database.transaction(write=True) as connection:
                current = self._state_for_update(connection, workspace_id)
                self._require_version(current, expected_state_version)
                self._require_transition(current.lifecycle_state, WorkspaceLifecycleState.ALLOCATED)
                path = self._physical_path_for_id(workspace_id)
                if path.exists() or path.is_symlink():
                    raise WorkspaceStateStale(
                        f"Workspace physical path already exists before allocation: {workspace_id}"
                    )
                self._prepare_allocation_marker(marker, current)
                prepared = True
                path.mkdir(mode=0o700)
                path.chmod(0o700)
                self._fsync_managed_root()
                allocated = self._write_state(
                    connection,
                    current=current,
                    next_state=WorkspaceLifecycleState.ALLOCATED,
                )
        except BaseException:
            if prepared:
                # Reconcile after an ambiguous COMMIT against the durable row;
                # never assume rollback merely because the caller saw failure.
                try:
                    self.reconcile_allocation(workspace_id)
                except BaseException as exc:
                    raise WorkspaceStateStale(
                        f"Workspace allocation outcome unresolved: {workspace_id}"
                    ) from exc
            raise
        self.reconcile_allocation(workspace_id)
        return allocated

    def reconcile_allocation(self, workspace_id: str) -> WorkspaceState:
        """Reconcile a prepared allocation, never adopt an unreceipted directory."""

        with self._database.transaction(write=True) as connection:
            current = self._state_for_update(connection, workspace_id)
            marker = self._allocation_marker_for_id(workspace_id)
            if not marker.exists() and not marker.is_symlink():
                return current
            self._verify_allocation_marker(marker, current)
            path = self._physical_path_for_id(workspace_id)
            if current.lifecycle_state is WorkspaceLifecycleState.REQUESTED:
                if path.exists():
                    if not path.is_dir() or path.is_symlink() or any(path.iterdir()):
                        raise WorkspaceStateStale(
                            f"Unresolved nonempty allocation cannot be destroyed: {workspace_id}"
                        )
                    path.rmdir()
                self._fsync_managed_root()
            elif current.lifecycle_state is WorkspaceLifecycleState.ALLOCATED:
                if not path.is_dir() or path.is_symlink():
                    raise WorkspaceStateStale(
                        f"Committed allocation has missing/replaced directory: {workspace_id}"
                    )
            else:
                raise WorkspaceStateStale(
                    f"Allocation receipt conflicts with durable state: {workspace_id}"
                )
            marker.unlink()
            self._fsync_managed_root()
            return current

    def _allocation_marker_for_id(self, workspace_id: str) -> Path:
        path = self._physical_path_for_id(workspace_id)
        return path.with_name(f".allocate-{path.name}")

    def _prepare_allocation_marker(self, marker: Path, current: WorkspaceState) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        payload = json.dumps(
            {
                "owner": "RepositoryWorkspaceAuthority",
                "workspace_id": current.workspace_id,
                "expected_state_version": current.state_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(marker, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_managed_root()

    @staticmethod
    def _verify_allocation_marker(marker: Path, current: WorkspaceState) -> None:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspaceStateStale("Allocation receipt was replaced")
        expected = {
            "owner": "RepositoryWorkspaceAuthority",
            "workspace_id": current.workspace_id,
            "expected_state_version": 0,
        }
        # REQUESTED has version 0; ALLOCATED is the only valid committed successor.
        if current.lifecycle_state is WorkspaceLifecycleState.ALLOCATED:
            expected["expected_state_version"] = current.state_version - 1
        if info.st_size > 1024 or json.loads(marker.read_text(encoding="utf-8")) != expected:
            raise WorkspaceStateStale("Allocation receipt identity or generation changed")

    def _fsync_managed_root(self) -> None:
        descriptor = os.open(
            self._root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def materialize_read_snapshot(
        self,
        workspace: WorkspaceRef,
        source: Path,
        *,
        expected_state_version: int,
    ) -> tuple[WorkspaceState, SourceSnapshotBinding]:
        """Publish one clean source-only READ_SNAPSHOT and advance ALLOCATED -> READY.

        Source copying happens in caller-private staging without holding a SQLite write
        transaction. Publication is fenced by the same durable state-version CAS that owns
        workspace lifecycle, so concurrent materializers cannot overwrite each other.
        """

        durable = self.reference(workspace.workspace_id)
        if durable != workspace:
            raise WorkspaceStateStale(
                f"Workspace reference does not match durable identity: {workspace.workspace_id}"
            )
        if workspace.kind is not WorkspaceKind.READ_SNAPSHOT:
            raise WorkspaceTransitionInvalid(
                "READ_SNAPSHOT materialization requires a READ_SNAPSHOT workspace"
            )
        current = self.state(workspace.workspace_id)
        self._require_version(current, expected_state_version)
        if current.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
            raise WorkspaceTransitionInvalid(
                "READ_SNAPSHOT materialization requires ALLOCATED workspace state"
            )

        destination = self.physical_path(workspace)
        if not destination.is_dir() or destination.is_symlink() or any(destination.iterdir()):
            raise WorkspaceStateStale(
                f"READ_SNAPSHOT workspace container is not an empty owned directory: {workspace.workspace_id}"
            )

        staging_parent = Path(tempfile.mkdtemp(prefix=".read-snapshot-", dir=self._root))
        staging = staging_parent / "tree"
        published = False
        try:
            identity = snapshot_source_directory(
                source,
                staging,
                label=f"READ_SNAPSHOT {workspace.workspace_id}",
            )
            binding = SourceSnapshotBinding(snapshot_path=staging, identity=identity)
            verify_clean_source_snapshot(binding, label="staged READ_SNAPSHOT")

            with self._database.transaction(write=True) as connection:
                fresh = self._state_for_update(connection, workspace.workspace_id)
                self._require_version(fresh, expected_state_version)
                if fresh.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
                    raise WorkspaceTransitionInvalid(
                        "READ_SNAPSHOT materialization requires ALLOCATED workspace state"
                    )
                if (
                    not destination.is_dir()
                    or destination.is_symlink()
                    or any(destination.iterdir())
                ):
                    raise WorkspaceStateStale(
                        f"READ_SNAPSHOT workspace container changed before publication: {workspace.workspace_id}"
                    )
                # Persist the exact owner/revision/source identity *before* the
                # filesystem rename. A crash between rename and SQLite commit
                # can then be independently reconciled without inventing READY.
                self._prepare_read_publication_marker(workspace, fresh, identity)
                destination.rmdir()
                staging.replace(destination)
                published = True
                self._fsync_managed_root()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO workspace_read_snapshots(
                        workspace_id, sha256, byte_count, entry_count, materialized_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        workspace.workspace_id,
                        identity.sha256,
                        identity.byte_count,
                        identity.entry_count,
                        now.isoformat(),
                    ),
                )
                ready = self._write_state(
                    connection,
                    current=fresh,
                    next_state=WorkspaceLifecycleState.READY,
                )
            final = SourceSnapshotBinding(snapshot_path=destination, identity=identity)
            verify_clean_source_snapshot(final, label="published READ_SNAPSHOT")
            self._read_publication_marker_for_id(workspace.workspace_id).unlink()
            self._fsync_managed_root()
            return ready, final
        except BaseException:
            if published:
                # A SQLite COMMIT may have succeeded even when the caller observed an
                # exception (or the post-commit snapshot reproof may have failed).
                # Never delete already-committed READY bytes to compensate for a
                # failure *after* the SQLite authority cut. An uncommitted physical
                # publication is left visible for owner reconciliation, rather than
                # guessing that it is safe to destroy after a commit-unknown cut.
                # Keep both publication and its preparation receipt. The
                # restart actor must decide against durable state and full
                # source identity; a cleanup here could remove committed bytes.
                self.state(workspace.workspace_id)
            raise
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    def _read_publication_marker_for_id(self, workspace_id: str) -> Path:
        path = self._physical_path_for_id(workspace_id)
        return path.with_name(f".read-publish-{path.name}")

    def _prepare_read_publication_marker(
        self, workspace: WorkspaceRef, state: WorkspaceState, identity: SnapshotIdentity
    ) -> None:
        marker = self._read_publication_marker_for_id(workspace.workspace_id)
        payload = {
            "owner": "RepositoryWorkspaceAuthority",
            "kind": "READ_SNAPSHOT",
            "workspace_id": workspace.workspace_id,
            "state_version": state.state_version,
            "sha256": identity.sha256,
            "byte_count": identity.byte_count,
            "entry_count": identity.entry_count,
        }
        descriptor = os.open(
            marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            os.write(
                descriptor, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_managed_root()

    def reconcile_read_snapshot(self, workspace: WorkspaceRef) -> WorkspaceState:
        """Finish or reject one interrupted original read-snapshot publication.

        Replays only a fully re-proven owned snapshot, using the original
        ALLOCATED -> READY CAS and original metadata table. Unknown/modified
        bytes are retained, not silently destroyed or admitted.
        """

        if workspace.kind is not WorkspaceKind.READ_SNAPSHOT:
            raise WorkspaceTransitionInvalid(
                "Read publication reconciliation requires READ_SNAPSHOT"
            )
        if self.reference(workspace.workspace_id) != workspace:
            raise WorkspaceStateStale(
                "Publication workspace reference does not match durable identity"
            )
        marker = self._read_publication_marker_for_id(workspace.workspace_id)
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace.workspace_id)
            if not marker.exists() and not marker.is_symlink():
                return state
            info = marker.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2048:
                raise WorkspaceStateStale("Read publication receipt is invalid or replaced")
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if (
                payload.get("owner") != "RepositoryWorkspaceAuthority"
                or payload.get("kind") != "READ_SNAPSHOT"
                or payload.get("workspace_id") != workspace.workspace_id
                or payload.get("state_version")
                not in {state.state_version, state.state_version - 1}
            ):
                raise WorkspaceStateStale(
                    "Read publication receipt generation or identity mismatch"
                )
            identity = SnapshotIdentity(
                sha256=payload["sha256"],
                byte_count=payload["byte_count"],
                entry_count=payload["entry_count"],
            )
            path = self._physical_path_for_id(workspace.workspace_id)
            if not path.is_dir() or path.is_symlink():
                raise WorkspaceStateStale("Read publication destination missing or replaced")
            if state.lifecycle_state is WorkspaceLifecycleState.ALLOCATED and not any(
                path.iterdir()
            ):
                # Crash after intent, before atomic rename. Remain ALLOCATED.
                marker.unlink()
                self._fsync_managed_root()
                return state
            binding = SourceSnapshotBinding(snapshot_path=path, identity=identity)
            try:
                verify_clean_source_snapshot(binding, label="recovered READ_SNAPSHOT")
            except Exception as exc:
                raise WorkspaceStateStale(
                    "Read publication bytes cannot be independently proven"
                ) from exc
            if state.lifecycle_state is WorkspaceLifecycleState.ALLOCATED:
                if state.state_version != payload["state_version"]:
                    raise WorkspaceStateStale("Read publication generation was superseded")
                connection.execute(
                    "INSERT INTO workspace_read_snapshots(workspace_id, sha256, byte_count, "
                    "entry_count, materialized_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        workspace.workspace_id,
                        identity.sha256,
                        identity.byte_count,
                        identity.entry_count,
                        utc_now().isoformat(),
                    ),
                )
                state = self._write_state(
                    connection,
                    current=state,
                    next_state=WorkspaceLifecycleState.READY,
                )
            elif state.lifecycle_state is WorkspaceLifecycleState.READY:
                row = connection.execute(
                    "SELECT sha256, byte_count, entry_count FROM workspace_read_snapshots WHERE workspace_id=?",
                    (workspace.workspace_id,),
                ).fetchone()
                if row is None or (
                    str(row["sha256"]),
                    int(row["byte_count"]),
                    int(row["entry_count"]),
                ) != (identity.sha256, identity.byte_count, identity.entry_count):
                    raise WorkspaceStateStale(
                        "Committed snapshot receipt differs from physical source"
                    )
            else:
                raise WorkspaceStateStale("Publication receipt conflicts with workspace lifecycle")
        # Sidecar is removed only after the final SQLite commit returned.
        marker.unlink()
        self._fsync_managed_root()
        return state

    def materialize_worktree(
        self,
        workspace: WorkspaceRef,
        source_repository: Path,
        source_snapshot: SourceSnapshotBinding,
        *,
        expected_state_version: int,
        git: GitService | None = None,
    ) -> tuple[WorkspaceState, CandidateCheckpoint]:
        """Materialize one mutable Git worktree without running repository checkout logic.

        Git registers an unpopulated detached worktree with ``--no-checkout`` and hooks
        disabled. Oh-Goon then populates repository bytes from an already-clean source
        snapshot. Git administration therefore remains metadata, not the candidate-content
        identity or a security boundary.
        """

        durable = self.reference(workspace.workspace_id)
        if durable != workspace:
            raise WorkspaceStateStale(
                f"Workspace reference does not match durable identity: {workspace.workspace_id}"
            )
        if workspace.kind is not WorkspaceKind.WORKTREE:
            raise WorkspaceTransitionInvalid(
                "WORKTREE materialization requires a WORKTREE workspace"
            )
        current = self.state(workspace.workspace_id)
        self._require_version(current, expected_state_version)
        if current.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
            raise WorkspaceTransitionInvalid(
                "WORKTREE materialization requires ALLOCATED workspace state"
            )

        verify_clean_source_snapshot(source_snapshot, label="candidate source snapshot")
        source_repository = canonical_path(source_repository)
        service = git or GitService()
        root = service.root(source_repository)
        if root is None or root != source_repository:
            raise WorkspaceStateStale("Candidate source must be the canonical Git repository root")
        source_observation = service.observe(source_repository)
        if source_observation.head_sha != workspace.base_revision:
            raise WorkspaceStateStale(
                "Candidate base revision does not match source repository HEAD: "
                f"expected {workspace.base_revision}, observed {source_observation.head_sha}"
            )
        if source_observation.changed_files:
            raise WorkspaceStateStale(
                "Candidate source repository must be clean before materialization"
            )
        if source_snapshot_identity(source_repository) != source_snapshot.identity:
            raise WorkspaceStateStale(
                "Candidate source snapshot does not match clean repository source"
            )

        destination = self.physical_path(workspace)
        if not destination.is_dir() or destination.is_symlink() or any(destination.iterdir()):
            raise WorkspaceStateStale(
                f"WORKTREE workspace container is not an empty owned directory: {workspace.workspace_id}"
            )
        # The marker is durable *before* Git is allowed to change either its
        # administration area or the workspace filesystem. Git and SQLite do not
        # share a transaction; a restart must not infer registration from .git.
        receipt_fd = self._prepare_worktree_marker(
            workspace, current, source_repository, source_snapshot.identity
        )
        try:
            destination.rmdir()
            result = service.run(
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                "--no-checkout",
                str(destination),
                workspace.base_revision,
                cwd=source_repository,
                env=_worktree_git_environment(),
            )
            if result.returncode != 0:
                raise WorkspaceStateStale(
                    f"Cannot register candidate Git worktree: {result.stderr}"
                )
            git_marker = destination / ".git"
            if not git_marker.is_file() or git_marker.is_symlink():
                raise WorkspaceStateStale(
                    "Candidate Git worktree did not create canonical .git metadata"
                )
            index_result = service.run(
                "-c",
                "core.hooksPath=/dev/null",
                "read-tree",
                "HEAD",
                cwd=destination,
                env=_worktree_git_environment(),
            )
            if index_result.returncode != 0:
                raise WorkspaceStateStale(
                    f"Cannot initialize candidate Git index without checkout: {index_result.stderr}"
                )
            shutil.copytree(
                source_snapshot.snapshot_path,
                destination,
                dirs_exist_ok=True,
                symlinks=True,
                copy_function=copy_regular_file,
            )
            identity = source_snapshot_identity(destination)
            if identity != source_snapshot.identity:
                raise WorkspaceStateStale("Candidate bytes differ from admitted source snapshot")
            observation = service.observe(destination)
            if observation.head_sha != workspace.base_revision or observation.changed_files:
                raise WorkspaceStateStale(
                    "Candidate worktree is not clean at its declared base revision"
                )

            with self._database.transaction(write=True) as connection:
                fresh = self._state_for_update(connection, workspace.workspace_id)
                self._require_version(fresh, expected_state_version)
                if fresh.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
                    raise WorkspaceTransitionInvalid(
                        "WORKTREE materialization requires ALLOCATED workspace state"
                    )
                now = utc_now()
                connection.execute(
                    "INSERT INTO workspace_worktrees("
                    "workspace_id, base_revision, git_marker_sha256, materialized_at"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        workspace.workspace_id,
                        workspace.base_revision,
                        _sha256_file(git_marker),
                        now.isoformat(),
                    ),
                )
                checkpoint = self._insert_checkpoint(
                    connection,
                    workspace_id=workspace.workspace_id,
                    parent_checkpoint_id=None,
                    identity=identity,
                    changed_paths=(),
                    attempt_id=None,
                    assignment_id=None,
                    created_at=now,
                )
                ready = self._write_state_with_checkpoint(
                    connection,
                    current=fresh,
                    next_state=WorkspaceLifecycleState.READY,
                    checkpoint_id=checkpoint.checkpoint_id,
                )
            self._clear_worktree_marker(workspace.workspace_id)
            return ready, checkpoint
        except BaseException:
            # A nonzero `git worktree add` is *not* evidence of no registration.
            # Recover only a provably absent effect. Registered or ambiguous
            # effects are retained for explicit owner reconciliation after restart.
            try:
                self._restore_absent_worktree_effect(workspace, service)
            except (WorkspaceStateStale, OSError):
                pass
            raise
        finally:
            os.close(receipt_fd)

    def _worktree_marker_for_id(self, workspace_id: str) -> Path:
        return self._physical_path_for_id(workspace_id).with_name(
            f".worktree-publish-{storage_component(workspace_id)}"
        )

    def _prepare_worktree_marker(
        self,
        workspace: WorkspaceRef,
        state: WorkspaceState,
        source_repository: Path,
        identity: SnapshotIdentity,
    ) -> int:
        repository_info = source_repository.lstat()
        payload = {
            "owner": "RepositoryWorkspaceAuthority",
            "kind": "WORKTREE",
            "workspace_id": workspace.workspace_id,
            "state_version": state.state_version,
            "base_revision": workspace.base_revision,
            "source_repository": str(source_repository),
            "source_device": repository_info.st_dev,
            "source_inode": repository_info.st_ino,
            "sha256": identity.sha256,
            "byte_count": identity.byte_count,
            "entry_count": identity.entry_count,
        }
        descriptor = os.open(
            self._worktree_marker_for_id(workspace.workspace_id),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            try:
                import fcntl
            except ImportError as exc:
                raise WorkspaceStateStale("Native Git registration requires POSIX flock") from exc
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            if os.write(descriptor, data) != len(data):
                raise WorkspaceStateStale("Short worktree publication receipt write")
            os.fsync(descriptor)
            self._fsync_managed_root()
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @contextmanager
    def _locked_worktree_receipt(self, workspace_id: str) -> Iterator[None]:
        """Fail before reconciliation if a live Git effect still holds the receipt.

        The original registration owner holds flock from *before* its first Git
        operation through its SQLite cut. Kernel death drops the lock; neither a
        PID check nor expiration time is substituted for this exclusion proof.
        """
        marker = self._worktree_marker_for_id(workspace_id)
        try:
            descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise WorkspaceStateStale("Missing original worktree receipt for recovery") from exc
        try:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkspaceStateStale("Native Git effect is still in flight") from exc
            opened = os.fstat(descriptor)
            current = marker.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise WorkspaceStateStale("Worktree receipt replaced during recovery")
            yield
        finally:
            os.close(descriptor)

    def _read_worktree_marker(
        self, workspace: WorkspaceRef, state: WorkspaceState
    ) -> _WorktreeReceipt:
        marker = self._worktree_marker_for_id(workspace.workspace_id)
        if marker.is_symlink() or not marker.is_file():
            raise WorkspaceStateStale("Missing original worktree publication receipt")
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise WorkspaceStateStale("Invalid worktree publication receipt")
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise WorkspaceStateStale("Malformed worktree publication receipt") from exc
        if (
            payload.get("owner") != "RepositoryWorkspaceAuthority"
            or payload.get("kind") != "WORKTREE"
            or payload.get("workspace_id") != workspace.workspace_id
            or payload.get("base_revision") != workspace.base_revision
            or payload.get("state_version") not in (state.state_version, state.state_version - 1)
        ):
            raise WorkspaceStateStale("Worktree publication receipt generation or identity changed")
        source = Path(payload.get("source_repository", ""))
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            raise WorkspaceStateStale("Worktree source repository is missing or replaced")
        source_info = source.lstat()
        if (source_info.st_dev, source_info.st_ino) != (
            payload.get("source_device"),
            payload.get("source_inode"),
        ):
            raise WorkspaceStateStale("Worktree source repository identity changed")
        if not isinstance(payload.get("sha256"), str) or len(payload["sha256"]) != 64:
            raise WorkspaceStateStale("Worktree source identity receipt invalid")
        return cast(_WorktreeReceipt, payload)

    @staticmethod
    def _registered_git_worktree(
        service: GitService, source_repository: Path, destination: Path
    ) -> bool:
        result = service.run(
            "worktree",
            "list",
            "--porcelain",
            "-z",
            cwd=source_repository,
            env=_worktree_git_environment(),
        )
        if result.returncode != 0:
            raise WorkspaceStateStale("Cannot prove native Git worktree registration")
        paths = [
            item.removeprefix("worktree ")
            for item in result.stdout.split("\0")
            if item.startswith("worktree ")
        ]
        if paths.count(str(destination)) > 1:
            raise WorkspaceStateStale("Duplicate native Git worktree registrations")
        return str(destination) in paths

    def _clear_worktree_marker(self, workspace_id: str) -> None:
        marker = self._worktree_marker_for_id(workspace_id)
        if marker.is_symlink() or not marker.is_file() or marker.lstat().st_nlink != 1:
            raise WorkspaceStateStale("Worktree publication receipt replaced during cleanup")
        marker.unlink()
        self._fsync_managed_root()

    def _restore_absent_worktree_effect(self, workspace: WorkspaceRef, service: GitService) -> bool:
        """Repair only a positively absent Git effect, never a registered one."""
        state = self.state(workspace.workspace_id)
        payload = self._read_worktree_marker(workspace, state)
        source = Path(payload["source_repository"])
        destination = self._physical_path_for_id(workspace.workspace_id)
        if state.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
            return False
        if self._registered_git_worktree(service, source, destination):
            return False
        if destination.is_symlink() or (
            destination.exists() and (not destination.is_dir() or any(destination.iterdir()))
        ):
            raise WorkspaceStateStale("Unregistered Git effect has unknown filesystem bytes")
        destination.mkdir(mode=0o700, exist_ok=True)
        self._fsync_managed_root()
        self._clear_worktree_marker(workspace.workspace_id)
        return True

    def reconcile_worktree(
        self, workspace: WorkspaceRef, *, git: GitService | None = None
    ) -> WorkspaceState:
        """Recover interrupted original Git publication using Git + SQLite proof.

        A partial registered worktree is retained for explicit intervention; only a
        complete, clean, source-identical tree can cross the original READY cut.
        """
        with self._locked_worktree_receipt(workspace.workspace_id):
            return self._reconcile_worktree_locked(workspace, git=git)

    def _reconcile_worktree_locked(
        self, workspace: WorkspaceRef, *, git: GitService | None = None
    ) -> WorkspaceState:
        if (
            workspace.kind is not WorkspaceKind.WORKTREE
            or self.reference(workspace.workspace_id) != workspace
        ):
            raise WorkspaceStateStale("Worktree reconciliation requires exact durable identity")
        service = git or GitService()
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace.workspace_id)
            payload = self._read_worktree_marker(workspace, state)
            source = Path(payload["source_repository"])
            destination = self._physical_path_for_id(workspace.workspace_id)
            if not self._registered_git_worktree(service, source, destination):
                # An absent registration is not proof of an absent filesystem
                # effect. In particular, do not replace a foreign path.
                if destination.is_symlink() or (
                    destination.exists()
                    and (not destination.is_dir() or any(destination.iterdir()))
                ):
                    raise WorkspaceStateStale("Unregistered worktree has unproven bytes")
                if state.lifecycle_state is not WorkspaceLifecycleState.ALLOCATED:
                    raise WorkspaceStateStale("Committed worktree lost native Git registration")
                destination.mkdir(mode=0o700, exist_ok=True)
            else:
                marker = destination / ".git"
                if (
                    not destination.is_dir()
                    or destination.is_symlink()
                    or not marker.is_file()
                    or marker.is_symlink()
                ):
                    raise WorkspaceStateStale(
                        "Registered Git worktree metadata is missing or replaced"
                    )
                common = service.run(
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                    cwd=destination,
                    env=_worktree_git_environment(),
                )
                expected_common = service.run(
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                    cwd=source,
                    env=_worktree_git_environment(),
                )
                if (
                    common.returncode != 0
                    or expected_common.returncode != 0
                    or common.stdout.strip() != expected_common.stdout.strip()
                ):
                    raise WorkspaceStateStale(
                        "Registered Git worktree belongs to another repository"
                    )
                observation = service.observe(destination, env=_worktree_git_environment())
                if observation.head_sha != workspace.base_revision or observation.changed_files:
                    raise WorkspaceStateStale(
                        "Registered Git worktree content is incomplete or dirty"
                    )
                actual = source_snapshot_identity(destination)
                if (actual.sha256, actual.byte_count, actual.entry_count) != (
                    payload["sha256"],
                    payload["byte_count"],
                    payload["entry_count"],
                ):
                    raise WorkspaceStateStale(
                        "Registered Git worktree content differs from exact source"
                    )
                if state.lifecycle_state is WorkspaceLifecycleState.ALLOCATED:
                    now = utc_now()
                    connection.execute(
                        "INSERT INTO workspace_worktrees(workspace_id, base_revision, git_marker_sha256, "
                        "materialized_at) VALUES (?, ?, ?, ?)",
                        (
                            workspace.workspace_id,
                            workspace.base_revision,
                            _sha256_file(marker),
                            now.isoformat(),
                        ),
                    )
                    checkpoint = self._insert_checkpoint(
                        connection,
                        workspace_id=workspace.workspace_id,
                        parent_checkpoint_id=None,
                        identity=actual,
                        changed_paths=(),
                        attempt_id=None,
                        assignment_id=None,
                        created_at=now,
                    )
                    state = self._write_state_with_checkpoint(
                        connection,
                        current=state,
                        next_state=WorkspaceLifecycleState.READY,
                        checkpoint_id=checkpoint.checkpoint_id,
                    )
                elif state.lifecycle_state is WorkspaceLifecycleState.READY:
                    row = connection.execute(
                        "SELECT base_revision, git_marker_sha256 FROM workspace_worktrees WHERE workspace_id=?",
                        (workspace.workspace_id,),
                    ).fetchone()
                    if (
                        row is None
                        or row["base_revision"] != workspace.base_revision
                        or row["git_marker_sha256"] != _sha256_file(marker)
                    ):
                        raise WorkspaceStateStale(
                            "Committed Git worktree metadata mismatches native Git"
                        )
                else:
                    raise WorkspaceStateStale(
                        "Worktree publication receipt conflicts with durable state"
                    )
        self._clear_worktree_marker(workspace.workspace_id)
        return state

    def _worktree_retirement_marker_for_id(self, workspace_id: str) -> Path:
        return self._physical_path_for_id(workspace_id).with_name(
            f".worktree-retire-{storage_component(workspace_id)}"
        )

    @staticmethod
    def _require_no_ever_started_writer(connection: sqlite3.Connection, workspace_id: str) -> None:
        """Until independent native LACE is qualified, never infer absence from an ACK.

        A settled lease or locally stored fence is NOT an independent physical
        observer. This development path supports only candidates that never
        delegated writer authority. Full writer-bearing cleanup remains gated.
        """
        row = connection.execute(
            "SELECT 1 FROM workspace_leases WHERE workspace_id=? "
            "AND writer_authority_kind IS NOT NULL LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if row is not None:
            raise WorkspaceStateStale("Native Git retirement requires independent writer absence")

    def _worktree_retirement_preflight(
        self, workspace: WorkspaceRef, *, expected_state_version: int, git: GitService
    ) -> _RetirementReceipt:
        if (
            workspace.kind is not WorkspaceKind.WORKTREE
            or self.reference(workspace.workspace_id) != workspace
        ):
            raise WorkspaceStateStale("Worktree retirement requires exact durable identity")
        if (
            self._worktree_marker_for_id(workspace.workspace_id).exists()
            or self._worktree_marker_for_id(workspace.workspace_id).is_symlink()
            or self._worktree_retirement_marker_for_id(workspace.workspace_id).exists()
            or self._worktree_retirement_marker_for_id(workspace.workspace_id).is_symlink()
        ):
            raise WorkspaceStateStale(
                "Worktree has unresolved Git retirement or publication effect"
            )
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace.workspace_id)
            self._require_version(state, expected_state_version)
            if state.lifecycle_state is not WorkspaceLifecycleState.FINALIZED:
                raise WorkspaceTransitionInvalid(
                    "Native Git retirement requires FINALIZED workspace"
                )
            self._require_no_unsettled_writer(connection, state)
            self._require_no_ever_started_writer(connection, workspace.workspace_id)
            row = connection.execute(
                "SELECT base_revision, git_marker_sha256 FROM workspace_worktrees WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()
            if row is None or row["base_revision"] != workspace.base_revision:
                raise WorkspaceStateStale("No matching original Git registration to retire")
            path = self._physical_path_for_id(workspace.workspace_id)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise WorkspaceStateStale("Native Git retirement destination replaced")
            marker = path / ".git"
            if (
                marker.is_symlink()
                or not marker.is_file()
                or _sha256_file(marker) != row["git_marker_sha256"]
            ):
                raise WorkspaceStateStale(
                    "Native Git marker differs from durable original registration"
                )
            common = git.run(
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                cwd=path,
                env=_worktree_git_environment(),
            )
            if common.returncode != 0:
                raise WorkspaceStateStale("Cannot prove original Git common directory")
            common_path = Path(common.stdout.strip())
            if (
                not common_path.is_absolute()
                or common_path.is_symlink()
                or not common_path.is_dir()
                or common_path.name != ".git"
            ):
                raise WorkspaceStateStale("Original Git common directory is unavailable")
            source = common_path.parent
            if (
                source.is_symlink()
                or not source.is_dir()
                or not self._registered_git_worktree(git, source, path)
            ):
                raise WorkspaceStateStale(
                    "Native Git registration cannot be proved before retirement"
                )
            observation = git.observe(path, env=_worktree_git_environment())
            if observation.head_sha != workspace.base_revision or observation.changed_files:
                raise WorkspaceStateStale(
                    "Native Git retirement refuses dirty or changed candidate"
                )
            checkpoint = connection.execute(
                "SELECT sha256, byte_count, entry_count FROM candidate_checkpoints WHERE checkpoint_id=? "
                "AND workspace_id=?",
                (state.current_checkpoint_id, workspace.workspace_id),
            ).fetchone()
            if checkpoint is None:
                raise WorkspaceStateStale(
                    "Native Git retirement requires current durable checkpoint"
                )
            identity = source_snapshot_identity(path)
            if (identity.sha256, identity.byte_count, identity.entry_count) != (
                checkpoint["sha256"],
                checkpoint["byte_count"],
                checkpoint["entry_count"],
            ):
                raise WorkspaceStateStale(
                    "Native Git retirement candidate differs from durable checkpoint"
                )
            common_info = common_path.lstat()
            return {
                "owner": "RepositoryWorkspaceAuthority",
                "kind": "WORKTREE_RETIRE",
                "workspace_id": workspace.workspace_id,
                "state_version": state.state_version,
                "base_revision": workspace.base_revision,
                "common_path": str(common_path),
                "common_device": common_info.st_dev,
                "common_inode": common_info.st_ino,
                "destination_device": info.st_dev,
                "destination_inode": info.st_ino,
                "git_marker_sha256": str(row["git_marker_sha256"]),
                "checkpoint_sha256": identity.sha256,
                "checkpoint_byte_count": identity.byte_count,
                "checkpoint_entry_count": identity.entry_count,
            }

    def _prepare_retirement_marker(self, workspace_id: str, payload: _RetirementReceipt) -> int:
        marker = self._worktree_retirement_marker_for_id(workspace_id)
        descriptor = os.open(
            marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            if os.write(descriptor, serialized) != len(serialized):
                raise WorkspaceStateStale("Short native Git retirement receipt write")
            os.fsync(descriptor)
            self._fsync_managed_root()
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _read_retirement_marker(
        self, workspace: WorkspaceRef, state: WorkspaceState
    ) -> _RetirementReceipt:
        marker = self._worktree_retirement_marker_for_id(workspace.workspace_id)
        info = marker.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 4096
            or marker.is_symlink()
        ):
            raise WorkspaceStateStale("Native Git retirement receipt replaced")
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise WorkspaceStateStale("Malformed native Git retirement receipt") from exc
        if (
            payload.get("owner") != "RepositoryWorkspaceAuthority"
            or payload.get("kind") != "WORKTREE_RETIRE"
            or payload.get("workspace_id") != workspace.workspace_id
            or payload.get("base_revision") != workspace.base_revision
            or payload.get("state_version") not in (state.state_version, state.state_version - 1)
        ):
            raise WorkspaceStateStale(
                "Native Git retirement receipt identity or generation changed"
            )
        common = Path(payload.get("common_path", ""))
        if (
            not common.is_absolute()
            or common.is_symlink()
            or not common.is_dir()
            or common.name != ".git"
        ):
            raise WorkspaceStateStale("Native Git retirement common directory replaced")
        observed = common.lstat()
        if (observed.st_dev, observed.st_ino) != (
            payload.get("common_device"),
            payload.get("common_inode"),
        ):
            raise WorkspaceStateStale("Native Git retirement common directory identity changed")
        return cast(_RetirementReceipt, payload)

    def _clear_retirement_marker(self, workspace_id: str) -> None:
        marker = self._worktree_retirement_marker_for_id(workspace_id)
        info = marker.lstat()
        if marker.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspaceStateStale("Native Git retirement receipt replaced during cleanup")
        marker.unlink()
        self._fsync_managed_root()

    def retire_worktree(
        self,
        workspace: WorkspaceRef,
        *,
        expected_state_version: int,
        git: GitService | None = None,
    ) -> WorkspaceState:
        """One original-owner native Git removal followed by SQLite RELEASED cut.

        Only FINALIZED, clean, never-delegated worktrees qualify until an
        independent writer-absence witness is available. A Git subprocess
        failure can leave effects; its receipt is retained for restart proof.
        """
        service = git or GitService()
        payload = self._worktree_retirement_preflight(
            workspace,
            expected_state_version=expected_state_version,
            git=service,
        )
        descriptor = self._prepare_retirement_marker(workspace.workspace_id, payload)
        try:
            # Reprove after acquiring the exclusive marker, before destructive Git.
            fresh = self.state(workspace.workspace_id)
            if fresh.state_version != expected_state_version:
                raise WorkspaceStateStale("Native Git retirement generation raced")
            # Exclude replacement of the original source admin area after the
            # preflight, without treating marker presence as evidence itself.
            if self._read_retirement_marker(workspace, fresh) != payload:
                raise WorkspaceStateStale("Native Git retirement receipt changed before effect")
            path = self._physical_path_for_id(workspace.workspace_id)
            info = path.lstat()
            if (info.st_dev, info.st_ino) != (
                payload["destination_device"],
                payload["destination_inode"],
            ):
                raise WorkspaceStateStale("Native Git retirement destination identity raced")
            source = Path(payload["common_path"]).parent
            if not self._registered_git_worktree(service, source, path):
                raise WorkspaceStateStale("Native Git worktree disappeared before retirement")
            native_marker = path / ".git"
            if (
                native_marker.is_symlink()
                or not native_marker.is_file()
                or _sha256_file(native_marker) != payload["git_marker_sha256"]
            ):
                raise WorkspaceStateStale("Native Git metadata changed during retirement prework")
            observed = service.observe(path, env=_worktree_git_environment())
            if observed.head_sha != workspace.base_revision or observed.changed_files:
                raise WorkspaceStateStale("Native Git candidate changed during retirement prework")
            identity = source_snapshot_identity(path)
            if (identity.sha256, identity.byte_count, identity.entry_count) != (
                payload["checkpoint_sha256"],
                payload["checkpoint_byte_count"],
                payload["checkpoint_entry_count"],
            ):
                raise WorkspaceStateStale(
                    "Native Git candidate content changed during retirement prework"
                )
            result = service.run(
                "worktree",
                "remove",
                str(path),
                cwd=source,
                env=_worktree_git_environment(),
            )
            if result.returncode != 0:
                raise WorkspaceStateStale(
                    f"Native Git worktree removal unresolved: {result.stderr}"
                )
            return self._reconcile_retirement_locked(workspace, git=service)
        finally:
            os.close(descriptor)

    def reconcile_worktree_retirement(
        self, workspace: WorkspaceRef, *, git: GitService | None = None
    ) -> WorkspaceState:
        """Only original owner can turn positive native absence into RELEASED."""
        marker = self._worktree_retirement_marker_for_id(workspace.workspace_id)
        try:
            descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise WorkspaceStateStale("Missing native Git retirement receipt") from exc
        try:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkspaceStateStale("Native Git retirement still in flight") from exc
            opened, observed = os.fstat(descriptor), marker.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino)
            ):
                raise WorkspaceStateStale("Native Git retirement receipt replaced during replay")
            return self._reconcile_retirement_locked(workspace, git=git or GitService())
        finally:
            os.close(descriptor)

    def _reconcile_retirement_locked(
        self, workspace: WorkspaceRef, *, git: GitService
    ) -> WorkspaceState:
        if (
            workspace.kind is not WorkspaceKind.WORKTREE
            or self.reference(workspace.workspace_id) != workspace
        ):
            raise WorkspaceStateStale("Native Git retirement requires exact durable owner identity")
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace.workspace_id)
            payload = self._read_retirement_marker(workspace, state)
            source = Path(payload["common_path"]).parent
            path = self._physical_path_for_id(workspace.workspace_id)
            registered = self._registered_git_worktree(git, source, path)
            if registered or path.exists() or path.is_symlink():
                raise WorkspaceStateStale(
                    "Native Git retirement unresolved: registration or bytes remain"
                )
            if state.lifecycle_state is WorkspaceLifecycleState.FINALIZED:
                self._require_no_unsettled_writer(connection, state)
                self._require_no_ever_started_writer(connection, workspace.workspace_id)
                row = connection.execute(
                    "SELECT base_revision, git_marker_sha256 FROM workspace_worktrees WHERE workspace_id=?",
                    (workspace.workspace_id,),
                ).fetchone()
                if (
                    row is None
                    or row["base_revision"] != workspace.base_revision
                    or row["git_marker_sha256"] != payload["git_marker_sha256"]
                    or state.state_version != payload["state_version"]
                ):
                    raise WorkspaceStateStale("Native Git retirement durable registration changed")
                connection.execute(
                    "DELETE FROM workspace_worktrees WHERE workspace_id=?",
                    (workspace.workspace_id,),
                )
                state = self._write_state(
                    connection,
                    current=state,
                    next_state=WorkspaceLifecycleState.RELEASED,
                )
            elif state.lifecycle_state is not WorkspaceLifecycleState.RELEASED:
                raise WorkspaceStateStale("Native Git retirement crossed incompatible lifecycle")
            else:
                row = connection.execute(
                    "SELECT base_revision FROM workspace_worktrees WHERE workspace_id=?",
                    (workspace.workspace_id,),
                ).fetchone()
                if row is not None:
                    raise WorkspaceStateStale("RELEASED native Git registration still persisted")
        self._clear_retirement_marker(workspace.workspace_id)
        return state

    def candidate_checkpoint(self, checkpoint_id: str) -> CandidateCheckpoint:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            raise CandidateCheckpointNotFound(f"Candidate checkpoint not found: {checkpoint_id}")
        return _candidate_checkpoint(row)

    def current_candidate_checkpoint(
        self,
        workspace: WorkspaceRef,
        *,
        reprove: bool = True,
        git: GitService | None = None,
    ) -> CandidateCheckpoint:
        durable = self.reference(workspace.workspace_id)
        if durable != workspace or workspace.kind is not WorkspaceKind.WORKTREE:
            raise WorkspaceStateStale("Candidate checkpoint requires the durable WORKTREE identity")
        state = self.state(workspace.workspace_id)
        if state.current_checkpoint_id is None:
            raise CandidateCheckpointNotFound(
                f"WORKTREE has no current checkpoint: {workspace.workspace_id}"
            )
        checkpoint = self.candidate_checkpoint(state.current_checkpoint_id)
        if checkpoint.workspace_id != workspace.workspace_id:
            raise CandidateCheckpointStale("Current checkpoint belongs to another workspace")
        if reprove:
            path = self.physical_path(workspace)
            self._verify_worktree_metadata(workspace, path)
            observed = source_snapshot_identity(path)
            expected = SnapshotIdentity(
                checkpoint.source_sha256,
                checkpoint.byte_count,
                checkpoint.entry_count,
            )
            if observed != expected:
                raise CandidateCheckpointStale(
                    f"Candidate bytes changed after checkpoint {checkpoint.checkpoint_id}"
                )
            observation = (git or GitService()).observe(path)
            if observation.head_sha != workspace.base_revision:
                raise CandidateCheckpointStale(
                    "Candidate Git base revision changed after checkpoint"
                )
        return checkpoint

    def create_candidate_checkpoint(
        self,
        workspace: WorkspaceRef,
        *,
        expected_state_version: int,
        expected_parent_checkpoint_id: str,
        attempt_id: str,
        assignment_id: str,
        git: GitService | None = None,
    ) -> tuple[WorkspaceState, CandidateCheckpoint]:
        """Atomically append one candidate-content checkpoint and advance workspace state."""

        if not attempt_id or not assignment_id:
            raise ValueError("Candidate checkpoint requires attempt_id and assignment_id")
        durable = self.reference(workspace.workspace_id)
        if durable != workspace or workspace.kind is not WorkspaceKind.WORKTREE:
            raise WorkspaceTransitionInvalid("Candidate checkpoints require a WORKTREE workspace")
        current = self.state(workspace.workspace_id)
        self._require_version(current, expected_state_version)
        if current.lifecycle_state not in {
            WorkspaceLifecycleState.READY,
            WorkspaceLifecycleState.IN_USE,
        }:
            raise WorkspaceTransitionInvalid(
                f"Cannot checkpoint WORKTREE from {current.lifecycle_state.value} state"
            )
        if current.current_checkpoint_id != expected_parent_checkpoint_id:
            raise CandidateCheckpointStale(
                "Candidate checkpoint parent changed concurrently: "
                f"expected {expected_parent_checkpoint_id}, observed {current.current_checkpoint_id}"
            )
        parent = self.candidate_checkpoint(expected_parent_checkpoint_id)
        if parent.workspace_id != workspace.workspace_id:
            raise CandidateCheckpointStale(
                "Candidate checkpoint parent belongs to another workspace"
            )

        path = self.physical_path(workspace)
        self._verify_worktree_metadata(workspace, path)
        service = git or GitService()
        observation = service.observe(path)
        if observation.head_sha != workspace.base_revision:
            raise CandidateCheckpointStale("Candidate Git base revision changed before checkpoint")
        identity = source_snapshot_identity(path)
        changed_paths = tuple(sorted(observation.changed_files))
        now = utc_now()
        with self._database.transaction(write=True) as connection:
            fresh = self._state_for_update(connection, workspace.workspace_id)
            self._require_version(fresh, expected_state_version)
            if fresh.current_checkpoint_id != expected_parent_checkpoint_id:
                raise CandidateCheckpointStale("Candidate checkpoint parent changed before commit")
            checkpoint = self._insert_checkpoint(
                connection,
                workspace_id=workspace.workspace_id,
                parent_checkpoint_id=expected_parent_checkpoint_id,
                identity=identity,
                changed_paths=changed_paths,
                attempt_id=attempt_id,
                assignment_id=assignment_id,
                created_at=now,
            )
            state = self._write_state_with_checkpoint(
                connection,
                current=fresh,
                next_state=fresh.lifecycle_state,
                checkpoint_id=checkpoint.checkpoint_id,
            )
        return state, checkpoint

    def read_snapshot_binding(self, workspace: WorkspaceRef) -> SourceSnapshotBinding:
        """Return a freshly re-proven immutable binding for a READY/IN_USE READ_SNAPSHOT."""

        durable = self.reference(workspace.workspace_id)
        if durable != workspace:
            raise WorkspaceStateStale(
                f"Workspace reference does not match durable identity: {workspace.workspace_id}"
            )
        if workspace.kind is not WorkspaceKind.READ_SNAPSHOT:
            raise WorkspaceTransitionInvalid("Workspace is not a READ_SNAPSHOT")
        state = self.state(workspace.workspace_id)
        if state.lifecycle_state not in {
            WorkspaceLifecycleState.READY,
            WorkspaceLifecycleState.IN_USE,
        }:
            raise WorkspaceTransitionInvalid(
                f"READ_SNAPSHOT is not executable from {state.lifecycle_state.value} state"
            )
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT sha256, byte_count, entry_count FROM workspace_read_snapshots "
                "WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceStateStale(
                f"READ_SNAPSHOT has no durable source identity: {workspace.workspace_id}"
            )
        binding = SourceSnapshotBinding(
            snapshot_path=self.physical_path(workspace),
            identity=SnapshotIdentity(
                sha256=str(row["sha256"]),
                byte_count=int(row["byte_count"]),
                entry_count=int(row["entry_count"]),
            ),
        )
        try:
            verify_clean_source_snapshot(binding, label="managed READ_SNAPSHOT")
        except Exception as exc:
            raise WorkspaceStateStale(
                f"READ_SNAPSHOT source identity is no longer current: {workspace.workspace_id}"
            ) from exc
        return binding

    def transition(
        self,
        workspace_id: str,
        *,
        expected_state_version: int,
        next_state: WorkspaceLifecycleState,
    ) -> WorkspaceState:
        """Advance lifecycle with explicit CAS; release/allocation keep physical ownership."""

        if next_state is WorkspaceLifecycleState.ALLOCATED:
            raise WorkspaceTransitionInvalid(
                "Use RepositoryWorkspaceAuthority.allocate for ALLOCATED"
            )
        if next_state is WorkspaceLifecycleState.RELEASED:
            raise WorkspaceTransitionInvalid(
                "Use RepositoryWorkspaceAuthority.release for RELEASED"
            )
        if (
            next_state is WorkspaceLifecycleState.READY
            and self.reference(workspace_id).kind is WorkspaceKind.READ_SNAPSHOT
        ):
            raise WorkspaceTransitionInvalid(
                "Use RepositoryWorkspaceAuthority.materialize_read_snapshot for READ_SNAPSHOT READY"
            )
        with self._database.transaction(write=True) as connection:
            current = self._state_for_update(connection, workspace_id)
            self._require_version(current, expected_state_version)
            self._require_transition(current.lifecycle_state, next_state)
            return self._write_state(connection, current=current, next_state=next_state)

    def release(self, workspace_id: str, *, expected_state_version: int) -> WorkspaceState:
        """Retire a workspace with one durable write cut and a recoverable FS move.

        Unlike unlink-before-COMMIT, a rename to a deterministic private quarantine
        can be reversed if the write cut rolls back. After a crash the same owner
        resolves that quarantine against the *durable* lifecycle state. This does
        not claim atomic filesystem + SQLite transactions or prove writer absence.
        """

        # One public release ingress: a completed native Git worktree delegates
        # to this same owner's receipted native retirement operation. Never
        # unlink a registered worktree through the generic directory path.
        durable = self.reference(workspace_id)
        if durable.kind is WorkspaceKind.WORKTREE:
            observed = self.state(workspace_id)
            if (
                observed.state_version == expected_state_version
                and observed.lifecycle_state is WorkspaceLifecycleState.FINALIZED
            ):
                return self.retire_worktree(
                    durable,
                    expected_state_version=expected_state_version,
                )
        renamed = False
        quarantine = self._release_quarantine_for_id(workspace_id)
        try:
            with self._database.transaction(write=True) as connection:
                current = self._state_for_update(connection, workspace_id)
                self._require_version(current, expected_state_version)
                if (
                    self._worktree_retirement_marker_for_id(workspace_id).exists()
                    or self._worktree_retirement_marker_for_id(workspace_id).is_symlink()
                ):
                    raise WorkspaceStateStale("Workspace has unresolved Git retirement")
                if current.lifecycle_state is WorkspaceLifecycleState.RELEASED:
                    return current
                self._require_transition(current.lifecycle_state, WorkspaceLifecycleState.RELEASED)
                self._require_no_unsettled_writer(connection, current)
                path = self._physical_path_for_id(workspace_id)
                if (
                    self._worktree_retirement_marker_for_id(workspace_id).exists()
                    or self._worktree_retirement_marker_for_id(workspace_id).is_symlink()
                ):
                    raise WorkspaceStateStale("Workspace has unresolved Git retirement")
                if self._worktree_marker_for_id(workspace_id).exists():
                    raise WorkspaceStateStale(
                        f"Workspace has unresolved native Git publication: {workspace_id}"
                    )
                # .git may have been removed or replaced externally while its
                # Git registration and durable metadata still exist. A missing
                # marker is never positive evidence that Git deregistered.
                native_row = connection.execute(
                    "SELECT 1 FROM workspace_worktrees WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()
                if native_row is not None:
                    raise WorkspaceStateStale(
                        f"Registered Git worktree requires native deregistration: {workspace_id}"
                    )
                if quarantine.exists() or quarantine.is_symlink():
                    raise WorkspaceStateStale(
                        f"Workspace has unresolved release quarantine: {workspace_id}"
                    )
                if self._release_receipt_for_id(workspace_id).exists():
                    raise WorkspaceStateStale(
                        f"Workspace has unresolved release receipt: {workspace_id}"
                    )
                if path.exists():
                    if not path.is_dir() or path.is_symlink():
                        raise WorkspaceStateStale(
                            f"Workspace physical path is not a directory: {workspace_id}"
                        )
                    # A registered Git worktree cannot be removed merely by
                    # deleting its filesystem tree: Git owns registration. Refuse
                    # until the original Git owner has supplied a cleanup proof.
                    if (path / ".git").exists() or (path / ".git").is_symlink():
                        raise WorkspaceStateStale(
                            f"Registered Git worktree requires native deregistration: {workspace_id}"
                        )
                    self._prepare_release_receipt(path, current)
                    path.rename(quarantine)
                    renamed = True
                released = self._write_state(
                    connection,
                    current=current,
                    next_state=WorkspaceLifecycleState.RELEASED,
                )
        except BaseException:
            if renamed:
                # The commit outcome must be read, not inferred from an exception.
                try:
                    durable_state = self.state(workspace_id)
                except BaseException as exc:
                    raise WorkspaceStateStale(
                        f"Workspace release commit is unknown; quarantine retained: {workspace_id}"
                    ) from exc
                if durable_state.lifecycle_state is not WorkspaceLifecycleState.RELEASED:
                    self._restore_release_quarantine(workspace_id)
                    self._clear_release_receipt(workspace_id)
            raise
        if renamed:
            self._clear_release_quarantine(workspace_id)
            self._clear_release_receipt(workspace_id)
        return released

    def reconcile_release(self, workspace_id: str) -> WorkspaceState:
        """Recover *only* this owner's unfinished release using the SQLite state.

        A durable RELEASED state permits cleanup, but a precommit crash means
        the quarantine must be restored. Missing or replaced paths fail closed.
        The current database transaction is the only recovery serialization owner.
        """

        if (
            self._worktree_retirement_marker_for_id(workspace_id).exists()
            or self._worktree_retirement_marker_for_id(workspace_id).is_symlink()
        ):
            raise WorkspaceStateStale(
                "Reconcile native Git retirement with original worktree owner"
            )
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace_id)
            quarantine = self._release_quarantine_for_id(workspace_id)
            if not quarantine.exists() and not quarantine.is_symlink():
                receipt = self._release_receipt_for_id(workspace_id)
                if receipt.exists():
                    path = self._physical_path_for_id(workspace_id)
                    if state.lifecycle_state is WorkspaceLifecycleState.RELEASED:
                        if path.exists() or path.is_symlink():
                            raise WorkspaceStateStale(
                                f"RELEASED workspace path is still present: {workspace_id}"
                            )
                    else:
                        self._verify_release_receipt(
                            workspace_id,
                            path,
                            durable_version=state.state_version,
                        )
                    self._clear_release_receipt(workspace_id)
                return state
            self._verify_release_receipt(
                workspace_id, quarantine, durable_version=state.state_version
            )
            if state.lifecycle_state is not WorkspaceLifecycleState.RELEASED:
                self._restore_release_quarantine(workspace_id, durable_version=state.state_version)
                self._clear_release_receipt(workspace_id)
                return state
        self._clear_release_quarantine(workspace_id)
        self._clear_release_receipt(workspace_id)
        return state

    def _release_receipt_for_id(self, workspace_id: str) -> Path:
        path = self._physical_path_for_id(workspace_id)
        return path.with_name(f".release-receipt-{path.name}")

    def _prepare_release_receipt(self, path: Path, current: WorkspaceState) -> None:
        info = path.lstat()
        receipt = self._release_receipt_for_id(current.workspace_id)
        payload = {
            "owner": "RepositoryWorkspaceAuthority",
            "workspace_id": current.workspace_id,
            "state_version": current.state_version,
            "device": info.st_dev,
            "inode": info.st_ino,
        }
        descriptor = os.open(
            receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            os.write(
                descriptor, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_managed_root()

    def _verify_release_receipt(
        self, workspace_id: str, path: Path, *, durable_version: int | None = None
    ) -> None:
        receipt = self._release_receipt_for_id(workspace_id)
        if receipt.is_symlink() or not receipt.is_file():
            raise WorkspaceStateStale(f"Missing owner release receipt: {workspace_id}")
        metadata = receipt.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 1024:
            raise WorkspaceStateStale(f"Invalid release receipt: {workspace_id}")
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        observed = path.lstat()
        if (
            payload.get("owner") != "RepositoryWorkspaceAuthority"
            or payload.get("workspace_id") != workspace_id
            or not stat.S_ISDIR(observed.st_mode)
            or payload.get("device") != observed.st_dev
            or payload.get("inode") != observed.st_ino
        ):
            raise WorkspaceStateStale(
                f"Release receipt does not match exact owned directory: {workspace_id}"
            )
        version = (
            self.state(workspace_id).state_version if durable_version is None else durable_version
        )
        if payload.get("state_version") not in (version, version - 1):
            raise WorkspaceStateStale(f"Release receipt state generation changed: {workspace_id}")

    def _clear_release_receipt(self, workspace_id: str) -> None:
        receipt = self._release_receipt_for_id(workspace_id)
        if receipt.exists():
            if receipt.is_symlink() or not receipt.is_file():
                raise WorkspaceStateStale(f"Release receipt replaced: {workspace_id}")
            receipt.unlink()
            self._fsync_managed_root()

    def _release_quarantine_for_id(self, workspace_id: str) -> Path:
        path = self._physical_path_for_id(workspace_id)
        quarantine = path.with_name(f".release-{path.name}")
        if quarantine.is_symlink():
            raise WorkspaceStateStale(f"Release quarantine became a symlink: {workspace_id}")
        return quarantine

    def _restore_release_quarantine(
        self, workspace_id: str, *, durable_version: int | None = None
    ) -> None:
        quarantine = self._release_quarantine_for_id(workspace_id)
        path = self._physical_path_for_id(workspace_id)
        if path.exists() or path.is_symlink():
            raise WorkspaceStateStale(
                f"Cannot restore release quarantine over another path: {workspace_id}"
            )
        if not quarantine.is_dir() or quarantine.is_symlink():
            raise WorkspaceStateStale(
                f"Release quarantine is not an owned directory: {workspace_id}"
            )
        self._verify_release_receipt(workspace_id, quarantine, durable_version=durable_version)
        quarantine.rename(path)

    def _clear_release_quarantine(self, workspace_id: str) -> None:
        quarantine = self._release_quarantine_for_id(workspace_id)
        if quarantine.exists():
            self._verify_release_receipt(workspace_id, quarantine)
            metadata = quarantine.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise WorkspaceStateStale(
                    f"Cannot remove replaced release quarantine: {workspace_id}"
                )
            shutil.rmtree(quarantine)

    @staticmethod
    def _require_no_unsettled_writer(connection: sqlite3.Connection, state: WorkspaceState) -> None:
        if state.current_lease_id is not None:
            raise WorkspaceStateStale(
                f"Workspace release requires settled lease: {state.workspace_id}"
            )
        row = connection.execute(
            """SELECT lease_id FROM workspace_leases WHERE workspace_id=?
               AND (status IN ('ACTIVE', 'QUARANTINED') OR
                   (writer_authority_kind IS NOT NULL AND fence_evidence_json IS NULL))
               LIMIT 1""",
            (state.workspace_id,),
        ).fetchone()
        if row is not None:
            raise WorkspaceStateStale(
                f"Workspace release has unproven writer quiescence: {state.workspace_id}"
            )

    def _verify_worktree_metadata(self, workspace: WorkspaceRef, path: Path) -> None:
        marker = path / ".git"
        if not marker.is_file() or marker.is_symlink():
            raise CandidateCheckpointStale("Candidate Git metadata marker is missing or replaced")
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT base_revision, git_marker_sha256 FROM workspace_worktrees WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()
        if row is None:
            raise CandidateCheckpointStale("Candidate has no durable worktree metadata evidence")
        if row["base_revision"] != workspace.base_revision:
            raise CandidateCheckpointStale("Candidate durable Git base revision changed")
        if _sha256_file(marker) != row["git_marker_sha256"]:
            raise CandidateCheckpointStale(
                "Candidate Git metadata marker changed after materialization"
            )

    @staticmethod
    def _insert_checkpoint(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        parent_checkpoint_id: str | None,
        identity: SnapshotIdentity,
        changed_paths: tuple[str, ...],
        attempt_id: str | None,
        assignment_id: str | None,
        created_at: datetime,
    ) -> CandidateCheckpoint:
        checkpoint = CandidateCheckpoint.model_validate(
            {
                "checkpoint_id": new_checkpoint_id(),
                "workspace_id": workspace_id,
                "parent_checkpoint_id": parent_checkpoint_id,
                "source_sha256": identity.sha256,
                "byte_count": identity.byte_count,
                "entry_count": identity.entry_count,
                "changed_paths": changed_paths,
                "attempt_id": attempt_id,
                "assignment_id": assignment_id,
                "created_at": created_at,
            }
        )
        connection.execute(
            """
            INSERT INTO candidate_checkpoints(
                checkpoint_id, workspace_id, parent_checkpoint_id, sha256,
                byte_count, entry_count, changed_paths_json, attempt_id,
                assignment_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.workspace_id,
                checkpoint.parent_checkpoint_id,
                checkpoint.source_sha256,
                checkpoint.byte_count,
                checkpoint.entry_count,
                json.dumps(checkpoint.changed_paths, separators=(",", ":")),
                checkpoint.attempt_id,
                checkpoint.assignment_id,
                checkpoint.created_at.isoformat(),
            ),
        )
        return checkpoint

    def _write_state_with_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        current: WorkspaceState,
        next_state: WorkspaceLifecycleState,
        checkpoint_id: str,
    ) -> WorkspaceState:
        now = utc_now()
        next_version = current.state_version + 1
        result = connection.execute(
            """
            UPDATE workspace_state
            SET lifecycle_state=?, state_version=?, current_checkpoint_id=?, updated_at=?
            WHERE workspace_id=? AND state_version=?
            """,
            (
                next_state.value,
                next_version,
                checkpoint_id,
                now.isoformat(),
                current.workspace_id,
                current.state_version,
            ),
        )
        if result.rowcount != 1:
            raise WorkspaceStateStale(
                f"Workspace state changed concurrently: {current.workspace_id}"
            )
        return WorkspaceState(
            workspace_id=current.workspace_id,
            lifecycle_state=next_state,
            state_version=next_version,
            current_lease_id=current.current_lease_id,
            current_checkpoint_id=checkpoint_id,
            updated_at=now,
        )

    def _physical_path_for_id(self, workspace_id: str) -> Path:
        root = canonical_path(self._root)
        candidate = root / storage_component(workspace_id)
        # Do not allow a pre-created link to turn the logical ID into host-path authority.
        if candidate.is_symlink():
            raise WorkspaceStateStale(
                f"Workspace physical path must not be a symlink: {workspace_id}"
            )
        resolved = candidate.resolve()
        if resolved.parent != root:
            raise WorkspaceStateStale(
                f"Workspace physical path escaped canonical root: {workspace_id}"
            )
        return resolved

    def _state_for_update(
        self, connection: sqlite3.Connection, workspace_id: str
    ) -> WorkspaceState:
        row = connection.execute(
            "SELECT * FROM workspace_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"Workspace state not found: {workspace_id}")
        return _workspace_state(row)

    @staticmethod
    def _require_version(current: WorkspaceState, expected: int) -> None:
        if current.state_version != expected:
            raise WorkspaceStateStale(
                "Workspace state changed concurrently: "
                f"{current.workspace_id} expected version {expected}, "
                f"observed {current.state_version}"
            )

    @staticmethod
    def _require_transition(
        current: WorkspaceLifecycleState, next_state: WorkspaceLifecycleState
    ) -> None:
        if next_state not in _ALLOWED_TRANSITIONS[current]:
            raise WorkspaceTransitionInvalid(
                f"Invalid workspace lifecycle transition: {current.value} -> {next_state.value}"
            )

    def _write_state(
        self,
        connection: sqlite3.Connection,
        *,
        current: WorkspaceState,
        next_state: WorkspaceLifecycleState,
    ) -> WorkspaceState:
        now = utc_now()
        next_version = current.state_version + 1
        result = connection.execute(
            """
            UPDATE workspace_state
            SET lifecycle_state=?, state_version=?, updated_at=?
            WHERE workspace_id=? AND state_version=?
            """,
            (
                next_state.value,
                next_version,
                now.isoformat(),
                current.workspace_id,
                current.state_version,
            ),
        )
        if result.rowcount != 1:
            raise WorkspaceStateStale(
                f"Workspace state changed concurrently: {current.workspace_id}"
            )
        return WorkspaceState(
            workspace_id=current.workspace_id,
            lifecycle_state=next_state,
            state_version=next_version,
            current_lease_id=current.current_lease_id,
            current_checkpoint_id=current.current_checkpoint_id,
            updated_at=now,
        )


def _workspace_ref(row: sqlite3.Row) -> WorkspaceRef:
    return WorkspaceRef(
        workspace_id=row["workspace_id"],
        repository_id=row["repository_id"],
        base_revision=row["base_revision"],
        kind=WorkspaceKind(row["kind"]),
    )


def _workspace_state(row: sqlite3.Row) -> WorkspaceState:
    return WorkspaceState(
        workspace_id=row["workspace_id"],
        lifecycle_state=WorkspaceLifecycleState(row["lifecycle_state"]),
        state_version=int(row["state_version"]),
        current_lease_id=row["current_lease_id"],
        current_checkpoint_id=row["current_checkpoint_id"],
        updated_at=row["updated_at"],
    )


def _candidate_checkpoint(row: sqlite3.Row) -> CandidateCheckpoint:
    return CandidateCheckpoint.model_validate(
        {
            "checkpoint_id": row["checkpoint_id"],
            "workspace_id": row["workspace_id"],
            "parent_checkpoint_id": row["parent_checkpoint_id"],
            "source_sha256": row["sha256"],
            "byte_count": int(row["byte_count"]),
            "entry_count": int(row["entry_count"]),
            "changed_paths": tuple(json.loads(row["changed_paths_json"])),
            "attempt_id": row["attempt_id"],
            "assignment_id": row["assignment_id"],
            "created_at": row["created_at"],
        }
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
