from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from oh_goon.core.time import utc_now
from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.scout_http_load_probe import (
    ROOT,
    SCOUT_HTTP_ACCESS_TOKEN,
    _get_json,
    _journal_revision,
    _port,
    _start_server,
    _stop_server,
    _wait_json,
)
from oh_goon.repository_tooling.scout_load_probe import build_scout_load_snapshot
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.scout.evidence import ScoutEvidenceSnapshot
from oh_goon.scout.store import ScoutObservationStore


@dataclass(frozen=True, slots=True)
class ScoutRetentionCheckpointResult:
    canonical_revision_before: int
    canonical_revision_after: int
    retained_runs: int
    retained_steps: int
    lag_revisions: int
    wal_bytes_after_maintenance: int
    blocked_outbound_attempts: int


def _renamed(snapshot: ScoutEvidenceSnapshot, prefix: str) -> ScoutEvidenceSnapshot:
    run = snapshot.runs[0]
    step = snapshot.steps[0]
    run_id = f"{prefix}-run"
    repository_id = f"{prefix}-repo"
    repository_name = f"{prefix}-repository"
    return ScoutEvidenceSnapshot(
        journal_revision=snapshot.journal_revision,
        runs=(
            replace(
                run,
                run_id=run_id,
                repository_id=repository_id,
                repository_name=repository_name,
            ),
        ),
        steps=(
            replace(
                step,
                run_id=run_id,
                repository_id=repository_id,
                repository_name=repository_name,
            ),
        ),
    )


def _mixed_age_snapshot(*, revision: int) -> ScoutEvidenceSnapshot:
    now = utc_now().replace(second=0, microsecond=0)
    pieces = (
        _renamed(
            build_scout_load_snapshot(
                repositories=1,
                steps_per_repository=1,
                now=now - timedelta(minutes=5),
                journal_revision=revision,
            ),
            "fresh",
        ),
        _renamed(
            build_scout_load_snapshot(
                repositories=1,
                steps_per_repository=1,
                now=now - timedelta(days=120),
                journal_revision=revision,
            ),
            "medium",
        ),
        _renamed(
            build_scout_load_snapshot(
                repositories=1,
                steps_per_repository=1,
                now=now - timedelta(days=400),
                journal_revision=revision,
            ),
            "old",
        ),
    )
    return ScoutEvidenceSnapshot(
        journal_revision=revision,
        runs=tuple(run for piece in pieces for run in piece.runs),
        steps=tuple(step for piece in pieces for step in piece.steps),
    )


def run_retention_checkpoint_certification(*, root: Path = ROOT) -> ScoutRetentionCheckpointResult:
    with TemporaryDirectory(prefix="oh-goon-scout-retention-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        prepare_acceptance_repository(repo)
        port = _port()
        base = f"http://127.0.0.1:{port}"
        server = _start_server(
            root=root,
            state=state,
            league=league,
            port=port,
            loopback_guard_log=guard_log,
        )
        try:
            _wait_json(f"{base}/api/health", process=server)
            run_acceptance_goon(
                base_url=base,
                server_repository_path=str(repo.resolve()),
                access_token=SCOUT_HTTP_ACCESS_TOKEN,
            )
        finally:
            _stop_server(server)
        database = LocalDatabase(state)
        revision = _journal_revision(database)
        snapshot = _mixed_age_snapshot(revision=revision)
        now = utc_now()
        store = ScoutObservationStore(database)
        store.reset()
        store.replace_changed_runs(
            run_ids=tuple(run.run_id for run in snapshot.runs),
            snapshot=snapshot,
            journal_revision=revision,
            run_retain_after=now - timedelta(days=1_000),
            step_retain_after=now - timedelta(days=1_000),
        )
        port = _port()
        base = f"http://127.0.0.1:{port}"
        server = _start_server(
            root=root,
            state=state,
            league=league,
            port=port,
            loopback_guard_log=guard_log,
        )
        try:
            _wait_json(f"{base}/api/health", process=server)
            health = _get_json(f"{base}/api/scout/health")
            revision_after = _journal_revision(LocalDatabase(state))
        finally:
            _stop_server(server)
        attempts = blocked_network_attempts(guard_log)
        if attempts:
            raise RuntimeError(
                f"Scout retention certification attempted outbound network: {attempts[:5]!r}"
            )
        wal = Path(f"{scout_analytical_database_path(state)}-wal")
        wal_bytes = wal.stat().st_size if wal.is_file() else 0
    if revision_after != revision:
        raise RuntimeError("Scout retention maintenance changed canonical Journal revision")
    if (
        health.get("retained_run_observations") != 2
        or health.get("retained_step_observations") != 1
    ):
        raise RuntimeError(f"Scout retention counts are wrong after real maintenance: {health!r}")
    if health.get("lag_revisions") != 0:
        raise RuntimeError("Scout retention maintenance introduced Journal lag")
    if wal_bytes != 0:
        raise RuntimeError(f"Scout retention maintenance did not truncate WAL: {wal_bytes} bytes")
    return ScoutRetentionCheckpointResult(
        canonical_revision_before=revision,
        canonical_revision_after=revision_after,
        retained_runs=int(health["retained_run_observations"]),
        retained_steps=int(health["retained_step_observations"]),
        lag_revisions=int(health["lag_revisions"]),
        wal_bytes_after_maintenance=wal_bytes,
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(json.dumps(asdict(run_retention_checkpoint_certification()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
