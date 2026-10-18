from __future__ import annotations

from oh_goon.core.capability_registry import recipe_capability, recipe_capability_definitions
from oh_goon.core.models.recipe import UvRecipeSpec
from oh_goon.core.vocabulary import UvRecipeId


def _model(recipe: UvRecipeId) -> UvRecipeSpec:
    definition = recipe_capability(recipe)
    assert definition.safety is not None
    return UvRecipeSpec(
        capability_id=definition.capability_id,
        recipe=recipe,
        label=definition.label,
        description=definition.description,
        safety=definition.safety,
        parallel_safe=definition.parallel_safe,
        requires_uv_project=definition.requires_uv_project,
        produces_security_findings=definition.produces_security_findings,
        produces_sbom=definition.produces_sbom,
    )


def recipe_specs() -> list[UvRecipeSpec]:
    return [
        _model(policy.recipe)
        for policy in recipe_capability_definitions()
        if policy.recipe is not None
    ]


def recipe_spec(recipe: UvRecipeId) -> UvRecipeSpec:
    return _model(recipe)
