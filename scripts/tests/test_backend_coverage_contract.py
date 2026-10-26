from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args

ROOT = Path(__file__).resolve().parents[2]


def _native_commands(target: str, *overrides: str) -> list[list[str]]:
    environment = os.environ.copy()
    for name in (
        "BACKEND_CHECK_TEST_ROOTS",
        "CHECK_PYTEST_MARKER",
        "TOOLING_COMPLETE_TEST_MARKER",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", target, *overrides],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return [
        shlex.split(line)
        for line in result.stdout.replace("\\\n", " ").splitlines()
        if line.strip()
    ]


def _batch_options(commands: list[list[str]]) -> list[argparse.Namespace]:
    options = []
    for command in commands:
        runner = next(
            (
                index
                for index, argument in enumerate(command)
                if argument.endswith("/oh-goon-test-batches")
            ),
            None,
        )
        if runner is None:
            continue
        assert command[:runner] == [
            "bash",
            "scripts/development_test_resources.sh",
            "sh",
            "-c",
            'cd backend && exec "$@"',
            "sh",
        ]
        with patch.object(sys, "argv", command[runner:]):
            _parser, arguments = parse_batch_args()
        assert arguments.qualified is True
        assert arguments.show_output is True
        assert arguments.pytest_config == Path("pyproject.toml")
        options.append(arguments)
    return options


def test_backend_coverage_measures_real_tooling_subprocesses_and_both_test_surfaces() -> (
    None
):
    pyproject = tomllib.loads(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    run = pyproject["tool"]["coverage"]["run"]
    report = pyproject["tool"]["coverage"]["report"]

    assert run["branch"] is True
    assert run["source"] == ["src/oh_goon"]
    assert run["patch"] == ["subprocess"]
    assert report["fail_under"] >= 80

    (coverage,) = _batch_options(_native_commands("coverage-python"))
    assert coverage.paths == [Path("tests"), Path("../scripts/tests")]
    assert coverage.marker == "not certification"
    assert coverage.coverage_source == ["src/oh_goon"]
    assert coverage.coverage_data_file == ROOT / "backend/.coverage"


def test_backend_coverage_floor_and_ratchet_never_drop_below_eighty_percent() -> None:
    floors = json.loads((ROOT / "quality-floors.json").read_text(encoding="utf-8"))

    assert floors["python"]["lines"] >= 80.0
    assert floors["python"]["branches"] >= 80.0


def test_frontend_coverage_policy_is_unchanged_by_backend_maturity_phase() -> None:
    floors = json.loads((ROOT / "quality-floors.json").read_text(encoding="utf-8"))
    assert floors["frontend"] == {
        "branches": 10.0,
        "functions": 15.0,
        "lines": 15.0,
        "statements": 15.0,
    }


def test_normal_coverage_paths_collect_json_without_printing_the_per_file_table() -> (
    None
):
    (coverage,) = _batch_options(_native_commands("coverage-python"))
    assert coverage.coverage_source == ["src/oh_goon"]
    assert coverage.coverage_report == ["json:../coverage-python.json"]
    assert coverage.coverage_fail_under == 0

    commands = _native_commands("coverage-report")
    assert any(
        command[-3:] == [".venv/bin/coverage", "report", "-m"] for command in commands
    )
    assert not _batch_options(commands)


def test_explicit_coverage_checks_and_floor_authoring_use_complete_native_coverage() -> (
    None
):
    for target in ("quality-check", "quality-raise"):
        (coverage,) = _batch_options(_native_commands(target))
        assert coverage.paths == [Path("tests"), Path("../scripts/tests")]
        assert coverage.marker == "not certification"
        assert coverage.coverage_source == ["src/oh_goon"]
        assert coverage.coverage_fail_under == 0
        assert coverage.coverage_report == ["json:../coverage-python.json"]


def test_ordinary_backend_gate_measures_correctness_without_coverage_targets() -> None:
    commands = _native_commands("backend-check")
    tests = _batch_options(commands)
    assert len(tests) == 1
    assert tests[0].paths == []
    assert tests[0].marker == "not certification and not slow"
    assert tests[0].workers == 1
    assert tests[0].batch_size == 4
    assert all(not test.coverage_source for test in tests)
    assert not any("check" in argv and "--area" in argv for argv in commands)


def test_backend_gate_preserves_explicit_roots_and_caller_marker() -> None:
    tests = _batch_options(
        _native_commands(
            "backend-check",
            "BACKEND_CHECK_TEST_ROOTS=tests/unit tests/dependency_complete",
            "CHECK_PYTEST_MARKER=not certification",
        )
    )
    (test,) = tests
    assert test.paths == [Path("tests/unit"), Path("tests/dependency_complete")]
    assert test.marker == "not certification"
    assert test.workers == 1
