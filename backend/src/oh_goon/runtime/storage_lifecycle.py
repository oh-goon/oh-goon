from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from oh_goon.core.game_tape_policy import (
    GAME_TAPE_MAX_CHUNKS_PER_RUN,
    GAME_TAPE_TERMINAL_RETENTION_SECONDS,
)
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.git_source_preparation import git_source_cache
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, database_path_for_environment
from oh_goon.runtime.diagnostics import (
    DEFAULT_DIAGNOSTIC_LOG_BACKUPS,
    DEFAULT_MAX_DIAGNOSTIC_LOG_BYTES,
)
from oh_goon.runtime.league_folders import league_preferences_path_for_environment
from oh_goon.runtime.league_lineups import lineup_root_for_environment
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.run_log_retention import (
    MAX_RETAINED_RUN_LOG_BYTES,
    MAX_RETAINED_RUN_LOG_FILES,
    prune_run_log_tree,
)
from oh_goon.runtime.run_workspace_reconciliation import reconcile_idle_run_workspaces
from oh_goon.runtime.shared_plans import shared_plan_root_for_environment


@dataclass(frozen=True, slots=True)
class StorageClass:
    storage_id: str
    path: Path | None
    owner: str
    durability: str
    retention: str
    cleanup_authority: str
    generic_prune: bool


@dataclass(frozen=True, slots=True)
class StorageMeasurement:
    entries: int
    bytes: int


@dataclass(frozen=True, slots=True)
class UnclassifiedStorage:
    path: Path
    root_kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class StorageCensus:
    inventory: tuple[StorageClass, ...]
    unclassified: tuple[UnclassifiedStorage, ...]


@dataclass(frozen=True, slots=True)
class StoragePruneReport:
    removed_run_logs: int
    removed_run_workspaces: tuple[str, ...]
    refused_run_workspaces: tuple[str, ...]
    removed_content_staging: tuple[str, ...]
    refused_content_staging: tuple[str, ...]
    removed_content_objects: tuple[str, ...]
    removed_launch_access: tuple[str, ...]
    refused_launch_access: tuple[str, ...]
    cleanup_incomplete_run_workspaces: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def _state_home(environment: dict[str, str]) -> Path:
    value = environment.get("XDG_STATE_HOME", "").strip()
    return (Path(value).expanduser() if value else Path.home() / ".local/state").resolve()


def _cache_evidence_paths(state_home: Path) -> Path:
    return state_home / "oh-goon" / "pytest-member-receipts"


def _inventory_paths(
    environment: dict[str, str],
) -> tuple[Path, ApplicationStatePathLayout, Path, Path, Path, Path, Path, Path, Path]:
    database = database_path_for_environment(environment)
    app = ApplicationStatePathLayout.from_database(database)
    state_home = _state_home(environment)
    pytest_receipts = _cache_evidence_paths(state_home)
    return (
        database,
        app,
        state_home / "oh-goon" / "run-logs",
        state_home / "oh-goon" / "certification",
        state_home / "oh-goon" / "self-certification",
        league_preferences_path_for_environment(environment),
        shared_plan_root_for_environment(environment),
        lineup_root_for_environment(environment),
        pytest_receipts,
    )


def storage_inventory(environment: dict[str, str] | None = None) -> tuple[StorageClass, ...]:
    """Return the canonical local-storage owner map without mutating any path."""

    env = dict(os.environ if environment is None else environment)
    (
        database,
        app,
        run_logs,
        certification,
        self_certification,
        league_file,
        shared_plans,
        lineups,
        pytest_receipts,
    ) = _inventory_paths(env)
    return (
        (
            StorageClass(
                "skills.git-sources",
                git_source_cache(env),
                "shared native Git Goon and Skill source preparation",
                "replaceable-source-cache",
                "commits retained by native Git refs; temporary worktrees close after capture",
                "native Git maintenance; explicit reset only while no worktrees are active",
                False,
            ),
            StorageClass(
                "configuration.league-folders",
                league_file,
                "LeagueFolderStore",
                "durable-user-configuration",
                "until explicit user replacement",
                "LeagueFolderStore exact-file owner only",
                False,
            ),
            StorageClass(
                "configuration.shared-plans",
                shared_plans,
                "SharedPlanFileStore",
                "durable-user-configuration",
                "until explicit user save/remove",
                "SharedPlanFileStore exact-file owner only",
                False,
            ),
            StorageClass(
                "configuration.lineups",
                lineups,
                "LeagueLineupFileStore",
                "durable-user-configuration",
                "until explicit user save/remove",
                "LeagueLineupFileStore exact-file owner only",
                False,
            ),
            StorageClass(
                "application.database",
                database,
                "LocalDatabase",
                "durable-control-state",
                "until explicit schema-compatible reset/migration",
                "local-state reset only",
                False,
            ),
            StorageClass(
                "application.retained-content",
                app.content_store,
                "FleetContentStore",
                "durable-retained-content",
                "while referenced or leased",
                "reference/lease-aware FleetContentStore GC",
                True,
            ),
            StorageClass(
                "application.content-staging",
                app.content_store / "staging",
                "FleetContentStore",
                "ephemeral-staging",
                "until publication completes; crash orphans require dead creator birth proof",
                "FleetContentStore stage reconciler",
                True,
            ),
            StorageClass(
                "application.run-workspaces",
                app.run_workspaces,
                "PreparedPlanRun + ExecutionCoordinator",
                "ephemeral-execution",
                "for the lifetime of the admitted run preparation/execution",
                "execution-idle fenced workspace reconciler",
                True,
            ),
            StorageClass(
                "application.executor-control",
                app.executor_control,
                "executor control authority",
                "durable-recovery-state",
                "until owning executor recovery/reset says otherwise",
                "executor owner only",
                False,
            ),
            StorageClass(
                "application.launch-facts",
                app.local_launches,
                "LocalLaunchEventStore",
                "durable-observed-state",
                "retained through local state epoch",
                "launch/state reset owner only",
                False,
            ),
            StorageClass(
                "application.launch-bindings",
                app.local_launch_bindings,
                "LocalLaunchBindingStore",
                "durable-binding-state",
                "retained through local state epoch",
                "launch/state reset owner only",
                False,
            ),
            StorageClass(
                "application.launch-access",
                app.local_launch_access,
                "LocalLaunchAccessStore",
                "secret-capability-state",
                "exact launch owner generation only",
                "terminal-generation-proven launch capability owner",
                True,
            ),
            StorageClass(
                "application.diagnostics",
                app.diagnostics_log.parent,
                "JsonlDiagnosticSink",
                "bounded-diagnostic",
                (
                    f"{DEFAULT_MAX_DIAGNOSTIC_LOG_BYTES} bytes x "
                    f"{DEFAULT_DIAGNOSTIC_LOG_BACKUPS} backups"
                ),
                "automatic diagnostic rotation",
                False,
            ),
            StorageClass(
                "application.plan-tools",
                app.plan_tools,
                "plan tool materialization owner",
                "managed-materialization",
                "until owner-specific replacement/reset",
                "plan-tool owner only",
                False,
            ),
            StorageClass(
                "application.extensions",
                app.extensions,
                "extension store",
                "managed-local-state",
                "until explicit extension replacement/reset",
                "extension owner only",
                False,
            ),
            StorageClass(
                "run-logs",
                run_logs,
                "run_logged.sh + run_log_retention",
                "bounded-diagnostic",
                (
                    f"max {MAX_RETAINED_RUN_LOG_FILES} completed logs / "
                    f"{MAX_RETAINED_RUN_LOG_BYTES} bytes per command kind"
                ),
                "automatic/canonical run-log retention",
                True,
            ),
            StorageClass(
                "self-certification.evidence",
                self_certification,
                "self-certification evidence/artifact owner",
                "durable-verification-evidence",
                "identity-keyed evidence retained for reuse/regrading and artifact proof",
                "self-certification owner only",
                False,
            ),
            StorageClass(
                "process.temporary-directories",
                None,
                "owning TemporaryDirectory/staging context",
                "process-scoped-ephemeral",
                "until owner scope exits; crash leftovers are not inferred safe generically",
                "owning context or explicit subsystem reconciler only",
                False,
            ),
            StorageClass(
                "game-tape.live-output",
                None,
                "GameTapeLiveOutputStore",
                "process-local-transient",
                (
                    f"max {GAME_TAPE_MAX_CHUNKS_PER_RUN} chunks/run; terminal handoff "
                    f"{GAME_TAPE_TERMINAL_RETENTION_SECONDS}s"
                ),
                "automatic process-local expiry",
                False,
            ),
        )
        + _cache_evidence_storage_classes(pytest_receipts)
        + _certification_control_state_storage_classes(certification)
        + _certification_run_workspace_storage_classes(certification)
    )


def _certification_control_state_storage_classes(certification: Path) -> tuple[StorageClass, ...]:
    if not certification.is_dir() or certification.is_symlink():
        return ()
    classes: list[StorageClass] = []
    for repository in _visible_children(certification):
        if not _is_certification_repository_directory(repository):
            continue
        state = repository / f"schema-{DATABASE_SCHEMA_VERSION}" / "state"
        if state.is_symlink() or not state.is_dir():
            continue
        classes.append(
            StorageClass(
                f"certification.control-state.{repository.name}",
                state,
                "certification_state_lease + LocalDatabase",
                "durable-certification-journal",
                "current repository/schema control state",
                "certification-state-reset for one exact repository only",
                False,
            )
        )
    return tuple(classes)


def _unclassified_certification_storage(
    certification: Path, *, reason: str
) -> tuple[UnclassifiedStorage, ...]:
    unknown: list[UnclassifiedStorage] = []
    current_schema = f"schema-{DATABASE_SCHEMA_VERSION}"
    for repository in _visible_children(certification):
        if not _is_certification_repository_directory(repository):
            unknown.append(_unclassified_child(repository, root_kind="state", reason=reason))
            continue
        for schema in _visible_children(repository):
            if schema.name != current_schema:
                unknown.append(_unclassified_child(schema, root_kind="state", reason=reason))
    return tuple(unknown)


def _certification_run_workspace_storage_classes(certification: Path) -> tuple[StorageClass, ...]:
    if not certification.is_dir() or certification.is_symlink():
        return ()
    classes: list[StorageClass] = []
    try:
        repositories = sorted(certification.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    for repository in repositories:
        if not _is_certification_repository_directory(repository):
            continue
        classes.extend(_repository_certification_run_workspace_classes(repository))
    return tuple(classes)


def _is_certification_repository_directory(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("repo-")
        and len(name) == 25
        and not any(character not in "0123456789abcdef" for character in name[5:])
        and not path.is_symlink()
        and path.is_dir()
    )


def _repository_certification_run_workspace_classes(repository: Path) -> tuple[StorageClass, ...]:
    schema = repository / f"schema-{DATABASE_SCHEMA_VERSION}"
    if schema.is_symlink() or not schema.is_dir():
        return ()
    state = schema / "state"
    if state.is_symlink() or not state.is_dir():
        return ()
    return (
        StorageClass(
            f"certification.run-workspaces.{repository.name}",
            state / "run-workspaces",
            "PreparedPlanRun + certification workflow lease + ExecutionCoordinator",
            "ephemeral-certification-execution",
            "current run lifetime; abandoned scratch reconciled before certification",
            "certification workflow lease + execution-idle fenced workspace reconciler",
            True,
        ),
    )


def _cache_evidence_storage_classes(pytest_receipts: Path) -> tuple[StorageClass, ...]:
    return (
        StorageClass(
            "pytest.member-receipts",
            pytest_receipts,
            "pytest batch receipt owner",
            "identity-keyed-execution-evidence",
            "resume/provenance history; lifecycle bounding pending Cache Lifecycle Closure",
            "pytest receipt owner only",
            False,
        ),
    )


def _unclassified_child(path: Path, *, root_kind: str, reason: str) -> UnclassifiedStorage:
    return UnclassifiedStorage(path=path, root_kind=root_kind, reason=reason)


def _visible_children(path: Path) -> tuple[Path, ...]:
    if not path.is_dir() or path.is_symlink():
        return ()
    try:
        return tuple(sorted(path.iterdir(), key=lambda item: item.name))
    except OSError:
        return ()


def _residual_named_children(
    root: Path, *, known: set[str], root_kind: str, reason: str
) -> tuple[UnclassifiedStorage, ...]:
    return tuple(
        _unclassified_child(child, root_kind=root_kind, reason=reason)
        for child in _visible_children(root)
        if child.name not in known
    )


def storage_census(environment: dict[str, str] | None = None) -> StorageCensus:
    """Account for declared Oh-Goon cache/state roots and expose residual bytes.

    Declared ownership must cover bytes, not merely reserve a convenient ancestor name.
    Only declared storage classes, including native Git Skill sources, own cache
    namespaces. Other bytes below the Oh-Goon cache root remain unclassified.
    Unknown bytes remain
    reporting-only and receive no cleanup authority.
    """

    env = dict(os.environ if environment is None else environment)
    inventory = storage_inventory(env)
    cache_value = env.get("XDG_CACHE_HOME", "").strip()
    cache_home = (
        Path(cache_value).expanduser() if cache_value else Path.home() / ".cache"
    ).resolve()
    cache_root = cache_home / "oh-goon"
    state_root = _state_home(env) / "oh-goon"
    generic_reason = "unclassified Oh-Goon storage sibling; no automatic cleanup authority"

    unknown = list(
        _residual_named_children(
            state_root,
            known={"run-logs", "certification", "self-certification", "pytest-member-receipts"},
            root_kind="state",
            reason=generic_reason,
        )
    )
    unknown.extend(
        _unclassified_certification_storage(state_root / "certification", reason=generic_reason)
    )
    unknown.extend(
        _residual_named_children(
            cache_root,
            known={"skill-sources"},
            root_kind="cache",
            reason=generic_reason,
        )
    )
    return StorageCensus(
        inventory=inventory,
        unclassified=tuple(sorted(unknown, key=lambda item: str(item.path))),
    )


def measure_storage(path: Path | None) -> StorageMeasurement | None:
    """Observe logical entry sizes, refusing an incomplete filesystem walk."""
    if path is None:
        return None
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    entries = 1
    bytes_ = metadata.st_size
    if not stat.S_ISDIR(metadata.st_mode):
        return StorageMeasurement(entries=entries, bytes=bytes_)
    directories = [os.scandir(path)]
    try:
        while directories:
            try:
                entry = next(directories[-1])
            except StopIteration:
                directories.pop().close()
                continue
            metadata = entry.stat(follow_symlinks=False)
            entries += 1
            bytes_ += metadata.st_size
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(os.scandir(entry.path))
    finally:
        for directory in directories:
            directory.close()
    return StorageMeasurement(entries=entries, bytes=bytes_)


def prune_generic_storage(environment: dict[str, str] | None = None) -> StoragePruneReport:
    """Prune only classes whose canonical owner can prove disposal is safe now."""

    env = dict(os.environ if environment is None else environment)
    database_path = database_path_for_environment(env)
    layout = ApplicationStatePathLayout.from_database(database_path)
    # A known active workspace must refuse maintenance before unrelated cleanup
    # starts. The original reconciler owns the durable execution-idle fence.
    workspaces = reconcile_idle_run_workspaces(database_path)
    run_log_results = prune_run_log_tree(_state_home(env) / "oh-goon" / "run-logs")
    removed_run_logs = sum(len(result.removed_files) for result in run_log_results)

    removed_staging: tuple[str, ...] = ()
    refused_staging: tuple[str, ...] = ()
    removed_objects: tuple[str, ...] = ()
    if layout.content_store.exists():
        store = FleetContentStore(layout.content_store)
        staging = store.prune_orphan_staging()
        removed_staging = staging.removed
        refused_staging = staging.refused
        removed_objects = store.garbage_collect()

    access = LocalLaunchAccessStore(root=layout.local_launch_access)
    launches = LocalLaunchEventStore(root=layout.local_launches)
    access_prune = access.prune_terminal(launches)

    return StoragePruneReport(
        removed_run_logs=removed_run_logs,
        removed_run_workspaces=workspaces.removed,
        refused_run_workspaces=workspaces.refused,
        removed_content_staging=removed_staging,
        refused_content_staging=refused_staging,
        removed_content_objects=removed_objects,
        removed_launch_access=access_prune.removed,
        refused_launch_access=access_prune.refused,
        cleanup_incomplete_run_workspaces=workspaces.cleanup_incomplete,
    )
