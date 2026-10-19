import pytest

from oh_goon.repository_tooling.acceptance.timing import (
    INTERRUPTIBLE_STEP_ENV,
    RESOLVED_TIMING_ENV,
    STEP_START_TIMEOUT_ENV,
    RuntimeAcceptanceTiming,
)


def test_runtime_acceptance_timing_resolves_caller_overrides_once():
    timing = RuntimeAcceptanceTiming.resolve(
        {
            STEP_START_TIMEOUT_ENV: "300",
            INTERRUPTIBLE_STEP_ENV: "480",
        }
    )
    assert timing.step_start_timeout_seconds == 300.0
    assert timing.interruptible_step_seconds == 480.0
    assert timing.cancellation_window_seconds == 180.0

    delegated = RuntimeAcceptanceTiming.resolve(
        {
            RESOLVED_TIMING_ENV: timing.serialized(),
            STEP_START_TIMEOUT_ENV: "1",
            INTERRUPTIBLE_STEP_ENV: "2",
        }
    )
    assert delegated == timing


def test_runtime_acceptance_timing_rejects_non_finite_and_unsafe_values():
    for value in ("0", "-1", "nan", "inf"):
        with pytest.raises(RuntimeError):
            RuntimeAcceptanceTiming.resolve({STEP_START_TIMEOUT_ENV: value})
    with pytest.raises(RuntimeError, match="stable observation window"):
        RuntimeAcceptanceTiming.resolve(
            {
                STEP_START_TIMEOUT_ENV: "300",
                INTERRUPTIBLE_STEP_ENV: "300",
            }
        )


def test_resolved_timing_rejects_unknown_fields():
    payload = RuntimeAcceptanceTiming(300, 480).serialized()
    tampered = payload[:-1] + ',"extra":1}'
    with pytest.raises(RuntimeError, match="unexpected or missing fields"):
        RuntimeAcceptanceTiming.resolve({RESOLVED_TIMING_ENV: tampered})


def test_resolved_timing_rejects_tampering_after_resolution():
    payload = (
        RuntimeAcceptanceTiming(300, 480)
        .serialized()
        .replace('"step_start_timeout_seconds":300', '"step_start_timeout_seconds":301')
    )
    with pytest.raises(RuntimeError, match="authority digest does not match"):
        RuntimeAcceptanceTiming.resolve({RESOLVED_TIMING_ENV: payload})
