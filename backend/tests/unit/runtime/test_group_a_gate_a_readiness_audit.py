"""M190 Gate A negative evidence: replay is not a licensed native cutover.

These are fail-before-work observations of existing owners, NOT certification
of Hashmarks, a native replay issuer, or physical LACE.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.vocabulary import FleetRunSource, FleetRunTerminalIntent
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.factories.journal import admit_run, terminalize_run
from tests.unit.runtime.test_readmission_lineage import (
    _intent,
    _placement_lost_failure,
    _target,
)


def _authenticated_lifecycle(database: LocalDatabase) -> FleetRunLifecycle:
    # Exercise the original lifecycle method and original admission serializer,
    # not an alternative mock implementation of the replay authority.
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._database = database
    lifecycle._require_authenticated_fleet = True
    lifecycle._fleet_prework = Mock(side_effect=AssertionError("native observation before gate"))
    lifecycle._fleet_provisioner = Mock(side_effect=AssertionError("provisioning before gate"))
    return lifecycle


@pytest.mark.parametrize("lineage_field", ["readmitted_from_run_id", "rerun_from_run_id"])
def test_original_lifecycle_denies_authenticated_replay_before_provisioner(
    tmp_path: Path, lineage_field: str,
) -> None:
    lifecycle = _authenticated_lifecycle(LocalDatabase(tmp_path / "authority.db"))
    kwargs = {lineage_field: "parent"}
    with pytest.raises(TrustProtocolError, match="replay Fleet ingress requires"):
        lifecycle.require_fleet_prework_available(_target(), "fresh-child", **kwargs)
    lifecycle._fleet_prework.assert_not_called()
    lifecycle._fleet_provisioner.assert_not_called()


def test_real_journal_missing_child_cannot_initiate_authenticated_readmission(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "gate-a-negative.db")
    journal = Journal(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})
    admit_run(journal, "parent", _intent("parent", repository, source=FleetRunSource.CICD), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())
    assert journal.read_view().readmission_child("parent") is None

    manager = object.__new__(FleetRunControlPlane)
    manager._journal = journal
    manager._lifecycle = _authenticated_lifecycle(database)
    manager._start_intent = Mock(side_effect=AssertionError("must not start admission"))

    with pytest.raises(TrustProtocolError, match="replay Fleet ingress requires"):
        manager.readmit("parent", new_fleet_run_id="child")

    manager._start_intent.assert_not_called()
    assert journal.read_view().readmission_child("parent") is None
    assert journal.read_view().run_detail("child") is None
    assert journal.read_view().required_run_detail("parent").run.fleet_run_id == "parent"


def test_real_journal_existing_child_is_read_only_not_new_replay_authority(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "already-admitted-child.db")
    journal = Journal(database)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})
    admit_run(journal, "parent", _intent("parent", repository, source=FleetRunSource.CICD), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())
    # This is a historical, already-admitted Journal child, not an authenticated
    # positive Replay issuer. The read path may return it but cannot recreate it.
    admit_run(journal, "child", _intent("child", repository, parent_id="parent", source=FleetRunSource.CICD), scope, [repository])
    original = journal.read_view().readmission_child("parent")
    assert original is not None

    manager = object.__new__(FleetRunControlPlane)
    manager._journal = journal
    manager._lifecycle = _authenticated_lifecycle(database)
    manager._start_intent = Mock(side_effect=AssertionError("must not begin a new child"))
    returned = manager.readmit("parent", new_fleet_run_id="client-retry")
    assert returned.fleet_run_id == original.run.fleet_run_id == "child"
    manager._start_intent.assert_not_called()
    assert journal.read_view().run_detail("client-retry") is None
