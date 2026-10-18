"""Pure, versioned Enforcer/LACE causal semantics (Group A, Phase 1).

This is a contract-level interpreter for *already durably committed* facts. It
never assigns sequence numbers, performs IO, chooses backends, mutates admission
state, or seals a Run. Later phases must bind these exact functions to the
single authenticated journal/evaluator/rollup owner before enabling execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Sequence

SEMANTICS_VERSION = "enforcer-lace/v1"


class SemanticError(ValueError):
    """Missing or contradictory durable authority; do not infer a result."""


class FactKind(StrEnum):
    RUN_ADMITTED = "RUN_ADMITTED"
    STEP_WORK_STARTED = "STEP_WORK_STARTED"
    OBSERVED = "OBSERVED"
    VIOLATION_MATCHED = "VIOLATION_MATCHED"
    EVALUATION_FAILED = "EVALUATION_FAILED"
    ABORT_REQUESTED = "ABORT_REQUESTED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    WORKLOAD_COMPLETED = "WORKLOAD_COMPLETED"
    WORKLOAD_FAILED = "WORKLOAD_FAILED"
    RUN_BINDING_FAILED = "RUN_BINDING_FAILED"
    STEP_BINDING_FAILED = "STEP_BINDING_FAILED"
    TEARDOWN_ENTERED = "TEARDOWN_ENTERED"
    QUIESCENCE_PROVEN = "QUIESCENCE_PROVEN"
    QUIESCENCE_UNPROVEN = "QUIESCENCE_UNPROVEN"
    JOURNAL_APPEND_FAILED = "JOURNAL_APPEND_FAILED"
    CONTROL_LEASE_LOST = "CONTROL_LEASE_LOST"


class CanonicalOutcome(StrEnum):
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    PREWORK_ABORTED = "PREWORK_ABORTED"
    RUN_BINDING_FAILED = "RUN_BINDING_FAILED"
    STEP_BINDING_FAILED = "STEP_BINDING_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_ABORTED = "EXECUTION_ABORTED"
    ENFORCEMENT_TERMINATED = "ENFORCEMENT_TERMINATED"
    CONTAINMENT_FAILED = "CONTAINMENT_FAILED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"


class ObservationClass(StrEnum):
    AUTHORITY_BEARING = "AUTHORITY_BEARING"
    INFORMATIONAL = "INFORMATIONAL"


class Evaluation(StrEnum):
    PERMITTED = "PERMITTED"
    VIOLATION = "VIOLATION"
    EVALUATION_FAILED = "EVALUATION_FAILED"


@dataclass(frozen=True, slots=True)
class CommittedFact:
    """One journal-committed fact, not a proposed/allocated future sequence."""

    run_id: str
    run_seq: int
    kind: FactKind
    step_id: str | None = None
    observation: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class FrozenRules:
    """Frozen evaluator policy supplied by the future AdmittedRunContract."""

    observation_classes: Mapping[str, ObservationClass]
    execution_permissions: frozenset[str]
    teardown_permissions: frozenset[str]
    expected_exit_codes: frozenset[int] = frozenset({0})

    def __post_init__(self) -> None:
        # An admitted contract must not depend on a caller's mutable dict/set.
        object.__setattr__(
            self, "observation_classes", MappingProxyType(dict(self.observation_classes))
        )
        object.__setattr__(self, "execution_permissions", frozenset(self.execution_permissions))
        object.__setattr__(self, "teardown_permissions", frozenset(self.teardown_permissions))
        object.__setattr__(self, "expected_exit_codes", frozenset(self.expected_exit_codes))


@dataclass(frozen=True, slots=True)
class BaseResult:
    outcome: CanonicalOutcome
    cause_run_seq: int


@dataclass(frozen=True, slots=True)
class ClassifiedStep:
    """Outcome classification is NOT a terminal journal seal/receipt."""

    step_id: str
    outcome: CanonicalOutcome
    base: BaseResult
    containment_proven: bool


def verify_committed_prefix(facts: Sequence[CommittedFact]) -> None:
    """Check *committed* run-local continuity, never infer omitted facts."""
    if not facts or facts[0].kind is not FactKind.RUN_ADMITTED:
        raise SemanticError("first committed event must be RUN_ADMITTED")
    run_id = facts[0].run_id
    if not run_id:
        raise SemanticError("committed run ID cannot be empty")
    for expected, fact in enumerate(facts, start=1):
        if fact.run_id != run_id or type(fact.run_seq) is not int or fact.run_seq != expected:
            raise SemanticError("committed run journal gap, duplicate, reorder or cross-run event")
        if not isinstance(fact.kind, FactKind):
            raise SemanticError("unknown event class is not authoritative")
        if fact.kind is FactKind.OBSERVED and (not fact.observation or not fact.step_id):
            raise SemanticError("observation requires step and named class")
        if fact.kind is FactKind.RUN_ADMITTED and (expected != 1 or fact.step_id is not None):
            raise SemanticError("RUN_ADMITTED must be the first Run-scoped event")
        if fact.kind is FactKind.RUN_BINDING_FAILED and fact.step_id is not None:
            raise SemanticError("Run binding failure cannot be attributed to a Step")
        if (
            fact.kind in {FactKind.STEP_BINDING_FAILED, FactKind.STEP_WORK_STARTED}
            and not fact.step_id
        ):
            raise SemanticError("Step lifecycle event requires explicit Step identity")
        if (
            fact.kind in {FactKind.WORKLOAD_COMPLETED, FactKind.WORKLOAD_FAILED}
            and fact.exit_code is None
        ):
            raise SemanticError("workload exit must carry exact exit code")
        # step_id=None identifies Run-scoped teardown, not an ambiguous Step.


def envelope_at(facts: Sequence[CommittedFact], run_seq: int, *, step_id: str) -> str:
    """Select execution vs teardown by committed transition, not clock/arrival."""
    verify_committed_prefix(facts)
    if run_seq < 1 or run_seq > len(facts):
        raise SemanticError("observation references an uncommitted run_seq")
    if not step_id:
        raise SemanticError("Step envelope owner required")
    return (
        "teardown"
        if any(
            f.kind is FactKind.TEARDOWN_ENTERED and f.step_id == step_id and f.run_seq < run_seq
            for f in facts
        )
        else "execution"
    )


def evaluate(rules: FrozenRules, observation: str, *, envelope: str) -> Evaluation:
    """Fail closed for unrecognised/insufficiently-described observations."""
    if envelope not in {"execution", "teardown"}:
        raise SemanticError("unknown active envelope")
    if not observation:
        return Evaluation.EVALUATION_FAILED
    category = rules.observation_classes.get(observation)
    if category is None or not isinstance(category, ObservationClass):
        return Evaluation.EVALUATION_FAILED
    if category is ObservationClass.INFORMATIONAL:
        return Evaluation.PERMITTED
    allowed = rules.teardown_permissions if envelope == "teardown" else rules.execution_permissions
    return Evaluation.PERMITTED if observation in allowed else Evaluation.VIOLATION


def derive_base_execution_result(
    facts: Sequence[CommittedFact],
    *,
    step_id: str | None,
    rules: FrozenRules,
    prior_step_work_started: bool = False,
) -> BaseResult | None:
    """First committed causal result wins; teardown-policy violations can supersede.

    An unresolved journal/lease never yields a fabricated terminal outcome.
    A later abort, timeout or completion cannot retroactively override a
    committed earlier result. A *teardown-envelope* violation can still win
    because it violates authority active after the first cause.
    """
    verify_committed_prefix(facts)
    if step_id == "":
        raise SemanticError("empty scope owner")
    seen_step_work = prior_step_work_started
    base: BaseResult | None = None
    teardown_entered = False
    for f in facts:
        if f.kind in {FactKind.JOURNAL_APPEND_FAILED, FactKind.CONTROL_LEASE_LOST}:
            return None  # no terminal projection may be inferred from this prefix
        if f.step_id != step_id:
            continue
        if f.kind is FactKind.TEARDOWN_ENTERED:
            if teardown_entered:
                raise SemanticError("duplicate Step teardown transition")
            teardown_entered = True
            continue
        if f.kind is FactKind.STEP_WORK_STARTED:
            if teardown_entered:
                raise SemanticError("Step work cannot restart after teardown entry")
            seen_step_work = True
            continue
        kind = f.kind
        outcome: CanonicalOutcome | None = None
        if teardown_entered and kind in {
            FactKind.WORKLOAD_COMPLETED,
            FactKind.WORKLOAD_FAILED,
            FactKind.RUN_BINDING_FAILED,
            FactKind.STEP_BINDING_FAILED,
        }:
            raise SemanticError("new workload/binding result after committed teardown")
        if kind is FactKind.OBSERVED:
            decision = evaluate(
                rules, f.observation or "", envelope="teardown" if teardown_entered else "execution"
            )
            if decision is Evaluation.VIOLATION or decision is Evaluation.EVALUATION_FAILED:
                outcome = CanonicalOutcome.ENFORCEMENT_TERMINATED
        elif kind in {FactKind.VIOLATION_MATCHED, FactKind.EVALUATION_FAILED}:
            outcome = CanonicalOutcome.ENFORCEMENT_TERMINATED
        elif kind in {FactKind.ABORT_REQUESTED, FactKind.DEADLINE_EXPIRED}:
            # A cancelled Step is EXECUTION_ABORTED even when it never started
            # workload; the Run rollup owns the first-Step PREWORK distinction.
            outcome = (
                CanonicalOutcome.EXECUTION_ABORTED
                if step_id is not None or seen_step_work
                else CanonicalOutcome.PREWORK_ABORTED
            )
        elif kind is FactKind.RUN_BINDING_FAILED:
            outcome = CanonicalOutcome.RUN_BINDING_FAILED
        elif kind is FactKind.STEP_BINDING_FAILED:
            outcome = CanonicalOutcome.STEP_BINDING_FAILED
        elif kind in {FactKind.WORKLOAD_COMPLETED, FactKind.WORKLOAD_FAILED}:
            outcome = (
                CanonicalOutcome.EXECUTION_COMPLETED
                if kind is FactKind.WORKLOAD_COMPLETED and f.exit_code in rules.expected_exit_codes
                else CanonicalOutcome.EXECUTION_FAILED
            )
        if outcome is not None:
            if base is None or (
                teardown_entered and outcome is CanonicalOutcome.ENFORCEMENT_TERMINATED
            ):
                base = BaseResult(outcome=outcome, cause_run_seq=f.run_seq)
    return base


def classify_step_outcome(
    facts: Sequence[CommittedFact],
    *,
    step_id: str,
    rules: FrozenRules,
    physical_authority_existed: bool,
    prior_step_work_started: bool = False,
) -> ClassifiedStep | None:
    """Classify only with a durable base cause and teardown evidence.

    None means there is not enough authoritative evidence to classify; it is
    not EXECUTION_COMPLETED. Containment failure is a failure classification,
    never a claim that physical resources are quiescent or a terminal seal.
    """
    base = derive_base_execution_result(
        facts, step_id=step_id, rules=rules, prior_step_work_started=prior_step_work_started
    )
    if base is None:
        return None
    relevant = tuple(f for f in facts if f.step_id == step_id)
    entered = any(f.kind is FactKind.TEARDOWN_ENTERED for f in relevant)
    proven = any(f.kind is FactKind.QUIESCENCE_PROVEN for f in relevant)
    unproven = any(f.kind is FactKind.QUIESCENCE_UNPROVEN for f in relevant)
    if proven and unproven:
        raise SemanticError("conflicting teardown/quiescence evidence")
    if (proven or unproven) and not entered:
        raise SemanticError("quiescence evidence without committed teardown entry")
    quiescence_seq = next(
        (
            f.run_seq
            for f in relevant
            if f.kind in {FactKind.QUIESCENCE_PROVEN, FactKind.QUIESCENCE_UNPROVEN}
        ),
        None,
    )
    if quiescence_seq is not None and any(
        f.run_seq > quiescence_seq
        and f.kind
        in {
            FactKind.OBSERVED,
            FactKind.VIOLATION_MATCHED,
            FactKind.EVALUATION_FAILED,
            FactKind.STEP_WORK_STARTED,
            FactKind.WORKLOAD_COMPLETED,
            FactKind.WORKLOAD_FAILED,
        }
        for f in relevant
    ):
        raise SemanticError("new effect evidence after Step quiescence invalidates its proof")
    entry_seq = next((f.run_seq for f in relevant if f.kind is FactKind.TEARDOWN_ENTERED), None)
    if any(
        f.kind in {FactKind.QUIESCENCE_PROVEN, FactKind.QUIESCENCE_UNPROVEN}
        and entry_seq is not None
        and f.run_seq <= entry_seq
        for f in relevant
    ):
        raise SemanticError("quiescence must be proven after teardown entry")
    if not entered or (not proven and not unproven):
        return None
    if unproven and not physical_authority_existed:
        # A claim that no resources were created requires proof, not weak absence.
        return None
    return ClassifiedStep(
        step_id=step_id,
        base=base,
        outcome=CanonicalOutcome.CONTAINMENT_FAILED
        if physical_authority_existed and unproven
        else base.outcome,
        containment_proven=proven,
    )


def rollup_run(
    steps: Sequence[ClassifiedStep],
    *,
    run_teardown_proven: bool | None,
    run_physical_authority_existed: bool,
    run_prework: BaseResult | None = None,
    any_step_work_started: bool | None = None,
) -> CanonicalOutcome | None:
    """Roll up an ordered, complete sequence; never invent missing Step history.

    The first non-success outcome blocks ordinary subsequent Step work. Later
    Step decisions must therefore not be supplied after an earlier failure.
    Non-terminal/unproven Run teardown cannot seal a successful Run.
    """
    if run_teardown_proven is None:
        return None
    if run_prework is not None:
        if steps or run_prework.outcome not in {
            CanonicalOutcome.RUN_BINDING_FAILED,
            CanonicalOutcome.PREWORK_ABORTED,
        }:
            raise SemanticError("Run prework outcome cannot own Step work")
        if run_physical_authority_existed and not run_teardown_proven:
            return CanonicalOutcome.CONTAINMENT_FAILED
        return run_prework.outcome
    if not steps:
        return None
    if len({step.step_id for step in steps}) != len(steps):
        raise SemanticError("duplicate Step rollup owner")
    if any(step.outcome is CanonicalOutcome.CONTAINMENT_FAILED for step in steps):
        return CanonicalOutcome.CONTAINMENT_FAILED
    if any(not step.containment_proven for step in steps):
        raise SemanticError("unproven Step containment cannot yield a sealed Run result")
    for index, step in enumerate(steps):
        if step.outcome is not CanonicalOutcome.EXECUTION_COMPLETED:
            if index != len(steps) - 1:
                raise SemanticError("Step work after first blocking failure")
            if run_physical_authority_existed and not run_teardown_proven:
                return CanonicalOutcome.CONTAINMENT_FAILED
            if step.outcome is CanonicalOutcome.EXECUTION_ABORTED:
                if any_step_work_started is None:
                    return None  # cannot invent PREWORK without authoritative history
                if not any_step_work_started:
                    return CanonicalOutcome.PREWORK_ABORTED
            return step.outcome
    if run_physical_authority_existed and not run_teardown_proven:
        return CanonicalOutcome.CONTAINMENT_FAILED
    return CanonicalOutcome.EXECUTION_COMPLETED
