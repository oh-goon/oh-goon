"""Run the shipped Python projects' native test loops in owned environments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.repository_tooling.pytest_execution_report import (
    _admit_report_directory_location,
    _open_report_directory,
)
from oh_goon.repository_tooling.pytest_process_environment import without_test_report_environment

ROOT = Path(__file__).resolve().parents[4]
_PROJECTS = (
    ("iris", "templates/goons/ml/iris-train", ("tests",)),
    (
        "orders",
        "templates/goons/general/orders-taskflow",
        ("tests",),
    ),
)
PROJECT_REPORT_SCHEMA = "oh-goon.python-project-checks/v1"


def _admit_report_directory(path: Path, root: Path) -> tuple[Path, int, tuple[int, int]]:
    if not path.is_absolute() or ".." in path.parts or path.is_relative_to(root.resolve()):
        raise ValueError("Python project reports require an absolute directory outside source")
    descriptor = _open_report_directory(path)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            raise ValueError("Python project report directory must be private")
        for name in ("summary.json", *(f"{name}.junit.xml" for name, _, _ in _PROJECTS)):
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise FileExistsError(f"Python project report already exists: {name}")
        for name in ("summary.json", *(f"{name}.junit.xml" for name, _, _ in _PROJECTS)):
            artifact = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=descriptor,
            )
            try:
                metadata = os.fstat(artifact)
                if name == "summary.json":
                    summary_identity = metadata.st_dev, metadata.st_ino
            finally:
                os.close(artifact)
        _admit_report_directory_location(path, descriptor)
        return path, descriptor, summary_identity
    except BaseException:
        os.close(descriptor)
        raise


def run_project_tests(
    root: Path = ROOT,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    report_directory: Path | None = None,
    continue_independent: bool = False,
) -> int:
    report = (
        _admit_report_directory(report_directory, root) if report_directory is not None else None
    )
    try:
        scratch = Path(tempfile.mkdtemp(prefix="oh-goon-python-project-tests-"))
    except BaseException:
        if report is not None:
            os.close(report[1])
        raise
    quiescent = True
    exit_code = 0
    failure: BaseException | None = None
    rows: list[dict[str, Any]] = [
        {
            "name": name,
            "project": relative,
            "sync_exit_code": None,
            "pytest_exit_code": None,
            "phase": "UNSTARTED",
            "process_cleanup": "VERIFIED",
            "junit_file": f"{name}.junit.xml",
        }
        for name, relative, _ in _PROJECTS
    ]
    cleanup = "INCOMPLETE"
    cleanup_error: str | None = None

    def observe_process(event: CommandProcessLifecycleEvent) -> None:
        nonlocal quiescent
        if event.kind == "PROCESS_STARTING":
            quiescent = False
        elif event.kind == "DESCENDANTS_QUIESCENT":
            quiescent = True

    def stream(_stream: str, text: str) -> None:
        print(text, end="", flush=True)

    try:
        for row, (name, relative, tests) in zip(rows, _PROJECTS, strict=True):
            project = root / relative
            environment = {
                **without_test_report_environment(os.environ),
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
                    *(("--maxfail=0",) if continue_independent else ()),
                    *(("-o", "xfail_strict=true") if report is not None else ()),
                    *(
                        (f"--junitxml={report[0] / row['junit_file']}",)
                        if report is not None
                        else ()
                    ),
                ),
            )
            project_failed = False
            for phase, command in zip(("sync", "pytest"), commands, strict=True):
                row["phase"] = phase.upper()
                try:
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
                except (CommandCancelledError, CommandTimeoutExpired) as exc:
                    quiescent = exc.supervisor.final_process_group_quiescent
                    row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                    row["phase"] = (
                        "CANCELLED"
                        if isinstance(exc, CommandCancelledError)
                        else phase.upper() + "_TIMEOUT"
                    )
                    failure = failure or exc
                    if (
                        isinstance(exc, CommandTimeoutExpired)
                        and continue_independent
                        and quiescent
                    ):
                        project_failed = True
                        break
                    raise
                except BaseException:
                    row["phase"] = "ERROR"
                    row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                    raise
                quiescent = result.supervisor.final_process_group_quiescent
                row[phase + "_exit_code"] = result.returncode
                row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                if result.returncode or not quiescent:
                    exit_code = exit_code or result.returncode or 1
                    row["phase"] = (
                        phase.upper() + "_FAILED" if quiescent else "PROCESS_CLEANUP_UNVERIFIED"
                    )
                    project_failed = True
                    break
                row["phase"] = "COMPLETE" if phase == "pytest" else "SYNC"
            if not quiescent or (project_failed and not continue_independent):
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
                cleanup = "VERIFIED"
            except OSError as exc:
                cleanup_error = "ENVIRONMENT_CLEANUP_FAILED"
                print(
                    f"Python project environment cleanup failed: {scratch}: {exc}", file=sys.stderr
                )
                if failure is not None:
                    failure.add_note(f"Python project environment cleanup failed: {scratch}")
                elif exit_code == 0:
                    exit_code = 1
        else:
            cleanup_error = "PROCESS_CLEANUP_UNVERIFIED"
            print(
                f"Python project environment retained: process cleanup is unproven: {scratch}",
                file=sys.stderr,
            )
        if report is not None:
            path, descriptor, identity = report
            try:
                _admit_report_directory_location(path, descriptor)
                current = os.stat("summary.json", dir_fd=descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != identity:
                    raise RuntimeError("Python project report destination changed")
                publish_text_file(
                    path / "summary.json",
                    json.dumps(
                        {
                            "schema": PROJECT_REPORT_SCHEMA,
                            "projects": rows,
                            "cleanup": cleanup,
                            "cleanup_error": cleanup_error,
                            "first_nonzero_exit_code": exit_code or None,
                            "exception": type(failure).__name__ if failure is not None else None,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                )
                _admit_report_directory_location(path, descriptor)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Python project report publication failed: {exc}", file=sys.stderr)
                if failure is not None:
                    failure.add_note("Python project report publication failed")
                elif exit_code == 0:
                    exit_code = 1
            finally:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    print(f"Python project report handle cleanup failed: {exc}", file=sys.stderr)
                    if failure is not None:
                        failure.add_note("Python project report handle cleanup failed")
                    elif exit_code == 0:
                        exit_code = 1
    if failure is not None:
        raise failure
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-directory", type=Path)
    parser.add_argument("--continue-independent", action="store_true")
    arguments = parser.parse_args(argv)
    return run_project_tests(
        report_directory=arguments.report_directory,
        continue_independent=arguments.continue_independent,
    )


if __name__ == "__main__":
    raise SystemExit(main())
