from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.repository_tooling.local_access_http import authorized_request
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED"}
GOON = """version: 1
steps:
- id: acceptance-step
  run: uv run --no-project python acceptance_step.py
"""


INTERRUPTIBLE_STEP = """import os
from pathlib import Path
import time

Path("acceptance-step.pid").write_text(str(os.getpid()), encoding="utf-8")
print("oh-goon interruptible acceptance step started", flush=True)
time.sleep({duration_seconds!r})
Path("acceptance-step.finished").write_text("finished", encoding="utf-8")
"""


def _prepare_repository(path: Path, *, script: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    (path / "goon.yml").write_text(GOON, encoding="utf-8")
    (path / "acceptance_step.py").write_text(script, encoding="utf-8")
    (path / "pyproject.toml").write_text(
        "[project]\nname = 'oh-goon-acceptance'\nversion = '0.0.0'\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("# Oh Goon runtime acceptance repository\n", encoding="utf-8")
    # Acceptance repositories must cross the same locked execution boundary as real
    # users. Generate from the canonical resolver instead of teaching certification
    # helpers an unlocked shortcut.
    entrypoint = path / "goon.yml"
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(entrypoint)
    plan = resolver.resolve_source_graph(graph)
    write_execution_lock(path / "goon.lock", build_execution_lock(plan))


def prepare_acceptance_repository(path: Path) -> None:
    _prepare_repository(path, script='print("oh-goon acceptance step passed")\n')


def prepare_interruptible_acceptance_repository(
    path: Path, *, duration_seconds: float = 300.0
) -> None:
    _prepare_repository(path, script=INTERRUPTIBLE_STEP.format(duration_seconds=duration_seconds))


def _request_json(
    method: str,
    url: str,
    *,
    access_token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(
        url, access_token=access_token, data=data, method=method, headers=headers
    )
    try:
        response = urllib.request.urlopen(request, timeout=5.0)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    with response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return value


def team_run_authority(
    *, base_url: str, server_repository_path: str, access_token: str
) -> tuple[str, str]:
    """Read the exact single-Team freshness authority exposed by the League API."""
    league = _request_json("GET", f"{base_url}/api/league", access_token=access_token)
    teams = league.get("teams")
    if not isinstance(teams, list):
        raise RuntimeError("League API did not return a Team list for acceptance admission")
    expected_path = str(Path(server_repository_path).resolve())
    for team in teams:
        if not isinstance(team, dict):
            continue
        repository = team.get("repository")
        if not isinstance(repository, dict) or repository.get("path") != expected_path:
            continue
        pipeline = team.get("pipeline")
        if not isinstance(pipeline, dict) or pipeline.get("state") != "CONFIGURED":
            raise RuntimeError(f"acceptance Team is not CONFIGURED in League projection: {team!r}")
        pipeline_sha256 = pipeline.get("sha256")
        repository_instance_id = team.get("repository_instance_id")
        if (
            not isinstance(pipeline_sha256, str)
            or len(pipeline_sha256) != 64
            or any(char not in "0123456789abcdef" for char in pipeline_sha256)
            or not isinstance(repository_instance_id, str)
            or not repository_instance_id
        ):
            raise RuntimeError(
                f"acceptance Team projection is missing freshness authority: {team!r}"
            )
        return pipeline_sha256, repository_instance_id
    raise RuntimeError(
        f"acceptance Team {expected_path!r} was not present in the League projection"
    )


def team_run_request_payload(
    base_url: str, server_repository_path: str, access_token: str, fleet_run_id: str
) -> dict[str, object]:
    expected_pipeline_sha256, expected_repository_instance_id = team_run_authority(
        base_url=base_url,
        server_repository_path=server_repository_path,
        access_token=access_token,
    )
    return {
        "repository": server_repository_path,
        "fleet_run_id": fleet_run_id,
        "expected_pipeline_sha256": expected_pipeline_sha256,
        "expected_repository_instance_id": expected_repository_instance_id,
        "inputs": {},
    }


def start_acceptance_goon(*, base_url: str, server_repository_path: str, access_token: str) -> str:
    parent = str(Path(server_repository_path).parent)
    locations = _request_json("GET", f"{base_url}/api/locations", access_token=access_token)
    existing = locations.get("locations", [])
    if parent not in existing:
        _request_json(
            "POST", f"{base_url}/api/locations", access_token=access_token, payload={"path": parent}
        )
    run_id = f"acceptance-{uuid.uuid4().hex[:16]}"
    _request_json(
        "POST",
        f"{base_url}/api/league/team-runs",
        access_token=access_token,
        payload=team_run_request_payload(base_url, server_repository_path, access_token, run_id),
    )
    return run_id


def cancel_acceptance_goon(*, base_url: str, run_id: str, access_token: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{base_url}/api/fleet/runs/{run_id}/cancel",
        access_token=access_token,
    )


def _run_progress_summary(detail: dict[str, Any] | None) -> dict[str, object]:
    if not isinstance(detail, dict):
        return {"run_status": None, "outcome": None, "canonical_failure": None}
    run = detail.get("run")
    run_status = run.get("status") if isinstance(run, dict) else None
    tape = detail.get("game_tape")
    repositories = tape.get("repositories") if isinstance(tape, dict) else None
    repository_status, step_status, attempt_count = _first_repository_progress(repositories)
    return {
        "run_status": run_status,
        "outcome": detail.get("outcome"),
        "repository_status": repository_status,
        "step_status": step_status,
        "attempt_count": attempt_count,
        "canonical_failure": detail.get("failure"),
        "issue_focus": tape.get("issue_focus") if isinstance(tape, dict) else None,
    }


def _first_repository_progress(repositories: object) -> tuple[object, object, object]:
    if (
        not isinstance(repositories, list)
        or not repositories
        or not isinstance(repositories[0], dict)
    ):
        return None, None, None
    repository = repositories[0]
    steps = repository.get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(steps[0], dict):
        return repository.get("status"), None, None
    step = steps[0]
    return repository.get("status"), step.get("status"), step.get("attempt_count")


def wait_for_acceptance_run_status(
    *,
    base_url: str,
    run_id: str,
    access_token: str,
    expected_status: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _request_json(
            "GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token
        )
        run = last.get("run")
        status = run.get("status") if isinstance(run, dict) else None
        if status == expected_status:
            return last
        if status in TERMINAL and status != expected_status:
            raise RuntimeError(
                f"acceptance Run reached {status!r}, expected {expected_status!r}: {last!r}"
            )
        time.sleep(0.05)
    raise RuntimeError(
        f"acceptance Run did not reach {expected_status!r} within {timeout_seconds}s; last={last!r}"
    )


def _canonical_process_start(
    *, base_url: str, run_id: str, access_token: str
) -> tuple[int | None, list[str]]:
    """Read canonical lifecycle evidence for the interruptible Step start boundary."""

    batch = _request_json(
        "GET", f"{base_url}/api/fleet/runs/{run_id}/events?after=0", access_token=access_token
    )
    raw_events = batch.get("events")
    if not isinstance(raw_events, list):
        return None, []
    event_types: list[str] = []
    started_pid: int | None = None
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if isinstance(event_type, str):
            event_types.append(event_type)
        if event_type == "PROCESS_STARTED":
            pid = event.get("pid")
            if isinstance(pid, int) and pid > 0:
                started_pid = pid
    return started_pid, event_types[-12:]


def wait_for_interruptible_step(
    *,
    base_url: str,
    run_id: str,
    repository: Path,
    access_token: str,
    timeout_seconds: float = 180.0,
) -> int:
    del (
        repository
    )  # Acceptance start authority is the canonical lifecycle journal, not a PID marker.
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    recent_events: list[str] = []
    while time.monotonic() < deadline:
        last = _request_json(
            "GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token
        )
        run = last.get("run")
        status = run.get("status") if isinstance(run, dict) else None
        if status in TERMINAL:
            raise RuntimeError(
                "interruptible acceptance Run terminalized before crash point; "
                f"diagnostic={_run_progress_summary(last)!r}; "
                "classification=CRASH_POINT_WINDOW_MISSED; "
                "next_diagnostic=inspect canonical lifecycle around PROCESS_STARTED"
            )
        try:
            started_pid, recent_events = _canonical_process_start(
                base_url=base_url, run_id=run_id, access_token=access_token
            )
        except (OSError, RuntimeError, urllib.error.URLError):
            started_pid = None
        if started_pid is not None:
            return started_pid
        time.sleep(0.05)
    progress = _run_progress_summary(last)
    latest_transition = recent_events[-1] if recent_events else None
    raise RuntimeError(
        "interruptible acceptance Step process start was not observed within "
        f"{timeout_seconds}s; diagnostic={progress!r}; "
        f"recent_events={recent_events!r}; latest_transition={latest_transition!r}; "
        "classification=INCOMPLETE_START_NOT_OBSERVED_WITHIN_ACCEPTANCE_BOUND; "
        "next_diagnostic=inspect canonical lifecycle predecessor/owner before PROCESS_STARTED"
    )


def run_acceptance_goon(
    *,
    base_url: str,
    server_repository_path: str,
    access_token: str,
    timeout_seconds: float = 60.0,
) -> str:
    run_id = start_acceptance_goon(
        base_url=base_url, server_repository_path=server_repository_path, access_token=access_token
    )
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            last = _request_json(
                "GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                time.sleep(0.2)
                continue
            raise
        run = last.get("run")
        status = run.get("status") if isinstance(run, dict) else None
        if status in TERMINAL:
            if status != "COMPLETED" or last.get("outcome") != "SUCCESS":
                raise RuntimeError(
                    f"acceptance Goon did not pass: status={status!r} outcome={last.get('outcome')!r}"
                )
            if "acceptance-step" not in json.dumps(last.get("game_tape")):
                raise RuntimeError("acceptance Game Tape does not contain the executed Step")
            return run_id
        time.sleep(0.2)
    raise RuntimeError(f"acceptance Goon did not finish within {timeout_seconds}s; last={last!r}")
