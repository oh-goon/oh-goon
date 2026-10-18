from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.product_identity import repository_id
from oh_goon.core.source_layout_policy import assert_canonical_source_layout
from oh_goon.core.strict_executor_authority import (
    CERTIFICATION_CGROUP_ROOT_ENV,
    EXECUTORS_FILE_ENV,
    SYSTEMD_DELEGATED_ENV,
    SYSTEMD_SUBGROUP_ENV,
)
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    delegated_process_subgroup,
    enable_delegated_cgroup_v2_controllers,
    place_current_process_in_delegated_cgroup_v2_subgroup,
    require_delegated_cgroup_v2_root,
)
from oh_goon.runtime._executor_configuration import (
    EXECUTOR_CONFIG_VERSION,
    load_executor_pool,
)
from oh_goon.runtime._executor_local_capacity import observe_local_executor_capacity
from oh_goon.runtime.database import (
    ADMITTED_SQLITE_REPROOF_ATTEMPTS,
    DATABASE_SCHEMA_VERSION,
    LocalDatabase,
    database_filesystem_is_local,
    database_filesystem_type,
    inspect_database_path,
)
from oh_goon.runtime.execution.coordinator import (
    PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS,
)
from oh_goon.runtime.local_state_maintenance import LocalStateCompatibility, reset_local_state
from oh_goon.runtime.storage_health import (
    probe_writable_directory,
    require_storage_capacity,
    resolve_storage_capacity_policy,
)
from oh_goon.services.certification_host_tools import (
    certification_tool_search_paths,
    required_certification_tool,
)
from oh_goon.services.certification_workspace_retention import (
    CertificationRunWorkspaceReconciliation,
    certification_run_workspace_names,
    certification_state_label,
    certification_state_root,
    certification_state_roots,
    reconcile_certification_run_workspaces_under_lease,
)

REQUIRED_CERTIFICATION_CONTROLLERS = {"cpu", "memory", "pids"}
CERTIFICATION_EXECUTOR_UNAVAILABLE = "CERTIFICATION EXECUTOR UNAVAILABLE"
CERTIFICATION_ATTEMPT_MEMORY_MIB = 4096
CERTIFICATION_CONTROL_HEADROOM_MIB = 1024
CERTIFICATION_EFFECTIVE_MEMORY_MIB = (
    CERTIFICATION_ATTEMPT_MEMORY_MIB + CERTIFICATION_CONTROL_HEADROOM_MIB
)
# Preserve a deliberately harsher fresh-connection stress proof before APP admission.
# Runtime preparation itself now uses one query-only observer connection for its whole
# immutable-input scope; preflight keeps 600 fresh opens as an independent storage stress
# test rather than pretending to mirror the runtime connection shape.
CERTIFICATION_DATABASE_INDEPENDENT_REOPEN_PROOFS = 3
CERTIFICATION_DATABASE_COMPRESSED_PREPARATION_SECONDS = 150
CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS = ceil(
    CERTIFICATION_DATABASE_COMPRESSED_PREPARATION_SECONDS
    / PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS
)
CERTIFICATION_DATABASE_FULL_INTEGRITY_INTERVAL = max(
    1, CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS // 2
)


class CertificationEnvironmentError(RuntimeError):
    """A physical/local prerequisite prevents certification bootstrap."""


class CertificationSourceError(RuntimeError):
    """Repository source/layout is invalid before certification admission."""


class CertificationStateBusy(RuntimeError):
    """Another certification workflow currently owns the exact certification state."""


def reconcile_certification_run_workspaces(
    *,
    state_home: Path,
    owned_state_root: Path | None = None,
) -> CertificationRunWorkspaceReconciliation:
    """Prune abandoned certification scratch across repository/schema namespaces.

    Every current-schema foreign repository namespace must grant its own non-blocking workflow
    lease before cleanup. The caller may name one already-owned state root (the certification
    frontdoor does this) so its lease is not recursively acquired. Unrecognized schema namespaces
    are outside current lifecycle authority and are left untouched. Corrupt, busy, or otherwise
    unprovable current namespaces are retained and reported rather than guessed safe.
    """

    owned = owned_state_root.resolve() if owned_state_root is not None else None
    removed: list[str] = []
    refused: list[str] = []
    cleanup_incomplete: list[str] = []
    for state in certification_state_roots(state_home=state_home):
        label = certification_state_label(state)
        names = certification_run_workspace_names(state)
        if not names:
            continue
        try:
            result = _reconcile_certification_state(state=state, owned=owned)
        except (
            CertificationStateBusy,
            FleetPlanInvalid,
            OSError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            del exc
            refused.extend(f"{label}/{name}" for name in names)
            continue
        removed.extend(f"{label}/{name}" for name in result.removed)
        refused.extend(f"{label}/{name}" for name in result.refused)
        cleanup_incomplete.extend(f"{label}/{name}" for name in result.cleanup_incomplete)
    return CertificationRunWorkspaceReconciliation(
        removed=tuple(removed),
        refused=tuple(refused),
        cleanup_incomplete=tuple(cleanup_incomplete),
    )


def _reconcile_certification_state(
    *, state: Path, owned: Path | None
) -> CertificationRunWorkspaceReconciliation:
    if owned is not None and state.resolve() == owned:
        return reconcile_certification_run_workspaces_under_lease(state)
    with certification_state_lease(state):
        return reconcile_certification_run_workspaces_under_lease(state)


@contextmanager
def certification_state_lease(state_root: Path):
    """Acquire one non-blocking workflow lease for the shared certification control DB.

    SQLite supports cooperating concurrent readers/writers, but certification is a
    single-owner workflow: two independent APP/release controllers must never share
    one execution lease/journal and race lifecycle authority.
    """

    root = state_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "certification-workflow.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                    os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CertificationStateBusy(
                "Another Oh-Goon certification workflow already owns this repository's "
                "certification state. Let that workflow finish before starting another "
                "make certify/prepare-release operation."
            ) from exc
        yield
    finally:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def reset_certification_control_state(*, source_root: Path, state_home: Path) -> tuple[Path, ...]:
    """Explicitly reset only the isolated certification SQLite control journal.

    Dependency-manager state is outside this control-state reset. Resetting the
    certification journal must not mutate uv/npm-owned materialization state.
    """

    state = certification_state_root(source_root=source_root, state_home=state_home)
    state.mkdir(parents=True, exist_ok=True)
    with certification_state_lease(state):
        return reset_local_state(state / "oh-goon.db")


def certification_executor_unavailable(reason: str) -> str:
    detail = reason.strip() or "the local delegation attempt returned no diagnostic"
    return (
        f"{CERTIFICATION_EXECUTOR_UNAVAILABLE}\n\n"
        "Strict release/CI execution requires a delegated cgroup-v2 executor.\n\n"
        "Automatic local delegation could not be created because:\n"
        f"{detail}\n\n"
        "Either:\n"
        f"- configure {EXECUTORS_FILE_ENV} with a real executor, or\n"
        "- enable a systemd user environment capable of Delegate=yes.\n\n"
        "For the repository-owned local/WSL setup path, run `make strict-executor-setup` once, then retry the same strict release/CI command."
    )


@dataclass(frozen=True, slots=True)
class CertificationBootstrapContext:
    """Backend-owned inputs required before one certification workflow can start.

    APP/self verification owns only durable control-state authority. Strict executor
    authority is present only for release/CI workflows that actually consume it.
    """

    repository_id: str
    state_root: Path
    database: Path
    league_file: Path
    lock_root: Path
    executors_file: Path | None = None

    def as_payload(self) -> dict[str, str]:
        payload = {
            "repository_id": self.repository_id,
            "state_root": str(self.state_root),
            "database": str(self.database),
            "league_file": str(self.league_file),
            "lock_root": str(self.lock_root),
        }
        if self.executors_file is not None:
            payload["executors_file"] = str(self.executors_file)
        return payload


def preflight_certification_database(
    *,
    source_root: Path,
    state_home: Path,
) -> dict[str, object]:
    """Run the certification SQLite proof under its own standalone lease.

    The canonical certification frontdoor uses
    :func:`preflight_certification_database_under_lease` instead so the same lease
    protects preflight, bootstrap, admission, execution, and terminal settlement.
    """

    state = certification_state_root(source_root=source_root, state_home=state_home)
    with certification_state_lease(state):
        return preflight_certification_database_under_lease(
            source_root=source_root,
            state_home=state_home,
        )


def _certification_database_path(state: Path) -> tuple[Path, str | None]:
    state.mkdir(parents=True, exist_ok=True)
    database = state / "oh-goon.db"
    filesystem = database_filesystem_type(state)
    if database_filesystem_is_local(state):
        return database, filesystem
    observed = filesystem or "unproven"
    raise CertificationEnvironmentError(
        "Certification SQLite control state must live on a proven local Linux filesystem; "
        f"observed filesystem={observed} at {state}.\n"
        "On WSL, keep the source checkout wherever you need it, but point durable state to "
        "the Linux filesystem, for example: export XDG_STATE_HOME=/home/$USER/.local/state"
    )


def _require_certification_database_ready(database: Path) -> None:
    inspection = inspect_database_path(database, quick_check=True, foreign_key_check=True)
    if inspection.status in {LocalStateCompatibility.NEW, LocalStateCompatibility.READY}:
        return
    raise CertificationEnvironmentError(
        "Certification SQLite durability preflight rejected existing control state. "
        + inspection.detail
        + "\nRepair only isolated certification control state with: "
        "make certification-state-reset\n"
        "Native dependency materialization is separate; rerun make init only if the native environment itself needs repair."
    )


def _require_database_path_identity(database: Path, identity, *, proof: str) -> None:
    current = database.stat()
    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
        return
    raise CertificationEnvironmentError(
        f"Certification SQLite path changed identity during {proof}; "
        "run make certification-state-reset and retry."
    )


def _prove_certification_database_endurance(
    admitted: LocalDatabase, database: Path, identity
) -> None:
    for _ in range(CERTIFICATION_DATABASE_INDEPENDENT_REOPEN_PROOFS):
        LocalDatabase(database, journal_mode="DELETE").durable_revision()
        _require_database_path_identity(database, identity, proof="independent reopen proof")

    for proof in range(1, CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS + 1):
        admitted.durable_revision()
        _require_database_path_identity(database, identity, proof="admitted endurance proof")
        if proof % CERTIFICATION_DATABASE_FULL_INTEGRITY_INTERVAL:
            continue
        inspection = inspect_database_path(database, integrity_check=True, foreign_key_check=True)
        if inspection.status is not LocalStateCompatibility.READY:
            raise CertificationEnvironmentError(
                "Certification SQLite endurance integrity proof failed before APP admission: "
                + inspection.detail
            )


def _require_certification_database_integrity(database: Path) -> None:
    inspection = inspect_database_path(database, integrity_check=True, foreign_key_check=True)
    if inspection.status is LocalStateCompatibility.READY:
        return
    raise CertificationEnvironmentError(
        "Certification SQLite preflight did not finish READY: " + inspection.detail
    )


def _certification_database_preflight_payload(
    database: Path, filesystem: str | None
) -> dict[str, object]:
    return {
        "database": str(database),
        "schema": DATABASE_SCHEMA_VERSION,
        "journal_mode": "DELETE",
        "filesystem": filesystem,
        "quick_check": "ok",
        "integrity_check": "ok",
        "foreign_key_check": "ok",
        "reopen_proofs": CERTIFICATION_DATABASE_INDEPENDENT_REOPEN_PROOFS,
        "admitted_read_proofs": CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS,
        "compressed_observation_seconds": float(
            CERTIFICATION_DATABASE_COMPRESSED_PREPARATION_SECONDS
        ),
        "runtime_preparation_observer": "persistent-query-only-connection-per-preparation",
        "runtime_observation_reproof_attempts": ADMITTED_SQLITE_REPROOF_ATTEMPTS,
    }


def preflight_certification_database_under_lease(
    *,
    source_root: Path,
    state_home: Path,
) -> dict[str, object]:
    """Prove the certification SQLite control path before APP admission.

    The fast proof catches pre-existing damage immediately. A deliberately harsher
    stress phase then drives fresh admitted read transactions without sleeping while
    preserving the exact admitted database path identity through the proof.
    """

    state = certification_state_root(source_root=source_root, state_home=state_home)
    database, filesystem = _certification_database_path(state)
    _require_certification_database_ready(database)
    try:
        admitted = LocalDatabase(database, journal_mode="DELETE")
        with admitted.transaction(write=True, track_revision=False):
            pass
        identity = database.stat()
        _prove_certification_database_endurance(admitted, database, identity)
        _require_certification_database_integrity(database)
    except CertificationEnvironmentError:
        raise
    except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
        raise CertificationEnvironmentError(
            "Certification SQLite endurance preflight failed before APP admission: "
            + str(exc)
            + "\nRepair only isolated certification control state with: "
            "make certification-state-reset\n"
            "Native dependency materialization is separate; rerun make init only if the native environment itself needs repair."
        ) from exc
    return _certification_database_preflight_payload(database, filesystem)


def prepare_certification_bootstrap(
    *,
    source_root: Path,
    state_home: Path,
    environment: dict[str, str],
    require_strict_executor: bool = False,
) -> CertificationBootstrapContext:
    """Prepare certification control state and only the authority the workflow consumes.

    APP/self verification intentionally does not discover, validate, or materialize a
    strict executor. Release/CI callers opt into strict executor authority explicitly;
    that path still delegates schema/capacity/cgroup validation to the runtime owners.
    """

    root = source_root.resolve()
    try:
        assert_canonical_source_layout(root)
    except ValueError as exc:
        raise CertificationSourceError(str(exc)) from exc
    repo_id = repository_id(root)
    state = certification_state_root(source_root=root, state_home=state_home)
    database = state / "oh-goon.db"
    lock_root = state / "locks"
    try:
        state.mkdir(parents=True, exist_ok=True)
        lock_root.mkdir(parents=True, exist_ok=True)
        probe_writable_directory(state)
        storage_policy = resolve_storage_capacity_policy(environment)
        require_storage_capacity(state, storage_policy)
    except (OSError, ValueError) as exc:
        raise CertificationEnvironmentError(
            f"Certification mutable state is not writable at {state}: {exc}"
        ) from exc

    inspection = inspect_database_path(database, quick_check=True)
    if inspection.status not in {LocalStateCompatibility.NEW, LocalStateCompatibility.READY}:
        raise CertificationEnvironmentError(
            "Certification control journal is not safely reusable. "
            + inspection.detail
            + "\nRepair only isolated certification control state with: "
            "make certification-state-reset\n"
            "Native dependency materialization is separate; rerun make init only if the native environment itself needs repair."
        )

    executors_file: Path | None = None
    if require_strict_executor:
        configured = environment.get(EXECUTORS_FILE_ENV, "").strip()
        if configured:
            executors_file = Path(configured)
            if not executors_file.is_absolute():
                raise FleetPlanInvalid(
                    f"{EXECUTORS_FILE_ENV} must be an absolute path for strict release/CI execution"
                )
            executors_file = executors_file.resolve()
            try:
                load_executor_pool(executors_file)
            except FleetPlanInvalid as exc:
                raise CertificationEnvironmentError(str(exc)) from exc
        else:
            try:
                executors_file = materialize_local_executor_inventory(
                    state=state, environment=environment
                )
                load_executor_pool(executors_file)
            except (FleetPlanInvalid, OSError) as exc:
                raise CertificationEnvironmentError(str(exc)) from exc

    return CertificationBootstrapContext(
        repository_id=repo_id,
        state_root=state,
        database=database,
        league_file=state / "league.yml",
        lock_root=lock_root,
        executors_file=executors_file,
    )


def materialize_local_executor_inventory(*, state: Path, environment: dict[str, str]) -> Path:
    """Materialize the canonical local Bubblewrap executor inventory."""

    bwrap = required_certification_tool("bwrap")
    runtime_inspector = required_certification_tool("ldd")
    try:
        cgroup_root = resolve_certification_cgroup_root(environment)
    except FleetPlanInvalid as exc:
        raise FleetPlanInvalid(certification_executor_unavailable(str(exc))) from exc
    capacity = observe_local_executor_capacity(cgroup_root)
    if capacity.cpu_millis < 2000:
        raise FleetPlanInvalid(
            "Strict release/CI execution requires at least 2000m CPU capacity; "
            f"observed {capacity.cpu_millis}m"
        )
    if capacity.memory_bytes < CERTIFICATION_EFFECTIVE_MEMORY_MIB * 1024 * 1024:
        raise FleetPlanInvalid(
            "Strict release/CI execution requires at least "
            f"{CERTIFICATION_EFFECTIVE_MEMORY_MIB} MiB effective memory capacity; "
            f"observed {capacity.memory_bytes // (1024 * 1024)} MiB across the cgroup hierarchy. "
            f"The strict release/CI attempt owns {CERTIFICATION_ATTEMPT_MEMORY_MIB} MiB and the certification "
            f"control plane reserves {CERTIFICATION_CONTROL_HEADROOM_MIB} MiB headroom while "
            "running the certification control plane and isolated workload."
        )
    if capacity.max_processes is not None and capacity.max_processes < 64:
        raise FleetPlanInvalid(
            "Strict release/CI execution requires at least 64 processes; "
            f"observed {capacity.max_processes}"
        )

    search_paths = certification_tool_search_paths(environment)
    if not search_paths:
        raise FleetPlanInvalid(
            "No absolute executable search paths are available for certification"
        )
    trusted_roots = sorted({path.resolve() for path in search_paths} | {runtime_inspector.parent})

    inventory = state / "executors.toml"
    payload = "\n".join(
        [
            f"version = {EXECUTOR_CONFIG_VERSION}",
            "placement_wait_seconds = 0",
            "placement_queue_limit = 64",
            f"runtime_inspector = {json.dumps(str(runtime_inspector))}",
            "trusted_host_roots = ["
            + ", ".join(json.dumps(str(path)) for path in trusted_roots)
            + "]",
            "tool_search_paths = ["
            + ", ".join(json.dumps(str(path)) for path in search_paths)
            + "]",
            "",
            "[[executors]]",
            'id = "certification-local"',
            "generation = 1",
            'execution = { environment = { backend = "bubblewrap" } }',
            f"bwrap_executable = {json.dumps(str(bwrap))}",
            "priority = 0",
            "cpu_millis = 2000",
            f"memory_mib = {CERTIFICATION_ATTEMPT_MEMORY_MIB}",
            "max_processes = 64",
            f"cgroup_root = {json.dumps(str(cgroup_root))}",
            "",
        ]
    )
    inventory.write_text(payload, encoding="utf-8")
    inventory.chmod(0o600)
    return inventory


def _current_process_cgroup_root() -> Path:
    try:
        unified = next(
            line.split("::", 1)[1]
            for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
            if line.startswith("0::")
        )
    except (OSError, StopIteration, IndexError) as exc:
        raise FleetPlanInvalid(
            "Strict release/CI execution cannot determine this process's cgroup-v2 path"
        ) from exc
    return (Path("/sys/fs/cgroup") / unified.lstrip("/")).resolve()


def resolve_certification_cgroup_root(environment: dict[str, str]) -> Path:
    delegated_subgroup = delegated_process_subgroup(
        environment,
        outer_delegated_env=SYSTEMD_DELEGATED_ENV,
        outer_subgroup_env=SYSTEMD_SUBGROUP_ENV,
    )
    override = environment.get(CERTIFICATION_CGROUP_ROOT_ENV, "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            raise FleetPlanInvalid("OH_GOON_CERTIFICATION_CGROUP_ROOT must be an absolute path")
        candidate = candidate.resolve()
        current = _current_process_cgroup_root()
        if current != candidate and not current.is_relative_to(candidate):
            raise FleetPlanInvalid(
                "OH_GOON_CERTIFICATION_CGROUP_ROOT cannot be used from outside its delegated "
                f"subtree; workflow={current}, delegated-root={candidate}"
            )
    else:
        current = _current_process_cgroup_root()
        if delegated_subgroup is not None:
            subgroup = delegated_subgroup
            if not subgroup:
                raise FleetPlanInvalid("delegated certification execution has no process subgroup")
            if current.name == subgroup:
                candidate = current.parent
            else:
                candidate = current
                placed = place_current_process_in_delegated_cgroup_v2_subgroup(
                    candidate,
                    subgroup=subgroup,
                    required_controllers=REQUIRED_CERTIFICATION_CONTROLLERS,
                )
                observed = _current_process_cgroup_root()
                if observed != placed:
                    raise FleetPlanInvalid(
                        "systemd strict-execution process did not remain inside the delegated "
                        f"process subgroup; expected {placed}, observed {observed}"
                    )
        else:
            candidate = current

    if delegated_subgroup is not None:
        return enable_delegated_cgroup_v2_controllers(
            candidate,
            required_controllers=REQUIRED_CERTIFICATION_CONTROLLERS,
        )
    return require_delegated_cgroup_v2_root(
        candidate,
        required_controllers=REQUIRED_CERTIFICATION_CONTROLLERS,
    )
