"""Native CLI A journeys and prohibited effects across fresh process boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.state import LocalState

ROOT = Path(__file__).resolve().parents[4]
PYTHON_FIXTURE = ROOT / "template-certification/fixtures/python/standard"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None or shutil.which("git") is None,
    reason="native POSIX Git and uv required",
)


def _environment(state: Path) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(state / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(state / "league.yml"),
        XDG_CONFIG_HOME=str(state / "config"),
        XDG_DATA_HOME=str(state / "data"),
        XDG_STATE_HOME=str(state / "state"),
        XDG_CACHE_HOME=str(state / "cache"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
    )
    return environment


def _cli(
    environment: dict[str, str], cwd: Path, *arguments: str, expected: int | tuple[int, ...] = 0
) -> str:
    __tracebackhide__ = True
    executable = Path(sys.executable).with_name("oh-goon")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=180,
        )
        output.seek(0)
        errors.seek(0)
        stdout = output.read(65537)
        stderr = errors.read(65537)
    assert len(stdout) <= 65536 and len(stderr) <= 65536, "Native fixture output exceeded its bound"
    text = stdout.decode("utf-8", errors="replace")
    assert completed.returncode in ((expected,) if isinstance(expected, int) else expected), (
        arguments,
        text,
        stderr.decode("utf-8", errors="replace"),
    )
    return text


def _snapshot(root: Path) -> dict[str, str]:
    """Compare semantic files; native SQLite read sidecars are not domain writes."""
    if not root.exists():
        return {}
    facts = {}
    for path in root.rglob("*"):
        if path.name in {"runtime.sqlite-wal", "runtime.sqlite-shm", "runtime.sqlite-journal"}:
            continue
        if path.is_file():
            with path.open("rb") as stream:
                facts[path.relative_to(root).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return facts


def _database_snapshot(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        names = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {
            name: hashlib.sha256(
                repr(
                    connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                ).encode()
            ).hexdigest()
            for name in names
        }


def test_two_commands_execute_native_python_then_preserve_recorded_run_after_relocation(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    repository = bundle / "app project"
    shutil.copytree(
        PYTHON_FIXTURE,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "junit.xml"),
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    environment = _environment(tmp_path / "original-state")
    environment["UV_CACHE_DIR"] = str(tmp_path / "native-uv-cache")
    with tempfile.TemporaryFile() as diagnostics:
        setup = subprocess.run(
            ["uv", "sync", "--locked", "--project", str(repository), "--group", "dev"],
            env=environment,
            stdout=diagnostics,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
        diagnostics.seek(0, os.SEEK_END)
        diagnostics.seek(max(0, diagnostics.tell() - 8192))
        assert setup.returncode == 0, diagnostics.read(8192).decode("utf-8", errors="replace")
    workflow = bundle / "workflow"
    initialized = json.loads(
        _cli(
            environment,
            repository,
            "init",
            "--template",
            "python/test",
            "--kind",
            "goon",
            "--repository",
            ".",
            "--output",
            str(workflow),
            "--no-input",
            "--json",
        )
    )
    assert initialized["status"] == "ready"
    assert initialized["target_registration"]["status"] == "registered"
    assert (workflow / "goon.lock").is_file()
    # The second task command uses the untouched first canonical lock from another CWD.
    receipt = json.loads(_cli(environment, tmp_path, "run", str(workflow), "--no-input", "--json"))
    run_id = receipt["fleet_run_id"]
    assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
    recorded = {
        view: json.loads(_cli(environment, tmp_path, "runs", view, run_id, "--json"))
        for view in ("show", "steps", "outputs", "tape")
    }
    assert all(view["fleet_run_id"] == run_id for view in recorded.values())
    assert recorded["show"]["outcome"] == "SUCCESS"
    assert recorded["tape"]["status"] == "PASSED"
    assert all(
        step["status"] == "PASSED" and step["exit_code"] == 0
        for step in recorded["steps"]["repositories"][0]["steps"]
    )
    assert b'tests="1"' in (repository / "junit.xml").read_bytes()

    selection = workflow / "run.yml"
    lock_path = workflow / "goon.lock"
    selection_bytes, lock_bytes = selection.read_bytes(), lock_path.read_bytes()
    for fault in ("missing", "tampered", "stale-configuration"):
        if fault == "missing":
            lock_path.unlink()
        elif fault == "tampered":
            lock_path.write_bytes(lock_bytes.replace(b"sha256:", b"sha257:", 1))
        else:
            selection.write_bytes(selection_bytes + b"\n# explicitly changed caller selection\n")
        before_state = _snapshot(tmp_path / "original-state")
        before_state.pop("runtime.sqlite")
        before_database = _database_snapshot(tmp_path / "original-state/runtime.sqlite")
        before_repository = _snapshot(repository)
        before_workflow = _snapshot(workflow)
        refused = json.loads(
            _cli(
                environment,
                tmp_path,
                "run",
                str(workflow),
                "--yes",
                "--no-input",
                "--json",
                expected=2,
            )
        )
        assert refused["status"] in {"rejected", "error"}
        assert refused.get("fleet_run_id") is None
        after_state = _snapshot(tmp_path / "original-state")
        after_state.pop("runtime.sqlite")
        assert after_state == before_state
        assert _database_snapshot(tmp_path / "original-state/runtime.sqlite") == before_database
        assert _snapshot(repository) == before_repository
        assert _snapshot(workflow) == before_workflow
        selection.write_bytes(selection_bytes)
        lock_path.write_bytes(lock_bytes)

    relocated = tmp_path / "relocated"
    bundle.rename(relocated)
    relocated_workflow = relocated / "workflow"
    fresh_state = tmp_path / "relocated-state"
    fresh_environment = _environment(fresh_state)
    fresh_environment["UV_CACHE_DIR"] = environment["UV_CACHE_DIR"]
    before = _snapshot(fresh_state)
    inspected = json.loads(
        _cli(fresh_environment, tmp_path, "workflow", "inspect", str(relocated_workflow), "--json")
    )
    assert inspected["target"]["registration"] == "unavailable"
    assert _snapshot(fresh_state) == before
    refused = json.loads(
        _cli(
            fresh_environment,
            tmp_path,
            "run",
            str(relocated_workflow),
            "--yes",
            "--no-input",
            "--json",
            expected=2,
        )
    )
    assert refused["code"] == "CONFIG_TARGET_UNAVAILABLE"
    assert _snapshot(fresh_state) == before
    prepared = json.loads(
        _cli(
            fresh_environment,
            tmp_path,
            "workflow",
            "prepare",
            str(relocated_workflow),
            "--register-target",
            "--no-input",
            "--json",
        )
    )
    assert prepared["target_registration"]["path"] == str(relocated / "app project")
    assert prepared["target_registration"]["status"] == "registered"
    assert yaml.safe_load((fresh_state / "league.yml").read_text())["folders"] == [
        str(relocated / "app project")
    ]
    # Relocation and a different host scope cannot rewrite the original Run's facts.
    assert (
        json.loads(_cli(environment, tmp_path, "runs", "show", run_id, "--json"))
        == recorded["show"]
    )
    assert (
        json.loads(_cli(environment, tmp_path, "runs", "outputs", run_id, "--json"))
        == recorded["outputs"]
    )


@pytest.mark.parametrize("database", ["absent", "corrupt"])
def test_basic_readonly_commands_do_not_initialize_or_repair_state(
    tmp_path: Path, database: str
) -> None:
    state = tmp_path / "state"
    environment = _environment(state)
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / "run.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": "../repository"},
            }
        )
    )
    (repository / ".env").write_text("PRIVATE_TOKEN=private-env-sentinel\n")
    if database == "corrupt":
        state.mkdir()
        (state / "runtime.sqlite").write_bytes(b"private-corrupt-database-sentinel")
    before_state, before_repository = _snapshot(state), _snapshot(repository)
    for arguments in (
        ("--help",),
        ("--version",),
        ("templates", "list", "--json"),
        ("doctor", "--json"),
    ):
        # Basic doctor may report missing optional host tools; it stays read-only either way.
        if arguments[0] == "doctor":
            tools = json.loads(_cli(environment, tmp_path, *arguments, expected=(0, 2)))
            assert tools["scope"] == "tools"
        else:
            _cli(environment, tmp_path, *arguments)
        assert _snapshot(state) == before_state
    for command in ("inspect", "validate"):
        output = _cli(
            environment,
            tmp_path,
            "workflow",
            command,
            str(workflow),
            "--json",
            expected=2 if database == "corrupt" or command == "validate" else 0,
        )
        assert (
            "private-env-sentinel" not in output
            and "private-corrupt-database-sentinel" not in output
        )
        report = json.loads(output)
        assert report["status"] in {"registration-unavailable", "rejected"}
    after_state = _snapshot(state)
    # Reading an existing database takes its original physical-identity guard.
    # A new empty guard lock is coordination metadata, never database repair.
    for added in set(after_state) - set(before_state):
        assert Path(added).parent == Path(".sqlite-authority")
        assert Path(added).suffix == ".lock"
        assert after_state.pop(added) == hashlib.sha256(b"").hexdigest()
    assert after_state == before_state
    assert _snapshot(repository) == before_repository


@contextmanager
def _denied_git_endpoint() -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Denied(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_error(403)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Denied)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/denied.git", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_unprepared_git_source_never_acquires_from_readonly_or_run_flags(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    environment = _environment(state)
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (state / "league.yml").write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    with _denied_git_endpoint() as (origin, requests):
        (workflow / "run.yml").write_text(
            yaml.safe_dump(
                {
                    "version": 2,
                    "source": {
                        "kind": "git-plan",
                        "git": origin,
                        "ref": "main",
                        "path": ".",
                        "plan": "plan.yml",
                    },
                    "target": {"repository": "../repository"},
                }
            )
        )
        before = _snapshot(tmp_path)
        for arguments in (
            ("workflow", "inspect", str(workflow), "--json"),
            ("workflow", "validate", str(workflow), "--json"),
            ("run", str(workflow), "--yes", "--no-input", "--json"),
        ):
            refused = json.loads(_cli(environment, tmp_path, *arguments, expected=2))
            assert refused["status"] == "rejected"
            assert requests == []
            assert _snapshot(tmp_path) == before
        # A denied explicit acquisition proves the endpoint instrumentation can observe native Git.
        _cli(
            environment,
            tmp_path,
            "workflow",
            "prepare",
            str(workflow),
            "--no-input",
            "--json",
            expected=2,
        )
        assert requests
        assert not (workflow / "goon.lock").exists()
        assert not (state / "runtime.sqlite").exists()


def test_retired_commands_and_v1_selection_refuse_before_state_or_execution(tmp_path: Path) -> None:
    state = tmp_path / "state"
    environment = _environment(state)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    selection = workflow / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository_id": "retired-opaque-target"},
                "inputs": {"private": "PRIVATE_SELECTION_SENTINEL"},
            }
        )
    )
    before = _snapshot(tmp_path)
    for arguments in (
        ("config", "inspect", str(workflow)),
        ("lock", str(workflow)),
        ("templates", "init", "python/quality"),
        ("plan", "run", str(workflow)),
        ("plan", "status", "retained-run-id"),
        ("start",),
        ("serve",),
    ):
        _cli(environment, tmp_path, *arguments, expected=2)
        assert _snapshot(tmp_path) == before
    for arguments in (
        ("workflow", "inspect"),
        ("workflow", "validate"),
        ("workflow", "prepare"),
        ("run",),
    ):
        text = _cli(environment, tmp_path, *arguments, str(workflow), "--json", expected=2)
        report = json.loads(text)
        assert report["status"] in {"error", "rejected"}
        assert "version 2" in report["safe_next_action"]
        assert "PRIVATE_SELECTION_SENTINEL" not in text
        assert _snapshot(tmp_path) == before
        assert not (workflow / "goon.lock").exists()
        assert not (state / "runtime.sqlite").exists()


def _abandoned_original_run(
    state_root: Path,
) -> tuple[LocalDatabase, Journal, LocalState, str, Path, Path]:
    """Seed valid original owners, then an out-of-band preference/projection drift."""
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution import ExecutionCoordinator
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.league_folders import LeagueFolderStore
    from oh_goon.runtime.scope_mutation_guard import DurableScopeMutationGuard
    from oh_goon.runtime.state import LocalState
    from tests.factories.capabilities import admit_test_run
    from tests.factories.journal import start_run

    old_repository = state_root.parent / "old-repository"
    replacement_repository = state_root.parent / "selected-repository"
    old_repository.mkdir()
    replacement_repository.mkdir()
    database = LocalDatabase(state_root / "runtime.sqlite")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    preferences = LeagueFolderStore(state_root / "league.yml")
    state = LocalState(
        database,
        journal=journal,
        league_folders=preferences,
        mutation_guard=DurableScopeMutationGuard(execution, journal),
    )
    state.add_location(old_repository)
    run_id = "original-controller-ended"
    admit_test_run(
        journal,
        run_id,
        OperationAction.INIT,
        [RepositoryIdentity.model_validate({"path": old_repository})],
    )
    start_run(journal, run_id)
    active = journal.active_run()
    assert active is not None and active.fleet_run_id == run_id
    assert state.read_view().locations == (old_repository,)
    preferences.replace((replacement_repository,))
    for name in ("plans", "lineups"):
        directory = state_root / name
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
        (directory / "user-data").write_text("preserve saved authoring")
    return database, journal, state, run_id, old_repository, replacement_repository


@pytest.mark.parametrize("entrypoint", ["ordinary-startup", "runs-readback"])
def test_run_readback_does_not_recover_interrupted_history_or_reconcile_scope(
    tmp_path: Path, entrypoint: str
) -> None:
    state_root = tmp_path / "state"
    database, journal, state, run_id, old_repository, selected_repository = _abandoned_original_run(
        state_root
    )
    environment = _environment(state_root)
    before_database = _database_snapshot(database.path)
    before_files = _snapshot(state_root)
    before_files.pop("runtime.sqlite")
    if entrypoint == "ordinary-startup":
        # The baseline must exercise the recovery/reconciliation mutations that
        # the observer mode is required to suppress, through a real CLI process.
        _cli(environment, tmp_path, "league", "scan", "--json")
        assert journal.active_run() is None
        assert journal.read_view().required_run_detail(run_id).run.status.terminal
        assert state.read_view().locations == (selected_repository,)
        assert _database_snapshot(database.path) != before_database
        return

    for command in ("show", "events", "logs", "steps", "outputs", "tape", "handoff"):
        report = json.loads(_cli(environment, tmp_path, "runs", command, run_id, "--json"))
        if command == "show":
            assert report["status"] == "RUNNING"
        elif command == "logs":
            assert report["available"] is False
        assert _database_snapshot(database.path) == before_database
    campaign = json.loads(
        _cli(environment, tmp_path, "runs", "campaign", run_id, "--json", expected=2)
    )
    assert campaign["status"] == "error"
    exported = tmp_path / "must-not-export.txt"
    _cli(
        environment,
        tmp_path,
        "runs",
        "export-artifact",
        run_id,
        "--step",
        "unrecorded",
        "--path",
        "unrecorded.txt",
        "--output",
        str(exported),
        "--json",
        expected=2,
    )
    assert not exported.exists()
    assert _database_snapshot(database.path) == before_database
    active = journal.active_run()
    assert active is not None and active.fleet_run_id == run_id
    assert state.read_view().locations == (old_repository,)
    after_files = _snapshot(state_root)
    after_files.pop("runtime.sqlite")
    assert after_files == before_files
    assert not (state_root / "content-store").exists()
    assert not (state_root / "diagnostics").exists()
    assert all((state_root / name).stat().st_mode & 0o777 == 0o755 for name in ("plans", "lineups"))


@pytest.mark.parametrize("database_state", ["absent", "incompatible"])
def test_runs_readback_refuses_unavailable_state_without_bootstrap(
    tmp_path: Path, database_state: str
) -> None:
    from oh_goon.runtime.database import LocalDatabase

    state_root = tmp_path / "state"
    path = state_root / "runtime.sqlite"
    if database_state == "incompatible":
        LocalDatabase(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA user_version = 999999")
    before = _snapshot(state_root)
    refused = json.loads(
        _cli(
            _environment(state_root),
            tmp_path,
            "runs",
            "show",
            "unknown-original-run",
            "--json",
            expected=2,
        )
    )
    assert refused["status"] in {"error", "rejected"}
    after = _snapshot(state_root)
    for added in set(after) - set(before):
        assert Path(added).parent == Path(".sqlite-authority")
        assert Path(added).suffix == ".lock"
        assert after.pop(added) == hashlib.sha256(b"").hexdigest()
    assert after == before
    assert not (state_root / "content-store").exists()
    assert not (state_root / "league.yml").exists()


@pytest.mark.parametrize("command", ["init", "prepare"])
def test_registration_does_not_recover_history_to_bypass_original_idle_guard(
    tmp_path: Path, command: str
) -> None:
    state_root = tmp_path / "state"
    database, journal, state, run_id, old_repository, _ = _abandoned_original_run(state_root)
    target = tmp_path / "new-target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    workflow = tmp_path / "workflow"
    arguments: tuple[str, ...]
    if command == "prepare":
        workflow.mkdir()
        (workflow / "run.yml").write_text(
            yaml.safe_dump(
                {
                    "version": 2,
                    "source": {"kind": "catalog-plan", "name": "python/quality"},
                    "target": {"repository": "../new-target"},
                }
            )
        )
        arguments = ("workflow", "prepare", str(workflow), "--register-target")
    else:
        arguments = (
            "init",
            "--template",
            "python/quality",
            "--repository",
            str(target),
            "--output",
            str(workflow),
        )
    before_database = _database_snapshot(database.path)
    before_state, before_target = _snapshot(state_root), _snapshot(target)
    before_state.pop("runtime.sqlite")
    refused = json.loads(
        _cli(_environment(state_root), tmp_path, *arguments, "--no-input", "--json", expected=2)
    )
    assert refused["status"] in {"error", "rejected"}
    assert _database_snapshot(database.path) == before_database
    active = journal.active_run()
    assert active is not None and active.fleet_run_id == run_id
    assert state.read_view().locations == (old_repository,)
    after_state = _snapshot(state_root)
    after_state.pop("runtime.sqlite")
    assert after_state == before_state
    assert _snapshot(target) == before_target
    assert not (workflow / "goon.lock").exists()
    if command == "init":
        assert not workflow.exists()
        assert not list(tmp_path.glob(".oh-goon-init-*"))


def test_missing_run_lock_refuses_before_runtime_recovery_or_scope_reconciliation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    database, journal, state, run_id, old_repository, target = _abandoned_original_run(state_root)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / "run.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": str(target)},
            }
        )
    )
    before_database = _database_snapshot(database.path)
    before_files = _snapshot(state_root)
    before_files.pop("runtime.sqlite")
    refused = json.loads(
        _cli(_environment(state_root), tmp_path, "run", str(workflow), "--json", expected=2)
    )
    assert refused["status"] in {"error", "rejected"}
    assert "missing" in json.dumps(refused).lower()
    assert _database_snapshot(database.path) == before_database
    active = journal.active_run()
    assert active is not None and active.fleet_run_id == run_id
    assert state.read_view().locations == (old_repository,)
    after_files = _snapshot(state_root)
    after_files.pop("runtime.sqlite")
    assert after_files == before_files
    assert not (workflow / "goon.lock").exists()


@pytest.mark.parametrize("source_state", ["locked", "stale-goon"])
def test_workflow_run_cannot_recover_previous_work_to_admit_its_selection(
    tmp_path: Path, source_state: str
) -> None:
    state_root = tmp_path / "state"
    database, journal, state, run_id, old_repository, target = _abandoned_original_run(state_root)
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    goon = package / "goon.yml"
    goon.write_text("steps:\n- run: echo original-source\n")
    (workflow / "plan.yml").write_text("goons:\n- import: general/check\n  from: goons\n")
    (workflow / "run.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository": str(target)},
            }
        )
    )
    environment = _environment(state_root)
    # Existing stateless preparation proves the original source/lock is valid.
    prepared = json.loads(
        _cli(environment, tmp_path, "workflow", "prepare", str(workflow), "--json")
    )
    assert prepared["status"] == "prepared"
    if source_state == "stale-goon":
        goon.write_text("steps:\n- run: echo changed-source\n")
    before_database = _database_snapshot(database.path)
    before_files = _snapshot(state_root)
    before_files.pop("runtime.sqlite")
    before_workflow = _snapshot(workflow)
    refused = json.loads(_cli(environment, tmp_path, "run", str(workflow), "--json", expected=2))
    assert refused["status"] in {"error", "rejected"}
    assert _database_snapshot(database.path) == before_database
    active = journal.active_run()
    assert active is not None and active.fleet_run_id == run_id
    assert state.read_view().locations == (old_repository,)
    after_files = _snapshot(state_root)
    after_files.pop("runtime.sqlite")
    for added in set(after_files) - set(before_files):
        metadata = Path(added)
        assert metadata.parent == Path(".admission-locks")
        assert metadata.name.startswith("admission-") and metadata.suffix == ".lock"
        assert after_files.pop(added) == hashlib.sha256(b"").hexdigest()
    assert after_files == before_files
    assert _snapshot(workflow) == before_workflow
