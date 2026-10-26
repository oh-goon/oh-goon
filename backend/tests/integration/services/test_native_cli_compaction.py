"""Installed native compaction owns context recovery inside the original Run.

Only the compatible provider is synthetic. Native overflow recognition,
summarization, continuation, typed acceptance and process cleanup remain real.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.agent_review import AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime


@pytest.mark.certification
@pytest.mark.parametrize("agent", ["pi", "opencode"])
def test_installed_native_cli_preserves_frozen_context_through_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: Literal["pi", "opencode"]
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
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    original = b"VALUE = 42\n"
    (repository / "source.py").write_bytes(original)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    skill = tmp_path / "skills" / "context"
    skill.mkdir(parents=True)
    # The bounded history exceeds Pi's native retained-tail default so its
    # overflow recovery has meaningful earlier input to summarize.
    skill_text = "# Compaction qualification\nRetain native-frozen-skill-canary.\n" + (
        "Preserve the supplied task, source identity and complete review scope.\n" * 1600
    )
    (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
    task = "Review source.py and retain native-original-task-canary through context recovery."
    identity: dict[str, str] | None = None
    producer_evidence: str | None = None
    output_contract: str | None = None
    observations: list[str] = []
    fixture_failure: str | None = None
    request_count = 0
    compaction_marker = "native-compacted-context-canary"

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
            nonlocal identity, producer_evidence, output_contract, fixture_failure, request_count
            try:
                size = int(self.headers["Content-Length"])
                assert 0 < size <= 1024 * 1024
                body = json.loads(self.rfile.read(size))
                request_count += 1
                assert request_count <= 6, "Native recovery exceeded the bounded scenario"
                assert self.path == "/v1/chat/completions"
                assert self.headers["Authorization"] == "Bearer SYNTHETIC_COMPACTION_KEY"
                assert body["model"] == "native-compaction-fixture"
                privileged = "\n".join(
                    text
                    for message in body["messages"]
                    if message.get("role") in {"system", "developer"}
                    for text in texts(message)
                )
                assert "Hashmarks" in privileged and skill_text in privileged
                all_text = "\n".join(
                    text for message in body["messages"] for text in texts(message)
                )
                if identity is None:
                    for message in body["messages"]:
                        if message.get("role") != "user":
                            continue
                        for text in texts(message):
                            start = text.find('{"source_authority":')
                            if start < 0:
                                continue
                            payload, _ = json.JSONDecoder().raw_decode(text[start:])
                            assert payload["task"] == task
                            assert payload["skill_instructions"] == skill_text
                            identity = payload["identity"]
                            producer_evidence = payload["repository_evidence"]
                            output_contract = payload["output_contract"]
                            evidence = json.loads(producer_evidence)
                            assert evidence["producer"] == "Hashmarks"
                            assert (
                                evidence["producer_implementation_identity"]
                                == require_loaded_hashmarks_runtime()
                            )
                            assert evidence["capture_identity"] == identity["candidate_identity"]
                assert identity is not None and producer_evidence is not None
                assert output_contract is not None
                assert producer_evidence in privileged
                assert task in all_text
                summarizing = (
                    "You are a context summarization assistant." in privileged
                    if agent == "pi"
                    else "Return only the structured summary in the requested format." in all_text
                )
                if summarizing:
                    assert "overflow" in observations
                    observations.append("compaction")
                    summary = (
                        compaction_marker
                        + "\nOriginal task: "
                        + task
                        + "\nFrozen identity: "
                        + json.dumps(identity)
                        + "\nRequired output: "
                        + output_contract
                    )
                    if agent == "opencode":
                        template = re.search(r"<template>\n(.*?)\n</template>", all_text, re.DOTALL)
                        assert template is not None
                        headings = re.findall(r"^#{1,3} .+", template[1], re.MULTILINE)
                        assert headings == [
                            "## Objective",
                            "## Requirements",
                            "## Decisions",
                            "## Work State",
                            "### Completed",
                            "### Active",
                            "### Blocked",
                            "## Next Move",
                            "## Relevant Files",
                            "## Important Context",
                        ]
                        summary = (
                            "## Objective\n- "
                            + task
                            + "\n## Requirements\n- "
                            + output_contract
                            + "\n## Decisions\n- Frozen source and identity remain selected."
                            + "\n## Work State\n- Continue the admitted review."
                            + "\n### Completed\n- "
                            + compaction_marker
                            + "\n### Active\n- Review source.py."
                            + "\n### Blocked\n- (none)"
                            + "\n## Next Move\n- Return the required typed review."
                            + "\n## Relevant Files\n- source.py: complete review scope."
                            + "\n## Important Context\n- "
                            + json.dumps(identity)
                        )
                    self.reply(summary)
                elif body.get("tools"):
                    if "overflow" not in observations:
                        observations.append("overflow")
                        self.reply_error()
                        return
                    assert "compaction" in observations
                    assert compaction_marker in all_text
                    assert output_contract in all_text
                    assert all(value in all_text for value in identity.values())
                    observations.append("continued")
                    record = {
                        "schema": "oh-goon.agent-review/v1",
                        **identity,
                        "verdict": "CLEAN",
                        "summary": "Native compaction context retained.",
                        "reviewed_paths": {"complete_scope": identity["scope_identity"]},
                        "findings": [],
                    }
                    self.reply(json.dumps(record))
                else:
                    observations.append("context-only")
                    self.reply("Native context qualification")
            except Exception as error:
                fixture_failure = fixture_failure or str(error)[:512]
                self.reply("Fixture rejected recovery; no typed review claim.")

        def reply_error(self) -> None:
            error = json.dumps(
                {
                    "error": {
                        "message": "context_length_exceeded: Your input exceeds the context window of this model",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                    }
                }
            )
            # Protocol errors inside an established native stream remain native
            # protocol facts; HTTP failure bodies are intentionally withheld by
            # the existing inference boundary.
            data = ("data: " + error + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def reply(self, text: str) -> None:
            chunks = [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
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
    monkeypatch.setenv("NATIVE_COMPACTION_KEY", "SYNTHETIC_COMPACTION_KEY")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever)
        worker.start()
        try:
            catalog = tmp_path / "providers.json"
            catalog.write_text(
                json.dumps(
                    {
                        "providers": {
                            "local": {
                                "api": "openai-chat-completions",
                                "base_url": f"http://127.0.0.1:{endpoint.server_port}/v1",
                                "api_key_env": "NATIVE_COMPACTION_KEY",
                            }
                        },
                        "models": {
                            "local": {"provider": "local", "model": "native-compaction-fixture"}
                        },
                        "native_profiles": {
                            "local": {
                                "backend": "bubblewrap",
                                "allowed_agents": [agent],
                                "workspace_roots": [str(repository.parent)],
                                "inference_profile": "local",
                                "timeout_seconds": 45,
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
                agent=agent, profile="local", workspace=repository, config_file=catalog
            )
            runtime = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                runtime.locations.add(repository)
                admitted = runtime.plans.run_native_agent(
                    selected=selected,
                    workspace=repository,
                    skill=skill,
                    task=task,
                    role="review",
                    verification=(
                        (
                            "/usr/bin/python3",
                            "-I",
                            "-c",
                            "from pathlib import Path; "
                            + f"assert Path('source.py').read_bytes() == {original!r}",
                        ),
                    ),
                )
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=60)
                assert detail.outcome is FleetRunOutcome.SUCCESS, (
                    fixture_failure,
                    observations,
                    runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[
                        -8192:
                    ],
                )
                assert fixture_failure is None
                assert observations.count("overflow") == observations.count("continued") == 1
                assert observations.count("compaction") == 1
                assert observations.count("context-only") <= 1
                assert observations[-1] == "continued"
                operation = detail.operations[0].evidence
                assert isinstance(operation, FleetPlanOperationEvidence)
                native = operation.plan.steps[0]
                records = [
                    item.record
                    for item in native.observations
                    if isinstance(item, AgentReviewObservation)
                ]
                assert len(records) == 1 and isinstance(records[0], AgentReviewRecord)
                assert records[0].verdict == "CLEAN" and records[0].reviewed_paths == (
                    "repo/source.py",
                )
                steps = detail.game_tape.repositories[0].steps
                assert steps[0].settlement.outcome == steps[1].settlement.outcome == "SUCCESS"
                assert steps[1].native_agent_observations == ()
                for step in steps:
                    assert step.settlement.cleanup == "VERIFIED"
                    assert step.attempts[0].supervisor is not None
                    assert step.attempts[0].supervisor.final_process_group_quiescent is True
                if agent == "pi":
                    context = [
                        event.summary
                        for claim in steps[0].native_agent_observations
                        for event in claim.events
                        if event.kind == "context"
                    ]
                    assert context == ["pi: compaction_start", "pi: compaction_end"]
                assert (repository / "source.py").read_bytes() == original
                assert "SYNTHETIC_COMPACTION_KEY" not in detail.model_dump_json()
                print(
                    json.dumps(
                        {
                            "agent": agent,
                            "version": version,
                            "stages": observations,
                            "outcome": detail.outcome.value,
                        }
                    )
                )
            finally:
                runtime.shutdown(wait_for_runs=True)
            retained_requests = request_count
            reopened = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                retained = reopened.runs.get(admitted.fleet_run_id)
                assert (
                    retained.run.fleet_run_id
                    == retained.game_tape.fleet_run_id
                    == admitted.fleet_run_id
                )
                retained_steps = retained.game_tape.repositories[0].steps
                assert (
                    retained_steps[0].native_agent_observations
                    == steps[0].native_agent_observations
                )
                assert tuple(step.settlement for step in retained_steps) == tuple(
                    step.settlement for step in steps
                )
                assert retained.operations == detail.operations
            finally:
                reopened.shutdown(wait_for_runs=True)
            assert request_count == retained_requests
        finally:
            endpoint.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
