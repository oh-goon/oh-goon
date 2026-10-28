"""Waited CLI signal adaptation preserves the caller's native process policy."""

from __future__ import annotations

import argparse
import json
import signal
from concurrent.futures import ThreadPoolExecutor
from types import FrameType, SimpleNamespace
from typing import cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_runtime_plans import _run
from oh_goon.core.errors import FleetPlanInvalid


def _args(*, wait: bool = True) -> argparse.Namespace:
    return argparse.Namespace(wait=wait, as_json=True, repair_handoff=False, fleet_run_id=None)


def test_default_sigterm_is_adapted_before_run_preparation_and_restored(capsys):
    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)

    def before_run(_run_id):
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        assert signal.getsignal(signal.SIGINT) is original_int
        handler(signal.SIGTERM, None)

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=before_run)),
    )
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        assert _run(runtime, _args()) == 143
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["status"] == "interrupted"
        assert receipt["fleet_run_id"] is None
        assert receipt["cancellation_requested"] is False
        assert receipt["cleanup"] == "not-established-by-interruption"
    finally:
        signal.signal(signal.SIGTERM, original_term)


@pytest.mark.parametrize("ignored", [False, True])
def test_explicit_native_sigterm_handlers_remain_caller_owned(capsys, ignored):
    original = signal.getsignal(signal.SIGTERM)

    def caller(_signum: int, _frame: FrameType | None) -> None:
        raise AssertionError("The Run must not invoke or replace a caller's handler")

    selected = signal.SIG_IGN if ignored else caller

    def before_run(_run_id):
        assert signal.getsignal(signal.SIGTERM) is selected
        raise FleetPlanInvalid("stop before work")

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=before_run)),
    )
    try:
        signal.signal(signal.SIGTERM, selected)
        assert _run(runtime, _args()) == 2
        assert signal.getsignal(signal.SIGTERM) is selected
        assert json.loads(capsys.readouterr().out)["status"] == "error"
    finally:
        signal.signal(signal.SIGTERM, original)


@pytest.mark.parametrize("worker_thread,wait", [(True, True), (False, False)])
def test_embedding_threads_and_nonwaited_runs_do_not_change_native_signal_handlers(
    capsys, worker_thread, wait
):
    original = signal.getsignal(signal.SIGTERM)

    def before_run(_run_id):
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        raise FleetPlanInvalid("stop before work")

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=before_run)),
    )
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        if worker_thread:
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(_run, runtime, _args(wait=wait)).result() == 2
        else:
            assert _run(runtime, _args(wait=wait)) == 2
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        assert json.loads(capsys.readouterr().out)["status"] == "error"
    finally:
        signal.signal(signal.SIGTERM, original)


def test_unexpected_failure_restores_default_handler():
    original = signal.getsignal(signal.SIGTERM)

    def fail(_run_id):
        assert callable(signal.getsignal(signal.SIGTERM))
        raise RuntimeError("native owner failed unexpectedly")

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=fail)),
    )
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        with pytest.raises(RuntimeError, match="native owner failed unexpectedly"):
            _run(runtime, _args())
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, original)


def test_later_explicit_caller_handler_is_not_overwritten(capsys):
    original = signal.getsignal(signal.SIGTERM)

    def caller(_signum: int, _frame: FrameType | None) -> None:
        return None

    def select_caller_policy(_run_id):
        signal.signal(signal.SIGTERM, caller)
        raise FleetPlanInvalid("stop before work")

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(plans=SimpleNamespace(require_cli_plan_preparation=select_caller_policy)),
    )
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        assert _run(runtime, _args()) == 2
        assert signal.getsignal(signal.SIGTERM) is caller
        capsys.readouterr()
    finally:
        signal.signal(signal.SIGTERM, original)
