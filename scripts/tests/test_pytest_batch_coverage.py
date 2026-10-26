from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from coverage import CoverageData

from oh_goon.engine.command import CommandResult
from oh_goon.repository_tooling import pytest_batch_coverage as coverage_batches
from oh_goon.repository_tooling.pytest_batch_coverage import (
    BatchCoverageSession,
    CoverageOptions,
)


def _configuration(tmp_path: Path, *, floor: int = 80) -> Path:
    path = tmp_path / ".coveragerc"
    path.write_text(f"[run]\nbranch = true\n[report]\nfail_under = {floor}\n")
    return path


def _subject(tmp_path: Path) -> Path:
    path = tmp_path / "subject.py"
    path.write_text(
        "def choose(enabled):\n    if enabled:\n        return 1\n    return 0\n"
    )
    return path


def _record_arcs(path: Path, subject: Path, arcs: tuple[tuple[int, int], ...]) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs({str(subject): arcs})
    data.write()


@pytest.mark.parametrize(
    "report", ["invented:result.json", "term:result.txt", "json:bad\0path"]
)
def test_native_report_configuration_rejects_invalid_specs_before_scratch_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: str
) -> None:
    created: list[bool] = []

    def unexpected_scratch(*args: Any, **kwargs: Any) -> None:
        created.append(True)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", unexpected_scratch)
    with pytest.raises(ValueError):
        CoverageOptions(data_file=tmp_path / ".coverage", reports=(report,))
    assert created == []


def test_report_destination_conflicts_use_native_pytest_cov_validation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="markdown"):
        CoverageOptions(
            data_file=tmp_path / ".coverage",
            reports=("markdown:same.md", "markdown-append:same.md"),
        )


@pytest.mark.parametrize("floor", [-1.0, 100.01, float("nan"), float("inf")])
def test_invalid_coverage_floor_is_refused_by_native_coverage_validation(
    tmp_path: Path, floor: float
) -> None:
    with pytest.raises(ValueError):
        CoverageOptions(data_file=tmp_path / ".coverage", fail_under=floor)


def test_native_configuration_errors_fail_before_entering_a_session(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / ".coveragerc"
    configuration.write_text("[run\nnot valid configuration\n")
    with pytest.raises(ValueError):
        BatchCoverageSession(
            CoverageOptions(data_file=tmp_path / ".coverage"),
            cwd=tmp_path,
            config_file=configuration,
        )


def test_collection_retries_and_concurrent_batches_have_unique_current_run_data(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    options = CoverageOptions(
        data_file=Path(".coverage"),
        sources=("subject", "other"),
        branch=True,
    )
    original = {"COVERAGE_FILE": "previous-run", "CALLER_VALUE": "retained"}
    with BatchCoverageSession(
        options, cwd=tmp_path, config_file=configuration
    ) as session:
        collection = session.environment_for_attempt(original, "collection")
        with ThreadPoolExecutor(max_workers=4) as pool:
            attempts = tuple(
                pool.map(
                    lambda _: session.environment_for_attempt(
                        original, "same-retried-batch"
                    ),
                    range(12),
                )
            )
        paths = {
            environment["COVERAGE_FILE"] for environment in (collection, *attempts)
        }
        assert len(paths) == 13
        assert all(
            environment["CALLER_VALUE"] == "retained" for environment in attempts
        )
        assert original["COVERAGE_FILE"] == "previous-run"
        assert {Path(path).parent for path in paths} == {
            Path(collection["COVERAGE_FILE"]).parent
        }
        assert session.native_args == (
            "-p",
            "pytest_cov",
            f"--cov-config={configuration}",
            "--cov=subject",
            "--cov=other",
            "--cov-branch",
            "--cov-report=",
            "--cov-fail-under=0",
        )
        scratch = Path(collection["COVERAGE_FILE"]).parent.parent
    assert not scratch.exists()


def test_cancellation_removes_owned_scratch_and_preserves_the_interruption(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with BatchCoverageSession(
            CoverageOptions(data_file=tmp_path / ".coverage"),
            cwd=tmp_path,
            config_file=configuration,
        ) as session:
            attempt = session.environment_for_attempt({}, "interrupted")
            scratch = Path(attempt["COVERAGE_FILE"]).parent.parent
            raise KeyboardInterrupt
    assert not scratch.exists()


@pytest.mark.parametrize(
    "failure,expected",
    [(OSError("not executable"), 127), (subprocess.TimeoutExpired("coverage", 1), 124)],
)
def test_native_finalizer_failures_remain_nonzero_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, expected: int
) -> None:
    def fail_native(*args: Any, **kwargs: Any) -> CommandResult:
        raise failure

    monkeypatch.setattr(coverage_batches, "run_command", fail_native)
    with BatchCoverageSession(
        CoverageOptions(data_file=tmp_path / ".coverage"),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
        python_prefix=(sys.executable, "-I"),
    ) as session:
        status, output, _elapsed = session.finish()
    assert status == expected
    assert "NATIVE COVERAGE" in output


def test_native_finalizer_preserves_the_resolved_qualified_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def native_failure(*args: str, **kwargs: Any) -> CommandResult:
        commands.append(args)
        return CommandResult(returncode=19, stdout="", stderr="combine refused")

    monkeypatch.setattr(coverage_batches, "run_command", native_failure)
    with BatchCoverageSession(
        CoverageOptions(data_file=tmp_path / ".coverage"),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
        python_prefix=(sys.executable, "-I"),
    ) as session:
        status, output, _elapsed = session.finish()
    assert status == 19
    assert "combine refused" in output
    assert commands[0][:4] == (sys.executable, "-I", "-m", "coverage")


def test_expired_suite_deadline_prevents_native_coverage_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_native(*args: Any, **kwargs: Any) -> CommandResult:
        pytest.fail("expired suite must not start coverage reporting")

    monkeypatch.setattr(coverage_batches, "run_command", unexpected_native)
    with BatchCoverageSession(
        CoverageOptions(data_file=tmp_path / ".coverage"),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
    ) as session:
        monkeypatch.setattr(time, "monotonic", lambda: 100.0)
        status, output, _elapsed = session.finish(deadline=99.0)
    assert status == 124
    assert "SUITE TIMEOUT" in output
    assert not (tmp_path / ".coverage").exists()


def test_native_coverage_commands_share_remaining_suite_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [100.0]
    observed: list[float] = []
    subject = _subject(tmp_path)

    def native(*args: str, **kwargs: Any) -> CommandResult:
        observed.append(float(kwargs["timeout"]))
        if "combine" in args:
            data = next(
                argument.split("=", 1)[1]
                for argument in args
                if argument.startswith("--data-file=")
            )
            _record_arcs(Path(data), subject, ((-1, 1), (1, -1)))
            clock[0] += 0.25
        else:
            clock[0] += 2.0
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(coverage_batches, "run_command", native)
    with BatchCoverageSession(
        CoverageOptions(
            data_file=tmp_path / ".coverage", reports=("json:report.json",)
        ),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
    ) as session:
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        status, output, _elapsed = session.finish(deadline=102.0)
    assert status == 124
    assert "SUITE TIMEOUT" in output
    assert observed == [2.0, 1.75]


@pytest.mark.parametrize("returncode", [124, 1], ids=["timeout", "resource-pressure"])
def test_partial_pass_receipts_without_native_data_cannot_recover_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
) -> None:
    from argparse import Namespace
    from concurrent.futures import Future

    from oh_goon.repository_tooling import pytest_batch_orchestration as orchestration
    from oh_goon.repository_tooling import test_batches as batches
    from oh_goon.repository_tooling.pytest_execution_membership import (
        ExecutionMembershipStore,
    )
    from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent

    members = ("a.py::test_first", "b.py::test_second")
    suite_identity = "sha256:" + "a" * 64
    plan_identity = "sha256:" + "b" * 64
    run_identity = "sha256:" + "c" * 64
    store = ExecutionMembershipStore(
        tmp_path / "receipts",
        suite_identity=suite_identity,
        execution_plan_identity=plan_identity,
        run_identity=run_identity,
    )
    launches: list[tuple[str, ...]] = []
    absent_data: list[Path] = []

    def interrupted_measurement(
        selected: tuple[str, ...],
        *,
        timeout: int,
        marker: str | None,
        coverage_session: BatchCoverageSession | None = None,
        **receipts: object,
    ) -> tuple[int, str, float]:
        launches.append(selected)
        attempt = receipts["receipt_attempt_identity"]
        assert isinstance(attempt, str)
        assert coverage_session is not None
        absent_data.append(
            Path(coverage_session.environment_for_attempt({}, attempt)["COVERAGE_FILE"])
        )
        store.record_terminal(
            selected[0],
            attempt_identity=attempt,
            observed_batch_identity="sha256:" + "d" * 64,
            status="PASSED",
        )
        store.record_started(
            selected[1],
            attempt_identity=attempt,
            observed_batch_identity="sha256:" + "d" * 64,
        )
        return returncode, "RuntimeError: can't start new thread\n", 0.1

    monkeypatch.setattr(batches, "_run_members", interrupted_measurement)
    batch = orchestration.Batch(index=1, total=1, members=members)
    with BatchCoverageSession(
        CoverageOptions(data_file=tmp_path / ".coverage"),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
    ) as session:
        result = batches._run(
            batch,
            timeout=10,
            marker=None,
            receipt_directory=store.directory,
            receipt_suite_identity=suite_identity,
            receipt_execution_plan_identity=plan_identity,
            receipt_run_identity=run_identity,
            coverage_session=session,
        )
        assert store.passed_members() == frozenset((members[0],))
        assert launches == [members]
        assert absent_data and all(not path.exists() for path in absent_data)
        future: Future[tuple[int, str, float, tuple[ExecutionRecoveryEvent, ...]]] = (
            Future()
        )
        future.set_result(result)
        state = orchestration._BatchRunState(
            unscheduled=[],
            selected_batches=0,
            suite_started=time.monotonic(),
            deadline=None,
            pending={},
            attempts={1: 1},
        )
        callbacks = orchestration.BatchCallbacks(
            run_batch=batches._run,
            progress_refresh=lambda message: None,
            progress_clear=lambda: None,
            bounded_failure_output=batches._bounded_failure_output,
            resource_pressure_failure=batches._resource_pressure_failure,
            suite_budget_stop=batches._suite_budget_stop,
        )
        retried = orchestration._consume_future(
            Namespace(environment_retries=2, marker=None, show_output=False),
            state,
            future,
            batch,
            callbacks,
            coverage=True,
        )
        assert not retried
        assert state.unscheduled == []
        assert state.failures == 1
        receipt, _ = store.finalize(
            selected=members,
            resumed=(),
            suite_passed=state.failures == 0,
            recovery_lineage=(),
        )
        assert not receipt.terminal_success
        assert receipt.missing == (members[1],)
        assert receipt.duplicates == ()
    assert "BATCH COVERAGE RECOVERY INCOMPLETE" in capsys.readouterr().out


@pytest.mark.process
def test_native_union_includes_collection_and_every_branch_without_prior_run_data(
    tmp_path: Path,
) -> None:
    subject = _subject(tmp_path)
    old = tmp_path / "old.py"
    old.write_text("OLD = True\n")
    final = tmp_path / ".coverage"
    _record_arcs(final, old, ((-1, 1), (1, -1)))
    _record_arcs(tmp_path / ".coverage.stale", old, ((-1, 1), (1, -1)))
    report = tmp_path / "coverage.json"
    with BatchCoverageSession(
        CoverageOptions(
            data_file=final, reports=(f"json:{report}",), branch=True, fail_under=93
        ),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
    ) as session:
        collection = session.environment_for_attempt({}, "collection")
        positive = session.environment_for_attempt({}, "positive")
        negative = session.environment_for_attempt({}, "negative")
        _record_arcs(Path(collection["COVERAGE_FILE"]), subject, ((-1, 1), (1, -1)))
        _record_arcs(
            Path(positive["COVERAGE_FILE"]), subject, ((-1, 2), (2, 3), (3, -1))
        )
        _record_arcs(
            Path(negative["COVERAGE_FILE"]), subject, ((-1, 2), (2, 4), (4, -1))
        )
        scratch = Path(collection["COVERAGE_FILE"]).parent.parent
        status, output, _elapsed = session.finish()
    assert status == 0, output
    data = CoverageData(basename=str(final))
    data.read()
    assert data.has_arcs()
    assert data.measured_files() == {str(subject)}
    totals = json.loads(report.read_text())["totals"]
    assert totals["missing_lines"] == 0
    assert totals["missing_branches"] == 0
    assert not scratch.exists()
    assert (tmp_path / ".coverage.stale").is_file()


@pytest.mark.process
@pytest.mark.parametrize(
    "floor,configured,expected",
    [(None, 100, 2), (0.0, 100, 0), (90.0, 80, 2), (93.0, 80, 2)],
)
def test_native_union_floor_is_applied_once_and_keeps_the_final_data_on_failure(
    tmp_path: Path, floor: float | None, configured: int, expected: int
) -> None:
    subject = _subject(tmp_path)
    final = tmp_path / ".coverage"
    report = tmp_path / "coverage.json"
    with BatchCoverageSession(
        CoverageOptions(
            data_file=final, reports=(f"json:{report}",), fail_under=floor, branch=True
        ),
        cwd=tmp_path,
        config_file=_configuration(tmp_path, floor=configured),
    ) as session:
        attempt = session.environment_for_attempt({}, "positive-only")
        _record_arcs(
            Path(attempt["COVERAGE_FILE"]),
            subject,
            ((-1, 1), (1, -1), (-1, 2), (2, 3), (3, -1)),
        )
        status, output, _elapsed = session.finish()
    assert status == expected, output
    assert final.is_file()
    assert report.is_file()
    assert json.loads(report.read_text())["totals"]["missing_branches"] == 1


@pytest.mark.process
@pytest.mark.parametrize("qualified", [False, True])
def test_native_pytest_cov_entrypoint_works_with_both_plugin_loading_modes(
    tmp_path: Path, qualified: bool
) -> None:
    subject = _subject(tmp_path)
    (tmp_path / "test_subject.py").write_text(
        "import subject\ndef test_positive():\n    assert subject.choose(True) == 1\n"
    )
    environment = os.environ.copy()
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        environment.pop(name, None)
    if not qualified:
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    prefix = (sys.executable, "-I") if qualified else (sys.executable,)
    with BatchCoverageSession(
        CoverageOptions(
            data_file=tmp_path / ".coverage",
            sources=(str(subject.parent),),
            fail_under=0,
        ),
        cwd=tmp_path,
        config_file=_configuration(tmp_path),
        environment=environment,
        python_prefix=prefix,
    ) as session:
        for phase in ("collection", "execution"):
            arguments = ("--collect-only",) if phase == "collection" else ()
            result = subprocess.run(
                (
                    *prefix,
                    "-m",
                    "pytest",
                    "-q",
                    *arguments,
                    *session.native_args,
                    "test_subject.py",
                ),
                cwd=tmp_path,
                env=session.environment_for_attempt(environment, phase),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        status, output, _elapsed = session.finish()
    assert status == 0, output
