"""AGENT-01B3F: Skill resource inspection through the ORIGINAL admitted Fleet Step."""

from __future__ import annotations

import hashlib

import pytest

from oh_goon.core.agent_model_selection import parse_local_model_tool_action
from oh_goon.core.errors import FleetPlanInvalid
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
from tests.integration.test_agent01b3e_frozen_read_file import _files_case


def _resources_case(tmp_path, monkeypatch, *, model_replies):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=model_replies)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-files", "local-model-skill-resources"))
    reference = tmp_path / "skills" / "team" / "search" / "references" / "checklist.md"
    reference.parent.mkdir()
    reference.write_text("FROZEN_SKILL_RESOURCE_SENTINEL\ncheck only captured source\n")
    return plan, repo, opts, reference


def test_list_skill_files_then_read_resource_in_same_original_fleet_step(tmp_path, monkeypatch):
    plan, repo, opts, reference = _resources_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_skill_paths"}',
            '{"decision":"tool","tool":"read_file","path":"skill/references/checklist.md"}',
            '{"decision":"finish","summary":"Applied the frozen Skill checklist"}',
        ],
    )
    frozen, handoff = _lock(plan, opts)
    assert frozen.steps[0].workload.harness == handoff.harness == "local-model-skill-resources"
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3f-skill-list-read")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"
    assert "skill/references/checklist.md" in (tmp_path / "prompt-2.txt").read_text()
    assert "FROZEN_SKILL_RESOURCE_SENTINEL" not in (tmp_path / "prompt-2.txt").read_text()
    assert "FROZEN_SKILL_RESOURCE_SENTINEL" in (tmp_path / "prompt-3.txt").read_text()
    assert detail.operations[0].evidence.plan.steps[0].returncode == 0


def test_read_skill_resource_directly_without_reopening_repo_or_skill_paths(tmp_path, monkeypatch):
    plan, repo, opts, _ = _resources_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"skill/references/checklist.md"}',
            '{"decision":"finish","summary":"Read frozen instructions"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3f-skill-read")
    assert detail.game_tape.status.value == "PASSED"
    assert "FROZEN_SKILL_RESOURCE_SENTINEL" in (tmp_path / "prompt-2.txt").read_text()
    assert "FROZEN_SKILL_RESOURCE_SENTINEL" not in (tmp_path / "prompt-1.txt").read_text()
    assert (tmp_path / "calls").read_text().strip() == "2"


@pytest.mark.parametrize(
    "action",
    [
        '{"tool":"read_file","path":"skill/../repo/code.txt"}',
        '{"tool":"read_file","path":"skill/missing.md"}',
        '{"tool":"read_file","path":"skill//SKILL.md"}',
        '{"tool":"read_file","path":"/etc/passwd"}',
        '{"tool":"read_file","path":"skill/references/checklist.md","shell":"sh"}',
        '{"tool":"list_skill_paths","path":"repo"}',
        '{"tool":"run","command":"cat skill/SKILL.md"}',
    ],
)
def test_untrusted_skill_resource_request_refused_without_tool_launch(
    tmp_path, monkeypatch, action
):
    plan, repo, opts, _ = _resources_case(tmp_path, monkeypatch, model_replies=[action])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3f-denied")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "1"


def test_older_harness_does_not_receive_resource_tools_or_skill_file_paths():
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_tool_action('{"tool":"list_skill_paths"}', allow_file=True)
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_tool_action(
            '{"tool":"read_file","path":"skill/SKILL.md"}', allow_file=True
        )
    assert (
        parse_local_model_tool_action(
            '{"tool":"read_file","path":"skill/SKILL.md"}',
            allow_file=True,
            allow_skill_file=True,
        ).path
        == "skill/SKILL.md"
    )


def test_skill_source_change_changes_frozen_lock_identity(tmp_path, monkeypatch):
    plan, _repo, opts, resource = _resources_case(
        tmp_path, monkeypatch, model_replies=['{"tool":"list_skill_paths"}']
    )
    before, handoff = _lock(plan, opts)
    digest_before = hashlib.sha256(handoff.encode_prework_delivery()).hexdigest()
    resource.write_text("Changed resource beyond frozen lock\n")
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    later, changed, _ = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert changed.handoff_identity != handoff.handoff_identity
    assert hashlib.sha256(changed.encode_prework_delivery()).hexdigest() != digest_before
    assert later.plan_id != before.plan_id
