"""M191: authenticated replay is not native prework without a lineage issuer.

Exercise the original Journal and the original FleetAdmissionPlanner producer.
No invented signer, receipt, Plan resolver, or alternate admission path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import FleetRunSource, FleetRunTerminalIntent
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.factories.journal import admit_run, terminalize_run
from tests.factories.run_admission import repository_admissions_for
from tests.unit.runtime.test_readmission_lineage import _intent, _placement_lost_failure


def _case(tmp_path: Path, *, kind: str):
    database = LocalDatabase(tmp_path / f"native-replay-{kind}.db")
    journal = Journal(database)
    repositories = (
        RepositoryIdentity(path=tmp_path / "repo-a"),
        RepositoryIdentity(path=tmp_path / "repo-b"),
    )
    scope = fleet_repository_scope({repo.repository_id for repo in repositories})
    parent = _intent("parent", repositories[0], source=FleetRunSource.CICD)
    parent = parent.model_copy(
        update={"repository_admissions": repository_admissions_for(parent.target, repositories)}
    )
    admit_run(journal, "parent", parent, scope, list(repositories))
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())
    child = _intent(
        "child",
        repositories[0],
        source=(FleetRunSource.CICD if kind == "readmission" else FleetRunSource.MANUAL),
    )
    if kind == "readmission":
        execution = child.execution.model_copy(update={"readmitted_from_run_id": "parent"})
    else:
        # An alternate native capture must fail before Journal policy decides
        # whether MANUAL strict rerun is eligible.
        execution = child.execution.model_copy(update={"rerun_from_run_id": "parent"})
    assert isinstance(execution, StrictFleetExecutionAdmission)
    child = child.model_copy(
        update={
            "execution": execution,
            "repository_admissions": repository_admissions_for(child.target, repositories),
        }
    )
    return database, journal, scope, repositories, child


@pytest.mark.parametrize("kind", ["readmission", "rerun"])
def test_original_native_capture_rejects_replay_before_journal_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    database, journal, scope, repositories, child = _case(tmp_path, kind=kind)
    called = []
    original = journal.prepare_run_admission_in

    def recording_prepare(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "prepare_run_admission_in", recording_prepare)
    with database.transaction(write=True) as connection:
        before = connection.total_changes
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
        assert journal.read_view_from_connection(connection).run_detail("child") is None
    assert not called, "replay must be denied before canonical candidate or issuer capture"
    assert journal.read_view().run_detail("child") is None
    assert journal.read_view().required_run_detail("parent").run.fleet_run_id == "parent"


def test_original_native_capture_keeps_ordinary_fleet_candidate(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "normal-native-candidate.db")
    journal = Journal(database)
    repositories = (
        RepositoryIdentity(path=tmp_path / "repo-a"),
        RepositoryIdentity(path=tmp_path / "repo-b"),
    )
    scope = fleet_repository_scope({r.repository_id for r in repositories})
    intent = _intent("ordinary", repositories[0], source=FleetRunSource.CICD)
    intent = intent.model_copy(
        update={"repository_admissions": repository_admissions_for(intent.target, repositories)}
    )
    with database.transaction(write=True) as connection:
        before = connection.total_changes
        candidate = FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
            journal=journal,
            connection=connection,
            run_id="ordinary",
            intent=intent,
            repository_scope=scope,
            targets=list(repositories),
        )
        assert candidate.fleet_run_id == "ordinary"
        assert connection.total_changes == before
    assert journal.read_view().run_detail("ordinary") is None


@pytest.mark.parametrize("kind", ["readmission", "rerun"])
def test_original_prework_adapter_cannot_smuggle_replay_into_native_capture(
    tmp_path: Path,
    kind: str,
) -> None:
    from oh_goon.repository_tooling.enforcer_lace_prework_capture import PreworkRunCapture

    database, journal, scope, repositories, child = _case(tmp_path, kind=kind)
    with database.transaction(write=True) as connection:
        before = connection.total_changes
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            PreworkRunCapture.capture(
                journal=journal,
                connection=connection,
                run_id="child",
                intent=child,
                repository_scope=scope,
                targets=list(repositories),
            )
        assert connection.total_changes == before
        assert journal.read_view_from_connection(connection).run_detail("child") is None
    assert journal.read_view().readmission_child("parent") is None
