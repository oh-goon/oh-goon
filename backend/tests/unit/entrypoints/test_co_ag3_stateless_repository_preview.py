"""CO-AG3: repository-scoped Plan preview reuses original read-only evaluation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService


def _source(key: str = "python/dependency-inventory") -> Path:
    return PlanTemplateStore().source(key).path


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GoonTemplateStore())


def _no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime",
        lambda **_kw: pytest.fail("repository preview started application/Fleet runtime"),
    )


def test_cli_preview_uses_original_repository_projection_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname="demo"\nversion="0.1.0"\n')
    source = _source()
    original_source = source.read_bytes()
    expected = _resolver().preview(source, repo)
    assert main(["plan", "preview", str(source), str(repo), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == json.loads(expected.model_dump_json())
    assert got["repository"] == str(repo)
    assert got["steps"]
    assert source.read_bytes() == original_source
    assert not (repo / "goon.lock").exists()
    assert not list(repo.rglob("*.sqlite*"))


def test_cli_preview_text_keeps_original_readiness_reason_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    source = _source()
    expected = _resolver().preview(source, tmp_path)
    assert main(["plan", "preview", str(source), str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"Plan ID: {expected.plan_id}" in out
    assert f"Repository: {expected.repository}" in out
    for step in expected.steps:
        assert f"{step.step_id}: {step.disposition.value}" in out


def test_cli_preview_preserves_canonical_import_applicability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    source = _source("python/quality")
    expected = _resolver().preview(source, tmp_path)
    assert main(["plan", "preview", str(source), str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads(expected.model_dump_json())
    assert len(report["imports"]) > 0
    assert any(step["disposition"] == "SKIPPED" for step in report["steps"])
    assert not (tmp_path / "goon.lock").exists()


def test_cli_preview_bad_plan_fails_before_runtime_or_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    plan = tmp_path / "invalid.yml"
    plan.write_text("version: 1\nsteps: invalid\n", encoding="utf-8")
    original = plan.read_bytes()
    assert main(["plan", "preview", str(plan), str(tmp_path), "--json"]) == 2
    assert capsys.readouterr().out
    assert plan.read_bytes() == original
    assert not (tmp_path / "goon.lock").exists()


def test_cli_preview_rejects_unknown_input_via_original_binder_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    assert main([
        "plan", "preview", str(_source()), str(tmp_path),
        "--input", "not_declared=value", "--json",
    ]) == 2
    assert capsys.readouterr().out
    assert not (tmp_path / "goon.lock").exists()


def test_original_service_preview_delegates_to_one_canonical_resolver(tmp_path: Path) -> None:
    resolver = _resolver()
    service = FleetPlanService(resolver, None, None, None, None)
    source = _source()
    assert service.preview(source, tmp_path) == resolver.preview(source, tmp_path)


def test_original_service_preview_delegates_once_without_second_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _resolver()
    service = FleetPlanService(resolver, None, None, None, None)
    expected = resolver.preview(_source(), tmp_path)
    seen: list[tuple[Path, Path]] = []

    def original_owner(plan_path, repository, *, extensions_path=None, inputs=None):
        seen.append((plan_path, repository))
        return expected

    monkeypatch.setattr(resolver, "preview", original_owner)
    monkeypatch.setattr(service, "resolve", lambda *_a, **_kw: pytest.fail("duplicate resolver"))
    assert service.preview(_source(), tmp_path) == expected
    assert seen == [(_source(), tmp_path)]
