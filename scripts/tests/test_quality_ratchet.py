from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from oh_goon.repository_tooling import check_quality_ratchet as quality


def test_cli_rejects_non_object_quality_floor_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floors, python_coverage, frontend_coverage = _coverage_files(tmp_path)
    _write(floors, {"python": {"lines": 80, "branches": 80}})
    _write(
        python_coverage,
        {
            "totals": {
                "percent_statements_covered": 95,
                "num_branches": 10,
                "covered_branches": 9,
            }
        },
    )
    _patch_paths(monkeypatch, tmp_path, floors, python_coverage, frontend_coverage)
    monkeypatch.setattr(
        sys, "argv", ["oh-goon-quality-ratchet", "check", "--area", "python"]
    )
    quality.main()

    floors.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON object"):
        quality.main()


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _coverage_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    floors = tmp_path / "quality-floors.json"
    python_coverage = tmp_path / "coverage-python.json"
    frontend_coverage = tmp_path / "coverage-summary.json"
    return floors, python_coverage, frontend_coverage


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    floors: Path,
    python_coverage: Path,
    frontend_coverage: Path,
) -> None:
    monkeypatch.setattr(quality, "ROOT", root)
    monkeypatch.setattr(quality, "FLOORS_PATH", floors)
    monkeypatch.setattr(quality, "PYTHON_COVERAGE", python_coverage)
    monkeypatch.setattr(quality, "UX_COVERAGE", frontend_coverage)


def test_minimum_floor_policy_rejects_any_backend_metric_below_eighty() -> None:
    floors: dict[str, object] = {
        "python": {"lines": 88.43, "branches": 79.99},
        "frontend": {
            "lines": 15.0,
            "statements": 15.0,
            "functions": 15.0,
            "branches": 10.0,
        },
    }

    assert quality._minimum_floor_failures(floors) == [
        "python.branches: configured floor 79.99% is below the backend minimum 80.00%"
    ]


def test_raise_never_writes_a_floor_below_eighty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floors, python_coverage, frontend_coverage = _coverage_files(tmp_path)
    _write(floors, {"python": {"lines": 10, "branches": 20}})
    _write(
        python_coverage,
        {
            "totals": {
                "percent_statements_covered": 74.25,
                "num_branches": 100,
                "covered_branches": 79,
            }
        },
    )
    _write(frontend_coverage, {"total": {}})
    _patch_paths(monkeypatch, tmp_path, floors, python_coverage, frontend_coverage)

    quality._raise_floors("python")

    assert json.loads(floors.read_text(encoding="utf-8"))["python"] == {
        "branches": 80.0,
        "lines": 80.0,
    }


def test_check_requires_both_current_coverage_and_eighty_percent_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floors, python_coverage, frontend_coverage = _coverage_files(tmp_path)
    _write(floors, {"python": {"lines": 88.43, "branches": 80.0}, "frontend": {}})
    _write(
        python_coverage,
        {
            "totals": {
                "percent_statements_covered": 90.0,
                "num_branches": 100,
                "covered_branches": 79,
            }
        },
    )
    _write(frontend_coverage, {"total": {}})
    _patch_paths(monkeypatch, tmp_path, floors, python_coverage, frontend_coverage)

    with pytest.raises(SystemExit) as error:
        quality._check("python")

    message = str(error.value)
    assert "python.branches: 79.00% < floor 80.00%" in message


def test_repository_python_quality_floors_meet_the_backend_minimum() -> None:
    floors = json.loads(quality.FLOORS_PATH.read_text(encoding="utf-8"))
    assert quality._minimum_floor_failures(floors) == []
