from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pytest

from oh_goon.repository_tooling.frontend_test_results import (
    collect_native_vitest,
    collect_native_playwright,
    native_playwright_arguments,
    require_native_playwright_collection,
    require_native_playwright_files,
    require_native_vitest_collection,
    require_native_vitest_files,
    vitest_outcomes,
)
from oh_goon.repository_tooling import frontend_test_results as frontend_owner


@pytest.fixture
def native_frontend_toolchain() -> None:
    """Declare native locked npm capability for collection, without setup effects."""


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
    native_frontend_toolchain: None,
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
    with pytest.raises(ValueError, match="temporary storage outside source"):
        collect_native_playwright(source, ("frontend/unit.test.mjs",))
    assert list(inside.iterdir()) == []


def _playwright_report(project_root: Path, *, executed: bool) -> dict[str, Any]:
    return {
        "config": {
            "configFile": str(project_root / "playwright.config.ts"),
            "rootDir": str(project_root / "e2e"),
            "version": "native-fixture",
            "forbidOnly": True,
            "projects": [
                {
                    "id": "chromium",
                    "name": "chromium",
                    "repeatEach": 1,
                    "retries": 0,
                    "testDir": str(project_root / "e2e"),
                }
            ],
        },
        "suites": [
            {
                "title": "same.spec.ts",
                "file": "same.spec.ts",
                "specs": [
                    {
                        "id": f"native-{index}",
                        "title": "same visible title",
                        "file": "same.spec.ts",
                        "line": index + 1,
                        "column": 1,
                        "ok": True,
                        "tests": [
                            {
                                "projectId": "chromium",
                                "projectName": "chromium",
                                "expectedStatus": "passed",
                                "status": "expected" if executed else "skipped",
                                "results": [
                                    {"status": "passed", "retry": 0, "errors": []}
                                ]
                                if executed
                                else [],
                            }
                        ],
                    }
                    for index in range(2)
                ],
            }
        ],
        "errors": [],
        "stats": {
            "expected": 2 if executed else 0,
            "skipped": 0 if executed else 2,
            "unexpected": 0,
            "flaky": 0,
        },
    }


def _playwright_collection(project_root: Path) -> dict[str, Any]:
    report = _playwright_report(project_root, executed=False)
    directory = Path(project_root.anchor) / "playwright-proof-scratch"
    return {
        "requested": ["frontend/e2e/same.spec.ts"],
        "selected": [
            {
                "id": f"native-{index}",
                "title": "same visible title",
                "file": "frontend/e2e/same.spec.ts",
                "line": index + 1,
                "column": 1,
                "project_id": "chromium",
                "project_name": "chromium",
                "occurrence": 0,
            }
            for index in range(2)
        ],
        "unavailable_advisory_targets": [],
        "native_project_root": str(project_root),
        "native_report": report,
        "native_execution": {
            "argv": [
                "npm",
                "exec",
                "--offline",
                "--no",
                "--",
                "playwright",
                "test",
                "--list",
                "--reporter=json",
                "--pass-with-no-tests",
                *native_playwright_arguments(
                    project_root, ("frontend/e2e/same.spec.ts",)
                ),
                f"--output={directory / 'artifacts'}",
            ],
            "returncode": 0,
            "process_group_quiescent": True,
            "report_path": str(directory / "collection.json"),
        },
    }


def test_playwright_membership_uses_native_ids_not_visible_titles(
    tmp_path: Path,
) -> None:
    collection = _playwright_collection(tmp_path)
    assert (
        len(
            require_native_playwright_collection(
                collection,
                project_root=tmp_path,
                expected_files=("frontend/e2e/same.spec.ts",),
            )
        )
        == 2
    )
    assert require_native_playwright_files(
        _playwright_report(tmp_path, executed=True),
        project_root=tmp_path,
        expected_files=("frontend/e2e/same.spec.ts",),
        collection=collection,
    ) == {"frontend/e2e/same.spec.ts"}


@pytest.mark.parametrize(
    "defect",
    (
        "omitted",
        "duplicate",
        "extra",
        "foreign",
        "identity",
        "project",
        "location",
        "context",
        "skipped",
        "expected-failure",
        "retry",
        "hidden-failure",
        "error",
        "stats",
        "no-results",
        "repeated-instance",
        "collection-error",
        "unsettled",
    ),
)
def test_playwright_incomplete_or_foreign_native_results_refuse(
    tmp_path: Path, defect: str
) -> None:
    collection = _playwright_collection(tmp_path)
    report = _playwright_report(tmp_path, executed=True)
    assert require_native_playwright_files(
        report,
        project_root=tmp_path,
        expected_files=("frontend/e2e/same.spec.ts",),
        collection=collection,
    )
    spec = report["suites"][0]["specs"][0]
    test = spec["tests"][0]
    if defect == "omitted":
        report["suites"][0]["specs"].pop()
        report["stats"]["expected"] = 1
    elif defect == "duplicate":
        report["suites"][0]["specs"].append(deepcopy(spec))
    elif defect == "extra":
        extra = deepcopy(spec)
        extra["id"] = "native-added"
        report["suites"][0]["specs"].append(extra)
        report["stats"]["expected"] = 3
    elif defect == "foreign":
        spec["file"] = str(tmp_path.parent / "foreign.spec.ts")
    elif defect == "identity":
        spec["id"] = "replaced-native-id"
    elif defect == "project":
        test["projectId"] = {}
    elif defect == "location":
        spec["line"] += 10
    elif defect == "context":
        report["config"]["version"] = "another-native-runtime"
    elif defect == "skipped":
        test["results"][0]["status"] = "skipped"
    elif defect == "expected-failure":
        test["expectedStatus"] = "failed"
    elif defect == "retry":
        test["results"][0]["retry"] = 1
    elif defect == "hidden-failure":
        test["results"].insert(
            0, {"status": "failed", "retry": 0, "errors": ["earlier failure"]}
        )
    elif defect == "error":
        report["errors"] = ["global runtime error"]
    elif defect == "stats":
        report["stats"]["flaky"] = 1
    elif defect == "no-results":
        test["results"] = []
    elif defect == "repeated-instance":
        spec["tests"].append(deepcopy(test))
    elif defect == "collection-error":
        collection["native_report"]["errors"] = ["collection failure"]
    else:
        collection["native_execution"]["process_group_quiescent"] = False
    with pytest.raises(ValueError):
        require_native_playwright_files(
            report,
            project_root=tmp_path,
            expected_files=("frontend/e2e/same.spec.ts",),
            collection=collection,
        )


def test_native_playwright_collection_and_pass_receipt_preserve_literal_files_and_repeats(
    tmp_path: Path,
    native_frontend_toolchain: None,
) -> None:
    from oh_goon.engine.command import run_command

    toolchain = Path(__file__).resolve().parents[2] / "frontend/node_modules"
    if (
        shutil.which("npm") is None
        or not (toolchain / "@playwright/test/package.json").is_file()
    ):
        pytest.skip("initialized native npm/Playwright environment required")
    project = tmp_path / "frontend"
    project.mkdir()
    (project / "node_modules").symlink_to(toolchain, target_is_directory=True)
    (project / "package.json").write_text(
        '{"name":"native-playwright-fixture","private":true}'
    )
    (project / "playwright.config.mjs").write_text(
        'export default {testDir:".",testMatch:"**/*.spec.mjs",forbidOnly:true,repeatEach:2,workers:1,projects:[{name:"chromium"},{name:"other"}]};\n'
    )
    sentinel = tmp_path / "native-assertions-executed"
    for name in ("case[1].spec.mjs", "--config=other.spec.mjs", "case1.spec.mjs"):
        (project / name).write_text(
            'import {test,expect} from "@playwright/test";\n'
            'import {appendFileSync} from "node:fs";\n'
            f'test("native assertion",()=>{{appendFileSync({json.dumps(str(sentinel))},"x");expect(2+2).toBe(4);}});\n'
        )
    (project / "unit.test.mjs").write_text(
        "throw new Error('not a Playwright member');\n"
    )
    targets = (
        "frontend/case[1].spec.mjs",
        "frontend/--config=other.spec.mjs",
        "frontend/unit.test.mjs",
    )
    collection = collect_native_playwright(tmp_path, targets)
    assert not sentinel.exists(), "native list executed assertions"
    assert collection["unavailable_advisory_targets"] == ["frontend/unit.test.mjs"]
    selected = {row["file"] for row in collection["selected"]}
    assert selected == set(targets[:2])
    assert len(collection["selected"]) == 4
    assert {row["occurrence"] for row in collection["selected"]} == {0, 1}
    with tempfile.TemporaryDirectory(
        prefix="oh-goon-playwright-native-proof-"
    ) as directory:
        report = Path(directory) / "result.json"
        result = run_command(
            "npm",
            "exec",
            "--offline",
            "--no",
            "--",
            "playwright",
            "test",
            "--reporter=json",
            *native_playwright_arguments(project, sorted(selected)),
            f"--output={Path(directory) / 'artifacts'}",
            cwd=project,
            env=dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_FILE=str(report)),
            timeout=60,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
        assert result.returncode == 0, result.stderr
        with report.open() as stream:
            native = json.load(stream)
    assert (
        require_native_playwright_files(
            native,
            project_root=project,
            expected_files=sorted(selected),
            collection=collection,
        )
        == selected
    )
    assert sentinel.read_text() == "xxxx"
    # No matching advice remains diagnostic, never a fabricated PASS member.
    empty = collect_native_playwright(tmp_path, ("frontend/unit.test.mjs",))
    assert empty["selected"] == []
    assert empty["unavailable_advisory_targets"] == ["frontend/unit.test.mjs"]
