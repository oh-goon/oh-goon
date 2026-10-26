"""Repair workload admission and stop decisions from original Fleet evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from oh_goon.core.agent_review_plan import agent_review_phases
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.models.agent_repair import AgentRepairStopReason as Stop
from oh_goon.core.models.agent_review import (
    AgentAdjudicationRecord,
    AgentReviewRecord,
    review_scope,
    review_wire_record,
)
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_lock_models import LockedExternalArtifact
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedPlanStep,
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepTeardownEvidence,
    PlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.core.models.fleet_plan_step_contract import (
    EmptyPlanCacheSource,
    RepositoryWorkspaceSource,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanStreamOutput,
    StepFailurePolicy,
    StepLifecycle,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_authority import repository_relative_parts
from oh_goon.integrations.agent_patch_apply import parse_apply_arguments, validate_write_roots
from oh_goon.services.fleet_plan_resolution_context import AgentCaptureOptions

if TYPE_CHECKING:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


@dataclass(frozen=True, slots=True)
class AgentRepairSteps:
    test: ResolvedCommandStep
    agent: ResolvedAgentStep
    apply: ResolvedCommandStep
    retest: ResolvedCommandStep
    feedback_name: str
    allowed_path: str


@dataclass(frozen=True, slots=True)
class PassedRepairTest:
    """The failure-driven policy's exact accepted native test occurrence."""

    step_id: str


def _requires_status(step: ResolvedPlanStep, source: str, status: str) -> bool:
    return (
        isinstance(step.when, StepStatusCondition)
        and step.when.step_id == source
        and step.when.statuses == (status,)
    )


def _apply_selection(argv: tuple[str, ...]) -> argparse.Namespace:
    """Admit the direct CLI or explicit locked uv project invocation used by source installs.

    This is a workload allowlist. Native uv still owns project/environment resolution.
    Directory-changing and arbitrary wrapper commands are not repair apply authority.
    """
    if Path(argv[0]).name == "oh-goon":
        application = argv[1:]
    elif Path(argv[0]).name == "uv" and argv[1:2] == ("run",):
        index = 2
        project = False
        switches = set()
        while index < len(argv):
            token = argv[index]
            if token == "--project" and not project and index + 1 < len(argv):
                project = True
                index += 2
            elif token in {"--offline", "--frozen", "--no-sync"} and token not in switches:
                switches.add(token)
                index += 1
            else:
                break
        if not project or switches != {"--offline", "--frozen", "--no-sync"}:
            raise FleetPlanInvalid(
                "repair uv apply requires an explicit offline, frozen, no-sync project"
            )
        if index >= len(argv) or argv[index] != "oh-goon":
            raise FleetPlanInvalid("repair apply must select the original oh-goon CLI")
        application = argv[index + 1 :]
    else:
        raise FleetPlanInvalid("repair apply must select the original oh-goon CLI")
    if application[:2] != ("agent", "apply"):
        raise FleetPlanInvalid("repair apply must select the original agent apply command")
    return parse_apply_arguments(application[2:])


def validate_agent_repair_plan(
    plan: ResolvedFleetPlan, handoff: FrozenAgentStepHandoff
) -> AgentRepairSteps:
    if plan.execution.purpose is ExecutionPurpose.CICD:
        raise FleetPlanInvalid(
            "automatic repair uses the selected standard-local execution profile"
        )
    if len(plan.steps) != 4:
        raise FleetPlanInvalid("repair requires exactly test, proposal, apply and retest Steps")
    test, agent, apply, retest = plan.steps
    if (
        type(test) is not ResolvedCommandStep
        or type(apply) is not ResolvedCommandStep
        or type(retest) is not ResolvedCommandStep
        or not isinstance(agent, ResolvedAgentStep)
        or agent.workload.harness != "local-model-proposal"
    ):
        raise FleetPlanInvalid(
            "repair requires native command, proposal Agent, apply and native retest"
        )
    if any(
        step.retry.max_attempts != 1
        or step.lifecycle is not StepLifecycle.NORMAL
        or step.working_directory != "."
        or getattr(step, "uv", None) is not None
        or not isinstance(step.workspace.source, RepositoryWorkspaceSource)
        or not isinstance(step.cache.source, EmptyPlanCacheSource)
        or step.workspace.publish
        or step.cache.publish
        or step.artifacts
        for step in (test, agent, apply, retest)
    ):
        raise FleetPlanInvalid(
            "repair Steps require single attempts in the selected repository workspace"
        )
    if (
        test.when is not None
        or test.on_failure is not StepFailurePolicy.CONTINUE
        or retest.on_failure is not StepFailurePolicy.CONTINUE
        or test.success_codes != (0,)
        or retest.success_codes != (0,)
        or not _requires_status(agent, test.step_id, "FAILED")
        or not _requires_status(apply, agent.step_id, "COMPLETED")
        or not _requires_status(retest, apply.step_id, "COMPLETED")
        or apply.on_failure is not StepFailurePolicy.STOP
        or apply.success_codes != (0,)
    ):
        raise FleetPlanInvalid(
            "repair requires explicit failure-only proposal and successful apply/retest dependencies"
        )
    reference = step_output_reference(agent.feedback_reference or "")
    if reference is None or reference[0] != test.step_id:
        raise FleetPlanInvalid("repair Agent must select its exact failed native test diagnostic")
    for producer in (test, retest):
        if (
            len(producer.outputs) != 1
            or not isinstance(producer.outputs[0], PlanStreamOutput)
            or producer.outputs[0].name != reference[1]
            or producer.outputs[0].max_bytes > 2048
        ):
            raise FleetPlanInvalid(
                "repair tests require one bounded native stream diagnostic of at most 2048 bytes"
            )
    selected = _apply_selection(apply.argv)
    if (
        step_output_reference(selected.proposal) != (agent.step_id, "proposal")
        or selected.expected_handoff_identity is not None
    ):
        raise FleetPlanInvalid("repair apply must consume this attempt's original proposal output")
    path = selected.expected_path
    repository_relative_parts(path, label="repair allowed write path")
    if path not in {member.path for member in handoff.source.repository.members}:
        raise FleetPlanInvalid("repair apply path is not an original captured repository member")
    return AgentRepairSteps(test, agent, apply, retest, reference[1], path)


def repair_attempt_decision(
    detail: FleetRunDetail, steps: AgentRepairSteps
) -> Stop | PassedRepairTest | None:
    """None means the exact failed retest permits another caller-budgeted attempt."""
    if getattr(getattr(detail.run, "failure", None), "code", None) == "OPERATION_DEADLINE_EXPIRED":
        return Stop.TIMED_OUT
    if detail.run.status.value in {"CANCELLED", "CANCELLING"}:
        return Stop.CANCELLED
    if detail.run.status.value != "COMPLETED" or len(detail.operations) != 1:
        return Stop.EXECUTION_UNRESOLVED
    evidence = detail.operations[0].evidence
    result = getattr(evidence, "plan", None)
    if result is None or tuple(item.step_id for item in result.steps) != (
        steps.test.step_id,
        steps.agent.step_id,
        steps.apply.step_id,
        steps.retest.step_id,
    ):
        return Stop.EXECUTION_UNRESOLVED
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget) or result.plan_id != target.plan.plan_id:
        return Stop.EXECUTION_UNRESOLVED
    for step in result.steps:
        if isinstance(step.teardown, FailedPlanStepTeardownEvidence):
            return Stop.EXECUTION_UNRESOLVED
        needs_native_proof = step.status == "COMPLETED" or (
            step.status == "FAILED" and step.step_id in {steps.test.step_id, steps.retest.step_id}
        )
        if needs_native_proof and len(step.attempts) != 1:
            return Stop.EXECUTION_UNRESOLVED
        if step.settlement.outcome == "TIMED_OUT":
            return Stop.TIMED_OUT
        if step.settlement.outcome == "CANCELLED":
            return Stop.CANCELLED
        if step.settlement.outcome == "OUTPUT_LIMITED":
            return Stop.FEEDBACK_UNAVAILABLE
        if needs_native_proof and (
            step.settlement.cleanup != "VERIFIED"
            or step.settlement.outcome not in {"SUCCESS", "FAILED"}
        ):
            return Stop.EXECUTION_UNRESOLVED
    test, agent, apply, retest = result.steps
    if test.status == "COMPLETED" and test.returncode == 0:
        return (
            PassedRepairTest(test.step_id)
            if all(step.status == "SKIPPED" for step in (agent, apply, retest))
            else Stop.EXECUTION_UNRESOLVED
        )
    if not _has_feedback(test, steps.feedback_name):
        return Stop.FEEDBACK_UNAVAILABLE
    if agent.status != "COMPLETED" or agent.returncode != 0:
        return Stop.PROPOSAL_FAILED
    if apply.status != "COMPLETED" or apply.returncode != 0:
        return Stop.APPLY_FAILED
    if retest.status == "COMPLETED" and retest.returncode == 0:
        return PassedRepairTest(retest.step_id)
    if retest.status != "FAILED" or not retest.attempts:
        return Stop.EXECUTION_UNRESOLVED
    if not _has_feedback(retest, steps.feedback_name):
        return Stop.FEEDBACK_UNAVAILABLE
    return None


def _has_feedback(step: PlanStepExecution, name: str) -> bool:
    return any(output.name == name and 0 < output.byte_count <= 2048 for output in step.outputs)


def completed_phase_steps(
    detail: FleetRunDetail, plan: ResolvedFleetPlan
) -> tuple[PlanStepExecution, ...]:
    """Require exact native terminal accounting, including supervision and cleanup."""
    if (
        not detail.run.status.terminal
        or len(detail.operations) != 1
        or detail.outcome.value != "SUCCESS"
    ):
        raise FleetPlanInvalid("phase Run did not finish successfully")
    operation = detail.operations[0].evidence
    if not isinstance(operation, FleetPlanOperationEvidence):
        raise FleetPlanInvalid("phase Run lacks native Plan evidence")
    evidence = operation.plan
    if (
        evidence is None
        or evidence.plan_id != plan.plan_id
        or tuple((s.step_id, s.kind) for s in evidence.steps)
        != tuple((s.step_id, s.kind) for s in plan.steps)
    ):
        raise FleetPlanInvalid("phase result has incomplete or different Step membership")
    for step in evidence.steps:
        if (
            step.status != "COMPLETED"
            or len(step.attempts) != 1
            or step.attempts[0].supervisor is None
            or step.attempts[0].supervisor.final_process_group_quiescent is not True
            or (
                step.attempts[0].standard_isolation is not None
                and step.attempts[0].standard_isolation.filesystem_cleanup != "verified"
            )
            or isinstance(step.teardown, FailedPlanStepTeardownEvidence)
            or step.settlement.outcome != "SUCCESS"
            or step.settlement.cleanup != "VERIFIED"
        ):
            raise FleetPlanInvalid("phase lacks complete successful native attempt evidence")
    return evidence.steps


class AgentReviewRepairCampaign:
    """Phase selection policy for the existing bounded Fleet repair driver.

    This owner never launches processes or Runs. Every selected phase is returned
    to the same original admission/wait/cancellation path as failure-driven repair.
    """

    def __init__(
        self,
        plan: ResolvedFleetPlan,
        handoffs: dict[str, FrozenAgentStepHandoff],
        *,
        resolver: FleetPlanResolver,
        plan_path: Path,
        capture_options: AgentCaptureOptions,
        passes: int,
        write_roots: tuple[str, ...],
        lock_artifacts: tuple[LockedExternalArtifact, ...] = (),
    ) -> None:
        self.original = plan
        self.handoffs = handoffs
        self.resolver = resolver
        self.plan_path = plan_path
        self.capture_options: AgentCaptureOptions = {
            **capture_options,
            "locked_skills": plan.skill_sources,
            "locked_goons": plan.goon_sources,
        }
        self.passes = passes
        self.write_roots = validate_write_roots(write_roots)
        self.phases = agent_review_phases(plan)
        if not self.phases:
            raise FleetPlanInvalid(
                "--review-passes requires a completely phase-classified Agent Plan"
            )
        self.phase = "review"
        self.review_pass = 1
        self.message = ""
        self.reviews: tuple[AgentReviewRecord, ...] = ()
        self.adjudication: AgentAdjudicationRecord | None = None
        self.history = {self.candidate_identity}
        self.finding_history: set[tuple[tuple[str, str], ...]] = set()
        self.original_digest = build_execution_lock(plan, external_identities=lock_artifacts).digest
        self._validate_apply()
        # Prove each projection before any phase effects, including native argv
        # bounds and references that must stay inside their owning phase Run.
        try:
            for phase in self.phases:
                self.phase = phase
                self._select_phase()
        except ValidationError as exc:
            raise FleetPlanInvalid(
                "Agent phase projections must retain valid local dependencies"
            ) from exc
        self.phase = "review"
        self.plan = self._select_phase()

    @property
    def candidate_identity(self) -> str:
        return next(iter(self.handoffs.values())).source.repository.content_identity

    @property
    def scope_identity(self) -> str:
        return review_scope(next(iter(self.handoffs.values())))

    def _validate_apply(self) -> None:
        for step in self.original.steps:
            if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native":
                native_selection = step.workload.native_agent
                if (
                    native_selection is None
                    or (
                        native_selection.profile.backend != "bubblewrap"
                        and native_selection.profile.codex_service is None
                    )
                    or native_selection.interactive
                ):
                    raise FleetPlanInvalid(
                        "native review phases require headless isolated read-only Agent execution"
                    )
        proposal, apply = (
            next(s for s in self.original.steps if s.step_id == identifier)
            for identifier in self.phases["repair"]
        )
        if not isinstance(apply, ResolvedCommandStep):
            raise FleetPlanInvalid("repair apply requires an explicit native command Step")
        selected = _apply_selection(apply.argv)
        if (
            step_output_reference(selected.proposal) != (proposal.step_id, "proposal")
            or selected.write_roots != ["@admitted"]
            or selected.expected_candidate_identity != "@candidate"
            or selected.expected_handoff_identity is not None
            or selected.expected_path is not None
            or apply.working_directory != "."
            or apply.success_codes != (0,)
        ):
            raise FleetPlanInvalid(
                "repair must consume its own proposal through native batch apply with @admitted roots and @candidate identity"
            )
        if any(
            not isinstance(step.workspace.source, RepositoryWorkspaceSource)
            or not isinstance(step.cache.source, EmptyPlanCacheSource)
            for step in self.original.steps
        ):
            raise FleetPlanInvalid(
                "Agent phases require the admitted repository workspace and empty cache"
            )

    def _select_phase(self) -> ResolvedFleetPlan:
        members = set(self.phases[self.phase])
        reviews = []
        # Command-only phases consume no Agent context. After apply their
        # candidate intentionally differs from the preceding review source.
        if self.phase in ("adjudicate", "repair"):
            for record in self.reviews:
                handoff = self.handoffs.get(record.reviewer)
                if handoff is None:
                    raise FleetPlanInvalid("review context lacks its original source handoff")
                reviews.append(review_wire_record(record, handoff, record.reviewer))
        context = json.dumps(
            {
                "candidate_identity": self.candidate_identity,
                "scope_identity": self.scope_identity,
                "write_roots": self.write_roots,
                "reviews": reviews,
                "adjudication": self.adjudication.model_dump(mode="json")
                if self.adjudication
                else None,
            },
            separators=(",", ":"),
        )
        if len(context.encode()) > 65536:
            raise FleetPlanInvalid("combined review evidence exceeds the phase context budget")
        steps = []
        for step in self.original.steps:
            if step.step_id not in members:
                continue
            if isinstance(step, ResolvedAgentStep):
                step = step.model_copy(update={"review_context": context})
            elif self.phase == "repair":
                if not isinstance(step, ResolvedCommandStep):
                    raise FleetPlanInvalid("repair effects require an explicit native command Step")
                argv = []
                for token in step.argv:
                    if token == "@admitted":
                        argv.append(self.write_roots[0])
                        for root in self.write_roots[1:]:
                            argv.extend(("--write-root", root))
                    else:
                        argv.append(self.candidate_identity if token == "@candidate" else token)
                try:
                    step = ResolvedCommandStep.model_validate(
                        {**step.model_dump(), "argv": tuple(argv)}
                    )
                except ValidationError as exc:
                    raise FleetPlanInvalid(
                        "native repair apply exceeds its command argument contract; narrow write roots"
                    ) from exc
            steps.append(step)
        imports = tuple(
            edge.model_copy(
                update={"phase": None, "step_ids": tuple(s for s in edge.step_ids if s in members)}
            )
            for edge in self.original.imports
            if members.intersection(edge.step_ids)
        )
        return ResolvedFleetPlan.create(
            name=self.original.name,
            description=self.original.description,
            execution=self.original.execution,
            steps=tuple(steps),
            imports=imports,
            requirements=self.original.requirements,
            step_sources=tuple(s for s in self.original.step_sources if s.step_id in members),
            skill_sources=self.original.skill_sources,
            goon_sources=self.original.goon_sources,
            native_agent=self.original.native_agent,
        )

    def _recapture(self, *, require_unchanged: bool) -> str:
        plan, handoffs, _ = self.resolver.resolve_frozen_agents(
            self.plan_path, previous=self.handoffs, **self.capture_options
        )
        # Recapture may replace candidate bytes, never the caller's resolved
        # commands, connection, policy, bindings or import membership.
        source_bindings: dict[str, Any] = {
            "plan_id": True,
            "steps": {"__all__": {"handoff_identity", "delivery_sha256"}},
        }
        if plan.model_dump(exclude=source_bindings) != self.original.model_dump(
            exclude=source_bindings
        ):
            raise FleetPlanInvalid("Agent continuation changed resolved execution configuration")
        identity = next(iter(handoffs.values())).source.repository.content_identity
        if require_unchanged and identity != self.candidate_identity:
            raise FleetPlanInvalid("candidate changed outside its admitted repair phase")
        self.original = plan
        self.handoffs = handoffs
        return identity

    def advance(self, detail: FleetRunDetail) -> Stop | None:
        if (
            getattr(getattr(detail.run, "failure", None), "code", None)
            == "OPERATION_DEADLINE_EXPIRED"
        ):
            return Stop.TIMED_OUT
        if detail.outcome.value == "CANCELLED":
            return Stop.CANCELLED
        for operation in detail.operations:
            evidence = getattr(operation.evidence, "plan", None)
            for step in evidence.steps if evidence is not None else ():
                if step.settlement.outcome == "TIMED_OUT":
                    return Stop.TIMED_OUT
                if step.settlement.outcome == "CANCELLED":
                    return Stop.CANCELLED

        try:
            steps = completed_phase_steps(detail, self.plan)
        except FleetPlanInvalid:
            return {
                "review": Stop.REVIEW_INCOMPLETE,
                "adjudicate": Stop.ADJUDICATION_INCOMPLETE,
                "repair": Stop.APPLY_FAILED,
                "regression": Stop.REGRESSION_FAILED,
                "verification": Stop.VERIFICATION_FAILED,
            }[self.phase]
        if self.phase == "review":
            records = []
            for step in steps:
                observations = [
                    o for o in step.observations if isinstance(o, AgentReviewObservation)
                ]
                if len(observations) != 1 or not isinstance(
                    observations[0].record, AgentReviewRecord
                ):
                    return Stop.REVIEW_INCOMPLETE
                record = observations[0].record
                try:
                    record.verify(self.handoffs[step.step_id], step.step_id)
                except FleetPlanInvalid as exc:
                    self.message = str(exc)
                    return Stop.REVIEW_INCOMPLETE
                records.append(record)
            self.reviews = tuple(records)
            self._recapture(require_unchanged=True)
            fingerprint = tuple(
                (record.reviewer, finding.model_dump_json())
                for record in self.reviews
                for finding in record.findings
            )
            if fingerprint and fingerprint in self.finding_history:
                return Stop.NO_PROGRESS
            if fingerprint:
                self.finding_history.add(fingerprint)
            if all(record.verdict == "CLEAN" for record in self.reviews):
                self.phase = "verification"
            elif any(
                record.verdict in ("LEAVE_ALONE", "INSUFFICIENT_EVIDENCE")
                for record in self.reviews
            ):
                return Stop.REVIEW_INCOMPLETE
            elif self.review_pass >= self.passes:
                return Stop.ATTEMPTS_EXHAUSTED
            else:
                self.phase = "adjudicate"
        elif self.phase == "adjudicate":
            observations = [
                o for o in steps[0].observations if isinstance(o, AgentReviewObservation)
            ]
            if len(observations) != 1 or not isinstance(
                observations[0].record, AgentAdjudicationRecord
            ):
                return Stop.ADJUDICATION_INCOMPLETE
            self.adjudication = observations[0].record
            try:
                self.adjudication.verify(self.reviews)
            except FleetPlanInvalid as exc:
                self.message = str(exc)
                return Stop.ADJUDICATION_INCOMPLETE
            if not any(d.decision == "repair" for d in self.adjudication.dispositions):
                return Stop.NO_PROGRESS
            self._recapture(require_unchanged=True)
            self.phase = "repair"
        elif self.phase == "repair":
            candidate = self._recapture(require_unchanged=False)
            if candidate in self.history:
                return Stop.NO_PROGRESS
            self.history.add(candidate)
            self.phase = "regression"
        elif self.phase == "regression":
            self._recapture(require_unchanged=True)
            self.review_pass += 1
            self.reviews = ()
            self.adjudication = None
            self.phase = "review"
        else:
            self._recapture(require_unchanged=True)
            return Stop.CONVERGED
        try:
            self.plan = self._select_phase()
        except FleetPlanInvalid as exc:
            self.message = str(exc)
            return Stop.FEEDBACK_UNAVAILABLE
        return None
