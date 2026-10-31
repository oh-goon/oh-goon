from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from oh_goon.cli import main
from oh_goon.workflow import APP_PLAN, SELF_VERIFY_PLAN


def test_self_verify_plan_json_exposes_canonical_fleet_selector(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[4]
    monkeypatch.chdir(root)
    assert main(["self", "verify", "--plan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload.pop("_cli") == {
        "schema": "oh-goon.cli/v1",
        "exit_code": 0,
        "exit_kind": "SUCCESS",
    }
    assert payload == {
        "command": "oh-goon self verify",
        "execution_authority": "FleetPlanService.run",
        "pipeline": ["resolve", "lock", "admit", "execute", "evidence"],
        "plan": SELF_VERIFY_PLAN,
    }


def test_self_certify_plan_selects_existing_app_certification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[4]
    monkeypatch.chdir(root)
    assert main(["self", "certify", "--plan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"] == APP_PLAN
    assert payload["execution_authority"] == "FleetPlanService.run"


def test_dev_shards_json_remains_diagnostic_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[4]
    monkeypatch.chdir(root)
    assert main(["dev", "shards", "scripts-non-certification", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["surface"] == "scripts-non-certification"
    assert payload["assigned"] == payload["test_count"]
    assert payload["duplicates"] == 0
    assert payload["missing"] == 0


def test_self_setup_reports_checkpoint_parent_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from oh_goon.repository_tooling import self_certification_artifacts

    root = Path(__file__).resolve().parents[4]
    parent = {
        "schema": 1,
        "version": "7.8.9",
        "artifact": "oh-goon-7.8.9.zip",
        "sha256": "1" * 64,
    }
    metadata = tmp_path / "checkpoint-parent.json"
    metadata.write_text(json.dumps(parent), encoding="utf-8")
    monkeypatch.setattr(self_certification_artifacts, "PARENT_METADATA", metadata)
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert main(["self", "setup"]) == 0
    output = capsys.readouterr().out
    assert "parent-source-zip: MISSING" in output
    assert cast(str, parent["artifact"]) in output
    assert cast(str, parent["sha256"]) in output


def test_self_verify_plan_output_is_compact_and_names_normal_authority(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[4]
    monkeypatch.chdir(root)
    assert main(["self", "verify", "--plan"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("SELF VERIFY\n")
    assert SELF_VERIFY_PLAN in output
    assert "FleetPlanService.run" in output
    assert "resolve -> lock -> admit -> execute -> evidence" in output
    assert "--run-unit" not in output


def test_self_parser_rejects_removed_run_unit_escape_hatch() -> None:
    try:
        main(["self", "verify", "--run-unit", "architecture"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--run-unit must not remain a public self-verification execution path")
