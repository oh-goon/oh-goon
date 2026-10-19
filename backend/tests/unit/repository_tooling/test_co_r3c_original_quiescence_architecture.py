"""Mutation tests enforce the CO-R3C negative physical-evidence boundary in X0."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _PROCESS_GROUP_OWNER,
    _R3_QUALIFIER,
    _R3C_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_x0_retains_original_supervisor_and_negative_writer_control(
    tmp_path: Path,
) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/runtime/test_co_r3c_trust_writer_quiescence.py",
            "tests/unit/runtime/test_r3c_dummy_instead.py",
        ),
        (
            _R3_QUALIFIER,
            "tests/unit/repository_tooling/test_co_r3c_original_quiescence_architecture.py",
            "tests/unit/repository_tooling/test_r3c_dummy_instead.py",
        ),
        (
            _PROCESS_GROUP_OWNER,
            "return False if inspected and not incomplete else None",
            "return False if inspected else None",
        ),
        (_PROCESS_GROUP_OWNER, "incomplete = True", "incomplete = False"),
        (_R3C_TEST, "start_new_session=True", "start_new_session=False"),
        (_R3C_TEST, "marker.stat().st_size > before", "marker.stat().st_size == before"),
        (_R3C_TEST, "os.killpg(escaped_pid, signal.SIGKILL)", "pass"),
        (_R3C_TEST, "run_command(", "never_start_original_supervisor("),
    ],
)
def test_bypass_original_quiescence_or_survivor_downgrades_x0(
    tmp_path: Path,
    relative: str,
    old: str,
    new: str,
) -> None:
    root = _fixture(tmp_path)
    file = root / relative
    source = file.read_text(encoding="utf-8")
    assert old in source
    file.write_text(source.replace(old, new, 1), encoding="utf-8")
    assert any(issue.path == file for issue in check_co_r01_lifecycle_edges(root))
