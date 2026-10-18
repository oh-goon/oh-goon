"""A1i: a Linux TID reused after exit must not inherit custody evidence.

These are synthetic negative stimuli. They do not attest a deployment host,
independent issuer ownership, or the original M205 certificate.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_prework_provisioner as owner
from tests.unit.services.test_a1h_process_wide_signer_custody import _census, _kernel, _stat
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _births(monkeypatch, *, changed_on: int = 0, replacement: str | None = None) -> None:
    """Patch the A1h fake kernel with a per-thread task birth identity."""
    _kernel(monkeypatch)
    original = Path.read_text
    observations = 0

    def read_text(path, *args, **kwargs):
        nonlocal observations
        if str(path) == "/proc/self/task/222/stat":
            observations += 1
            if changed_on and observations >= changed_on:
                return replacement if replacement is not None else _stat(222, started=20000)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_stable_birth_census_remains_exact(monkeypatch):
    _births(monkeypatch)
    assert _census() == (77, 2, 111, 222, 10999, 10999)


@pytest.mark.parametrize("changed_on", [2, 3])
def test_same_tid_recycled_during_or_after_status_is_rejected(monkeypatch, changed_on):
    _births(monkeypatch, changed_on=changed_on)
    with pytest.raises(TrustProtocolError, match="thread replaced"):
        _census()


@pytest.mark.parametrize(
    "bad_stat",
    [
        "",
        "222 (task) S 0 1\n",
        "999 (task) S " + " ".join(["0"] * 19),
        "222 (task) S " + " ".join(["0"] * 18 + ["invalid"]),
        "222 task S " + " ".join(["0"] * 18 + ["1000"]),
    ],
)
def test_missing_or_corrupt_native_task_birth_identity_rejected(monkeypatch, bad_stat):
    _births(monkeypatch, changed_on=1, replacement=bad_stat)
    with pytest.raises(TrustProtocolError, match="birth identity"):
        _census()


def test_original_admission_refuses_tid_reuse_before_any_work(tmp_path, monkeypatch):
    args, _params, conn, calls, lifecycle, _binding, provisioner, _inv, _roots, admit = provisioned(
        tmp_path, monkeypatch
    )
    try:
        with monkeypatch.context() as patches:
            patches.setattr(
                owner,
                "_native_unprivileged_signer_identity",
                lambda: owner._native_signer_thread_census((1001,) * 4, (1001,) * 4, (1001, 2002)),
            )
            _births(patches, changed_on=2)
            patches.setattr(os, "geteuid", lambda: 1001)
            lifecycle._fleet_provisioner = replace(provisioner, public_root_owner_uid=0)
            with pytest.raises(TrustProtocolError, match="thread replaced"):
                admit()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()
