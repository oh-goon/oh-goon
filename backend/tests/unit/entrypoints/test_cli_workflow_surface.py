"""The public workflow surface reuses original owners and refuses retired ingress."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.cli_parser import build_parser
from oh_goon.cli_plan_summary import captured_workflow_summary
from oh_goon.core.models.tool_health import ReadyToolHealth
from oh_goon.core.vocabulary import ToolStatus
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.system_health import SystemHealthService


@pytest.mark.parametrize(
    "argv",
    [
        ["config", "inspect", "run.yml"],
        ["lock", "run.yml"],
        ["templates", "init", "python/quality"],
        ["plan", "status", "run-id"],
        ["plan", "logs", "run-id"],
        ["start"],
        ["status"],
        ["stop"],
        ["serve"],
    ],
)
def test_retired_public_ingress_is_refused_before_runtime(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Invalid public ingress must not initialize the runtime")

    monkeypatch.setattr("oh_goon.cli._build_application_runtime", forbidden)
    with pytest.raises(SystemExit) as refusal:
        main(argv)
    assert refusal.value.code == 2


def test_workflow_flags_keep_independent_refresh_and_authorization() -> None:
    parser = build_parser()
    prepared = parser.parse_args(
        [
            "workflow",
            "prepare",
            ".",
            "--refresh",
            "goons",
            "--refresh",
            "applications",
            "--register-target",
            "--json",
        ]
    )
    assert prepared.configuration == Path(".")
    assert prepared.refresh == ["goons", "applications"]
    assert prepared.register_target and prepared.as_json
    selected = parser.parse_args(["run", ".", "--no-input", "--yes"])
    assert selected.no_input and selected.yes
    assert selected.authorize_repair is False
    initial = parser.parse_args(
        [
            "init",
            "--template",
            "python/quality",
            "--repository",
            ".",
            "--input",
            "label=null",
            "--input-null",
            "optional",
            "--no-input",
        ]
    )
    assert initial.kind == "plan" and initial.key == "python/quality"
    assert initial.input_values == ["label=null"] and initial.input_nulls == ["optional"]


def test_basic_doctor_uses_native_tool_owner_without_application_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "corrupt.sqlite"
    database.write_bytes(b"deliberately invalid SQLite")
    monkeypatch.setenv("OH_GOON_DB", str(database))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Basic doctor must not initialize the application")

    monkeypatch.setattr("oh_goon.cli._build_application_runtime", forbidden)
    monkeypatch.setattr(
        SystemHealthService,
        "inspect_tools",
        classmethod(
            lambda cls: (
                ReadyToolHealth(
                    name="git", required=True, status=ToolStatus.READY, version="git fixture"
                ),
                ReadyToolHealth(
                    name="uv", required=True, status=ToolStatus.READY, version="uv fixture"
                ),
            )
        ),
    )
    assert main(["doctor", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "tools" and result["status"] == "ready"
    assert [tool["name"] for tool in result["tools"]] == ["git", "uv"]
    assert database.read_bytes() == b"deliberately invalid SQLite"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["corrupt.sqlite"]


def test_declared_effect_presentation_uses_captured_graph_without_echoing_command_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "goon.yml"
    source.write_text("steps:\n- run: echo PRIVATE_ARGUMENT_VALUE\n")
    graph = FleetPlanResolver().analyze_source(source)
    source.unlink()
    summary = "\n".join(captured_workflow_summary(graph))
    assert "Execution purpose: verification" in summary
    assert "network=none" in summary
    assert "repository=execution workspace" in summary
    assert "PRIVATE_ARGUMENT_VALUE" not in summary


@pytest.mark.parametrize(
    "failure,code",
    [
        (
            DatabasePathIdentityLost("PRIVATE_DATABASE_PATH identity changed"),
            "RUNTIME_DATABASE_IDENTITY_LOST",
        ),
        (
            sqlite3.OperationalError("database or disk is full: PRIVATE_DATABASE_PATH"),
            "RUNTIME_STORAGE_UNAVAILABLE",
        ),
    ],
)
def test_explicit_registration_reports_original_storage_failure_as_one_safe_json_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    code: str,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    (repository / "goon.yml").write_text("steps:\n- run: echo ready\n")
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    configuration = workflow / "run.yml"
    configuration.write_text(
        "version: 2\nsource: {kind: repository-goon}\ntarget: {repository: ../repository}\n"
    )
    league = tmp_path / "league.yml"
    league.write_text("version: 1\nfolders: []\n")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    database = tmp_path / "state.db"
    monkeypatch.setenv("OH_GOON_DB", str(database))

    def fail(**_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr("oh_goon.application.ApplicationRuntime.build_registration_service", fail)
    assert main(["workflow", "prepare", str(workflow), "--register-target", "--json"]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["code"] == code and report["target_registration"] is None
    assert "PRIVATE_DATABASE_PATH" not in captured.out and captured.err == ""
    assert not database.exists() and not (workflow / "goon.lock").exists()
    assert league.read_text() == "version: 1\nfolders: []\n"
