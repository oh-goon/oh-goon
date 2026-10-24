from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
)
from oh_goon.scout.models import ScoutWindow
from oh_goon.scout.projection import project_scout_findings, project_scout_overview
from oh_goon.scout.store import ScoutObservationStore


@dataclass(frozen=True, slots=True)
class ScoutLoadProbeResult:
    repositories: int
    steps_per_repository: int
    concurrent_users: int
    observations: int
    overview_projection_ms: float
    findings_projection_ms: float
    store_materialization_ms: float
    concurrent_cached_reads_ms: float
    cached_read_p50_ms: float
    cached_read_p95_ms: float
    revision_cycles: int
    changed_repositories_per_cycle: int
    revision_refresh_total_ms: float
    revision_refresh_p50_ms: float
    revision_refresh_p95_ms: float
    cache_hits: int
    cache_misses: int


@dataclass(frozen=True, slots=True)
class _StoreProbeMetrics:
    materialization_ms: float
    concurrent_ms: float
    cached_latencies: tuple[float, ...]
    churn_latencies: tuple[float, ...]
    changed_count: int
    cache_hits: int
    cache_misses: int


def run_probe(
    *,
    repositories: int,
    steps_per_repository: int,
    concurrent_users: int,
    revision_cycles: int = 10,
    changed_repositories_per_cycle: int = 50,
) -> ScoutLoadProbeResult:
    now = utc_now().replace(second=0, microsecond=0)
    window = ScoutWindow(
        start_at=now - timedelta(hours=24), end_at=now + timedelta(minutes=1), hours=24
    )
    snapshot = build_scout_load_snapshot(
        repositories=repositories, steps_per_repository=steps_per_repository, now=now
    )
    overview_ms = _projection_ms(lambda: project_scout_overview(snapshot, window=window, limit=20))
    findings_ms = _projection_ms(
        lambda: project_scout_findings(
            snapshot, window=window, category="CPU_OUTLIER", offset=0, limit=20
        )
    )
    store_metrics = _probe_store(
        snapshot=snapshot,
        window=window,
        now=now,
        concurrent_users=concurrent_users,
        revision_cycles=revision_cycles,
        changed_repositories_per_cycle=changed_repositories_per_cycle,
    )
    ordered = sorted(store_metrics.cached_latencies)
    churn = sorted(store_metrics.churn_latencies)
    return ScoutLoadProbeResult(
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        concurrent_users=concurrent_users,
        observations=len(snapshot.steps),
        overview_projection_ms=round(overview_ms, 3),
        findings_projection_ms=round(findings_ms, 3),
        store_materialization_ms=round(store_metrics.materialization_ms, 3),
        concurrent_cached_reads_ms=round(store_metrics.concurrent_ms, 3),
        cached_read_p50_ms=round(statistics.median(ordered), 3),
        cached_read_p95_ms=round(_percentile(ordered, 0.95), 3),
        revision_cycles=revision_cycles,
        changed_repositories_per_cycle=store_metrics.changed_count,
        revision_refresh_total_ms=round(sum(churn), 3),
        revision_refresh_p50_ms=round(statistics.median(churn), 3) if churn else 0.0,
        revision_refresh_p95_ms=round(_percentile(churn, 0.95), 3),
        cache_hits=store_metrics.cache_hits,
        cache_misses=store_metrics.cache_misses,
    )


def _projection_ms(project: Callable[[], object]) -> float:
    started = perf_counter()
    project()
    return _milliseconds_since(started)


def _probe_store(
    *,
    snapshot: ScoutEvidenceSnapshot,
    window: ScoutWindow,
    now: datetime,
    concurrent_users: int,
    revision_cycles: int,
    changed_repositories_per_cycle: int,
) -> _StoreProbeMetrics:
    with TemporaryDirectory(prefix="oh-goon-scout-load-") as directory:
        store = ScoutObservationStore(LocalDatabase(Path(directory) / "state.sqlite3"))
        started = perf_counter()
        store.replace_changed_runs(
            run_ids=tuple(run.run_id for run in snapshot.runs),
            snapshot=snapshot,
            journal_revision=snapshot.journal_revision,
            run_retain_after=now - timedelta(days=365),
            step_retain_after=now - timedelta(days=90),
        )
        materialization_ms = _milliseconds_since(started)
        store.load_snapshot(
            start_at=window.start_at,
            end_at=window.end_at,
            journal_revision=snapshot.journal_revision,
        )
        changed_count = min(len(snapshot.runs), changed_repositories_per_cycle)
        current_revision, churn = _apply_revision_churn(
            store=store,
            snapshot=snapshot,
            window=window,
            now=now,
            revision_cycles=revision_cycles,
            changed_count=changed_count,
        )
        concurrent_ms, latencies = _concurrent_cached_reads(
            store=store,
            window=window,
            journal_revision=current_revision,
            concurrent_users=concurrent_users,
        )
        stats = store.stats()
    return _StoreProbeMetrics(
        materialization_ms=materialization_ms,
        concurrent_ms=concurrent_ms,
        cached_latencies=latencies,
        churn_latencies=churn,
        changed_count=changed_count,
        cache_hits=stats.snapshot_cache_hits,
        cache_misses=stats.snapshot_cache_misses,
    )


def _apply_revision_churn(
    *,
    store: ScoutObservationStore,
    snapshot: ScoutEvidenceSnapshot,
    window: ScoutWindow,
    now: datetime,
    revision_cycles: int,
    changed_count: int,
) -> tuple[int, tuple[float, ...]]:
    current_revision = snapshot.journal_revision
    latencies: list[float] = []
    for cycle in range(revision_cycles):
        current_revision += 1
        changed = _changed_snapshot(
            snapshot,
            cycle=cycle,
            changed_repositories=changed_count,
            journal_revision=current_revision,
        )
        started = perf_counter()
        store.replace_changed_runs(
            run_ids=tuple(run.run_id for run in changed.runs),
            snapshot=changed,
            journal_revision=current_revision,
            run_retain_after=now - timedelta(days=365),
            step_retain_after=now - timedelta(days=90),
        )
        loaded = store.load_snapshot(
            start_at=window.start_at,
            end_at=window.end_at,
            journal_revision=current_revision,
        )
        assert loaded.journal_revision == current_revision
        latencies.append(_milliseconds_since(started))
    return current_revision, tuple(latencies)


def _concurrent_cached_reads(
    *,
    store: ScoutObservationStore,
    window: ScoutWindow,
    journal_revision: int,
    concurrent_users: int,
) -> tuple[float, tuple[float, ...]]:
    def cached_read(_index: int) -> float:
        started = perf_counter()
        loaded = store.load_snapshot(
            start_at=window.start_at,
            end_at=window.end_at,
            journal_revision=journal_revision,
        )
        assert loaded.journal_revision == journal_revision
        return _milliseconds_since(started)

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=min(64, concurrent_users)) as executor:
        latencies = tuple(executor.map(cached_read, range(concurrent_users)))
    return _milliseconds_since(started), latencies


def build_scout_load_snapshot(
    *, repositories: int, steps_per_repository: int, now: datetime, journal_revision: int = 1
) -> ScoutEvidenceSnapshot:
    runs = tuple(
        RepositoryRunObservation(
            run_id=f"run-{repository}",
            started_at=now,
            run_status=GameTapeStatus.PASSED,
            repository_id=f"repo-{repository}",
            repository_name=f"repository-{repository}",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan-standard",
            plan_name="standard",
            duration_seconds=30.0,
        )
        for repository in range(repositories)
    )
    steps = tuple(
        ScoutStepObservation(
            run_id=f"run-{repository}",
            started_at=now,
            repository_id=f"repo-{repository}",
            repository_name=f"repository-{repository}",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan-standard",
            plan_name="standard",
            goon_name="python/python-tests",
            goon_source_sha256="a" * 64,
            step_id=f"step-{step}",
            step_status=GameTapeStatus.PASSED,
            attempt_count=1,
            eligible_attempts=1,
            cpu_observed_attempts=1,
            memory_observed_attempts=1,
            observed_cpu_seconds=20.0 if repository == 0 else float((repository % 7) + 1),
            complete_cpu_seconds=20.0 if repository == 0 else float((repository % 7) + 1),
            observed_peak_memory_bytes=256 * 1024 * 1024,
            complete_peak_memory_bytes=256 * 1024 * 1024,
            environment_key=(
                "local",
                "isolation",
                "b" * 64,
                "toolchain",
                ((2000, 536870912),),
            ),
            duration_seconds=5.0,
        )
        for repository in range(repositories)
        for step in range(steps_per_repository)
    )
    return ScoutEvidenceSnapshot(journal_revision=journal_revision, runs=runs, steps=steps)


def _changed_snapshot(
    snapshot: ScoutEvidenceSnapshot,
    *,
    cycle: int,
    changed_repositories: int,
    journal_revision: int,
) -> ScoutEvidenceSnapshot:
    repository_count = len(snapshot.runs)
    selected = {
        f"run-{(cycle * changed_repositories + offset) % repository_count}"
        for offset in range(changed_repositories)
    }
    runs = tuple(run for run in snapshot.runs if run.run_id in selected)
    steps = tuple(
        replace(
            step,
            observed_cpu_seconds=step.observed_cpu_seconds + cycle + 1,
            complete_cpu_seconds=(
                step.complete_cpu_seconds + cycle + 1
                if step.complete_cpu_seconds is not None
                else None
            ),
        )
        for step in snapshot.steps
        if step.run_id in selected
    )
    return ScoutEvidenceSnapshot(journal_revision=journal_revision, runs=runs, steps=steps)


def _milliseconds_since(started: float) -> float:
    return (perf_counter() - started) * 1000


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return values[index]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local deterministic Scout scale/concurrency probe."
    )
    parser.add_argument("--repositories", type=int, default=5000)
    parser.add_argument("--steps-per-repository", type=int, default=4)
    parser.add_argument("--concurrent-users", type=int, default=500)
    parser.add_argument("--revision-cycles", type=int, default=10)
    parser.add_argument("--changed-repositories-per-cycle", type=int, default=50)
    args = parser.parse_args()
    if any(
        value < 1
        for value in (
            args.repositories,
            args.steps_per_repository,
            args.concurrent_users,
            args.revision_cycles,
            args.changed_repositories_per_cycle,
        )
    ):
        parser.error("all probe dimensions must be positive integers")
    print(
        json.dumps(
            asdict(
                run_probe(
                    repositories=args.repositories,
                    steps_per_repository=args.steps_per_repository,
                    concurrent_users=args.concurrent_users,
                    revision_cycles=args.revision_cycles,
                    changed_repositories_per_cycle=args.changed_repositories_per_cycle,
                )
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
