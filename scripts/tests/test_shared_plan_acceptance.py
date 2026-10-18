from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import urllib.error
from io import BytesIO

from oh_goon.api.schemas.league import SharedPlanPreviewRequest, SharedPlanRunRequest
from oh_goon.repository_tooling.acceptance import shared_plan

PREVIEW_PAYLOAD = {
    "plan_id": "plan-reviewed",
    "sha256": "a" * 64,
    "execution_digest": "sha256:" + "d" * 64,
    "ready_count": 2,
    "blocked_count": 0,
    "teams": [
        {
            "state": "READY",
            "repository": {"repository_id": "repo-left", "path": "/left"},
            "repository_instance_id": "instance-left",
        },
        {
            "state": "READY",
            "repository": {"repository_id": "repo-right", "path": "/right"},
            "repository_instance_id": "instance-right",
        },
    ],
}


def test_baseline_shared_plan_preview_to_run_carries_exact_authority(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    preview_payload = PREVIEW_PAYLOAD


    def request(method, url, *, access_token, payload=None):
        calls.append((method, url, payload))
        if url.endswith("/shared-plan-previews"):
            return preview_payload
        if "/shared-plans/acceptance-shared" in url:
            return {"plan_key": "acceptance-shared", "sha256": "b" * 64}
        if url.endswith("/shared-plan-runs") and payload is not None:
            return {"fleet_run_id": payload["fleet_run_id"]}
        return {}

    monkeypatch.setattr(shared_plan, "_request_json", request)
    monkeypatch.setattr(
        shared_plan,
        "_wait_run",
        lambda *args, **kwargs: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": {
                "repositories": [
                    {"repository": {"repository_id": "repo-left"}, "status": "PASSED", "steps": [{"status": "PASSED"}]},
                    {"repository": {"repository_id": "repo-right"}, "status": "PASSED", "steps": [{"status": "PASSED"}]},
                ]
            },
        },
    )
    document = request(
        "PUT",
        "http://local/api/league/shared-plans/acceptance-shared",
        access_token="token",
        payload={"content": shared_plan.SHARED_PLAN, "expected_revision": None},
    )
    preview = request(
        "POST",
        "http://local/api/league/shared-plan-previews",
        access_token="token",
        payload={
            "plan_key": "acceptance-shared",
            "expected_revision": document["sha256"],
            "repository_ids": ["repo-left", "repo-right"],
            "inputs": {"release_channel": "beta", "revision": "abcdef1"},
        },
    )
    shared_plan._run_ready_shared_plan(
        "http://local",
        access_token="token",
        preview=preview,
        repository_ids=["repo-left", "repo-right"],
        inputs={"release_channel": "beta", "revision": "abcdef1"},
    )

    preview_request = calls[1][2]
    run_request = calls[2][2]
    assert preview_request is not None
    SharedPlanPreviewRequest.model_validate(preview_request)
    assert run_request is not None
    SharedPlanRunRequest.model_validate(run_request)
    assert run_request["expected_plan_revision"] == "a" * 64
    assert run_request["repository_instance_ids"] == {
        "repo-left": "instance-left",
        "repo-right": "instance-right",
    }


def test_invalid_shared_plan_is_rejected_before_it_becomes_saved_state(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        if request.get_method() == "PUT":
            body = {
                "code": "FLEET_PLAN_INVALID",
                "message": "Plans do not own input schemas",
            }
            raise urllib.error.HTTPError(
                request.full_url, 400, "bad request", hdrs=None,
                fp=BytesIO(json.dumps(body).encode()),
            )
        body = {"code": "FLEET_PLAN_SOURCE_NOT_FOUND", "message": "not found"}
        raise urllib.error.HTTPError(
            request.full_url, 404, "not found", hdrs=None,
            fp=BytesIO(json.dumps(body).encode()),
        )

    monkeypatch.setattr(shared_plan.urllib.request, "urlopen", urlopen)

    shared_plan._assert_invalid_shared_plan_is_rejected_before_persistence(
        "http://local", access_token="token"
    )

    assert [method for method, _url in calls] == ["PUT", "GET"]


def test_ready_shared_plan_repository_can_run_release_metadata_offline(tmp_path: Path) -> None:
    repository = tmp_path / "ready"
    shared_plan._repository(repository, version="1.2.3")
    environment = dict(os.environ)
    environment["TERM"] = "dumb"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--offline",
            "release-metadata",
            "--channel",
            "beta",
            "--revision",
            "abcdef1",
            "--output-dir",
            ".fleet/release",
        ],
        cwd=repository,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert (repository / ".fleet/release/PROJECT_VERSION").read_text() == "1.2.3\n"
    assert (repository / ".fleet/release/RELEASE_VERSION").read_text() == "1.2.3-beta.abcdef1\n"
