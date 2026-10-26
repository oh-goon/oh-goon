from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase, inspect_local_database
from oh_goon.runtime.local_state_maintenance import (
    LocalStateCompatibility,
    reset_local_state,
    sqlite_state_files,
)


def _set_database(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(path))


def test_local_state_check_reports_new_database_without_creating_it(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    assert main(["local-state", "check"]) == 0
    assert not path.exists()
    output = capsys.readouterr().out
    assert "Local state: NEW" in output
    assert str(path) in output


def test_local_state_diagnostics_path_uses_canonical_state_layout(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    assert main(["local-state", "diagnostics-path"]) == 0
    assert capsys.readouterr().out.strip() == str(
        (tmp_path / "diagnostics" / "diagnostics.jsonl").resolve()
    )


def test_local_state_check_gives_one_reset_command_for_stale_epoch(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["local-state", "check"]) == 2
    output = capsys.readouterr().out
    assert "Local state: STALE" in output
    assert str(path) in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_local_state_reset_requires_explicit_yes(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    path.write_bytes(b"local-state")

    assert main(["local-state", "reset"]) == 2
    assert path.exists()
    assert "Nothing was deleted" in capsys.readouterr().out


def test_local_state_reset_deletes_database_and_sqlite_sidecars_only(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path, journal_mode="DELETE")
    Path(f"{path}-journal").touch()
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")

    assert main(["local-state", "reset", "--yes"]) == 0
    assert all(not state_file.exists() for state_file in sqlite_state_files(path))
    assert unrelated.read_text() == "keep"
    output = capsys.readouterr().out
    assert "make dev (source)" in output
    assert "oh-goon start (built release)" in output


def test_local_state_reset_durably_publishes_each_existing_file_absence(
    tmp_path, monkeypatch
) -> None:
    from oh_goon.integrations import exact_file_write

    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path, journal_mode="DELETE")
    Path(f"{path}-journal").touch()
    synced: list[Path] = []
    monkeypatch.setattr(exact_file_write, "_fsync_directory", synced.append)

    deleted = reset_local_state(path)

    assert deleted == (path, Path(f"{path}-journal"))
    assert synced == [tmp_path] * len(deleted)
    assert all(not state_file.exists() for state_file in sqlite_state_files(path))


def test_schema_mismatch_exception_names_path_and_supported_reset_command(tmp_path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    try:
        LocalDatabase(path)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("stale database should fail")

    assert str(path) in message
    assert "Source checkout: make local-state-reset" in message
    assert "Built release:   oh-goon local-state reset --yes" in message
    assert "do not mix" in message.lower()


def test_inspection_accepts_current_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path)

    inspection = inspect_local_database(expected_schema_version=DATABASE_SCHEMA_VERSION)
    assert inspection.compatible is True
    assert inspection.found_schema_version == DATABASE_SCHEMA_VERSION


def test_stateful_cli_turns_schema_mismatch_into_actionable_startup_error(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["doctor"]) == 2
    output = capsys.readouterr().out
    assert "Local oh-goon database schema mismatch" in output
    assert str(path) in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_contract45_journal_refuses_at_schema_boundary_without_decoding_or_mutation(
    tmp_path, monkeypatch, capsys
) -> None:
    from oh_goon.runtime.journal_codec import JournalFactCodec

    path = tmp_path / "historical.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path, journal_mode="DELETE")
    assert inspect_local_database().compatible
    # Synthetic admission authored and read back by the original contract-45
    # writer at 997efd1, including native UV selection without a lock-mode field.
    fixture = Path(__file__).parents[2] / "fixtures/contract45_run_admitted.json"
    row = json.loads(fixture.read_text())
    assert json.loads(row["event_json"])["target"]["plan"]["contract_version"] == 45
    with closing(sqlite3.connect(path)) as connection, connection:
        columns = tuple(row)
        connection.execute(
            f"INSERT INTO journal_entries ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        connection.execute("PRAGMA user_version = 89")
    before = path.read_bytes()

    def unexpected_decode(**_kwargs):
        pytest.fail("incompatible storage must refuse before Journal payload decoding")

    monkeypatch.setattr(JournalFactCodec, "decode", unexpected_decode)
    assert main(["doctor"]) == 2
    output = capsys.readouterr().out
    assert f"found 89; expected {DATABASE_SCHEMA_VERSION}" in output
    assert "matching original runtime" in output
    assert "A fresh state path does not settle an original unresolved writer" in output
    assert "JournalCorruption" not in output
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "fixture_name,legacy_schema",
    [("schema90_cancelled_run.json", 90), ("schema91_contract46_run.json", 91)],
)
def test_incompatible_journal_refuses_before_codec_or_ddl_without_mutation(
    tmp_path, monkeypatch, capsys, fixture_name, legacy_schema
) -> None:
    from oh_goon.core.errors import DatabaseSchemaMismatch
    from oh_goon.runtime.journal_codec import JournalFactCodec

    path = tmp_path / "historical.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path, journal_mode="DELETE")
    assert inspect_local_database().compatible
    fixture = Path(__file__).parents[2] / "fixtures" / fixture_name
    legacy = json.loads(fixture.read_text(encoding="utf-8"))
    assert legacy["schema_version"] == legacy_schema
    events = [json.loads(row["event_json"]) for row in legacy["entries"] if row["event_json"]]
    if legacy_schema == 90:
        cancellation = next(
            event["cancellation"] for event in events if event["event_type"] == "STEP_CANCELLED"
        )
        assert cancellation["native_agent_observations"] == []
        assert cancellation["physical_execution_id"] is None and cancellation["duration_ms"] == 0
        assert events[-1]["event_type"] == "RUN_CANCELLED"
    else:
        # Authored and independently decoded by the original schema91 reader at
        # 95c5411; contract46 selected package source through the native UV field.
        plan = events[0]["target"]["plan"]
        assert plan["contract_version"] == 46
        assert plan["steps"][0]["uv"]["source"] == "package"
    with closing(sqlite3.connect(path)) as connection, connection:
        for row in legacy["entries"]:
            columns = tuple(row)
            connection.execute(
                f"INSERT INTO journal_entries ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(row[column] for column in columns),
            )
        connection.execute(f"PRAGMA user_version = {legacy['schema_version']}")
    before = path.read_bytes()

    def unexpected_decode(**_kwargs):
        pytest.fail("incompatible history must refuse before payload decoding")

    statements = []
    native_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = native_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(JournalFactCodec, "decode", unexpected_decode)
    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    with pytest.raises(DatabaseSchemaMismatch):
        LocalDatabase(path, journal_mode="WAL")
    assert main(["doctor"]) == 2
    output = capsys.readouterr().out
    assert f"found {legacy_schema}; expected {DATABASE_SCHEMA_VERSION}" in output
    assert "matching original runtime" in output and "JournalCorruption" not in output
    assert path.read_bytes() == before
    assert not any(
        sql.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ", "BEGIN IMMEDIATE"))
        or "PRAGMA JOURNAL_MODE =" in sql.upper()
        or "PRAGMA USER_VERSION =" in sql.upper()
        for sql in statements
    )


def test_serve_stops_before_uvicorn_on_stale_local_state(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["serve"]) == 2
    output = capsys.readouterr().out
    assert "Startup stopped before opening the application." in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_make_dev_has_one_python_launch_frontdoor() -> None:
    script = (Path(__file__).resolve().parents[4] / "scripts" / "dev.sh").read_text()
    assert "oh-goon-local-dev-launch" in script
    assert "uvicorn" not in script
    assert "npm run dev" not in script
    assert "observe-ready" not in script


def test_reset_does_not_delete_existing_league_preferences(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    repository_root = tmp_path / "repos"
    repository_root.mkdir()

    from oh_goon.runtime.league_folders import LeagueFolderStore

    preferences = LeagueFolderStore()
    preferences.replace((repository_root,))
    LocalDatabase(path)

    assert main(["local-state", "reset", "--yes"]) == 0
    assert preferences.list() == (repository_root.resolve(),)


def test_source_dev_recovery_target_remains_repository_owned() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "local-state-reset:" in makefile
    assert "backend/.venv/bin/oh-goon-local-state reset" in makefile


def test_source_operator_docs_use_make_owned_local_state_recovery() -> None:
    root = Path(__file__).resolve().parents[4]
    docs = [
        root / "README.md",
        root / "docs" / "TROUBLESHOOTING.md",
        root / "docs" / "GETTING_STARTED.md",
        root / "docs" / "LOCAL_FIRST.md",
    ]
    for document in docs:
        text = document.read_text(encoding="utf-8")
        assert "make local-state-reset" in text, document
    troubleshooting = (root / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    assert "make local-state-check" in troubleshooting
    assert "make local-state-path" in troubleshooting
    assert "different/global binary" in troubleshooting


def test_make_owns_source_local_state_check_path_and_reset_commands() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "local-state-check:" in makefile
    assert "local-state-path:" in makefile
    assert "diagnostics-path:" in makefile
    assert "diagnostics-log:" in makefile
    assert "backend/.venv/bin/oh-goon-local-state check" in makefile
    assert "backend/.venv/bin/oh-goon-local-state path" in makefile
    assert "backend/.venv/bin/oh-goon-local-state diagnostics-path" in makefile
    assert "backend/.venv/bin/oh-goon-local-state reset" in makefile
    local_owner = (root / "backend/src/oh_goon/repository_tooling/local_state_source.py").read_text(
        encoding="utf-8"
    )
    assert "from oh_goon.runtime.database import" in local_owner
    assert "from oh_goon.runtime.local_state_maintenance import reset_local_state" in local_owner


def test_local_state_inspection_distinguishes_future_schema_from_stale(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "future.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION + 1}")

    inspection = inspect_local_database()

    assert inspection.status is LocalStateCompatibility.FUTURE
    assert inspection.compatible is False
    assert "newer than this build supports" in inspection.detail


def test_local_database_future_schema_fails_closed_without_mutating_epoch(tmp_path) -> None:
    path = tmp_path / "future.db"
    future = DATABASE_SCHEMA_VERSION + 1
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {future}")

    with pytest.raises(Exception, match="newer than this build supports"):
        LocalDatabase(path)

    with closing(sqlite3.connect(path)) as connection, connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == future


def test_deep_inspection_rejects_foreign_key_damage_that_schema_and_quick_check_accept(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "foreign-key-damage.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")

    schema_only = inspect_local_database()
    quick_only = inspect_local_database(quick_check=True)
    deep = inspect_local_database(
        integrity_check=True,
        foreign_key_check=True,
    )

    assert schema_only.status is LocalStateCompatibility.READY
    assert quick_only.status is LocalStateCompatibility.READY
    assert deep.status is LocalStateCompatibility.INVALID
    assert "foreign_key_check failed" in deep.detail


def test_local_state_check_stops_before_launch_on_foreign_key_damage(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "foreign-key-startup-damage.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")

    assert main(["local-state", "check"]) == 2
    output = capsys.readouterr().out
    assert "Local state: INVALID" in output
    assert "foreign_key_check failed" in output
    assert "make local-state-reset" in output


def test_source_local_state_reset_entrypoint_accepts_make_confirmation(
    tmp_path, monkeypatch, capsys
) -> None:
    import sys

    from oh_goon.repository_tooling.local_state_source import main as source_local_state_main

    path = tmp_path / "source-reset.db"
    monkeypatch.setenv("OH_GOON_DB", str(path))
    LocalDatabase(path)
    assert path.is_file()
    monkeypatch.setattr(sys, "argv", ["oh-goon-local-state", "reset", "--yes"])

    assert source_local_state_main() == 0
    assert not path.exists()
    output = capsys.readouterr().out
    assert "Local state reset:" in output


def test_source_local_state_reset_entrypoint_refuses_unconfirmed_deletion(
    tmp_path, monkeypatch, capsys
) -> None:
    import sys

    from oh_goon.repository_tooling.local_state_source import main as source_local_state_main

    path = tmp_path / "source-reset-unconfirmed.db"
    monkeypatch.setenv("OH_GOON_DB", str(path))
    LocalDatabase(path)
    monkeypatch.setattr(sys, "argv", ["oh-goon-local-state", "reset"])

    assert source_local_state_main() == 2
    assert path.is_file()
    assert "Nothing was deleted." in capsys.readouterr().out


def test_source_make_local_state_reset_is_explicit_confirmed_recovery() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("local-state-reset:", 1)[1].split("\n\n", 1)[0]
    assert "backend/.venv/bin/oh-goon-local-state reset --yes" in target


@pytest.mark.parametrize("schema", [0, DATABASE_SCHEMA_VERSION - 1, DATABASE_SCHEMA_VERSION + 1])
def test_reset_preserves_unknown_or_incompatible_store_bytes(tmp_path, schema):
    from oh_goon.core.errors import DatabaseSchemaMismatch

    path = tmp_path / "original.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE original_writer_evidence(value TEXT)")
        connection.execute("INSERT INTO original_writer_evidence VALUES ('unresolved effect')")
        connection.execute(f"PRAGMA user_version = {schema}")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(DatabaseSchemaMismatch):
        reset_local_state(path)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute("SELECT value FROM original_writer_evidence").fetchone()[0]
            == "unresolved effect"
        )


def test_reset_refuses_active_canonical_run_without_deleting_authority(tmp_path):
    from oh_goon.core.errors import StateConflict
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.runtime.journal import Journal
    from tests.factories.capabilities import admit_test_run

    database = LocalDatabase(tmp_path / "state.db", journal_mode="DELETE")
    journal = Journal(database)
    repository = RepositoryIdentity(name="repo", path=tmp_path / "repo")
    admit_test_run(journal, "active-original", OperationAction.INIT, [repository])
    before = database.path.read_bytes()
    with pytest.raises(StateConflict, match="active or unresolved Runs"):
        reset_local_state(database.path)
    assert database.path.read_bytes() == before
    assert journal.read_view().required_run_detail("active-original").run.status.value == "QUEUED"


def test_reset_refuses_incomplete_campaign_between_phase_admissions(tmp_path):
    from datetime import timedelta

    from oh_goon.core.errors import StateConflict
    from oh_goon.core.models.agent_repair import AgentCampaignIntent, AgentRepairBudget
    from oh_goon.core.time import utc_now
    from oh_goon.runtime.journal import Journal

    database = LocalDatabase(tmp_path / "state.db", journal_mode="DELETE")
    journal = Journal(database)
    journal.begin_agent_campaign(
        AgentCampaignIntent(
            campaign_id="between-phases",
            repository_id="repo",
            original_execution_digest="original",
            budget=AgentRepairBudget(max_attempts=1, timeout_seconds=60, step_timeout_seconds=10),
            maximum_runs=2,
            deadline_at=utc_now() + timedelta(seconds=60),
            initial_phase="review",
            initial_step_ids=("review",),
        )
    )
    before = database.path.read_bytes()
    with pytest.raises(StateConflict, match="incomplete campaign controller"):
        reset_local_state(database.path)
    assert database.path.read_bytes() == before
    assert journal.agent_campaign("between-phases").intent is not None


def _terminal_cleanup_journal(tmp_path, mode):
    import subprocess

    from oh_goon.core.models.fleet_plan import (
        ResolvedCommandStep,
        ResolvedDependencyInventoryStep,
        ResolvedFleetPlan,
    )
    from oh_goon.core.models.fleet_plan_evidence import (
        FleetPlanResult,
        PlanStepSettlementEvidence,
    )
    from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
    from oh_goon.core.models.operation import FleetPlanOperationEvidence
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        FleetRunAdmissionIntent,
        fleet_repository_scope,
    )
    from oh_goon.core.product_identity import repository_id
    from oh_goon.core.vocabulary import (
        EffectBoundaryKind,
        FleetRunEventType,
        FleetRunTerminalIntent,
        OperationAction,
        Verdict,
    )
    from oh_goon.engine.command import CommandSupervisorReceipt
    from oh_goon.engine.git import GitService
    from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence, step_evidence
    from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
    from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
    from oh_goon.runtime.journal import Journal
    from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement
    from tests.factories.capabilities import capability_snapshot
    from tests.factories.fleet_plan_targets import structural_fleet_plan_target
    from tests.factories.journal import admit_run, start_repository, start_run, terminalize_run
    from tests.factories.operation_reports import make_operation_report
    from tests.factories.run_admission import repository_admissions_for

    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    subprocess.run(["git", "init", "-q", str(repository_path)], check=True)
    repository = RepositoryIdentity(
        repository_id=repository_id(repository_path), name="repo", path=repository_path
    )
    database = LocalDatabase(tmp_path / "state.db", journal_mode="DELETE")
    journal = Journal(database)
    if mode == "in_process":
        step = ResolvedDependencyInventoryStep(step_id="owned")
        outcome = StepOutcome(argv=(), returncode=0, completion="in_process")
    else:
        step = ResolvedCommandStep(step_id="owned", argv=("native-work",))
        outcome = StepOutcome(
            argv=() if mode == "skipped" else ("native-work",),
            returncode=0 if mode in {"skipped", "unavailable"} else 17,
            skipped=mode == "skipped",
            supervisor=None
            if mode in {"unavailable", "never_started", "skipped"}
            else CommandSupervisorReceipt(final_process_group_quiescent=mode != "unverified"),
        )
    attempts = (
        ()
        if mode in {"never_started", "skipped", "in_process"}
        else (
            attempt_evidence(
                step=step,
                attempt=1,
                outcome=outcome,
                repository_state_before="before",
                repository_state_after="after",
                worktree_files_after=(),
            ),
        )
    )
    execution = step_evidence(
        step=step,
        outcome=outcome,
        attempts=attempts,
        artifacts=(),
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    if mode == "skipped":
        execution = execution.model_copy(
            update={
                "settlement": PlanStepSettlementEvidence(
                    outcome="SKIPPED",
                    cleanup="UNAVAILABLE",
                    reason="NOT_STARTED",
                )
            }
        )
    plan = ResolvedFleetPlan.create(
        name="settled-cleanup",
        description="Canonical terminal cleanup evidence",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
    )
    target = structural_fleet_plan_target(plan)
    capability = capability_snapshot(OperationAction.EXECUTE_PLAN, target)
    admit_run(
        journal,
        "terminal-cleanup",
        FleetRunAdmissionIntent(
            target=target,
            capability=capability,
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, (repository,)),
        ),
        fleet_repository_scope({repository.repository_id}),
        [repository],
    )
    start_run(journal, "terminal-cleanup")
    operation_id = start_repository(journal, "terminal-cleanup", repository)
    terminal_type = (
        FleetRunEventType.STEP_SKIPPED
        if mode == "skipped"
        else FleetRunEventType.STEP_SUCCEEDED
        if execution.status == "COMPLETED"
        else FleetRunEventType.STEP_FAILED
    )
    for event_type in (
        FleetRunEventType.STEP_REACHED,
        FleetRunEventType.STEP_STARTED,
        terminal_type,
    ):
        if mode == "skipped" and event_type is FleetRunEventType.STEP_STARTED:
            continue
        journal.record_execution_lifecycle(
            fleet_run_id="terminal-cleanup",
            repository_id=repository.repository_id,
            operation_id=operation_id,
            event_type=event_type,
            step_id=step.step_id,
            step_order=1,
            returncode=getattr(execution, "returncode", None)
            if event_type is terminal_type
            else None,
        )
    failed = execution.status == "FAILED"
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        fleet_run_id="terminal-cleanup",
        operation_id=operation_id,
        verdict=Verdict.FAILED if failed else Verdict.READY,
        returncode=getattr(execution, "returncode", 0),
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(execution,)),
        ),
    )
    attempt_store = RepositoryEffectAttemptStore(database)
    settlement = RepositoryEffectSettlement(database, journal, attempt_store)
    baseline = GitService().fingerprint(repository_path)
    with database.transaction(write=True) as connection:
        attempt_store.begin_fleet_in(
            connection,
            repository_path,
            capability,
            fleet_run_id="terminal-cleanup",
            operation_id=operation_id,
            target=target,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempt_store.start_effect_in(connection, operation_id)
        settlement.commit_success_in(connection, report, operation_id)
    terminalize_run(journal, "terminal-cleanup", FleetRunTerminalIntent.COMPLETE)
    if report.resource_settlement == "UNRESOLVED":
        assert len(attempt_store.all()) == 1
        assert attempt_store.all()[0].status.value == "OUTCOME_UNKNOWN"
    else:
        assert attempt_store.all() == []
    assert journal.active_run() is None
    assert journal.read_view().required_run_detail("terminal-cleanup").run.status.terminal
    return database, journal


@pytest.mark.parametrize("mode", ["unverified", "unavailable"])
def test_reset_preserves_terminal_step_cleanup_after_effect_coordination_retired(tmp_path, mode):
    from oh_goon.core.errors import StateConflict

    database, journal = _terminal_cleanup_journal(tmp_path, mode)
    before = database.path.read_bytes()
    with pytest.raises(StateConflict, match="unresolved original Step cleanup"):
        reset_local_state(database.path)
    assert database.path.read_bytes() == before
    assert journal.read_view().required_run_detail("terminal-cleanup").operations


@pytest.mark.parametrize("mode", ["verified_failed", "in_process", "skipped", "never_started"])
def test_reset_accepts_settled_failure_inprocess_and_never_started_cleanup(tmp_path, mode):
    database, _ = _terminal_cleanup_journal(tmp_path, mode)
    assert database.path in reset_local_state(database.path)
    assert not database.path.exists()


def _maintenance_mutation_probe(tmp_path):
    import shlex
    import sys

    import yaml

    directory = tmp_path / "probe"
    directory.mkdir()
    (tmp_path / "repo/mutation_probe.py").write_text(
        "from pathlib import Path\nPath('mutation-marker').write_text('new execution\\n')\n"
    )
    source = directory / "goon.yml"
    source.write_text(
        yaml.safe_dump(
            {
                "execution": {"purpose": "maintenance"},
                "steps": [{"run": shlex.join((sys.executable, "mutation_probe.py"))}],
            }
        )
    )
    return source


@pytest.mark.parametrize("mode", ["unverified", "unavailable"])
def test_committed_cleanup_fence_survives_runtime_restart_and_blocks_mutation(
    tmp_path,
    monkeypatch,
    mode,
):
    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.errors import RuntimeSafetyBlocked, StateConflict
    from oh_goon.core.product_identity import repository_id
    from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore

    database, journal = _terminal_cleanup_journal(tmp_path, mode)
    attempt_store = RepositoryEffectAttemptStore(database)
    original = attempt_store.all()[0]
    operation = journal.require_operation(original.operation_id)
    assert operation.resource_settlement == "UNRESOLVED"
    monkeypatch.setenv("OH_GOON_DB", str(database.path))
    monkeypatch.setenv("OH_GOON_SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    plan = _maintenance_mutation_probe(tmp_path)
    for restart in range(2):
        runtime = ApplicationRuntime.build(standard_local=True)
        try:
            runtime.locations.add(tmp_path)
            assert attempt_store.get(original.operation_id) == original
            assert journal.require_operation(original.operation_id) == operation
            assert journal.require_operation(original.operation_id).outcome == operation.outcome
            revision = journal.read_view().revision
            with pytest.raises(
                StateConflict, match="filesystem reconciliation cannot prove quiescence"
            ):
                runtime.recovery.reconcile(
                    original.operation_id, expected_record_version=original.record_version
                )
            with pytest.raises(
                StateConflict, match="operator acknowledgement cannot prove quiescence"
            ):
                runtime.recovery.acknowledge(
                    original.operation_id,
                    note="Operator says done",
                    expected_record_version=original.record_version,
                )
            assert journal.read_view().revision == revision
            runtime.plans.lock(plan)
            with pytest.raises(RuntimeSafetyBlocked, match="ORIGINAL_RESOURCE_CLEANUP_UNRESOLVED"):
                runtime.plans.run(
                    plan,
                    repository_ids={repository_id(tmp_path / "repo")},
                    fleet_run_id=f"forbidden-{restart}",
                )
            assert not (tmp_path / "repo/mutation-marker").exists()
            assert journal.read_view().run_detail(f"forbidden-{restart}") is None
            assert attempt_store.get(original.operation_id) == original
            assert len(journal.read_view().run_details()) == 1
        finally:
            runtime.shutdown(wait_for_runs=True)


def test_verified_failed_command_allows_a_valid_fresh_native_mutation(tmp_path, monkeypatch):
    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.product_identity import repository_id

    database, journal = _terminal_cleanup_journal(tmp_path, "verified_failed")
    monkeypatch.setenv("OH_GOON_DB", str(database.path))
    monkeypatch.setenv("OH_GOON_SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    plan = _maintenance_mutation_probe(tmp_path)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(tmp_path)
        runtime.plans.lock(plan)
        state = runtime.plans.run(
            plan,
            repository_ids={repository_id(tmp_path / "repo")},
            fleet_run_id="verified-fresh-mutation",
        )
        detail = runtime.runs.wait_for_terminal(state.fleet_run_id)
        assert detail.outcome.value == "SUCCESS"
        assert (tmp_path / "repo/mutation-marker").read_text() == "new execution\n"
        original = journal.read_view().required_run_detail("terminal-cleanup").operations[0]
        assert original.resource_settlement == "SETTLED"
        assert not original.outcome.verdict.successful
    finally:
        runtime.shutdown(wait_for_runs=True)
    assert database.path in reset_local_state(database.path)
