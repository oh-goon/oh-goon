"""Opt-in installed OpenCode MCP conformance through the original managed Run.

The HTTP fixture drives native tool calling; it proves no model reasoning quality.
Select the installed executable and its public runtime root explicitly.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.vocabulary import FleetRunOutcome


@pytest.mark.certification
@pytest.mark.parametrize("mcp_startup_delay_seconds", [0, 5], ids=["immediate", "delayed"])
def test_installed_opencode_calls_mcp_inside_original_run_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_startup_delay_seconds: int
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_OPENCODE")
    runtime_root = os.environ.get("OH_GOON_NATIVE_OPENCODE_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed OpenCode executable and public runtime root")
    workspace = tmp_path / "repos" / "app"
    workspace.mkdir(parents=True)
    (workspace / "source.py").write_text("VALUE = 42\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    private = tmp_path / "foreign-host-source"
    private.mkdir()
    (private / "credential").write_text("HOST_PRIVATE_CANARY")
    inputs = tmp_path / "public-tools"
    inputs.mkdir()
    server_script = inputs / "boundary.py"
    catalog_ready = workspace / "mcp-catalog-ready"
    server_script.write_text(
        """import json, pathlib, socket, sys, time
private, port, receipt, startup_delay = sys.argv[1:]
time.sleep(float(startup_delay))
methods = []
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    methods.append(method)
    pathlib.Path(receipt).write_text(json.dumps({'methods': methods}))
    if 'id' not in request:
        continue
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'boundary', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'prove_boundary', 'description': 'Inspect the inherited boundary', 'inputSchema': {'type': 'object', 'properties': {}}}]}
    elif method == 'tools/call':
        try:
            with socket.create_connection(('127.0.0.1', int(port)), timeout=.2):
                reachable = True
        except OSError:
            reachable = False
        proof = {'methods': methods, 'private_visible': pathlib.Path(private).exists(), 'host_reachable': reachable, 'credential_in_environment': 'NATIVE_MCP_HOST_KEY' in __import__('os').environ}
        pathlib.Path(receipt).write_text(json.dumps(proof))
        result = {'content': [{'type': 'text', 'text': 'Boundary inspected'}]}
    else:
        result = {'resources': [], 'resourceTemplates': [], 'prompts': []}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
    if method == 'tools/list':
        pathlib.Path(receipt).with_name('mcp-catalog-ready').touch()
"""
    )
    request_count = 0
    tool_request_count = 0
    approved_transport = True
    hashmarks_every_request = True
    native_result_replayed = False
    discovery_deadline: float | None = None
    discovery_timed_out = False

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal request_count, tool_request_count, approved_transport
            nonlocal hashmarks_every_request, native_result_replayed
            nonlocal discovery_deadline, discovery_timed_out
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            request_count += 1
            approved_transport &= (
                self.path == "/v1/chat/completions"
                and self.headers["Authorization"] == "Bearer HOST_INFERENCE_CANARY"
            )
            hashmarks_every_request &= "Hashmarks" in body["messages"][0]["content"]
            names = [item["function"]["name"] for item in body.get("tools", [])]
            if "boundary_prove_boundary" in names:
                tool_request_count += 1
                if tool_request_count == 2:
                    native_result_replayed = any(
                        message.get("role") == "tool"
                        and "Boundary inspected" in json.dumps(message.get("content"))
                        for message in body["messages"]
                    )
            call = None
            # Synchronize with the real MCP catalog response, then let the native
            # loop refresh its tools. Keep discovery within the 90-second Run;
            # retain only counters/facts rather than growing conversation bodies.
            if "read" in names and "boundary_prove_boundary" not in names:
                if discovery_deadline is None:
                    discovery_deadline = monotonic() + 30
                while not catalog_ready.exists() and monotonic() < discovery_deadline:
                    sleep(0.01)
                if monotonic() < discovery_deadline:
                    call = {"name": "read", "arguments": json.dumps({"path": "source.py"})}
                else:
                    discovery_timed_out = True
            elif tool_request_count == 1 and "boundary_prove_boundary" in names:
                call = {"name": "boundary_prove_boundary", "arguments": "{}"}
            if call is not None:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{request_count}",
                            "type": "function",
                            "function": call,
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "Native MCP completed"}
                finish = "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            data = (payload + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("NATIVE_MCP_HOST_KEY", "HOST_INFERENCE_CANARY")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "host-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "host-config"))
    with ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever)
        worker.start()
        try:
            with socket.create_connection(("127.0.0.1", endpoint.server_port), timeout=1):
                pass
            configuration = tmp_path / "native-config"
            configuration.mkdir()
            native = {
                "model": "managed/fixture",
                "update": "disable",
                "snapshots": False,
                "providers": {
                    "managed": {
                        "package": "@opencode/ai/providers/openai-compatible",
                        "settings": {
                            "baseURL": "http://127.0.0.1:18743/v1",
                            "apiKey": "oh-goon-local",
                        },
                        "models": {"fixture": {"modelID": "native-fixture"}},
                    }
                },
                "mcp": {
                    "servers": {
                        "boundary": {
                            "type": "local",
                            "codemode": False,
                            "timeout": {"startup": 30_000, "catalog": 30_000},
                            "command": [
                                "/usr/bin/python3",
                                str(server_script),
                                str(private),
                                str(endpoint.server_port),
                                str(workspace / "mcp-proof.json"),
                                str(mcp_startup_delay_seconds),
                            ],
                        }
                    }
                },
            }
            native_bytes = json.dumps(native).encode()
            (configuration / "opencode.json").write_bytes(native_bytes)
            catalog = tmp_path / "providers.json"
            catalog.write_text(
                json.dumps(
                    {
                        "default_model": "selected",
                        "providers": {
                            "approved": {
                                "api": "openai-chat-completions",
                                "base_url": f"http://127.0.0.1:{endpoint.server_port}/v1",
                                "api_key_env": "NATIVE_MCP_HOST_KEY",
                            }
                        },
                        "models": {"selected": {"provider": "approved", "model": "native-fixture"}},
                        "native_profiles": {
                            "mcp-probe": {
                                "backend": "bubblewrap",
                                "allowed_agents": ["opencode"],
                                "workspace_roots": [str(workspace.parent)],
                                "inference_profile": "selected",
                                "timeout_seconds": 90,
                            }
                        },
                        "native_agents": {
                            "opencode": {
                                "executable": executable,
                                "runtime_roots": [runtime_root, str(inputs)],
                                "configuration_directory": str(configuration),
                            }
                        },
                    }
                )
            )
            selected, backend, git = resolve_native_cli_selection(
                agent="opencode",
                profile="mcp-probe",
                workspace=workspace,
                config_file=catalog,
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
                    task="Call boundary_prove_boundary, then report completion.",
                )
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
                assert detail.outcome is FleetRunOutcome.SUCCESS
                assert not discovery_timed_out, "Native MCP discovery exceeded 30 seconds"
                proof = json.loads((workspace / "mcp-proof.json").read_text())
                assert approved_transport
                assert {"initialize", "tools/list", "tools/call"}.issubset(proof["methods"])
                assert not proof["private_visible"]
                assert not proof["host_reachable"]
                assert not proof["credential_in_environment"]
                assert tool_request_count == 2
                assert native_result_replayed
                assert hashmarks_every_request
                step = detail.game_tape.repositories[0].steps[0]
                assert any(
                    event.kind == "tool"
                    for claim in step.native_agent_observations
                    for event in claim.events
                )
                assert step.attempts[0].standard_isolation is not None
                assert (configuration / "opencode.json").read_bytes() == native_bytes
                assert "HOST_INFERENCE_CANARY" not in detail.model_dump_json()
            finally:
                runtime.shutdown(wait_for_runs=True)
        finally:
            endpoint.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
