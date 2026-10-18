"""T2: strict input-file parsing is independent of admission and Run identity."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli_plan_input_files import (
    read_caller_input_file, require_caller_input_file_unchanged,
)
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.runtime.goon_templates import GoonTemplateStore


def _file(tmp_path: Path, content: str = 'inputs:\n  coverage_threshold: 85\n') -> Path:
    path = tmp_path / "staging.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _args(path: Path | None, *items: str) -> argparse.Namespace:
    return argparse.Namespace(plan_inputs=list(items), inputs_file=path)


def test_frozen_snapshot_path_digest_and_typed_mapping(tmp_path):
    p = _file(tmp_path, 'inputs:\n  x: 85\n  truth: true\n  literal: "001"\n  vacant: null\n')
    snap = read_caller_input_file(p, root=tmp_path)
    assert snap.values() == {'x': 85, 'truth': True, 'literal': '001', 'vacant': None}
    assert snap.relative_path == 'staging.yml' and len(snap.sha256) == 64
    assert snap == read_caller_input_file(p, root=tmp_path)
    p.write_text('inputs:\n  x: 86\n', encoding='utf-8')
    assert snap.sha256 != read_caller_input_file(p, root=tmp_path).sha256
    assert snap.values()['x'] == 85


@pytest.mark.parametrize('text', [
    'anything: 85\n', 'inputs: 85\n', 'inputs:\n  x: [1]\n',
    'inputs:\n  x: {k: v}\n', 'inputs:\n  x: 1.0\n',
    'inputs:\n  x: 1\n  x: 2\n', 'inputs:\n  x: 1\ninputs:\n  y: 2\n',
    'inputs:\n  bad.name: 1\n', 'inputs:\n  x: ${{ inputs.x }}\n',
    'inputs:\n  x: &v a\n  y: *v\n',
    'inputs:\n  x: !custom value\n',
    'inputs:\n  <<: {x: 1}\n',
    'inputs:\n  x: 2026-10-11\n',
    '', 'inputs: null\n',
])
def test_invalid_yaml_shapes_rejected_without_value_resolution(tmp_path, text):
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(_file(tmp_path, text), root=tmp_path)


def test_bad_encoding_oversized_too_many_keys_and_empty_root(tmp_path):
    p = _file(tmp_path)
    p.write_bytes(b'\xff\xfe')
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(p, root=tmp_path)
    p.write_bytes(b' ' * 65537)
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(p, root=tmp_path)
    p.write_text('inputs:\n' + ''.join(f'  k{i}: {i}\n' for i in range(65)))
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(p, root=tmp_path)


def test_escape_symlink_directory_and_file_rejected(tmp_path):
    p = _file(tmp_path)
    outside = tmp_path.parent / 'outside-t2.yml'
    # Never reads an outside path; a lexical escape is enough to reject.
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(outside, root=tmp_path)
    (tmp_path / 'link.yml').symlink_to(p)
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(tmp_path / 'link.yml', root=tmp_path)
    (tmp_path / 'link-dir').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(tmp_path / 'link-dir' / 'staging.yml', root=tmp_path)
    with pytest.raises(FleetPlanInvalid):
        read_caller_input_file(tmp_path, root=tmp_path)


def test_disjoint_cli_and_file_preserve_scalar_types_and_duplicate_failure(tmp_path, monkeypatch):
    p = _file(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert plan_inputs(_args(p, 'release=stable')) == {'release': 'stable', 'coverage_threshold': 85}
    with pytest.raises(FleetPlanInvalid, match='both --input and --inputs-file'):
        plan_inputs(_args(p, 'coverage_threshold=85'))
    assert plan_inputs(_args(None, 'coverage_threshold=85')) == {'coverage_threshold': '85'}


def test_all_plan_value_commands_accept_flag_but_not_input_discovery():
    parser = build_parser()
    for name, args in [
        ('validate', ['one.yml']), ('prepare', ['one.yml']),
        ('preview', ['one.yml', '.']), ('explain', ['one.yml']),
        ('check', ['one.yml']), ('diff', ['one.yml', 'two.yml']),
        ('run', ['one.yml', '--all-repositories']),
        ('schedule', ['one.yml', '--schedule-id', 'x', '--repository-id', 'r', '--interval-minutes', '5']),
        ('reschedule', ['one.yml', '--schedule-id', 'x', '--repository-id', 'r', '--interval-minutes', '5']),
    ]:
        parsed = parser.parse_args(['plan', name, *args, '--inputs-file', 'values.yml'])
        assert parsed.inputs_file == Path('values.yml')
    with pytest.raises(SystemExit):
        parser.parse_args(['plan', 'inputs', 'one.yml', '--inputs-file', 'values.yml'])


def test_existing_native_typed_binder_rejects_invalid_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = _file(tmp_path)
    from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate
    from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputType
    from oh_goon.core.fleet_plan_composition import bind_inputs
    template = CompiledExecutionTemplate.model_validate({
        'name': 'test', 'description': '', 'inputs': [{
            'name': 'coverage_threshold', 'input_type': 'integer', 'minimum': 70, 'maximum': 100,
        }], 'steps': [{'kind': 'command', 'step_id': 'one', 'argv': ['true']}],
    })
    assert bind_inputs(template, plan_inputs(_args(values)), context='file')['coverage_threshold'] == 85
    values.write_text('inputs:\n  coverage_threshold: 150\n')
    with pytest.raises(FleetPlanInvalid):
        bind_inputs(template, plan_inputs(_args(values)), context='file')


def test_run_preparation_rejects_before_file_read_and_work(monkeypatch, tmp_path, capsys):
    from oh_goon import cli_plan_inputs
    path = tmp_path / 'never-read.yml'
    called = []
    def preflight(_):
        called.append('preflight')
        raise FleetPlanInvalid('NO_ENROLLED_RUN')
    runtime = SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=preflight,
                                                    run=lambda *a, **k: called.append('work')))
    args = argparse.Namespace(command='plan', plan_command='run', fleet_run_id=None,
                              inputs_file=path, plan_inputs=[], lock_file=None)
    monkeypatch.setattr(cli_plan_inputs, 'read_caller_input_file', lambda *a, **k: called.append('read'))
    assert run_plan_command(runtime, args) == 2
    assert called == ['preflight']
    assert 'NO_ENROLLED_RUN' in capsys.readouterr().out


def test_enrolled_run_invalid_or_uncontained_file_rejects_before_effect(tmp_path, capsys):
    calls=[]
    runtime=SimpleNamespace(plans=SimpleNamespace(
        require_cli_plan_preparation=lambda _: calls.append('preflight'),
        run=lambda *a, **k: calls.append('run')))
    args=argparse.Namespace(command='plan',plan_command='run',fleet_run_id='enrolled',
                            inputs_file=tmp_path / 'missing.yml',plan_inputs=[])
    assert run_plan_command(runtime,args) == 2
    assert calls == ['preflight']
    assert '--inputs-file must be a file contained' in capsys.readouterr().out


@pytest.mark.parametrize('operation', ['schedule', 'reschedule'])
def test_schedule_values_file_missing_rejects_before_effect(tmp_path, operation):
    calls = []
    runtime = SimpleNamespace(plans=SimpleNamespace(**{operation: lambda *a, **k: calls.append('work')}))
    args = argparse.Namespace(command='plan', plan_command=operation,
                              inputs_file=tmp_path / 'missing.yml', plan_inputs=[])
    assert run_plan_command(runtime, args) == 2
    assert calls == []


@pytest.mark.parametrize('operation', ['schedule', 'reschedule'])
def test_scheduled_cli_transports_one_frozen_caller_identity(tmp_path, monkeypatch, operation):
    monkeypatch.chdir(tmp_path)
    original = _file(tmp_path)
    calls = []
    def create(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(schedule_id='daily', record_version=1, target=SimpleNamespace(plan=SimpleNamespace(plan_id='plan-1')), repository_ids=['r'])
    runtime = SimpleNamespace(plans=SimpleNamespace(**{operation: create}))
    args = argparse.Namespace(command='plan', plan_command=operation,
        plan=tmp_path / 'plan.yml', extensions=None, schedule_id='daily',
        repository_ids=['r'], name='Daily', interval_minutes=15,
        inputs_file=original, plan_inputs=[])
    assert run_plan_command(runtime, args) == 0
    assert len(calls) == 1
    assert calls[0]['inputs'] == {'coverage_threshold': 85}
    identity = calls[0]['caller_input_file']
    assert identity.sha256 == args._caller_input_file_snapshot.sha256
    assert identity.relative_path == original.name
    assert 'entries' not in identity.model_dump()
    assert set(identity.model_dump()) == {'caller_root', 'relative_path', 'sha256', 'source_device', 'source_inode'}


def test_one_snapshot_even_when_collector_called_twice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = _file(tmp_path)
    args = _args(values)
    assert plan_inputs(args) == {'coverage_threshold': 85}
    observed = args._caller_input_file_snapshot
    values.write_text('inputs:\n  coverage_threshold: 86\n', encoding='utf-8')
    assert plan_inputs(args) == {'coverage_threshold': 85}
    assert args._caller_input_file_snapshot is observed


def test_reject_parent_dir_traversal_even_if_result_is_within_root(tmp_path):
    _file(tmp_path)
    child = tmp_path / 'child'
    child.mkdir()
    with pytest.raises(FleetPlanInvalid, match='parent-directory'):
        read_caller_input_file(child / '..' / 'staging.yml', root=tmp_path)


def test_frozen_file_identity_rechecked_without_rebinding_values(tmp_path):
    source = _file(tmp_path)
    snap = read_caller_input_file(source, root=tmp_path)
    require_caller_input_file_unchanged(snap, root=tmp_path)
    source.write_text('inputs:\n  coverage_threshold: 86\n', encoding='utf-8')
    with pytest.raises(FleetPlanInvalid, match='changed after'):
        require_caller_input_file_unchanged(snap, root=tmp_path)
    # A snapshot remains frozen; no re-selection during a staleness check.
    assert snap.values()['coverage_threshold'] == 85


def test_frozen_file_replaced_same_bytes_is_stale(tmp_path):
    source = _file(tmp_path)
    snap = read_caller_input_file(source, root=tmp_path)
    other = tmp_path / 'replacement.yml'
    other.write_bytes(source.read_bytes())
    other.replace(source)
    with pytest.raises(FleetPlanInvalid, match='replaced after snapshot'):
        require_caller_input_file_unchanged(snap, root=tmp_path)


def test_frozen_file_removed_or_swapped_to_link_is_stale(tmp_path):
    source = _file(tmp_path)
    snap = read_caller_input_file(source, root=tmp_path)
    source.unlink()
    with pytest.raises(FleetPlanInvalid, match='not a readable regular file'):
        require_caller_input_file_unchanged(snap, root=tmp_path)
    source.symlink_to(tmp_path / 'secret.yml')
    with pytest.raises(FleetPlanInvalid, match='symlink'):
        require_caller_input_file_unchanged(snap, root=tmp_path)


def test_frozen_file_bytes_are_never_reparsed_during_identity_check(tmp_path, monkeypatch):
    source = _file(tmp_path)
    snap = read_caller_input_file(source, root=tmp_path)
    from oh_goon import cli_plan_input_files
    def forbidden(*args, **kwargs):
        raise AssertionError('YAML parser cannot select a new value at the staleness fence')
    monkeypatch.setattr(cli_plan_input_files.yaml, 'load', forbidden)
    require_caller_input_file_unchanged(snap, root=tmp_path)


def test_staleness_fence_rejects_different_caller_root_even_for_same_hardlinked_inode(tmp_path):
    original = tmp_path / 'original'
    alternate = tmp_path / 'alternate'
    original.mkdir()
    alternate.mkdir()
    source = _file(original)
    (alternate / source.name).hardlink_to(source)
    snapshot = read_caller_input_file(source, root=original)
    with pytest.raises(FleetPlanInvalid, match='caller root differs'):
        require_caller_input_file_unchanged(snapshot, root=alternate)


def test_staleness_fence_rejects_mutation_during_identity_recheck(tmp_path, monkeypatch):
    source = _file(tmp_path)
    snapshot = read_caller_input_file(source, root=tmp_path)
    from oh_goon.services import fleet_caller_file_freshness
    original = fleet_caller_file_freshness.read_caller_file_bytes
    def change_after_snapshot(path, *, expected):
        data = original(path, expected=expected)
        path.write_text('inputs:\n  coverage_threshold: 87\n', encoding='utf-8')
        return data
    monkeypatch.setattr(fleet_caller_file_freshness, 'read_caller_file_bytes', change_after_snapshot)
    with pytest.raises(FleetPlanInvalid, match='changed after'):
        require_caller_input_file_unchanged(snapshot, root=tmp_path)


def test_enrolled_manual_run_transports_exact_source_without_reselecting(tmp_path, monkeypatch, capsys):
    source = _file(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    def start(*args, **kwargs):
        calls.append(('run', args, kwargs))
        identity = kwargs['caller_input_file']
        assert identity.sha256 == read_caller_input_file(source, root=tmp_path).sha256
        assert identity.relative_path == source.name
        assert 'entries' not in identity.model_dump()
        assert kwargs['inputs'] == {'coverage_threshold': 85}
        return SimpleNamespace(fleet_run_id='enrolled', target=SimpleNamespace(plan=SimpleNamespace(plan_id='p')),
                               status=SimpleNamespace(value='queued'))
    runtime = SimpleNamespace(plans=SimpleNamespace(
        require_cli_plan_preparation=lambda _: calls.append(('preflight',)), run=start))
    args = argparse.Namespace(command='plan', plan_command='run', fleet_run_id='enrolled',
        inputs_file=source, plan=tmp_path / 'plan.yml', extensions=None, repository_ids=['repo'],
        all_repositories=False, priority=None, plan_inputs=[], lock_file=None, wait=False)
    assert run_plan_command(runtime, args) == 0
    assert [c[0] for c in calls] == ['preflight', 'run']
    assert 'Fleet Run ID: enrolled' in capsys.readouterr().out


def test_identity_roundtrip_and_stale_after_queue_simulation(tmp_path):
    from oh_goon.core.models.caller_input_file import CallerInputFileIdentity
    from oh_goon.services.fleet_caller_file_freshness import require_caller_file_fresh
    source = _file(tmp_path)
    identity = read_caller_input_file(source, root=tmp_path).authority()
    transported = CallerInputFileIdentity.model_validate_json(identity.model_dump_json())
    assert transported == identity
    require_caller_file_fresh(transported)
    source.write_text('inputs:\n  coverage_threshold: 86\n')
    with pytest.raises(FleetPlanInvalid, match='changed after'):
        require_caller_file_fresh(transported)


def test_fleet_control_plane_admission_rejects_stale_file_before_prework_or_planning(tmp_path):
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
    from tests.factories.schedules import scheduled_plan
    source = _file(tmp_path)
    snapshot = read_caller_input_file(source, root=tmp_path)
    plan = scheduled_plan('file-fence')
    target = FleetPlanTarget(kind='fleet_plan', plan=plan,
        source=PlanFileSource(sha256='0'*64),
        identity=ExecutionIdentity(digest=build_execution_lock(plan).digest),
        caller_input_file=snapshot.authority())
    roundtrip = FleetPlanTarget.model_validate_json(target.model_dump_json())
    assert roundtrip.caller_input_file == snapshot.authority()
    assert roundtrip.execution_digest == target.execution_digest
    source.write_text('inputs:\n  coverage_threshold: 86\n')
    calls = []
    manager = SimpleNamespace(_lifecycle=SimpleNamespace(require_fleet_prework_available=lambda *a, **k: calls.append('prework')))
    intent = SimpleNamespace(target=roundtrip)
    with pytest.raises(FleetPlanInvalid, match='changed after'):
        FleetRunControlPlane._start_intent(manager, intent,
            repository_scope=None, fleet_run_id='enrolled', source=None,
            readmitted_from_run_id=None, rerun_from_run_id=None)
    assert calls == []


def test_worker_rejects_changed_queued_file_before_run_started_or_effects(tmp_path, monkeypatch):
    from oh_goon.services.fleet_run_execution import FleetRunExecution
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.core.models.target import FleetPlanTarget
    from tests.factories.schedules import scheduled_plan
    import oh_goon.services.fleet_run_execution as module
    source = _file(tmp_path)
    snapshot = read_caller_input_file(source, root=tmp_path)
    plan = scheduled_plan('worker-fence')
    target = FleetPlanTarget(kind='fleet_plan', plan=plan,
        source=PlanFileSource(sha256='0'*64),
        identity=ExecutionIdentity(digest=build_execution_lock(plan).digest),
        caller_input_file=snapshot.authority())
    calls = []
    lifecycle = SimpleNamespace(
        execution_authority=lambda _: SimpleNamespace(target=target, capability='cap', targets=()),
        begin=lambda _: calls.append('begin'))
    worker = FleetRunExecution(lifecycle, SimpleNamespace(execute=lambda *a, **k: calls.append('effects')),
        SimpleNamespace())
    monkeypatch.setattr(module, 'resolve_bound_capability_intent', lambda *a: SimpleNamespace())
    source.write_text('inputs:\n  coverage_threshold: 86\n')
    terminal, failure = worker._run(SimpleNamespace())
    assert terminal.name == 'FAIL' and failure is not None
    assert calls == []


def test_legacy_target_without_file_keeps_prior_wire_shape():
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.core.models.target import FleetPlanTarget
    from tests.factories.schedules import scheduled_plan
    plan = scheduled_plan('legacy-wire')
    target = FleetPlanTarget(kind='fleet_plan', plan=plan, source=PlanFileSource(sha256='0'*64),
        identity=ExecutionIdentity(digest=build_execution_lock(plan).digest))
    assert target.caller_input_file is None
    assert 'caller_input_file' not in target.model_dump(mode='json')
    assert 'caller_input_file' not in target.model_dump_json()
