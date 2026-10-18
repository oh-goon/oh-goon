from __future__ import annotations

import hashlib
import json
import os
import signal
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.repository_tooling.network_certification import blocked_network_attempts
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
class ScoutCrashRestartResult:
    repositories: int
    observations: int
    concurrent_users: int
    journal_revision: int
    retained_runs_after_restart: int
    retained_steps_after_restart: int
    lag_revisions_after_restart: int
    fingerprints_preserved: bool
    sidecar_bytes_after_crash: int
    sidecar_bytes_after_restart: int
    successful_requests_after_restart: int
    wave_total_ms: float
    wave_p50_ms: float
    wave_p95_ms: float
    blocked_outbound_attempts: int


def _crash_server(process) -> None:
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=10)
    if process.returncode != -signal.SIGKILL:
        raise RuntimeError(
            f"Scout crash certification expected SIGKILL, got {process.returncode}"
        )


def _sidecar_bytes(state: Path) -> int:
    sidecar = scout_analytical_database_path(state)
    family = (sidecar, Path(f"{sidecar}-wal"), Path(f"{sidecar}-shm"))
    return sum(path.stat().st_size for path in family if path.is_file())


def _stable_payload(value):
    if isinstance(value, dict):
        return {
            key: _stable_payload(item)
            for key, item in sorted(value.items())
            if key != "window"
        }
    if isinstance(value, list):
        return [_stable_payload(item) for item in value]
    return value


def _route_fingerprints(routes: dict[str, str]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for name, url in routes.items():
        if name == "health":
            continue
        payload = _stable_payload(_get_json(url))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        fingerprints[name] = hashlib.sha256(encoded).hexdigest()
    return fingerprints


def _assert_health_persistence(
    health: dict[str, Any], *, repositories: int, observations: int, revision: int
) -> None:
    expected = {
        "retained_run_observations": repositories,
        "retained_step_observations": observations,
        "journal_revision": revision,
        "processed_journal_revision": revision,
        "lag_revisions": 0,
    }
    for name, value in expected.items():
        if health.get(name) != value:
            raise RuntimeError(
                f"Scout crash/restart persistence mismatch for {name}: "
                f"{health.get(name)!r} != {value!r}"
            )


def _pre_crash_phase(
    *,
    root: Path,
    state: Path,
    league: Path,
    port: int,
    base: str,
    guard_log: Path,
    repositories: int,
    observations: int,
    revision: int,
    concurrent_users: int,
) -> tuple[dict[str, str], int]:
    first = _start_server(
        root=root, state=state, league=league, port=port, loopback_guard_log=guard_log
    )
    try:
        _wait_json(f"{base}/api/health", process=first)
        routes = _representative_routes(base)
        _measure_routes(routes)
        _concurrent_routes(routes, min(50, concurrent_users))
        _assert_health_persistence(
            _get_json(routes["health"]),
            repositories=repositories,
            observations=observations,
            revision=revision,
        )
        fingerprints = _route_fingerprints(routes)
        _crash_server(first)
    except BaseException:
        if first.poll() is None:
            _stop_server(first)
        raise
    bytes_after_crash = _sidecar_bytes(state)
    if bytes_after_crash <= 0:
        raise RuntimeError(
            "Scout analytical sidecar disappeared after abrupt server crash"
        )
    return fingerprints, bytes_after_crash


def _post_restart_phase(
    *,
    root: Path,
    state: Path,
    league: Path,
    port: int,
    base: str,
    guard_log: Path,
    repositories: int,
    observations: int,
    revision: int,
    concurrent_users: int,
    before_fingerprints: dict[str, str],
) -> tuple[dict[str, Any], float, tuple[float, ...], dict[str, str]]:
    second = _start_server(
        root=root, state=state, league=league, port=port, loopback_guard_log=guard_log
    )
    try:
        _wait_json(f"{base}/api/health", process=second)
        routes = _representative_routes(base)
        _assert_health_persistence(
            _get_json(routes["health"]),
            repositories=repositories,
            observations=observations,
            revision=revision,
        )
        after_fingerprints = _route_fingerprints(routes)
        if after_fingerprints != before_fingerprints:
            raise RuntimeError(
                "Scout analytical responses changed across crash/restart"
            )
        wave_total, latencies = _concurrent_routes(routes, concurrent_users)
        final_health = _get_json(routes["health"])
        _assert_health_persistence(
            final_health,
            repositories=repositories,
            observations=observations,
            revision=revision,
        )
        return final_health, wave_total, latencies, after_fingerprints
    finally:
        _stop_server(second)


def run_crash_restart_certification(
    *,
    root: Path = ROOT,
    repositories: int = 5_000,
    steps_per_repository: int = 4,
    concurrent_users: int = 500,
) -> ScoutCrashRestartResult:
    with TemporaryDirectory(prefix="oh-goon-scout-crash-restart-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        port = _port()
        base = f"http://127.0.0.1:{port}"
        revision, observations = _prepare_certification_state(
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
        before_fingerprints, bytes_after_crash = _pre_crash_phase(
            root=root,
            state=state,
            league=league,
            port=port,
            base=base,
            guard_log=guard_log,
            repositories=repositories,
            observations=observations,
            revision=revision,
            concurrent_users=concurrent_users,
        )
        final_health, wave_total, latencies, after_fingerprints = _post_restart_phase(
            root=root,
            state=state,
            league=league,
            port=port,
            base=base,
            guard_log=guard_log,
            repositories=repositories,
            observations=observations,
            revision=revision,
            concurrent_users=concurrent_users,
            before_fingerprints=before_fingerprints,
        )
        attempts = blocked_network_attempts(guard_log)
        if attempts:
            raise RuntimeError(
                f"Scout crash/restart certification attempted outbound network: {attempts[:5]!r}"
            )
        bytes_after_restart = _sidecar_bytes(state)
    return ScoutCrashRestartResult(
        repositories=repositories,
        observations=observations,
        concurrent_users=concurrent_users,
        journal_revision=revision,
        retained_runs_after_restart=int(final_health["retained_run_observations"]),
        retained_steps_after_restart=int(final_health["retained_step_observations"]),
        lag_revisions_after_restart=int(final_health["lag_revisions"]),
        fingerprints_preserved=after_fingerprints == before_fingerprints,
        sidecar_bytes_after_crash=bytes_after_crash,
        sidecar_bytes_after_restart=bytes_after_restart,
        successful_requests_after_restart=len(latencies),
        wave_total_ms=round(wave_total, 3),
        wave_p50_ms=round(statistics.median(latencies), 3),
        wave_p95_ms=round(_percentile(latencies, 0.95), 3),
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(
        json.dumps(asdict(run_crash_restart_certification()), indent=2, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
