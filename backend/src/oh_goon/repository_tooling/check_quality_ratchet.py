from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FLOORS_PATH = ROOT / "quality-floors.json"
PYTHON_COVERAGE = ROOT / "coverage-python.json"
UX_COVERAGE = ROOT / "frontend" / "coverage" / "coverage-summary.json"
METRICS = ("lines", "statements", "functions", "branches")
MINIMUM_COVERAGE_PERCENT = 80.0
TRACKED_METRICS = {"python": ("lines", "branches")}


def _load_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _python_metrics() -> dict[str, float]:
    data = _load_json(PYTHON_COVERAGE)
    totals = data["totals"]
    if not isinstance(totals, dict):
        raise ValueError("coverage-python.json has no totals object")
    line_pct = float(totals["percent_statements_covered"])
    branch_total = int(totals.get("num_branches", 0))
    branch_covered = int(totals.get("covered_branches", 0))
    branch_pct = 100.0 if branch_total == 0 else branch_covered * 100.0 / branch_total
    return {"lines": line_pct, "branches": branch_pct}


def _ux_metrics() -> dict[str, float]:
    data = _load_json(UX_COVERAGE)
    total = data["total"]
    if not isinstance(total, dict):
        raise ValueError("frontend coverage summary has no total object")
    result: dict[str, float] = {}
    for metric in METRICS:
        row = total[metric]
        if not isinstance(row, dict):
            raise ValueError(f"frontend coverage summary has no {metric} object")
        result[metric] = float(row["pct"])
    return result


def _current(area: str) -> dict[str, dict[str, float]]:
    requested = ("python", "frontend") if area == "all" else (area,)
    paths = {"python": PYTHON_COVERAGE, "frontend": UX_COVERAGE}
    missing = [paths[name] for name in requested if not paths[name].exists()]
    if missing:
        names = ", ".join(str(path.relative_to(ROOT)) for path in missing)
        raise SystemExit(f"Missing coverage artifacts: {names}. Run the coverage commands first.")
    result: dict[str, dict[str, float]] = {}
    if "python" in requested:
        result["python"] = _python_metrics()
    if "frontend" in requested:
        result["frontend"] = _ux_metrics()
    return result


def _minimum_floor_failures(floors: dict[str, object]) -> list[str]:
    failures: list[str] = []
    for area, metrics in TRACKED_METRICS.items():
        floor_area = floors.get(area)
        if not isinstance(floor_area, dict):
            failures.append(f"{area}: quality floor area is missing")
            continue
        for metric in metrics:
            value = floor_area.get(metric)
            if value is None:
                failures.append(f"{area}.{metric}: quality floor is missing")
                continue
            floor = float(value)
            if floor + 1e-9 < MINIMUM_COVERAGE_PERCENT:
                failures.append(
                    f"{area}.{metric}: configured floor {floor:.2f}% is below "
                    f"the backend minimum {MINIMUM_COVERAGE_PERCENT:.2f}%"
                )
    return failures


def _require_minimum_floors(floors: dict[str, object]) -> None:
    failures = _minimum_floor_failures(floors)
    if failures:
        raise SystemExit("Quality floors violate the backend minimum:\n- " + "\n- ".join(failures))


def _check(area: str) -> None:
    floors = _load_json(FLOORS_PATH)
    current = _current(area)
    failures = _minimum_floor_failures(floors)
    for area, metrics in current.items():
        floor_area = floors.get(area)
        if not isinstance(floor_area, dict):
            failures.append(f"quality floor missing area {area}")
            continue
        for metric, value in metrics.items():
            floor = float(floor_area.get(metric, -1))
            if value + 1e-9 < floor:
                failures.append(f"{area}.{metric}: {value:.2f}% < floor {floor:.2f}%")
    if failures:
        raise SystemExit("Quality ratchet failed:\n- " + "\n- ".join(failures))
    print("Quality ratchet passed")
    for area, metrics in current.items():
        print(
            f"  {area}: " + ", ".join(f"{metric}={value:.2f}%" for metric, value in metrics.items())
        )


def _raise_floors(area: str) -> None:
    current = _current(area)
    old = _load_json(FLOORS_PATH) if FLOORS_PATH.exists() else {}
    updated: dict[str, object] = dict(old)
    for area_name, metrics in current.items():
        previous = old.get(area_name, {})
        previous = previous if isinstance(previous, dict) else {}
        updated[area_name] = {
            metric: max(
                MINIMUM_COVERAGE_PERCENT,
                float(previous.get(metric, 0)),
                math.floor(value * 100.0 + 1e-9) / 100.0,
            )
            for metric, value in metrics.items()
        }
    FLOORS_PATH.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Raised quality floors in {FLOORS_PATH.relative_to(ROOT)}")


def _guard_floor_history() -> None:
    current = _load_json(FLOORS_PATH)
    _require_minimum_floors(current)
    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if not base_ref:
        print("Quality floor history guard skipped: no pull-request base ref")
        return
    ref = f"origin/{base_ref}:quality-floors.json"
    completed = subprocess.run(
        ["git", "show", ref],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"Unable to read base quality floors from {ref}: {completed.stderr.strip()}"
        )
    base = json.loads(completed.stdout)
    failures: list[str] = []
    for area, metrics in base.items():
        if not isinstance(metrics, dict):
            continue
        current_area = current.get(area)
        if not isinstance(current_area, dict):
            failures.append(f"{area}: area was removed from quality floors")
            continue
        for metric, value in metrics.items():
            current_value = current_area.get(metric)
            if current_value is None or float(current_value) + 1e-9 < float(value):
                failures.append(f"{area}.{metric}: floor decreased from {float(value):.2f}%")
    if failures:
        raise SystemExit(
            "Quality floors are one-way and cannot decrease:\n- " + "\n- ".join(failures)
        )
    print(f"Quality floor history guard passed against {base_ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enforce or raise one-way coverage quality floors")
    parser.add_argument("mode", choices=("check", "raise", "guard"))
    parser.add_argument("--area", choices=("all", "python", "frontend"), default="all")
    args = parser.parse_args()
    if args.mode == "check":
        _check(args.area)
    elif args.mode == "raise":
        _raise_floors(args.area)
    else:
        _guard_floor_history()


if __name__ == "__main__":
    main()
