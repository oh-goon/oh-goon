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
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.vocabulary import FleetRunOutcome


@pytest.mark.certification
def test_installed_opencode_calls_mcp_inside_original_run_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    server_script.write_text(
        """import json, pathlib, socket, sys
private, port, receipt = sys.argv[1:]
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
"""
    )
    requests: list[dict[str, Any]] = []
    headers: list[tuple[str, str | None]] = []
    tool_requests: list[dict[str, Any]] = []
    discovery_reads = 0

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal discovery_reads
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            headers.append((self.path, self.headers["Authorization"]))
            names = [item["function"]["name"] for item in body.get("tools", [])]
            if "boundary_prove_boundary" in names:
                tool_requests.append(body)
            call = None
            # Native MCP startup is asynchronous. Let its own loop read source
            # while discovery completes; never synthesize an MCP result.
            if "read" in names and "boundary_prove_boundary" not in names:
                discovery_reads += 1
                if discovery_reads <= 3:
                    call = {"name": "read", "arguments": json.dumps({"path": "source.py"})}
            elif len(tool_requests) == 1 and "boundary_prove_boundary" in names:
                call = {"name": "boundary_prove_boundary", "arguments": "{}"}
            if call is not None:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{len(requests)}",
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
                            "command": [
                                "/usr/bin/python3",
                                str(server_script),
                                str(private),
                                str(endpoint.server_port),
                                str(workspace / "mcp-proof.json"),
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
                proof = json.loads((workspace / "mcp-proof.json").read_text())
                assert all(
                    path == "/v1/chat/completions" and auth == "Bearer HOST_INFERENCE_CANARY"
                    for path, auth in headers
                )
                assert {"initialize", "tools/list", "tools/call"}.issubset(proof["methods"])
                assert not proof["private_visible"]
                assert not proof["host_reachable"]
                assert not proof["credential_in_environment"]
                assert len(tool_requests) == 2
                assert any(
                    message.get("role") == "tool"
                    and "Boundary inspected" in json.dumps(message.get("content"))
                    for message in tool_requests[1]["messages"]
                )
                for request in requests:
                    assert "Hashmarks" in request["messages"][0]["content"]
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
