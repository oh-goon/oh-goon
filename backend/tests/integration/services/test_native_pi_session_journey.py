"""Public CLI continuation and retirement through the original Pi Run owner."""

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api.dependencies import fleet_run_control_plane
from oh_goon.api.routes.fleet_runs import router as run_router
from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.errors import PiSessionRefusalCode, StateConflict
from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.models.agent_review import AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation, NativeAgentObservation
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.pi_session_storage import PI_SESSION_NEW_FILE
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStateUnknownEvent,
    StepAttemptCreatedEvent,
    StepFailedEvent,
    StepSucceededEvent,
    TerminationRequestedEvent,
)
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunWorkerLaunch
from oh_goon.runtime.journal import Journal
from tests.fakes.native_protocol import observe_native_protocol

_SECRET = "synthetic-pi-journey-broker-key"
_PRIVATE_TOOL_OUTPUT = "PI_NATIVE_PRIVATE_TOOL_OUTPUT_53"
_MODEL_REPLY = "The requested review is complete."
_INTERRUPTED_REVIEW = "The interrupted review looks complete."
_RECOVERED_REVIEW = "The current review completed after native retry."
_UNRELATED_TASK = "Keep this separate synthetic Pi session after retiring the first."
_TASKS = (
    "Record the original Pi conversation before exiting Oh-Goon.",
    "Use the earlier Pi conversation in the initialized Plan.",
    "Continue the initialized Plan's Pi session in a fresh CLI process.",
    "Read the explicit prior Run handoff without adopting its native session.",
)

type _ContinuationResult = Literal["error", "length", "malformed", "retry"]


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item["text"] for item in content if item.get("type") == "text")
    return ""


@contextmanager
def _provider(
    hold_inference: Callable[[], None] | None = None,
    *,
    hold_request_number: int = 2,
    reply_after_hold: bool = False,
    continuation_result: _ContinuationResult | None = None,
) -> Iterator[tuple[str, list[str], list[str], list[str], list[str]]]:
    errors: list[str] = []
    requests: list[str] = []
    source_digests: list[str] = []
    cross_harness_handoffs: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                response = self.respond()
                if hold_inference is not None and len(requests) == hold_request_number:
                    hold_inference()
                    if not reply_after_hold:
                        return
                self.reply(*response)
            except Exception as error:
                errors.append(str(error))
                self.reply({"role": "assistant", "content": "Fixture refusal."}, "stop")

        def respond(self) -> tuple[dict[str, Any], str]:
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == f"Bearer {_SECRET}"
            size = int(self.headers["Content-Length"])
            assert 0 < size < 1024 * 1024
            body = json.loads(self.rfile.read(size))
            assert body["model"] == "pi-session-journey-fixture"
            messages = body["messages"]
            users = "\n".join(
                _message_text(message) for message in messages if message["role"] == "user"
            )
            privileged = "\n".join(
                _message_text(message)
                for message in messages
                if message["role"] in {"system", "developer"}
            )
            stages = len(requests)
            phase = min(max(stages - 1, 0), 3)
            if continuation_result == "retry" and stages == 3:
                phase = 1
            unrelated = _UNRELATED_TASK in privileged
            assert stages < (7 if unrelated else 6), "Unexpected broker inference"
            task = _UNRELATED_TASK if unrelated else _TASKS[phase]
            assert task in privileged
            assert "# Session journey Skill" in privileged
            payload = next(
                payload
                for row in users.splitlines()
                if (start := row.find('{"source_authority":')) >= 0
                and (payload := json.JSONDecoder().raw_decode(row[start:])[0])["task"] == task
            )
            evidence = json.loads(payload["repository_evidence"])
            assert evidence["producer"] == "Hashmarks"
            assert (
                evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
            )
            assert evidence["capture_identity"] == payload["identity"]["candidate_identity"]
            assert payload["repository_evidence"] in privileged
            if unrelated:
                assert _UNRELATED_TASK not in requests
                assert not any(old_task in users for old_task in _TASKS)
                assert _PRIVATE_TOOL_OUTPUT not in json.dumps(messages)
                requests.append(task)
                return {"role": "assistant", "content": _MODEL_REPLY}, "stop"
            requests.append(task)
            source_digests.append(evidence["capture_identity"])
            if phase == 3:
                assert _PRIVATE_TOOL_OUTPUT not in json.dumps(messages)
                context = payload["context"]
                assert context["authority"] == "UNTRUSTED_PRIOR_RUN_CONTEXT"
                handoff = context["prior_run_handoff"]
                assert handoff["pi_session"]["native"]["session_id"]
                cross_harness_handoffs.append(handoff["fleet_run_id"])
                return {"role": "assistant", "content": "The separate handoff is complete."}, "stop"
            if stages == 0:
                return (
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-private-journey-tool",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": json.dumps(
                                        {
                                            "command": f"printf %s {shlex.quote(_PRIVATE_TOOL_OUTPUT)}"
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    "tool_calls",
                )
            assert any(
                _PRIVATE_TOOL_OUTPUT in _message_text(message)
                for message in messages
                if message["role"] == "tool"
            ), "Pi forgot the first Run's private native conversation"
            for historical_task in _TASKS[:phase]:
                assert historical_task in users and historical_task not in privileged
            if phase > 0:
                assert {item["function"]["name"] for item in body["tools"]} == {
                    "read",
                    "grep",
                    "find",
                    "ls",
                }
            if phase == 1 and continuation_result is not None:
                identity = payload["identity"]
                scope = 17 if continuation_result == "malformed" else identity["scope_identity"]
                review = {
                    "schema": "oh-goon.agent-review/v1",
                    **identity,
                    "verdict": "CLEAN",
                    "summary": _INTERRUPTED_REVIEW if stages == 2 else _RECOVERED_REVIEW,
                    "reviewed_paths": {"complete_scope": scope},
                    "findings": [],
                }
                return (
                    {"role": "assistant", "content": json.dumps(review)},
                    continuation_result
                    if stages == 2 and continuation_result != "malformed"
                    else "stop",
                )
            return {"role": "assistant", "content": _MODEL_REPLY}, "stop"

        def reply(self, delta: dict[str, Any], finish: str) -> None:
            terminal: dict[str, Any] = {
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]
            }
            if finish in {"error", "retry"}:
                terminal = {
                    "error": {
                        "message": "503 Service Unavailable"
                        if finish == "retry"
                        else "Synthetic terminal inference rejection",
                        "type": "server_error" if finish == "retry" else "invalid_request_error",
                    }
                }
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                terminal,
            ]
            data = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as endpoint:
        thread = Thread(target=endpoint.serve_forever)
        thread.start()
        try:
            yield (
                f"http://127.0.0.1:{endpoint.server_port}/v1",
                requests,
                errors,
                source_digests,
                cross_harness_handoffs,
            )
        finally:
            endpoint.shutdown()
            thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.certification
def test_installed_pi_session_reopens_via_initialized_plan_and_cli(tmp_path: Path) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed Pi executable and public runtime root")
    public_runtime = Path(runtime_root).resolve(strict=True)
    program = Path(executable).resolve(strict=True)
    assert program.is_relative_to(public_runtime)
    opencode = os.environ.get("OH_GOON_NATIVE_OPENCODE")
    opencode_runtime = os.environ.get("OH_GOON_NATIVE_OPENCODE_RUNTIME_ROOT")
    assert bool(opencode) == bool(opencode_runtime)
    assert shutil.which("bwrap")
    package_cli = Path(sys.executable).with_name("oh-goon")
    assert package_cli.is_file()
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(repository)), check=True, timeout=10)
    source = repository / "source.py"
    source.write_text("VALUE = 42\n")
    foreign_repository = repository.with_name("other-app")
    foreign_repository.mkdir()
    subprocess.run(("git", "init", "-q", str(foreign_repository)), check=True, timeout=10)
    foreign_source = foreign_repository / "source.py"
    foreign_source.write_text("VALUE = 43\n")
    verifier = repository / "verify"
    verifier.write_text("#!/bin/sh\nset -eu\ntest \"$(cat source.py)\" = 'VALUE = 43'\n")
    verifier.chmod(0o755)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Session journey Skill\nReview the selected source.\n")
    database = tmp_path / "state" / "app.sqlite"
    session_root = ApplicationStatePathLayout.from_database(database).pi_sessions
    config = tmp_path / "providers.json"
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "OH_GOON_DB": str(database),
        "OH_GOON_PROVIDERS_FILE": str(config),
        "OH_GOON_TEST_PI_JOURNEY_KEY": _SECRET,
    }

    def cli(*args: str, expected: int = 0) -> str:
        result = subprocess.run(
            (str(package_cli), *args),
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=65,
        )
        combined = result.stdout + result.stderr
        assert _SECRET not in combined and _PRIVATE_TOOL_OUTPUT not in combined
        assert str(session_root) not in combined
        assert "/run/oh-goon-pi-session" not in combined
        if args[0] in {"ai", "run", "runs"}:
            assert not any(task in combined for task in (*_TASKS[:3], _UNRELATED_TASK))
            assert _MODEL_REPLY not in combined
        if result.returncode != expected:
            pytest.fail(
                f"{args!r} exited {result.returncode}, expected {expected}\n"
                + result.stderr[-3000:]
                + result.stdout[-5000:]
            )
        return result.stdout

    def observation(run_id: str, session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        result = json.loads(
            cli("runs", "events", run_id, "--after", "0", "--limit", "100", "--json")
        )
        assert result["run"]["fleet_run_id"] == run_id and not result["has_more"]
        assert result["pi_session"] == session
        (reserved,) = [
            event
            for event in result["events"]
            if event["event_type"] == "PI_SESSION_WRITER_RESERVED"
        ]
        (observed,) = [
            event
            for event in result["events"]
            if event["event_type"] == "PI_SESSION_WRITER_OBSERVED"
        ]
        assert observed["reservation_sequence"] == reserved["sequence"]
        assert observed["observation"]["persistence"] == "observed"
        assert any(
            event["event_type"] == "DESCENDANTS_QUIESCENT"
            and event["sequence"] == observed["quiescent_sequence"]
            for event in result["events"]
        )
        return reserved, observed["observation"]

    def session_summary(payload: dict[str, Any], prior_run: str | None) -> dict[str, Any]:
        session = payload["pi_session"]
        assert isinstance(session, dict)
        assert session["mode"] == ("continue" if prior_run is not None else "new")
        assert session["prior_run_id"] == prior_run
        assert session["persistence"] == "observed"
        assert session["byte_identity"] == "digest_recorded"
        assert session["availability"] == "not_checked"
        assert session["repository_source"] and session["skill_identity"]
        assert session["settlement"]["cleanup"] == "VERIFIED"
        (verifier_step,) = session["following_steps"]
        assert verifier_step["settlement"]["outcome"] == "SUCCESS"
        return session

    with _provider() as (url, requests, errors, source_digests, cross_harness_handoffs):
        config.write_text(
            json.dumps(
                {
                    "providers": {
                        "local": {
                            "api": "openai-chat-completions",
                            "base_url": url,
                            "api_key_env": "OH_GOON_TEST_PI_JOURNEY_KEY",
                        }
                    },
                    "models": {
                        "journey": {"provider": "local", "model": "pi-session-journey-fixture"}
                    },
                    "native_profiles": {
                        "pi-rpc": {
                            "backend": "bubblewrap",
                            "allowed_agents": ["pi"],
                            "workspace_roots": [str(repository), str(foreign_repository)],
                            "inference_profile": "journey",
                            "pi_rpc": True,
                            "timeout_seconds": 40,
                        },
                        **(
                            {
                                "opencode": {
                                    "backend": "bubblewrap",
                                    "allowed_agents": ["opencode"],
                                    "workspace_roots": [str(repository)],
                                    "inference_profile": "journey",
                                    "timeout_seconds": 40,
                                }
                            }
                            if opencode
                            else {}
                        ),
                    },
                    "native_agents": {
                        "pi": {"executable": str(program), "runtime_roots": [str(public_runtime)]},
                        **(
                            {
                                "opencode": {
                                    "executable": opencode,
                                    "runtime_roots": [opencode_runtime],
                                }
                            }
                            if opencode
                            else {}
                        ),
                    },
                }
            )
        )
        assert all(
            term in cli("ai", "--help") for term in ("ephemeral", "run:RUN_ID", "--handoff-from")
        )
        ai = ("ai", "--agent", "pi", "--profile", "pi-rpc", "--skill", str(skill))
        first_verifier = shlex.join(
            (
                "/usr/bin/python3",
                "-I",
                "-c",
                "from pathlib import Path; assert Path('source.py').read_text().strip() == 'VALUE = 42'",
            )
        )
        first = json.loads(
            cli(
                *ai,
                "--session",
                "new",
                "--prompt",
                _TASKS[0],
                "--verify",
                first_verifier,
                "--json",
            )
        )
        assert first["outcome"] == "SUCCESS" and not errors, errors
        first_id = first["fleet_run_id"]
        first_session = session_summary(first, None)
        first_reserved, first_native = observation(first_id, first_session)
        assert first_session["native"]["session_id"] == first_native["session_id"]
        assert requests == [_TASKS[0], _TASKS[0]]

        workflow = tmp_path / "workflow"
        cli(
            "init",
            "--template",
            "agents/native-coding",
            "--kind",
            "plan",
            "--output",
            str(workflow),
            "--repository",
            str(repository),
            "--draft",
        )
        package = workflow / "goons" / "agents" / "resume-pi"
        package.mkdir(parents=True)
        (package / "README.md").write_text("# agents/resume-pi\n\nReview the continued session.\n")
        (package / "goon.yml").write_text(
            "inputs:\n  repo: {type: repository_reference}\n  skill: {type: skill_reference}\n"
            "  goal:\n  acceptance: {type: acceptance_profile, default: native-coding}\n"
            "steps:\n- uses: agent\n  harness: native\n  role: report\n"
            "  task: ${{ inputs.goal }}\n"
            f"  session: {{mode: continue, prior_run_id: {first_id}}}\n"
            "  requested_effects: {repository_access: read_only, recipients: [configured_model]}\n"
        )
        (workflow / "plan.yml").write_text(
            "goons:\n- import: agents/resume-pi\n  from: goons\n- agents/verification\n"
        )
        shutil.copytree(skill, workflow / "skills" / "journey")
        run_configuration = workflow / "run.yml"
        authored = yaml.safe_load(run_configuration.read_text())
        authored["source"] = {"kind": "file-plan", "path": "plan.yml"}
        authored["target"] = {"repository": str(repository)}
        authored["agent"] = {
            "implementation": "pi",
            "profile": "pi-rpc",
            "repository_root": str(repository.parent),
            "acceptance": "native-coding",
            "skill_root": "skills",
        }
        authored["inputs"] = {
            "repo": "local:app",
            "skill": "local:journey",
            "goal": _TASKS[1],
            "verification_command": "verify",
        }
        run_configuration.write_text(yaml.safe_dump(authored, sort_keys=False))
        source.write_text("VALUE = 43\n")
        cli("workflow", "validate", str(run_configuration), "--json")
        cli("workflow", "prepare", str(run_configuration), "--no-input")
        assert requests == [_TASKS[0], _TASKS[0]]
        second = json.loads(cli("run", str(run_configuration), "--json", "--no-input"))
        assert second["outcome"] == "SUCCESS" and not errors, errors
        second_id = second["fleet_run_id"]
        second_session = session_summary(second, first_id)
        second_reserved, second_native = observation(second_id, second_session)
        assert second_reserved["continuation"]["prior_run_id"] == first_id
        assert second_reserved["storage_id"] == first_reserved["storage_id"]
        assert second_native["session_id"] == first_native["session_id"]
        assert second_native["leaf_id"] != first_native["leaf_id"]
        assert second_native["file"]["inode"] == first_native["file"]["inode"]
        assert second_session["native"]["leaf_id"] == second_native["leaf_id"]
        assert source_digests[0] != source_digests[2]
        assert first_id != second_id

        for readback in ("handoff", "outputs", "tape"):
            payload = json.loads(cli("runs", readback, second_id, "--json"))
            assert payload["fleet_run_id"] == second_id
            assert payload["pi_session"] == second_session
        human_handoff = cli("runs", "handoff", second_id)
        assert f"Previous Pi Run {first_id}" in human_handoff
        assert "Current Pi session availability was not checked" in human_handoff
        feedback = json.loads(cli("runs", "feedback", second_id, "--json"))
        (goal,) = feedback["goals"]
        assert goal["summary"] is None and goal["sha256"]
        read_database = LocalDatabase(database, read_only=True)
        revision = read_database.durable_revision()
        view = Journal(read_database).read_view()
        api = FastAPI()
        api.include_router(run_router, prefix="/api")
        api.dependency_overrides[fleet_run_control_plane] = lambda: SimpleNamespace(
            get=view.required_run_detail,
            events=view.run_events,
            history=view.run_history,
        )
        with TestClient(api) as client:
            detail_response = client.get(f"/api/fleet/runs/{second_id}")
            event_response = client.get(f"/api/fleet/runs/{second_id}/events?after=0&limit=1")
            history_response = client.get("/api/fleet/runs")
        for response in (detail_response, event_response, history_response):
            assert response.status_code == 200
            for private in (
                *_TASKS,
                _MODEL_REPLY,
                _PRIVATE_TOOL_OUTPUT,
                _SECRET,
                str(session_root),
                "/run/oh-goon-pi-session",
            ):
                assert private not in response.text
        api_detail = detail_response.json()
        api_events = event_response.json()
        assert (
            api_detail["game_tape"]["pi_session"]
            == api_events["pi_session"]
            == second["pi_session"]
        )
        assert api_events["next_sequence"] == 1 and api_events["has_more"]
        assert {item["run"]["fleet_run_id"] for item in history_response.json()["items"]} == {
            first_id,
            second_id,
        }
        operation = api_detail["operations"][0]
        (native_step,) = [
            step for step in operation["evidence"]["plan"]["steps"] if step["kind"] == "agent"
        ]
        assert native_step["argv"] == [] and native_step["attempts"][0]["argv"] == []
        assert all(
            not claim["events"]
            for claim in native_step["observations"]
            if claim["kind"] == "native_agent"
        )
        assert read_database.durable_revision() == revision
        assert requests == [_TASKS[0], _TASKS[0], _TASKS[1]]
        blocked = json.loads(
            cli(
                *ai,
                "--session",
                f"run:{second_id}",
                "--handoff-from",
                first_id,
                "--prompt",
                _TASKS[2],
                "--json",
                expected=2,
            )
        )
        assert blocked["code"] == "PI_SESSION_SELECTION_INVALID"
        assert requests == [_TASKS[0], _TASKS[0], _TASKS[1]]
        third = json.loads(
            cli(
                *ai,
                "--session",
                f"run:{second_id}",
                "--prompt",
                _TASKS[2],
                "--verify",
                "./verify",
                "--json",
            )
        )
        assert third["outcome"] == "SUCCESS" and not errors, errors
        third_id = third["fleet_run_id"]
        third_session = session_summary(third, second_id)
        third_reserved, third_native = observation(third_id, third_session)
        assert third_session["native"]["leaf_id"] == third_native["leaf_id"]
        assert third_reserved["continuation"]["prior_run_id"] == second_id
        assert third_id not in {first_id, second_id}
        assert third_native["session_id"] == first_native["session_id"]
        assert third_native["leaf_id"] != second_native["leaf_id"]
        assert requests == [_TASKS[0], _TASKS[0], _TASKS[1], _TASKS[2]]
        assert source.read_text() == "VALUE = 43\n"
        completed = {first_id, second_id, third_id}
        if opencode:
            cross = json.loads(
                cli(
                    "ai",
                    "--agent",
                    "opencode",
                    "--profile",
                    "opencode",
                    "--skill",
                    str(skill),
                    "--handoff-from",
                    third_id,
                    "--prompt",
                    _TASKS[3],
                    "--verify",
                    "./verify",
                    "--json",
                )
            )
            assert cross["outcome"] == "SUCCESS" and not errors, errors
            cross_id = cross["run"]["fleet_run_id"]
            assert cross_id not in completed
            completed.add(cross_id)
            assert cross_harness_handoffs and set(cross_harness_handoffs) == {third_id}
            tape = json.loads(cli("runs", "tape", cross_id, "--json"))
            assert tape.get("pi_session") is None
            agent_step, verifier_step = tape["repositories"][0]["steps"]
            assert agent_step["native_agent_observations"][0]["agent"] == "opencode"
            assert verifier_step["settlement"]["outcome"] == "SUCCESS"
        inference_count = len(requests)
        missing_id = "absent-pi-journey"
        missing = json.loads(
            cli(
                *ai,
                "--session",
                f"run:{missing_id}",
                "--prompt",
                _TASKS[2],
                "--json",
                expected=2,
            )
        )
        assert missing["code"] == "PI_NO_PERSISTENT_SESSION"
        assert missing["prior_run_handoff_argv"] == [
            "oh-goon",
            "runs",
            "handoff",
            missing_id,
            "--json",
        ]
        assert len(requests) == inference_count and not errors

        separate = json.loads(
            cli(
                *ai,
                "--session",
                "new",
                "--prompt",
                _UNRELATED_TASK,
                "--verify",
                "./verify",
                "--json",
            )
        )
        assert separate["outcome"] == "SUCCESS" and not errors, errors
        separate_id = separate["fleet_run_id"]
        assert separate_id not in completed
        completed.add(separate_id)
        separate_session = session_summary(separate, None)
        separate_reserved, separate_native = observation(separate_id, separate_session)
        assert separate_native["session_id"] != third_native["session_id"]
        assert separate_reserved["storage_id"] != third_reserved["storage_id"]
        native_file = session_root / third_reserved["storage_id"] / PI_SESSION_NEW_FILE.name
        separate_file = session_root / separate_reserved["storage_id"] / PI_SESSION_NEW_FILE.name
        assert native_file.stat().st_ino == third_native["file"]["inode"]
        separate_before = separate_file.stat()
        assert separate_before.st_ino == separate_native["file"]["inode"]
        assert len(requests) == inference_count + 1 and requests[-1] == _UNRELATED_TASK

        # History surfaces cannot turn private native storage into an artifact.
        logs = json.loads(cli("runs", "logs", third_id, "--json", expected=3))
        assert logs["fleet_run_id"] == third_id and logs["state"] == "UNAVAILABLE"
        assert not logs["chunks"]
        destination = tmp_path / "undeclared-export.jsonl"
        cli(
            "runs",
            "export-artifact",
            third_id,
            "--step",
            third_session["step_id"],
            "--path",
            PI_SESSION_NEW_FILE.name,
            "--output",
            str(destination),
            "--json",
            expected=2,
        )
        assert not destination.exists()
        events_before = json.loads(
            cli("runs", "events", third_id, "--after", "0", "--limit", "100", "--json")
        )["events"]
        tape_before = json.loads(cli("runs", "tape", third_id, "--json"))
        before = json.loads(cli("runs", "pi-session", third_id, "--json"))
        assert before["availability"] == "available" and before["retirement"] == "none"
        assert before["native"] == third_session["native"]
        cli("runs", "retire-pi-session", third_id, "--json", expected=2)
        assert native_file.stat().st_ino == third_native["file"]["inode"]

        retired = json.loads(cli("runs", "retire-pi-session", third_id, "--authorize", "--json"))
        assert retired["fleet_run_id"] == third_id and retired["native"] == before["native"]
        assert retired["availability"] == "unavailable" and retired["retirement"] == "verified"
        assert retired["refusal_code"] == "PI_SESSION_UNAVAILABLE"
        with pytest.raises(FileNotFoundError):
            native_file.lstat()
        assert separate_file.stat() == separate_before
        assert (
            json.loads(cli("runs", "pi-session", separate_id, "--json"))["availability"]
            == "available"
        )
        assert json.loads(cli("runs", "pi-session", third_id, "--json")) == retired
        assert (
            json.loads(cli("runs", "retire-pi-session", third_id, "--authorize", "--json"))
            == retired
        )

        events_after = json.loads(
            cli("runs", "events", third_id, "--after", "0", "--limit", "100", "--json")
        )["events"]
        assert events_after[: len(events_before)] == events_before
        assert [event["event_type"] for event in events_after[len(events_before) :]] == [
            "PI_SESSION_RETIREMENT_REQUESTED",
            "PI_SESSION_RETIRED",
        ]
        tape_after = json.loads(cli("runs", "tape", third_id, "--json"))
        assert tape_after["repositories"] == tape_before["repositories"]
        assert tape_after["pi_session"] == tape_before["pi_session"]
        assert tape_after["status"] == tape_before["status"] == "PASSED"
        assert json.loads(cli("runs", "handoff", first_id, "--json"))["pi_session"] == first_session
        blocked = json.loads(
            cli(*ai, "--session", f"run:{third_id}", "--prompt", _TASKS[2], "--json", expected=2)
        )
        assert blocked["code"] == "PI_SESSION_UNAVAILABLE"
        assert len(requests) == inference_count + 1 and not errors
        history = json.loads(cli("runs", "list", "--json"))
        assert {item["fleet_run_id"] for item in history["items"]} == completed

        before_foreign = separate_file.stat()
        cli("league", "add-folder", str(foreign_repository))
        foreign = json.loads(
            cli(
                *ai,
                "--workspace",
                str(foreign_repository),
                "--session",
                f"run:{separate_id}",
                "--prompt",
                _TASKS[2],
                "--json",
                expected=2,
            )
        )
        assert foreign["code"] == "PI_SESSION_IDENTITY_MISMATCH"
        assert foreign["prior_run_id"] == separate_id
        assert foreign_source.read_text() == "VALUE = 43\n"
        assert separate_file.stat() == before_foreign
        history = json.loads(cli("runs", "list", "--json"))
        assert {item["fleet_run_id"] for item in history["items"]} == completed
        assert len(requests) == inference_count + 1 and not errors

        # The saved bytes still exist under a neighboring name. A fresh claimed
        # Run must refuse the absent exact file without starting Pi or discovering
        # the renamed resource as an alternative session.
        retained_directories = set(session_root.iterdir())
        renamed = separate_file.with_name("moved.jsonl")
        separate_file.rename(renamed)
        rejected = json.loads(
            cli(
                *ai,
                "--session",
                f"run:{separate_id}",
                "--prompt",
                _TASKS[2],
                "--json",
                expected=1,
            )
        )
        rejected_id = rejected["fleet_run_id"]
        assert rejected_id not in completed
        assert rejected["status"] == "FAILED"
        assert rejected["pi_session"]["prior_run_id"] == separate_id
        assert rejected["pi_session"]["refusal_code"] == "PI_SESSION_UNAVAILABLE"
        assert rejected["pi_session"]["persistence"] == "unknown"
        rejected_events = json.loads(
            cli("runs", "events", rejected_id, "--after", "0", "--limit", "100", "--json")
        )["events"]
        (fence,) = [
            event
            for event in rejected_events
            if event["event_type"] == "PI_SESSION_WRITER_RESERVED"
        ]
        assert fence["storage_id"] == separate_reserved["storage_id"]
        assert fence["continuation"]["prior_run_id"] == separate_id
        assert (
            rejected_events[fence["attempt_sequence"] - 1]["event_type"] == "STEP_ATTEMPT_CREATED"
        )
        assert not any(
            event["sequence"] > fence["sequence"] and event["step_id"] == fence["step_id"]
            for event in rejected_events
            if event["event_type"] == "PROCESS_STARTING"
        )
        assert not any(
            event["event_type"] == "PI_SESSION_WRITER_OBSERVED" for event in rejected_events
        )
        assert set(session_root.iterdir()) == retained_directories
        assert set(separate_file.parent.iterdir()) == {renamed}
        assert not tuple(native_file.parent.iterdir())
        assert renamed.stat().st_ino == separate_native["file"]["inode"]
        with renamed.open("rb") as stream:
            assert (
                hashlib.file_digest(stream, "sha256").hexdigest()
                == separate_native["file"]["sha256"]
            )
        assert len(requests) == inference_count + 1 and not errors
        history = json.loads(cli("runs", "list", "--json"))
        assert {item["fleet_run_id"] for item in history["items"]} == completed | {rejected_id}
        print(
            json.dumps(
                {
                    "pi": "create -> initialized-plan -> cli -> retired",
                    "exact_open_refusals": ["different repository", "renamed retained file"],
                    "cross_harness": bool(opencode),
                }
            )
        )


@dataclass(frozen=True)
class _HeldPiJourney:
    repository: Path
    skill: Path
    database_path: Path
    config: Path
    ai: tuple[str, ...]
    held_inference_arrived: Event
    release_held_inference: Event
    requests: list[str]
    errors: list[str]

    def invoke(self, session: str, task: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (*self.ai, "--prompt", task, "--session", session),
            cwd=self.repository,
            capture_output=True,
            text=True,
            timeout=65,
        )


@contextmanager
def _held_pi_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hold_request_number: int = 2,
    reply_after_hold: bool = False,
    continuation_result: _ContinuationResult | None = None,
) -> Iterator[_HeldPiJourney]:
    executable = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed Pi executable and public runtime root")
    assert shutil.which("bwrap")
    public_runtime = Path(runtime_root).resolve(strict=True)
    program = Path(executable).resolve(strict=True)
    assert program.is_relative_to(public_runtime)
    package_cli = Path(sys.executable).with_name("oh-goon")
    assert package_cli.is_file()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "-q", str(repository)), check=True, timeout=10)
    (repository / "source.py").write_text("VALUE = 42\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Session journey Skill\nReview the selected source.\n")
    database_path = tmp_path / "state" / "app.sqlite"
    config = tmp_path / "providers.json"
    for name, path in (
        ("HOME", tmp_path / "home"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("OH_GOON_DB", database_path),
        ("OH_GOON_LEAGUE_FILE", tmp_path / "league.yml"),
        ("OH_GOON_PROVIDERS_FILE", config),
    ):
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("OH_GOON_TEST_PI_JOURNEY_KEY", _SECRET)

    held_inference_arrived, release_held_inference = Event(), Event()

    def hold_inference() -> None:
        held_inference_arrived.set()
        assert release_held_inference.wait(65), "Fixture did not release the held inference"

    ai = (
        str(package_cli),
        "ai",
        "--agent",
        "pi",
        "--profile",
        "pi-rpc",
        "--skill",
        str(skill),
        "--verify",
        "/usr/bin/true",
        "--json",
    )
    with _provider(
        hold_inference,
        hold_request_number=hold_request_number,
        reply_after_hold=reply_after_hold,
        continuation_result=continuation_result,
    ) as (
        url,
        requests,
        errors,
        _,
        _,
    ):
        config.write_text(
            json.dumps(
                {
                    "providers": {
                        "local": {
                            "api": "openai-chat-completions",
                            "base_url": url,
                            "api_key_env": "OH_GOON_TEST_PI_JOURNEY_KEY",
                        }
                    },
                    "models": {
                        "journey": {"provider": "local", "model": "pi-session-journey-fixture"}
                    },
                    "native_profiles": {
                        "pi-rpc": {
                            "backend": "bubblewrap",
                            "allowed_agents": ["pi"],
                            "workspace_roots": [str(repository)],
                            "inference_profile": "journey",
                            "pi_rpc": True,
                            "timeout_seconds": 90,
                        }
                    },
                    "native_agents": {
                        "pi": {"executable": str(program), "runtime_roots": [str(public_runtime)]}
                    },
                }
            )
        )
        try:
            yield _HeldPiJourney(
                repository,
                skill,
                database_path,
                config,
                ai,
                held_inference_arrived,
                release_held_inference,
                requests,
                errors,
            )
        finally:
            release_held_inference.set()


@pytest.mark.certification
def test_installed_pi_worker_crash_preserves_reserved_native_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        pytest.skip("The installed worker crash cut requires Linux pidfds")
    with _held_pi_journey(tmp_path, monkeypatch) as journey:
        database_path = journey.database_path
        session_root = ApplicationStatePathLayout.from_database(database_path).pi_sessions
        log_path = tmp_path / "controller.log"
        with log_path.open("wb") as console:
            controller = subprocess.Popen(
                (*journey.ai, "--prompt", _TASKS[0], "--session", "new"),
                cwd=journey.repository,
                stdout=console,
                stderr=subprocess.STDOUT,
            )
        try:
            assert journey.held_inference_arrived.wait(65), (
                journey.errors,
                log_path.read_text()[-3000:],
            )
            database = LocalDatabase(database_path, read_only=True)
            lease = ExecutionCoordinator(database).active_lease()
            assert lease is not None and lease.fleet_run_id and lease.custody is not None
            run_id, custody = lease.fleet_run_id, lease.custody
            assert custody.phase == "WORKER_OWNED"
            assert custody.owner_pid not in {os.getpid(), controller.pid}
            active_journal = Journal(database)
            live = active_journal.run_events(run_id)
            assert live.run.status is FleetRunStatus.RUNNING
            assert active_journal.inspect_pi_session(run_id).availability == "busy"
            (reservation,) = (
                event for event in live.events if isinstance(event, PiSessionWriterReservedEvent)
            )
            (started,) = (
                event
                for event in live.events
                if type(event) is ProcessStartedEvent
                and event.step_id == reservation.step_id
                and event.sequence > reservation.sequence
            )
            assert reservation.attempt_sequence < reservation.sequence < started.sequence
            assert not any(isinstance(event, PiSessionWriterObservedEvent) for event in live.events)
            slot = session_root / reservation.storage_id
            native_file = slot / PI_SESSION_NEW_FILE.name
            before = native_file.stat()
            assert 0 < before.st_size < 1024 * 1024
            with native_file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()

            identity = SystemLeaseEnvironment()
            assert identity.process_identity(custody.owner_pid) == custody.owner_identity
            worker = os.pidfd_open(custody.owner_pid)
            try:
                assert identity.process_identity(custody.owner_pid) == custody.owner_identity
                controller.kill()
                controller.wait(timeout=10)
                assert identity.process_alive(custody.owner_pid)
                signal.pidfd_send_signal(worker, signal.SIGKILL)
                exited = select.poll()
                exited.register(worker, select.POLLIN)
                assert exited.poll(10_000), "Original claimed Run worker did not exit"
            finally:
                os.close(worker)
        finally:
            journey.release_held_inference.set()
            if controller.poll() is None:
                try:
                    controller.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    controller.kill()
                    controller.wait(timeout=5)

        interrupted = Journal(LocalDatabase(database_path, read_only=True)).run_events(run_id)
        assert interrupted.run.status is FleetRunStatus.RUNNING
        recovered = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        try:
            reconciled = recovered.recovery.recover_worker_exit(
                run_id, quiescence_timeout_seconds=10
            )
            (quarantine,) = reconciled.effect_quarantines
            assert quarantine.operation_id == reservation.operation_id
            acknowledged = recovered.recovery.acknowledge(
                quarantine.operation_id,
                note="Inspected the synthetic Pi crash fixture",
                expected_record_version=quarantine.record_version,
            )
            assert acknowledged.effect_quarantines == ()
        finally:
            recovered.shutdown(wait_for_runs=True)
        reopened = LocalDatabase(database_path, read_only=True)
        journal = Journal(reopened)
        failed = journal.run_events(run_id)
        assert failed.run.status is FleetRunStatus.FAILED
        assert failed.events[: len(live.events)] == live.events
        assert not any(
            isinstance(event, (PiSessionWriterObservedEvent, StepSucceededEvent))
            for event in failed.events
        )
        native_settlement = tuple(
            type(event)
            for event in failed.events[len(live.events) :]
            if isinstance(event, ProcessStartedEvent)
            and event.physical_execution_id == started.physical_execution_id
        )
        assert native_settlement == (ProcessStateUnknownEvent, DescendantsQuiescentEvent)
        assert failed.pi_session is not None and failed.pi_session.persistence == "unknown"
        assert failed.pi_session.native is None
        inspection = journal.inspect_pi_session(run_id)
        assert inspection.availability == "orphaned" and inspection.native is None
        assert inspection.retirement == "none"
        assert inspection.refusal_code == PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
        with reopened.transaction() as connection:
            assert ExecutionCoordinator(reopened).retained_leases_in(connection) == ()

        refused = journey.invoke(f"run:{run_id}", _TASKS[0])
        assert refused.returncode == 2, (refused.stdout[-3000:], refused.stderr[-3000:])
        assert json.loads(refused.stdout)["code"] == PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
        assert journal.run_events(run_id) == failed
        assert journey.requests == [_TASKS[0], _TASKS[0]] and not journey.errors
        assert tuple(session_root.iterdir()) == (slot,)
        after = native_file.stat()
        assert (after.st_dev, after.st_ino, after.st_size) == (
            before.st_dev,
            before.st_ino,
            before.st_size,
        )
        with native_file.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == digest


@pytest.mark.certification
def test_installed_pi_active_continuation_fences_second_controller_through_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _held_pi_journey(tmp_path, monkeypatch, hold_request_number=3) as journey:
        initial = journey.invoke("new", _TASKS[0])
        assert initial.returncode == 0, (initial.stdout[-3000:], initial.stderr[-3000:])
        seed = json.loads(initial.stdout)
        assert seed["outcome"] == "SUCCESS"
        seed_id = seed["fleet_run_id"]
        prior = f"run:{seed_id}"
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="pi-rpc",
            workspace=journey.repository,
            config_file=journey.config,
            session=PiSessionIntent(mode="continue", prior_run_id=seed_id),
        )
        runtime = ApplicationRuntime.build(
            standard_local=True,
            standard_process_isolation=backend,
            git_service=git,
            startup_purpose="workflow",
        )
        try:
            writer = runtime.plans.run_native_agent(
                selected=selected,
                workspace=journey.repository,
                skill=journey.skill,
                task=_TASKS[1],
                verification=(("/usr/bin/true",),),
            )
            run_id = writer.fleet_run_id
            assert journey.held_inference_arrived.wait(65), journey.errors
            reader = LocalDatabase(journey.database_path, read_only=True)
            journal = Journal(reader)
            coordinator = ExecutionCoordinator(reader)
            live = journal.run_events(run_id)
            live_revision = journal.read_view().revision
            lease = coordinator.active_lease()
            assert lease is not None and lease.fleet_run_id == run_id and lease.custody is not None
            assert lease.custody.phase == "WORKER_OWNED" and lease.custody.owner_pid != os.getpid()
            assert live.run.status is FleetRunStatus.RUNNING
            assert journal.inspect_pi_session(seed_id).availability == "busy"
            (reservation,) = (
                event for event in live.events if isinstance(event, PiSessionWriterReservedEvent)
            )
            assert reservation.continuation is not None
            assert reservation.continuation.prior_run_id == seed_id
            (writer_start,) = (
                event
                for event in live.events
                if type(event) is ProcessStartedEvent
                and event.step_id == reservation.step_id
                and event.sequence > reservation.sequence
            )
            busy = journey.invoke(prior, _TASKS[1])
            assert busy.returncode == 2, (busy.stdout[-3000:], busy.stderr[-3000:])
            assert json.loads(busy.stdout)["code"] == PiSessionRefusalCode.BUSY
            assert coordinator.active_lease() == lease
            assert journal.read_view().revision == live_revision
            assert journey.requests == [_TASKS[0], _TASKS[0], _TASKS[1]]

            cancel = runtime.runs.cancel(run_id)
            assert cancel.status.cancel_request_satisfied
            assert runtime.runs.wait_for_terminal(run_id, timeout=25).run.status is (
                FleetRunStatus.CANCELLED
            )
        finally:
            journey.release_held_inference.set()
            runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=2)

        journal = Journal(LocalDatabase(journey.database_path, read_only=True))
        retained = journal.run_events(run_id)
        assert retained.run.status is FleetRunStatus.CANCELLED
        assert retained.events[: len(live.events)] == live.events
        original_lifetime = [
            event
            for event in retained.events
            if isinstance(
                event, (TerminationRequestedEvent, ProcessExitedEvent, DescendantsQuiescentEvent)
            )
            and event.physical_execution_id == writer_start.physical_execution_id
        ]
        assert [type(event) for event in original_lifetime] == [
            TerminationRequestedEvent,
            ProcessExitedEvent,
            DescendantsQuiescentEvent,
        ]
        (observed,) = (
            event for event in retained.events if isinstance(event, PiSessionWriterObservedEvent)
        )
        assert observed.reservation_sequence == reservation.sequence
        assert observed.quiescent_sequence == original_lifetime[-1].sequence < observed.sequence
        assert observed.observation.persistence == "unknown" and observed.observation.file is None
        assert not any(isinstance(event, StepSucceededEvent) for event in retained.events)
        assert coordinator.active_lease() is None

        inspection = journal.inspect_pi_session(run_id)
        assert inspection.availability == "unverified"
        assert inspection.native is None
        assert inspection.refusal_code == PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
        assert journal.inspect_pi_session(seed_id).availability == "unavailable"
        after_cancel = journey.invoke(f"run:{run_id}", _TASKS[2])
        assert after_cancel.returncode == 2, (
            after_cancel.stdout[-3000:],
            after_cancel.stderr[-3000:],
        )
        assert json.loads(after_cancel.stdout)["code"] == "REPOSITORY_EFFECT_QUARANTINED"
        final = journal.run_events(run_id)
        assert (final.run, final.events, final.pi_session) == (
            retained.run,
            retained.events,
            retained.pi_session,
        )
        assert journey.requests == [_TASKS[0], _TASKS[0], _TASKS[1]]
        assert not journey.errors


@pytest.mark.certification
@pytest.mark.parametrize("result", ["error", "length", "malformed", "retry"])
def test_installed_pi_resumed_turn_keeps_native_and_step_results_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: _ContinuationResult
) -> None:
    recovering = result == "retry"
    with _held_pi_journey(
        tmp_path,
        monkeypatch,
        hold_request_number=3,
        reply_after_hold=True,
        continuation_result=result,
    ) as journey:
        initial = journey.invoke("new", _TASKS[0])
        assert initial.returncode == 0, (initial.stdout[-3000:], initial.stderr[-3000:])
        seed_id = json.loads(initial.stdout)["fleet_run_id"]
        reader = Journal(LocalDatabase(journey.database_path, read_only=True))
        seed = reader.run_events(seed_id)
        assert seed.pi_session is not None and seed.pi_session.persistence == "observed"

        read_protocol = observe_native_protocol(tmp_path, monkeypatch)
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="pi-rpc",
            workspace=journey.repository,
            config_file=journey.config,
            session=PiSessionIntent(mode="continue", prior_run_id=seed_id),
        )
        runtime = ApplicationRuntime.build(
            standard_local=True,
            standard_process_isolation=backend,
            git_service=git,
            startup_purpose="workflow",
        )
        try:
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=journey.repository,
                skill=journey.skill,
                task=_TASKS[1],
                role="review",
                verification=(("/usr/bin/true",),),
            )
            run_id = admitted.fleet_run_id
            assert journey.held_inference_arrived.wait(65), journey.errors
            journey.release_held_inference.set()
            detail = runtime.runs.wait_for_terminal(run_id, timeout=60)
            assert not journey.errors, journey.errors
            assert detail.outcome is (
                FleetRunOutcome.SUCCESS if recovering else FleetRunOutcome.ATTENTION_REQUIRED
            )
            events = runtime.runs.events(run_id).events
            protocol = read_protocol(detail)
        finally:
            journey.release_held_inference.set()
            runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=2)

        records = [json.loads(line) for line in protocol["stdout"].splitlines()]
        assistant_messages = [
            record["message"]
            for record in records
            if record["type"] == "message_end" and record["message"]["role"] == "assistant"
        ]
        expected_reasons = (
            ["error", "stop"] if recovering else ["stop" if result == "malformed" else result]
        )
        assert [message["stopReason"] for message in assistant_messages] == expected_reasons
        assert _INTERRUPTED_REVIEW in _message_text(assistant_messages[0])
        kinds = [record["type"] for record in records]
        assert kinds.count("agent_settled") == 1
        assert kinds.index("agent_end") < kinds.index("agent_settled")
        if recovering:
            assert kinds.index("auto_retry_start") < kinds.index("auto_retry_end")
            assert kinds.index("auto_retry_end") < kinds.index("agent_settled")

        agent, verifier = detail.game_tape.repositories[0].steps
        assert agent.settlement.observed_returncode == 0
        assert agent.settlement.outcome == ("SUCCESS" if recovering else "FAILED")
        assert agent.settlement.cleanup == "VERIFIED"
        assert len(agent.attempts) == 1
        operation = detail.operations[0].evidence
        assert isinstance(operation, FleetPlanOperationEvidence)
        agent_result = operation.plan.steps[0]
        (native,) = (
            item for item in agent_result.observations if isinstance(item, NativeAgentObservation)
        )
        assert native.settled and native.failed is (result in {"error", "length"})
        assert all(_MODEL_REPLY not in item.summary for item in native.events)
        reviews = [
            item.record
            for item in agent_result.observations
            if isinstance(item, AgentReviewObservation)
        ]
        if recovering:
            (review,) = reviews
            assert isinstance(review, AgentReviewRecord) and review.summary == _RECOVERED_REVIEW
        else:
            assert not reviews
        assert (
            any(
                isinstance(event, StepAttemptCreatedEvent) and event.step_id == verifier.step_id
                for event in events
            )
            is recovering
        )
        if recovering:
            assert verifier.settlement.outcome == "SUCCESS"

        (receipt,) = (event for event in events if isinstance(event, PiSessionWriterObservedEvent))
        quiescent = events[receipt.quiescent_sequence - 1]
        assert isinstance(quiescent, DescendantsQuiescentEvent)
        assert receipt.observation.persistence == "observed"
        (exited,) = (
            event
            for event in events
            if isinstance(event, ProcessExitedEvent)
            and event.physical_execution_id == quiescent.physical_execution_id
        )
        (terminal,) = (
            event
            for event in events
            if isinstance(event, (StepFailedEvent, StepSucceededEvent))
            and event.step_id == agent.step_id
        )
        assert exited.returncode == 0
        assert exited.sequence < quiescent.sequence < receipt.sequence < terminal.sequence

        reopened = Journal(LocalDatabase(journey.database_path, read_only=True))
        retained = reopened.read_view().required_run_detail(run_id)
        assert retained.run == detail.run and retained.operations == detail.operations
        assert retained.game_tape.repositories[0].steps == detail.game_tape.repositories[0].steps
        assert reopened.run_events(run_id).events == events
        assert reopened.inspect_pi_session(run_id).availability == (
            "available" if recovering else "unverified"
        )
        retained_seed = reopened.run_events(seed_id)
        assert (retained_seed.run, retained_seed.events, retained_seed.pi_session) == (
            seed.run,
            seed.events,
            seed.pi_session,
        )
        expected_requests = [_TASKS[0], _TASKS[0], _TASKS[1]]
        assert journey.requests == (
            expected_requests + [_TASKS[1]] if recovering else expected_requests
        )


@pytest.mark.certification
def test_installed_pi_torn_save_stays_unqualified_after_successful_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _held_pi_journey(tmp_path, monkeypatch, reply_after_hold=True) as journey:
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="pi-rpc",
            workspace=journey.repository,
            config_file=journey.config,
            session=PiSessionIntent(mode="new"),
        )
        runtime = ApplicationRuntime.build(
            standard_local=True,
            standard_process_isolation=backend,
            git_service=git,
            startup_purpose="workflow",
        )
        try:
            runtime.locations.add(journey.repository)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=journey.repository,
                skill=journey.skill,
                task=_TASKS[0],
                verification=(("/usr/bin/true",),),
            )
            run_id = admitted.fleet_run_id
            assert journey.held_inference_arrived.wait(65), journey.errors
            journal = Journal(LocalDatabase(journey.database_path, read_only=True))
            held = journal.run_events(run_id)
            assert held.run.status is FleetRunStatus.RUNNING
            (reservation,) = (
                event for event in held.events if isinstance(event, PiSessionWriterReservedEvent)
            )
            assert not any(isinstance(event, PiSessionWriterObservedEvent) for event in held.events)
            session_root = ApplicationStatePathLayout.from_database(
                journey.database_path
            ).pi_sessions
            transcript = session_root / reservation.storage_id / PI_SESSION_NEW_FILE.name
            before = transcript.read_bytes()
            assert before.endswith(b"\n") and _PRIVATE_TOOL_OUTPUT.encode() in before
            incomplete = b'{"type":"h76-incomplete-fixture"'
            # Feed Pi a normal final response after leaving a partial append in its owned file.
            with transcript.open("ab", buffering=0) as stream:
                assert stream.write(incomplete) == len(incomplete)
            journey.release_held_inference.set()
            detail = runtime.runs.wait_for_terminal(run_id, timeout=40)
        finally:
            journey.release_held_inference.set()
            runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=2)

        assert detail.outcome is FleetRunOutcome.SUCCESS
        native, verifier = detail.game_tape.repositories[0].steps
        assert native.settlement is not None and native.settlement.outcome == "SUCCESS"
        assert any(observation.settled for observation in native.native_agent_observations)
        assert verifier.settlement is not None and verifier.settlement.outcome == "SUCCESS"
        retained = transcript.read_bytes()
        assert retained.startswith(before + incomplete) and len(retained) > len(before + incomplete)
        with pytest.raises(json.JSONDecodeError):
            json.loads(retained[len(before) :].split(b"\n", 1)[0])

        reopened = Journal(LocalDatabase(journey.database_path, read_only=True))
        settled = reopened.run_events(run_id)
        assert settled.events[: len(held.events)] == held.events
        (receipt,) = (
            event for event in settled.events if isinstance(event, PiSessionWriterObservedEvent)
        )
        assert receipt.reservation_sequence == reservation.sequence
        assert isinstance(settled.events[receipt.quiescent_sequence - 1], DescendantsQuiescentEvent)
        assert receipt.observation.persistence == "unknown" and receipt.observation.file is None
        assert settled.pi_session is not None and settled.pi_session.persistence == "unknown"
        assert settled.pi_session.native is None
        assert reopened.inspect_pi_session(run_id).availability == "unverified"

        fresh = subprocess.run(
            (journey.ai[0], "runs", "events", run_id, "--after", "0", "--limit", "100", "--json"),
            cwd=journey.repository,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert fresh.returncode == 0, fresh.stderr
        assert json.loads(fresh.stdout)["pi_session"]["persistence"] == "unknown"
        refused = journey.invoke(f"run:{run_id}", _TASKS[1])
        assert refused.returncode == 2, (refused.stdout, refused.stderr)
        assert json.loads(refused.stdout)["code"] == PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
        visible = fresh.stdout + fresh.stderr + refused.stdout + refused.stderr
        assert incomplete.decode() not in visible and _PRIVATE_TOOL_OUTPUT not in visible
        assert _SECRET not in visible and str(transcript) not in visible
        assert transcript.read_bytes() == retained
        assert tuple(session_root.iterdir()) == (transcript.parent,)
        assert journey.requests == [_TASKS[0], _TASKS[0]] and not journey.errors


_PI_SAVE_WORKER_FAULT = """
import errno
import os
import runpy
import signal
import sys
from pathlib import Path

from oh_goon.core.models.pi_session_storage import PI_SESSION_NEW_FILE
from oh_goon.runtime.journal import Journal

cut, fault_fd, worker, *worker_args = sys.argv[1:]

def reached_cut():
    assert os.write(int(fault_fd), b'R') == 1

def is_session_file(descriptor):
    return Path(os.readlink(f'/proc/self/fd/{descriptor}')).name == PI_SESSION_NEW_FILE.name

if cut == 'publication':
    def interrupt_receipt(self, session, reservation, *, observation):
        assert observation.persistence == 'observed'
        assert observation.file is not None and observation.file.save_verification == 'pi_rpc_synced'
        reached_cut()
        os.kill(os.getpid(), signal.SIGKILL)

    Journal.observe_pi_session_writer = interrupt_receipt
elif cut == 'sync':
    fsync = os.fsync

    def refuse_session_sync(descriptor):
        if is_session_file(descriptor):
            reached_cut()
            raise OSError(errno.ENOSPC, 'synthetic native session sync fault')
        fsync(descriptor)

    os.fsync = refuse_session_sync
elif cut == 'close':
    close = os.close

    def refuse_session_close(descriptor):
        is_session = is_session_file(descriptor)
        close(descriptor)
        if is_session:
            reached_cut()
            raise OSError(errno.EIO, 'synthetic native session handle close fault')

    os.close = refuse_session_close
else:
    raise AssertionError(cut)

sys.argv = [worker, *worker_args]
runpy.run_module(worker, run_name='__main__', alter_sys=True)
"""


@pytest.mark.certification
@pytest.mark.parametrize("cut", ["sync", "close", "publication"])
def test_installed_pi_save_fault_preserves_original_run_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: str
) -> None:
    with _held_pi_journey(tmp_path, monkeypatch, reply_after_hold=True) as journey:
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="pi-rpc",
            workspace=journey.repository,
            config_file=journey.config,
            session=PiSessionIntent(mode="new"),
        )
        ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
        submit = RunExecutionSupervisor.submit

        def submit_faulted_worker(
            supervisor: RunExecutionSupervisor, run_id: str, launch: RunWorkerLaunch
        ) -> None:
            executable, isolated, module_flag, module, *arguments = launch.argv
            assert (isolated, module_flag, module) == ("-I", "-m", "oh_goon._application_worker")
            submit(
                supervisor,
                run_id,
                replace(
                    launch,
                    argv=(
                        executable,
                        isolated,
                        "-c",
                        _PI_SAVE_WORKER_FAULT,
                        cut,
                        str(ready_write),
                        module,
                        *arguments,
                    ),
                    pass_fds=(*launch.pass_fds, ready_write),
                ),
            )

        try:
            with monkeypatch.context() as patch:
                patch.setattr(RunExecutionSupervisor, "submit", submit_faulted_worker)
                runtime = ApplicationRuntime.build(
                    standard_local=True,
                    standard_process_isolation=backend,
                    git_service=git,
                    startup_purpose="workflow",
                )
                try:
                    runtime.locations.add(journey.repository)
                    admitted = runtime.plans.run_native_agent(
                        selected=selected,
                        workspace=journey.repository,
                        skill=journey.skill,
                        task=_TASKS[0],
                        verification=(("/usr/bin/true",),),
                    )
                    run_id = admitted.fleet_run_id
                    assert journey.held_inference_arrived.wait(65), journey.errors
                    journey.release_held_inference.set()
                    if cut == "publication":
                        with pytest.raises(StateConflict, match="RUN_WORKER_CRASHED"):
                            runtime.runs.wait_for_terminal(run_id, timeout=40)
                    else:
                        detail = runtime.runs.wait_for_terminal(run_id, timeout=40)
                        assert detail.outcome is FleetRunOutcome.SUCCESS
                        native, verifier = detail.game_tape.repositories[0].steps
                        assert any(item.settled for item in native.native_agent_observations)
                        assert verifier.settlement is not None
                        assert verifier.settlement.outcome == "SUCCESS"
                finally:
                    journey.release_held_inference.set()
                    runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=2)
            assert select.select([ready_read], [], [], 0)[0]
            assert os.read(ready_read, 1) == b"R"
        finally:
            os.close(ready_write)
            os.close(ready_read)

        journal = Journal(LocalDatabase(journey.database_path, read_only=True))
        finished = journal.run_events(run_id)
        (reservation,) = (
            item for item in finished.events if isinstance(item, PiSessionWriterReservedEvent)
        )
        session_root = ApplicationStatePathLayout.from_database(journey.database_path).pi_sessions
        transcript = session_root / reservation.storage_id / PI_SESSION_NEW_FILE.name
        retained = transcript.read_bytes()
        assert retained.endswith(b"\n") and _MODEL_REPLY.encode() in retained
        assert _PRIVATE_TOOL_OUTPUT.encode() in retained
        assert all(json.loads(line)["type"] for line in retained.splitlines())
        assert finished.pi_session is not None
        assert finished.pi_session.native is None
        assert finished.pi_session.quiescent_sequence is not None
        assert isinstance(
            finished.events[finished.pi_session.quiescent_sequence - 1], DescendantsQuiescentEvent
        )
        if cut == "publication":
            assert finished.run.status is FleetRunStatus.RUNNING
            assert finished.pi_session.persistence == "not_recorded"
            assert not any(
                isinstance(item, PiSessionWriterObservedEvent) for item in finished.events
            )
            recovered = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
            try:
                status = recovered.recovery.recover_worker_exit(
                    run_id, quiescence_timeout_seconds=10
                )
                for quarantine in status.effect_quarantines:
                    assert quarantine.operation_id == reservation.operation_id
                    recovered.recovery.acknowledge(
                        quarantine.operation_id,
                        note="Inspected the synthetic Pi publication cut",
                        expected_record_version=quarantine.record_version,
                    )
            finally:
                recovered.shutdown(wait_for_runs=True)
            journal = Journal(LocalDatabase(journey.database_path, read_only=True))
            interrupted = journal.run_events(run_id)
            assert interrupted.events[: len(finished.events)] == finished.events
            assert interrupted.run.status is FleetRunStatus.FAILED
            assert interrupted.pi_session is not None
            assert interrupted.pi_session.persistence == "unknown"
            assert interrupted.pi_session.native is None
            assert not any(
                isinstance(item, (PiSessionWriterObservedEvent, StepSucceededEvent))
                for item in interrupted.events
            )
            assert journal.inspect_pi_session(run_id).availability == "orphaned"
        else:
            assert finished.pi_session.persistence == "unknown"
            assert journal.inspect_pi_session(run_id).availability == "unverified"
            (receipt,) = (
                item for item in finished.events if isinstance(item, PiSessionWriterObservedEvent)
            )
            assert receipt.observation.persistence == "unknown" and receipt.observation.file is None

        refused = journey.invoke(f"run:{run_id}", _TASKS[1])
        assert refused.returncode == 2, (refused.stdout, refused.stderr)
        assert json.loads(refused.stdout)["code"] == PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
        visible = refused.stdout + refused.stderr
        assert _PRIVATE_TOOL_OUTPUT not in visible and _SECRET not in visible
        assert str(transcript) not in visible and transcript.read_bytes() == retained
        assert tuple(session_root.iterdir()) == (transcript.parent,)
        assert journey.requests == [_TASKS[0], _TASKS[0]] and not journey.errors
