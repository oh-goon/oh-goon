"""T2S: Schedule freezes resolved target, never carries a caller file dependency to future Runs."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.cli_plan_input_files import read_caller_input_file
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.caller_input_file import ScheduledCallerFileReceipt
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.services.fleet_caller_file_freshness import require_target_caller_file_fresh
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.schedule_admission import _admission_plan
from tests.factories.schedules import schedule_create


def _fresh(tmp_path: Path):
    source = tmp_path / 'staging.yml'
    source.write_text('inputs:\n  coverage_threshold: 85\n')
    identity = read_caller_input_file(source, root=tmp_path).authority()
    return source, identity


def _prepare(tmp_path: Path):
    source, identity = _fresh(tmp_path)
    original = schedule_create().target
    calls = []
    service = object.__new__(FleetPlanService)
    def prepare(*a, **kw):
        calls.append(kw)
        return FleetPlanTarget.model_validate({
            **original.model_dump(), 'caller_input_file': identity.model_dump()
        })
    service.prepare_execution_target = prepare
    return source, identity, original, service, calls


def test_scheduled_receipt_only_references_frozen_plan_and_no_caller_root(tmp_path):
    source, identity, original, service, calls = _prepare(tmp_path)
    target = service._prepare_scheduled_target(
        Path('plan.yml'), extensions_path=None, inputs={'coverage_threshold': 85},
        caller_input_file=identity,
    )
    assert len(calls) == 1
    assert calls[0]['caller_input_file'] == identity
    assert target.plan == original.plan
    assert target.identity == original.identity
    assert target.caller_input_file is None
    receipt = target.scheduled_caller_file
    assert receipt is not None and receipt.sha256 == identity.sha256
    assert receipt.execution_digest == target.execution_digest
    assert receipt.relative_path == 'staging.yml'
    assert 'caller_root' not in target.model_dump_json()
    assert '85' not in receipt.model_dump_json()
    assert FleetPlanTarget.model_validate_json(target.model_dump_json()) == target
    source.unlink()
    require_target_caller_file_fresh(target)  # A future occurrence needs no old file.


def test_scheduled_creation_source_changes_fail_before_target_can_persist(tmp_path):
    source, identity, _original, service, _calls = _prepare(tmp_path)
    source.write_text('inputs:\n  coverage_threshold: 86\n')
    with pytest.raises(FleetPlanInvalid):
        service._prepare_scheduled_target(Path('plan.yml'), extensions_path=None, inputs={}, caller_input_file=identity)


def test_source_changed_during_resolution_fails_closed(tmp_path):
    source, identity, original, service, calls = _prepare(tmp_path)
    def swapped(*a, **kw):
        calls.append(kw)
        source.write_text('inputs:\n  coverage_threshold: 86\n')
        return original
    service.prepare_execution_target = swapped
    with pytest.raises(FleetPlanInvalid):
        service._prepare_scheduled_target(Path('plan.yml'), extensions_path=None, inputs={}, caller_input_file=identity)


def test_durable_schedule_definition_and_occurrence_do_not_reopen_caller_file(tmp_path):
    source, identity, _original, service, _calls = _prepare(tmp_path)
    target = service._prepare_scheduled_target(Path('plan.yml'), extensions_path=None, inputs={}, caller_input_file=identity)
    request = schedule_create().model_copy(update={'target': target})
    db = LocalDatabase(tmp_path / 'store.db')
    store = ScheduleDefinitionStore(db)
    saved = store.create('with-values', request)
    source.unlink()
    loaded = store.get('with-values')
    assert loaded.target == saved.target == target
    assert loaded.target.scheduled_caller_file is not None
    assert loaded.target.caller_input_file is None
    intent = _admission_plan(loaded, db.durable_revision()).intent
    assert intent.target == target
    require_target_caller_file_fresh(intent.target)


def test_target_receipt_fails_on_forged_execution_digest_dual_source_or_invalid_path(tmp_path):
    _source, identity, original, service, _calls = _prepare(tmp_path)
    target = service._prepare_scheduled_target(Path('plan.yml'), extensions_path=None, inputs={}, caller_input_file=identity)
    payload = target.model_dump()
    payload['scheduled_caller_file']['execution_digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValidationError, match='not bound'):
        FleetPlanTarget.model_validate(payload)
    payload = target.model_dump()
    payload['caller_input_file'] = identity.model_dump()
    with pytest.raises(ValidationError, match='mutually exclusive'):
        FleetPlanTarget.model_validate(payload)
    with pytest.raises(ValidationError):
        ScheduledCallerFileReceipt(relative_path='../secrets.yml', sha256=identity.sha256, execution_digest=target.execution_digest)


def test_legacy_schedule_target_wire_identity_is_unchanged():
    target = schedule_create().target
    encoded = target.model_dump_json()
    assert 'scheduled_caller_file' not in encoded
    assert 'caller_input_file' not in encoded
    assert FleetPlanTarget.model_validate_json(encoded) == target


def test_real_catalog_values_file_parameter_is_frozen_in_scheduled_execution(tmp_path):
    """Prove a real recipient-`pass:` value survives without the original YAML."""
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES

    source, identity = _fresh(tmp_path)
    template = PLAN_TEMPLATES.source('python/quality').path
    resolved = FleetPlanResolver(goon_templates=GOON_TEMPLATES).resolve(
        template, inputs={'coverage_threshold': 85},
    )
    baseline = FleetPlanTarget(
        kind='fleet_plan', plan=resolved,
        source=PlanFileSource(sha256='0' * 64),
        identity=ExecutionIdentity(digest=build_execution_lock(resolved).digest),
    )
    service = object.__new__(FleetPlanService)
    service.prepare_execution_target = lambda *a, **kw: FleetPlanTarget.model_validate({
        **baseline.model_dump(), 'caller_input_file': identity.model_dump(),
    })
    scheduled = service._prepare_scheduled_target(
        template, extensions_path=None, inputs={'coverage_threshold': 85},
        caller_input_file=identity,
    )
    assert '--cov-fail-under=85' in next(
        arg for step in scheduled.plan.steps for arg in step.argv if arg.startswith('--cov-fail-under=')
    )
    source.write_text('inputs:\n  coverage_threshold: 90\n')
    assert '--cov-fail-under=85' in next(
        arg for step in FleetPlanTarget.model_validate_json(scheduled.model_dump_json()).plan.steps
        for arg in step.argv if arg.startswith('--cov-fail-under=')
    )
    require_target_caller_file_fresh(scheduled)


def test_schedule_create_reschedule_cas_and_changed_file_do_not_reselect_without_request(tmp_path):
    source, original_identity, baseline, service, _calls = _prepare(tmp_path)
    db = LocalDatabase(tmp_path / 'cas.db')
    definitions = ScheduleDefinitionStore(db)
    service._schedules = SimpleNamespace(
        create_prepared=definitions.create,
        definition=definitions.get,
        replace_prepared=definitions.replace,
    )
    service.repository_instance_ids_for_target = lambda target, repository_ids: {
        rid: 'instance-' + rid for rid in repository_ids
    }
    opts = dict(
        schedule_id='daily', extensions_path=None, repository_ids={'repo-a'},
        interval_minutes=60, name='Daily', inputs={'coverage_threshold': 85},
    )
    first = service.schedule(Path('plan.yml'), caller_input_file=original_identity, **opts)
    assert first.record_version == 0
    assert first.target.scheduled_caller_file.sha256 == original_identity.sha256
    assert service.schedule(Path('plan.yml'), caller_input_file=original_identity, **opts) == first
    source.write_text('inputs:\n  coverage_threshold: 86\n')
    assert definitions.get('daily') == first
    with pytest.raises(FleetPlanInvalid):
        service.reschedule(Path('plan.yml'), caller_input_file=original_identity, **opts)
    assert definitions.get('daily') == first
    new_identity = read_caller_input_file(source, root=tmp_path).authority()
    second = service.reschedule(Path('plan.yml'), caller_input_file=new_identity, **opts)
    assert second.record_version == 1
    assert second.target.scheduled_caller_file.sha256 == new_identity.sha256
    assert second.target.scheduled_caller_file.sha256 != first.target.scheduled_caller_file.sha256
    assert second.target.plan == first.target.plan  # Same Plan semantics, different source bytes.
    assert definitions.get('daily') == second
