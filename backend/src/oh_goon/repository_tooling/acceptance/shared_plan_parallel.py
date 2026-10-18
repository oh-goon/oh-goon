from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request
import uuid

from oh_goon.core.process_birth import observe_process_liveness
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.repository_tooling.acceptance.shared_plan_evidence import (
    require_preview_counts,
    require_preview_work,
    require_repository_bound_step_started,
)
from oh_goon.repository_tooling.acceptance.uv_fixture import (
    editable_console_build_backend,
    editable_uv_lock,
)
from oh_goon.repository_tooling.local_access_http import authorized_request
PARALLEL_PLAN_KEY = "acceptance-parallel-shared"

PARALLEL_SHARED_PLAN = """version: 1
execution:
  resources:
    cpu_millis: 500
    memory_mib: 256
    max_processes: 16
  repositories:
    mode: parallel
    max_parallel: 2
inputs:
- release_channel
- revision
goons:
- python/release-metadata
"""

_PARALLEL_RUNTIME_TEMPLATE = r'''from __future__ import annotations

import argparse
from pathlib import Path
import os
import time

BEHAVIOR = {behavior!r}
MARKER_ROOT = Path({marker_root!r})

def _marker(kind: str, revision: str) -> Path:
    return MARKER_ROOT / f"parallel-{{kind}}-{{revision}}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(f"OH_GOON_PARALLEL_ACCEPTANCE revision={{args.revision}} pid={{os.getpid()}} behavior={{BEHAVIOR}}", flush=True)
    _marker("started", args.revision).write_text(f"{{os.getpid()}}\n", encoding="utf-8")
    if args.revision.startswith("c"):
        time.sleep(30)
    elif args.revision.startswith("f") and BEHAVIOR == "fail":
        time.sleep(1.25)
        _marker("failed", args.revision).write_text("intentional repository-owned failure\n", encoding="utf-8")
        raise SystemExit(17)
    elif args.revision.startswith("f") and BEHAVIOR == "slow-success":
        time.sleep(2.0)
    else:
        time.sleep(0.75)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    output.joinpath("PROJECT_VERSION").write_text("1.0.0\n", encoding="utf-8")
    output.joinpath("RELEASE_VERSION").write_text(
        f"1.0.0-{{args.channel}}.{{args.revision[:7]}}\n", encoding="utf-8"
    )
    _marker("finished", args.revision).write_text("completed\n", encoding="utf-8")
'''


def _parallel_runtime(path: Path, behavior: str) -> str:
    if behavior not in {"normal", "fail", "slow-success"}:
        raise ValueError(f"unsupported parallel acceptance behavior: {behavior}")
    return _PARALLEL_RUNTIME_TEMPLATE.format(
        behavior=behavior, marker_root=str(path.resolve())
    )




def prepare_parallel_shared_repository(path: Path, *, behavior: str = "normal") -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    (path / "pyproject.toml").write_text(
        """[project]
name = "oh-goon-parallel-acceptance"
version = "1.0.0"
requires-python = ">=3.11"

[project.scripts]
release-metadata = "acceptance_parallel:main"

[dependency-groups]
dev = []

[build-system]
requires = []
build-backend = "acceptance_build"
backend-path = ["."]
""",
        encoding="utf-8",
    )
    (path / "uv.lock").write_text(
        editable_uv_lock("oh-goon-parallel-acceptance", "1.0.0"), encoding="utf-8"
    )
    (path / "acceptance_parallel.py").write_text(
        _parallel_runtime(path, behavior), encoding="utf-8"
    )
    (path / "acceptance_build.py").write_text(
        editable_console_build_backend(
            distribution_name="oh-goon-parallel-acceptance",
            module_name="acceptance_parallel",
            command_name="release-metadata",
            version="1.0.0",
        ),
        encoding="utf-8",
    )
    (path / "README.md").write_text("# Oh-Goon parallel Shared Plan acceptance\n", encoding="utf-8")


def repair_parallel_shared_repository(path: Path) -> None:
    """Repair only the repository-owned failing executable; keep repository identity stable."""

    (path / "acceptance_parallel.py").write_text(
        _parallel_runtime(path, "normal"), encoding="utf-8"
    )


def _request_json(
    method: str,
    url: str,
    *,
    access_token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(
        url, access_token=access_token, data=data, method=method, headers=headers
    )
    try:
        response = urllib.request.urlopen(request, timeout=10.0)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    with response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return value


def _team_ids(
    base_url: str, repositories: tuple[Path, Path], *, access_token: str
) -> dict[Path, str]:
    league = _request_json("GET", f"{base_url}/api/league", access_token=access_token)
    teams = league.get("teams")
    if not isinstance(teams, list):
        raise RuntimeError("parallel Shared Plan acceptance did not receive League Teams")
    by_path: dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        repository = team.get("repository")
        if isinstance(repository, dict):
            path = repository.get("path")
            repository_id = repository.get("repository_id")
            if isinstance(path, str) and isinstance(repository_id, str):
                by_path[path] = repository_id
    result: dict[Path, str] = {}
    for repository in repositories:
        key = str(repository.resolve())
        if key not in by_path:
            raise RuntimeError(f"parallel Shared Plan Team was not discovered: {key}")
        result[repository] = by_path[key]
    return result


def _document_revision(document: dict[str, object]) -> str:
    revision = document.get("sha256")
    if not isinstance(revision, str) or len(revision) != 64:
        raise RuntimeError("parallel Shared Plan save lost canonical revision authority")
    return revision


def _processes_for_revision(revision: str) -> set[int]:
    matches: set[int] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return matches
    needle = revision.encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in command and b"release-metadata" in command:
            matches.add(int(entry.name))
    return matches


def _pid_alive(pid: int) -> bool:
    observed = observe_process_liveness(pid)
    if observed is not None:
        return observed
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_parallel_children(revision: str, *, timeout_seconds: float = 30.0) -> tuple[int, int]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        matches = sorted(pid for pid in _processes_for_revision(revision) if _pid_alive(pid))
        if len(matches) >= 2:
            return matches[0], matches[1]
        time.sleep(0.05)
    raise RuntimeError("parallel Shared Plan never had two physical release-metadata children alive")


def _wait_children_dead(pids: tuple[int, int], revision: str, *, timeout_seconds: float = 8.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(not _pid_alive(pid) for pid in pids) and not _processes_for_revision(revision):
            return
        time.sleep(0.05)
    raise RuntimeError(
        f"parallel Shared Plan cancellation left physical children alive: pids={pids}, "
        f"matching={sorted(_processes_for_revision(revision))}"
    )


def _wait_run(
    base_url: str,
    run_id: str,
    *,
    access_token: str,
    expected_status: str,
    timeout_seconds: float = 45.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        last = _request_json("GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token)
        run = last.get("run")
        status = run.get("status") if isinstance(run, dict) else None
        if status == expected_status:
            return last
        if status in {"COMPLETED", "FAILED", "CANCELLED"} and status != expected_status:
            raise RuntimeError(
                f"parallel Shared Plan reached {status!r}, expected {expected_status!r}: {last!r}"
            )
        time.sleep(0.1)
    raise RuntimeError(
        f"parallel Shared Plan did not reach {expected_status!r} within {timeout_seconds}s; last={last!r}"
    )


def _preview(
    base_url: str,
    *,
    access_token: str,
    repository_ids: list[str],
    inputs: dict[str, str],
    expected_revision: str,
) -> dict[str, object]:
    preview = _request_json(
        "POST",
        f"{base_url}/api/league/shared-plan-previews",
        access_token=access_token,
        payload={
            "plan_key": PARALLEL_PLAN_KEY,
            "expected_revision": expected_revision,
            "repository_ids": repository_ids,
            "inputs": inputs,
        },
    )
    teams = require_preview_counts(
        preview, ready=2, blocked=0, label="parallel Shared Plan preview"
    )
    if len(teams) != 2:
        raise RuntimeError("parallel Shared Plan preview lost exact Team evidence")
    for team in teams:
        require_preview_work(
            team,
            applicability="APPLICABLE",
            disposition="READY",
            label="parallel Shared Plan selected Team",
        )
    return preview


def _repository_instance_authority(
    preview: dict[str, object], repository_ids: list[str]
) -> dict[str, str]:
    teams = preview.get("teams")
    if not isinstance(teams, list):
        raise RuntimeError("parallel Shared Plan preview lost Team authority")
    authority: dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        repository = team.get("repository")
        repository_id = repository.get("repository_id") if isinstance(repository, dict) else None
        instance_id = team.get("repository_instance_id")
        if isinstance(repository_id, str) and isinstance(instance_id, str):
            authority[repository_id] = instance_id
    if set(authority) != set(repository_ids):
        raise RuntimeError(
            "parallel Shared Plan preview repository instance authority does not match selected Teams"
        )
    return authority


def _start_payload(
    *,
    repository_ids: list[str],
    preview: dict[str, object],
    inputs: dict[str, str],
    run_id: str,
) -> dict[str, object]:
    return {
        "plan_key": PARALLEL_PLAN_KEY,
        "expected_plan_id": preview.get("plan_id"),
        "expected_execution_digest": preview.get("execution_digest"),
        "expected_plan_revision": preview.get("sha256"),
        "repository_ids": repository_ids,
        "repository_instance_ids": _repository_instance_authority(preview, repository_ids),
        "fleet_run_id": run_id,
        "inputs": inputs,
    }


def _request_expected_error(
    method: str,
    url: str,
    *,
    access_token: str,
    payload: dict[str, object] | None,
    expected_status: int,
    expected_code: str,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(
        url, access_token=access_token, data=data, method=method, headers=headers
    )
    try:
        urllib.request.urlopen(request, timeout=10.0)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code != expected_status:
            raise RuntimeError(
                f"expected HTTP {expected_status} from {url}, got {exc.code}: {body}"
            ) from exc
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("code") != expected_code:
            raise RuntimeError(
                f"expected {expected_code} from {url}, got {value!r}"
            ) from exc
        return value
    raise RuntimeError(f"expected HTTP {expected_status} from {url}, request succeeded")


def _start(
    base_url: str,
    *,
    access_token: str,
    repository_ids: list[str],
    preview: dict[str, object],
    inputs: dict[str, str],
    run_id: str,
) -> None:
    result = _request_json(
        "POST",
        f"{base_url}/api/league/shared-plan-runs",
        access_token=access_token,
        payload=_start_payload(
            repository_ids=repository_ids, preview=preview, inputs=inputs, run_id=run_id
        ),
    )
    if result.get("fleet_run_id") != run_id:
        raise RuntimeError("parallel Shared Plan run did not preserve requested Fleet Run identity")


def _wait_marker(path: Path, kind: str, revision: str, *, timeout_seconds: float = 8.0) -> Path:
    marker = path / f"parallel-{kind}-{revision}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker.is_file():
            return marker
        time.sleep(0.05)
    raise RuntimeError(f"parallel Shared Plan did not produce {kind} marker for {path.name}")


def exercise_parallel_shared_plan_stale_preview_recovery(
    base_url: str,
    repositories_root: Path,
    *,
    access_token: str,
) -> tuple[str, str]:
    """Replace one repository after preview; stale authority must admit no physical work."""

    if not os.environ.get(EXECUTORS_FILE_ENV):
        raise RuntimeError(
            f"parallel Shared Plan stale-preview acceptance requires {EXECUTORS_FILE_ENV}; "
            "strict CI/CD authority is never replaced by a non-strict shortcut"
        )
    left = repositories_root / "parallel-stale-left"
    right = repositories_root / "parallel-stale-right"
    prepare_parallel_shared_repository(left)
    prepare_parallel_shared_repository(right)
    ids = _team_ids(base_url, (left, right), access_token=access_token)
    repository_ids = [ids[left], ids[right]]
    document = _request_json(
        "PUT",
        f"{base_url}/api/league/shared-plans/{PARALLEL_PLAN_KEY}",
        access_token=access_token,
        payload={"content": PARALLEL_SHARED_PLAN, "expected_revision": None},
    )
    document_revision = _document_revision(document)
    stale_revision = "s" + uuid.uuid4().hex + "abcdef0"
    stale_inputs = {"release_channel": "beta", "revision": stale_revision}
    stale_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=stale_inputs,
        expected_revision=document_revision,
    )
    stale_instances = _repository_instance_authority(stale_preview, repository_ids)
    stale_left_instance = stale_instances[ids[left]]

    # Simulate a real checkout replacement/re-clone at the exact displayed path. Stable
    # repository_id remains the same, while repository_instance_id must change.
    shutil.rmtree(left)
    prepare_parallel_shared_repository(left)
    refreshed_instance = _team_ids(base_url, (left, right), access_token=access_token)
    if refreshed_instance[left] != ids[left]:
        raise RuntimeError("repository replacement changed stable Team repository_id")
    from oh_goon.core.product_identity import repository_instance_id

    if repository_instance_id(left) == stale_left_instance:
        raise RuntimeError("repository replacement did not change repository instance authority")

    stale_run_id = f"shared-parallel-stale-{uuid.uuid4().hex[:12]}"
    _request_expected_error(
        "POST",
        f"{base_url}/api/league/shared-plan-runs",
        access_token=access_token,
        payload=_start_payload(
            repository_ids=repository_ids,
            preview=stale_preview,
            inputs=stale_inputs,
            run_id=stale_run_id,
        ),
        expected_status=409,
        expected_code="FLEET_PLAN_SOURCE_STALE",
    )
    if _processes_for_revision(stale_revision):
        raise RuntimeError("stale Shared Plan preview launched physical repository work")
    if any(
        (repository / f"parallel-started-{stale_revision}").exists()
        for repository in (left, right)
    ):
        raise RuntimeError("stale Shared Plan preview produced repository start effects")
    _request_expected_error(
        "GET",
        f"{base_url}/api/fleet/runs/{stale_run_id}",
        access_token=access_token,
        payload=None,
        expected_status=404,
        expected_code="RUN_NOT_FOUND",
    )
    fleet = _request_json("GET", f"{base_url}/api/fleet", access_token=access_token)
    if fleet.get("active_fleet_run") is not None:
        raise RuntimeError("stale Shared Plan preview rejection left a ghost active Fleet Run")

    recovery_revision = "r" + uuid.uuid4().hex + "abcdef0"
    recovery_inputs = {"release_channel": "beta", "revision": recovery_revision}
    recovery_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=recovery_inputs,
        expected_revision=document_revision,
    )
    recovery_run_id = f"shared-parallel-stale-recovered-{uuid.uuid4().hex[:12]}"
    _start(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        preview=recovery_preview,
        inputs=recovery_inputs,
        run_id=recovery_run_id,
    )
    recovered = _wait_run(
        base_url, recovery_run_id, access_token=access_token, expected_status="COMPLETED"
    )
    if recovered.get("outcome") != "SUCCESS":
        raise RuntimeError(f"refreshed parallel Shared Plan did not succeed: {recovered!r}")
    _wait_marker(left, "finished", recovery_revision)
    _wait_marker(right, "finished", recovery_revision)
    return stale_run_id, recovery_run_id


def exercise_parallel_shared_plan_failure_repair(
    base_url: str,
    repositories_root: Path,
    *,
    access_token: str,
) -> tuple[str, str]:
    """Prove one real parallel target may fail without corrupting its running sibling, then repair/rerun."""

    if not os.environ.get(EXECUTORS_FILE_ENV):
        raise RuntimeError(
            f"parallel Shared Plan failure/retry acceptance requires {EXECUTORS_FILE_ENV}; "
            "strict CI/CD concurrency is never replaced by a non-strict test shortcut"
        )
    failing = repositories_root / "parallel-failure-left"
    sibling = repositories_root / "parallel-failure-right"
    prepare_parallel_shared_repository(failing, behavior="fail")
    prepare_parallel_shared_repository(sibling, behavior="slow-success")
    ids = _team_ids(base_url, (failing, sibling), access_token=access_token)
    repository_ids = [ids[failing], ids[sibling]]
    document = _request_json(
        "PUT",
        f"{base_url}/api/league/shared-plans/{PARALLEL_PLAN_KEY}",
        access_token=access_token,
        payload={"content": PARALLEL_SHARED_PLAN, "expected_revision": None},
    )
    if document.get("name") != PARALLEL_PLAN_KEY:
        raise RuntimeError("parallel failure Shared Plan save lost authored identity")

    failure_revision = "f" + uuid.uuid4().hex + "abcdef0"
    failure_inputs = {"release_channel": "beta", "revision": failure_revision}
    failure_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=failure_inputs,
        expected_revision=_document_revision(document),
    )
    failed_run_id = f"shared-parallel-failure-{uuid.uuid4().hex[:12]}"
    _start(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        preview=failure_preview,
        inputs=failure_inputs,
        run_id=failed_run_id,
    )
    children = _wait_parallel_children(failure_revision)
    _wait_marker(failing, "started", failure_revision)
    _wait_marker(sibling, "started", failure_revision)
    failed = _wait_run(
        base_url, failed_run_id, access_token=access_token, expected_status="FAILED"
    )
    _wait_marker(failing, "failed", failure_revision)
    _wait_marker(sibling, "finished", failure_revision)
    if (failing / f"parallel-finished-{failure_revision}").exists():
        raise RuntimeError("failing parallel target reached its forbidden completion marker")
    if not all(not _pid_alive(pid) for pid in children):
        raise RuntimeError(f"failed parallel Shared Plan left target child alive: {children!r}")
    failed_rendered = json.dumps(failed)
    if failing.name not in failed_rendered or sibling.name not in failed_rendered:
        raise RuntimeError("failed parallel Shared Plan lost per-repository attribution")
    if "intentional repository-owned failure" not in failed_rendered and "exit" not in failed_rendered.lower():
        raise RuntimeError("failed parallel Shared Plan lost deterministic failure evidence")
    game_tape = failed.get("game_tape")
    repository_facts = game_tape.get("repositories") if isinstance(game_tape, dict) else None
    if not isinstance(repository_facts, list):
        raise RuntimeError("failed parallel Shared Plan lost repository Game Tape facts")
    statuses: dict[str, str] = {}
    for fact in repository_facts:
        repository = fact.get("repository") if isinstance(fact, dict) else None
        name = repository.get("name") if isinstance(repository, dict) else None
        status = fact.get("status") if isinstance(fact, dict) else None
        if isinstance(name, str) and isinstance(status, str):
            statuses[name] = status
    if statuses.get(failing.name) != "FAILED":
        raise RuntimeError(f"failed parallel target lost deterministic FAILED attribution: {statuses!r}")
    if statuses.get(sibling.name) != "SUCCESS":
        raise RuntimeError(f"running sibling did not finish SUCCESS after peer failure: {statuses!r}")
    operations = failed.get("operations")
    if not isinstance(operations, list):
        raise RuntimeError("failed parallel Shared Plan lost successful operation evidence")
    successful_names = {
        repository.get("name")
        for row in operations
        if isinstance(row, dict)
        for repository in [row.get("repository")]
        if isinstance(repository, dict) and isinstance(repository.get("name"), str)
    }
    if sibling.name not in successful_names or failing.name in successful_names:
        raise RuntimeError(
            f"parallel failure mixed successful operation ownership across repositories: {successful_names!r}"
        )
    fleet = _request_json("GET", f"{base_url}/api/fleet", access_token=access_token)
    if fleet.get("active_fleet_run") is not None:
        raise RuntimeError("failed parallel Shared Plan left a ghost active Fleet Run")

    repair_parallel_shared_repository(failing)
    repair_revision = "r" + uuid.uuid4().hex + "abcdef0"
    repair_inputs = {"release_channel": "beta", "revision": repair_revision}
    repair_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=repair_inputs,
        expected_revision=_document_revision(document),
    )
    repaired_run_id = f"shared-parallel-repaired-{uuid.uuid4().hex[:12]}"
    _start(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        preview=repair_preview,
        inputs=repair_inputs,
        run_id=repaired_run_id,
    )
    repaired = _wait_run(
        base_url, repaired_run_id, access_token=access_token, expected_status="COMPLETED"
    )
    if repaired.get("outcome") != "SUCCESS":
        raise RuntimeError(f"repaired parallel Shared Plan did not succeed: {repaired!r}")
    _wait_marker(failing, "finished", repair_revision)
    _wait_marker(sibling, "finished", repair_revision)
    repaired_rendered = json.dumps(repaired)
    for repository in (failing, sibling):
        if repository.name not in repaired_rendered:
            raise RuntimeError(
                f"repaired parallel Shared Plan lost repository identity {repository.name!r}"
            )
    return failed_run_id, repaired_run_id


def exercise_parallel_shared_plan_cancellation(
    base_url: str,
    repositories_root: Path,
    *,
    access_token: str,
) -> tuple[str, str]:
    """Prove two real Shared-Plan targets overlap, cancel physically, then run again."""

    if not os.environ.get(EXECUTORS_FILE_ENV):
        raise RuntimeError(
            f"parallel Shared Plan real-world acceptance requires {EXECUTORS_FILE_ENV}; "
            "strict CI/CD concurrency is never replaced by a non-strict test shortcut"
        )
    left = repositories_root / "parallel-shared-left"
    right = repositories_root / "parallel-shared-right"
    prepare_parallel_shared_repository(left)
    prepare_parallel_shared_repository(right)
    ids = _team_ids(base_url, (left, right), access_token=access_token)
    repository_ids = [ids[left], ids[right]]
    document = _request_json(
        "PUT",
        f"{base_url}/api/league/shared-plans/{PARALLEL_PLAN_KEY}",
        access_token=access_token,
        payload={"content": PARALLEL_SHARED_PLAN, "expected_revision": None},
    )
    if document.get("name") != PARALLEL_PLAN_KEY:
        raise RuntimeError("parallel Shared Plan save lost authored identity")

    cancel_revision = "c" + uuid.uuid4().hex + "abcdef0"
    cancel_inputs = {"release_channel": "beta", "revision": cancel_revision}
    cancel_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=cancel_inputs,
        expected_revision=_document_revision(document),
    )
    cancelled_run_id = f"shared-parallel-cancel-{uuid.uuid4().hex[:12]}"
    _start(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        preview=cancel_preview,
        inputs=cancel_inputs,
        run_id=cancelled_run_id,
    )
    children = _wait_parallel_children(cancel_revision)
    live = _request_json(
        "GET", f"{base_url}/api/fleet/runs/{cancelled_run_id}", access_token=access_token
    )
    require_repository_bound_step_started(
        live, repository_ids, label="parallel Shared Plan with two physical children"
    )
    _request_json(
        "POST",
        f"{base_url}/api/fleet/runs/{cancelled_run_id}/cancel",
        access_token=access_token,
    )
    cancelled = _wait_run(
        base_url, cancelled_run_id, access_token=access_token, expected_status="CANCELLED"
    )
    if cancelled.get("outcome") != "CANCELLED":
        raise RuntimeError(f"parallel Shared Plan cancellation lost CANCELLED outcome: {cancelled!r}")
    _wait_children_dead(children, cancel_revision)
    fleet = _request_json("GET", f"{base_url}/api/fleet", access_token=access_token)
    if fleet.get("active_fleet_run") is not None:
        raise RuntimeError("parallel Shared Plan cancellation left a ghost active Fleet Run")

    success_revision = "a" + uuid.uuid4().hex + "abcdef0"
    success_inputs = {"release_channel": "beta", "revision": success_revision}
    success_preview = _preview(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        inputs=success_inputs,
        expected_revision=_document_revision(document),
    )
    success_run_id = f"shared-parallel-success-{uuid.uuid4().hex[:12]}"
    _start(
        base_url,
        access_token=access_token,
        repository_ids=repository_ids,
        preview=success_preview,
        inputs=success_inputs,
        run_id=success_run_id,
    )
    success = _wait_run(
        base_url, success_run_id, access_token=access_token, expected_status="COMPLETED"
    )
    if success.get("outcome") != "SUCCESS":
        raise RuntimeError(f"fresh parallel Shared Plan did not succeed after cancellation: {success!r}")
    success_rendered = json.dumps(success)
    for repository in (left, right):
        if repository.name not in success_rendered:
            raise RuntimeError(
                f"fresh parallel Shared Plan Game Tape lost repository identity {repository.name!r}"
            )
    return cancelled_run_id, success_run_id
