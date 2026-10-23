"""Opt-in installed OpenCode MCP conformance through the original managed Run.

The HTTP fixture drives native tool calling; it proves no model reasoning quality.
Select the installed executable and its public runtime root explicitly.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.agent_review import AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime


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


@pytest.mark.certification
def test_installed_opencode_reviews_through_the_read_only_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_OPENCODE")
    runtime_root = os.environ.get("OH_GOON_NATIVE_OPENCODE_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed OpenCode executable and public runtime root")
    program = Path(executable).resolve(strict=True)
    public_runtime = Path(runtime_root).resolve(strict=True)
    assert public_runtime.is_dir() and program.is_relative_to(public_runtime)
    workspace = tmp_path / "repos" / "app"
    workspace.mkdir(parents=True)
    original_source = b"VALUE = 42\n"
    (workspace / "source.py").write_bytes(original_source)
    (workspace / "original-root").write_text(str(workspace), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "source.py"], check=True)
    original_index = (workspace / ".git" / "index").read_bytes()
    private = tmp_path / "foreign-host-source"
    private.write_text("synthetic-host-private-canary", encoding="utf-8")
    tools = tmp_path / "public-tools"
    tools.mkdir()
    probe = tools / "readonly_probe.py"
    probe.write_text(
        r"""import json,os,pathlib,socket,subprocess,sys
private,port=sys.argv[1:]
root=pathlib.Path('/workspace')
original=pathlib.Path((root/'original-root').read_text())
facts={'source_read':True,'source_write_denied':True,'rename_denied':True,'delete_denied':True,'shell_write_denied':True,'git_index_write_denied':True,'git_command_denied':True}
for alias in (root,original):
    source=alias/'source.py'
    facts['source_read'] &= source.read_bytes()==b'VALUE = 42\n'
    for name,action in (
        ('source_write_denied',lambda: source.write_text('forbidden')),
        ('rename_denied',lambda: source.rename(alias/'renamed.py')),
        ('delete_denied',lambda: source.unlink()),
        ('git_index_write_denied',lambda: (alias/'.git/index').write_bytes(b'forbidden')),
    ):
        try: action()
        except OSError: pass
        else: facts[name]=False
    shell=subprocess.run(['/bin/sh','-c','printf forbidden > source.py'],cwd=alias,capture_output=True)
    facts['shell_write_denied'] &= shell.returncode!=0
    git=subprocess.run(['/usr/bin/git','-C',str(alias),'update-index','--force-remove','source.py'],capture_output=True)
    facts['git_command_denied'] &= git.returncode!=0
scratch=pathlib.Path('/tmp/oh-goon-opencode-readonly-probe')
scratch.write_text('allowed private output')
facts['private_output_allowed']=scratch.read_text()=='allowed private output'
scratch.unlink()
facts['host_private_denied']=not pathlib.Path(private).exists()
facts['host_credential_absent']='NATIVE_RO_HOST_KEY' not in os.environ
try:
    with socket.create_connection(('127.0.0.1',int(port)),timeout=.2): pass
except OSError: facts['host_network_denied']=True
else: facts['host_network_denied']=False
print('OPENCODE_READONLY_PROOF '+json.dumps(facts,sort_keys=True))
""",
        encoding="utf-8",
    )
    skill = tmp_path / "skills" / "readonly"
    skill.mkdir(parents=True)
    skill_text = (
        "# Native review\nRetain opencode-readonly-skill-canary. Review all captured source.\n"
    )
    (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
    task = "Inspect the complete captured source with the native read-only boundary probe."
    identity: dict[str, str] | None = None
    request_count = 0
    qualified_requests = 0
    context_only_requests = 0
    native_tool_requested = False
    native_proof: dict[str, bool] | None = None
    fixture_failure: str | None = None
    expected_facts = {
        "source_read",
        "source_write_denied",
        "rename_denied",
        "delete_denied",
        "shell_write_denied",
        "git_index_write_denied",
        "git_command_denied",
        "private_output_allowed",
        "host_private_denied",
        "host_credential_absent",
        "host_network_denied",
    }

    def texts(message: dict[str, Any]) -> list[str]:
        content = message.get("content", [])
        if isinstance(content, str):
            return [content]
        return [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal identity, request_count, qualified_requests, native_proof, fixture_failure
            nonlocal context_only_requests, native_tool_requested
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                request_count += 1
                assert request_count <= 3, (
                    "at most one native context-only request and one tool/follow-up pair"
                )
                assert self.path == "/v1/chat/completions"
                assert self.headers["Authorization"] == "Bearer SYNTHETIC_RO_HOST_KEY"
                assert body["model"] == "native-readonly-fixture"
                privileged = "\n".join(
                    text
                    for message in body["messages"]
                    if message.get("role") in ("system", "developer")
                    for text in texts(message)
                )
                assert "Hashmarks" in privileged and skill_text in privileged, (
                    "qualified Hashmarks/Skill context missing"
                )
                for message in body["messages"]:
                    if message.get("role") != "user":
                        continue
                    for text in texts(message):
                        start = text.find('{"source_authority":')
                        if start < 0:
                            continue
                        payload, _ = json.JSONDecoder().raw_decode(text[start:])
                        if payload.get("task") != task:
                            continue
                        evidence = json.loads(payload["repository_evidence"])
                        assert evidence["producer"] == "Hashmarks"
                        assert (
                            evidence["producer_implementation_identity"]
                            == require_loaded_hashmarks_runtime()
                        )
                        assert (
                            evidence["capture_identity"]
                            == payload["identity"]["candidate_identity"]
                        )
                        assert payload["repository_evidence"] in privileged
                        identity = payload["identity"]
                assert identity is not None, "frozen user prompt identity missing"
                qualified_requests += 1
                functions = [
                    item["function"]
                    for item in body.get("tools", [])
                    if isinstance(item.get("function"), dict)
                ]
                delta: dict[str, Any]
                if not functions:
                    context_only_requests += 1
                    assert context_only_requests == 1
                    delta = {"role": "assistant", "content": "Read-only native review"}
                    finish = "stop"
                elif not native_tool_requested:
                    selected = next(
                        (
                            item
                            for item in functions
                            if item.get("name")
                            in ("bash", "shell", "shell_command", "exec_command")
                            and (
                                "command" in item.get("parameters", {}).get("properties", {})
                                or "cmd" in item.get("parameters", {}).get("properties", {})
                            )
                        ),
                        None,
                    )
                    assert selected is not None, (
                        "installed OpenCode advertised no supported native shell tool: "
                        + json.dumps([item.get("name") for item in functions])
                    )
                    properties = selected["parameters"]["properties"]
                    command = shlex.join(
                        ("/usr/bin/python3", str(probe), str(private), str(endpoint.server_port))
                    )
                    field = "command" if "command" in properties else "cmd"
                    assert properties[field].get("type") == "string"
                    arguments = {field: command}
                    if "description" in properties:
                        arguments["description"] = "Inspect inherited read-only execution boundary"
                    assert set(selected["parameters"].get("required", [])).issubset(arguments)
                    delta = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_readonly",
                                "type": "function",
                                "function": {
                                    "name": selected["name"],
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    }
                    native_tool_requested = True
                    finish = "tool_calls"
                else:
                    for message in body["messages"]:
                        if message.get("role") != "tool":
                            continue
                        for text in texts(message):
                            marker = "OPENCODE_READONLY_PROOF "
                            if marker not in text:
                                continue
                            native_proof, _ = json.JSONDecoder().raw_decode(
                                text.split(marker, 1)[1]
                            )
                    assert isinstance(native_proof, dict) and set(native_proof) == expected_facts
                    assert all(value is True for value in native_proof.values()), native_proof
                    record = {
                        "schema": "oh-goon.agent-review/v1",
                        **identity,
                        "verdict": "CLEAN",
                        "summary": "Synthetic native read-only boundary probe completed.",
                        "reviewed_paths": {"complete_scope": identity["scope_identity"]},
                        "findings": [],
                    }
                    delta = {"role": "assistant", "content": json.dumps(record)}
                    finish = "stop"
                self.reply(delta, finish)
            except Exception as error:
                fixture_failure = fixture_failure or str(error)
                # A valid non-review stop preserves the first fixture failure
                # without triggering the native client's HTTP retry policy.
                self.reply(
                    {
                        "role": "assistant",
                        "content": "Fixture rejected the request; no review claim.",
                    },
                    "stop",
                )

        def reply(self, delta: dict[str, Any], finish: str) -> None:
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            data = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state/app.sqlite"))
    monkeypatch.setenv("NATIVE_RO_HOST_KEY", "SYNTHETIC_RO_HOST_KEY")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "host-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "host-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "synthetic-home"))
    with ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever)
        worker.start()
        try:
            configuration = tmp_path / "native-config"
            configuration.mkdir()
            native_bytes = json.dumps(
                {
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
                            "models": {"fixture": {"modelID": "native-readonly-fixture"}},
                        }
                    },
                }
            ).encode()
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
                                "api_key_env": "NATIVE_RO_HOST_KEY",
                            }
                        },
                        "models": {
                            "selected": {"provider": "approved", "model": "native-readonly-fixture"}
                        },
                        "native_profiles": {
                            "readonly": {
                                "backend": "bubblewrap",
                                "allowed_agents": ["opencode"],
                                "workspace_roots": [str(workspace.parent)],
                                "inference_profile": "selected",
                                "timeout_seconds": 90,
                            }
                        },
                        "native_agents": {
                            "opencode": {
                                "executable": str(program),
                                "runtime_roots": [str(public_runtime), str(tools)],
                                "configuration_directory": str(configuration),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            selected, backend, git = resolve_native_cli_selection(
                agent="opencode", profile="readonly", workspace=workspace, config_file=catalog
            )
            assert selected.profile.sandbox_owner == "harness"
            runtime = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                runtime.locations.add(workspace)
                admitted = runtime.plans.run_native_agent(
                    selected=selected, workspace=workspace, skill=skill, task=task, role="review"
                )
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
                assert detail.outcome is FleetRunOutcome.SUCCESS, (fixture_failure, request_count)
                assert fixture_failure is None
                assert native_tool_requested
                assert request_count == qualified_requests == 2 + context_only_requests
                assert native_proof is not None and all(native_proof.values())
                operation = detail.operations[0].evidence
                assert isinstance(operation, FleetPlanOperationEvidence)
                step = operation.plan.steps[0]
                assert step.status == "COMPLETED" and step.returncode == 0
                reviews = [
                    item.record
                    for item in step.observations
                    if isinstance(item, AgentReviewObservation)
                ]
                assert len(reviews) == 1 and isinstance(reviews[0], AgentReviewRecord)
                assert reviews[0].verdict == "CLEAN"
                assert set(reviews[0].reviewed_paths) == {"repo/source.py", "repo/original-root"}
                receipt = step.attempts[0].standard_isolation
                assert receipt is not None and receipt.workspace_read_only
                assert receipt.host_and_external_ip_networking == "denied"
                assert receipt.scoped_write_roots == () and receipt.protected_paths == (".git",)
                assert receipt.credential_visibility == "none"
                assert receipt.preflight == receipt.filesystem_cleanup == "verified"
                assert (
                    step.attempts[0].supervisor is not None
                    and step.attempts[0].supervisor.final_process_group_quiescent is True
                )
                assert any(
                    event.kind == "tool"
                    for claim in detail.game_tape.repositories[0].steps[0].native_agent_observations
                    for event in claim.events
                )
                assert (workspace / "source.py").read_bytes() == original_source
                assert (workspace / ".git/index").read_bytes() == original_index
                assert (configuration / "opencode.json").read_bytes() == native_bytes
                assert "SYNTHETIC_RO_HOST_KEY" not in detail.model_dump_json()
                version = subprocess.run(
                    [str(program), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                ).stdout.strip()
                print(
                    json.dumps(
                        {
                            "harness": version,
                            "platform": platform.platform(),
                            "profile": "bubblewrap; harness-owned native tools; read-only review",
                            "requests": request_count,
                            "context_only_requests": context_only_requests,
                            "outcome": detail.outcome.value,
                            "boundary": receipt.model_dump(mode="json"),
                        },
                        sort_keys=True,
                    )
                )
            finally:
                runtime.shutdown(wait_for_runs=True)
        finally:
            endpoint.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
