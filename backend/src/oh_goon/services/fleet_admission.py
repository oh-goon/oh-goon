from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.enforcer_lace_issuer_evidence import IssuerEvidence, TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract, AuthenticatedReceipt, ReceiptAuthenticator, TrustProtocolError,
)
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.errors import (
    CapabilityUnavailable,
    ExecutionBusy,
    FleetPlanInvalid,
    TargetSelectionError,
)
from oh_goon.core.vocabulary import CommandSafety
from oh_goon.runtime.safety_cut import ConsistentRuntimeSafetyCut, RuntimeSafetyCut
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
from oh_goon.runtime.journal import Journal
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    SignerTrustEpochAuthority, SignerTrustGrant, identity_for_policy, identity_for_roots,
)
from oh_goon.native_issuer.enforcer_lace_signer_custody import deployment_signer_policy_snapshot
from pathlib import Path
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease, SQLiteExecutionLeaseStore
from oh_goon.core.models.capability import (
    CapabilityRequirement,
    CapabilityRequirementCondition,
    CapabilityRequirementTarget,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run_admission import (
    FleetRepositoryScope,
    FleetRunAdmissionIntent,
    PlanRepositoryAdmissionEvidence,
    fleet_readmission_parent,
    fleet_rerun_parent,
)
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.runtime.state import LocalState, LocalStateReadView
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.target_selection import FleetTargetSelector


if TYPE_CHECKING:
    from oh_goon.core.path_authority import StrictHostPathAuthority
    from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
    from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment
    from oh_goon.services.fleet_native_run_contract_compiler import DeploymentRunContractPolicy


@dataclass(frozen=True, slots=True)
class FleetAdmissionPlan:
    """Targets and safety epoch derived from one durable admission observation."""

    durable_revision: int
    targets: tuple[IncludedRepositoryCatalogEntry, ...]
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]


_PublicationResult = TypeVar("_PublicationResult")


class FleetAdmissionPlanner:
    """Own Fleet target eligibility, prerequisites, and revision-bound safety admission."""

    @staticmethod
    def require_fresh_native_admission(intent: FleetRunAdmissionIntent) -> None:
        """M192: one original-owner replay fence at every native atomic ingress.

        Authenticated parent/child lineage needs a dedicated native issuer and
        one proven Journal/signer/lease cutover. Neither an already signed
        contract nor a reserved lease can substitute for that missing proof.
        """
        if not isinstance(intent, FleetRunAdmissionIntent):
            raise TrustProtocolError("canonical Fleet native prework intent required")
        if (fleet_readmission_parent(intent.execution) is not None
                or fleet_rerun_parent(intent.execution) is not None):
            raise TrustProtocolError(
                "authenticated replay requires a dedicated native lineage issuer"
            )

    def __init__(
        self,
        state: LocalState,
        selectors: FleetTargetSelector,
        auditor: SystemInvariantAuditor,
        runtime_safety: RuntimeSafetyService,
    ) -> None:
        self._state = state
        self._selectors = selectors
        self._auditor = auditor
        self._runtime_safety = runtime_safety

    @staticmethod
    def admit_runtime_cut(
        runtime_safety: RuntimeSafetyService,
        *,
        cut: RuntimeSafetyCut | None = None,
    ) -> ConsistentRuntimeSafetyCut:
        """Enforcer's single common revision-bound safety proof for all effect modes."""
        return (
            runtime_safety.admit_effect_cut()
            if cut is None
            else runtime_safety.admit_cut(cut)
        )

    @staticmethod
    def require_effects_for_cut(
        runtime_safety: RuntimeSafetyService,
        cut: ConsistentRuntimeSafetyCut,
        *,
        safety: CommandSafety,
        repository_ids: tuple[str, ...],
    ) -> None:
        """Use the *same* admitted cut for scoped repository quarantine proof."""
        runtime_safety.require_repository_effects_allowed(
            cut, safety=safety, repository_ids=repository_ids,
        )

    @classmethod
    def admit_repository_effect_cut(
        cls,
        runtime_safety: RuntimeSafetyService,
        *,
        safety: CommandSafety,
        repository_ids: tuple[str, ...],
    ) -> ConsistentRuntimeSafetyCut:
        """Direct effect admission through the same Enforcer owner as Fleet.

        Direct's post-proof CAS reservation and replay identity remain separate
        operational responsibilities; no Plan/backend containment is implied.
        """
        admitted = cls.admit_runtime_cut(runtime_safety)
        cls.require_effects_for_cut(
            runtime_safety, admitted, safety=safety, repository_ids=repository_ids,
        )
        return admitted

    @staticmethod
    def require_exact_contract_environment(
        contract: AdmittedRunContract,
        *,
        configured: ResolvedExecutionEnvironment,
        current: ResolvedExecutionEnvironment,
    ) -> None:
        """Check the M147→M148 identity bridge *before* any authenticated publication.

        This is a pure guard, not admission or attestation. A caller cannot use it
        to prove a backend capable, to create an admitted Run, or to start work.
        Phase 4's live cut still requires authenticated capability and lease
        authorities that are not available here.
        """
        contract.verify()
        configured.require_unchanged(current)
        fields = contract.fields
        if (
            fields["execution_environment_identity"] != configured.execution_environment_identity
            or fields["backend_capability_manifest_identity"] != configured.capability_manifest_identity
            or fields["executor_generation"] != configured.executor_generation
        ):
            raise TrustProtocolError(
                "AdmittedRunContract does not bind the exact configured backend, "
                "capability identity and executor generation"
            )

    @staticmethod
    def prepare_fleet_prework_capture_in(
        *, journal: Journal, connection: sqlite3.Connection,
        run_id: str, intent: FleetRunAdmissionIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> RunAdmittedEvent:
        """M166 fail-before-work Fleet candidate from the sole canonical Journal.

        No durable mutation, issuer signature, control-lease acquisition, or
        physical capability is implied. The M167 trusted prework adapter may
        consume this candidate without a committed Run, and M168 can publish
        the same event atomically. Never fall back to old committed-Run issuer
        mode when native pre-work proof is unavailable.
        """
        if not isinstance(journal, Journal):
            raise TrustProtocolError("canonical operational Fleet Journal owner required")
        FleetAdmissionPlanner.require_fresh_native_admission(intent)
        candidate = journal.prepare_run_admission_in(
            connection, run_id, intent, repository_scope, targets,
        )
        if not isinstance(candidate.target, FleetPlanTarget) or len(candidate.repository_admissions) < 2:
            raise TrustProtocolError("pre-work issuer scope requires exact multi-repository Fleet Plan")
        return candidate

    @classmethod
    def publish_compiled_strict_fleet_in(
        cls, *, connection: sqlite3.Connection, execution: "ExecutionCoordinator",
        operational_journal: Journal, binding: "StrictFleetPolicyEnrollment",
        intent: FleetRunAdmissionIntent, repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity], fleet_run_id: str,
    ) -> "FleetRunState":
        """M176: publish ONLY inside the original strict executor placement cut.

        The scheduler has already reserved its placement and consumed its queue
        ticket in this write transaction; the original coordinator has already
        reserved its lease. No new transaction, selection, signer or fallback.
        """
        from oh_goon.runtime.execution import ExecutionCoordinator
        from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment
        from oh_goon.services.fleet_native_run_contract_compiler import compile_native_prework_run_contract
        from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
        cls.require_fresh_native_admission(intent)
        if (type(binding) is not StrictFleetPolicyEnrollment
                or not isinstance(execution, ExecutionCoordinator)
                or not isinstance(operational_journal, Journal)):
            raise TrustProtocolError("original strict Fleet admission owners required")
        # Do not use an alternative database connection (or a second BEGIN).
        if not connection.in_transaction:
            raise TrustProtocolError("strict placement must own one active write transaction")
        lease = execution.active_lease_in(connection)
        if lease is None or lease.kind != "FLEET" or lease.fleet_run_id != fleet_run_id:
            raise TrustProtocolError("strict placement does not own original Fleet lease")
        binding.require_matching(
            run_id=fleet_run_id, intent=intent, scope=repository_scope, targets=targets,
        )
        capture = PreworkRunCapture.capture(
            journal=operational_journal, connection=connection,
            run_id=fleet_run_id, intent=intent,
            repository_scope=repository_scope, targets=targets,
        )
        contract = compile_native_prework_run_contract(
            prework=capture, deployment_policy=binding.deployment_policy,
            path_authority=binding.path_authority,
            control_state_root=binding.control_state_root,
        )
        if (contract.fields["run_id"] != fleet_run_id
                or contract.fields["executor_generation"] != binding.configured.executor_generation
                or contract.fields["execution_environment_identity"]
                   != binding.configured.execution_environment_identity
                or contract.fields["backend_capability_manifest_identity"]
                   != binding.configured.capability_manifest_identity):
            raise TrustProtocolError("strict native Run contract differs from original executor selection")
        cls.require_exact_contract_environment(
            contract, configured=binding.configured, current=binding.current,
        )
        return cls.publish_atomic_fleet_prework_ingress(
            operational_journal=operational_journal,
            causal_journal=CausalRunJournal(connection),
            lease_store=execution._leases, lease=lease,
            signer_authority=SignerTrustEpochAuthority(connection),
            contract=contract, authenticator=binding.authenticator,
            configured=binding.configured, current=binding.current,
            intent=intent, repository_scope=repository_scope, targets=targets,
            path_authority=binding.path_authority, key_directory=binding.key_directory,
            trusted_public_roots=binding.trusted_public_roots,
            control_state_root=binding.control_state_root,
            original_reservation_connection=connection,
            deployment_policy=binding.deployment_policy,
        )

    @classmethod
    def reserve_atomic_fleet_policy_ingress(
        cls, *, execution: "ExecutionCoordinator", expected_durable_revision: int,
        operational_journal: Journal, run_id: str,
        deployment_policy: "DeploymentRunContractPolicy",
        authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment, current: ResolvedExecutionEnvironment,
        intent: FleetRunAdmissionIntent, repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        path_authority: "StrictHostPathAuthority", key_directory: Path,
        trusted_public_roots: "Mapping[str, tuple[str, bytes]]",
        control_state_root: Path | None = None,
    ) -> "tuple[ExecutionTicket, FleetRunState]":
        """M174: derive the contract in the original lease transaction, never before.

        Preplanning enrollment contains Run, explicit Plan scope, policy and key
        authority, NOT caller-provided native identity hashes. Reuse M168's sole
        signed operational+causal publication; no nested transaction or fallback.
        """
        from oh_goon.runtime.execution import ExecutionCoordinator
        from oh_goon.services.fleet_native_run_contract_compiler import (
            DeploymentRunContractPolicy, compile_native_prework_run_contract,
        )
        from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
        cls.require_fresh_native_admission(intent)
        if (not isinstance(execution, ExecutionCoordinator)
                or type(run_id) is not str or not run_id
                or type(deployment_policy) is not DeploymentRunContractPolicy):
            raise TrustProtocolError("original execution owner and frozen Fleet policy required")
        deployment_policy.read()
        configured.require_unchanged(current)

        def admit_in(connection: sqlite3.Connection) -> FleetRunState:
            lease = execution.active_lease_in(connection)
            if lease is None or lease.kind != "FLEET" or lease.fleet_run_id != run_id:
                raise TrustProtocolError("original Fleet reservation must own exact policy Run")
            capture = PreworkRunCapture.capture(
                journal=operational_journal, connection=connection,
                run_id=run_id, intent=intent, repository_scope=repository_scope,
                targets=targets,
            )
            contract = compile_native_prework_run_contract(
                prework=capture, deployment_policy=deployment_policy,
                path_authority=path_authority, control_state_root=control_state_root,
            )
            if contract.fields["run_id"] != run_id:
                raise TrustProtocolError("native compiler selected wrong authorized Fleet Run")
            cls.require_exact_contract_environment(
                contract, configured=configured, current=current,
            )
            return cls.publish_atomic_fleet_prework_ingress(
                operational_journal=operational_journal,
                causal_journal=CausalRunJournal(connection),
                lease_store=execution._leases, lease=lease,
                signer_authority=SignerTrustEpochAuthority(connection),
                contract=contract, authenticator=authenticator,
                configured=configured, current=current,
                intent=intent, repository_scope=repository_scope, targets=targets,
                path_authority=path_authority, key_directory=key_directory,
                trusted_public_roots=trusted_public_roots,
                control_state_root=control_state_root,
                original_reservation_connection=connection,
                deployment_policy=deployment_policy,
            )

        return execution.reserve_fleet_with(
            run_id, expected_durable_revision=expected_durable_revision,
            mutate=admit_in,
        )

    @classmethod
    def reserve_atomic_fleet_prework_ingress(
        cls, *, execution: "ExecutionCoordinator", expected_durable_revision: int,
        operational_journal: Journal,
        contract: AdmittedRunContract, authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment, current: ResolvedExecutionEnvironment,
        intent: FleetRunAdmissionIntent, repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        path_authority: "StrictHostPathAuthority", key_directory: Path,
        trusted_public_roots: "Mapping[str, tuple[str, bytes]]",
        control_state_root: Path | None = None,
        deployment_policy: "DeploymentRunContractPolicy | None" = None,
    ) -> "tuple[ExecutionTicket, FleetRunState]":
        """M169 opt-in Fleet execution-owner admission; **not** live-wired.

        The original ExecutionCoordinator allocates its actual lease and publishes
        both Run journals in its *one* reservation transaction. No separate
        pre-acquired lease, second BEGIN, external caller writer or post-commit
        signature is allowed. Production composition/contract construction is
        still a separate, unqualified ingress migration.
        """
        from oh_goon.runtime.execution import ExecutionCoordinator
        cls.require_fresh_native_admission(intent)
        if not isinstance(execution, ExecutionCoordinator):
            raise TrustProtocolError("original Fleet execution reservation owner required")
        cls.require_exact_contract_environment(
            contract, configured=configured, current=current,
        )
        run_id = contract.fields["run_id"]

        def admit_in(connection: sqlite3.Connection) -> FleetRunState:
            lease = execution.active_lease_in(connection)
            if lease is None or lease.kind != "FLEET" or lease.fleet_run_id != run_id:
                raise TrustProtocolError("Fleet reservation did not create the exact Run lease")
            return cls.publish_atomic_fleet_prework_ingress(
                operational_journal=operational_journal,
                causal_journal=CausalRunJournal(connection),
                lease_store=execution._leases, lease=lease,
                signer_authority=SignerTrustEpochAuthority(connection),
                contract=contract, authenticator=authenticator,
                configured=configured, current=current,
                intent=intent, repository_scope=repository_scope, targets=targets,
                path_authority=path_authority, key_directory=key_directory,
                trusted_public_roots=trusted_public_roots,
                control_state_root=control_state_root,
                original_reservation_connection=connection,
                deployment_policy=deployment_policy,
            )

        return execution.reserve_fleet_with(
            run_id, expected_durable_revision=expected_durable_revision,
            mutate=admit_in,
        )

    @classmethod
    def publish_atomic_fleet_prework_ingress(
        cls, *, operational_journal: Journal, causal_journal: CausalRunJournal,
        lease_store: SQLiteExecutionLeaseStore, lease: ExecutionLease,
        signer_authority: SignerTrustEpochAuthority,
        contract: AdmittedRunContract, authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment, current: ResolvedExecutionEnvironment,
        intent: FleetRunAdmissionIntent, repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
        path_authority: "StrictHostPathAuthority", key_directory: Path,
        trusted_public_roots: "Mapping[str, tuple[str, bytes]]",
        control_state_root: Path | None = None,
        original_reservation_connection: sqlite3.Connection | None = None,
        deployment_policy: "DeploymentRunContractPolicy | None" = None,
    ) -> "FleetRunState":
        """M168 opt-in single-transaction Fleet cut; never starts physical work.

        Prework issuer observation, signature verification, operational admission,
        causal admission and the final lease/trust fences share the SAME SQLite
        BEGIN IMMEDIATE. An issuer cannot require a previously committed Run.
        Neither the Run nor a caller provides issuer callbacks or admission writes.
        This is not yet wired to any production executable entrypoint.
        """
        from collections.abc import Mapping
        from oh_goon.core.path_authority import StrictHostPathAuthority
        from oh_goon.core.enforcer_lace_trust import admission_receipt
        from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
        from oh_goon.native_issuer.enforcer_lace_native_issuer_suite import (
            issue_fixed_prework_native_evidence_with_signer_grant,
        )
        from oh_goon.native_issuer.enforcer_lace_signer_custody import (
            deployment_signer_policy_snapshot,
        )

        # M192: the direct atomic issuer API can be called with a precompiled
        # contract and a pre-existing lease; deny replay before receipt/signing.
        cls.require_fresh_native_admission(intent)

        if (not isinstance(operational_journal, Journal)
                or not isinstance(causal_journal, CausalRunJournal)
                or not isinstance(lease_store, SQLiteExecutionLeaseStore)
                or not isinstance(lease, ExecutionLease)
                or not isinstance(signer_authority, SignerTrustEpochAuthority)
                or not isinstance(path_authority, StrictHostPathAuthority)
                or not isinstance(key_directory, Path) or not key_directory.is_absolute()
                or not isinstance(trusted_public_roots, Mapping)
                or (control_state_root is not None and not isinstance(control_state_root, Path))):
            raise TrustProtocolError("fixed original Fleet issuer, lease and admission owners required")
        connection = causal_journal.connection
        if (signer_authority.connection is not connection
                or lease_store._database is not operational_journal._database):
            raise TrustProtocolError("Fleet, signer and causal authorities must use one original database")
        run_id = contract.fields["run_id"]
        if lease.kind != "FLEET" or lease.fleet_run_id != run_id or not lease.token:
            raise TrustProtocolError("original Fleet control lease must own the exact Run")
        cls.require_exact_contract_environment(contract, configured=configured, current=current)
        roots = dict(TrustedIssuerVerifier(trusted_public_roots)._roots)
        receipt = admission_receipt(authenticator, contract)
        admitted_grant: list[SignerTrustGrant] = []

        def original_lease_fence() -> None:
            lease_store.require_initial_fleet_control_in(
                connection, run_id=run_id, control_epoch=1,
                lease_identity=lease.token,
            )

        def operational_admission(original_connection: sqlite3.Connection) -> FleetRunState:
            if original_connection is not connection or not connection.in_transaction:
                raise TrustProtocolError("original Fleet transaction is required")
            original_lease_fence()
            capture = PreworkRunCapture.capture(
                journal=operational_journal, connection=connection,
                run_id=run_id, intent=intent, repository_scope=repository_scope,
                targets=targets,
            )
            if deployment_policy is not None:
                from oh_goon.services.fleet_native_run_contract_compiler import (
                    DeploymentRunContractPolicy, compile_native_prework_run_contract,
                )
                if type(deployment_policy) is not DeploymentRunContractPolicy:
                    raise TrustProtocolError("explicit frozen deployment contract policy required")
                compiled = compile_native_prework_run_contract(
                    prework=capture, deployment_policy=deployment_policy,
                    path_authority=path_authority, control_state_root=control_state_root,
                )
                if compiled.identity != contract.identity or compiled.fields != contract.fields:
                    raise TrustProtocolError("native compiled Run contract differs from enrolled authority")
            grant, evidence = issue_fixed_prework_native_evidence_with_signer_grant(
                prework=capture, contract=contract, path_authority=path_authority,
                key_directory=key_directory, trusted_public_roots=roots,
                signer_authority=signer_authority, control_state_root=control_state_root,
            )
            # This is not a caller-selected lease oracle: the verifier invokes
            # the actual same-transaction SQLiteExecutionLeaseStore owner.
            def prove_lease(check_run: str, epoch: int, token: str) -> bool:
                if (check_run, epoch, token) != (run_id, 1, lease.token):
                    raise TrustProtocolError("issuer evidence binds a different control lease")
                original_lease_fence()
                return True
            TrustedIssuerVerifier(roots).verify(
                contract, evidence=evidence, control_epoch=1,
                lease_identity=lease.token, lease_is_current=prove_lease,
            )
            capture.read(journal=operational_journal, run_id=run_id)
            # The original Journal is the only RUN_ADMITTED constructor/writer.
            result = operational_journal.admit_run_in(
                connection, run_id, capture.intent, capture.scope, list(capture.targets),
            )
            committed = operational_journal.read_view_from_connection(
                connection).run_events(run_id).events[0]
            if (committed.model_dump(mode="python", exclude={"generated_at"}, exclude_computed_fields=True)
                    != capture.admitted.model_dump(mode="python", exclude={"generated_at"}, exclude_computed_fields=True)):
                raise TrustProtocolError("published Fleet admission differs from canonical pre-work capture")
            admitted_grant.append(grant)
            return result

        def final_fence() -> None:
            if len(admitted_grant) != 1 or not connection.in_transaction:
                raise TrustProtocolError("fully verified pre-work admission grant required")
            original_lease_fence()
            signer_authority.require_in(
                connection, grant=admitted_grant[0],
                policy_snapshot=deployment_signer_policy_snapshot(
                    key_directory=key_directory, trusted_public_roots=roots),
                roots=roots,
            )
            cls.require_exact_contract_environment(
                contract, configured=configured, current=current,
            )
            # B8: bind the original native signer generation to THIS admitted
            # Run. This is part of the one existing strict Fleet transaction,
            # not a new journal or authority; any failure rolls back all work.
            from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
                append_original_admitted_signer_epoch_in,
            )
            append_original_admitted_signer_epoch_in(
                causal_journal, run_id=run_id, auth=authenticator,
                grant=admitted_grant[0],
            )

        if original_reservation_connection is not None:
            if original_reservation_connection is not connection or not connection.in_transaction:
                raise TrustProtocolError("original active Fleet reservation transaction required")
            # DB1: BEGIN IMMEDIATE on an unrelated handle is not proof of the
            # operational transaction owner. The original coordinator's
            # LocalDatabase must have admitted this exact active write cut.
            operational_journal._database.require_atomic_write_cut(connection)
            # The enclosing ExecutionCoordinator reservation owns rollback and
            # commit. It has already reserved the exact Fleet lease in THIS cut.
            return causal_journal.publish_admission_in(
                connection, contract, receipt, authenticator, control_epoch=1,
                lease_identity=lease.token, operational_admission=operational_admission,
                before_commit=final_fence,
            )
        return causal_journal.publish_admission_with_operation(
            contract, receipt, authenticator, control_epoch=1,
            lease_identity=lease.token, operational_admission=operational_admission,
            before_commit=final_fence,
        )

    @classmethod
    def publish_atomic_run_admission(
        cls, *, journal: CausalRunJournal,
        contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment,
        current: ResolvedExecutionEnvironment,
        control_epoch: int, lease_identity: str,
        operational_admission: Callable[[sqlite3.Connection], _PublicationResult],
        before_commit: Callable[[], None] | None = None,
    ) -> _PublicationResult:
        """Single-owner atomic publication primitive for the future Enforcer cut.

        Authority is checked before any operational callback. The journal then
        owns *one* transaction for the pre-existing operational admission and
        the authenticated causal publication. This does not assert live backend
        attestation, key custody or actual LACE binding: Phase 4 must supply and
        prove those before connecting any physical execution entrypoint.
        """
        cls.require_exact_contract_environment(
            contract, configured=configured, current=current,
        )
        return journal.publish_admission_with_operation(
            contract, receipt, authenticator, control_epoch=control_epoch,
            lease_identity=lease_identity, operational_admission=operational_admission,
            before_commit=before_commit,
        )

    def repository_instance_ids(
        self,
        intent: ResolvedCapabilityIntent,
        *,
        repository_scope: FleetRepositoryScope,
    ) -> dict[str, str]:
        """Snapshot current repository instances for one already-resolved Fleet intent."""
        state = self._state.read_view()
        targets = self._eligible_targets(intent, state, repository_scope)
        return {
            entry.repository.repository_id: repository_subject_instance_id(entry.repository.path)
            for entry in targets
        }

    def plan(
        self,
        intent: ResolvedCapabilityIntent,
        *,
        repository_scope: FleetRepositoryScope,
        expected_repository_instance_ids: dict[str, str] | None = None,
    ) -> FleetAdmissionPlan:
        with self._state.database.transaction() as connection:
            cut = self._auditor.read_cut_in(connection)
            admitted_cut = self.admit_runtime_cut(self._runtime_safety, cut=cut)
            state = self._state.read_view_in(connection, journal=admitted_cut.journal)

        if admitted_cut.active_lease is not None:
            owner = (
                f"Fleet execution {admitted_cut.active_lease.fleet_run_id}"
                if admitted_cut.active_lease.kind == "FLEET"
                else "Direct execution"
            )
            raise ExecutionBusy(f"{owner} is already active")
        if state.durable_revision != admitted_cut.durable_revision:
            raise AssertionError("Fleet admission cut produced mismatched durable revisions")

        targets = self._eligible_targets(intent, state, repository_scope)
        self._validate_repository_instances(targets, expected_repository_instance_ids)
        repository_admissions = self._validate_plan_repository_admission(intent.target, list(targets))
        self.require_effects_for_cut(
            self._runtime_safety, admitted_cut,
            safety=intent.definition.safety,
            repository_ids=tuple(target.repository.repository_id for target in targets),
        )
        return FleetAdmissionPlan(
            durable_revision=admitted_cut.durable_revision,
            targets=targets,
            repository_admissions=repository_admissions,
        )


    @staticmethod
    def _validate_repository_instances(
        targets: tuple[IncludedRepositoryCatalogEntry, ...],
        expected: dict[str, str] | None,
    ) -> None:
        if expected is None:
            return
        target_ids = {entry.repository.repository_id for entry in targets}
        if set(expected) != target_ids:
            raise TargetSelectionError("Repository instance authority does not match selected Fleet targets")
        stale = sorted(
            entry.repository.repository_id
            for entry in targets
            if repository_subject_instance_id(entry.repository.path)
            != expected[entry.repository.repository_id]
        )
        if stale:
            raise TargetSelectionError(
                "Repository instance changed since the Fleet snapshot: " + ", ".join(stale)
            )

    def _eligible_targets(
        self,
        intent: ResolvedCapabilityIntent,
        state: LocalStateReadView,
        repository_scope: FleetRepositoryScope,
    ) -> tuple[IncludedRepositoryCatalogEntry, ...]:
        capability = intent.definition
        if not capability.fleet_supported:
            raise CapabilityUnavailable(
                f"Fleet capability is not supported: {capability.capability_id}"
            )
        selected = self._selectors.select(
            capability,
            state,
            intent.target,
            repository_scope,
        )
        for requirement in capability.requirements:
            self._validate_requirement(requirement, selected, intent.target, state)
        return tuple(selected)

    def _validate_plan_repository_admission(
        self,
        target: OperationTarget | None,
        selected: list[IncludedRepositoryCatalogEntry],
    ) -> tuple[PlanRepositoryAdmissionEvidence, ...]:
        """Close effect-free repository prerequisites before any Fleet Run exists.

        All Fleet Plans, including verification/direct plans, prove repository-resolvable
        Step authority before ``RUN_ADMITTED``. Strict CI/CD adds stronger topology and
        typed repository-requirement admission on top. Runtime preparation may revalidate
        these facts immediately before effect-start to catch drift, but it must not be the
        first place an invalid run is discovered.
        """

        if not isinstance(target, FleetPlanTarget):
            return ()

        strict = isinstance(target.plan.execution, CicdExecutionPolicy)
        if strict:
            roots = tuple(entry.repository.path for entry in selected)
            try:
                RepositoryPathSpace.require_disjoint_sources(roots)
            except FleetPlanInvalid as exc:
                raise TargetSelectionError(
                    f"Strict CI/CD admission rejected before RUN_ADMITTED: selected repositories: {exc}"
                ) from exc

        blocked: list[str] = []
        admitted: list[PlanRepositoryAdmissionEvidence] = []
        for entry in selected:
            try:
                if strict:
                    RepositoryPathSpace.from_root(entry.repository.path).require_isolated_source()
                evidence = repository_plan_admission_evidence(entry.repository, target.plan)
            except (FleetPlanInvalid, ValueError) as exc:
                blocked.append(f"{entry.repository.name}: {exc}")
                continue

            admitted.append(evidence)

        if blocked:
            label = "Strict CI/CD" if strict else "Fleet Plan"
            raise TargetSelectionError(
                f"{label} admission rejected before RUN_ADMITTED: " + " | ".join(blocked)
            )
        return tuple(sorted(admitted, key=lambda item: item.repository_id))

    @staticmethod
    def _validate_requirement(
        requirement: CapabilityRequirement,
        selected: list[IncludedRepositoryCatalogEntry],
        target: OperationTarget | None,
        state: LocalStateReadView,
    ) -> None:
        required_target = FleetAdmissionPlanner._required_target(requirement, target)
        blocked: list[str] = []
        for entry in selected:
            current = state.journal.current_capability(
                entry.repository.path,
                requirement.capability_id,
                required_target,
            )
            if requirement.condition is CapabilityRequirementCondition.READY:
                allowed = current is not None and current.readiness_state.can_update
            elif requirement.condition is CapabilityRequirementCondition.SUCCESSFUL:
                allowed = current is not None and current.current_successful
            else:
                raise CapabilityUnavailable(
                    f"Unhandled capability requirement condition: {requirement.condition.value}"
                )
            if not allowed:
                blocked.append(entry.repository.name)
        if blocked:
            raise TargetSelectionError(
                f"Capability {requirement.capability_id} prerequisite is not satisfied for: "
                + ", ".join(sorted(blocked))
            )

    @staticmethod
    def _required_target(
        requirement: CapabilityRequirement,
        target: OperationTarget | None,
    ) -> OperationTarget | None:
        if requirement.target_binding is CapabilityRequirementTarget.NONE:
            return None
        if target is None:
            raise CapabilityUnavailable(
                "Capability prerequisite "
                f"{requirement.capability_id} requires the same typed target"
            )
        return target

    @classmethod
    def publish_verified_ingress_admission(
        cls, *, journal: CausalRunJournal,
        contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment,
        current: ResolvedExecutionEnvironment,
        control_epoch: int, lease_identity: str,
        issuer_verifier: TrustedIssuerVerifier,
        issuer_evidence: Sequence[IssuerEvidence],
        lease_is_current: Callable[[str, int, str], bool],
        commit_lease_is_current: Callable[[sqlite3.Connection, str, int, str], bool],
        operational_admission: Callable[[sqlite3.Connection], _PublicationResult],
    ) -> _PublicationResult:
        """M151 future ingress: require external issuer proofs *before* any work.

        M152 also requires a same-transaction, commit-time fence against the
        authoritative durable lease. A pre-transaction lease check cannot prove
        validity at publication; a failed fence rolls back both journals and the
        operational admission. No live path calls this until real issuer/key
        custody and the durable lease owner have been connected.
        """
        cls.require_exact_contract_environment(contract, configured=configured, current=current)
        if not callable(commit_lease_is_current):
            raise TrustProtocolError("trusted transactional control-lease fence required")
        issuer_verifier.verify(
            contract, evidence=issuer_evidence,
            control_epoch=control_epoch, lease_identity=lease_identity,
            lease_is_current=lease_is_current,
        )
        def fence_before_commit() -> None:
            # The same BEGIN IMMEDIATE / connection protects the lease proof and
            # both journal publications. This callback must be wired to the real
            # durable lease owner; arbitrary caller-supplied functions are NOT
            # production admission authority.
            connection = journal.connection
            if not connection.in_transaction or commit_lease_is_current(
                connection, contract.fields["run_id"], control_epoch, lease_identity,
            ) is not True:
                raise TrustProtocolError("control lease stale at admission commit")

        return cls.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=receipt,
            authenticator=authenticator, configured=configured, current=current,
            control_epoch=control_epoch, lease_identity=lease_identity,
            operational_admission=operational_admission,
            before_commit=fence_before_commit,
        )

    @classmethod
    def publish_with_fenced_signer_trust(
        cls, *, journal: CausalRunJournal, signer_authority: SignerTrustEpochAuthority,
        signer_grant: SignerTrustGrant, key_directory: Path,
        trusted_public_roots: dict[str, tuple[str, bytes]],
        contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment, current: ResolvedExecutionEnvironment,
        control_epoch: int, lease_identity: str,
        issuer_evidence: Sequence[IssuerEvidence],
        lease_is_current: Callable[[str, int, str], bool],
        commit_lease_is_current: Callable[[sqlite3.Connection, str, int, str], bool],
        operational_admission: Callable[[sqlite3.Connection], _PublicationResult],
    ) -> _PublicationResult:
        """M165 opt-in transactional signer revocation fence (NOT live ingress).

        The same connection that publishes operational/causal RUN_ADMITTED owns the
        durable trust epoch.  A revoked/rotated epoch rolls both publications back.
        The deployment signer files are independently observed; they are not a
        substitute for a same-database revocation protocol. A filesystem-only
        concurrent change is not atomic with SQLite; no live cutover is claimed.
        """
        if signer_authority.connection is not journal.connection or not isinstance(signer_grant, SignerTrustGrant):
            raise TrustProtocolError("signer epoch must belong to exact admission journal")
        if not isinstance(key_directory, Path) or not key_directory.is_absolute():
            raise TrustProtocolError("explicit signer custody directory required")
        snapshot = deployment_signer_policy_snapshot(
            key_directory=key_directory, trusted_public_roots=trusted_public_roots)
        if (signer_authority.active() != signer_grant
                or signer_grant.policy_identity != identity_for_policy(snapshot)
                or signer_grant.root_identity != identity_for_roots(trusted_public_roots)):
            raise TrustProtocolError("signer trust epoch stale before admission")

        def final_fence(connection: sqlite3.Connection, run_id: str, epoch: int, token: str) -> bool:
            # Called INSIDE the existing BEGIN IMMEDIATE immediately before commit;
            # no other SQLite revocation transaction may interleave.
            if commit_lease_is_current(connection, run_id, epoch, token) is not True:
                raise TrustProtocolError("control lease stale at admission commit")
            signer_authority.require_in(
                connection, grant=signer_grant, policy_snapshot=deployment_signer_policy_snapshot(
                    key_directory=key_directory, trusted_public_roots=trusted_public_roots),
                roots=trusted_public_roots,
            )
            return True

        return cls.publish_verified_ingress_admission(
            journal=journal, contract=contract, receipt=receipt,
            authenticator=authenticator, configured=configured, current=current,
            control_epoch=control_epoch, lease_identity=lease_identity,
            issuer_verifier=TrustedIssuerVerifier(trusted_public_roots), issuer_evidence=issuer_evidence,
            lease_is_current=lease_is_current, commit_lease_is_current=final_fence,
            operational_admission=operational_admission,
        )

    @classmethod
    def publish_with_owned_fleet_control(
        cls, *, journal: CausalRunJournal,
        lease_store: SQLiteExecutionLeaseStore, lease: ExecutionLease,
        contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        configured: ResolvedExecutionEnvironment,
        current: ResolvedExecutionEnvironment,
        issuer_verifier: TrustedIssuerVerifier,
        issuer_evidence: Sequence[IssuerEvidence],
        operational_admission: Callable[[sqlite3.Connection], _PublicationResult],
    ) -> _PublicationResult:
        """M153 opt-in Enforcer bridge to the *existing* Fleet lease owner.

        No caller-provided lease proof callback is trusted here. The precheck
        and post-publication fence both come from SQLiteExecutionLeaseStore,
        and the latter reads the identical SQLite connection inside BEGIN
        IMMEDIATE. This does NOT supply real six-domain issuer key custody or
        enable a physical execution path. Control transfer beyond epoch 1 is
        intentionally unsupported until the control-plane protocol exists.
        """
        if (lease.kind != "FLEET" or lease.fleet_run_id != contract.fields["run_id"]):
            raise TrustProtocolError("Fleet control lease does not own the admitted Run")
        return cls.publish_verified_ingress_admission(
            journal=journal, contract=contract, receipt=receipt,
            authenticator=authenticator, configured=configured, current=current,
            control_epoch=1, lease_identity=lease.token,
            issuer_verifier=issuer_verifier, issuer_evidence=issuer_evidence,
            lease_is_current=lease_store.initial_fleet_control_is_current,
            commit_lease_is_current=lambda connection, run_id, epoch, token: (
                cls._assert_owned_initial_control(lease_store, connection, run_id, epoch, token)
            ),
            operational_admission=operational_admission,
        )

    @staticmethod
    def _assert_owned_initial_control(
        store: SQLiteExecutionLeaseStore, connection: sqlite3.Connection,
        run_id: str, epoch: int, token: str,
    ) -> bool:
        store.require_initial_fleet_control_in(
            connection, run_id=run_id, control_epoch=epoch, lease_identity=token,
        )
        return True
