"""B12D1: the original authenticated intent must own a fenced pre-launch cut."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.ports.local_lace_allocation import LocalLaceAllocationObservation
from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _digest, _reserve, _resources, _store


def observation() -> LocalLaceAllocationObservation:
    return LocalLaceAllocationObservation(
        digest("oh-goon/b12d/resource-binding/v1", canonical_bytes(_resources())),
        "/sys/fs/cgroup/delegated/attempt-0123456789abcdef",
        _digest("5"),
        "host-netns",
        "uvf0123456789",
        "ufh0123456789",
        "uvf_0123456789",
        _digest("6"),
    )


def publish(
    journal: CausalRunJournal,
    *,
    intent_seq: int = 2,
    epoch: int = 1,
    lease_identity: str = "lease-one",
    step_id: str = "step-1",
    attempt_identity: str = _digest("d"),
    receipt: LocalLaceAllocationObservation | None = None,
) -> int:
    return journal._transaction(
        lambda: journal.publish_local_allocation_observation_in(
            "run-one",
            epoch=epoch,
            lease_identity=lease_identity,
            auth=authenticator(),
            step_id=step_id,
            attempt_identity=attempt_identity,
            intent_run_seq=intent_seq,
            observation=asdict(receipt or observation()),
        )
    )


def test_observation_commits_exact_original_intent_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "authority.db"
    conn, journal = _store(path)
    assert _reserve(journal) == 2
    assert publish(journal) == 3
    events = journal.verify("run-one", auth=authenticator())
    assert [e["kind"] for e in events] == [
        "RUN_ADMITTED",
        "RESOURCE_INTENT",
        "RESOURCE_ALLOCATION_OBSERVED",
    ]
    assert events[-1]["payload"]["stage"] == "PRE_LAUNCH_ONLY"
    assert not events[-1]["payload"]["process_tree_proven"]
    assert not events[-1]["payload"]["bubblewrap_mounts_proven"]
    assert not events[-1]["payload"]["teardown_proven"]
    conn.close()
    reopened = sqlite3.connect(path, isolation_level=None)
    assert CausalRunJournal(reopened).verify("run-one", auth=authenticator()) == events
    reopened.close()


@pytest.mark.parametrize(
    "variant",
    [
        "unknown",
        "foreign-step",
        "wrong-attempt",
        "stale-epoch",
        "stale-lease",
        "duplicate",
        "tampered-history",
        "bad-digest",
        "invented-network",
        "resource-drift",
        "wrong-intent-sequence",
        "lost-control",
    ],
)
def test_observation_rejects_all_unbound_or_stale_authority_without_append(
    tmp_path: Path, variant: str
) -> None:
    conn, journal = _store(tmp_path / "authority.db")
    _reserve(journal)
    kwargs: dict[str, object] = {}
    if variant == "unknown":
        kwargs["intent_seq"] = 88
    elif variant == "wrong-intent-sequence":
        kwargs["intent_seq"] = 1
    elif variant == "foreign-step":
        kwargs["step_id"] = "foreign"
    elif variant == "wrong-attempt":
        kwargs["attempt_identity"] = _digest("e")
    elif variant == "stale-epoch":
        kwargs["epoch"] = 2
    elif variant == "stale-lease":
        kwargs["lease_identity"] = "other"
    elif variant == "duplicate":
        publish(journal)
    elif variant == "tampered-history":
        conn.execute("UPDATE lace_run_events SET event_payload=? WHERE run_seq=2", (b"{}",))
    elif variant == "bad-digest":
        receipt = observation()
        kwargs["receipt"] = LocalLaceAllocationObservation(
            receipt.resource_intent_identity,
            receipt.cgroup_path,
            "fake",
            receipt.network_kind,
            receipt.network_namespace,
            receipt.host_veth,
            receipt.nft_table,
            receipt.network_identity,
        )
    elif variant == "invented-network":
        receipt = observation()
        kwargs["receipt"] = LocalLaceAllocationObservation(
            receipt.resource_intent_identity,
            receipt.cgroup_path,
            receipt.cgroup_identity,
            "not-allocated",
            receipt.network_namespace,
            receipt.host_veth,
            receipt.nft_table,
            receipt.network_identity,
        )
    elif variant == "resource-drift":
        receipt = observation()
        kwargs["receipt"] = LocalLaceAllocationObservation(
            _digest("9"),
            receipt.cgroup_path,
            receipt.cgroup_identity,
            receipt.network_kind,
            receipt.network_namespace,
            receipt.host_veth,
            receipt.nft_table,
            receipt.network_identity,
        )
    elif variant == "lost-control":
        journal.transfer_control(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            next_epoch=2,
            next_lease_identity="lease-two",
        )
    count = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
    with pytest.raises(CausalJournalError):
        publish(journal, **kwargs)
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == count
    conn.close()


def test_failed_observation_rolls_back_and_keeps_intent_recoverable(tmp_path: Path) -> None:
    conn, journal = _store(tmp_path / "authority.db")
    _reserve(journal)
    with pytest.raises(RuntimeError, match="pre-commit-crash"):

        def crash() -> None:
            journal.publish_local_allocation_observation_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=authenticator(),
                step_id="step-1",
                attempt_identity=_digest("d"),
                intent_run_seq=2,
                observation=asdict(observation()),
            )
            raise RuntimeError("pre-commit-crash")

        journal._transaction(crash)
    assert [e["kind"] for e in journal.verify("run-one", auth=authenticator())] == [
        "RUN_ADMITTED",
        "RESOURCE_INTENT",
    ]
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "cancellation",
        "foreign-lease",
        "control-takeover",
        "finished",
        "postappend-revocation",
    ],
)
def test_original_fleet_second_cut_rechecks_control_before_native_launch(
    tmp_path: Path,
    monkeypatch,
    attack: str,
) -> None:
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError
    from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _original_reservation

    backend, _, request, conn, journal, session, permit, checks = _original_reservation(
        tmp_path,
        monkeypatch,
    )
    try:
        proof = permit.reserve(request, backend)
        assert session.commit_count == 1
        if attack == "cancellation":
            session.cancelled = True
        elif attack == "foreign-lease":
            session.token = "foreign-lease"
        elif attack == "control-takeover":
            journal.transfer_control(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                next_epoch=2,
                next_lease_identity="new-owner",
            )
        elif attack == "finished":
            session.finished = True
        elif attack == "postappend-revocation":

            def revoke_after_append(_connection):
                checks.append("guard")
                if len(checks) >= 4:
                    raise TrustProtocolError("revoked before observation commit")

            permit = replace(permit, validate_authority_in=revoke_after_append)
        prior_count = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
        with pytest.raises((CausalJournalError, TrustProtocolError)):
            permit.observe(
                proof,
                replace(
                    observation(),
                    resource_intent_identity=digest(
                        "oh-goon/b12d/resource-binding/v1",
                        canonical_bytes(local_resource_identities(request)),
                    ),
                ),
            )
        assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == prior_count
        assert session.commit_count == 1
    finally:
        conn.close()


def test_original_fleet_second_cut_is_exactly_once(tmp_path: Path, monkeypatch) -> None:
    from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _original_reservation

    backend, _, request, conn, journal, session, permit, checks = _original_reservation(
        tmp_path,
        monkeypatch,
    )
    try:
        proof = permit.reserve(request, backend)
        receipt = replace(
            observation(),
            resource_intent_identity=digest(
                "oh-goon/b12d/resource-binding/v1",
                canonical_bytes(local_resource_identities(request)),
            ),
        )
        permit.observe(proof, receipt)
        assert session.commit_count == 2
        assert len(checks) == 4
        with pytest.raises(CausalJournalError, match="duplicate"):
            permit.observe(
                proof,
                replace(
                    observation(),
                    resource_intent_identity=digest(
                        "oh-goon/b12d/resource-binding/v1",
                        canonical_bytes(local_resource_identities(request)),
                    ),
                ),
            )
        assert session.commit_count == 2
    finally:
        conn.close()
