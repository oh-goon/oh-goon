from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.process_metrics import read_linux_process_sample
from oh_goon.repository_tooling.scout_http_load_probe import (
    ROOT,
    _concurrent_routes,
    _get_json,
    _measure_routes,
    _percentile,
    _port,
    _prepare_certification_state,
    _representative_routes,
    _start_server,
    _stop_server,
    _wait_json,
)


@dataclass(frozen=True, slots=True)
class ScoutRuntimeOverheadResult:
    repositories: int
    observations: int
    concurrent_users: int
    successful_requests: int
    wave_total_ms: float
    wave_p50_ms: float
    wave_p95_ms: float
    process_cpu_seconds: float
    rss_before_mib: float
    rss_after_mib: float
    peak_rss_mib: float
    analytical_storage_mib: float
    journal_lag_revisions: int
    snapshot_cache_hits: int
    snapshot_cache_misses: int
    last_snapshot_load_ms: float | None
    blocked_outbound_attempts: int


def _mib(value: int) -> float:
    return round(value / (1024 * 1024), 3)


def run_runtime_overhead_certification(
    *,
    root: Path = ROOT,
    repositories: int = 5_000,
    steps_per_repository: int = 4,
    concurrent_users: int = 500,
) -> ScoutRuntimeOverheadResult:
    if not Path("/proc/self/stat").is_file():
        raise RuntimeError("Scout runtime overhead certification requires Linux /proc")
    with TemporaryDirectory(prefix="oh-goon-scout-overhead-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        port = _port()
        base = f"http://127.0.0.1:{port}"
        _revision, observations = _prepare_certification_state(
            root=root,
            state=state,
            league=league,
            repo=repo,
            port=port,
            base=base,
            guard_log=guard_log,
            repositories=repositories,
            steps_per_repository=steps_per_repository,
        )
        server = _start_server(
            root=root,
            state=state,
            league=league,
            port=port,
            loopback_guard_log=guard_log,
        )
        try:
            _wait_json(f"{base}/api/health", process=server)
            routes = _representative_routes(base)
            _measure_routes(routes)  # Warm each representative analytical path before sampling.
            before = read_linux_process_sample(server.pid)
            wave_total, latencies = _concurrent_routes(routes, concurrent_users)
            after = read_linux_process_sample(server.pid)
            health = _get_json(routes["health"])
        finally:
            _stop_server(server)
        stderr = server.stderr.read().decode("utf-8", errors="replace") if server.stderr else ""
        attempts = blocked_network_attempts(guard_log)
        if attempts:
            raise RuntimeError(f"Scout overhead probe attempted outbound network: {attempts[:5]!r}")
        if server.returncode not in (0, -15) and stderr.strip():
            raise RuntimeError(f"guarded Uvicorn failed during overhead probe:\n{stderr[-8000:]}")
    return ScoutRuntimeOverheadResult(
        repositories=repositories,
        observations=observations,
        concurrent_users=concurrent_users,
        successful_requests=len(latencies),
        wave_total_ms=round(wave_total, 3),
        wave_p50_ms=round(statistics.median(latencies), 3),
        wave_p95_ms=round(_percentile(latencies, 0.95), 3),
        process_cpu_seconds=round(max(0.0, after.cpu_seconds - before.cpu_seconds), 3),
        rss_before_mib=_mib(before.rss_bytes),
        rss_after_mib=_mib(after.rss_bytes),
        peak_rss_mib=_mib(after.peak_rss_bytes),
        analytical_storage_mib=_mib(int(health["analytical_storage_bytes"])),
        journal_lag_revisions=int(health["lag_revisions"]),
        snapshot_cache_hits=int(health["snapshot_cache_hits"]),
        snapshot_cache_misses=int(health["snapshot_cache_misses"]),
        last_snapshot_load_ms=(
            float(health["last_snapshot_load_ms"])
            if health.get("last_snapshot_load_ms") is not None
            else None
        ),
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(json.dumps(asdict(run_runtime_overhead_certification()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
