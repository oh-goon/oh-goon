from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from oh_goon.core.models.fleet_plan import (
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
)
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_kubernetes_execution import execute_kubernetes_set_image
from oh_goon.integrations.fleet_plan_kubernetes_rollout_execution import (
    execute_kubernetes_rollout_verify,
)
from oh_goon.integrations.fleet_plan_kubernetes_transport import KubernetesCommandRunner


def execute_kubernetes_step(
    repository: Path,
    step: ResolvedKubernetesSetImageStep | ResolvedKubernetesRolloutVerifyStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    host_environment: Mapping[str, str],
    command_runner: KubernetesCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    """Dispatch one explicit Kubernetes primitive without merging mutation and observation."""

    if isinstance(step, ResolvedKubernetesSetImageStep):
        return execute_kubernetes_set_image(
            repository,
            step,
            paths,
            environment=environment,
            host_environment=host_environment,
            command_runner=command_runner,
            cancel_requested=cancel_requested,
        )
    return execute_kubernetes_rollout_verify(
        step,
        paths,
        environment=environment,
        host_environment=host_environment,
        command_runner=command_runner,
        cancel_requested=cancel_requested,
    )
