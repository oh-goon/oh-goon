"""Small native training Plan through ordinary admission and the physical Run worker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
import yaml

from oh_goon._application_foundation import build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunSource
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.parametrize("source", (FleetRunSource.MANUAL, FleetRunSource.CICD))
def test_native_training_plan_has_worker_custody_and_canonical_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: FleetRunSource
) -> None:
    for name, value in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    repository = tmp_path / "repos" / "training"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "train.py").write_text(
        "import json, os, time\n"
        "from pathlib import Path\n"
        "Path('ready').write_text(str(os.getpid()))\n"
        "print('training ready', flush=True)\n"
        "deadline = time.monotonic() + 45\n"
        "while not Path('release').exists():\n"
        "    if time.monotonic() > deadline: raise TimeoutError('test release unavailable')\n"
        "    time.sleep(0.01)\n"
        "weight = 0.0\n"
        "for epoch in range(100):\n"
        "    weight -= 0.01 * sum(2*x*(weight*x-y) for x,y in ((1.,2.),(2.,4.),(3.,6.)))/3\n"
        "Path('metrics.json').write_text(json.dumps({'weight': weight}))\n"
        "print('training completed', flush=True)\n"
    )
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    plan_path = workflow / "goon.yml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "execution": {"purpose": "verification"},
                "steps": [{"id": "train", "run": f"{sys.executable} train.py"}],
            }
        )
    )
    foundation = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
    runtime = build_product_services(foundation)
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(plan_path)
        target = runtime.plans.prepare_execution_target(plan_path, repository=repository)
        repository_id = RepositoryIdentity.model_validate({"path": repository}).repository_id
        run = runtime.runs.start_prepared_plan(
            target,
            repository_ids={repository_id},
            fleet_run_id=f"training-{source.value.lower()}",
            source=source,
        )
        deadline = monotonic() + 15
        while not (repository / "ready").exists():
            if monotonic() >= deadline:
                pytest.fail("native training Step did not start")
            Event().wait(0.01)
        with foundation.database.transaction() as connection:
            lease = connection.execute(
                "SELECT custody_phase, owner_pid, owner_session_id FROM execution_lease"
                " WHERE fleet_run_id=?",
                (run.fleet_run_id,),
            ).fetchone()
        assert lease is not None and lease[0] == "WORKER_OWNED"
        assert lease[1] != os.getpid()
        assert observe_process_birth(lease[1]) is not None
        assert lease[2] != foundation.database.session_id
        deadline = monotonic() + 10
        while True:
            live = runtime.runs.game_tape_live_output(run.fleet_run_id, after=0)
            if "training ready\n" in "".join(chunk.text for chunk in live.chunks):
                break
            if monotonic() >= deadline:
                pytest.fail("claimed worker did not forward its flushed live output")
            Event().wait(0.01)
        assert live.state == "ACTIVE" and live.available
        sequences = [chunk.sequence for chunk in live.chunks]
        assert sequences == sorted(set(sequences))
        cursor = live.next_sequence
        assert runtime.runs.game_tape_live_output(run.fleet_run_id, after=cursor).chunks == ()
        current = runtime.runs.get(run.fleet_run_id)
        assert current.run.status.value == "RUNNING"
        scope = ("--repository-id", repository_id, "--plan-id", target.plan.plan_id)
        with foundation.database.transaction() as connection:
            before_observers = tuple(connection.iterdump())
        for arguments in (
            ("doctor", "--json"),
            ("doctor", "--runtime", "--json"),
            ("runs", "list", "--limit", "1", *scope, "--json"),
            ("runs", "show", run.fleet_run_id, "--json"),
            ("runs", "show", "--last", *scope, "--json"),
            ("runs", "explain", run.fleet_run_id, "--json"),
        ):
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                observed = subprocess.run(
                    [str(Path(sys.executable).with_name("oh-goon")), *arguments],
                    cwd=tmp_path,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    timeout=15,
                    check=False,
                )
                output.seek(0)
                errors.seek(0)
                stdout, stderr = output.read(65_537), errors.read(65_537)
            assert observed.returncode in ((0, 2) if arguments[0] == "doctor" else (0,)), stderr
            assert not stderr
            assert len(stdout) <= 65_536
            report = json.loads(stdout)
            if arguments[0] == "doctor":
                assert report["scope"] == ("runtime" if "--runtime" in arguments else "tools")
                assert report["paths"]["database"] == str(foundation.database.path)
                if "--runtime" in arguments:
                    assert "runtime_safety" in report
            else:
                if arguments[1] == "list":
                    assert len(report["items"]) == 1
                    assert report["next_before_revision"] is None
                    report = report["items"][0]
                assert report["fleet_run_id"] == current.run.fleet_run_id
                assert report["status"] == current.run.status.value
                assert report["outcome"] == current.outcome.value
            with foundation.database.transaction() as connection:
                assert tuple(connection.iterdump()) == before_observers
            assert observe_process_birth(lease[1]) is not None
            assert not (repository / "metrics.json").exists()
        for flags in (("--json",), ("--follow",)):
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                observed = subprocess.run(
                    [
                        str(Path(sys.executable).with_name("oh-goon")),
                        "runs",
                        "logs",
                        run.fleet_run_id,
                        *flags,
                    ],
                    cwd=tmp_path,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    timeout=15,
                    check=False,
                )
                output.seek(0)
                errors.seek(0)
                stdout, stderr = output.read(65_537), errors.read(65_537)
            assert observed.returncode == 3 and not stderr
            assert len(stdout) <= 65_536
            if flags == ("--json",):
                report = json.loads(stdout)
                assert report["state"] == "UNAVAILABLE" and report["available"] is False
                assert report["_cli"]["exit_kind"] == "EVIDENCE_UNAVAILABLE"
            else:
                assert b"Transient output unavailable" in stdout
            with foundation.database.transaction() as connection:
                assert tuple(connection.iterdump()) == before_observers
            assert observe_process_birth(lease[1]) is not None
            assert (
                runtime.runs.game_tape_live_output(run.fleet_run_id, after=cursor).state == "ACTIVE"
            )
        (repository / "release").touch()
        detail = runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=20)
        assert detail.outcome is FleetRunOutcome.SUCCESS
        assert detail.run.source is source
        assert json.loads((repository / "metrics.json").read_text())["weight"] == pytest.approx(
            2.0, abs=0.001
        )
        deadline = monotonic() + 5
        while (
            terminal_output := runtime.runs.game_tape_live_output(run.fleet_run_id, after=cursor)
        ).state != "SEALED":
            if monotonic() >= deadline:
                pytest.fail("original live-output owner did not seal terminal output")
            Event().wait(0.01)
        assert terminal_output.available and terminal_output.expires_at is not None
        assert all(chunk.sequence > cursor for chunk in terminal_output.chunks)
        assert terminal_output.next_sequence > cursor
        assert "training completed" in "".join(chunk.text for chunk in terminal_output.chunks)
        with foundation.database.transaction() as connection:
            assert connection.execute("SELECT 1 FROM execution_lease").fetchone() is None
    finally:
        (repository / "release").touch()
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.process
@pytest.mark.skipif(
    not atomic_execution_bytes_supported()
    or not hasattr(os, "pidfd_open")
    or not hasattr(signal, "pidfd_send_signal"),
    reason="native claimed worker and identity-bound signal observation required",
)
@pytest.mark.parametrize("termination,expected", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_original_owner_log_follow_interrupt_keeps_claimed_run_running(
    tmp_path: Path, termination: signal.Signals, expected: int
) -> None:
    repository = tmp_path / "repos" / "observer"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "work.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "print('observer-ready', flush=True)\n"
        "deadline = time.monotonic() + 45\n"
        "while not Path('release').exists():\n"
        "    if time.monotonic() > deadline: raise TimeoutError('observer release unavailable')\n"
        "    time.sleep(.01)\n"
        "print('observer-completed', flush=True)\n"
    )
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    plan = workflow / "goon.yml"
    plan.write_text(yaml.safe_dump({"steps": [{"id": "work", "run": f"{sys.executable} work.py"}]}))
    ready = tmp_path / "follower-ready.json"
    controller = tmp_path / "controller.py"
    controller.write_text(r"""
import argparse, contextlib, json, os, sys, tempfile, time
from pathlib import Path
from oh_goon._application_foundation import build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.cli_plan_run_readback import run_plan_readback
from oh_goon.cli_result import invoke_cli
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.vocabulary import FleetRunOutcome

repository, plan, ready = map(Path, sys.argv[1:4])
foundation = build_runtime_foundation(standard_local=True, startup_purpose='workflow')
runtime = build_product_services(foundation)
try:
    runtime.locations.add(repository.parent)
    runtime.plans.lock(plan)
    target = runtime.plans.prepare_execution_target(plan, repository=repository)
    run = runtime.runs.start_prepared_plan(target,
        repository_ids={RepositoryIdentity.model_validate({'path': repository}).repository_id},
        fleet_run_id='native-follow-observer')
    deadline = time.monotonic() + 15
    while True:
        batch = runtime.runs.game_tape_live_output(run.fleet_run_id, after=0)
        if 'observer-ready\n' in ''.join(c.text for c in batch.chunks): break
        assert time.monotonic() < deadline, 'native output was not forwarded'
        time.sleep(.01)
    assert batch.state == 'ACTIVE'
    with foundation.database.transaction() as connection:
        lease = connection.execute('SELECT custody_phase, owner_pid FROM execution_lease').fetchone()
        before = tuple(connection.iterdump())
    assert lease[0] == 'WORKER_OWNED' and lease[1] != os.getpid()
    birth = observe_process_birth(lease[1]); assert birth is not None
    class ReadyWriter:
        def __init__(self, output): self.output, self.size = output, 0
        def write(self, text):
            self.size += len(text.encode('utf-8'))
            assert self.size <= 65536
            written = self.output.write(text)
            if text.startswith('Next sequence:') and not ready.exists():
                temporary = ready.with_suffix('.tmp')
                temporary.write_text(json.dumps({'controller_pid': os.getpid(), 'worker_pid': lease[1]}))
                temporary.replace(ready)
            return written
        def flush(self): self.output.flush()
        def isatty(self): return False
    args = argparse.Namespace(command='runs', runs_command='logs', fleet_run_id=run.fleet_run_id,
        as_json=False, after=0, follow=True, last=False, repository_id=None, plan_id=None)
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as output:
        with contextlib.redirect_stdout(ReadyWriter(output)):
            result = invoke_cli(lambda: run_plan_readback(runtime, args), possible_json=False)
        output.seek(0); text = output.read(65537)
    assert len(text) <= 65536 and text.count('observer-ready') == 1
    assert result in (130, 143)
    assert observe_process_birth(lease[1]) == birth
    with foundation.database.transaction() as connection:
        assert tuple(connection.iterdump()) == before
    assert runtime.runs.get(run.fleet_run_id).run.status.value == 'RUNNING'
    assert runtime.runs.game_tape_live_output(run.fleet_run_id, after=batch.next_sequence).state == 'ACTIVE'
    (repository / 'release').touch()
    detail = runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=15)
    assert detail.outcome is FleetRunOutcome.SUCCESS
    deadline = time.monotonic() + 5
    while (sealed := runtime.runs.game_tape_live_output(run.fleet_run_id, after=batch.next_sequence)).state != 'SEALED':
        assert time.monotonic() < deadline, 'terminal output did not seal'
        time.sleep(.01)
    assert 'observer-completed' in ''.join(c.text for c in sealed.chunks)
    receipt = {'follower_exit': result, 'same_worker_after_interrupt': True,
        'database_unchanged': True, 'run_id': run.fleet_run_id, 'final_outcome': detail.outcome.value,
        'terminal_output': sealed.state}
finally:
    (repository / 'release').touch()
    runtime.shutdown(wait_for_runs=True)
print(json.dumps(receipt))
""")
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "state/runtime.sqlite"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    worker_handle = None
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [sys.executable, str(controller), str(repository), str(plan), str(ready)],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            start_new_session=True,
        )
        try:
            deadline = monotonic() + 20
            while not ready.exists() and process.poll() is None and monotonic() < deadline:
                Event().wait(0.01)
            assert ready.exists(), "original owner follower did not start"
            authority = json.loads(ready.read_text())
            assert authority["controller_pid"] == process.pid
            worker_handle = os.pidfd_open(authority["worker_pid"])
            process.send_signal(termination)
            assert process.wait(timeout=25) == 0
            output.seek(0)
            errors.seek(0)
            stdout, stderr = output.read(65_537), errors.read(65_537)
            assert len(stdout) <= 65_536 and len(stderr) <= 65_536
            receipt = json.loads(stdout)
            assert receipt["follower_exit"] == expected
            assert receipt["same_worker_after_interrupt"] and receipt["database_unchanged"]
            assert receipt["final_outcome"] == "SUCCESS" and receipt["terminal_output"] == "SEALED"
            with pytest.raises(ProcessLookupError):
                signal.pidfd_send_signal(worker_handle, 0)
        finally:
            (repository / "release").touch()
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if worker_handle is not None:
                try:
                    signal.pidfd_send_signal(worker_handle, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(worker_handle)
