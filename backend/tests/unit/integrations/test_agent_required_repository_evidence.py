"""Required repository evidence stays inside the original captured Agent Step."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from oh_goon.core.agent_model_selection import prepend_repository_evidence
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
)
from oh_goon.core.models.agent_source import (
    FrozenAgentSourceCapture,
    FrozenAgentStepHandoff,
    FrozenSourceMember,
    FrozenSourceTree,
)
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects, ResolvedAgentStep
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution
from oh_goon.integrations.fleet_plan_step_execution import (
    CommandRunner,
    StepRuntime,
    _execute_typed_step,
    _StepProcess,
    prepare_frozen_readonly_skill_search,
)
from oh_goon.integrations.hashmarks.agent_tools import prompt_observation, repository_observation
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

_LOCAL_HARNESSES = (
    "local-model-search",
    "local-model-review",
    "local-model-tools",
    "local-model-files",
    "local-model-skill-resources",
    "local-model-proposal",
)
_PROVIDER_HARNESSES = ("provider-review", "provider-adjudicate", "provider-proposal")


def _tree(kind: str, entries: dict[str, bytes]) -> FrozenSourceTree:
    members = tuple(
        FrozenSourceMember(path=path, data=data, sha256=hashlib.sha256(data).hexdigest())
        for path, data in sorted(entries.items())
    )
    identity = stable_id(
        "agent-source-closure-v1",
        {"members": [{"path": m.path, "sha256": m.sha256, "bytes": len(m.data)} for m in members]},
    )
    return FrozenSourceTree(kind=kind, content_identity=identity, members=members)


def _case(
    tmp_path: Path,
    harness: str,
    runner: CommandRunner,
    *,
    cancelled: Callable[[], bool] | None = None,
    supervision: CommandRuntimeSupervision | None = None,
) -> tuple[FrozenAgentStepHandoff, ResolvedAgentStep, _StepProcess, StepRuntime]:
    provider = harness.startswith("provider-")
    handoff = FrozenAgentStepHandoff(
        source=FrozenAgentSourceCapture(
            plan_source_sha256="a" * 64,
            plan_graph_identity="original-plan",
            acceptance_profile="review",
            repository_reference="local:unavailable/repository",
            resolved_skill_reference="local:unavailable/skill",
            authored_skill_reference="review-skill",
            repository=_tree(
                "repository",
                {
                    "code.py": b"def needle():\n    return 1\n",
                    "other.txt": b"entire review scope\n",
                },
            ),
            skill=_tree("skill", {"SKILL.md": b"# Review all supplied source\n"}),
        ),
        owner_source_sha256="b" * 64,
        step_id="review",
        harness=harness,
        task="Review needle behavior",
        timeout_seconds=30,
        requested_effects=AgentStepRequestedEffects()
        if provider
        else AgentStepRequestedEffects(recipients=("ollama_local",), model="review-model"),
    )
    packet = handoff.encode_prework_delivery()
    connection = (
        AgentConnection.select(
            AgentProvidersConfig(
                default_model="review",
                providers={
                    "test": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:12345"
                    )
                },
                models={"review": AgentModel(provider="test", model="test/reviewer")},
            )
        )
        if provider
        else None
    )
    step = ResolvedAgentStep.model_validate(
        dict(
            step_id=handoff.step_id,
            timeout_seconds=30,
            workload={"harness": harness, "model": None, "connection": connection}
            if provider
            else {"harness": harness, "model": "review-model"},
            handoff_identity=handoff.handoff_identity,
            source_step_id=handoff.step_id,
            delivery_sha256=hashlib.sha256(packet).hexdigest(),
        )
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    process = SimpleNamespace(
        paths=SimpleNamespace(tmp=scratch),
        environment={},
        host_environment={},
        command_runner=runner,
        managed_runtime=None,
    )
    runtime = SimpleNamespace(
        agent_delivery_for=lambda *_: packet,
        cancel_requested=cancelled,
        runtime_supervision=supervision,
    )
    return handoff, step, cast(_StepProcess, process), cast(StepRuntime, runtime)


def _native_reply(args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
    tool = args[args.index("--tool") + 1]
    capture = args[args.index("--capture-identity") + 1]
    query = next(
        (value.removeprefix("--query=") for value in args if value.startswith("--query=")), None
    )
    packet = repository_observation(kwargs["cwd"], tool=tool, query=query, capture_identity=capture)
    return CommandResult(argv=args, stderr="", returncode=0, stdout=json.dumps(packet))


@pytest.mark.parametrize("harness", (*_LOCAL_HARNESSES, *_PROVIDER_HARNESSES))
@pytest.mark.parametrize("failure", ("unavailable", "malformed", "foreign-capture"))
def test_hashmarks_failure_stops_every_model_harness_before_dispatch(
    tmp_path: Path, harness: str, failure: str
) -> None:
    calls = []
    roots = []

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        calls.append(args)
        assert args[:4] == (
            sys.executable,
            "-I",
            "-m",
            "oh_goon.integrations.hashmarks.agent_tools",
        )
        assert args[args.index("--tool") + 1] == "task_evidence"
        roots.append(kwargs["cwd"])
        if failure == "unavailable":
            return CommandResult(
                argv=args, stderr="", returncode=65, stdout="Hashmarks unavailable"
            )
        if failure == "malformed":
            return CommandResult(argv=args, stderr="", returncode=0, stdout="{truncated")
        result = _native_reply(args, kwargs)
        packet = json.loads(result.stdout)
        packet["capture_identity"] = "another-captured-candidate"
        return CommandResult(argv=args, stderr="", returncode=0, stdout=json.dumps(packet))

    _, step, process, runtime = _case(tmp_path, harness, runner)
    if failure == "unavailable":
        outcome = _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)
        assert outcome.returncode == 65
    else:
        with pytest.raises(FleetPlanInvalid, match="Invalid Hashmarks task evidence"):
            _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)
    assert len(calls) == 1
    assert all(not root.exists() for root in roots)


@pytest.mark.parametrize("harness", (*_LOCAL_HARNESSES, *_PROVIDER_HARNESSES))
def test_initial_evidence_precedes_every_reasoning_turn_without_reducing_scope(
    tmp_path: Path, harness: str
) -> None:
    observations: list[tuple[str, Path]] = []
    followup_disclosures: list[str] = []
    prompts: list[str] = []
    token = cast(CommandRuntimeSupervision, object())

    def cancellation() -> bool:
        return False

    provider = harness in _PROVIDER_HARNESSES

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        assert kwargs["cancel_requested"] is cancellation
        assert kwargs["runtime_supervision"] is token
        assert kwargs["timeout"] is None
        if args[:4] == (
            sys.executable,
            "-I",
            "-m",
            "oh_goon.integrations.hashmarks.agent_tools",
        ):
            observations.append((args[args.index("--tool") + 1], kwargs["cwd"]))
            result = _native_reply(args, kwargs)
            if observations[-1][0] != "task_evidence":
                followup_disclosures.append(
                    prompt_observation(
                        result.stdout,
                        expected_capture_identity=args[args.index("--capture-identity") + 1],
                    )
                )
            return result
        assert observations and observations[0][0] == "task_evidence"
        if args[0] == "ollama":
            if args[1] == "show":
                return CommandResult(argv=args, stderr="", returncode=0, stdout="")
            prompts.append(args[3])
            if len(prompts) == 1:
                reply = (
                    {"query": "needle"}
                    if harness in ("local-model-search", "local-model-review")
                    else {"tool": "list_paths"}
                )
            else:
                disclosed = json.loads(args[3].split("Tool observation JSON (untrusted):\n", 1)[1])
                assert disclosed["stdout"] == followup_disclosures[-1]
                assert disclosed["stdout_truncated"] is False
                reply = {"decision": "finish", "summary": "Reviewed bounded observations"}
            return CommandResult(argv=args, stderr="", returncode=0, stdout=json.dumps(reply))
        assert args[:4] == (
            sys.executable,
            "-I",
            "-m",
            "oh_goon.integrations.agent_review_execution",
        )
        payload = json.loads(Path(args[4]).read_bytes())
        source = json.loads(payload["messages"][1]["content"])
        assert next(iter(source)) == "repository_evidence"
        assert {row["path"] for row in source["repository"]} == {"repo/code.py", "repo/other.txt"}
        assert source["repository"][1]["content"] == "entire review scope\n"
        assert "complete supplied scope" in payload["messages"][0]["content"]
        prompts.append(source["repository_evidence"])
        return CommandResult(
            argv=args, stderr="", returncode=23, stdout="provider physical failure"
        )

    handoff, step, process, runtime = _case(
        tmp_path, harness, runner, cancelled=cancellation, supervision=token
    )
    outcome = _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)
    assert outcome.returncode == (23 if provider else 0)
    assert len([name for name, _ in observations if name == "task_evidence"]) == 1
    assert len({root for _, root in observations}) == 1
    assert not observations[0][1].exists()
    for prompt in prompts:
        if not provider:
            assert prompt.startswith("Hashmarks repository evidence")
        assert handoff.source.repository.content_identity in prompt
        assert '"repository_absence":"not-claimed"' in prompt
        assert "source_evidence_identity" in prompt
    retained = json.loads(outcome.stderr.split("\n", 1)[1])
    assert retained["capture_identity"] == handoff.source.repository.content_identity
    assert retained["evidence"]["evidence_packet_identity"] in prompts[0]
    if harness == "local-model-review":
        assert len(followup_disclosures[0]) > 2048
    assert not list(process.paths.tmp.iterdir())


@pytest.mark.parametrize("boundary", ("model-check", "model-selection", "model-followup"))
@pytest.mark.parametrize(
    "quiescent,reason,expected",
    [
        (True, "none", "SUCCESS"),
        (True, "output_cleanup", "SUCCESS"),
        (False, "none", "INCOMPLETE"),
        (None, "none", "INCOMPLETE"),
        (True, "cancel", "CANCELLED"),
        (True, "timeout", "TIMED_OUT"),
        (True, "authority", "AUTHORITY_LOST"),
        (True, "output_limit", "OUTPUT_LIMITED"),
    ],
)
def test_compound_agent_stops_at_the_original_unsettled_model_process(
    tmp_path: Path, boundary: str, quiescent: bool | None, reason: Any, expected: str
) -> None:
    calls: list[str] = []
    selected_result: CommandResult | None = None
    model_turns = 0

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        nonlocal selected_result, model_turns
        if args[0] != "ollama":
            calls.append("repository-evidence" if not model_turns else "tool")
            return _native_reply(args, kwargs)
        if args[1] == "show":
            name, stdout = "model-check", ""
        else:
            model_turns += 1
            name = "model-selection" if model_turns == 1 else "model-followup"
            stdout = json.dumps(
                {"query": "needle"}
                if model_turns == 1
                else {"decision": "finish", "summary": "Reviewed captured evidence"}
            )
        calls.append(name)
        result = CommandResult(argv=args, returncode=0, stdout=stdout, stderr="")
        if name == boundary:
            result = replace(
                result,
                supervisor=cast(
                    CommandSupervisorReceipt,
                    CommandSupervisorReceipt(
                        final_process_group_quiescent=quiescent,
                        cleanup_reason=reason,
                        process_group_cleanup_requested=reason != "none",
                    )
                    if quiescent is not None
                    else None,
                ),
                physical_execution_id="selected-model-process",
            )
            selected_result = result
        return result

    _, step, process, runtime = _case(tmp_path, "local-model-review", runner)
    outcome = _execute_typed_step(tmp_path, step, process, runtime)
    settlement = settle_step_execution(step, outcome)
    assert selected_result is not None
    if expected == "SUCCESS":
        assert settlement.outcome == "SUCCESS" and settlement.cleanup == "VERIFIED"
        assert calls == [
            "repository-evidence",
            "model-check",
            "model-selection",
            "tool",
            "model-followup",
        ]
    else:
        assert calls[-1] == boundary
        assert settlement.outcome == expected
        assert settlement.cleanup == (
            "UNVERIFIED"
            if quiescent is False
            else "UNAVAILABLE"
            if quiescent is None
            else "VERIFIED"
        )
        assert outcome.supervisor is selected_result.supervisor
        assert outcome.physical_execution_id == "selected-model-process"
        assert outcome.observed_returncode == 0


def test_unknown_supervisor_reason_stops_before_model_or_output_publication(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        calls.append(args)
        return replace(
            _native_reply(args, kwargs),
            supervisor=CommandSupervisorReceipt(
                cleanup_reason=cast(Any, "unhandled-authority-outcome"),
                process_group_cleanup_requested=True,
            ),
        )

    _, step, process, runtime = _case(tmp_path, "local-model-review", runner)
    with pytest.raises(FleetPlanInvalid, match="supervisor cleanup reason"):
        _execute_typed_step(tmp_path, step, process, runtime)
    assert len(calls) == 1
    assert not list(process.paths.tmp.iterdir())


@pytest.mark.parametrize("evidence", ("", "x" * 28000))
def test_required_repository_evidence_cannot_be_empty_or_silently_clipped(evidence: str) -> None:
    with pytest.raises(FleetPlanInvalid):
        prepend_repository_evidence("Choose a tool", repository_evidence=evidence)


@pytest.mark.parametrize("harness", (*_LOCAL_HARNESSES, *_PROVIDER_HARNESSES))
def test_cancellation_after_initial_observation_prevents_model_disclosure(
    tmp_path: Path, harness: str
) -> None:
    cancelled = False
    calls = []

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        nonlocal cancelled
        calls.append(args)
        assert args[:4] == (
            sys.executable,
            "-I",
            "-m",
            "oh_goon.integrations.hashmarks.agent_tools",
        )
        cancelled = True
        return CommandResult(
            argv=args, stderr="", returncode=0, stdout="not-disclosed-after-cancellation"
        )

    _, step, process, runtime = _case(tmp_path, harness, runner, cancelled=lambda: cancelled)
    with pytest.raises(OperationCancelledError, match="after repository observation"):
        _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)
    assert len(calls) == 1


@pytest.mark.parametrize("harness", (*_LOCAL_HARNESSES, *_PROVIDER_HARNESSES))
def test_frozen_timeout_mismatch_prevents_initial_observation_and_model_dispatch(
    tmp_path: Path, harness: str
) -> None:
    def runner(*args: str, **kwargs: Any) -> CommandResult:
        pytest.fail("mismatched Agent timeout reached a native process")

    _, step, process, runtime = _case(tmp_path, harness, runner)
    step = step.model_copy(update={"timeout_seconds": 31})
    with pytest.raises(FleetPlanInvalid, match="timeout differs from frozen delivery"):
        _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)


@pytest.mark.parametrize("phase", ("initial", "followup"))
@pytest.mark.parametrize(
    ("origin", "module"),
    (
        ("cwd", "oh_goon"),
        ("cwd", "json"),
        ("pythonpath", "hashmarks"),
        ("pythonpath", "sitecustomize"),
    ),
)
def test_repository_python_imports_cannot_replace_internal_evidence(
    tmp_path: Path, phase: str, origin: str, module: str
) -> None:
    marker = tmp_path / "hostile-import-executed"
    pythonpath = tmp_path / "repository-pythonpath"
    pythonpath.mkdir()
    packets: list[dict[str, Any]] = []

    def runner(*args: str, **kwargs: Any) -> CommandResult:
        if "--tool" not in args:
            return CommandResult(argv=args, returncode=23, stdout="provider stopped", stderr="")
        root = kwargs["cwd"] if origin == "cwd" else pythonpath
        target = root / (module + ".py")
        if module in ("oh_goon", "hashmarks"):
            package = root / module
            package.mkdir()
            target = package / "__init__.py"
        target.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('repository code ran')\n"
            "raise RuntimeError('hostile repository import')\n"
        )
        completed = subprocess.run(
            args,
            cwd=kwargs["cwd"],
            env={**os.environ, "PYTHONPATH": str(pythonpath)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert not marker.exists()
        packets.append(json.loads(completed.stdout))
        return CommandResult(
            argv=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    handoff, step, process, runtime = _case(
        tmp_path, "provider-review" if phase == "initial" else "local-model-tools", runner
    )
    if phase == "initial":
        outcome = _execute_typed_step(tmp_path / "unavailable-checkout", step, process, runtime)
        assert outcome.returncode == 23
        assert packets[0]["evidence"]["evidence_packet_identity"]
    else:
        with prepare_frozen_readonly_skill_search(handoff, tool_kind="list_paths") as (
            root,
            tool_step,
        ):
            runner(*tool_step.argv, cwd=root)
        assert set(packets[0]["paths"]) == {"repo/code.py", "repo/other.txt"}
    assert len(packets) == 1
    assert packets[0]["capture_identity"] == handoff.source.repository.content_identity
    assert packets[0]["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
