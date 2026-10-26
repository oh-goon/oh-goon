from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.git_sources import LocalApplicationSource
from oh_goon.integrations.python_goon_projects import materialize_python_goon_project
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def author(root: Path, declaration: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "goon.yml"
    source.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    return source


def application(root: Path, *, python: bool = False) -> Path:
    root.mkdir(parents=True)
    (root / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    if python:
        (root / "pyproject.toml").write_text(
            '[project]\nname="independent"\nversion="0.1.0"\n'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
        )
        (root / "uv.lock").write_text("version=1\n")
        (root / "backend.py").write_text("raise AssertionError('discovery ran a build hook')\n")
    return root


@pytest.mark.parametrize("absolute", [False, True])
def test_local_application_captures_exact_root_without_python_or_goon_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absolute: bool
) -> None:
    root = tmp_path / "workflow"
    app = application((tmp_path / "external") if absolute else (root / "apps/example"))
    source = author(
        root,
        {
            "application": {"path": str(app) if absolute else "apps/example"},
            "steps": [{"run": "python app.py"}],
        },
    )
    monkeypatch.chdir(tmp_path)
    graph = FleetPlanResolver().analyze_source(source)
    plan = FleetPlanResolver().resolve_source_graph(graph)
    step = plan.steps[0]
    assert step.project_source is not None and step.uv is None
    assert step.project_source.application == LocalApplicationSource(path=str(app))
    assert len(graph.projects()) == len(plan._project_archives) == 1
    captured = graph.projects()[0]
    shutil.rmtree(app)
    restored = tmp_path / "restored"
    materialize_python_goon_project(captured.archive, captured.identity, restored)
    assert (restored / "app.py").read_text() == "VALUE = 42\n"
    assert not (restored / "goon.yml").exists()
    assert not (restored / "pyproject.toml").exists()


@pytest.mark.parametrize("mode", ["frozen", "locked"])
def test_python_application_keeps_native_environment_separate_from_source(
    tmp_path: Path, mode: str
) -> None:
    app = application(tmp_path / "independent", python=True)
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "runtime": "python",
            "uv": {"lock_mode": mode},
            "steps": [{"run": "independent"}],
        },
    )
    plan = FleetPlanResolver().resolve(source)
    step = plan.steps[0]
    assert step.project_source is not None and step.project_source.application is not None
    assert step.uv is not None and step.uv.source == "captured"
    assert step.uv.lock_mode == mode and step.uv.group is None
    assert step.argv[:4] == ("uv", "run", f"--{mode}", "--no-default-groups")
    assert not (app / "goon.yml").exists() and not (app / "README.md").exists()


def test_plain_application_does_not_interpret_python_metadata(tmp_path: Path) -> None:
    app = application(tmp_path / "app")
    (app / "pyproject.toml").write_text("not valid TOML metadata\n")
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "steps": [{"run": "python app.py"}],
        },
    )
    plan = FleetPlanResolver().resolve(source)
    assert plan.steps[0].uv is None and plan.steps[0].project_source is not None


@pytest.mark.parametrize("workspace", [False, True])
def test_selected_native_uv_project_consumes_its_native_lock_location(
    tmp_path: Path, workspace: bool
) -> None:
    app = application(tmp_path / "app")
    selected = application(app / "packages/nested")
    if workspace:
        (app / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers=["packages/*"]\n')
    (selected / "pyproject.toml").write_text(
        '[project]\nname="nested-independent"\nversion="0.1.0"\nrequires-python=">=3.11"\n'
    )
    for arguments in (("lock",), ("sync", "--frozen", "--no-default-groups")):
        subprocess.run(
            ["uv", *arguments, "--offline", "--project", str(selected), "--python", sys.executable],
            check=True,
            capture_output=True,
            text=True,
        )
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "runtime": "python",
            "uv": {"project": "packages/nested"},
            "steps": [{"run": "python app.py"}],
        },
    )
    graph = FleetPlanResolver().analyze_source(source)
    step = FleetPlanResolver().resolve_source_graph(graph).steps[0]
    assert step.uv.project == step.working_directory == "packages/nested"
    assert step.project_source == graph.projects()[0].identity
    native_lock = app / "uv.lock" if workspace else selected / "uv.lock"
    assert native_lock.is_file()
    if workspace:
        assert not (selected / "uv.lock").exists()
    else:
        assert not (app / "pyproject.toml").exists() and not (app / "uv.lock").exists()
    native_lock.unlink()
    with pytest.raises(FleetPlanInvalid, match="regular uv.lock on its captured ancestor chain"):
        FleetPlanResolver().resolve(source)


@pytest.mark.parametrize("live_name", [".env", ".env.production"])
def test_independent_application_refuses_live_dotenv_before_archive_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_name: str
) -> None:
    from oh_goon.services.execution_lock_persistence import write_execution_lock

    app = application(tmp_path / "app", python=True)
    nested = app / "config"
    nested.mkdir()
    for name in (".env.example", ".env.sample", ".env.template"):
        (nested / name).write_text("EXAMPLE_VALUE=example\n")
    (app / ".gitignore").write_text(".env*\n")
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "runtime": "python",
            "steps": [{"run": "independent"}],
        },
    )
    graph = FleetPlanResolver().analyze_source(source)
    lock_path = source.parent / "goon.lock"
    write_execution_lock(
        lock_path, build_execution_lock(FleetPlanResolver().resolve_source_graph(graph))
    )
    locked_bytes = lock_path.read_bytes()
    restored = tmp_path / "restored"
    project = graph.projects()[0]
    materialize_python_goon_project(project.archive, project.identity, restored)
    assert (restored / "config/.env.example").read_text() == "EXAMPLE_VALUE=example\n"
    secret = "PRIVATE_APPLICATION_DOTENV_VALUE"
    (nested / live_name).write_text(f"TOKEN={secret}\n")

    def no_archive(*args, **kwargs):
        pytest.fail("Live dotenv reached project archive creation")

    monkeypatch.setattr("oh_goon.integrations.python_goon_projects.tarfile.open", no_archive)
    with pytest.raises(FleetPlanInvalid, match="live dotenv") as captured:
        FleetPlanResolver().resolve(source)
    assert secret not in str(captured.value)
    assert lock_path.read_bytes() == locked_bytes


@pytest.mark.parametrize("escape", ["workspace", "build-backend"])
def test_application_native_metadata_refuses_external_closure_before_build(
    tmp_path: Path, escape: str
) -> None:
    app = application(tmp_path / "app", python=True)
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "runtime": "python",
            "steps": [{"run": "independent"}],
        },
    )
    assert FleetPlanResolver().resolve(source).steps[0].project_source is not None
    metadata = app / "pyproject.toml"
    text = metadata.read_text()
    text = (
        text + '\n[tool.uv.workspace]\nmembers=["../external"]\n'
        if escape == "workspace"
        else text.replace('backend-path=["."]', 'backend-path=["../external"]')
    )
    metadata.write_text(text)
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().resolve(source)


def test_application_symlinks_use_original_source_closure_policy(tmp_path: Path) -> None:
    app = application(tmp_path / "app")
    (app / "alias.py").symlink_to("app.py")
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "steps": [{"run": "python app.py"}],
        },
    )
    assert FleetPlanResolver().resolve(source).steps[0].project_source is not None
    (app / "alias.py").unlink()
    (app / "alias.py").symlink_to(tmp_path / "outside")
    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        FleetPlanResolver().resolve(source)


@pytest.mark.parametrize("metadata_name", ["pyproject.toml", "uv.lock", "uv.toml"])
def test_selected_uv_subproject_metadata_keeps_captured_root_authority(
    tmp_path: Path, metadata_name: str
) -> None:
    app = application(tmp_path / "app", python=True)
    selected = application(app / "nested", python=True)
    (selected / "uv.toml").write_text('find-links=["../wheels"]\n')
    (app / "wheels").mkdir()
    outside = application(tmp_path / "outside", python=True)
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "runtime": "python",
            "uv": {"project": "nested"},
            "steps": [{"run": "independent"}],
        },
    )
    baseline = FleetPlanResolver().resolve(source)
    assert baseline.steps[0].uv.project == "nested"
    assert baseline.steps[0].working_directory == "nested"
    metadata = selected / metadata_name
    if metadata_name == "pyproject.toml":
        metadata.write_text(
            metadata.read_text()
            + "\n[tool.uv.sources]\nprivate={path="
            + repr(str(outside))
            + ",editable=true}\n"
        )
    elif metadata_name == "uv.lock":
        metadata.write_text(
            'version=1\n[[package]]\nname="private"\nversion="0.1.0"\nsource={directory='
            + repr(str(outside))
            + "}\n"
        )
    else:
        metadata.write_text("find-links=[" + repr(str(outside)) + "]\n")
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().resolve(source)


def test_candidate_recapture_reuses_application_bytes_after_source_disappears(
    tmp_path: Path,
) -> None:
    app = application(tmp_path / "app")
    source = author(
        tmp_path / "workflow",
        {
            "application": {"path": str(app)},
            "steps": [{"run": "python app.py"}],
        },
    )
    resolver = FleetPlanResolver()
    original = resolver.analyze_source(source)
    original_plan = resolver.resolve_source_graph(original)
    (app / "app.py").write_text("VALUE = 'changed independently'\n")
    changed = resolver.resolve(source)
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(build_execution_lock(original_plan), build_execution_lock(changed))
    shutil.rmtree(app)
    recaptured = resolver.analyze_source(source, retained_graph=original)
    assert recaptured.projects() == original.projects()
    assert build_execution_lock(resolver.resolve_source_graph(recaptured)) == build_execution_lock(
        original_plan
    )
    source.write_text(
        "application: {path: /never-acquire-this-new-source}\nsteps:\n- run: python app.py\n"
    )
    with pytest.raises(FleetPlanInvalid, match="workflow changed"):
        resolver.analyze_source(source, retained_graph=original)


@pytest.mark.parametrize(
    "selection",
    [
        {"path": "${{ inputs.app }}"},
        {"path": "../app"},
        {"git": "https://example.test/app.git", "ref": "main", "path": "${{ inputs.app }}"},
    ],
)
def test_application_selection_is_literal_and_not_a_caller_scalar(
    tmp_path: Path, selection: dict
) -> None:
    source = author(tmp_path, {"application": selection, "steps": [{"run": "python app.py"}]})
    with pytest.raises(FleetPlanInvalid):
        FleetPlanResolver().resolve(source)


def test_invalid_git_application_public_diagnostic_never_echoes_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic

    secret = "PRIVATE_APPLICATION_PASSWORD"
    source = author(
        tmp_path,
        {
            "application": {
                "git": f"https://user:{secret}@host.invalid/app.git",
                "ref": "main",
                "path": ".",
            },
            "steps": [{"run": "true"}],
        },
    )

    def no_network(*args, **kwargs):
        pytest.fail("Invalid application source reached Git acquisition")

    monkeypatch.setattr(
        "oh_goon.integrations.git_source_preparation.prepare_git_source", no_network
    )
    with pytest.raises(FleetPlanInvalid) as captured:
        FleetPlanResolver().resolve(source)
    assert str(source) in str(captured.value)
    assert secret not in str(captured.value)
    print_safe_plan_diagnostic(captured.value, as_json=True)
    diagnostic = capsys.readouterr().out
    assert '"status": "rejected"' in diagnostic and secret not in diagnostic
    assert '"execution": "not_started_by_diagnostic"' in diagnostic
    assert "https://user:" not in diagnostic


def commit(root: Path) -> str:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "source",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_git_application_pin_refresh_is_distinct_from_goon_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = application(tmp_path / "upstream")
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    first = commit(upstream)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = author(
        tmp_path / "workflow",
        {
            "application": {"git": str(upstream), "ref": "main", "path": "."},
            "steps": [{"run": "python app.py"}],
        },
    )
    resolver = FleetPlanResolver()
    original = resolver.analyze_source(source, acquire_sources=True)
    pin = original.execution_sources[0]
    assert pin.kind == "application" and pin.commit == first and pin.path == "."
    (upstream / "app.py").write_text("VALUE = 43\n")
    second = commit(upstream)
    locked = resolver.analyze_source(
        source, acquire_sources=True, locked_sources=original.execution_sources, update_goons=True
    )
    assert locked.execution_sources == original.execution_sources
    assert locked.projects()[0].identity == original.projects()[0].identity
    updated = resolver.analyze_source(
        source,
        acquire_sources=True,
        locked_sources=original.execution_sources,
        update_applications=True,
    )
    assert updated.execution_sources[0].commit == second
    assert updated.projects()[0].identity != original.projects()[0].identity


@pytest.mark.parametrize("relative", [False, True])
def test_shared_git_ref_requires_explicit_cross_kind_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    upstream = tmp_path / "upstream"
    app = application(upstream / "app")
    package = upstream / "goons/general/driver"
    author(
        package,
        {
            "application": (
                {"path": "app"}
                if relative
                else {"git": str(upstream), "ref": "main", "path": "app"}
            ),
            "steps": [{"run": "python app.py"}],
        },
    )
    (package / "README.md").write_text("# general/driver\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    first = commit(upstream)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    plan = workflow / "plan.yml"
    plan.write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {
                        "import": "general/driver",
                        "from": {
                            "git": str(upstream),
                            "ref": "main",
                            "path": "goons/general/driver",
                        },
                    }
                ]
            }
        )
    )
    resolver = FleetPlanResolver()
    original = resolver.analyze_source(plan, acquire_sources=True)
    assert {pin.kind for pin in original.execution_sources} == {"goon", "application"}
    assert {pin.commit for pin in original.execution_sources} == {first}
    (app / "app.py").write_text("VALUE = 43\n")
    second = commit(upstream)
    for updates in ({"update_goons": True}, {"update_applications": True}):
        with pytest.raises(FleetPlanInvalid, match="refresh conflicts"):
            resolver.analyze_source(
                plan, acquire_sources=True, locked_sources=original.execution_sources, **updates
            )
    updated = resolver.analyze_source(
        plan,
        acquire_sources=True,
        locked_sources=original.execution_sources,
        update_goons=True,
        update_applications=True,
    )
    assert {pin.commit for pin in updated.execution_sources} == {second}


@pytest.mark.parametrize("authoring_kind", ["goon", "plan"])
def test_relative_git_application_retains_logical_provenance_after_reacquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authoring_kind: str
) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.services.execution_lock_persistence import write_execution_lock
    from oh_goon.services.run_configuration import (
        capture_run_configuration_source,
        load_run_configuration,
    )

    upstream = tmp_path / "upstream"
    authoring_root = upstream / "workflow" if authoring_kind == "plan" else upstream
    application(authoring_root / "app")
    package = authoring_root / "goons/general/driver"
    author(
        package,
        {"application": {"path": "app"}, "steps": [{"run": "python app.py"}]},
    )
    (package / "README.md").write_text("# general/driver\n")
    (authoring_root / "plan.yml").write_text("goons:\n- import: general/driver\n  from: ./goons\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    revision = commit(upstream)
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    workflow = tmp_path / "caller"
    workflow.mkdir()
    if authoring_kind == "plan":
        selection = {
            "kind": "git-plan",
            "git": str(upstream),
            "ref": "main",
            "path": "workflow",
            "plan": "plan.yml",
        }
    else:
        selection = {"kind": "file-plan", "path": "plan.yml"}
        (workflow / "plan.yml").write_text(
            yaml.safe_dump(
                {
                    "goons": [
                        {
                            "import": "general/driver",
                            "from": {
                                "git": str(upstream),
                                "ref": "main",
                                "path": "goons/general/driver",
                            },
                        }
                    ]
                }
            )
        )
    target = tmp_path / "target"
    target.mkdir()
    repository = RepositoryIdentity(path=target)
    run_path = workflow / "run.yml"
    run_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": selection,
                "target": {"repository_id": repository.repository_id},
            }
        )
    )
    snapshot = load_run_configuration(run_path)
    resolver = FleetPlanResolver()
    _, original = capture_run_configuration_source(
        snapshot, (repository,), resolver, acquire=True, allow_network=True
    )
    plan = resolver.resolve_source_graph(original)
    source = plan.steps[0].project_source.application
    assert source.kind == "application" and source.commit == revision
    assert source.path == ("workflow/app" if authoring_kind == "plan" else "app")
    assert {pin.kind for pin in plan.execution_sources} == {authoring_kind, "application"}
    expected = build_execution_lock(plan, external_artifacts=snapshot.external_artifacts())
    write_execution_lock(snapshot.lock_path, expected)
    shutil.rmtree(cache)
    _, reacquired = capture_run_configuration_source(
        snapshot, (repository,), resolver, allow_network=True
    )
    assert original.projects()[0].identity == reacquired.projects()[0].identity
    actual = build_execution_lock(
        resolver.resolve_source_graph(reacquired), external_artifacts=snapshot.external_artifacts()
    )
    assert actual == expected
