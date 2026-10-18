"""M167: six authenticating signers may use a transaction-owned uncommitted Fleet cut.

No operational RUN_ADMITTED exists, and no callback may supply a different event.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS, IssuerEvidence, ISSUER_PROTOCOL, SIGNATURE_DOMAIN, TrustedIssuerVerifier,
)
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.repository_tooling import enforcer_lace_native_issuer_suite as suite
from oh_goon.repository_tooling import (
    enforcer_lace_backend_capability_issuer as capability_owner,
    enforcer_lace_captured_run_issuer as run_owner,
    enforcer_lace_execution_environment_issuer as environment_owner,
    enforcer_lace_native_lock_issuer as lock_owner,
    enforcer_lace_native_tool_issuer as tool_owner,
)
from oh_goon.repository_tooling.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.unit.services.test_group_a_fixed_native_issuer_suite import (
    fixture as configured_fixture,
)
from tests.unit.services.test_group_a_fleet_prework_capture import setup as new_run_fixture


def _setup(tmp_path, monkeypatch):
    (tmp_path / "configured").mkdir()
    (tmp_path / "fresh").mkdir()
    _, _, _, key_dir, roots, _ = configured_fixture(tmp_path / "configured", monkeypatch)
    journal, contract, path_authority, intent, event = new_run_fixture(tmp_path / "fresh", monkeypatch)
    return journal, contract, path_authority, key_dir, roots, intent, event


def _capture(journal, conn, intent, event):
    return PreworkRunCapture.capture(
        journal=journal, connection=conn, run_id=event.fleet_run_id,
        intent=intent, repository_scope=event.repository_scope, targets=list(event.targets),
    )


def _native_owner_stubs(monkeypatch, contract):
    # Stub only unavailable offline external measurements; each owner must read
    # original prework Journal authority, then sign the exact v2 claim.
    owners = [
        (run_owner, "issue_observed_captured_run_repository_identity", "repository_identity"),
        (run_owner, "issue_observed_captured_run_source", "source_identity"),
        (lock_owner, "issue_observed_captured_run_lock_identity", "lock_identity"),
        (tool_owner, "issue_observed_captured_run_native_tool_identity", "native_tool_identity"),
        (environment_owner, "issue_observed_captured_run_execution_environment_identity", "execution_environment_identity"),
        (capability_owner, "issue_observed_captured_run_backend_capability_manifest_identity", "backend_capability_manifest_identity"),
    ]
    observed = []
    for module, method, field in owners:
        def issuer(*, journal, contract, issuer_id, signing_key, prework=None, _field=field, **kwargs):
            assert type(prework) is PreworkRunCapture
            observed.append(_field)
            admission, revision = run_owner._read_original_admission(
                journal, contract.fields["run_id"], prework=prework)
            assert admission.sequence == 1
            assert admission.fleet_run_id == contract.fields["run_id"]
            assert journal.read_view_from_connection(prework.connection).run_detail(admission.fleet_run_id) is None
            payload = canonical_bytes({
                "protocol": ISSUER_PROTOCOL, "field": _field,
                "run_id": contract.fields["run_id"], "contract_identity": contract.identity,
                "identity": contract.fields[_field],
                "executor_generation": contract.fields["executor_generation"],
            })
            signed = SIGNATURE_DOMAIN + _field.encode("ascii") + b"\x00" + payload
            return IssuerEvidence(_field, issuer_id, payload, signing_key.sign(signed).hex())
        monkeypatch.setattr(module, method, issuer)
    return observed


def test_six_independent_precommit_issuers_before_any_run_fact(tmp_path, monkeypatch):
    journal, contract, authority, directory, roots, intent, event = _setup(tmp_path, monkeypatch)
    calls = _native_owner_stubs(monkeypatch, contract)
    with journal._database.transaction(write=True) as conn:
        before = conn.total_changes
        cut = _capture(journal, conn, intent, event)
        evidence = suite.issue_fixed_native_admission_evidence(
            journal=journal, contract=contract, path_authority=authority,
            key_directory=directory, trusted_public_roots=roots, prework=cut)
        assert len(evidence) == len(AUTHORITY_FIELDS) == 6
        assert set(calls) == AUTHORITY_FIELDS
        assert conn.total_changes == before
        assert journal.read_view_from_connection(conn).run_detail(event.fleet_run_id) is None
        TrustedIssuerVerifier(roots).verify(contract, evidence=evidence, control_epoch=1,
                                            lease_identity="lease", lease_is_current=lambda *_: True)
    assert journal.read_view().run_detail(event.fleet_run_id) is None


@pytest.mark.parametrize("fault", [
    "expired-transaction", "wrong-journal", "wrong-run", "fake-cut", "mutated-intent",
    "committed-early", "unexpected-journal-write", "no-cut",
])
def test_prework_rejects_mutation_and_owner_substitution(tmp_path, monkeypatch, fault):
    journal, contract, authority, directory, roots, intent, event = _setup(tmp_path, monkeypatch)
    calls = _native_owner_stubs(monkeypatch, contract)
    with journal._database.transaction(write=True) as conn:
        cut = _capture(journal, conn, intent, event)
        if fault == "wrong-journal":
            with pytest.raises(TrustProtocolError):
                cut.read(journal=Journal(LocalDatabase(tmp_path / "other.sqlite")), run_id=event.fleet_run_id)
        elif fault == "wrong-run":
            with pytest.raises(TrustProtocolError):
                cut.read(journal=journal, run_id="other-run")
        elif fault == "fake-cut":
            with pytest.raises(TrustProtocolError):
                suite.issue_fixed_native_admission_evidence(
                    journal=journal, contract=contract, path_authority=authority,
                    key_directory=directory, trusted_public_roots=roots, prework=object())
        elif fault == "mutated-intent":
            # Caller changes the original model after capture: immutable source copy
            # is still validated against the real original Journal, not new caller data.
            intent = intent.model_copy(update={"source": "NOT_AN_AUTHORITY"})
            assert cut.read(journal=journal, run_id=event.fleet_run_id)[0] == cut.admitted
        elif fault == "committed-early":
            journal.admit_run_in(conn, event.fleet_run_id, cut.intent, cut.scope, list(cut.targets))
            with pytest.raises(TrustProtocolError):
                cut.read(journal=journal, run_id=event.fleet_run_id)
        elif fault == "unexpected-journal-write":
            conn.execute("CREATE TABLE m167_wrong_mutation (id INTEGER)")
            conn.execute("INSERT INTO m167_wrong_mutation VALUES (1)")
            with pytest.raises(TrustProtocolError):
                cut.read(journal=journal, run_id=event.fleet_run_id)
        elif fault == "no-cut":
            with pytest.raises(TrustProtocolError):
                suite.issue_fixed_native_admission_evidence(
                    journal=journal, contract=contract, path_authority=authority,
                    key_directory=directory, trusted_public_roots=roots)
        else:
            assert fault == "expired-transaction"
    if fault == "expired-transaction":
        with pytest.raises(TrustProtocolError):
            cut.read(journal=journal, run_id=event.fleet_run_id)
    assert not calls


def test_precommit_reader_does_not_accept_a_caller_claimed_event(tmp_path, monkeypatch):
    journal, _, _, _, _, intent, event = _setup(tmp_path, monkeypatch)
    with journal._database.transaction(write=True) as conn:
        cut = _capture(journal, conn, intent, event)
        # A direct dataclass replacement is still subject to re-validation of
        # the actual original Journal's canonical projected event.
        forged = replace(cut, admitted=cut.admitted.model_copy(update={"fleet_run_id": "attacker"}))
        with pytest.raises(TrustProtocolError, match="capture changed"):
            forged.read(journal=journal, run_id=event.fleet_run_id)


def test_lookalike_sqlite_database_cannot_sponsor_original_run(tmp_path, monkeypatch):
    journal, _, _, _, _, intent, event = _setup(tmp_path, monkeypatch)
    unrelated = LocalDatabase(tmp_path / "unrelated.sqlite")
    with unrelated.transaction(write=True) as foreign:
        assert foreign.in_transaction
        with pytest.raises(TrustProtocolError, match="original operational database"):
            PreworkRunCapture.capture(
                journal=journal, connection=foreign, run_id=event.fleet_run_id,
                intent=intent, repository_scope=event.repository_scope,
                targets=list(event.targets))
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_run_published_mid_bundle_rolls_back_even_signed_provisional_claims(tmp_path, monkeypatch):
    journal, contract, authority, directory, roots, intent, event = _setup(tmp_path, monkeypatch)
    _native_owner_stubs(monkeypatch, contract)
    original = suite.issue_with_deployment_signer
    count = 0
    with pytest.raises(TrustProtocolError):
        with journal._database.transaction(write=True) as connection:
            cut = _capture(journal, connection, intent, event)
            def inject_admission(**kwargs):
                nonlocal count
                claim = original(**kwargs)
                count += 1
                if count == 2:
                    journal.admit_run_in(connection, event.fleet_run_id,
                                         cut.intent, cut.scope, list(cut.targets))
                return claim
            monkeypatch.setattr(suite, "issue_with_deployment_signer", inject_admission)
            suite.issue_fixed_native_admission_evidence(
                journal=journal, contract=contract, path_authority=authority,
                key_directory=directory, trusted_public_roots=roots, prework=cut)
    assert count == 2
    # A simulated premature operational append happened inside the transaction,
    # but the prework issuer detected it and the transaction rolled back.
    assert journal.read_view().run_detail(event.fleet_run_id) is None


@pytest.mark.parametrize("native_field", [
    "lock_identity", "execution_environment_identity", "backend_capability_manifest_identity",
])
def test_real_native_lock_or_backend_owner_signs_uncommitted_candidate(tmp_path, monkeypatch, native_field):
    """Exercise unmocked uv/inventory/backend owners, not only fixed-map test doubles."""
    from oh_goon.core.models.run_admission import FleetRunAdmissionIntent
    from tests.unit.services.test_group_a_native_lock_issuer import fixture as lock_fixture
    from tests.unit.services.test_group_a_execution_environment_issuer import fixture as env_fixture
    from tests.unit.services.test_group_a_backend_capability_issuer import prepared as capability_fixture
    from tests.unit.services.test_group_a_verified_ingress_authorities import _test_private_key

    if native_field == "lock_identity":
        committed, contract, _ = lock_fixture(tmp_path, monkeypatch)
        module = lock_owner
        method = module.issue_observed_captured_run_lock_identity
    elif native_field == "execution_environment_identity":
        committed, contract, _, _ = env_fixture(tmp_path, monkeypatch)
        module = environment_owner
        method = module.issue_observed_captured_run_execution_environment_identity
    else:
        committed, contract, _, _ = capability_fixture(tmp_path, monkeypatch)
        module = capability_owner
        method = module.issue_observed_captured_run_backend_capability_manifest_identity

    event = committed.read_view().run_events(contract.fields["run_id"]).events[0]
    fresh = Journal(LocalDatabase(tmp_path / "uncommitted-m167.sqlite"))
    intent = FleetRunAdmissionIntent(
        source=event.source, target=event.target, capability=event.capability,
        execution=event.execution, repository_admissions=event.repository_admissions,
        application_launch=event.application_launch, scheduling=event.scheduling,
    )
    with fresh._database.transaction(write=True) as conn:
        cut = PreworkRunCapture.capture(
            journal=fresh, connection=conn, run_id=event.fleet_run_id,
            intent=intent, repository_scope=event.repository_scope, targets=list(event.targets))
        signed = method(journal=fresh, contract=contract,
                        issuer_id=f"owner-{native_field}",
                        signing_key=_test_private_key(native_field), prework=cut)
        assert signed.field == native_field
        assert fresh.read_view_from_connection(conn).run_detail(event.fleet_run_id) is None
        assert cut.read(journal=fresh, run_id=event.fleet_run_id)[0].fleet_run_id == event.fleet_run_id
    assert fresh.read_view().run_detail(event.fleet_run_id) is None
