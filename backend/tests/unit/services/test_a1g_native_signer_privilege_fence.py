"""A1g: exact native signer privilege is a fail-before-work deployment precondition.

The credentials/status samples here are synthetic regression stimuli; they are
never an authentic host principal, issuer or deployment attestation.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_prework_provisioner as owner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _status(*, no_new_privs: str = "1", cap_inh: str = "0", cap_prm: str = "0",
            cap_eff: str = "0", cap_amb: str = "0", fsuid: int = 1001,
            fsgid: int = 1001) -> str:
    return ("Name:\toh-goon\n"
            f"Uid:\t1001\t1001\t1001\t{fsuid}\n"
            f"Gid:\t1001\t1001\t1001\t{fsgid}\n"
            f"NoNewPrivs:\t{no_new_privs}\nCapInh:\t{cap_inh}\n"
            f"CapPrm:\t{cap_prm}\nCapEff:\t{cap_eff}\nCapAmb:\t{cap_amb}\n")


def _credentials(monkeypatch, *, uids=(1001, 1001, 1001), gids=(1001, 1001, 1001),
                 status: str | None = None, euid: int | None = None) -> None:
    monkeypatch.setattr(os, "getresuid", lambda: uids)
    monkeypatch.setattr(os, "getresgid", lambda: gids)
    monkeypatch.setattr(os, "getgroups", lambda: [1001, 2002])
    # A1g tests isolate caller credentials; A1h separately exercises task census.
    monkeypatch.setattr(owner, "_native_signer_thread_census", lambda *_: (99, 1, 111))
    monkeypatch.setattr(os, "geteuid", lambda: uids[1] if euid is None else euid)
    original = Path.read_text
    def read_text(self, *args, **kwargs):
        if self == Path("/proc/thread-self/status"):
            return _status() if status is None else status
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)


def test_unprivileged_native_principal_is_observed_without_claiming_attestation(monkeypatch):
    _credentials(monkeypatch)
    observed = owner._native_unprivileged_signer_identity()
    assert observed[:8] == (1001,) * 8
    assert observed[-5:] == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("case,kwargs", [
    ("root-euid", {"uids": (0, 0, 0)}),
    ("saved-root", {"uids": (1001, 1001, 0)}),
    ("real-root", {"uids": (0, 1001, 1001)}),
    ("saved-other", {"uids": (1001, 1001, 1002)}),
    ("mismatched-group", {"gids": (1001, 1002, 1001)}),
    ("observer-drift", {"euid": 1002}),
    ("may-gain-privilege-on-exec", {"status": _status(no_new_privs="0")}),
    ("inheritable-capability", {"status": _status(cap_inh="0000000000000001")}),
    ("permitted-capability", {"status": _status(cap_prm="0000000000000001")}),
    ("effective-capability", {"status": _status(cap_eff="0000000000000001")}),
    ("ambient-capability", {"status": _status(cap_amb="0000000000000001")}),
    ("filesystem-root", {"status": _status(fsuid=0)}),
    ("filesystem-group-drift", {"status": _status(fsgid=1002)}),
    ("kernel-uid-drift", {"uids": (1002, 1002, 1002)}),
    ("kernel-gid-drift", {"gids": (1002, 1002, 1002)}),
    ("no-proc-status", {"status": "Name:\toh-goon\n"}),
    ("duplicate-status", {"status": _status() + "CapEff:\t0000000000000000\n"}),
    ("corrupt-capability", {"status": _status(cap_prm="not-hex")}),
])
def test_privilege_bypass_or_missing_native_evidence_fails_closed(monkeypatch, case, kwargs):
    with monkeypatch.context() as patches:
        _credentials(patches, **kwargs)
        with pytest.raises(TrustProtocolError):
            owner._native_unprivileged_signer_identity()


def test_original_provisioner_does_not_admit_privileged_deploying_process(tmp_path, monkeypatch):
    args, _params, conn, calls, lifecycle, binding, provisioner, _inventory, roots, admit = provisioned(
        tmp_path, monkeypatch,
    )
    try:
        # The fake POSIX credentials are only a negative regression fixture.
        # The actual trust roots remain signer-owned: fail before any Fleet work.
        with monkeypatch.context() as patches:
            _credentials(patches, status=_status(cap_eff="1"))
            lifecycle._fleet_provisioner = replace(provisioner, public_root_owner_uid=0)
            with pytest.raises(TrustProtocolError, match="privileged OS authority"):
                admit()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_principal_change_during_ancestry_observation_is_rejected(tmp_path, monkeypatch):
    _args, _params, conn, _calls, _lifecycle, binding, provisioner, *_rest = provisioned(
        tmp_path, monkeypatch,
    )
    try:
        # /usr is used only to test the ancestry observation, not as an issuer
        # trust root, and no trust/Run authority is derived from the path.
        strict = replace(provisioner, trusted_key_directory=Path("/usr"), public_root_owner_uid=65534)
        sequence = iter(((1001,), (1002,)))
        with monkeypatch.context() as patches:
            patches.setattr(os, "geteuid", lambda: 1001)
            patches.setattr(owner, "_native_unprivileged_signer_identity", lambda: next(sequence))
            with pytest.raises(TrustProtocolError, match="principal changed"):
                strict._require_separate_custody(binding)
    finally:
        conn.close()
