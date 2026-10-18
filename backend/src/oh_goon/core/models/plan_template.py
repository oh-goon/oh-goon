from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.template_identity import TemplateClassification


class PlanTemplateSummary(ImmutableModel):
    template_key: str = Field(min_length=1, max_length=128)
    classification: TemplateClassification
    description: str = Field(min_length=1, max_length=1000)
    template_path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanTemplateDocument(PlanTemplateSummary):
    content: str = Field(min_length=1)


class PlanTemplateCatalog(ImmutableModel):
    templates: tuple[PlanTemplateSummary, ...] = Field(default_factory=tuple)
