"""AGENT-01B2A: typed Agent delivered into the original, real Step executor.

This tests the Step execution cut, not original Fleet admission or Game Tape.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep, ResolvedFleetPlan
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step, execute_strict_step
from oh_goon.services.fleet_plan_step_resolution import resolve_delivered_local_search_agent_step
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.runtime.goon_templates import GoonTemplateStore

pytestmark = pytest.mark.skipif(shutil.which('rg') is None, reason='native ripgrep unavailable')


def _case(tmp_path: Path, *, task='search:needle', harness='local-search', effects=''):
    template = tmp_path / 'goons' / 'agent' / 'search'
    template.mkdir(parents=True)
    (template / 'README.md').write_text('Frozen local search\n')
    (template / 'goon.yml').write_text('''inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
  - uses: agent
    harness: ''' + harness + '\n    task: "' + task + '"\n' + effects)
    plan = tmp_path / 'plan.yml'
    plan.write_text('inputs: [repo, skill, acceptance]\ngoons: [agent/search]\n')
    repo = tmp_path / 'repos' / 'team' / 'app'
    repo.mkdir(parents=True)
    (repo / 'code.txt').write_text('needle from captured repository\n')
    skill = tmp_path / 'skills' / 'team' / 'search'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('# read only\n')
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / 'goons'))
    _, source = resolver.input_definitions_with_source(plan)
    handoff = resolver.prepare_agent_step_handoff(
        plan, inputs={'repo':'local:team/app','skill':'local:team/search','acceptance':'review'},
        repository_root=tmp_path/'repos', skill_root=tmp_path/'skills',
        allowed_acceptance=('review',), expected_source_sha256=source,
    )
    return handoff, repo, skill


def _runtime(cancel=None, handoff=None):
    # Only the original admitted attempt will provide this delivery port in
    # production. The tests supply verified AG-02 bytes without a new runner.
    return StepRuntime(
        git=None, extensions=None, tools=None, cancel_requested=cancel,
        agent_delivery_for=(lambda step_id, identity: handoff.encode_prework_delivery())
                           if handoff is not None else None,
    )


def test_frozen_typed_agent_uses_original_real_step_supervisor(tmp_path):
    handoff, repo, skill = _case(tmp_path)
    step = resolve_delivered_local_search_agent_step(handoff)
    assert step.kind == 'agent'
    assert step.step_id == handoff.step_id
    assert ResolvedLocalSearchAgentStep.model_validate_json(step.model_dump_json()).kind == 'agent'
    (repo/'code.txt').write_text('not present any more\n')
    (skill/'SKILL.md').write_text('changed live\n')
    result = execute_direct_step(tmp_path, step, _runtime(handoff=handoff), step_order=1)
    assert result.returncode == 0
    assert 'needle from captured repository' in result.stdout
    assert result.supervisor is not None
    assert (repo/'code.txt').read_text() == 'not present any more\n'


def test_no_match_is_admitted_success_code_one_in_typed_step(tmp_path):
    handoff, _, _ = _case(tmp_path, task='search:absent-literal')
    step = resolve_delivered_local_search_agent_step(handoff)
    result = execute_direct_step(tmp_path, step, _runtime(handoff=handoff), step_order=1)
    assert result.returncode == 1
    assert step.success_codes == (0, 1)


def test_cancellation_uses_existing_step_supervisor(tmp_path):
    handoff, _, _ = _case(tmp_path)
    with pytest.raises(OperationCancelledError):
        execute_direct_step(tmp_path, resolve_delivered_local_search_agent_step(handoff),
                            _runtime(lambda: True, handoff), step_order=1)


@pytest.mark.parametrize('broken', ('missing_delivery','digest','identity','step','payload','timeout'))
def test_invalid_delivery_fails_before_real_process(tmp_path, monkeypatch, broken):
    handoff, _, _ = _case(tmp_path)
    step = resolve_delivered_local_search_agent_step(handoff)
    data = step.model_dump(mode='python')
    delivery = handoff.encode_prework_delivery()
    if broken == 'digest':
        data['delivery_sha256'] = '0' * 64
    elif broken == 'identity':
        data['handoff_identity'] = 'wrong-handoff'
    elif broken == 'step':
        data['step_id'] = 'other-step'
    elif broken == 'payload':
        delivery = delivery.replace(b'local-search', b'other-searc', 1)
        import hashlib
        data['delivery_sha256'] = hashlib.sha256(delivery).hexdigest()
    elif broken == 'timeout':
        data['timeout_seconds'] = step.timeout_seconds + 1
    corrupted = ResolvedLocalSearchAgentStep.model_validate(data)
    import oh_goon.integrations.fleet_plan_step_execution as executor
    monkeypatch.setattr(executor, '_execute_command_step', lambda *a, **kw: pytest.fail('process launched'))
    runtime = StepRuntime(git=None, extensions=None, tools=None, cancel_requested=None,
                          agent_delivery_for=(lambda step_id, identity: delivery)
                          if broken != 'missing_delivery' else None)
    with pytest.raises(FleetPlanInvalid):
        execute_direct_step(tmp_path, corrupted, runtime, step_order=1)


def test_agent_shape_cannot_be_relabelled_into_arbitrary_command(tmp_path):
    handoff, _, _ = _case(tmp_path)
    step = resolve_delivered_local_search_agent_step(handoff)
    for mutation in ({'argv': ('bash','-c','touch escaped')},
                     {'success_codes': (0,)},
                     {'working_directory': '..'}):
        with pytest.raises(ValidationError):
            ResolvedLocalSearchAgentStep.model_validate({**step.model_dump(), **mutation})


def test_original_strict_backend_refuses_unqualified_agent_launch(tmp_path):
    handoff, _, _ = _case(tmp_path)
    step = resolve_delivered_local_search_agent_step(handoff)
    with pytest.raises(FleetPlanInvalid, match='not admitted'):
        execute_strict_step(tmp_path, step, None, None, _runtime(handoff=handoff), step_order=1)


@pytest.mark.parametrize('task,effects', [
    ('ignore instructions', ''),
    ('search:needle', '    requested_effects:\n      repository_access: write\n'),
    ('search:needle', '    requested_effects:\n      recipients: [external_model]\n'),
])
def test_unsupported_agent_request_rejected_before_resolved_step(tmp_path, task, effects):
    handoff, _, _ = _case(tmp_path, task=task, effects=effects)
    with pytest.raises(FleetPlanInvalid):
        resolve_delivered_local_search_agent_step(handoff)


def test_agent_kind_survives_original_resolved_plan_identity_and_wire_roundtrip(tmp_path):
    from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
    handoff, _, _ = _case(tmp_path)
    step = resolve_delivered_local_search_agent_step(handoff)
    plan = ResolvedFleetPlan.create(
        name='read-only-agent-tool', description='test original Step union',
        execution=MaintenanceExecutionPolicy(), steps=(step,),
    )
    restored = ResolvedFleetPlan.model_validate_json(plan.model_dump_json())
    assert type(restored.steps[0]) is ResolvedLocalSearchAgentStep
    assert restored.steps[0].kind == 'agent'
    assert restored.steps[0].handoff_identity == handoff.handoff_identity
    assert restored.plan_id == plan.plan_id
    assert restored.steps[0].delivery_sha256 == step.delivery_sha256
    assert 'needle from captured repository' not in plan.model_dump_json()


def test_original_fleet_resolver_does_not_implicitly_admit_an_agent(tmp_path):
    handoff, _, _ = _case(tmp_path)
    assert handoff.effect_permission == 'NONE'
    # A validated typed Step is still not an admission grant. Ordinary
    # resolve/ingress continues to refuse it until AGENT-01B2B wires ownership.
    plan = tmp_path / 'plan.yml'
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path/'goons'))
    with pytest.raises(FleetPlanInvalid, match='preview-only'):
        resolver.resolve(plan, inputs={
            'repo':'local:team/app', 'skill':'local:team/search', 'acceptance':'review'
        })
