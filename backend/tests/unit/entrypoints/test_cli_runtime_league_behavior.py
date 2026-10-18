from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_league import run_league_command
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


class _Json:
    def __init__(self, text: str = '{"ok": true}', **values) -> None:
        self._text = text
        self.__dict__.update(values)

    def model_dump_json(self, indent: int = 2) -> str:
        return self._text


def _args(*parts: str):
    return build_parser().parse_args(list(parts))


def test_league_add_folder_success_and_domain_failure(tmp_path: Path, capsys) -> None:
    path = tmp_path / "repos"
    runtime = SimpleNamespace(locations=SimpleNamespace(add=Mock(return_value=(path,))))
    assert run_league_command(runtime, _args("league", "add-folder", str(path))) == 0
    output = capsys.readouterr().out
    assert "League folder ready" in output
    assert "Configured League folders: 1" in output

    runtime.locations.add.side_effect = ValueError("not a repository root")
    assert run_league_command(runtime, _args("league", "add-folder", str(path))) == 2
    assert "not a repository root" in capsys.readouterr().out


def test_league_scan_text_and_json_cover_pipeline_states(capsys) -> None:
    teams = (
        SimpleNamespace(
            repository=SimpleNamespace(name="configured"),
            pipeline=SimpleNamespace(state="CONFIGURED", sha256="abc", reason=None),
        ),
        SimpleNamespace(
            repository=SimpleNamespace(name="invalid"),
            pipeline=SimpleNamespace(state="INVALID", sha256=None, reason="broken"),
        ),
        SimpleNamespace(
            repository=SimpleNamespace(name="missing"),
            pipeline=SimpleNamespace(state="MISSING", sha256=None, reason=None),
        ),
    )
    league = _Json(
        '{"summary": "json"}',
        summary=SimpleNamespace(
            folders=1,
            git_repositories=3,
            configured_teams=1,
            missing_goon_yml=1,
            invalid_goon_yml=1,
        ),
        teams=teams,
    )
    runtime = SimpleNamespace(league=SimpleNamespace(map=Mock(return_value=league)))

    assert run_league_command(runtime, _args("league", "scan")) == 0
    output = capsys.readouterr().out
    assert "configured: CONFIGURED sha256=abc" in output
    assert "invalid: INVALID reason=broken" in output
    assert "missing: MISSING" in output

    assert run_league_command(runtime, _args("league", "scan", "--json")) == 0
    assert '{"summary": "json"}' in capsys.readouterr().out


def test_team_validate_text_json_and_domain_error(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    inspection = SimpleNamespace(
        team=SimpleNamespace(repository=SimpleNamespace(path=repo)),
        preview=_Json(
            '{"preview": true}',
            plan_id="plan-1",
            steps=(
                SimpleNamespace(step_id="a", disposition=SimpleNamespace(value="RUN"), reason=None),
                SimpleNamespace(
                    step_id="b",
                    disposition=SimpleNamespace(value="SKIP"),
                    reason="condition false",
                ),
            ),
        ),
    )
    runtime = SimpleNamespace(goon=SimpleNamespace(inspect=Mock(return_value=inspection)))

    assert run_league_command(runtime, _args("team", "validate", str(repo))) == 0
    output = capsys.readouterr().out
    assert "Plan ID: plan-1" in output
    assert "a: RUN" in output
    assert "b: SKIP — condition false" in output

    assert run_league_command(runtime, _args("team", "validate", str(repo), "--json")) == 0
    assert '{"preview": true}' in capsys.readouterr().out

    runtime.goon.inspect.side_effect = FleetPlanInvalid("invalid team")
    assert run_league_command(runtime, _args("team", "validate", str(repo))) == 2
    assert "invalid team" in capsys.readouterr().out


def test_team_run_interrupt_before_admission_and_cancel_failure(capsys) -> None:
    args = _args("team", "run")
    runtime = SimpleNamespace(
        league=SimpleNamespace(configured_team=Mock(side_effect=KeyboardInterrupt())),
        goon=SimpleNamespace(run=Mock()),
        runs=SimpleNamespace(),
    )
    assert run_league_command(runtime, args) == 130
    assert "interrupted before admission" in capsys.readouterr().out

    admitted = SimpleNamespace(fleet_run_id="run-1")
    runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(
                return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc"))
            )
        ),
        goon=SimpleNamespace(run=Mock(return_value=admitted)),
        runs=SimpleNamespace(
            wait_for_terminal=Mock(side_effect=KeyboardInterrupt()),
            cancel=Mock(side_effect=FleetPlanInvalid("cancel refused")),
        ),
    )
    assert run_league_command(runtime, args) == 130
    assert "Cancellation could not be confirmed: cancel refused" in capsys.readouterr().out


def test_team_run_json_and_admission_error(capsys) -> None:
    args = _args("team", "run", "--json")
    detail = _Json(
        '{"run": "done"}',
        outcome=FleetRunOutcome.SUCCESS,
        run=SimpleNamespace(status=FleetRunStatus.COMPLETED),
    )
    runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(
                return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc"))
            )
        ),
        goon=SimpleNamespace(run=Mock(return_value=SimpleNamespace(fleet_run_id="run-1"))),
        runs=SimpleNamespace(wait_for_terminal=Mock(return_value=detail)),
    )
    assert run_league_command(runtime, args) == 0
    assert '{"run": "done"}' in capsys.readouterr().out

    runtime.league.configured_team.side_effect = ValueError("not configured")
    assert run_league_command(runtime, args) == 2
    assert "not configured" in capsys.readouterr().out


def test_shared_plan_list_text_and_json_include_history_state(capsys) -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    catalog = _Json(
        '{"plans": []}',
        plans=(
            SimpleNamespace(
                plan_key="never",
                sha256="a" * 64,
                last_run=None,
                changed_since_last_run=False,
            ),
            SimpleNamespace(
                plan_key="changed",
                sha256="b" * 64,
                last_run=SimpleNamespace(accepted_at=now, status=SimpleNamespace(value="FAILED")),
                changed_since_last_run=True,
            ),
        ),
    )
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(root=Path("/plans"), catalog=Mock(return_value=catalog))
    )
    assert run_league_command(runtime, _args("shared-plan", "list")) == 0
    output = capsys.readouterr().out
    assert "never run" in output
    assert "changed-since-last-run" in output

    assert run_league_command(runtime, _args("shared-plan", "list", "--json")) == 0
    assert '{"plans": []}' in capsys.readouterr().out


def test_shared_plan_templates_history_and_save_paths(tmp_path: Path, capsys) -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    templates = _Json(
        '{"templates": []}',
        templates=(
            SimpleNamespace(
                template_key="python/quality", template_path="path/plan.yml", description="Quality"
            ),
        ),
    )
    empty_history = _Json('{"history": []}', plan_key="plan", current_sha256="a" * 64, runs=())
    run_history = _Json(
        '{"history": [1]}',
        plan_key="plan",
        current_sha256="a" * 64,
        runs=(
            SimpleNamespace(
                accepted_at=now,
                status=SimpleNamespace(value="COMPLETED"),
                team_count=2,
                fleet_run_id="run-current",
                source_sha256="a" * 64,
            ),
            SimpleNamespace(
                accepted_at=now,
                status=SimpleNamespace(value="FAILED"),
                team_count=1,
                fleet_run_id="run-old",
                source_sha256="b" * 64,
            ),
        ),
    )
    source = tmp_path / "plan.yml"
    source.write_text("version: 1\n", encoding="utf-8")
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            templates=Mock(return_value=templates),
            history=Mock(side_effect=[empty_history, run_history, run_history]),
            save=Mock(return_value=SimpleNamespace(plan_key="saved", sha256="c" * 64)),
        )
    )

    assert run_league_command(runtime, _args("shared-plan", "templates")) == 0
    assert "Quality" in capsys.readouterr().out
    assert run_league_command(runtime, _args("shared-plan", "templates", "--json")) == 0
    assert '{"templates": []}' in capsys.readouterr().out

    assert run_league_command(runtime, _args("shared-plan", "history", "plan")) == 0
    assert "No FleetRun history" in capsys.readouterr().out
    assert run_league_command(runtime, _args("shared-plan", "history", "plan")) == 0
    output = capsys.readouterr().out
    assert "CURRENT" in output and "OLDER REVISION" in output
    assert run_league_command(runtime, _args("shared-plan", "history", "plan", "--json")) == 0
    assert '{"history": [1]}' in capsys.readouterr().out

    assert run_league_command(runtime, _args("shared-plan", "save", "saved", str(source))) == 0
    assert "Saved: saved/plan.yml" in capsys.readouterr().out


def test_shared_plan_from_template_unknown_is_domain_safe(capsys) -> None:
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(templates=Mock(return_value=SimpleNamespace(templates=())))
    )
    assert (
        run_league_command(runtime, _args("shared-plan", "from-template", "missing", "saved")) == 2
    )
    assert "Unknown Plan template" in capsys.readouterr().out


def _preview(ready: int, blocked: int) -> _Json:
    teams = (
        SimpleNamespace(
            state="READY",
            repository=SimpleNamespace(name="ready"),
            preview=SimpleNamespace(plan_id="plan-ready"),
            reason=None,
        ),
        SimpleNamespace(
            state="BLOCKED",
            repository=SimpleNamespace(name="blocked"),
            preview=None,
            reason="invalid input",
        ),
    )
    return _Json(
        '{"preview": true}',
        plan_key="shared",
        sha256="a" * 64,
        ready_count=ready,
        blocked_count=blocked,
        teams=teams,
    )


def test_shared_plan_preview_text_json_and_repository_selection(capsys) -> None:
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            preview=Mock(side_effect=[_preview(1, 1), _preview(2, 0), _preview(2, 0)]),
            all_repository_ids=Mock(return_value={"all"}),
            repository_ids=Mock(return_value={"explicit"}),
        ),
        lineups=SimpleNamespace(repository_ids=Mock(return_value={"lineup"})),
    )
    assert (
        run_league_command(runtime, _args("shared-plan", "preview", "shared", "--team", "/repo"))
        == 1
    )
    output = capsys.readouterr().out
    assert "READY   ready" in output
    assert "BLOCKED blocked: invalid input" in output

    assert (
        run_league_command(
            runtime, _args("shared-plan", "preview", "shared", "--all-teams", "--json")
        )
        == 0
    )
    assert '{"preview": true}' in capsys.readouterr().out

    assert run_league_command(
        runtime, _args("shared-plan", "preview", "shared", "--lineup", "release")
    ) in {0, 1}
    runtime.lineups.repository_ids.assert_called_with("release")


def test_shared_plan_run_text_json_interrupt_and_terminal_failure(capsys) -> None:
    completed = SimpleNamespace(
        outcome=FleetRunOutcome.SUCCESS, run=SimpleNamespace(status=FleetRunStatus.COMPLETED)
    )
    failed = SimpleNamespace(
        outcome=FleetRunOutcome.FAILED, run=SimpleNamespace(status=FleetRunStatus.FAILED)
    )
    admitted = SimpleNamespace(fleet_run_id="run-1")
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            repository_ids=Mock(return_value={"repo"}),
            preview=Mock(
                return_value=SimpleNamespace(
                    blocked_count=0,
                    plan_id="reviewed-plan",
                    execution_digest="sha256:" + "d" * 64,
                    sha256="a" * 64,
                    teams=(
                        SimpleNamespace(
                            repository=SimpleNamespace(repository_id="repo"),
                            repository_instance_id="instance-repo",
                        ),
                    ),
                )
            ),
            run=Mock(side_effect=[admitted, admitted, admitted, KeyboardInterrupt()]),
        ),
        runs=SimpleNamespace(
            wait_for_terminal=Mock(side_effect=[completed, failed, KeyboardInterrupt()])
        ),
    )

    assert (
        run_league_command(runtime, _args("shared-plan", "run", "shared", "--team", "/repo")) == 0
    )
    assert "Status: COMPLETED" in capsys.readouterr().out
    assert (
        run_league_command(runtime, _args("shared-plan", "run", "shared", "--team", "/repo")) == 1
    )

    runtime.runs.cancel = Mock(side_effect=FleetPlanInvalid("cancel failed"))
    assert (
        run_league_command(runtime, _args("shared-plan", "run", "shared", "--team", "/repo")) == 130
    )
    assert "Cancellation could not be confirmed" in capsys.readouterr().out

    assert (
        run_league_command(runtime, _args("shared-plan", "run", "shared", "--team", "/repo")) == 130
    )
    assert "interrupted before admission" in capsys.readouterr().out


def test_shared_plan_run_blocked_preview_is_domain_safe_and_never_starts_work(capsys) -> None:
    run = Mock()
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            repository_ids=Mock(return_value={"repo"}),
            preview=Mock(return_value=SimpleNamespace(blocked_count=1)),
            run=run,
        ),
    )

    assert (
        run_league_command(runtime, _args("shared-plan", "run", "shared", "--team", "/repo")) == 2
    )
    assert "preview blocked 1 selected Team(s)" in capsys.readouterr().out
    run.assert_not_called()


def test_shared_plan_dispatch_converts_domain_and_os_errors(capsys) -> None:
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(catalog=Mock(side_effect=OSError("disk")))
    )
    assert run_league_command(runtime, _args("shared-plan", "list")) == 2
    assert "disk" in capsys.readouterr().out


def test_lineup_list_save_inspect_text_json_and_error(capsys) -> None:
    catalog = _Json(
        '{"lineups": []}',
        lineups=(SimpleNamespace(lineup_key="release", name="Release", team_count=2),),
    )
    document = _Json('{"lineup": "saved"}', lineup_key="release", team_count=2, revision="a" * 64)
    ready = _Json(
        '{"inspect": "ready"}',
        name="Release",
        ready_count=1,
        unavailable_count=1,
        revision="b" * 64,
        teams=(
            SimpleNamespace(
                state="READY", repository=SimpleNamespace(name="api"), configured_path="/api"
            ),
            SimpleNamespace(state="UNAVAILABLE", repository=None, configured_path="/missing"),
        ),
    )
    runtime = SimpleNamespace(
        lineups=SimpleNamespace(
            root=Path("/lineups"),
            catalog=Mock(return_value=catalog),
            save=Mock(return_value=document),
            inspect=Mock(return_value=ready),
        )
    )
    assert run_league_command(runtime, _args("lineup", "list")) == 0
    assert "release: Release teams=2" in capsys.readouterr().out
    assert run_league_command(runtime, _args("lineup", "list", "--json")) == 0
    assert '{"lineups": []}' in capsys.readouterr().out

    assert (
        run_league_command(
            runtime,
            _args(
                "lineup", "save", "release", "--name", "Release", "--team", "/api", "--team", "/web"
            ),
        )
        == 0
    )
    assert "Saved Lineup: release" in capsys.readouterr().out
    assert (
        run_league_command(
            runtime,
            _args("lineup", "save", "release", "--name", "Release", "--team", "/api", "--json"),
        )
        == 0
    )
    assert '{"lineup": "saved"}' in capsys.readouterr().out

    assert run_league_command(runtime, _args("lineup", "inspect", "release")) == 1
    output = capsys.readouterr().out
    assert "READY       api" in output
    assert "UNAVAILABLE /missing" in output
    assert run_league_command(runtime, _args("lineup", "inspect", "release", "--json")) == 1
    assert '{"inspect": "ready"}' in capsys.readouterr().out

    runtime.lineups.catalog.side_effect = ValueError("bad lineup")
    assert run_league_command(runtime, _args("lineup", "list")) == 2
    assert "bad lineup" in capsys.readouterr().out


def test_non_league_command_is_not_claimed() -> None:
    assert run_league_command(SimpleNamespace(), SimpleNamespace(command="other")) is None


def test_terminal_cli_success_uses_outcome_not_completed_lifecycle_status(capsys) -> None:
    attention = SimpleNamespace(
        outcome=FleetRunOutcome.ATTENTION_REQUIRED,
        run=SimpleNamespace(status=FleetRunStatus.COMPLETED),
    )
    admitted = SimpleNamespace(fleet_run_id="run-attention")
    team_runtime = SimpleNamespace(
        league=SimpleNamespace(
            configured_team=Mock(
                return_value=SimpleNamespace(pipeline=SimpleNamespace(sha256="abc"))
            )
        ),
        goon=SimpleNamespace(run=Mock(return_value=admitted)),
        runs=SimpleNamespace(wait_for_terminal=Mock(return_value=attention)),
    )

    assert run_league_command(team_runtime, _args("team", "run")) == 1
    output = capsys.readouterr().out
    assert "Status: COMPLETED" in output
    assert "Outcome: ATTENTION_REQUIRED" in output

    shared_runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            repository_ids=Mock(return_value={"repo"}),
            preview=Mock(
                return_value=SimpleNamespace(
                    blocked_count=0,
                    plan_id="reviewed-plan",
                    execution_digest="sha256:" + "d" * 64,
                    sha256="a" * 64,
                    teams=(
                        SimpleNamespace(
                            repository=SimpleNamespace(repository_id="repo"),
                            repository_instance_id="instance-repo",
                        ),
                    ),
                )
            ),
            run=Mock(return_value=admitted),
        ),
        runs=SimpleNamespace(wait_for_terminal=Mock(return_value=attention)),
    )

    assert (
        run_league_command(
            shared_runtime,
            _args("shared-plan", "run", "shared", "--team", "/repo"),
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "Status: COMPLETED" in output
    assert "Outcome: ATTENTION_REQUIRED" in output
