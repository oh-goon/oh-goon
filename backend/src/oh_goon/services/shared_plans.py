from __future__ import annotations

from pathlib import Path
from typing import Protocol

from oh_goon.core.errors import (
    FleetDomainError,
    FleetPlanInvalid,
    FleetPlanSourceNotFound,
    FleetPlanSourceStale,
)
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.core.models.plan_template import PlanTemplateCatalog
from oh_goon.core.models.run import (
    FleetRunHistoryPage,
    FleetRunState,
)
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.shared_plan import (
    BlockedSharedPlanRepositoryPreview,
    ReadySharedPlanRepositoryPreview,
    SharedPlanCatalog,
    SharedPlanDocument,
    SharedPlanExplicitInput,
    SharedPlanLastRun,
    SharedPlanLockPreview,
    SharedPlanPreview,
    SharedPlanRepositoryPreview,
    SharedPlanRunHistory,
    SharedPlanSummary,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.runtime.shared_plans import SharedPlanFileStore, normalize_shared_plan_key
from oh_goon.services.execution_lock_persistence import render_execution_lock
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.league import LeagueService
from oh_goon.services.shared_plan_preview_diagnostics import (
    blocked_preview_diagnostic,
    preview_readiness,
)


class SharedPlanRunHistoryReader(Protocol):
    def history(self, *, limit: int, before_revision: int | None = None) -> FleetRunHistoryPage: ...


class SharedPlanService:
    """Own oh-goon-local plan.yml files and apply one exact Plan to explicit repositories."""

    def __init__(
        self,
        store: SharedPlanFileStore,
        league: LeagueService,
        plans: FleetPlanService,
        runs: SharedPlanRunHistoryReader,
        templates: PlanTemplateStore,
    ) -> None:
        self._store = store
        self._league = league
        self._plans = plans
        self._runs = runs
        self._templates = templates

    @property
    def root(self) -> Path:
        return self._store.root

    def catalog(self) -> SharedPlanCatalog:
        keys = self._store.list_keys()
        latest = self._latest_runs_by_key(set(keys))
        return SharedPlanCatalog(
            plans=tuple(
                self._summary(self._store.read(key), last_run=latest.get(key)) for key in keys
            )
        )

    def read(
        self,
        plan_key: str,
        *,
        include_inputs: bool = False,
        expected_revision: str | None = None,
    ) -> SharedPlanDocument:
        return self._document(
            self._store.read(plan_key, expected_revision=expected_revision),
            include_inputs=include_inputs,
        )

    def templates(self) -> PlanTemplateCatalog:
        return self._templates.catalog()

    def create_from_template(
        self,
        template_key: str,
        expected_template_sha256: str,
        *,
        plan_key: str | None = None,
    ) -> SharedPlanDocument:
        """Materialize the exact reviewed shipped Plan template into Shared Plan storage.

        The template bytes are read once and fenced by their SHA-256 before any write.
        Template selection is idempotent when the saved Plan still matches those bytes.
        A diverged saved Plan is never overwritten implicitly.
        """
        template = self._templates.read(template_key)
        if template.sha256 != expected_template_sha256:
            raise FleetPlanSourceStale(
                f"Plan template {template.template_key} changed; expected SHA-256 "
                f"{expected_template_sha256}, current SHA-256 {template.sha256}"
            )
        destination_key = normalize_shared_plan_key(
            plan_key or template.template_key.replace("/", "-")
        )
        try:
            current = self._store.read(destination_key)
        except FleetPlanSourceNotFound:
            return self.save(destination_key, template.content)
        if current.content == template.content:
            return self._document(current)
        raise FleetPlanSourceStale(
            f"Shared Plan {destination_key} differs from shipped template {template.template_key}; "
            "rename or explicitly edit the saved Plan before using that template"
        )

    def history(
        self,
        plan_key: str,
        *,
        limit: int = 20,
        before_revision: int | None = None,
    ) -> SharedPlanRunHistory:
        if limit < 1 or limit > 100:
            raise FleetPlanInvalid("Shared Plan history limit must be between 1 and 100")
        if before_revision is not None and before_revision < 1:
            raise FleetPlanInvalid("Shared Plan history cursor must be at least 1")
        document = self.read(plan_key)
        matches: list[tuple[int, SharedPlanLastRun]] = []
        scan_before_revision = before_revision
        required = limit + 1
        while len(matches) < required:
            page = self._runs.history(limit=100, before_revision=scan_before_revision)
            for item in page.items:
                target = item.run.target
                if not isinstance(target, FleetPlanTarget) or not isinstance(
                    target.source, SharedPlanSource
                ):
                    continue
                if target.source.plan_key != document.plan_key:
                    continue
                matches.append((item.admitted_revision, self._run_fact(item.run, target.source)))
                if len(matches) == required:
                    break
            if len(matches) == required or page.next_before_revision is None:
                break
            scan_before_revision = page.next_before_revision

        visible = matches[:limit]
        return SharedPlanRunHistory(
            plan_key=document.plan_key,
            current_sha256=document.sha256,
            runs=tuple(run for _, run in visible),
            next_before_revision=(visible[-1][0] if len(matches) > limit and visible else None),
        )

    def save(
        self,
        plan_key: str,
        content: str,
        *,
        expected_revision: str | None = None,
    ) -> SharedPlanDocument:
        # Validate once before persistence so malformed YAML never becomes selected state.
        # The committed bytes are already the exact content we just validated; do not
        # reload/reparse them after the atomic store write.
        compiled = parse_plan_template(
            content, source=f"shared:{plan_key}/plan.yml", expected_name=plan_key
        )
        if any(getattr(step, "source_root", None) is not None for step in compiled.steps):
            raise FleetPlanInvalid(
                "Local Goon imports require a file-backed Plan and its catalog; "
                "Shared Plan storage saves only plan.yml"
            )
        return self._document(
            self._store.write(plan_key, content, expected_revision=expected_revision),
            validated=True,
        )

    def lock_preview(
        self,
        plan_key: str,
        *,
        inputs: dict[str, str] | None = None,
        expected_revision: str | None = None,
    ) -> SharedPlanLockPreview:
        document = self._store.read(plan_key, expected_revision=expected_revision)
        plan, source_sha256 = self._plans.resolve_with_source(
            self._store.path(document.plan_key),
            inputs=inputs,
            root_expected_name=document.plan_key,
        )
        self._require_source_revision(document, source_sha256)
        lock = build_execution_lock(plan)
        return SharedPlanLockPreview(
            plan_key=document.plan_key,
            plan_id=plan.plan_id,
            sha256=document.sha256,
            execution_digest=lock.digest,
            content=render_execution_lock(lock),
        )

    def inspect_inputs(
        self,
        plan_key: str,
        *,
        inputs: dict[str, str] | None = None,
        expected_revision: str,
    ) -> tuple[str, str, str]:
        """Resolve one pinned source cut; never create a run, lock or admission receipt."""
        document = self._store.read(plan_key, expected_revision=expected_revision)
        plan, source_sha256 = self._plans.resolve_with_source(
            self._store.path(document.plan_key),
            inputs=inputs,
            root_expected_name=document.plan_key,
        )
        self._require_source_revision(document, source_sha256)
        return document.plan_key, document.sha256, plan.plan_id

    def preview(
        self,
        plan_key: str,
        *,
        repository_ids: set[str],
        inputs: dict[str, str] | None = None,
        expected_revision: str | None = None,
    ) -> SharedPlanPreview:
        if not repository_ids:
            raise FleetPlanInvalid(
                "Shared Plan preview requires at least one explicitly selected Team"
            )
        document = self._store.read(plan_key, expected_revision=expected_revision)
        plan, source_sha256 = self._plans.resolve_with_source(
            self._store.path(document.plan_key),
            inputs=inputs,
            root_expected_name=document.plan_key,
        )
        self._require_source_revision(document, source_sha256)
        teams = self._preview_teams(plan, repository_ids)
        ready = sum(item.state == "READY" for item in teams)
        return SharedPlanPreview(
            plan_key=document.plan_key,
            plan_id=plan.plan_id,
            sha256=document.sha256,
            execution_digest=build_execution_lock(plan).digest,
            explicit_inputs=tuple(
                SharedPlanExplicitInput(name=name, value=value)
                for name, value in sorted((inputs or {}).items())
            ),
            readiness=preview_readiness(
                item for item in teams if isinstance(item, ReadySharedPlanRepositoryPreview)
            ),
            teams=tuple(teams),
            ready_count=ready,
            blocked_count=len(teams) - ready,
        )

    def _preview_teams(
        self,
        plan: ResolvedFleetPlan,
        repository_ids: set[str],
    ) -> tuple[SharedPlanRepositoryPreview, ...]:
        current = {item.repository_id: item for item in self._league.repositories()}
        unknown = sorted(repository_ids - set(current))
        if unknown:
            raise FleetPlanInvalid(
                "Shared Plan preview contains Team IDs outside the current League: "
                + ", ".join(unknown)
            )
        teams: list[SharedPlanRepositoryPreview] = []
        for repository_id in sorted(repository_ids):
            repository = current[repository_id]
            before_instance = repository_subject_instance_id(repository.path)
            try:
                preview = self._plans.preview_resolved(plan, repository.path)
            except (FleetDomainError, ValueError) as exc:
                team = BlockedSharedPlanRepositoryPreview(
                    repository=repository,
                    repository_instance_id=before_instance,
                    reason=str(exc),
                    diagnostic=blocked_preview_diagnostic(exc),
                )
            else:
                team = ReadySharedPlanRepositoryPreview(
                    repository=repository,
                    repository_instance_id=before_instance,
                    preview=preview,
                )
            if repository_subject_instance_id(repository.path) != before_instance:
                raise FleetPlanSourceStale(
                    "Shared Plan Team repository instance changed while it was being previewed: "
                    + repository_id
                )
            teams.append(team)
        return tuple(teams)

    def run(
        self,
        plan_key: str,
        *,
        repository_ids: set[str],
        expected_plan_id: str,
        expected_execution_digest: str,
        expected_plan_revision: str,
        repository_instance_ids: dict[str, str],
        inputs: dict[str, str] | None = None,
        fleet_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
    ) -> FleetRunState:
        # This is the HTTP Shared Plan execution owner. Check explicit Run
        # enrollment before reading saved Plan bytes, inspecting selected Teams,
        # or entering the canonical resolver. The original final Fleet admission
        # still validates Plan/source/instance/signers and owns the lease.
        self._plans.require_plan_ingress_preparation(fleet_run_id)
        if not repository_ids:
            raise FleetPlanInvalid("Shared Plan runs require at least one explicitly selected Team")
        document = self._store.read(plan_key, expected_revision=expected_plan_revision)
        if set(repository_instance_ids) != repository_ids:
            raise FleetPlanInvalid(
                "Shared Plan run repository instance authority must exactly match selected Teams"
            )
        # Deliberately do not inspect repository-owned goon.yml files here. Target selection
        # validates only that requested IDs are current managed Git Teams in the League.
        return self._plans.run_reviewed(
            self._store.path(document.plan_key),
            expected_execution_digest=expected_execution_digest,
            repository_ids=repository_ids,
            fleet_run_id=fleet_run_id,
            requested_priority=requested_priority,
            inputs=inputs,
            source=SharedPlanSource(plan_key=document.plan_key, sha256=document.sha256),
            expected_plan_id=expected_plan_id,
            expected_repository_instance_ids=repository_instance_ids,
            root_expected_name=document.plan_key,
        )

    def repository_ids(self, repositories: tuple[Path, ...]) -> set[str]:
        """Resolve CLI Team paths without requiring or reading their repository-owned goon.yml."""
        return {
            repository.repository_id
            for repository in self._league.resolve_repositories(repositories)
        }

    def all_repository_ids(self) -> set[str]:
        """Snapshot every current managed Git Team without inspecting repository goon.yml files."""
        repository_ids = {repository.repository_id for repository in self._league.repositories()}
        if not repository_ids:
            raise FleetPlanInvalid("Entire League contains no managed Git Teams")
        return repository_ids

    def _document(
        self,
        document: SharedPlanDocument,
        *,
        include_inputs: bool = False,
        validated: bool = False,
    ) -> SharedPlanDocument:
        if include_inputs:
            # Input projection is already a canonical parser/resolver operation.
            # Calling parse_plan_template here as well would interpret the same bytes twice.
            inputs, source_sha256 = self._plans.input_definitions_with_source(
                self._store.path(document.plan_key),
                root_expected_name=document.plan_key,
            )
            self._require_source_revision(document, source_sha256)
            return document.model_copy(update={"inputs": inputs})
        if not validated:
            parse_plan_template(
                document.content,
                source=f"shared:{document.plan_key}/plan.yml",
                expected_name=document.plan_key,
            )
        return document

    @staticmethod
    def _require_source_revision(document: SharedPlanDocument, source_sha256: str) -> None:
        if source_sha256 != document.sha256:
            raise FleetPlanSourceStale(
                "Shared Plan changed while it was being resolved; reload the current SHA-256 revision"
            )

    def _summary(
        self, document: SharedPlanDocument, *, last_run: SharedPlanLastRun | None = None
    ) -> SharedPlanSummary:
        resolved = self._document(document)
        return SharedPlanSummary(
            plan_key=resolved.plan_key,
            sha256=resolved.sha256,
            byte_count=resolved.byte_count,
            last_run=last_run,
        )

    @staticmethod
    def _run_fact(run: FleetRunState, source: SharedPlanSource) -> SharedPlanLastRun:
        return SharedPlanLastRun(
            fleet_run_id=run.fleet_run_id,
            accepted_at=run.accepted_at,
            status=run.status,
            source_sha256=source.sha256,
            team_count=len(run.targets),
        )

    def _latest_runs_by_key(self, pending: set[str]) -> dict[str, SharedPlanLastRun]:
        latest: dict[str, SharedPlanLastRun] = {}
        before_revision: int | None = None
        while pending:
            page = self._runs.history(limit=100, before_revision=before_revision)
            for item in page.items:
                target = item.run.target
                if not isinstance(target, FleetPlanTarget) or not isinstance(
                    target.source, SharedPlanSource
                ):
                    continue
                key = target.source.plan_key
                if key not in pending:
                    continue
                latest[key] = self._run_fact(item.run, target.source)
                pending.remove(key)
            if page.next_before_revision is None:
                break
            before_revision = page.next_before_revision
        return latest
