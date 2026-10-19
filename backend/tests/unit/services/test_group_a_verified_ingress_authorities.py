"""M151: external issuer evidence must be verified before any operational write."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS,
    ISSUER_PROTOCOL,
    SIGNATURE_DOMAIN,
    IssuerEvidence,
    TrustedIssuerVerifier,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    admission_receipt,
    canonical_bytes,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator, authority
from tests.unit.services.test_group_a_enforcer_owner_bridge import _environment

FIELDS = tuple(sorted(AUTHORITY_FIELDS))


def roots():
    return {
        field: (
            f"{field}-authority-v2",
            _test_private_key(field)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ),
        )
        for field in FIELDS
    }


def _test_private_key(field):
    # Test-only fixture. Private keys NEVER go to the Enforcer verifier.
    seed = hashlib.sha256(f"external-test-owner:{field}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def proof(field, contract, roots_by_field=None):
    roots_by_field = roots() if roots_by_field is None else roots_by_field
    issuer, _public_key = roots_by_field[field]
    values = contract.fields
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": field,
            "run_id": values["run_id"],
            "contract_identity": contract.identity,
            "identity": values[field],
            "executor_generation": values["executor_generation"],
        }
    )
    signature = (
        _test_private_key(field)
        .sign(SIGNATURE_DOMAIN + field.encode("ascii") + b"\x00" + payload)
        .hex()
    )
    return IssuerEvidence(field, issuer, payload, signature)


def setup():
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    db.execute("CREATE TABLE operational_lease (run_id TEXT PRIMARY KEY)")
    env = _environment()
    fields = authority()
    fields.update(
        execution_environment_identity=env.execution_environment_identity,
        backend_capability_manifest_identity=env.capability_manifest_identity,
        executor_generation=env.executor_generation,
    )
    contract = AdmittedRunContract.freeze(fields)
    keys = roots()
    witnesses = tuple(proof(f, contract, keys) for f in FIELDS)
    return db, journal, env, contract, keys, witnesses


def publish(db, journal, env, contract, keys, witnesses, *, lease=None, override=None):
    auth = authenticator()
    args = dict(
        journal=journal,
        contract=contract,
        receipt=admission_receipt(auth, contract),
        authenticator=auth,
        configured=env,
        current=env,
        control_epoch=7,
        lease_identity="lease-exact",
        issuer_verifier=TrustedIssuerVerifier(keys),
        issuer_evidence=witnesses,
        lease_is_current=lease
        or (lambda run, epoch, identity: (run, epoch, identity) == ("run-one", 7, "lease-exact")),
        commit_lease_is_current=lambda c, run, epoch, identity: (
            c.in_transaction
            and (run, epoch, identity) == ("run-one", 7, "lease-exact")
            and c.execute("SELECT run_id FROM operational_lease WHERE run_id=?", (run,)).fetchone()
            == ("run-one",)
        ),
        operational_admission=lambda c: c.execute(
            "INSERT INTO operational_lease VALUES ('run-one')"
        ),
    )
    if override:
        args.update(override)
    return FleetAdmissionPlanner.publish_verified_ingress_admission(**args)


def assert_no_effect(db):
    assert db.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 0


def test_every_independently_signed_contract_identity_and_current_lease_required_then_atomic():
    db, journal, env, contract, keys, witnesses = setup()
    assert publish(db, journal, env, contract, keys, witnesses).rowcount == 1
    assert db.execute("SELECT run_id FROM operational_lease").fetchall() == [("run-one",)]
    assert journal.verify("run-one", auth=authenticator())[0]["kind"] == "RUN_ADMITTED"
    db.close()


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "duplicate",
        "extra",
        "modified-signature",
        "incorrect-issuer",
        "wrong-field",
        "wrong-contract",
        "wrong-run",
        "wrong-identity",
        "wrong-generation",
        "noncanonical",
        "invalid-payload",
        "unsigned",
        "stale-lease",
        "missing-lease-verifier",
        "bad-epoch",
        "different-execution-environment",
    ],
)
def test_malformed_missing_stale_or_forged_evidence_never_enters_operational_transaction(attack):
    db, journal, env, contract, keys, witnesses = setup()
    witnesses = list(witnesses)
    override = {}
    first = witnesses[0]
    if attack == "missing":
        witnesses.pop()
    elif attack == "duplicate":
        witnesses[-1] = witnesses[0]
    elif attack == "extra":
        witnesses.append(witnesses[0])
    elif attack == "modified-signature":
        witnesses[0] = replace(first, signature_hex="0" * 128)
    elif attack == "incorrect-issuer":
        witnesses[0] = replace(first, issuer_id="fake-root")
    elif attack == "wrong-field":
        witnesses[0] = replace(first, field="unknown-field")
    elif attack in {"wrong-contract", "wrong-run", "wrong-identity", "wrong-generation"}:
        raw = json.loads(first.payload)
        key = {
            "wrong-contract": "contract_identity",
            "wrong-run": "run_id",
            "wrong-identity": "identity",
            "wrong-generation": "executor_generation",
        }[attack]
        raw[key] = "sha256:wrong" if key.endswith("identity") else "other"
        witnesses[0] = replace(first, payload=canonical_bytes(raw))
    elif attack == "noncanonical":
        witnesses[0] = replace(first, payload=b'{ "a": 1 }')
    elif attack == "invalid-payload":
        witnesses[0] = replace(first, payload=b"{")
    elif attack == "unsigned":
        witnesses[0] = replace(first, payload=first.payload, signature_hex="")
    elif attack == "stale-lease":
        override["lease_is_current"] = lambda *_: False
    elif attack == "missing-lease-verifier":
        override["lease_is_current"] = None
    elif attack == "bad-epoch":
        override["control_epoch"] = 0
    else:
        override["current"] = _environment(executor_generation=env.executor_generation + 1)
    with pytest.raises((TrustProtocolError, FleetPlanInvalid)):
        publish(db, journal, env, contract, keys, tuple(witnesses), override=override)
    assert_no_effect(db)
    db.close()


@pytest.mark.parametrize(
    "attack", ["missing-root", "extra-root", "shared-id", "shared-key", "short-key"]
)
def test_untrusted_or_ambiguous_issuer_roots_fail_before_work(attack):
    keys = roots()
    if attack == "missing-root":
        keys.pop(FIELDS[0])
    elif attack == "extra-root":
        keys["other"] = ("other-issuer", b"v" * 32)
    elif attack == "shared-id":
        keys[FIELDS[0]] = (keys[FIELDS[1]][0], keys[FIELDS[0]][1])
    elif attack == "shared-key":
        keys[FIELDS[0]] = (keys[FIELDS[0]][0], keys[FIELDS[1]][1])
    else:
        keys[FIELDS[0]] = (keys[FIELDS[0]][0], b"tiny")
    with pytest.raises(TrustProtocolError):
        TrustedIssuerVerifier(keys)


def test_lease_authority_exception_does_not_fall_back_to_cached_or_default_truth():
    db, journal, env, contract, keys, witnesses = setup()

    def unavailable(*_):
        raise ConnectionError("lease issuer unavailable")

    with pytest.raises(ConnectionError, match="unavailable"):
        publish(db, journal, env, contract, keys, witnesses, lease=unavailable)
    assert_no_effect(db)
    db.close()
