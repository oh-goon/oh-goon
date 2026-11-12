from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.frontend_test_results import (
    collect_native_vitest,
    require_native_vitest_files,
    vitest_outcomes,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_vitest_keeps_pure_typescript_tests_out_of_jsdom() -> None:
    config = (FRONTEND / "vitest.config.ts").read_text(encoding="utf-8")

    assert 'name: "node"' in config
    assert 'environment: "node"' in config
    assert 'include: ["src/**/*.test.ts", "e2e/**/*.test.ts"]' in config
    assert 'name: "dom"' in config
    assert 'environment: "jsdom"' in config
    assert 'include: ["src/**/*.test.tsx", ...domBackedTypeScriptTests]' in config
    assert 'setupFiles: ["./tests/setup.ts"]' in config
    assert "globals: true" in config


def test_dom_backed_typescript_tests_are_explicit_and_bounded() -> None:
    config = (FRONTEND / "vitest.config.ts").read_text(encoding="utf-8")
    for path in (
        "src/app/api/httpTransport.test.ts",
        "src/app/api/localAccess.test.ts",
        "src/app/fleetRun/watchFleetRun.test.ts",
        "src/app/pages/runReviewState.test.ts",
    ):
        assert f'"{path}"' in config

    # Pure model/domain .ts tests must not silently move the entire suite back to jsdom.
    assert 'environment: "jsdom"' in config
    assert 'environment: "node"' in config
    assert config.count('environment: "jsdom"') == 1


def test_run_step_url_stays_node_only_by_using_explicit_base_href() -> None:
    config = (FRONTEND / "vitest.config.ts").read_text(encoding="utf-8")
    test_source = (FRONTEND / "src/app/pages/runStepUrl.test.ts").read_text(
        encoding="utf-8"
    )

    assert '"src/app/pages/runStepUrl.test.ts"' not in config
    assert "window.location" not in test_source
    assert "runReviewUrl(state, baseHref)" in test_source
    assert "window.location" not in test_source


def test_vitest_uses_one_threaded_file_worker_to_avoid_wsl_transport_failures() -> None:
    config = (FRONTEND / "vitest.config.ts").read_text(encoding="utf-8")
    assert 'pool: "threads"' in config
    assert "fileParallelism: false" in config
    assert "maxWorkers: 2" not in config


def test_dom_setup_is_the_single_test_side_effect_i18n_owner() -> None:
    setup = (FRONTEND / "tests" / "setup.ts").read_text(encoding="utf-8")
    assert 'import "@/i18n/setup";' in setup

    redundant_side_effect_imports = []
    for path in (FRONTEND / "src").rglob("*.test.tsx"):
        source = path.read_text(encoding="utf-8")
        if 'import "@/i18n/setup";' in source:
            redundant_side_effect_imports.append(path.relative_to(FRONTEND).as_posix())
    assert redundant_side_effect_imports == []


def test_development_runner_warns_but_never_blocks_on_windows_mounted_wsl_workspace() -> (
    None
):
    runner = (ROOT / "scripts" / "development_test.sh").read_text(encoding="utf-8")
    assert "WSL Windows-mounted workspace detected" in runner
    assert "performance note only" in runner
    warning_block = runner.split('if [[ "$ROOT" == /mnt/?/* ]]', 1)[1].split("\nfi", 1)[
        0
    ]
    assert "exit" not in warning_block


def test_backend_unit_gate_is_bounded_and_full_qualification_remains_complete() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-python:" in makefile
    assert "--batch-timeout 120" in makefile
    assert "--suite-timeout 600" in makefile
    assert "not slow and not process and not scale and not certification" in makefile
    assert "test-python-complete:" in makefile
    assert "--batch-timeout 180" in makefile
    assert "--suite-timeout 1200" in makefile
    assert "--marker ''" in makefile
    assert "test-python-dependency-complete:" in makefile
    assert "--batch-timeout 60" in makefile
    assert "--suite-timeout 120" in makefile


def test_init_delegates_frontend_materialization_directly_to_native_npm() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = makefile.split("\ninit:\n", 1)[1].split("\ninit-doctor:", 1)[0]
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in body
    assert "frontend_install.sh" not in body
    assert "frontend-dependencies-check" not in body
    assert "setup-smoke:" not in makefile


@pytest.fixture
def native_frontend_toolchain() -> None:
    """Declare native locked npm capability without setup during collection."""


@pytest.mark.process
@pytest.mark.native_resources
def test_ram_profile_preserves_authored_node_and_dom_native_membership(
    tmp_path: Path, native_frontend_toolchain: None
) -> None:
    files = (
        "src/domain/concurrency/MutationController.test.ts",
        "src/domain/reads/readResources.test.ts",
        "src/app/pages/runStepUrl.test.ts",
        "src/app/pages/CatalogPage.test.tsx",
        "src/features/game-tape/components/GameTapeOutputs.test.tsx",
    )
    targets = tuple("frontend/" + path for path in files)
    collection = collect_native_vitest(ROOT, targets)
    projects = {row["file"]: row["projectName"] for row in collection["selected"]}
    assert set(projects.values()) == {"node", "dom"}
    signatures: list[list[tuple[str, str, str, str]]] = []
    counts: list[dict[str, int]] = []
    for mode in ("baseline", "ram"):
        directory = tmp_path / mode
        directory.mkdir(mode=0o700)
        report = directory / "vitest.json"
        environment = {
            **os.environ,
            "OH_GOON_TEST_CPUS": "2",
            "OH_GOON_VITEST_JSON_REPORT": str(report),
        }
        environment.pop("OH_GOON_LOCAL_TEST_PROFILE", None)
        if mode == "ram":
            environment.pop("OH_GOON_TEST_CPUS")
            environment["OH_GOON_LOCAL_TEST_PROFILE"] = "ram"
        native = run_command(
            "bash",
            str(ROOT / "scripts/development_test_resources.sh"),
            "npm",
            "run",
            "test",
            "--",
            *("./" + path for path in files),
            env=environment,
            cwd=FRONTEND,
            timeout=120,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=64 * 1024,
            retain_output_tail=True,
        )
        assert native.returncode == 0, (native.stdout, native.stderr)
        assert native.supervisor.final_process_group_quiescent is True
        assert "at most 2 CPUs (verified inherited cgroup quota)" in native.stderr
        with report.open("rb") as source:
            encoded = source.read(4 * 1024 * 1024 + 1)
        assert len(encoded) <= 4 * 1024 * 1024
        payload = json.loads(encoded)
        complete, passed, native_counts = vitest_outcomes(payload)
        assert complete and passed
        assert native_counts["numPassedTests"] == native_counts["numTotalTests"] > 0
        assert require_native_vitest_files(
            payload,
            project_root=FRONTEND,
            expected_files=targets,
            collection=collection,
        ) == set(targets)
        cases: list[tuple[str, str, str, str]] = []
        project_counts = {"node": 0, "dom": 0}
        for result in payload["testResults"]:
            path = "frontend/" + Path(result["name"]).relative_to(FRONTEND).as_posix()
            for assertion in result["assertionResults"]:
                cases.append(
                    (projects[path], path, assertion["fullName"], assertion["status"])
                )
                project_counts[projects[path]] += 1
        assert all(value > 0 for value in project_counts.values())
        signatures.append(sorted(cases))
        counts.append(native_counts)
    assert signatures[0] == signatures[1]
    assert counts[0] == counts[1]
