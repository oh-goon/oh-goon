"""Real managed Pi tools exchange selected notes while both original Runs are active."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkRequest
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.engine.git import GitService
from oh_goon.runtime.agent_notes import AgentNotesStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.parallel_agent_work import maintain_parallel_note_retention
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import native_two_run_case

_POLICY_ENV = "OH_GOON_HUG225_NOTE_POLICY"
_WORK_ID = "native-peer-note-proof"
_IDEA = "Use the independently captured candidate before adjudication."
_REPLY = "The alternative candidate kept its own verifier input."


def _call(name: str, arguments: dict[str, Any], call_id: str) -> tuple[dict[str, Any], str]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }, "tool_calls"


def _edit_candidate(label: str, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    tool = next(
        item["function"]
        for item in body["tools"]
        if item["function"]["name"] in {"bash", "shell", "shell_command", "exec_command"}
    )
    value = 42 if label == "a" else 43
    source = f"from pathlib import Path; Path('code.py').write_text('VALUE = {value}\\n')"
    arguments = {
        "command" if "command" in tool["parameters"]["properties"] else "cmd": shlex.join(
            ("/usr/bin/python3", "-c", source)
        )
    }
    if "description" in tool["parameters"]["properties"]:
        arguments["description"] = "Edit the admitted candidate"
    return _call(tool["name"], arguments, f"edit_{label}")


@pytest.mark.certification
@pytest.mark.no_resume
def test_two_native_pi_candidates_exchange_notes_live(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    native_pi = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    assert native_pi and runtime_root, "Select the installed Pi executable and runtime root"
    selected = os.environ.get(_POLICY_ENV)
    if selected is None:
        with selected_native_concurrency_environment(
            aggregate_resources=CicdResourcePolicy(
                cpu_millis=2000, memory_mib=3072, max_processes=1024
            ),
            worker_resources=CicdResourcePolicy(cpu_millis=750, memory_mib=768, max_processes=256),
            controller_reserve=CicdResourcePolicy(
                cpu_millis=500, memory_mib=1024, max_processes=256
            ),
        ) as environment:
            result = environment.run(
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "-m",
                    "certification",
                    str(Path(__file__)) + "::" + request.node.name,
                ),
                cwd=Path(__file__).parents[3],
                environment={**os.environ, _POLICY_ENV: environment.policy.model_dump_json()},
                timeout=240,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout)
        return

    policy = RunConcurrencyPolicy.model_validate_json(selected)
    published_a, published_b, read_b, done = Event(), Event(), Event(), Event()
    turns = {"a": 0, "b": 0}

    def exchange(label: str, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        turns[label] += 1
        turn = turns[label]
        tools = {item["function"]["name"] for item in body["tools"]}
        assert {"agent_notes_read", "agent_notes_publish"} <= tools
        tool_returns = json.dumps(
            [message for message in body["messages"] if message.get("role") == "tool"]
        )
        if label == "a" and turn == 1:
            return _call(
                "agent_notes_publish",
                {"request_id": "a-idea", "kind": "idea", "body": _IDEA},
                "publish_a",
            )
        if label == "a" and turn == 2:
            assert "a-idea" in tool_returns
            published_a.set()
            assert published_b.wait(30), "B did not publish a reply"
            return _call("agent_notes_read", {"after": 1}, "read_a")
        if label == "a" and turn == 3:
            assert _REPLY in tool_returns and "UNTRUSTED_AGENT_CONTEXT" in tool_returns
            read_b.set()
            return _edit_candidate(label, body)
        if label == "b" and turn == 1:
            assert published_a.wait(30), "A did not publish an idea"
            return _call("agent_notes_read", {}, "read_b")
        if label == "b" and turn == 2:
            assert _IDEA in tool_returns and "UNTRUSTED_AGENT_CONTEXT" in tool_returns
            return _call(
                "agent_notes_publish",
                {"request_id": "b-reply", "kind": "observation", "reply_to": 1, "body": _REPLY},
                "publish_b",
            )
        if label == "b" and turn == 3:
            assert "b-reply" in tool_returns
            published_b.set()
            return _edit_candidate(label, body)
        assert turn == 4, (label, turn)
        assert done.wait(30), "The live note exchange could not be inspected"
        return {"role": "assistant", "content": "Candidate edited."}, "stop"

    with native_two_run_case(
        tmp_path,
        policy,
        program=Path(native_pi).resolve(strict=True),
        public_runtime=Path(runtime_root).resolve(strict=True),
        same_repository=True,
        model_turn=exchange,
    ) as case:
        repository = case.repositories["a"]
        base = GitService().observe(repository).head_sha
        assert base is not None
        executable = Path("/usr/bin/python3").resolve(strict=True)
        verifier = (
            "import pathlib,sys\n"
            "value = pathlib.Path(sys.argv[1], 'code.py').read_bytes()\n"
            "assert value in (b'VALUE = 42\\n', b'VALUE = 43\\n')\n"
        )
        started = case.runtime.plans.start_parallel_agent_work(
            ParallelAgentWorkRequest(
                work_id=_WORK_ID,
                workspace=repository,
                expected_base_revision=base,
                task="Compare two approaches while sharing a short idea and reply.",
                briefs=("Set VALUE to 42", "Set VALUE to 43"),
                native_profiles=(case.selected["a"].profile_name, case.selected["b"].profile_name),
                capacity=policy,
                notes_enabled=True,
                verifier=ParallelCandidateVerifier(
                    acceptance="The candidate contains one permitted VALUE alternative.",
                    executable=str(executable),
                    executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                    arguments=("-I", "{input:verify.py}", "{candidate}"),
                    inputs=(
                        FrozenVerifierInput(
                            name="verify.py",
                            content=verifier,
                            sha256=hashlib.sha256(verifier.encode()).hexdigest(),
                        ),
                    ),
                ),
            ),
            selected=(case.selected["a"], case.selected["b"]),
        )
        run_a, run_b = (child.fleet_run_id for child in started.children)
        try:
            assert all(child.dispatch == "admitted" for child in started.children)
            assert read_b.wait(60), (case.errors, turns)
            assert all(
                not case.runtime.runs.get(run_id).run.status.terminal for run_id in (run_a, run_b)
            )
            fresh_database = LocalDatabase(case.database_path, read_only=True)
            with fresh_database.transaction() as connection:
                assert (
                    connection.execute(
                        "SELECT expires_at FROM parallel_agent_note_expiry WHERE work_id=?",
                        (_WORK_ID,),
                    ).fetchone()
                    is None
                )
            notes = AgentNotesStore(fresh_database, Journal(fresh_database))
            records = notes.read_page(_WORK_ID).notes
            assert [record.body for record in records] == [_IDEA, _REPLY]
            assert [record.receipt.author.fleet_run_id for record in records] == [run_a, run_b]
            assert records[1].receipt.reply_to == records[0].receipt.note_id
            delivered = notes.deliveries(_WORK_ID)
            assert [(item.note_id, item.receiving_run_id, item.outcome) for item in delivered] == [
                (1, run_b, "acknowledged"),
                (2, run_a, "acknowledged"),
            ]
        finally:
            done.set()
            published_a.set()
            published_b.set()

        for run_id in (run_a, run_b):
            outcome = case.runtime.runs.wait_for_terminal(run_id, timeout=60)
            assert outcome.outcome.value == "SUCCESS", outcome.model_dump_json()
            tape = case.runtime.runs.get(run_id).game_tape
            assert tape is not None
            assert _IDEA not in tape.model_dump_json()
            assert _REPLY not in tape.model_dump_json()
        # Programmatic runs converge after their original resource release without
        # a start-parallel CLI waiting in the same process. A cold pass recovers
        # the same deadline if the earlier process died before the ancillary save.
        settled_database = LocalDatabase(case.database_path)
        with settled_database.transaction(write=True) as connection:
            original_expiry = connection.execute(
                "SELECT expires_at FROM parallel_agent_note_expiry WHERE work_id=?", (_WORK_ID,)
            ).fetchone()
            assert original_expiry is not None
            connection.execute(
                "DELETE FROM parallel_agent_note_expiry WHERE work_id=?", (_WORK_ID,)
            )
        maintain_parallel_note_retention(settled_database)
        with settled_database.transaction() as connection:
            recovered_expiry = connection.execute(
                "SELECT expires_at FROM parallel_agent_note_expiry WHERE work_id=?", (_WORK_ID,)
            ).fetchone()
            assert recovered_expiry is not None and recovered_expiry[0] == original_expiry[0]
        assert not case.errors, case.errors
        assert (repository / "code.py").read_text().endswith("VALUE = 41\n")
