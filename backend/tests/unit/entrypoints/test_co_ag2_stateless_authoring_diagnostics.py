"""CO-AG2: original Plan owner serves read-only explanations and diffs without Fleet startup."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    def reject_runtime(**kwargs):
        pytest.fail('Read-only Plan diagnostics must not construct ApplicationRuntime')

    monkeypatch.setattr(cli, '_build_application_runtime', reject_runtime)


def _quality() -> Path:
    return PlanTemplateStore().source('python/quality').path


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GoonTemplateStore())


def test_explain_matches_original_owner_without_database(monkeypatch, capsys):
    _without_runtime(monkeypatch)
    source = _quality()
    expected = _resolver().explain(source)
    assert main(['plan', 'explain', str(source), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(expected.model_dump_json())


def test_routes_preserves_original_value_redaction_and_input_origin(monkeypatch, capsys, tmp_path):
    _without_runtime(monkeypatch)
    source = _quality()
    file = tmp_path / 'inputs.yml'
    file.write_text('inputs:\n  coverage_threshold: 87\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    assert main(['plan', 'explain', str(source), '--routes', '--json', '--inputs-file', 'inputs.yml']) == 0
    routed = json.loads(capsys.readouterr().out)
    assert routed['schema'] == 'oh-goon.plan-input-routing/v1'
    assert routed['status'] == 'resolved_not_executed'
    assert routed['inputs'][0]['origin']['kind'] == 'inputs_file'
    assert routed['inputs'][0]['origin']['path'] == 'inputs.yml'
    assert all('value' not in item for item in routed['bindings'])
    assert not (tmp_path / 'goon.lock').exists()


def test_diff_matches_single_original_semantic_projection_without_database(monkeypatch, capsys):
    _without_runtime(monkeypatch)
    before = _quality()
    after = PlanTemplateStore().source('python/dependency-inventory').path
    expected = _resolver().diff(before, after)
    assert main(['plan', 'diff', str(before), str(after), '--json']) == 0
    actual = json.loads(capsys.readouterr().out)
    assert actual == json.loads(expected.model_dump_json())
    assert actual['before_plan_id'] != actual['after_plan_id']


def test_explain_diagnostic_rejects_bad_input_without_runtime_or_secret_echo(monkeypatch, capsys):
    _without_runtime(monkeypatch)
    private = 'SENSITIVE_VALUE_DO_NOT_PRINT'
    assert main(['plan', 'explain', str(_quality()), '--routes', '--diagnostics', '--json',
                 '--input', f'unknown={private}']) == 2
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result['status'] == 'rejected'
    assert result['execution'] == 'not_started_by_diagnostic'
    assert private not in raw


def test_diff_rejects_invalid_plan_without_runtime_or_lock_write(monkeypatch, capsys, tmp_path):
    _without_runtime(monkeypatch)
    bad = tmp_path / 'invalid.yml'
    bad.write_text('version: 1\ngoons:\n- missing/canonical-owner\n', encoding='utf-8')
    before = bad.read_bytes()
    assert main(['plan', 'diff', str(_quality()), str(bad), '--json']) == 2
    assert capsys.readouterr().out
    assert bad.read_bytes() == before
    assert not (tmp_path / 'goon.lock').exists()


def test_original_service_delegates_semantic_diff_to_same_resolver():
    from oh_goon.services.fleet_plans import FleetPlanService

    resolver = _resolver()
    service = FleetPlanService(resolver, None, None, None, None)
    before = _quality()
    after = PlanTemplateStore().source('python/dependency-inventory').path
    assert service.diff(before, after) == resolver.diff(before, after)


def test_check_matches_resolver_and_covers_invalid_contract_without_runtime(monkeypatch, capsys, tmp_path):
    _without_runtime(monkeypatch)
    good = _quality()
    expected = _resolver().check(good)
    assert main(['plan', 'check', str(good), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(expected.model_dump_json())
    bad = tmp_path / 'invalid.yml'
    bad.write_text('version: 1\nsteps: unexpected\n', encoding='utf-8')
    assert main(['plan', 'check', str(bad), '--json']) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['issues']
    assert not (tmp_path / 'goon.lock').exists()


def test_directory_check_maintains_original_syntax_only_semantics_without_runtime(monkeypatch, capsys, tmp_path):
    _without_runtime(monkeypatch)
    (tmp_path / 'valid.yml').write_text(
        'version: 1\ngoons:\n- python/quality\n',
        encoding='utf-8',
    )
    assert main(['plan', 'check', str(tmp_path), '--json']) == 0
    actual = json.loads(capsys.readouterr().out)
    assert actual['checked_files'] == 1
    assert actual['issues'] == []
