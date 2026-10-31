from __future__ import annotations

import argparse
import json
import shlex
import sys
from operator import setitem
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.core.execution_lock_identity import LOCK_VERSION
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.services.execution_lock_persistence import load_execution_lock
from oh_goon.services.run_configuration import RunConfigurationSnapshot


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


@pytest.mark.parametrize(
    "selected_repair,flags", [(True, []), (True, ["--yes"]), (False, ["--authorize-repair"])]
)
def test_run_repair_consent_refuses_before_source_acquisition(
    agent_workflow, monkeypatch, capsys, selected_repair, flags
):
    if not selected_repair:
        payload = yaml.safe_load(agent_workflow.read_text())
        del payload["repair"]
        agent_workflow.write_text(yaml.safe_dump(payload))

    def forbidden(*_args, **_kwargs):
        pytest.fail("Repair consent mismatch reached source acquisition or runtime")

    monkeypatch.setattr("oh_goon.cli_configuration.capture_run_configuration_source", forbidden)
    monkeypatch.setattr("oh_goon.cli._build_application_runtime", forbidden)
    assert main(["run", str(agent_workflow), "--json", *flags]) == 2
    assert json.loads(capsys.readouterr().out)["execution"] == "not_started_by_diagnostic"


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repo)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(preferences.parent / "application.sqlite"))
    output = tmp_path / "workflow"
    output.mkdir()
    selected = output / "run.yml"
    selected.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": "../repository"},
            }
        )
    )
    return selected


@pytest.fixture
def typed_catalog(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    package = root / "goons/general/typed"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/typed\n\nTyped public input fixture.\n")
    (package / "goon.yml").write_text(
        "inputs:\n  text: {type: string}\n  optional: {type: string, optional: true}\n"
        "  count: {type: integer, min: 0, default: 1}\n"
        "  enabled: {type: boolean, default: true}\nsteps:\n- run: python --version\n"
    )
    plan = root / "plans/general/typed"
    plan.mkdir(parents=True)
    (plan / "README.md").write_text("# general/typed\n\nTyped public input fixture.\n")
    (plan / "plan.yml").write_text("goons:\n- general/typed\n")
    monkeypatch.setattr(
        "oh_goon.runtime.goon_templates.default_goon_templates_root", lambda: root / "goons"
    )
    monkeypatch.setattr(
        "oh_goon.runtime.plan_templates.default_plan_templates_root", lambda: root / "plans"
    )


def test_public_init_keeps_literal_null_distinct_from_explicit_none(
    typed_catalog, tmp_path, capsys
):
    output = tmp_path / "typed-workflow"
    assert (
        main(
            [
                "init",
                "--template",
                "general/typed",
                "--draft",
                "--output",
                str(output),
                "--input",
                "text=null",
                "--input-null",
                "optional",
                "--input",
                "count=0",
                "--input",
                "enabled=false",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "not_ready" and result["required_fields"] == ["target.repository"]
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert selected["inputs"] == {"text": "null", "optional": None, "count": 0, "enabled": False}
    assert not (output / "goon.lock").exists()


@pytest.mark.parametrize(
    "values",
    [
        ["--input", "text=PRIVATE_FIRST", "--input", "text=PRIVATE_SECOND"],
        ["--input", "text=PRIVATE_FIRST", "--input-null", "text"],
    ],
)
def test_duplicate_init_values_refuse_before_publication(tmp_path, values, capsys):
    output = tmp_path / "refused"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--draft",
                "--output",
                str(output),
                "--json",
                *values,
            ]
        )
        == 2
    )
    captured = capsys.readouterr().out
    assert json.loads(captured)["code"] == "DUPLICATE_INPUT"
    assert "PRIVATE_" not in captured and not output.exists()


@pytest.mark.parametrize(
    "flags,tty,eof",
    [
        ([], False, False),
        (["--json"], True, False),
        (["--no-input"], True, False),
        ([], True, True),
    ],
)
def test_missing_init_values_never_wait_or_start_runtime(
    workflow, typed_catalog, monkeypatch, capsys, flags, tty, eof
):
    def read(_limit):
        if eof:
            return ""
        pytest.fail("Noninteractive initialization attempted to read stdin")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Missing initialization values reached application startup")

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: tty, readline=read))
    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service", forbidden
    )
    output = workflow.parent.parent / "refused"
    repository = workflow.parent.parent / "repository"
    assert (
        main(
            [
                "init",
                "--template",
                "general/typed",
                "--repository",
                str(repository),
                "--output",
                str(output),
                *flags,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    if "--json" in flags:
        assert json.loads(captured.out)["code"] == "MISSING_REQUIRED_INPUT"
    assert not output.exists()
    assert not (workflow.parent.parent / "application.sqlite").exists()


def test_generation_selects_catalog_without_copy_and_refuses_overwrite(workflow, capsys):
    before = workflow.read_bytes()
    assert sorted(path.name for path in workflow.parent.iterdir()) == ["run.yml"]
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--draft",
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
    assert (
        main(["init", "--template", key, "--kind", "goon", "--draft", "--output", str(output)]) == 0
    )
    assert sorted(path.name for path in output.iterdir()) == ["plan.yml", "run.yml"]
    assert yaml.safe_load((output / "plan.yml").read_text()) == {"goons": [key]}
    config = output / "run.yml"
    selected = yaml.safe_load(config.read_text())
    assert selected["source"] == {"kind": "file-plan", "path": "plan.yml"}
    assert selected["inputs"] == {
        "project": ".",
        "dependency_group": "dev",
        "typechecker": "pyright",
    }
    selected["target"] = yaml.safe_load(workflow.read_text())["target"]
    config.write_text(yaml.safe_dump(selected))
    capsys.readouterr()
    assert main(["workflow", "validate", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in report["inputs"]] == [
        "project",
        "dependency_group",
        "typechecker",
    ]
    assert any("python/typecheck" in owner for owner in report["inputs"][2]["owners"])
    assert not (output / "goon.lock").exists()


def test_template_initialization_defaults_to_the_plan_catalog(tmp_path):
    output = tmp_path / "workflow"
    assert main(["init", "--template", "python/quality", "--draft", "--output", str(output)]) == 0
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert selected["source"] == {"kind": "catalog-plan", "name": "python/quality"}
    assert not (output / "goon.lock").exists()


@pytest.mark.parametrize("key", ["python/quality", "agents/agentic-review-repair-loop"])
def test_initialization_selects_registered_repository_path_without_runtime(
    workflow, monkeypatch, capsys, key
):
    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service",
        lambda **kw: pytest.fail("runtime started"),
    )
    repository = workflow.parent.parent / "repository"
    output = workflow.parent.parent / "selected"
    assert (
        main(
            [
                "init",
                "--template",
                key,
                "--kind",
                "plan",
                *(["--draft"] if key.startswith("agents/") else []),
                "--output",
                str(output),
                "--repository",
                str(repository),
            ]
        )
        == 0
    )
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert selected["target"] == yaml.safe_load(workflow.read_text())["target"]
    if key.startswith("agents/"):
        assert "repo" not in selected.get("inputs", {})
        assert "model_profile" not in selected.get("agent", {})
        assert "regression_command" not in selected.get("inputs", {})
        assert "REQUIRED_" not in (output / "run.yml").read_text()
    else:
        assert (output / "goon.lock").is_file()
    assert "REPOSITORY_ID" not in (output / "run.yml").read_text()


def test_initializer_never_uses_same_name_as_repository_lookup(tmp_path, monkeypatch):
    folders = []
    for name in ("left", "right"):
        root = tmp_path / name / "repository"
        (root / ".git").mkdir(parents=True)
        folders.append(root)
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(tmp_path)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(preferences.parent / "application.sqlite"))
    monkeypatch.chdir(tmp_path)
    refused = tmp_path / "refused"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--output",
                str(refused),
                "--repository",
                "repository",
            ]
        )
        == 2
    )
    assert not refused.exists()
    output = tmp_path / "selected"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--output",
                str(output),
                "--repository",
                str(folders[1]),
            ]
        )
        == 0
    )
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert (output / selected["target"]["repository"]).resolve() == folders[1]


def test_initializer_refuses_missing_agent_selection_before_publication(workflow, capsys):
    outside = workflow.parent.parent / "unregistered"
    (outside / ".git").mkdir(parents=True)
    output = workflow.parent.parent / "refused"
    assert (
        main(
            [
                "init",
                "--template",
                "agents/agentic-review-repair-loop",
                "--kind",
                "plan",
                "--output",
                str(output),
                "--repository",
                str(outside),
            ]
        )
        == 2
    )
    assert not output.exists()
    refusal = capsys.readouterr().out
    assert "MISSING_REQUIRED_INPUT" in refusal
    assert "Publication: not_published" in refusal


@pytest.mark.parametrize("invalid_member", ("unregistered", "duplicate"))
def test_initializer_repeated_paths_refuse_incomplete_or_duplicate_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_member: str
) -> None:
    repository = tmp_path / "application"
    repository.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    output = tmp_path / "workflow"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--output",
                str(output),
                "--repository",
                str(repository),
                "--repository",
                str(repository if invalid_member == "duplicate" else other),
            ]
        )
        == 2
    )
    assert not output.exists()


@pytest.mark.parametrize("kind", ("plan", "goon"))
def test_initializer_repeated_paths_select_an_exact_registered_target_set_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    applications = [tmp_path / "application-one", tmp_path / "application-two"]
    for application in applications:
        application.mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(
        yaml.safe_dump({"version": 1, "folders": [str(root) for root in applications]})
    )
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service",
        lambda **kw: pytest.fail("runtime started"),
    )
    output = tmp_path / "workflow"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                kind,
                "--output",
                str(output),
                "--repository",
                str(applications[0]),
                "--repository",
                str(applications[1]),
            ]
        )
        == 0
    )
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert selected["target"] == {"repositories": ["../application-one", "../application-two"]}
    assert (output / "goon.lock").is_file()


@pytest.fixture
def fresh_initialization_targets(tmp_path, monkeypatch):
    from oh_goon.engine.git import GitService

    roots = (tmp_path / "application-one", tmp_path / "application-two")
    for root in roots:
        assert GitService().run("init", "--quiet", str(root)).returncode == 0
    preferences = tmp_path / "league.yml"
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    output = tmp_path / "workflow"
    arguments = [
        "init",
        "--template",
        "python/quality",
        "--output",
        str(output),
        "--repository",
        str(roots[0]),
        "--repository",
        str(roots[1]),
        "--no-input",
        "--json",
    ]
    return roots, preferences, output, arguments


@pytest.mark.parametrize("invalid", ("non-git", "input"))
def test_plural_initialization_validates_all_known_contracts_before_registration(
    fresh_initialization_targets, monkeypatch, capsys, invalid
):
    roots, preferences, output, arguments = fresh_initialization_targets
    if invalid == "non-git":
        (roots[1] / ".git").rename(roots[1] / "removed-git")
    else:
        arguments.extend(("--input", "typechecker=not-a-checker"))

    def forbidden(**_kwargs):
        pytest.fail("Known-invalid selection reached runtime or registration")

    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service", forbidden
    )
    assert main(arguments) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["publication"] == "not_published"
    assert not preferences.exists()
    assert not output.exists()


@pytest.mark.parametrize("draft", (False, True))
def test_initializer_refuses_raw_symlink_before_parent_path_normalization(
    fresh_initialization_targets, monkeypatch, capsys, draft
):
    roots, preferences, output, arguments = fresh_initialization_targets
    foreign = output.parent / "outside/inner"
    foreign.mkdir(parents=True)
    alias = output.parent / "alias"
    alias.symlink_to(foreign, target_is_directory=True)
    arguments[arguments.index("--repository") + 1] = str(alias / ".." / roots[0].name)
    if draft:
        arguments.append("--draft")

    def forbidden(**_kwargs):
        pytest.fail("Unsafe target reached runtime or registration")

    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service", forbidden
    )
    assert main(arguments) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == "CONFIG_TARGET_UNAVAILABLE"
    assert report["publication"] == "not_published"
    assert not preferences.exists()
    assert not output.exists()


def test_plural_initialization_preserves_partial_registration_and_retry_converges(
    fresh_initialization_targets, monkeypatch, capsys
):
    from oh_goon.runtime.league_folders import LeagueFolderStore
    from oh_goon.services.locations import LocationService

    roots, preferences, output, arguments = fresh_initialization_targets
    original = LocationService.register_target

    def register(service, path, **kwargs):
        if path == roots[1]:
            raise OSError("Selected second registration refused")
        return original(service, path, **kwargs)

    monkeypatch.setattr(LocationService, "register_target", register)
    assert main(arguments) == 2
    failed = json.loads(capsys.readouterr().out)
    receipts = failed["target_registration"]["repositories"]
    assert [(item["path"], item["status"]) for item in receipts] == [(str(roots[0]), "registered")]
    assert failed["publication"] == "not_published"
    assert LeagueFolderStore(preferences).list() == (roots[0],)
    assert not output.exists()

    monkeypatch.setattr(LocationService, "register_target", original)
    assert main(arguments) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "ready"
    assert [item["status"] for item in completed["target_registration"]["repositories"]] == [
        "already-registered",
        "registered",
    ]
    assert LeagueFolderStore(preferences).list() == roots
    assert (output / "goon.lock").is_file()


def test_plural_initialization_retains_all_registration_receipts_after_lock_failure(
    fresh_initialization_targets, monkeypatch, capsys
):
    from oh_goon.runtime.league_folders import LeagueFolderStore

    roots, preferences, output, arguments = fresh_initialization_targets

    def fail_lock(_args):
        raise OSError("Canonical lock publication refused")

    monkeypatch.setattr("oh_goon.cli_lock.generate_stateless_lock", fail_lock)
    assert main(arguments) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["publication"] == "not_published"
    assert [item["path"] for item in report["target_registration"]["repositories"]] == [
        str(root) for root in roots
    ]
    assert LeagueFolderStore(preferences).list() == roots
    assert not output.exists()


@pytest.mark.parametrize("failure", ("identity", "storage"))
def test_initializer_reports_classified_database_failure_without_disclosing_private_detail(
    fresh_initialization_targets, monkeypatch, capsys, failure
):
    import sqlite3

    from oh_goon.runtime.database import DatabasePathIdentityLost

    _, preferences, output, arguments = fresh_initialization_targets
    error = (
        DatabasePathIdentityLost("PRIVATE_DATABASE_SENTINEL")
        if failure == "identity"
        else sqlite3.OperationalError("database or disk is full: PRIVATE_DATABASE_SENTINEL")
    )

    def fail_runtime(**_kwargs):
        raise error

    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service", fail_runtime
    )
    assert main(arguments) == 2
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["code"] == (
        "RUNTIME_DATABASE_IDENTITY_LOST" if failure == "identity" else "RUNTIME_STORAGE_UNAVAILABLE"
    )
    assert report["publication"] == "not_published"
    assert "PRIVATE_DATABASE_SENTINEL" not in text
    assert not preferences.exists()
    assert not output.exists()


def test_plural_selection_reports_invalid_element_without_disclosing_value(workflow, capsys):
    payload = yaml.safe_load(workflow.read_text())
    payload["target"] = {
        "repositories": ["../repository", {"PRIVATE_FIELD": "PRIVATE_TARGET_SENTINEL"}]
    }
    workflow.write_text(yaml.safe_dump(payload))
    assert main(["workflow", "inspect", str(workflow), "--json"]) == 2
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["code"] == "CONFIG_SCHEMA_INVALID"
    assert "target.repositories.1" in report["fields"]
    assert "PRIVATE_FIELD" not in text and "PRIVATE_TARGET_SENTINEL" not in text


def test_plural_inspection_merges_exact_filesystem_and_discovered_git_roots_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import oh_goon.cli as cli
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    application = tmp_path / "application"
    application.mkdir()
    git_root = tmp_path / "git-projects" / "project"
    (git_root / ".git").mkdir(parents=True)
    preferences = tmp_path / "league.yml"
    preferences.write_text(
        yaml.safe_dump({"version": 1, "folders": [str(application), str(git_root.parent)]})
    )
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime started")
    )
    package = tmp_path / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    (package / "goon.yml").write_text("steps:\n- id: check\n  run: /bin/true\n")
    (tmp_path / "plan.yml").write_text("goons:\n- import: general/check\n  from: ./goons\n")
    roots = (application, git_root)
    path = tmp_path / "run.yml"
    payload: dict[str, Any] = {
        "version": 2,
        "source": {"kind": "file-plan", "path": "plan.yml"},
        "target": {"repositories": [str(root) for root in roots]},
    }
    path.write_text(yaml.safe_dump(payload))
    analyzed: list[Path] = []
    original = FleetPlanResolver.analyze_source

    def analyze(self: FleetPlanResolver, source: Path, **kwargs: Any) -> Any:
        analyzed.append(source)
        return original(self, source, **kwargs)

    monkeypatch.setattr(FleetPlanResolver, "analyze_source", analyze)
    assert main(["workflow", "validate", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [entry["path"] for entry in report["target"]["repositories"]] == [
        str(root) for root in roots
    ]
    assert analyzed == [tmp_path / "plan.yml"]

    git_root.rename(git_root.with_name("moved-project"))
    assert main(["workflow", "validate", str(path), "--json"]) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "CONFIG_TARGET_UNAVAILABLE"
    assert analyzed == [tmp_path / "plan.yml"]


def test_plural_run_refuses_a_replaced_member_before_any_application_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import oh_goon.cli as cli
    from oh_goon.application import ApplicationRuntime

    applications = (tmp_path / "application-one", tmp_path / "application-two")
    for application in applications:
        application.mkdir()
        (application / "verify.sh").write_text("printf executed > executed.txt\n")
    preferences = tmp_path / "league.yml"
    preferences.write_text(
        yaml.safe_dump({"version": 1, "folders": [str(root) for root in applications]})
    )
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    package = tmp_path / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    (package / "goon.yml").write_text("steps:\n- id: check\n  run: /bin/sh verify.sh\n")
    (tmp_path / "plan.yml").write_text("goons:\n- import: general/check\n  from: ./goons\n")
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repositories": [str(root) for root in applications]},
            }
        )
    )
    assert main(["workflow", "prepare", str(selection)]) == 0
    assert main(["run", str(selection), "--json"]) == 0
    baseline = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert baseline["outcome"] == "SUCCESS"
    for application in applications:
        marker = application / "executed.txt"
        assert marker.read_text() == "executed"
        marker.unlink()
    build = cli._build_application_runtime

    def replaced_before_build(**kwargs: Any) -> ApplicationRuntime | None:
        applications[1].rename(tmp_path / "original-application-two")
        applications[1].mkdir()
        (applications[1] / "verify.sh").write_text("printf replaced > executed.txt\n")
        return build(**kwargs)

    monkeypatch.setattr(cli, "_build_application_runtime", replaced_before_build)
    assert main(["run", str(selection), "--json"]) == 2
    refusal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert refusal["fleet_run_id"] is None
    assert all(not (application / "executed.txt").exists() for application in applications)
    monkeypatch.setattr(cli, "_build_application_runtime", build)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        assert [item.run.fleet_run_id for item in runtime.runs.history().items] == [
            baseline["fleet_run_id"]
        ]
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("path_form", ["absolute", "home-relative"])
def test_initialization_selects_registered_filesystem_root_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_form: str
) -> None:
    repository = tmp_path / "plain application"
    repository.mkdir()
    requested = repository
    if path_form == "home-relative":
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        requested = Path("~/plain application")
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(preferences.parent / "application.sqlite"))
    monkeypatch.setattr(
        "oh_goon.application.ApplicationRuntime.build_registration_service",
        lambda **kw: pytest.fail("runtime started"),
    )
    output = tmp_path / "workflow"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--repository",
                str(requested),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    selection = yaml.safe_load((output / "run.yml").read_text())
    assert (output / selection["target"]["repository"]).resolve() == repository
    assert not (repository / ".git").exists()


@pytest.mark.parametrize(
    "command", [("workflow", "inspect"), ("workflow", "validate"), ("workflow", "prepare")]
)
def test_configuration_selects_registered_filesystem_root_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...]
) -> None:
    import oh_goon.cli as cli

    repository = tmp_path / "plain application"
    repository.mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(preferences.parent / "application.sqlite"))
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime started")
    )
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": str(repository)},
            }
        )
    )
    assert main([*command, str(selection)]) == 0
    assert not (repository / ".git").exists()


@pytest.mark.parametrize("registration", ["parent-only", "unregistered", "missing-root"])
def test_filesystem_target_requires_its_exact_available_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registration: str,
) -> None:
    import oh_goon.cli as cli

    repository = tmp_path / "projects/application"
    repository.mkdir(parents=True)
    folders = [str(repository.parent if registration == "parent-only" else repository)]
    if registration == "unregistered":
        folders = []
    if registration == "missing-root":
        repository.rmdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": folders}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(preferences.parent / "application.sqlite"))
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime started")
    )
    output = tmp_path / "workflow"
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--repository",
                str(repository),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": str(repository)},
            }
        )
    )
    capsys.readouterr()
    assert main(["workflow", "validate", str(selection), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    if registration == "missing-root":
        assert report["code"] == "CONFIG_TARGET_UNAVAILABLE"
    else:
        assert report["target"]["registration"] == "unavailable"
        assert report["proof"]["admission"] == "not-started"


def test_inspection_and_validation_use_the_same_owner_report_without_runtime(
    workflow, monkeypatch, capsys
):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime started")
    )
    capsys.readouterr()
    assert main(["workflow", "inspect", str(workflow), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert main(["workflow", "validate", str(workflow), "--json"]) == 0
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
    assert main(["workflow", "prepare", str(workflow)]) == 0
    before = load_execution_lock(workflow.parent / "goon.lock")
    data = yaml.safe_load(workflow.read_text())
    data["inputs"] = {"typechecker": "ty"}
    workflow.write_text(yaml.safe_dump(data))
    assert main(["workflow", "validate", str(workflow)]) == 0
    assert load_execution_lock(workflow.parent / "goon.lock") == before
    assert main(["workflow", "prepare", str(workflow)]) == 0
    assert load_execution_lock(workflow.parent / "goon.lock").digest != before.digest


@pytest.mark.parametrize("values", [{"typechecker": "invalid"}, {"unknown": "secret-sentinel"}])
def test_invalid_inputs_are_refused_without_echoing_values(workflow, values, capsys):
    data = yaml.safe_load(workflow.read_text())
    data["inputs"] = values
    workflow.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert main(["workflow", "validate", str(workflow), "--json"]) == 2
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
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--draft",
                "--output",
                str(alias),
            ]
        )
        == 2
    )
    assert not list(target.iterdir())


def test_ready_initializer_prints_the_executable_waited_run_command(workflow, capsys):
    output = workflow.parent.parent / "workflow's literal $selection"
    repository = workflow.parent.parent / "repository"
    capsys.readouterr()
    assert (
        main(
            [
                "init",
                "--template",
                "python/quality",
                "--kind",
                "plan",
                "--output",
                str(output),
                "--repository",
                str(repository),
            ]
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    rendered = next(line.removeprefix("Next: ") for line in lines if line.startswith("Next: "))
    assert shlex.split(rendered) == ["oh-goon", "run", str(output)]
    assert (output / "goon.lock").is_file()
    assert "target.repository_id" not in "\n".join(lines)


def test_internal_templates_use_their_owner_command(tmp_path):
    assert (
        main(
            [
                "init",
                "--template",
                "oh-goon/app-certification",
                "--kind",
                "plan",
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
    assert main(["workflow", "inspect", str(workflow), "--json"]) == 0
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
    assert main(["workflow", "validate", str(workflow), "--json"]) == 2
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
    assert main(["workflow", "inspect", str(workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inputs-valid"
    assert len(report["steps"]) == 9
    assert {step["proof"] for step in report["steps"]} == {"authored-not-resolved"}
    assert report["steps"][0]["requested_effects"]["repository_access"] == "read_only"
    assert report["proof"]["skill_closure"] == "not-captured"
    assert report["proof"]["execution"] == "not-started"
    assert all("task" not in step and "argv" not in step for step in report["steps"])


@pytest.mark.parametrize(
    "command", [["workflow", "inspect"], ["workflow", "validate"], ["workflow", "prepare"]]
)
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
    if strict:
        data["inputs"] = {"base_registry_hostname": "registry.example"}
    workflow.write_text(yaml.safe_dump(data))
    assert main(["workflow", "prepare", str(workflow)]) == 0
    observed = []

    def observe_runtime(**kwargs: Any) -> None:
        observed.append(kwargs)

    monkeypatch.setattr(cli, "_build_application_runtime", observe_runtime)
    monkeypatch.setattr(cli, "_execute_stateful_command", lambda runtime, args: 2)
    assert main(["run", str(workflow)]) == 2
    assert observed[0]["standard_local"] is (not strict)
    assert observed[0]["startup_purpose"] == "workflow"


@pytest.mark.parametrize("plural", [False, True])
def test_budgeted_run_captures_installed_argv_before_source_or_runtime_work(
    workflow: Path, monkeypatch: pytest.MonkeyPatch, plural: bool
) -> None:
    import oh_goon.cli as cli
    import oh_goon.cli_run_resources as resources
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    selected = yaml.safe_load(workflow.read_text())
    selected["resources"] = {"cpu_millis": 1000, "memory_mib": 512}
    if plural:
        second = workflow.parent.parent / "second-repository"
        (second / ".git").mkdir(parents=True)
        selected["target"] = {"repositories": ["../repository", "../second-repository"]}
    workflow.write_text(yaml.safe_dump(selected))
    argv = ["run", str(workflow), "--json"]
    monkeypatch.setattr(sys, "argv", ["oh-goon", *argv])
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime before native scope")
    )
    monkeypatch.setattr(
        FleetPlanResolver,
        "analyze_source",
        lambda *args, **kw: pytest.fail("source capture before native scope"),
    )
    captured: list[tuple[RunConfigurationSnapshot, list[str]]] = []

    def launch(
        args: argparse.Namespace, snapshot: RunConfigurationSnapshot, original_argv: list[str]
    ) -> int:
        captured.append((snapshot, original_argv))
        assert args.command == "run"
        assert args.as_json
        return 7

    monkeypatch.setattr(resources, "run_with_resource_envelope", launch)
    assert main() == 7
    assert len(captured) == 1
    snapshot, original_argv = captured[0]
    assert original_argv == argv
    assert snapshot.path == workflow
    assert snapshot.selection.resources is not None
    assert snapshot.selection.resources.cpu_millis == 1000
    assert snapshot.content == workflow.read_bytes()
    assert snapshot.selection.version == 2
    assert snapshot.selection.target.selected_paths() == (
        ("../repository", "../second-repository") if plural else ("../repository",)
    )
    from oh_goon.services.run_configuration import (
        decode_run_configuration_snapshot,
        encode_run_configuration_snapshot,
    )

    restored = decode_run_configuration_snapshot(encode_run_configuration_snapshot(snapshot))
    assert restored == snapshot


def test_budgeted_run_invalid_repair_consent_refuses_before_native_launch(
    workflow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.cli as cli
    import oh_goon.cli_configuration as configuration
    import oh_goon.cli_run_resources as resources
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    selected = yaml.safe_load(workflow.read_text())
    selected["resources"] = {"cpu_millis": 1000}
    workflow.write_text(yaml.safe_dump(selected))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Invalid repair consent must refuse before native launch or source work")

    monkeypatch.setattr(cli, "_build_application_runtime", forbidden)
    monkeypatch.setattr(resources, "run_with_resource_envelope", forbidden)
    monkeypatch.setattr(configuration, "resolve_run_configuration_targets", forbidden)
    monkeypatch.setattr(FleetPlanResolver, "analyze_source", forbidden)
    assert main(["run", str(workflow), "--authorize-repair", "--json"]) == 2
    assert not (workflow.parent / "goon.lock").exists()


def test_budget_unavailable_refuses_before_source_capture_or_runtime(
    workflow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.cli as cli
    import oh_goon.cli_run_resources as resources
    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    selected = yaml.safe_load(workflow.read_text())
    selected["resources"] = {"cpu_millis": 1000}
    workflow.write_text(yaml.safe_dump(selected))
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("runtime after refused cap")
    )
    monkeypatch.setattr(
        FleetPlanResolver,
        "analyze_source",
        lambda *args, **kw: pytest.fail("source acquired after refused cap"),
    )
    calls: list[tuple[str, CicdResourcePolicy]] = []

    def unavailable(run_id: str, policy: CicdResourcePolicy) -> NoReturn:
        calls.append((run_id, policy))
        raise FleetPlanInvalid("native scope would discard caller resource limits")

    monkeypatch.setattr(resources, "prepare_run_resource_scope", unavailable)
    assert main(["run", str(workflow)]) == 2
    assert len(calls) == 1
    assert calls[0][1].cpu_millis == 1000
    assert not (workflow.parent / "goon.lock").exists()


def test_budgeted_schedule_refuses_before_source_capture_or_runtime(
    workflow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.cli as cli
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    selected = yaml.safe_load(workflow.read_text())
    selected["resources"] = {"memory_mib": 512}
    workflow.write_text(yaml.safe_dump(selected))
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("schedule runtime started")
    )
    monkeypatch.setattr(
        FleetPlanResolver,
        "analyze_source",
        lambda *args, **kw: pytest.fail("unsupported scheduled budget acquired source"),
    )
    assert (
        main(
            [
                "plan",
                "schedule",
                str(workflow),
                "--schedule-id",
                "budgeted",
                "--interval-minutes",
                "60",
            ]
        )
        == 2
    )


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
    assert main(["workflow", "prepare", str(workflow)]) == 2
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
        setitem(cast(Any, captured.selection.inputs), 0, ("typechecker", "pyright"))


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
            lock_version=LOCK_VERSION,
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
    assert main(["workflow", "inspect", str(agent_workflow), "--json"]) == 0
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
    assert main(["workflow", "inspect", str(agent_workflow)]) == 0
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
    assert main(["workflow", "inspect", str(agent_workflow), "--json"]) == 0
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
    assert main(["workflow", "inspect", str(agent_workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skills"]["pin_status"] == "unavailable"
    assert report["skills"]["pins"] == []
    assert report["proof"]["admission"] == "not-started"


def test_human_and_json_inspection_preserve_zero_numeric_constraint(workflow, capsys):
    data = yaml.safe_load(workflow.read_text())
    data["source"]["name"] = "ml/iris-training"
    workflow.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert main(["workflow", "inspect", str(workflow), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    seed = next(item for item in report["inputs"] if item["name"] == "random_seed")
    assert seed["contract"]["minimum"] == 0
    assert main(["workflow", "inspect", str(workflow)]) == 0
    assert "'minimum': 0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "command",
    [("workflow", "inspect"), ("workflow", "validate"), ("workflow", "prepare"), ("run",)],
)
@pytest.mark.parametrize(
    ("harness", "effects", "include_selection"),
    [
        ("provider-review", {}, True),
        ("local-model-search", {"model": "local-model", "recipients": ["ollama_local"]}, True),
        ("native", {}, True),
        ("local-search", {"repository_access": "write"}, True),
        ("local-search", {"recipients": ["external_model"]}, True),
        ("local-search", {"capabilities": {"tools": ["sh"]}}, True),
        ("local-search", {}, False),
    ],
)
def test_source_only_agent_selection_refuses_inference_or_effects_before_work(
    workflow, monkeypatch, command, harness, effects, include_selection
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
    if not include_selection:
        del selected["agent"]
    workflow.write_text(yaml.safe_dump(selected))
    assert main([*command, str(workflow)]) == 2
    assert not (workflow.parent / "goon.lock").exists()
