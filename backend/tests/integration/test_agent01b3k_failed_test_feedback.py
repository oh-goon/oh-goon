"""AGENT-01B3K: original Fleet transports bounded failed-test diagnostics truthfully."""
from __future__ import annotations

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, StepFailurePolicy
from oh_goon.integrations._fleet_plan_step_retry import _successful_direct_publication
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations._fleet_plan_step_dataflow import bind_step_outputs
from oh_goon.core.models.fleet_plan_step_primitives import PlanPublishedOutput, StepRetryPolicy
from tests.integration.test_agent01b2b_original_fleet import _case, _lock


def _failed_step():
    return ResolvedCommandStep(
        step_id='test', argv=('probe-fail',), on_failure=StepFailurePolicy.CONTINUE,
        outputs=(PlanPublishedOutput(name='diagnostic',path='diagnostic.txt',max_bytes=128),),
    )


def test_failed_command_can_publish_explicit_bounded_diagnostics_without_success(tmp_path):
    (tmp_path/'diagnostic.txt').write_text('failure: needle\n')
    step = _failed_step()
    publication = _successful_direct_publication(step, StepOutcome(argv=('probe-fail',), returncode=13), tmp_path)
    assert publication.outcome.returncode == 13
    assert publication.outputs == {'diagnostic':'failure: needle'}
    assert publication.output_evidence[0].byte_count == len('failure: needle')
    bound = bind_step_outputs(ResolvedCommandStep(
        step_id='consume', argv=('echo','${{ steps.test.outputs.diagnostic }}'),
    ), {'test': publication.outputs})
    assert bound.argv == ('echo', 'failure: needle')


def test_failed_tool_without_explicit_continue_does_not_publish(tmp_path):
    (tmp_path/'diagnostic.txt').write_text('failure: needle\n')
    step = _failed_step()
    for denied in (
        step.model_copy(update={'on_failure':StepFailurePolicy.STOP}),
        step.model_copy(update={'retry':StepRetryPolicy(max_attempts=2, known_transient_exit_codes=(13,),reason='transient',delays_seconds=(0,))}),
    ):
        assert _successful_direct_publication(denied, StepOutcome(argv=('probe-fail',), returncode=13), tmp_path).outputs == {}


def test_missing_or_oversized_failure_diagnostics_never_publish(tmp_path):
    step=_failed_step()
    missing=_successful_direct_publication(step, StepOutcome(argv=('probe-fail',), returncode=13),tmp_path)
    assert missing.outputs == {} and missing.outcome.returncode != 0
    (tmp_path/'diagnostic.txt').write_text('x'*129)
    over=_successful_direct_publication(step, StepOutcome(argv=('probe-fail',), returncode=13),tmp_path)
    assert over.outputs == {} and over.outcome.returncode != 0


def test_original_fleet_failed_test_feedback_into_conditional_followup(tmp_path,monkeypatch):
    plan, repo, opts=_case(tmp_path,monkeypatch)
    bin_dir=tmp_path/'bin'
    bin_dir.mkdir(exist_ok=True)
    native=bin_dir/'probe-fail'
    native.write_text('#!/bin/sh\nprintf "%s\\n" "needle" > diagnostic.txt\nexit 13\n')
    native.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + ':' + __import__('os').environ.get('PATH',''))
    goon=tmp_path/'goons'/'agent'/'search'/'goon.yml'
    goon.write_text(goon.read_text()+'''  - id: test
    run: "probe-fail"
    on_failure: continue
    outputs:
      - {name: diagnostic, path: diagnostic.txt, max_bytes: 128}
  - id: review
    run: "rg --fixed-strings ${{ steps.test.outputs.diagnostic }} code.txt"
    when: {kind: step_status, step_id: test, statuses: [FAILED]}
  - id: success-only
    run: "rg --fixed-strings missing-from-source code.txt"
    when: {kind: step_status, step_id: test, statuses: [COMPLETED]}
''')
    frozen,_=_lock(plan,opts)
    assert len(frozen.steps)==4
    from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
    result=_run(plan,repo,opts,fleet_run_id='agent01b3k-test-feedback')
    steps=result.operations[0].evidence.plan.steps
    assert [(s.status,s.returncode if hasattr(s,'returncode') else None) for s in steps] == [
        ('COMPLETED',0), ('FAILED',13), ('COMPLETED',0), ('SKIPPED',None),
    ]
    assert steps[1].outputs[0].name=='diagnostic'
    assert result.game_tape.status.value!='PASSED'  # failed physical test never laundered
    reopened=ApplicationRuntime.build(standard_local=True)
    try:
        persisted=reopened.runs.get('agent01b3k-test-feedback')
        assert persisted.operations[0].evidence.plan.steps[1].returncode==13
        assert persisted.operations[0].evidence.plan.steps[2].status=='COMPLETED'
    finally:
        reopened.shutdown(wait_for_runs=True)
