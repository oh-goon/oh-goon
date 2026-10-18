from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_league import run_league_command
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


def test_team_run_cli_waits_for_terminal_run_and_returns_success(tmp_path: Path, capsys) -> None:
    repository = tmp_path / "api"
    args = build_parser().parse_args(["team", "run", str(repository), "--fleet-run-id", "run-1"])
    admitted = SimpleNamespace(fleet_run_id="run-1")
    terminal = SimpleNamespace(
        outcome=FleetRunOutcome.SUCCESS,
        run=SimpleNamespace(fleet_run_id="run-1", status=FleetRunStatus.COMPLETED),
    )
    runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc123")))
        ),
        goon=SimpleNamespace(run=Mock(return_value=admitted)),
        runs=SimpleNamespace(wait_for_terminal=Mock(return_value=terminal)),
    )

    result = run_league_command(runtime, args)

    assert result == 0
    runtime.goon.run.assert_called_once_with(
        repository,
        expected_pipeline_sha256="abc123",
        inputs={},
        fleet_run_id="run-1",
        requested_priority=SchedulingPriority.NORMAL,
    )
    runtime.runs.wait_for_terminal.assert_called_once_with("run-1")
    assert "Status: COMPLETED" in capsys.readouterr().out


def test_team_run_cli_returns_nonzero_for_failed_run(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    args = build_parser().parse_args(["team", "run", str(repository)])
    admitted = SimpleNamespace(fleet_run_id="run-2")
    terminal = SimpleNamespace(
        outcome=FleetRunOutcome.FAILED,
        run=SimpleNamespace(fleet_run_id="run-2", status=FleetRunStatus.FAILED),
    )
    runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc123")))
        ),
        goon=SimpleNamespace(run=Mock(return_value=admitted)),
        runs=SimpleNamespace(wait_for_terminal=Mock(return_value=terminal)),
    )

    assert run_league_command(runtime, args) == 1


def test_team_run_cli_defaults_to_current_path() -> None:
    args = build_parser().parse_args(["team", "run"])
    assert args.repository == Path()


def test_team_validate_cli_defaults_to_current_path() -> None:
    args = build_parser().parse_args(["team", "validate"])
    assert args.repository == Path()


def test_team_run_cli_ctrl_c_requests_durable_cancel(capsys) -> None:
    args = build_parser().parse_args(["team", "run"])
    admitted = SimpleNamespace(fleet_run_id="run-cancel")
    terminal = SimpleNamespace(
        outcome=FleetRunOutcome.CANCELLED,
        run=SimpleNamespace(fleet_run_id="run-cancel", status=FleetRunStatus.CANCELLED),
    )
    wait = Mock(side_effect=[KeyboardInterrupt(), terminal])
    cancel = Mock(return_value=SimpleNamespace(fleet_run_id="run-cancel"))
    runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc123")))
        ),
        goon=SimpleNamespace(run=Mock(return_value=admitted)),
        runs=SimpleNamespace(wait_for_terminal=wait, cancel=cancel),
    )

    assert run_league_command(runtime, args) == 130
    cancel.assert_called_once_with("run-cancel")
    assert wait.call_count == 2
    assert "Cancellation requested: run-cancel" in capsys.readouterr().out


def test_shared_plan_templates_cli_uses_canonical_template_fields(capsys) -> None:
    args = build_parser().parse_args(["shared-plan", "templates"])
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            templates=Mock(return_value=SimpleNamespace(templates=(
                SimpleNamespace(
                    template_key="python/quality",
                    template_path="templates/plans/python/quality/plan.yml",
                    description="Quality checks",
                    sha256="a" * 64,
                ),
            )))
        )
    )

    assert run_league_command(runtime, args) == 0
    output = capsys.readouterr().out
    assert "python/quality" in output
    assert "templates/plans/python/quality/plan.yml" in output


def test_shared_plan_from_template_cli_binds_catalog_sha_and_destination(capsys) -> None:
    args = build_parser().parse_args([
        "shared-plan", "from-template", "python/quality", "shared-quality"
    ])
    document = SimpleNamespace(plan_key="shared-quality", sha256="c" * 64)
    create = Mock(return_value=document)
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            templates=Mock(return_value=SimpleNamespace(templates=(
                SimpleNamespace(template_key="python/quality", sha256="b" * 64),
            ))),
            create_from_template=create,
        )
    )

    assert run_league_command(runtime, args) == 0
    create.assert_called_once_with(
        "python/quality", "b" * 64, plan_key="shared-quality"
    )
    assert "Saved: shared-quality/plan.yml" in capsys.readouterr().out
