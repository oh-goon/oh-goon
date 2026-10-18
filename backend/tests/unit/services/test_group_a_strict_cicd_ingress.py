"""M176: original strict scheduler cut owns authenticated Run and both journals.

External native measurements and physical backend availability are stubbed in
this hosted ring, but scheduler placement, capacity, lease, SQLite publication,
revision, signatures and signer epoch are real production owners.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.errors import ExecutorPlacementLost, FleetPlanInvalid, StateConflict
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_admission import (
    PlanRepositoryAdmissionEvidence,
    StrictFleetExecutionAdmission,
)
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import OperationAction
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.services import fleet_native_run_contract_compiler as native
from oh_goon.services.fleet_strict_policy_enrollment import (
    StrictFleetEnrollmentIntent,
    StrictFleetPolicyEnrollment,
)
from tests.fixtures.db1b2_legacy_sqlite import SignerTrustEpochAuthority
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_policy_only_fleet_provisioning import policy_only
from tests.unit.services.test_group_a_precommit_native_issuer_migration import _native_owner_stubs


def strict_fixture(tmp_path, monkeypatch):
    args, conn, _calls, lifecycle, policy, _admit, _observed = policy_only(tmp_path, monkeypatch)
    original = args["intent"]
    old_plan = original.target.plan
    strict = CicdExecutionPolicy(
        trust_repository_code=True,
        resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=512),
        repositories=ParallelRepositoryExecution(max_parallel=2),
    )
    plan = ResolvedFleetPlan.create(
        name=old_plan.name,
        description=old_plan.description,
        steps=old_plan.steps,
        execution=strict,
        requirements=old_plan.requirements,
        imports=old_plan.imports,
        step_sources=old_plan.step_sources,
    )
    target = original.target.model_copy(
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
        for item in original.repository_admissions
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    args["intent"] = original.model_copy(
        update={
            "target": target,
            "repository_admissions": admissions,
        }
    )
    strict_intent = StrictFleetEnrollmentIntent(
        source=original.source,
        target=target,
        capability=intent.definition.durable_snapshot(),
        repository_admissions=admissions,
        application_launch=original.application_launch,
    )
    enrollment = StrictFleetPolicyEnrollment(
        **{
            **{
                field.name: getattr(policy, field.name)
                for field in fields(policy)
                if field.name != "intent"
            },
            "intent": strict_intent,
        }
    )
    lifecycle._fleet_provisioner = replace(lifecycle._fleet_provisioner, enrolled=(enrollment,))
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
    from oh_goon.runtime import _executor_placement as scheduler

    # External host preflights are not available in the hosted test runner.
    monkeypatch.setattr(scheduler, "preflight_executor_tool_feasibility", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "preflight_host_environment_feasibility", lambda *a, **k: None)
    monkeypatch.setattr(BubblewrapProcessIsolation, "require_available", lambda *a, **k: None)
    pool = FleetExecutorPool(args["journal"]._database, configured_executor_pool())
    lifecycle._executor_pool = pool
    contract = AdmittedRunContract.freeze(
        {
            **policy.deployment_policy.read(),
            **{
                key: value
                for key, value in {
                    "run_id": policy.run_id,
                    "resolved_plan_identity": target.execution_digest,
                    "executor_generation": enrollment.configured.executor_generation,
                    "execution_environment_identity": enrollment.configured.execution_environment_identity,
                    "backend_capability_manifest_identity": enrollment.configured.capability_manifest_identity,
                    "repository_identity": "sha256:" + "1" * 64,
                    "source_identity": "sha256:" + "2" * 64,
                    "lock_identity": "sha256:" + "3" * 64,
                    "native_tool_identity": "sha256:" + "4" * 64,
                }.items()
            },
        }
    )
    observed = []

    def measured(prework, authority, control_state_root, *, check_uv):
        captured, _ = prework.read(journal=args["journal"], run_id=policy.run_id)
        assert isinstance(captured.execution, StrictFleetExecutionAdmission)
        assert captured.repository_admissions == admissions
        assert prework.connection.in_transaction
        assert (
            args["journal"].read_view_from_connection(prework.connection).run_detail(policy.run_id)
            is None
        )
        observed.append(check_uv)
        return {key: contract.fields[key] for key in native.DERIVED_FIELDS}

    monkeypatch.setattr(native, "_owner_observation", measured)
    calls = _native_owner_stubs(monkeypatch, contract)

    def admit(**changes):
        kw = dict(
            fleet_run_id=policy.run_id,
            intent=intent,
            repository_scope=enrollment.repository_scope,
            targets=list(enrollment.targets),
            expected_durable_revision=args["journal"]._database.durable_revision(),
            repository_admissions=admissions,
            source=original.source,
            requested_priority=SchedulingPriority.NORMAL,
        )
        kw.update(changes)
        return lifecycle.admit(**kw)

    return args, conn, lifecycle, pool, enrollment, admit, observed, calls


def assert_no_admission(args, conn, run_id="durable-run"):
    assert args["journal"].read_view().run_detail(run_id) is None
    assert conn.execute("SELECT count(*) FROM lace_run_authority").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM lace_run_events").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM executor_capacity_reservations").fetchone()[0] == 0


def test_strict_placed_original_transaction_publishes_once(tmp_path, monkeypatch):
    args, conn, lifecycle, pool, enrolled, admit, observed, calls = strict_fixture(
        tmp_path, monkeypatch
    )
    try:
        ticket, state = admit()
        assert ticket._lease.fleet_run_id == "durable-run" and state is not None
        detail = args["journal"].read_view().required_run_detail("durable-run")
        assert isinstance(detail.execution, StrictFleetExecutionAdmission)
        assert (
            detail.execution.placement.executor_capacity.executor_id
            == enrolled.configured.executor_id
        )
        assert detail.scheduling.requested_priority is SchedulingPriority.NORMAL
        assert facts(args, conn)[1:] == (1, 2)
        assert observed == [True, False, True, False]
        assert len(calls) == 6
        assert pool.placement_queue().entries == ()
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM executor_capacity_reservations").fetchone()[0] == 1
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "unavailable-placement",
        "revoked-signer",
        "stale-revision",
        "stale-plan",
        "stale-source",
        "strict-rerun",
        "strict-readmission",
        "backend-mismatch",
        "native-drift",
        "failed-causal-publication",
        "wrong-priority",
    ],
)
def test_strict_denials_rollback_capacity_lease_both_journals_and_work(
    tmp_path, monkeypatch, attack
):
    args, conn, lifecycle, pool, enrolled, admit, observed, calls = strict_fixture(
        tmp_path, monkeypatch
    )
    try:
        if attack == "unavailable-placement":
            from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
                BubblewrapProcessIsolation,
            )

            monkeypatch.setattr(
                BubblewrapProcessIsolation,
                "require_available",
                lambda *a, **k: (_ for _ in ()).throw(FleetPlanInvalid("physical unavailable")),
            )
        elif attack == "revoked-signer":
            signer = SignerTrustEpochAuthority(conn)
            signer.revoke(expected=signer.active())
        elif attack == "stale-plan":
            lifecycle._fleet_provisioner = replace(
                lifecycle._fleet_provisioner,
                enrolled=(
                    replace(
                        enrolled,
                        intent=replace(
                            enrolled.intent,
                            target=enrolled.intent.target.model_copy(
                                update={"identity": ExecutionIdentity(digest="sha256:" + "0" * 64)}
                            ),
                        ),
                    ),
                ),
            )
        elif attack == "stale-source":
            lifecycle._fleet_provisioner = replace(
                lifecycle._fleet_provisioner,
                enrolled=(
                    replace(
                        enrolled,
                        intent=replace(
                            enrolled.intent,
                            repository_admissions=tuple(
                                reversed(enrolled.intent.repository_admissions)
                            ),
                        ),
                    ),
                ),
            )
        elif attack == "backend-mismatch":
            original = pool.require_bound_in

            def incompatible(connection, placement, plan):
                original(connection, placement, plan)
                raise ExecutorPlacementLost("physical backend changed")

            monkeypatch.setattr(pool, "require_bound_in", incompatible)
        elif attack == "native-drift":
            monkeypatch.setattr(
                native,
                "_owner_observation",
                lambda *a, **k: (_ for _ in ()).throw(
                    TrustProtocolError("native lock/source changed")
                ),
            )
        elif attack == "failed-causal-publication":
            from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal

            monkeypatch.setattr(
                CausalRunJournal,
                "_append_locked",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("causal journal unavailable")),
            )
        kwargs = {}
        if attack == "stale-revision":
            kwargs["expected_durable_revision"] = -1
        elif attack == "strict-rerun":
            kwargs["rerun_from_run_id"] = "old-run"
        elif attack == "strict-readmission":
            kwargs["readmitted_from_run_id"] = "old-run"
        elif attack == "wrong-priority":
            kwargs["requested_priority"] = SchedulingPriority.HIGH
        expected_error = (
            RuntimeError
            if attack == "failed-causal-publication"
            else (TrustProtocolError, FleetPlanInvalid, StateConflict)
        )
        with pytest.raises(expected_error):
            admit(**kwargs)
        assert_no_admission(args, conn)
        assert pool.placement_queue().entries == ()
        assert len(calls) == 6 if attack == "failed-causal-publication" else calls == []
    finally:
        conn.close()


def test_strict_preplacement_capability_cannot_authorize_view_only(tmp_path, monkeypatch):
    args, conn, lifecycle, pool, enrolled, admit, observed, calls = strict_fixture(
        tmp_path, monkeypatch
    )
    try:
        view_only = enrolled.intent.capability.model_copy(update={"action": OperationAction.STATUS})
        with pytest.raises(TrustProtocolError, match="pre-placement"):
            replace(enrolled.intent, capability=view_only)
        assert_no_admission(args, conn)
        assert calls == []
    finally:
        conn.close()


def test_strict_missing_run_enrollment_is_preplanning_rejection(tmp_path, monkeypatch):
    args, conn, lifecycle, pool, enrolled, admit, observed, calls = strict_fixture(
        tmp_path, monkeypatch
    )
    try:
        # Another Run ID has no explicit enrollment, and cannot enter the queue.
        with pytest.raises(TrustProtocolError, match="provisioned"):
            admit(fleet_run_id="not-enrolled")
        assert_no_admission(args, conn)
        assert pool.placement_queue().entries == ()
        assert calls == []
    finally:
        conn.close()
