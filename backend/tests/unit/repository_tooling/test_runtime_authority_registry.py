from __future__ import annotations

import json
from pathlib import Path

from oh_goon.repository_tooling.architecture.structural_launch_contract import (
    _check_runtime_authority_registry,
)

ROOT = Path(__file__).resolve().parents[4]


def test_runtime_authority_registry_classifies_every_physical_owner() -> None:
    assert _check_runtime_authority_registry(ROOT) == []


def test_runtime_authority_registry_names_semantic_domains_not_common_helper() -> None:
    payload = json.loads(
        (ROOT / "docs/development/RUNTIME_AUTHORITY_REGISTRY.json").read_text(
            encoding="utf-8"
        )
    )
    domains = set(payload["owners"].values())
    assert "local-launch-process-lifecycle" in domains
    assert "admitted-step-command-launch-lifecycle" in domains
    assert "admitted-step-command-process-tree-lifecycle" in domains
    assert "repository-workflow-frontdoor-lifecycle" in domains
    assert "opensandbox-isolation-lifecycle" in domains
    assert "release-qualification-harness" in domains
    assert len(domains) >= 7
