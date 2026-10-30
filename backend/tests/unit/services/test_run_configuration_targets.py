from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from typing import NoReturn

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.cli_configuration import (
    prepare_configuration,
    register_prepared_configuration_target,
)
from oh_goon.cli_lock import generate_stateless_lock
from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES
from oh_goon.core.errors import (
    DatabaseSchemaMismatch,
    FleetPlanInvalid,
    FleetPlanSourceStale,
    ScheduleNotFound,
    TargetSelectionError,
)
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.league import LeagueService
from oh_goon.services.league_lineups import LeagueLineupService
from oh_goon.services.locations import LocationService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.run_configuration import (
    capture_run_configuration_source,
    load_run_configuration,
    resolve_run_configuration_target,
    resolve_run_configuration_targets,
)
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.local_state import local_state

type Selection = tuple[Path, Path, Path]


@pytest.fixture
def selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Selection:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    path = workflow / "run.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "python/quality"},
                "target": {"repository": "../repository"},
            }
        )
    )
    preferences = tmp_path / "league.yml"
    preferences.write_text("version: 1\nfolders: []\n")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state.db"))
    return path, repository, preferences


def _args(path: Path, *, register: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        command="workflow",
        workflow_command="prepare",
        configuration=path,
        register_target=register,
        update_goons=False,
        update_applications=False,
        update_skills=False,
    )


def _plural_selection(
    selection: Selection, tmp_path: Path, *, second_git: bool = True
) -> tuple[Path, Path, Path, Path]:
    path, first, preferences = selection
    second = tmp_path / "second-repository"
    second.mkdir()
    if second_git:
        (second / ".git").mkdir()
    payload = yaml.safe_load(path.read_text())
    payload["target"] = {"repositories": ["../repository", "../second-repository"]}
    path.write_text(yaml.safe_dump(payload))
    return path, first, second, preferences


def test_plural_scope_and_registration_keep_every_target_without_a_primary(
    selection: Selection, tmp_path: Path
) -> None:
    path, first, second, preferences = _plural_selection(selection, tmp_path)
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(first)]}))
    inspected = _args(path)
    inspected.workflow_command = "inspect"
    prepare_configuration(inspected)
    selected = inspected._run_configuration
    assert tuple(item.path for item in selected.repositories) == (first, second)
    assert selected.registrations == (True, False)
    assert not selected.registered
    assert selected.resolution_repository is None
    assert selected.repository_instance_ids == {
        member.repository.repository_id: member.repository_instance_id
        for member in inspected._configuration_targets
    }
    with pytest.raises(FleetPlanInvalid, match="single repository context"):
        _ = selected.repository
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.reconcile_league_folders()
    args = _args(path, register=True)
    prepare_configuration(args)
    captured_graph = args._source_graph
    register_prepared_configuration_target(args, LocationService(state))
    assert args._source_graph is captured_graph
    assert args._run_configuration.registered
    assert [row["path"] for row in args._target_registration["repositories"]] == [
        str(first),
        str(second),
    ]
    assert LeagueFolderStore(preferences).list() == (first, second)
    assert state.read_view().locations == (first, second)


@pytest.mark.parametrize("alias", ["../repository/../repository", "absolute"])
def test_plural_path_aliases_are_rejected_without_scope_or_source_effects(
    selection: Selection, tmp_path: Path, alias: str
) -> None:
    path, repository, preferences = selection
    payload = yaml.safe_load(path.read_text())
    payload["target"] = {
        "repositories": ["../repository", str(repository) if alias == "absolute" else alias]
    }
    path.write_text(yaml.safe_dump(payload))
    before = preferences.read_bytes()
    with pytest.raises(FleetPlanInvalid, match="distinct canonical"):
        resolve_run_configuration_targets(load_run_configuration(path))
    assert preferences.read_bytes() == before
    assert not (tmp_path / "state.db").exists()
    assert not (path.parent / "goon.lock").exists()


def test_replacement_of_any_captured_target_refuses_before_source_acquisition(
    selection: Selection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, second, _ = _plural_selection(selection, tmp_path)
    snapshot = load_run_configuration(path)
    targets = resolve_run_configuration_targets(snapshot)
    second.rename(tmp_path / "retired-second")
    (second / ".git").mkdir(parents=True)
    resolver = FleetPlanResolver()

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("A replaced target cannot acquire or analyze selected source")

    monkeypatch.setattr(resolver, "analyze_source", forbidden)
    with pytest.raises(FleetPlanInvalid, match="changed before source acquisition"):
        capture_run_configuration_source(
            snapshot, (), resolver, targets=targets, acquire=True, allow_network=True
        )
    assert not (path.parent / "goon.lock").exists()


def test_every_registration_root_is_validated_before_any_registration_effect(
    selection: Selection, tmp_path: Path
) -> None:
    path, _, _, preferences = _plural_selection(selection, tmp_path, second_git=False)
    before = preferences.read_bytes()
    with pytest.raises(FleetPlanInvalid, match="exact Git root"):
        prepare_configuration(_args(path, register=True))
    assert preferences.read_bytes() == before
    assert not (tmp_path / "state.db").exists()


@pytest.mark.parametrize("mutation", ["replacement", "exclusion"])
def test_registration_rechecks_every_member_before_registering_the_first(
    selection: Selection, tmp_path: Path, mutation: str
) -> None:
    path, _, second, preferences = _plural_selection(selection, tmp_path)
    args = _args(path, register=True)
    prepare_configuration(args)
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    if mutation == "replacement":
        second.rename(tmp_path / "retired-second")
        (second / ".git").mkdir(parents=True)
    else:
        state.exclude(second, expected_durable_revision=state.read_view().durable_revision)
    before = state.read_view()
    with pytest.raises(FleetPlanInvalid, match="changed before registration|excluded"):
        register_prepared_configuration_target(args, LocationService(state))
    assert state.read_view() == before
    assert LeagueFolderStore(preferences).list() == ()


def test_file_or_directory_selection_is_exact_and_cwd_independent(
    selection: Selection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, repository, _ = selection
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "run.yml").write_text("not the selected configuration")
    monkeypatch.chdir(decoy)
    file_snapshot = load_run_configuration(path)
    directory_snapshot = load_run_configuration(path.parent)
    assert file_snapshot == directory_snapshot
    target = resolve_run_configuration_target(file_snapshot)
    assert target.repository.path == repository
    assert not target.registered
    assert target.repository_instance_id == repository_subject_instance_id(repository)
    assert not (tmp_path / "state.db").exists()


def test_relative_target_relocates_with_workflow_pair(selection: Selection, tmp_path: Path) -> None:
    path, repository, _ = selection
    destination = tmp_path / "relocated"
    destination.mkdir()
    moved_workflow = destination / "workflow"
    path.parent.rename(moved_workflow)
    moved_repository = destination / "repository"
    repository.rename(moved_repository)
    target = resolve_run_configuration_target(load_run_configuration(moved_workflow))
    assert target.repository.path == moved_repository


@pytest.mark.parametrize("operation,code", [("inspect", 0), ("validate", 2)])
def test_unregistered_inspection_reports_selected_target_without_state_writes(
    selection: Selection,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    code: int,
) -> None:
    path, repository, preferences = selection
    before = preferences.read_bytes()
    assert main(["workflow", operation, str(path.parent), "--json"]) == code
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "registration-unavailable"
    assert report["target"]["path"] == str(repository)
    assert report["target"]["registration"] == "unavailable"
    assert report["target"]["authority"] == "selected;not-admitted"
    assert report["proof"]["admission"] == "not-started"
    assert preferences.read_bytes() == before
    assert not (tmp_path / "state.db").exists()
    assert not (path.parent / "goon.lock").exists()


def test_prepare_requires_explicit_registration_before_source_work(
    selection: Selection, tmp_path: Path
) -> None:
    path, _, _ = selection
    with pytest.raises(FleetPlanInvalid, match="registered repository"):
        prepare_configuration(_args(path))
    assert not (tmp_path / "state.db").exists()
    assert not (path.parent / "goon.lock").exists()


def test_registration_persists_only_selected_root_and_reuses_captured_source(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, preferences = selection
    sibling = tmp_path / "sibling"
    (sibling / ".git").mkdir(parents=True)
    args = _args(path, register=True)
    prepare_configuration(args)
    graph = args._source_graph
    assert not args._run_configuration.registered
    assert not (tmp_path / "state.db").exists()
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    service = LocationService(state)
    register_prepared_configuration_target(args, service)
    assert args._source_graph is graph
    assert args._run_configuration.registered
    assert args._target_registration == {
        "path": str(repository),
        "repository_id": args._run_configuration.repository.repository_id,
        "status": "registered",
    }
    assert LeagueFolderStore(preferences).list() == (repository,)
    assert state.read_view().locations == (repository,)
    assert sibling not in state.read_view().locations
    again = _args(path, register=True)
    prepare_configuration(again)
    assert again._target_registration["status"] == "already-registered"
    assert LeagueFolderStore(preferences).list() == (repository,)


def test_invalid_input_fails_before_registration_state_is_opened(
    selection: Selection, tmp_path: Path
) -> None:
    path, _, preferences = selection
    data = yaml.safe_load(path.read_text())
    data["inputs"] = {"typechecker": "invalid"}
    path.write_text(yaml.safe_dump(data))
    before = preferences.read_bytes()
    with pytest.raises(FleetPlanInvalid):
        prepare_configuration(_args(path, register=True))
    assert preferences.read_bytes() == before
    assert not (tmp_path / "state.db").exists()


def test_existing_exact_filesystem_subject_needs_no_git_or_runtime(selection: Selection) -> None:
    path, repository, preferences = selection
    (repository / ".git").rmdir()
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    args = _args(path)
    prepare_configuration(args)
    assert args._run_configuration.registered
    assert args._run_configuration.repository.path == repository
    assert not (repository / ".git").exists()


def test_target_exclusion_is_observed_without_runtime_startup(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, preferences = selection
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.reconcile_league_folders()
    state.exclude(repository, expected_durable_revision=state.read_view().durable_revision)
    before = state.read_view()
    with pytest.raises(FleetPlanInvalid, match="excluded"):
        resolve_run_configuration_target(load_run_configuration(path))
    after = state.read_view()
    assert after.durable_revision == before.durable_revision
    assert after.exclusions == before.exclusions


def test_replaced_target_is_rejected_by_registration_owner(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, preferences = selection
    args = _args(path, register=True)
    prepare_configuration(args)
    repository.rename(tmp_path / "retired")
    (repository / ".git").mkdir(parents=True)
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    with pytest.raises(FleetPlanInvalid, match="changed before registration"):
        register_prepared_configuration_target(args, LocationService(state))
    assert LeagueFolderStore(preferences).list() == ()
    assert state.read_view().locations == ()


def test_excluded_after_preflight_cannot_be_registered(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, preferences = selection
    args = _args(path, register=True)
    prepare_configuration(args)
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.exclude(repository, expected_durable_revision=state.read_view().durable_revision)
    with pytest.raises(FleetPlanInvalid, match="excluded"):
        register_prepared_configuration_target(args, LocationService(state))
    assert LeagueFolderStore(preferences).list() == ()
    assert state.read_view().locations == ()


def test_nested_directory_never_substitutes_containing_registered_git_root(
    selection: Selection,
) -> None:
    path, repository, preferences = selection
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    nested = repository / "nested"
    nested.mkdir()
    payload = yaml.safe_load(path.read_text())
    payload["target"]["repository"] += "/nested"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(FleetPlanInvalid, match="exact repository root"):
        resolve_run_configuration_target(load_run_configuration(path))


def test_target_alias_with_parent_traversal_is_not_canonical_authority(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, _ = selection
    alias = path.parent / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    payload = yaml.safe_load(path.read_text())
    payload["target"]["repository"] = "alias/../repository"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        resolve_run_configuration_target(load_run_configuration(path))


def test_initializer_logical_root_changes_only_relative_target_base(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, _ = selection
    snapshot = load_run_configuration(path)
    final = tmp_path / "unpublished-workflow"
    selected = resolve_run_configuration_target(snapshot, target_root=final)
    assert selected.repository.path == repository
    assert snapshot.root == path.parent
    assert snapshot.lock_path == path.parent / "goon.lock"


def test_existing_stale_database_is_observed_without_reset_or_schema_repair(
    selection: Selection, tmp_path: Path
) -> None:
    path, _, preferences = selection
    database_path = tmp_path / "state.db"
    LocalDatabase(database_path)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA user_version = 1")
    before = preferences.read_bytes()
    with pytest.raises(DatabaseSchemaMismatch, match="current existing local state"):
        resolve_run_configuration_target(load_run_configuration(path))
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert preferences.read_bytes() == before


def test_concurrent_registration_converges_without_duplicate_preferences(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, preferences = selection
    first, second = _args(path, register=True), _args(path, register=True)
    prepare_configuration(first)
    prepare_configuration(second)
    services = [
        LocationService(local_state(LocalDatabase(tmp_path / "state.db"))) for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(register_prepared_configuration_target, args, service)
            for args, service in zip((first, second), services, strict=True)
        ]
        for future in futures:
            future.result(timeout=10)
    assert {first._target_registration["status"], second._target_registration["status"]} == {
        "registered",
        "already-registered",
    }
    assert LeagueFolderStore(preferences).list() == (repository,)


def test_ambiguous_committed_registration_retains_owner_receipt(
    selection: Selection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, repository, preferences = selection
    args = _args(path, register=True)
    prepare_configuration(args)
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    original = database.transaction
    reply_lost = False

    @contextmanager
    def ambiguous_transaction(*, write: bool = False) -> Iterator[sqlite3.Connection]:
        nonlocal reply_lost
        with original(write=write) as connection:
            yield connection
        if write and not reply_lost:
            reply_lost = True
            raise OSError("commit response lost")

    monkeypatch.setattr(database, "transaction", ambiguous_transaction)
    register_prepared_configuration_target(args, LocationService(state))
    assert reply_lost
    assert args._target_registration["status"] == "registered"
    assert args._run_configuration.registered
    assert LeagueFolderStore(preferences).list() == (repository,)
    assert state.read_view().locations == (repository,)


def test_path_selection_and_saved_lineup_share_original_identity_and_exclusion_scope(
    selection: Selection, tmp_path: Path
) -> None:
    path, repository, _ = selection
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    args = _args(path, register=True)
    prepare_configuration(args)
    register_prepared_configuration_target(args, LocationService(state))
    league = LeagueService(state, RepositoryDiscoveryService())
    lineups = LeagueLineupService(LeagueLineupFileStore(tmp_path / "lineups"), league)
    lineups.save("selected", name="Selected", repositories=(repository,))
    assert lineups.repository_ids("selected") == {args._run_configuration.repository.repository_id}
    assert lineups.inspect("selected").ready_count == 1
    selector = FleetTargetSelector(
        RepositoryService(state, RepositoryDiscoveryService()),
        MigrationService(state, RepositoryDiscoveryService()),
        league,
    )
    scope = ExplicitRepositoryScope(repository_ids=tuple(lineups.repository_ids("selected")))
    admitted = selector.select(PLAN_CAPABILITIES[0], state.read_view(), None, scope)
    assert [entry.repository for entry in admitted] == [args._run_configuration.repository]
    state.exclude(repository, expected_durable_revision=state.read_view().durable_revision)
    with pytest.raises(FleetPlanInvalid, match="excluded"):
        resolve_run_configuration_target(load_run_configuration(path))
    # Saved membership remains a choice; the original Fleet selector owns
    # current inclusion for Shared Runs and Schedules using those identities.
    with pytest.raises(TargetSelectionError):
        selector.select(PLAN_CAPABILITIES[0], state.read_view(), None, scope)


@pytest.mark.parametrize("operation", ["schedule", "reschedule"])
def test_path_selected_schedule_rejects_replaced_instance_before_persistence(
    selection: Selection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path, repository, _ = selection
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (repository / "goon.yml").write_text("steps:\n- run: printf scheduled\n")
    payload = yaml.safe_load(path.read_text())
    payload["source"] = {"kind": "repository-goon"}
    path.write_text(yaml.safe_dump(payload))
    args = _args(path, register=True)
    prepare_configuration(args)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        register_prepared_configuration_target(args, runtime.locations)
        generate_stateless_lock(args)
        selected = args._run_configuration
        options = {
            "repository_ids": {selected.repository.repository_id},
            "interval_minutes": 60,
            "repository": repository,
            "repository_goon": True,
            "caller_input_file": selected.snapshot.authority(),
            "external_artifacts": selected.snapshot.external_artifacts(),
            "source_graph": args._source_graph,
            "lock_path": selected.snapshot.lock_path,
            "expected_repository_instance_ids": {
                selected.repository.repository_id: selected.repository_instance_id,
            },
        }
        saved = runtime.plans.schedule(args.plan, schedule_id="baseline", **options)
        assert saved.repository_instance_ids == options["expected_repository_instance_ids"]
        repository.rename(tmp_path / "retired")
        (repository / ".git").mkdir(parents=True)
        (repository / "goon.yml").write_text("steps:\n- run: printf replacement\n")
        schedule_id = "replacement" if operation == "schedule" else "baseline"
        with pytest.raises(FleetPlanSourceStale, match="repository selection changed"):
            getattr(runtime.plans, operation)(args.plan, schedule_id=schedule_id, **options)
        assert runtime.schedules.definition("baseline") == saved
        with pytest.raises(ScheduleNotFound):
            runtime.schedules.definition("replacement")
        assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)
