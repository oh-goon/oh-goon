"""Installed Pi review recurrence through the public CLI and authenticated HTTP."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import httpx2
import pytest

from oh_goon.api.local_access import LOCAL_ACCESS_HEADER
from oh_goon.core.local_access_protocol import LOCAL_ACCESS_ENV
from oh_goon.core.vocabulary import ScheduleExecutionStatus
from oh_goon.engine.git import GitService
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime

_TASK = "Review the exact retained source without changing it."
_SKILL = "Use the retained review guidance for this independent review."
_PROFILE = "retained-review-proof"
_SCHEDULE_ID = "retained-review"
_KEY_NAME = "OH_GOON_TEST_REVIEW_KEY"
_MODEL_KEY = "synthetic-retained-review-model-key"
_ACCESS = "synthetic-retained-review-local-access"
_PROBE = "READ_ONLY_REVIEW_PROVED"


@dataclass
class _ReviewModel:
    endpoint: str
    completed: list[tuple[str, str]]
    failures: list[str]


def _texts(message: dict[str, Any]) -> list[str]:
    value = message.get("content")
    if isinstance(value, str):
        return [value]
    return [
        part["text"]
        for part in value or []
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]


def _frozen_prompt(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if message.get("role") != "user":
            continue
        for value in _texts(message):
            start = value.find('{"source_authority":')
            if start < 0:
                continue
            payload, _ = json.JSONDecoder().raw_decode(value[start:])
            if isinstance(payload, dict) and payload.get("task") == _TASK:
                return payload
    raise AssertionError("Missing original frozen review task")


@contextmanager
def _review_provider() -> Generator[_ReviewModel, None, None]:
    completed: list[tuple[str, str]] = []
    failures: list[str] = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                assert self.path == "/v1/chat/completions"
                assert self.headers.get("Authorization") == f"Bearer {_MODEL_KEY}"
                size = int(self.headers["Content-Length"])
                assert 0 < size <= 1024 * 1024
                body = self.rfile.read(size)
                assert _MODEL_KEY.encode() not in body and _ACCESS.encode() not in body
                payload = json.loads(body)
                assert payload["model"] == "retained-review-fixture"
                messages = payload["messages"]
                frozen = _frozen_prompt(messages)
                evidence = json.loads(frozen["repository_evidence"])
                identity = frozen["identity"]
                assert not frozen["context"]
                assert evidence["producer"] == "Hashmarks"
                assert (
                    evidence["producer_implementation_identity"]
                    == require_loaded_hashmarks_runtime()
                )
                assert evidence["capture_identity"] == identity["candidate_identity"]
                privileged = "\n".join(
                    text
                    for item in messages
                    if item["role"] in {"system", "developer"}
                    for text in _texts(item)
                )
                assert frozen["repository_evidence"] in privileged
                assert _SKILL in privileged and _TASK in privileged

                tools = [item["function"] for item in payload.get("tools", ())]
                tool_results = [
                    text for item in messages if item["role"] == "tool" for text in _texts(item)
                ]
                if not tools:
                    self.reply({"role": "assistant", "content": "Read the retained task."}, "stop")
                elif not tool_results:
                    tool = next(
                        item
                        for item in tools
                        if item["name"]
                        in {
                            "bash",
                            "shell",
                            "shell_command",
                            "exec_command",
                        }
                    )
                    probe = (
                        "import os, pathlib\n"
                        "assert pathlib.Path('source.py').read_bytes() == b'VALUE = 42\\n'\n"
                        f"assert {_KEY_NAME!r} not in os.environ and {LOCAL_ACCESS_ENV!r} not in os.environ\n"
                        "try: pathlib.Path('unauthorized-write').write_text('attempt')\n"
                        "except OSError: pass\n"
                        "else: raise AssertionError('Review root accepted a write')\n"
                        f"print({_PROBE!r})\n"
                    )
                    arguments = {
                        "command"
                        if "command" in tool["parameters"]["properties"]
                        else "cmd": shlex.join(("/usr/bin/python3", "-I", "-c", probe))
                    }
                    if "description" in tool["parameters"]["properties"]:
                        arguments["description"] = "Check the exact read-only source"
                    self.reply(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "review_workspace_probe",
                                    "type": "function",
                                    "function": {
                                        "name": tool["name"],
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                        "tool_calls",
                    )
                else:
                    assert len(tool_results) == 1 and _PROBE in tool_results[0]
                    completed.append((identity["candidate_identity"], identity["scope_identity"]))
                    record = {
                        "schema": "oh-goon.agent-review/v1",
                        **identity,
                        "verdict": "CLEAN",
                        "summary": "Checked retained source and read-only credentials boundary.",
                        "reviewed_paths": {"complete_scope": identity["scope_identity"]},
                        "findings": [],
                    }
                    self.reply({"role": "assistant", "content": json.dumps(record)}, "stop")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")
                self.reply({"role": "assistant", "content": "Fixture rejected the input."}, "stop")

        def reply(self, delta: dict[str, Any], finish: str) -> None:
            data = (
                "".join(
                    "data: " + json.dumps(event) + "\n\n"
                    for event in (
                        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                    )
                )
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Endpoint) as server:
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield _ReviewModel(f"http://127.0.0.1:{server.server_port}/v1", completed, failures)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def _cli_json(cli: Path, env: Mapping[str, str], cwd: Path, *arguments: str) -> dict[str, Any]:
    # This is an operator process, so transport its explicit control-plane
    # coordinates without the workload runner's Agent-child scrubbing.
    with tempfile.TemporaryFile(dir=cwd) as output, tempfile.TemporaryFile(dir=cwd) as errors:
        result = subprocess.run(
            (str(cli), *arguments, "--json"),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=65,
            check=False,
        )
        output.seek(0)
        errors.seek(0)
        stdout = output.read(1024 * 1024 + 1)
        stderr = errors.read(4096)
        assert len(stdout) <= 1024 * 1024
    assert _MODEL_KEY.encode() not in stdout and _ACCESS.encode() not in stdout
    assert result.returncode == 0, (arguments, stdout[-4096:], stderr)
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    return payload


@contextmanager
def _application_server(
    cli: Path,
    root: Path,
    repository: Path,
    catalog: Path,
    env: Mapping[str, str],
    label: str,
) -> Generator[httpx2.Client, None, None]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = root / f"{label}.log"
    with (
        log.open("wb") as output,
        subprocess.Popen(
            (
                str(cli),
                "app",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--scheduled-agent-workspace",
                str(repository),
                "--scheduled-agent-profile",
                _PROFILE,
                "--scheduled-agent-catalog",
                str(catalog),
            ),
            cwd=root,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process,
    ):
        try:
            with httpx2.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={LOCAL_ACCESS_HEADER: _ACCESS},
                timeout=4,
                trust_env=False,
            ) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        if client.get("/healthz").status_code == 204:
                            break
                    except httpx2.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    with log.open("rb") as diagnostic:
                        diagnostic.seek(max(0, log.stat().st_size - 4096))
                        tail = (
                            diagnostic.read().decode(errors="replace").replace(_ACCESS, "[access]")
                        )
                    pytest.fail(f"Public application did not reach readiness: {tail}")
                yield client
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                    pytest.fail("The public application did not stop within its cleanup budget.")
            assert process.returncode in (0, -15), process.returncode


def _payload(response: httpx2.Response, status: int = 200) -> dict[str, Any]:
    assert _MODEL_KEY not in response.text and _ACCESS not in response.text
    assert response.status_code == status, (response.url, response.status_code, response.text)
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _schedule(client: httpx2.Client) -> dict[str, Any]:
    (schedule,) = _payload(client.get("/api/schedules"))["schedules"]
    assert isinstance(schedule, dict)
    assert schedule["definition"]["schedule_id"] == _SCHEDULE_ID
    return schedule


def _await_occurrence(
    client: httpx2.Client, *, prior_id: str | None, due: datetime
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, (due - datetime.now(due.tzinfo)).total_seconds()) + 45
    current = None
    while time.monotonic() < deadline:
        current = _schedule(client)["latest_occurrence"]
        if (
            isinstance(current, dict)
            and current["schedule_run_id"] != prior_id
            and ScheduleExecutionStatus(current["status"]).terminal
        ):
            assert current["status"] == "COMPLETED" and current["successful"] == 1, current
            assert datetime.fromisoformat(current["started_at"]) >= due
            return current
        time.sleep(1)
    pytest.fail(f"Original scheduler did not settle the due occurrence: {current}")


def _scheduled_run(
    client: httpx2.Client,
    occurrence: dict[str, Any],
    expected_target: dict[str, Any],
    *,
    cli: Path,
    env: Mapping[str, str],
    root: Path,
) -> tuple[str, set[str]]:
    schedule_run_id = occurrence["schedule_run_id"]
    path = f"/api/schedules/{_SCHEDULE_ID}/occurrences/{schedule_run_id}/repositories"
    (repository,) = _payload(client.get(path))["items"]
    assert repository["status"] == "SUCCESSFUL"
    run_id = repository["fleet_run_id"]
    run = _payload(client.get(f"/api/fleet/runs/{run_id}"))
    assert run["outcome"] == "SUCCESS" and run["run"]["source"] == "SCHEDULED"
    assert run["run"]["target"] == expected_target
    (tape,) = run["game_tape"]["repositories"]
    (step,) = tape["steps"]
    (claim,) = step["native_agent_observations"]
    assert step["status"] == "PASSED" and claim["agent"] == "pi"
    assert claim["settled"] and not claim["failed"] and claim["role"] == "review"
    handoff = _cli_json(cli, env, root, "runs", "handoff", run_id)
    assert handoff["outcome"] == "SUCCESS" and handoff["fleet_run_id"] == run_id
    events = _payload(client.get(f"/api/fleet/runs/{run_id}/events"))["events"]
    assert sum(event["event_type"] == "RUN_ADMITTED" for event in events) == 1
    executions = {
        event["physical_execution_id"]
        for event in events
        if event["event_type"] == "PROCESS_STARTED"
    }
    assert executions
    return run_id, executions


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_review_schedule_runs_twice_after_public_application_reopen(
    tmp_path: Path,
) -> None:
    native_pi = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not native_pi or not runtime_root:
        pytest.skip("Select the installed Pi executable and public runtime root")
    program = Path(native_pi).resolve(strict=True)
    public_runtime = Path(runtime_root).resolve(strict=True)
    assert program.is_relative_to(public_runtime)
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file(), "Prepare the selected Oh-Goon CLI environment before qualification"

    repository = tmp_path / "repositories" / "sample"
    repository.mkdir(parents=True)
    source = repository / "source.py"
    source.write_bytes(b"VALUE = 42\n")
    git = GitService()
    assert git.run("init", "-q", cwd=repository).returncode == 0
    assert git.run("add", "source.py", cwd=repository).returncode == 0
    assert (
        git.run(
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Schedule Qualification",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "review source",
            cwd=repository,
        ).returncode
        == 0
    )
    original = git.observe(repository).fingerprint.state_id

    task = tmp_path / "task.txt"
    task.write_text(_TASK, encoding="utf-8")
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(f"# Review\n\n{_SKILL}\n", encoding="utf-8")
    catalog = tmp_path / "providers.json"
    env = {
        "PATH": os.defpath,
        "OH_GOON_DB": str(tmp_path / "state" / "application.sqlite"),
        "OH_GOON_LEAGUE_FILE": str(tmp_path / "league.yml"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "runtime"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "HASHMARKS_NO_UPDATE_CHECK": "1",
        LOCAL_ACCESS_ENV: _ACCESS,
        _KEY_NAME: _MODEL_KEY,
    }

    with _review_provider() as model:
        catalog.write_text(
            json.dumps(
                {
                    "providers": {
                        "review": {
                            "api": "openai-chat-completions",
                            "base_url": model.endpoint,
                            "api_key_env": _KEY_NAME,
                        }
                    },
                    "models": {
                        "selected": {"provider": "review", "model": "retained-review-fixture"}
                    },
                    "native_agents": {
                        "pi": {
                            "executable": str(program),
                            "runtime_roots": [str(public_runtime)],
                        }
                    },
                    "native_profiles": {
                        _PROFILE: {
                            "backend": "bubblewrap",
                            "allowed_agents": ["pi"],
                            "workspace_roots": [str(repository)],
                            "inference_profile": "selected",
                            "timeout_seconds": 30.0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        seed = _cli_json(
            cli,
            env,
            tmp_path,
            "ai",
            "--agent",
            "pi",
            "--profile",
            _PROFILE,
            "--workspace",
            str(repository),
            "--config",
            str(catalog),
            "--review",
            "--prompt-file",
            str(task),
            "--skill",
            str(skill),
        )
        assert seed["outcome"] == "SUCCESS", (seed, model.failures)
        seed_id = seed["run"]["fleet_run_id"]
        assert len(model.completed) == 1 and not model.failures

        with _application_server(cli, tmp_path, repository, catalog, env, "initial") as client:
            path = f"/api/schedules/{_SCHEDULE_ID}/from-review-run"
            arguments = {"fleet_run_id": seed_id, "name": "Retained review", "interval_minutes": 1}
            unauthenticated = httpx2.post(
                client.base_url.join(path),
                json=arguments,
                timeout=4,
                trust_env=False,
            )
            assert unauthenticated.status_code == 401
            assert _payload(client.get("/api/schedules"))["schedules"] == []
            definition = _payload(client.post(path, json=arguments), 201)
            assert definition["target"]["retained_review_run_id"] == seed_id
            assert definition["target"]["plan"] == seed["run"]["target"]["plan"]
            assert (
                definition["target"]["agent_deliveries"]
                == seed["run"]["target"]["agent_deliveries"]
            )
            assert (
                definition["target"]["reviewed_repository_source"]
                == (seed["repository_admissions"][0]["source_authority"])
            )
            task.unlink()
            skill.unlink()
            status = _schedule(client)
            assert status["latest_occurrence"] is None
            first_due = datetime.fromisoformat(status["next_run_at"])
            assert first_due == datetime.fromisoformat(definition["created_at"]) + timedelta(
                minutes=1
            )
            first = _await_occurrence(client, prior_id=None, due=first_due)
            first_id, first_processes = _scheduled_run(
                client,
                first,
                definition["target"],
                cli=cli,
                env=env,
                root=tmp_path,
            )
            second_due = datetime.fromisoformat(_schedule(client)["next_run_at"])
            assert second_due == datetime.fromisoformat(first["finished_at"]) + timedelta(minutes=1)

        with _application_server(cli, tmp_path, repository, catalog, env, "reopened") as client:
            reopened = _schedule(client)
            assert reopened["definition"]["target"]["retained_review_run_id"] == seed_id
            assert reopened["latest_occurrence"] == first
            assert datetime.fromisoformat(reopened["next_run_at"]) == second_due
            second = _await_occurrence(
                client,
                prior_id=first["schedule_run_id"],
                due=second_due,
            )
            second_id, second_processes = _scheduled_run(
                client,
                second,
                definition["target"],
                cli=cli,
                env=env,
                root=tmp_path,
            )
            assert len({seed_id, first_id, second_id}) == 3
            assert first_processes.isdisjoint(second_processes)
            replace = {
                key: definition[key]
                for key in (
                    "generation_id",
                    "record_version",
                    "name",
                    "target",
                    "interval_minutes",
                    "repository_ids",
                    "repository_instance_ids",
                )
            }
            paused = _payload(
                client.put(f"/api/schedules/{_SCHEDULE_ID}", json={**replace, "state": "paused"})
            )
            assert paused["state"] == "paused"
            status = _schedule(client)
            assert status["latest_occurrence"] == second and status["next_run_at"] is None

        assert source.read_bytes() == b"VALUE = 42\n"
        assert not (repository / "unauthorized-write").exists()
        assert git.observe(repository).fingerprint.state_id == original
        assert not model.failures
        assert len(model.completed) == 3 and len(set(model.completed)) == 1
