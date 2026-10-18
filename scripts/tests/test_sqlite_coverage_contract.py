from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
SURFACE = yaml.safe_load(
    (ROOT / "docs" / "development" / "MAKE_TARGET_SURFACE.yml").read_text(encoding="utf-8")
)


def _target_body(name: str) -> str:
    marker = f"{name}:"
    start = MAKEFILE.index(marker) + len(marker)
    start = MAKEFILE.index("\n", start) + 1
    remainder = MAKEFILE[start:]
    end = remainder.find("\n\n")
    return remainder if end < 0 else remainder[:end]


def test_sqlite_coverage_target_is_frozen_owner_local_validation() -> None:
    owner_targets = SURFACE["noncanonical_make_target_classes"]["owner-local-validation"]
    assert "test-sqlite-coverage" in owner_targets


def test_sqlite_coverage_target_ratcheted_authority_and_resource_hygiene() -> None:
    body = _target_body("test-sqlite-coverage")

    assert ".venv/bin/oh-goon-qualification-exec pytest" in body
    assert "uv run" not in body
    assert "-W error::ResourceWarning" in body
    assert "--cov=oh_goon.runtime.database" in body
    assert "--cov=oh_goon.runtime.execution.sqlite_lease" in body
    assert "--cov=oh_goon.runtime._sqlite_access_diagnostics" in body
    assert "--cov-branch --cov-fail-under=90" in body
    assert '$(STATE_HOME)/oh-goon/coverage/sqlite-core.json' in body
