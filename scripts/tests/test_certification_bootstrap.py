from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup_resources
from oh_goon.services import certification_bootstrap


def _source_with_lock(root: Path) -> Path:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    return root


def test_bootstrap_rejects_invalid_source_before_creating_control_state(tmp_path) -> None:
    source = _source_with_lock(tmp_path / "repo")
    (source / "repair.patch").write_text("transport only\n", encoding="utf-8")
    state_home = tmp_path / "state-home"

    with pytest.raises(certification_bootstrap.CertificationSourceError) as exc:
        certification_bootstrap.prepare_certification_bootstrap(
            source_root=source,
            state_home=state_home,
            environment={},
        )

    assert "repository source layout is invalid" in str(exc.value)
    assert not state_home.exists()


def test_existing_executor_inventory_is_validated_by_runtime_owner(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    state_home = tmp_path / "state-home"
    inventory = tmp_path / "operator-executors.toml"
    inventory.write_text("version = 6\n", encoding="utf-8")
    observed: list[Path] = []

    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-test")
    monkeypatch.setattr(
        certification_bootstrap,
        "load_executor_pool",
        lambda path: observed.append(path),
    )

    context = certification_bootstrap.prepare_certification_bootstrap(
        source_root=source,
        state_home=state_home,
        environment={"OH_GOON_EXECUTORS_FILE": str(inventory)},
        require_strict_executor=True,
    )

    assert context.repository_id == "repo-test"
    assert context.executors_file == inventory.resolve()
    assert context.database == context.state_root / "oh-goon.db"
    assert context.league_file == context.state_root / "league.yml"
    assert context.lock_root == context.state_root / "locks"
    assert context.lock_root.is_dir()
    assert observed == [inventory.resolve()]
    assert f"schema-{certification_bootstrap.DATABASE_SCHEMA_VERSION}" in str(context.state_root)


def test_app_bootstrap_does_not_discover_validate_or_materialize_strict_executor(
    tmp_path, monkeypatch
) -> None:
    source = _source_with_lock(tmp_path / "repo")
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-app")
    monkeypatch.setattr(
        certification_bootstrap,
        "load_executor_pool",
        lambda _path: (_ for _ in ()).throw(AssertionError("APP must not validate strict executor authority")),
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "materialize_local_executor_inventory",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("APP must not materialize strict executor authority")),
    )

    context = certification_bootstrap.prepare_certification_bootstrap(
        source_root=source,
        state_home=tmp_path / "state-home",
        environment={"OH_GOON_EXECUTORS_FILE": "relative-and-invalid.toml"},
    )

    assert context.repository_id == "repo-app"
    assert context.executors_file is None
    assert "executors_file" not in context.as_payload()
    assert context.database == context.state_root / "oh-goon.db"
    assert context.lock_root == context.state_root / "locks"


def test_bootstrap_cli_parser_keeps_strict_executor_authority_opt_in(tmp_path) -> None:
    from oh_goon.cli_parser import build_parser

    state = str(tmp_path / "state")
    app = build_parser().parse_args(
        ["dev", "bootstrap-certification", "--state-root", state]
    )
    release = build_parser().parse_args(
        [
            "dev",
            "bootstrap-certification",
            "--state-root",
            state,
            "--require-strict-executor",
        ]
    )

    assert app.require_strict_executor is False
    assert release.require_strict_executor is True


def test_existing_state_must_be_actually_writable_before_certification_admission(
    tmp_path, monkeypatch
) -> None:
    source = _source_with_lock(tmp_path / "repo")
    inventory = tmp_path / "operator-executors.toml"
    inventory.write_text("version = 6\n", encoding="utf-8")
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-test")
    monkeypatch.setattr(certification_bootstrap, "load_executor_pool", lambda _path: None)

    def fail_probe(_path):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(certification_bootstrap, "probe_writable_directory", fail_probe)
    with pytest.raises(certification_bootstrap.CertificationEnvironmentError) as exc:
        certification_bootstrap.prepare_certification_bootstrap(
            source_root=source,
            state_home=tmp_path / "cache",
            environment={"OH_GOON_EXECUTORS_FILE": str(inventory)},
            require_strict_executor=True,
        )
    assert "mutable state is not writable" in str(exc.value)
    assert "Read-only file system" in str(exc.value)

def test_invalid_operator_inventory_is_environment_failure(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    inventory = tmp_path / "bad.toml"
    inventory.write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-test")
    monkeypatch.setattr(
        certification_bootstrap,
        "load_executor_pool",
        lambda _path: (_ for _ in ()).throw(FleetPlanInvalid("invalid executor inventory")),
    )

    with pytest.raises(certification_bootstrap.CertificationEnvironmentError) as exc:
        certification_bootstrap.prepare_certification_bootstrap(
            source_root=source,
            state_home=tmp_path / "cache",
            environment={"OH_GOON_EXECUTORS_FILE": str(inventory)},
            require_strict_executor=True,
        )
    assert "invalid executor inventory" in str(exc.value)


def test_local_inventory_is_materialized_then_reparsed_by_runtime_owner(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    observed: list[Path] = []
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-test")
    monkeypatch.setattr(
        certification_bootstrap,
        "materialize_local_executor_inventory",
        lambda *, state, environment: state / "executors.toml",
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "load_executor_pool",
        lambda path: observed.append(path),
    )

    context = certification_bootstrap.prepare_certification_bootstrap(
        source_root=source,
        state_home=tmp_path / "cache",
        environment={},
        require_strict_executor=True,
    )

    assert context.executors_file == context.state_root / "executors.toml"
    assert context.lock_root == context.state_root / "locks"
    assert observed == [context.executors_file]


def test_systemd_reentry_validates_unit_root_through_canonical_cgroup_owner(monkeypatch) -> None:
    current = Path("/sys/fs/cgroup/user.slice/user-1000.slice/run-r123.service/oh-goon-workflow")
    observed: list[tuple[Path, set[str]]] = []
    monkeypatch.setattr(certification_bootstrap, "_current_process_cgroup_root", lambda: current)

    def validate(root: Path, *, required_controllers: set[str]) -> Path:
        observed.append((root, required_controllers))
        return root

    monkeypatch.setattr(
        certification_bootstrap,
        "enable_delegated_cgroup_v2_controllers",
        validate,
    )

    root = certification_bootstrap.resolve_certification_cgroup_root(
        {
            certification_bootstrap.SYSTEMD_DELEGATED_ENV: "1",
            certification_bootstrap.SYSTEMD_SUBGROUP_ENV: "oh-goon-workflow",
        }
    )

    assert root == current.parent
    assert observed == [(current.parent, {"cpu", "memory", "pids"})]


def test_systemd_reentry_self_places_process_before_enabling_controllers(monkeypatch) -> None:
    unit = Path("/sys/fs/cgroup/user.slice/user-1000.slice/run-r123.service")
    child = unit / "oh-goon-workflow"
    observed_roots = iter((unit, child))
    monkeypatch.setattr(
        certification_bootstrap,
        "_current_process_cgroup_root",
        lambda: next(observed_roots),
    )
    placements: list[tuple[Path, str, set[str]]] = []
    enabled: list[tuple[Path, set[str]]] = []

    def place(root: Path, *, subgroup: str, required_controllers: set[str]) -> Path:
        placements.append((root, subgroup, required_controllers))
        return child

    def enable(root: Path, *, required_controllers: set[str]) -> Path:
        enabled.append((root, required_controllers))
        return root

    monkeypatch.setattr(
        certification_bootstrap,
        "place_current_process_in_delegated_cgroup_v2_subgroup",
        place,
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "enable_delegated_cgroup_v2_controllers",
        enable,
    )

    root = certification_bootstrap.resolve_certification_cgroup_root(
        {
            certification_bootstrap.SYSTEMD_DELEGATED_ENV: "1",
            certification_bootstrap.SYSTEMD_SUBGROUP_ENV: "oh-goon-workflow",
        }
    )

    required = {"cpu", "memory", "pids"}
    assert root == unit
    assert placements == [(unit, "oh-goon-workflow", required)]
    assert enabled == [(unit, required)]


def test_nested_strict_reentry_self_places_process_before_enabling_controllers(monkeypatch) -> None:
    delegated = Path("/sys/fs/cgroup/nested-execution")
    child = delegated / "oh-goon-nested-process"
    observed_roots = iter((delegated, child))
    monkeypatch.setattr(
        certification_bootstrap,
        "_current_process_cgroup_root",
        lambda: next(observed_roots),
    )
    placements: list[tuple[Path, str, set[str]]] = []
    enabled: list[tuple[Path, set[str]]] = []

    def place(root: Path, *, subgroup: str, required_controllers: set[str]) -> Path:
        placements.append((root, subgroup, required_controllers))
        return child

    def enable(root: Path, *, required_controllers: set[str]) -> Path:
        enabled.append((root, required_controllers))
        return root

    monkeypatch.setattr(
        certification_bootstrap,
        "place_current_process_in_delegated_cgroup_v2_subgroup",
        place,
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "enable_delegated_cgroup_v2_controllers",
        enable,
    )

    root = certification_bootstrap.resolve_certification_cgroup_root(
        {
            cgroup_resources.NESTED_CGROUP_DELEGATED_ENV: "1",
            cgroup_resources.NESTED_CGROUP_SUBGROUP_ENV: "oh-goon-nested-process",
        }
    )

    required = {"cpu", "memory", "pids"}
    assert root == delegated
    assert placements == [(delegated, "oh-goon-nested-process", required)]
    assert enabled == [(delegated, required)]


def test_executor_unavailable_message_never_suggests_a_weaker_fallback() -> None:
    message = certification_bootstrap.certification_executor_unavailable(
        "systemd user manager is unavailable"
    )
    assert message.startswith("CERTIFICATION EXECUTOR UNAVAILABLE")
    assert "OH_GOON_EXECUTORS_FILE" in message
    assert "Delegate=yes" in message
    assert "make strict-executor-setup" in message
    assert "process-only" not in message
    assert "ignore" not in message.lower()


def test_explicit_certification_cgroup_root_cannot_pull_workflow_across_delegation_boundary(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        certification_bootstrap,
        "_current_process_cgroup_root",
        lambda: Path("/sys/fs/cgroup/init.scope"),
    )

    with pytest.raises(FleetPlanInvalid) as exc:
        certification_bootstrap.resolve_certification_cgroup_root(
            {"OH_GOON_CERTIFICATION_CGROUP_ROOT": "/sys/fs/cgroup/operator-delegated"}
        )

    message = str(exc.value)
    assert "outside its delegated subtree" in message
    assert "init.scope" in message
    assert "operator-delegated" in message


def test_missing_committed_backend_lock_is_source_failure(tmp_path) -> None:
    source = tmp_path / "repo"
    source.mkdir()

    with pytest.raises(certification_bootstrap.CertificationSourceError) as exc:
        certification_bootstrap.prepare_certification_bootstrap(
            source_root=source, state_home=tmp_path / "cache", environment={}
        )

    assert "backend/uv.lock" in str(exc.value)
    assert "committed backend dependency lock is missing" in str(exc.value)


def test_local_certification_reserves_attempt_plus_control_plane_memory(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(certification_bootstrap, "required_certification_tool", lambda name: tmp_path / name)
    monkeypatch.setattr(
        certification_bootstrap,
        "resolve_certification_cgroup_root",
        lambda _environment: tmp_path / "cgroup",
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "observe_local_executor_capacity",
        lambda _root: type("Capacity", (), {
            "cpu_millis": 8000,
            "memory_bytes": 4096 * 1024 * 1024,
            "max_processes": 512,
        })(),
    )

    with pytest.raises(FleetPlanInvalid) as exc:
        certification_bootstrap.materialize_local_executor_inventory(
            state=state, environment={"PATH": str(tmp_path)}
        )

    message = str(exc.value)
    assert "5120 MiB effective memory capacity" in message
    assert "observed 4096 MiB" in message
    assert "cgroup hierarchy" in message
    assert "strict release/CI attempt owns 4096 MiB" in message
    assert "reserves 1024 MiB headroom" in message


def test_local_certification_inventory_advertises_four_gib_attempt_memory(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    for name in ("bwrap", "ldd"):
        tool = tmp_path / name
        tool.write_text("tool", encoding="utf-8")
    monkeypatch.setattr(certification_bootstrap, "required_certification_tool", lambda name: tmp_path / name)
    monkeypatch.setattr(
        certification_bootstrap,
        "resolve_certification_cgroup_root",
        lambda _environment: tmp_path / "cgroup",
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "observe_local_executor_capacity",
        lambda _root: type("Capacity", (), {
            "cpu_millis": 8000,
            "memory_bytes": 16 * 1024 * 1024 * 1024,
            "max_processes": 512,
        })(),
    )

    inventory = certification_bootstrap.materialize_local_executor_inventory(
        state=state, environment={"PATH": str(tmp_path)}
    )
    text = inventory.read_text(encoding="utf-8")
    assert "memory_mib = 4096" in text
    assert "cpu_millis = 2000" in text
    assert "max_processes = 64" in text


def test_corrupt_certification_database_fails_before_executor_admission(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    state_home = tmp_path / "state-home"
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-corrupt")
    state = certification_bootstrap.certification_state_root(
        source_root=source,
        state_home=state_home,
    )
    state.mkdir(parents=True, exist_ok=True)
    (state / "oh-goon.db").write_bytes(b"this is not sqlite")

    with pytest.raises(certification_bootstrap.CertificationEnvironmentError) as exc:
        certification_bootstrap.prepare_certification_bootstrap(
            source_root=source,
            state_home=state_home,
            environment={},
        )

    message = str(exc.value)
    assert "Certification control journal is not safely reusable" in message
    assert "SQLite could not read the local database" in message
    assert "make certification-state-reset" in message
    assert "Native dependency materialization is separate" in message


def test_certification_state_lease_refuses_second_controller(tmp_path) -> None:
    state = tmp_path / "state"
    with certification_bootstrap.certification_state_lease(state):
        with pytest.raises(certification_bootstrap.CertificationStateBusy):
            with certification_bootstrap.certification_state_lease(state):
                raise AssertionError("second certification controller must never acquire the lease")


def test_certification_state_reset_preserves_retained_dependency_supply(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    state_home = tmp_path / "state-home"
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-reset")
    state = certification_bootstrap.certification_state_root(
        source_root=source,
        state_home=state_home,
    )
    state.mkdir(parents=True, exist_ok=True)
    database = state / "oh-goon.db"
    database.write_bytes(b"broken sqlite bytes")
    Path(f"{database}-journal").write_bytes(b"journal")

    cache = tmp_path / "cache"
    supply = cache / "oh-goon" / "certification-dependencies" / "schema-8" / "current"
    supply.mkdir(parents=True, exist_ok=True)
    sentinel = supply / "manifest.json"
    sentinel.write_text('{"sealed": true}\n', encoding="utf-8")

    deleted = certification_bootstrap.reset_certification_control_state(
        source_root=source,
        state_home=state_home,
    )

    assert database in deleted
    assert Path(f"{database}-journal") in deleted
    assert not database.exists()
    assert sentinel.read_text(encoding="utf-8") == '{"sealed": true}\n'


def test_unclassified_cache_bytes_are_not_certification_control_authority(tmp_path, monkeypatch) -> None:
    source = _source_with_lock(tmp_path / "repo")
    state_home = tmp_path / "state-home"
    cache = tmp_path / "cache"
    inventory = tmp_path / "operator-executors.toml"
    inventory.write_text("version = 6\n", encoding="utf-8")
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-state-split")
    monkeypatch.setattr(certification_bootstrap, "load_executor_pool", lambda _path: None)

    unclassified = cache / "unclassified" / "state"
    unclassified.mkdir(parents=True, exist_ok=True)
    (unclassified / "oh-goon.db").write_bytes(b"unclassified bytes are not control state")

    context = certification_bootstrap.prepare_certification_bootstrap(
        source_root=source,
        state_home=state_home,
        environment={"OH_GOON_EXECUTORS_FILE": str(inventory)},
        require_strict_executor=True,
    )

    assert str(context.state_root).startswith(str(state_home.resolve()))
    assert not str(context.state_root).startswith(str(cache.resolve()))
    assert context.database != unclassified / "oh-goon.db"


def test_certification_database_preflight_initializes_reopens_and_quick_checks(
    tmp_path, monkeypatch
) -> None:
    source = _source_with_lock(tmp_path / "repo-preflight")
    state_home = tmp_path / "state-home"
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-preflight")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_type", lambda _path: "ext4")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_is_local", lambda _path: True)

    payload = certification_bootstrap.preflight_certification_database(
        source_root=source,
        state_home=state_home,
    )

    database = Path(payload["database"])
    assert database.is_file()
    assert payload["schema"] == certification_bootstrap.DATABASE_SCHEMA_VERSION
    assert payload["journal_mode"] == "DELETE"
    assert payload["quick_check"] == "ok"
    assert payload["integrity_check"] == "ok"
    assert payload["foreign_key_check"] == "ok"
    assert payload["reopen_proofs"] == certification_bootstrap.CERTIFICATION_DATABASE_INDEPENDENT_REOPEN_PROOFS
    assert payload["admitted_read_proofs"] == certification_bootstrap.CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS
    assert payload["compressed_observation_seconds"] == 150.0
    assert payload["runtime_preparation_observer"] == "persistent-query-only-connection-per-preparation"
    assert payload["runtime_observation_reproof_attempts"] == 3
    inspection = certification_bootstrap.inspect_database_path(
        database, integrity_check=True, foreign_key_check=True
    )
    assert inspection.status is certification_bootstrap.LocalStateCompatibility.READY



def test_certification_database_preflight_detects_same_inode_notadb_during_endurance(
    tmp_path, monkeypatch
) -> None:
    source = _source_with_lock(tmp_path / "repo-preflight-corrupt")
    state_home = tmp_path / "state-home"
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-preflight-corrupt")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_type", lambda _path: "ext4")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_is_local", lambda _path: True)

    original = certification_bootstrap.LocalDatabase.durable_revision
    calls = 0
    corrupted_inode = None

    def corrupt_same_inode_then_read(database_owner):
        nonlocal calls, corrupted_inode
        calls += 1
        if calls == 8:
            before = database_owner.path.stat()
            with database_owner.path.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"NOTADB-SAME-INODE")
                handle.flush()
            after = database_owner.path.stat()
            assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            corrupted_inode = after.st_ino
        return original(database_owner)

    monkeypatch.setattr(
        certification_bootstrap.LocalDatabase,
        "durable_revision",
        corrupt_same_inode_then_read,
    )

    with pytest.raises(
        certification_bootstrap.CertificationEnvironmentError,
        match="endurance preflight failed before APP admission",
    ) as exc:
        certification_bootstrap.preflight_certification_database(
            source_root=source,
            state_home=state_home,
        )

    assert corrupted_inode is not None
    assert "file is not a database" in str(exc.value).lower()
    assert calls < certification_bootstrap.CERTIFICATION_DATABASE_ADMITTED_READ_PROOFS


def test_certification_database_preflight_rejects_nonlocal_storage_before_sqlite_work(
    tmp_path, monkeypatch
) -> None:
    source = _source_with_lock(tmp_path / "repo-preflight")
    state_home = tmp_path / "state-home"
    monkeypatch.setattr(certification_bootstrap, "repository_id", lambda _root: "repo-preflight")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_type", lambda _path: "drvfs")
    monkeypatch.setattr(certification_bootstrap, "database_filesystem_is_local", lambda _path: False)

    with pytest.raises(certification_bootstrap.CertificationEnvironmentError, match="local Linux filesystem"):
        certification_bootstrap.preflight_certification_database(
            source_root=source,
            state_home=state_home,
        )
