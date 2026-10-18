"""M156: no self-asserted Fleet/Direct source identity can become issuer evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as issuer
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
    setup,
)


def prepared(monkeypatch, tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "code.txt").write_text("baseline", encoding="utf8")
    native_snapshot = SnapshotIdentity("b" * 64, 8, 1)
    native_subject = "repo-subject-instance-" + "c" * 64

    def observed(path):
        assert path == root
        payload = (path / "code.txt").read_bytes()
        return (
            native_snapshot
            if payload == b"baseline"
            else SnapshotIdentity("d" * 64, len(payload), 1)
        )

    monkeypatch.setattr(issuer, "source_snapshot_identity", observed)
    monkeypatch.setattr(issuer, "repository_subject_instance_id", lambda path: native_subject)
    authority = RepositorySourceAuthority.create(
        subject_instance_id=native_subject,
        source_snapshot=PlanSnapshotEvidence(
            sha256=native_snapshot.sha256,
            byte_count=native_snapshot.byte_count,
            entry_count=native_snapshot.entry_count,
        ),
    )
    _db, _journal, _env, contract, keys, _evidence = setup()
    fields = contract.fields
    fields["source_identity"] = issuer.admitted_repository_source_identity(authority)
    contract = AdmittedRunContract.freeze(fields)
    return root, authority, contract, keys


def issue(repository_root, authority, contract, **override):
    opts = dict(
        root=repository_root,
        authority=authority,
        contract=contract,
        issuer_id=roots()["source_identity"][0],
        signing_key=_test_private_key("source_identity"),
    )
    opts.update(override)
    return issuer.issue_observed_repository_source_identity(**opts)


def test_owner_observation_signs_only_admitted_repository_source(monkeypatch, tmp_path):
    root, authority, contract, keys = prepared(monkeypatch, tmp_path)
    signed = issue(root, authority, contract)
    evidence = tuple(
        signed if field == "source_identity" else proof(field, contract, keys) for field in FIELDS
    )
    TrustedIssuerVerifier(keys).verify(
        contract,
        evidence=evidence,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    assert signed.field == "source_identity"
    assert issuer.admitted_repository_source_identity(authority).startswith("sha256:")


def test_self_asserted_contract_identity_is_not_enough(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    fields = contract.fields
    fields["source_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="contract source identity differs"):
        issue(root, authority, AdmittedRunContract.freeze(fields))


def test_modified_repository_source_fails_before_signing(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    (root / "code.txt").write_text("different", encoding="utf8")
    with pytest.raises(TrustProtocolError, match="differs"):
        issue(root, authority, contract)


def test_source_changed_between_native_observations_fails(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    native = issuer.source_snapshot_identity
    n = 0

    def race(path):
        nonlocal n
        n += 1
        return native(path) if n == 1 else SnapshotIdentity("e" * 64, 8, 1)

    monkeypatch.setattr(issuer, "source_snapshot_identity", race)
    with pytest.raises(TrustProtocolError, match="changed"):
        issue(root, authority, contract)
    assert n == 2


def test_subject_replacement_fails_before_signing(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    monkeypatch.setattr(
        issuer, "repository_subject_instance_id", lambda _: "repo-subject-instance-" + "a" * 64
    )
    with pytest.raises(TrustProtocolError, match="differs"):
        issue(root, authority, contract)


def test_subject_replaced_during_issuance_fails(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    n = 0

    def race(_):
        nonlocal n
        n += 1
        return authority.subject_instance_id if n == 1 else "repo-subject-instance-" + "a" * 64

    monkeypatch.setattr(issuer, "repository_subject_instance_id", race)
    with pytest.raises(TrustProtocolError, match="changed"):
        issue(root, authority, contract)


def test_forged_snapshot_counts_are_rejected(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    false_authority = RepositorySourceAuthority.create(
        subject_instance_id=authority.subject_instance_id,
        source_snapshot=PlanSnapshotEvidence(sha256="b" * 64, byte_count=100, entry_count=1),
    )
    fields = contract.fields
    fields["source_identity"] = issuer.admitted_repository_source_identity(false_authority)
    with pytest.raises(TrustProtocolError, match="differs"):
        issue(root, false_authority, AdmittedRunContract.freeze(fields))


def test_no_ambient_key_and_no_symlink_or_relative_root(monkeypatch, tmp_path):
    root, authority, contract, keys = prepared(monkeypatch, tmp_path)
    symlink = tmp_path / "aliased"
    symlink.symlink_to(root, target_is_directory=True)
    for overrides in (
        {"root": symlink},
        {"root": Path("repository")},
        {"root": tmp_path / "missing"},
        {"signing_key": None},
        {"signing_key": keys["source_identity"][1]},
        {"issuer_id": "invalid/issuer"},
    ):
        with pytest.raises(TrustProtocolError):
            issue(root, authority, contract, **overrides)


def test_projection_not_confused_with_release_source(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)
    assert (
        issuer.admitted_repository_source_identity(authority)
        != "sha256:" + authority.source_snapshot.sha256
    )
    assert "fleet-repository" in issuer.SOURCE_IDENTITY_DOMAIN


def test_native_hashmarks_unavailable_fails_closed(monkeypatch, tmp_path):
    root, authority, contract, _ = prepared(monkeypatch, tmp_path)

    def unavailable(_):
        raise ModuleNotFoundError("No module named 'hashmarks'")

    monkeypatch.setattr(issuer, "source_snapshot_identity", unavailable)
    with pytest.raises(TrustProtocolError, match="native repository snapshot observation failed"):
        issue(root, authority, contract)
