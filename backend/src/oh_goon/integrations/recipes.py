from __future__ import annotations

from typing import cast

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import OperationTarget, UvRecipeTarget
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectBoundaryKind
from oh_goon.engine.command import CommandCancelledError, run_command
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.recipe_advisories import extract_security_findings
from oh_goon.integrations.recipe_command import decode_recipe_payload, prepare_recipe_command
from oh_goon.integrations.recipe_reporting import build_recipe_execution


class UvRecipeRunner:
    """Execute one capability-defined uv recipe using the durable admitted effect identity."""

    def effect_boundary(self, target: OperationTarget | None) -> EffectBoundaryKind:
        del target
        return EffectBoundaryKind.EXTERNAL

    def prepare(self, request: CapabilityExecutionRequest) -> UvRecipeRunner:
        target = request.attempt.target
        if not isinstance(target, UvRecipeTarget):
            raise CapabilityUnavailable("Recipe capability requires a recipe target")
        definition = request.definition
        if definition.recipe is not target.recipe:
            raise ValueError("Resolved recipe capability does not match target")
        return self

    @property
    def execution_input_id(self) -> None:
        return None

    def close(self) -> None:
        return None

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        attempt = request.attempt
        target = cast(UvRecipeTarget, attempt.target)
        definition = request.definition
        execution_policy = definition.recipe_execution
        assert execution_policy is not None
        started_at = utc_now()
        with prepare_recipe_command(target, definition) as prepared:
            try:
                result = run_command(
                    *prepared.argv,
                    cwd=attempt.repository.path,
                    timeout=3600.0,
                    cancel_requested=request.cancel_requested,
                )
            except CommandCancelledError as exc:
                raise OperationCancelledError(str(exc)) from exc
            payload = decode_recipe_payload(execution_policy.structured_json, result.stdout)
            findings = extract_security_findings(payload) if execution_policy.audit_findings else []
            return build_recipe_execution(
                attempt=attempt,
                started_at=started_at,
                definition=definition,
                argv=prepared.argv,
                result=result,
                payload=payload,
                findings=findings,
            )
