from __future__ import annotations

import json
from pathlib import Path

import pytest
from hashmarks import CodeMap

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.hashmarks.agent_tools import prompt_observation, repository_observation


def test_native_source_evidence_survives_bounded_prompt_disclosure(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("# " + "needle " * 100 + "\n")
    state = tmp_path / "evidence-state"
    with CodeMap(repo, state_dir=state, artifact_db=state / "artifacts.sqlite3") as native:
        native.index.sync()
    packet = repository_observation(
        tmp_path, tool="search_text", query="needle", capture_identity="candidate"
    )
    original = json.dumps(packet, sort_keys=True)
    disclosed = prompt_observation(json.dumps(packet))
    projection = json.loads(disclosed)
    assert len(disclosed) <= 2048
    assert projection["projection_coverage"] == "partial"
    assert projection["repository_absence"] == "not-claimed"
    assert projection["source"]["observation_identity"] == packet["source"]["observation_identity"]
    assert projection["source"]["freshness"] == packet["source"]["freshness"]
    assert sum(projection["omitted"].values()) > 0
    assert json.dumps(packet, sort_keys=True) == original


def test_empty_retrieval_never_becomes_a_repository_absence_claim(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("VALUE = 1\n")
    state = tmp_path / "evidence-state"
    with CodeMap(repo, state_dir=state, artifact_db=state / "artifacts.sqlite3") as native:
        native.index.sync()
    packet = repository_observation(
        tmp_path, tool="search_text", query="missing-literal", capture_identity="candidate"
    )
    projection = json.loads(prompt_observation(json.dumps(packet)))
    assert projection["repository_absence"] == "not-claimed"
    assert projection["source"] is None
    assert "match" not in projection


@pytest.mark.parametrize("output", ["{truncated", "{}", "[]"])
def test_incomplete_tool_output_cannot_become_model_evidence(output):
    with pytest.raises(FleetPlanInvalid):
        prompt_observation(output)
