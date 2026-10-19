"""The effect boundary consumes immutable source without authoring services."""

from __future__ import annotations

import builtins
import hashlib
from pathlib import Path

import pytest

from oh_goon.core.models.agent_source import (
    FrozenAgentSourceCapture,
    FrozenAgentStepHandoff,
    FrozenSourceMember,
    FrozenSourceTree,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.integrations.fleet_plan_step_execution import prepare_delivered_readonly_skill_search


def _tree(kind: str, path: str, data: bytes) -> FrozenSourceTree:
    member = FrozenSourceMember(path=path, sha256=hashlib.sha256(data).hexdigest(), data=data)
    identity = stable_id(
        "agent-source-closure-v1",
        {
            "members": [
                {"path": path, "sha256": member.sha256, "bytes": len(data)},
            ]
        },
    )
    return FrozenSourceTree(kind=kind, content_identity=identity, members=(member,))


def test_delivered_source_preparation_never_imports_authoring_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing capture ownership must not pull product services into execution."""
    handoff = FrozenAgentStepHandoff(
        source=FrozenAgentSourceCapture(
            plan_source_sha256="a" * 64,
            plan_graph_identity="original-plan-graph",
            acceptance_profile="review",
            repository_reference="local:unavailable/repository",
            resolved_skill_reference="local:unavailable/skill",
            authored_skill_reference="review-skill",
            repository=_tree("repository", "source.txt", b"frozen search target\n"),
            skill=_tree("skill", "SKILL.md", b"# Read-only review\n"),
        ),
        owner_source_sha256="b" * 64,
        step_id="review",
        harness="local-search",
        task="search:target",
    )
    packet = handoff.encode_prework_delivery()
    original_import = builtins.__import__

    def reject_service_import(name: str, *args, **kwargs):
        if name == "oh_goon.services" or name.startswith("oh_goon.services."):
            raise AssertionError("execution attempted an upward authoring-service import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_service_import)
    with prepare_delivered_readonly_skill_search(
        packet,
        expected_handoff_identity=handoff.handoff_identity,
        expected_step_id="review",
    ) as (root, step):
        assert (root / "repo/source.txt").read_bytes() == b"frozen search target\n"
        assert (root / "skill/SKILL.md").read_bytes() == b"# Read-only review\n"
        assert step.step_id == handoff.step_id
        assert handoff.encode_prework_delivery() == packet
        materialized_root = Path(root)
    assert not materialized_root.exists()
