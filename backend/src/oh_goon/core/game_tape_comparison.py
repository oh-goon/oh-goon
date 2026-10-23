from __future__ import annotations

from oh_goon.core.models.game_tape import (
    GameTape,
    GameTapeRepositoryDiff,
    GameTapeRepositoryExecution,
    GameTapeRunDiff,
    GameTapeStepDiff,
    GameTapeStepExecution,
)


def compare_game_tapes(base_tape: GameTape, compared_tape: GameTape) -> GameTapeRunDiff:
    """Compare two already-projected canonical Game Tapes."""
    base_repositories = {item.repository.repository_id: item for item in base_tape.repositories}
    compared_repositories = {
        item.repository.repository_id: item for item in compared_tape.repositories
    }
    repository_ids = tuple(dict.fromkeys((*base_repositories, *compared_repositories)))
    return GameTapeRunDiff(
        base_run_id=base_tape.fleet_run_id,
        compared_run_id=compared_tape.fleet_run_id,
        base_plan_id=base_tape.plan_id,
        compared_plan_id=compared_tape.plan_id,
        plan_changed=base_tape.plan_id != compared_tape.plan_id,
        base_duration_ms=base_tape.receipt.duration_ms,
        compared_duration_ms=compared_tape.receipt.duration_ms,
        duration_delta_ms=_delta(base_tape.receipt.duration_ms, compared_tape.receipt.duration_ms),
        repositories=tuple(
            _repository_diff(
                base_repositories.get(repository_id), compared_repositories.get(repository_id)
            )
            for repository_id in repository_ids
        ),
    )


def _repository_diff(
    base: GameTapeRepositoryExecution | None,
    compared: GameTapeRepositoryExecution | None,
) -> GameTapeRepositoryDiff:
    representative = compared or base
    assert representative is not None
    base_steps = _steps_by_id(base)
    compared_steps = _steps_by_id(compared)
    step_ids = tuple(dict.fromkeys((*base_steps, *compared_steps)))
    return GameTapeRepositoryDiff(
        repository_id=representative.repository.repository_id,
        repository_name=representative.repository.name,
        base_head_sha=base.fingerprint.repository_head_sha if base is not None else None,
        compared_head_sha=(
            compared.fingerprint.repository_head_sha if compared is not None else None
        ),
        base_branch=base.fingerprint.branch if base is not None else None,
        compared_branch=compared.fingerprint.branch if compared is not None else None,
        workspace_changed=_workspace_identity(base) != _workspace_identity(compared),
        runtime_changed=_runtime_identity(base) != _runtime_identity(compared),
        toolchain_changed=_toolchain_identity(base) != _toolchain_identity(compared),
        isolation_changed=_isolation_identity(base) != _isolation_identity(compared),
        steps=tuple(
            _step_diff(base_steps.get(step_id), compared_steps.get(step_id)) for step_id in step_ids
        ),
    )


def _steps_by_id(
    repository: GameTapeRepositoryExecution | None,
) -> dict[str, GameTapeStepExecution]:
    if repository is None:
        return {}
    return {step.step_id: step for step in repository.steps}


def _step_diff(
    base: GameTapeStepExecution | None,
    compared: GameTapeStepExecution | None,
) -> GameTapeStepDiff:
    representative = compared or base
    assert representative is not None
    return GameTapeStepDiff(
        step_id=representative.step_id,
        base_status=base.status if base is not None else None,
        compared_status=compared.status if compared is not None else None,
        base_duration_ms=base.duration_ms if base is not None else None,
        compared_duration_ms=compared.duration_ms if compared is not None else None,
        duration_delta_ms=_delta(
            base.duration_ms if base is not None else None,
            compared.duration_ms if compared is not None else None,
        ),
        command_changed=_command(base) != _command(compared),
        provenance_changed=_provenance(base) != _provenance(compared),
        base_attempt_count=base.attempt_count if base is not None else 0,
        compared_attempt_count=compared.attempt_count if compared is not None else 0,
        attempt_count_changed=_attempt_count(base) != _attempt_count(compared),
        tool_changed=_attempt_fact(base, "tool") != _attempt_fact(compared, "tool"),
        egress_changed=_attempt_fact(base, "egress") != _attempt_fact(compared, "egress"),
        isolation_changed=(
            _attempt_fact(base, "standard_isolation")
            != _attempt_fact(compared, "standard_isolation")
        ),
        resource_policy_changed=_resource_policy(base) != _resource_policy(compared),
        failure_signature_changed=_failure_signature(base) != _failure_signature(compared),
    )


def _command(step: GameTapeStepExecution | None) -> tuple[str, ...] | None:
    return step.command if step is not None else None


def _provenance(
    step: GameTapeStepExecution | None,
) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    if step is None:
        return None
    return tuple((item.name, item.sources) for item in step.environment)


def _attempt_count(step: GameTapeStepExecution | None) -> int:
    return step.attempt_count if step is not None else 0


def _attempt_fact(step: GameTapeStepExecution | None, field: str) -> tuple[object, ...] | None:
    if step is None:
        return None
    return tuple(getattr(attempt, field) for attempt in step.attempts)


def _resource_policy(step: GameTapeStepExecution | None) -> tuple[str | None, ...] | None:
    if step is None:
        return None
    return tuple(
        attempt.resources.policy_id if attempt.resources is not None else None
        for attempt in step.attempts
    )


def _failure_signature(step: GameTapeStepExecution | None) -> str | None:
    return step.failure.signature_id if step is not None and step.failure is not None else None


def _delta(base: int | None, compared: int | None) -> int | None:
    if base is None or compared is None:
        return None
    return compared - base


def _workspace_identity(repository: GameTapeRepositoryExecution | None) -> str | None:
    return repository.fingerprint.base_workspace_sha256 if repository is not None else None


def _runtime_identity(repository: GameTapeRepositoryExecution | None) -> str | None:
    return repository.fingerprint.runtime_root_sha256 if repository is not None else None


def _toolchain_identity(repository: GameTapeRepositoryExecution | None) -> str | None:
    return repository.fingerprint.host_toolchain_id if repository is not None else None


def _isolation_identity(
    repository: GameTapeRepositoryExecution | None,
) -> tuple[str | None, str | None, str | None] | None:
    if repository is None:
        return None
    fingerprint = repository.fingerprint
    return (
        fingerprint.isolation_backend_id,
        fingerprint.isolation_configuration_id,
        fingerprint.isolation_kernel_boundary,
    )
