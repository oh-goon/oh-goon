from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.capability_model import (
    CapabilityRunnerKind,
    CapabilityTargetSelector,
    operation_evidence_kind,
)
from oh_goon.core.capability_registry import (
    CapabilityRegistry,
    capability_definitions,
    capability_for,
    capability_info,
    recipe_capability,
)
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.models.target import MigrationTarget, UvRecipeTarget
from oh_goon.core.vocabulary import CommandSafety, OperationAction, UvRecipeId


def test_registry_resolves_execution_policy_from_typed_intent() -> None:
    generic = capability_for(OperationAction.PLAN)
    recipe = capability_for(
        OperationAction.RECIPE, UvRecipeTarget(kind="recipe", recipe=UvRecipeId.AUDIT)
    )
    migration = capability_for(
        OperationAction.ADOPT_UV,
        MigrationTarget(
            kind="migration",
        ),
    )

    assert generic.capability_id == "plan"
    assert recipe.runner_kind is CapabilityRunnerKind.RECIPE
    assert recipe.target_kind is CapabilityTargetKind.RECIPE
    assert migration.runner_kind is CapabilityRunnerKind.MIGRATION
    assert migration.requires_uv_project is False


def test_every_uv_recipe_is_defined_by_one_capability() -> None:
    policies = [recipe_capability(recipe) for recipe in UvRecipeId]
    assert {policy.recipe for policy in policies} == set(UvRecipeId)
    assert len({policy.capability_id for policy in policies}) == len(UvRecipeId)
    assert recipe_capability(UvRecipeId.AUDIT).produces_security_findings is True
    assert recipe_capability(UvRecipeId.SBOM).produces_sbom is True


def test_public_capability_catalog_is_backend_driven() -> None:
    descriptors = capability_info(fleet_only=True)
    ids = {item.capability_id for item in descriptors}
    assert "initialize" in ids
    assert "dependency-update" not in ids
    assert "dependency-plan" not in ids
    assert "recipe:audit" in ids
    assert "adopt-uv" in ids
    assert "git-ready" not in ids


def test_recipe_definition_filters_are_registry_owned() -> None:
    from oh_goon.core.capability_registry import recipe_capability_definitions

    configurable = recipe_capability_definitions(configurable=True)
    fixed = recipe_capability_definitions(configurable=False)

    assert [item.recipe for item in configurable] == [UvRecipeId.RUN_TASK]
    assert UvRecipeId.RUN_TASK not in {item.recipe for item in fixed}
    assert len(capability_definitions()) > len(configurable) + len(fixed)


def test_registered_capabilities_exhaust_canonical_dispatch_vocabularies() -> None:
    definitions = capability_definitions()

    assert {item.action for item in definitions} == set(OperationAction)
    assert {item.runner_kind for item in definitions} == set(CapabilityRunnerKind)
    assert {item.selector for item in definitions} == set(CapabilityTargetSelector)
    assert {operation_evidence_kind(action) for action in OperationAction}


def test_registry_rejects_duplicate_capability_ids() -> None:
    definitions = capability_definitions()

    with pytest.raises(ValueError, match="Capability IDs must be unique"):
        CapabilityRegistry((*definitions, definitions[0]))


def test_registry_rejects_missing_recipe_definition() -> None:
    definitions = tuple(
        item for item in capability_definitions() if item.recipe is not UvRecipeId.AUDIT
    )

    with pytest.raises(ValueError, match="Every UV recipe"):
        CapabilityRegistry(definitions)


def test_registry_rejects_unknown_capability_invalidation_reference() -> None:
    definitions = capability_definitions()
    first = replace(definitions[0], invalidates_capabilities=frozenset({"missing-capability"}))

    with pytest.raises(ValueError, match="invalidates unknown capabilities"):
        CapabilityRegistry((first, *definitions[1:]))


def test_project_task_declares_unbounded_result_invalidation() -> None:
    capability = recipe_capability(UvRecipeId.RUN_TASK)

    assert capability.invalidates_all is True
    assert capability.invalidates_capabilities == frozenset()
    assert capability.durable_snapshot().invalidates_all is True


def test_registry_requires_worktree_mutation_to_declare_result_invalidation() -> None:
    definitions = capability_definitions()
    first = replace(definitions[0], safety=CommandSafety.WORKTREE_MUTATION)

    with pytest.raises(ValueError, match="must declare result invalidation"):
        CapabilityRegistry((first, *definitions[1:]))
