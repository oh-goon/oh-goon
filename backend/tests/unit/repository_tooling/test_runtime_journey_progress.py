from __future__ import annotations

from pathlib import Path

import pytest

import oh_goon.repository_tooling.release.runtime_journey as runtime_journey


def test_interruptible_start_timeout_uses_canonical_lifecycle_not_transient_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def request_json(method: str, url: str, **_kwargs):
        assert method == "GET"
        calls.append(url)
        if url.endswith("/events?after=0"):
            return {"events": [{"event_type": "RUN_STARTED"}]}
        if url.endswith("/live-output?after=0"):
            raise AssertionError("transient activity must not be lifecycle authority")
        return {
            "run": {"status": "RUNNING"},
            "outcome": "IN_PROGRESS",
            "game_tape": {"repositories": [], "issue_focus": []},
            "failure": None,
        }

    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(runtime_journey, "_request_json", request_json)
    monkeypatch.setattr(runtime_journey.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runtime_journey.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError) as exc_info:
        runtime_journey.wait_for_interruptible_step(
            base_url="http://127.0.0.1:8000",
            run_id="run-1",
            repository=tmp_path,
            access_token="token",
            timeout_seconds=1.0,
        )

    message = str(exc_info.value)
    assert "latest_transition='RUN_STARTED'" in message
    assert "canonical lifecycle predecessor/owner before PROCESS_STARTED" in message
    assert "classification=INCOMPLETE_START_NOT_OBSERVED_WITHIN_ACCEPTANCE_BOUND" in message
    assert not any(url.endswith("/live-output?after=0") for url in calls)


def test_interruptible_start_returns_pid_from_canonical_process_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def request_json(method: str, url: str, **_kwargs):
        assert method == "GET"
        if url.endswith("/events?after=0"):
            return {
                "events": [
                    {"event_type": "STEP_ATTEMPT_CREATED"},
                    {"event_type": "PROCESS_STARTED", "pid": 4321},
                ]
            }
        return {"run": {"status": "RUNNING"}}

    clock = iter((0.0, 0.0))
    monkeypatch.setattr(runtime_journey, "_request_json", request_json)
    monkeypatch.setattr(runtime_journey.time, "monotonic", lambda: next(clock))
    assert (
        runtime_journey.wait_for_interruptible_step(
            base_url="http://127.0.0.1:8000",
            run_id="run-1",
            repository=tmp_path,
            access_token="token",
            timeout_seconds=1.0,
        )
        == 4321
    )
