"""M195: original Journal parent binding is not native replay entitlement.

Keep the one Journal lineage validator and original Fleet native issuer boundary;
never derive parent identity from an opaque deployment-owned policy map.
"""

from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.journal import JournalConflict
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.services.test_group_a_m191_native_replay_capture_fence import _case


def test_exact_parent_child_candidate_is_read_only_and_not_native_replay(tmp_path):
    db, journal, scope, repositories, child = _case(tmp_path, kind="readmission")
    with db.transaction(write=True) as connection:
        before = connection.total_changes
        event = journal.prepare_run_admission_in(
            connection, "child", child, scope, list(repositories)
        )
        assert event.fleet_run_id == "child"
        assert event.execution.readmitted_from_run_id == "parent"
        assert connection.total_changes == before
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=journal,
                connection=connection,
                run_id="child",
                intent=child,
                repository_scope=scope,
                targets=list(repositories),
            )
        assert connection.total_changes == before
    assert journal.read_view().run_detail("child") is None


@pytest.mark.parametrize("forged_parent", ["other-parent", "child", ""])
def test_original_journal_rejects_wrong_parent_without_new_child(tmp_path, forged_parent):
    db, journal, scope, repositories, child = _case(tmp_path, kind="readmission")
    forged_execution = child.execution.model_copy(update={"readmitted_from_run_id": forged_parent})
    forged_child = child.model_copy(update={"execution": forged_execution})
    with db.transaction(write=True) as connection:
        before = connection.total_changes
        with pytest.raises((JournalConflict, ValueError)):
            journal.prepare_run_admission_in(
                connection, "child", forged_child, scope, list(repositories)
            )
        assert connection.total_changes == before
    assert journal.read_view().run_detail("child") is None


def test_native_owner_rejects_forged_parent_before_even_consulting_journal(tmp_path, monkeypatch):
    db, journal, scope, repositories, child = _case(tmp_path, kind="readmission")
    forged_execution = child.execution.model_copy(update={"readmitted_from_run_id": "other-parent"})
    forged_child = child.model_copy(update={"execution": forged_execution})
    calls = []
    original = journal.prepare_run_admission_in

    def record(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "prepare_run_admission_in", record)
    with db.transaction(write=True) as connection:
        before = connection.total_changes
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=journal,
                connection=connection,
                run_id="child",
                intent=forged_child,
                repository_scope=scope,
                targets=list(repositories),
            )
        assert connection.total_changes == before
    assert not calls
