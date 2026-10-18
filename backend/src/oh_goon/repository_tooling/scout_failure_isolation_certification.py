from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
import urllib.error
import urllib.request

from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.local_access_http import authorized_request
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.scout_http_load_probe import (
    ROOT,
    SCOUT_HTTP_ACCESS_TOKEN,
    _get_json,
    _port,
    _start_server,
    _stop_server,
    _wait_json,
)


@dataclass(frozen=True, slots=True)
class ScoutFailureIsolationResult:
    scout_failure_status: int
    execution_run_id: str
    execution_status: str
    execution_outcome: str
    game_tape_contains_acceptance_step: bool
    scout_still_failed_after_execution: bool
    blocked_outbound_attempts: int


def _scout_failure_status(base: str) -> int:
    try:
        request = authorized_request(
            f"{base}/api/scout/health", access_token=SCOUT_HTTP_ACCESS_TOKEN
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code)


def run_failure_isolation_certification(
    *, root: Path = ROOT
) -> ScoutFailureIsolationResult:
    with TemporaryDirectory(prefix="oh-goon-scout-failure-isolation-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        state.parent.mkdir(parents=True, exist_ok=True)
        broken_sidecar = scout_analytical_database_path(state)
        broken_sidecar.mkdir()
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
            scout_status = _scout_failure_status(base)
            if scout_status < 500:
                raise RuntimeError(
                    f"Scout failure-isolation setup did not produce a real analytical failure: HTTP {scout_status}"
                )
            run_id = run_acceptance_goon(
                base_url=base,
                server_repository_path=str(repo.resolve()),
                access_token=SCOUT_HTTP_ACCESS_TOKEN,
            )
            fleet = _get_json(f"{base}/api/fleet/runs/{run_id}")
            scout_after = _scout_failure_status(base)
        finally:
            _stop_server(server)
        attempts = blocked_network_attempts(guard_log)
        if attempts:
            raise RuntimeError(
                f"Scout failure-isolation certification attempted outbound network: {attempts[:5]!r}"
            )
    run = fleet.get("run") if isinstance(fleet, dict) else None
    status = run.get("status") if isinstance(run, dict) else None
    outcome = fleet.get("outcome") if isinstance(fleet, dict) else None
    game_tape_ok = "acceptance-step" in json.dumps(fleet.get("game_tape"))
    if status != "COMPLETED" or outcome != "SUCCESS" or not game_tape_ok:
        raise RuntimeError(
            "Scout analytical failure changed the real acceptance Goon outcome"
        )
    if scout_after < 500:
        raise RuntimeError(
            "Scout analytical failure unexpectedly disappeared during execution"
        )
    return ScoutFailureIsolationResult(
        scout_failure_status=scout_status,
        execution_run_id=run_id,
        execution_status=str(status),
        execution_outcome=str(outcome),
        game_tape_contains_acceptance_step=game_tape_ok,
        scout_still_failed_after_execution=True,
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(
        json.dumps(
            asdict(run_failure_isolation_certification()), indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
