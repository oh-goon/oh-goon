from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition, JournalConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.product_identity import repository_attempt_operation_id, repository_id
from oh_goon.core.vocabulary import (
    EffectBoundaryKind,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.journal import Journal, RunResourceObservationContext
from oh_goon.runtime.journal_store import JournalFactStore
from tests.factories.journal import admit_run, commit_operation, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for
from tests.factories.run_resources import (
    run_resource_admission,
    run_resource_envelope,
    run_resource_observation,
)


def _journal(
    tmp_path: Path, *, terminal: bool = True
) -> tuple[Journal, RunResourceEnvelopeEvidence, RepositoryIdentity]:
    journal = Journal(LocalDatabase(tmp_path / "state.db", journal_mode="DELETE"))
    path = tmp_path / "repo"
    repository = RepositoryIdentity(repository_id=repository_id(path), name="repo", path=path)
    repository.path.mkdir()
    expected = run_resource_envelope()
    admit_run(
        journal,
        expected.fleet_run_id,
        run_resource_admission(repository, expected),
        fleet_repository_scope({repository.repository_id}),
        [repository],
    )
    start_run(journal, expected.fleet_run_id)
    if terminal:
        _fail(journal, expected)
    return journal, expected, repository


def _fail(journal: Journal, expected: RunResourceEnvelopeEvidence) -> None:
    terminalize_run(
        journal,
        expected.fleet_run_id,
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.recovery("Original workload failure"),
    )


def _row_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0])


def test_terminal_context_append_needs_no_runtime_initialization_or_history_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, expected, repository = _journal(tmp_path)
    for index in range(8):
        commit_operation(
            journal,
            make_operation_report(
                repository,
                OperationAction.INIT,
                fleet_run_id=None,
                operation_id=f"other-operation-{index}",
            ),
        )
    original = journal.read_view().required_run_detail(expected.fleet_run_id)
    context = journal.capture_run_resource_observation_context(expected)
    transported = RunResourceObservationContext.model_validate_json(context.model_dump_json())
    assert transported == context
    before = _row_count(journal._database.path)
    observed = run_resource_observation(expected)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Parent append cannot initialize runtime or load global history")

    with monkeypatch.context() as guarded:
        guarded.setattr(LocalDatabase, "__init__", forbidden)
        guarded.setattr(LocalDatabase, "_initialize", forbidden)
        guarded.setattr(LocalDatabase, "_connect", forbidden)
        guarded.setattr(JournalFactStore, "read_view", forbidden)
        guarded.setattr(JournalFactStore, "load_entries", forbidden)
        guarded.setattr(JournalFactStore, "load_entries_after", forbidden)
        Journal.record_run_resource_observation_from_context(transported, observed)

    assert _row_count(journal._database.path) == before + 1
    reopened = Journal(LocalDatabase(journal._database.path, journal_mode="DELETE"))
    detail = reopened.read_view().required_run_detail(expected.fleet_run_id)
    assert detail.run == original.run
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.resource_observation == observed
    assert detail.game_tape.resource_observation == observed
    assert detail.execution == original.execution
    assert (
        reopened.run_events(expected.fleet_run_id).events[-1].sequence
        == context.last_run_sequence + 1
    )


def test_context_capture_requires_terminal_run(tmp_path: Path) -> None:
    journal, expected, _repository = _journal(tmp_path, terminal=False)
    before = _row_count(journal._database.path)
    with pytest.raises(InvalidRunTransition, match="terminal"):
        journal.capture_run_resource_observation_context(expected)
    assert _row_count(journal._database.path) == before
    _fail(journal, expected)
    assert (
        journal.capture_run_resource_observation_context(expected).terminal_status
        is FleetRunStatus.FAILED
    )


def test_terminal_context_cannot_hide_unsettled_original_effect(tmp_path: Path) -> None:
    journal, expected, repository = _journal(tmp_path, terminal=False)
    intent = run_resource_admission(repository, expected)
    with journal._database.transaction(write=True) as connection:
        RepositoryEffectAttemptStore(journal._database).begin_fleet_in(
            connection,
            repository.path,
            intent.capability,
            fleet_run_id=expected.fleet_run_id,
            operation_id=repository_attempt_operation_id(
                expected.fleet_run_id, repository.repository_id
            ),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.CONTAINED,
        )
    _fail(journal, expected)
    before = _row_count(journal._database.path)
    with pytest.raises(InvalidRunTransition, match="settled original effects"):
        journal.capture_run_resource_observation_context(expected)
    assert _row_count(journal._database.path) == before


@pytest.mark.parametrize("field", ("unit_name", "invocation_id", "handoff_sha256"))
def test_context_capture_requires_exact_original_admission(tmp_path: Path, field: str) -> None:
    journal, expected, _repository = _journal(tmp_path)
    assert journal.capture_run_resource_observation_context(expected)
    values = {
        "unit_name": "oh-goon-run-" + "d" * 32 + ".scope",
        "invocation_id": "e" * 32,
        "handoff_sha256": "sha256:" + "f" * 64,
    }
    changed = RunResourceEnvelopeEvidence.model_validate(
        {**expected.model_dump(), field: values[field]}
    )
    with pytest.raises(InvalidRunTransition, match="matching original admission"):
        journal.capture_run_resource_observation_context(changed)


@pytest.mark.parametrize("field", ("fleet_run_id", "unit_name", "invocation_id"))
def test_parent_append_refuses_another_native_observation(tmp_path: Path, field: str) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    valid = run_resource_observation(expected)
    changed = valid.model_validate(
        {
            **valid.model_dump(),
            field: {
                "fleet_run_id": "other-run",
                "unit_name": "oh-goon-run-" + "d" * 32 + ".scope",
                "invocation_id": "e" * 32,
            }[field],
        }
    )
    before = _row_count(journal._database.path)
    with pytest.raises(InvalidRunTransition, match="original admission"):
        Journal.record_run_resource_observation_from_context(context, changed)
    assert _row_count(journal._database.path) == before
    Journal.record_run_resource_observation_from_context(context, valid)


def test_consumed_context_refuses_replay_without_another_append(tmp_path: Path) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    observed = run_resource_observation(expected)
    Journal.record_run_resource_observation_from_context(context, observed)
    before = _row_count(journal._database.path)
    with pytest.raises(JournalConflict, match="changed"):
        Journal.record_run_resource_observation_from_context(context, observed)
    assert _row_count(journal._database.path) == before
    with pytest.raises(InvalidRunTransition, match="already recorded"):
        journal.capture_run_resource_observation_context(expected)


def test_parent_append_refuses_unrelated_durable_state_change(tmp_path: Path) -> None:
    journal, expected, repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    commit_operation(
        journal,
        make_operation_report(
            repository, OperationAction.INIT, fleet_run_id=None, operation_id="later-operation"
        ),
    )
    before = _row_count(journal._database.path)
    with pytest.raises(JournalConflict, match="Database changed"):
        Journal.record_run_resource_observation_from_context(
            context, run_resource_observation(expected)
        )
    assert _row_count(journal._database.path) == before
    assert (
        journal.read_view().required_run_detail(expected.fleet_run_id).resource_observation is None
    )


@pytest.mark.parametrize("change", ("revision", "hash"))
def test_parent_append_checks_exact_global_head_even_without_revision_clock_change(
    tmp_path: Path, change: str
) -> None:
    journal, expected, repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    with journal._database.transaction(write=True, track_revision=False) as connection:
        if change == "revision":
            journal._facts.append_operation(
                connection,
                make_operation_report(
                    repository, OperationAction.INIT, fleet_run_id=None, operation_id="later-head"
                ),
            )
        else:
            altered = ("0" if context.journal_hash[0] != "0" else "1") + context.journal_hash[1:]
            connection.execute(
                "UPDATE journal_entries SET fact_hash = ? WHERE revision = ?",
                (altered, context.journal_revision),
            )
    before = _row_count(journal._database.path)
    with pytest.raises(JournalConflict, match="Journal changed"):
        Journal.record_run_resource_observation_from_context(
            context, run_resource_observation(expected)
        )
    assert _row_count(journal._database.path) == before


@pytest.mark.parametrize("change", ("file", "schema", "instance", "mode"))
def test_parent_append_refuses_database_identity_replacement(tmp_path: Path, change: str) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    path = journal._database.path
    if change == "file":
        replacement = tmp_path / "replacement.db"
        shutil.copyfile(path, replacement)
        replacement.replace(path)
    else:
        with closing(sqlite3.connect(path)) as connection:
            if change == "schema":
                connection.execute(f"PRAGMA schema_version = {context.database.schema_cookie + 1}")
            elif change == "instance":
                cookie = context.database.database_identity_cookie
                connection.execute(f"PRAGMA application_id = {1 if cookie != 1 else 2}")
            else:
                assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    before = _row_count(path)
    with pytest.raises(DatabasePathIdentityLost):
        Journal.record_run_resource_observation_from_context(
            context, run_resource_observation(expected)
        )
    assert _row_count(path) == before


def test_parent_append_never_recreates_a_missing_database(tmp_path: Path) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    path = journal._database.path
    path.unlink()

    with pytest.raises(DatabasePathIdentityLost):
        Journal.record_run_resource_observation_from_context(
            context, run_resource_observation(expected)
        )
    assert not path.exists()


def test_parent_append_does_not_retry_a_failed_native_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    before = _row_count(journal._database.path)
    attempts = 0

    def unavailable(_database: LocalDatabase, *, admitted: bool) -> sqlite3.Connection:
        nonlocal attempts
        attempts += 1
        assert admitted
        raise sqlite3.DatabaseError("native connection unavailable")

    with monkeypatch.context() as guarded:
        guarded.setattr(LocalDatabase, "_open_sqlite_connection", unavailable)
        with pytest.raises(sqlite3.DatabaseError, match="native connection unavailable"):
            Journal.record_run_resource_observation_from_context(
                context, run_resource_observation(expected)
            )
    assert attempts == 1
    assert _row_count(journal._database.path) == before


def test_context_json_rejects_nonterminal_claim(tmp_path: Path) -> None:
    journal, expected, _repository = _journal(tmp_path)
    context = journal.capture_run_resource_observation_context(expected)
    payload = json.loads(context.model_dump_json())
    payload["terminal_status"] = "RUNNING"
    with pytest.raises(ValueError):
        RunResourceObservationContext.model_validate_json(json.dumps(payload))
