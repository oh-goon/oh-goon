"""M157 non-live multi-repository authority: exact membership and two observations."""

from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as native
from oh_goon.repository_tooling import enforcer_lace_run_source_issuer as issuer
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
    setup,
)


def prepared(monkeypatch, tmp_path):
    repository_roots = {}
    admissions = []
    observed = {}
    for _index, key in enumerate(("a", "b", "c")):
        repository_id = "repo-" + key * 20
        root = tmp_path / f"repository-{key}"
        root.mkdir()
        (root / "code.txt").write_bytes(bytes(key, "ascii"))
        subject = "repo-subject-instance-" + key * 64
        snapshot = SnapshotIdentity(key * 64, 1, 1)
        authority = RepositorySourceAuthority.create(
            subject_instance_id=subject,
            source_snapshot=PlanSnapshotEvidence(
                sha256=snapshot.sha256, byte_count=1, entry_count=1
            ),
        )
        admission = PlanRepositoryAdmissionEvidence.create(
            repository_id=repository_id,
            plan_id="same-plan",
            semantic_preview_id="fleet-plan-repository-preview-" + key * 64,
            source_authority=authority,
        )
        admissions.append(admission)
        repository_roots[repository_id] = root
        observed[root] = (subject, snapshot)
    monkeypatch.setattr(native, "repository_subject_instance_id", lambda root: observed[root][0])

    def observe(root):
        content = (root / "code.txt").read_bytes()
        snapshot = observed[root][1]
        return (
            snapshot
            if content == snapshot.sha256[:1].encode()
            else SnapshotIdentity("e" * 64, len(content), 1)
        )

    monkeypatch.setattr(native, "source_snapshot_identity", observe)
    admissions = tuple(admissions)
    selected = tuple(item.repository_id for item in admissions)
    _db, _journal, _env, contract, keys, _ = setup()
    values = contract.fields
    values["source_identity"] = issuer.admitted_run_repository_source_identity(
        admissions=admissions, selected_repository_ids=selected
    )
    return (
        repository_roots,
        admissions,
        selected,
        AdmittedRunContract.freeze(values),
        keys,
        observed,
    )


def issue(repo_roots, admissions, selected, contract, **overrides):
    args = dict(
        repositories=repo_roots,
        admissions=admissions,
        selected_repository_ids=selected,
        contract=contract,
        issuer_id=roots()["source_identity"][0],
        signing_key=_test_private_key("source_identity"),
    )
    args.update(overrides)
    return issuer.issue_observed_run_repository_source_identity(**args)


def test_full_run_claim_verifies_against_independent_ed25519_root(monkeypatch, tmp_path):
    repos, admissions, selected, contract, keys, _ = prepared(monkeypatch, tmp_path)
    signed = issue(repos, admissions, selected, contract)
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
    assert contract.fields["source_identity"] != native.admitted_repository_source_identity(
        admissions[0].source_authority
    )


@pytest.mark.parametrize("selected_indices", [(0, 1), (0, 2), (1, 2), (0, 1, 1)])
def test_partial_duplicate_or_reordered_capture_rejected(monkeypatch, tmp_path, selected_indices):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    wrong = tuple(selected[i] for i in selected_indices)
    with pytest.raises(TrustProtocolError, match="scope|canonical"):
        issue(repos, admissions, wrong, contract)


def test_subset_or_extra_repository_root_rejected(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    for changed in (
        {k: v for k, v in repos.items() if k != selected[-1]},
        {**repos, "repo-" + "f" * 20: tmp_path},
    ):
        with pytest.raises(TrustProtocolError, match="exact repository set"):
            issue(changed, admissions, selected, contract)


def test_swapping_same_admissions_changes_run_identity(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    changed = tuple(reversed(admissions))
    with pytest.raises(TrustProtocolError, match="canonical"):
        issuer.admitted_run_repository_source_identity(
            admissions=changed, selected_repository_ids=selected
        )
    fields = contract.fields
    fields["source_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="contract source identity differs"):
        issue(repos, admissions, selected, AdmittedRunContract.freeze(fields))


def test_one_member_content_drift_invalidates_whole_run(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    (repos[selected[-1]] / "code.txt").write_text("tampered")
    with pytest.raises(TrustProtocolError, match="differ"):
        issue(repos, admissions, selected, contract)


def test_one_member_changes_on_second_native_observation(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, observed = prepared(monkeypatch, tmp_path)
    original = native.source_snapshot_identity
    calls = 0

    def race(root):
        nonlocal calls
        calls += 1
        return SnapshotIdentity("d" * 64, 1, 1) if calls == 6 else original(root)

    monkeypatch.setattr(native, "source_snapshot_identity", race)
    with pytest.raises(TrustProtocolError, match="changed"):
        issue(repos, admissions, selected, contract)
    assert calls == 6


def test_subject_replaced_during_second_pass(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, observed = prepared(monkeypatch, tmp_path)
    calls = 0

    def race(root):
        nonlocal calls
        calls += 1
        return "repo-subject-instance-" + "f" * 64 if calls == 5 else observed[root][0]

    monkeypatch.setattr(native, "repository_subject_instance_id", race)
    with pytest.raises(TrustProtocolError, match="changed"):
        issue(repos, admissions, selected, contract)


def test_duplicate_admission_and_mixed_plans_rejected(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    dup = (admissions[0], admissions[0], admissions[2])
    with pytest.raises(TrustProtocolError, match="canonical"):
        issue(repos, dup, selected, contract)
    other = PlanRepositoryAdmissionEvidence.create(
        repository_id=admissions[1].repository_id,
        plan_id="other-plan",
        semantic_preview_id=admissions[1].semantic_preview_id,
        source_authority=admissions[1].source_authority,
    )
    with pytest.raises(TrustProtocolError, match="one admitted Plan"):
        issue(repos, (admissions[0], other, admissions[2]), selected, contract)


def test_alias_and_nested_roots_rejected(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    alias = tmp_path / "symlink"
    alias.symlink_to(repos[selected[0]], target_is_directory=True)
    with pytest.raises(TrustProtocolError, match="unsafe"):
        issue({**repos, selected[0]: alias}, admissions, selected, contract)
    with pytest.raises(TrustProtocolError, match="disjoint"):
        issue({**repos, selected[1]: repos[selected[0]]}, admissions, selected, contract)
    nested = repos[selected[0]] / "child"
    nested.mkdir()
    with pytest.raises(TrustProtocolError, match="disjoint"):
        issue({**repos, selected[1]: nested}, admissions, selected, contract)


def test_missing_signer_or_hashmarks_rejects(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    with pytest.raises(TrustProtocolError, match="independent signing"):
        issue(repos, admissions, selected, contract, signing_key=None)
    monkeypatch.setattr(
        native,
        "source_snapshot_identity",
        lambda root: (_ for _ in ()).throw(ModuleNotFoundError("hashmarks unavailable")),
    )
    with pytest.raises(TrustProtocolError, match="native repository snapshot observation failed"):
        issue(repos, admissions, selected, contract)


def test_single_repository_scope_rejects_multi_contract(monkeypatch, tmp_path):
    repos, admissions, selected, contract, _, _ = prepared(monkeypatch, tmp_path)
    with pytest.raises(TrustProtocolError, match="multi-repository admission"):
        issuer.admitted_run_repository_source_identity(
            admissions=admissions[:1], selected_repository_ids=selected[:1]
        )
