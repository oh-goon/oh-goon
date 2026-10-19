from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_node_localhost_transport_regressions_pass_on_real_host() -> None:
    completed = subprocess.run(
        [
            "node",
            "--test",
            "e2e/browser-localhost-transport.test.mjs",
            "e2e/localhost-tcp-probe.test.mjs",
        ],
        cwd=FRONTEND,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_public_browser_entrypoints_own_transport_without_make_duplication() -> None:
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))[
        "scripts"
    ]
    for name in (
        "test:browser",
        "test:browser:chromium",
        "test:browser:accessibility",
        "test:browser:visibility",
        "test:visual",
        "test:visual:update",
        "browser:preflight",
        "browser:harness-preflight",
        "browser:runtime-preflight",
        "browser:check",
    ):
        assert scripts[name].startswith("npm run browser:transport-preflight && "), name
    assert scripts["playwright:test"] == "playwright test"
    assert scripts["browser:test"] == "node e2e/browser-certification-runner.mjs"
    assert scripts["browser:runtime"].startswith("npm run browser:preflight && ")

    release = scripts["browser:release"]
    assert release.count("browser:transport-preflight") == 1
    for duplicated_public_gate in (
        "browser:harness-preflight",
        "browser:preflight",
        "browser:runtime-preflight",
        "test:browser",
        "test:visual",
    ):
        assert f"npm run {duplicated_public_gate}" not in release
    assert "playwright test --list" in release
    assert "node e2e/browser-preflight.mjs" in release
    assert "node e2e/browser-runtime-preflight.mjs" in release
    assert "npm run browser:test --" in release
    assert (
        "playwright test --config=playwright.visual.config.ts --project=chromium"
        in release
    )

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "browser-preflight",
        "browser-harness-preflight",
        "browser-runtime-preflight",
    ):
        header = makefile.split(f"{target}:", 1)[1].split("\n", 1)[0]
        assert "browser-localhost-transport-preflight" not in header
