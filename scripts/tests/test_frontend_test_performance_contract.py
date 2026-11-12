from __future__ import annotations

from pathlib import Path


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
