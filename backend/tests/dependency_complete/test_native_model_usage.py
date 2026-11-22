"""Owned service usage is passive, scoped and preserved through physical failure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.dependency_complete.test_agent_codex_service_execution import execute_fixture

from oh_goon.core.models.model_usage import MAX_MODEL_USAGE_COUNT
from oh_goon.integrations.agent_codex_service_execution import (
    CodexServiceExecutionCancelled,
    CodexServiceExecutionFailure,
)


def _usage(
    value: object, *, thread: str = "owned-thread", turn: str = "owned-turn"
) -> dict[str, Any]:
    return {
        "method": "thread/tokenUsage/updated",
        "params": {"threadId": thread, "turnId": turn, "tokenUsage": value},
    }


def _snapshot(value: int) -> dict[str, Any]:
    return {
        "last": {"inputTokens": 9999, "outputTokens": 8888},
        "total": {
            "inputTokens": value,
            "outputTokens": 5,
            "cachedInputTokens": 3,
            "reasoningOutputTokens": 2,
            "totalTokens": value + 5,
        },
        "private": "SYNTHETIC_PRIVATE_USAGE_CANARY",
    }


def _terminal(*, failed: bool = False) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": "owned-thread",
            "turn": {
                "id": "owned-turn",
                "status": "failed" if failed else "completed",
                "error": {"message": "Native failure"} if failed else None,
            },
        },
    }


def test_missing_native_usage_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, _, _ = execute_fixture(tmp_path, monkeypatch)
    result = invoke()
    assert result.outcome.returncode == 0
    assert result.outcome.model_usage is None


def test_native_total_replaces_duplicates_and_ignores_foreign_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, _, physical = execute_fixture(
        tmp_path,
        monkeypatch,
        notifications=[
            _usage(_snapshot(10)),
            _usage(_snapshot(10)),
            _usage(_snapshot(777), thread="foreign-thread"),
            _usage(_snapshot(888), turn="foreign-turn"),
            _usage(_snapshot(20)),
            _terminal(),
        ],
    )
    result = invoke()
    report = result.outcome.model_usage
    assert result.outcome.returncode == 0
    assert result.outcome.supervisor is physical[0].supervisor
    assert report is not None and not report.truncated
    assert len(report.observations) == 1
    observation = report.observations[0]
    assert observation.source == "codex-service" and observation.scope == "native_thread"
    assert observation.implementation_identity == "sha256:" + "0" * 64
    assert observation.requested_model == "native-model" and observation.reported_model is None
    assert observation.input_tokens == 20 and observation.output_tokens == 5
    assert observation.cache_read_tokens == 3 and observation.cache_write_tokens is None
    assert observation.reasoning_tokens == 2 and observation.total_tokens == 25
    assert observation.cost is None and observation.completeness == "reported"
    encoded = report.model_dump_json()
    assert "owned-thread" not in encoded and "owned-turn" not in encoded
    assert "foreign-" not in encoded and "SYNTHETIC_PRIVATE_USAGE_CANARY" not in encoded


def test_reported_native_zero_is_distinct_from_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, _, _ = execute_fixture(
        tmp_path,
        monkeypatch,
        notifications=[
            _usage({"total": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}}),
            _terminal(),
        ],
    )
    result = invoke()
    assert result.outcome.returncode == 0
    report = result.outcome.model_usage
    assert report is not None
    observation = report.observations[0]
    assert observation.input_tokens == observation.output_tokens == observation.total_tokens == 0
    assert observation.cache_read_tokens is observation.reasoning_tokens is None
    assert observation.completeness == "reported"


@pytest.mark.parametrize(
    ("model", "retained"),
    (
        ("native-model", "native-model"),
        ("org/SYNTHETIC_PARENT_CONTROL_SECRET:model", None),
    ),
)
def test_usage_model_identity_cannot_copy_a_known_host_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, retained: str | None
) -> None:
    invoke, _, _ = execute_fixture(
        tmp_path,
        monkeypatch,
        requested_model=model,
        protect_host_secret=True,
        notifications=[_usage(_snapshot(7)), _terminal()],
    )
    result = invoke()
    assert result.outcome.returncode == 0
    report = result.outcome.model_usage
    assert report is not None
    assert report.observations[0].requested_model == retained
    assert report.observations[0].input_tokens == 7
    assert "SYNTHETIC_PARENT_CONTROL_SECRET" not in report.model_dump_json()


@pytest.mark.parametrize(
    ("value", "completeness", "output"),
    (
        (None, "malformed", None),
        ({"total": {"inputTokens": False}}, "malformed", None),
        ({"total": {"inputTokens": -1, "outputTokens": 4}}, "partial", 4),
        ({"total": {"totalTokens": MAX_MODEL_USAGE_COUNT + 1}}, "malformed", None),
    ),
)
def test_malformed_optional_native_usage_cannot_change_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    completeness: str,
    output: int | None,
) -> None:
    invoke, _, _ = execute_fixture(
        tmp_path, monkeypatch, notifications=[_usage(value), _terminal()]
    )
    result = invoke()
    assert result.outcome.returncode == 0
    report = result.outcome.model_usage
    assert report is not None
    observation = report.observations[0]
    assert observation.completeness == completeness and observation.output_tokens == output
    assert observation.input_tokens is observation.total_tokens is None


@pytest.mark.parametrize("recover", (False, True))
def test_malformed_later_update_preserves_one_coherent_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    notifications = [
        _usage(_snapshot(10)),
        _usage({"total": {"inputTokens": 17, "outputTokens": False}}),
        _usage(None),
    ]
    if recover:
        notifications.append(_usage(_snapshot(20)))
    notifications.append(_terminal())
    invoke, _, _ = execute_fixture(tmp_path, monkeypatch, notifications=notifications)
    result = invoke()
    assert result.outcome.returncode == 0
    report = result.outcome.model_usage
    assert report is not None and report.truncated and len(report.observations) == 1
    observation = report.observations[0]
    assert observation.input_tokens == (20 if recover else 10)
    assert observation.output_tokens == 5 and observation.total_tokens == (25 if recover else 15)
    assert observation.completeness == ("reported" if recover else "partial")


@pytest.mark.parametrize("mode", ("native-failure", "cancel", "normal_daemon_exit"))
def test_native_usage_survives_original_failure_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    notifications = [_usage(_snapshot(12))]
    if mode != "cancel":
        notifications.append(_terminal(failed=mode == "native-failure"))
    invoke, _, physical = execute_fixture(
        tmp_path,
        monkeypatch,
        fault=mode if mode in {"cancel", "normal_daemon_exit"} else None,
        notifications=notifications,
    )
    expected = CodexServiceExecutionCancelled if mode == "cancel" else CodexServiceExecutionFailure
    with pytest.raises(expected) as caught:
        invoke()
    failure = caught.value
    assert isinstance(failure, (CodexServiceExecutionCancelled, CodexServiceExecutionFailure))
    outcome = failure.outcome
    assert outcome.returncode != 0
    assert outcome.supervisor is physical[0].supervisor
    assert outcome.supervisor.final_process_group_quiescent is True
    assert outcome.physical_execution_id == physical[0].physical_execution_id
    report = outcome.model_usage
    assert report is not None and report.observations[0].input_tokens == 12
    assert failure.model_usage == report
