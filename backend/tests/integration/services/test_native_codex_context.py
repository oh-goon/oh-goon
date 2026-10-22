"""Opt-in installed Codex context transport through the original managed Run.

A bounded local Responses fixture drives real native tools and automatic
compaction. It proves instruction delivery, not remote model reasoning quality.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime


@pytest.mark.certification
def test_installed_codex_retains_instructions_across_followup_and_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    if not executable:
        pytest.skip("Select an installed Codex executable explicitly")
    program = Path(executable).resolve(strict=True)
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    (repo / "code.py").write_text("VALUE = 41\n")
    (repo / "verify.py").write_text("from code import VALUE\nassert VALUE == 42\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = tmp_path / "skills" / "context"
    skill.mkdir(parents=True)
    skill_text = "# Context qualification\nRetain the installed-codex-skill-canary.\n"
    (skill / "SKILL.md").write_text(skill_text)
    task = "Change VALUE to 42, retaining the supplied repository evidence and instructions."
    home = tmp_path / "synthetic-auth"
    home.mkdir()
    home.joinpath("auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "synthetic-codex-context-key"})
    )
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="signed-in-context",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            timeout_seconds=60,
            codex_login=CodexLoginConnection(
                home=str(home), model="gpt-6-luna", reasoning_effort="low"
            ),
        ),
        executable_sha256=native_executable_sha256(program),
    )
    prompt: bytes | None = None
    native_results: list[CommandResult] = []
    native_launch_count = 0
    verification_launch_count = 0
    observations: list[dict[str, Any]] = []
    endpoint_errors: list[str] = []
    request_count = 0
    request_lock = Lock()
    compact_item = {
        "type": "compaction",
        "id": "cmp_installed_context",
        "encrypted_content": "opaque-installed-context-compaction",
    }

    class ResponsesEndpoint(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def do_GET(self) -> None:
            # Native Codex owns its WebSocket-to-HTTP fallback. This fixture
            # serves Responses HTTP only; no alternate native loop is involved.
            self.send_error(404)

        def do_POST(self) -> None:
            nonlocal request_count
            with request_lock:
                request_count += 1
                sequence = request_count
            if sequence > 4:
                self.send_error(429)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4 * 1024 * 1024:
                    raise ValueError("Request size outside the fixture bound")
                body = json.loads(self.rfile.read(length))
                assert prompt is not None
                original = prompt.decode("utf-8")
                messages = body["input"]
                privileged = [
                    part["text"]
                    for item in messages
                    if item.get("type") == "message" and item.get("role") == "developer"
                    for part in item.get("content", [])
                    if part.get("type") == "input_text"
                ]
                user_texts = [
                    part["text"]
                    for item in messages
                    if item.get("type") == "message" and item.get("role") == "user"
                    for part in item.get("content", [])
                    if part.get("type") == "input_text"
                ]
                outputs = {
                    item["call_id"]: json.dumps(item.get("output", ""))
                    for item in messages
                    if item.get("type") == "custom_tool_call_output"
                }
                observation = {
                    "privileged_exact_count": privileged.count(original),
                    "user_duplicate": original in user_texts,
                    "followup": "CONTEXT_FOLLOWUP_PROOF" in outputs.get("context_first", ""),
                    "edit": "CONTEXT_EDIT_PROOF" in outputs.get("context_second", ""),
                    "retained_tool_outputs": bool(outputs),
                    "compaction_trigger": any(
                        item.get("type") == "compaction_trigger" for item in messages
                    ),
                    "retained_compaction": compact_item in messages,
                    "path": self.path,
                    "synthetic_auth": self.headers.get("Authorization")
                    == "Bearer synthetic-codex-context-key",
                }
                observations.append(observation)
                if sequence in (1, 2):
                    command = (
                        "printf CONTEXT_FOLLOWUP_PROOF"
                        if sequence == 1
                        else "printf 'VALUE = 42\\n' > code.py; printf CONTEXT_EDIT_PROOF"
                    )
                    item: dict[str, Any] = {
                        "type": "custom_tool_call",
                        "id": f"ctc_context_{sequence}",
                        "call_id": "context_first" if sequence == 1 else "context_second",
                        "name": "exec",
                        "namespace": "functions",
                        "input": "text(await tools.exec_command("
                        + json.dumps({"cmd": command})
                        + "));",
                        "status": "completed",
                    }
                elif sequence == 3:
                    item = compact_item
                else:
                    item = {
                        "type": "message",
                        "id": "msg_context_complete",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Installed Codex context transport complete",
                                "annotations": [],
                            }
                        ],
                        "status": "completed",
                    }
                # Native reported usage triggers its existing default threshold;
                # the fixture does not replace or configure the compaction loop.
                input_tokens = 2_000_000 if sequence == 2 else 1
                response = {
                    "id": f"resp_context_{sequence}",
                    "object": "response",
                    "created_at": 0,
                    "model": "gpt-6-luna",
                    "status": "completed",
                    "output": [item],
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 1,
                        "total_tokens": input_tokens + 1,
                    },
                }
                events = (
                    {
                        "type": "response.created",
                        "response": {**response, "status": "in_progress", "output": []},
                    },
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": response},
                )
                data = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            except (AssertionError, KeyError, OSError, TypeError, ValueError) as exc:
                endpoint_errors.append(type(exc).__name__)
                self.close_connection = True

        def log_message(self, *_args: object) -> None:
            pass

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(home))
    with ThreadingHTTPServer(("127.0.0.1", 0), ResponsesEndpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever, name="installed-codex-responses")
        worker.start()

        def command_runner(*argv: str, **kwargs: Any) -> CommandResult:
            nonlocal prompt, native_launch_count, verification_launch_count
            if argv[0] == str(program):
                native_launch_count += 1
                assert native_launch_count == 1
                prompt = kwargs["input_data"]
                assert isinstance(prompt, bytes)
                settings: dict[str, Any] = {}
                for index, argument in enumerate(argv[:-1]):
                    if argument == "-c":
                        settings.update(tomllib.loads(argv[index + 1]))
                assert Path(settings["model_instructions_file"]).read_bytes() == prompt
                assert kwargs["env"]["CODEX_HOME"] == str(home)
                payload = json.loads(prompt)
                assert payload["task"] == task
                assert payload["skill_instructions"] == skill_text
                assert payload["source_authority"]
                evidence = json.loads(payload["repository_evidence"])
                assert evidence["producer"] == "Hashmarks"
                assert (
                    evidence["producer_implementation_identity"]
                    == require_loaded_hashmarks_runtime()
                )
                assert evidence["capture_identity"] == payload["identity"]["candidate_identity"]
                # Add only the local transport destination to the production
                # command. Its session, instructions and tool policy stay intact.
                actual = (
                    *argv[:-1],
                    "-c",
                    "openai_base_url=" + json.dumps(f"http://127.0.0.1:{endpoint.server_port}/v1"),
                    argv[-1],
                )
                result = run_command(*actual, **kwargs)
                native_results.append(result)
                return result
            if argv == (sys.executable, "verify.py"):
                verification_launch_count += 1
            return run_command(*argv, **kwargs)

        monkeypatch.setattr(
            "oh_goon.integrations.fleet_plan_process_isolation.run_command", command_runner
        )

        runtime = None
        try:
            runtime = ApplicationRuntime.build(standard_local=True)
            runtime.locations.add(repo)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=repo,
                skill=skill,
                task=task,
                verification=((sys.executable, "verify.py"),),
            )
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=90)
            assert not endpoint_errors
            assert request_count == 4, (
                native_launch_count,
                [(result.returncode, result.stderr[-4000:]) for result in native_results],
                runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[-8000:],
            )
            assert len(observations) == 4
            assert all(item["path"] == "/v1/responses" for item in observations)
            assert all(item["synthetic_auth"] for item in observations)
            # stdin also duplicates these bytes as user content. That copy
            # cannot satisfy the separate exact privileged-context assertion.
            assert observations[0]["user_duplicate"]
            assert all(item["privileged_exact_count"] == 1 for item in observations)
            assert observations[1]["followup"]
            assert observations[2]["followup"] and observations[2]["edit"]
            assert [item["compaction_trigger"] for item in observations] == [
                False,
                False,
                True,
                False,
            ]
            assert [item["retained_compaction"] for item in observations] == [
                False,
                False,
                False,
                True,
            ]
            assert not observations[3]["retained_tool_outputs"]
            assert native_launch_count == 1
            assert native_results[0].returncode == 0
            events = [json.loads(line) for line in native_results[0].stdout.splitlines()]
            assert events[-1]["type"] == "turn.completed"
            assert (repo / "code.py").read_text() == "VALUE = 42\n"
            assert detail.outcome is FleetRunOutcome.SUCCESS, runtime.runs.game_tape_live_output(
                admitted.fleet_run_id
            ).model_dump_json()
            assert verification_launch_count == 1
            steps = detail.game_tape.repositories[0].steps
            assert [step.status for step in steps] == [GameTapeStatus.PASSED, GameTapeStatus.PASSED]
            native = steps[0].native_agent_observations[0]
            assert native.settled and not native.failed
            assert native.agent == "codex" and native.profile == selected.profile_name
            assert prompt is not None
            assert native.occurrence == json.loads(prompt)["identity"]["reviewer"]
        finally:
            try:
                if runtime is not None:
                    runtime.shutdown(wait_for_runs=True)
            finally:
                endpoint.shutdown()
                worker.join(timeout=5)
                assert not worker.is_alive()
