from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid

_TEMPLATE_SEGMENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TemplateClassification(StrEnum):
    PRODUCTION = "production"
    TEACHING_EXAMPLE = "teaching-example"
    INTERNAL_SELF_HOSTING = "internal-self-hosting"


_CATEGORY_CLASSIFICATION: dict[str, TemplateClassification] = {
    "advanced": TemplateClassification.TEACHING_EXAMPLE,
    "general": TemplateClassification.TEACHING_EXAMPLE,
    "kubernetes": TemplateClassification.PRODUCTION,
    "ml": TemplateClassification.TEACHING_EXAMPLE,
    "oci": TemplateClassification.PRODUCTION,
    "oh-goon": TemplateClassification.INTERNAL_SELF_HOSTING,
    "python": TemplateClassification.PRODUCTION,
    "security": TemplateClassification.PRODUCTION,
}


def template_key_parts(value: str, *, kind: str) -> tuple[str, str]:
    """Return one exact category/name identity; no aliases or path guessing exist."""

    if not isinstance(value, str):
        raise FleetPlanInvalid(f"{kind} import must be a string in category/name form")
    parts = value.split("/")
    if len(parts) != 2 or any(_TEMPLATE_SEGMENT.fullmatch(part) is None for part in parts):
        raise FleetPlanInvalid(
            f"{kind} import {value!r} must use exactly category/name with lowercase letters, numbers, and '-'"
        )
    return parts[0], parts[1]


def canonical_template_key(value: str, *, kind: str) -> str:
    category, name = template_key_parts(value, kind=kind)
    return f"{category}/{name}"


def template_classification(value: str, *, kind: str) -> TemplateClassification:
    """Classify one shipped template from its canonical category identity only.

    Classification is catalog policy, not Fleet YAML semantics. Keeping it outside the
    parser/resolver prevents portable Goons from gaining a hidden maturity field while
    still making the shipped catalog's production/teaching/internal boundary explicit.
    """

    category, _ = template_key_parts(value, kind=kind)
    try:
        return _CATEGORY_CLASSIFICATION[category]
    except KeyError as exc:
        raise FleetPlanInvalid(
            f"{kind} category {category!r} has no shipped template classification"
        ) from exc


def template_reference_allowed(
    owner: TemplateClassification, imported: TemplateClassification
) -> bool:
    """Return whether one shipped catalog class may directly reference another.

    Production may reference production only. Teaching examples may reuse production
    capabilities but never internal self-hosting machinery. Internal self-hosting may
    reuse production capabilities but never teaching examples.
    """

    if owner is TemplateClassification.PRODUCTION:
        return imported is TemplateClassification.PRODUCTION
    if owner is TemplateClassification.TEACHING_EXAMPLE:
        return imported is not TemplateClassification.INTERNAL_SELF_HOSTING
    return imported is not TemplateClassification.TEACHING_EXAMPLE


def template_segment(value: str, *, kind: str) -> str:
    if _TEMPLATE_SEGMENT.fullmatch(value) is None:
        raise FleetPlanInvalid(f"{kind} names use only lowercase letters, numbers, and '-'")
    return value


def import_step_id(template_key: str, occurrence: int) -> str:
    """Create one deterministic internal alias while preserving authored duplicates."""

    category, name = template_key_parts(template_key, kind="Goon")
    base = f"{category}-{name}"
    suffix = "" if occurrence == 1 else f"-{occurrence}"
    candidate = f"{base}{suffix}"
    if len(candidate) <= 40:
        return candidate
    # Compiled import aliases are internal identities, not authored Goon keys.
    # Keep the public 40-character Step identity contract and preserve collision
    # resistance instead of widening the model whenever a valid Goon name grows.
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    prefix_length = 40 - len(suffix) - len(digest) - 1
    return f"{base[:prefix_length]}-{digest}{suffix}"


def catalog_template_name(path: Path, *, kind: str) -> str | None:
    """Derive shipped catalog identity only from the canonical package layout."""
    plural = "goons" if kind == "goon" else "plans"
    source_name = "goon.yml" if kind == "goon" else "plan.yml"
    parts = path.parts
    if (
        len(parts) < 5
        or parts[-5] != "templates"
        or parts[-4] != plural
        or parts[-1] != source_name
    ):
        return None
    category, name = parts[-3], parts[-2]
    if _TEMPLATE_SEGMENT.fullmatch(category) is None or _TEMPLATE_SEGMENT.fullmatch(name) is None:
        return None
    return name
