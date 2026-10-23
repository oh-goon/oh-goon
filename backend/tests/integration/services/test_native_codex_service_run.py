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
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
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
