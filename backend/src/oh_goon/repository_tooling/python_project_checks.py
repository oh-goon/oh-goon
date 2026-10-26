"""Run the shipped Python projects' native test loops in owned environments."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    run_command,
)

ROOT = Path(__file__).resolve().parents[4]


def run_project_tests(
    root: Path = ROOT, *, cancel_requested: Callable[[], bool] | None = None
) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="oh-goon-python-project-tests-"))
    quiescent = True
    exit_code = 0
    failure: BaseException | None = None

    def observe_process(event: CommandProcessLifecycleEvent) -> None:
        nonlocal quiescent
        if event.kind == "PROCESS_STARTING":
            quiescent = False
        elif event.kind == "DESCENDANTS_QUIESCENT":
            quiescent = True

    def stream(_stream: str, text: str) -> None:
        print(text, end="", flush=True)

    try:
        for name, relative, tests in (
            ("iris", "templates/goons/ml/iris-train", ("tests",)),
            (
                "orders",
                "templates/goons/general/orders-taskflow",
                ("tests/test_operations.py", "tests/test_cli.py"),
            ),
        ):
            project = root / relative
            environment = {
                **os.environ,
                "UV_PROJECT_ENVIRONMENT": str(scratch / name),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            commands = (
                (
                    "uv",
                    "sync",
                    "--project",
                    str(project),
                    "--frozen",
                    "--no-default-groups",
                    "--group",
                    "dev",
                    "--no-python-downloads",
                ),
                (
                    "uv",
                    "run",
                    "--project",
                    str(project),
                    "--offline",
                    "--frozen",
                    "--no-sync",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    *(str(project / test) for test in tests),
                ),
            )
            for command in commands:
                result = run_command(
                    *command,
                    cwd=root,
                    env=environment,
                    timeout=600,
                    cancel_requested=cancel_requested,
                    process_observer=observe_process,
                    stream_output=stream,
                    merge_stderr=True,
                    require_process_group_quiescence=True,
                    parent_death_signal=True,
                    max_capture_bytes_per_stream=64 * 1024,
                    retain_output_tail=True,
                )
                quiescent = result.supervisor.final_process_group_quiescent
                exit_code = result.returncode
                if exit_code or not quiescent:
                    exit_code = exit_code or 1
                    break
            if exit_code:
                break
    except (CommandCancelledError, CommandTimeoutExpired) as exc:
        quiescent = exc.supervisor.final_process_group_quiescent
        failure = exc
        raise
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if quiescent:
            try:
                shutil.rmtree(scratch)
            except OSError as exc:
                print(
                    f"Python project environment cleanup failed: {scratch}: {exc}", file=sys.stderr
                )
                if failure is not None:
                    failure.add_note(f"Python project environment cleanup failed: {scratch}")
                elif exit_code == 0:
                    exit_code = 1
        else:
            print(
                f"Python project environment retained: process cleanup is unproven: {scratch}",
                file=sys.stderr,
            )
    return exit_code


def main() -> int:
    return run_project_tests()


if __name__ == "__main__":
    raise SystemExit(main())
