"""CO-AG5: agent/CI change detection uses the original resolved-meaning owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _stateless(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    def forbidden_runtime(**kwargs: object) -> None:
        pytest.fail("semantic diff must not construct application runtime")

    monkeypatch.setattr(cli, "_build_application_runtime", forbidden_runtime)


def _plans() -> tuple[Path, Path]:
    store = PlanTemplateStore()
    return store.source("python/quality").path, store.source("python/dependency-inventory").path


@pytest.mark.parametrize("as_json", [False, True])
def test_changes_report_exit_one_and_original_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    as_json: bool,
) -> None:
    _stateless(monkeypatch)
    before, after = _plans()
    expected = FleetPlanResolver(goon_templates=GoonTemplateStore()).diff(before, after)
    assert expected.changed
    argv = ["plan", "diff", str(before), str(after), "--exit-code"]
    if as_json:
        argv.append("--json")
    assert main(argv) == 1
    output = capsys.readouterr().out
    if as_json:
        actual = json.loads(output)
        assert actual.pop("_cli") == {
            "schema": "oh-goon.cli/v1",
            "exit_code": 1,
            "exit_kind": "NON_SUCCESS",
        }
        assert actual == json.loads(expected.model_dump_json())
        assert "changed" not in actual  # existing owner schema unchanged
    else:
        assert f"Before: {expected.before_plan_id}" in output
        assert f"After:  {expected.after_plan_id}" in output
    assert not (tmp_path / "goon.lock").exists()


@pytest.mark.parametrize("as_json", [False, True])
def test_unchanged_resolved_meaning_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    _stateless(monkeypatch)
    before, _ = _plans()
    argv = ["plan", "diff", str(before), str(before), "--exit-code"]
    if as_json:
        argv.append("--json")
    assert main(argv) == 0
    output = capsys.readouterr().out
    if as_json:
        result = json.loads(output)
        assert result["plan_fields"] == []
        assert result["steps"] == []
    else:
        assert "Execution meaning: unchanged" in output


def test_source_relocation_respects_original_resolved_name_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _stateless(monkeypatch)
    before, _ = _plans()
    copied = tmp_path / "quality" / "plan.yml"
    copied.parent.mkdir()
    copied.write_bytes(before.read_bytes())
    assert main(["plan", "diff", str(before), str(copied), "--exit-code", "--json"]) == 1
    diff = json.loads(capsys.readouterr().out)
    # Source outside its registered template path resolves a different name.
    # The CLI must honor that original-owner fact, not compare raw YAML bytes.
    assert diff["plan_fields"] == [{"field": "name", "before": "quality", "after": "plan"}]
    assert diff["steps"] == []


def test_backward_compatible_default_exit_even_when_changed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stateless(monkeypatch)
    before, after = _plans()
    assert main(["plan", "diff", str(before), str(after), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["steps"]


@pytest.mark.parametrize("as_json", [False, True])
def test_invalid_plan_remains_exit_two_and_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    as_json: bool,
) -> None:
    _stateless(monkeypatch)
    before, _ = _plans()
    bad = tmp_path / "bad-plan.yml"
    bad.write_text("version: 1\nsteps: invalid\n", encoding="utf-8")
    original = bad.read_bytes()
    argv = ["plan", "diff", str(before), str(bad), "--exit-code"]
    if as_json:
        argv.append("--json")
    assert main(argv) == 2
    assert capsys.readouterr().out
    assert bad.read_bytes() == original
    assert not (tmp_path / "goon.lock").exists()


def test_existing_stateful_dispatch_adapter_matches_same_exit_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Service dispatch is compatibility-only; never start a second diff implementation."""
    from argparse import Namespace

    from oh_goon.cli_plan_definition_commands import _diff
    from oh_goon.services.fleet_plans import FleetPlanService

    before, after = _plans()
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    unused = cast(Any, None)
    service = FleetPlanService(resolver, unused, unused, unused, unused)
    args = Namespace(
        before=before,
        after=after,
        extensions=None,
        plan_inputs=None,
        inputs_file=None,
        as_json=True,
        exit_code=True,
    )
    runtime = type("Runtime", (), {"plans": service})()
    assert _diff(cast(Any, runtime), args) == 1
    assert json.loads(capsys.readouterr().out) == json.loads(
        resolver.diff(before, after).model_dump_json()
    )
