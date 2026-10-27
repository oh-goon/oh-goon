from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args

ROOT = Path(__file__).resolve().parents[2]
SURFACE = yaml.safe_load(
    (ROOT / "docs" / "development" / "MAKE_TARGET_SURFACE.yml").read_text(
        encoding="utf-8"
    )
)


def _native_coverage_options(state_home: Path) -> argparse.Namespace:
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
        environment.pop(name, None)
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            "test-journal-coverage",
            f"STATE_HOME={state_home}",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commands = [
        shlex.split(line)
        for line in result.stdout.replace("\\\n", " ").splitlines()
        if line.strip()
    ]
    (command,) = [
        argv
        for argv in commands
        if any(arg.endswith("/oh-goon-test-batches") for arg in argv)
    ]
    runner = next(
        index
        for index, arg in enumerate(command)
        if arg.endswith("/oh-goon-test-batches")
    )
    with patch.object(sys, "argv", command[runner:]):
        _parser, options = parse_batch_args()
    return options


def test_journal_coverage_target_is_frozen_owner_local_validation() -> None:
    owner_targets = SURFACE["noncanonical_make_target_classes"][
        "owner-local-validation"
    ]
    assert "test-journal-coverage" in owner_targets


def test_journal_coverage_targets_durable_fact_authority_not_read_projection(
    tmp_path: Path,
) -> None:
    state_home = tmp_path / "state data"
    options = _native_coverage_options(state_home)

    assert options.qualified is True
    assert options.show_output is True
    assert options.pytest_config == Path("pyproject.toml")
    assert options.paths and all(
        path.is_relative_to(Path("tests/unit/runtime")) for path in options.paths
    )
    assert options.marker == ""
    assert options.resume is False and options.batch is None
    assert options.pytest_arg == [
        "-W",
        "error::ResourceWarning",
        "-W",
        "error::pytest.PytestUnraisableExceptionWarning",
    ]
    assert options.coverage_source == [
        "oh_goon.runtime.journal",
        "oh_goon.runtime.journal_store",
        "oh_goon.runtime.journal_index",
        "oh_goon.runtime.journal_codec",
        "oh_goon.runtime.journal_facts",
    ]
    assert (
        options.coverage_data_file
        == state_home / "oh-goon/coverage/journal-durability.coverage"
    )
    # An omitted override would inherit the full-package configuration's floor.
    assert options.coverage_fail_under == 0
    assert options.coverage_report == [
        "term-missing",
        f"json:{state_home}/oh-goon/coverage/journal-durability.json",
    ]
