from __future__ import annotations

import os
from datetime import timedelta
from threading import Lock

from oh_goon.core.errors import InvalidRequest, OperationNotFound
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.scout.evidence import ScoutEvidenceSnapshot, project_scout_evidence
from oh_goon.scout.models import (
    ScoutDetailSection,
    ScoutFindingCategory,
    ScoutFindingsPage,
    ScoutGoonDetail,
    ScoutGoonLeadersPage,
    ScoutHealth,
    ScoutMetricDictionary,
    ScoutOverview,
    ScoutPlanDetail,
    ScoutRepositoryDetail,
    ScoutRunEvidence,
    ScoutSearchPage,
    ScoutWindow,
)
from oh_goon.scout.policy import (
    SCOUT_DEFAULT_LIMIT,
    SCOUT_DETAIL_ENTITY_LIMIT,
    SCOUT_DETAIL_RETENTION_DAYS,
    SCOUT_MAX_LIMIT,
    SCOUT_MEASUREMENT_SCHEMA_VERSION,
    SCOUT_RUN_RETENTION_DAYS,
    SCOUT_RUN_SPIKE_HISTORY_PERIODS,
)
from oh_goon.scout.projection import (
    project_scout_findings,
    project_scout_goon,
    project_scout_goon_leaders,
    project_scout_overview,
    project_scout_plan,
    project_scout_repository,
    scout_metric_dictionary,
)
from oh_goon.scout.run_reader import ScoutRunReader
from oh_goon.scout.search import ScoutSearchReader
from oh_goon.scout.store import ScoutObservationStore


class ScoutService:
    """Optional read-only analytics downstream of canonical Journal/Game Tape facts."""

    def __init__(
        self,
        journal: Journal,
        *,
        database: LocalDatabase | None = None,
        enabled: bool = True,
    ) -> None:
        self._journal = journal
        self._database = database
        self._enabled = enabled
        self._sync_lock = Lock()
        self._store_instance: ScoutObservationStore | None = None
        self._search_reader_instance: ScoutSearchReader | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def overview(self, *, hours: int = 24, limit: int = SCOUT_DEFAULT_LIMIT) -> ScoutOverview:
        window = _window(hours)
        if not self._enabled:
            return project_scout_overview(
                _empty_snapshot(), window=window, limit=_limit(limit), enabled=False
            )
        return project_scout_overview(self._evidence(window), window=window, limit=_limit(limit))

    def findings(
        self,
        *,
        hours: int = 24,
        category: ScoutFindingCategory | None = None,
        offset: int = 0,
        limit: int = SCOUT_DEFAULT_LIMIT,
    ) -> ScoutFindingsPage:
        if offset < 0:
            raise InvalidRequest("Scout findings offset must be zero or greater")
        window = _window(hours)
        if not self._enabled:
            return project_scout_findings(
                _empty_snapshot(),
                window=window,
                category=category,
                offset=offset,
                limit=_limit(limit),
                enabled=False,
            )
        return project_scout_findings(
            self._evidence(window),
            window=window,
            category=category,
            offset=offset,
            limit=_limit(limit),
        )

    def goon_leaders(
        self, *, hours: int = 24, offset: int = 0, limit: int = SCOUT_DEFAULT_LIMIT
    ) -> ScoutGoonLeadersPage:
        if offset < 0:
            raise InvalidRequest("Scout Goon Leaders offset must be zero or greater")
        window = _window(hours)
        selected_limit = _limit(limit)
        snapshot = self._evidence(window) if self._enabled else _empty_snapshot()
        return project_scout_goon_leaders(
            snapshot, window=window, offset=offset, limit=selected_limit, enabled=self._enabled
        )

    def repository(
        self,
        repository_id: str,
        *,
        hours: int = 24,
        plan_id: str | None = None,
        section: ScoutDetailSection | None = None,
        offset: int = 0,
        limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    ) -> ScoutRepositoryDetail:
        selected_limit = _detail_page(
            section=section,
            offset=offset,
            limit=limit,
            allowed=frozenset({"plans", "goons", "steps"}),
            owner="repository",
        )
        _require_enabled(self._enabled)
        window = _window(hours)
        result = project_scout_repository(
            self._evidence(window),
            window=window,
            repository_id=repository_id,
            plan_id=plan_id,
            page_section=section,
            page_offset=offset,
            page_limit=selected_limit,
        )
        if result is None:
            raise OperationNotFound(
                f"No Scout observations for repository {repository_id!r} and selected Plan in the selected window"
            )
        return result

    def plan(
        self,
        plan_id: str,
        *,
        hours: int = 24,
        section: ScoutDetailSection | None = None,
        offset: int = 0,
        limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    ) -> ScoutPlanDetail:
        selected_limit = _detail_page(
            section=section,
            offset=offset,
            limit=limit,
            allowed=frozenset({"repositories", "goons", "steps"}),
            owner="Plan",
        )
        _require_enabled(self._enabled)
        window = _window(hours)
        result = project_scout_plan(
            self._evidence(window),
            window=window,
            plan_id=plan_id,
            page_section=section,
            page_offset=offset,
            page_limit=selected_limit,
        )
        if result is None:
            raise OperationNotFound(
                f"No Scout observations for Plan {plan_id!r} in the selected window"
            )
        return result

    def goon(
        self,
        goon_name: str,
        *,
        source_sha256: str | None = None,
        hours: int = 24,
        section: ScoutDetailSection | None = None,
        offset: int = 0,
        limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    ) -> ScoutGoonDetail:
        selected_limit = _detail_page(
            section=section,
            offset=offset,
            limit=limit,
            allowed=frozenset({"versions", "repositories", "steps"}),
            owner="Goon",
        )
        _require_enabled(self._enabled)
        window = _window(hours)
        result = project_scout_goon(
            self._evidence(window),
            window=window,
            goon_name=goon_name,
            source_sha256=source_sha256,
            page_section=section,
            page_offset=offset,
            page_limit=selected_limit,
        )
        if result is None:
            raise OperationNotFound(
                f"No Scout observations for Goon {goon_name!r} in the selected window"
            )
        return result

    def search(
        self, *, query: str, hours: int = 24, offset: int = 0, limit: int = SCOUT_DEFAULT_LIMIT
    ) -> ScoutSearchPage:
        if offset < 0:
            raise InvalidRequest("Scout search offset must be zero or greater")
        window = _window(hours)
        normalized = _search_query(query)
        selected_limit = _limit(limit)
        if not self._enabled:
            return ScoutSearchPage(
                enabled=False,
                journal_revision=0,
                window=window,
                query=normalized,
                total=0,
                offset=offset,
                limit=selected_limit,
                items=(),
            )
        store, journal_revision = self._synchronize()
        total, items = self._search_reader().search(
            query=normalized,
            start_at=window.start_at,
            end_at=window.end_at,
            limit=selected_limit,
            offset=offset,
        )
        return ScoutSearchPage(
            enabled=True,
            journal_revision=journal_revision,
            window=window,
            query=normalized,
            total=total,
            offset=offset,
            limit=selected_limit,
            items=items,
        )

    def run(self, run_id: str) -> ScoutRunEvidence:
        _require_enabled(self._enabled)
        self._journal.read_view().required_run_detail(run_id)
        _, revision = self._synchronize()
        return ScoutRunReader(_require_database(self._database)).read(
            run_id, journal_revision=revision
        )

    def health(self) -> ScoutHealth:
        if not self._enabled:
            return _disabled_health()
        store, journal_revision = self._synchronize()
        return _health_from_store(store, journal_revision=journal_revision)

    def metric_dictionary(self) -> ScoutMetricDictionary:
        return scout_metric_dictionary()

    def _evidence(self, window: ScoutWindow) -> ScoutEvidenceSnapshot:
        store, journal_revision = self._synchronize()
        history_start = window.start_at - timedelta(
            hours=window.hours * (SCOUT_RUN_SPIKE_HISTORY_PERIODS + 1)
        )
        retention_start = window.end_at - timedelta(days=SCOUT_RUN_RETENTION_DAYS)
        return store.load_snapshot(
            start_at=max(history_start, retention_start),
            end_at=window.end_at,
            journal_revision=journal_revision,
        )

    def _synchronize(self) -> tuple[ScoutObservationStore, int]:
        with self._sync_lock:
            store = self._store()
            watermark = store.journal_revision()
            journal_revision, changed = self._journal.run_details_changed_after(watermark)
            if journal_revision < watermark:
                store.reset()
                watermark = 0
                journal_revision, changed = self._journal.run_details_changed_after(watermark)
            now = utc_now()
            if journal_revision != watermark:
                journal_revision = store.replace_changed_runs(
                    run_ids=tuple(detail.run.fleet_run_id for detail in changed),
                    snapshot=project_scout_evidence(changed, journal_revision=journal_revision),
                    journal_revision=journal_revision,
                    run_retain_after=now - timedelta(days=SCOUT_RUN_RETENTION_DAYS),
                    step_retain_after=now - timedelta(days=SCOUT_DETAIL_RETENTION_DAYS),
                )
            _maintain_retention(store, now=now)
            return store, journal_revision

    def _search_reader(self) -> ScoutSearchReader:
        if self._search_reader_instance is not None:
            return self._search_reader_instance
        database = _require_database(self._database)
        self._search_reader_instance = ScoutSearchReader(database)
        return self._search_reader_instance

    def _store(self) -> ScoutObservationStore:
        if self._store_instance is not None:
            return self._store_instance
        database = _require_database(self._database)
        self._store_instance = ScoutObservationStore(database)
        return self._store_instance


def _health_from_store(store: ScoutObservationStore, *, journal_revision: int) -> ScoutHealth:
    stats = store.stats()
    return ScoutHealth(
        enabled=True,
        measurement_schema_version=SCOUT_MEASUREMENT_SCHEMA_VERSION,
        journal_revision=journal_revision,
        processed_journal_revision=stats.journal_revision,
        lag_revisions=max(0, journal_revision - stats.journal_revision),
        retained_run_observations=stats.run_observations,
        retained_step_observations=stats.step_observations,
        snapshot_cache_entries=stats.snapshot_cache_entries,
        snapshot_cache_hits=stats.snapshot_cache_hits,
        snapshot_cache_misses=stats.snapshot_cache_misses,
        last_snapshot_load_ms=stats.last_snapshot_load_ms,
        analytical_storage_bytes=stats.analytical_storage_bytes,
        detail_retention_days=SCOUT_DETAIL_RETENTION_DAYS,
        run_retention_days=SCOUT_RUN_RETENTION_DAYS,
    )


def _maintain_retention(store: ScoutObservationStore, *, now) -> None:
    store.prune_if_due(
        now=now,
        run_retain_after=now - timedelta(days=SCOUT_RUN_RETENTION_DAYS),
        step_retain_after=now - timedelta(days=SCOUT_DETAIL_RETENTION_DAYS),
    )


def _require_database(database: LocalDatabase | None) -> LocalDatabase:
    if database is None:
        raise InvalidRequest("Scout analytical store database is not configured")
    return database


def scout_enabled_from_environment() -> bool:
    value = os.getenv("OH_GOON_SCOUT_ENABLED", "1").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    # Invalid optional observability configuration must not make execution unavailable.
    return False


def _disabled_health() -> ScoutHealth:
    return ScoutHealth(
        enabled=False,
        measurement_schema_version=SCOUT_MEASUREMENT_SCHEMA_VERSION,
        journal_revision=0,
        processed_journal_revision=0,
        lag_revisions=0,
        retained_run_observations=0,
        retained_step_observations=0,
        snapshot_cache_entries=0,
        snapshot_cache_hits=0,
        snapshot_cache_misses=0,
        last_snapshot_load_ms=None,
        analytical_storage_bytes=0,
        detail_retention_days=SCOUT_DETAIL_RETENTION_DAYS,
        run_retention_days=SCOUT_RUN_RETENTION_DAYS,
    )


def _empty_snapshot() -> ScoutEvidenceSnapshot:
    return ScoutEvidenceSnapshot(journal_revision=0, runs=(), steps=())


def _window(hours: int) -> ScoutWindow:
    if hours < 1 or hours > 24 * 90:
        raise InvalidRequest("Scout hours must be between 1 and 2160")
    end = utc_now().replace(second=0, microsecond=0)
    return ScoutWindow(start_at=end - timedelta(hours=hours), end_at=end, hours=hours)


def _limit(limit: int) -> int:
    if limit < 1 or limit > SCOUT_MAX_LIMIT:
        raise InvalidRequest(f"Scout limit must be between 1 and {SCOUT_MAX_LIMIT}")
    return limit


def _detail_page(
    *,
    section: ScoutDetailSection | None,
    offset: int,
    limit: int,
    allowed: frozenset[str],
    owner: str,
) -> int:
    if offset < 0:
        raise InvalidRequest(f"Scout {owner} detail offset must be zero or greater")
    if section is None and offset != 0:
        raise InvalidRequest(f"Scout {owner} detail offset requires an explicit section")
    if section is not None and section not in allowed:
        expected = ", ".join(sorted(allowed))
        raise InvalidRequest(
            f"Scout {owner} detail section {section!r} is not valid; expected one of: {expected}"
        )
    return _limit(limit)


def _search_query(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise InvalidRequest("Scout search query must contain between 1 and 120 characters")
    return normalized


def _require_enabled(enabled: bool) -> None:
    if not enabled:
        raise InvalidRequest("Scout is disabled for this Oh Goon deployment")
