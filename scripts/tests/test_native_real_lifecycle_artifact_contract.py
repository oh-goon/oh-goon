from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "backend/src/oh_goon/repository_tooling/native_real_lifecycle_artifact.py"
)


def test_native_artifact_gate_is_stdlib_only_and_runs_public_lifecycle_in_order() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    ast.parse(text)
    assert "backend/src" not in text
    assert "PYTHONPATH" not in text
    pyproject = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert (
        'oh-goon-native-real-lifecycle-artifact = "oh_goon.repository_tooling.native_real_lifecycle_artifact:main"'
        in pyproject
    )
    commands = [
        '_run(root, "make", "init")',
        '_run(root, "make", "local-state-prelaunch-check")',
        '_run(root, "make", "goon-static-admission")',
        '_run(root, "make", "test-real-lifecycle")',
        '_run(root, "make", "test-real-world")',
        '_run(root, "make", "certify")',
    ]
    assert '_run(root, "make", "startup-check")' not in text
    assert '_run(root, "make", "diagnostic-startup-check")' not in text
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)


def test_native_artifact_gate_supports_bootstrap_only_without_weakening_default() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"--bootstrap-only"' in text
    assert "if not args.bootstrap_only:" in text
    assert '_run(root, "make", "certify")' in text


def test_deep_dogfood_is_opt_in_public_cli_only_and_never_replaces_certification() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"--deep-dogfood"' in text
    assert '"--expected-sha256"' in text
    assert (
        "deep-dogfood artifact digest does not match the retained prepare-release receipt"
        in text
    )
    assert "if args.deep_dogfood:" in text
    assert 'cli = root / "backend/.venv/bin/oh-goon"' in text
    assert '_run_capture(root, str(cli), "self", "verify")' in text
    assert 'r"^Fleet Run ID:\\s*(\\S+)\\s*$"' in text
    assert '_run(root, str(cli), "dev", "explain-run", run_ids[0])' in text
    assert '_run(root, str(cli), "self", "certify")' in text
    assert "PYTHONPATH" not in text
    assert "backend/src" not in text
    assert '_run(root, "make", "certify")' in text
