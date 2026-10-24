from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from oh_goon.repository_tooling.acceptance.shared_plan_evidence import (
    require_preview_counts,
    require_preview_work,
    require_successful_repository_execution,
)
from oh_goon.repository_tooling.acceptance.uv_fixture import (
    editable_console_build_backend,
    editable_uv_lock,
)
from oh_goon.repository_tooling.local_access_http import authorized_request

SHARED_PLAN = """version: 1
inputs:
- release_channel
- revision
goons:
- python/release-metadata
"""

INVALID_SHARED_PLAN = """version: 1
inputs:
  revision:
    type: git_revision
goons:
- python/release-metadata
"""

REPOSITORY_GOON_THAT_MUST_NOT_RUN = """version: 1
steps:
- run: python must_not_run.py
"""

_BASIC_RELEASE_METADATA_RUNTIME = r"""from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    output.joinpath("PROJECT_VERSION").write_text("{version}\n", encoding="utf-8")
    output.joinpath("RELEASE_VERSION").write_text(
        f"{version}-{{args.channel}}.{{args.revision[:7]}}\n", encoding="utf-8"
    )
"""


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


def _repository(
    path: Path,
    *,
    version: str,
    include_dev_group: bool = True,
    repository_goon: str | None = None,
) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    project = [
        "[project]",
        'name = "oh-goon-shared-acceptance"',
        f'version = "{version}"',
        'requires-python = ">=3.11"',
        "",
        "[project.scripts]",
        'release-metadata = "acceptance_release_metadata:main"',
    ]
    if include_dev_group:
        project.extend(["", "[dependency-groups]", "dev = []"])
    project.extend(
        [
            "",
            "[build-system]",
            "requires = []",
            'build-backend = "acceptance_build"',
            'backend-path = ["."]',
        ]
    )
    (path / "pyproject.toml").write_text("\n".join(project) + "\n", encoding="utf-8")
    (path / "uv.lock").write_text(
        editable_uv_lock("oh-goon-shared-acceptance", version, include_dev_group=include_dev_group),
        encoding="utf-8",
    )
    (path / "acceptance_release_metadata.py").write_text(
        _BASIC_RELEASE_METADATA_RUNTIME.format(version=version), encoding="utf-8"
    )
    (path / "acceptance_build.py").write_text(
        editable_console_build_backend(
            distribution_name="oh-goon-shared-acceptance",
            module_name="acceptance_release_metadata",
            command_name="release-metadata",
            version=version,
        ),
        encoding="utf-8",
    )
    if repository_goon is not None:
        (path / "goon.yml").write_text(repository_goon, encoding="utf-8")
        (path / "must_not_run.py").write_text("raise SystemExit(99)\n", encoding="utf-8")


def _team_ids(
    base_url: str, repositories: tuple[Path, ...], *, access_token: str
) -> dict[Path, str]:
    league = _request_json("GET", f"{base_url}/api/league", access_token=access_token)
    teams = league.get("teams")
    if not isinstance(teams, list):
        raise RuntimeError("League response does not contain Team rows")
    by_path: dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        repository = team.get("repository")
        if (
            isinstance(repository, dict)
            and isinstance(repository.get("path"), str)
            and isinstance(repository.get("repository_id"), str)
        ):
            by_path[repository["path"]] = repository["repository_id"]
    result: dict[Path, str] = {}
    for repository in repositories:
        key = str(repository.resolve())
        if key not in by_path:
            raise RuntimeError(f"Shared Plan acceptance Team was not discovered: {key}")
        result[repository] = by_path[key]
    return result


def _assert_invalid_shared_plan_is_rejected_before_persistence(
    base_url: str,
    *,
    access_token: str,
) -> None:
    plan_url = f"{base_url}/api/league/shared-plans/acceptance-invalid-shared"
    request = authorized_request(
        plan_url,
        access_token=access_token,
        data=json.dumps({"content": INVALID_SHARED_PLAN, "expected_revision": None}).encode(
            "utf-8"
        ),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10.0)
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        if exc.code != 400 or payload.get("code") != "FLEET_PLAN_INVALID":
            raise RuntimeError(
                f"invalid Shared Plan save did not return FLEET_PLAN_INVALID/400: {exc.code} {payload!r}"
            ) from exc
        if "Plans do not own input schemas" not in str(payload.get("message")):
            raise RuntimeError(
                f"invalid Shared Plan save lost domain explanation: {payload!r}"
            ) from exc
    else:
        raise RuntimeError("invalid Shared Plan save unexpectedly succeeded")

    read_request = authorized_request(plan_url, access_token=access_token)
    try:
        urllib.request.urlopen(read_request, timeout=10.0)
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        if exc.code != 404 or payload.get("code") != "FLEET_PLAN_SOURCE_NOT_FOUND":
            raise RuntimeError(
                "invalid Shared Plan rejection did not leave storage absent: "
                f"{exc.code} {payload!r}"
            ) from exc
        return
    raise RuntimeError("invalid Shared Plan became readable after rejected save")


def _wait_run(
    base_url: str,
    run_id: str,
    *,
    access_token: str,
    timeout_seconds: float = 90.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        last = _request_json(
            "GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token
        )
        run = last.get("run")
        status = run.get("status") if isinstance(run, dict) else None
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Shared Plan acceptance run did not finish; last={last!r}")


def _assert_preview(
    preview: dict[str, object],
    *,
    ready_with_repo_goon: Path,
    ready_without_repo_goon: Path,
    not_applicable: Path,
) -> None:
    teams = require_preview_counts(preview, ready=3, blocked=0, label="Shared Plan preview")
    by_path: dict[str, dict[str, object]] = {}
    for team in teams:
        selected_repository = team.get("repository")
        if isinstance(selected_repository, dict):
            repository_path = selected_repository.get("path")
            if isinstance(repository_path, str):
                by_path[repository_path] = team
    for repository in (ready_with_repo_goon, ready_without_repo_goon, not_applicable):
        selected_team = by_path.get(str(repository.resolve()))
        if not isinstance(selected_team, dict) or selected_team.get("state") != "READY":
            raise RuntimeError(
                f"Shared Plan did not keep {repository.name} as a valid Team preview"
            )

    for repository in (ready_with_repo_goon, ready_without_repo_goon):
        require_preview_work(
            by_path[str(repository.resolve())],
            applicability="APPLICABLE",
            disposition="READY",
            label=f"Shared Plan {repository.name}",
        )
    require_preview_work(
        by_path[str(not_applicable.resolve())],
        applicability="NOT_APPLICABLE",
        disposition="SKIPPED",
        reason_contains="dependency group is missing: dev",
        label=f"Shared Plan {not_applicable.name}",
    )


def _repository_instance_authority(
    preview: dict[str, object], repository_ids: list[str]
) -> dict[str, str]:
    teams = preview.get("teams")
    if not isinstance(teams, list):
        raise RuntimeError("Shared Plan preview did not return Team authority")
    expected = set(repository_ids)
    authority: dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict) or team.get("state") != "READY":
            continue
        repository = team.get("repository")
        repository_id = repository.get("repository_id") if isinstance(repository, dict) else None
        instance_id = team.get("repository_instance_id")
        if repository_id in expected and isinstance(instance_id, str):
            authority[repository_id] = instance_id
    if set(authority) != expected:
        raise RuntimeError("Shared Plan preview lost exact repository instance authority")
    return authority


def _run_ready_shared_plan(
    base_url: str,
    *,
    access_token: str,
    preview: dict[str, object],
    repository_ids: list[str],
    inputs: dict[str, str],
) -> str:
    run_id = f"shared-acceptance-{uuid.uuid4().hex[:12]}"
    response = _request_json(
        "POST",
        f"{base_url}/api/league/shared-plan-runs",
        access_token=access_token,
        payload={
            "plan_key": "acceptance-shared",
            "expected_plan_id": preview.get("plan_id"),
            "expected_execution_digest": preview.get("execution_digest"),
            "expected_plan_revision": preview.get("sha256"),
            "repository_ids": repository_ids,
            "repository_instance_ids": _repository_instance_authority(preview, repository_ids),
            "fleet_run_id": run_id,
            "inputs": inputs,
        },
    )
    if response.get("fleet_run_id") != run_id:
        raise RuntimeError("Shared Plan run did not preserve requested Fleet Run identity")
    detail = _wait_run(base_url, run_id, access_token=access_token)
    run = detail.get("run")
    status = run.get("status") if isinstance(run, dict) else None
    if status != "COMPLETED" or detail.get("outcome") != "SUCCESS":
        failure = run.get("failure") if isinstance(run, dict) else None
        game_tape = detail.get("game_tape")
        issue_focus = game_tape.get("issue_focus") if isinstance(game_tape, dict) else None
        repositories = game_tape.get("repositories") if isinstance(game_tape, dict) else None
        raise RuntimeError(
            "Shared Plan acceptance failed: "
            f"status={status!r} outcome={detail.get('outcome')!r} "
            f"failure={failure!r} issue_focus={issue_focus!r} "
            f"repositories={repositories!r}"
        )
    require_successful_repository_execution(detail, repository_ids, label="Shared Plan Game Tape")
    rendered = json.dumps(detail)
    if "repository-goon-must-not-run" in rendered:
        raise RuntimeError("Shared Plan execution merged repository-owned goon.yml work")
    return run_id


def exercise_shared_plan(
    base_url: str, repositories_root: Path, *, access_token: str
) -> str | None:
    """Exercise Shared Plan preview/run against heterogeneous real repositories."""

    ready_with_repo_goon = repositories_root / "shared-ready-owned-goon"
    ready_without_repo_goon = repositories_root / "shared-ready-no-goon"
    not_applicable = repositories_root / "shared-not-applicable-missing-dev-group"
    _repository(
        ready_with_repo_goon,
        version="1.2.3",
        repository_goon=REPOSITORY_GOON_THAT_MUST_NOT_RUN,
    )
    _repository(ready_without_repo_goon, version="2.0.0")
    _repository(not_applicable, version="3.0.0", include_dev_group=False)
    team_ids = _team_ids(
        base_url,
        (ready_with_repo_goon, ready_without_repo_goon, not_applicable),
        access_token=access_token,
    )
    document = _request_json(
        "PUT",
        f"{base_url}/api/league/shared-plans/acceptance-shared",
        access_token=access_token,
        payload={"content": SHARED_PLAN, "expected_revision": None},
    )
    if document.get("plan_key") != "acceptance-shared":
        raise RuntimeError("Shared Plan save did not return the authored plan identity")
    expected_revision = document.get("sha256")
    if not isinstance(expected_revision, str):
        raise RuntimeError("Shared Plan save did not return source revision authority")
    inputs = {"release_channel": "beta", "revision": "abcdef1"}
    selected_ids = list(team_ids.values())
    _assert_invalid_shared_plan_is_rejected_before_persistence(base_url, access_token=access_token)
    preview = _request_json(
        "POST",
        f"{base_url}/api/league/shared-plan-previews",
        access_token=access_token,
        payload={
            "plan_key": "acceptance-shared",
            "expected_revision": expected_revision,
            "repository_ids": selected_ids,
            "inputs": inputs,
        },
    )
    _assert_preview(
        preview,
        ready_with_repo_goon=ready_with_repo_goon,
        ready_without_repo_goon=ready_without_repo_goon,
        not_applicable=not_applicable,
    )
    return _run_ready_shared_plan(
        base_url,
        access_token=access_token,
        preview=preview,
        repository_ids=[
            team_ids[ready_with_repo_goon],
            team_ids[ready_without_repo_goon],
        ],
        inputs=inputs,
    )
