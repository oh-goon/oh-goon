from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from oh_goon.core.errors import ExternalEffectOutcomeUnknown
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedKubernetesSetImageStep
from oh_goon.core.models.fleet_plan_kubernetes import (
    ExactCurrentKubernetesImage,
    is_digest_image_reference,
)
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import fleet_plan_kubernetes_observation as kubernetes_observation
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    from_command,
    process_boundary_settled,
)
from oh_goon.integrations.fleet_plan_kubernetes_release import (
    KubernetesDeploymentObservation,
    kubernetes_release_evidence,
)
from oh_goon.integrations.fleet_plan_kubernetes_transport import (
    KubernetesCommandRunner,
    KubernetesPrivateConfig,
    cleanup_kubernetes_private_config,
    kubernetes_http_status,
    prepare_kubernetes_private_config,
    run_kubernetes_request,
)

_KNOWN_REJECTION_HTTP = frozenset({400, 401, 403, 404, 405, 409, 415, 422})


@dataclass(frozen=True)
class _KubernetesPrivateConfig:
    transport: KubernetesPrivateConfig
    before_response: Path
    patch_request: Path
    patch_response: Path


def execute_kubernetes_set_image(
    repository: Path,
    step: ResolvedKubernetesSetImageStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    host_environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    """Commit exactly one Deployment image spec change; rollout is a separate concern."""

    outputs = kubernetes_observation.resolve_kubernetes_set_image_output_paths(repository, step)
    kubernetes_observation.clear_kubernetes_set_image_outputs(outputs)
    if not is_digest_image_reference(step.image):
        return _contract_failure(
            "Fleet Kubernetes desired image was not bound to one digest-qualified OCI reference"
        )
    try:
        config = _prepare_private_config(step, paths, host_environment)
    except (KeyError, OSError, ValueError) as exc:
        return _contract_failure(str(exc))
    try:
        outcome = _execute_with_private_config(
            step,
            outputs,
            config,
            environment=environment,
            command_runner=command_runner,
            cancel_requested=cancel_requested,
        )
    except BaseException as exc:
        _cleanup_with_note(config.transport, exc)
        raise
    cleanup_error = _cleanup_error(config.transport)
    if cleanup_error is None:
        return outcome
    kubernetes_observation.clear_kubernetes_set_image_outputs(outputs)
    return replace(
        _contract_failure(
            f"Private Kubernetes credential cleanup failed: {cleanup_error}", outcome
        ),
        resource_cleanup="unverified",
    )


def _execute_with_private_config(
    step: ResolvedKubernetesSetImageStep,
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    config: _KubernetesPrivateConfig,
    *,
    environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    observed = _read_current_deployment(
        step,
        config,
        environment=environment,
        command_runner=command_runner,
        cancel_requested=cancel_requested,
    )
    if isinstance(observed, StepOutcome):
        return observed
    before_result, before_status, before = observed
    precondition = _precondition_outcome(step, outputs, before_result, before_status, before)
    if precondition is not None:
        return precondition
    if before.image == step.image:
        return _already_desired_outcome(step, outputs, before_result, before_status, before)
    return _commit_patch(
        step,
        outputs,
        config,
        before_result,
        before,
        environment=environment,
        command_runner=command_runner,
        cancel_requested=cancel_requested,
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


def _read_current_deployment(
    step: ResolvedKubernetesSetImageStep,
    config: _KubernetesPrivateConfig,
    *,
    environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[CommandResult, int, KubernetesDeploymentObservation] | StepOutcome:
    result = _request(
        step,
        config,
        command_runner=command_runner,
        environment=environment,
        cancel_requested=cancel_requested,
        method="GET",
        output=config.before_response,
    )
    status = kubernetes_http_status(result)
    if process_boundary_settled(from_command(result)) is not True:
        return _outcome(result)
    if result.returncode != 0:
        return _read_failure(result, status, operation="read Deployment")
    if status != 200:
        return _contract_failure(
            f"Fleet Kubernetes Deployment read returned unexpected HTTP {status}", _outcome(result)
        )
    try:
        observation = kubernetes_observation.parse_kubernetes_deployment(
            config.before_response, step
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _contract_failure(str(exc), _outcome(result))
    return result, status, observation


def _precondition_outcome(
    step: ResolvedKubernetesSetImageStep,
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    result: CommandResult,
    status: int,
    before: KubernetesDeploymentObservation,
) -> StepOutcome | None:
    if not isinstance(step.precondition, ExactCurrentKubernetesImage):
        return None
    if before.image == step.precondition.image:
        return None
    rejected = _contract_failure(
        "Fleet Kubernetes current-image precondition failed: "
        f"observed {before.image!r}, expected {step.precondition.image!r}",
        _outcome(result),
    )
    try:
        kubernetes_observation.write_kubernetes_set_image_evidence(
            step,
            outputs,
            before=before,
            after=None,
            mutated=False,
            already_desired=before.image == step.image,
            http_status=status,
            result="precondition_failed",
        )
    except OSError as exc:
        return _local_evidence_failure(
            outputs,
            "Kubernetes precondition rejection was observed but local evidence could not be "
            f"finalized: {exc.__class__.__name__}",
            rejected,
        )
    return rejected


def _already_desired_outcome(
    step: ResolvedKubernetesSetImageStep,
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    result: CommandResult,
    status: int,
    before: KubernetesDeploymentObservation,
) -> StepOutcome:
    released = replace(
        from_command(result),
        argv=("curl",),
        returncode=0,
        stdout="Kubernetes Deployment already uses the desired immutable image",
        stdout_evidence=None,
        duration_ms=result.duration_ms,
        release_effect=kubernetes_release_evidence(
            step, before=before, after=before, outcome="ALREADY_DESIRED"
        ),
    )
    try:
        kubernetes_observation.write_kubernetes_set_image_success_outputs(outputs, before)
        kubernetes_observation.write_kubernetes_set_image_evidence(
            step,
            outputs,
            before=before,
            after=before,
            mutated=False,
            already_desired=True,
            http_status=status,
            result="already_desired",
        )
    except OSError as exc:
        return _local_evidence_failure(
            outputs,
            "Kubernetes desired state was observed but local release evidence could not be "
            f"finalized: {exc.__class__.__name__}",
            released,
        )
    if step.policy.if_already_desired == "fail":
        return _contract_failure(
            "Fleet Kubernetes Deployment already uses the desired immutable image", released
        )
    return released


def _commit_patch(
    step: ResolvedKubernetesSetImageStep,
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    config: _KubernetesPrivateConfig,
    before_result: CommandResult,
    before: KubernetesDeploymentObservation,
    *,
    environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    _write_patch(config.patch_request, step, before)
    result = _request(
        step,
        config,
        command_runner=command_runner,
        environment=environment,
        cancel_requested=cancel_requested,
        method="PATCH",
        output=config.patch_response,
        body=config.patch_request,
    )
    status = kubernetes_http_status(result)
    rejected = _known_patch_rejection(step, outputs, before_result, before, result, status)
    if rejected is not None:
        return rejected
    _require_authoritative_patch_success(result, status)
    try:
        after = kubernetes_observation.parse_kubernetes_deployment(config.patch_response, step)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes image PATCH reported success but the returned Deployment could not be "
            f"validated: {exc}"
        ) from exc
    _validate_committed_transition(before, after, step)
    released = replace(
        from_command(result),
        argv=("curl",),
        returncode=0,
        stdout="Kubernetes Deployment image spec update committed; rollout readiness not observed",
        stdout_evidence=None,
        stderr=result.stderr,
        duration_ms=before_result.duration_ms + result.duration_ms,
        release_effect=kubernetes_release_evidence(
            step, before=before, after=after, outcome="COMMITTED"
        ),
    )
    try:
        kubernetes_observation.write_kubernetes_set_image_success_outputs(outputs, after)
        kubernetes_observation.write_kubernetes_set_image_evidence(
            step,
            outputs,
            before=before,
            after=after,
            mutated=True,
            already_desired=False,
            http_status=status,
            result="spec_update_committed",
        )
    except OSError as exc:
        return _local_evidence_failure(
            outputs,
            "Kubernetes image mutation committed but local release evidence could not be finalized: "
            f"{exc.__class__.__name__}",
            released,
        )
    return released


def _known_patch_rejection(
    step: ResolvedKubernetesSetImageStep,
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    before_result: CommandResult,
    before: KubernetesDeploymentObservation,
    result: CommandResult,
    status: int,
) -> StepOutcome | None:
    if result.returncode == 0:
        return None
    if status not in _KNOWN_REJECTION_HTTP:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes image PATCH did not return an authoritative rejection/success response; "
            f"HTTP={status or 'unavailable'} curl_exit={result.returncode}. "
            "Automatic replay is blocked because the remote mutation outcome may be unknown."
        )
    rejected = _outcome(result, before_result.duration_ms)
    try:
        kubernetes_observation.write_kubernetes_set_image_evidence(
            step,
            outputs,
            before=before,
            after=None,
            mutated=False,
            already_desired=False,
            http_status=status,
            result="server_rejected",
        )
    except OSError as exc:
        return _local_evidence_failure(
            outputs,
            "Kubernetes server rejection was observed but local evidence could not be finalized: "
            f"{exc.__class__.__name__}",
            rejected,
        )
    return rejected


def _local_evidence_failure(
    outputs: kubernetes_observation.KubernetesSetImageOutputPaths,
    message: str,
    outcome: StepOutcome,
) -> StepOutcome:
    kubernetes_observation.clear_kubernetes_set_image_outputs(outputs)
    return _contract_failure(message, outcome)


def _require_authoritative_patch_success(result: CommandResult, status: int) -> None:
    if (
        result.returncode != 0
        or status < 200
        or status >= 300
        or process_boundary_settled(from_command(result)) is not True
    ):
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes image PATCH returned a non-success response whose commit status is not "
            f"classified as an authoritative rejection: HTTP {status or 'unavailable'}"
        )


def _prepare_private_config(
    step: ResolvedKubernetesSetImageStep,
    paths: AttemptPathLayout,
    host_environment: Mapping[str, str],
) -> _KubernetesPrivateConfig:
    transport = prepare_kubernetes_private_config(
        paths, step.credentials, host_environment, owner="kubernetes-set-image"
    )
    return _KubernetesPrivateConfig(
        transport=transport,
        before_response=paths.tmp / "kubernetes-before.json",
        patch_request=paths.tmp / "kubernetes-patch.json",
        patch_response=paths.tmp / "kubernetes-after.json",
    )


def _request(
    step: ResolvedKubernetesSetImageStep,
    config: _KubernetesPrivateConfig,
    *,
    command_runner: KubernetesCommandRunner,
    environment: Mapping[str, str],
    cancel_requested: Callable[[], bool] | None,
    method: str,
    output: Path,
    body: Path | None = None,
) -> CommandResult:
    return run_kubernetes_request(
        step.target,
        config.transport,
        command_runner=command_runner,
        environment=environment,
        cancel_requested=cancel_requested,
        request_timeout_seconds=step.policy.request_timeout_seconds,
        transport_retries=step.policy.transport_retries,
        step_timeout_seconds=step.timeout_seconds,
        method=method,
        output=output,
        body=body,
        content_type="application/json-patch+json" if body is not None else None,
    )


def _write_patch(
    path: Path,
    step: ResolvedKubernetesSetImageStep,
    before: KubernetesDeploymentObservation,
) -> None:
    index = before.container_index
    patch = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": before.resource_version},
        {
            "op": "test",
            "path": f"/spec/template/spec/containers/{index}/name",
            "value": step.target.container,
        },
        {
            "op": "test",
            "path": f"/spec/template/spec/containers/{index}/image",
            "value": before.image,
        },
        {
            "op": "replace",
            "path": f"/spec/template/spec/containers/{index}/image",
            "value": step.image,
        },
    ]
    path.write_text(json.dumps(patch, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _validate_committed_transition(
    before: KubernetesDeploymentObservation,
    after: KubernetesDeploymentObservation,
    step: ResolvedKubernetesSetImageStep,
) -> None:
    if after.image != step.image:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes PATCH returned success but the returned Deployment image is not the desired digest"
        )
    if after.resource_version == before.resource_version:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes PATCH returned success without advancing resourceVersion"
        )
    if after.generation <= before.generation:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes PATCH returned success without advancing Deployment generation"
        )
    if after.container_index != before.container_index:
        raise ExternalEffectOutcomeUnknown(
            "Kubernetes PATCH returned success but target container index changed unexpectedly"
        )


def _outcome(result: CommandResult, extra_duration_ms: int = 0) -> StepOutcome:
    return replace(
        from_command(result),
        stdout="",
        stdout_evidence=None,
        stderr=result.stderr,
        duration_ms=result.duration_ms + extra_duration_ms,
    )


def _read_failure(result: CommandResult, status: int, *, operation: str) -> StepOutcome:
    outcome = _outcome(result)
    if status in _KNOWN_REJECTION_HTTP:
        return outcome
    return _contract_failure(
        f"Fleet Kubernetes could not {operation}; HTTP={status or 'unavailable'} curl_exit={result.returncode}",
        outcome,
    )


def _contract_failure(message: str, outcome: StepOutcome | None = None) -> StepOutcome:
    base = outcome or StepOutcome(
        argv=("curl",),
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout="",
        stderr="",
        duration_ms=0,
    )
    return replace(
        base,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stderr=(base.stderr + f"\n{message}").strip(),
    )
