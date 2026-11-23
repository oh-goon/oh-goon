"""Public CLI continuation through the same managed Pi and original Run owner."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api.dependencies import fleet_run_control_plane
from oh_goon.api.routes.fleet_runs import router as run_router
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal

_SECRET = "synthetic-pi-journey-broker-key"
_PRIVATE_TOOL_OUTPUT = "PI_NATIVE_PRIVATE_TOOL_OUTPUT_53"
_MODEL_REPLY = "The requested review is complete."
_TASKS = (
    "Record the original Pi conversation before exiting Oh-Goon.",
    "Use the earlier Pi conversation in the initialized Plan.",
    "Continue the initialized Plan's Pi session in a fresh CLI process.",
    "Read the explicit prior Run handoff without adopting its native session.",
)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item["text"] for item in content if item.get("type") == "text")
    return ""


@contextmanager
def _provider() -> Iterator[tuple[str, list[str], list[str], list[str], list[str]]]:
    errors: list[str] = []
    requests: list[str] = []
    source_digests: list[str] = []
    cross_harness_handoffs: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                self.reply(*self.respond())
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
            stages = len(requests)
            assert stages < 6, "Readback or unexpected replay triggered broker inference"
            phase = min(max(stages - 1, 0), 3)
            task = _TASKS[phase]
            privileged = "\n".join(
                _message_text(message)
                for message in messages
                if message["role"] in {"system", "developer"}
            )
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
            return {"role": "assistant", "content": _MODEL_REPLY}, "stop"

        def reply(self, delta: dict[str, Any], finish: str) -> None:
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
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
            assert not any(task in combined for task in _TASKS[:3])
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
                            "workspace_roots": [str(repository)],
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
            get=view.required_run_detail, events=view.run_events
        )
        with TestClient(api) as client:
            detail_response = client.get(f"/api/fleet/runs/{second_id}")
            event_response = client.get(f"/api/fleet/runs/{second_id}/events?after=0&limit=1")
        for response in (detail_response, event_response):
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
        history = json.loads(cli("runs", "list", "--json"))
        assert {item["fleet_run_id"] for item in history["items"]} == completed
        print(
            json.dumps({"pi": "create -> initialized-plan -> cli", "cross_harness": bool(opencode)})
        )
