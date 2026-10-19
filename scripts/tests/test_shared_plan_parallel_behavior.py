from __future__ import annotations

from email.message import Message

import os
import time
import urllib.request

import io
import json
import urllib.error
from pathlib import Path

import pytest

from oh_goon.repository_tooling.acceptance import shared_plan_parallel as parallel


def _error(code: int, body: dict[str, object]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://example.test",
        code,
        "error",
        hdrs=Message(),
        fp=io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def test_parallel_runtime_accepts_only_declared_behaviors(tmp_path: Path) -> None:
    rendered = parallel._parallel_runtime(tmp_path, "normal")
    assert "BEHAVIOR = 'normal'" in rendered
    assert str(tmp_path.resolve()) in rendered
    with pytest.raises(ValueError, match="unsupported parallel acceptance behavior"):
        parallel._parallel_runtime(tmp_path, "surprise")


def test_team_ids_extracts_only_valid_repository_rows_and_requires_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    payload = {
        "teams": [
            "ignored",
            {"repository": None},
            {"repository": {"path": str(left.resolve()), "repository_id": "left-id"}},
            {"repository": {"path": str(right.resolve()), "repository_id": "right-id"}},
        ]
    }
    monkeypatch.setattr(parallel, "_request_json", lambda *args, **kwargs: payload)
    assert parallel._team_ids(
        "http://frontend", (left, right), access_token="token"
    ) == {
        left: "left-id",
        right: "right-id",
    }

    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: {"teams": None}
    )
    with pytest.raises(RuntimeError, match="did not receive League Teams"):
        parallel._team_ids("http://frontend", (left, right), access_token="token")

    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: {"teams": []}
    )
    with pytest.raises(RuntimeError, match="Team was not discovered"):
        parallel._team_ids("http://frontend", (left, right), access_token="token")


def test_document_revision_requires_exact_sha256_shape() -> None:
    revision = "a" * 64
    assert parallel._document_revision({"sha256": revision}) == revision
    for value in (None, 123, "short"):
        with pytest.raises(RuntimeError, match="lost canonical revision authority"):
            parallel._document_revision({"sha256": value})


def test_wait_parallel_children_and_dead_cover_success_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    monkeypatch.setattr(parallel, "_processes_for_revision", lambda revision: {20, 10})
    monkeypatch.setattr(parallel, "_pid_alive", lambda pid: True)
    ticks = iter([0.0, 0.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.1))
    assert parallel._wait_parallel_children("rev", timeout_seconds=1.0) == (10, 20)

    monkeypatch.setattr(parallel, "_processes_for_revision", lambda revision: {10})
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 2.0))
    with pytest.raises(RuntimeError, match="never had two physical"):
        parallel._wait_parallel_children("rev", timeout_seconds=1.0)

    monkeypatch.setattr(parallel, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(parallel, "_processes_for_revision", lambda revision: set())
    ticks = iter([0.0, 0.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.1))
    parallel._wait_children_dead((10, 20), "rev", timeout_seconds=1.0)

    monkeypatch.setattr(parallel, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(parallel, "_processes_for_revision", lambda revision: {10})
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 2.0))
    with pytest.raises(RuntimeError, match="left physical children alive"):
        parallel._wait_children_dead((10, 20), "rev", timeout_seconds=1.0)


def test_pid_alive_falls_back_to_os_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parallel, "observe_process_liveness", lambda pid: True)
    assert parallel._pid_alive(1) is True
    monkeypatch.setattr(parallel, "observe_process_liveness", lambda pid: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    assert parallel._pid_alive(1) is True
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert parallel._pid_alive(1) is False
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(PermissionError()),
    )
    assert parallel._pid_alive(1) is True


def test_wait_run_handles_progress_wrong_terminal_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    replies = iter(
        [
            {"run": {"status": "RUNNING"}},
            {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
        ]
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: next(replies)
    )
    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.2))
    observed = parallel._wait_run(
        "http://frontend",
        "run",
        access_token="token",
        expected_status="COMPLETED",
        timeout_seconds=1,
    )
    assert observed["outcome"] == "SUCCESS"

    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: {"run": {"status": "FAILED"}}
    )
    ticks = iter([0.0, 0.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.1))
    with pytest.raises(RuntimeError, match="reached 'FAILED'"):
        parallel._wait_run(
            "http://frontend",
            "run",
            access_token="token",
            expected_status="COMPLETED",
            timeout_seconds=1,
        )

    monkeypatch.setattr(
        parallel,
        "_request_json",
        lambda *args, **kwargs: {"run": {"status": "RUNNING"}},
    )
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 2.0))
    with pytest.raises(RuntimeError, match="did not reach"):
        parallel._wait_run(
            "http://frontend",
            "run",
            access_token="token",
            expected_status="COMPLETED",
            timeout_seconds=1,
        )


def test_preview_and_repository_instance_authority_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = {
        "ready_count": 2,
        "blocked_count": 0,
        "teams": [
            {
                "repository": {"repository_id": "left"},
                "repository_instance_id": "left-instance",
                "preview": {
                    "imports": [{"applicability": "APPLICABLE"}],
                    "steps": [{"disposition": "READY"}],
                },
            },
            {
                "repository": {"repository_id": "right"},
                "repository_instance_id": "right-instance",
                "preview": {
                    "imports": [{"applicability": "APPLICABLE"}],
                    "steps": [{"disposition": "READY"}],
                },
            },
        ],
    }
    monkeypatch.setattr(parallel, "_request_json", lambda *args, **kwargs: preview)
    observed = parallel._preview(
        "http://frontend",
        access_token="token",
        repository_ids=["left", "right"],
        inputs={"revision": "r"},
        expected_revision="a" * 64,
    )
    assert observed is preview
    assert parallel._repository_instance_authority(preview, ["left", "right"]) == {
        "left": "left-instance",
        "right": "right-instance",
    }

    monkeypatch.setattr(
        parallel,
        "_request_json",
        lambda *args, **kwargs: {"ready_count": 1, "blocked_count": 1},
    )
    with pytest.raises(
        RuntimeError, match="did not preserve 2 READY / 0 BLOCKED Team semantics"
    ):
        parallel._preview(
            "http://frontend",
            access_token="token",
            repository_ids=["left", "right"],
            inputs={},
            expected_revision="a" * 64,
        )

    with pytest.raises(RuntimeError, match="lost Team authority"):
        parallel._repository_instance_authority({"teams": None}, ["left"])
    with pytest.raises(RuntimeError, match="does not match selected Teams"):
        parallel._repository_instance_authority(
            {
                "teams": [
                    {
                        "repository": {"repository_id": "left"},
                        "repository_instance_id": "i",
                    }
                ]
            },
            ["left", "right"],
        )


def test_expected_error_and_start_validate_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_error(request: urllib.request.Request, timeout: float) -> None:
        raise _error(409, {"code": "FLEET_PLAN_SOURCE_STALE"})

    monkeypatch.setattr(urllib.request, "urlopen", open_error)
    assert (
        parallel._request_expected_error(
            "POST",
            "http://frontend/run",
            access_token="token",
            payload={"x": 1},
            expected_status=409,
            expected_code="FLEET_PLAN_SOURCE_STALE",
        )["code"]
        == "FLEET_PLAN_SOURCE_STALE"
    )

    with pytest.raises(RuntimeError, match="expected HTTP 404"):
        parallel._request_expected_error(
            "GET",
            "http://frontend/run",
            access_token="token",
            payload=None,
            expected_status=404,
            expected_code="RUN_NOT_FOUND",
        )

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: object(),
    )
    with pytest.raises(RuntimeError, match="request succeeded"):
        parallel._request_expected_error(
            "GET",
            "http://frontend/run",
            access_token="token",
            payload=None,
            expected_status=404,
            expected_code="RUN_NOT_FOUND",
        )

    monkeypatch.setattr(
        parallel,
        "_repository_instance_authority",
        lambda preview, ids: {ids[0]: "instance"},
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: {"fleet_run_id": "run-1"}
    )
    parallel._start(
        "http://frontend",
        access_token="token",
        repository_ids=["repo-1"],
        preview={"plan_id": "plan", "sha256": "a" * 64},
        inputs={},
        run_id="run-1",
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: {"fleet_run_id": "other"}
    )
    with pytest.raises(
        RuntimeError, match="did not preserve requested Fleet Run identity"
    ):
        parallel._start(
            "http://frontend",
            access_token="token",
            repository_ids=["repo-1"],
            preview={"plan_id": "plan", "sha256": "a" * 64},
            inputs={},
            run_id="run-1",
        )


def test_wait_marker_observes_real_marker_and_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "parallel-started-rev"
    marker.write_text("1\n", encoding="utf-8")
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    assert (
        parallel._wait_marker(tmp_path, "started", "rev", timeout_seconds=1) == marker
    )

    marker.unlink()
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    with pytest.raises(RuntimeError, match="did not produce started marker"):
        parallel._wait_marker(tmp_path, "started", "rev", timeout_seconds=1)


def test_parallel_cancellation_full_success_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/tmp/executors.yml")
    left = tmp_path / "parallel-shared-left"
    right = tmp_path / "parallel-shared-right"
    monkeypatch.setattr(
        parallel,
        "prepare_parallel_shared_repository",
        lambda path, **kwargs: path.mkdir(parents=True),
    )
    monkeypatch.setattr(
        parallel,
        "_team_ids",
        lambda base, repos, access_token: {left: "left", right: "right"},
    )
    request_replies = iter(
        [
            {"name": parallel.PARALLEL_PLAN_KEY, "sha256": "a" * 64},
            {
                "game_tape": {
                    "play_by_play": [
                        {"event_type": "STEP_STARTED", "repository_id": "left"},
                        {"event_type": "STEP_STARTED", "repository_id": "right"},
                    ]
                }
            },
            {},
            {"active_fleet_run": None},
        ]
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: next(request_replies)
    )
    previews = iter(
        [
            {"plan_id": "p1", "sha256": "1" * 64},
            {"plan_id": "p2", "sha256": "2" * 64},
        ]
    )
    monkeypatch.setattr(parallel, "_preview", lambda *args, **kwargs: next(previews))
    monkeypatch.setattr(parallel, "_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(parallel, "_wait_parallel_children", lambda revision: (11, 12))
    waits = iter(
        [
            {"outcome": "CANCELLED"},
            {"outcome": "SUCCESS", "repositories": [left.name, right.name]},
        ]
    )
    monkeypatch.setattr(parallel, "_wait_run", lambda *args, **kwargs: next(waits))
    monkeypatch.setattr(parallel, "_wait_children_dead", lambda *args, **kwargs: None)

    cancelled, success = parallel.exercise_parallel_shared_plan_cancellation(
        "http://frontend", tmp_path, access_token="token"
    )
    assert cancelled.startswith("shared-parallel-cancel-")
    assert success.startswith("shared-parallel-success-")


def test_parallel_failure_repair_full_success_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/tmp/executors.yml")
    failing = tmp_path / "parallel-failure-left"
    sibling = tmp_path / "parallel-failure-right"
    monkeypatch.setattr(
        parallel,
        "prepare_parallel_shared_repository",
        lambda path, **kwargs: path.mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr(
        parallel, "repair_parallel_shared_repository", lambda path: None
    )
    monkeypatch.setattr(
        parallel,
        "_team_ids",
        lambda base, repos, access_token: {failing: "left", sibling: "right"},
    )
    requests = iter(
        [
            {"name": parallel.PARALLEL_PLAN_KEY, "sha256": "a" * 64},
            {"active_fleet_run": None},
        ]
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: next(requests)
    )
    monkeypatch.setattr(
        parallel,
        "_preview",
        lambda *args, **kwargs: {"plan_id": "p", "sha256": "b" * 64},
    )
    monkeypatch.setattr(parallel, "_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(parallel, "_wait_parallel_children", lambda revision: (11, 12))
    monkeypatch.setattr(
        parallel, "_wait_marker", lambda *args, **kwargs: Path("marker")
    )
    monkeypatch.setattr(parallel, "_pid_alive", lambda pid: False)
    failed = {
        "message": f"intentional repository-owned failure {failing.name} {sibling.name}",
        "game_tape": {
            "repositories": [
                {"repository": {"name": failing.name}, "status": "FAILED"},
                {"repository": {"name": sibling.name}, "status": "SUCCESS"},
            ]
        },
        "operations": [{"repository": {"name": sibling.name}}],
    }
    repaired = {"outcome": "SUCCESS", "repositories": [failing.name, sibling.name]}
    waits = iter([failed, repaired])
    monkeypatch.setattr(parallel, "_wait_run", lambda *args, **kwargs: next(waits))

    failed_id, repaired_id = parallel.exercise_parallel_shared_plan_failure_repair(
        "http://frontend", tmp_path, access_token="token"
    )
    assert failed_id.startswith("shared-parallel-failure-")
    assert repaired_id.startswith("shared-parallel-repaired-")


def test_parallel_stale_preview_full_recovery_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/tmp/executors.yml")
    left = tmp_path / "parallel-stale-left"
    right = tmp_path / "parallel-stale-right"
    monkeypatch.setattr(
        parallel,
        "_team_ids",
        lambda base, repos, access_token: {left: "left", right: "right"},
    )
    requests = iter(
        [
            {"name": parallel.PARALLEL_PLAN_KEY, "sha256": "a" * 64},
            {"active_fleet_run": None},
        ]
    )
    monkeypatch.setattr(
        parallel, "_request_json", lambda *args, **kwargs: next(requests)
    )
    stale_preview = {
        "plan_id": "p1",
        "sha256": "b" * 64,
        "teams": [
            {
                "repository": {"repository_id": "left"},
                "repository_instance_id": "definitely-old",
            },
            {
                "repository": {"repository_id": "right"},
                "repository_instance_id": "right-old",
            },
        ],
    }
    recovery_preview = {"plan_id": "p2", "sha256": "c" * 64}
    previews = iter([stale_preview, recovery_preview])
    monkeypatch.setattr(parallel, "_preview", lambda *args, **kwargs: next(previews))
    monkeypatch.setattr(
        parallel,
        "_request_expected_error",
        lambda *args, **kwargs: {"code": kwargs["expected_code"]},
    )
    monkeypatch.setattr(parallel, "_processes_for_revision", lambda revision: set())
    monkeypatch.setattr(parallel, "_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        parallel, "_wait_run", lambda *args, **kwargs: {"outcome": "SUCCESS"}
    )
    monkeypatch.setattr(
        parallel, "_wait_marker", lambda *args, **kwargs: Path("marker")
    )

    stale_id, recovered_id = (
        parallel.exercise_parallel_shared_plan_stale_preview_recovery(
            "http://frontend", tmp_path, access_token="token"
        )
    )
    assert stale_id.startswith("shared-parallel-stale-")
    assert recovered_id.startswith("shared-parallel-stale-recovered-")


def test_parallel_real_world_functions_refuse_missing_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    for function in (
        parallel.exercise_parallel_shared_plan_cancellation,
        parallel.exercise_parallel_shared_plan_failure_repair,
        parallel.exercise_parallel_shared_plan_stale_preview_recovery,
    ):
        with pytest.raises(RuntimeError, match="requires OH_GOON_EXECUTORS_FILE"):
            function("http://frontend", tmp_path, access_token="token")
