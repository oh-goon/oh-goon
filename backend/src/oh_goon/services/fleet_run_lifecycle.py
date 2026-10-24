from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.enforcer_lace_trust import (
    UNPROVISIONED_FLEET_ADMISSION,
    UNPROVISIONED_FLEET_PLAN_INGRESS,
)
from oh_goon.core.errors import (
    FleetPlanInvalid,
    InvalidRunTransition,
    JournalConflict,
    StateConflict,
)
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_contract import RetainedPlanCacheSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    FleetRunDetail,
    FleetRunFailure,
    FleetRunState,
)
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetExecutionAdmission,
    FleetRepositoryScope,
    FleetRunAdmissionIntent,
    PlanRepositoryAdmissionEvidence,
    StrictFleetExecutionAdmission,
)
from oh_goon.core.models.run_launch import ApplicationLaunchReceipt
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.run_identity import require_fleet_run_detail_binding
from oh_goon.core.vocabulary import FleetRunSource, FleetRunTerminalIntent
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_repository_scope import admitted_active_plan
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.enforcer_lace_restart_reclaim_fence import (
    require_no_original_lace_intent_before_reclamation_in,
)
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionSession, ExecutionTicket
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.run_admission_lock import serialize_run_admission
from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt
from oh_goon.services.fleet_policy_enrollment import FleetPolicyEnrollment
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
    from oh_goon.core.models.fleet_plan import ResolvedPlanStep
    from oh_goon.core.ports.original_mount_intent import OriginalMountIntentDescriptor
    from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend
    from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
    from oh_goon.runtime.enforcer_lace_fleet_resource_owner import (
        OriginalFleetLocalLaceRecoveryOwner,
        OriginalFleetLocalLaceReservation,
    )


@dataclass(frozen=True, slots=True)
class FleetExecutionAuthority:
    """Exact durable capability, target, repository capture, and execution admission."""

    capability: CapabilitySnapshot
    target: OperationTarget | None
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    execution: FleetExecutionAdmission
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    # The original journal already reconstructed this at the authority cut.
    # Transport it to the worker without making mutable progress/status part of
    # the immutable placement equality checked at subsequent effect cuts.
    admitted_run: FleetRunDetail | None = field(default=None, compare=False)


class FleetRunLifecycle:
    """Own live FleetRun mutations that cross Journal and execution authority.

    Normal admission/cancellation/terminalization must never coordinate the lease and
    Journal in separate service transactions. Startup recovery is intentionally not
    here: it operates only after proving there is no live execution owner.
    """

    def __init__(
        self,
        database: LocalDatabase,
        journal: Journal,
        execution: ExecutionCoordinator,
        executor_pool: FleetExecutorPool,
        content_store: FleetContentStore | None = None,
        *,
        require_authenticated_fleet: bool = False,
        fleet_prework: FleetPreworkComposition | None = None,
        fleet_provisioner: FleetPreworkProvisioner | None = None,
        require_compiled_fleet_contract: bool = False,
        standard_local_fleet: bool = False,
    ) -> None:
        self._database = database
        self._journal = journal
        self._execution = execution
        self._executor_pool = executor_pool
        self._content_store = content_store
        self._require_authenticated_fleet = require_authenticated_fleet
        self._fleet_prework = fleet_prework
        self._fleet_provisioner = fleet_provisioner
        self._require_compiled_fleet_contract = require_compiled_fleet_contract
        self._standard_local_fleet = standard_local_fleet
        if standard_local_fleet and (
            require_authenticated_fleet
            or require_compiled_fleet_contract
            or fleet_prework is not None
            or fleet_provisioner is not None
        ):
            raise StateConflict("standard-local cannot combine with authenticated admission")
        if require_compiled_fleet_contract and not require_authenticated_fleet:
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            raise TrustProtocolError("compiled Fleet contract requires authenticated admission")
        if (
            fleet_prework is not None or fleet_provisioner is not None
        ) and not require_authenticated_fleet:
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            raise TrustProtocolError("provisioned Fleet Enforcer must be explicitly activated")
        if fleet_prework is not None and fleet_provisioner is not None:
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            raise TrustProtocolError("Fleet Enforcer must have one provisioning authority")

    @contextmanager
    def execution_connection_scope(self) -> Iterator[None]:
        """Delegate one worker-thread SQLite handle lifetime to the database owner."""
        with self._database.execution_connection_scope():
            yield

    @contextmanager
    def serialize_admission(self, identity: str) -> Iterator[None]:
        """Serialize one caller-stable admission identity without creating durable state."""
        with serialize_run_admission(self._database.path, identity):
            yield

    def require_cli_plan_preparation(self, run_id: str | None) -> None:
        """Retain the CLI spelling while sharing one pre-resolution ingress owner."""
        self.require_plan_ingress_preparation(run_id)

    def require_plan_ingress_preparation(self, run_id: str | None) -> None:
        """Reject unprovisioned CLI/HTTP Plan runs before reading the Plan.

        Only the deployment's original provisioner can establish membership.
        Final authorization remains in require_fleet_prework_available/admit.
        No native issuer observation, planning, journal, or reservation here.
        """
        if not self._require_authenticated_fleet:
            return
        if type(self._fleet_provisioner) is not FleetPreworkProvisioner:
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            raise TrustProtocolError(UNPROVISIONED_FLEET_PLAN_INGRESS)
        if run_id is None:
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            raise TrustProtocolError("explicit Fleet Run identity required")
        self._fleet_provisioner.require_enrolled_identity(run_id)

    def require_fleet_prework_available(
        self,
        target: OperationTarget | None,
        run_id: str | None = None,
        *,
        readmitted_from_run_id: str | None = None,
        rerun_from_run_id: str | None = None,
    ) -> None:
        """Reject uncut Fleet ingress before Plan work, replay, or native observation."""
        if isinstance(target, FleetPlanTarget) and target.agent_repair is not None:
            repair = target.agent_repair
            if readmitted_from_run_id is not None or rerun_from_run_id is not None:
                raise FleetPlanInvalid("repair limits cannot be inherited by rerun or readmission")
            if repair.attempt == 1 and run_id != repair.repair_id:
                raise FleetPlanInvalid(
                    "first repair admission must use the selected repair identity"
                )
            if repair.attempt > 1 and run_id in {repair.repair_id, repair.previous_run_id}:
                raise FleetPlanInvalid("repair continuation requires a separate Run identity")
        if isinstance(target, FleetPlanTarget) and self._standard_local_fleet:
            if isinstance(target.plan.execution, CicdExecutionPolicy):
                raise FleetPlanInvalid("standard-local host execution refuses CI/CD plans")
            if any(step.kind == "agent" for step in target.plan.steps):
                from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep

                agent_steps = [step for step in target.plan.steps if step.kind == "agent"]
                if any(
                    not isinstance(step, ResolvedLocalSearchAgentStep) for step in agent_steps
                ) or len(target.agent_deliveries) != len(agent_steps):
                    raise FleetPlanInvalid(
                        "standard-local Agent requires original typed frozen delivery"
                    )
        elif isinstance(target, FleetPlanTarget) and any(
            step.kind == "agent" for step in target.plan.steps
        ):
            raise FleetPlanInvalid(
                "Agent Step requires explicitly selected standard-local admission"
            )
        if isinstance(target, FleetPlanTarget) and self._require_authenticated_fleet:
            # Replay requires its own authenticated lineage cutover. Do not let
            # an enrolled child reach preplanning, idempotent journal lookup, or
            # native provisioner observation before this gate has an owner.
            if readmitted_from_run_id is not None or rerun_from_run_id is not None:
                from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                raise TrustProtocolError("replay Fleet ingress requires its own Enforcer cutover")
            if type(self._fleet_provisioner) is FleetPreworkProvisioner:
                if run_id is None:
                    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                    raise TrustProtocolError("explicit Fleet Run identity required")
                binding = self._fleet_provisioner.resolve(run_id)
                if isinstance(target.plan.execution, CicdExecutionPolicy):
                    if type(binding) is not StrictFleetPolicyEnrollment:
                        from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                        raise TrustProtocolError(
                            "strict Fleet Plan requires strict policy enrollment"
                        )
                    binding.require_preplanning(run_id=run_id, target=target)
                elif type(binding) is StrictFleetPolicyEnrollment:
                    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                    raise TrustProtocolError("strict enrollment cannot authorize Direct Fleet Plan")
                if (
                    type(binding) is FleetPolicyEnrollment
                    and not self._require_compiled_fleet_contract
                ):
                    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                    raise TrustProtocolError(
                        "policy-only Fleet enrollment requires native compiler"
                    )
                if self._require_compiled_fleet_contract and binding.deployment_policy is None:
                    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                    raise TrustProtocolError(
                        "compiled Fleet contract deployment policy not provisioned"
                    )
            elif type(self._fleet_prework) is not FleetPreworkComposition:
                from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                raise TrustProtocolError(UNPROVISIONED_FLEET_ADMISSION)
            elif (
                self._require_compiled_fleet_contract
                and self._fleet_prework.deployment_policy is None
            ):
                from oh_goon.core.enforcer_lace_trust import TrustProtocolError

                raise TrustProtocolError(
                    "compiled Fleet contract deployment policy not provisioned"
                )

    def admit(
        self,
        fleet_run_id: str,
        intent: ResolvedCapabilityIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        *,
        expected_durable_revision: int,
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
        source: FleetRunSource,
        readmitted_from_run_id: str | None = None,
        rerun_from_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        schedule_run_id: str | None = None,
    ) -> tuple[ExecutionTicket, FleetRunState]:
        if isinstance(intent.target, FleetPlanTarget) and not repository_admissions:
            raise StateConflict(
                "Fleet Plan admission is missing repository/Plan evidence before RUN_ADMITTED"
            )
        target = intent.target
        from oh_goon.services.fleet_caller_file_freshness import require_target_caller_file_fresh

        require_target_caller_file_fresh(target)
        self.require_fleet_prework_available(
            target,
            fleet_run_id,
            readmitted_from_run_id=readmitted_from_run_id,
            rerun_from_run_id=rerun_from_run_id,
        )
        application_launch = current_application_launch_receipt()
        if isinstance(target, FleetPlanTarget) and self._require_authenticated_fleet:
            # No legacy fallback (including Strict), replay or execution-ticket
            # creation is allowed when the Enforcer cut is required.
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError
            from oh_goon.services.fleet_admission import FleetAdmissionPlanner

            binding: (
                FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment | None
            ) = self._fleet_prework
            if type(self._fleet_provisioner) is FleetPreworkProvisioner:
                binding = self._fleet_provisioner.resolve(fleet_run_id)
            if type(binding) not in (
                FleetPreworkComposition,
                FleetPolicyEnrollment,
                StrictFleetPolicyEnrollment,
            ):
                raise TrustProtocolError(UNPROVISIONED_FLEET_ADMISSION)
            assert isinstance(
                binding,
                (FleetPreworkComposition, FleetPolicyEnrollment, StrictFleetPolicyEnrollment),
            )
            if (
                type(binding) in (FleetPolicyEnrollment, StrictFleetPolicyEnrollment)
                and not self._require_compiled_fleet_contract
            ):
                raise TrustProtocolError("policy-only Fleet enrollment requires native compiler")
            if isinstance(target.plan.execution, CicdExecutionPolicy):
                if type(binding) is not StrictFleetPolicyEnrollment:
                    raise TrustProtocolError(
                        "strict Fleet ingress requires exact strict policy enrollment"
                    )
                self._preflight_retained_inputs(target.plan, repository_admissions)
                # The original strict placement queue and its final write cut own
                # capacity, executor epoch, scheduling and the control lease.
                # No Direct reserve_fleet_with() or fallback on failure.
                _, admitted = self._executor_pool.admit_strict_with(
                    fleet_run_id=fleet_run_id,
                    plan=target.plan,
                    targets=tuple(targets),
                    repository_admissions=repository_admissions,
                    requested_priority=requested_priority,
                    commit=lambda connection, placement: self._admit_authenticated_strict_in(
                        connection,
                        placement,
                        fleet_run_id,
                        intent,
                        repository_scope,
                        targets,
                        binding=binding,
                        expected_durable_revision=expected_durable_revision,
                        repository_admissions=repository_admissions,
                        source=source,
                        application_launch=application_launch,
                        schedule_run_id=schedule_run_id,
                    ),
                )
                return admitted
            _require_direct_priority_supported(requested_priority)
            captured_intent = FleetRunAdmissionIntent(
                source=source,
                target=target,
                capability=intent.definition.durable_snapshot(),
                schedule_run_id=schedule_run_id,
                execution=DirectFleetExecutionAdmission(rerun_from_run_id=None),
                repository_admissions=repository_admissions,
                application_launch=application_launch,
            )
            binding.require_matching(
                run_id=fleet_run_id,
                intent=captured_intent,
                scope=repository_scope,
                targets=targets,
            )
            if type(binding) is FleetPolicyEnrollment:
                return FleetAdmissionPlanner.reserve_atomic_fleet_policy_ingress(
                    execution=self._execution,
                    expected_durable_revision=expected_durable_revision,
                    operational_journal=self._journal,
                    run_id=fleet_run_id,
                    deployment_policy=binding.deployment_policy,
                    authenticator=binding.authenticator,
                    configured=binding.configured,
                    current=binding.current,
                    intent=captured_intent,
                    repository_scope=repository_scope,
                    targets=targets,
                    path_authority=binding.path_authority,
                    key_directory=binding.key_directory,
                    trusted_public_roots=binding.trusted_public_roots,
                    control_state_root=binding.control_state_root,
                )
            assert isinstance(binding, FleetPreworkComposition)
            return FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(
                execution=self._execution,
                expected_durable_revision=expected_durable_revision,
                operational_journal=self._journal,
                contract=binding.contract,
                authenticator=binding.authenticator,
                configured=binding.configured,
                current=binding.current,
                intent=captured_intent,
                repository_scope=repository_scope,
                targets=targets,
                path_authority=binding.path_authority,
                key_directory=binding.key_directory,
                trusted_public_roots=binding.trusted_public_roots,
                control_state_root=binding.control_state_root,
                deployment_policy=(
                    binding.deployment_policy if self._require_compiled_fleet_contract else None
                ),
            )
        if isinstance(target, FleetPlanTarget) and isinstance(
            target.plan.execution, CicdExecutionPolicy
        ):
            try:
                self._preflight_retained_inputs(target.plan, repository_admissions)
                _, admitted = self._executor_pool.admit_strict_with(
                    fleet_run_id=fleet_run_id,
                    plan=target.plan,
                    targets=tuple(targets),
                    repository_admissions=repository_admissions,
                    requested_priority=requested_priority,
                    commit=lambda connection, placement: self._admit_strict_in(
                        connection,
                        fleet_run_id,
                        intent,
                        repository_scope,
                        targets,
                        placement=placement,
                        expected_durable_revision=expected_durable_revision,
                        repository_admissions=repository_admissions,
                        source=source,
                        readmitted_from_run_id=readmitted_from_run_id,
                        rerun_from_run_id=rerun_from_run_id,
                        application_launch=application_launch,
                        schedule_run_id=schedule_run_id,
                    ),
                )
                return admitted
            except BaseException:
                replay = _replay_admission(
                    self._database,
                    self._journal,
                    self._execution,
                    self._executor_pool,
                    fleet_run_id,
                    intent,
                    repository_scope,
                    targets,
                    source,
                    readmitted_from_run_id,
                    rerun_from_run_id,
                    strict=True,
                )
                if replay is None:
                    raise
                return replay
        _require_direct_priority_supported(requested_priority)
        admitted_intent = FleetRunAdmissionIntent(
            source=source,
            target=intent.target,
            capability=intent.definition.durable_snapshot(),
            execution=DirectFleetExecutionAdmission(rerun_from_run_id=rerun_from_run_id),
            repository_admissions=repository_admissions,
            application_launch=application_launch,
            schedule_run_id=schedule_run_id,
        )
        try:
            return self._execution.reserve_fleet_with(
                fleet_run_id,
                expected_durable_revision=expected_durable_revision,
                mutate=lambda connection: self._journal.admit_run_in(
                    connection, fleet_run_id, admitted_intent, repository_scope, targets
                ),
            )
        except BaseException:
            replay = _replay_admission(
                self._database,
                self._journal,
                self._execution,
                self._executor_pool,
                fleet_run_id,
                intent,
                repository_scope,
                targets,
                source,
                readmitted_from_run_id,
                rerun_from_run_id,
                strict=False,
            )
            if replay is None:
                raise
            return replay

    def _preflight_retained_inputs(
        self,
        plan: ResolvedFleetPlan,
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
    ) -> None:
        """Reject missing/wrong-owner/wrong-kind retained inputs before placement.

        Only metadata and object presence are checked here. The execution preparation
        owner still leases and verifies the immutable bytes before effects.
        """

        if not any(isinstance(step.cache.source, RetainedPlanCacheSource) for step in plan.steps):
            return

        for admitted in repository_admissions:
            active_plan = admitted_active_plan(plan, admitted)
            retained = tuple(
                step.cache.source
                for step in active_plan.steps
                if isinstance(step.cache.source, RetainedPlanCacheSource)
            )
            if not retained:
                continue
            if self._content_store is None:
                raise FleetPlanInvalid(
                    "Fleet plan requires retained cache objects without a content store"
                )
            for source in retained:
                self._content_store.preflight_reference_metadata(
                    source.reference_id,
                    owner_repository_id=admitted.repository_id,
                    expected_kind="tree",
                )

    def _admit_strict_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: ResolvedCapabilityIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        *,
        placement: FleetExecutorPlacement,
        expected_durable_revision: int,
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
        source: FleetRunSource,
        readmitted_from_run_id: str | None,
        rerun_from_run_id: str | None,
        application_launch: ApplicationLaunchReceipt,
        schedule_run_id: str | None = None,
    ) -> tuple[ExecutionTicket, FleetRunState]:
        ticket = self._execution.reserve_fleet_in(
            connection,
            fleet_run_id,
            expected_durable_revision=expected_durable_revision,
        )
        state = self._journal.admit_run_in(
            connection,
            fleet_run_id,
            FleetRunAdmissionIntent(
                source=source,
                target=intent.target,
                capability=intent.definition.durable_snapshot(),
                execution=StrictFleetExecutionAdmission(
                    placement=placement,
                    readmitted_from_run_id=readmitted_from_run_id,
                    rerun_from_run_id=rerun_from_run_id,
                ),
                repository_admissions=repository_admissions,
                application_launch=application_launch,
                schedule_run_id=schedule_run_id,
                scheduling=self._executor_pool.scheduling_receipt_in(connection, fleet_run_id),
            ),
            repository_scope,
            targets,
        )
        return ticket, state

    def _admit_authenticated_strict_in(
        self,
        connection: sqlite3.Connection,
        placement: FleetExecutorPlacement,
        fleet_run_id: str,
        intent: ResolvedCapabilityIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        *,
        binding: StrictFleetPolicyEnrollment,
        expected_durable_revision: int,
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
        source: FleetRunSource,
        application_launch: ApplicationLaunchReceipt,
        schedule_run_id: str | None = None,
    ) -> tuple[ExecutionTicket, FleetRunState]:
        """M176: strict scheduler's original capacity+lease+Run+causal write cut."""
        from oh_goon.services.fleet_admission import FleetAdmissionPlanner

        target = intent.target
        assert isinstance(target, FleetPlanTarget)

        ticket = self._execution.reserve_fleet_in(
            connection,
            fleet_run_id,
            expected_durable_revision=expected_durable_revision,
        )
        captured = FleetRunAdmissionIntent(
            source=source,
            target=intent.target,
            capability=intent.definition.durable_snapshot(),
            execution=StrictFleetExecutionAdmission(placement=placement),
            repository_admissions=repository_admissions,
            application_launch=application_launch,
            schedule_run_id=schedule_run_id,
            scheduling=self._executor_pool.scheduling_receipt_in(connection, fleet_run_id),
        )
        binding.require_matching(
            run_id=fleet_run_id,
            intent=captured,
            scope=repository_scope,
            targets=targets,
        )
        # The original executor pool rechecks the physical backend and its
        # durable reservation; a provisioner is not an alternate selector.
        self._executor_pool.require_bound_in(connection, placement, target.plan)
        state = FleetAdmissionPlanner.publish_compiled_strict_fleet_in(
            connection=connection,
            execution=self._execution,
            operational_journal=self._journal,
            binding=binding,
            intent=captured,
            repository_scope=repository_scope,
            targets=targets,
            fleet_run_id=fleet_run_id,
        )
        self._executor_pool.require_bound_in(connection, placement, target.plan)
        return ticket, state

    def reserve_original_agent_source_for_attempt(
        self,
        session: ExecutionSession,
        expected: FleetExecutionAuthority,
        *,
        repository_id: str,
        operation_id: str,
        step_id: str,
        attempt_number: int,
        packet: bytes,
        expected_handoff_identity: str,
        source_attempt: "FleetPlanStepAttempt | None" = None,
    ) -> int:
        """AG-02B2B3: one original Fleet cut for identity + optional private stage.

        Require the ORIGINAL operational STEP_ATTEMPT_CREATED fact, then bind
        it to the existing signed causal journal in the SAME session mutation.
        No source reread, new admission, Step launcher, effect or transport path.
        Real Agent execution is still refused by the canonical resolver.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError, digest
        from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
        from oh_goon.core.models.run_events import StepAttemptCreatedEvent
        from oh_goon.core.path_authority import storage_component
        from oh_goon.core.product_identity import (
            fleet_step_attempt_id,
            validate_repository_attempt_operation_id,
        )
        from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority
        from oh_goon.services.agent_authoring_sources import reserve_verified_agent_source_in

        if (
            type(session) is not ExecutionSession
            or not self._require_authenticated_fleet
            or type(self._fleet_provisioner) is not FleetPreworkProvisioner
            or not isinstance(expected.execution, StrictFleetExecutionAdmission)
            or expected.execution.placement.isolation_backend.backend_id != "bubblewrap"
            or type(repository_id) is not str
            or not repository_id
            or type(operation_id) is not str
            or not operation_id
            or type(step_id) is not str
            or not step_id
            or type(attempt_number) is not int
            or attempt_number < 1
        ):
            raise TrustProtocolError("original strict Fleet Agent attempt authority required")
        if source_attempt is not None and (
            type(source_attempt) is not FleetPlanStepAttempt
            or source_attempt.attempt_number != attempt_number
            or source_attempt.paths.root.name != f"{attempt_number:02d}"
            or source_attempt.paths.root.parent.name != storage_component(step_id)
        ):
            raise TrustProtocolError("Agent source staging requires exact original Step attempt")
        strict_execution = expected.execution
        assert isinstance(strict_execution, StrictFleetExecutionAdmission)
        run_id = session.require_fleet_run_id()
        validate_repository_attempt_operation_id(run_id, repository_id, operation_id)
        binding = self._fleet_provisioner.resolve(run_id)
        if type(binding) is not StrictFleetPolicyEnrollment:
            raise TrustProtocolError("original strict Fleet enrollment required for Agent source")
        binding.configured.require_unchanged(binding.current)
        attempt_id = fleet_step_attempt_id(operation_id, step_id, attempt_number)
        # Existing causal journal's attempt_identity shape is a domain-bound
        # sha256 commitment; never accept a caller-authored attempt digest.
        attempt_digest = digest(
            "oh-goon/original-fleet-agent-attempt/v1", attempt_id.encode("ascii")
        )

        def original_cut(connection: sqlite3.Connection) -> int:
            self.require_execution_authority_in(connection, run_id, expected)
            if session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences Agent source prework")
            events = self._journal.read_view_from_connection(connection).run_events(run_id).events
            matches = [
                event
                for event in events
                if isinstance(event, StepAttemptCreatedEvent)
                and event.repository_id == repository_id
                and event.operation_id == operation_id
                and event.step_id == step_id
                and event.attempt_number == attempt_number
                and event.step_attempt_id == attempt_id
            ]
            if len(matches) != 1:
                raise CausalJournalError("original Fleet Step attempt fact absent or ambiguous")
            # A previously terminalized attempt is not eligible for new custody.
            if any(
                event.event_type.value
                in {
                    "STEP_SUCCEEDED",
                    "STEP_FAILED",
                    "STEP_SKIPPED",
                    "STEP_CANCELLED",
                    "RUN_CANCELLED",
                    "RUN_COMPLETED",
                    "RUN_FAILED",
                }
                and (getattr(event, "step_id", step_id) == step_id)
                for event in events
                if event.sequence > matches[0].sequence
            ):
                raise CausalJournalError("original Fleet attempt already terminal")
            journal = CausalRunJournal(connection)
            row = journal._row(run_id)
            if row[2] != session.original_fleet_lease_token():
                raise CausalJournalError("original Agent source lease differs from Fleet owner")

            def stage_after_original_reservation(
                handoff: FrozenAgentStepHandoff, reservation_seq: int
            ) -> None:
                if source_attempt is None:
                    return
                # Original attempt storage owner, not the Resolver, stages the
                # already-decoded source once. No host source path is reopened.
                stage = source_attempt._stage_agent_prework_bytes(handoff)
                # The stage record signs the EXACT no-effect mount proposal.
                # Do not re-resolve source or mint a second mount grant later.
                source_mount = source_attempt._inspect_agent_prework_readonly_mount(handoff, stage)
                self.require_execution_authority_in(connection, run_id, expected)
                if session.cancel_requested_in(connection):
                    raise CausalJournalError("Fleet cancellation changed during Agent stage")
                journal.record_agent_prework_stage_in(
                    run_id,
                    epoch=row[1],
                    lease_identity=row[2],
                    auth=binding.authenticator,
                    reservation_seq=reservation_seq,
                    step_id=step_id,
                    attempt_identity=attempt_digest,
                    stage=stage,
                    source_mount=source_mount,
                )

            result = reserve_verified_agent_source_in(
                journal,
                run_id,
                epoch=row[1],
                lease_identity=row[2],
                auth=binding.authenticator,
                signer_grant=SignerTrustEpochAuthority(connection).active(),
                step_id=step_id,
                attempt_identity=attempt_digest,
                executor_generation=strict_execution.placement.executor_capacity.executor_generation,
                packet=packet,
                expected_handoff_identity=expected_handoff_identity,
                after_reservation=stage_after_original_reservation
                if source_attempt is not None
                else None,
            )
            self.require_execution_authority_in(connection, run_id, expected)
            if session.cancel_requested_in(connection):
                raise CausalJournalError(
                    "Fleet cancellation changed during Agent source reservation"
                )
            return result

        return session.mutate(original_cut)

    def inspect_original_agent_source_mount_for_attempt(
        self,
        session: ExecutionSession,
        expected: FleetExecutionAuthority,
        *,
        repository_id: str,
        operation_id: str,
        step_id: str,
        attempt_number: int,
        source_attempt: "FleetPlanStepAttempt",
        handoff: "FrozenAgentStepHandoff",
    ) -> OriginalMountIntentDescriptor:
        """AG-02B2B3B2: read-only original Fleet cut for a staged mount intent.

        The SAME signed original stage receipt, operational Step attempt, live
        lease, signer and generation must agree before the original attempt
        owner may propose a mount. This is intentionally NOT RESOURCE_INTENT,
        an executable mount grant, physical custody or an Agent launch.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError, digest
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
        from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
        from oh_goon.core.models.run_events import StepAttemptCreatedEvent
        from oh_goon.core.path_authority import storage_component
        from oh_goon.core.ports.original_mount_intent import OriginalMountIntentDescriptor
        from oh_goon.core.product_identity import (
            fleet_step_attempt_id,
            validate_repository_attempt_operation_id,
        )
        from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
        from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
            require_original_admitted_signer_epoch_in,
        )
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority

        if (
            type(session) is not ExecutionSession
            or not self._require_authenticated_fleet
            or type(self._fleet_provisioner) is not FleetPreworkProvisioner
            or not isinstance(expected.execution, StrictFleetExecutionAdmission)
            or expected.execution.placement.isolation_backend.backend_id != "bubblewrap"
            or type(source_attempt) is not FleetPlanStepAttempt
            or type(handoff) is not FrozenAgentStepHandoff
            or handoff.step_id != step_id
            or type(attempt_number) is not int
            or attempt_number < 1
            or source_attempt.attempt_number != attempt_number
            or source_attempt.paths.root.name != f"{attempt_number:02d}"
            or source_attempt.paths.root.parent.name != storage_component(step_id)
        ):
            raise TrustProtocolError("original strict Fleet Agent attempt/stage required")
        handoff.verify()
        strict_execution = expected.execution
        assert isinstance(strict_execution, StrictFleetExecutionAdmission)
        run_id = session.require_fleet_run_id()
        validate_repository_attempt_operation_id(run_id, repository_id, operation_id)
        binding = self._fleet_provisioner.resolve(run_id)
        if type(binding) is not StrictFleetPolicyEnrollment:
            raise TrustProtocolError("original strict Fleet provisioner required")
        binding.configured.require_unchanged(binding.current)
        attempt_id = fleet_step_attempt_id(operation_id, step_id, attempt_number)
        attempt_digest = digest(
            "oh-goon/original-fleet-agent-attempt/v1", attempt_id.encode("ascii")
        )

        def original_cut(connection: sqlite3.Connection) -> OriginalMountIntentDescriptor:
            self.require_execution_authority_in(connection, run_id, expected)
            if session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences Agent source mount intent")
            events = self._journal.read_view_from_connection(connection).run_events(run_id).events
            created = [
                event
                for event in events
                if isinstance(event, StepAttemptCreatedEvent)
                and event.repository_id == repository_id
                and event.operation_id == operation_id
                and event.step_id == step_id
                and event.attempt_number == attempt_number
                and event.step_attempt_id == attempt_id
            ]
            if len(created) != 1:
                raise CausalJournalError("original Step attempt missing or ambiguous")
            if any(
                event.event_type.value
                in {
                    "STEP_SUCCEEDED",
                    "STEP_FAILED",
                    "STEP_SKIPPED",
                    "STEP_CANCELLED",
                    "RUN_CANCELLED",
                    "RUN_COMPLETED",
                    "RUN_FAILED",
                }
                and getattr(event, "step_id", step_id) == step_id
                for event in events
                if event.sequence > created[0].sequence
            ):
                raise CausalJournalError("original Agent attempt already terminal")
            journal = CausalRunJournal(connection)
            row = journal._row(run_id)
            if row[2] != session.original_fleet_lease_token():
                raise CausalJournalError("original Agent mount custody lease mismatch")
            admitted = journal.verify(run_id, auth=binding.authenticator)
            grant = SignerTrustEpochAuthority(connection).active()
            if grant is None:
                raise CausalJournalError("original Fleet signer revoked")
            require_original_admitted_signer_epoch_in(
                journal,
                run_id=run_id,
                auth=binding.authenticator,
                expected=grant,
            )
            reservation = [
                e
                for e in admitted
                if e["kind"] == "AGENT_SOURCE_PREWORK_RESERVED"
                and e["payload"]["binding"]["step_id"] == step_id
                and e["payload"]["binding"]["attempt_identity"] == attempt_digest
            ]
            if len(reservation) != 1:
                raise CausalJournalError("exact original signed Agent source reservation missing")
            reserve = reservation[0]["payload"]["binding"]
            staged = [
                e
                for e in admitted
                if e["kind"] == "AGENT_SOURCE_PREWORK_STAGED"
                and e["payload"]["binding"]["reservation_seq"] == reservation[0]["run_seq"]
            ]
            if (
                len(staged) != 1
                or reserve["handoff_identity"] != handoff.handoff_identity
                or reserve["executor_generation"]
                != strict_execution.placement.executor_capacity.executor_generation
                or reserve["lease_identity"] != row[2]
                or reserve["control_epoch"] != row[1]
                or staged[0]["payload"]["binding"]["attempt_identity"] != attempt_digest
            ):
                raise CausalJournalError("Agent mount proposal differs from signed original stage")
            signed_stage = staged[0]["payload"]["binding"]
            stage = signed_stage["stage"]
            proposal = source_attempt._inspect_agent_prework_readonly_mount(handoff, stage)
            # Pre-B3B3B3 stage receipts had no signed mount proposal: they may
            # be verified historically but must never be upgraded by inference.
            if signed_stage.get("source_mount") != {
                "source": proposal.source,
                "target": proposal.target,
                "sha256": proposal.sha256,
            }:
                raise CausalJournalError("Agent source mount does not match original signed stage")
            result = OriginalMountIntentDescriptor((proposal,), ())
            self.require_execution_authority_in(connection, run_id, expected)
            if session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation changed during mount inspection")
            return result

        return session.mutate(original_cut)

    def local_lace_intent_for(
        self,
        session: ExecutionSession,
        expected: FleetExecutionAuthority,
    ) -> (
        Callable[[ResolvedPlanStep, FleetPlanStepAttempt, int], OriginalFleetLocalLaceReservation]
        | None
    ):
        """Capture one original authenticated Fleet Run authority, not a journal signer.

        A non-authenticated development runner retains its historic behavior.
        Authenticated strict work can proceed only with the native provisioner's
        already-admitted verifier and the exact bound local backend; B3 remote
        authority is separate and must not be silently replaced with Bubblewrap.
        """
        if not self._require_authenticated_fleet:
            return None
        if not isinstance(expected.execution, StrictFleetExecutionAdmission):
            return None
        run_id = session.require_fleet_run_id()
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError
        from oh_goon.runtime.enforcer_lace_fleet_resource_owner import (
            OriginalFleetLocalLaceReservation,
        )

        if (
            type(self._fleet_provisioner) is not FleetPreworkProvisioner
            or expected.execution.placement.isolation_backend.backend_id != "bubblewrap"
        ):
            raise TrustProtocolError(
                "original local LACE provisioner and Bubblewrap required; no remote fallback"
            )
        binding = self._fleet_provisioner.resolve(run_id)
        if type(binding) is not StrictFleetPolicyEnrollment:
            raise TrustProtocolError("original strict Fleet enrollment required for local LACE")
        backend_identity = expected.execution.placement.isolation_backend.configuration_id
        if backend_identity != binding.configured.backend_configuration_identity:
            raise TrustProtocolError("admitted physical backend differs from native enrollment")
        configured = binding.configured

        def for_step(
            step: ResolvedPlanStep, attempt: FleetPlanStepAttempt, ordinal: int
        ) -> OriginalFleetLocalLaceReservation:
            if (
                not isinstance(step.step_id, str)
                or not step.step_id
                or type(attempt.attempt_number) is not int
                or type(ordinal) is not int
                or ordinal < 1
            ):
                raise TrustProtocolError("invalid original local LACE Step/attempt identity")

            def original_agent_attempt_identity_in(connection: sqlite3.Connection) -> str:
                # Same original Fleet Run-event projection as AG-02 prework;
                # never derive agent authority from the requested tool command.
                from oh_goon.core.enforcer_lace_trust import digest
                from oh_goon.core.models.run_events import StepAttemptCreatedEvent
                from oh_goon.core.path_authority import storage_component
                from oh_goon.core.product_identity import (
                    fleet_step_attempt_id,
                    validate_repository_attempt_operation_id,
                )
                from oh_goon.runtime.enforcer_lace_journal import CausalJournalError

                if (
                    attempt.paths.root.name != f"{attempt.attempt_number:02d}"
                    or attempt.paths.root.parent.name != storage_component(step.step_id)
                ):
                    raise CausalJournalError("native Agent attempt root differs from original Step")
                events = (
                    self._journal.read_view_from_connection(connection).run_events(run_id).events
                )
                created = [
                    event
                    for event in events
                    if isinstance(event, StepAttemptCreatedEvent)
                    and event.step_id == step.step_id
                    and event.attempt_number == attempt.attempt_number
                ]
                if len(created) != 1:
                    raise CausalJournalError(
                        "original Agent operational Step attempt missing or ambiguous"
                    )
                fact = created[0]
                validate_repository_attempt_operation_id(
                    run_id, fact.repository_id, fact.operation_id
                )
                attempt_id = fleet_step_attempt_id(
                    fact.operation_id,
                    step.step_id,
                    attempt.attempt_number,
                )
                if fact.step_attempt_id != attempt_id:
                    raise CausalJournalError("original Agent Step attempt identity mismatch")
                if any(
                    event.event_type.value
                    in {
                        "STEP_SUCCEEDED",
                        "STEP_FAILED",
                        "STEP_SKIPPED",
                        "STEP_CANCELLED",
                        "RUN_CANCELLED",
                        "RUN_COMPLETED",
                        "RUN_FAILED",
                    }
                    and getattr(event, "step_id", step.step_id) == step.step_id
                    for event in events
                    if event.sequence > fact.sequence
                ):
                    raise CausalJournalError("original Agent operational attempt is terminal")
                return digest(
                    "oh-goon/original-fleet-agent-attempt/v1",
                    attempt_id.encode("ascii"),
                )

            return OriginalFleetLocalLaceReservation(
                session=session,
                validate_authority_in=lambda connection: self.require_execution_authority_in(
                    connection, run_id, expected
                ),
                authenticator=binding.authenticator,
                run_id=run_id,
                step_id=step.step_id,
                attempt_number=attempt.attempt_number,
                command_ordinal=ordinal,
                attempt_root=attempt.paths.root,
                execution_environment_identity=configured.execution_environment_identity,
                backend_capability_manifest_identity=configured.capability_manifest_identity,
                backend_configuration_identity=backend_identity,
                agent_workload=getattr(step, "kind", None) == "agent",
                agent_attempt_identity_in=(
                    original_agent_attempt_identity_in
                    if getattr(step, "kind", None) == "agent"
                    else None
                ),
            )

        return for_step

    def existing_owned_local_lace_ticket(self, run_id: str) -> ExecutionTicket:
        """Read the original scheduler lease, never admit or transfer a new owner.

        After a real restart the old process/session cannot satisfy this cut. An
        independently authorised durable transfer is still required; this API
        cannot promote an abandoned or foreign lease into recovery authority.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError
        from oh_goon.core.errors import OwnershipLost

        if type(run_id) is not str or not run_id or not self._require_authenticated_fleet:
            raise TrustProtocolError("original authenticated Fleet run required")
        with self._database.transaction(write=True) as connection:
            ticket = self._execution.owned_fleet_ticket_in(connection, run_id)
            if ticket is None:
                raise OwnershipLost(
                    "no live original Fleet owner; post-restart transfer not established"
                )
            expected = _execution_authority_in(
                connection, self._journal, self._executor_pool, run_id
            )
            if (
                not isinstance(expected.execution, StrictFleetExecutionAdmission)
                or expected.execution.placement.isolation_backend.backend_id != "bubblewrap"
            ):
                raise TrustProtocolError("original local Bubblewrap recovery admission missing")
            return ticket

    def original_local_lace_recovery_backend(
        self, expected: FleetExecutionAuthority
    ) -> ProcessIsolationBackend:
        """Obtain recovery observation config from the original executor inventory.

        Authority is checked by ``local_lace_recovery_owner`` through its
        session transaction. This method only returns the exact installed backend
        and cannot grant a new lease or sign an admission.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError

        if not isinstance(expected.execution, StrictFleetExecutionAdmission):
            raise TrustProtocolError("strict original Fleet placement required")
        return self._executor_pool.original_local_lace_recovery_backend(
            expected.execution.placement
        )

    def local_lace_recovery_owner(
        self,
        session: ExecutionSession,
        expected: FleetExecutionAuthority,
    ) -> OriginalFleetLocalLaceRecoveryOwner:
        """Bind recovery to this *already activated* original Fleet owner.

        No startup signer, fabricated takeover lease or new admission is created.
        The original transaction verifies the complete admitted placement,
        enrolment, live execution lease, and cancellation before returning a
        recovery-only owner. The owner repeats these checks for every receipt.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError
        from oh_goon.runtime.enforcer_lace_fleet_resource_owner import (
            OriginalFleetLocalLaceRecoveryOwner,
        )
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

        if type(session) is not ExecutionSession or type(expected) is not FleetExecutionAuthority:
            raise TrustProtocolError("original activated Fleet recovery session required")
        if (
            not self._require_authenticated_fleet
            or type(self._fleet_provisioner) is not FleetPreworkProvisioner
        ):
            raise TrustProtocolError("original enrolled Fleet recovery provisioner required")
        if not isinstance(expected.execution, StrictFleetExecutionAdmission):
            raise TrustProtocolError(
                "strict admitted Fleet placement required for local LACE recovery"
            )
        if expected.execution.placement.isolation_backend.backend_id != "bubblewrap":
            raise TrustProtocolError("remote LACE cannot be recovered by local Bubblewrap owner")
        run_id = session.require_fleet_run_id()
        provisioner = self._fleet_provisioner
        assert isinstance(provisioner, FleetPreworkProvisioner)
        binding = provisioner.resolve(run_id)
        if type(binding) is not StrictFleetPolicyEnrollment:
            raise TrustProtocolError("original strict Fleet policy enrollment required")
        binding.configured.require_unchanged(binding.current)
        if (
            binding.configured.backend_configuration_identity
            != expected.execution.placement.isolation_backend.configuration_id
        ):
            raise TrustProtocolError(
                "local recovery backend differs from admitted physical backend"
            )

        def verify(connection: sqlite3.Connection) -> None:
            self.require_execution_authority_in(connection, run_id, expected)
            if session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences local LACE recovery")
            journal = CausalRunJournal(connection)
            row = journal._row(run_id)
            journal._check_fence(
                row, epoch=row[1], lease_identity=session.original_fleet_lease_token()
            )
            if session.recovery_only:
                # B8: revocation is not just a one-time transfer gate. An
                # activated post-restart custodian must repeat the signed
                # admitted-epoch/native provisioning fence before AND after
                # every claim, observation and cleanup-adjacent transaction.
                from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
                    SignerTrustEpochAuthority,
                )
                from oh_goon.services.fleet_recovery_issuer_fence import (
                    require_original_restart_issuer_in,
                )

                grant = SignerTrustEpochAuthority(connection).active()
                require_original_restart_issuer_in(
                    connection,
                    provisioner=provisioner,
                    binding=binding,
                    run_id=run_id,
                    grant=grant,
                )

        # Generation comes from the admitted placement, never the recovery caller.
        generation = expected.execution.placement.executor_capacity.executor_generation
        if type(generation) is not int or generation < 1:
            raise CausalJournalError("original recovery executor generation unavailable")
        issuer = binding.authenticator.key_id
        if type(issuer) is not str or not issuer:
            raise CausalJournalError("original recovery issuer identity unavailable")
        (session.mutate_recovery if session.recovery_only else session.mutate)(verify)
        return OriginalFleetLocalLaceRecoveryOwner(
            session=session,
            validate_authority_in=verify,
            authenticator=binding.authenticator,
            run_id=run_id,
            admitted_executor_generation=generation,
            admitted_issuer_key_id=issuer,
        )

    def transfer_original_dead_fleet_recovery(
        self,
        *,
        run_id: str,
        predecessor_token: str,
        expected_epoch: int,
    ) -> ExecutionTicket:
        """Original strict scheduler + original issuer + causal journal in one cut.

        Explicit worker recovery only; never an HTTP/CLI ingress or startup scan.
        This transfers cleanup-only custody, NOT executable Step authority.
        """
        from oh_goon.core.enforcer_lace_trust import TrustProtocolError
        from oh_goon.core.errors import OwnershipLost
        from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
        from oh_goon.runtime.enforcer_lace_restart_transfer import (
            commit_original_restart_transfer_in,
        )
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority
        from oh_goon.services.fleet_recovery_issuer_fence import require_original_restart_issuer_in

        if (
            not self._require_authenticated_fleet
            or type(self._fleet_provisioner) is not FleetPreworkProvisioner
        ):
            raise TrustProtocolError("original provisioned Fleet issuer required for transfer")
        binding = self._fleet_provisioner.resolve(run_id)
        if type(binding) is not StrictFleetPolicyEnrollment:
            raise TrustProtocolError("original strict Fleet enrollment required for transfer")
        binding.configured.require_unchanged(binding.current)
        with self._database.transaction(write=True) as connection:
            # This is the same durable epoch authority used by original Fleet
            # RUN_ADMITTED. A fixture signer or an unenrolled deployment cannot
            # acquire production recovery custody. No implicit enrollment here.
            grant = SignerTrustEpochAuthority(connection).active()
            require_original_restart_issuer_in(
                connection,
                provisioner=self._fleet_provisioner,
                binding=binding,
                run_id=run_id,
                grant=grant,
            )
            expected = _execution_authority_in(
                connection, self._journal, self._executor_pool, run_id
            )
            if (
                not isinstance(expected.execution, StrictFleetExecutionAdmission)
                or expected.execution.placement.isolation_backend.backend_id != "bubblewrap"
                or binding.configured.backend_configuration_identity
                != expected.execution.placement.isolation_backend.configuration_id
            ):
                raise TrustProtocolError("original immutable Bubblewrap placement required")
            detail = self._journal.read_view_from_connection(connection).required_run_detail(run_id)
            if detail.run.status.terminal or detail.run.status.cancel_request_satisfied:
                raise OwnershipLost("original Fleet run already terminal or cancelled")
            queued = connection.execute(
                "SELECT cancellation_requested FROM executor_placement_queue WHERE fleet_run_id=?",
                (run_id,),
            ).fetchone()
            if queued is not None and queued[0] != 0:
                raise OwnershipLost("original scheduler cancellation fences recovery")
            journal = CausalRunJournal(connection)
            ticket = commit_original_restart_transfer_in(
                journal=journal,
                coordinator=self._execution,
                run_id=run_id,
                predecessor_token=predecessor_token,
                expected_epoch=expected_epoch,
                auth=binding.authenticator,
                admitted_generation=expected.execution.placement.executor_capacity.executor_generation,
                admitted_trust_epoch=expected.execution.placement.executor_trust_epoch,
                admitted_issuer_key_id=binding.authenticator.key_id,
            )
            # The original transaction still owns the lease, causal event and
            # signer-revocation row. Reobserve native policy, six public roots,
            # and executor inventory after all provisional mutation. Any drift
            # rolls back the entire signed custody transfer.
            require_original_restart_issuer_in(
                connection,
                provisioner=self._fleet_provisioner,
                binding=binding,
                run_id=run_id,
                grant=grant,
            )
            return ticket

    def activate_recovery(self, ticket: ExecutionTicket) -> ExecutionSession:
        return self._execution.activate_recovery(ticket)

    def activate(self, ticket: ExecutionTicket) -> ExecutionSession:
        """Materialize and validate the exact admitted Fleet owner."""
        session = self._execution.activate(ticket)
        session.require_fleet_run_id()
        return session

    def execution_authority(
        self,
        session: ExecutionSession,
    ) -> FleetExecutionAuthority:
        """Read lease, admitted run, trust, and capacity from one SQLite authority cut."""
        fleet_run_id = session.require_fleet_run_id()
        return session.observe(
            lambda connection: _execution_authority_in(
                connection, self._journal, self._executor_pool, fleet_run_id
            )
        )

    def require_execution_authority_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        expected: FleetExecutionAuthority,
    ) -> None:
        """Revalidate one admitted execution inside an already owner-bound transaction."""
        current = _execution_authority_in(
            connection, self._journal, self._executor_pool, fleet_run_id
        )
        if current != expected:
            raise StateConflict(
                f"Fleet execution authority changed after admission: {fleet_run_id}"
            )
        # CO-R3P: the existing Fleet resource-intent/physical-effect callback
        # uses this exact original write cut. Placement equality cannot detect
        # an administrative signer revocation: the immutable admitted placement
        # is unchanged when the single durable trust epoch is revoked. Reprove
        # the original signed Run epoch against that SAME SQLite row before the
        # reservation and after its provisional journal mutation. No new epoch
        # owner, credential, trust cache, or physical-cleanup permission exists.
        if self._require_authenticated_fleet and isinstance(
            expected.execution, StrictFleetExecutionAdmission
        ):
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError
            from oh_goon.native_issuer.enforcer_lace_signer_custody import (
                deployment_signer_policy_snapshot,
            )
            from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
                require_original_admitted_signer_epoch_in,
            )
            from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
            from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
                SignerTrustEpochAuthority,
            )

            if type(self._fleet_provisioner) is not FleetPreworkProvisioner:
                raise TrustProtocolError(
                    "original strict Fleet issuer provisioner required at effect cut"
                )
            binding = self._fleet_provisioner.resolve(fleet_run_id)
            if type(binding) is not StrictFleetPolicyEnrollment:
                raise TrustProtocolError(
                    "original strict Fleet signer enrollment required at effect cut"
                )
            binding.configured.require_unchanged(binding.current)
            roots = binding.trusted_public_roots
            grant = SignerTrustEpochAuthority(connection).active()
            SignerTrustEpochAuthority(connection).require_in(
                connection,
                grant=grant,
                policy_snapshot=deployment_signer_policy_snapshot(
                    key_directory=binding.key_directory,
                    trusted_public_roots=roots,
                ),
                roots=roots,
            )
            require_original_admitted_signer_epoch_in(
                CausalRunJournal(connection),
                run_id=fleet_run_id,
                auth=binding.authenticator,
                expected=grant,
            )

    def begin(self, session: ExecutionSession) -> FleetRunState:
        """Start the session-owned Fleet run and replay only its exact lost commit response."""
        return _begin_owned_run(self._journal, session)

    def cancel(self, fleet_run_id: str) -> FleetRunState:
        """Cancel atomically and replay canonical cancellation after an ambiguous commit response."""
        try:
            with self._database.transaction(write=True) as connection:
                detail = self._journal.read_view_from_connection(connection).required_run_detail(
                    fleet_run_id
                )
                state = detail.run
                if state.status.cancel_request_satisfied:
                    return state
                if state.status.terminal:
                    raise InvalidRunTransition(f"Fleet run is already terminal: {fleet_run_id}")

                # Recovery-only custody is not a normal executable Fleet owner.
                # A generic cancel would mutate its protected cleanup lease and
                # deny the original signer the ability to prove teardown.
                custody = connection.execute(
                    "SELECT owner_session_id FROM execution_lease "
                    "WHERE singleton=1 AND fleet_run_id=?",
                    (fleet_run_id,),
                ).fetchone()
                if custody is not None and str(custody[0]).startswith("recovery:"):
                    raise StateConflict(
                        "original recovery-only LACE custody cannot be cancelled by generic Fleet writer"
                    )
                # A live owner can receive ordinary cancellation. For a dead
                # owner, the generic cancel path would erase the original lease;
                # preserve signed LACE resource custody before that mutation.
                if self._execution.active_lease_in(connection) is None:
                    require_no_original_lace_intent_before_reclamation_in(
                        connection,
                        fleet_run_id,
                    )
                live_owner = self._execution.request_fleet_cancel_in(connection, fleet_run_id)
                if live_owner:
                    return self._journal.request_cancel_in(connection, fleet_run_id)
                state = self._journal.terminalize_run_in(
                    connection,
                    fleet_run_id,
                    FleetRunTerminalIntent.CANCEL,
                )
                self._executor_pool.release_run_in(connection, fleet_run_id)
        except BaseException:
            failed_detail = self._journal.read_view().run_detail(fleet_run_id)
            if failed_detail is None or not failed_detail.run.status.cancel_request_satisfied:
                raise
            return failed_detail.run
        return state

    def finish(
        self,
        session: ExecutionSession,
        intent: FleetRunTerminalIntent,
        failure: FleetRunFailure | None = None,
    ) -> FleetRunState:
        return self.finish_detail(session, intent, failure).run

    def finish_detail(
        self,
        session: ExecutionSession,
        intent: FleetRunTerminalIntent,
        failure: FleetRunFailure | None = None,
    ) -> FleetRunDetail:
        """Commit terminal state and return its canonical projection in the same DB scope."""
        return _finish_owned_run_detail(self, session, intent, failure)

    def _terminalize_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: FleetRunTerminalIntent,
        failure: FleetRunFailure | None,
    ) -> FleetRunState:
        state = self._journal.terminalize_run_in(connection, fleet_run_id, intent, failure)
        self._executor_pool.release_run_in(connection, fleet_run_id)
        return state

    def finish_admitted(
        self,
        ticket: ExecutionTicket,
        intent: FleetRunTerminalIntent,
        failure: FleetRunFailure | None = None,
    ) -> FleetRunState:
        return self.finish(self.activate(ticket), intent, failure)


def _execution_authority_in(
    connection: sqlite3.Connection,
    journal: Journal,
    executor_pool: FleetExecutorPool,
    fleet_run_id: str,
) -> FleetExecutionAuthority:
    detail = journal.read_view_from_connection(connection).required_run_detail(fleet_run_id)
    execution = detail.execution
    if isinstance(execution, StrictFleetExecutionAdmission):
        if not isinstance(detail.run.target, FleetPlanTarget):
            raise FleetPlanInvalid(
                "Strict executor admission cannot be revalidated without Fleet-plan evidence"
            )
        executor_pool.require_bound_in(connection, execution.placement, detail.run.target.plan)
    return FleetExecutionAuthority(
        capability=detail.run.capability,
        target=detail.run.target,
        repository_scope=detail.run.repository_scope,
        targets=detail.run.targets,
        execution=execution,
        repository_admissions=detail.repository_admissions,
        admitted_run=detail,
    )


def _finish_owned_run_detail(
    lifecycle: FleetRunLifecycle,
    session: ExecutionSession,
    intent: FleetRunTerminalIntent,
    failure: FleetRunFailure | None,
) -> FleetRunDetail:
    """Return only the exact terminal projection produced by this owner transaction."""
    fleet_run_id = session.require_fleet_run_id()
    intended: FleetRunDetail | None = None

    def settle(connection: sqlite3.Connection) -> FleetRunDetail:
        nonlocal intended
        state = lifecycle._terminalize_in(connection, fleet_run_id, intent, failure)
        intended = lifecycle._journal.read_view_from_connection(connection).required_run_detail(
            fleet_run_id
        )
        if intended.run != state:
            raise JournalConflict("terminal FleetRun projection does not match committed state")
        return intended

    try:
        return session.finish(settle)
    except BaseException:
        if intended is None:
            raise
        detail = lifecycle._journal.read_view().run_detail(fleet_run_id)
        if detail != intended:
            raise
        return detail


def _begin_owned_run(journal: Journal, session: ExecutionSession) -> FleetRunState:
    fleet_run_id = session.require_fleet_run_id()
    intended: FleetRunState | None = None

    def start(connection: sqlite3.Connection) -> FleetRunState:
        nonlocal intended
        intended = _start_unless_cancelled_in(journal, connection, fleet_run_id)
        return intended

    try:
        return session.mutate(start)
    except BaseException as error:
        if intended is None:
            raise
        try:
            session.assert_fleet_owner(fleet_run_id)
            detail = journal.read_view().run_detail(fleet_run_id)
        except BaseException as verify_error:
            raise error from verify_error
        if detail is None or detail.run != intended:
            raise
        return detail.run


def _replay_admission(
    database: LocalDatabase,
    journal: Journal,
    execution: ExecutionCoordinator,
    executor_pool: FleetExecutorPool,
    fleet_run_id: str,
    intent: ResolvedCapabilityIntent,
    repository_scope: FleetRepositoryScope,
    targets: list[RepositoryIdentity],
    source: FleetRunSource,
    readmitted_from_run_id: str | None,
    rerun_from_run_id: str | None,
    *,
    strict: bool,
) -> tuple[ExecutionTicket, FleetRunState] | None:
    """Replay only an exact admission bundle owned by this runtime session."""
    with database.transaction() as connection:
        detail = journal.read_view_from_connection(connection).run_detail(fleet_run_id)
        if detail is None:
            return None
        try:
            require_fleet_run_detail_binding(
                detail,
                source=source,
                capability=intent.definition.durable_snapshot(),
                target=intent.target,
                repository_scope=repository_scope,
                readmitted_from_run_id=readmitted_from_run_id,
                rerun_from_run_id=rerun_from_run_id,
            )
        except JournalConflict:
            return None
        if detail.run.targets != tuple(targets):
            return None
        if strict:
            if not isinstance(detail.execution, StrictFleetExecutionAdmission):
                return None
            if not executor_pool.reservation_matches_in(connection, detail.execution.placement):
                return None
        elif not isinstance(detail.execution, DirectFleetExecutionAdmission):
            return None
        ticket = execution.owned_fleet_ticket_in(connection, fleet_run_id)
        return (ticket, detail.run) if ticket is not None else None


def _start_unless_cancelled_in(
    journal: Journal, connection: sqlite3.Connection, fleet_run_id: str
) -> FleetRunState:
    detail = journal.read_view_from_connection(connection).required_run_detail(fleet_run_id)
    if detail.run.status.cancel_request_satisfied or detail.run.status.terminal:
        return detail.run
    return journal.start_run_in(connection, fleet_run_id)


def _require_direct_priority_supported(requested_priority: SchedulingPriority) -> None:
    if requested_priority is not SchedulingPriority.NORMAL:
        raise FleetPlanInvalid(
            "Priority scheduling is only supported for queued strict CI/CD admission; "
            "direct execution cannot silently ignore requested priority"
        )
