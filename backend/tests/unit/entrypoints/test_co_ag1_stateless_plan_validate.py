"""CO-AG1: single original authoring resolver, independent of runtime startup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("Plan validation must not start SQLite or Fleet"),
    )


def test_shipped_plan_validate_is_exact_canonical_resolution_without_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_runtime(monkeypatch)
    path = PlanTemplateStore().source("python/quality").path
    expected = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve(path)
    assert main(["plan", "validate", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(expected.model_dump_json())


def test_invalid_plan_rejects_before_runtime_and_does_not_mutate_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_runtime(monkeypatch)
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ngoons:\n- missing/does-not-exist\n", encoding="utf-8")
    before = path.read_bytes()
    assert main(["plan", "validate", str(path), "--json", "--diagnostics"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected"
    assert payload["execution"] == "not_started_by_diagnostic"
    assert path.read_bytes() == before
    assert not (tmp_path / "goon.lock").exists()


def test_input_errors_reject_without_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_runtime(monkeypatch)
    path = PlanTemplateStore().source("python/quality").path
    assert main(["plan", "validate", str(path), "--input", "not-owned=1"]) == 2
    assert capsys.readouterr().out


def test_typed_input_is_resolved_once_without_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_runtime(monkeypatch)
    path = PlanTemplateStore().source("python/quality").path
    assert main(["plan", "validate", str(path), "--input", "coverage_threshold=90", "--json"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["steps"]
    assert not any("hashmarks" in item.lower() for item in resolved.keys())


def test_missing_plan_rejected_without_application_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_runtime(monkeypatch)
    assert main(["plan", "validate", str(tmp_path / "absent.yml")]) == 2
    assert capsys.readouterr().out
