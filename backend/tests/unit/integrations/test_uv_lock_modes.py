"""Native uv owns frozen consumption and opt-in metadata freshness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.path_layouts import RunPathLayout
from oh_goon.engine.git import GitService
from oh_goon.integrations import fleet_plan_step_execution
from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step
from oh_goon.integrations.python_goon_projects import materialize_plan_projects
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.python_goon_projects import write_isolated_build_project


@pytest.mark.parametrize("package", (False, True))
@pytest.mark.parametrize("changed", (False, True))
@pytest.mark.parametrize("mode", ("frozen", "locked"))
@pytest.mark.parametrize("ambient", ("opposing_mode", "UV_NO_SYNC", "UV_NO_PROJECT"))
def test_native_modes_preserve_lock_and_do_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: bool,
    changed: bool,
    mode: str,
    ambient: str,
) -> None:
    project = tmp_path / "project"
    if package:
        write_isolated_build_project(project)
        command = "isolated-build"
    else:
        project.mkdir()
        (project / "README.md").write_text("# general/native-mode\n")
        (project / "pyproject.toml").write_text(
            '[project]\nname="native-mode"\nversion="1.0.0"\n'
            'requires-python=">=3.12"\ndependencies=[]\n[tool.uv]\npackage=false\n'
        )
        (project / "check.py").write_text('print("installed-command-ran")\n')
        locked = subprocess.run(
            ["uv", "lock", "--project", str(project), "--offline", "--python", sys.executable],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert locked.returncode == 0, locked.stderr
        command = "uv run python check.py"
    lock = (project / "uv.lock").read_bytes()
    if changed:
        metadata = (project / "pyproject.toml").read_text()
        if "dependencies=[]" in metadata:
            metadata = metadata.replace(
                "dependencies=[]", 'dependencies=["absent-mode-probe==0.0.1"]'
            )
        else:
            metadata = metadata.replace(
                "[project.scripts]", 'dependencies=["absent-mode-probe==0.0.1"]\n[project.scripts]'
            )
        (project / "pyproject.toml").write_text(metadata)
    (project / "goon.yml").write_text(
        yaml.safe_dump(
            {
                **({"runtime": "python"} if package else {}),
                "uv": {"lock_mode": mode},
                "execution": {"purpose": "verification"},
                "steps": [{"run": command}],
            }
        )
    )
    developer = project / ".venv/developer-marker"
    developer.parent.mkdir()
    developer.write_text("untouched")
    resolver = FleetPlanResolver()
    plan = resolver.resolve(project / "goon.yml")
    step = plan.steps[0]
    assert isinstance(step, ResolvedCommandStep) and step.uv is not None
    captured = materialize_plan_projects(plan, RunPathLayout.from_root(tmp_path / "run"))
    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
        project_sources=captured,
    )
    # A conflicting ambient switch cannot silently change the resolved selection.
    monkeypatch.setenv(
        ("UV_LOCKED" if mode == "frozen" else "UV_FROZEN")
        if ambient == "opposing_mode"
        else ambient,
        "1",
    )
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    observed: list[tuple[str, ...]] = []
    native = fleet_plan_step_execution.run_command

    def observe(*argv: str, **options: Any):
        assert argv[:2] != ("uv", "lock"), "frozen execution must not add a freshness validator"
        observed.append(tuple(argv))
        environment = options["env"]
        assert not {"UV_LOCKED", "UV_FROZEN", "UV_NO_SYNC", "UV_NO_PROJECT"}.intersection(
            environment
        )
        return native(*argv, **options)

    monkeypatch.setattr(fleet_plan_step_execution, "run_command", observe)
    outcome = execute_direct_step(project, step, runtime, step_order=0)
    if changed and mode == "locked":
        assert outcome.returncode != 0
        assert "installed-command-ran" not in outcome.stdout
        assert len(observed) == 1
    else:
        assert outcome.returncode == 0, outcome.stderr
        assert "installed-command-ran" in outcome.stdout
        assert len(observed) == (2 if package else 1)
    assert (project / "uv.lock").read_bytes() == lock
    assert developer.read_text() == "untouched"
    assert outcome.uv_preparation is not None
    assert outcome.uv_preparation.lock_mode == mode
    assert outcome.uv_preparation.freshness == (
        "not_checked" if mode == "frozen" else "not_established" if changed else "checked"
    )
    receipt = attempt_evidence(
        step=step,
        attempt=1,
        outcome=outcome,
        repository_state_before="captured",
        repository_state_after="captured",
        worktree_files_after=(),
    )
    assert (
        type(receipt)
        .model_validate_json(receipt.model_dump_json(exclude_computed_fields=True))
        .uv_preparation
        == outcome.uv_preparation
    )
    if package and outcome.returncode == 0:
        assert observed[-1][:3] == ("uv", "run", "--no-sync")
        assert "--locked" not in observed[-1] and "--frozen" not in observed[-1]


def test_missing_native_lock_and_conflicting_mode_import_fail_before_work(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# general/no-lock\n")
    (project / "pyproject.toml").write_text('[project]\nname="no-lock"\nversion="1"\n')
    (project / "goon.yml").write_text("runtime: python\nsteps:\n- run: python\n")
    with pytest.raises(FleetPlanInvalid, match="uv.lock"):
        FleetPlanResolver().resolve(project / "goon.yml")
    (project / "goon.yml").write_text(
        "uv: {lock_mode: frozen}\nenv: {UV_LOCKED: {value: '1'}}\nsteps:\n"
        "- run: uv run python\n  imports: {env: [UV_LOCKED]}\n"
    )
    with pytest.raises((FleetPlanInvalid, ValidationError), match="uv selection owns"):
        FleetPlanResolver().resolve(project / "goon.yml")


def test_mode_is_bound_to_execution_identity_and_old_contract_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "goon.yml"
    resolver = FleetPlanResolver()
    plans = []
    for mode in ("frozen", "locked"):
        path.write_text(f"uv: {{lock_mode: {mode}}}\nsteps:\n- run: uv run python check.py\n")
        plans.append(resolver.resolve(path))
    assert build_execution_lock(plans[0]).digest != build_execution_lock(plans[1]).digest
    retained = plans[0].model_dump()
    assert retained["steps"][0]["uv"]["lock_mode"] == "frozen"
    retained["contract_version"] = 45
    with pytest.raises(ValidationError, match="Incompatible resolved Plan contract"):
        ResolvedFleetPlan.model_validate(retained)
    retained["contract_version"] = 46
    retained["steps"][0]["uv"]["lock_mode"] = "locked"
    with pytest.raises(ValidationError, match="selected native lock mode"):
        ResolvedFleetPlan.model_validate(retained)
    del retained["steps"][0]["uv"]["lock_mode"]
    with pytest.raises(ValidationError, match="lock_mode"):
        ResolvedFleetPlan.model_validate(retained)


def test_non_uv_goon_runs_without_python_project_metadata(tmp_path: Path) -> None:
    path = tmp_path / "goon.yml"
    path.write_text("steps:\n- run: echo ordinary-command-ran\n")
    (tmp_path / "README.md").write_text("# general/ordinary-command\n")
    plan = FleetPlanResolver().resolve(path)
    runtime = StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None)
    outcome = execute_direct_step(tmp_path, plan.steps[0], runtime, step_order=0)
    assert outcome.returncode == 0 and "ordinary-command-ran" in outcome.stdout
    assert outcome.uv_preparation is None
    assert not (tmp_path / "pyproject.toml").exists() and not (tmp_path / "uv.lock").exists()


@pytest.mark.parametrize("mode", ("frozen", "locked"))
def test_missing_native_lock_is_not_created_during_execution(tmp_path: Path, mode: str) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="missing-lock"\nversion="1"\n')
    (tmp_path / "check.py").write_text('print("must-not-launch")\n')
    (tmp_path / "goon.yml").write_text(
        f"uv: {{lock_mode: {mode}}}\nsteps:\n- run: uv run python check.py\n"
    )
    step = FleetPlanResolver().resolve(tmp_path / "goon.yml").steps[0]
    outcome = execute_direct_step(
        tmp_path,
        step,
        StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
        step_order=0,
    )
    assert outcome.returncode != 0 and "must-not-launch" not in outcome.stdout
    assert not (tmp_path / "uv.lock").exists()
