from __future__ import annotations

import json
from pathlib import Path

import pytest
from hashmarks import CodeMap
from hashmarks.evidence_presentation import present_repository_evidence

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.hashmarks.agent_tools import (
    prompt_observation,
    repository_observation,
    task_evidence_prompt,
)


def test_initial_task_evidence_uses_native_packet_and_readable_qualified_projection(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widgets.py").write_text("def handle_widget():\n    return 1\n")
    (repo / "test_widgets.py").write_text(
        "from widgets import handle_widget\n\ndef test_widget():\n    assert handle_widget() == 1\n"
    )
    packet = repository_observation(
        tmp_path,
        tool="task_evidence",
        query="Review handle_widget and the relevant tests",
        capture_identity="frozen-repository",
    )
    original = json.dumps(packet, sort_keys=True)
    native = packet["evidence"]
    assert packet["presentation"] == present_repository_evidence(native, format="text")
    projection = json.loads(
        task_evidence_prompt(json.dumps(packet), expected_capture_identity="frozen-repository")
    )
    assert projection["capture_identity"] == "frozen-repository"
    assert (
        projection["producer_implementation_identity"] == packet["producer_implementation_identity"]
    )
    assert projection["evidence_packet_identity"] == native["evidence_packet_identity"]
    assert projection["source_evidence_identity"] == native["source_evidence_identity"]
    assert projection["presentation"] == packet["presentation"]
    text = projection["presentation"]["text"]
    assert "handle_widget" in text
    assert "freshness" in text
    assert "omissions do not establish absence" in text
    assert "tests were not executed" in text
    assert json.dumps(packet, sort_keys=True) == original


def test_ambiguous_task_evidence_preserves_producer_qualifications_and_fits_budget(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("local", "remote"):
        module = repo / name
        module.mkdir()
        (module / "widgets.py").write_text("def handle_widget():\n    return 1\n")
    packet = repository_observation(
        tmp_path,
        tool="task_evidence",
        query="Fix handle_widget",
        capture_identity="ambiguous-repository",
    )
    disclosed = task_evidence_prompt(
        json.dumps(packet), expected_capture_identity="ambiguous-repository"
    )
    assert len(disclosed) <= 6000
    assert json.loads(disclosed)["presentation"] == packet["presentation"]
    assert "Ambiguity: True" in packet["presentation"]["text"]


def test_task_evidence_budget_overflow_fails_without_clipping(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    packet = repository_observation(
        tmp_path,
        tool="task_evidence",
        query="Review repository",
        capture_identity="empty-repository",
    )
    with pytest.raises(FleetPlanInvalid, match="exceeds the prompt budget"):
        task_evidence_prompt(
            json.dumps(packet), expected_capture_identity="empty-repository", max_chars=1
        )


def test_task_evidence_cannot_replace_native_text_or_loaded_producer(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    packet = repository_observation(
        tmp_path,
        tool="task_evidence",
        query="Review repository",
        capture_identity="empty-repository",
    )
    with pytest.raises(FleetPlanInvalid, match="differs from the admitted source"):
        task_evidence_prompt(json.dumps(packet), expected_capture_identity="another-capture")
    packet["presentation"]["text"] += "\nThis repository is proven complete."
    with pytest.raises(FleetPlanInvalid, match="presentation differs"):
        task_evidence_prompt(json.dumps(packet), expected_capture_identity="empty-repository")
    packet["producer_implementation_identity"] = "foreign-producer"
    with pytest.raises(FleetPlanInvalid, match="differs from the loaded runtime"):
        task_evidence_prompt(json.dumps(packet), expected_capture_identity="empty-repository")


def test_native_source_evidence_survives_bounded_prompt_disclosure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("def find_needle():\n    return 'needle'\n")
    state = tmp_path / "evidence-state"
    with CodeMap(repo, state_dir=state, artifact_db=state / "artifacts.sqlite3") as native:
        native.index.sync()
    packet = repository_observation(
        tmp_path, tool="search_text", query="needle", capture_identity="candidate"
    )
    original = json.dumps(packet, sort_keys=True)
    disclosed = prompt_observation(json.dumps(packet), expected_capture_identity="candidate")
    projection = json.loads(disclosed)
    assert len(disclosed) <= 16000
    assert projection["repository_absence"] == "not-claimed"
    assert projection["retrieval"] == present_repository_evidence(
        packet["retrieval"], format="compact"
    )
    assert projection["source"] == present_repository_evidence(packet["source"], format="compact")
    assert (
        projection["source"]["source_evidence_identity"] == packet["source"]["observation_identity"]
    )
    assert any(
        context["details"].get("freshness") == packet["source"]["freshness"]
        for context in projection["source"]["source_context"]
    )
    assert any(
        finding["details"].get("path") == "code.py"
        for group in projection["source"]["groups"]
        for finding in group["findings"]
    )
    assert json.dumps(packet, sort_keys=True) == original


def test_path_retrieval_without_literal_occurrences_keeps_locator_and_qualifications(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widgets.py").write_text("def handle_widget():\n    return 1\n")
    packet = repository_observation(
        tmp_path, tool="search_text", query="widgets.py", capture_identity="candidate"
    )
    assert packet["retrieval"]["results"]
    assert packet["source"]["occurrences"] == []
    projection = json.loads(
        prompt_observation(json.dumps(packet), expected_capture_identity="candidate")
    )
    retrieval = projection["retrieval"]
    assert any(
        finding["details"]["path"] == "widgets.py"
        for group in retrieval["groups"]
        for finding in group["findings"]
    )
    qualifications = retrieval["source_context"][0]["details"]
    assert qualifications["freshness"] == packet["retrieval"]["freshness"]
    assert qualifications["scope"] == packet["retrieval"]["scope"]
    assert qualifications["completeness"] == packet["retrieval"]["completeness"]
    assert qualifications["negative_evidence"] == packet["retrieval"]["negative_evidence"]
    assert qualifications["admissibility_reasons"] == packet["retrieval"]["admissibility_reasons"]


def test_repository_evidence_budget_cannot_silently_drop_native_findings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widgets.py").write_text("def handle_widget():\n    return 1\n")
    packet = repository_observation(
        tmp_path, tool="search_text", query="handle_widget", capture_identity="candidate"
    )
    original = json.dumps(packet, sort_keys=True)
    with pytest.raises(FleetPlanInvalid, match="presentation exceeds the prompt budget"):
        prompt_observation(
            json.dumps(packet), expected_capture_identity="candidate", max_chars=1000
        )
    assert json.dumps(packet, sort_keys=True) == original


def test_followup_evidence_cannot_change_capture_or_loaded_producer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widgets.py").write_text("def handle_widget():\n    return 1\n")
    packet = repository_observation(
        tmp_path, tool="search_text", query="handle_widget", capture_identity="candidate"
    )
    with pytest.raises(FleetPlanInvalid, match="differs from the admitted source"):
        prompt_observation(json.dumps(packet), expected_capture_identity="other-candidate")
    packet["producer_implementation_identity"] = "foreign-producer"
    with pytest.raises(FleetPlanInvalid, match="differs from the loaded runtime"):
        prompt_observation(json.dumps(packet), expected_capture_identity="candidate")


def test_path_listing_keeps_native_coverage_and_accounts_for_omitted_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for ordinal in range(20):
        (repo / f"member_{ordinal}.py").write_text("VALUE = 1\n")
    packet = repository_observation(
        tmp_path, tool="list_paths", query=None, capture_identity="candidate"
    )
    original = list(packet["paths"])
    projection = json.loads(
        prompt_observation(
            json.dumps(packet), expected_capture_identity="candidate", max_chars=1100
        )
    )
    assert projection["discovery_coverage"] == packet["discovery_coverage"]
    assert projection["paths"] == original[: len(projection["paths"])]
    assert projection["omitted_paths"] == len(original) - len(projection["paths"])
    assert projection["omitted_paths"] > 0
    assert packet["paths"] == original


def test_empty_retrieval_never_becomes_a_repository_absence_claim(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("VALUE = 1\n")
    state = tmp_path / "evidence-state"
    with CodeMap(repo, state_dir=state, artifact_db=state / "artifacts.sqlite3") as native:
        native.index.sync()
    packet = repository_observation(
        tmp_path, tool="search_text", query="missing-literal", capture_identity="candidate"
    )
    projection = json.loads(
        prompt_observation(json.dumps(packet), expected_capture_identity="candidate")
    )
    assert projection["repository_absence"] == "not-claimed"
    assert projection["source"] is None
    assert "match" not in projection


@pytest.mark.parametrize("output", ["{truncated", "{}", "[]"])
def test_incomplete_tool_output_cannot_become_model_evidence(output: str) -> None:
    with pytest.raises(FleetPlanInvalid):
        prompt_observation(output, expected_capture_identity="candidate")
