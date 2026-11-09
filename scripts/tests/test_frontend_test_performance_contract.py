from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest


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
    """Declare the native Node/npm capability without materializing during collection."""


@pytest.mark.process
def test_native_node_compile_cache_executes_changed_source_bytes(
    tmp_path: Path, native_frontend_toolchain: None
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("initialized native Node frontend toolchain required")
    cache = tmp_path / "compile-cache"
    source = tmp_path / "subject.cjs"
    source.write_text('module.exports = "first source";\n')
    environment = {**os.environ, "NODE_COMPILE_CACHE": str(cache)}
    environment.pop("NODE_DISABLE_COMPILE_CACHE", None)

    def execute() -> dict[str, str]:
        native = subprocess.run(
            [
                node,
                "-e",
                "const {getCompileCacheDir}=require('node:module');console.log(JSON.stringify({value:require(process.argv[1]),cache:getCompileCacheDir(),version:process.version}));",
                str(source),
            ],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        payload = json.loads(native.stdout)
        assert isinstance(payload, dict)
        assert all(
            isinstance(payload[key], str) for key in ("value", "cache", "version")
        )
        return cast(dict[str, str], payload)

    first = execute()
    assert first["value"] == "first source"
    selected = Path(first["cache"])
    assert selected.is_relative_to(cache) and selected.is_dir()
    assert any(path.is_file() for path in selected.rglob("*"))
    assert execute() == first
    original_stat = source.stat()
    source.write_text('module.exports = "other source";\n')
    assert source.stat().st_size == original_stat.st_size
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    edited = execute()
    assert edited == {**first, "value": "other source"}
    cached_files = sorted(path for path in selected.rglob("*") if path.is_file())
    assert cached_files
    for cached_file in cached_files:
        with cached_file.open("r+b") as stream:
            stream.write(b"invalid-v8-cache")
    assert execute() == edited
    # No cache-layout or version-key algorithm is duplicated here. Native Node
    # chooses the runtime namespace and validates source bytes on each process.


@pytest.mark.process
def test_native_v8_coverage_disables_compile_cache_before_vitest_startup(
    tmp_path: Path, native_frontend_toolchain: None
) -> None:
    npm = shutil.which("npm")
    if npm is None or not (FRONTEND / "node_modules/vitest/vitest.mjs").is_file():
        pytest.skip("initialized locked native npm/Vitest toolchain required")
    package = json.loads((FRONTEND / "package.json").read_bytes())
    project = tmp_path / "coverage-project"
    project.mkdir()
    (project / "node_modules").symlink_to(
        FRONTEND / "node_modules", target_is_directory=True
    )
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "native-coverage-fixture",
                "private": True,
                "type": "module",
                "scripts": {"test:coverage": package["scripts"]["test:coverage"]},
            }
        )
    )
    (project / "vitest.config.mjs").write_text(
        'export default {test:{pool:"threads",fileParallelism:false,include:["subject.test.js"],coverage:{include:["subject.js"]}}};\n'
    )
    (project / "subject.js").write_text(
        'export function classify(value) { return value ? "selected" : "other"; }\n'
    )
    (project / "subject.test.js").write_text(
        'import {expect,test} from "vitest";import {classify} from "./subject.js";\n'
        'test("records fresh branch coverage",()=>{expect(classify(true)).toBe("selected");});\n'
    )
    events = tmp_path / "native-startup.jsonl"
    preload = tmp_path / "observe-native-startup.mjs"
    preload.write_text(
        'import {appendFileSync} from "node:fs";import {getCompileCacheDir} from "node:module";import {isMainThread} from "node:worker_threads";\n'
        f'appendFileSync({json.dumps(str(events))},JSON.stringify({{lifecycle:process.env.npm_lifecycle_event,disabled:process.env.NODE_DISABLE_COMPILE_CACHE,cache:getCompileCacheDir()??null,main:isMainThread}})+"\\n");\n'
    )
    base_environment = dict(os.environ)
    base_environment.pop("NODE_COMPILE_CACHE", None)
    base_environment.pop("NODE_DISABLE_COMPILE_CACHE", None)
    base_environment["NODE_OPTIONS"] = (
        base_environment.get("NODE_OPTIONS", "") + " --import=" + preload.as_uri()
    ).strip()
    summaries = []
    for name, environment in (
        ("baseline", base_environment),
        (
            "inherited-cache",
            {
                **base_environment,
                "NODE_COMPILE_CACHE": str(tmp_path / "inherited-cache"),
            },
        ),
    ):
        reports = tmp_path / name / "coverage-output"
        reports.mkdir(parents=True)
        (reports / "coverage-summary.json").write_text('{"stale":true}')
        observed = subprocess.run(
            [
                npm,
                "run",
                "test:coverage",
                "--",
                f"--coverage.reportsDirectory={reports}",
            ],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert observed.returncode == 0, observed.stdout + observed.stderr
        summaries.append(json.loads((reports / "coverage-summary.json").read_bytes()))
    assert summaries[0] == summaries[1]
    assert summaries[1]["total"]["statements"]["covered"] > 0
    assert (
        0
        < summaries[1]["total"]["branches"]["covered"]
        < summaries[1]["total"]["branches"]["total"]
    )
    startup = [json.loads(row) for row in events.read_text().splitlines()]
    coverage_processes = [
        row for row in startup if row.get("lifecycle") == "test:coverage"
    ]
    assert coverage_processes and any(
        row["main"] is False for row in coverage_processes
    )
    assert all(
        row["disabled"] == "1" and row["cache"] is None for row in coverage_processes
    )
