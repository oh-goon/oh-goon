"""A1f: independently OS-owned verifier roots are possible but never an issuer.

These fixtures are diagnostic only, not proof of a real deploying-process
principal, key provenance, host attestation, or authenticated Run admission.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.enforcer_lace_issuer_keyring import load_explicit_issuer_verifier
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_explicit_issuer_keyring import secure_roots
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned

pytestmark = pytest.mark.certification


def _foreign_uid() -> int:
    return 65534 if os.geteuid() != 65534 else 65533


def _make_foreign_roots(directory: Path) -> int:
    if os.geteuid() != 0:
        pytest.skip("real cross-UID filesystem custody test requires chown authority")
    uid = _foreign_uid()
    for leaf in directory.iterdir():
        os.chown(leaf, uid, -1)
        leaf.chmod(0o644)
    os.chown(directory, uid, -1)
    directory.chmod(0o755)
    return uid


def test_explicit_foreign_uid_root_verifies_without_signing(tmp_path):
    directory, refs, _keys = secure_roots(tmp_path)
    uid = _make_foreign_roots(directory)
    verifier = load_explicit_issuer_verifier(directory, refs, public_root_owner_uid=uid)
    assert len(verifier.public_roots()) == 6
    assert not hasattr(verifier, "sign")
    assert not hasattr(verifier, "issue")
    with pytest.raises(TrustProtocolError):
        load_explicit_issuer_verifier(directory, refs)  # no ambient foreign-owner fallback


@pytest.mark.parametrize(
    "attack",
    [
        "unpinned",
        "same-process-owner",
        "bool-owner",
        "negative-owner",
        "wrong-owner",
        "writable-directory",
        "writable-leaf",
        "wrong-leaf-owner",
        "symlink-leaf",
        "same-byte-leaf-replacement",
    ],
)
def test_foreign_owned_root_rejects_untrusted_authority(tmp_path, monkeypatch, attack):
    directory, refs, _keys = secure_roots(tmp_path)
    uid = _make_foreign_roots(directory)
    if attack == "unpinned":
        uid = None
    elif attack == "same-process-owner":
        uid = os.geteuid()
    elif attack == "bool-owner":
        uid = True
    elif attack == "negative-owner":
        uid = -1
    elif attack == "wrong-owner":
        uid += 1
    elif attack == "writable-directory":
        directory.chmod(0o777)
    elif attack == "writable-leaf":
        (directory / refs[next(iter(refs))].filename).chmod(0o666)
    elif attack == "wrong-leaf-owner":
        os.chown(directory / refs[next(iter(refs))].filename, os.geteuid(), -1)
    elif attack == "symlink-leaf":
        leaf = directory / refs[next(iter(refs))].filename
        value = leaf.read_bytes()
        leaf.unlink()
        replacement = directory / "replacement.pub"
        replacement.write_bytes(value)
        os.chown(replacement, _foreign_uid(), -1)
        leaf.symlink_to(replacement)
    elif attack == "same-byte-leaf-replacement":
        real_read = os.read
        swapped = False

        def replace_after_read(fd, n):
            nonlocal swapped
            result = real_read(fd, n)
            if not swapped:
                swapped = True
                field = sorted(refs)[0]
                leaf = directory / refs[field].filename
                backup = directory / "same-byte.pub"
                backup.write_bytes(leaf.read_bytes())
                os.chown(backup, _foreign_uid(), -1)
                backup.chmod(0o644)
                os.replace(backup, leaf)
            return result

        monkeypatch.setattr(os, "read", replace_after_read)
    with pytest.raises(TrustProtocolError):
        load_explicit_issuer_verifier(directory, refs, public_root_owner_uid=uid)


def test_original_provisioner_rejects_privileged_signer_even_with_distinct_uids(
    tmp_path, monkeypatch
):
    args, _params, conn, calls, _lifecycle, binding, provisioner, _inventory, roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    uid = _make_foreign_roots(roots)
    try:
        owner = replace(provisioner, public_root_owner_uid=uid)
        with pytest.raises(TrustProtocolError, match="privileged OS authority"):
            owner.inspect_independent_root_custody("durable-run")
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
    finally:
        conn.close()


def test_legacy_or_unenrolled_custody_never_qualifies(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, _binding, provisioner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    try:
        with pytest.raises(TrustProtocolError, match="not provisioned"):
            provisioner.inspect_independent_root_custody("durable-run")
        with pytest.raises(TrustProtocolError, match="not provisioned"):
            provisioner.inspect_independent_root_custody("unknown-run")
        with pytest.raises(
            TrustProtocolError, match="complete explicit Fleet deployment provisioning"
        ):
            replace(provisioner, public_root_owner_uid=os.geteuid())
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_signer_mutable_public_ancestor_rejected_before_inventory(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, provisioner, _inventory, roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    # In this hosted fixture, public roots are below a signer-controlled tmp
    # parent. Simulate an unprivileged signer EUID solely for the ancestry
    # classifier; this is NOT a positive real-host principal observation.
    try:
        owner = replace(provisioner, public_root_owner_uid=_foreign_uid())
        from oh_goon.services import fleet_prework_provisioner as module

        with monkeypatch.context() as patch:
            patch.setattr(os, "geteuid", lambda: 1001)
            patch.setattr(module, "_native_unprivileged_signer_identity", lambda: (1001,))
            with pytest.raises(TrustProtocolError, match="signer-mutable"):
                owner._require_separate_custody(binding)
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_public_verifier_observer_euid_drift_fails_closed(tmp_path, monkeypatch):
    directory, refs, _keys = secure_roots(tmp_path)
    original = os.geteuid()
    observations = 0

    def changed_euid():
        nonlocal observations
        observations += 1
        return original if observations <= 8 else original + 1

    monkeypatch.setattr(os, "geteuid", changed_euid)
    with pytest.raises(TrustProtocolError, match="observer principal changed"):
        load_explicit_issuer_verifier(directory, refs)
