"""Q1 native CORE regression: every Git-retirement test must collect in isolation.

The original QENV1 CORE selects explicit modules. A complete hosted suite can
hide accidental unqualified test imports through sys.path effects; independently
collecting the exact module must work from the backend root without injection.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_native_core_git_retirement_collects_without_sibling_import_side_effects() -> (
    None
):
    backend = Path(__file__).resolve().parents[2] / "backend"
    target = "tests/unit/runtime/test_co_r2w3_git_retirement.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-vv", target],
        cwd=backend,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "test_original_owner_retires_real_git_and_durable_row" in result.stdout
    assert (
        "test_git_removal_process_death_replays_only_positive_absence" in result.stdout
    )
