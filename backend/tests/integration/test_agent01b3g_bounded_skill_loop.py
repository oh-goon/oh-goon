"""AGENT-01B3G: opt-in four-action Skill review under the original Fleet Step."""
from __future__ import annotations

import hashlib

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
from tests.integration.test_agent01b3f_frozen_skill_resources import _resources_case


def _loop_case(tmp_path, monkeypatch, *, model_replies, **kwargs):
    plan, repo, opts, resource = _resources_case(
        tmp_path, monkeypatch, model_replies=model_replies, **kwargs,
    )
    goon = tmp_path / 'goons' / 'agent' / 'search' / 'goon.yml'
    goon.write_text(goon.read_text().replace('local-model-skill-resources', 'local-model-skill-loop'))
    return plan, repo, opts, resource


def test_four_different_tools_then_final_evaluation_is_persisted(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as execution
    recorded = {}
    original = execution._run_local_model_review

    def record(**kwargs):
        result = original(**kwargs)
        recorded['stdout'] = result.stdout
        return result

    monkeypatch.setattr(execution, '_run_local_model_review', record)
    plan, repo, opts, resource = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_skill_paths"}',
        '{"decision":"tool","tool":"read_file","path":"skill/references/checklist.md"}',
        '{"decision":"tool","tool":"list_paths"}',
        '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Reviewed Skill checklist and repository source"}',
    ])
    frozen, handoff = _lock(plan, opts)
    assert handoff.harness == frozen.steps[0].harness == 'local-model-skill-loop'
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3g-four')
    assert detail.game_tape.status.value == 'PASSED'
    assert (tmp_path / 'calls').read_text().strip() == '5'
    assert 'FROZEN_SKILL_RESOURCE_SENTINEL' in (tmp_path / 'prompt-3.txt').read_text()
    assert 'repo/code.txt' in (tmp_path / 'prompt-4.txt').read_text()
    assert 'needle from real original Fleet' in (tmp_path / 'prompt-5.txt').read_text()
    assert 'Previous tool observations' in (tmp_path / 'prompt-5.txt').read_text()
    assert 'list_skill_paths' in (tmp_path / 'prompt-5.txt').read_text()
    assert 'Model evaluation: Reviewed Skill checklist' in recorded['stdout']
    assert detail.operations[0].evidence.plan.steps[0].stdout_evidence.sha256 == hashlib.sha256(recorded['stdout'].encode()).hexdigest()


def test_fifth_tool_is_denied_without_running_it(tmp_path, monkeypatch):
    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_skill_paths"}',
        '{"decision":"tool","tool":"list_paths"}',
        '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"tool","tool":"read_file","path":"skill/references/checklist.md"}',
        '{"decision":"tool","tool":"search_text","query":"needle"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3g-budget')
    assert detail.game_tape.status.value != 'PASSED'
    assert (tmp_path / 'calls').read_text().strip() == '5'


@pytest.mark.parametrize('forbidden', [
    '{"decision":"tool","tool":"run","command":"echo unauthorized"}',
    '{"decision":"tool","tool":"read_file","path":"/etc/passwd"}',
    '{"decision":"tool","tool":"read_file","path":"skill/../repo/code.txt"}',
    '{"decision":"tool","tool":"read_file","path":"repo/nonexistent.txt"}',
    '{"decision":"tool","tool":"list_paths","shell":"sh"}',
])
def test_illegal_followup_cannot_dispatch(tmp_path, monkeypatch, forbidden):
    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_skill_paths"}', forbidden,
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3g-denied')
    assert detail.game_tape.status.value != 'PASSED'
    assert (tmp_path / 'calls').read_text().strip() == '2'


def test_repeated_tool_refused_without_execution(tmp_path, monkeypatch):
    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_paths"}', '{"decision":"tool","tool":"list_paths"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3g-repeat')
    assert detail.game_tape.status.value != 'PASSED'
    assert (tmp_path / 'calls').read_text().strip() == '2'


def test_stale_skill_capture_fails_before_work(tmp_path, monkeypatch):
    plan, repo, opts, resource = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_skill_paths"}',
    ])
    _, prior = _lock(plan, opts)
    resource.write_text('the instruction changed')
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    _, new, _ = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve_frozen_local_search_agent(
        plan, inputs=opts['inputs'], repository_root=opts['agent_repository_root'],
        skill_root=opts['agent_skill_root'], allowed_acceptance=opts['allowed_agent_acceptance'],
    )
    assert prior.handoff_identity != new.handoff_identity


def test_failed_read_file_exit_one_is_not_a_successful_model_observation(tmp_path, monkeypatch):
    """A read is not an rg no-match: its exit 1 means physical failure."""
    from dataclasses import replace
    import oh_goon.integrations.fleet_plan_step_execution as execution

    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Cannot turn failed read into success"}',
    ])
    original = execution._execute_command_step

    def fail_after_real_read(repository, step, process, runtime):
        result = original(repository, step, process, runtime)
        if step.argv[0] == 'head':
            return replace(result, returncode=1, stderr='read_file returned an I/O failure')
        return result

    monkeypatch.setattr(execution, '_execute_command_step', fail_after_real_read)
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3h-failed-read')
    assert detail.game_tape.status.value != 'PASSED'
    assert detail.operations[0].evidence.plan.steps[0].returncode == 125
    # There is a single model-selection process, but no model evaluation after failed read.
    assert (tmp_path / 'calls').read_text().strip() == '1'
