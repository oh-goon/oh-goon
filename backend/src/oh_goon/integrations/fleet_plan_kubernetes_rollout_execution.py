from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    PLAN_TIMEOUT_RETURN_CODE,
    ResolvedKubernetesRolloutVerifyStep,
)
from oh_goon.core.models.fleet_plan_kubernetes import is_digest_image_reference
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    KubernetesRolloutVerificationOutcome,
)
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_kubernetes_transport import (
    KubernetesCommandRunner,
    KubernetesPrivateConfig,
    cleanup_kubernetes_private_config,
    kubernetes_http_status,
    prepare_kubernetes_private_config,
    run_kubernetes_request,
)


@dataclass(frozen=True, slots=True)
class _RolloutObservation:
    resource_version: str
    generation: int
    image: str
    observed_generation: int | None
    desired_replicas: int
    replicas: int
    updated_replicas: int
    available_replicas: int
    unavailable_replicas: int
    progressing_status: Literal["True", "False", "Unknown"] | None
    progressing_reason: str | None


def execute_kubernetes_rollout_verify(
    step: ResolvedKubernetesRolloutVerifyStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    host_environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> StepOutcome:
    """Observe one exact generation until ready; never mutates Kubernetes state."""

    try:
        expected_generation = _expected_generation(step.expected_generation)
    except ValueError as exc:
        return _contract_failure(str(exc))
    if not is_digest_image_reference(step.expected_image):
        return _contract_failure("Fleet Kubernetes rollout expected image is not digest-qualified")

    try:
        config = prepare_kubernetes_private_config(
            paths, step.credentials, host_environment, owner="kubernetes-rollout-verify"
        )
    except (KeyError, OSError, ValueError) as exc:
        return _contract_failure(str(exc))
    try:
        outcome = _run_rollout_observation_window(
            step,
            paths,
            expected_generation=expected_generation,
            environment=environment,
            command_runner=command_runner,
            config=config,
            cancel_requested=cancel_requested,
            clock=clock,
            sleeper=sleeper,
        )
    except BaseException as exc:
        _cleanup_with_note(config, exc)
        raise
    cleanup_error = _cleanup_error(config)
    if cleanup_error is None:
        return outcome
    return replace(
        outcome,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stderr=(
            outcome.stderr + f"\nPrivate Kubernetes credential cleanup failed: {cleanup_error}"
        ).strip(),
    )


def _cleanup_error(config: KubernetesPrivateConfig) -> OSError | None:
    try:
        cleanup_kubernetes_private_config(config)
    except OSError as exc:
        return exc
    return None


def _cleanup_with_note(config: KubernetesPrivateConfig, error: BaseException) -> None:
    cleanup_error = _cleanup_error(config)
    if cleanup_error is not None:
        error.add_note(f"Private Kubernetes credential cleanup also failed: {cleanup_error}")


def _run_rollout_observation_window(
    step: ResolvedKubernetesRolloutVerifyStep,
    paths: AttemptPathLayout,
    *,
    expected_generation: int,
    environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    config: KubernetesPrivateConfig,
    cancel_requested: Callable[[], bool] | None,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> StepOutcome:
    response = paths.tmp / "kubernetes-rollout-observation.json"
    started = clock()
    deadline = started + step.timeout_seconds
    observations = 0
    last: _RolloutObservation | None = None
    last_argv: tuple[str, ...] = ()
    while True:
        _raise_if_cancelled(cancel_requested)
        remaining = deadline - clock()
        if remaining <= 0:
            return _timeout_outcome(step, last, observations, started, clock, last_argv)
        result = run_kubernetes_request(
            step.target,
            config,
            command_runner=command_runner,
            environment=environment,
            cancel_requested=cancel_requested,
            request_timeout_seconds=step.policy.request_timeout_seconds,
            transport_retries=step.policy.transport_retries,
            step_timeout_seconds=min(remaining, step.policy.request_timeout_seconds + 5),
            method="GET",
            output=response,
        )
        last_argv = result.argv
        status = kubernetes_http_status(result)
        if result.returncode != 0 or status != 200:
            return StepOutcome(
                argv=result.argv,
                returncode=result.returncode or PLAN_CONTRACT_RETURN_CODE,
                stderr=(
                    result.stderr
                    or f"Kubernetes rollout read returned HTTP {status or 'unavailable'}"
                )[:2000],
                duration_ms=_elapsed_ms(started, clock),
            )
        try:
            current = _parse_rollout(response, step)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _contract_failure(
                str(exc), argv=result.argv, duration_ms=_elapsed_ms(started, clock)
            )
        observations += 1
        last = current
        terminal = _terminal_outcome(step, current, expected_generation)
        if terminal is not None:
            outcome, returncode, message = terminal
            return StepOutcome(
                argv=result.argv,
                returncode=returncode,
                stdout=message if returncode == 0 else "",
                stderr=message if returncode != 0 else "",
                duration_ms=_elapsed_ms(started, clock),
                release_verification=_evidence(
                    step, current, expected_generation, observations, outcome
                ),
            )
        remaining = deadline - clock()
        if remaining <= 0:
            return _timeout_outcome(step, last, observations, started, clock, last_argv)
        sleeper(min(step.policy.poll_interval_seconds, remaining))


def _terminal_outcome(
    step: ResolvedKubernetesRolloutVerifyStep,
    current: _RolloutObservation,
    expected_generation: int,
) -> tuple[KubernetesRolloutVerificationOutcome, int, str] | None:
    if current.generation != expected_generation or current.image != step.expected_image:
        return (
            KubernetesRolloutVerificationOutcome.SUPERSEDED,
            PLAN_CONTRACT_RETURN_CODE,
            "Kubernetes rollout target was superseded before readiness could be proven",
        )
    if (
        current.progressing_status == "False"
        and current.progressing_reason == "ProgressDeadlineExceeded"
    ):
        return (
            KubernetesRolloutVerificationOutcome.PROGRESS_DEADLINE_EXCEEDED,
            PLAN_CONTRACT_RETURN_CODE,
            "Kubernetes Deployment reported ProgressDeadlineExceeded",
        )
    if _ready(current, expected_generation):
        return (
            KubernetesRolloutVerificationOutcome.READY,
            0,
            "Kubernetes rollout verified for the exact admitted Deployment generation",
        )
    return None


def _ready(current: _RolloutObservation, expected_generation: int) -> bool:
    return (
        current.observed_generation is not None
        and current.observed_generation >= expected_generation
        and current.replicas == current.desired_replicas
        and current.updated_replicas == current.desired_replicas
        and current.available_replicas == current.desired_replicas
        and current.unavailable_replicas == 0
    )


def _timeout_outcome(
    step: ResolvedKubernetesRolloutVerifyStep,
    last: _RolloutObservation | None,
    observations: int,
    started: float,
    clock: Callable[[], float],
    argv: tuple[str, ...],
) -> StepOutcome:
    if last is None:
        return StepOutcome(
            argv=argv or ("kubernetes_rollout_verify", step.step_id),
            returncode=PLAN_TIMEOUT_RETURN_CODE,
            stderr="Kubernetes rollout verification timed out before one authoritative Deployment observation",
            duration_ms=_elapsed_ms(started, clock),
        )
    evidence = _evidence(
        step,
        last,
        _expected_generation(step.expected_generation),
        observations,
        KubernetesRolloutVerificationOutcome.TIMEOUT,
    )
    return StepOutcome(
        argv=argv,
        returncode=PLAN_TIMEOUT_RETURN_CODE,
        stderr="Kubernetes rollout verification reached its declared timeout before readiness",
        duration_ms=_elapsed_ms(started, clock),
        release_verification=evidence,
    )


def _evidence(
    step: ResolvedKubernetesRolloutVerifyStep,
    current: _RolloutObservation,
    expected_generation: int,
    observations: int,
    outcome: KubernetesRolloutVerificationOutcome,
) -> KubernetesRolloutVerificationEvidence:
    return KubernetesRolloutVerificationEvidence(
        outcome=outcome,
        api_authority=step.target.api.authority,
        namespace=step.target.namespace,
        deployment=step.target.name,
        container=step.target.container,
        expected_generation=expected_generation,
        expected_image=step.expected_image,
        observations=observations,
        final_resource_version=current.resource_version,
        final_generation=current.generation,
        final_image=current.image,
        observed_generation=current.observed_generation,
        desired_replicas=current.desired_replicas,
        replicas=current.replicas,
        updated_replicas=current.updated_replicas,
        available_replicas=current.available_replicas,
        unavailable_replicas=current.unavailable_replicas,
        progressing_status=current.progressing_status,
        progressing_reason=current.progressing_reason,
        timeout_seconds=step.timeout_seconds,
        poll_interval_seconds=step.policy.poll_interval_seconds,
        request_timeout_seconds=step.policy.request_timeout_seconds,
        transport_retries=step.policy.transport_retries,
        tls_verified=step.policy.tls_verify,
    )


def _parse_rollout(path: Path, step: ResolvedKubernetesRolloutVerifyStep) -> _RolloutObservation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("apiVersion") != "apps/v1"
        or payload.get("kind") != "Deployment"
    ):
        raise ValueError("Fleet Kubernetes rollout response is not one apps/v1 Deployment")
    metadata = _mapping(payload.get("metadata"), "metadata")
    spec = _mapping(payload.get("spec"), "spec")
    status = _mapping(payload.get("status", {}), "status")
    if (
        metadata.get("name") != step.target.name
        or metadata.get("namespace") != step.target.namespace
    ):
        raise ValueError("Fleet Kubernetes rollout API returned a different Deployment identity")
    resource_version = _non_empty_string(metadata.get("resourceVersion"), "resourceVersion")
    generation = _non_negative_int(metadata.get("generation"), "generation", minimum=1)
    image = _target_image(spec, step.target.container)
    desired_replicas = _non_negative_int(spec.get("replicas", 1), "spec.replicas")
    observed_generation = status.get("observedGeneration")
    if observed_generation is not None:
        observed_generation = _non_negative_int(observed_generation, "status.observedGeneration")
    progressing_status, progressing_reason = _progressing_condition(status.get("conditions", []))
    return _RolloutObservation(
        resource_version=resource_version,
        generation=generation,
        image=image,
        observed_generation=observed_generation,
        desired_replicas=desired_replicas,
        replicas=_status_count(status, "replicas"),
        updated_replicas=_status_count(status, "updatedReplicas"),
        available_replicas=_status_count(status, "availableReplicas"),
        unavailable_replicas=_status_count(status, "unavailableReplicas"),
        progressing_status=progressing_status,
        progressing_reason=progressing_reason,
    )


def _target_image(spec: dict[str, object], container_name: str) -> str:
    template = _mapping(spec.get("template"), "spec.template")
    pod_spec = _mapping(template.get("spec"), "spec.template.spec")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        raise ValueError("Fleet Kubernetes rollout Deployment has no pod container list")
    matches = [
        item for item in containers if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Fleet Kubernetes rollout requires exactly one container named {container_name!r}"
        )
    return _non_empty_string(matches[0].get("image"), "target container image")


def _progressing_condition(
    value: object,
) -> tuple[Literal["True", "False", "Unknown"] | None, str | None]:
    if not isinstance(value, list):
        raise ValueError("Fleet Kubernetes rollout conditions must be a list")
    matches = [
        item for item in value if isinstance(item, dict) and item.get("type") == "Progressing"
    ]
    if len(matches) > 1:
        raise ValueError(
            "Fleet Kubernetes rollout response contains duplicate Progressing conditions"
        )
    if not matches:
        return None, None
    status = matches[0].get("status")
    if status not in {"True", "False", "Unknown"}:
        raise ValueError("Fleet Kubernetes rollout Progressing condition has invalid status")
    reason = matches[0].get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 120):
        raise ValueError("Fleet Kubernetes rollout Progressing reason is invalid")
    return cast(Literal["True", "False", "Unknown"], status), reason


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"Fleet Kubernetes rollout response is missing {label}")
    return value


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"Fleet Kubernetes rollout {label} is invalid")
    return value


def _non_negative_int(value: object, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"Fleet Kubernetes rollout {label} must be an integer >= {minimum}")
    return value


def _status_count(status: dict[str, object], name: str) -> int:
    return _non_negative_int(status.get(name, 0), f"status.{name}")


def _expected_generation(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise ValueError(
            "Fleet Kubernetes rollout expected generation did not resolve to a positive integer"
        )
    return int(value)


def _raise_if_cancelled(cancel_requested: Callable[[], bool] | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError("Fleet Kubernetes rollout verification cancelled")


def _contract_failure(
    message: str, *, argv: tuple[str, ...] = (), duration_ms: int = 0
) -> StepOutcome:
    return StepOutcome(
        argv=argv,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stderr=message[:2000],
        duration_ms=duration_ms,
    )


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return max(0, int((clock() - started) * 1000))
