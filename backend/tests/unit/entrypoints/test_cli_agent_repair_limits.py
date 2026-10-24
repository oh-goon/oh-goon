"""Malformed persisted repair selections cannot grant fresh Run authority."""

from __future__ import annotations

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.core.models.repository import RepositoryIdentity


@pytest.mark.parametrize(
    "repair",
    [
        {"review_passes": 2},
        {
            "review_passes": 0,
            "write_roots": ["src"],
            "timeout_seconds": 30,
            "step_timeout_seconds": 10,
        },
        {
            "review_passes": 2,
            "write_roots": ["../outside"],
            "timeout_seconds": 30,
            "step_timeout_seconds": 10,
        },
        {"review_passes": 2, "write_roots": [], "timeout_seconds": 30, "step_timeout_seconds": 10},
        {
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": float("nan"),
            "step_timeout_seconds": 10,
        },
        {
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": 30,
            "step_timeout_seconds": True,
        },
    ],
)
def test_invalid_budget_or_write_scope_refused_before_runtime(tmp_path, monkeypatch, repair):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("invalid repair reached work")
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ngoons: [python/quality]\n")
    source = tmp_path / "run.yml"
    source.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {
                    "repository_id": RepositoryIdentity.model_validate(
                        {"path": repository}
                    ).repository_id
                },
                "agent": {
                    "implementation": "pi",
                    "profile": "isolated-managed",
                    "acceptance": "review",
                    "repository_root": str(tmp_path),
                    "skill_root": ".",
                },
                "repair": repair,
            }
        )
    )
    assert main(["run", str(source), "--authorize-repair"]) == 2


def test_persisted_repair_scope_needs_fresh_action_before_native_or_runtime_effects(
    tmp_path, monkeypatch
):
    from tests.factories.run_configuration import write_run_configuration

    source = write_run_configuration(
        tmp_path,
        monkeypatch,
        source={"kind": "catalog-plan", "name": "agents/agentic-review-repair-loop"},
        agent=True,
    )
    data = yaml.safe_load(source.read_text())
    data["repair"] = {
        "review_passes": 2,
        "write_roots": ["src"],
        "timeout_seconds": 30,
        "step_timeout_seconds": 10,
    }
    source.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(
        "oh_goon.cli._build_application_runtime",
        lambda **kw: pytest.fail("persisted scope authorized runtime"),
    )
    monkeypatch.setattr(
        "oh_goon.cli_ai.native_plan_selection", lambda *a, **kw: pytest.fail("native work began")
    )
    monkeypatch.setattr(
        "oh_goon.cli_lock._generate_execution_lock",
        lambda *a, **kw: pytest.fail("lock owner began"),
    )
    assert main(["run", str(source)]) == 2


@pytest.mark.parametrize("operation", ["schedule", "reschedule"])
def test_schedule_transports_one_configuration_cut_into_original_owner(
    tmp_path, monkeypatch, operation
):
    from types import SimpleNamespace

    from oh_goon.cli_configuration import prepare_configuration
    from oh_goon.cli_parser import build_parser
    from oh_goon.cli_runtime_plans import run_plan_command
    from tests.factories.run_configuration import write_run_configuration

    source = write_run_configuration(tmp_path, monkeypatch)
    args = build_parser().parse_args(
        ["plan", operation, str(source), "--schedule-id", "daily", "--interval-minutes", "5"]
    )
    prepare_configuration(args)
    observed = []

    def capture(*values, **kwargs):
        observed.append((values, kwargs))
        return SimpleNamespace(
            schedule_id="daily",
            record_version=1,
            repository_ids=["registered-target"],
            target=SimpleNamespace(plan=SimpleNamespace(plan_id="canonical-plan")),
        )

    runtime = SimpleNamespace(plans=SimpleNamespace(**{operation: capture}))
    assert run_plan_command(runtime, args) == 0
    assert len(observed) == 1
    supplied = observed[0][1]
    assert supplied["external_artifacts"] == (("run.yml", source.read_bytes()),)
    assert supplied["lock_path"] == source.with_name("goon.lock")
    assert supplied["caller_input_file"] == args._run_configuration.snapshot.authority()
    assert supplied["repository_ids"] == {args._run_configuration.repository.repository_id}
