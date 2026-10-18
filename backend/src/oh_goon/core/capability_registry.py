from __future__ import annotations

from types import MappingProxyType

from oh_goon.core.capability_engine_definitions import ENGINE_CAPABILITIES
from oh_goon.core.capability_migration_definitions import MIGRATION_CAPABILITIES
from oh_goon.core.capability_model import CapabilityDefinition, ResolvedCapabilityIntent
from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES
from oh_goon.core.capability_recipe_definitions import RECIPE_CAPABILITIES
from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.capability import (
    CapabilityInfo,
    CapabilityRequirementTarget,
    CapabilitySnapshot,
    CapabilityTargetKind,
    capability_target_kind,
)
from oh_goon.core.models.target import OperationTarget, UvRecipeTarget
from oh_goon.core.vocabulary import CommandSafety, OperationAction, UvRecipeId

CAPABILITY_DEFINITIONS: tuple[CapabilityDefinition, ...] = (
    *ENGINE_CAPABILITIES,
    *RECIPE_CAPABILITIES,
    *MIGRATION_CAPABILITIES,
    *PLAN_CAPABILITIES,
)


class CapabilityRegistry:
    def __init__(self, definitions: tuple[CapabilityDefinition, ...]) -> None:
        self._definitions = definitions
        ids = [item.capability_id for item in definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("Capability IDs must be unique")
        self._by_id = MappingProxyType({item.capability_id: item for item in definitions})

        recipe_definitions = tuple(item for item in definitions if item.recipe is not None)
        recipe_ids = [item.recipe for item in recipe_definitions]
        if set(recipe_ids) != set(UvRecipeId) or len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("Every UV recipe must have exactly one capability definition")
        self._recipe_definitions = recipe_definitions
        self._by_recipe = MappingProxyType(
            {item.recipe: item for item in recipe_definitions if item.recipe is not None}
        )

        by_intent: dict[
            tuple[OperationAction, CapabilityTargetKind, UvRecipeId | None], CapabilityDefinition
        ] = {}
        for definition in definitions:
            key = _definition_intent_key(definition)
            if key in by_intent:
                other = by_intent[key]
                raise ValueError(
                    "Capability intent must resolve uniquely: "
                    f"{other.capability_id} and {definition.capability_id}"
                )
            by_intent[key] = definition
        self._by_intent = MappingProxyType(by_intent)

        for definition in definitions:
            if definition.invalidates_all and definition.invalidates_capabilities:
                raise ValueError(
                    f"Capability {definition.capability_id} cannot combine invalidates_all with "
                    "explicit invalidates_capabilities"
                )
            if (
                definition.safety is CommandSafety.WORKTREE_MUTATION
                and not definition.invalidates_all
                and not definition.invalidates_capabilities
            ):
                raise ValueError(
                    f"Worktree-mutating capability {definition.capability_id} must declare "
                    "result invalidation"
                )
            unknown_invalidations = sorted(
                set(definition.invalidates_capabilities) - set(self._by_id)
            )
            if unknown_invalidations:
                raise ValueError(
                    f"Capability {definition.capability_id} invalidates unknown capabilities: "
                    f"{unknown_invalidations}"
                )
            for requirement in definition.requirements:
                required = self._by_id.get(requirement.capability_id)
                if required is None:
                    raise ValueError(
                        f"Capability {definition.capability_id} requires unknown capability "
                        f"{requirement.capability_id}"
                    )
                if requirement.target_binding is CapabilityRequirementTarget.NONE:
                    if required.target_kind is not CapabilityTargetKind.NONE:
                        raise ValueError(
                            f"Capability {definition.capability_id} requires {required.capability_id} "
                            "without a target, but the required capability is targeted"
                        )
                elif required.target_kind is not definition.target_kind:
                    raise ValueError(
                        f"Capability {definition.capability_id} requires same-target capability "
                        f"{required.capability_id} with a different target kind"
                    )

    def definitions(self) -> tuple[CapabilityDefinition, ...]:
        return self._definitions

    def by_id(self, capability_id: str) -> CapabilityDefinition:
        try:
            return self._by_id[capability_id]
        except KeyError as exc:
            raise CapabilityUnavailable(f"Unknown capability: {capability_id}") from exc

    def public_info(self, *, fleet_only: bool = False) -> list[CapabilityInfo]:
        return [
            item.public_info()
            for item in self._definitions
            if not fleet_only or item.fleet_supported
        ]

    def recipe_definitions(self, *, configurable: bool | None = None) -> list[CapabilityDefinition]:
        if configurable is None:
            return list(self._recipe_definitions)
        return [item for item in self._recipe_definitions if item.user_configurable is configurable]

    def recipe(self, recipe: UvRecipeId) -> CapabilityDefinition:
        try:
            return self._by_recipe[recipe]
        except KeyError as exc:
            raise CapabilityUnavailable(
                f"Capability registry invariant violated for recipe {recipe.value}"
            ) from exc

    def resolve(
        self, action: OperationAction, target: OperationTarget | None = None
    ) -> CapabilityDefinition:
        key = _intent_key(action, target)
        try:
            return self._by_intent[key]
        except KeyError as exc:
            target_label = getattr(target, "kind", None) or "none"
            raise CapabilityUnavailable(
                f"Unsupported operation capability: action={action.value}, target={target_label}"
            ) from exc

    def resolve_intent(
        self, action: OperationAction, target: OperationTarget | None = None
    ) -> ResolvedCapabilityIntent:
        return ResolvedCapabilityIntent(
            definition=self.resolve(action, target),
            target=target,
        )


def _definition_intent_key(
    definition: CapabilityDefinition,
) -> tuple[OperationAction, CapabilityTargetKind, UvRecipeId | None]:
    return (definition.action, definition.target_kind, definition.recipe)


def _intent_key(
    action: OperationAction, target: OperationTarget | None
) -> tuple[OperationAction, CapabilityTargetKind, UvRecipeId | None]:
    recipe = target.recipe if isinstance(target, UvRecipeTarget) else None
    return (action, capability_target_kind(target), recipe)


CAPABILITY_REGISTRY = CapabilityRegistry(CAPABILITY_DEFINITIONS)


def capability_definitions() -> tuple[CapabilityDefinition, ...]:
    return CAPABILITY_REGISTRY.definitions()


def capability_info(*, fleet_only: bool = False) -> list[CapabilityInfo]:
    return CAPABILITY_REGISTRY.public_info(fleet_only=fleet_only)


def capability_by_id(capability_id: str) -> CapabilityDefinition:
    return CAPABILITY_REGISTRY.by_id(capability_id)


def recipe_capability_definitions(
    *, configurable: bool | None = None
) -> list[CapabilityDefinition]:
    return CAPABILITY_REGISTRY.recipe_definitions(configurable=configurable)


def recipe_capability(recipe: UvRecipeId) -> CapabilityDefinition:
    return CAPABILITY_REGISTRY.recipe(recipe)


def capability_for(
    action: OperationAction, target: OperationTarget | None = None
) -> CapabilityDefinition:
    return CAPABILITY_REGISTRY.resolve(action, target)


def resolve_capability_intent(
    action: OperationAction, target: OperationTarget | None = None
) -> ResolvedCapabilityIntent:
    return CAPABILITY_REGISTRY.resolve_intent(action, target)


def resolve_bound_capability_intent(
    snapshot: CapabilitySnapshot,
    target: OperationTarget | None = None,
) -> ResolvedCapabilityIntent:
    """Re-bind queued durable intent only when current executable policy is identical."""
    definition = CAPABILITY_REGISTRY.by_id(snapshot.capability_id)
    if definition.durable_snapshot() != snapshot:
        raise CapabilityUnavailable(
            f"Capability policy changed since durable admission: {snapshot.capability_id}"
        )
    if not snapshot.matches_intent(definition.action, target):
        raise CapabilityUnavailable(
            "Capability identity no longer matches the queued durable target binding: "
            f"{snapshot.capability_id}"
        )
    return ResolvedCapabilityIntent(definition=definition, target=target)
