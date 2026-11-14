"""A second installed CLI requests cancellation from the original live API owner."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.process_birth import observe_process_birth, observe_process_liveness
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_plan import LocalLaunchPlan, LocalServicePlan
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.local_launch_supervisor import start_contributor_launch

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(
        sys.platform != "linux" or shutil.which("git") is None,
        reason="native Linux process-birth and Git observations required",
    ),
]

_SERVER = """\
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

from oh_goon.api import app as app_module, dependencies
from oh_goon.api.local_access import ensure_local_access_token
from oh_goon.application import ApplicationRuntime
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION, BACKEND_SERVICE, LocalLaunchOwner, built_local_intent,
)
from oh_goon.runtime.local_launch_plan import built_local_plan
from oh_goon.runtime.local_launch_supervisor import start_inprocess_readiness_monitor

root = Path(sys.argv[1])
listener = socket.socket(fileno=int(sys.argv[2]))
port = listener.getsockname()[1]
repository = root / 'repository'
runtime = ApplicationRuntime.build(standard_local=True)
runtime.locations.add(repository.parent)
runtime.plans.lock(root / 'workflow/plan.yml')
owner = LocalLaunchOwner.create(
    intent=built_local_intent(host='127.0.0.1', port=port),
    workspace=root,
    asserted_by='native-cancellation-fixture',
)
context = owner.install_runtime_context()
token = ensure_local_access_token()
LocalLaunchAccessStore.configured().publish(
    launch_id=owner.identity.launch_id,
    owner_generation=owner.identity.owner_generation,
    token=token,
)
owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
# One actual runtime supplies lifespan and every API dependency. No alternate executor.
app_module.application = lambda: runtime
dependencies.application = lambda: runtime
app = app_module.create_app(local_access_token=token)
server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))

def admitted_after_readiness():
    admitted = runtime.plans.run(
        root / 'workflow/plan.yml', repository_ids={repository_id(repository)},
    )
    (root / 'admitted.json').write_text(json.dumps({
        'fleet_run_id': admitted.fleet_run_id,
        'launch_id': owner.identity.launch_id,
        'owner_generation': owner.identity.owner_generation,
        'backend_pid': os.getpid(),
    }))

monitor = start_inprocess_readiness_monitor(
    owner=owner,
    plan=built_local_plan(host='127.0.0.1', port=port),
    on_ready=admitted_after_readiness,
    on_failure=lambda: setattr(server, 'should_exit', True),
)
try:
    server.run(sockets=[listener])
finally:
    monitor.cancel()
    runtime.shutdown(wait_for_runs=False)
    owner.finish_owned_launch()
    context.clear()
    listener.close()
"""


def _environment(root: Path) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(root / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(root / "league.yml"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
        XDG_STATE_HOME=str(root / "state"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        HASHMARKS_NO_UPDATE_CHECK="1",
    )
    return environment


def _workflow(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "README.md").write_text("# Cancellation subject\n")
    workflow = root / "workflow"
    package = workflow / "goons/general/wait"
    package.mkdir(parents=True)
    # A real shell and its real child must both cease executing after owner cancellation.
    command = (
        "sleep 60 & child=$!; "
        'printf \'%s %s\\n\' "$$" "$child" > cancellation-native.pid; '
        'test -z "$OH_GOON_LOCAL_ACCESS_TOKEN" || exit 19; '
        'wait "$child"'
    )
    (repository / "wait.sh").write_text("#!/bin/sh\n" + command + "\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump({"steps": [{"id": "wait", "run": "sh wait.sh"}]})
    )
    (package / "README.md").write_text("# general/wait\n\nBounded native work.\n")
    (workflow / "plan.yml").write_text("goons:\n- import: general/wait\n  from: goons\n")
    selection = workflow / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "./plan.yml"},
                "target": {"repository": str(repository)},
            }
        )
    )
    return selection


def _cli(
    root: Path,
    environment: dict[str, str],
    *arguments: str,
    expected: int | tuple[int, ...] = 0,
) -> dict[str, Any]:
    executable = Path(sys.executable).with_name("oh-goon")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            [str(executable), *arguments, "--json"],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=30,
        )
        assert output.tell() <= 512 * 1024 and errors.tell() <= 128 * 1024
        output.seek(0)
        errors.seek(0)
        stdout = output.read().decode()
        stderr = errors.read().decode()
    assert completed.returncode in ((expected,) if isinstance(expected, int) else expected), (
        arguments,
        stdout[-8192:],
        stderr[-4096:],
    )
    result = json.loads(stdout)
    assert isinstance(result, dict)
    return result


def _await_file(path: Path, process: subprocess.Popen[Any], *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert process.poll() is None, "original native owner exited before its observation"
        if path.is_file():
            return
        time.sleep(0.05)
    pytest.fail("original native owner did not reach its bounded observation")


@dataclass(frozen=True)
class NativeOwner:
    root: Path
    environment: dict[str, str]
    port: int
    run_id: str
    launch_id: str
    owner_generation: int
    token: str
    process: subprocess.Popen[Any]

    def http(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        expected: tuple[int, ...] = (200, 202),
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={
                    "X-Oh-Goon-Local-Access": self.token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = response.read(2 * 1024 * 1024 + 1)
            assert len(payload) <= 2 * 1024 * 1024
            assert response.status in expected, (response.status, payload[:1024])
            result = json.loads(payload)
            assert isinstance(result, dict)
            return result
        finally:
            connection.close()

    def detail(self) -> dict[str, Any]:
        return self.http("GET", f"/api/fleet/runs/{self.run_id}")


@contextmanager
def _managed_owner(root: Path) -> Iterator[NativeOwner]:
    _workflow(root)
    environment = _environment(root)
    with socket.socket() as listener, tempfile.TemporaryFile() as diagnostics:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-c", _SERVER, str(root), str(listener.fileno())],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=diagnostics,
            stderr=subprocess.STDOUT,
            pass_fds=(listener.fileno(),),
            start_new_session=True,
        )
        owner = None
        try:
            _await_file(root / "admitted.json", process)
            admitted = json.loads((root / "admitted.json").read_text())
            token = LocalLaunchAccessStore.from_database(Path(environment["OH_GOON_DB"])).token(
                launch_id=admitted["launch_id"],
                owner_generation=admitted["owner_generation"],
            )
            owner = NativeOwner(
                root,
                environment,
                port,
                admitted["fleet_run_id"],
                admitted["launch_id"],
                admitted["owner_generation"],
                token,
                process,
            )
            _await_file(root / "repository/cancellation-native.pid", process)
            yield owner
        finally:
            if process.poll() is None:
                try:
                    if owner is not None and owner.detail()["run"]["status"] not in {
                        "COMPLETED",
                        "CANCELLED",
                        "FAILED",
                        "INTERRUPTED",
                    }:
                        owner.http("POST", f"/api/fleet/runs/{owner.run_id}/cancel")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        pytest.fail("original API owner did not shut down within its bound")
            diagnostics.seek(0, os.SEEK_END)
            size = diagnostics.tell()
            diagnostics.seek(max(0, size - 8192))
            tail = diagnostics.read().decode(errors="replace")
            # Native uvicorn may re-raise its handled SIGTERM after graceful shutdown.
            assert process.returncode in {0, -signal.SIGTERM}, tail


def _terminal(owner: NativeOwner) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        detail = owner.detail()
        if detail["run"]["status"] in {"CANCELLED", "COMPLETED", "FAILED", "INTERRUPTED"}:
            return detail
        time.sleep(0.05)
    pytest.fail("original Run did not settle within the cancellation bound")


def test_second_cli_cancels_the_original_native_run_and_fresh_cli_reads_cleanup(
    tmp_path: Path,
) -> None:
    with _managed_owner(tmp_path) as owner:
        before = owner.detail()
        receipt = before["application_launch"]
        assert receipt["kind"] == "managed" and receipt["backend_pid"] == owner.process.pid
        assert before["run"]["status"] == "RUNNING"
        pids = [
            int(value)
            for value in (tmp_path / "repository/cancellation-native.pid").read_text().split()
        ]
        assert len(pids) == 2 and all(observe_process_liveness(pid) is True for pid in pids)

        events_before = owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events")
        unknown = _cli(tmp_path, owner.environment, "runs", "cancel", "absent-run", expected=2)
        assert unknown["code"] == "RUN_NOT_FOUND" and unknown["request_attempted"] is False
        wrong_state = _environment(tmp_path / "other-state")
        refused = _cli(tmp_path, wrong_state, "runs", "cancel", owner.run_id, expected=2)
        assert refused["code"] == "STATE_IDENTITY_UNAVAILABLE"
        assert not (tmp_path / "other-state/runtime.sqlite").exists()
        assert owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events") == events_before

        # A copied row, complete launch record and real API capability still do not
        # authorize this different physical state root to control the original Run.
        clone = tmp_path / "copied-state"
        clone.mkdir()
        original_database = Path(owner.environment["OH_GOON_DB"])
        clone_database = clone / "runtime.sqlite"
        with (
            closing(sqlite3.connect(original_database.as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(clone_database)) as destination,
        ):
            source.backup(destination)
        for store in (LocalLaunchAccessStore, LocalLaunchEventStore):
            source_path = store.from_database(original_database).path(owner.launch_id)
            destination_path = store.from_database(clone_database).path(owner.launch_id)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
        copied = _cli(tmp_path, _environment(clone), "runs", "cancel", owner.run_id, expected=2)
        assert copied["code"] == "CONTROL_OWNER_UNAVAILABLE"
        assert copied["request_attempted"] is True and copied["cancellation_requested"] is False
        assert owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events") == events_before

        access = LocalLaunchAccessStore.from_database(Path(owner.environment["OH_GOON_DB"]))
        access.publish(
            launch_id=owner.launch_id,
            owner_generation=owner.owner_generation + 1,
            token="foreign-generation-capability",
        )
        try:
            stale = _cli(tmp_path, owner.environment, "runs", "cancel", owner.run_id, expected=2)
            assert stale["code"] == "CONTROL_OWNER_UNAVAILABLE"
            assert stale["request_attempted"] is False
            assert owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events") == events_before
        finally:
            access.publish(
                launch_id=owner.launch_id,
                owner_generation=owner.owner_generation,
                token=owner.token,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _cli,
                    tmp_path,
                    owner.environment,
                    "runs",
                    "cancel",
                    owner.run_id,
                    expected=(0, 2),
                )
                for _ in range(2)
            ]
            requests = [future.result() for future in futures]
        assert any(value["code"] == "CANCELLATION_REQUESTED" for value in requests)
        for value in requests:
            assert value["fleet_run_id"] == owner.run_id
            assert owner.token not in json.dumps(value)
            if value["cancellation_requested"] is True:
                assert value["cleanup"] == "NOT_ESTABLISHED_BY_CANCELLATION_REQUEST"
        settled = _terminal(owner)
        assert settled["run"]["status"] == "CANCELLED"
        assert settled["application_launch"] == receipt
        events_after = owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events")
        assert (
            sum(event["event_type"] == "RUN_CANCEL_REQUESTED" for event in events_after["events"])
            == 1
        )
        assert sum(event["event_type"] == "RUN_CANCELLED" for event in events_after["events"]) == 1
        tape = _cli(tmp_path, owner.environment, "runs", "tape", owner.run_id)
        step = tape["repositories"][0]["steps"][0]
        assert step["cancellation"]["settlement"]["cleanup"] == "VERIFIED"
        assert step["cancellation"]["supervisor"]["final_process_group_quiescent"] is True
        assert all(observe_process_liveness(pid) is False for pid in pids)
        repeated = _cli(tmp_path, owner.environment, "runs", "cancel", owner.run_id, expected=2)
        assert repeated["code"] == "INVALID_RUN_TRANSITION"
        assert repeated["request_attempted"] is False
        assert owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events") == events_after

        # Launch context is provenance from the backend process, not authority a
        # fresh CLI can acquire by inheriting the three public context values.
        # The cancelled opaque command's target properly remains quarantined.
        # Use a fresh target and a successful short Run as the exact valid baseline.
        fresh_target = tmp_path / "fresh-target"
        fresh_target.mkdir()
        subprocess.run(["git", "init", "-q", str(fresh_target)], check=True, timeout=10)
        (fresh_target / "README.md").write_text("# Fresh admission subject\n")
        (fresh_target / "wait.sh").write_text(
            "#!/bin/sh\nprintf 'admitted\\n' >> admission.marker\n"
        )
        selection = tmp_path / "workflow/run.yml"
        configuration = yaml.safe_load(selection.read_text())
        configuration["target"] = {"repository": str(fresh_target)}
        selection.write_text(yaml.safe_dump(configuration))
        _cli(
            tmp_path,
            owner.environment,
            "workflow",
            "prepare",
            str(selection),
            "--register-target",
            "--no-input",
        )
        baseline = _cli(tmp_path, owner.environment, "run", str(selection), "--no-input")
        assert baseline["status"] == "COMPLETED" and baseline["outcome"] == "SUCCESS"
        assert baseline["fleet_run_id"] != owner.run_id
        effect_marker = fresh_target / "admission.marker"
        assert effect_marker.read_bytes() == b"admitted\n"
        history_before = _cli(tmp_path, owner.environment, "runs", "list")
        events_before_forgery = owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events")
        assert events_before_forgery["events"] == events_after["events"]
        marker = tmp_path / "repository/cancellation-native.pid"
        marker_before = marker.read_bytes()
        inherited = {
            **owner.environment,
            "OH_GOON_LAUNCH_ID": receipt["launch_id"],
            "OH_GOON_LAUNCH_OWNER_GENERATION": str(receipt["owner_generation"]),
            "OH_GOON_LAUNCH_BACKEND_GENERATION": str(receipt["backend_generation"]),
        }
        assert "OH_GOON_LOCAL_ACCESS_TOKEN" not in inherited
        forged = _cli(tmp_path, inherited, "run", str(selection), "--no-input", expected=2)
        assert forged["status"] == "error" and forged["fleet_run_id"] is None
        assert forged["code"] == "CONTROL_OWNER_UNAVAILABLE"
        assert forged["stage"] == "run_admission"
        assert _cli(tmp_path, owner.environment, "runs", "list") == history_before
        assert owner.http("GET", f"/api/fleet/runs/{owner.run_id}/events") == events_before_forgery
        assert effect_marker.read_bytes() == b"admitted\n"
        assert marker.read_bytes() == marker_before
        assert all(observe_process_liveness(pid) is False for pid in pids)


def test_second_cli_refuses_unmanaged_run_and_original_terminal_still_cancels(
    tmp_path: Path,
) -> None:
    workflow = _workflow(tmp_path)
    environment = _environment(tmp_path)
    _cli(
        tmp_path,
        environment,
        "workflow",
        "prepare",
        str(workflow),
        "--register-target",
        "--no-input",
    )
    executable = Path(sys.executable).with_name("oh-goon")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [str(executable), "run", str(workflow), "--no-input", "--json"],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            start_new_session=True,
        )
        try:
            _await_file(tmp_path / "repository/cancellation-native.pid", process)
            history = _cli(tmp_path, environment, "runs", "list")
            run_id = history["items"][0]["fleet_run_id"]
            before = _cli(tmp_path, environment, "runs", "events", run_id)
            refusal = _cli(tmp_path, environment, "runs", "cancel", run_id, expected=2)
            assert refusal["code"] == "CONTROL_OWNER_UNAVAILABLE"
            assert refusal["request_attempted"] is False
            assert refusal["cancellation_requested"] is False
            assert _cli(tmp_path, environment, "runs", "events", run_id) == before
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail("original CLI did not shut down within its cancellation bound")
        assert process.returncode == 130
        assert output.tell() <= 512 * 1024 and errors.tell() <= 128 * 1024
        output.seek(0)
        result = json.loads(output.read().decode())
        assert result["fleet_run_id"] == run_id and result["status"] == "interrupted"
        assert result["cancellation_requested"] is True
        assert result["execution_status"] in {"CANCELLING", "CANCELLED"}
        assert result["cleanup"] == "not-established-by-interruption"
        fresh = _cli(tmp_path, environment, "runs", "show", run_id)
        assert fresh["status"] == "CANCELLED"


def _reload_team(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "README.md").write_text("# Native reload cancellation subject\n")
    (repository / "goon.yml").write_text("steps:\n- id: wait\n  run: sh wait.sh\n")
    (repository / "wait.sh").write_text(
        "#!/bin/sh\n"
        "sleep 60 & child=$!\n"
        'printf \'%s %s\\n\' "$$" "$child" > cancellation-native.pid\n'
        'test -z "$OH_GOON_LOCAL_ACCESS_TOKEN" || exit 19\n'
        'wait "$child"\n'
    )
    return repository


def _start_team(owner: NativeOwner, repository: Path, run_id: str) -> NativeOwner:
    teams = owner.http("GET", "/api/league")["teams"]
    team = next(value for value in teams if value["repository"]["path"] == str(repository))
    state = owner.http(
        "POST",
        "/api/league/team-runs",
        json.dumps(
            {
                "repository": str(repository),
                "fleet_run_id": run_id,
                "expected_pipeline_sha256": team["pipeline"]["sha256"],
                "expected_repository_instance_id": team["repository_instance_id"],
            }
        ).encode(),
    )
    assert state["fleet_run_id"] == run_id
    _await_file(repository / "cancellation-native.pid", owner.process)
    return replace(owner, run_id=run_id)


def test_native_reload_keeps_wrapper_and_rebinds_only_new_run_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.api.local_access import new_local_access_token
    from oh_goon.application import ApplicationRuntime

    environment = _environment(tmp_path)
    repositories = tmp_path / "repositories"
    first_target = _reload_team(repositories, "first")
    second_target = _reload_team(repositories, "second")
    # Prepare the exact Team locks through the ordinary service before any server
    # or Run exists. All admissions below go through the real unmodified API.
    with monkeypatch.context() as selected:
        for name, value in environment.items():
            if name.startswith(("OH_GOON_", "XDG_")):
                selected.setenv(name, value)
        runtime = ApplicationRuntime.build(startup_purpose="workflow")
        try:
            runtime.locations.add(repositories)
            for target in (first_target, second_target):
                runtime.plans.lock(target / "goon.yml")
        finally:
            runtime.shutdown(wait_for_runs=False)

    watch = tmp_path / "native-reload-watch"
    watch.mkdir()
    trigger = watch / "native_reload_trigger.py"
    trigger.write_text("GENERATION = 1\n")
    with socket.socket() as selection:
        selection.bind(("127.0.0.1", 0))
        port = selection.getsockname()[1]
    repository_root = Path(__file__).resolve().parents[4]
    entrypoint = Path(sys.executable).with_name("oh-goon-local-dev-backend")
    service = LocalServicePlan(
        service="backend",
        generation=1,
        command=(
            str(entrypoint),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--reload",
            "--reload-dir",
            str(watch),
            "--log-level",
            "warning",
        ),
        cwd=watch,
        host="127.0.0.1",
        port=port,
        readiness_path="/healthz",
        readiness_status=204,
    )
    launch = start_contributor_launch(
        root=repository_root,
        env=environment,
        plan=LocalLaunchPlan(profile="contributor-dev", services=(service,)),
        token=new_local_access_token(),
    )
    observations = launch.owner.store
    process = launch.processes[0]
    transport = NativeOwner(
        tmp_path,
        environment,
        port,
        "not-admitted",
        launch.owner.identity.launch_id,
        launch.owner.identity.owner_generation,
        launch.token,
        process,
    )
    active: list[NativeOwner] = []
    runtime_pids: list[int] = []
    try:
        backend = observations.manifest(transport.launch_id).observed.services[0]
        assert backend.process is not None and backend.process.birth_identity is not None
        assert backend.runtime_process is not None
        assert backend.runtime_process.birth_identity is not None
        wrapper_birth = backend.process.birth_identity.value
        first_birth = backend.runtime_process.birth_identity.value
        assert wrapper_birth.pid == process.pid and first_birth.pid != process.pid
        runtime_pids.append(first_birth.pid)
        first = _start_team(transport, first_target, "native-reload-first")
        active.append(first)
        first_receipt = first.detail()["application_launch"]
        assert first_receipt["backend_pid"] == first_birth.pid
        cancelled = _cli(tmp_path, environment, "runs", "cancel", first.run_id)
        assert cancelled["code"] == "CANCELLATION_REQUESTED"
        assert _terminal(first)["run"]["status"] == "CANCELLED"
        first_events = first.http("GET", f"/api/fleet/runs/{first.run_id}/events")["events"]
        first_tape = _cli(tmp_path, environment, "runs", "tape", first.run_id)
        assert (
            first_tape["repositories"][0]["steps"][0]["cancellation"]["settlement"]["cleanup"]
            == "VERIFIED"
        )

        trigger.write_text("GENERATION = 2\n")
        changed = trigger.stat().st_mtime + 2
        os.utime(trigger, (changed, changed))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            backend = observations.manifest(transport.launch_id).observed.services[0]
            current = backend.runtime_process
            if current is not None and current.birth_identity is not None:
                second_birth = current.birth_identity.value
                if (
                    second_birth != first_birth
                    and observe_process_birth(second_birth.pid) == second_birth
                ):
                    break
            assert process.poll() is None, launch.diagnostic_tail()
            time.sleep(0.05)
        else:
            pytest.fail(
                "native uvicorn did not replace its actual API child within the reload bound"
            )
        assert backend.process is not None and backend.process.birth_identity is not None
        assert backend.process.birth_identity.value == wrapper_birth
        assert observe_process_birth(process.pid) == wrapper_birth
        assert observe_process_liveness(first_birth.pid) is False
        runtime_pids.append(second_birth.pid)
        assert first.detail()["application_launch"] == first_receipt

        second = _start_team(transport, second_target, "native-reload-second")
        active.append(second)
        second_receipt = second.detail()["application_launch"]
        assert second_receipt["backend_pid"] == second_birth.pid
        assert second_receipt["launch_id"] == first_receipt["launch_id"]
        assert second_receipt["backend_generation"] == first_receipt["backend_generation"]
        assert second_receipt["backend_start_ticks"] != first_receipt["backend_start_ticks"]

        database = LocalDatabase(Path(environment["OH_GOON_DB"]), read_only=True)
        with database.transaction() as connection:
            state = database.capture_append_context(connection)
        second_events = second.http("GET", f"/api/fleet/runs/{second.run_id}/events")["events"]
        refused = first.http(
            "POST",
            f"/api/fleet/runs/{first.run_id}/cancel",
            json.dumps(
                {"application_launch": first_receipt, "state": state.model_dump(mode="json")}
            ).encode(),
            expected=(409,),
        )
        assert refused["code"] == "CONTROL_OWNER_UNAVAILABLE"
        assert (
            second.http("GET", f"/api/fleet/runs/{second.run_id}/events")["events"] == second_events
        )
        assert first.http("GET", f"/api/fleet/runs/{first.run_id}/events")["events"] == first_events
        assert second.detail()["run"]["status"] == "RUNNING"
        cancelled = _cli(tmp_path, environment, "runs", "cancel", second.run_id)
        assert cancelled["code"] == "CANCELLATION_REQUESTED"
        assert _terminal(second)["run"]["status"] == "CANCELLED"
        second_tape = _cli(tmp_path, environment, "runs", "tape", second.run_id)
        assert (
            second_tape["repositories"][0]["steps"][0]["cancellation"]["settlement"]["cleanup"]
            == "VERIFIED"
        )
        for target in (first_target, second_target):
            pids = [
                int(value) for value in (target / "cancellation-native.pid").read_text().split()
            ]
            assert all(observe_process_liveness(pid) is False for pid in pids)
    finally:
        try:
            for owner in active:
                if owner.detail()["run"]["status"] not in {
                    "CANCELLED",
                    "COMPLETED",
                    "FAILED",
                    "INTERRUPTED",
                }:
                    owner.http("POST", f"/api/fleet/runs/{owner.run_id}/cancel")
        finally:
            launch.stop()
            for child in launch.processes:
                if child.stdout is not None:
                    child.stdout.close()
        assert process.poll() is not None
        assert all(observe_process_liveness(pid) is False for pid in runtime_pids)
