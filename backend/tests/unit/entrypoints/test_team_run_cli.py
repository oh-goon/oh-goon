from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_league import run_league_command


def test_team_validate_cli_defaults_to_current_path() -> None:
    args = build_parser().parse_args(["team", "validate"])
    assert args.repository == Path()


def test_shared_plan_templates_cli_uses_canonical_template_fields(capsys) -> None:
    args = build_parser().parse_args(["shared-plan", "templates"])
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            templates=Mock(
                return_value=SimpleNamespace(
                    templates=(
                        SimpleNamespace(
                            template_key="python/quality",
                            template_path="templates/plans/python/quality/plan.yml",
                            description="Quality checks",
                            sha256="a" * 64,
                        ),
                    )
                )
            )
        )
    )

    assert run_league_command(runtime, args) == 0
    output = capsys.readouterr().out
    assert "python/quality" in output
    assert "templates/plans/python/quality/plan.yml" in output


def test_shared_plan_from_template_cli_binds_catalog_sha_and_destination(capsys) -> None:
    args = build_parser().parse_args(
        ["shared-plan", "from-template", "python/quality", "shared-quality"]
    )
    document = SimpleNamespace(plan_key="shared-quality", sha256="c" * 64)
    create = Mock(return_value=document)
    runtime = SimpleNamespace(
        shared_plan=SimpleNamespace(
            templates=Mock(
                return_value=SimpleNamespace(
                    templates=(SimpleNamespace(template_key="python/quality", sha256="b" * 64),)
                )
            ),
            create_from_template=create,
        )
    )

    assert run_league_command(runtime, args) == 0
    create.assert_called_once_with("python/quality", "b" * 64, plan_key="shared-quality")
    assert "Saved: shared-quality/plan.yml" in capsys.readouterr().out
