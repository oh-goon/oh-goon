"""WF-03C1: TL-1 consent travels ONLY inside original RUN_ADMITTED journal authority.

This is not interactive issuance, worker effect fencing, or Fleet execution qualification.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_executor import (
    ExecutorCapacityEvidenceInput,
    FleetExecutorIdentity,
    FleetExecutorPlacement,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCapabilities,
    UnrestrictedHostPlanEgress,
)
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    FleetRunAdmissionIntent,
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunSource, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_admission import repository_admission_for

RUN = "wf03c-run"


def _admission(
    tmp_path: Path,
    *,
    consent: bool = True,
    backend_id: str = "trusted-local",
    backend_configuration_id: str = "wf03c-config",
):
    repository = RepositoryIdentity(name="iris", path=tmp_path / "iris")
    plan = ResolvedFleetPlan.create(
        name="iris-training",
        description="",
        execution=CicdExecutionPolicy(trust_repository_code=True),
        steps=(
            ResolvedCommandStep(
                step_id="train",
                timeout_seconds=60,
                argv=("true",),
                capabilities=PlanStepCapabilities(egress=UnrestrictedHostPlanEgress()),
            ),
        ),
    )
    target = structural_fleet_plan_target(plan)
    backend = PlanIsolationBackendEvidence(
        backend_id=backend_id,
        configuration_id=backend_configuration_id,
        kernel_boundary="shared_host_kernel",
        remote_execution=False,
        experimental=True,
    )
    capacity = PlanExecutorCapacityEvidence.create(
        ExecutorCapacityEvidenceInput(
            fleet_run_id=RUN,
            executor=FleetExecutorIdentity("local", 2),
            source="local_verified",
            backend_configuration_id=backend.configuration_id,
            capacity_cpu_millis=2000,
            capacity_memory_bytes=512 * 1024 * 1024,
            capacity_max_processes=64,
            requested_parallelism=1,
            admitted_parallelism=1,
            per_attempt_cpu_millis=None,
            per_attempt_memory_bytes=None,
            per_attempt_max_processes=None,
            pool_id="local",
            placement_policy="priority_rendezvous_fifo_v2",
            placement_rank=1,
            executor_priority=1,
        )
    )
    placement = FleetExecutorPlacement(
        isolation_backend=backend,
        executor_capacity=capacity,
        executor_trust_epoch=0,
    )
    grant = (
        TrustedLocalRunConsent(
            fleet_run_id=RUN,
            execution_digest=target.execution_digest,
            backend_id=backend_id,
            backend_configuration_id=backend.configuration_id,
            executor_id="local",
            executor_generation=2,
            operator_interactive=True,
            explicitly_acknowledged_ambient_host_access=True,
        )
        if consent and backend_id == "trusted-local"
        else None
    )
    execution = StrictFleetExecutionAdmission(
        placement=placement,
        trusted_local_consent=grant,
    )
    intent = FleetRunAdmissionIntent(
        source=FleetRunSource.MANUAL,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=execution,
        repository_admissions=(
            repository_admission_for(
                repository_id=repository.repository_id,
                plan_id=plan.plan_id,
            ),
        ),
    )
    return repository, target, intent


def _admitted(tmp_path: Path):
    repository, _target, intent = _admission(tmp_path)
    scope = fleet_repository_scope({repository.repository_id})
    return RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id=RUN,
        source=intent.source,
        target=intent.target,
        capability=intent.capability,
        repository_scope=scope,
        targets=(repository,),
        execution=intent.execution,
        repository_admissions=intent.repository_admissions,
    )


def test_requires_explicit_consent_for_ambient_host_placement(tmp_path: Path):
    with pytest.raises(ValidationError, match="explicit per-Run consent"):
        _admission(tmp_path, consent=False)


def test_tl1_immutable_admission_round_trips_through_original_journal(tmp_path: Path):
    repository, _target, intent = _admission(tmp_path)
    database_path = tmp_path / "fleet.db"
    writer = Journal(LocalDatabase(database_path))
    with writer._database.transaction() as connection:
        writer.admit_run_in(
            connection,
            RUN,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
    # A distinct original Journal and SQLite connection reconstruct the same facts.
    reopened = Journal(LocalDatabase(database_path))
    detail = reopened.read_view().required_run_detail(RUN)
    assert isinstance(detail.execution, StrictFleetExecutionAdmission)
    assert detail.execution.trusted_local_consent == intent.execution.trusted_local_consent
    assert reopened.run_events(RUN).events[0].execution == detail.execution
    assert detail.execution.placement.isolation_backend.backend_id == "trusted-local"
    assert detail.execution.trusted_local_consent.filesystem_access == "operator_host_permissions"


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("fleet_run_id", "other-run"),
        ("backend_configuration_id", "other-config"),
        ("executor_id", "other-owner"),
        ("executor_generation", 3),
    ],
)
def test_wrong_run_or_backend_cannot_be_journaled(tmp_path: Path, field, wrong):
    repository, target, intent = _admission(tmp_path)
    consent = intent.execution.trusted_local_consent.model_copy(update={field: wrong})
    with pytest.raises(ValidationError, match="exact admitted Run and executor placement"):
        StrictFleetExecutionAdmission(
            placement=intent.execution.placement, trusted_local_consent=consent
        )


def test_wrong_plan_digest_refused_on_original_admission(tmp_path: Path):
    admitted = _admitted(tmp_path)
    consent = admitted.execution.trusted_local_consent.model_copy(
        update={"execution_digest": "f" * 64}
    )
    # Transported consent must remain consistent with an actual resolved Plan.
    changed = StrictFleetExecutionAdmission(
        placement=admitted.execution.placement, trusted_local_consent=consent
    )
    with pytest.raises(ValidationError, match="trusted-local durable Run admission rejected"):
        RunAdmittedEvent.model_validate(
            {
                **admitted.model_dump(mode="python", exclude_computed_fields=True),
                "execution": changed,
            }
        )


def test_reusing_consent_for_scheduled_or_multi_repository_admission_refused(tmp_path: Path):
    admitted = _admitted(tmp_path)
    for source in (FleetRunSource.SCHEDULED, FleetRunSource.CICD, FleetRunSource.SHARED_RUN):
        with pytest.raises(ValidationError, match="scheduled, rerun or unattended"):
            RunAdmittedEvent.model_validate(
                {
                    **admitted.model_dump(mode="python", exclude_computed_fields=True),
                    "source": source,
                }
            )
    with pytest.raises(ValidationError, match="exactly one explicitly"):
        RunAdmittedEvent.model_validate(
            {
                **admitted.model_dump(mode="python", exclude_computed_fields=True),
                "repository_scope": fleet_repository_scope(None),
            }
        )


def test_strict_backend_cannot_consume_tl1_grant(tmp_path: Path):
    repository, target, intent = _admission(tmp_path)
    caps = intent.execution.placement.isolation_backend.model_copy(
        update={"backend_id": "bubblewrap"}
    )
    placement = intent.execution.placement.model_copy(update={"isolation_backend": caps})
    with pytest.raises(ValidationError, match="strict isolation backend"):
        StrictFleetExecutionAdmission(
            placement=placement,
            trusted_local_consent=intent.execution.trusted_local_consent,
        )


def test_original_admitted_event_rechecks_model_copy_bypass(tmp_path: Path):
    admitted = _admitted(tmp_path)
    forged = admitted.execution.model_copy(update={"trusted_local_consent": None})
    with pytest.raises(ValidationError, match="explicit per-Run consent"):
        RunAdmittedEvent.model_validate(
            {
                **admitted.model_dump(mode="python", exclude_computed_fields=True),
                "execution": forged,
            }
        )


def test_actual_execution_digest_is_sha256_prefixed(tmp_path: Path):
    admitted = _admitted(tmp_path)
    assert admitted.target.execution_digest.startswith("sha256:")
    assert (
        admitted.execution.trusted_local_consent.execution_digest
        == admitted.target.execution_digest
    )


def test_legacy_strict_journal_schema_does_not_gain_null_consent(tmp_path: Path):
    _repository, _target, intent = _admission(tmp_path, backend_id="bubblewrap", consent=False)
    assert "trusted_local_consent" not in intent.execution.model_dump(
        mode="json", exclude_computed_fields=True
    )
    assert intent.execution.trusted_local_consent is None


def test_invalid_copy_bypassing_nested_validation_leaves_no_durable_run(tmp_path: Path):
    repository, _target, intent = _admission(tmp_path)
    copied = intent.execution.model_copy(update={"trusted_local_consent": None})
    bypassed = intent.model_copy(update={"execution": copied})
    journal = Journal(LocalDatabase(tmp_path / "refused.db"))
    with pytest.raises(ValidationError, match="explicit per-Run consent"):
        with journal._database.transaction() as connection:
            journal.admit_run_in(
                connection,
                RUN,
                bypassed,
                fleet_repository_scope({repository.repository_id}),
                [repository],
            )
    assert journal.read_view().run_detail(RUN) is None


# WF-03C2A: same source of truth must drive exact worker backend selection.
def test_reopened_original_journal_selects_exact_tl1_backend_only_with_consent(tmp_path: Path):
    from oh_goon.integrations.fleet_plan_process_isolation import (
        admitted_tl1_run_context,
        isolation_backend_evidence,
        select_bound_process_isolation_backend,
    )
    from oh_goon.integrations.fleet_plan_trusted_local_isolation import TrustedLocalProcessIsolation

    backend = TrustedLocalProcessIsolation()
    repository, target, intent = _admission(
        tmp_path,
        backend_configuration_id=backend.configuration_identity(),
    )
    database_path = tmp_path / "runs.db"
    writer = Journal(LocalDatabase(database_path))
    with writer._database.transaction() as connection:
        writer.admit_run_in(
            connection,
            RUN,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
    durable = Journal(LocalDatabase(database_path)).read_view().required_run_detail(RUN)
    admitted = isolation_backend_evidence(backend)
    assert durable.execution.placement.isolation_backend == admitted
    consent, scope = admitted_tl1_run_context(target.plan, admitted, durable)
    assert consent == intent.execution.trusted_local_consent
    assert scope.fleet_run_id == RUN
    assert scope.execution_digest == target.execution_digest
    assert scope.executor_id == "local"
    assert (
        select_bound_process_isolation_backend(
            target.plan,
            admitted,
            (backend,),
            admitted_run=durable,
        )
        is backend
    )

    # No caller-provided standalone consent can replace the original journal.
    with pytest.raises(FleetPlanInvalid, match="RUN_ADMITTED consent"):
        select_bound_process_isolation_backend(target.plan, admitted, (backend,))


def test_worker_selection_rejects_rebinding_plan_placement_or_consent(tmp_path: Path):
    from oh_goon.integrations.fleet_plan_process_isolation import (
        isolation_backend_evidence,
        select_bound_process_isolation_backend,
    )
    from oh_goon.integrations.fleet_plan_trusted_local_isolation import TrustedLocalProcessIsolation

    backend = TrustedLocalProcessIsolation()
    repository, target, intent = _admission(
        tmp_path,
        backend_configuration_id=backend.configuration_identity(),
    )
    journal = Journal(LocalDatabase(tmp_path / "fleet.db"))
    with journal._database.transaction() as connection:
        journal.admit_run_in(
            connection,
            RUN,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
    original = Journal(LocalDatabase(tmp_path / "fleet.db")).read_view().required_run_detail(RUN)
    admitted = isolation_backend_evidence(backend)

    different_plan = ResolvedFleetPlan.create(
        name="different",
        description="",
        execution=target.plan.execution,
        steps=target.plan.steps,
    )
    with pytest.raises(FleetPlanInvalid, match="immutable RUN_ADMITTED Plan"):
        select_bound_process_isolation_backend(
            different_plan, admitted, (backend,), admitted_run=original
        )

    wrong_backend = admitted.model_copy(update={"configuration_id": "wrong"})
    with pytest.raises(FleetPlanInvalid, match="RUN_ADMITTED placement"):
        select_bound_process_isolation_backend(
            target.plan, wrong_backend, (backend,), admitted_run=original
        )

    wrong_consent = original.execution.trusted_local_consent.model_copy(
        update={"executor_generation": 99}
    )
    tampered_execution = original.execution.model_copy(
        update={"trusted_local_consent": wrong_consent}
    )
    tampered = original.model_copy(update={"execution": tampered_execution})
    with pytest.raises(FleetPlanInvalid, match="stale or belongs"):
        select_bound_process_isolation_backend(
            target.plan, admitted, (backend,), admitted_run=tampered
        )


def test_working_strict_without_tl1_still_uses_its_original_requirements(tmp_path: Path):
    from oh_goon.integrations.fleet_plan_process_isolation import (
        select_bound_process_isolation_backend,
    )
    from oh_goon.integrations.fleet_plan_trusted_local_isolation import TrustedLocalProcessIsolation

    backend = TrustedLocalProcessIsolation()
    plan = _admission(tmp_path)[1].plan
    # Calling with a forged physical backend identity cannot make the original
    # strict capability obligations disappear.
    with pytest.raises(FleetPlanInvalid, match="RUN_ADMITTED consent"):
        select_bound_process_isolation_backend(
            plan,
            PlanIsolationBackendEvidence(
                backend_id="trusted-local",
                configuration_id=backend.configuration_identity(),
                kernel_boundary="shared_host_kernel",
                remote_execution=False,
                experimental=True,
            ),
            (backend,),
        )
