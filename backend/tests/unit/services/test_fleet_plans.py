from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from oh_goon.core.capability_registry import capability_for
from oh_goon.core.errors import (
    FleetExtensionInvalid,
    FleetPlanInvalid,
)
from oh_goon.core.models.fleet_plan import (
    ManagedToolRuntime,
    MissingConfigPolicy,
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.models.schedule_execution import ScheduledFleetRunIntent
from oh_goon.core.vocabulary import CommandSafety, OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_compilation import load_extension_catalog
from oh_goon.integrations.gitlab_ci_component import GitLabComponentFetcher
from oh_goon.integrations.gitlab_ci_goon import GitLabCiGoonMigrator
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
        """[project]\nname = \"quality-extension\"\nversion = \"0.1.0\"\nrequires-python = ">=3.12"\n\n[project.scripts]\nquality-lint = \"quality.cli:main\"\nquality-test = \"quality.cli:test\"\n""",
        encoding="utf-8",
    )
    package = project / "quality"
    package.mkdir()
    (package / "cli.py").write_text(
        "def main(): return 0\ndef test(): return 0\n", encoding="utf-8"
    )
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


def _extension_catalog(root: Path, project: Path, *, commands: tuple[str, ...]) -> Path:
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


@pytest.fixture(autouse=True)
def _prepared_extension_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep plan/identity tests on real Git while avoiding unrelated uv materialization."""

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        environment = Path(kwargs["env"]["UV_PROJECT_ENVIRONMENT"])
        if "--check" in argv:
            return CommandResult(
                argv=tuple(argv),
                returncode=0 if environment.is_dir() else 1,
                stdout="",
                stderr="",
                duration_ms=1,
            )
        environment.mkdir(parents=True, exist_ok=True)
        return CommandResult(
            argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1
        )

    monkeypatch.setattr("oh_goon.engine.fleet_extensions.run_command", fake_run_command)


def _extension_store(root: Path) -> FleetExtensionStore:
    return FleetExtensionStore(
        root / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )


def _sync_extensions(root: Path, catalog: Path) -> tuple:
    store = _extension_store(root)
    parsed = load_extension_catalog(catalog)
    return store.sync_catalog(parsed, catalog_root=catalog.parent)


def _resolver(root: Path) -> FleetPlanResolver:
    return FleetPlanResolver(_extension_store(root), _tools(root))


def _sync_and_resolve(
    root: Path,
    plan_path: Path,
    catalog: Path,
) -> tuple[FleetPlanResolver, ResolvedFleetPlan]:
    _sync_extensions(root, catalog)
    resolver = _resolver(root)
    return resolver, resolver.resolve(plan_path, extensions_path=catalog)


def test_yaml_and_toml_resolve_to_one_stable_python_plan(tmp_path: Path) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: git\n    id: clean\n    timeout_seconds: 60\n    operation: assert-clean\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n    args: [--strict]\n""",
        encoding="utf-8",
    )
    resolver = _resolver(tmp_path)
    _sync_extensions(tmp_path, catalog)

    first = resolver.resolve(plan_path, extensions_path=catalog)
    second = resolver.resolve(plan_path, extensions_path=catalog)

    assert first == second
    assert first.plan_id == second.plan_id
    extension = first.steps[1]
    assert isinstance(extension, ResolvedExtensionStep)
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    assert store.checkout_path(extension.extension_id) != project.resolve()
    assert "project_path" not in type(extension).model_fields
    assert extension.revision == _git_output(project, "rev-parse", "HEAD")
    assert extension.entrypoint == "quality.cli:main"
    assert extension.args == ("--strict",)


def test_extension_command_must_be_explicitly_allowlisted(tmp_path: Path) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-test",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )

    resolver = _resolver(tmp_path)
    _sync_extensions(tmp_path, catalog)
    with pytest.raises(FleetExtensionInvalid, match="does not allow command"):
        resolver.resolve(plan_path, extensions_path=catalog)


def test_precommit_sidecar_template_mode_is_not_supported(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - uses: pre_commit
    id: hooks
    timeout_seconds: 3600
    mode: template
    template: precommit.yml
    if_missing: fail
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="Extra inputs are not permitted"):
        FleetPlanResolver(tools=_tools(tmp_path)).resolve(plan_path)


def test_extension_source_drift_is_rejected_before_uv_execution(tmp_path: Path) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )
    _, plan = _sync_and_resolve(tmp_path, plan_path, catalog)
    extension = plan.steps[0]
    assert isinstance(extension, ResolvedExtensionStep)
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    (store.checkout_path(extension.extension_id) / "quality" / "cli.py").write_text(
        "def main(): return 1\n", encoding="utf-8"
    )

    with pytest.raises(FleetExtensionInvalid, match="Git state changed"):
        PhysicalFleetPlanExecutorHarness(GitService(), store).prepare_direct_run(
            RepositoryIdentity(path=tmp_path), plan, cancel_requested=None
        )


def test_extension_executes_native_uv_project_script_without_changing_target_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n    args: [--strict]\n""",
        encoding="utf-8",
    )
    _, plan = _sync_and_resolve(tmp_path, plan_path, catalog)
    extension = plan.steps[0]
    assert isinstance(extension, ResolvedExtensionStep)
    target = tmp_path / "target"
    target.mkdir()
    observed: dict[str, object] = {}

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        observed["argv"] = argv
        observed["cwd"] = kwargs["cwd"]
        observed["env"] = kwargs["env"]
        return CommandResult(argv=tuple(argv), returncode=0, stdout="ok", stderr="", duration_ms=1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    results = PhysicalFleetPlanExecutorHarness(GitService(), store).execute(
        target, plan, cancel_requested=None
    )

    assert results.steps[0].returncode == 0
    checkout = store.checkout_path(extension.extension_id)
    assert observed["argv"] == (
        "uv",
        "run",
        "--project",
        str(checkout),
        "--no-sync",
        "quality-lint",
        "--strict",
    )
    assert observed["cwd"] == target
    env = observed["env"]
    assert isinstance(env, dict)
    assert env["UV_PROJECT_ENVIRONMENT"] == str(store.environment_path(extension.extension_id))
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_existing_precommit_can_be_skipped_without_repository_config(tmp_path: Path) -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="hooks",
        description="",
        steps=(
            ResolvedPreCommitStep(
                step_id="hooks",
                if_missing=MissingConfigPolicy.SKIP,
                timeout_seconds=30,
                runtime_id="tool-precommit",
                runtime_pyproject_sha256="1" * 64,
                runtime_uv_lock_sha256="2" * 64,
            ),
        ),
    )

    results = PhysicalFleetPlanExecutorHarness(GitService(), tools=_tools(tmp_path)).execute(
        tmp_path, plan, cancel_requested=None
    )

    assert results.steps[0].status == "SKIPPED"
    assert results.steps[0].reason == "Repository has no .pre-commit-config.yaml"


def test_gitlab_migration_is_argv_conservative_and_blocks_unknown_shell_semantics(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [precommit, test, shell]\nprecommit:\n  stage: precommit\n  timeout: 10 minutes\n  script: pre-commit run --all-files\npytest:\n  stage: test\n  timeout: 10 minutes\n  script: uv run pytest -q\nshell-heavy:\n  stage: shell\n  timeout: 10 minutes\n  script: uv run pytest && echo done\n""",
        encoding="utf-8",
    )

    migrated = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)

    assert len(migrated.goon.steps) == 3
    assert migrated.goon.execution.purpose.value == "cicd"
    assert migrated.goon.steps[0].kind == "pre_commit"
    command = migrated.goon.steps[1]
    assert command.kind == "command"
    assert command.argv == ("uv", "run", "pytest", "-q")
    assert migrated.ready is False
    assert any(issue.field == "script.shell" for issue in migrated.issues)


def test_fleet_plan_is_explicitly_schedulable_while_other_mutating_tasks_remain_blocked() -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="scheduled",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600, step_id="check", argv=("uv", "run", "pytest")
            ),
        ),
    )
    capability = capability_for(
        OperationAction.EXECUTE_PLAN,
        structural_fleet_plan_target(plan),
    )

    assert capability.safety is CommandSafety.OPAQUE_MUTATION
    assert capability.schedule_supported is True
    target = structural_fleet_plan_target(plan)
    intent = ScheduledFleetRunIntent(
        definition_generation_id="schedule-generation",
        definition_record_version=0,
        capability=capability.durable_snapshot(),
        target=target,
        repository_scope=ExplicitRepositoryScope(repository_ids=("repo-a",)),
        repository_instance_ids={"repo-a": "instance-scheduled-authority"},
    )
    assert intent.target == target
    assert intent.repository_scope.repository_ids == ("repo-a",)


def test_extension_import_uses_committed_revision_not_dirty_source_worktree(tmp_path: Path) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    (project / "untracked.txt").write_text("dirty", encoding="utf-8")
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )

    resolver = _resolver(tmp_path)
    _sync_extensions(tmp_path, catalog)
    plan = resolver.resolve(plan_path, extensions_path=catalog)
    extension = plan.steps[0]
    assert isinstance(extension, ResolvedExtensionStep)
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    checkout = store.checkout_path(extension.extension_id)
    assert checkout != project.resolve()
    assert GitService().observe(checkout).worktree_state.value == "CLEAN"


def test_plan_schedule_persists_exact_resolved_target_in_central_workflow(tmp_path: Path) -> None:
    from typing import cast

    from oh_goon.core.models.schedule import ScheduleCreate
    from oh_goon.services.fleet_plans import FleetPlanService
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
    from oh_goon.services.schedules import ScheduleService

    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: tests\n    run: "uv run pytest -q"\n""",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    class RecordingSchedules:
        def create_prepared(self, schedule_id: str, request: ScheduleCreate):
            observed["schedule_id"] = schedule_id
            observed["request"] = request
            return type("Created", (), {"schedule_id": schedule_id})()

    extension_store = _extension_store(tmp_path)
    tools = _tools(tmp_path)
    instance_ids = {"repo-a": "instance-7f91c2", "repo-b": "instance-c42e91"}
    runs = Mock(spec=FleetRunControlPlane)
    runs.repository_instance_ids_for_prepared_plan.return_value = instance_ids
    service = FleetPlanService(
        FleetPlanResolver(extension_store, tools),
        runs,
        cast(ScheduleService, RecordingSchedules()),
        extension_store,
        tools,
    )
    service.lock(plan_path)
    created = service.schedule(
        plan_path,
        schedule_id="nightly-quality",
        repository_ids={"repo-a", "repo-b"},
        interval_minutes=1440,
    )

    assert created.schedule_id == "nightly-quality"
    request = cast(ScheduleCreate, observed["request"])
    assert request.state.value == "active"
    assert request.interval_minutes == 1440
    assert request.repository_ids == ("repo-a", "repo-b")
    assert request.repository_instance_ids == instance_ids
    runs.repository_instance_ids_for_prepared_plan.assert_called_once_with(
        request.target, repository_ids={"repo-a", "repo-b"}
    )
    assert request.target.plan == FleetPlanResolver().resolve(plan_path)
    assert request.target.source.sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert request.target.execution_digest.startswith("sha256:")


def test_extension_resolution_requires_explicit_sync(tmp_path: Path) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )
    resolver = _resolver(tmp_path)

    with pytest.raises(FleetExtensionInvalid, match="is not imported"):
        resolver.resolve(plan_path, extensions_path=catalog)


def test_step_evidence_records_repository_state_before_and_after(
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
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="mutate",
        description="",
        steps=(ResolvedCommandStep(timeout_seconds=3600, step_id="change", argv=("fake-change",)),),
    )

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        (repository / "pyproject.toml").write_text(
            "[project]\nname='changed'\n",
            encoding="utf-8",
        )
        return CommandResult(
            argv=tuple(argv), returncode=0, stdout="changed", stderr="", duration_ms=1
        )

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    result = (
        PhysicalFleetPlanExecutorHarness(GitService())
        .execute(repository, plan, cancel_requested=None)
        .steps[0]
    )

    assert result.repository_state_before != result.repository_state_after
    assert result.worktree_files_after == ("pyproject.toml",)


def test_resolved_extension_plan_identity_does_not_depend_on_fleet_data_root(
    tmp_path: Path,
) -> None:
    project = _extension_repo(tmp_path)
    catalog = _extension_catalog(tmp_path, project, commands=("quality-lint",))
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: extension\n    id: lint\n    timeout_seconds: 3600\n    extension: quality\n    command: quality-lint\n""",
        encoding="utf-8",
    )
    first_store = FleetExtensionStore(
        tmp_path / "fleet-a" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    second_store = FleetExtensionStore(
        tmp_path / "fleet-b" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    first = FleetPlanResolver(first_store)
    second = FleetPlanResolver(second_store)
    parsed = load_extension_catalog(catalog)
    first_store.sync_catalog(parsed, catalog_root=catalog.parent)
    second_store.sync_catalog(parsed, catalog_root=catalog.parent)

    first_plan = first.resolve(plan_path, extensions_path=catalog)
    second_plan = second.resolve(plan_path, extensions_path=catalog)

    assert first_plan == second_plan
    assert first_plan.plan_id == second_plan.plan_id
    step = first_plan.steps[0]
    assert isinstance(step, ResolvedExtensionStep)
    assert "project_path" not in type(step).model_fields
    assert first_store.checkout_path(step.extension_id) != second_store.checkout_path(
        step.extension_id
    )


def test_precommit_runner_is_bound_into_resolved_plan_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.core.models.fleet_plan import DEFAULT_PRE_COMMIT_REQUIREMENT

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - uses: pre_commit\n    id: hooks\n    timeout_seconds: 3600\n    if_missing: fail\n""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver(tools=_tools(tmp_path)).resolve(plan_path)
    step = plan.steps[0]
    assert isinstance(step, ResolvedPreCommitStep)
    assert step.runner_requirement == DEFAULT_PRE_COMMIT_REQUIREMENT
    observed: dict[str, tuple[str, ...]] = {}

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        observed["argv"] = tuple(argv)
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    PhysicalFleetPlanExecutorHarness(GitService(), tools=_tools(tmp_path)).execute(
        repository, plan, cancel_requested=None
    )

    assert observed["argv"][:2] == (str(tmp_path / "fake-pre-commit"), "run")


def test_canonical_step_evidence_does_not_persist_raw_command_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "token-super-secret-value"
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="secret-output",
        description="",
        steps=(
            ResolvedCommandStep(timeout_seconds=3600, step_id="command", argv=("fake-command",)),
        ),
    )

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        return CommandResult(
            argv=tuple(argv),
            returncode=0,
            stdout=f"result {secret}",
            stderr=f"warning {secret}",
            duration_ms=1,
        )

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    execution = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        tmp_path, plan, cancel_requested=None
    )
    durable = execution.steps[0].model_dump_json()

    assert secret in execution.stdout
    assert secret in execution.stderr
    assert secret not in durable
    assert execution.steps[0].stdout_evidence.byte_count > 0
    assert execution.steps[0].stderr_evidence.byte_count > 0
