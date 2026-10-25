from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.services.execution_lock_persistence import load_execution_lock


@pytest.fixture
def agent_workflow(workflow):
    data = yaml.safe_load(workflow.read_text())
    data.update(
        source={"kind": "catalog-plan", "name": "agents/agentic-review-repair-loop"},
        inputs={
            "repo": "local:repository",
            "goal": "PRIVATE_TASK_SENTINEL",
            "regression_command": "scripts/regression",
            "verification_command": "scripts/verify",
        },
        agent={
            "model_profile": "fixture",
            "acceptance": "agentic-review",
            "repository_root": str(workflow.parent.parent),
            "skill_root": ".",
        },
        repair={
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": 60,
            "step_timeout_seconds": 30,
        },
    )
    workflow.write_text(yaml.safe_dump(data))
    return workflow


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repo)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    output = tmp_path / "workflow"
    assert (
        main(["templates", "init", "--kind", "plan", "python/quality", "--output", str(output)])
        == 0
    )
    selected = output / "run.yml"
    data = yaml.safe_load(selected.read_text())
    data["target"]["repository_id"] = RepositoryIdentity.model_validate(
        {"path": repo}
    ).repository_id
    selected.write_text(yaml.safe_dump(data))
    return selected


def test_generation_selects_catalog_without_copy_and_refuses_overwrite(workflow, capsys):
    before = workflow.read_bytes()
    assert sorted(path.name for path in workflow.parent.iterdir()) == ["run.yml"]
    assert (
        main(
            [
                "templates",
                "init",
                "--kind",
                "plan",
                "python/quality",
                "--output",
                str(workflow.parent),
            ]
        )
        == 2
    )
    assert workflow.read_bytes() == before


@pytest.mark.parametrize("key", ["python/typecheck", "python/quality"])
def test_goon_initialization_creates_only_plan_intent_and_shared_run_config(workflow, key, capsys):
    output = workflow.parent.parent / "selected-goon"
    assert main(["templates", "init", key, "--kind", "goon", "--output", str(output)]) == 0
    assert sorted(path.name for path in output.iterdir()) == ["plan.yml", "run.yml"]
    assert yaml.safe_load((output / "plan.yml").read_text()) == {"goons": [key]}
    config = output / "run.yml"
    selected = yaml.safe_load(config.read_text())
    assert selected["source"] == {"kind": "file-plan", "path": "plan.yml"}
    assert "inputs" not in selected
    selected["target"] = yaml.safe_load(workflow.read_text())["target"]
    config.write_text(yaml.safe_dump(selected))
    capsys.readouterr()
    assert main(["config", "validate", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in report["inputs"]] == [
        "project",
        "dependency_group",
        "typechecker",
    ]
    assert any("python/typecheck" in owner for owner in report["inputs"][2]["owners"])
    assert not (output / "goon.lock").exists()


def test_template_initialization_requires_an_explicit_kind(tmp_path):
    output = tmp_path / "workflow"
    with pytest.raises(SystemExit) as refusal:
        main(["templates", "init", "python/quality", "--output", str(output)])
    assert refusal.value.code == 2
    assert not output.exists()


def test_inspection_and_validation_use_the_same_owner_report_without_runtime(
    workflow, monkeypatch, capsys
):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime started")
    )
    capsys.readouterr()
    assert main(["config", "inspect", str(workflow), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert main(["config", "validate", str(workflow), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == inspected
    assert inspected["proof"]["admission"] == "not-started"
    assert [entry["name"] for entry in inspected["inputs"]] == [
        "project",
        "dependency_group",
        "typechecker",
    ]
    assert inspected["inputs"][2]["contract"]["choices"] == ["pyright", "ty"]
    assert any("python/typecheck" in owner for owner in inspected["inputs"][2]["owners"])
    assert not (workflow.parent / "goon.lock").exists()


def test_lock_binds_complete_selection_and_refresh_is_explicit(workflow, capsys):
    assert main(["lock", str(workflow)]) == 0
    before = load_execution_lock(workflow.parent / "goon.lock")
    data = yaml.safe_load(workflow.read_text())
    data["inputs"] = {"typechecker": "ty"}
    workflow.write_text(yaml.safe_dump(data))
    assert main(["config", "validate", str(workflow)]) == 0
    assert load_execution_lock(workflow.parent / "goon.lock") == before
    assert main(["lock", str(workflow)]) == 0
    assert load_execution_lock(workflow.parent / "goon.lock").digest != before.digest


@pytest.mark.parametrize("values", [{"typechecker": "invalid"}, {"unknown": "secret-sentinel"}])
def test_invalid_inputs_are_refused_without_echoing_values(workflow, values, capsys):
    data = yaml.safe_load(workflow.read_text())
    data["inputs"] = values
    workflow.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert main(["config", "validate", str(workflow), "--json"]) == 2
    output = capsys.readouterr().out
    assert "secret-sentinel" not in output
    assert not (workflow.parent / "goon.lock").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["plan", "run", "plan.yml", "--repository-id", "repo-id"],
        ["plan", "validate", "plan.yml"],
        ["lock", "plan.yml", "--input", "project=backend"],
        ["run", "run.yml", "--inputs-file", "values.yml"],
        ["team", "run", "."],
        ["shared-plan", "run", "quality", "--all-teams"],
    ],
)
def test_removed_launch_forms_are_not_accepted(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_generator_refuses_symlink_destination(tmp_path, capsys):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    assert (
        main(["templates", "init", "--kind", "plan", "python/quality", "--output", str(alias)]) == 2
    )
    assert not list(target.iterdir())


def test_internal_templates_use_their_owner_command(tmp_path):
    assert (
        main(
            [
                "templates",
                "init",
                "--kind",
                "plan",
                "oh-goon/app-certification",
                "--output",
                str(tmp_path / "new"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "new").exists()


def test_arbitrary_valid_strings_and_source_defaults_are_not_disclosed(workflow, capsys):
    source = workflow.parent / "plan.yml"
    # Use a normal imported input, with its canonical type, so this proves
    # privacy of valid values rather than only safe error presentation.
    source.write_text("version: 1\ngoons:\n- python/quality\n")
    local = workflow.parent / "goons/general/private"
    local.mkdir(parents=True)
    (local / "README.md").write_text("# general/private\n\nA privacy regression fixture.\n")
    (local / "goon.yml").write_text(
        "version: 1\ninputs:\n  note: {type: string, default: private-default-sentinel}\n"
        "steps:\n- run: git status --short\n"
    )
    source.write_text(source.read_text() + "- import: general/private\n  from: goons\n")
    data = yaml.safe_load(workflow.read_text())
    data["source"] = {"kind": "file-plan", "path": "plan.yml"}
    data["inputs"] = {"project": "private-project-sentinel", "typechecker": "ty"}
    workflow.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert main(["config", "inspect", str(workflow), "--json"]) == 0
    output = capsys.readouterr().out
    assert "private-project-sentinel" not in output
    assert "private-default-sentinel" not in output
    inspected = json.loads(output)
    project = next(item for item in inspected["inputs"] if item["name"] == "project")
    assert project["value_state"] == "redacted" and project["value"] is None
    assert (
        next(item for item in inspected["inputs"] if item["name"] == "typechecker")["value"] == "ty"
    )


@pytest.mark.parametrize("case", ["native-provider-mismatch", "missing-repair", "extra-repair"])
def test_inspection_refuses_agent_source_group_conflicts_without_acquisition(
    workflow, monkeypatch, capsys, case
):
    import oh_goon.services.agent_skill_registry as registry

    data = yaml.safe_load(workflow.read_text())
    data["source"] = {
        "kind": "catalog-plan",
        "name": "agents/agentic-review-repair-loop",
    }
    data["inputs"] = {
        "repo": "local:repository",
        "regression_command": "scripts/regression",
        "verification_command": "scripts/verify",
    }
    data["agent"] = {
        "model_profile": "fixture",
        "acceptance": "agentic-review",
        "repository_root": str(workflow.parent.parent),
        "skill_root": ".",
    }
    if case != "missing-repair":
        data["source"]["name"] = "agents/native-review"
        data["inputs"] = {"repo": "local:repository", "skill": "local:skills/review"}
    if case == "extra-repair":
        data["agent"].pop("model_profile")
        data["agent"].update(implementation="pi", profile="isolated-managed")
        data["repair"] = {
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": 60,
            "step_timeout_seconds": 30,
        }
    workflow.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(
        registry,
        "original_skill_aliases",
        lambda *args, **kwargs: pytest.fail("inspection acquired Skill resources"),
    )
    capsys.readouterr()
    assert main(["config", "validate", str(workflow), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "rejected"
    assert report["execution"] == "not_started_by_diagnostic"


def test_agent_inspection_shows_declared_steps_without_claiming_captured_execution(
    workflow, monkeypatch, capsys
):
    import oh_goon.services.agent_skill_registry as registry

    data = yaml.safe_load(workflow.read_text())
    data.update(
        source={"kind": "catalog-plan", "name": "agents/agentic-review-repair-loop"},
        inputs={
            "repo": "local:repository",
            "goal": "private prompt sentinel",
            "regression_command": "scripts/regression",
            "verification_command": "scripts/verify",
        },
        agent={
            "model_profile": "fixture",
            "acceptance": "agentic-review",
            "repository_root": str(workflow.parent.parent),
            "skill_root": ".",
        },
        repair={
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": 60,
            "step_timeout_seconds": 30,
        },
    )
    workflow.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(
        registry,
        "original_skill_aliases",
        lambda *args, **kwargs: pytest.fail("inspection acquired Skill resources"),
    )
    capsys.readouterr()
    assert main(["config", "inspect", str(workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inputs-valid"
    assert len(report["steps"]) == 9
    assert {step["proof"] for step in report["steps"]} == {"authored-not-resolved"}
    assert report["steps"][0]["requested_effects"]["repository_access"] == "read_only"
    assert report["proof"]["skill_closure"] == "not-captured"
    assert report["proof"]["execution"] == "not-started"
    assert all("task" not in step and "argv" not in step for step in report["steps"])


@pytest.mark.parametrize("command", [["config", "inspect"], ["config", "validate"], ["lock"]])
def test_each_configuration_command_analyzes_one_exact_source_graph(workflow, monkeypatch, command):
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    original = FleetPlanResolver.analyze_source
    captured = []

    def analyze(self, *args, **kwargs):
        graph = original(self, *args, **kwargs)
        captured.append(graph)
        return graph

    monkeypatch.setattr(FleetPlanResolver, "analyze_source", analyze)
    assert main([*command, str(workflow)]) == 0
    assert len(captured) == 1


@pytest.mark.parametrize("plan,strict", [("python/quality", False), ("oci/image", True)])
def test_source_execution_policy_selects_the_original_runtime_profile(
    workflow, monkeypatch, plan, strict
):
    import oh_goon.cli as cli

    data = yaml.safe_load(workflow.read_text())
    data["source"]["name"] = plan
    workflow.write_text(yaml.safe_dump(data))
    observed = []
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kwargs: observed.append(kwargs) or object()
    )
    monkeypatch.setattr(cli, "_execute_stateful_command", lambda runtime, args: 2)
    assert main(["run", str(workflow)]) == 2
    assert observed[0]["standard_local"] is (not strict)


@pytest.mark.parametrize("plan,repair", [("python/quality", False), ("agents/native-review", True)])
def test_invalid_agent_or_repair_selection_refuses_before_native_probe(
    workflow, monkeypatch, plan, repair
):
    import oh_goon.cli as cli
    from oh_goon import cli_ai

    data = yaml.safe_load(workflow.read_text())
    data["source"]["name"] = plan
    data["agent"] = {
        "implementation": "pi",
        "profile": "isolated-managed",
        "acceptance": "agentic-review",
        "repository_root": str(workflow.parent),
        "skill_root": ".",
    }
    if repair:
        data["repair"] = {
            "review_passes": 2,
            "write_roots": ["src"],
            "timeout_seconds": 30,
            "step_timeout_seconds": 10,
        }
    workflow.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(
        cli_ai,
        "native_plan_selection",
        lambda args: pytest.fail("native probe before invalid selection"),
    )
    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kw: pytest.fail("runtime before invalid selection"),
    )
    assert main(["lock", str(workflow)]) == 2
    assert main(["run", str(workflow), *(["--authorize-repair"] if repair else [])]) == 2


def test_captured_input_choices_are_immutable(workflow):
    from oh_goon.services.run_configuration import load_run_configuration

    data = yaml.safe_load(workflow.read_text())
    data["inputs"] = {"typechecker": "ty"}
    workflow.write_text(yaml.safe_dump(data))
    captured = load_run_configuration(workflow)
    exposed = captured.inputs
    exposed["typechecker"] = "pyright"
    assert captured.inputs == {"typechecker": "ty"}
    with pytest.raises(TypeError):
        captured.selection.inputs[0] = ("typechecker", "pyright")


def test_inspection_projects_authored_skill_references_and_unverified_pin_records(
    agent_workflow, monkeypatch, capsys
):
    import socket
    import subprocess

    from oh_goon.core.models.execution_lock_models import ExecutionLock, LockedSkillSource
    from oh_goon.services import agent_skill_registry
    from oh_goon.services.execution_lock_persistence import write_execution_lock

    lock_path = agent_workflow.with_name("goon.lock")
    write_execution_lock(
        lock_path,
        ExecutionLock(
            lock_version=1,
            plan="sha256:" + "0" * 64,
            digest="sha256:" + "1" * 64,
            skill_sources=(
                LockedSkillSource(
                    name="cookbook",
                    source="https://PRIVATE_URL_TOKEN@private.invalid/cookbook.git",
                    ref="PRIVATE_REF_SENTINEL",
                    commit="a" * 40,
                    skills=("skills/PRIVATE_RESOURCE_SENTINEL",),
                ),
            ),
        ),
    )
    original = lock_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("read-only inspection attempted native, network or Skill acquisition")

    monkeypatch.setattr(agent_skill_registry, "original_skill_aliases", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    capsys.readouterr()
    assert main(["config", "inspect", str(agent_workflow), "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["skills"] == {
        "pins": [{"source_alias": "cookbook", "commit": "a" * 40}],
        "pin_status": "lock-record-not-verified",
        "current_closure": "unknown",
    }
    assert report["proof"]["lock"] == "lock-record-not-verified"
    assert report["proof"]["skill_closure"] == "not-captured"
    references = [
        binding["skill"]
        for occurrence in report["occurrences"]
        for binding in occurrence["bindings"]
        if "skill" in binding
    ]
    assert [item["reference"] for item in references] == [
        "cookbook/single-source-of-truth-review",
        "cookbook/semantic-redecision-review",
        "cookbook/resolved-fact-regression-review",
        "cookbook/canonical-consumer-contract-review",
    ]
    assert {item["proof"] for item in references} == {"authored-contract-validated"}
    for private in (
        "PRIVATE_URL_TOKEN",
        "PRIVATE_REF_SENTINEL",
        "PRIVATE_RESOURCE_SENTINEL",
        "PRIVATE_TASK_SENTINEL",
    ):
        assert private not in output
    assert lock_path.read_bytes() == original
    assert main(["config", "inspect", str(agent_workflow)]) == 0
    human = capsys.readouterr().out
    assert "cookbook/single-source-of-truth-review" in human
    assert "lock-record-not-verified" in human and "a" * 40 in human


def test_private_symlink_lock_is_unavailable_without_reading_target(
    agent_workflow, monkeypatch, capsys
):
    from oh_goon.services import run_configuration_inspection

    private = agent_workflow.parent.parent / "private-native-auth.json"
    private.write_text('{"token":"PRIVATE_NATIVE_LOCK_SENTINEL"}')
    agent_workflow.with_name("goon.lock").symlink_to(private)
    original = Path.open

    def public_open(path, *args, **kwargs):
        assert path.resolve() != private, "inspection read private lock symlink"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", public_open)
    monkeypatch.setattr(
        run_configuration_inspection,
        "read_caller_file_bytes",
        lambda *args, **kwargs: pytest.fail("inspection read an unsafe lock source"),
    )
    capsys.readouterr()
    assert main(["config", "inspect", str(agent_workflow), "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["proof"]["lock"] == "unavailable"
    assert report["skills"]["pin_status"] == "unavailable"
    assert report["skills"]["pins"] == []
    assert report["skills"]["current_closure"] == "unknown"
    assert "PRIVATE_NATIVE_LOCK_SENTINEL" not in output


@pytest.mark.parametrize("contents", ["not a valid lock", "x" * 65537])
def test_unreadable_or_unsupported_pin_record_is_not_readiness(agent_workflow, capsys, contents):
    agent_workflow.with_name("goon.lock").write_text(contents)
    capsys.readouterr()
    assert main(["config", "inspect", str(agent_workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skills"]["pin_status"] == "unavailable"
    assert report["skills"]["pins"] == []
    assert report["proof"]["admission"] == "not-started"


def test_human_and_json_inspection_preserve_zero_numeric_constraint(workflow, capsys):
    data = yaml.safe_load(workflow.read_text())
    data["source"]["name"] = "ml/iris-training"
    workflow.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert main(["config", "inspect", str(workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    seed = next(item for item in report["inputs"] if item["name"] == "random_seed")
    assert seed["contract"]["minimum"] == 0
    assert main(["config", "inspect", str(workflow)]) == 0
    assert "'minimum': 0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "command", [("config", "inspect"), ("config", "validate"), ("lock",), ("run",)]
)
@pytest.mark.parametrize(
    ("harness", "effects"),
    [
        ("provider-review", {}),
        ("local-model-search", {"model": "local-model", "recipients": ["ollama_local"]}),
        ("native", {}),
        ("local-search", {"repository_access": "write"}),
        ("local-search", {"recipients": ["external_model"]}),
        ("local-search", {"capabilities": {"tools": ["sh"]}}),
    ],
)
def test_source_only_agent_selection_refuses_inference_or_effects_before_work(
    workflow, monkeypatch, command, harness, effects
):
    import oh_goon.cli as cli
    import oh_goon.cli_agent_providers as providers
    import oh_goon.services.agent_skill_registry as skills

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid source-only selection reached runtime, provider or Skill acquisition")

    monkeypatch.setattr(cli, "_build_application_runtime", forbidden)
    monkeypatch.setattr(providers, "agent_connection_from_args", forbidden)
    monkeypatch.setattr(skills, "original_skill_aliases", forbidden)
    package = workflow.parent / "goons/agents/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# agents/check\n\nSource selection refusal fixture.\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "inputs": {
                    "repo": {"type": "repository_reference"},
                    "skill": {"type": "skill_reference"},
                    "acceptance": {"type": "acceptance_profile"},
                },
                "steps": [
                    {
                        "uses": "agent",
                        "harness": harness,
                        "task": "search:needle",
                        "requested_effects": effects,
                    }
                ],
            }
        )
    )
    (workflow.parent / "plan.yml").write_text("goons:\n- import: agents/check\n  from: goons\n")
    selected = yaml.safe_load(workflow.read_text())
    selected["source"] = {"kind": "file-plan", "path": "plan.yml"}
    selected["inputs"] = {"repo": "local:repository", "skill": "local:skill"}
    selected["agent"] = {
        "acceptance": "review",
        "repository_root": str(workflow.parent.parent),
        "skill_root": ".",
    }
    workflow.write_text(yaml.safe_dump(selected))
    assert main([*command, str(workflow)]) == 2
    assert not (workflow.parent / "goon.lock").exists()
