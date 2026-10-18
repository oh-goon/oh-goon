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


def test_journal_coverage_target_is_frozen_owner_local_validation() -> None:
    owner_targets = SURFACE["noncanonical_make_target_classes"]["owner-local-validation"]
    assert "test-journal-coverage" in owner_targets


def test_journal_coverage_targets_durable_fact_authority_not_read_projection() -> None:
    body = _target_body("test-journal-coverage")

    assert ".venv/bin/oh-goon-qualification-exec pytest" in body
    assert "uv run" not in body
    assert "-W error::ResourceWarning" in body
    assert "--cov=oh_goon.runtime.journal" in body
    assert "--cov=oh_goon.runtime.journal_store" in body
    assert "--cov=oh_goon.runtime.journal_index" in body
    assert "--cov=oh_goon.runtime.journal_codec" in body
    assert "--cov=oh_goon.runtime.journal_facts" in body
    assert "--cov=oh_goon.runtime.journal_view" not in body
    assert "--cov-branch --cov-fail-under=93" in body
    assert '$(STATE_HOME)/oh-goon/coverage/journal-durability.json' in body
