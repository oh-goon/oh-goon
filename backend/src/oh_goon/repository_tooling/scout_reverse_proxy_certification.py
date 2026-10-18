from __future__ import annotations

import json
import shutil
import statistics
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.scout_http_load_probe import (
    ROOT,
    SCOUT_HTTP_ACCESS_TOKEN,
    _concurrent_routes,
    _get_json,
    _journal_revision,
    _measure_routes,
    _percentile,
    _port,
    _representative_routes,
    _seed_scout_sidecar,
    _start_server,
    _stop_server,
    _wait_json,
)
from oh_goon.runtime.database import LocalDatabase


@dataclass(frozen=True, slots=True)
class ScoutReverseProxyResult:
    repositories: int
    steps_per_repository: int
    concurrent_users: int
    observations: int
    journal_revision: int
    proxy_version: str
    route_latencies_ms: dict[str, float]
    concurrent_total_ms: float
    concurrent_p50_ms: float
    concurrent_p95_ms: float
    successful_requests: int
    blocked_outbound_attempts: int


def _nginx_version(executable: str) -> str:
    result = subprocess.run([executable, "-v"], text=True, capture_output=True, check=True)
    return (result.stderr or result.stdout).strip()


def _nginx_config(*, prefix: Path, listen_port: int, upstream_port: int) -> str:
    return f"""worker_processes 1;
pid {prefix / "nginx.pid"};
error_log stderr warn;
events {{ worker_connections 2048; }}
http {{
    access_log off;
    client_body_temp_path {prefix / "client_body"};
    proxy_temp_path {prefix / "proxy_temp"};
    server {{
        listen 127.0.0.1:{listen_port};
        location / {{
            proxy_pass http://127.0.0.1:{upstream_port};
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-Proto http;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_connect_timeout 5s;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }}
    }}
}}
"""


def _start_nginx(
    *, temp: Path, listen_port: int, upstream_port: int
) -> tuple[subprocess.Popen[bytes], str]:
    executable = shutil.which("nginx")
    if executable is None:
        raise RuntimeError("nginx is required for Scout reverse-proxy certification")
    prefix = temp / f"nginx-{listen_port}"
    shutil.rmtree(prefix, ignore_errors=True)
    prefix.mkdir(parents=True)
    (prefix / "client_body").mkdir()
    (prefix / "proxy_temp").mkdir()
    config = prefix / "nginx.conf"
    config.write_text(
        _nginx_config(prefix=prefix, listen_port=listen_port, upstream_port=upstream_port),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [executable, "-p", f"{prefix}/", "-c", str(config), "-g", "daemon off;"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return process, _nginx_version(executable)


def _stop_process(process: subprocess.Popen[bytes]) -> str:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""


def _prepare_proxy_state(
    *,
    root: Path,
    temp: Path,
    state: Path,
    league: Path,
    repo: Path,
    backend_port: int,
    proxy_port: int,
    guard_log: Path,
    repositories: int,
    steps_per_repository: int,
) -> tuple[int, int, str]:
    prepare_acceptance_repository(repo)
    backend = _start_server(
        root=root,
        state=state,
        league=league,
        port=backend_port,
        loopback_guard_log=guard_log,
    )
    proxy, version = _start_nginx(temp=temp, listen_port=proxy_port, upstream_port=backend_port)
    base = f"http://127.0.0.1:{proxy_port}"
    try:
        _wait_json(f"http://127.0.0.1:{backend_port}/api/health", process=backend)
        _wait_json(f"{base}/api/health", process=proxy)
        run_acceptance_goon(
            base_url=base,
            server_repository_path=str(repo.resolve()),
            access_token=SCOUT_HTTP_ACCESS_TOKEN,
        )
    finally:
        proxy_error = _stop_process(proxy)
        _stop_server(backend)
    if proxy.returncode not in (0, -15):
        raise RuntimeError(f"nginx failed during acceptance setup: {proxy_error[-4000:]}")
    database = LocalDatabase(state)
    revision = _journal_revision(database)
    observations = _seed_scout_sidecar(
        database=database,
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        journal_revision=revision,
    )
    return revision, observations, version


def _run_proxy_workload(
    *,
    root: Path,
    temp: Path,
    state: Path,
    league: Path,
    backend_port: int,
    proxy_port: int,
    guard_log: Path,
    repositories: int,
    observations: int,
    concurrent_users: int,
) -> tuple[dict[str, float], float, tuple[float, ...]]:
    backend = _start_server(
        root=root,
        state=state,
        league=league,
        port=backend_port,
        loopback_guard_log=guard_log,
    )
    proxy, _ = _start_nginx(temp=temp, listen_port=proxy_port, upstream_port=backend_port)
    base = f"http://127.0.0.1:{proxy_port}"
    failure: BaseException | None = None
    try:
        _wait_json(f"http://127.0.0.1:{backend_port}/api/health", process=backend)
        _wait_json(f"{base}/api/health", process=proxy)
        routes = _representative_routes(base)
        route_latencies = _measure_routes(routes)
        concurrent_total, latencies = _concurrent_routes(routes, concurrent_users)
        health = _get_json(routes["health"])
        if health.get("retained_run_observations") != repositories:
            raise RuntimeError("proxied Scout health lost the seeded run population")
        if health.get("retained_step_observations") != observations:
            raise RuntimeError("proxied Scout health lost the seeded Step population")
    except BaseException as exc:
        failure = exc
    finally:
        proxy_error = _stop_process(proxy)
        _stop_server(backend)
    attempts = blocked_network_attempts(guard_log)
    if attempts:
        raise RuntimeError(
            "proxied Scout runtime attempted non-loopback outbound network access: "
            + json.dumps(attempts[:5], sort_keys=True)
        ) from failure
    if failure is not None:
        raise RuntimeError(f"{failure}\nnginx stderr:\n{proxy_error[-8000:]}") from failure
    return route_latencies, concurrent_total, latencies


def run_reverse_proxy_certification(
    *,
    root: Path = ROOT,
    repositories: int = 5_000,
    steps_per_repository: int = 4,
    concurrent_users: int = 500,
) -> ScoutReverseProxyResult:
    with TemporaryDirectory(prefix="oh-goon-scout-proxy-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        backend_port, proxy_port = _port(), _port()
        revision, observations, version = _prepare_proxy_state(
            root=root,
            temp=temp,
            state=state,
            league=league,
            repo=repo,
            backend_port=backend_port,
            proxy_port=proxy_port,
            guard_log=guard_log,
            repositories=repositories,
            steps_per_repository=steps_per_repository,
        )
        route_latencies, concurrent_total, latencies = _run_proxy_workload(
            root=root,
            temp=temp,
            state=state,
            league=league,
            backend_port=backend_port,
            proxy_port=proxy_port,
            guard_log=guard_log,
            repositories=repositories,
            observations=observations,
            concurrent_users=concurrent_users,
        )
    return ScoutReverseProxyResult(
        repositories=repositories,
        steps_per_repository=steps_per_repository,
        concurrent_users=concurrent_users,
        observations=observations,
        journal_revision=revision,
        proxy_version=version,
        route_latencies_ms=route_latencies,
        concurrent_total_ms=round(concurrent_total, 3),
        concurrent_p50_ms=round(statistics.median(latencies), 3),
        concurrent_p95_ms=round(_percentile(latencies, 0.95), 3),
        successful_requests=len(latencies),
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(json.dumps(asdict(run_reverse_proxy_certification()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
