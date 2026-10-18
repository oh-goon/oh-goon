"""B12 negative tests for authenticated, durably fenced *pre-physical* intent."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError, admission_receipt
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authority, authenticator


def _digest(c: str) -> str:
    return "sha256:" + c * 64


def _resources() -> dict[str, str]:
    return {name: _digest(c) for name, c in zip(
        ("workspace", "runtime", "network", "cgroup", "mounts"), "12345", strict=True
    )}


def _store(path: Path, *, missing_teardown: bool = False) -> tuple[sqlite3.Connection, CausalRunJournal]:
    conn = sqlite3.connect(path, isolation_level=None)
    journal = CausalRunJournal(conn)
    journal.initialize_schema()
    fields = authority()
    if missing_teardown:
        fields["step_teardown_envelopes"] = {"another-step": {"network": "none"}}
    contract = AdmittedRunContract.freeze(fields)
    signer = authenticator()
    journal.publish_admission(
        contract, admission_receipt(signer, contract), signer,
        control_epoch=1, lease_identity="lease-one",
    )
    return conn, journal


def _reserve(journal: CausalRunJournal, **kwargs: object) -> int:
    inputs: dict[str, object] = {
        "epoch": 1, "lease_identity": "lease-one", "auth": authenticator(),
        "step_id": "step-1", "attempt_identity": _digest("d"),
        "execution_environment_identity": _digest("a"),
        "backend_capability_manifest_identity": _digest("b"),
        "resources": _resources(),
    }
    inputs.update(kwargs)
    return journal.publish_local_resource_intent("run-one", **inputs)


def test_b12_resource_intent_exact_authenticated_sqlite_restart(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    conn, journal = _store(path)
    assert _reserve(journal) == 2
    assert _reserve(journal, attempt_identity=_digest("e")) == 3
    events = journal.verify("run-one", auth=authenticator())
    payload = events[1]["payload"]
    assert payload["backend"] == "bubblewrap"
    assert payload["contract_identity"].startswith("sha256:")
    assert payload["resources"] == _resources()
    conn.close()
    reopened = sqlite3.connect(path, isolation_level=None)
    assert CausalRunJournal(reopened).verify("run-one", auth=authenticator()) == events
    reopened.close()


@pytest.mark.parametrize("variant", [
    "unknown-step", "unknown-teardown-step", "wrong-environment", "wrong-backend",
    "wrong-lease", "wrong-epoch", "bad-attempt", "missing-resource", "extra-resource",
    "noncanonical-resource", "wrong-signing-root", "duplicate", "generic-append",
    "generic-created", "inactive", "tampered-admission", "crash-before-commit",
])
def test_b12_rejects_unbound_uncommitted_or_replayed_intents(tmp_path: Path, variant: str) -> None:
    conn, journal = _store(tmp_path / "journal.db", missing_teardown=variant == "unknown-teardown-step")
    kwargs: dict[str, object] = {}
    if variant == "unknown-step":
        kwargs["step_id"] = "another-step"
    elif variant == "wrong-environment":
        kwargs["execution_environment_identity"] = _digest("9")
    elif variant == "wrong-backend":
        kwargs["backend_capability_manifest_identity"] = _digest("9")
    elif variant == "wrong-lease":
        kwargs["lease_identity"] = "old-lease"
    elif variant == "wrong-epoch":
        kwargs["epoch"] = 2
    elif variant == "bad-attempt":
        kwargs["attempt_identity"] = "derived-from-path"
    elif variant == "missing-resource":
        res = _resources()
        del res["network"]
        kwargs["resources"] = res
    elif variant == "extra-resource":
        kwargs["resources"] = {**_resources(), "unknown": _digest("6")}
    elif variant == "noncanonical-resource":
        kwargs["resources"] = {**_resources(), "network": "sha256:BAD"}
    elif variant == "wrong-signing-root":
        from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
        kwargs["auth"] = ReceiptAuthenticator(key_id="foreign-key", secret=b"f" * 32)
    elif variant == "duplicate":
        _reserve(journal)
    elif variant == "inactive":
        from oh_goon.core.enforcer_lace_trust import PROTOCOL_VERSION
        row = journal._row("run-one")
        auth = authenticator()
        receipt = auth.issue("unresolved", {
            "protocol": PROTOCOL_VERSION, "run_id": "run-one", "contract_identity": row[0],
            "control_epoch": row[1], "last_run_seq": row[3],
            "head_identity": row[4], "state": "JOURNAL_UNRESOLVED",
        })
        journal.mark_unresolved("run-one", auth=auth, receipt=receipt)
    elif variant == "tampered-admission":
        conn.execute("UPDATE lace_run_authority SET admission_mac=?", ("0" * 64,))
    elif variant == "crash-before-commit":
        def crash() -> None:
            raise RuntimeError("power-loss simulation before commit")
        kwargs["before_commit"] = crash
    earlier = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
    with pytest.raises((CausalJournalError, TrustProtocolError, RuntimeError)):
        if variant in {"generic-append", "generic-created"}:
            journal.append(
                "run-one", epoch=1, lease_identity="lease-one",
                kind="RESOURCE_INTENT" if variant == "generic-append" else "RESOURCE_CREATED",
                payload={"arbitrary": "unbound"},
            )
        else:
            _reserve(journal, **kwargs)
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == earlier
    conn.close()


def test_b12_rejects_history_mutation_before_reservation(tmp_path: Path) -> None:
    conn, journal = _store(tmp_path / "journal.db")
    conn.execute("UPDATE lace_run_events SET event_payload=? WHERE run_seq=1", (json.dumps({"forged": True}).encode(),))
    with pytest.raises(CausalJournalError):
        _reserve(journal)
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
    conn.close()
