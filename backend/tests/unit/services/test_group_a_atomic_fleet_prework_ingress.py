"""M168: one original Fleet admission transaction; zero physical effects."""

from __future__ import annotations

import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import FleetPlanInvalid, JournalConflict, OwnershipLost
from oh_goon.repository_tooling.enforcer_lace_signer_custody import (
    deployment_signer_policy_snapshot,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal, SignerTrustEpochAuthority
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_execution_lease import FakeLeaseEnvironment
from tests.unit.services.test_group_a_enforcer_owner_bridge import _environment
from tests.unit.services.test_group_a_precommit_native_issuer_migration import (
    _native_owner_stubs,
    _setup,
)


def prepared(tmp_path, monkeypatch):
    (tmp_path / "source").mkdir()
    source, contract, tools, directory, roots, intent, event = _setup(
        tmp_path / "source", monkeypatch
    )
    native_calls = _native_owner_stubs(monkeypatch, contract)
    # Opt-in schema MUST be created before LocalDatabase captures its authority.
    bootstrap = LocalDatabase(tmp_path / "one-authority.sqlite")
    external = sqlite3.connect(bootstrap.path, isolation_level=None)
    external.row_factory = sqlite3.Row
    causal = CausalRunJournal(external)
    causal.initialize_schema()
    signer = SignerTrustEpochAuthority(external)
    signer.initialize_schema()
    database = LocalDatabase(bootstrap.path)
    journal = Journal(database)
    lease_store = SQLiteExecutionLeaseStore(database, environment=FakeLeaseEnvironment())
    lease, _ = lease_store.acquire_fleet_with(
        event.fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _: None,
    )
    signer.enroll(
        policy_snapshot=deployment_signer_policy_snapshot(
            key_directory=directory, trusted_public_roots=roots
        ),
        roots=roots,
    )
    return (
        dict(
            journal=journal,
            causal_journal=causal,
            lease_store=lease_store,
            lease=lease,
            signer_authority=signer,
            contract=contract,
            authenticator=authenticator(),
            configured=_environment(),
            current=_environment(),
            intent=intent,
            repository_scope=event.repository_scope,
            targets=list(event.targets),
            path_authority=tools,
            key_directory=directory,
            trusted_public_roots=roots,
        ),
        external,
        native_calls,
    )


def publish(args):
    return FleetAdmissionPlanner.publish_atomic_fleet_prework_ingress(
        operational_journal=args["journal"], **{k: v for k, v in args.items() if k != "journal"}
    )


def facts(args, conn):
    run = args["contract"].fields["run_id"]
    return (
        args["journal"].read_view().run_detail(run),
        conn.execute("SELECT count(*) FROM lace_run_authority").fetchone()[0],
        conn.execute("SELECT count(*) FROM lace_run_events").fetchone()[0],
    )


def test_all_six_signed_before_one_atomic_operational_and_causal_run(tmp_path, monkeypatch):
    args, conn, calls = prepared(tmp_path, monkeypatch)
    try:
        assert facts(args, conn) == (None, 0, 0)
        result = publish(args)
        assert result is not None
        assert facts(args, conn)[1:] == (1, 2)
        assert set(calls) == set(args["trusted_public_roots"])
        assert (
            args["journal"].read_view().run_events("durable-run").events[0].event_type.value
            == "RUN_ADMITTED"
        )
        # B8 added exactly one authenticated signer generation after admission.
        # Two causal rows are intentional, not a flaky duplicate RUN_ADMITTED.
        kinds = [
            e["kind"]
            for e in CausalRunJournal(conn).verify("durable-run", auth=args["authenticator"])
        ]
        assert kinds == ["RUN_ADMITTED", "RUN_SIGNER_EPOCH_BOUND"]
        with pytest.raises(JournalConflict, match="already queued"):
            publish(args)
        assert facts(args, conn)[1:] == (1, 2)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-run",
        "wrong-db",
        "wrong-signer-connection",
        "revoked",
        "lease-cancelled",
        "missing-issuer",
        "changed-policy",
        "wrong-environment",
    ],
)
def test_inadmissible_prework_never_publishes_any_run(tmp_path, monkeypatch, attack):
    args, conn, calls = prepared(tmp_path, monkeypatch)
    if attack == "wrong-run":
        args["contract"] = args["contract"].__class__.freeze(
            {**args["contract"].fields, "run_id": "other-run"}
        )
    elif attack == "wrong-db":
        args["causal_journal"] = CausalRunJournal(sqlite3.connect(":memory:", isolation_level=None))
    elif attack == "wrong-signer-connection":
        args["signer_authority"] = SignerTrustEpochAuthority(
            sqlite3.connect(":memory:", isolation_level=None)
        )
    elif attack == "revoked":
        owner = args["signer_authority"]
        owner.revoke(expected=owner.active())
    elif attack == "lease-cancelled":
        conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    elif attack == "missing-issuer":
        from oh_goon.repository_tooling import enforcer_lace_native_tool_issuer as tool_owner

        monkeypatch.setattr(
            tool_owner, "issue_observed_captured_run_native_tool_identity", lambda **kw: None
        )
    elif attack == "changed-policy":
        (args["key_directory"] / "signers.json").unlink()
    elif attack == "wrong-environment":
        args["current"] = _environment(executor_generation=99)
    try:
        with pytest.raises(
            (TrustProtocolError, FleetPlanInvalid, OwnershipLost, OSError, TypeError, ValueError)
        ):
            publish(args)
        assert facts(args, conn) == (None, 0, 0)
    finally:
        conn.close()


def test_commit_epoch_and_lease_fences_rollback_all_provisional_run_facts(tmp_path, monkeypatch):
    from oh_goon.repository_tooling import enforcer_lace_native_issuer_suite as suite

    for attack in ("lease", "revoked", "birth"):
        base = tmp_path / attack
        base.mkdir()
        args, conn, _ = prepared(base, monkeypatch)
        original = suite.issue_fixed_prework_native_evidence_with_signer_grant

        def changed(*, original=original, attack=attack, conn=conn, args=args, **kwargs):
            result = original(**kwargs)
            if attack == "lease":
                conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
            elif attack == "revoked":
                conn.execute(
                    "UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1,state='REVOKED'"
                )
            else:
                args["lease_store"]._environment.identities[
                    args["lease_store"]._environment.pid
                ] = "reused"
            return result

        with monkeypatch.context() as scope:
            scope.setattr(suite, "issue_fixed_prework_native_evidence_with_signer_grant", changed)
            with pytest.raises((TrustProtocolError, OwnershipLost)):
                publish(args)
        assert facts(args, conn) == (None, 0, 0)
        if attack == "revoked":
            assert args["signer_authority"].active().epoch == 1
        if attack == "lease":
            assert tuple(
                conn.execute("SELECT cancel_requested_at FROM execution_lease").fetchone()
            ) == (None,)
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "lease-cancel-after-causal",
        "signer-revoke-after-causal",
        "causal-append-fails",
        "owner-birth-after-causal",
    ],
)
def test_after_both_provisional_admissions_final_fence_rolls_everything_back(
    tmp_path, monkeypatch, attack
):
    args, conn, _ = prepared(tmp_path, monkeypatch)
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as RuntimeCausalRunJournal

    original = RuntimeCausalRunJournal._append_locked

    def after_causal_append(self, run_id, kind, payload):
        result = original(self, run_id, kind, payload)
        if attack == "causal-append-fails":
            raise RuntimeError("causal journal unavailable")
        if attack == "lease-cancel-after-causal":
            conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
        elif attack == "signer-revoke-after-causal":
            conn.execute(
                "UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1,state='REVOKED'"
            )
        else:
            env = args["lease_store"]._environment
            env.identities[env.pid] = "reused-owner-birth"
        return result

    with monkeypatch.context() as scope:
        scope.setattr(RuntimeCausalRunJournal, "_append_locked", after_causal_append)
        with pytest.raises((RuntimeError, OwnershipLost, TrustProtocolError)):
            publish(args)
    assert facts(args, conn) == (None, 0, 0)
    if attack == "lease-cancel-after-causal":
        assert conn.execute("SELECT cancel_requested_at FROM execution_lease").fetchone()[0] is None
    if attack == "signer-revoke-after-causal":
        assert args["signer_authority"].active().epoch == 1
    conn.close()


def test_operational_row_factory_shared_by_signer_epoch_fence(tmp_path, monkeypatch):
    args, conn, _ = prepared(tmp_path, monkeypatch)
    assert conn.row_factory is sqlite3.Row
    # M165 incorrectly compared the returned sqlite3.Row to a tuple. Verify
    # the original operational row format is accepted under a real admission.
    publish(args)
    assert facts(args, conn)[1:] == (1, 2)
    conn.close()
