from __future__ import annotations

import json
import os
import socket
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlencode

from oh_goon.core import local_access_protocol
from oh_goon.core.time import utc_now
from oh_goon.repository_tooling.local_access_http import (
    authorized_request,
    new_certification_access_token,
)
from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.scout_load_probe import build_scout_load_snapshot
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.store import ScoutObservationStore

ROOT = Path(__file__).resolve().parents[4]
SCOUT_HTTP_ACCESS_TOKEN = new_certification_access_token()


@dataclass(frozen=True, slots=True)
class ScoutHttpLoadProbeResult:
    repositories: int
    steps_per_repository: int
    concurrent_users: int
    observations: int
    journal_revision: int
    route_latencies_ms: dict[str, float]
    concurrent_total_ms: float
    concurrent_p50_ms: float
    concurrent_p95_ms: float
    successful_requests: int
    loopback_guard_enabled: bool
    blocked_outbound_attempts: int


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_exit_stderr(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    return process.stderr.read().decode("utf-8", errors="replace")[-12000:].strip()


def _wait_json(
    url: str,
    timeout_seconds: float = 30.0,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and (returncode := process.poll()) is not None:
            detail = _server_exit_stderr(process)
            message = f"Scout server exited with code {returncode} while waiting for {url}"
            if detail:
                message += f"\nScout server stderr:\n{detail}"
            raise RuntimeError(message)
        try:
            return _get_json(url)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def _get_json(url: str) -> dict[str, Any]:
    request = authorized_request(url, access_token=SCOUT_HTTP_ACCESS_TOKEN)
    with urllib.request.urlopen(request, timeout=30.0) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return payload


def _start_server(
    *,
    root: Path,
    state: Path,
    league: Path,
    port: int,
    loopback_guard_log: Path | None = None,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "UV_RESOLUTION",
        "UV_PRERELEASE",
        "UV_EXCLUDE_NEWER",
    ):
        env.pop(name, None)
    if loopback_guard_log is not None:
        env["OH_GOON_LOOPBACK_GUARD_LOG"] = str(loopback_guard_log)
    env.update(
        {
            "OH_GOON_DB": str(state),
            "OH_GOON_LEAGUE_FILE": str(league),
            "OH_GOON_SCOUT_ENABLED": "1",
            local_access_protocol.LOCAL_ACCESS_ENV: SCOUT_HTTP_ACCESS_TOKEN,
        }
    )
    argv = ["uv", "run", "--frozen", "--no-dev", "--project", "backend"]
    if loopback_guard_log is not None:
        argv.append("oh-goon-guarded-uvicorn")
    else:
        argv.extend(("uvicorn", "oh_goon.api.app:app"))
    argv.extend(("--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"))
    return subprocess.Popen(
        tuple(argv),
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _stop_server(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _journal_revision(database: LocalDatabase) -> int:
    with database.transaction(track_revision=False) as connection:
        row = connection.execute("SELECT MAX(revision) AS revision FROM journal_entries").fetchone()
    return int(row["revision"] or 0)


def _seed_scout_sidecar(
    *,
    database: LocalDatabase,
    repositories: int,
    steps_per_repository: int,
    journal_revision: int,
) -> int:
    if journal_revision < 1:
        raise RuntimeError("HTTP load setup requires canonical Journal truth before Scout seeding")
    now = (utc_now() - timedelta(minutes=5)).replace(second=0, microsecond=0)
    snapshot = build_scout_load_snapshot(
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        now=now,
        journal_revision=journal_revision,
    )
    store = ScoutObservationStore(database)
    store.reset()
    stored_revision = store.replace_changed_runs(
        run_ids=tuple(run.run_id for run in snapshot.runs),
        snapshot=snapshot,
        journal_revision=journal_revision,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    if stored_revision != journal_revision:
        raise RuntimeError("Scout load sidecar did not retain the canonical Journal revision")
    return len(snapshot.steps)


def _representative_routes(base: str) -> dict[str, str]:
    goon_query = urlencode({"name": "python/python-tests", "source_sha256": "a" * 64, "hours": 24})
    return {
        "health": f"{base}/api/scout/health",
        "overview": f"{base}/api/scout/overview?hours=24&limit=20",
        "findings": f"{base}/api/scout/findings?hours=24&category=CPU_OUTLIER&limit=20",
        "goon_leaders": f"{base}/api/scout/goon-leaders?hours=24&limit=20",
        "repository": f"{base}/api/scout/repositories/repo-0?hours=24",
        "plan": f"{base}/api/scout/plans/plan-standard?hours=24",
        "goon": f"{base}/api/scout/goons?{goon_query}",
        "search": f"{base}/api/scout/search?q=repository-1&hours=24&limit=20",
    }


def _measure_routes(routes: dict[str, str]) -> dict[str, float]:
    measured: dict[str, float] = {}
    for name, url in routes.items():
        started = time.perf_counter()
        try:
            payload = _get_json(url)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Scout route {name} failed with HTTP {exc.code}: {detail}") from exc
        if payload.get("enabled") is False:
            raise RuntimeError(f"Scout unexpectedly disabled for route {name}")
        measured[name] = round((time.perf_counter() - started) * 1000, 3)
    return measured


def _concurrent_routes(routes: dict[str, str], users: int) -> tuple[float, tuple[float, ...]]:
    heavy = (
        routes["overview"],
        routes["findings"],
        routes["goon_leaders"],
        routes["repository"],
        routes["plan"],
        routes["goon"],
    )

    def selected_url(index: int) -> str:
        position = index / max(1, users)
        if position < 0.80:
            return routes["health"]
        if position < 0.94:
            return routes["search"]
        return heavy[index % len(heavy)]

    def request(index: int) -> float:
        url = selected_url(index)
        started = time.perf_counter()
        try:
            _get_json(url)
        except Exception as exc:
            raise RuntimeError(f"concurrent Scout request failed for {url}: {exc}") from exc
        return (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as executor:
        latencies = tuple(executor.map(request, range(users)))
    return (time.perf_counter() - started) * 1000, latencies


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _prepare_certification_state(
    *,
    root: Path,
    state: Path,
    league: Path,
    repo: Path,
    port: int,
    base: str,
    guard_log: Path | None,
    repositories: int,
    steps_per_repository: int,
) -> tuple[int, int]:
    prepare_acceptance_repository(repo)
    server = _start_server(
        root=root, state=state, league=league, port=port, loopback_guard_log=guard_log
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
    observations = _seed_scout_sidecar(
        database=database,
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        journal_revision=revision,
    )
    return revision, observations


def _measure_certified_http_runtime(
    *,
    root: Path,
    state: Path,
    league: Path,
    port: int,
    base: str,
    guard_log: Path | None,
    repositories: int,
    observations: int,
    concurrent_users: int,
) -> tuple[dict[str, float], float, tuple[float, ...]]:
    server = _start_server(
        root=root, state=state, league=league, port=port, loopback_guard_log=guard_log
    )
    failure: BaseException | None = None
    try:
        _wait_json(f"{base}/api/health", process=server)
        routes = _representative_routes(base)
        route_latencies = _measure_routes(routes)
        concurrent_total, latencies = _concurrent_routes(routes, concurrent_users)
        health = _get_json(routes["health"])
        if health.get("retained_run_observations") != repositories:
            raise RuntimeError(
                "HTTP Scout health does not expose the seeded repository/run population"
            )
        if health.get("retained_step_observations") != observations:
            raise RuntimeError("HTTP Scout health does not expose the seeded Step population")
    except BaseException as exc:
        failure = exc
    finally:
        _stop_server(server)
    server_stderr = server.stderr.read().decode("utf-8", errors="replace") if server.stderr else ""
    attempts = blocked_network_attempts(guard_log) if guard_log is not None else ()
    if attempts:
        raise RuntimeError(
            "Scout real-HTTP certification attempted non-loopback outbound network access: "
            + json.dumps(attempts[:5], sort_keys=True)
        ) from failure
    if failure is not None:
        if server_stderr.strip():
            raise RuntimeError(
                f"{failure}\nGuarded Uvicorn stderr:\n{server_stderr[-12000:]}"
            ) from failure
        raise failure
    return route_latencies, concurrent_total, latencies


def run_http_probe(
    *,
    root: Path = ROOT,
    repositories: int = 5_000,
    steps_per_repository: int = 4,
    concurrent_users: int = 500,
    enforce_loopback_only: bool = False,
) -> ScoutHttpLoadProbeResult:
    with TemporaryDirectory(prefix="oh-goon-scout-http-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        port = _port()
        base = f"http://127.0.0.1:{port}"
        guard_log = temp / "network-audit.jsonl" if enforce_loopback_only else None
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
        route_latencies, concurrent_total, latencies = _measure_certified_http_runtime(
            root=root,
            state=state,
            league=league,
            port=port,
            base=base,
            guard_log=guard_log,
            repositories=repositories,
            observations=observations,
            concurrent_users=concurrent_users,
        )
    return ScoutHttpLoadProbeResult(
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        concurrent_users=concurrent_users,
        observations=observations,
        journal_revision=revision,
        route_latencies_ms=route_latencies,
        concurrent_total_ms=round(concurrent_total, 3),
        concurrent_p50_ms=round(statistics.median(latencies), 3),
        concurrent_p95_ms=round(_percentile(latencies, 0.95), 3),
        successful_requests=len(latencies),
        loopback_guard_enabled=enforce_loopback_only,
        blocked_outbound_attempts=0,
    )


def main() -> int:
    result = run_http_probe()
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
