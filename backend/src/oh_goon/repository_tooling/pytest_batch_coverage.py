"""Native pytest-cov collection per attempt and one coverage.py union/report owner."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Callable, NoReturn, TextIO, cast

from oh_goon.engine.command import run_command


class _CoverageReportParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def _native_reports(specifications: tuple[str, ...]) -> dict[str, str | None]:
    if any("\0" in specification for specification in specifications):
        raise ValueError("coverage report contains a null byte")
    plugin = importlib.import_module("pytest_cov.plugin")
    validate = cast(Callable[[str], Sequence[str | None]], plugin.validate_report)
    parser = _CoverageReportParser(add_help=False)
    parser.add_argument(
        "--cov-report",
        action=cast(type[argparse.Action], plugin.StoreReport),
        type=validate,
        default={},
    )
    parsed = parser.parse_args(
        [f"--cov-report={specification}" for specification in specifications]
    )
    return cast(dict[str, str | None], parsed.cov_report)


@dataclass(frozen=True)
class CoverageOptions:
    data_file: Path
    sources: tuple[str, ...] = ()
    reports: tuple[str, ...] = ("term",)
    fail_under: float | None = None
    branch: bool = False
    _native_reports: tuple[tuple[str, str | None], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if "\0" in os.fspath(self.data_file):
            raise ValueError("coverage data file contains a null byte")
        if any("\0" in source for source in self.sources):
            raise ValueError("coverage source contains a null byte")
        object.__setattr__(self, "_native_reports", tuple(_native_reports(self.reports).items()))
        if self.fail_under is not None:
            from coverage.exceptions import CoverageException
            from coverage.results import should_fail_under

            # The same native validator/rounding owner used by coverage.py's CLI.
            try:
                should_fail_under(0.0, self.fail_under, 0)
            except CoverageException as exc:
                raise ValueError(str(exc)) from exc


class BatchCoverageSession:
    """Own current-run data until all collection/execution children have settled.

    The original batch/process owner must settle its children before leaving this
    context, including on cancellation. This helper neither schedules nor supervises
    tests. Native pytest-cov measures each child; native coverage.py combines and
    evaluates the complete union. Reused correctness receipts supply no coverage.
    """

    def __init__(
        self,
        options: CoverageOptions,
        *,
        cwd: Path,
        config_file: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 120,
        python_prefix: tuple[str, ...] | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("coverage command timeout must be positive")
        self.options = options
        self.cwd = cwd.resolve()
        self.config_file = self._absolute(config_file)
        self.data_file = self._absolute(options.data_file)
        from coverage import Coverage
        from coverage.exceptions import CoverageException
        from coverage.results import should_fail_under

        try:
            native_configuration = Coverage(config_file=str(self.config_file))
            self._fail_under = (
                options.fail_under
                if options.fail_under is not None
                else cast(float, native_configuration.get_option("report:fail_under"))
            )
            should_fail_under(0.0, self._fail_under, 0)
        except CoverageException as exc:
            raise ValueError(str(exc)) from exc
        self._environment = dict(os.environ if environment is None else environment)
        self._python_prefix = (sys.executable,) if python_prefix is None else python_prefix
        if not self._python_prefix or any("\0" in argument for argument in self._python_prefix):
            raise ValueError("coverage requires a valid resolved Python command prefix")
        self._timeout = timeout
        self._reports = dict(options._native_reports)
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._attempt_directory: Path | None = None
        self._combined_data: Path | None = None
        self._finished = False
        self._deadline: float | None = None
        self._lock = threading.Lock()

    def _absolute(self, path: Path) -> Path:
        return path if path.is_absolute() else self.cwd / path

    @property
    def native_args(self) -> tuple[str, ...]:
        # Use the installed entry-point name: qualified autoload then recognizes
        # the same plugin rather than registering pytest_cov.plugin twice.
        arguments = ["-p", "pytest_cov", f"--cov-config={self.config_file}"]
        arguments.extend(f"--cov={source}" for source in self.options.sources or ("",))
        if self.options.branch:
            arguments.append("--cov-branch")
        arguments.extend(("--cov-report=", "--cov-fail-under=0"))
        return tuple(arguments)

    def __enter__(self) -> BatchCoverageSession:
        if self._temporary is not None:
            raise RuntimeError("coverage session has already been entered")
        self._temporary = tempfile.TemporaryDirectory(prefix="oh-goon-pytest-coverage-")
        root = Path(self._temporary.name)
        try:
            self._attempt_directory = root / "attempts"
            self._attempt_directory.mkdir()
            combined = root / "combined"
            combined.mkdir()
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        self._combined_data = combined / ".coverage"
        return self

    def environment_for_attempt(
        self, environment: Mapping[str, str], attempt_identity: str
    ) -> dict[str, str]:
        with self._lock:
            if self._attempt_directory is None or self._finished:
                raise RuntimeError("coverage attempt requires an active unfinished session")
            identity = hashlib.sha256(attempt_identity.encode()).hexdigest()[:16]
            data_file = self._attempt_directory / f".coverage.{identity}.{uuid.uuid4().hex}"
        child = dict(environment)
        child["COVERAGE_FILE"] = str(data_file)
        return child

    def _native(
        self, arguments: tuple[str, ...], *, output: TextIO | None = None
    ) -> tuple[int, str]:
        timeout = self._timeout
        if self._deadline is not None:
            timeout = min(timeout, self._deadline - time.monotonic())
            if timeout <= 0:
                return 124, "NATIVE COVERAGE SUITE TIMEOUT before report work\n"
        transfer_errors: list[OSError | ValueError] = []

        def observe(stream: str, chunk: str) -> None:
            if output is not None and stream == "stdout" and not transfer_errors:
                try:
                    output.write(chunk)
                except (OSError, ValueError) as exc:
                    transfer_errors.append(exc)

        try:
            result = run_command(
                *self._python_prefix,
                "-m",
                "coverage",
                *arguments,
                cwd=self.cwd,
                env=self._environment,
                timeout=timeout,
                stream_output=observe if output is not None else None,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                retain_output_tail=True,
            )
        except subprocess.TimeoutExpired as exc:
            return 124, f"NATIVE COVERAGE TIMEOUT: {exc}\n"
        except OSError as exc:
            return 127, f"NATIVE COVERAGE START FAILED: {exc}\n"
        if transfer_errors:
            return 74, f"NATIVE COVERAGE REPORT TRANSFER FAILED: {transfer_errors[0]}\n"
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return 124, "NATIVE COVERAGE SUITE TIMEOUT during report work\n"
        return result.returncode, ("" if output is not None else result.stdout) + result.stderr

    def _report_arguments(self, kind: str, destination: str | None) -> tuple[str, ...]:
        assert self._combined_data is not None
        command = (
            "report" if kind in ("term", "term-missing", "markdown", "markdown-append") else kind
        )
        arguments = [
            command,
            f"--data-file={self._combined_data}",
            f"--rcfile={self.config_file}",
            "--ignore-errors",
        ]
        if kind != "annotate":
            arguments.append("--fail-under=0")
        if kind == "term-missing":
            arguments.append("--show-missing")
        if kind in ("term", "term-missing") and destination == "skip-covered":
            arguments.append("--skip-covered")
        elif kind in ("html", "annotate") and destination is not None:
            arguments.append(f"--directory={destination}")
        elif kind in ("json", "xml", "lcov") and destination is not None:
            arguments.extend(("-o", destination))
        elif kind in ("markdown", "markdown-append"):
            arguments.append("--format=markdown")
        return tuple(arguments)

    def _promote_data(self) -> None:
        assert self._combined_data is not None
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.data_file.name}.", dir=self.data_file.parent
        )
        temporary = Path(name)
        try:
            with (
                os.fdopen(descriptor, "wb") as destination,
                self._combined_data.open("rb") as source,
            ):
                shutil.copyfileobj(source, destination)
            os.replace(temporary, self.data_file)
        finally:
            temporary.unlink(missing_ok=True)

    def finish(self, *, deadline: float | None = None) -> tuple[int, str, float]:
        """Combine only this invocation, retain its data, then report/floor natively."""
        started = time.monotonic()
        self._deadline = deadline
        try:
            return self._finish()
        except OSError as exc:
            return 74, f"NATIVE COVERAGE ARTIFACT FAILED: {exc}\n", time.monotonic() - started

    def _finish(self) -> tuple[int, str, float]:
        with self._lock:
            if self._combined_data is None or self._attempt_directory is None or self._finished:
                raise RuntimeError("coverage finish requires an active unfinished session")
            self._finished = True
        started = time.monotonic()
        status, output = self._native(
            (
                "combine",
                f"--data-file={self._combined_data}",
                f"--rcfile={self.config_file}",
                str(self._attempt_directory),
            )
        )
        if status != 0:
            return status, output, time.monotonic() - started
        # Reporting stays in the isolated data directory: coverage.py's native
        # report command may auto-combine siblings. Old final .coverage.* files
        # therefore cannot contaminate this invocation's coverage union.
        self._promote_data()
        reports = dict(self._reports)
        if "term-missing" in reports:
            if reports.get("term") == "skip-covered":
                reports["term-missing"] = "skip-covered"
            reports.pop("term", None)
        for kind, destination in reports.items():
            if not kind:
                continue
            arguments = self._report_arguments(kind, destination)
            if kind in ("markdown", "markdown-append"):
                path = self._absolute(Path(destination or "coverage.md"))
                with path.open(
                    "a" if kind == "markdown-append" else "w", encoding="utf-8"
                ) as stream:
                    status, report_output = self._native(arguments, output=stream)
            else:
                status, report_output = self._native(arguments)
            output += report_output
            if status != 0:
                return status, output, time.monotonic() - started
        threshold_arguments = (
            "report",
            f"--data-file={self._combined_data}",
            f"--rcfile={self.config_file}",
            "--ignore-errors",
            "--format=total",
            f"--fail-under={self._fail_under}",
        )
        status, threshold_output = self._native(threshold_arguments)
        return status, output + threshold_output, time.monotonic() - started

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        temporary = self._temporary
        self._attempt_directory = None
        self._combined_data = None
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError as cleanup_error:
                print(f"COVERAGE SCRATCH CLEANUP FAILED: {cleanup_error}", file=sys.stderr)
