"""Native preparation and namespace mapping, without strict admission claims."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import cast

import pytest
import yaml

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_layouts import SANDBOX_PATHS, AttemptPathLayout, RunPathLayout
from oh_goon.integrations._fleet_plan_run_resources import _capture_managed_paths
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.fleet_plan_managed_runtime import prepare_uv_project_runtimes
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.integrations.python_goon_projects import materialize_plan_projects
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.fleet_plan_executor import prepare_physical_test_run
from tests.factories.path_authority import test_path_authority_for_plan as path_authority_for_plan

SHOWCASE = Path(__file__).resolve().parents[4] / "examples/airflow-taskflow-showcase"


@pytest.mark.skipif(shutil.which("uv") is None, reason="native uv required")
def test_native_package_preparation_freezes_exact_source_and_maps_it_separately_from_target(
    tmp_path: Path,
) -> None:
    project = tmp_path / "authoring"
    shutil.copytree(
        SHOWCASE,
        project,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    developer = project / ".venv/keep"
    developer.parent.mkdir()
    developer.write_text("author environment")
    plan = FleetPlanResolver().resolve(project / "goon.yml")
    target = tmp_path / "target"
    target.mkdir()
    # A package-owned runtime must not acquire this selected target's project.
    (target / "pyproject.toml").write_text("invalid target Python metadata")
    target_environment = target / ".venv/keep"
    target_environment.parent.mkdir()
    target_environment.write_text("target environment")
    run = RunPathLayout.from_root(tmp_path / "run")
    run.root.mkdir()
    sources = materialize_plan_projects(plan, run)
    # Receivers and native preparation consume captured source after author removal.
    original = tmp_path / "moved-authoring"
    project.rename(original)
    runtimes = prepare_uv_project_runtimes(
        plan,
        source_root=target,
        run_root=run.root,
        path_authority=path_authority_for_plan(plan),
        cancel_requested=None,
    )
    step = plan.steps[0]
    assert step.kind == "command" and step.project_source is not None
    managed = runtimes[step.step_id]
    package = sources[step.project_source.content_sha256]
    assert managed.package_source == package
    assert managed.group is None
    assert target_environment.read_text() == "target environment"
    assert (original / ".venv/keep").read_text() == "author environment"
    assert not (package / ".venv").exists()
    bindings = _capture_managed_paths(run, {step.step_id: managed}, None)[step.step_id]
    package_binding = next(binding for binding in bindings if binding.declared_path == package)
    assert package_binding.identity.sha256 == step.project_source.content_sha256
    assert (package_binding.snapshot_path / "src/orders_pipeline/data/official.json").is_file()

    paths = AttemptPathLayout.from_root(run.attempt_root(step.step_id, 1))
    paths.workspace.mkdir(parents=True)
    environment = paths.cache / "uv-project-environment"
    # The mapping owner consumes physical path facts; this is no admitted attempt.
    attempt = cast(
        FleetPlanStepAttempt,
        SimpleNamespace(
            paths=paths,
            sandbox_bindings=bindings,
            host_tools=(),
            runtime_root=SimpleNamespace(uv_system_python=PurePosixPath(str(managed.python))),
        ),
    )
    launch = sandbox_process_launch(
        IsolatedProcessRequest(
            attempt=attempt,
            argv=(
                "uv",
                "run",
                "--no-sync",
                "--project",
                str(package),
                str(environment / "bin/orders-pipeline"),
            ),
            cwd=paths.workspace,
            env={"UV_PROJECT_ENVIRONMENT": str(environment)},
        )
    )
    assert launch.argv[launch.argv.index("--project") + 1] == str(package_binding.sandbox_path)
    assert launch.argv[-1] == "/cache/uv-project-environment/bin/orders-pipeline"
    assert launch.cwd == SANDBOX_PATHS.workspace
    assert launch.env["UV_PROJECT_ENVIRONMENT"] == "/cache/uv-project-environment"
    assert all(str(target) not in token and str(original) not in token for token in launch.argv)


@pytest.mark.skipif(shutil.which("uv") is None, reason="native uv required")
def test_prepared_physical_package_attempt_creates_output_working_directory(tmp_path: Path) -> None:
    """Prove physical preparation, without claiming admitted strict process execution."""
    project = tmp_path / "authoring"
    shutil.copytree(
        SHOWCASE,
        project,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["execution"] = {"purpose": "cicd"}
    for authored_step in declaration["steps"]:
        authored_step["working_directory"] = "scratch/results"
    goon.write_text(yaml.safe_dump(declaration))
    plan = FleetPlanResolver().resolve(goon)
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "subject.txt"
    marker.write_text("selected target")
    step = plan.steps[0]
    with prepare_physical_test_run(
        RepositoryIdentity.model_validate({"path": target}), plan
    ) as prepared:
        attempt = prepared.create_attempt(step, 1)
        assert (attempt.paths.workspace / "scratch/results").is_dir()
        assert (attempt.paths.workspace / "subject.txt").read_text() == "selected target"
        assert not (project / "scratch").exists()
        assert not (target / "scratch").exists()
    assert marker.read_text() == "selected target"


@pytest.mark.parametrize("quiescent", (True, False))
def test_preparation_cancellation_retains_run_scratch_only_for_unsettled_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiescent: bool
) -> None:
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep
    from oh_goon.engine.command import CommandCancelledError, CommandSupervisorReceipt
    from oh_goon.integrations import fleet_plan_run
    from tests.factories.fleet_plan_executor import strict_test_plan

    target = tmp_path / "target"
    target.mkdir()
    (target / "subject.txt").write_text("selected target")
    roots: list[Path] = []
    failure = CommandCancelledError(
        "cancelled native preparation",
        supervisor=CommandSupervisorReceipt(final_process_group_quiescent=quiescent),
    )

    def cancel_preparation(repository: object, root: Path, *args: object, **kwargs: object) -> None:
        roots.append(root)
        (root / "partial-preparation").write_text("owned native scratch")
        raise failure

    monkeypatch.setattr(fleet_plan_run, "_FleetPlanRunInputs", cancel_preparation)
    with pytest.raises(CommandCancelledError) as captured:
        prepare_physical_test_run(
            RepositoryIdentity.model_validate({"path": target}),
            strict_test_plan(ResolvedCommandStep(step_id="check", argv=("true",))),
            run_parent=tmp_path / "runs",
        )
    assert captured.value is failure
    assert len(roots) == 1
    if quiescent:
        assert not roots[0].exists()
    else:
        assert (roots[0] / "partial-preparation").read_text() == "owned native scratch"
        assert str(roots[0]) in "\n".join(failure.__notes__)
