"""CO-R3Q: original admitted signer/cancellation must remain live after reserve.

These are hosted transaction and handoff negative controls, not physical custody
or native writable-cgroup evidence.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceReservation
from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _original_reservation
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _execute


def test_original_reservation_rechecked_after_commit_with_same_signed_intent(tmp_path, monkeypatch):
    backend, _touched, request, conn, journal, session, original, checks = _original_reservation(tmp_path, monkeypatch)
    try:
        proof = original.reserve(request, backend)
        original.require_effect_authority(proof)
        assert len(checks) == 4  # original reserve before/after + effect before/after
        assert session.commit_count == 2
        assert [e['kind'] for e in journal.verify('run-one', auth=original.authenticator)] == [
            'RUN_ADMITTED', 'RESOURCE_INTENT',
        ]
        with pytest.raises(CausalJournalError, match='original reservation'):
            original.require_effect_authority(replace(proof, step_id='foreign'))
        with pytest.raises(CausalJournalError, match='signed original intent'):
            original.require_effect_authority(replace(proof, attempt_identity='sha256:'+'f'*64))
        session.cancelled = True
        with pytest.raises(CausalJournalError, match='cancellation'):
            original.require_effect_authority(proof)
    finally:
        conn.close()


def test_revocation_after_reservation_prevents_cgroup_open(tmp_path, monkeypatch):
    backend, _touched, request, conn, journal, session, original, _ = _original_reservation(tmp_path, monkeypatch)
    try:
        proof = original.reserve(request, backend)
        def revoked(_connection):
            raise TrustProtocolError('original admitted signer epoch revoked')
        # Focus the live generation cut after original reservation, not a
        # replay of a forged proof. No native allocation should be attempted.
        permit = replace(original, validate_authority_in=revoked)
        monkeypatch.setattr(OriginalFleetLocalLaceReservation, 'reserve', lambda *_: proof)
        request.local_resource_intent = permit
        import oh_goon.integrations.fleet_plan_bubblewrap_isolation as native
        opened = Mock()
        lease = SimpleNamespace(open=opened, close=Mock(), lace_intent=None)
        monkeypatch.setattr(native.CgroupV2AttemptLease, 'create', lambda *_a, **_k: lease)
        monkeypatch.setattr(native.RestrictedEgressLease, 'create', lambda *_a, **_k: None)
        with pytest.raises(TrustProtocolError, match='revoked'):
            _execute(backend, request)
        opened.assert_not_called()
        assert len(journal.verify('run-one', auth=original.authenticator)) == 2
    finally:
        conn.close()


def test_cancel_after_cgroup_open_prevents_network_effect_and_cleans_up(tmp_path, monkeypatch):
    backend, _touched, request, conn, _journal, session, original, _ = _original_reservation(tmp_path, monkeypatch)
    try:
        proof = original.reserve(request, backend)
        monkeypatch.setattr(OriginalFleetLocalLaceReservation, 'reserve', lambda *_: proof)
        request.local_resource_intent = original
        import oh_goon.integrations.fleet_plan_bubblewrap_isolation as native
        def open_cgroup():
            session.cancelled = True
        cg_close = Mock()
        net_open = Mock()
        net_close = Mock()
        monkeypatch.setattr(native.CgroupV2AttemptLease, 'create', lambda *_a, **_k:
                            SimpleNamespace(open=open_cgroup, close=cg_close, lace_intent=None))
        monkeypatch.setattr(native.RestrictedEgressLease, 'create', lambda *_a, **_k:
                            SimpleNamespace(open=net_open, close=net_close, lace_intent=None))
        with pytest.raises(CausalJournalError, match='cancellation'):
            _execute(backend, request)
        net_open.assert_not_called()
        net_close.assert_called_once()
        cg_close.assert_called_once()
    finally:
        conn.close()


def test_revocation_after_allocation_observation_blocks_payload_launch(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import oh_goon.integrations.fleet_plan_bubblewrap_isolation as native

    backend, _touched, request, conn, _journal, session, original, _ = _original_reservation(tmp_path, monkeypatch)
    try:
        proof = original.reserve(request, backend)
        monkeypatch.setattr(OriginalFleetLocalLaceReservation, 'reserve', lambda *_: proof)
        request.local_resource_intent = original
        revoked = False
        observed = Mock()
        closed = Mock()
        def observe_then_revoke(*_a):
            nonlocal revoked
            observed()
            revoked = True
        def live_cut(_connection):
            if revoked:
                raise TrustProtocolError('signed epoch revoked before payload launch')
        request.local_resource_intent = replace(original, validate_authority_in=live_cut)
        monkeypatch.setattr(OriginalFleetLocalLaceReservation, 'observe', observe_then_revoke)
        monkeypatch.setattr(native.CgroupV2AttemptLease, 'create', lambda *_a, **_k:
                            SimpleNamespace(open=Mock(), close=closed, lace_intent=None,
                                            command_prefix=lambda: ()))
        monkeypatch.setattr(native.RestrictedEgressLease, 'create', lambda *_a, **_k: None)
        monkeypatch.setattr(native, 'observe_local_allocation', lambda *_a, **_k: object())
        monkeypatch.setattr(native.OriginalBindSourceLease, 'pin_prepared',
                            lambda *_: nullcontext(SimpleNamespace(verify_kernel_custody=Mock())))
        monkeypatch.setattr(native, 'sandbox_process_launch', lambda *_: object())
        atomic = SimpleNamespace(verify_kernel_custody=Mock(), rewind_for_bubblewrap=Mock(),
                                 receipt=None, economics=None)
        monkeypatch.setattr(native, 'bind_atomic_runtime_authority', lambda *_: nullcontext(atomic))
        monkeypatch.setattr(backend, '_command', lambda *_a, **_k: (('/bin/true',), {}))
        monkeypatch.setattr(native, '_require_original_native_fd_handoff', lambda *_: ())
        payload = Mock(side_effect=AssertionError('payload must not start'))
        monkeypatch.setattr(native, 'run_command', payload)
        with pytest.raises(TrustProtocolError, match='before payload launch'):
            _execute(backend, request)
        observed.assert_called_once()
        closed.assert_called_once()
        payload.assert_not_called()
    finally:
        conn.close()
