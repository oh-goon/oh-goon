"""AGENT-01B3L: failed physical tests enter a later read-only model Step as DATA."""
from __future__ import annotations

import os

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep
from oh_goon.integrations._fleet_plan_step_dataflow import bind_step_outputs
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
from tests.integration.test_agent01b3g_bounded_skill_loop import _loop_case


def _feedback_case(tmp_path, monkeypatch, *, note='FAILURE_SENTINEL: expected 8 got 9',
                   bound_bytes=2048, have_diagnostic=True, harness='local-model-skill-loop',
                   selected_feedback='${{ steps.test.outputs.diagnostic }}'):
    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Investigate the failing physical test"}',
    ])
    bin_dir = tmp_path / 'bin'
    native = bin_dir / 'failing-probe'
    native.write_text('#!/bin/sh\nprintf "%s\\n" "' + note + '" > fail.txt\nexit 13\n')
    native.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + ':' + os.environ['PATH'])
    goon = tmp_path / 'goons' / 'agent' / 'search' / 'goon.yml'
    content = goon.read_text().replace('steps:\n', f'''steps:
  - id: test
    run: "failing-probe"
    on_failure: continue
    outputs:
      - {{name: diagnostic, path: fail.txt, max_bytes: {bound_bytes}}}
''', 1)
    content = content.replace('harness: local-model-skill-loop', 'harness: ' + harness)
    content = content.replace('    requested_effects:',
                              '    feedback: "' + selected_feedback + '"\n'
                              '    when: {kind: step_status, step_id: test, statuses: [FAILED]}\n'
                              '    requested_effects:', 1)
    if not have_diagnostic:
        native.write_text('#!/bin/sh\nexit 13\n')
    goon.write_text(content)
    return plan, repo, opts, native


def test_failed_test_diagnostic_reaches_only_selected_readonly_agent(tmp_path, monkeypatch):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    assert len(frozen.steps) == 2
    agent = frozen.steps[1]
    assert agent.feedback_reference == '${{ steps.agent-search.test.outputs.diagnostic }}'
    assert agent.feedback_value is None
    assert 'FAILURE_SENTINEL' not in handoff.task
    result = _run(plan, repo, opts, fleet_run_id='agent01b3l-failure-review')
    steps = result.operations[0].evidence.plan.steps
    assert [(s.status, s.returncode if hasattr(s, 'returncode') else None) for s in steps] == [
        ('FAILED',13), ('COMPLETED',0),
    ]
    for path in ('prompt-1.txt','prompt-2.txt'):
        prompt = (tmp_path/path).read_text()
        assert 'FAILURE_SENTINEL: expected 8 got 9' in prompt
        assert 'UNTRUSTED DATA' in prompt
        assert 'Operator task:' in prompt
        assert 'failed_test_output' in prompt
    assert result.game_tape.status.value != 'PASSED'
    from oh_goon.application import ApplicationRuntime
    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        detail = reopened.runs.get('agent01b3l-failure-review')
        assert detail.operations[0].evidence.plan.steps[0].returncode == 13
        assert detail.operations[0].evidence.plan.steps[1].returncode == 0
    finally:
        reopened.shutdown(wait_for_runs=True)


def test_absent_failed_test_output_never_invokes_model(tmp_path, monkeypatch):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch, have_diagnostic=False)
    _lock(plan, opts)
    result = _run(plan, repo, opts, fleet_run_id='agent01b3l-missing')
    assert result.game_tape.status.value != 'PASSED'
    assert not (tmp_path/'calls').exists()


def test_oversized_bound_diagnostic_rejected_before_model_launch(tmp_path, monkeypatch):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch, note='Z'*2060, bound_bytes=4096)
    _lock(plan, opts)
    result = _run(plan, repo, opts, fleet_run_id='agent01b3l-overlong')
    assert result.game_tape.status.value != 'PASSED'
    assert not (tmp_path/'calls').exists()


@pytest.mark.parametrize('bad_reference',[ 
    'not-a-reference', '${{ steps.future.outputs.diagnostic }}',
    '${{ steps.test.outputs.undeclared }}',
    '${{ steps.test.outputs.diagnostic }} and extra',
])
def test_invalid_or_future_feedback_ref_refused_during_resolution(tmp_path, monkeypatch, bad_reference):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch, selected_feedback=bad_reference)
    with pytest.raises((FleetPlanInvalid, ValueError)):
        _lock(plan, opts)
    assert not (tmp_path/'calls').exists()


def test_older_harness_cannot_gain_feedback_input(tmp_path, monkeypatch):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch, harness='local-model-proposal')
    with pytest.raises((FleetPlanInvalid, ValueError)):
        _lock(plan, opts)
    assert not (tmp_path/'calls').exists()


def test_unbound_feedback_cannot_become_an_agent_model_turn(tmp_path, monkeypatch):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch)
    frozen, _ = _lock(plan, opts)
    agent = frozen.steps[1]
    assert isinstance(agent, ResolvedLocalSearchAgentStep)
    with pytest.raises(FleetPlanInvalid, match='unavailable output'):
        bind_step_outputs(agent, {})
    assert bind_step_outputs(agent, {agent.feedback_reference.split("steps.",1)[1].split(".outputs",1)[0]: {"diagnostic": "FAILURE_SENTINEL"}}).feedback_value == 'FAILURE_SENTINEL'


def test_successful_test_skips_agent_without_model_disclosure(tmp_path, monkeypatch):
    plan, repo, opts, native = _feedback_case(tmp_path, monkeypatch)
    native.write_text('#!/bin/sh\nprintf "%s\\n" "test passed" > fail.txt\nexit 0\n')
    _lock(plan, opts)
    result = _run(plan, repo, opts, fleet_run_id='agent01b3l-passing-test')
    statuses = [s.status for s in result.operations[0].evidence.plan.steps]
    assert statuses == ['COMPLETED', 'SKIPPED']
    assert not (tmp_path/'calls').exists()


@pytest.mark.parametrize('wrong_condition', [
    'when: {kind: step_status, step_id: test, statuses: [COMPLETED]}',
    'when: {kind: step_status, step_id: test, statuses: [FAILED, COMPLETED]}',
    'when: {kind: step_status, step_id: another, statuses: [FAILED]}',
])
def test_feedback_requires_matching_failed_only_condition(tmp_path, monkeypatch, wrong_condition):
    plan, repo, opts, _ = _feedback_case(tmp_path, monkeypatch)
    goon = tmp_path / 'goons' / 'agent' / 'search' / 'goon.yml'
    goon.write_text(goon.read_text().replace(
        'when: {kind: step_status, step_id: test, statuses: [FAILED]}', wrong_condition))
    with pytest.raises((FleetPlanInvalid, ValueError)):
        _lock(plan, opts)
