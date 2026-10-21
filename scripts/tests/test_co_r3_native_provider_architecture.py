"""CO-R3A negative source mutations in the original X0 and native QENV1 gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.factories.x0_architecture import replace_python_syntax
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _R3_QUALIFIER,
    _R3_TEST,
    check_co_r01_lifecycle_edges,
)


def test_native_conformance_is_on_original_architecture_gate(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/runtime/test_co_r3_native_provider_conformance.py",
            "tests/unit/runtime/other_provider_tests.py",
        ),
        (
            _R3_TEST,
            "@pytest.mark.parametrize('mode', ['DELETE', 'WAL'])",
            "@pytest.mark.parametrize('mode', ['DELETE'])",
        ),
        (
            _R3_TEST,
            "result = real_run(self, *args, **kwargs)",
            'result = CommandResult(returncode=0, stdout="", stderr="")',
        ),
        (
            _R3_TEST,
            "restarted.reconcile_worktree_retirement(workspace)",
            "restarted._force_released(workspace)",
        ),
        (_R3_TEST, "os._exit(181)", 'raise RuntimeError("not a real child exit")'),
        (
            _R3_TEST,
            "LocalDatabase(path, journal_mode='UNREGISTERED_PROVIDER')",
            "LocalDatabase(path, journal_mode='AUTO')",
        ),
    ],
)
def test_conformance_bypass_is_rejected_by_original_x0(
    tmp_path: Path,
    relative: str,
    old: str,
    new: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    content = target.read_text(encoding="utf-8")
    mutated = (
        replace_python_syntax(content, old, new)
        if target.suffix == ".py" and not old.startswith("os._exit(")
        else content.replace(old, new, 1)
    )
    assert mutated != content
    target.write_text(mutated, encoding="utf-8")
    assert any(issue.path == target for issue in check_co_r01_lifecycle_edges(root))
