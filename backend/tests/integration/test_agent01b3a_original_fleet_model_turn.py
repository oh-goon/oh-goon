"""AGENT-01B3A: one model-selected search via the original admitted Fleet Step."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.agent_model_selection import parse_one_local_model_query
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.test_agent01b2b_original_fleet import _case, _lock

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("rg") is None, reason="Linux/WSL ripgrep required"
)


def _case_model(
    tmp_path: Path,
    monkeypatch,
    *,
    reply='{"query":"needle"}',
    selected=True,
    show_status=0,
    task="Select one useful search for a code review",
):
    plan, repository, options = _case(tmp_path, monkeypatch, task=task)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(
        goon.read_text().replace("harness: local-search", "harness: local-model-search")
        + (
            """    requested_effects:
      recipients: [ollama_local]
      model: qwen3:0.6b
"""
            if selected
            else ""
        )
    )
    cli = tmp_path / "bin" / "ollama"
    cli.parent.mkdir()
    # Real supervised POSIX process for both model preparation and model invocation.
    # The fixture simulates the model's response, not a live neural inference.
    cli.write_text(f'''#!/bin/sh
printf '%s\\n' "$1" >> "{tmp_path / "model-calls.log"}"
printf '%s\\n' "$OLLAMA_HOST" >> "{tmp_path / "model-hosts.log"}"
if [ "$1" = show ]; then exit {show_status}; fi
if [ "$1" = run ]; then
  printf '%s' "$3" > "{tmp_path / "model-prompt.txt"}"
  printf '%s\\n' '{reply}'
  exit 0
fi
exit 3
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(cli.parent) + ":" + os.environ["PATH"])
    monkeypatch.setenv("OLLAMA_HOST", "https://unrequested-model.example.invalid")
    return plan, repository, options


def test_bounded_model_selected_search_is_original_fleet_success_and_durable(tmp_path, monkeypatch):
    plan, repository, opts = _case_model(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    step = frozen.steps[0]
    assert step.kind == "agent" and step.workload.harness == "local-model-search"
    assert step.workload.model == "qwen3:0.6b"
    assert handoff.requested_effects.recipients == ("ollama_local",)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="agent01b3a-model",
            **opts,
        )
        finished = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert finished.game_tape.status.value == "PASSED"
        assert finished.outcome.value == "SUCCESS"
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value == "PASSED"
        assert (tmp_path / "model-calls.log").read_text().splitlines() == ["show", "run"]
        assert (tmp_path / "model-hosts.log").read_text().splitlines() == [
            "http://127.0.0.1:11434",
            "http://127.0.0.1:11434",
        ]
        prompt = (tmp_path / "model-prompt.txt").read_text()
        assert "Select one useful search" in prompt
        assert "Offline read-only skill" in prompt
        assert prompt.startswith("Hashmarks repository evidence")
        assert "repository_absence" in prompt
        assert "capture_identity" in prompt
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize(
    "reply",
    [
        "not JSON",
        '{"command":"rm -rf repo"}',
        '{"query":"needle","shell":"sh"}',
        '{"query":"needle","query":"other"}',
        '{"query":"bad\\nvalue"}',
        '{"query":""}',
        '{"query":true}',
        '["needle"]',
    ],
)
def test_untrusted_model_action_cannot_escape_one_literal_search(reply):
    with pytest.raises(FleetPlanInvalid):
        parse_one_local_model_query(reply)


@pytest.mark.parametrize(
    "reply,show_status",
    [
        ('{"shell":"false"}', 0),
        ('<think>untrusted thought</think>\n{"query":"needle"}', 0),
        ('{"query":"wrapped\u001b[5D\u001b[K\nquery"}', 0),
        ('{"query":"needle"}', 1),
    ],
)
def test_bad_or_unavailable_model_does_not_dispatch_search_or_pass_game_tape(
    tmp_path,
    monkeypatch,
    reply,
    show_status,
):
    plan, repository, opts = _case_model(
        tmp_path, monkeypatch, reply=reply, show_status=show_status
    )
    _lock(plan, opts)
    import oh_goon.integrations.fleet_plan_step_execution as steps

    original = steps._execute_command_step
    initial_observations = []

    def reject_tool(repository, step, process, runtime):
        if "--tool" in step.argv and step.argv[step.argv.index("--tool") + 1] == "task_evidence":
            initial_observations.append(step.argv)
            return original(repository, step, process, runtime)
        pytest.fail("bad model launched a follow-up repository tool")

    monkeypatch.setattr(steps, "_execute_command_step", reject_tool)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="agent01b3a-model-rejected",
            **opts,
        )
        ended = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert ended.game_tape.status.value != "PASSED"
        assert len(initial_observations) == 1
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value != "PASSED"
        assert (tmp_path / "model-calls.log").read_text().splitlines() == (
            ["show"] if show_status else ["show", "run"]
        )
    finally:
        runtime.shutdown(wait_for_runs=True)
        monkeypatch.setattr(steps, "_execute_command_step", original)


def test_recipient_missing_and_model_malformed_refuse_before_launch(tmp_path, monkeypatch):
    plan, repo, opts = _case_model(tmp_path, monkeypatch, selected=False)
    with pytest.raises(FleetPlanInvalid):
        _lock(plan, opts)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(
        goon.read_text()
        + """    requested_effects:
      recipients: [external_model]
      model: qwen3:0.6b
"""
    )
    with pytest.raises(FleetPlanInvalid):
        _lock(plan, opts)


def test_model_identity_changes_lock_and_packet(tmp_path, monkeypatch):
    plan, _, opts = _case_model(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("qwen3:0.6b", "qwen3:1.7b"))
    changed, changed_handoff, _ = FleetPlanResolver(
        goon_templates=GoonTemplateStore()
    ).resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert changed_handoff.handoff_identity != handoff.handoff_identity
    assert changed.steps[0].delivery_sha256 != frozen.steps[0].delivery_sha256
    assert changed.plan_id != frozen.plan_id


def test_model_selected_no_match_is_not_false_failure(tmp_path, monkeypatch):
    plan, repository, opts = _case_model(tmp_path, monkeypatch, reply='{"query":"not-in-source"}')
    _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="agent01b3a-no-match",
            **opts,
        )
        persisted = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert persisted.game_tape.status.value == "PASSED"
        detail = runtime.runs.get(started.fleet_run_id)
        assert detail.operations[0].evidence.plan.steps[0].returncode == 0
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_model_process_cancellation_reaches_original_supervisor(tmp_path, monkeypatch):
    import time

    plan, repository, opts = _case_model(tmp_path, monkeypatch)
    _lock(plan, opts)
    cli = tmp_path / "bin" / "ollama"
    cli.write_text(f'''#!/bin/sh
if [ "$1" = show ]; then exit 0; fi
if [ "$1" = run ]; then
  touch "{tmp_path / "model-started"}"
  sleep 20
  printf '%s\\n' '{{"query":"needle"}}'
  exit 0
fi
exit 3
''')
    cli.chmod(0o755)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="agent01b3a-cancel",
            **opts,
        )
        for _ in range(300):
            if (tmp_path / "model-started").exists():
                break
            time.sleep(0.03)
        assert (tmp_path / "model-started").exists(), "supervised model process did not launch"
        runtime.runs.cancel(started.fleet_run_id)
        ended = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert ended.game_tape.status.value != "PASSED"
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value != "PASSED"
    finally:
        runtime.shutdown(wait_for_runs=True)
