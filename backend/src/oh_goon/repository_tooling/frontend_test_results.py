"""Transport configured native Vitest membership and assertion outcomes."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

_COLLECTION_BYTES = 4 * 1024 * 1024


def native_frontend_temporary_base(source_root: Path) -> Path:
    """Validate native tempfile's selected base without selecting a fallback."""
    selected = Path(tempfile.gettempdir())
    canonical = selected.resolve(strict=True)
    if selected != canonical or canonical.is_relative_to(source_root.resolve(strict=True)):
        raise ValueError(
            "Native frontend reports require canonical temporary storage outside source"
        )
    return selected


def _frontend_file(value: object, project_root: Path) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise ValueError("Native Vitest file identity is unavailable")
    member = Path(value)
    if ".." in member.parts:
        raise ValueError("Native Vitest file identity contains traversal")
    member = member if member.is_absolute() else project_root / member
    if not member.is_relative_to(project_root):
        raise ValueError("Native Vitest file belongs to another project")
    return "frontend/" + member.relative_to(project_root).as_posix()


def _requested_files(paths: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(paths)
    if any(
        not isinstance(path, str)
        or not path.startswith("frontend/")
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or "\\" in path
        or "\n" in path
        or "\x00" in path
        or Path(path).as_posix() != path
        for path in selected
    ) or len(selected) != len(set(selected)):
        raise ValueError("Vitest targets require distinct canonical frontend project files")
    return selected


def _collection_members(collection: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    rows = collection.get("selected")
    if not isinstance(rows, list):
        raise ValueError("Native Vitest collection membership is unavailable")
    selected: list[dict[str, str]] = []
    variants: set[tuple[str, str | None]] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) - {"file", "projectName"}
            or not isinstance(row.get("file"), str)
            or ("projectName" in row and not isinstance(row["projectName"], str))
        ):
            raise ValueError("Native Vitest collection member has an invalid shape")
        _requested_files((row["file"],))
        variant = (row["file"], row.get("projectName"))
        if variant in variants:
            raise ValueError("Native Vitest collection duplicates a project file")
        variants.add(variant)
        selected.append(row)
    return tuple(selected)


def require_native_vitest_collection(
    collection: Mapping[str, Any],
    *,
    project_root: Path,
    expected_files: Iterable[str],
) -> tuple[dict[str, str], ...]:
    """Validate the original native collection receipt without collecting again."""
    if (
        not project_root.is_absolute()
        or ".." in project_root.parts
        or collection.get("native_project_root") != str(project_root)
    ):
        raise ValueError("Native Vitest collection belongs to another project root")
    requested_value = collection.get("requested")
    unavailable_value = collection.get("unavailable_advisory_targets")
    if not isinstance(requested_value, list) or not isinstance(unavailable_value, list):
        raise ValueError("Native Vitest advisory accounting is unavailable")
    requested = _requested_files(requested_value)
    unavailable = _requested_files(unavailable_value)
    expected = _requested_files(expected_files)
    members = _collection_members(collection)
    files = {row["file"] for row in members}
    if (
        not requested
        or files != set(expected)
        or not files <= set(requested)
        or set(unavailable) != set(requested) - files
    ):
        raise ValueError("Native Vitest collection differs from admitted advisory files")
    native = collection.get("native_execution")
    if (
        not isinstance(native, Mapping)
        or type(native.get("returncode")) is not int
        or native["returncode"] != 0
        or native.get("process_group_quiescent") is not True
    ):
        raise ValueError("Native Vitest collection has no settled successful invocation")
    argv = native.get("argv")
    if (
        not isinstance(argv, list)
        or any(not isinstance(value, str) for value in argv)
        or argv[:7] != ["npm", "exec", "--offline", "--no", "--", "vitest", "list"]
        or argv[7:-2] != ["./" + path.removeprefix("frontend/") for path in requested]
        or argv[-2:-1] != ["--filesOnly"]
        or not argv[-1].startswith("--json=")
    ):
        raise ValueError("Native Vitest collection command differs from its request")
    report_path = Path(argv[-1].partition("=")[2])
    if (
        not report_path.is_absolute()
        or ".." in report_path.parts
        or report_path.is_relative_to(project_root.parent)
    ):
        raise ValueError("Native Vitest collection report is not outside source")
    return members


def collect_native_vitest(root: Path, targets: Sequence[str]) -> dict[str, Any]:
    """Let configured Vitest discover advisory files without running assertions.

    A package-manifest runner hint does not establish native collection. Missing
    advice remains explicit; no filename or extension substitutes another runner.
    """
    from oh_goon.engine.command import run_command

    requested = _requested_files(targets)
    if not requested:
        raise ValueError("Native Vitest collection requires advisory targets")
    project_root = root / "frontend"
    if not project_root.is_absolute() or ".." in project_root.parts:
        raise ValueError("Native Vitest collection requires its exact project root")
    temporary_base = native_frontend_temporary_base(root)
    with TemporaryDirectory(prefix="oh-goon-vitest-collection-", dir=temporary_base) as directory:
        report = Path(directory) / "collection.json"
        result = run_command(
            "npm",
            "exec",
            "--offline",
            "--no",
            "--",
            "vitest",
            "list",
            *("./" + path.removeprefix("frontend/") for path in requested),
            "--filesOnly",
            f"--json={report}",
            cwd=project_root,
            timeout=60,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=64 * 1024,
            retain_output_tail=True,
        )
        if result.returncode != 0 or result.supervisor.final_process_group_quiescent is not True:
            raise ValueError("Native Vitest collection did not complete successfully")
        with report.open("rb") as stream:
            raw = stream.read(_COLLECTION_BYTES + 1)
        if len(raw) > _COLLECTION_BYTES:
            raise ValueError("Native Vitest collection exceeds its evidence byte budget")
        payload: object = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("Native Vitest collection must contain a JSON file list")
    selected: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict) or set(row) - {"file", "projectName"}:
            raise ValueError("Native Vitest collection member has an invalid shape")
        selected.append({**row, "file": _frontend_file(row.get("file"), project_root)})
    collection = {
        "requested": list(requested),
        "selected": selected,
        "native_project_root": str(project_root),
        "native_execution": {
            "argv": list(result.argv),
            "returncode": result.returncode,
            "process_group_quiescent": result.supervisor.final_process_group_quiescent,
        },
    }
    members = _collection_members(collection)
    files = {row["file"] for row in members}
    if not files <= set(requested):
        raise ValueError("Native Vitest collection expanded outside the advisory files")
    collection["unavailable_advisory_targets"] = sorted(set(requested) - files)
    require_native_vitest_collection(
        collection, project_root=project_root, expected_files=sorted(files)
    )
    return collection


def require_native_vitest_files(
    report: Mapping[str, Any],
    *,
    project_root: Path,
    expected_files: Iterable[str],
    collection: Mapping[str, Any] | None = None,
) -> set[str]:
    """Require exact native file occurrences, separately from assertion grading.

    Current native JSON omits project labels. Preserve collected project variants
    and compare occurrence counts; when results expose labels, compare them too.
    """
    if not project_root.is_absolute() or ".." in project_root.parts:
        raise ValueError("Native Vitest invocation root is unavailable")
    expected = _requested_files(expected_files)
    if not expected:
        raise ValueError("Native Vitest membership requires admitted files")
    members = (
        require_native_vitest_collection(
            collection, project_root=project_root, expected_files=expected
        )
        if collection is not None
        else tuple({"file": path} for path in expected)
    )
    if {row["file"] for row in members} != set(expected):
        raise ValueError("Native Vitest collection differs from admitted files")
    expected_counts = Counter(row["file"] for row in members)
    results = report.get("testResults")
    if not isinstance(results, list) or not results:
        raise ValueError("Native Vitest file results are unavailable")
    actual: list[tuple[str, str | None]] = []
    project_labels = False
    for row in results:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("assertionResults"), list)
            or not row["assertionResults"]
            or ("projectName" in row and not isinstance(row["projectName"], str))
        ):
            raise ValueError("Native Vitest file has no meaningful assertion results")
        actual.append((_frontend_file(row.get("name"), project_root), row.get("projectName")))
        project_labels = project_labels or "projectName" in row
    if Counter(path for path, _project in actual) != expected_counts:
        raise ValueError("Native Vitest files are missing, duplicated or extra")
    if project_labels and Counter(actual) != Counter(
        (row["file"], row.get("projectName")) for row in members
    ):
        raise ValueError("Native Vitest project variants differ from collection")
    return set(expected)


def vitest_outcomes(report: Mapping[str, Any]) -> tuple[bool, bool, dict[str, int]]:
    """Return coverage and all-PASS facts from one native configured invocation."""
    required = (
        "numTotalTests",
        "numPassedTests",
        "numFailedTests",
        "numPendingTests",
        "numTodoTests",
        "numTotalTestSuites",
        "numPassedTestSuites",
        "numFailedTestSuites",
        "numPendingTestSuites",
    )
    if any(type(report.get(name)) is not int or report[name] < 0 for name in required):
        raise ValueError("native Vitest outcome counts are invalid or missing")
    counts = {name: int(report[name]) for name in required}
    results = report.get("testResults")
    if (
        not isinstance(results, list)
        or not results
        or any(
            not isinstance(row, dict) or not isinstance(row.get("assertionResults"), list)
            for row in results
        )
    ):
        raise ValueError("native Vitest assertion results are missing")
    # Different native projects can report the same path/title. Every occurrence
    # remains an instance; the configured invocation supplies their common variant.
    assertions = [item for row in results for item in row["assertionResults"]]
    if any(
        not isinstance(item, dict)
        or item.get("status") not in {"passed", "failed", "pending", "todo", "skipped"}
        for item in assertions
    ):
        raise ValueError("native Vitest assertion outcome is unknown")
    complete = (
        counts["numTotalTests"] > 0
        and len(assertions) == counts["numTotalTests"]
        and report.get("wasInterrupted", False) is False
        and report.get("numRuntimeErrorTestSuites", 0) == 0
        and counts["numPendingTestSuites"] == 0
    )
    satisfied = (
        complete
        and report.get("success") is True
        and counts["numPassedTests"] == counts["numTotalTests"]
        and counts["numTotalTestSuites"] == counts["numPassedTestSuites"]
        and all(item["status"] == "passed" for item in assertions)
        and not any(
            counts[name]
            for name in ("numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites")
        )
    )
    return complete, satisfied, counts
