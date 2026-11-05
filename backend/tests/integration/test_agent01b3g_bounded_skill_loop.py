"""AGENT-01B3G: bounded Skill resource and proposal actions under the original Fleet Step."""

from __future__ import annotations

import hashlib

import pytest

from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import (
    _observe_model_stdout,
    _run,
)
from tests.integration.test_agent01b3f_frozen_skill_resources import _resources_case


def _bounded_agent_case(tmp_path, monkeypatch, *, model_replies, proposal=False, **kwargs):
    plan, repo, opts, resource = _resources_case(
        tmp_path,
        monkeypatch,
        model_replies=model_replies,
        **kwargs,
    )
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    if proposal:
        goon.write_text(
            goon.read_text().replace("local-model-skill-resources", "local-model-proposal")
        )
    return plan, repo, opts, resource


def test_four_different_tools_then_final_evaluation_is_persisted(tmp_path, monkeypatch):
    from tests.integration.test_agent01b3i_source_bound_patch_proposal import _payload

    plan, repo, opts, resource = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        proposal=True,
        model_replies=[
            '{"tool":"list_skill_paths"}',
            '{"decision":"tool","tool":"read_file","path":"skill/references/checklist.md"}',
            '{"decision":"tool","tool":"list_paths"}',
            '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    observe_stdout = _observe_model_stdout(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    assert handoff.harness == frozen.steps[0].workload.harness == "local-model-proposal"
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3g-four")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "5"
    assert "FROZEN_SKILL_RESOURCE_SENTINEL" in (tmp_path / "prompt-3.txt").read_text()
    assert "repo/code.txt" in (tmp_path / "prompt-4.txt").read_text()
    assert "needle from real original Fleet" in (tmp_path / "prompt-5.txt").read_text()
    assert "Previous tool observations" in (tmp_path / "prompt-5.txt").read_text()
    assert "list_skill_paths" in (tmp_path / "prompt-5.txt").read_text()
    stdout = observe_stdout(detail)
    assert "Agent proposal only" in stdout
    assert (
        detail.operations[0].evidence.plan.steps[0].stdout_evidence.sha256
        == hashlib.sha256(stdout.encode()).hexdigest()
    )


def test_third_resource_tool_is_denied_without_running_it(tmp_path, monkeypatch):
    plan, repo, opts, _ = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_skill_paths"}',
            '{"decision":"tool","tool":"list_paths"}',
            '{"decision":"tool","tool":"search_text","query":"needle"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3g-budget")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"


@pytest.mark.parametrize(
    "forbidden",
    [
        '{"decision":"tool","tool":"run","command":"echo unauthorized"}',
        '{"decision":"tool","tool":"read_file","path":"/etc/passwd"}',
        '{"decision":"tool","tool":"read_file","path":"skill/../repo/code.txt"}',
        '{"decision":"tool","tool":"read_file","path":"repo/nonexistent.txt"}',
        '{"decision":"tool","tool":"list_paths","shell":"sh"}',
    ],
)
def test_illegal_followup_cannot_dispatch(tmp_path, monkeypatch, forbidden):
    plan, repo, opts, _ = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_skill_paths"}',
            forbidden,
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3g-denied")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_repeated_tool_refused_without_execution(tmp_path, monkeypatch):
    plan, repo, opts, _ = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            '{"decision":"tool","tool":"list_paths"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3g-repeat")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_stale_skill_capture_fails_before_work(tmp_path, monkeypatch):
    plan, repo, opts, resource = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_skill_paths"}',
        ],
    )
    _, prior = _lock(plan, opts)
    resource.write_text("the instruction changed")
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    _, new, _ = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert prior.handoff_identity != new.handoff_identity


def test_failed_read_file_exit_one_is_not_a_successful_model_observation(tmp_path, monkeypatch):
    """A read is not an rg no-match: its exit 1 means physical failure."""
    import json
    import shutil
    import subprocess
    import sys

    from oh_goon.core.models.run_events import ProcessExitedEvent

    plan, repo, opts, _ = _bounded_agent_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            '{"decision":"finish","summary":"Cannot turn failed read into success"}',
        ],
    )
    native_head = shutil.which("head")
    assert native_head is not None
    control = subprocess.run(
        [native_head, "-c", "2048", "--", str(repo / "code.txt")],
        capture_output=True,
        check=False,
    )
    assert control.returncode == 0 and b"needle from real original Fleet" in control.stdout
    marker = tmp_path / "head-call.json"
    head = tmp_path / "bin" / "head"
    head.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:]==['-c','2048','--','repo/code.txt']\n"
        "assert b'needle from real original Fleet' in Path(sys.argv[-1]).read_bytes()\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'argv':sys.argv[1:]}}))\n"
        "raise SystemExit(1)\n"
    )
    head.chmod(0o700)
    frozen, _ = _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3h-failed-read")
    assert detail.game_tape.status.value != "PASSED"
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 125
    assert step.settlement.outcome == "FAILED" and step.settlement.cleanup == "VERIFIED"
    observed = json.loads(marker.read_text())
    assert observed["argv"] == ["-c", "2048", "--", "repo/code.txt"]
    exits = [
        event
        for event in detail.lifecycle_events
        if type(event) is ProcessExitedEvent
        and event.pid == observed["pid"]
        and event.step_id == frozen.steps[0].step_id
        and event.attempt_number == step.attempts[0].attempt
    ]
    assert len(exits) == 1 and exits[0].returncode == 1
    # There is a single model-selection process, but no model evaluation after failed read.
    assert (tmp_path / "calls").read_text().strip() == "1"
