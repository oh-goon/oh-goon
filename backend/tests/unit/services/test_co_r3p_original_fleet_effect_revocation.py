"""CO-R3P: original Fleet physical-intent writer rechecks the admitted signer epoch.

The direct Fleet fixture supplies real SQLite admission, signed causal events,
deployment keys and the original lifecycle. Only the strict *type selector* is
substituted in these focused tests; the trust epoch, signed journal, and policy
are real. This is not a native process or independent LACE custody test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_run_lifecycle as owner
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _admitted(tmp_path, monkeypatch):
    args, _params, external, _calls, lifecycle, binding, _provisioner, _inventory, _roots, admit = provisioned(
        tmp_path, monkeypatch,
    )
    admit()  # REAL original Fleet and authenticated causal admission in one database.
    # Focus the live original authority writer, without constructing a second
    # execution authority or a test-only signer. The fixture is Direct; replace
    # ONLY the two classification classes to exercise the strict branch.
    current = args["journal"].read_view().required_run_detail("durable-run")
    expected = SimpleNamespace(execution=current.execution)
    monkeypatch.setattr(owner, "_execution_authority_in", lambda *_: expected)
    monkeypatch.setattr(owner, "StrictFleetExecutionAdmission", type(expected.execution))
    monkeypatch.setattr(owner, "StrictFleetPolicyEnrollment", type(binding))
    return args, external, lifecycle, expected


def _cut(args, lifecycle, expected):
    with args["journal"]._database.transaction(write=True) as connection:
        lifecycle.require_execution_authority_in(connection, "durable-run", expected)


def test_real_admitted_signed_epoch_allows_original_effect_cut(tmp_path, monkeypatch):
    args, external, lifecycle, expected = _admitted(tmp_path, monkeypatch)
    try:
        _cut(args, lifecycle, expected)
        assert external.execute("SELECT count(*) FROM lace_run_events").fetchone()[0] == 2
    finally:
        external.close()


@pytest.mark.parametrize("attack", [
    "revoked", "rotated", "missing-epoch", "revoked-at-commit", "foreign-signed-run",
])
def test_original_effect_cut_rejects_untrusted_or_stale_generation(tmp_path, monkeypatch, attack):
    args, external, lifecycle, expected = _admitted(tmp_path, monkeypatch)
    try:
        if attack == "revoked":
            args["journal"]._database.revoke_signer_trust_atomically(
                expected=args["signer_authority"].active(),
            )
        elif attack == "rotated":
            external.execute("UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1")
        elif attack == "missing-epoch":
            external.execute("DELETE FROM lace_signer_trust_epoch")
        elif attack == "revoked-at-commit":
            external.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
        elif attack == "foreign-signed-run":
            external.execute("UPDATE lace_run_events SET event_payload=? WHERE run_seq=2", (b'{}',))
        with pytest.raises(TrustProtocolError):
            _cut(args, lifecycle, expected)
    finally:
        external.close()


def test_same_original_writer_rechecks_after_provisional_fleet_intent(tmp_path, monkeypatch):
    """No second admission: a revoked epoch inside the original cut rolls back."""
    from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceReservation
    from oh_goon.runtime.enforcer_lace_local_resource_intent import LocalLaceJournalReservation
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
    from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _OriginalSession

    args, external, lifecycle, expected = _admitted(tmp_path, monkeypatch)
    try:
        database = args["journal"]._database
        with database.transaction(write=True) as connection:
            # The original live signer check is valid before provisional writes.
            lifecycle.require_execution_authority_in(connection, "durable-run", expected)
        # This is a focused negative cut: original session callback rather than
        # the tool/FS stack, because no cgroup effects may execute before commit.
        class CommittedSession:
            def mutate(self, action):
                with database.transaction(write=True) as connection:
                    return action(connection)
            def original_fleet_lease_token(self):
                return "stub-token"
            def cancel_requested_in(self, connection):
                return False

        def changed_in_original_cut(connection):
            lifecycle.require_execution_authority_in(connection, "durable-run", expected)
            connection.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
        # A reservation callback's *second* live trust check must veto its
        # entire provisional transaction, rather than permitting publication.
        participant = OriginalFleetLocalLaceReservation(
            session=CommittedSession(), validate_authority_in=changed_in_original_cut,
            authenticator=args["authenticator"], run_id="durable-run", step_id="step-1",
            attempt_number=1, command_ordinal=1, attempt_root=tmp_path,
            execution_environment_identity="sha256:" + "a"*64,
            backend_capability_manifest_identity="sha256:" + "b"*64,
            backend_configuration_identity="test",
        )
        class FakeRequest: pass
        def fake_reserve(self, request, backend):
            # Reach the second, original authority fence after provisional write.
            external_row = self.journal.connection.execute("SELECT state FROM lace_signer_trust_epoch").fetchone()
            assert external_row[0] == "REVOKED"
            return object()
        monkeypatch.setattr(LocalLaceJournalReservation, "reserve", fake_reserve)
        with pytest.raises(TrustProtocolError):
            participant.reserve(FakeRequest(), object())
        assert external.execute("SELECT state FROM lace_signer_trust_epoch").fetchone()[0] == "ACTIVE"
    finally:
        external.close()

@pytest.mark.parametrize('override', [
    {'observer_custody': 'INDEPENDENT'},
    {'coverage': 'COMPLETE'},
    {'effect_permission': 'CLEANUP'},
    {'unverified_resource_classes': ()},
])
def test_local_empty_cgroup_cannot_promote_foreign_writer_absence(override):
    """LACE's current observer is diagnostic, even when its cgroup is empty.

    A detached writer, mount namespace holder, or foreign FD owner is not
    disproved by zero local cgroup processes. Never mint a custody grant.
    """
    from oh_goon.core.ports.physical_observation import OriginalRunPhysicalEnvelope

    kwargs = dict(
        run_id='durable-run', intent_run_seq=2, claim_run_seq=3,
        control_epoch=1, subject_identity='sha256:' + '1'*64,
        signed_intent_identity='sha256:' + '2'*64,
        allocation_identity='sha256:' + '3'*64,
        inventory_identity='sha256:' + '4'*64,
        native_record_identity='sha256:' + '5'*64,
        mount_intent_identity='sha256:' + '6'*64,
        declared_mount_targets=('/workspace',), native_cgroup_device=1,
        native_cgroup_inode=2,
        observed_processes=((123, 'boot:4', 'mnt:[5]', 'sha256:'+'7'*64),),
    )
    envelope = OriginalRunPhysicalEnvelope(**kwargs)
    assert envelope.observer_custody == 'UNVERIFIED'
    assert envelope.effect_permission == 'NONE'
    assert envelope.coverage == 'INCOMPLETE_NATIVE_RESOURCE_SCOPE'
    assert envelope.unverified_resource_classes
    with pytest.raises(ValueError, match='cannot grant authority'):
        OriginalRunPhysicalEnvelope(**(kwargs | override))
