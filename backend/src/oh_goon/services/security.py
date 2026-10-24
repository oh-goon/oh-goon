from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from oh_goon.core.capability_registry import recipe_capability
from oh_goon.core.errors import RunNotFound, SecurityRunNotFound
from oh_goon.core.models.operation import RecipeOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.security import (
    FleetSbomBundle,
    FleetSbomDocument,
    FleetSecurityFinding,
    FleetSecuritySummary,
)
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import OperationAction, UvRecipeId
from oh_goon.runtime.state import LocalState
from oh_goon.services.repositories import RepositoryService


@dataclass
class _FindingGroup:
    aliases: set[str] = field(default_factory=set)
    fixed: set[str] = field(default_factory=set)
    summary: str | None = None
    repositories: dict[str, RepositoryIdentity] = field(default_factory=dict)


class SecurityService:
    """Aggregate UV audit/SBOM facts without persisting a second security database."""

    def __init__(self, repositories: RepositoryService, state: LocalState) -> None:
        self._repositories = repositories
        self._state = state

    def summary(self) -> FleetSecuritySummary:
        state = self._state.read_view()
        included = self._repositories.included(state)
        target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.AUDIT)
        capability_id = recipe_capability(UvRecipeId.AUDIT).capability_id
        audited = clean = stale = 0
        affected_ids: set[str] = set()
        grouped: dict[tuple[str, str | None], _FindingGroup] = defaultdict(_FindingGroup)
        for entry in included:
            current = state.journal.current_capability(entry.repository.path, capability_id, target)
            if current is None:
                continue
            if current.is_stale:
                stale += 1
                continue
            audited += 1
            evidence = current.report.evidence
            if not isinstance(evidence, RecipeOperationEvidence):
                continue
            observed_findings = evidence.recipe.findings
            if not observed_findings:
                clean += 1
                continue
            affected_ids.add(entry.repository.repository_id)
            for finding in observed_findings:
                key = (finding.advisory_id, finding.package)
                row = grouped[key]
                row.aliases.update(finding.aliases)
                row.fixed.update(finding.fixed_versions)
                row.summary = row.summary or finding.summary
                row.repositories[entry.repository.repository_id] = entry.repository
        findings = [
            FleetSecurityFinding(
                advisory_id=advisory,
                package=package,
                aliases=tuple(sorted(row.aliases)),
                fixed_versions=tuple(sorted(row.fixed)),
                summary=row.summary,
                repositories=tuple(
                    sorted(row.repositories.values(), key=lambda item: item.name.casefold())
                ),
            )
            for (advisory, package), row in grouped.items()
        ]
        findings.sort(key=lambda item: (item.advisory_id, item.package or ""))
        latest = state.journal.latest_run_exact(capability_id, target)
        return FleetSecuritySummary(
            durable_revision=state.durable_revision,
            journal_revision=state.journal.revision,
            repositories=len(included),
            repository_ids=tuple(sorted(entry.repository.repository_id for entry in included)),
            repository_instance_ids={
                entry.repository.repository_id: repository_subject_instance_id(
                    entry.repository.path
                )
                for entry in included
            },
            audited=audited,
            clean=clean,
            affected_repositories=len(affected_ids),
            stale=stale,
            findings=tuple(findings),
            latest_audit_run_id=latest.run.fleet_run_id if latest else None,
        )

    def sbom_bundle(self, fleet_run_id: str) -> FleetSbomBundle:
        state = self._state.read_view()
        try:
            detail = state.journal.required_run_detail(fleet_run_id)
        except RunNotFound as exc:
            raise SecurityRunNotFound(f"Unknown SBOM fleet run: {fleet_run_id}") from exc
        target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.SBOM)
        if detail.run.action is not OperationAction.RECIPE or detail.run.target != target:
            raise SecurityRunNotFound(f"Unknown SBOM fleet run: {fleet_run_id}")
        documents: list[FleetSbomDocument] = []
        for report in detail.operations:
            evidence = report.evidence
            if not isinstance(evidence, RecipeOperationEvidence):
                continue
            recipe = evidence.recipe
            if not isinstance(recipe.structured_output, dict):
                continue
            documents.append(
                FleetSbomDocument(
                    repository=report.repository,
                    operation_id=report.provenance.operation_id,
                    component_count=recipe.sbom_component_count or 0,
                    document=recipe.structured_output,
                )
            )
        return FleetSbomBundle(
            fleet_run_id=fleet_run_id,
            journal_revision=state.journal.revision,
            documents=tuple(documents),
        )
