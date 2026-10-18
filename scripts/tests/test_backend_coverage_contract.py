from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _target_body(name: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile.index(f"{name}:")
    next_target = makefile.find("\n\n", start)
    return makefile[start : len(makefile) if next_target < 0 else next_target]


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

    coverage = _target_body("coverage-python")
    assert 'pytest tests ../scripts/tests -m "not certification"' in coverage
    assert "--cov=src/oh_goon --cov-branch" in coverage


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


def test_backend_coverage_pytest_roots_have_unique_test_module_basenames() -> None:
    roots = (ROOT / "backend/tests", ROOT / "scripts/tests")
    modules: dict[str, list[str]] = {}
    for test_root in roots:
        for path in test_root.rglob("test_*.py"):
            modules.setdefault(path.name, []).append(path.relative_to(ROOT).as_posix())

    collisions = {name: paths for name, paths in modules.items() if len(paths) > 1}
    assert collisions == {}, (
        "coverage-python collects backend/tests and scripts/tests in one pytest process; "
        f"duplicate test module basenames are import-identity collisions: {collisions}"
    )


def test_normal_coverage_paths_collect_json_without_printing_the_per_file_table() -> (
    None
):
    coverage = _target_body("coverage-python")
    backend = _target_body("check-backend")
    for target in (coverage, backend):
        assert "--cov=src/oh_goon --cov-branch" in target
        assert "--cov-report=json:../coverage-python.json" in target
        assert "--cov-report=term" not in target

    report = _target_body("coverage-report")
    assert ".venv/bin/coverage report -m" in report
    assert "pytest" not in report
