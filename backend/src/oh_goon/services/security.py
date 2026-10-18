from __future__ import annotations

from collections import defaultdict

from oh_goon.core.capability_registry import recipe_capability
from oh_goon.core.errors import RunNotFound, SecurityRunNotFound
from oh_goon.core.models.operation import RecipeOperationEvidence
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
        grouped: dict[tuple[str, str | None], dict[str, object]] = defaultdict(
            lambda: {"aliases": set(), "fixed": set(), "summary": None, "repositories": {}}
        )
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
            findings = evidence.recipe.findings
            if not findings:
                clean += 1
                continue
            affected_ids.add(entry.repository.repository_id)
            for finding in findings:
                key = (finding.advisory_id, finding.package)
                row = grouped[key]
                row["aliases"].update(finding.aliases)  # type: ignore[union-attr]
                row["fixed"].update(finding.fixed_versions)  # type: ignore[union-attr]
                row["summary"] = row["summary"] or finding.summary
                row["repositories"][entry.repository.repository_id] = entry.repository  # type: ignore[index]
        findings = [
            FleetSecurityFinding(
                advisory_id=advisory,
                package=package,
                aliases=sorted(row["aliases"]),  # type: ignore[arg-type]
                fixed_versions=sorted(row["fixed"]),  # type: ignore[arg-type]
                summary=row["summary"] if isinstance(row["summary"], str) else None,
                repositories=sorted(
                    row["repositories"].values(), key=lambda item: item.name.casefold()
                ),  # type: ignore[union-attr]
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
            findings=findings,
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
            documents=documents,
        )
