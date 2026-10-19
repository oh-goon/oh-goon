"""AG-02B2B2: one original Fleet session -> operational Step fact -> signed prework cut."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError, digest
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.run_events import StepAttemptCreatedEvent
from oh_goon.core.product_identity import fleet_step_attempt_id, repository_attempt_operation_id
from oh_goon.core.vocabulary import FleetRunEventType
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.execution import ExecutionSession
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture


class OwnerTransaction:
    def __init__(self, conn):
        self.conn = conn

    def mutate_as_owner(self, lease, callback):
        assert lease.token == "lease-one" and lease.fleet_run_id == "run-one"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = callback(self.conn)
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return result

    def cancellation_requested_in(self, conn, lease):
        row = conn.execute(
            "SELECT cancel_requested_at FROM execution_lease WHERE lease_token=?", (lease.token,)
        ).fetchone()
        if row is None:
            raise CausalJournalError("owner lease disappeared")
        return row[0] is not None


def make_case(tmp_path, monkeypatch):
    conn, journal, args, packet = fixture(tmp_path)
    run_id, repo_id = "run-one", "repo-one"
    operation_id = repository_attempt_operation_id(run_id, repo_id)
    step_id = args["step_id"]
    attempt_number = 1
    attempt_id = fleet_step_attempt_id(operation_id, step_id, attempt_number)
    event = StepAttemptCreatedEvent(
        event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
        sequence=1,
        fleet_run_id=run_id,
        repository_id=repo_id,
        operation_id=operation_id,
        step_id=step_id,
        step_order=1,
        attempt_number=attempt_number,
        step_attempt_id=attempt_id,
    )
    fake_events = [event]
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._require_authenticated_fleet = True
    lifecycle._fleet_provisioner = object.__new__(FleetPreworkProvisioner)
    binding = object.__new__(StrictFleetPolicyEnrollment)
    object.__setattr__(binding, "authenticator", args["auth"])
    object.__setattr__(binding, "configured", SimpleNamespace(require_unchanged=lambda _: None))
    object.__setattr__(binding, "current", object())
    monkeypatch.setattr(FleetPreworkProvisioner, "resolve", lambda self, _: binding)
    lifecycle._journal = SimpleNamespace(
        read_view_from_connection=lambda connection: SimpleNamespace(
            run_events=lambda _: SimpleNamespace(events=tuple(fake_events))
        )
    )
    lifecycle.require_execution_authority_in = lambda connection, rid, expected: None
    expected = SimpleNamespace(
        execution=StrictFleetExecutionAdmission.model_construct(
            placement=SimpleNamespace(
                isolation_backend=SimpleNamespace(backend_id="bubblewrap"),
                executor_capacity=SimpleNamespace(executor_generation=8),
            )
        )
    )
    session = ExecutionSession(
        OwnerTransaction(conn), ExecutionLease(token="lease-one", kind="FLEET", fleet_run_id=run_id)
    )
    invocation = dict(
        repository_id=repo_id,
        operation_id=operation_id,
        step_id=step_id,
        attempt_number=attempt_number,
        packet=packet,
        expected_handoff_identity=args["expected_handoff_identity"],
    )
    return conn, journal, lifecycle, session, expected, invocation, fake_events, attempt_id, args


def test_original_session_binds_operational_attempt_and_signed_prework_once(tmp_path, monkeypatch):
    conn, journal, lifecycle, session, expected, kw, events, attempt_id, args = make_case(
        tmp_path, monkeypatch
    )
    assert lifecycle.reserve_original_agent_source_for_attempt(session, expected, **kw) == 3
    binding = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    assert binding["attempt_identity"] == digest(
        "oh-goon/original-fleet-agent-attempt/v1", attempt_id.encode()
    )
    assert binding["effect_permission"] == "NONE"
    assert binding["authority"] == "PREWORK_NOT_ADMITTED"
    with pytest.raises(CausalJournalError, match="replay"):
        lifecycle.reserve_original_agent_source_for_attempt(session, expected, **kw)
    assert len(journal.verify("run-one", auth=args["auth"])) == 3
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "missing-attempt",
        "duplicate-attempt",
        "foreign-repository",
        "foreign-step",
        "wrong-number",
        "wrong-operation",
        "terminal",
        "cancelled",
        "stale-session",
        "stale-authority",
        "changed-packet",
        "wrong-signer",
    ],
)
def test_reject_bad_attempt_before_original_journal_commit(tmp_path, monkeypatch, attack):
    conn, journal, lifecycle, session, expected, kw, events, attempt_id, args = make_case(
        tmp_path, monkeypatch
    )
    if attack == "missing-attempt":
        events.clear()
    if attack == "duplicate-attempt":
        events.append(events[0])
    if attack == "foreign-repository":
        kw["repository_id"] = "wrong-repo"
    if attack == "foreign-step":
        kw["step_id"] = "foreign-step"
    if attack == "wrong-number":
        kw["attempt_number"] = 2
    if attack == "wrong-operation":
        kw["operation_id"] = "foreign-op"
    if attack == "terminal":
        events.append(
            SimpleNamespace(
                event_type=FleetRunEventType.STEP_FAILED, step_id=kw["step_id"], sequence=2
            )
        )
    if attack == "cancelled":
        conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    if attack == "stale-session":
        session._lease = ExecutionLease(token="foreign-lease", kind="FLEET", fleet_run_id="run-one")
    if attack == "stale-authority":
        lifecycle.require_execution_authority_in = lambda *args: (_ for _ in ()).throw(
            CausalJournalError("stale authority")
        )
    if attack == "changed-packet":
        kw["packet"] = kw["packet"] + b"bad"
    if attack == "wrong-signer":
        conn.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
    before = tuple(conn.execute("SELECT event_payload FROM lace_run_events ORDER BY run_seq"))
    if attack == "stale-session":
        expected_error = AssertionError
    elif attack in {"foreign-repository", "wrong-operation"}:
        expected_error = ValueError
    else:
        expected_error = (TrustProtocolError, FleetPlanInvalid)
    with pytest.raises(expected_error):
        lifecycle.reserve_original_agent_source_for_attempt(session, expected, **kw)
    assert (
        tuple(conn.execute("SELECT event_payload FROM lace_run_events ORDER BY run_seq")) == before
    )
    conn.close()


def test_in_transaction_exception_rolls_back_original_signed_event(tmp_path, monkeypatch):
    conn, journal, lifecycle, session, expected, kw, events, attempt_id, args = make_case(
        tmp_path, monkeypatch
    )
    counter = [0]

    def fence(*_):
        counter[0] += 1
        if counter[0] == 2:
            raise CausalJournalError("authority changed after reservation")

    lifecycle.require_execution_authority_in = fence
    with pytest.raises(CausalJournalError, match="authority changed"):
        lifecycle.reserve_original_agent_source_for_attempt(session, expected, **kw)
    assert len(journal.verify("run-one", auth=args["auth"])) == 2
    conn.close()


def test_x0_original_lifecycle_reuses_one_prework_decoder_and_one_signed_publisher():
    """Guard a single Fleet session mutation and exact existing journal receiver."""
    import ast

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    owner = ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py"
    tree = ast.parse(owner.read_text())
    lifecycle = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FleetRunLifecycle"
    )
    method = [
        node
        for node in lifecycle.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "reserve_original_agent_source_for_attempt"
    ]
    assert len(method) == 1
    calls = [ast.unparse(node.func) for node in ast.walk(method[0]) if isinstance(node, ast.Call)]
    assert calls.count("session.mutate") == 1
    assert calls.count("reserve_verified_agent_source_in") == 1
    assert calls.count("self._journal.read_view_from_connection") == 1
    assert not any(".execute(" in call and "conn" not in call for call in calls)
    assert not any(
        any(
            bad in call
            for bad in ("subprocess", "os.exec", "os.spawn", "sqlite3.connect", "httpx.", "socket.")
        )
        for call in calls
    )
