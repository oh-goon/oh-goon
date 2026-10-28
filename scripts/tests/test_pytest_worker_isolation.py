from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.repository_tooling.pytest_resource_budget import (
    resolve_pytest_resource_budget,
)

pytestmark = pytest.mark.process


_OWNED_FIXTURE = r"""
import contextlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def owned_resources(tmp_path, monkeypatch, request):
    control = Path(os.environ["OH_GOON_ISOLATION_CONTROL"])
    case = request.node.path.stem
    assert os.environ.get("OH_GOON_CASE_OWNER") is None
    assert os.environ["HOME"] == str(control.parent / "caller-home")
    resource = tmp_path
    resource.mkdir(exist_ok=True)
    guard = resource / "active-owner"
    try:
        guard.mkdir()
    except FileExistsError:
        (control / "collision").write_text(case)
        pytest.fail("native test-owned resource collision")
    child = None
    unix_path = resource / "control.sock"
    try:
        home = resource / "home"
        state = resource / "state"
        config = resource / "config"
        for path in (home, state, config):
            path.mkdir(mode=0o700)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        monkeypatch.setenv("OH_GOON_CASE_OWNER", case)
        with contextlib.closing(sqlite3.connect(state / "journal.sqlite")) as database:
            database.execute("CREATE TABLE identity (owner TEXT NOT NULL)")
            database.execute("INSERT INTO identity VALUES (?)", (case,))
            database.commit()
            database.execute("BEGIN IMMEDIATE")
            with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as unix:
                tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp.bind(("127.0.0.1", 0))
                tcp.listen()
                unix.bind(str(unix_path))
                unix.listen()
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                started = time.monotonic_ns()
                active = control / (case + ".active.tmp")
                active.write_text(json.dumps({
                    "resource": str(resource), "pid": os.getpid(), "child": child.pid,
                    "port": tcp.getsockname()[1], "socket": str(unix_path),
                    "database": str(state / "journal.sqlite"),
                }))
                active.replace(control / (case + ".active.json"))
                (control / (case + ".ready")).write_text(str(started))
                deadline = time.monotonic() + 10
                target = int(os.environ.get("OH_GOON_ISOLATION_OVERLAP", "2"))
                while len(tuple(control.glob("*.ready"))) < target:
                    if (control / "collision").exists():
                        break
                    assert time.monotonic() < deadline, "concurrent workers were not admitted"
                    time.sleep(0.01)
                with socket.create_connection(tcp.getsockname(), timeout=2) as client:
                    client.sendall(case.encode())
                    peer, _ = tcp.accept()
                    with peer:
                        assert peer.recv(128).decode() == case
                assert database.execute("SELECT owner FROM identity").fetchall() == [(case,)]
                (resource / "generated.json").write_text(json.dumps({"owner": case}))
                yield case
                assert json.loads((resource / "generated.json").read_text()) == {"owner": case}
                assert os.environ["OH_GOON_CASE_OWNER"] == case
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        unix_path.unlink(missing_ok=True)
        guard.rmdir()
        (control / (case + ".json")).write_text(json.dumps({
            "resource": str(resource),
            "pid": os.getpid(),
            "child": child.pid if child is not None else None,
            "child_returncode": child.returncode if child is not None else None,
            "started": started if child is not None else None,
            "finished": time.monotonic_ns(),
            "socket_released": not unix_path.exists(),
            "guard_released": not guard.exists(),
        }))
"""


def _subject(tmp_path: Path) -> tuple[Path, tuple[Path, ...]]:
    project = tmp_path / "subject"
    project.mkdir(mode=0o700)
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n'
        '[tool.coverage.run]\nbranch = true\nsource = ["subject"]\n'
    )
    (project / "conftest.py").write_text(_OWNED_FIXTURE)
    (project / "subject.py").write_text(
        "def choose(case):\n"
        "    if case == 0: return 0\n"
        "    if case == 1: return 1\n"
        "    if case == 2: return 2\n"
        "    return 3\n"
    )
    members = tuple(project / f"test_case_{index}.py" for index in range(4))
    for index, member in enumerate(members):
        member.write_text(
            "from subject import choose\n"
            "def test_owned(owned_resources):\n"
            f"    assert choose({index}) == {index}\n"
        )
    return project, members


def _command(
    project: Path,
    members: tuple[Path, ...],
    control: Path,
    *,
    coverage: bool = False,
    batch_size: int = 1,
    workers: int = 2,
    batch_timeout: int = 30,
) -> list[str]:
    report = control / "report.json"
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        *(str(member) for member in members),
        "--qualified",
        "--pytest-config",
        str(project / "pyproject.toml"),
        "--granularity",
        "files",
        "--batch-size",
        str(batch_size),
        "--workers",
        str(workers),
        "--batch-timeout",
        str(batch_timeout),
        "--marker",
        "",
        "--resume-state-root",
        str(control / "receipts"),
        "--report-path",
        str(report),
    ]
    if coverage:
        command += [
            "--coverage-source=subject",
            "--coverage-data-file",
            str(control / ".coverage"),
            "--coverage-report=json:" + str(control / "coverage.json"),
            "--coverage-fail-under=100",
        ]
    return command


def _environment(control: Path, temp_root: str, *, overlap: int = 2) -> dict[str, str]:
    control.mkdir(mode=0o700)
    home = control.parent / "caller-home"
    home.mkdir(mode=0o700, exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "PYTEST_DEBUG_TEMPROOT": temp_root,
        "OH_GOON_ISOLATION_CONTROL": str(control),
        "OH_GOON_ISOLATION_OVERLAP": str(overlap),
        "OH_GOON_TEST_CPUS": "2",
    }


def _run(
    project: Path,
    members: tuple[Path, ...],
    control: Path,
    *,
    coverage: bool = False,
    batch_size: int = 1,
    workers: int = 2,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="hgwi-") as temp_root:
        result = subprocess.run(
            _command(
                project,
                members,
                control,
                coverage=coverage,
                batch_size=batch_size,
                workers=workers,
            ),
            cwd=project,
            env=_environment(control, temp_root, overlap=min(workers, len(members))),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    return result, json.loads((control / "report.json").read_text())


def _require_two_workers() -> None:
    _require_native_sockets()
    if resolve_pytest_resource_budget(2).worker_ceiling < 2:
        pytest.skip("native admission cannot establish two-worker concurrency")


def _require_native_sockets() -> None:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        pytest.skip("native POSIX socket/process qualification is unavailable")


def test_native_workers_isolate_owned_fixtures_and_union_coverage(
    tmp_path: Path,
) -> None:
    _require_two_workers()
    project, members = _subject(tmp_path)
    control = tmp_path / "baseline"
    result, payload = _run(project, members, control, coverage=True, batch_size=2)
    assert result.returncode == 0, result.stdout[-8192:] + result.stderr[-8192:]
    assert payload["status"] == "PASS"
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["resource_budget"]["effective_workers"] == 2
    membership = payload["membership_receipt"]
    assert isinstance(membership, dict)
    assert membership["selected"] == [member.name for member in members]
    assert membership["passed"] == membership["selected"]
    assert (
        not membership["missing"]
        and not membership["duplicates"]
        and not membership["extras"]
    )
    facts = [
        json.loads((control / (member.stem + ".json")).read_text())
        for member in members
    ]
    assert len({fact["resource"] for fact in facts}) == len(members)
    assert len({fact["pid"] for fact in facts}) == 2
    assert any(
        left["pid"] != right["pid"]
        and max(left["started"], right["started"])
        < min(left["finished"], right["finished"])
        for left in facts
        for right in facts
    )
    assert all(fact["child_returncode"] is not None for fact in facts)
    assert all(fact["socket_released"] and fact["guard_released"] for fact in facts)
    assert (
        json.loads((control / "coverage.json").read_text())["totals"]["percent_covered"]
        == 100
    )


def test_native_shared_fixture_collision_is_a_failure_with_complete_accounting(
    tmp_path: Path,
) -> None:
    _require_two_workers()
    project, members = _subject(tmp_path)
    baseline, baseline_payload = _run(project, members[:2], tmp_path / "baseline")
    assert baseline.returncode == 0, baseline.stdout[-8192:] + baseline.stderr[-8192:]
    assert baseline_payload["status"] == "PASS"
    fixture = project / "conftest.py"
    fixture.write_text(
        _OWNED_FIXTURE.replace(
            "resource = tmp_path",
            'resource = Path(os.environ["PYTEST_DEBUG_TEMPROOT"]) / "shared"',
        )
    )
    control = tmp_path / "collision"
    result, payload = _run(project, members[:2], control)
    assert result.returncode != 0
    assert "native test-owned resource collision" in result.stdout
    assert payload["status"] == "FAIL"
    membership = payload["membership_receipt"]
    assert isinstance(membership, dict)
    assert membership["completed"] == membership["selected"]
    assert membership["failed"]
    assert not membership["terminal_success"]
    assert (
        not membership["missing"]
        and not membership["duplicates"]
        and not membership["extras"]
    )
    settled_paths = [control / (member.stem + ".json") for member in members[:2]]
    settled = [json.loads(path.read_text()) for path in settled_paths if path.exists()]
    assert settled
    assert all(fact["guard_released"] and fact["socket_released"] for fact in settled)
    assert all(fact["child_returncode"] is not None for fact in settled)


def _assert_no_live_process(pid: int) -> None:
    status = Path(f"/proc/{pid}/stat")
    if status.exists():
        try:
            assert status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        except FileNotFoundError:
            pass
        return
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("settlement", ("timeout", "interrupt"))
def test_rejected_batch_releases_live_fixture_process_and_socket_authority(
    tmp_path: Path, settlement: str
) -> None:
    _require_native_sockets()
    project, members = _subject(tmp_path)
    baseline, _ = _run(project, members[:1], tmp_path / "baseline", workers=1)
    assert baseline.returncode == 0, baseline.stdout[-8192:] + baseline.stderr[-8192:]
    members[0].write_text(
        "import time\ndef test_owned(owned_resources): time.sleep(60)\n"
    )
    control = tmp_path / "rejected"
    facts: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="hgwi-") as temp_root:
        environment = _environment(control, temp_root, overlap=1)
        with (control / "console.log").open("w") as output:
            process = subprocess.Popen(
                _command(
                    project,
                    members[:1],
                    control,
                    workers=1,
                    batch_timeout=5 if settlement == "timeout" else 30,
                ),
                cwd=project,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                active = control / (members[0].stem + ".active.json")
                deadline = time.monotonic() + 20
                while not active.exists():
                    assert process.poll() is None, (control / "console.log").read_text()
                    assert time.monotonic() < deadline, (
                        "fixture never acquired live resources"
                    )
                    time.sleep(0.01)
                facts = json.loads(active.read_text())
                if settlement == "interrupt":
                    process.send_signal(signal.SIGINT)
                process.wait(timeout=8)
                assert process.returncode != 0
                report = control / "report.json"
                if report.exists():
                    payload = json.loads(report.read_text())
                    assert payload["status"] != "PASS"
                    assert not payload["membership_receipt"]["terminal_success"]
                    assert payload["membership_receipt"]["missing"]
                if settlement == "interrupt":
                    assert process.returncode in (130, -signal.SIGINT)
            finally:
                if process.poll() is None:
                    pytest_pid = facts.get("pid")
                    if isinstance(pytest_pid, int):
                        try:
                            os.killpg(pytest_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.kill()
                    process.wait(timeout=5)
        assert isinstance(facts["pid"], int)
        assert isinstance(facts["child"], int)
        _assert_no_live_process(facts["pid"])
        _assert_no_live_process(facts["child"])
        assert isinstance(facts["port"], int)
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", facts["port"]))
        with socket.socket(socket.AF_UNIX) as probe:
            with pytest.raises((ConnectionRefusedError, FileNotFoundError)):
                probe.connect(str(facts["socket"]))
        with closing(sqlite3.connect(str(facts["database"]), timeout=0.1)) as database:
            database.execute("BEGIN IMMEDIATE")
            database.rollback()
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
