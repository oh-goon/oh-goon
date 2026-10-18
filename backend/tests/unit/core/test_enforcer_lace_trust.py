"""Phase 3 receipt canonicalization/authentication negative contract ring."""

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import (
    CONTRACT_KEYS,
    PROTOCOL_VERSION,
    AdmittedRunContract,
    ReceiptAuthenticator,
    TrustProtocolError,
    admission_receipt,
    canonical_bytes,
    rejected_receipt,
)


def authority(run_id="run-one"):
    a = {name: "authority:" + name for name in CONTRACT_KEYS}
    a.update(
        {
            "run_id": run_id,
            "executor_generation": 8,
            "execution_environment_identity": "sha256:" + "a" * 64,
            "backend_capability_manifest_identity": "sha256:" + "b" * 64,
            "run_authority": {"network": ["none"]},
            "step_envelopes": {"step-1": {"network": "none"}},
            "step_teardown_envelopes": {"step-1": {"network": "none"}},
            "run_teardown_envelope": {"network": "none"},
        }
    )
    return a


def authenticator(key_id="key-1"):
    return ReceiptAuthenticator(key_id=key_id, secret=b"control-plane-key-material-32-bytes-plus")


def test_contract_canonical_identity_independent_of_order_and_caller_mutation():
    a = authority()
    c1 = AdmittedRunContract.freeze(a)
    c2 = AdmittedRunContract.freeze(dict(reversed(list(a.items()))))
    assert c1 == c2
    a["step_envelopes"]["step-1"]["network"] = "unrestricted"
    assert c1.fields["step_envelopes"]["step-1"]["network"] == "none"
    assert c1.identity.startswith("sha256:")
    assert c1.encoded == canonical_bytes(c1.fields)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.pop("run_id"),
        lambda a: a.update(shadow="unowned"),
        lambda a: a.update(executor_generation=True),
        lambda a: a.update(execution_environment_identity="sha256:" + "g" * 64),
        lambda a: a.update(backend_capability_manifest_identity="unknown"),
        lambda a: a.update(run_authority={}),
        lambda a: a.update(step_envelopes={"step-1": {"cpu": 1.5}}),
    ],
)
def test_missing_unknown_unbound_or_uncanonical_contract_rejected(mutate):
    a = authority()
    mutate(a)
    with pytest.raises(TrustProtocolError):
        AdmittedRunContract.freeze(a)


def test_authenticated_admission_and_rejection_are_distinct_domains():
    auth = authenticator()
    contract = AdmittedRunContract.freeze(authority())
    signed = admission_receipt(auth, contract)
    assert auth.verify(signed, domain="admission") == {
        "decision": "ADMITTED",
        "run_id": "run-one",
        "contract_identity": contract.identity,
        "protocol": PROTOCOL_VERSION,
    }
    rejected = rejected_receipt(auth, run_id="run-one", reason="no capabilities")
    assert auth.verify(rejected, domain="admission")["decision"] == "REJECTED"
    with pytest.raises(TrustProtocolError):
        auth.verify(signed, domain="terminal")


@pytest.mark.parametrize("case", ["payload", "mac", "key", "domain", "noncanonical", "cross-key"])
def test_modified_replayed_wrong_domain_key_and_noncannonical_receipts_rejected(case):
    auth = authenticator()
    receipt = admission_receipt(auth, AdmittedRunContract.freeze(authority()))
    if case == "payload":
        receipt = replace(receipt, payload=receipt.payload.replace(b"run-one", b"run-two"))
    elif case == "mac":
        receipt = replace(receipt, mac_hex="0" * 64)
    elif case == "key":
        receipt = replace(receipt, key_id="other")
    elif case == "domain":
        receipt = replace(receipt, domain="terminal")
    elif case == "noncanonical":
        receipt = replace(receipt, payload=b'{ "decision": "ADMITTED" }')
    with pytest.raises(TrustProtocolError):
        (authenticator("other-key") if case == "cross-key" else auth).verify(
            receipt, domain="admission"
        )


def test_no_fallback_to_missing_or_too_short_secret():
    for value in [b"", b"short"]:
        with pytest.raises(TrustProtocolError):
            ReceiptAuthenticator(key_id="key", secret=value)


def test_direct_dataclass_forgery_cannot_be_published():
    c = AdmittedRunContract.freeze(authority())
    with pytest.raises(TrustProtocolError):
        replace(c, identity="sha256:" + "f" * 64).verify()
    with pytest.raises(TrustProtocolError):
        replace(c, encoded=b"{}").verify()


def test_step_output_receipt_binds_type_lineage_run_and_contract():
    from oh_goon.core.enforcer_lace_trust import step_output_receipt

    auth = authenticator()
    c = AdmittedRunContract.freeze(authority())
    receipt = step_output_receipt(
        auth,
        run_id="run-one",
        step_id="step-one",
        contract_identity=c.identity,
        output_identity="sha256:" + "3" * 64,
        semantic_type="executable",
        lineage_identity="lineage-one",
    )
    assert auth.verify(receipt, domain="step-output")["semantic_type"] == "executable"
    assert auth.verify(receipt, domain="step-output")["lineage_identity"] == "lineage-one"
    with pytest.raises(TrustProtocolError):
        auth.verify(receipt, domain="admission")
    with pytest.raises(TrustProtocolError):
        step_output_receipt(
            auth,
            run_id="run-one",
            step_id="step-one",
            contract_identity=c.identity,
            output_identity="",
            semantic_type="data",
            lineage_identity="lineage-one",
        )
