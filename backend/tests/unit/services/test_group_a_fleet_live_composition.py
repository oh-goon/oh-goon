"""M170: actual FleetRunLifecycle must not bypass required Enforcer ingress."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import StateConflict
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    PlanRepositoryAdmissionEvidence,
)
from oh_goon.core.vocabulary import OperationAction
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_reservation_transaction_bridge import _bridge


def _real_path(tmp_path, monkeypatch, *, enabled=True, binding=True):
    args, params, connection, calls = _bridge(tmp_path, monkeypatch)
    # This live reservation path is DIRECT Fleet. The retained M169 fixture
    # deliberately covers CICD, whose own placement cutover is not ready.
    initial = args["intent"]
    original = initial.target.plan
    plan = ResolvedFleetPlan.create(
        name=original.name,
        description=original.description,
        steps=original.steps,
        execution=MaintenanceExecutionPolicy(),
        requirements=original.requirements,
        imports=original.imports,
        step_sources=original.step_sources,
    )
    target = initial.target.model_copy(
        update={
            "plan": plan,
            "identity": ExecutionIdentity(digest=build_execution_lock(plan).digest),
        }
    )
    admissions = tuple(
        PlanRepositoryAdmissionEvidence.create(
            repository_id=item.repository_id,
            plan_id=plan.plan_id,
            semantic_preview_id=item.semantic_preview_id,
            import_applicability=item.import_applicability,
            active_step_ids=item.active_step_ids,
            source_authority=item.source_authority,
        )
        for item in initial.repository_admissions
    )
    args["intent"] = initial.model_copy(
        update={
            "target": target,
            "execution": DirectFleetExecutionAdmission(),
            "repository_admissions": admissions,
        }
    )
    params["intent"] = args["intent"]
    from oh_goon.services import fleet_run_lifecycle

    monkeypatch.setattr(
        fleet_run_lifecycle,
        "current_application_launch_receipt",
        lambda: args["intent"].application_launch,
    )
    authority = FleetPreworkComposition(
        contract=params["contract"],
        authenticator=params["authenticator"],
        configured=params["configured"],
        current=params["current"],
        intent=args["intent"],
        repository_scope=params["repository_scope"],
        targets=tuple(params["targets"]),
        path_authority=params["path_authority"],
        key_directory=params["key_directory"],
        trusted_public_roots=params["trusted_public_roots"],
    )
    lifecycle = FleetRunLifecycle(
        args["journal"]._database,
        args["journal"],
        params["execution"],
        object(),
        require_authenticated_fleet=enabled,
        fleet_prework=authority if binding else None,
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)

    def admit(**changes):
        kwargs = dict(
            fleet_run_id=params["contract"].fields["run_id"],
            intent=intent,
            repository_scope=params["repository_scope"],
            targets=params["targets"],
            expected_durable_revision=params["expected_durable_revision"],
            repository_admissions=args["intent"].repository_admissions,
            source=args["intent"].source,
        )
        kwargs.update(changes)
        return lifecycle.admit(**kwargs)

    return args, params, connection, calls, lifecycle, authority, admit


def test_lifecycle_routes_real_direct_fleet_through_single_atomic_reservation(
    tmp_path, monkeypatch
):
    args, params, conn, calls, _, _, admit = _real_path(tmp_path, monkeypatch)
    try:
        ticket, state = admit()
        assert ticket._lease.fleet_run_id == params["contract"].fields["run_id"]
        assert state is not None
        assert facts(args, conn)[1:] == (1, 2)
        assert set(calls) == set(params["trusted_public_roots"])
    finally:
        conn.close()


@pytest.mark.parametrize(
    "fault",
    [
        "unprovisioned",
        "wrong-run",
        "wrong-scope",
        "wrong-source",
        "wrong-revision",
        "strict-replay",
    ],
)
def test_live_fleet_rejects_before_lease_or_run_or_work(tmp_path, monkeypatch, fault):
    args, params, conn, calls, lifecycle, authority, admit = _real_path(
        tmp_path,
        monkeypatch,
        binding=fault != "unprovisioned",
    )
    try:
        if fault == "wrong-run":
            lifecycle._fleet_prework = replace(
                authority,
                contract=authority.contract.__class__.freeze(
                    {**authority.contract.fields, "run_id": "different-run"}
                ),
            )
        elif fault == "wrong-scope":
            lifecycle._fleet_prework = replace(authority, targets=authority.targets[:1])
        elif fault == "wrong-source":
            lifecycle._fleet_prework = replace(
                authority, intent=authority.intent.model_copy(update={"source": "FAKE"})
            )
        elif fault == "wrong-revision":
            params["expected_durable_revision"] = -1
        if fault == "strict-replay":

            def rejected():
                return admit(rerun_from_run_id="other")
        elif fault == "wrong-revision":

            def rejected():
                return admit(expected_durable_revision=-1)
        else:
            rejected = admit
        with pytest.raises((TrustProtocolError, StateConflict)):
            rejected()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_control_plane_blocks_unprovisioned_enforcer_before_planner_and_submit(
    tmp_path, monkeypatch
):
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane

    args, _, conn, calls, lifecycle, _, _ = _real_path(tmp_path, monkeypatch, binding=False)

    class ForbiddenPlanner:
        def plan(self, *a, **k):
            raise AssertionError("admission planner must never start")

    class ForbiddenSupervisor:
        def submit(self, *a, **k):
            raise AssertionError("no work may be submitted")

    plane = FleetRunControlPlane(
        ForbiddenPlanner(), args["journal"], lifecycle, object(), ForbiddenSupervisor(), object()
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)
    try:
        with pytest.raises(TrustProtocolError, match="not provisioned"):
            plane._start_intent(
                intent,
                repository_scope=args["repository_scope"],
                fleet_run_id="durable-run",
                source=args["intent"].source,
                readmitted_from_run_id=None,
                rerun_from_run_id=None,
            )
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_provisioned_authority_without_activation_rejected_at_composition(tmp_path, monkeypatch):
    args, params, conn, calls, lifecycle, binding, _ = _real_path(tmp_path, monkeypatch)
    try:
        with pytest.raises(TrustProtocolError, match="explicitly activated"):
            FleetRunLifecycle(
                args["journal"]._database,
                args["journal"],
                params["execution"],
                object(),
                require_authenticated_fleet=False,
                fleet_prework=binding,
            )
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert not calls
    finally:
        conn.close()
