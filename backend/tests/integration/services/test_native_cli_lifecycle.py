"""Installed native CLI failure/cancellation through original Run supervision.

The local model emits a completion, rejects inference, or leaves a text stream
open. Synthetic state and credentials establish lifecycle conformance only.
"""

from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any, Literal

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from tests.fakes.codex_responses import CodexResponsesFixture


@pytest.mark.certification
@pytest.mark.parametrize("scenario", ("success", "error", "cancel", "timeout"))
@pytest.mark.parametrize("agent", ("opencode", "pi", "codex"))
def test_installed_native_terminal_claim_respects_original_run_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    agent: Literal["opencode", "pi", "codex"],
) -> None:
    executable = os.environ.get(f"OH_GOON_NATIVE_{agent.upper()}")
    runtime_root = os.environ.get(f"OH_GOON_NATIVE_{agent.upper()}_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip(f"Select the installed {agent} executable and public runtime root")
    program = Path(executable).resolve(strict=True)
    public_runtime = Path(runtime_root).resolve(strict=True)
    assert public_runtime.is_dir() and program.is_relative_to(public_runtime)
    version = subprocess.run(
        [str(program), "--version"], capture_output=True, text=True, timeout=10, check=True
    ).stdout.strip()
    workspace = tmp_path / "repos" / "app"
    workspace.mkdir(parents=True)
    source = workspace / "source.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    model_started, release_model = Event(), Event()
    request_count = 0
    model_requests = 0
    fixture_errors: list[str] = []

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal request_count, model_requests
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                request_count += 1
                assert request_count <= 5
                assert self.path == (
                    "/v1/responses" if agent == "codex" else "/v1/chat/completions"
                )
                assert self.headers["Authorization"] == "Bearer SYNTHETIC_LIFECYCLE_KEY"
                assert body["model"] == "native-lifecycle-fixture"
                active = bool(body.get("tools"))
                if active:
                    model_requests += 1
                    assert model_requests == 1, "Native turn unexpectedly retried inference"
                hanging = active and scenario in {"cancel", "timeout"}
                if agent == "codex":
                    item = {
                        "type": "message",
                        "id": "msg_lifecycle_claim",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "CLEAN: synthetic lifecycle completion claim.",
                                "annotations": [],
                            }
                        ],
                        "status": "completed",
                    }
                    events = list(CodexResponsesFixture._events("resp_lifecycle", item, 1))
                    for event in events:
                        if isinstance(event.get("response"), dict):
                            event["response"]["model"] = body["model"]
                    if active and scenario == "error":
                        failed = events.pop()["response"]
                        failed.update(
                            status="failed",
                            error={
                                "code": "invalid_prompt",
                                "message": "Synthetic terminal inference rejection",
                            },
                        )
                        events.append({"type": "response.failed", "response": failed})
                    elif hanging:
                        events.pop()
                    data = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
                    )
                else:
                    delta: dict[str, Any] = {
                        "role": "assistant",
                        "content": "CLEAN: synthetic lifecycle completion claim.",
                    }
                    chunks: list[dict[str, Any]] = [
                        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                    ]
                    if active and scenario == "error":
                        chunks.append(
                            {
                                "error": {
                                    "message": "Synthetic terminal inference rejection",
                                    "type": "invalid_request_error",
                                    "code": "invalid_request_error",
                                }
                            }
                        )
                    elif not hanging:
                        chunks.append(
                            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                        )
                    data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    if not hanging:
                        data += "data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(data.encode())
                self.wfile.flush()
                if active:
                    model_started.set()
                if hanging:
                    assert release_model.wait(timeout=45), "Controller did not close its Run"
            except (BrokenPipeError, ConnectionResetError):
                # Native cancellation owns transport closure while fixture output
                # remains bounded; the original supervisor supplies cleanup proof.
                return
            except Exception as error:
                fixture_errors.append(str(error))
                model_started.set()

        def log_message(self, *_args: object) -> None:
            pass

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state/app.sqlite"))
    monkeypatch.setenv("NATIVE_LIFECYCLE_KEY", "SYNTHETIC_LIFECYCLE_KEY")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "host-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "host-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "synthetic-home"))
    with ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever)
        worker.start()
        try:
            catalog = tmp_path / "providers.json"
            catalog.write_text(
                json.dumps(
                    {
                        "default_model": "selected",
                        "providers": {
                            "approved": {
                                "api": "openai-responses"
                                if agent == "codex"
                                else "openai-chat-completions",
                                "base_url": f"http://127.0.0.1:{endpoint.server_port}/v1",
                                "api_key_env": "NATIVE_LIFECYCLE_KEY",
                            }
                        },
                        "models": {
                            "selected": {
                                "provider": "approved",
                                "model": "native-lifecycle-fixture",
                            }
                        },
                        "native_profiles": {
                            "isolated": {
                                "backend": "bubblewrap",
                                "allowed_agents": [agent],
                                "workspace_roots": [str(workspace.parent)],
                                "inference_profile": "selected",
                                "timeout_seconds": 15 if scenario == "timeout" else 45,
                            }
                        },
                        "native_agents": {
                            agent: {
                                "executable": str(program),
                                "runtime_roots": [str(public_runtime)],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            selected, backend, git = resolve_native_cli_selection(
                agent=agent, profile="isolated", workspace=workspace, config_file=catalog
            )
            runtime = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                runtime.locations.add(workspace)
                admitted = runtime.plans.run_native_agent(
                    selected=selected,
                    workspace=workspace,
                    skill=None,
                    task="Read source.py and report the result. Do not edit source.",
                )
                if scenario == "cancel":
                    assert model_started.wait(timeout=20), "Native inference did not start"
                    assert not fixture_errors, fixture_errors
                    cancellation = runtime.runs.cancel(admitted.fleet_run_id)
                    assert cancellation.status.cancel_request_satisfied
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=50)
                assert not fixture_errors, fixture_errors
                assert model_started.is_set() and model_requests == 1
                step = detail.game_tape.repositories[0].steps[0]
                assert step.settlement is not None
                assert (detail.outcome is FleetRunOutcome.SUCCESS) == (scenario == "success")
                supervisor = None
                if scenario == "cancel":
                    assert detail.run.status is FleetRunStatus.CANCELLED
                    assert detail.run.cancel_requested_at is not None
                    assert step.status.value == "CANCELLED"
                    assert step.settlement.outcome == "CANCELLED"
                    assert step.settlement.cleanup == "VERIFIED"
                    assert step.cancellation is not None
                    assert step.cancellation.supervisor is not None
                    supervisor = step.cancellation.supervisor
                    assert supervisor.cleanup_reason == "cancel"
                    assert supervisor.final_process_group_quiescent is True
                    assert not step.attempts, (
                        "Cancellation must not invent post-cancel source facts"
                    )
                    events = runtime.runs.events(admitted.fleet_run_id).events
                    terminated = {
                        event.physical_execution_id
                        for event in events
                        if event.event_type is FleetRunEventType.TERMINATION_REQUESTED
                        and event.reason == "cancellation_requested"
                    }
                    assert terminated, [event.event_type.value for event in events]
                    for kind in (
                        FleetRunEventType.PROCESS_STARTED,
                        FleetRunEventType.PROCESS_EXITED,
                        FleetRunEventType.DESCENDANTS_QUIESCENT,
                    ):
                        assert terminated <= {
                            event.physical_execution_id
                            for event in events
                            if event.event_type is kind
                        }
                    assert any(
                        event.event_type is FleetRunEventType.STEP_CANCELLED for event in events
                    )
                    assert step.cancellation.physical_execution_id in terminated
                else:
                    expected = {"success": "SUCCESS", "error": "FAILED", "timeout": "TIMED_OUT"}[
                        scenario
                    ]
                    assert step.settlement.outcome == expected, step.model_dump(mode="json")
                    assert step.settlement.cleanup == "VERIFIED"
                    assert len(step.attempts) == 1
                    attempt = step.attempts[0]
                    supervisor = attempt.supervisor
                    assert supervisor is not None
                    assert supervisor.final_process_group_quiescent is True
                    assert supervisor.cleanup_reason == (
                        "timeout" if scenario == "timeout" else "none"
                    )
                    if scenario in {"success", "error"}:
                        isolation = attempt.standard_isolation
                        assert isolation is not None and isolation.filesystem_cleanup == "verified"
                claims = step.native_agent_observations
                if scenario == "success":
                    assert len(claims) == 1 and claims[0].settled and not claims[0].failed
                if scenario == "error":
                    assert len(claims) == 1 and claims[0].failed
                assert runtime.runs.get(admitted.fleet_run_id).operations == detail.operations
                assert source.read_text(encoding="utf-8") == "VALUE = 42\n"
                assert "SYNTHETIC_LIFECYCLE_KEY" not in detail.model_dump_json()
                print(
                    json.dumps(
                        {
                            "harness": version,
                            "scenario": scenario,
                            "requests": model_requests,
                            "native_events": [
                                event.summary for claim in claims for event in claim.events
                            ],
                            "settlement": step.settlement.model_dump(mode="json"),
                            "supervisor": supervisor.model_dump(mode="json")
                            if supervisor is not None
                            else None,
                        }
                    )
                )
            finally:
                runtime.shutdown(wait_for_runs=True)
            requests_before_readback = (request_count, model_requests)
            reopened = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                retained = reopened.runs.get(admitted.fleet_run_id)
                assert retained.run.status is detail.run.status
                retained_step = retained.game_tape.repositories[0].steps[0]
                assert retained_step.settlement == step.settlement
                assert retained_step.cancellation == step.cancellation
                if scenario == "cancel":
                    assert not retained.operations
                else:
                    assert retained.operations == detail.operations
                    assert retained_step.attempts == step.attempts
                    assert retained_step.native_agent_observations == step.native_agent_observations
            finally:
                reopened.shutdown(wait_for_runs=True)
            assert (request_count, model_requests) == requests_before_readback
        finally:
            release_model.set()
            endpoint.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
