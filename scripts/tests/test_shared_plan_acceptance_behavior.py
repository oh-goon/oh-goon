from __future__ import annotations

import io
import json
from pathlib import Path
import urllib.error

import pytest

from oh_goon.repository_tooling.acceptance import shared_plan


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _http_error(code: int, payload: dict[str, object]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", code, "bad", {}, io.BytesIO(json.dumps(payload).encode())
    )


def test_request_json_success_http_error_and_non_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shared_plan.urllib.request, "urlopen", lambda *_a, **_k: Response({"ok": True})
    )
    assert shared_plan._request_json(
        "POST", "http://x", access_token="t", payload={"x": 1}
    ) == {"ok": True}
    error = _http_error(409, {"message": "bad"})
    monkeypatch.setattr(
        shared_plan.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )
    with pytest.raises(RuntimeError, match="HTTP 409"):
        shared_plan._request_json("GET", "http://x", access_token="t")
    monkeypatch.setattr(
        shared_plan.urllib.request, "urlopen", lambda *_a, **_k: Response([])
    )
    with pytest.raises(RuntimeError, match="expected JSON object"):
        shared_plan._request_json("GET", "http://x", access_token="t")


def test_team_ids_filter_rows_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    payload = {
        "teams": [
            "bad",
            {"repository": None},
            {"repository": {"path": str(left.resolve()), "repository_id": "l"}},
            {"repository": {"path": str(right.resolve()), "repository_id": "r"}},
        ]
    }
    monkeypatch.setattr(shared_plan, "_request_json", lambda *_a, **_k: payload)
    assert shared_plan._team_ids("x", (left, right), access_token="t") == {
        left: "l",
        right: "r",
    }
    monkeypatch.setattr(shared_plan, "_request_json", lambda *_a, **_k: {"teams": None})
    with pytest.raises(RuntimeError, match="does not contain Team rows"):
        shared_plan._team_ids("x", (left,), access_token="t")
    monkeypatch.setattr(shared_plan, "_request_json", lambda *_a, **_k: {"teams": []})
    with pytest.raises(RuntimeError, match="was not discovered"):
        shared_plan._team_ids("x", (left,), access_token="t")


def test_invalid_shared_plan_is_rejected_without_becoming_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            _http_error(
                400,
                {
                    "code": "FLEET_PLAN_INVALID",
                    "message": "Plans do not own input schemas",
                },
            ),
            _http_error(
                404, {"code": "FLEET_PLAN_SOURCE_NOT_FOUND", "message": "not found"}
            ),
        ]
    )
    monkeypatch.setattr(
        shared_plan.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(next(replies)),
    )

    shared_plan._assert_invalid_shared_plan_is_rejected_before_persistence(
        "http://x", access_token="t"
    )


def _clock(values):
    it = iter(values)
    return lambda: next(it)


def test_wait_run_terminal_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(
        [
            {"run": {"status": "RUNNING"}},
            {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
        ]
    )
    monkeypatch.setattr(shared_plan, "_request_json", lambda *_a, **_k: next(replies))
    monkeypatch.setattr(shared_plan.time, "monotonic", _clock([0.0, 0.1, 0.2]))
    monkeypatch.setattr(shared_plan.time, "sleep", lambda _x: None)
    assert (
        shared_plan._wait_run("x", "r", access_token="t", timeout_seconds=1)["outcome"]
        == "SUCCESS"
    )
    monkeypatch.setattr(
        shared_plan, "_request_json", lambda *_a, **_k: {"run": {"status": "RUNNING"}}
    )
    monkeypatch.setattr(shared_plan.time, "monotonic", _clock([0.0, 2.0]))
    with pytest.raises(RuntimeError, match="did not finish"):
        shared_plan._wait_run("x", "r", access_token="t", timeout_seconds=1)


def _preview(tmp_path: Path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    not_applicable = tmp_path / "not-applicable"
    for path in (left, right, not_applicable):
        path.mkdir()

    def executable(
        path: Path, repository_id: str, instance_id: str
    ) -> dict[str, object]:
        return {
            "state": "READY",
            "repository": {"path": str(path.resolve()), "repository_id": repository_id},
            "repository_instance_id": instance_id,
            "preview": {
                "imports": [{"applicability": "APPLICABLE", "reason": ""}],
                "steps": [{"disposition": "READY"}],
            },
        }

    return (
        left,
        right,
        not_applicable,
        {
            "ready_count": 3,
            "blocked_count": 0,
            "plan_id": "p",
            "sha256": "a" * 64,
            "execution_digest": "sha256:" + "d" * 64,
            "teams": [
                executable(left, "l", "li"),
                executable(right, "r", "ri"),
                {
                    "state": "READY",
                    "repository": {
                        "path": str(not_applicable.resolve()),
                        "repository_id": "n",
                    },
                    "repository_instance_id": "ni",
                    "preview": {
                        "imports": [
                            {
                                "applicability": "NOT_APPLICABLE",
                                "reason": "locked UV dependency group is missing: dev",
                            }
                        ],
                        "steps": [{"disposition": "SKIPPED"}],
                    },
                },
            ],
        },
    )


def test_preview_preserves_applicability_without_collapsing_it_into_team_failure(
    tmp_path: Path,
) -> None:
    left, right, not_applicable, preview = _preview(tmp_path)
    shared_plan._assert_preview(
        preview,
        ready_with_repo_goon=left,
        ready_without_repo_goon=right,
        not_applicable=not_applicable,
    )
    assert shared_plan._repository_instance_authority(preview, ["l", "r"]) == {
        "l": "li",
        "r": "ri",
    }

    collapsed = dict(preview, ready_count=2, blocked_count=1)
    with pytest.raises(RuntimeError, match="3 READY / 0 BLOCKED"):
        shared_plan._assert_preview(
            collapsed,
            ready_with_repo_goon=left,
            ready_without_repo_goon=right,
            not_applicable=not_applicable,
        )

    missing_evidence = dict(preview)
    missing_evidence["teams"] = None
    with pytest.raises(RuntimeError, match="did not return Team evidence"):
        shared_plan._assert_preview(
            missing_evidence,
            ready_with_repo_goon=left,
            ready_without_repo_goon=right,
            not_applicable=not_applicable,
        )

    wrong_applicability = dict(preview)
    rows = [dict(row) for row in preview["teams"]]
    rows[2] = dict(
        rows[2],
        preview={
            "imports": [{"applicability": "APPLICABLE", "reason": ""}],
            "steps": [{"disposition": "READY"}],
        },
    )
    wrong_applicability["teams"] = rows
    with pytest.raises(RuntimeError, match="NOT_APPLICABLE/SKIPPED"):
        shared_plan._assert_preview(
            wrong_applicability,
            ready_with_repo_goon=left,
            ready_without_repo_goon=right,
            not_applicable=not_applicable,
        )

    with pytest.raises(RuntimeError, match="lost exact repository instance authority"):
        shared_plan._repository_instance_authority({"teams": []}, ["l"])


def test_run_ready_shared_plan_success_and_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _left, _right, _not_applicable, preview = _preview(tmp_path)
    monkeypatch.setattr(
        shared_plan,
        "_repository_instance_authority",
        lambda *_a: {"l": "li", "r": "ri"},
    )
    captured = {}

    def request(_method, _url, **kwargs):
        captured.update(kwargs.get("payload") or {})
        return {"fleet_run_id": captured.get("fleet_run_id")}

    monkeypatch.setattr(shared_plan, "_request_json", request)
    monkeypatch.setattr(
        shared_plan,
        "_wait_run",
        lambda *_a, **_k: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": {
                "repositories": [
                    {
                        "repository": {"repository_id": "l"},
                        "status": "PASSED",
                        "steps": [{"status": "PASSED"}],
                    },
                    {
                        "repository": {"repository_id": "r"},
                        "status": "PASSED",
                        "steps": [{"status": "PASSED"}],
                    },
                ]
            },
        },
    )
    run_id = shared_plan._run_ready_shared_plan(
        "x", access_token="t", preview=preview, repository_ids=["l", "r"], inputs={}
    )
    assert run_id.startswith("shared-acceptance-")
    monkeypatch.setattr(
        shared_plan, "_request_json", lambda *_a, **_k: {"fleet_run_id": "wrong"}
    )
    with pytest.raises(RuntimeError, match="preserve requested Fleet Run identity"):
        shared_plan._run_ready_shared_plan(
            "x", access_token="t", preview=preview, repository_ids=["l", "r"], inputs={}
        )
    monkeypatch.setattr(shared_plan, "_request_json", request)
    monkeypatch.setattr(
        shared_plan,
        "_wait_run",
        lambda *_a, **_k: {"run": {"status": "FAILED"}, "outcome": "FAIL"},
    )
    with pytest.raises(RuntimeError, match="acceptance failed"):
        shared_plan._run_ready_shared_plan(
            "x", access_token="t", preview=preview, repository_ids=["l", "r"], inputs={}
        )
    monkeypatch.setattr(
        shared_plan,
        "_wait_run",
        lambda *_a, **_k: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": {
                "repositories": [
                    {
                        "repository": {"repository_id": "l"},
                        "status": "PASSED",
                        "steps": [{"status": "PASSED"}],
                    }
                ]
            },
        },
    )
    with pytest.raises(RuntimeError, match="exact selected Team execution"):
        shared_plan._run_ready_shared_plan(
            "x", access_token="t", preview=preview, repository_ids=["l", "r"], inputs={}
        )
    monkeypatch.setattr(
        shared_plan,
        "_wait_run",
        lambda *_a, **_k: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": {
                "repositories": [
                    {
                        "repository": {"repository_id": "l"},
                        "status": "PASSED",
                        "steps": [
                            {
                                "status": "PASSED",
                                "reason": "repository-goon-must-not-run",
                            }
                        ],
                    },
                    {
                        "repository": {"repository_id": "r"},
                        "status": "PASSED",
                        "steps": [{"status": "PASSED"}],
                    },
                ]
            },
        },
    )
    with pytest.raises(RuntimeError, match="merged repository-owned"):
        shared_plan._run_ready_shared_plan(
            "x", access_token="t", preview=preview, repository_ids=["l", "r"], inputs={}
        )


def test_exercise_shared_plan_runs_after_preview_without_parent_executor_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    repos = [
        tmp_path / "shared-ready-owned-goon",
        tmp_path / "shared-ready-no-goon",
        tmp_path / "shared-not-applicable-missing-dev-group",
    ]
    monkeypatch.setattr(
        shared_plan,
        "_team_ids",
        lambda *_a, **_k: {repos[0]: "l", repos[1]: "r", repos[2]: "b"},
    )
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if "shared-plans/acceptance-shared" in url:
            return {"plan_key": "acceptance-shared", "sha256": "a" * 64}
        if "shared-plan-previews" in url:
            return {
                "ready_count": 3,
                "blocked_count": 0,
                "plan_id": "p",
                "sha256": "a" * 64,
                "execution_digest": "sha256:" + "d" * 64,
                "teams": [],
            }
        return {}

    monkeypatch.setattr(shared_plan, "_request_json", request)
    monkeypatch.setattr(
        shared_plan,
        "_assert_invalid_shared_plan_is_rejected_before_persistence",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(shared_plan, "_assert_preview", lambda *_a, **_k: None)
    monkeypatch.setattr(shared_plan, "_run_ready_shared_plan", lambda *_a, **_k: "run")
    assert shared_plan.exercise_shared_plan("x", tmp_path, access_token="t") == "run"
    assert all(path.exists() for path in repos)
