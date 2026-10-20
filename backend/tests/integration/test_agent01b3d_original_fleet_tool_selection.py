"""AGENT-01B3D: two read-only tool choices, one original Fleet Step/attempt."""

from __future__ import annotations

import hashlib
import json

import pytest

from oh_goon.core.agent_model_selection import (
    LocalModelToolAction,
    frozen_local_model_tool_observation_prompt,
    parse_local_model_tool_action,
    parse_local_model_tool_review_decision,
)
from oh_goon.core.errors import FleetPlanInvalid
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import (
    _review_case,
    _run,
)


def _tools_case(tmp_path, monkeypatch, *, model_replies, exits=None, sleep_review=False):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=model_replies,
        exits=exits,
        sleep_review=sleep_review,
    )
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-review", "local-model-tools"))
    return plan, repo, opts


def test_listing_files_then_model_finish_uses_original_fleet_and_tape(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as execution

    original = execution._run_local_model_review
    output = {}

    def capture(**kwargs):
        result = original(**kwargs)
        output["stdout"] = result.stdout
        return result

    monkeypatch.setattr(execution, "_run_local_model_review", capture)
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"finish","summary":"The frozen repository contains code.txt"}',
        ],
    )
    frozen, handoff = _lock(plan, opts)
    assert frozen.steps[0].harness == handoff.harness == "local-model-tools"
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-list")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"
    prompt = (tmp_path / "prompt-2.txt").read_text()
    assert '"tool": "list_paths"' in prompt
    assert "repo/code.txt" in prompt
    assert "UNTRUSTED DATA" in prompt
    assert "repo/code.txt" not in (tmp_path / "prompt-1.txt").read_text()
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.stdout_evidence.sha256 == hashlib.sha256(output["stdout"].encode()).hexdigest()
    assert "Model evaluation: The frozen repository" in output["stdout"]


def test_list_paths_then_one_search_and_final_evaluation(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"tool","tool":"search_text","query":"needle"}',
            '{"decision":"finish","summary":"The needle appears in the captured source"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-two-tools")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"
    assert "repo/code.txt" in (tmp_path / "prompt-2.txt").read_text()
    assert "needle from real original Fleet" in (tmp_path / "prompt-3.txt").read_text()


def test_search_first_then_list_paths_works_and_is_limited(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"search_text","query":"not-found"}',
            '{"decision":"tool","tool":"list_paths"}',
            '{"decision":"finish","summary":"Listed captured files after the no-match"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-no-match")
    assert detail.game_tape.status.value == "PASSED"
    observation = json.loads(
        (tmp_path / "prompt-2.txt").read_text().split("Tool observation JSON (untrusted):\n")[1]
    )
    assert "matched" not in observation
    assert json.loads(observation["stdout"])["repository_absence"] == "not-claimed"
    assert '"tool": "list_paths"' in (tmp_path / "prompt-3.txt").read_text()


@pytest.mark.parametrize(
    "initial",
    [
        '{"tool":"run","command":"ls"}',
        '{"tool":"list_paths","path":"/etc"}',
        '{"tool":"list_paths","query":"*.py"}',
        '{"tool":"search_text"}',
        '{"tool":"search_text","query":"a\\nb"}',
        '{"tool":"list_paths","tool":"search_text"}',
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"tool":"search_text","query":""}',
    ],
)
def test_invalid_first_action_does_not_launch_tool_or_pass(tmp_path, monkeypatch, initial):
    plan, repo, opts = _tools_case(tmp_path, monkeypatch, model_replies=[initial])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-invalid")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "1"


@pytest.mark.parametrize(
    "review",
    [
        '{"decision":"tool","tool":"run","command":"cat"}',
        '{"decision":"tool","tool":"list_paths","path":"../"}',
        '{"decision":"finish","summary":"ok","shell":"sh"}',
        '{"decision":"tool","tool":"search_text","query":"needle","network":true}',
        '{"decision":"tool","tool":"list_paths","decision":"finish"}',
    ],
)
def test_model_review_cannot_escalate_tool_authority(tmp_path, monkeypatch, review):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            review,
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-reject-review")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_repeated_listing_refused_without_second_tool(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"tool","tool":"list_paths"}',
        ],
    )
    _lock(plan, opts)
    assert (
        _run(plan, repo, opts, fleet_run_id="agent01b3d-repeat").game_tape.status.value != "PASSED"
    )
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_third_tool_cannot_run(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"tool","tool":"search_text","query":"needle"}',
            '{"decision":"tool","tool":"search_text","query":"something"}',
        ],
    )
    _lock(plan, opts)
    assert (
        _run(plan, repo, opts, fleet_run_id="agent01b3d-third").game_tape.status.value != "PASSED"
    )
    assert (tmp_path / "calls").read_text().strip() == "3"


def test_failed_first_model_does_not_execute_tool(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
        ],
        exits={1: 4},
    )
    _lock(plan, opts)
    assert (
        _run(plan, repo, opts, fleet_run_id="agent01b3d-model-failed").game_tape.status.value
        != "PASSED"
    )
    assert (tmp_path / "calls").read_text().strip() == "1"


def test_tool_protocol_is_bounded_and_local_recipient_only():
    assert parse_local_model_tool_action('{"tool":"list_paths"}') == LocalModelToolAction(
        tool="list_paths"
    )
    assert parse_local_model_tool_review_decision(
        '{"decision":"tool","tool":"search_text","query":"abc"}',
        can_continue=True,
    ).action == LocalModelToolAction(tool="search_text", query="abc")
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_tool_review_decision(
            '{"decision":"tool","tool":"list_paths"}',
            can_continue=False,
        )
    prompt = frozen_local_model_tool_observation_prompt(
        task="Review",
        skill_bytes=b"# Frozen",
        recipient="ollama_local",
        action=LocalModelToolAction(tool="list_paths"),
        returncode=0,
        stdout="x" * 16_000,
        stderr="y" * 3_000,
        can_search=False,
    )
    assert "x" * 16_000 in prompt
    assert '"stdout_truncated": false' in prompt
    assert len(prompt) <= 28_000
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_tool_observation_prompt(
            task="Review",
            skill_bytes=b"# Frozen",
            recipient="remote",
            action=LocalModelToolAction(tool="list_paths"),
            returncode=0,
            stdout="private",
            stderr="",
            can_search=True,
        )


def test_original_step_cancellation_during_tool_review(tmp_path, monkeypatch):
    import time

    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.product_identity import repository_id

    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"finish","summary":"ignored"}',
        ],
        sleep_review=True,
    )
    _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repo)},
            fleet_run_id="agent01b3d-cancel",
            **opts,
        )
        for _ in range(300):
            if (tmp_path / "review-started").exists():
                break
            time.sleep(0.03)
        assert (tmp_path / "review-started").exists()
        runtime.runs.cancel(started.fleet_run_id)
        ended = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert ended.game_tape.status.value != "PASSED"
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value != "PASSED"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_physical_file_listing_failure_cannot_be_relabelled_success(tmp_path, monkeypatch):
    from dataclasses import replace

    import oh_goon.integrations.fleet_plan_step_execution as execution

    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"finish","summary":"ignore physical failure"}',
        ],
    )
    _lock(plan, opts)
    original = execution._execute_command_step

    def failing_tool(repository, step, process, runtime):
        result = original(repository, step, process, runtime)
        if "--tool" in step.argv and step.argv[step.argv.index("--tool") + 1] == "list_paths":
            return replace(result, returncode=2, stderr="Hashmarks repository discovery failed")
        return result

    monkeypatch.setattr(execution, "_execute_command_step", failing_tool)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3d-list-failed")
    assert detail.game_tape.status.value != "PASSED"
    assert detail.operations[0].evidence.plan.steps[0].returncode == 2
    assert (tmp_path / "calls").read_text().strip() == "1"


def test_tool_harness_is_bound_into_original_handoff_and_lock(tmp_path, monkeypatch):
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    plan, _, opts = _tools_case(tmp_path, monkeypatch, model_replies=['{"tool":"list_paths"}'])
    before, first = _lock(plan, opts)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-tools", "local-model-review"))
    after, second, _ = FleetPlanResolver(
        goon_templates=GoonTemplateStore()
    ).resolve_frozen_local_search_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert before.plan_id != after.plan_id
    assert first.handoff_identity != second.handoff_identity
    assert before.steps[0].delivery_sha256 != after.steps[0].delivery_sha256
    # Stale original goon.lock must be refused, not silently recompiled.
    with pytest.raises(FleetPlanInvalid, match="stale"):
        _run(
            plan,
            tmp_path / "repositories" / "team" / "app",
            opts,
            fleet_run_id="agent01b3d-stale-lock",
        )


def test_model_review_process_failure_cannot_publish_pass(tmp_path, monkeypatch):
    plan, repo, opts = _tools_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"finish","summary":"misleading"}',
        ],
        exits={2: 5},
    )
    _lock(plan, opts)
    assert (
        _run(plan, repo, opts, fleet_run_id="agent01b3d-review-failed").game_tape.status.value
        != "PASSED"
    )
    assert (tmp_path / "calls").read_text().strip() == "2"
