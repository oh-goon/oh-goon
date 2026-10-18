from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_evidence import (
    MissingPlanArtifact,
    PlanArtifactEvidence,
    PlanRetainedContentEvidence,
    PresentPlanArtifact,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.integrations._fleet_plan_step_dataflow import (
    collect_published_outputs,
    repository_relative_path,
)
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    output_contract_failure,
)
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.fleet_content_store import FleetContentObject
from oh_goon.integrations.fleet_plan_run import (
    FleetPlanStepAttempt,
    PreparedPlanRun,
    SealedAttemptOutputs,
)


@dataclass(frozen=True, slots=True)
class AttemptPublication:
    """Named successful-attempt publication facts consumed by retry evidence."""

    outputs: dict[str, str]
    output_evidence: tuple[PublishedPlanOutputEvidence, ...]
    outcome: StepOutcome
    cache_identity: SnapshotIdentity | None
    retained_cache: PlanRetainedContentEvidence | None


def publish_direct_attempt_outputs(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository: Path,
) -> AttemptPublication:
    """Publish one successful direct attempt without strict lineage side effects."""

    return _published_outputs(step, outcome, repository)


def publish_strict_attempt_outputs(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository: Path,
    prepared_run: PreparedPlanRun,
    attempt: FleetPlanStepAttempt,
    sealed_outputs: SealedAttemptOutputs,
) -> AttemptPublication:
    """Publish one successful strict attempt only with its ownership-seal capability."""

    sealed_outputs.prove_for(attempt)
    if outcome.runtime_authority is None:
        return AttemptPublication(
            outputs={},
            output_evidence=(),
            outcome=output_contract_failure(
                step,
                FleetPlanInvalid("Strict output publication requires runtime authority evidence"),
                outcome.argv,
                base=outcome,
            ),
            cache_identity=None,
            retained_cache=None,
        )
    if outcome.physical_execution_id is None:
        return AttemptPublication(
            outputs={},
            output_evidence=(),
            outcome=output_contract_failure(
                step,
                FleetPlanInvalid("Strict output publication requires physical execution identity"),
                outcome.argv,
                base=outcome,
            ),
            cache_identity=None,
            retained_cache=None,
        )
    producer_execution_identity = stable_id(
        "attempt-output",
        {
            "repository_id": prepared_run.repository_id,
            "step_id": step.step_id,
            "attempt": attempt.attempt_number,
            "physical_execution_id": outcome.physical_execution_id,
            "runtime_authority_receipt_identity": outcome.runtime_authority.receipt_identity,
        },
    )
    publication = _published_outputs(
        step,
        outcome,
        repository,
        producer_execution_identity=producer_execution_identity,
    )
    if publication.outcome is not outcome:
        return publication
    try:
        prepared_run.publish_workspace(step, repository)
        cache_identity, retained = prepared_run.publish_cache(step, attempt.paths.cache)
    except FleetPlanInvalid as exc:
        prepared_run.discard_publications(step)
        return AttemptPublication(
            outputs={},
            output_evidence=(),
            outcome=output_contract_failure(step, exc, outcome.argv, base=outcome),
            cache_identity=None,
            retained_cache=None,
        )
    except BaseException:
        prepared_run.discard_publications(step)
        raise
    retained_cache = _retained_content_evidence(retained) if retained is not None else None
    return AttemptPublication(
        outputs=publication.outputs,
        output_evidence=publication.output_evidence,
        outcome=publication.outcome,
        cache_identity=cache_identity,
        retained_cache=retained_cache,
    )


def _published_outputs(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository: Path,
    *,
    producer_execution_identity: str | None = None,
) -> AttemptPublication:
    try:
        values, evidence = collect_published_outputs(
            repository,
            step,
            producer_execution_identity=producer_execution_identity,
            streams={"stdout": outcome.stdout, "stderr": outcome.stderr},
        )
    except FleetPlanInvalid as exc:
        return AttemptPublication(
            outputs={},
            output_evidence=(),
            outcome=output_contract_failure(step, exc, outcome.argv, base=outcome),
            cache_identity=None,
            retained_cache=None,
        )
    # A model's validated proposal is data, not an effect grant. Publish it
    # through the SAME successful-attempt output owner as ordinary Step files,
    # with the original producer execution identity on strict attempts.
    if isinstance(step, ResolvedLocalSearchAgentStep) and step.harness == "local-model-proposal":
        try:
            from oh_goon.integrations.agent_patch_apply import proposal_from_step_stdout

            proposal_text = proposal_from_step_stdout(
                outcome.stdout,
                handoff_identity=step.handoff_identity,
            )
        except FleetPlanInvalid as exc:
            return AttemptPublication(
                outputs={},
                output_evidence=(),
                outcome=output_contract_failure(step, exc, outcome.argv, base=outcome),
                cache_identity=None,
                retained_cache=None,
            )
        values["proposal"] = proposal_text
        payload = proposal_text.encode("utf-8")
        evidence = (
            *evidence,
            PublishedPlanOutputEvidence(
                name="proposal",
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                producer_execution_identity=producer_execution_identity,
            ),
        )
    return AttemptPublication(
        outputs=values,
        output_evidence=evidence,
        outcome=outcome,
        cache_identity=None,
        retained_cache=None,
    )


def collect_direct_artifacts(
    repository: Path,
    declarations,
) -> tuple[PlanArtifactEvidence, ...]:
    """Collect direct-run artifacts; retained content is strict-only by contract."""

    if any(declaration.retention == "content_store" for declaration in declarations):
        raise FleetPlanInvalid(
            "Direct Fleet execution cannot publish artifacts to the strict content store"
        )
    return _collect_artifacts(repository, declarations, retain_file=None)


def collect_strict_artifacts(
    repository: Path,
    declarations,
    prepared_run: PreparedPlanRun,
) -> tuple[PlanArtifactEvidence, ...]:
    """Collect strict-run artifacts and retain explicitly declared file objects."""

    def retain_file(path: Path, value: str) -> PlanRetainedContentEvidence:
        if prepared_run.content_store is None:
            raise FleetPlanInvalid(
                f"Fleet artifact {value} requested content-store retention without a strict run store"
            )
        object_ = prepared_run.content_store.put_file(
            prepared_run.retained_content_path(
                path,
                owned_root=repository,
                label=f"Fleet artifact {value}",
            )
        )
        return _retained_content_evidence(object_)

    return _collect_artifacts(repository, declarations, retain_file=retain_file)


def _collect_artifacts(
    repository: Path,
    declarations,
    *,
    retain_file: Callable[[Path, str], PlanRetainedContentEvidence] | None,
) -> tuple[PlanArtifactEvidence, ...]:
    evidence: list[PlanArtifactEvidence] = []
    for declaration in declarations:
        value = declaration.path
        path = repository_relative_path(repository, value, kind="artifact")
        if not path.is_file():
            evidence.append(MissingPlanArtifact(path=value))
            continue
        sha256, byte_count = sha256_file_with_size(path)
        retained = None
        if declaration.retention == "content_store":
            if retain_file is None:
                raise FleetPlanInvalid(
                    f"Fleet artifact {value} requested content-store retention without strict execution"
                )
            retained = retain_file(path, value)
        evidence.append(
            PresentPlanArtifact(
                path=value,
                sha256=sha256,
                byte_count=byte_count,
                retained_object=retained,
            )
        )
    return tuple(evidence)


def _retained_content_evidence(object_: FleetContentObject) -> PlanRetainedContentEvidence:
    return PlanRetainedContentEvidence(
        reference_id=object_.reference_id,
        owner_repository_id=object_.owner_repository_id,
        kind=object_.kind,
        sha256=object_.identity.sha256,
        byte_count=object_.identity.byte_count,
        entry_count=object_.identity.entry_count,
    )
