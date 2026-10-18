from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from oh_goon import __version__
from oh_goon.core.capability_registry import capability_for
from oh_goon.core.errors import MigrationSourceUnsupported
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.vocabulary import FleetRunSource, FleetRunStatus, MigrationSource, OperationAction, UvRecipeId
from oh_goon.integrations.migration_plan import automatic_migration_plan
from oh_goon.integrations.recipe_advisories import extract_security_findings
from oh_goon.integrations.recipe_command import prepare_recipe_command
from tests.factories.run_admission import repository_admissions_for
from tests.factories.schedules import persist_active_schedule, schedule_create

ROOT = Path(__file__).resolve().parents[4]


def test_release_identity_includes_frontend_and_openapi() -> None:
    expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    openapi = json.loads(
        (ROOT / "generated" / "openapi" / "openapi.json").read_text(encoding="utf-8")
    )

    assert __version__ == expected
    assert project["project"]["version"] == expected
    assert frontend["version"] == expected
    assert openapi["info"]["version"] == expected


def test_recipe_command_owner_cleans_temporary_build_output() -> None:
    target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.BUILD)
    with prepare_recipe_command(target, capability_for(OperationAction.RECIPE, target)) as prepared:
        assert prepared.temporary_output is not None
        temporary_path = Path(prepared.temporary_output.name)
        assert temporary_path.is_dir()
        assert str(temporary_path) in prepared.argv
    assert not temporary_path.exists()


def test_recipe_advisory_parser_preserves_nested_fixed_versions() -> None:
    findings = extract_security_findings(
        {
            "package": {"name": "demo", "version": "1.0"},
            "advisories": [
                {
                    "id": "GHSA-demo",
                    "aliases": ["CVE-2026-0001"],
                    "summary": "demo advisory",
                    "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.2"}]}]}],
                }
            ],
        }
    )

    assert len(findings) == 1
    assert findings[0].advisory_id == "GHSA-demo"
    assert findings[0].package == "demo"
    assert findings[0].version == "1.0"
    assert findings[0].fixed_versions == ("1.2",)


def test_migration_plan_is_pure_and_source_specific() -> None:
    pyproject = automatic_migration_plan(
        MigrationSource.PYPROJECT,
        has_compiled_requirements=False,
    )
    assert pyproject.steps[0] == ("uv", "lock")
    assert pyproject.allowed_changed_files == frozenset({"uv.lock"})

    pip_tools = automatic_migration_plan(
        MigrationSource.PIP_TOOLS,
        has_compiled_requirements=True,
    )
    assert pip_tools.steps[1][-2:] == ("-c", "requirements.txt")
    assert pip_tools.allowed_changed_files == frozenset({"pyproject.toml", "uv.lock"})


def test_migration_plan_rejects_unsupported_source_with_typed_domain_error() -> None:
    with pytest.raises(MigrationSourceUnsupported):
        automatic_migration_plan(
            MigrationSource.POETRY,
            has_compiled_requirements=False,
        )


def _running_fleet_detail(execution, repository):
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id
    from oh_goon.core.models.run import FleetRunDetail, FleetRunProgress, RunningFleetRun
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        ExplicitRepositoryScope,
    )
    from oh_goon.core.time import utc_now

    now = utc_now()
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(execution.intent.target, (repository,)),
        run=RunningFleetRun(
            status=FleetRunStatus.RUNNING,
            fleet_run_id=schedule_repository_fleet_run_id(
                execution.schedule_run_id, repository.repository_id
            ),
            source=FleetRunSource.SCHEDULED,
            target=execution.intent.target,
            capability=execution.intent.capability,
            repository_scope=ExplicitRepositoryScope(repository_ids=(repository.repository_id,)),
            targets=(repository,),
            accepted_at=now,
            started_at=now,
            progress=FleetRunProgress(total=0, succeeded=0, failed=0),
        ),
        execution=DirectFleetExecutionAdmission(),
    )


def _completed_fleet_detail(execution, repository):
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id
    from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        ExplicitRepositoryScope,
    )
    from oh_goon.core.time import utc_now

    now = utc_now()
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(execution.intent.target, (repository,)),
        run=CompletedFleetRun(
            status=FleetRunStatus.COMPLETED,
            fleet_run_id=schedule_repository_fleet_run_id(
                execution.schedule_run_id, repository.repository_id
            ),
            source=FleetRunSource.SCHEDULED,
            target=execution.intent.target,
            capability=execution.intent.capability,
            repository_scope=ExplicitRepositoryScope(repository_ids=(repository.repository_id,)),
            targets=(repository,),
            accepted_at=now,
            started_at=now,
            finished_at=now,
            progress=FleetRunProgress(total=0, succeeded=0, failed=0),
        ),
        execution=DirectFleetExecutionAdmission(),
    )


def test_schedule_advance_observes_active_fleet_run_once(tmp_path: Path) -> None:
    """A Fleet Run that changes after the observation must never be observed twice in one advance."""
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-race.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    definition = definitions.create(
        "schedule-single-observation",
        schedule_create(name="Single observation", repository_ids=(repository.repository_id,)),
    )
    execution = persist_active_schedule(database, executions, definition)
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id
    from oh_goon.core.schedule_transitions import mark_repository_running

    execution = executions.compare_and_set(
        execution,
        mark_repository_running(
            execution,
            repository.repository_id,
            schedule_repository_fleet_run_id(execution.schedule_run_id, repository.repository_id),
        ),
    )
    running = _running_fleet_detail(execution, repository)
    completed = _completed_fleet_detail(execution, repository)

    class _ChangingRunReader:
        def __init__(self) -> None:
            self.get_calls = 0
            self.start_calls = 0

        def get(self, _fleet_run_id: str):
            self.get_calls += 1
            return running if self.get_calls == 1 else completed

        def start_bound(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.start_calls += 1
            raise AssertionError(
                "same scheduled Fleet Run must not be started from a second observation"
            )

    runs = _ChangingRunReader()
    engine = ScheduleExecutionEngine(executions, runs)  # type: ignore[arg-type]
    result = engine.advance(execution.schedule_run_id)
    assert result == execution
    assert runs.get_calls == 1
    assert runs.start_calls == 0


def test_schedule_records_terminal_fleet_run_while_new_effect_admission_is_blocked(
    tmp_path: Path,
) -> None:
    """Safety degradation must not hide already-completed Fleet Run evidence."""
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.schedule_execution import CompletedScheduleRun
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-safety-progression.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    definition = definitions.create(
        "schedule-safety-progression",
        schedule_create(name="Safety progression", repository_ids=(repository.repository_id,)),
    )
    execution = persist_active_schedule(database, executions, definition)
    from oh_goon.core.schedule_transitions import mark_repository_running

    execution = executions.compare_and_set(
        execution,
        mark_repository_running(
            execution,
            repository.repository_id,
            schedule_repository_fleet_run_id(execution.schedule_run_id, repository.repository_id),
        ),
    )
    completed = _completed_fleet_detail(execution, repository)

    class _Runs:
        def __init__(self) -> None:
            self.start_calls = 0

        def get(self, _fleet_run_id: str):
            return completed

        def start_bound(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.start_calls += 1
            raise AssertionError("terminal evidence must not admit another Fleet Run")

    runs = _Runs()
    engine = ScheduleExecutionEngine(executions, runs)  # type: ignore[arg-type]

    result = engine.advance(execution.schedule_run_id, allow_effect_admission=False)

    assert isinstance(result, CompletedScheduleRun)
    assert result.repository_runs[0].fleet_run_id == schedule_repository_fleet_run_id(
        execution.schedule_run_id, repository.repository_id
    )
    assert runs.start_calls == 0


def test_schedule_admission_busy_is_recorded_once_instead_of_hidden_retry(tmp_path: Path) -> None:
    """One Schedule execution never silently retries a failed Fleet Run admission."""
    from oh_goon.core.errors import ExecutionBusy, RunNotFound
    from oh_goon.core.models.schedule_execution import FailedScheduleRun
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-admission-busy.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create(
        "schedule-admission-busy",
        schedule_create(name="Admission busy", repository_ids=("repo-a",)),
    )
    execution = persist_active_schedule(database, executions, definition)

    class _Runs:
        def __init__(self) -> None:
            self.start_calls = 0

        def get(self, _fleet_run_id: str):
            raise RunNotFound("not admitted")

        def start_bound(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.start_calls += 1
            raise ExecutionBusy("executor admission is busy")

    runs = _Runs()
    engine = ScheduleExecutionEngine(executions, runs)  # type: ignore[arg-type]

    first = engine.advance(execution.schedule_run_id)
    second = engine.advance(execution.schedule_run_id)

    assert isinstance(first, FailedScheduleRun)
    assert first.failure.code == "EXECUTION_BUSY"
    assert first.failure.retryable is True
    assert second == first
    assert runs.start_calls == 1


def test_schedule_occurrence_fans_out_one_canonical_run_per_repository_in_target_order(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.schedule_execution import CompletedScheduleRun
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-fanout.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    repositories = sorted(
        (
            RepositoryIdentity(path=tmp_path / "repo-a"),
            RepositoryIdentity(path=tmp_path / "repo-b"),
        ),
        key=lambda item: item.repository_id,
    )
    definition = definitions.create(
        "schedule-fanout",
        schedule_create(repository_ids=tuple(item.repository_id for item in repositories)),
    )
    execution = persist_active_schedule(database, executions, definition)

    class _Runs:
        def __init__(self) -> None:
            self.started: list[tuple[str, str]] = []

        def start_bound(self, *args: object, **kwargs: object) -> object:
            del args
            scope = kwargs["repository_scope"]
            run_id = kwargs["fleet_run_id"]
            repository_id = scope.repository_ids[0]
            self.started.append((repository_id, run_id))
            # Simulate the original Fleet admission commit, which now binds
            # the Schedule row before start_bound returns.
            from oh_goon.core.schedule_transitions import mark_repository_running
            before = executions.get(execution.schedule_run_id)
            executions.compare_and_set(before, mark_repository_running(before, repository_id, run_id))
            return object()

        def get(self, fleet_run_id: str):
            repository = next(
                item
                for item in repositories
                if item.repository_id in dict(self.started)
                and dict(self.started)[item.repository_id] == fleet_run_id
            )
            return _completed_fleet_detail(executions.get(execution.schedule_run_id), repository)

    runs = _Runs()
    engine = ScheduleExecutionEngine(executions, runs)  # type: ignore[arg-type]
    engine.advance(execution.schedule_run_id)
    engine.advance(execution.schedule_run_id)
    engine.advance(execution.schedule_run_id)
    result = engine.advance(execution.schedule_run_id)

    assert isinstance(result, CompletedScheduleRun)
    assert [item[0] for item in runs.started] == [item.repository_id for item in repositories]
    assert len({run_id for _, run_id in runs.started}) == 2
    assert all(row.fleet_run_id is not None for row in result.repository_runs)


def test_schedule_rejects_child_run_with_mismatched_structural_binding(tmp_path: Path) -> None:
    from oh_goon.core.errors import JournalConflict
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.schedule_transitions import mark_repository_running
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-binding.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    definition = definitions.create(
        "schedule-binding",
        schedule_create(name="Binding", repository_ids=(repository.repository_id,)),
    )
    execution = persist_active_schedule(database, executions, definition)
    execution = executions.compare_and_set(
        execution,
        mark_repository_running(
            execution,
            repository.repository_id,
            schedule_repository_fleet_run_id(execution.schedule_run_id, repository.repository_id),
        ),
    )
    wrong = _completed_fleet_detail(execution, repository)
    wrong = wrong.model_copy(update={"run": wrong.run.model_copy(update={"source": FleetRunSource.MANUAL})})

    class _Runs:
        def get(self, _fleet_run_id: str):
            return wrong

    with pytest.raises(JournalConflict, match="another execution source"):
        ScheduleExecutionEngine(executions, _Runs()).advance(execution.schedule_run_id)  # type: ignore[arg-type]


def test_schedule_child_run_uses_frozen_repository_instance_authority(tmp_path: Path) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.services.schedule_execution import ScheduleExecutionEngine

    database = LocalDatabase(tmp_path / "schedule-instance-authority.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    request = schedule_create(name="Instance authority", repository_ids=(repository.repository_id,))
    frozen = "frozen-repository-instance"
    request = request.model_copy(
        update={"repository_instance_ids": {repository.repository_id: frozen}}
    )
    definition = definitions.create("schedule-instance-authority", request)
    execution = persist_active_schedule(database, executions, definition)

    class _Runs:
        def __init__(self) -> None:
            self.expected = None

        def start_bound(self, *args: object, **kwargs: object) -> object:
            del args
            self.expected = kwargs["expected_repository_instance_ids"]
            from oh_goon.core.schedule_transitions import mark_repository_running
            before = executions.get(execution.schedule_run_id)
            executions.compare_and_set(before, mark_repository_running(
                before, repository.repository_id, kwargs["fleet_run_id"]
            ))
            return object()

    runs = _Runs()
    ScheduleExecutionEngine(executions, runs).advance(execution.schedule_run_id)  # type: ignore[arg-type]
    assert runs.expected == {repository.repository_id: frozen}
