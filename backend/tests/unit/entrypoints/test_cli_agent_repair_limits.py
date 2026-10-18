"""Malformed repair authority is refused before reading caller input files."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command


@pytest.mark.parametrize(
    "limits",
    [
        ["--repair-attempts", "2"],
        ["--repair-attempts", "0", "--repair-timeout", "30", "--repair-step-timeout", "10"],
        ["--repair-attempts", "2", "--repair-timeout", "nan", "--repair-step-timeout", "10"],
        [
            "--repair-attempts",
            "2",
            "--repair-timeout",
            "30",
            "--repair-step-timeout",
            "10",
            "--inputs-file",
            "/must-not-read/inputs.yml",
        ],
    ],
)
def test_invalid_budget_or_file_authority_is_refused_before_source_access(monkeypatch, limits):
    args = build_parser().parse_args(
        [
            "plan",
            "run",
            "/must-not-read/goon.yml",
            "--repository-id",
            "selected-repository",
            "--standard-local",
            "--wait",
            "--agent-repository-root",
            "/authorized/repos",
            "--agent-skill-root",
            "/authorized/skills",
            "--allow-agent-acceptance",
            "review",
            *limits,
        ]
    )
    runtime = SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=lambda _: None))
    monkeypatch.setattr(
        "oh_goon.cli_runtime_plans.plan_inputs",
        lambda *_: pytest.fail("invalid repair authority reached caller source reading"),
    )
    assert run_plan_command(runtime, args) == 2
