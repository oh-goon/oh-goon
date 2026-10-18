from pathlib import Path

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.core.models.scheduling import SchedulingPriority


def test_shared_plan_preview_accepts_exactly_one_selection_source() -> None:
    parser = build_parser()
    lineup = parser.parse_args(["shared-plan", "preview", "quality", "--lineup", "backend"])
    teams = parser.parse_args(
        ["shared-plan", "run", "quality", "--team", "/league/a", "--team", "/league/b"]
    )
    entire_league = parser.parse_args(["shared-plan", "run", "trivy-audit", "--all-teams"])

    assert lineup.lineup_key == "backend"
    assert lineup.teams is None
    assert teams.lineup_key is None
    assert teams.teams == [Path("/league/a"), Path("/league/b")]
    assert entire_league.all_teams is True

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["shared-plan", "run", "quality", "--lineup", "backend", "--team", "/league/a"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["shared-plan", "run", "quality", "--all-teams", "--team", "/league/a"])


def test_goon_templates_use_template_vocabulary_and_stable_keys() -> None:
    parser = build_parser()

    catalog = parser.parse_args(["shared-plan", "templates"])
    create = parser.parse_args(["shared-plan", "from-template", "python-quality", "shared-quality"])

    assert catalog.shared_plan_command == "templates"
    assert create.shared_plan_command == "from-template"
    assert create.template_key == "python-quality"
    assert create.plan_key == "shared-quality"

    with pytest.raises(SystemExit):
        parser.parse_args(["shared-plan", "examples"])
    with pytest.raises(SystemExit):
        parser.parse_args(["shared-plan", "copy-example", "python-quality"])


def test_lineup_save_accepts_explicit_expected_revision() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "lineup",
            "save",
            "backend",
            "--name",
            "Backend",
            "--team",
            "/league/api",
            "--expected-revision",
            "a" * 64,
        ]
    )
    assert args.expected_revision == "a" * 64


def test_shared_plan_save_accepts_explicit_expected_revision() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "shared-plan",
            "save",
            "python-quality",
            "/tmp/plan.yml",
            "--expected-revision",
            "b" * 64,
        ]
    )
    assert args.expected_revision == "b" * 64


def test_gitlab_import_accepts_explicit_expected_output_revision() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "goon",
            "import-gitlab",
            ".gitlab-ci.yml",
            "--output",
            "fleet.yml",
            "--expected-revision",
            "c" * 64,
        ]
    )
    assert args.expected_revision == "c" * 64


def test_run_surfaces_parse_canonical_priority() -> None:
    parser = build_parser()
    shared = parser.parse_args(["shared-plan", "run", "quality", "--all-teams", "--priority", "HIGH"])
    team = parser.parse_args(["team", "run", "--priority", "LOW"])
    plan = parser.parse_args(["plan", "run", "plan.yml", "--all-repositories", "--priority", "BACKGROUND"])

    assert shared.priority is SchedulingPriority.HIGH
    assert team.priority is SchedulingPriority.LOW
    assert plan.priority is SchedulingPriority.BACKGROUND
