"""CO-R3B: real process-death observations at existing original-owner cuts.

No alternate transaction, schedule, evidence, or backup runtime is defined here.
Physical quiescence, injected-clock tests, and WAL on unsupported hosts cannot be
promoted to independent kernel containment or release evidence.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.models.schedule_execution import RunningScheduleRun
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.vocabulary import ScheduleRepositoryRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore

_CHILD = r"""
import os, sys
from contextlib import contextmanager
from pathlib import Path
import pytest
from oh_goon.runtime.database import LocalDatabase

root, domain, cut = sys.argv[1:]
root = Path(root)
if domain == 'schedule':
    from tests.unit.services.test_co_r2a_schedule_atomic_admission import _fixture, _admit
    path, db, store, repo, occurrence, run_id, intent, scope = _fixture(root)
    if cut == 'precommit':
        original = db.transaction
        @contextmanager
        def process_dies_before_commit(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                yield conn
                if kwargs.get('write') is True:
                    os._exit(191)
        db.transaction = process_dies_before_commit
    _admit(db, run_id, intent, scope, repo)
    os._exit(192)
elif domain == 'evidence':
    import oh_goon.runtime.execution_evidence_acceptance as acceptance_module
    from tests.unit.runtime.test_co_r2c_evidence_acceptance_cut import (
        _accept, _acceptance_ledger, NOW,
    )
    from datetime import timedelta
    from oh_goon.core.errors import ExecutionEvidenceRejected
    changes = pytest.MonkeyPatch()
    db, workspace, assignment, lease, materialization, authority = _acceptance_ledger(root, changes)
    changes.setattr(acceptance_module, 'utc_now',
                    lambda: NOW + timedelta(seconds=60 if cut == 'expired' else 30))
    if cut == 'precommit':
        original = db.transaction
        @contextmanager
        def process_dies_before_commit(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                yield conn
                if kwargs.get('write') is True:
                    os._exit(193)
        db.transaction = process_dies_before_commit
    if cut == 'expired':
        try:
            _accept(authority, workspace, assignment, lease, materialization)
        except ExecutionEvidenceRejected as exc:
            assert 'expired' in str(exc)
            os._exit(194)
        os._exit(198)
    _accept(authority, workspace, assignment, lease, materialization)
    os._exit(195)
elif domain == 'backup':
    import oh_goon.runtime.database as database_module
    from tests.unit.runtime.test_db5_backup_publication_failures import _original_source
    owner, target = _original_source(root)
    if cut == 'prelink':
        def death_before_link(*args, **kwargs):
            assert not target.exists()
            os._exit(196)
        database_module.os.link = death_before_link
    else:
        def death_after_link_before_directory_sync(directory):
            assert directory == target.parent
            assert target.is_file()
            os._exit(197)
        database_module._sync_backup_directory = death_after_link_before_directory_sync
    owner.export_verified_backup(target)
    os._exit(198)
else:
    raise AssertionError(domain)
"""


def _die(tmp_path: Path, domain: str, cut: str) -> int:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path), domain, cut],
        cwd=Path(__file__).resolve().parents[3],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[3])
            + os.pathsep
            + str(Path(__file__).resolve().parents[3] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.stderr == "", result.stderr
    return result.returncode


@pytest.mark.skipif(os.name != "posix", reason="requires native child process exit")
@pytest.mark.parametrize(
    "cut,exit_code,committed",
    [
        ("precommit", 191, False),
        ("postcommit", 192, True),
    ],
)
def test_original_scheduled_admission_after_real_process_death(
    tmp_path: Path,
    cut: str,
    exit_code: int,
    committed: bool,
) -> None:
    assert _die(tmp_path, "schedule", cut) == exit_code
    db = LocalDatabase(tmp_path / "co-r2a.db")
    with db.transaction() as conn:
        rows = conn.execute("SELECT schedule_run_id FROM schedule_executions").fetchall()
    assert len(rows) == 1
    occurrence = ScheduleExecutionStore(db).get(rows[0]["schedule_run_id"])
    assert isinstance(occurrence, RunningScheduleRun)
    assert len(occurrence.repository_runs) == 1
    child = occurrence.repository_runs[0]
    expected_id = schedule_repository_fleet_run_id(occurrence.schedule_run_id, child.repository_id)
    assert child.fleet_run_id == (expected_id if committed else None)
    assert child.status is (
        ScheduleRepositoryRunStatus.RUNNING if committed else ScheduleRepositoryRunStatus.PENDING
    )
    view = Journal(db).read_view().run_detail(expected_id)
    assert (view is not None) is committed


@pytest.mark.skipif(os.name != "posix", reason="requires native child process exit")
@pytest.mark.parametrize(
    "cut,exit_code,committed",
    [
        ("precommit", 193, False),
        ("postcommit", 195, True),
        ("expired", 194, False),
    ],
)
def test_original_acceptance_after_real_process_death_or_expiry(
    tmp_path: Path,
    cut: str,
    exit_code: int,
    committed: bool,
) -> None:
    assert _die(tmp_path, "evidence", cut) == exit_code
    db = LocalDatabase(tmp_path / "ledger.db", journal_mode="DELETE")
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT acceptance_id, accepted_at FROM execution_evidence_acceptances"
        ).fetchall()
        assert len(rows) == int(committed)
        if committed:
            assert rows[0]["accepted_at"] == "2026-10-15T08:00:30+00:00"
    if committed:
        restored = object.__new__(ExecutionEvidenceAcceptanceAuthority)
        restored._database = db
        assert (
            restored.acceptance(rows[0]["acceptance_id"]).acceptance_id == rows[0]["acceptance_id"]
        )


@pytest.mark.skipif(os.name != "posix", reason="requires native SQLite backup and process exit")
@pytest.mark.parametrize(
    "cut,exit_code,published",
    [
        ("prelink", 196, False),
        ("postlink", 197, True),
    ],
)
def test_original_backup_crash_never_admits_an_unacknowledged_candidate(
    tmp_path: Path,
    cut: str,
    exit_code: int,
    published: bool,
) -> None:
    assert _die(tmp_path, "backup", cut) == exit_code
    target = tmp_path / "snapshots" / "offline.sqlite3"
    assert target.exists() is published
    owner = LocalDatabase(tmp_path / "state.sqlite3")
    with owner.transaction() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM locations WHERE path='/db5/record'").fetchone()[0]
            == 1
        )
    if published:
        # No receipt was returned. An ambiguous but present snapshot is NOT
        # automatically restored and cannot overwrite any unrelated candidate.
        with pytest.raises(FileExistsError, match="already exists"):
            owner.export_verified_backup(target)
        assert target.is_file()
    else:
        # Unacknowledged stage from the killed process is not a published backup.
        assert list(target.parent.glob(".*.snapshot-tmp"))
        receipt = owner.export_verified_backup(target)
        LocalDatabase.verify_offline_backup(receipt)
