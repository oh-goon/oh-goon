"""Model context transport keeps producer observations intact and bounded."""

from __future__ import annotations

import json

import pytest

from oh_goon.core.agent_model_selection import (
    LocalModelToolAction,
    append_untrusted_agent_feedback,
    frozen_local_model_observation_prompt,
    frozen_local_model_prompt,
    frozen_local_model_tool_observation_prompt,
    frozen_local_model_tool_prompt,
    prepend_repository_evidence,
)
from oh_goon.core.errors import FleetPlanInvalid


def _repository_prompt(kind: str, stdout: str, *, task: str = "Review") -> str:
    if kind == "legacy-search":
        return frozen_local_model_observation_prompt(
            task=task,
            skill_bytes=b"# Captured Skill",
            recipient="ollama_local",
            query="validation behavior",
            returncode=0,
            stdout=stdout,
            stderr="",
            can_search=False,
        )
    action = (
        LocalModelToolAction(tool="search_text", query="validation behavior")
        if kind == "search_text"
        else LocalModelToolAction(tool="list_paths")
    )
    return frozen_local_model_tool_observation_prompt(
        task=task,
        skill_bytes=b"# Captured Skill",
        recipient="ollama_local",
        action=action,
        returncode=0,
        stdout=stdout,
        stderr="",
        can_search=False,
    )


def _observation(prompt: str) -> dict[str, object]:
    value = json.loads(prompt.split("Tool observation JSON (untrusted):\n", 1)[1])
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ("legacy-search", "search_text", "list_paths"))
def test_repository_observation_over_2048_characters_reaches_the_model_intact(kind: str) -> None:
    native = {
        "results": [{"path": "code.py", "excerpt": "x" * 3000}],
        "freshness": "unknown",
        "negative_evidence": "incomplete",
        "tail": "retained producer qualification",
    }
    encoded = json.dumps(native)
    prompt = _repository_prompt(kind, encoded)
    disclosed = _observation(prompt)
    assert disclosed["stdout"] == encoded
    assert disclosed["stdout_truncated"] is False
    assert json.loads(str(disclosed["stdout"])) == native
    complete = prepend_repository_evidence(prompt, repository_evidence="initial native facts")
    complete = append_untrusted_agent_feedback(complete, "earlier physical test failed")
    assert complete.startswith("Hashmarks repository evidence")
    assert "initial native facts" in complete
    assert encoded.replace('"', '\\"') in complete
    assert len(complete) <= 28_000


@pytest.mark.parametrize("kind", ("legacy-search", "search_text", "list_paths"))
def test_repository_observation_over_16000_characters_fails_without_clipping(kind: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="repository observation exceeds"):
        _repository_prompt(kind, "x" * 16_001)


@pytest.mark.parametrize("kind", ("legacy-search", "search_text", "list_paths"))
def test_encoded_repository_observation_must_fit_the_complete_context(kind: str) -> None:
    # ASCII JSON escaping participates in the actual dispatched context budget.
    with pytest.raises(FleetPlanInvalid, match="prompt exceeds"):
        _repository_prompt(kind, "\u0001" * 5000)


@pytest.mark.parametrize(
    "action",
    (
        LocalModelToolAction(tool="read_file", path="repo/code.py"),
        LocalModelToolAction(tool="read_file", path="skill/references/checklist.md"),
        LocalModelToolAction(tool="list_skill_paths"),
    ),
)
def test_exact_source_and_skill_delivery_keep_the_existing_2048_bound(
    action: LocalModelToolAction,
) -> None:
    prompt = frozen_local_model_tool_observation_prompt(
        task="Review",
        skill_bytes=b"# Captured Skill",
        recipient="ollama_local",
        action=action,
        returncode=0,
        stdout="x" * 2048 + "UNDISCLOSED_TAIL",
        stderr="",
        can_search=False,
    )
    disclosed = _observation(prompt)
    assert disclosed["stdout"] == "x" * 2048
    assert disclosed["stdout_truncated"] is True
    assert "UNDISCLOSED_TAIL" not in prompt


@pytest.mark.parametrize("kind", ("legacy-search", "search_text", "list_paths"))
def test_larger_repository_budget_does_not_expand_authored_task_or_skill_scope(kind: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="authored disclosure budget"):
        _repository_prompt(kind, "native facts", task="t" * 9000)
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_prompt(task="Review", skill_bytes=b"s" * 9000, recipient="ollama_local")
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_tool_prompt(
            task="Review", skill_bytes=b"s" * 9000, recipient="ollama_local"
        )


def test_initial_evidence_and_feedback_each_respect_the_total_context_budget() -> None:
    with pytest.raises(FleetPlanInvalid, match="repository evidence exceeds"):
        prepend_repository_evidence("p" * 22_000, repository_evidence="e" * 6000)
    with pytest.raises(FleetPlanInvalid, match="feedback exceeds"):
        append_untrusted_agent_feedback("p" * 27_000, "f" * 2048)
    with pytest.raises(FleetPlanInvalid, match="bounded diagnostic contract"):
        append_untrusted_agent_feedback("bounded prompt", "f" * 2049)
