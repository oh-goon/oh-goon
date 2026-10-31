"""The selected configuration enters the original lock owner without application state."""

from __future__ import annotations

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import load_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.run_configuration import load_run_configuration


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kw: pytest.fail("lock creation started application/Fleet runtime"),
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(league.parent / "test-state.db"))
    selected = tmp_path / "run.yml"
    selected.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/dependency-inventory"},
                "target": {"repository": str(repository)},
            }
        )
    )
    return selected


def test_workflow_lock_matches_original_service_without_database(workflow, tmp_path, capsys):
    snapshot = load_run_configuration(workflow)
    source = snapshot.source_path()
    expected_path = tmp_path / "reference/goon.lock"
    expected = FleetPlanService(
        FleetPlanResolver(goon_templates=GoonTemplateStore()),
        None,
        None,
        None,
        None,
    ).lock(source, output=expected_path, external_artifacts=snapshot.external_artifacts())
    assert main(["workflow", "prepare", str(workflow)]) == 0
    destination = snapshot.lock_path
    assert (
        capsys.readouterr().out
        == f"Generated: {destination}\nExecution digest: {expected.digest}\n"
    )
    assert destination.read_bytes() == expected_path.read_bytes()
    assert load_execution_lock(destination) == expected
    assert not list(tmp_path.rglob("*.sqlite*"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_invalid_source_and_input_do_not_write_or_create_database(workflow, capsys):
    payload = yaml.safe_load(workflow.read_text())
    payload["inputs"] = {"unknown": "secret-sentinel"}
    workflow.write_text(yaml.safe_dump(payload))
    assert main(["workflow", "prepare", str(workflow)]) == 2
    output = capsys.readouterr().out
    assert "secret-sentinel" not in output
    assert not workflow.with_name("goon.lock").exists()
    payload.pop("inputs")
    payload["source"] = {"kind": "file-plan", "path": "plan.yml"}
    workflow.write_text(yaml.safe_dump(payload))
    workflow.with_name("plan.yml").write_text("version: 1\nsteps: invalid\n")
    assert main(["workflow", "prepare", str(workflow)]) == 2
    assert not workflow.with_name("goon.lock").exists()


def test_original_lock_writer_is_called_once(workflow, monkeypatch):
    import oh_goon.cli_lock as cli_lock

    observed = []
    original = cli_lock._generate_execution_lock

    def exact_original(resolver, plan_path, **kwargs):
        observed.append((plan_path, kwargs))
        return original(resolver, plan_path, **kwargs)

    monkeypatch.setattr(cli_lock, "_generate_execution_lock", exact_original)
    assert main(["workflow", "prepare", str(workflow)]) == 0
    snapshot = load_run_configuration(workflow)
    assert len(observed) == 1
    assert observed[0][0] == snapshot.source_path()
    assert observed[0][1]["output"] == snapshot.lock_path
    assert observed[0][1]["external_artifacts"] == snapshot.external_artifacts()


def test_atomic_writer_preserves_previous_lock_on_resolution_failure(workflow):
    destination = workflow.with_name("goon.lock")
    destination.write_text("do-not-replace", encoding="utf-8")
    payload = yaml.safe_load(workflow.read_text())
    payload["inputs"] = {"unknown": True}
    workflow.write_text(yaml.safe_dump(payload))
    assert main(["workflow", "prepare", str(workflow)]) == 2
    assert destination.read_text() == "do-not-replace"
    assert not list(workflow.parent.glob(".goon.lock.*.tmp"))


def test_managed_agent_lock_refuses_captured_repository_different_from_target(
    tmp_path, monkeypatch, capsys
):
    from tests.factories.agent_review import agent_review_case

    _, plan, _, options = agent_review_case(tmp_path)
    plan.write_text(
        "version: 1\ngoons:\n- import: agents/review\n"
        "  with: {skill: cookbook/single-source-of-truth-review}\n"
    )
    selected = options["repository_root"] / "selected"
    selected.mkdir()
    (selected / ".git").mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(selected)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(league.parent / "test-state.db"))
    providers = tmp_path / "providers.toml"
    providers.write_text(
        '[providers.test]\napi = "openai-chat-completions"\nbase_url = "http://127.0.0.1:12345"\n'
        '[models.review]\nprovider = "test"\nmodel = "synthetic/reviewer"\n'
    )
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
    configuration = tmp_path / "run.yml"
    configuration.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository": str(selected)},
                "inputs": {"repo": "local:repo"},
                "agent": {
                    "model_profile": "review",
                    "acceptance": "agentic-review",
                    "repository_root": str(options["repository_root"]),
                    "skill_root": ".",
                },
            }
        )
    )
    assert main(["workflow", "prepare", str(configuration)]) == 2
    assert not configuration.with_name("goon.lock").exists()
    assert "Generated:" not in capsys.readouterr().out
