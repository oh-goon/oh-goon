from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.repository_tooling import _dependency_resolution_primitives as dep
from oh_goon.repository_tooling import check_quality_ratchet as quality


def test_native_version_and_requirement_language_is_fail_closed() -> None:
    assert dep._version_tuple("uv 0.12.5") == (0, 12, 5)
    assert dep._version_tuple("uv 1.2") == (1, 2, 0)
    with pytest.raises(dep.DependencyResolutionError, match="Cannot parse"):
        dep._version_tuple("uv unknown")
    assert dep._minimum_version(">=0.10.0", owner="uv") == (0, 10, 0)
    with pytest.raises(dep.DependencyResolutionError, match="simple >="):
        dep._minimum_version(">=0.10,<1", owner="uv")
    assert dep._bounded_version_match((0, 12, 5), ">=0.10.0", owner="uv")
    assert dep._bounded_version_match((0, 12, 5), ">=0.10,<0.13", owner="uv")
    assert not dep._bounded_version_match((0, 13, 0), ">=0.10,<0.13", owner="uv")
    with pytest.raises(dep.DependencyResolutionError, match="must declare"):
        dep._bounded_version_match((1,), "~=1", owner="uv")
    with pytest.raises(dep.DependencyResolutionError, match="invalid native uv range"):
        dep._bounded_version_match((1,), ">=2,<1", owner="uv")
    assert dep._requirement_name("Foo_Bar>=1") == "foo-bar"
    with pytest.raises(dep.DependencyResolutionError, match="Cannot normalize"):
        dep._requirement_name(" @@@")


def _patch_quality_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path]:
    floors = tmp_path / "quality-floors.json"
    py = tmp_path / "coverage-python.json"
    ux = tmp_path / "coverage-summary.json"
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    monkeypatch.setattr(quality, "FLOORS_PATH", floors)
    monkeypatch.setattr(quality, "PYTHON_COVERAGE", py)
    monkeypatch.setattr(quality, "UX_COVERAGE", ux)
    return floors, py, ux


def test_quality_metric_parsers_and_current_artifact_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floors, py, ux = _patch_quality_paths(monkeypatch, tmp_path)
    py.write_text(
        json.dumps(
            {"totals": {"percent_statements_covered": 90, "num_branches": 0, "covered_branches": 0}}
        )
    )
    ux.write_text(json.dumps({"total": {m: {"pct": 50} for m in quality.METRICS}}))
    assert quality._python_metrics() == {"lines": 90.0, "branches": 100.0}
    assert quality._ux_metrics() == {m: 50.0 for m in quality.METRICS}
    assert set(quality._current("all")) == {"python", "frontend"}
    py.write_text(json.dumps({"totals": []}))
    with pytest.raises(ValueError, match="totals"):
        quality._python_metrics()
    ux.write_text(json.dumps({"total": []}))
    with pytest.raises(ValueError, match="total"):
        quality._ux_metrics()
    py.unlink()
    ux.unlink()
    with pytest.raises(SystemExit, match="Missing coverage artifacts"):
        quality._current("all")


def test_quality_frontend_rows_floor_shape_and_check_success_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    floors, py, ux = _patch_quality_paths(monkeypatch, tmp_path)
    py.write_text(
        json.dumps(
            {
                "totals": {
                    "percent_statements_covered": 90,
                    "num_branches": 10,
                    "covered_branches": 9,
                }
            }
        )
    )
    ux.write_text(json.dumps({"total": {m: {"pct": 90} for m in quality.METRICS}}))
    floors.write_text(
        json.dumps(
            {"python": {"lines": 80, "branches": 80}, "frontend": {m: 80 for m in quality.METRICS}}
        )
    )
    quality._check("all")
    assert "Quality ratchet passed" in capsys.readouterr().out
    bad = {"python": None}
    assert "python: quality floor area is missing" in quality._minimum_floor_failures(bad)
    with pytest.raises(SystemExit, match="backend minimum"):
        quality._require_minimum_floors(bad)
    ux.write_text(json.dumps({"total": {**{m: {"pct": 90} for m in quality.METRICS}, "lines": []}}))
    with pytest.raises(ValueError, match="lines"):
        quality._ux_metrics()


def test_quality_raise_and_history_guard_cover_one_way_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    floors, py, ux = _patch_quality_paths(monkeypatch, tmp_path)
    py.write_text(
        json.dumps(
            {
                "totals": {
                    "percent_statements_covered": 88.439,
                    "num_branches": 100,
                    "covered_branches": 81,
                }
            }
        )
    )
    floors.write_text(json.dumps({"python": {"lines": 90, "branches": 80}}))
    quality._raise_floors("python")
    raised = json.loads(floors.read_text())
    assert raised["python"] == {"lines": 90.0, "branches": 81.0}
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    quality._guard_floor_history()
    assert "skipped" in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setattr(
        quality.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stderr="no ref", stdout=""),
    )
    with pytest.raises(SystemExit, match="Unable to read"):
        quality._guard_floor_history()
    monkeypatch.setattr(
        quality.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps({"python": {"lines": 91, "branches": 80}})
        ),
    )
    with pytest.raises(SystemExit, match="cannot decrease"):
        quality._guard_floor_history()
    monkeypatch.setattr(
        quality.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"python": {"lines": 89, "branches": 80}, "legacy": "ignore"}),
        ),
    )
    quality._guard_floor_history()
    assert "passed against main" in capsys.readouterr().out
