"""Guide evidence and human review into the original Fleet Plan/Run owners."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args

from oh_goon_catalog_renderer import (
    CatalogCandidate,
    CatalogComponentType,
    CatalogLifecycle,
    render_catalog,
)

from oh_goon.core.errors import (
    FleetPlanInvalid,
    ReviewedRepositorySourceStale,
)
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.catalog_showcase import (
    CatalogCandidateOptions,
    CatalogEvidenceQualification,
    CatalogExploration,
    CatalogGitAuthor,
    CatalogLanguageObservation,
    CatalogPreview,
    CatalogRelation,
    CatalogRelationshipQualification,
    CatalogSearchHit,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.hashmarks import observe_repository_exploration
from oh_goon.integrations.source_snapshot import snapshot_source_directory
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.repositories import RepositoryService

_CATALOG_PLAN = "general/catalog-render"
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_ENTRIES = 10_000


def _source_artifacts(
    authority: RepositorySourceAuthority,
) -> tuple[tuple[str, bytes], ...]:
    # Existing external-artifact hashing binds the exact reviewed source to the
    # same execution lock as the caller's candidate inputs, without a side store.
    return (("reviewed-source.json", authority.model_dump_json().encode("utf-8")),)


def _candidate_inputs(candidate: CatalogCandidate) -> dict[str, PlanInputValue]:
    return {"candidate_json": json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":"))}


class CatalogShowcaseService:
    def __init__(
        self,
        repositories: RepositoryService,
        plans: FleetPlanService,
        resolver: FleetPlanResolver,
        templates: PlanTemplateStore,
        git: GitService | None,
    ) -> None:
        self._repositories = repositories
        self._plans = plans
        self._resolver = resolver
        self._templates = templates
        self._git = git if git is not None else GitService()

    def _repository(self, repository_id: str, instance_id: str) -> RepositoryIdentity:
        selected = self._repositories.require_included(repository_id)
        if repository_subject_instance_id(selected.path) != instance_id:
            raise ReviewedRepositorySourceStale(
                "Selected repository changed since it was displayed"
            )
        return selected

    def explore(
        self,
        repository_id: str,
        *,
        expected_repository_instance_id: str,
        query: str,
        path: str | None,
    ) -> CatalogExploration:
        repository = self._repository(repository_id, expected_repository_instance_id)
        try:
            with TemporaryDirectory(prefix="oh-goon-catalog-source-") as directory:
                captured = Path(directory) / "source"
                identity = snapshot_source_directory(
                    repository.path,
                    captured,
                    label="catalog discovery source",
                    max_bytes=_MAX_SOURCE_BYTES,
                    max_entries=_MAX_SOURCE_ENTRIES,
                )
                authority = RepositorySourceAuthority.create(
                    subject_instance_id=expected_repository_instance_id,
                    source_snapshot=PlanSnapshotEvidence(
                        sha256=identity.sha256,
                        byte_count=identity.byte_count,
                        entry_count=identity.entry_count,
                    ),
                )
                evidence = observe_repository_exploration(captured, query=query, path=path)
        except (FleetPlanInvalid, OSError, ValueError) as exc:
            raise FleetPlanInvalid(
                "Repository evidence is unavailable under the selected source bounds or visibility policy"
            ) from exc
        # A changed League scope or replaced root cannot expose a completed old
        # observation. Captured content itself remains explicitly historical.
        self._repository(repository_id, expected_repository_instance_id)
        orientation, search = evidence["orientation"], evidence["search"]
        relations = evidence["relationships"]
        nodes = (
            {node["symbol_id"]: node["path"] for node in relations["nodes"]}
            if relations is not None
            else {}
        )
        author = self._last_author(repository.path, path)
        self._repository(repository_id, expected_repository_instance_id)
        relation_qualification = None
        if relations is not None:
            bounds = relations["bounds"]
            coverage = relations.get("resource_lifecycle", {}).get("coverage", {})
            relation_qualification = CatalogRelationshipQualification(
                max_depth=bounds.get("max_depth"),
                call_limit_per_symbol=bounds.get("call_limit_per_symbol"),
                ref_limit_per_symbol=bounds.get("ref_limit_per_symbol"),
                unresolved_reasons=tuple(item["reason"] for item in relations["unresolved_calls"]),
                external_or_unindexed_reasons=tuple(
                    item["reason"] for item in relations["external_or_unindexed_calls"]
                ),
                lifecycle_completeness=coverage.get("completeness"),
                lifecycle_truncated=coverage.get("truncated"),
            )
        return CatalogExploration(
            repository=repository,
            repository_instance_id=expected_repository_instance_id,
            source_authority=authority,
            producer_identity=evidence["producer_identity"],
            candidate_options=CatalogCandidateOptions(
                component_types=get_args(CatalogComponentType),
                lifecycles=get_args(CatalogLifecycle),
            ),
            languages=tuple(
                CatalogLanguageObservation(name=name, count=count)
                for name, count in sorted(orientation["languages"].items())
            ),
            hits=tuple(
                CatalogSearchHit(
                    path=hit["path"],
                    kind=hit["kind"],
                    name=hit["name"],
                    qualname=hit["qualname"],
                    signature=hit.get("signature"),
                    first_line=hit["lines"][0] if hit.get("lines") is not None else None,
                    last_line=hit["lines"][1] if hit.get("lines") is not None else None,
                )
                for hit in search["results"]
            ),
            relations=tuple(
                CatalogRelation(
                    source_path=edge["path"],
                    target=edge["target_text"],
                    kind="call",
                    line=edge.get("line"),
                    confidence=edge["confidence"],
                    resolved_paths=(nodes[edge["resolved_symbol_id"]],)
                    if edge.get("resolved_symbol_id") in nodes
                    else (),
                )
                for edge in (relations["edges"] if relations is not None else ())
            ),
            relationship_status=evidence["relationship_status"],
            relationship_message=(
                "Producer-bounded, one-hop symbol call observations; unresolved calls remain unproven."
                if relations is not None
                else "Select a localized symbol hit in this file for bounded producer relationships; unavailable evidence does not prove absence."
            ),
            relationship_qualification=relation_qualification,
            qualification=CatalogEvidenceQualification(
                generation=search.get("generation"),
                identity_generation=search.get("identity_generation"),
                freshness=search["freshness"],
                completeness=search["completeness"],
                truncated=search["truncated"],
                negative_evidence=search["negative_evidence"],
                admissibility_reasons=tuple(search["admissibility_reasons"]),
                repository_wide_completeness=evidence["coverage"]["repository_wide_completeness"],
            ),
            git_author=author,
            technical_evidence=evidence,
        )

    def _last_author(self, repository: Path, path: str | None) -> CatalogGitAuthor:
        qualification = (
            "Native Git's last matching commit author at the selected revision; "
            "this does not establish team ownership or authorship of uncommitted source."
        )
        if path is None:
            return CatalogGitAuthor(
                status="select-file", message="Select a source hit to inspect Git history."
            )
        runner = partial(
            self._git.observation_runner or self._git.command_runner or run_command,
            max_capture_bytes_per_stream=16_384,
        )
        try:
            exact_git = replace(self._git, observation_runner=runner)
            observed_root = exact_git.root(repository)
            if observed_root is None or observed_root.resolve() != repository.resolve():
                raise ValueError("Selected subject is not the exact native Git root")
            revision = self._git.run(
                "rev-parse", "--verify", "HEAD", cwd=repository, command_runner=runner
            )
            if revision.returncode != 0 or revision.stdout_truncated:
                raise ValueError("Git revision unavailable")
            head = revision.stdout.strip()
            if len(head) not in {40, 64} or any(
                character not in "0123456789abcdef" for character in head
            ):
                raise ValueError("Git revision unavailable")
            result = self._git.run(
                "--literal-pathspecs",
                "log",
                "-1",
                "--format=%H%x00%an%x00%aI",
                head,
                "--",
                path,
                cwd=repository,
                command_runner=runner,
            )
            fields = result.stdout.strip().split("\x00")
            if result.returncode != 0 or result.stdout_truncated or len(fields) != 3:
                raise ValueError("Git path history unavailable")
            commit, author, date = fields
            if not author or len(author) > 240 or any(ord(character) < 32 for character in author):
                raise ValueError("Git author unavailable")
            return CatalogGitAuthor(
                status="available",
                path=path,
                revision=head,
                commit=commit,
                author=author,
                date=date,
                message=qualification,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return CatalogGitAuthor(
                status="unavailable",
                path=path,
                message="Native Git history is unavailable for this selected path. "
                + qualification,
            )

    def preview(
        self,
        repository_id: str,
        *,
        source_authority: RepositorySourceAuthority,
        candidate: CatalogCandidate,
    ) -> CatalogPreview:
        repository = self._repository(repository_id, source_authority.subject_instance_id)
        source = self._templates.source(_CATALOG_PLAN)
        graph = self._resolver.analyze_source(source.path, root_snapshot=source)
        plan = self._resolver.resolve_source_graph(graph, inputs=_candidate_inputs(candidate))
        observed = repository_plan_admission_evidence(repository, plan)
        if observed.source_authority != source_authority:
            raise ReviewedRepositorySourceStale("Repository source changed since exploration")
        lock = build_execution_lock(plan, external_artifacts=_source_artifacts(source_authority))
        return CatalogPreview(
            repository=repository,
            source_authority=source_authority,
            candidate=candidate,
            yaml=render_catalog(candidate),
            plan_id=plan.plan_id,
            plan_sha256=source.sha256,
            execution_digest=lock.digest,
        )

    def run(
        self,
        repository_id: str,
        *,
        source_authority: RepositorySourceAuthority,
        candidate: CatalogCandidate,
        expected_plan_id: str,
        expected_plan_sha256: str,
        expected_execution_digest: str,
        fleet_run_id: str,
    ) -> FleetRunState:
        self._plans.require_plan_ingress_preparation(fleet_run_id)
        self._repository(repository_id, source_authority.subject_instance_id)
        source = self._templates.source(_CATALOG_PLAN)
        if source.sha256 != expected_plan_sha256:
            raise FleetPlanInvalid("Catalog Plan source changed after preview; review it again")
        graph = self._resolver.analyze_source(source.path, root_snapshot=source)
        return self._plans.run_reviewed(
            source.path,
            inputs=_candidate_inputs(candidate),
            source=PlanFileSource(sha256=source.sha256),
            expected_plan_id=expected_plan_id,
            expected_execution_digest=expected_execution_digest,
            expected_repository_instance_ids={repository_id: source_authority.subject_instance_id},
            reviewed_repository_source=source_authority,
            external_artifacts=_source_artifacts(source_authority),
            source_graph=graph,
            repository_ids={repository_id},
            fleet_run_id=fleet_run_id,
        )
