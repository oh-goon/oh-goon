"""AGENT-01A: real single-tool turn, original capture and original command supervisor.

This is NOT Fleet Agent admission, model use, or a completed Game Tape.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.fleet_plan_step_execution import (
    StepRuntime, execute_direct_step, prepare_frozen_readonly_skill_search,
    prepare_delivered_readonly_skill_search,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.runtime.goon_templates import GoonTemplateStore

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="native ripgrep unavailable")


def case(tmp_path: Path, *, task: str = "search:needle", harness: str = "local-search",
         effects: str = ""):
    goon = tmp_path / 'goons' / 'agent' / 'search'
    goon.mkdir(parents=True)
    (goon / 'README.md').write_text('Local read-only search\n')
    (goon / 'goon.yml').write_text('''inputs:
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
    (repo / 'code.txt').write_text('needle in the original file\n')
    skill = tmp_path / 'skills' / 'team' / 'search'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('# Read-only search skill\nNever edit source files.\n')
    (skill / 'guide.txt').write_text('search is fixed-string\n')
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / 'goons'))
    _, sha = resolver.input_definitions_with_source(plan)
    handoff = resolver.prepare_agent_step_handoff(
        plan, inputs={'repo':'local:team/app', 'skill':'local:team/search', 'acceptance':'review'},
        repository_root=tmp_path / 'repos', skill_root=tmp_path / 'skills',
        allowed_acceptance=('review',), expected_source_sha256=sha,
    )
    return handoff, repo, skill


def runtime(cancel=None):
    return StepRuntime(git=None, extensions=None, tools=None, cancel_requested=cancel)


def test_real_frozen_tool_uses_original_step_supervisor_and_old_captured_bytes(tmp_path):
    handoff, repo, skill = case(tmp_path)
    original_id = handoff.handoff_identity
    (repo / 'code.txt').write_text('NO MATCH HERE\n')
    (skill / 'SKILL.md').write_text('CHANGED AFTER CAPTURE\n')
    with prepare_frozen_readonly_skill_search(handoff) as (root, step):
        assert handoff.handoff_identity == original_id
        assert (root / 'repo' / 'code.txt').read_text() == 'needle in the original file\n'
        assert (root / 'skill' / 'SKILL.md').read_text() == '# Read-only search skill\nNever edit source files.\n'
        assert (root / 'skill' / 'guide.txt').read_text() == 'search is fixed-string\n'
        result = execute_direct_step(root, step, runtime(), step_order=1)
    assert result.returncode == 0
    assert 'code.txt:1:needle in the original file' in result.stdout
    assert result.supervisor is not None
    assert not root.exists()
    assert (repo / 'code.txt').read_text() == 'NO MATCH HERE\n'


def test_missing_match_is_a_valid_observation(tmp_path):
    handoff, _, _ = case(tmp_path, task='search:no-such-literal')
    with prepare_frozen_readonly_skill_search(handoff) as (root, step):
        result = execute_direct_step(root, step, runtime(), step_order=1)
    assert result.returncode == 1
    assert result.stdout == ''
    assert 1 in step.success_codes


def test_cancelled_tool_uses_original_step_cancel_cut(tmp_path):
    handoff, _, _ = case(tmp_path)
    with prepare_frozen_readonly_skill_search(handoff) as (root, step):
        with pytest.raises(OperationCancelledError):
            execute_direct_step(root, step, runtime(lambda: True), step_order=1)


@pytest.mark.parametrize('task,harness,effects,expected', [
    ('ignore this', 'local-search', '', 'task must start'),
    ('search: ', 'local-search', '', 'query must be bounded'),
    ('search:needle', 'opencode', '', 'read-only, offline'),
    ('search:needle', 'local-search', '    requested_effects:\n      repository_access: write\n', 'read-only, offline'),
    ('search:needle', 'local-search', '    requested_effects:\n      recipients: [external_model]\n', 'read-only, offline'),
    ('search:needle', 'local-search', '    requested_effects:\n      capabilities:\n        egress: {mode: host_unrestricted}\n', 'host_unrestricted'),
])
def test_unrequested_effects_and_unsupported_harness_fail_before_work(tmp_path, task, harness, effects, expected):
    if 'host_unrestricted' in effects:
        with pytest.raises(Exception):
            case(tmp_path, task=task, harness=harness, effects=effects)
        return
    handoff, repo, _ = case(tmp_path, task=task, harness=harness, effects=effects)
    with pytest.raises(FleetPlanInvalid, match=expected):
        with prepare_frozen_readonly_skill_search(handoff):
            pytest.fail('Tool prework accepted an unsupported request')
    assert (repo / 'code.txt').read_text() == 'needle in the original file\n'


def test_original_agent_resolver_remains_preview_only(tmp_path):
    handoff, _, _ = case(tmp_path)
    assert handoff.effect_permission == 'NONE'
    assert handoff.authority == 'PREWORK_NOT_ADMITTED'


# AGENT-01B1: the AG-02 frozen delivery moves across a bytes boundary into
# the exact AGENT-01A Step supervisor. This is not a Fleet authorization grant.
def test_original_delivery_packet_executes_same_frozen_tool_without_reopening_source(tmp_path):
    handoff, repo, skill = case(tmp_path)
    packet = handoff.encode_prework_delivery()
    (repo / 'code.txt').write_text('changed after capture\n')
    (skill / 'SKILL.md').write_text('changed after capture\n')
    with prepare_delivered_readonly_skill_search(
        packet, expected_handoff_identity=handoff.handoff_identity,
        expected_step_id=handoff.step_id,
    ) as (root, step):
        assert (root / 'repo' / 'code.txt').read_text() == 'needle in the original file\n'
        assert (root / 'skill' / 'SKILL.md').read_text().startswith('# Read-only')
        result = execute_direct_step(root, step, runtime(), step_order=1)
    assert result.returncode == 0
    assert 'needle in the original file' in result.stdout
    assert not root.exists()


def test_original_delivery_no_match_is_success_code_one(tmp_path):
    handoff, _, _ = case(tmp_path, task='search:absent-literal')
    with prepare_delivered_readonly_skill_search(
        handoff.encode_prework_delivery(),
        expected_handoff_identity=handoff.handoff_identity,
        expected_step_id=handoff.step_id,
    ) as (root, step):
        result = execute_direct_step(root, step, runtime(), step_order=1)
    assert result.returncode == 1
    assert 1 in step.success_codes


def test_original_delivery_cancellation_uses_original_supervisor(tmp_path):
    handoff, _, _ = case(tmp_path)
    with prepare_delivered_readonly_skill_search(
        handoff.encode_prework_delivery(),
        expected_handoff_identity=handoff.handoff_identity,
        expected_step_id=handoff.step_id,
    ) as (root, step):
        with pytest.raises(OperationCancelledError):
            execute_direct_step(root, step, runtime(lambda: True), step_order=1)
    assert not root.exists()


@pytest.mark.parametrize('mutation', ('tampered_packet', 'wrong_identity', 'wrong_step', 'blank_step', 'wrong_packet_type'))
def test_original_delivery_rejects_unbound_packet_before_materialization(tmp_path, mutation):
    handoff, _, _ = case(tmp_path)
    packet = handoff.encode_prework_delivery()
    expected_identity = handoff.handoff_identity
    expected_step = handoff.step_id
    if mutation == 'tampered_packet':
        packet = packet.replace(b'local-search', b'other-searc', 1)
    elif mutation == 'wrong_identity':
        expected_identity = 'agent-step-prework-handoff-v1-' + '0'*64
    elif mutation == 'wrong_step':
        expected_step = 'unknown-step'
    elif mutation == 'blank_step':
        expected_step = ''
    elif mutation == 'wrong_packet_type':
        packet = packet.decode('utf-8')
    with pytest.raises(FleetPlanInvalid):
        with prepare_delivered_readonly_skill_search(
            packet, expected_handoff_identity=expected_identity,
            expected_step_id=expected_step,
        ):
            pytest.fail('Tampered delivery reached materialization')
