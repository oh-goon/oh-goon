"""Opt-in installed Codex service turns through the original durable Run.

Only the bounded local Responses server is simulated. Native tools, automatic
compaction, source admission, verification and process closure remain real.
No real native authentication or configuration stores are used.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Event
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation, NativeAgentObservation
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.core.projections.agent_campaign import project_agent_campaign
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.integrations.agent_codex_service import CodexServiceClient
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.services.execution_lock_persistence import write_execution_lock
from tests.factories.agent_review import agent_review_case
from tests.fakes.codex_responses import CodexResponsesFixture

_TASK = "Change VALUE to 42 and retain the supplied evidence through follow-up and compaction."
_SKILL = "# Service coding context\nRetain the service-run-skill-canary.\n"
_TOKEN = "synthetic-host-only-service-run-key"


class _RunContextResponses(CodexResponsesFixture):
    """Drive native tool follow-up and the harness-owned local compaction path."""

    summary = "SERVICE_RUN_COMPACTION_SUMMARY: VALUE is 42; preserve the frozen instructions."

    def __init__(self, commands: tuple[str, str]) -> None:
        self.instructions: bytes | None = None
        self.payload: dict[str, Any] | None = None
        super().__init__(
            transport="http",
            prompt=lambda: self.instructions,
            auth_token=_TOKEN,
            tool_commands=commands,
        )

    def _response(
        self, body: dict[str, Any], endpoint: BaseHTTPRequestHandler, transport: str
    ) -> tuple[dict[str, Any], ...]:
        messages = [
            {
                **item,
                "type": "message",
                "content": [{"type": "input_text", "text": item["content"]}],
            }
            if isinstance(item.get("content"), str) and item.get("role") in {"developer", "user"}
            else item
            for item in body["input"]
        ]
        developer_texts = [
            part["text"]
            for item in messages
            if item.get("role") == "developer"
            for part in item.get("content", [])
            if part.get("type") == "input_text"
        ]
        if self.instructions is None:
            candidates = []
            for text in developer_texts:
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("task") == _TASK:
                    candidates.append((text, payload))
            assert len(candidates) == 1, "Original Run must deliver its exact frozen context"
            instructions, self.payload = candidates[0]
            self.instructions = instructions.encode()
            assert self.payload["skill_instructions"] == _SKILL
            assert self.payload["working_workspace"]["repository_access"] == "write"
            evidence = json.loads(self.payload["repository_evidence"])
            assert evidence["producer"] == "Hashmarks"
            assert (
                evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
            )
            assert evidence["capture_identity"] == self.payload["identity"]["candidate_identity"]
            assert self.tool_commands is not None
            self.tool_commands = (
                self.payload["repository_tools"]["list"] + " && " + self.tool_commands[0],
                self.tool_commands[1],
            )
        user_texts = [
            part["text"]
            for item in messages
            if item.get("role") == "user"
            for part in item.get("content", [])
            if part.get("type") == "input_text"
        ]
        events = super()._response({**body, "input": messages}, endpoint, transport)
        observation = self.observations[-1]
        observation["tool_outputs"] = {
            item["call_id"]: json.dumps(item.get("output", ""))[:4096]
            for item in messages
            if item.get("type") == "custom_tool_call_output"
        }
        observation["task_only_user_input"] = _TASK in user_texts
        observation["local_compaction_request"] = any(
            "CONTEXT CHECKPOINT COMPACTION" in text for text in user_texts
        )
        observation["retained_local_summary"] = any(self.summary in text for text in user_texts)
        if self.request_count == 3:
            assert observation["local_compaction_request"]
            item = {
                "type": "message",
                "id": "msg_service_run_compaction",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.summary, "annotations": []}],
                "status": "completed",
            }
            events = self._events(observation["response_id"], item, 1)
        for event in events:
            if isinstance(event.get("response"), dict):
                event["response"]["model"] = "gpt-6.1-sol"
        return events


@pytest.mark.certification
@pytest.mark.parametrize("verification_value", (42, 43))
def test_installed_codex_service_run_retains_context_and_verifies_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verification_value: int
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    runtime_root = os.environ.get("OH_GOON_NATIVE_CODEX_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select installed Codex and its complete public runtime root explicitly")
    if sys.platform != "linux":
        pytest.skip("Installed native service qualification targets Linux")
    program, public_runtime = Path(executable), Path(runtime_root)
    assert program.is_absolute() and public_runtime.is_absolute()
    assert program.is_relative_to(public_runtime)
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    (repository / "code.py").write_text("VALUE = 41\n")
    (repository / "verify.py").write_text(
        f"from code import VALUE\nassert VALUE == {verification_value}\n"
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    git_config = (repository / ".git" / "config").read_bytes()
    skill = tmp_path / "skills" / "context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL)
    native_home = tmp_path / "synthetic-native-home"
    native_home.mkdir(mode=0o700)
    private = native_home / "native-private-canary"
    private.write_text("SYNTHETIC_NATIVE_PRIVATE_DATA")
    first_script = (
        "import os,pathlib\n"
        "source=pathlib.Path('code.py')\n"
        "assert source.read_text()=='VALUE = 41\\n'\n"
        "source.write_text('VALUE = 42\\n')\n"
        "assert 'SERVICE_RUN_KEY' not in os.environ\n"
        f"for path in (pathlib.Path({str(native_home / 'config.toml')!r}),pathlib.Path({str(private)!r})):\n"
        " try: path.read_bytes()\n"
        " except OSError: pass\n"
        " else: raise AssertionError('native private file was readable')\n"
        "try: pathlib.Path('.git/config').write_text('forbidden')\n"
        "except OSError: pass\n"
        "else: raise AssertionError('Git metadata write was allowed')\n"
        "try: pathlib.Path('.codex').mkdir()\n"
        "except OSError: pass\n"
        "else: raise AssertionError('native config directory creation was allowed')\n"
        "try: pathlib.Path('.codex/config.toml').write_text('forbidden')\n"
        "except OSError: pass\n"
        "else: raise AssertionError('native config creation was allowed')\n"
        "try: pathlib.Path('.codex').rmdir()\n"
        "except OSError: pass\n"
        "else: raise AssertionError('native metadata directory deletion was allowed')\n"
        "try: pathlib.Path('.codex').rename('.codex-moved')\n"
        "except OSError: pass\n"
        "else: raise AssertionError('native metadata directory rename was allowed')\n"
        "print('CONTEXT_FOLLOWUP_PROOF')\n"
    )
    second_script = (
        "import pathlib\n"
        "assert pathlib.Path('code.py').read_text()=='VALUE = 42\\n'\n"
        "print('CONTEXT_EDIT_PROOF')\n"
    )
    commands = tuple(
        shlex.join(("/usr/bin/python3", "-I", "-c", text)) for text in (first_script, second_script)
    )
    assert len(commands) == 2
    endpoint = _RunContextResponses((commands[0], commands[1]))
    with endpoint:
        # This synthetic native configuration is test-owned. Product code sends
        # model/protocol data and does not acquire its contents or credentials.
        native_config = (
            'model="gpt-6.1-sol"\nmodel_provider="service-fixture"\n'
            '[model_providers.service-fixture]\nname="service-fixture"\n'
            f"base_url={json.dumps(endpoint.base_url + '/v1')}\n"
            'env_key="SERVICE_RUN_KEY"\nwire_api="responses"\n'
            "requires_openai_auth=false\nsupports_websockets=false\n"
            "request_max_retries=0\nstream_max_retries=0\n"
        )
        (native_home / "config.toml").write_text(native_config)
        monkeypatch.setenv("HOME", str(tmp_path / "synthetic-controller-home"))
        monkeypatch.setenv("CODEX_HOME", str(native_home))
        monkeypatch.setenv("SERVICE_RUN_KEY", _TOKEN)
        monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        for variable in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            monkeypatch.delenv(variable, raising=False)
        selected = ResolvedNativeAgent(
            agent="codex",
            profile_name="native-service-context",
            definition=NativeAgentDefinition(
                executable=str(program), runtime_roots=(str(public_runtime),)
            ),
            profile=NativeAgentProfile(
                timeout_seconds=60.0,
                codex_service=CodexServiceConnection(
                    home=str(native_home), model="gpt-6.1-sol", reasoning_effort="low"
                ),
            ),
            executable_sha256=native_executable_sha256(program),
        )
        runtime = None
        try:
            runtime = ApplicationRuntime.build(standard_local=True)
            runtime.locations.add(repository)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=repository,
                skill=skill,
                task=_TASK,
                verification=((sys.executable, "verify.py"),),
            )
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=90)
            live_output = runtime.runs.game_tape_live_output(
                admitted.fleet_run_id
            ).model_dump_json()
            facts = {
                "outcome": detail.outcome,
                "failure": detail.model_dump(mode="json")["run"].get("failure"),
                "fixture_errors": endpoint.errors,
                "observations": endpoint.observations,
                "live_output": live_output[-16000:],
            }
            (tmp_path / "service-run-context-receipt.json").write_text(json.dumps(facts))
            assert endpoint.errors == [], facts
            assert endpoint.request_count == 4, facts
            assert all(
                item["synthetic_auth"] and item["privileged_exact_count"] == 1
                for item in endpoint.observations
            ), facts
            assert endpoint.observations[0]["task_only_user_input"], facts
            assert not any(item["user_duplicate"] for item in endpoint.observations), facts
            assert endpoint.observations[1]["followup"] and endpoint.observations[2]["edit"], facts
            assert [item["local_compaction_request"] for item in endpoint.observations] == [
                False,
                False,
                True,
                False,
            ], facts
            assert [item["retained_local_summary"] for item in endpoint.observations] == [
                False,
                False,
                False,
                True,
            ], facts
            assert endpoint.account_checks == 0 and endpoint.websocket_connections == 0, facts
            assert (repository / "code.py").read_text() == "VALUE = 42\n", facts
            assert (repository / ".git" / "config").read_bytes() == git_config, facts
            # Native Bubblewrap materializes its reserved metadata mountpoint.
            # Keep that native-owned artifact; agents cannot populate or mutate it.
            metadata_directory = repository / ".codex"
            assert metadata_directory.is_dir() and not metadata_directory.is_symlink(), facts
            assert list(metadata_directory.iterdir()) == [], facts
            assert private.read_text() == "SYNTHETIC_NATIVE_PRIVATE_DATA", facts
            assert _TOKEN not in live_output, facts
            assert detail.outcome is (
                FleetRunOutcome.SUCCESS
                if verification_value == 42
                else FleetRunOutcome.ATTENTION_REQUIRED
            ), facts
            steps = detail.game_tape.repositories[0].steps
            assert [step.status for step in steps] == [
                GameTapeStatus.PASSED,
                GameTapeStatus.PASSED if verification_value == 42 else GameTapeStatus.FAILED,
            ], facts
            native = steps[0].native_agent_observations[0]
            assert native.settled and not native.failed and native.agent == "codex", facts
            assert native.profile == selected.profile_name and any(
                event.kind == "tool" for event in native.events
            ), facts
            assert native.authority == "UNTRUSTED_HARNESS_CLAIM", facts
            assert any(event.kind == "context" for event in native.events), facts
            execution = native.execution
            assert execution is not None, facts
            assert execution.authority == "ORIGINAL_STEP_EXECUTION", facts
            assert execution.native_version == "0.163.0", facts
            assert execution.repository_access == "write", facts
            assert execution.preflight == "verified" and execution.mcp_catalog == "empty", facts
            assert execution.native_state_separation == "verified", facts
            assert execution.process_cleanup == "verified", facts
            assert steps[0].attempts[0].supervisor is not None, facts
            assert steps[0].attempts[0].supervisor.final_process_group_quiescent is True, facts
        finally:
            if runtime is not None:
                runtime.shutdown(wait_for_runs=True)


class _CampaignResponses(CodexResponsesFixture):
    """Native tools run; the model returns deterministic source-bound phase data."""

    def __init__(self, private: Path, cancelled: Event, scenario: str) -> None:
        self.private = private
        self.cancelled = cancelled
        self.scenario = scenario
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.probed: set[tuple[str, str]] = set()
        self.schema_bound_occurrences: set[tuple[str, str]] = set()
        super().__init__(transport="http", prompt=lambda: None, auth_token=_TOKEN)

    def _record(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = payload["identity"]
        repository = Path(payload["captured_repository"])
        members = json.loads(Path(payload["complete_source_manifest"]).read_text())["repository"]
        source = next(member for member in members if member["path"] == "repo/code.txt")
        contract = payload["output_contract"]
        if "oh-goon.agent-review/v1" in contract:
            clean = (repository / "code.txt").read_text() == "repaired\n"
            record = dict(
                schema="oh-goon.agent-review/v1",
                **identity,
                verdict="CLEAN" if clean else "FINDING",
                summary="Inspected complete captured source with native read-only tools",
                reviewed_paths={"complete_scope": identity["scope_identity"]},
                findings=[]
                if clean
                else [
                    dict(
                        finding_id="f1",
                        explanation="The content fails the selected behavioral check",
                        evidence=[dict(**source, line=1)],
                    )
                ],
            )
            if self.scenario == "multiple" and not clean:
                other = next(member for member in members if member["path"] == "repo/alternate.txt")
                record["findings"].append(
                    dict(
                        finding_id="f2",
                        explanation="The independent alternate-content check also fails",
                        evidence=[dict(**other, line=1)],
                    )
                )
            return record
        if "oh-goon.agent-adjudication/v1" in contract:
            references = [
                dict(reviewer=review["reviewer"], finding_id=finding["finding_id"])
                for review in payload["context"]["reviews"]
                for finding in review["findings"]
            ]
            return dict(
                schema="oh-goon.agent-adjudication/v1",
                candidate_identity=identity["candidate_identity"],
                scope_identity=identity["scope_identity"],
                dispositions=[
                    dict(**reference, decision="repair", rationale="One reproducible defect")
                    for reference in references
                ],
                queue=[
                    dict(
                        queue_id="q1" if finding == "f1" else "q2",
                        findings=[
                            reference
                            for reference in references
                            if reference["finding_id"] == finding
                        ],
                        rationale="The reviews identify the same source defect",
                        regression_expectation="The check requires repaired content",
                    )
                    for finding in dict.fromkeys(
                        reference["finding_id"] for reference in references
                    )
                ],
            )
        record = dict(
            schema="agent-patch-batch-v1",
            candidate_identity=identity["candidate_identity"],
            summary="Repair source and add the causal regression check",
            regressions=[
                dict(
                    queue_id="q1",
                    path="repo/regression.txt",
                    expectation="Require repaired content",
                )
            ],
            operations=[
                dict(
                    action="update",
                    path="repo/code.txt",
                    source_sha256=source["sha256"],
                    replacement="repaired\n",
                ),
                dict(
                    action="create",
                    path="repo/regression.txt",
                    source_sha256=None,
                    replacement='#!/bin/sh\n[ "$(cat code.txt)" = repaired ]\n',
                ),
            ],
        )
        if self.scenario == "multiple":
            other = next(member for member in members if member["path"] == "repo/alternate.txt")
            record["regressions"].append(
                dict(
                    queue_id="q2",
                    path="repo/regression-other.txt",
                    expectation="Require the independent repair",
                )
            )
            record["operations"].extend(
                [
                    dict(
                        action="update",
                        path="repo/alternate.txt",
                        source_sha256=other["sha256"],
                        replacement="independently-repaired\n",
                    ),
                    dict(
                        action="create",
                        path="repo/regression-other.txt",
                        source_sha256=None,
                        replacement='#!/bin/sh\n[ "$(cat alternate.txt)" = independently-repaired ]\n',
                    ),
                ]
            )
        return record

    def _response(
        self, body: dict[str, Any], endpoint: BaseHTTPRequestHandler, transport: str
    ) -> tuple[dict[str, Any], ...]:
        self.request_count += 1
        assert self.request_count <= 20
        assert endpoint.headers.get("Authorization") == "Bearer " + _TOKEN
        candidates = []
        for item in body["input"]:
            if item.get("role") != "developer":
                continue
            content = item.get("content", [])
            texts = (
                [content]
                if isinstance(content, str)
                else [part.get("text", "") for part in content]
            )
            for text in texts:
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if isinstance(payload, dict) and "working_workspace" in payload:
                    candidates.append(payload)
        assert len(candidates) == 1
        payload = candidates[0]
        assert "UNADMITTED_MCP_GUIDANCE_CANARY" not in json.dumps(body)
        assert "SYNTHETIC_PRIVATE_MCP_CONFIG_VALUE" not in json.dumps(body)
        identity = payload["identity"]
        key = identity["reviewer"], identity["candidate_identity"]
        output_format = body.get("text", {}).get("format")
        if "oh-goon.agent-adjudication/v1" in payload["output_contract"]:
            assert isinstance(output_format, dict) and output_format["type"] == "json_schema"
            assert output_format["strict"] is True
            schema = output_format["schema"]
            canonical_schema = AgentAdjudicationRecord.model_json_schema(by_alias=True)
            assert set(schema["properties"]) == set(canonical_schema["properties"])
            assert set(schema["required"]) == set(canonical_schema["properties"])
            assert schema["additionalProperties"] is False
            assert schema["$defs"] == canonical_schema["$defs"]
            assert schema["properties"]["candidate_identity"]["enum"] == [
                identity["candidate_identity"]
            ]
            assert schema["properties"]["scope_identity"]["enum"] == [identity["scope_identity"]]
            self.schema_bound_occurrences.add(key)
        else:
            assert output_format is None or (
                output_format.get("type") != "json_schema" and "schema" not in output_format
            )
        assert payload["working_workspace"]["repository_access"] == "read_only"
        evidence = json.loads(payload["repository_evidence"])
        assert evidence["capture_identity"] == identity["candidate_identity"]
        assert evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
        assert payload["skill_instructions"] and _TOKEN not in json.dumps(payload)
        self.inputs[key] = payload
        if key not in self.probed:
            self.probed.add(key)
            script = (
                "import os,pathlib\n"
                f"root=pathlib.Path({payload['working_workspace']['root']!r})\n"
                f"private=pathlib.Path({str(self.private)!r})\n"
                "for target in (root/'code.txt',root/'.git/config'):\n"
                " try: target.write_text('UNAUTHORIZED')\n"
                " except OSError: pass\n"
                " else: raise AssertionError('native reasoning wrote protected source')\n"
                "try: private.read_bytes()\n"
                "except OSError: pass\n"
                "else: raise AssertionError('native reasoning read its private store')\n"
                "assert 'SERVICE_CAMPAIGN_KEY' not in os.environ\n"
                "scratch=pathlib.Path(os.environ['TMPDIR'])/'phase-check'\n"
                "scratch.write_text('scratch'); scratch.unlink()\n"
                "print('CAMPAIGN_NATIVE_BOUNDARY_PROOF')\n"
            )
            command = shlex.join(("/usr/bin/python3", "-I", "-c", script))
            tool_input = "text(await tools.exec_command(" + json.dumps({"cmd": command}) + "));"
            if self.scenario == "configured_mcp":
                assert "mcp__registered" not in json.dumps(body.get("tools", []))
                tool_input = (
                    "let blocked=false; try { await tools.mcp__registered__escape({}); } "
                    "catch(error) { blocked=true; text('MCP_DISABLED_PROOF '+String(error)); } "
                    "if (!blocked) throw Error('Unadmitted MCP tool became callable'); "
                    + tool_input
                )
            item = dict(
                type="custom_tool_call",
                id=f"ctc_campaign_{self.request_count}",
                call_id=f"campaign_{self.request_count}",
                name="exec",
                namespace="functions",
                input=tool_input,
                status="completed",
            )
        else:
            outputs = [
                json.dumps(item.get("output", ""))
                for item in body["input"]
                if item.get("type") == "custom_tool_call_output"
            ]
            assert any("CAMPAIGN_NATIVE_BOUNDARY_PROOF" in output for output in outputs)
            if self.scenario == "configured_mcp":
                assert any("MCP_DISABLED_PROOF" in output for output in outputs)
            if self.scenario == "cancel" and "agent-patch-batch-v1" in payload["output_contract"]:
                self.cancelled.set()
            item = dict(
                type="message",
                id=f"msg_campaign_{self.request_count}",
                role="assistant",
                content=[
                    dict(type="output_text", text=json.dumps(self._record(payload)), annotations=[])
                ],
                status="completed",
            )
        events = self._events(f"resp_campaign_{self.request_count}", item, 1)
        for event in events:
            if isinstance(event.get("response"), dict):
                event["response"]["model"] = "gpt-6.1-sol"
        return events


@pytest.mark.certification
@pytest.mark.parametrize("scenario", ("repair", "multiple", "clean", "cancel", "configured_mcp"))
def test_installed_codex_service_campaign_preserves_phase_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    runtime_root = os.environ.get("OH_GOON_NATIVE_CODEX_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select installed Codex and its complete public runtime root explicitly")
    if sys.platform != "linux":
        pytest.skip("Installed native service qualification targets Linux")
    program, public_runtime = Path(executable), Path(runtime_root)
    assert program.is_absolute() and public_runtime.is_absolute()
    assert program.is_relative_to(public_runtime)
    native_home = tmp_path / "synthetic-native-home"
    native_home.mkdir(mode=0o700)
    private = native_home / "private-canary"
    private.write_text("SYNTHETIC_NATIVE_PRIVATE_DATA")
    refused_mcp_threads: list[str] = []
    mcp_configuration = ""
    if scenario == "configured_mcp":
        original_exclusion_check = CodexServiceClient.require_mcp_servers_excluded

        async def verify_native_tool_refusal(client: CodexServiceClient, thread_id: str) -> None:
            await original_exclusion_check(client, thread_id)
            # Use the native MCP RPC with its exact server/tool identity. The
            # Code Mode helper spelling must not be the sole exclusion proof.
            with pytest.raises(AgentProviderError, match="refused"):
                await client._request(
                    "mcpServer/tool/call",
                    {
                        "threadId": thread_id,
                        "server": "registered",
                        "tool": "escape",
                        "arguments": {},
                    },
                )
            refused_mcp_threads.append(thread_id)

        monkeypatch.setattr(
            CodexServiceClient, "require_mcp_servers_excluded", verify_native_tool_refusal
        )
        public_mcp = tmp_path / "public-mcp"
        public_mcp.mkdir()
        mcp_trace = tmp_path / "mcp-startup-trace"
        mcp_program = public_mcp / "server.py"
        mcp_program.write_text(
            textwrap.dedent("""\
            import json
            import pathlib
            import sys

            trace = pathlib.Path(sys.argv[1])
            with trace.open("a") as stream:
                stream.write("startup\\n")
            for line in sys.stdin:
                request = json.loads(line)
                if "id" not in request:
                    continue
                method = request.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": request["params"]["protocolVersion"],
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "synthetic-registered", "version": "1"},
                        "instructions": "UNADMITTED_MCP_GUIDANCE_CANARY",
                    }
                elif method == "tools/list":
                    result = {"tools": [{
                        "name": "escape", "description": "Must never reach the workload",
                        "inputSchema": {"type": "object", "properties": {}},
                    }]}
                elif method == "tools/call":
                    with trace.open("a") as stream:
                        stream.write("CALLED\\n")
                    result = {"content": [{"type": "text", "text": "UNAUTHORIZED"}]}
                else:
                    result = {"resources": [], "resourceTemplates": [], "prompts": []}
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
            """)
        )
        mcp_configuration = (
            '\n[mcp_servers.registered]\ncommand="/usr/bin/python3"\nargs='
            + json.dumps(["-u", str(mcp_program), str(mcp_trace)])
            + '\n[mcp_servers.registered.env]\nMCP_PRIVATE_KEY="SYNTHETIC_PRIVATE_MCP_CONFIG_VALUE"\n'
        )
    cancelled = Event()
    with _CampaignResponses(private, cancelled, scenario) as endpoint:
        (native_home / "config.toml").write_text(
            'model="gpt-6.1-sol"\nmodel_provider="campaign-fixture"\n'
            '[model_providers.campaign-fixture]\nname="campaign-fixture"\n'
            f"base_url={json.dumps(endpoint.base_url + '/v1')}\n"
            'env_key="SERVICE_CAMPAIGN_KEY"\nwire_api="responses"\n'
            "requires_openai_auth=false\nsupports_websockets=false\n"
            "request_max_retries=0\nstream_max_retries=0\n" + mcp_configuration
        )
        native_config_before = (native_home / "config.toml").read_bytes()
        monkeypatch.setenv("SERVICE_CAMPAIGN_KEY", _TOKEN)
        monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state/app.sqlite"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv(
            "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        )
        selected = ResolvedNativeAgent(
            agent="codex",
            profile_name="native-service-campaign",
            definition=NativeAgentDefinition(
                executable=str(program), runtime_roots=(str(public_runtime),)
            ),
            profile=NativeAgentProfile(
                codex_service=CodexServiceConnection(
                    home=str(native_home), model="gpt-6.1-sol", reasoning_effort="low"
                )
            ),
            executable_sha256=native_executable_sha256(program),
        )
        resolver, path, repository, options = agent_review_case(tmp_path, native_agent=selected)
        write_roots = ("code.txt", "regression.txt")
        if scenario == "multiple":
            (repository / "alternate.txt").write_text("independently-broken\n")
            check = repository / "check"
            check.write_text(check.read_text() + "/bin/sh regression-other.txt\n")
            write_roots += ("alternate.txt", "regression-other.txt")
        if scenario == "clean":
            (repository / "code.txt").write_text("repaired\n")
        source = yaml.safe_load(
            (tmp_path / "native-goons/agents/agentic-review-repair-loop/goon.yml").read_text()
        )
        source["execution"] = {"purpose": "verification"}
        for edge in source["goons"]:
            edge["from"] = "native-goons"
        path.write_text(yaml.safe_dump(source, sort_keys=False))
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        git_config = (repository / ".git/config").read_bytes()
        plan, _, _ = resolver.resolve_frozen_agents(path, **options)
        lock = build_execution_lock(plan)
        write_execution_lock(path.parent / "goon.lock", lock)
        lock_before = (path.parent / "goon.lock").read_bytes()
        runtime = ApplicationRuntime.build(standard_local=True)
        try:
            runtime.locations.add(repository.parent)
            result = runtime.plans.repair(
                path,
                repository_id=repository_id(repository),
                budget=AgentRepairBudget(
                    max_attempts=2, timeout_seconds=180, step_timeout_seconds=30
                ),
                inputs=options["inputs"],
                agent_repository_root=options["repository_root"],
                agent_skill_root=options["skill_root"],
                allowed_agent_acceptance=options["allowed_acceptance"],
                review_write_roots=write_roots,
                native_agent=selected,
                native_workspace=repository,
                cancel_requested=cancelled.is_set,
            )
            facts = dict(
                stop=result.stop_reason.value, message=result.message, errors=endpoint.errors
            )
            assert endpoint.errors == [], facts
            assert len(endpoint.schema_bound_occurrences) == (0 if scenario == "clean" else 1)
            assert result.stop_reason.value == (
                "cancelled" if scenario == "cancel" else "converged"
            ), facts
            assert result.test_passed is (scenario != "cancel"), facts
            phases, reviews, native_claims = [], [], []
            for index, detail in enumerate(result.attempts):
                target = detail.run.target
                assert isinstance(target, FleetPlanTarget) and target.agent_repair is not None
                context = target.agent_repair
                phases.append(context.phase)
                assert context.previous_run_id == (
                    result.admitted_run_ids[index - 1] if index else None
                )
                assert context.original_execution_digest == lock.digest
                assert runtime.runs.get(detail.run.fleet_run_id).operations == detail.operations
                for operation in detail.operations:
                    execution = getattr(operation.evidence, "plan", None)
                    for step in execution.steps if execution is not None else ():
                        for observation in step.observations:
                            if isinstance(observation, AgentReviewObservation) and isinstance(
                                observation.record, AgentReviewRecord
                            ):
                                reviews.append(observation.record)
                            if isinstance(observation, NativeAgentObservation):
                                native_claims.append(observation)
                                if observation.execution is not None:
                                    assert observation.execution.repository_access == "read_only"
                                    assert observation.execution.process_cleanup == "verified"
                                    assert observation.execution.mcp_catalog == "empty"
                live = runtime.runs.game_tape_live_output(detail.run.fleet_run_id).model_dump_json()
                assert _TOKEN not in live
            assert (
                phases
                == {
                    "repair": [
                        "review",
                        "adjudicate",
                        "repair",
                        "regression",
                        "review",
                        "verification",
                    ],
                    "multiple": [
                        "review",
                        "adjudicate",
                        "repair",
                        "regression",
                        "review",
                        "verification",
                    ],
                    "configured_mcp": [
                        "review",
                        "adjudicate",
                        "repair",
                        "regression",
                        "review",
                        "verification",
                    ],
                    "clean": ["review", "verification"],
                    "cancel": ["review", "adjudicate", "repair"],
                }[scenario]
            ), facts
            if scenario == "cancel":
                # Cancellation can precede final native observation publication.
                # Only the five already completed reasoning Steps are guaranteed.
                assert 5 <= len(native_claims) <= 6
            else:
                assert (
                    len(native_claims)
                    == {"repair": 10, "multiple": 10, "clean": 4, "configured_mcp": 10}[scenario]
                )
            if scenario in ("repair", "multiple", "configured_mcp"):
                assert [review.verdict for review in reviews] == ["FINDING"] * 4 + ["CLEAN"] * 4
                assert reviews[0].candidate_identity != reviews[-1].candidate_identity
                assert "repo/regression.txt" in reviews[-1].reviewed_paths
                assert (repository / "regression.txt").is_file()
                assert len(reviews[0].findings) == (2 if scenario == "multiple" else 1)
            if scenario == "multiple":
                assert (repository / "alternate.txt").read_text() == "independently-repaired\n"
                assert (repository / "regression-other.txt").is_file()
            if scenario == "cancel":
                assert (repository / "code.txt").read_text() == "broken\n"
                assert not (repository / "regression.txt").exists()
            else:
                assert not result.unresolved_run_ids
                assert (repository / "code.txt").read_text() == "repaired\n"
            assert (repository / ".git/config").read_bytes() == git_config
            assert (path.parent / "goon.lock").read_bytes() == lock_before
            assert private.read_text() == "SYNTHETIC_NATIVE_PRIVATE_DATA"
            assert (native_home / "config.toml").read_bytes() == native_config_before
            if scenario == "configured_mcp":
                startup_trace = mcp_trace.read_text().splitlines()
                assert startup_trace and set(startup_trace) == {"startup"}
                assert len(endpoint.probed) == 10
                assert len(refused_mcp_threads) == len(set(refused_mcp_threads)) == 10
            invocation = project_agent_campaign(
                result.repair_id, result.attempts, invocation=result
            )
            assert invocation["result"] == ("incomplete" if scenario == "cancel" else "converged")
            assert invocation["controller_result"]["stop_reason"] == result.stop_reason.value
            assert [row["fleet_run_id"] for row in invocation["runs"]] == list(
                result.admitted_run_ids
            )
            assert len(invocation["findings"]) == sum(len(review.findings) for review in reviews)
            if scenario in ("repair", "multiple", "configured_mcp"):
                assert invocation["initial_candidate"] != invocation["final_candidate"]
                assert all(
                    finding["adjudication"] is not None for finding in invocation["findings"]
                )
                assert (
                    invocation["repair_attempts"][0]["candidate_after"]
                    == invocation["final_candidate"]
                )
            if result.converged:
                assert invocation["verification"][-1]["phase"] == "verification"
                assert invocation["verification"][-1]["unverified_step_ids"] == []
            retained = runtime.runs.agent_campaign(result.admitted_run_ids[-1])
            assert retained["campaign_id"] == result.repair_id
            assert retained["result"] == "incomplete"
            assert retained["controller_result"]["availability"] == "unavailable"
            assert retained["runs"] == invocation["runs"]
            assert retained["findings"] == invocation["findings"]
            assert retained["verification"] == invocation["verification"]
            assert _TOKEN not in json.dumps((invocation, retained))
        finally:
            runtime.shutdown(wait_for_runs=True)
        if scenario == "cancel":
            inference_requests = endpoint.request_count
            reopened = ApplicationRuntime.build(standard_local=True)
            try:
                recovered = reopened.runs.agent_campaign(result.admitted_run_ids[-1])
                assert recovered["result"] == "incomplete"
                assert recovered["controller_result"]["availability"] == "unavailable"
                for field in ("runs", "reviews", "findings", "repair_attempts", "verification"):
                    assert recovered[field] == retained[field]
                assert _TOKEN not in json.dumps(recovered)
                assert (repository / "code.txt").read_text() == "broken\n"
                assert not (repository / "regression.txt").exists()
            finally:
                reopened.shutdown(wait_for_runs=True)
            assert endpoint.request_count == inference_requests
