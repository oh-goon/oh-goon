"""Read the original Pi writer and accepted Step facts from one supplied Journal cut."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from oh_goon.core.models.fleet_observation import NativeAgentObservation
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedNativeAgentWorkload,
    ResolvedPlanStep,
)
from oh_goon.core.models.game_tape_pi_session import (
    GameTapePiFollowingStep,
    GameTapePiSessionEvidence,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.pi_session_storage import PiSessionNativeObservation
from oh_goon.core.models.run import (
    FailedFleetRun,
    FleetRunDetail,
    FleetRunHistoryPage,
    FleetRunState,
)
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    FleetRunEventBatch,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
    StepCancelledEvent,
)
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.plan_validation_diagnostics import pi_session_safe_next_action

if TYPE_CHECKING:
    from oh_goon.core.models.fleet import FleetSnapshot

_FOLLOWING_STEP_LIMIT = 8


def is_private_pi_step(step: ResolvedPlanStep) -> bool:
    return (
        isinstance(step, ResolvedAgentStep)
        and isinstance(step.workload, ResolvedNativeAgentWorkload)
        and step.workload.native_agent.uses_persistent_pi_session
    )


def fleet_run_event_readback(batch: FleetRunEventBatch) -> dict[str, Any]:
    """Serialize the original event page without publishing Pi prompt excerpts."""
    result = batch.model_dump(mode="json")
    evidence = batch.pi_session
    if evidence is None:
        return result
    # The target's original execution and delivery digests remain references
    # to the admitted bytes. Do not change the model used by the Journal.
    protected = {evidence.step_id}
    for item in (result["run"], *result["events"]):
        _hide_pi_task(item, protected)
    return result


def fleet_run_state_readback(run: FleetRunState) -> dict[str, Any] | None:
    """Serialize a private Pi Run acknowledgement without exposing its task fields."""
    private_steps = _private_pi_step_ids(run.target)
    if not private_steps:
        return None
    result = run.model_dump(mode="json", by_alias=True)
    _hide_pi_task(result, private_steps)
    return result


def fleet_run_detail_readback(detail: FleetRunDetail) -> dict[str, Any] | None:
    """Reuse Game Tape's safe Step metadata in the original detail response."""
    private_steps = _private_pi_step_ids(detail.run.target)
    if not private_steps:
        return None
    result = detail.model_dump(mode="json", by_alias=True)
    _hide_private_pi_run_detail(result, private_steps)
    return result


def fleet_snapshot_readback(snapshot: FleetSnapshot) -> dict[str, Any] | None:
    """Protect the embedded copies of private Pi Runs on the Fleet surface."""
    step_ids = {
        detail.run.fleet_run_id: ids
        for detail in (*snapshot.active_fleet_runs, *snapshot.latest_fleet_runs)
        if (ids := _private_pi_step_ids(detail.run.target))
    }
    if not step_ids:
        return None
    result = snapshot.model_dump(mode="json", by_alias=True)
    for detail in (
        *result["active_fleet_runs"],
        *result["latest_fleet_runs"],
        result["active_fleet_run"],
    ):
        if detail is not None and (private := step_ids.get(detail["run"]["fleet_run_id"])):
            _hide_private_pi_run_detail(detail, private)
    return result


def _hide_private_pi_run_detail(result: dict[str, Any], private_steps: set[str]) -> None:
    _hide_pi_task(result["run"], private_steps)
    safe_steps = {
        (repository["repository"]["repository_id"], step["step_id"]): step
        for repository in result["game_tape"]["repositories"]
        for step in repository["steps"]
        if step["step_id"] in private_steps
    }
    for operation in result["operations"]:
        _hide_pi_task(operation, private_steps)
        evidence = operation["evidence"]
        if evidence["kind"] != "FLEET_PLAN":
            continue
        for step in evidence["plan"]["steps"]:
            if step["step_id"] not in private_steps:
                continue
            safe_step = safe_steps.get(
                (operation["repository"]["repository_id"], step["step_id"]), {}
            )
            step["argv"] = safe_step.get("command", [])
            step["attempts"] = safe_step.get("attempts", [])
            step["outputs"] = safe_step.get("outputs", [])
            safe_observations = iter(safe_step.get("native_agent_observations", []))
            public_observations = []
            for entry in step["observations"]:
                # The accepted review record can repeat text from the private
                # conversation, even when its typed result is valid.
                if entry["kind"] == "agent_review":
                    continue
                public = next(safe_observations, None) if entry["kind"] == "native_agent" else entry
                if public is not None:
                    public_observations.append(public)
            step["observations"] = public_observations


def fleet_run_history_readback(page: FleetRunHistoryPage) -> dict[str, Any] | None:
    """Keep the original Run page and cursor without persistent Pi task excerpts."""
    step_ids = {
        item.run.fleet_run_id: ids
        for item in page.items
        if (ids := _private_pi_step_ids(item.run.target))
    }
    if not step_ids:
        return None
    result = page.model_dump(mode="json", by_alias=True)
    for item in result["items"]:
        run = item["run"]
        if private := step_ids.get(run["fleet_run_id"]):
            _hide_pi_task(run, private)
    return result


def _private_pi_step_ids(target: OperationTarget | None) -> set[str]:
    if not isinstance(target, FleetPlanTarget):
        return set()
    return {step.step_id for step in target.plan.steps if is_private_pi_step(step)}


def _hide_pi_task(item: dict[str, Any], private_steps: set[str]) -> None:
    target = item.get("target")
    if not isinstance(target, dict) or target.get("kind") != "fleet_plan":
        return
    for step in target["plan"]["steps"]:
        if step.get("step_id") in private_steps:
            for field in (
                "task_summary",
                "task_sha256",
                "task_omitted_characters",
                "task_redacted",
                "review_context",
                "feedback_value",
            ):
                step.pop(field, None)


def project_pi_session_evidence(detail: FleetRunDetail) -> GameTapePiSessionEvidence | None:
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget) or len(detail.run.targets) != 1:
        return None
    selected = target.plan.native_agent
    if selected is None or selected.agent != "pi":
        return None
    intent = selected.session
    if intent is None or intent.mode == "ephemeral":
        return None
    occurrence = next(
        (
            (index, step)
            for index, step in enumerate(target.plan.steps)
            if isinstance(step, ResolvedAgentStep)
            and isinstance(step.workload, ResolvedNativeAgentWorkload)
            and step.workload.native_agent == selected
        ),
        None,
    )
    if occurrence is None:
        return None
    index, step = occurrence
    repository_id = detail.run.targets[0].repository_id
    source = next(
        (
            item.source_authority
            for item in detail.repository_admissions
            if item.repository_id == repository_id
        ),
        None,
    )
    events = detail.lifecycle_events
    reservation = next(
        (
            event
            for event in events
            if isinstance(event, PiSessionWriterReservedEvent)
            and event.repository_id == repository_id
            and event.step_id == step.step_id
        ),
        None,
    )
    observed = next(
        (
            event
            for event in events
            if isinstance(event, PiSessionWriterObservedEvent)
            and reservation is not None
            and event.reservation_sequence == reservation.sequence
        ),
        None,
    )
    quiescent_sequence = (
        observed.quiescent_sequence
        if observed is not None
        else next(
            (
                event.sequence
                for event in events
                if isinstance(event, DescendantsQuiescentEvent)
                and reservation is not None
                and event.sequence > reservation.sequence
                and (event.repository_id, event.operation_id, event.step_id, event.attempt_number)
                == (
                    reservation.repository_id,
                    reservation.operation_id,
                    reservation.step_id,
                    reservation.attempt_number,
                )
            ),
            None,
        )
    )
    persistence: Literal["not_recorded", "unknown", "observed"]
    native = None
    byte_identity: Literal["digest_recorded", "metadata_only"] | None = None
    save_verification: Literal["pi_rpc_synced"] | None = None
    if observed is not None:
        observation = observed.observation
        persistence = observation.persistence
        if (
            observation.file is not None
            and observation.session_id is not None
            and observation.leaf_id is not None
        ):
            native = PiSessionNativeObservation(
                session_id=observation.session_id, leaf_id=observation.leaf_id
            )
            byte_identity = (
                "digest_recorded" if observation.file.sha256 is not None else "metadata_only"
            )
            save_verification = observation.file.save_verification
    else:
        persistence = (
            "unknown" if reservation is not None and detail.run.status.terminal else "not_recorded"
        )
    results = {
        result.step_id: result
        for report in detail.operations
        if report.repository.repository_id == repository_id
        and isinstance(report.evidence, FleetPlanOperationEvidence)
        for result in report.evidence.plan.steps
    }
    agent_result = results.get(step.step_id)
    settlements = {
        event.step_id: event.cancellation.settlement
        for event in events
        if isinstance(event, StepCancelledEvent)
        and event.repository_id == repository_id
        and event.cancellation is not None
    }
    settlements.update({step_id: result.settlement for step_id, result in results.items()})
    skill_identity = (
        next(
            (
                claim.skill_identity
                for claim in agent_result.observations
                if isinstance(claim, NativeAgentObservation) and claim.agent == "pi"
            ),
            None,
        )
        if agent_result is not None
        else None
    )
    refusal_code = (
        detail.run.failure.code
        if isinstance(detail.run, FailedFleetRun)
        else agent_result.failure_kind
        if agent_result is not None
        else None
    )
    action = pi_session_safe_next_action(refusal_code) if refusal_code is not None else None
    following = target.plan.steps[index + 1 :]
    return GameTapePiSessionEvidence(
        mode=intent.mode,
        prior_run_id=intent.prior_run_id,
        prior_writer=reservation.continuation if reservation is not None else None,
        repository_id=repository_id,
        repository_source=source,
        step_id=step.step_id,
        implementation_identity=f"pi:sha256:{selected.executable_sha256}",
        handoff_identity=step.handoff_identity,
        delivery_sha256=step.delivery_sha256,
        skill_identity=skill_identity,
        writer_reservation_sequence=reservation.sequence if reservation is not None else None,
        writer_observation_sequence=observed.sequence if observed is not None else None,
        quiescent_sequence=quiescent_sequence,
        persistence=persistence,
        byte_identity=byte_identity,
        save_verification=save_verification,
        native=native,
        settlement=settlements.get(step.step_id),
        following_steps=tuple(
            GameTapePiFollowingStep(
                step_id=following_step.step_id, settlement=settlements.get(following_step.step_id)
            )
            for following_step in following[:_FOLLOWING_STEP_LIMIT]
        ),
        omitted_following_steps=max(0, len(following) - _FOLLOWING_STEP_LIMIT),
        refusal_code=refusal_code if action is not None else None,
        safe_next_action=action,
    )
