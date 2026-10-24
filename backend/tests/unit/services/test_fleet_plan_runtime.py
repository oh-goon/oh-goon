from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetExtensionInvalid, FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    ExtensionDeclaration,
    ManagedToolRuntime,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_compilation import load_extension_catalog
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _extension_repo(root: Path) -> Path:
    project = root / "quality-extension"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """[project]\nname = \"quality-extension\"\nversion = \"0.1.0\"\nrequires-python = ">=3.12"\n\n[project.scripts]\nquality-lint = \"quality.cli:main\"\n""",
        encoding="utf-8",
    )
    package = project / "quality"
    package.mkdir()
    (package / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
    (project / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n\n'
        '[[package]]\nname = "quality-extension"\nversion = "0.1.0"\n'
        'source = { virtual = "." }\n',
        encoding="utf-8",
    )
    _git(project, "init", "-q", "-b", "main")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", ".")
    _git(project, "commit", "-q", "-m", "extension baseline")
    return project


def _extension_catalog(
    root: Path,
    project: Path,
    *,
    commands: tuple[str, ...] = ("quality-lint",),
) -> Path:
    catalog = root / "extensions.toml"
    command_values = ", ".join(f'"{command}"' for command in commands)
    catalog.write_text(
        f"""version = 2\n\n[[extensions]]\nname = \"quality\"\nsource = \"{project.name}\"\nrevision = \"{_git_output(project, "rev-parse", "HEAD")}\"\ncommands = [{command_values}]\n""",
        encoding="utf-8",
    )
    return catalog


class _PreparedTools:
    def __init__(self, root: Path) -> None:
        self.executable = root / "fake-pre-commit"
        self.executable.write_text("", encoding="utf-8")
        self.runtime = ManagedToolRuntime(
            runtime_id="tool-precommit",
            tool="pre-commit",
            requirement="pre-commit==4.6.2",
            pyproject_sha256="1" * 64,
            uv_lock_sha256="2" * 64,
        )

    def prepare_pre_commit(self, requirement: str) -> ManagedToolRuntime:
        assert requirement == self.runtime.requirement
        return self.runtime

    def resolve_prepared_pre_commit(self, requirement: str) -> ManagedToolRuntime:
        assert requirement == self.runtime.requirement
        return self.runtime

    def check_pre_commit(self, **kwargs) -> Path:
        assert kwargs["runtime_id"] == self.runtime.runtime_id
        return self.executable


def _tools(root: Path) -> FleetToolStore:
    return cast(FleetToolStore, _PreparedTools(root))


def _extension_store(root: Path) -> FleetExtensionStore:
    return FleetExtensionStore(
        root / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )


def _sync_extensions(root: Path, catalog: Path) -> tuple:
    store = _extension_store(root)
    return store.sync_catalog(load_extension_catalog(catalog), catalog_root=catalog.parent)


def _resolver(root: Path) -> FleetPlanResolver:
    return FleetPlanResolver(_extension_store(root), _tools(root))


def test_extension_local_source_must_be_a_directory_before_git_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "extension.txt"
    source.write_text("not a source directory", encoding="utf-8")
    git_acquisition = Mock(side_effect=AssertionError("Git must not acquire an invalid source"))
    monkeypatch.setattr(
        "oh_goon.engine.fleet_extensions.capture_native_git_authority", git_acquisition
    )
    with pytest.raises(FleetPlanInvalid, match="local extension source is not a directory"):
        _extension_store(tmp_path).sync(
            ExtensionDeclaration(
                name="quality", source=source.name, revision="a" * 40, commands=("quality-lint",)
            ),
            catalog_root=tmp_path,
        )
    git_acquisition.assert_not_called()


def test_extension_runtime_must_be_prepared_and_sync_repairs_missing_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )
    resolver = _resolver(tmp_path)
    imported = _sync_extensions(tmp_path, catalog)[0]
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    shutil.rmtree(store.environment_path(imported.extension_id))

    with pytest.raises(FleetExtensionInvalid, match="runtime is not prepared"):
        resolver.resolve(plan_path, extensions_path=catalog)

    _sync_extensions(tmp_path, catalog)
    assert (
        resolver.resolve(plan_path, extensions_path=catalog).steps[0].extension_id
        == imported.extension_id
    )

    calls: list[tuple[str, ...]] = []

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        calls.append(tuple(argv))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr("oh_goon.engine.fleet_extensions.run_command", fake_run_command)
    _sync_extensions(tmp_path, catalog)
    assert len(calls) == 1
    assert "--check" in calls[0]


def test_atomic_goon_cannot_hide_composition_in_steps(tmp_path: Path) -> None:
    goon = tmp_path / "goon.yml"
    goon.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - uses: template
    id: outside
    template: general/other
""",
        encoding="utf-8",
    )

    with pytest.raises(FleetPlanInvalid, match="unsupported typed executor: template"):
        FleetPlanResolver(tools=_tools(tmp_path)).resolve(goon)


def test_plan_environment_is_explicitly_allowlisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLEET_ALLOWED_TOKEN", "allowed-value")
    monkeypatch.setenv("FLEET_HIDDEN_TOKEN", "hidden-value")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="FLEET_ALLOWED_TOKEN",
                        parts=(HostEnvironmentPart(name="FLEET_ALLOWED_TOKEN"),),
                    ),
                ),
            ),
        ),
    )
    observed: dict[str, object] = {}

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        observed["env"] = kwargs["env"]
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    PhysicalFleetPlanExecutorHarness(GitService()).execute(tmp_path, plan, cancel_requested=None)

    env = cast(dict[str, str], observed["env"])
    assert env["FLEET_ALLOWED_TOKEN"] == "allowed-value"
    assert "FLEET_HIDDEN_TOKEN" not in env


@pytest.mark.parametrize(
    "control_name",
    (
        "OH_GOON_DB",
        "OH_GOON_EXECUTORS_FILE",
        "OH_GOON_LEAGUE_FILE",
        "OH_GOON_LOCAL_ACCESS_TOKEN",
        "OH_GOON_SQLITE_JOURNAL_MODE",
    ),
)
def test_plan_cannot_import_control_plane_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_name: str,
) -> None:
    monkeypatch.setenv(control_name, "must-not-reach-step")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="CONTROL_PLANE_TOKEN",
                        parts=(HostEnvironmentPart(name=control_name),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(
        FleetPlanInvalid, match="cannot import process-local control-plane environment value"
    ):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


@pytest.mark.parametrize(
    "control_name",
    (
        "oh_goon_local_access_token",
        "Oh_Goon_Db",
        "oh_goon_sqlite_journal_mode",
    ),
)
def test_plan_cannot_import_control_plane_authority_with_case_variant(
    tmp_path: Path,
    control_name: str,
) -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="CONTROL_PLANE_TOKEN",
                        parts=(HostEnvironmentPart(name=control_name),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(
        FleetPlanInvalid, match="cannot import process-local control-plane environment value"
    ):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


@pytest.mark.parametrize(
    "control_name",
    ("oh_goon_db", "Oh_Goon_Local_Access_Token"),
)
def test_plan_cannot_publish_under_case_variant_control_plane_name(
    tmp_path: Path, control_name: str
) -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name=control_name,
                        parts=(LiteralEnvironmentPart(value="must-not-be-published"),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(
        FleetPlanInvalid, match="cannot import process-local control-plane environment value"
    ):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


def test_plan_cannot_publish_literal_value_under_control_plane_environment_name(
    tmp_path: Path,
) -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="OH_GOON_DB",
                        parts=(LiteralEnvironmentPart(value="/tmp/not-the-controller.db"),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(
        FleetPlanInvalid, match="cannot import process-local control-plane environment value"
    ):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


def test_plan_preflight_rejects_missing_declared_environment(tmp_path: Path) -> None:
    os.environ.pop("OH_GOON_TEST_MISSING_ENV", None)
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="environment",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="command",
                argv=("fake-command",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="OH_GOON_TEST_MISSING_ENV",
                        parts=(HostEnvironmentPart(name="OH_GOON_TEST_MISSING_ENV"),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(FleetPlanInvalid, match="requires missing imported host environment values"):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


def test_local_git_actions_are_typed_and_execute_through_git_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "baseline")
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - uses: git
    id: branch
    timeout_seconds: 60
    operation: create-branch
    branch: fleet/change
  - id: change
    run: "fake-change"
  - uses: git
    id: add
    timeout_seconds: 60
    operation: add
    paths: [pyproject.toml]
  - uses: git
    id: commit
    timeout_seconds: 60
    operation: commit
    message: Fleet change
""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver().resolve(plan_path)

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        assert kwargs["cwd"] == repository
        if argv and argv[0] == "git":
            completed = subprocess.run(
                argv, cwd=repository, check=False, capture_output=True, text=True
            )
            return CommandResult(
                argv=tuple(argv),
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=1,
            )
        assert argv == ("fake-change",)
        (repository / "pyproject.toml").write_text(
            "[project]\nname='changed'\n",
            encoding="utf-8",
        )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    result = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        repository, plan, cancel_requested=None
    )

    assert len(result.steps) == 4
    assert _git_output(repository, "branch", "--show-current") == "fleet/change"
    assert _git_output(repository, "log", "-1", "--pretty=%s") == "Fleet change"
    assert GitService().observe(repository).worktree_state.value == "CLEAN"


def test_plan_reschedule_uses_schedule_generation_and_version_cas(tmp_path: Path) -> None:
    from oh_goon.core.models.schedule import ScheduleDefinition, ScheduleReplace
    from oh_goon.core.time import utc_now
    from oh_goon.core.vocabulary import ScheduleState
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plans import FleetPlanService
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
    from oh_goon.services.schedules import ScheduleService

    goon_root = tmp_path / "goons"
    for name, step_id, argv in (
        ("first", "first", "[uv, run, pytest]"),
        ("second", "second", "[uv, run, ruff, check]"),
    ):
        package = goon_root / "general" / name
        package.mkdir(parents=True)
        (package / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        (package / "goon.yml").write_text(
            f"""version: 1
steps:
  - id: {step_id}
    run: {argv.strip("[]").replace(",", "")}
""",
            encoding="utf-8",
        )
    initial_path = tmp_path / "initial.yml"
    initial_path.write_text(
        """version: 1
goons:
  - import: general/first
""",
        encoding="utf-8",
    )
    replacement_path = tmp_path / "replacement.yml"
    replacement_path.write_text(
        """version: 1
goons:
  - import: general/second
""",
        encoding="utf-8",
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goon_root))
    initial = resolver.resolve(initial_path)
    now = utc_now()
    current = ScheduleDefinition(
        schedule_id="plan-schedule",
        generation_id="generation-1",
        record_version=7,
        name="scheduled",
        repository_ids=("repo-a",),
        repository_instance_ids={"repo-a": "instance-before-reschedule"},
        target=structural_fleet_plan_target(initial),
        interval_minutes=60,
        state=ScheduleState.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    observed: dict[str, object] = {}

    class RecordingSchedules:
        def definition(self, schedule_id: str) -> ScheduleDefinition:
            assert schedule_id == "plan-schedule"
            return current

        def replace_prepared(
            self, schedule_id: str, request: ScheduleReplace
        ) -> ScheduleDefinition:
            observed["schedule_id"] = schedule_id
            observed["request"] = request
            return current

    extension_store = _extension_store(tmp_path)
    tools = _tools(tmp_path)
    instance_ids = {"repo-a": "instance-c42e91"}
    runs = Mock(spec=FleetRunControlPlane)
    runs.repository_instance_ids_for_prepared_plan.return_value = instance_ids
    service = FleetPlanService(
        resolver,
        runs,
        cast(ScheduleService, RecordingSchedules()),
        extension_store,
        tools,
    )
    service.lock(replacement_path)
    service.reschedule(
        replacement_path,
        schedule_id="plan-schedule",
        repository_ids={"repo-a"},
        interval_minutes=120,
    )

    request = cast(ScheduleReplace, observed["request"])
    assert request.generation_id == "generation-1"
    assert request.record_version == 7
    assert request.state is ScheduleState.ACTIVE
    assert request.interval_minutes == 120
    assert request.repository_ids == ("repo-a",)
    assert request.repository_instance_ids == instance_ids
    runs.repository_instance_ids_for_prepared_plan.assert_called_once_with(
        request.target, repository_ids={"repo-a"}
    )
    assert request.target.plan.plan_id == resolver.resolve(replacement_path).plan_id


def test_verification_plan_preview_projects_imports_before_run_admission(tmp_path: Path) -> None:
    """Verification imports have one complete pre-RUN_ADMITTED preview."""
    from oh_goon.integrations.fleet_plan_preview import preview_plan
    from oh_goon.runtime.goon_templates import GoonTemplateStore

    goon_root = tmp_path / "goons"
    package = goon_root / "general" / "verify"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# verify\n", encoding="utf-8")
    (package / "goon.yml").write_text(
        """version: 1
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
goons:
  - import: general/verify
""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(goon_root)).resolve(plan_path)
    repository = tmp_path / "repo"
    repository.mkdir()

    preview = preview_plan(repository, plan)

    assert len(preview.imports) == 1
    assert preview.imports[0].applicability == "APPLICABLE"
    assert len(preview.steps) == 1
    assert preview.steps[0].disposition.value == "READY"
