from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pytest

from oh_goon.repository_tooling.frontend_test_results import (
    collect_native_vitest,
    require_native_vitest_collection,
    require_native_vitest_files,
    vitest_outcomes,
)
from oh_goon.repository_tooling import frontend_test_results as frontend_owner


def _native() -> dict[str, Any]:
    return {
        "numTotalTests": 2,
        "numPassedTests": 2,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
        "numTotalTestSuites": 2,
        "numPassedTestSuites": 2,
        "numFailedTestSuites": 0,
        "numPendingTestSuites": 0,
        "success": True,
        "testResults": [
            {
                "name": "same.test.ts",
                "status": "passed",
                "assertionResults": [{"fullName": "same title", "status": "passed"}],
            },
            {
                "name": "same.test.ts",
                "status": "passed",
                "assertionResults": [{"fullName": "same title", "status": "passed"}],
            },
        ],
    }


def _collection(project_root: Path) -> dict[str, Any]:
    return {
        "requested": ["frontend/same.test.ts"],
        "selected": [
            {"file": "frontend/same.test.ts", "projectName": "first"},
            {"file": "frontend/same.test.ts", "projectName": "second"},
        ],
        "unavailable_advisory_targets": [],
        "native_project_root": str(project_root),
        "native_execution": {
            "argv": [
                "npm",
                "exec",
                "--offline",
                "--no",
                "--",
                "vitest",
                "list",
                "./same.test.ts",
                "--filesOnly",
                "--json="
                + str(
                    Path(project_root.anchor) / "vitest-proof-scratch/collection.json"
                ),
            ],
            "returncode": 0,
            "process_group_quiescent": True,
        },
    }


def test_native_assertion_occurrences_are_preserved_without_title_deduplication() -> (
    None
):
    complete, satisfied, counts = vitest_outcomes(_native())
    assert complete and satisfied
    assert counts["numTotalTests"] == counts["numPassedTests"] == 2


@pytest.mark.parametrize("status", ("failed", "pending", "todo", "skipped"))
def test_native_non_pass_outcome_cannot_hide_behind_green_counters(status: str) -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    report["testResults"][0]["assertionResults"][0]["status"] = status
    assert vitest_outcomes(report)[1] is False


@pytest.mark.parametrize(
    "damage",
    (
        "zero",
        "omission",
        "interrupt",
        "runtime-error",
        "pending-suite",
        "failed-suite",
        "failed-native-success",
    ),
)
def test_missing_or_unsatisfied_native_work_is_nonqualifying(damage: str) -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    if damage == "zero":
        report["numTotalTests"] = report["numPassedTests"] = 0
        report["testResults"] = []
    elif damage == "omission":
        report["testResults"].pop()
    elif damage == "interrupt":
        report["wasInterrupted"] = True
    elif damage == "runtime-error":
        report["numRuntimeErrorTestSuites"] = 1
    elif damage == "pending-suite":
        report["numPendingTestSuites"] = 1
    elif damage == "failed-suite":
        report["numFailedTestSuites"] = 1
    else:
        report["success"] = False
    if damage == "zero":
        with pytest.raises(ValueError, match="assertion results"):
            vitest_outcomes(report)
    else:
        assert vitest_outcomes(report)[1] is False


def test_unknown_or_malformed_native_result_is_rejected() -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    for damage in ("unknown", "missing", "boolean"):
        changed = deepcopy(report)
        if damage == "unknown":
            changed["testResults"][0]["assertionResults"][0]["status"] = (
                "some-future-status"
            )
        elif damage == "missing":
            changed.pop("numTotalTests")
        else:
            changed["numPassedTests"] = True
        with pytest.raises(ValueError):
            vitest_outcomes(changed)


def test_green_counters_cannot_qualify_an_unexecuted_advisory_file(
    tmp_path: Path,
) -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    with pytest.raises(ValueError, match="missing, duplicated or extra"):
        require_native_vitest_files(
            report,
            project_root=tmp_path,
            expected_files=("frontend/same.test.ts", "frontend/omitted.spec.ts"),
        )


def test_native_project_occurrences_are_preserved_without_filename_deduplication(
    tmp_path: Path,
) -> None:
    collection = _collection(tmp_path)
    assert require_native_vitest_files(
        _native(),
        project_root=tmp_path,
        expected_files=("frontend/same.test.ts",),
        collection=collection,
    ) == {"frontend/same.test.ts"}


@pytest.mark.parametrize(
    "defect", ("omitted", "extra", "duplicate", "outside", "empty", "project")
)
def test_native_file_membership_rejects_unsatisfied_or_foreign_work(
    tmp_path: Path, defect: str
) -> None:
    collection = _collection(tmp_path)
    report = _native()
    assert require_native_vitest_files(
        report,
        project_root=tmp_path,
        expected_files=("frontend/same.test.ts",),
        collection=collection,
    ) == {"frontend/same.test.ts"}
    if defect == "omitted":
        report["testResults"].pop()
    elif defect == "extra":
        report["testResults"].append(deepcopy(report["testResults"][0]))
    elif defect == "duplicate":
        collection["selected"] = [
            {"file": "frontend/same.test.ts", "projectName": "first"}
        ]
    elif defect == "outside":
        report["testResults"][0]["name"] = str(tmp_path.parent / "foreign.test.ts")
    elif defect == "empty":
        report["testResults"][0]["assertionResults"] = []
    else:
        for row in report["testResults"]:
            row["projectName"] = "first"
    with pytest.raises(ValueError):
        require_native_vitest_files(
            report,
            project_root=tmp_path,
            expected_files=("frontend/same.test.ts",),
            collection=collection,
        )


def test_collection_cannot_duplicate_a_single_native_project_occurrence(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="duplicates a project file"):
        require_native_vitest_files(
            _native(),
            project_root=tmp_path,
            expected_files=("frontend/same.test.ts",),
            collection={
                **_collection(tmp_path),
                "selected": [
                    {"file": "frontend/same.test.ts", "projectName": "first"},
                    {"file": "frontend/same.test.ts", "projectName": "first"},
                ],
            },
        )


@pytest.mark.parametrize(
    "defect",
    (
        "root",
        "command",
        "status",
        "unsettled",
        "requested",
        "unhashable-request",
        "missing-advice",
        "extra-advice",
    ),
)
def test_collection_replay_requires_original_settled_context_and_advisory_accounting(
    tmp_path: Path, defect: str
) -> None:
    collection = _collection(tmp_path)
    assert (
        len(
            require_native_vitest_collection(
                collection,
                project_root=tmp_path,
                expected_files=("frontend/same.test.ts",),
            )
        )
        == 2
    )
    if defect == "root":
        collection["native_project_root"] = str(tmp_path.parent / "another-project")
    elif defect == "command":
        collection["native_execution"]["argv"][7] = "different.test.ts"
    elif defect == "status":
        collection["native_execution"]["returncode"] = False
    elif defect == "unsettled":
        collection["native_execution"]["process_group_quiescent"] = False
    elif defect == "requested":
        collection["requested"] = []
    elif defect == "unhashable-request":
        collection["requested"] = [{}]
    elif defect == "missing-advice":
        collection["requested"].append("frontend/unavailable.spec.ts")
    else:
        collection["unavailable_advisory_targets"] = ["frontend/unrequested.spec.ts"]
    with pytest.raises(ValueError):
        require_native_vitest_collection(
            collection, project_root=tmp_path, expected_files=("frontend/same.test.ts",)
        )


def test_native_configured_collection_retains_real_files_and_unavailable_advice(
    tmp_path: Path,
) -> None:
    toolchain = Path(__file__).resolve().parents[2] / "frontend/node_modules"
    if shutil.which("npm") is None or not (toolchain / "vitest/vitest.mjs").is_file():
        pytest.skip("initialized native npm/Vitest environment required")
    project = tmp_path / "frontend"
    project.mkdir()
    (project / "node_modules").symlink_to(toolchain, target_is_directory=True)
    (project / "package.json").write_text(
        '{"name":"native-collection-fixture","private":true}'
    )
    (project / "vitest.config.mjs").write_text(
        "export default { test: { projects: [\n"
        ' { test: { name: "first", include: ["unit.test.mjs", "--config=other.test.mjs"] } },\n'
        ' { test: { name: "second", include: ["unit.test.mjs", "--config=other.test.mjs"] } }\n'
        "] } };\n"
    )
    for name in ("unit.test.mjs", "browser.spec.mjs", "--config=other.test.mjs"):
        (project / name).write_text(
            "throw new Error('filesOnly must not execute assertions');\n"
        )
    collection = collect_native_vitest(
        tmp_path,
        (
            "frontend/unit.test.mjs",
            "frontend/browser.spec.mjs",
            "frontend/--config=other.test.mjs",
        ),
    )
    assert sorted(
        collection["selected"], key=lambda row: (row["file"], row["projectName"])
    ) == [
        {"file": "frontend/--config=other.test.mjs", "projectName": "first"},
        {"file": "frontend/--config=other.test.mjs", "projectName": "second"},
        {"file": "frontend/unit.test.mjs", "projectName": "first"},
        {"file": "frontend/unit.test.mjs", "projectName": "second"},
    ]
    assert collection["unavailable_advisory_targets"] == ["frontend/browser.spec.mjs"]
    assert collection["native_execution"]["process_group_quiescent"] is True


def test_native_temp_base_inside_source_refuses_before_scratch_or_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    inside = source / "selected-temp"
    inside.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(inside))

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail(
            "Invalid native temporary storage reached a scratch or process effect"
        )

    monkeypatch.setattr(frontend_owner, "TemporaryDirectory", forbidden)
    monkeypatch.setattr("oh_goon.engine.command.run_command", forbidden)
    with pytest.raises(ValueError, match="temporary storage outside source"):
        collect_native_vitest(source, ("frontend/unit.test.mjs",))
    assert list(inside.iterdir()) == []
