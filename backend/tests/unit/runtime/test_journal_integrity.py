from pathlib import Path

import pytest

from oh_goon.core.errors import JournalCorruption
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import commit_operation, start_run
from tests.factories.operation_reports import make_operation_report


def _journal_with_two_facts(tmp_path: Path) -> tuple[LocalDatabase, Journal]:
    database = LocalDatabase(tmp_path / "journal-integrity.db")
    journal = Journal(database)
    admit_test_run(
        journal,
        "fleet-integrity",
        OperationAction.PLAN,
        [RepositoryIdentity(name="repo", path=tmp_path / "repo")],
    )
    start_run(journal, "fleet-integrity")
    return database, journal


def test_journal_detects_tampered_payload_before_projection(tmp_path: Path) -> None:
    database, journal = _journal_with_two_facts(tmp_path)
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE journal_entries SET event_json = ? WHERE revision = 1",
            ('{"tampered":true}',),
        )

    with pytest.raises(JournalCorruption):
        journal.read_view()


def test_journal_detects_broken_hash_chain_before_projection(tmp_path: Path) -> None:
    database, journal = _journal_with_two_facts(tmp_path)
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE journal_entries SET previous_hash = ? WHERE revision = 2",
            ("f" * 64,),
        )

    with pytest.raises(JournalCorruption):
        journal.read_view()


def test_cached_read_view_revalidates_after_external_sqlite_change(tmp_path: Path) -> None:
    from oh_goon.core.errors import JournalCorruption
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal

    database = LocalDatabase(tmp_path / "cached-integrity.db")
    journal = Journal(database)
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    report = make_operation_report(
        identity, OperationAction.INIT, operation_id="op-cache-integrity", fleet_run_id=None
    )
    commit_operation(journal, report)
    assert journal.read_view().revision == 1

    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE journal_entries SET report_json = replace(report_json, 'READY', 'FAILED') WHERE revision = 1"
        )

    with pytest.raises(JournalCorruption):
        journal.read_view()


def test_journal_read_cache_publishes_key_and_view_as_one_concurrent_snapshot() -> None:
    from threading import Event, Thread
    from typing import cast

    from oh_goon.runtime.journal_cache import (
        JournalCacheKey,
        JournalFileSignature,
        JournalReadCache,
    )
    from oh_goon.runtime.journal_view import JournalReadView

    files = JournalFileSignature(0, 0, 0, 0)
    first_key = JournalCacheKey(1, "a" * 64, files)
    second_key = JournalCacheKey(2, "b" * 64, files)
    first_view = cast(JournalReadView, object())
    second_view = cast(JournalReadView, object())
    entered_equality = Event()
    release_equality = Event()

    class BlockingLookupKey:
        def __eq__(self, other: object) -> bool:
            entered_equality.set()
            assert release_equality.wait(timeout=2)
            return other == first_key

    cache = JournalReadCache()
    cache.store(first_key, first_view)
    observed: list[JournalReadView | None] = []
    thread = Thread(
        target=lambda: observed.append(cache.lookup(BlockingLookupKey())),  # type: ignore[arg-type]
    )
    thread.start()
    assert entered_equality.wait(timeout=2)
    cache.store(second_key, second_view)
    release_equality.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert observed == [first_view]


def test_journal_codec_rejects_unknown_fact_kind_even_before_payload_projection() -> None:
    from oh_goon.runtime.journal_codec import GENESIS_HASH, JournalFactCodec

    with pytest.raises(JournalCorruption, match="Unknown journal fact kind"):
        JournalFactCodec.decode(
            revision=1,
            session_id="session-test",
            kind="UNKNOWN",
            report_json=None,
            event_json=None,
            effect_json=None,
            committed_at="2026-01-01T00:00:00+00:00",
            previous_hash=GENESIS_HASH,
            fact_hash="unused",
            expected_previous_hash=GENESIS_HASH,
        )


def test_journal_codec_rejects_hash_valid_fact_with_wrong_shape() -> None:
    from oh_goon.runtime.journal_codec import (
        GENESIS_HASH,
        EncodedJournalFact,
        JournalFactCodec,
        JournalFactKind,
    )

    committed_at = "2026-01-01T00:00:00+00:00"
    encoded = EncodedJournalFact(JournalFactKind.OPERATION, None, None, None)
    fact_hash = JournalFactCodec.hash_fact(
        previous_hash=GENESIS_HASH,
        session_id="session-test",
        encoded=encoded,
        committed_at=committed_at,
    )

    with pytest.raises(JournalCorruption, match="fact shape does not match kind"):
        JournalFactCodec.decode(
            revision=1,
            session_id="session-test",
            kind=JournalFactKind.OPERATION.value,
            report_json=None,
            event_json=None,
            effect_json=None,
            committed_at=committed_at,
            previous_hash=GENESIS_HASH,
            fact_hash=fact_hash,
            expected_previous_hash=GENESIS_HASH,
        )


@pytest.mark.parametrize("fact_kind", ("OPERATION", "RUN_EVENT", "FLEET_OPERATION"))
@pytest.mark.parametrize("effect_json", ('{"unexpected":"effect"}', "null"))
def test_journal_codec_rejects_unexpected_effect_column_after_valid_round_trip(
    tmp_path: Path, fact_kind: str, effect_json: str
) -> None:
    from dataclasses import replace

    from oh_goon.core.models.run_events import RepositoryCompletedEvent, RunStartedEvent
    from oh_goon.core.vocabulary import FleetRunEventType
    from oh_goon.runtime.journal_codec import GENESIS_HASH, EncodedJournalFact, JournalFactCodec
    from oh_goon.runtime.journal_facts import (
        FleetOperationCommitted,
        JournalEntry,
        OperationCommitted,
        RunEventCommitted,
    )

    session_id = "session-codec-shape"
    report = make_operation_report(
        tmp_path / "repo",
        OperationAction.INIT,
        operation_id="operation-codec-shape",
        fleet_run_id=None if fact_kind == "OPERATION" else "run-codec-shape",
    )
    encoded: EncodedJournalFact
    expected: JournalEntry
    if fact_kind == "OPERATION":
        encoded = JournalFactCodec.encode_operation(report)
        expected = OperationCommitted(1, session_id, report)
    elif fact_kind == "RUN_EVENT":
        started = RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=1,
            fleet_run_id="run-codec-shape",
        )
        encoded = JournalFactCodec.encode_run_event(started)
        expected = RunEventCommitted(1, session_id, started)
    else:
        completed = RepositoryCompletedEvent(
            event_type=FleetRunEventType.REPOSITORY_COMPLETED,
            sequence=1,
            fleet_run_id="run-codec-shape",
            operation_id=report.provenance.operation_id,
        )
        encoded = JournalFactCodec.encode_fleet_operation(report, completed)
        expected = FleetOperationCommitted(1, session_id, report, completed)
    committed_at = "2026-01-01T00:00:00+00:00"

    def decode(fact: EncodedJournalFact) -> JournalEntry:
        return JournalFactCodec.decode(
            revision=1,
            session_id=session_id,
            kind=fact.kind.value,
            report_json=fact.report_json,
            event_json=fact.event_json,
            effect_json=fact.effect_json,
            committed_at=committed_at,
            previous_hash=GENESIS_HASH,
            fact_hash=JournalFactCodec.hash_fact(
                previous_hash=GENESIS_HASH,
                session_id=session_id,
                encoded=fact,
                committed_at=committed_at,
            ),
            expected_previous_hash=GENESIS_HASH,
        )

    assert decode(encoded) == expected
    with pytest.raises(JournalCorruption, match="fact shape does not match kind"):
        decode(replace(encoded, effect_json=effect_json))


def test_journal_codec_rejects_hash_valid_invalid_acknowledgement_payload() -> None:
    from oh_goon.runtime.journal_codec import (
        GENESIS_HASH,
        EncodedJournalFact,
        JournalFactCodec,
        JournalFactKind,
    )

    committed_at = "2026-01-01T00:00:00+00:00"
    encoded = EncodedJournalFact(
        JournalFactKind.EFFECT_ACKNOWLEDGEMENT,
        None,
        None,
        "{}",
    )
    fact_hash = JournalFactCodec.hash_fact(
        previous_hash=GENESIS_HASH,
        session_id="session-test",
        encoded=encoded,
        committed_at=committed_at,
    )

    with pytest.raises(JournalCorruption, match="payload is invalid"):
        JournalFactCodec.decode(
            revision=1,
            session_id="session-test",
            kind=JournalFactKind.EFFECT_ACKNOWLEDGEMENT.value,
            report_json=None,
            event_json=None,
            effect_json="{}",
            committed_at=committed_at,
            previous_hash=GENESIS_HASH,
            fact_hash=fact_hash,
            expected_previous_hash=GENESIS_HASH,
        )
