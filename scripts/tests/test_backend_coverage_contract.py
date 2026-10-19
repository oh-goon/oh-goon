from __future__ import annotations

import json
import shlex
import subprocess
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
    assert (
        'pytest -c pyproject.toml tests ../scripts/tests -m "not certification"'
        in coverage
    )
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
    assert "--cov=src/oh_goon --cov-branch" in coverage
    assert "--cov-report=json:../coverage-python.json" in coverage
    assert "--cov-report=term" not in coverage

    report = _target_body("coverage-report")
    assert ".venv/bin/coverage report -m" in report
    assert "pytest" not in report


def test_explicit_coverage_checks_and_floor_authoring_use_complete_native_coverage() -> (
    None
):
    for target in ("quality-check", "quality-raise"):
        result = subprocess.run(
            ["make", "--no-print-directory", "--dry-run", target],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        commands = [
            shlex.split(line) for line in result.stdout.splitlines() if "pytest" in line
        ]
        coverage = [argv for argv in commands if "--cov=src/oh_goon" in argv]
        assert len(coverage) == 1
        assert coverage[0][
            coverage[0].index("pyproject.toml") + 1 : coverage[0].index("-m")
        ] == ["tests", "../scripts/tests"]
        assert coverage[0][coverage[0].index("-m") + 1] == "not certification"
        assert "--cov-fail-under=0" in coverage[0]
        assert coverage[0][coverage[0].index("-c") + 1] == "pyproject.toml"


def test_ordinary_backend_gate_measures_correctness_without_coverage_targets() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "backend-check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commands = [shlex.split(line) for line in result.stdout.splitlines()]
    tests = [argv for argv in commands if "pytest" in argv]
    assert len(tests) == 1
    assert tests[0][tests[0].index("-m") + 1] == "not certification"
    assert not any(arg.startswith("--cov") for arg in tests[0])
    assert not any("check" in argv and "--area" in argv for argv in commands)
