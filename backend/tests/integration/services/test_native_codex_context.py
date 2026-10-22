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
from pathlib import Path
from typing import Any, Literal

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
from tests.fakes.codex_auth import prepare_codex_auth
from tests.fakes.codex_responses import CodexResponsesFixture


@pytest.mark.certification
@pytest.mark.parametrize("authentication", ("api-key", "chatgpt"))
@pytest.mark.parametrize("transport", ("http", "websocket"))
def test_installed_codex_retains_instructions_across_followup_and_compaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authentication: str,
    transport: Literal["http", "websocket"],
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    if not executable:
        pytest.skip("Select an installed Codex executable explicitly")
    if authentication == "chatgpt" and transport == "http" and sys.version_info < (3, 14):
        pytest.skip("Native compressed ChatGPT fixture requires Python 3.14 compression.zstd")
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
    auth = prepare_codex_auth(home, authentication=authentication)
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
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(home))
    with CodexResponsesFixture(
        transport=transport,
        prompt=lambda: prompt,
        auth_token=auth.token,
        account_id=auth.account_id,
        tls_certificate=auth.certificate,
        tls_key=auth.key,
    ) as endpoint:

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
                destination = (
                    "chatgpt_base_url="
                    + json.dumps(
                        endpoint.base_url.replace("127.0.0.1", "localhost") + "/backend-api/"
                    )
                    if authentication == "chatgpt"
                    else "openai_base_url=" + json.dumps(endpoint.base_url + "/v1")
                )
                actual = (*argv[:-1], "-c", destination, argv[-1])
                if auth.ca is not None:
                    kwargs["env"] = {**kwargs["env"], "CODEX_CA_CERTIFICATE": str(auth.ca)}
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
            assert not endpoint.errors, (
                endpoint.errors,
                endpoint.observations,
                [(result.returncode, result.stderr[-2000:]) for result in native_results],
            )
            assert endpoint.request_count == 4, (
                native_launch_count,
                [(result.returncode, result.stderr[-4000:]) for result in native_results],
                runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[-8000:],
            )
            observations = endpoint.observations
            assert len(observations) == 4
            assert all(item["synthetic_auth"] for item in observations)
            expected_path = "/backend-api/codex/responses" if auth.account_id else "/v1/responses"
            assert all(item["path"] == expected_path for item in observations)
            inference_host = endpoint.base_url.split("//", 1)[1]
            assert all(item["host"] == inference_host for item in observations)
            assert all(item["account_id"] == auth.account_id for item in observations)
            if authentication == "chatgpt":
                assert endpoint.account_checks >= 1
                assert all(item["synthetic_auth"] for item in endpoint.routing_observations)
                discovery_host = inference_host.replace("127.0.0.1", "localhost")
                assert all(item["host"] == discovery_host for item in endpoint.routing_observations)
                assert all(
                    item["account_id"] == auth.account_id for item in endpoint.routing_observations
                )
            else:
                assert endpoint.account_checks == 0
            # stdin also duplicates these bytes as user content. That copy
            # cannot satisfy the separate exact privileged-context assertion.
            assert observations[0]["user_duplicate"]
            if transport == "http":
                assert all(item["privileged_exact_count"] == 1 for item in observations)
                assert endpoint.websocket_connections == 0
                assert endpoint.http_inference_requests == 4
            else:
                assert endpoint.websocket_connections == 1
                assert endpoint.warmup_count == 1
                warmup = endpoint.warmup_observations[0]
                assert warmup["privileged_exact_count"] == 1
                assert warmup["synthetic_auth"]
                assert warmup["account_id"] == auth.account_id
                assert warmup["path"] == expected_path
                assert warmup["host"] == inference_host
                assert observations[0]["previous_response_id"] == warmup["response_id"]
                assert endpoint.http_inference_requests == 0
                assert all(item["transport"] == "websocket" for item in observations)
                assert [item["privileged_exact_count"] for item in observations] == [0, 0, 0, 1]
                assert observations[1]["previous_response_id"] == observations[0]["response_id"]
                assert observations[2]["previous_response_id"] == observations[1]["response_id"]
                assert observations[3]["previous_response_id"] is None
                assert all(item["privileged_in_chain"] for item in observations)
            assert not observations[0]["retained_tool_outputs"]
            assert observations[1]["followup"]
            assert observations[2]["edit"]
            if transport == "http":
                assert observations[2]["followup"]
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
            if runtime is not None:
                runtime.shutdown(wait_for_runs=True)
