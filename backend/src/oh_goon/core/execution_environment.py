"""Phase 2: immutable, deployment-selected execution-environment authority.

This is a projection of one configured backend, never a second selector or an
admission decision. Enforcer will consume this contract in the next phases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest

EXECUTION_ENVIRONMENT_SCHEMA = "oh-goon.execution-environment/v1"
SUPPORTED_BACKENDS = frozenset({"bubblewrap", "opensandbox"})


def configured_environment_backend(execution: object) -> str:
    """Require exactly execution.environment.backend; never infer or fall back."""
    if not isinstance(execution, dict) or set(execution) != {"environment"}:
        raise FleetPlanInvalid("Executor must declare exactly execution.environment.backend")
    environment = execution["environment"]
    if not isinstance(environment, dict) or set(environment) != {"backend"}:
        raise FleetPlanInvalid("Executor must declare exactly execution.environment.backend")
    backend = environment["backend"]
    if not isinstance(backend, str) or backend not in SUPPORTED_BACKENDS:
        raise FleetPlanInvalid(
            "Unsupported execution.environment.backend; choose exactly 'bubblewrap' or 'opensandbox'"
        )
    return backend


@dataclass(frozen=True, slots=True)
class ResolvedExecutionEnvironment:
    """Frozen identity-bearing projection, not a runnable capability or authorization."""

    schema: str
    backend: str
    executor_id: str
    executor_generation: int
    backend_configuration_identity: str
    capability_manifest_identity: str
    execution_environment_identity: str

    @classmethod
    def from_authorities(
        cls,
        *,
        backend: str,
        executor_id: str,
        executor_generation: int,
        backend_configuration_identity: str,
        capability_statement: Mapping[str, Any],
        deployment_authority: Mapping[str, Any],
    ) -> ResolvedExecutionEnvironment:
        if backend not in SUPPORTED_BACKENDS or capability_statement.get("backend_id") != backend:
            raise FleetPlanInvalid("ExecutionEnvironment capability/backend identity mismatch")
        if not executor_id or executor_generation < 1 or not backend_configuration_identity:
            raise FleetPlanInvalid("ExecutionEnvironment authority is incomplete")
        capability_id = digest("oh-goon/backend-capability-manifest/v1", canonical_bytes(dict(capability_statement)))
        identity = digest(
            "oh-goon/execution-environment/v1",
            canonical_bytes({
                "schema": EXECUTION_ENVIRONMENT_SCHEMA,
                "backend": backend,
                "executor": {"id": executor_id, "generation": executor_generation},
                "backend_configuration_identity": backend_configuration_identity,
                "capability_manifest_identity": capability_id,
                "deployment_authority": dict(deployment_authority),
            }),
        )
        return cls(
            schema=EXECUTION_ENVIRONMENT_SCHEMA,
            backend=backend,
            executor_id=executor_id,
            executor_generation=executor_generation,
            backend_configuration_identity=backend_configuration_identity,
            capability_manifest_identity=capability_id,
            execution_environment_identity=identity,
        )

    def require_unchanged(self, fresh: ResolvedExecutionEnvironment) -> None:
        if self != fresh:
            raise FleetPlanInvalid("ExecutionEnvironment identity/capability/generation drift; fresh admission required")
