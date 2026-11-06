"""Real wall-clock Iris occurrences through the public Schedule and Run owners."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import yaml

from oh_goon.api.local_access import LOCAL_ACCESS_HEADER

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = (
    "ml-output/iris-training-metrics.json",
    "ml-output/iris-model-parameters.json",
)
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None,
    reason="native POSIX Git and uv required",
)


def _native(
    arguments: list[str], root: Path, environment: dict[str, str], *, timeout: float
) -> str:
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        result = subprocess.run(
            arguments,
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout,
            check=False,
        )
        assert stdout.tell() <= 256 * 1024 and stderr.tell() <= 128 * 1024
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode()
        error = stderr.read().decode()
    assert result.returncode == 0, (arguments[:3], output[-4096:], error[-4096:])
    return output


def _json_request(
    base: str,
    token: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: float = 3,
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode()
    request = Request(
        base + path,
        data=payload,
        method=method or ("GET" if body is None else "PUT"),
        headers={
            LOCAL_ACCESS_HEADER: token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    assert len(raw) <= 1024 * 1024
    result = json.loads(raw)
    assert isinstance(result, dict)
    return result


def _status(
    base: str, token: str, schedule_id: str, *, timeout: float
) -> dict[str, Any]:
    schedules = _json_request(base, token, "/api/schedules", timeout=timeout)[
        "schedules"
    ]
    return next(
        item for item in schedules if item["definition"]["schedule_id"] == schedule_id
    )


def _stop(server: subprocess.Popen[bytes], complete_deadline: float) -> int:
    if server.poll() is not None:
        return server.returncode
    server.terminate()
    try:
        # Keep two seconds of the owned cleanup reserve for forced reaping.
        grace = min(8, max(0.05, complete_deadline - time.monotonic() - 2))
        return server.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=min(2, max(0.05, complete_deadline - time.monotonic())))
        raise AssertionError(
            "application shutdown exceeded its owned cleanup budget"
        ) from None


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("pause", id="pause-before-first-due"),
        pytest.param("manual-failure", id="manual-failed-training-occurrence"),
        pytest.param("clock", id="two-real-clock-occurrences", marks=pytest.mark.daily),
    ],
)
def test_scheduled_iris_uses_real_clock_and_retained_canonical_runs(
    tmp_path: Path,
    mode: str,
) -> None:
    # The ordinary Schedule timing/admission tests retain fast boundary proof.
    # This complete native witness deliberately waits for two real intervals.
    started = time.monotonic()
    deadline = started + 166
    complete_deadline = started + 176
    shutdown_pending = False
    expected_occurrences = {"pause": 0, "manual-failure": 1, "clock": 2}[mode]
    manual_started = False

    def remaining(limit: float, *, shutdown: bool | None = None) -> float:
        reserved = shutdown_pending if shutdown is None else shutdown
        available = (deadline if reserved else complete_deadline) - time.monotonic()
        assert available > 0, "native witness exhausted its bounded operation budget"
        return min(limit, available)

    application = tmp_path / "iris-application"
    shutil.copytree(ROOT / "templates/goons/ml/iris-train", application)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OH_GOON_") and key != "UV_PROJECT_ENVIRONMENT"
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        HASHMARKS_NO_UPDATE_CHECK="1",
    )
    executable = str(Path(sys.executable).with_name("oh-goon"))

    def native(arguments: list[str], selected_env: dict[str, str] | None = None) -> str:
        return _native(
            arguments,
            tmp_path,
            environment if selected_env is None else selected_env,
            timeout=remaining(30),
        )

    def cli(*arguments: str) -> dict[str, Any]:
        value = json.loads(native([executable, *arguments, "--json"]))
        assert isinstance(value, dict)
        return value

    native(["git", "init", "-q", str(application)])
    developer = tmp_path / "developer-environment"
    native(
        [
            "uv",
            "sync",
            "--project",
            str(application),
            "--frozen",
            "--no-default-groups",
        ],
        {**environment, "UV_PROJECT_ENVIRONMENT": str(developer)},
    )
    developer_config = (developer / "pyvenv.cfg").read_bytes()
    native_lock = (application / "uv.lock").read_bytes()
    workflow = tmp_path / "iris-workflow"
    cli(
        "init",
        "--template",
        "ml/iris-training",
        "--kind",
        "plan",
        "--output",
        str(workflow),
        "--repository",
        str(application),
        "--no-input",
    )
    selection = workflow / "run.yml"
    if mode == "manual-failure":
        configured = yaml.safe_load(selection.read_text())
        configured["inputs"] = {"minimum_accuracy_percent": 100}
        selection.write_text(yaml.safe_dump(configured, sort_keys=False))
        cli("workflow", "prepare", str(selection), "--no-input")
    selected_bytes = selection.read_bytes()
    schedule_id = "recurring-iris"
    native(
        [
            executable,
            "plan",
            "schedule",
            str(selection),
            "--schedule-id",
            schedule_id,
            "--interval-minutes",
            "1",
        ],
    )
    assert cli("runs", "list")["items"] == []

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    token = secrets.token_urlsafe(32)
    server_environment = {
        **environment,
        "OH_GOON_LOCAL_ACCESS_TOKEN": token,
        "PYTHONUNBUFFERED": "1",
    }
    base = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "server.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    server: subprocess.Popen[bytes] | None = None
    observed: list[dict[str, Any]] = []
    observed_ids: set[str] = set()
    definition: dict[str, Any] | None = None
    run_ids: set[str] = set()
    readbacks: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    restarted = False

    def readback(occurrence: dict[str, Any]) -> None:
        assert definition is not None
        run_id = occurrence["repository_run"]["fleet_run_id"]
        assert run_id not in run_ids
        run_ids.add(run_id)
        tape = cli("runs", "tape", run_id)
        assert tape["fleet_run_id"] == run_id
        assert tape["run_source"] == "SCHEDULED"
        assert tape["status"] == (
            "ATTENTION_REQUIRED" if mode == "manual-failure" else "PASSED"
        )
        assert (
            tape["execution_reference"]["plan_id"]
            == definition["target"]["plan"]["plan_id"]
        )
        assert tape["configuration"] == definition["target"]["plan"]["configuration"]
        steps = tape["repositories"][0]["steps"]
        assert len(steps) == 1
        assert steps[0]["status"] == (
            "FAILED" if mode == "manual-failure" else "PASSED"
        )
        if mode == "manual-failure":
            assert steps[0]["exit_code"] == 1
        assert steps[0]["settlement"]["cleanup"] == "VERIFIED"
        assert steps[0]["uv"]["lock_mode"] == "frozen"
        content_sha = definition["target"]["plan"]["steps"][0]["project_source"][
            "content_sha256"
        ]
        assert steps[0]["project_source"]["content_sha256"] == content_sha
        outputs = cli("runs", "outputs", run_id)
        artifacts = outputs["repositories"][0]["steps"][0]["artifacts"]
        assert {item["path"] for item in artifacts} == set(ARTIFACTS)
        for item in artifacts:
            destination = tmp_path / f"{len(run_ids)}-{Path(item['path']).name}"
            assert item["status"] == "PRESENT"
            cli(
                "runs",
                "export-artifact",
                run_id,
                "--step",
                steps[0]["step_id"],
                "--path",
                item["path"],
                "--output",
                str(destination),
            )
            payload = destination.read_bytes()
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            assert len(payload) == item["byte_count"]
            value = json.loads(payload)
            if "random_seed" in value:
                assert value["random_seed"] == 42
                if mode == "manual-failure":
                    assert value["accuracy"] < value["minimum_accuracy"] == 1
                else:
                    assert value["accuracy"] >= value["minimum_accuracy"] == 0.9
            else:
                assert value["coefficients"]
        readbacks[run_id] = tape
        receipts.append(
            {
                "schedule_run_id": occurrence["schedule_run_id"],
                "fleet_run_id": run_id,
                "started_at": occurrence["started_at"],
                "finished_at": occurrence["finished_at"],
                "plan_id": tape["execution_reference"]["plan_id"],
                "project_content_sha256": content_sha,
                "artifacts": [
                    {key: item[key] for key in ("path", "sha256", "byte_count")}
                    for item in artifacts
                ],
            }
        )

    try:
        with os.fdopen(descriptor, "wb") as log:

            def launch() -> subprocess.Popen[bytes]:
                remaining(1, shutdown=True)
                return subprocess.Popen(
                    [
                        executable,
                        "app",
                        "serve",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=tmp_path,
                    env=server_environment,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )

            shutdown_pending = True
            server = launch()
            while time.monotonic() < deadline:
                assert server.poll() is None, (
                    "foreground application exited before qualification"
                )
                try:
                    status = _status(base, token, schedule_id, timeout=remaining(3))
                except HTTPError as error:
                    raise AssertionError(
                        f"public Schedule observation returned HTTP {error.code}"
                    ) from None
                except URLError:
                    time.sleep(remaining(0.2))
                    continue
                definition = status["definition"]
                occurrence = status["latest_occurrence"]
                if occurrence is None:
                    assert status["next_run_at"] is not None
                    due = datetime.fromisoformat(status["next_run_at"])
                    created = datetime.fromisoformat(definition["created_at"])
                    assert due == created + timedelta(minutes=1)
                    if expected_occurrences == 0:
                        assert datetime.now(UTC) < due
                        break
                    if mode == "manual-failure" and not manual_started:
                        assert datetime.now(UTC) < due
                        admitted = _json_request(
                            base,
                            token,
                            f"/api/schedules/{schedule_id}/runs",
                            body={
                                "schedule_run_id": "manual-failed-training",
                                "expected_generation_id": definition["generation_id"],
                                "expected_record_version": definition["record_version"],
                            },
                            method="POST",
                            timeout=remaining(3),
                        )
                        assert admitted["schedule_run_id"] == "manual-failed-training"
                        manual_started = True
                elif occurrence["status"] == (
                    "ATTENTION_REQUIRED" if mode == "manual-failure" else "COMPLETED"
                ):
                    identity = occurrence["schedule_run_id"]
                    if identity not in observed_ids:
                        assert occurrence["expected"] == 1
                        if mode == "manual-failure":
                            assert occurrence["successful"] == 0
                            assert occurrence["attention_required"] == 1
                        else:
                            assert occurrence["successful"] == 1
                        assert occurrence["failed"] == occurrence["cancelled"] == 0
                        page = _json_request(
                            base,
                            token,
                            f"/api/schedules/{schedule_id}/occurrences/{identity}/repositories",
                            timeout=remaining(3),
                        )
                        assert page["total"] == 1
                        member = page["items"][0]
                        assert (
                            member["status"]
                            == (
                                "ATTENTION_REQUIRED"
                                if mode == "manual-failure"
                                else "SUCCESSFUL"
                            )
                            and member["fleet_run_id"]
                        )
                        occurrence["repository_run"] = member
                        observed.append(occurrence)
                        observed_ids.add(identity)
                        # The first cold read/export runs during the next real
                        # interval, rather than adding process startups after it.
                        readback(occurrence)
                        if len(observed) == expected_occurrences:
                            break
                    finished = datetime.fromisoformat(occurrence["finished_at"])
                    assert datetime.fromisoformat(
                        status["next_run_at"]
                    ) == finished + timedelta(minutes=1)
                    if (
                        len(observed) == 1
                        and not restarted
                        and datetime.now(UTC)
                        >= finished + timedelta(minutes=1, seconds=-10)
                    ):
                        assert _stop(server, complete_deadline) == 0
                        server = launch()
                        restarted = True
                elif occurrence["status"] not in {"RUNNING"}:
                    raise AssertionError(
                        "Schedule did not produce a successful training occurrence"
                    )
                time.sleep(remaining(0.5))
            assert len(observed) == expected_occurrences, (
                "eligible occurrences did not finish within the native budget"
            )
            assert definition is not None
            paused = _json_request(
                base,
                token,
                f"/api/schedules/{schedule_id}",
                body={
                    key: value
                    for key, value in {**definition, "state": "paused"}.items()
                    if key not in {"schedule_id", "created_at", "updated_at"}
                },
                timeout=remaining(3),
            )
            assert paused["state"] == "paused"
            assert paused["target"] == definition["target"]
            assert (
                _status(base, token, schedule_id, timeout=remaining(3))["next_run_at"]
                is None
            )
            assert _stop(server, complete_deadline) == 0
            shutdown_pending = False
    finally:
        if server is not None and server.poll() is None:
            _stop(server, complete_deadline)

    assert definition is not None
    assert len(observed_ids) == expected_occurrences
    if mode == "clock":
        assert datetime.fromisoformat(
            observed[0]["started_at"]
        ) >= datetime.fromisoformat(definition["created_at"]) + timedelta(minutes=1)
        assert datetime.fromisoformat(
            observed[1]["started_at"]
        ) >= datetime.fromisoformat(observed[0]["finished_at"]) + timedelta(minutes=1)
    elif mode == "pause":
        assert cli("runs", "list")["items"] == []
    else:
        assert manual_started and not restarted
    if mode == "clock":
        assert restarted
        first_id = observed[0]["repository_run"]["fleet_run_id"]
        reopened = cli("runs", "tape", first_id)
        for key in ("status", "configuration", "execution_reference", "repositories"):
            assert reopened[key] == readbacks[first_id][key]
    assert len(run_ids) == expected_occurrences
    assert (application / "uv.lock").read_bytes() == native_lock
    assert (developer / "pyvenv.cfg").read_bytes() == developer_config
    assert selection.read_bytes() == selected_bytes
    assert not (application / "ml-output").exists()
    assert time.monotonic() - started < 180
    evidence = {
        "mode": mode,
        "schedule_id": schedule_id,
        "created_at": definition["created_at"],
        "interval_minutes": definition["interval_minutes"],
        "restarted_before_second_due": restarted,
        "occurrences": receipts,
        "elapsed_seconds": time.monotonic() - started,
    }
    # Only public IDs, timestamps and content digests leave the private fixture.
    print("SCHEDULE_IRIS_EVIDENCE " + json.dumps(evidence, sort_keys=True))
