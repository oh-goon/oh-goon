from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_browser_filesystem_materialization_oracle_is_removed() -> None:
    assert not (FRONTEND / "e2e/browser-materialization-check.mjs").exists()
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "browser:materialization-check" not in scripts
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "browser-materialization-check" not in makefile


def test_browser_runner_uses_npm_package_bin_authority_not_playwright_internal_layout() -> None:
    runner = (FRONTEND / "e2e/browser-certification-runner.mjs").read_text(encoding="utf-8")
    assert '["run", "playwright:test", "--", ...process.argv.slice(2)]' in runner
    assert "node_modules/@playwright/test/cli.js" not in runner
    assert 'join(frontendRoot, "node_modules"' not in runner
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts["playwright:test"] == "playwright test"


def test_browser_preflight_uses_actual_playwright_launch_as_semantic_readiness() -> None:
    preflight = (FRONTEND / "e2e/browser-preflight.mjs").read_text(encoding="utf-8")
    assert "browserType.launch(" in preflight
    assert "fs.statSync" not in preflight
    assert "X_OK" not in preflight
    assert ".executablePath()" not in preflight
