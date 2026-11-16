"""Concurrent public CLIs retain their original state-domain and Run owners.

The callable journeys also accept an installed command. Their controller may
import source dependencies; every CLI child gets an explicit clean environment
and executes only its selected command. No installer or execution owner is added.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any, BinaryIO

import pytest

from oh_goon.core.models.run_events import ProcessStartedEvent
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment
from oh_goon.runtime.journal import Journal

pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux" or any(shutil.which(tool) is None for tool in ("git", "uv")),
        reason="native Linux PIDfds and git/uv required",
    ),
]
_CAPTURE_BYTES = 1024 * 1024
_WORKLOAD = """import json,os,sys,time
from pathlib import Path
marker,release,effect,label=map(str,sys.argv[1:])
with Path(effect).open('a') as stream:
    stream.write(label+'\\n')
temporary=Path(marker+'.tmp')
temporary.write_text(json.dumps({'pid':os.getpid(),'cwd':str(Path.cwd())}))
temporary.replace(marker)
deadline=time.monotonic()+25
while not Path(release).exists():
    if time.monotonic()>=deadline:
        raise TimeoutError('bounded concurrency workload release unavailable')
    time.sleep(0.02)
print('native concurrency workload completed',flush=True)
"""


@dataclass(frozen=True)
class CliCommandFact:
    argv: tuple[str, ...]
    exit_code: int
    duration_ms: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True)
class NativeRunFact:
    run_id: str
    worker: ProcessBirthIdentity
    command: ProcessBirthIdentity
    application: ProcessBirthIdentity
    physical_execution_id: str


@dataclass(frozen=True)
class CliConcurrencyFacts:
    scenario: str
    state_databases: tuple[str, ...]
    runs: tuple[NativeRunFact, ...]
    outcomes: tuple[str, ...]
    busy_code: str | None
    simultaneous_live: bool
    commands: tuple[CliCommandFact, ...]
    scope: str
    step_statuses: tuple[str, ...] = ()
    interruption_receipt_cleanup: str | None = None


@dataclass
class _Capture:
    process: subprocess.Popen[bytes]
    output: BinaryIO
    errors: BinaryIO
    argv: tuple[str, ...]
    started: float


class _CliCase:
    """Own only test control processes, captures and exact native PIDfds."""

    def __init__(self, command: Path, root: Path) -> None:
        self.command = command
        self.root = root
        self.captures: list[_Capture] = []
        self.handles: dict[int, int] = {}
        self.commands: list[CliCommandFact] = []
        self.runs: list[NativeRunFact] = []
        root.mkdir(parents=True, exist_ok=True)

    def start(self, arguments: tuple[str, ...], environment: Mapping[str, str]) -> _Capture:
        env = dict(environment)
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        index = len(self.captures)
        output = (self.root / f"cli-{index}.stdout").open("w+b")
        errors = (self.root / f"cli-{index}.stderr").open("w+b")
        argv = (str(self.command), *arguments)
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
            )
        except BaseException:
            output.close()
            errors.close()
            raise
        capture = _Capture(process, output, errors, argv, monotonic())
        self.captures.append(capture)
        return capture

    def finish(self, capture: _Capture, *, expected: int = 0) -> dict[str, Any]:
        code = capture.process.wait(timeout=45)
        capture.output.seek(0)
        capture.errors.seek(0)
        output, errors = (
            capture.output.read(_CAPTURE_BYTES + 1),
            capture.errors.read(_CAPTURE_BYTES + 1),
        )
        assert len(output) <= _CAPTURE_BYTES and len(errors) <= _CAPTURE_BYTES
        self.commands.append(
            CliCommandFact(
                capture.argv,
                code,
                int((monotonic() - capture.started) * 1000),
                hashlib.sha256(output).hexdigest(),
                hashlib.sha256(errors).hexdigest(),
            )
        )
        assert code == expected, (capture.argv, code, output[-8192:], errors[-8192:])
        value = json.loads(output)
        assert isinstance(value, dict)
        return value

    def cli(
        self, arguments: tuple[str, ...], env: Mapping[str, str], *, expected: int = 0
    ) -> dict[str, Any]:
        return self.finish(self.start((*arguments, "--json"), env), expected=expected)

    def prepare(self, label: str, repository: Path, env: Mapping[str, str]) -> Path:
        repository.mkdir(parents=True, exist_ok=True)
        if not (repository / ".git").exists():
            subprocess.run(
                ("git", "init", "-q", str(repository)), env=dict(env), check=True, timeout=10
            )
        (repository / "concurrency_work.py").write_text(_WORKLOAD)
        authoring = self.root / f"workflow-{label}"
        package = authoring / "goons/general/concurrency"
        package.mkdir(parents=True)
        (package / "README.md").write_text("# general/concurrency\n")
        args = " ".join(
            shlex.quote(str(self.root / f"{label}.{kind}"))
            for kind in ("ready", "release", "effects")
        )
        (package / "goon.yml").write_text(
            "steps:\n- id: held-native\n  run: uv run --offline --no-project python concurrency_work.py "
            + args
            + " "
            + shlex.quote(label)
            + "\n"
        )
        (authoring / "plan.yml").write_text(
            "goons:\n- import: general/concurrency\n  from: goons\n"
        )
        configuration = authoring / "run.yml"
        configuration.write_text(
            json.dumps(
                {
                    "version": 2,
                    "source": {"kind": "file-plan", "path": "plan.yml"},
                    "target": {"repository": str(repository)},
                }
            )
        )
        prepared = self.cli(
            ("workflow", "prepare", str(configuration), "--register-target", "--no-input"), env
        )
        assert prepared["status"] == "prepared"
        return configuration

    def launch(self, configuration: Path, env: Mapping[str, str]) -> _Capture:
        return self.start(("run", str(configuration), "--no-input", "--yes", "--json"), env)

    def held(self, label: str, env: Mapping[str, str], capture: _Capture) -> NativeRunFact:
        marker = self.root / f"{label}.ready"
        deadline = monotonic() + 35
        while not marker.exists():
            assert capture.process.poll() is None, "control CLI exited before native readiness"
            assert monotonic() < deadline, "native concurrency readiness unavailable"
            sleep(0.02)
        native = json.loads(marker.read_text())
        database = LocalDatabase(Path(env["OH_GOON_DB"]), read_only=True)
        lease = ExecutionCoordinator(database).active_lease()
        assert lease is not None and lease.fleet_run_id is not None and lease.custody is not None
        assert lease.custody.phase == "WORKER_OWNED"
        assert (
            SystemLeaseEnvironment().process_identity(lease.custody.owner_pid)
            == lease.custody.owner_identity
        )
        ancestors = [int(native["pid"])]
        ancestor = ancestors[0]
        for _ in range(8):
            status = Path(f"/proc/{ancestor}/status").read_text()
            ancestor = int(
                next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:"))
            )
            if ancestor == lease.custody.owner_pid:
                break
            ancestors.append(ancestor)
        else:
            raise AssertionError("native application is not below the claimed worker")
        events = Journal(database).run_events(lease.fleet_run_id).events
        leaders = [
            row
            for row in events
            if type(row) is ProcessStartedEvent and row.pid in ancestors and row.step_id is not None
        ]
        assert len(leaders) == 1
        command = leaders[0]
        births: list[ProcessBirthIdentity] = []
        for pid in (lease.custody.owner_pid, command.pid, native["pid"]):
            birth = observe_process_birth(pid)
            assert birth is not None
            if pid not in self.handles:
                self.handles[pid] = os.pidfd_open(pid)
            assert observe_process_birth(pid) == birth
            births.append(birth)
        assert command.process_identity == f"{births[1].boot_id}:{births[1].start_ticks}"
        fact = NativeRunFact(
            lease.fleet_run_id, births[0], births[1], births[2], command.physical_execution_id
        )
        self.runs.append(fact)
        assert (self.root / f"{label}.effects").read_text() == label + "\n"
        return fact

    def live(self, fact: NativeRunFact) -> None:
        for birth in (fact.worker, fact.command, fact.application):
            assert observe_process_birth(birth.pid) == birth
            assert not select.select([self.handles[birth.pid]], [], [], 0)[0]

    def retired(self, fact: NativeRunFact) -> None:
        for birth in (fact.worker, fact.command, fact.application):
            assert select.select([self.handles[birth.pid]], [], [], 5)[0], (
                "original native process did not retire"
            )

    def close(self) -> None:
        original = sys.exception()
        try:
            for capture in self.captures:
                if capture.process.poll() is None:
                    capture.process.send_signal(signal.SIGTERM)
                    try:
                        capture.process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        capture.process.kill()
                        capture.process.wait(timeout=5)
            for descriptor in self.handles.values():
                assert select.select([descriptor], [], [], 30)[0], (
                    "lifetime unknown; preserve private fixture"
                )
        except BaseException as error:
            if original is None:
                raise
            original.add_note(
                f"Concurrency fixture cleanup remains unverified at {self.root}: {error}"
            )
        finally:
            for capture in self.captures:
                capture.output.close()
                capture.errors.close()
            for descriptor in self.handles.values():
                os.close(descriptor)


def _readback(
    case: _CliCase, run_id: str, env: Mapping[str, str], *, outcome: str
) -> dict[str, Any]:
    shown = case.cli(("runs", "show", run_id), env)
    tape = case.cli(("runs", "tape", run_id), env)
    assert shown["outcome"] == outcome and tape["fleet_run_id"] == run_id
    step: dict[str, Any] = tape["repositories"][0]["steps"][0]
    if outcome == "SUCCESS":
        assert step["settlement"]["cleanup"] == "VERIFIED"
    else:
        assert shown["status"] == "CANCELLED"
        assert step["settlement"]["outcome"] != "SUCCESS"
    return step


def exercise_shared_state_cli(
    command: Path,
    root: Path,
    environment: Mapping[str, str],
    *,
    disjoint_target: bool,
) -> CliConcurrencyFacts:
    case = _CliCase(command, root)
    try:
        repository_a = root / "repositories/a"
        repository_b = root / "repositories/b" if disjoint_target else repository_a
        a_config = case.prepare("a", repository_a, environment)
        b_config = case.prepare("b", repository_b, environment)
        a = case.launch(a_config, environment)
        a_fact = case.held("a", environment, a)
        before = case.cli(("runs", "list"), environment)
        assert case.cli(("runs", "show", a_fact.run_id), environment)["status"] == "RUNNING"
        assert (
            case.cli(("runs", "tape", a_fact.run_id), environment)["fleet_run_id"] == a_fact.run_id
        )
        b_busy = case.cli(("run", str(b_config), "--no-input", "--yes"), environment, expected=2)
        assert b_busy.get("code") == "EXECUTION_BUSY"
        assert b_busy.get("stage") == "execution_admission"
        assert b_busy.get("fleet_run_id") is None
        assert not (root / "b.ready").exists() and not (root / "b.effects").exists()
        after_busy = case.cli(("runs", "list"), environment)
        assert after_busy["items"] == before["items"]
        case.live(a_fact)
        (root / "a.release").touch()
        assert case.finish(a)["outcome"] == "SUCCESS"
        case.retired(a_fact)
        a_step = _readback(case, a_fact.run_id, environment, outcome="SUCCESS")
        b = case.launch(b_config, environment)
        b_fact = case.held("b", environment, b)
        assert b_fact.run_id != a_fact.run_id and b_fact.worker != a_fact.worker
        (root / "b.release").touch()
        assert case.finish(b)["outcome"] == "SUCCESS"
        case.retired(b_fact)
        assert _readback(case, a_fact.run_id, environment, outcome="SUCCESS") == a_step
        b_step = _readback(case, b_fact.run_id, environment, outcome="SUCCESS")
        assert (root / "a.effects").read_text() == "a\n" and (
            root / "b.effects"
        ).read_text() == "b\n"
        return CliConcurrencyFacts(
            "same-state-disjoint-targets" if disjoint_target else "same-state-same-target",
            (environment["OH_GOON_DB"],),
            tuple(case.runs),
            ("SUCCESS", "SUCCESS"),
            b_busy.get("code"),
            False,
            tuple(case.commands),
            "one SQLite authority refuses a second fresh Run; prework may precede admission",
            (a_step["status"], b_step["status"]),
        )
    finally:
        case.close()


def exercise_independent_state_cli(
    command: Path,
    root: Path,
    environments: tuple[Mapping[str, str], Mapping[str, str]],
) -> CliConcurrencyFacts:
    first, second = environments
    assert Path(first["OH_GOON_DB"]).parent.resolve() != Path(second["OH_GOON_DB"]).parent.resolve()
    case = _CliCase(command, root)
    try:
        repository = root / "repositories/shared"
        a_config = case.prepare("a", repository, first)
        b_config = case.prepare("b", repository, second)
        a = case.launch(a_config, first)
        a_fact = case.held("a", first, a)
        b = case.launch(b_config, second)
        b_fact = case.held("b", second, b)
        case.live(a_fact)
        case.live(b_fact)
        a.process.send_signal(signal.SIGINT)
        interrupted = case.finish(a, expected=130)
        assert interrupted["status"] == "interrupted"
        assert interrupted["cleanup"] == "not-established-by-interruption"
        case.retired(a_fact)
        case.live(b_fact)
        assert case.cli(("runs", "show", b_fact.run_id), second)["status"] == "RUNNING"
        (root / "b.release").touch()
        assert case.finish(b)["outcome"] == "SUCCESS"
        case.retired(b_fact)
        a_step = _readback(case, a_fact.run_id, first, outcome="CANCELLED")
        b_step = _readback(case, b_fact.run_id, second, outcome="SUCCESS")
        assert (root / "a.effects").read_text() == "a\n" and (
            root / "b.effects"
        ).read_text() == "b\n"
        return CliConcurrencyFacts(
            "independent-state-same-target",
            (first["OH_GOON_DB"], second["OH_GOON_DB"]),
            tuple(case.runs),
            ("CANCELLED", "SUCCESS"),
            None,
            True,
            tuple(case.commands),
            "independent authority roots; controlled per-Run files only; no shared lease, global cap or same-workspace mutation qualification",
            (a_step["status"], b_step["status"]),
            interrupted["cleanup"],
        )
    finally:
        case.close()


def _environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("OH_GOON_") or name in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}:
            environment.pop(name, None)
    environment.update(
        HOME=str(root / "home"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
        XDG_STATE_HOME=str(root / "state"),
        XDG_CACHE_HOME=str(root / "cache"),
        OH_GOON_DB=str(root / "control/runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(root / "league.yml"),
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
        UV_OFFLINE="1",
    )
    return environment


@pytest.mark.parametrize("disjoint_target", (False, True))
def test_public_clis_share_one_state_admission(disjoint_target: bool) -> None:
    root = Path(tempfile.mkdtemp(prefix="oh-goon-cli-concurrency-"))
    try:
        facts = exercise_shared_state_cli(
            Path(sys.executable).with_name("oh-goon"),
            root,
            _environment(root / "state"),
            disjoint_target=disjoint_target,
        )
    except BaseException as error:
        error.add_note(
            f"Private concurrency fixture retained at {root}; establish owner quiescence before removal"
        )
        raise
    else:
        print("CLI_CONCURRENCY=" + json.dumps(asdict(facts), sort_keys=True))
        shutil.rmtree(root)


def test_independent_cli_state_cancellation_does_not_stop_other_run() -> None:
    root = Path(tempfile.mkdtemp(prefix="oh-goon-cli-independent-"))
    try:
        facts = exercise_independent_state_cli(
            Path(sys.executable).with_name("oh-goon"),
            root,
            (_environment(root / "state-a"), _environment(root / "state-b")),
        )
    except BaseException as error:
        error.add_note(
            f"Private concurrency fixture retained at {root}; establish owner quiescence before removal"
        )
        raise
    else:
        print("CLI_CONCURRENCY=" + json.dumps(asdict(facts), sort_keys=True))
        shutil.rmtree(root)
