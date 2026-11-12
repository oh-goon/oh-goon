from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from hashmarks import CodeMap
from hashmarks.evidence_presentation import present_repository_evidence

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.hashmarks import (
    agent_evidence_main,
    prompt_observation,
    repository_observation,
    task_evidence_prompt,
)


@pytest.mark.process
def test_query_file_transport_delivers_private_task_without_argv_text(tmp_path: Path) -> None:
    import subprocess

    captured = tmp_path / "captured"
    (captured / "repo").mkdir(parents=True)
    source = captured / "repo" / "widget.py"
    original_source = "def handle_widget():\n    return 1\n"
    source.write_text(original_source)
    query = tmp_path / "query.txt"
    query.write_text("Review handle_widget, private-task-canary")
    argv = [
        sys.executable,
        "-I",
        "-m",
        "oh_goon.integrations.hashmarks",
        "--root",
        str(captured),
        "--query-file",
        str(query),
        "--tool",
        "task_evidence",
        "--capture-identity",
        "frozen",
    ]
    assert "private-task-canary" not in " ".join(argv)
    with (
        (tmp_path / "stdout.json").open("w+b") as output,
        (tmp_path / "stderr.txt").open("w+b") as errors,
    ):
        result = subprocess.run(
            argv, cwd=tmp_path, stdout=output, stderr=errors, timeout=30, check=False
        )
        output.seek(0)
        data = output.read(1_048_577)
        errors.seek(0)
        diagnostics = errors.read(65_537)
    assert len(data) <= 1_048_576
    assert len(diagnostics) <= 65_536
    assert result.returncode == 0, diagnostics.decode("utf-8", errors="replace")
    packet = json.loads(data)
    assert packet["capture_identity"] == "frozen"
    assert packet["producer"] == "Hashmarks"
    assert "handle_widget" in packet["presentation"]["text"]
    assert source.read_text() == original_source


@pytest.mark.parametrize("data", [b"x" * 65537, b"\xff", b"\x00", b"  "])
def test_invalid_query_file_is_rejected_before_index_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], data: bytes
) -> None:
    query = tmp_path / "query.txt"
    query.write_bytes(data)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent_tools",
            "--root",
            str(tmp_path),
            "--query-file",
            str(query),
            "--tool",
            "task_evidence",
            "--capture-identity",
            "frozen",
        ],
    )
    assert agent_evidence_main() == 65
    assert "unavailable" in capsys.readouterr().out
    assert not (tmp_path / "evidence-state").exists()


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


def test_native_followup_can_keep_index_state_outside_read_only_capture(tmp_path: Path) -> None:
    captured = tmp_path / "captured"
    repo = captured / "repo"
    repo.mkdir(parents=True)
    source = repo / "code.py"
    original = b"def handle_widget():\n    return 1\n"
    source.write_bytes(original)
    source.chmod(0o400)
    repo.chmod(0o500)
    captured.chmod(0o500)
    state = tmp_path / "disposable-index"
    try:
        packet = repository_observation(
            captured,
            tool="search_text",
            query="handle_widget",
            capture_identity="captured-source",
            state_directory=state,
        )
        disclosed = json.loads(
            prompt_observation(json.dumps(packet), expected_capture_identity="captured-source")
        )
        assert disclosed["producer"] == "Hashmarks"
        assert disclosed["capture_identity"] == "captured-source"
        assert disclosed["retrieval"] == present_repository_evidence(
            packet["retrieval"], format="compact"
        )
        assert state.is_dir()
        assert not (captured / "evidence-state").exists()
        assert source.read_bytes() == original
    finally:
        captured.chmod(0o700)
        repo.chmod(0o700)


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


def _correlation_request(path: str, **claims: object) -> dict[str, Any]:
    return {
        "bundles": [
            {
                "bundle_id": "native-failure",
                "producer": {"kind": "diagnostic"},
                "scope": {"run": "original-run", "step": "verify", "attempt": 1},
                "completeness": "incomplete",
                "truncation": "truncated",
                "anchors": [{"anchor_id": "frame", "path": path, "line": 2, **claims}],
            }
        ],
        "path_mappings": [{"external_prefix": "/runtime", "repository_prefix": ""}],
        "include_relationships": False,
    }


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("worker.py", "def worker():\n    return 1\n"),
        ("worker.ts", "export function worker() {\n    return 1;\n}\n"),
    ],
)
def test_diagnostic_correlation_preserves_source_equivalence_and_original_observation(
    tmp_path: Path, name: str, source: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    member = repo / name
    member.write_text(source, encoding="utf-8")
    request = _correlation_request("/runtime/" + name)

    def observe(capture: str) -> dict[str, Any]:
        return repository_observation(
            tmp_path,
            tool="correlate_evidence",
            query=json.dumps(request),
            capture_identity=capture,
        )

    unknown = observe("first-capture")["correlation"]
    anchor = unknown["bundles"][0]["anchors"][0]
    assert anchor["resolution"]["repository_path"] == name
    assert anchor["resolution"]["state"] == "resolved-unique"
    assert anchor["source_equivalence"]["state"] == "unknown"
    evidence = unknown["repository_evidence"]["bindings"][0]["evidence"][0]
    request["bundles"][0]["anchors"][0]["member_revision"] = evidence["member_revision"]
    original = observe("first-capture")
    before = json.dumps(original, sort_keys=True)
    correlation = original["correlation"]
    assert correlation["bundles"][0]["anchors"][0]["source_equivalence"]["state"] == "proven"
    assert correlation["bundles"][0]["scope"] == request["bundles"][0]["scope"]
    assert correlation["bundles"][0]["truncation"] == "truncated"
    assert correlation["completeness"]["state"] == "incomplete"
    assert correlation["completeness"]["negative_evidence"] == "not-admissible"
    assert correlation["causation"] == "not-inferred"
    assert correlation["execution_effect"] == "none"
    projection = json.loads(
        prompt_observation(json.dumps(original), expected_capture_identity="first-capture")
    )
    assert projection["correlation"] == present_repository_evidence(correlation, format="compact")
    with pytest.raises(FleetPlanInvalid, match="capture differs"):
        prompt_observation(json.dumps(original), expected_capture_identity="different-capture")
    with pytest.raises(FleetPlanInvalid, match="exceeds the prompt budget"):
        prompt_observation(
            json.dumps(original), expected_capture_identity="first-capture", max_chars=100
        )

    member.write_text(source.replace("return 1", "return 2"), encoding="utf-8")
    changed = observe("second-capture")["correlation"]
    assert changed["bundles"][0]["anchors"][0]["source_equivalence"]["state"] == "mismatch"
    assert json.dumps(original, sort_keys=True) == before
    member.unlink()
    deleted = observe("third-capture")["correlation"]
    assert deleted["bundles"][0]["anchors"][0]["resolution"]["state"] == "unresolved"


def test_diagnostic_correlation_preserves_ambiguity_and_distinct_nearby_locations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "left.py").write_text("def same():\n    return 1\n\ndef unrelated():\n    return 2\n")
    (repo / "right.py").write_text("def same():\n    return 3\n")
    request = _correlation_request("/runtime/left.py")
    request["bundles"][0]["anchors"] = [
        {"anchor_id": "ambiguous", "symbol": "same"},
        {"anchor_id": "failure", "path": "/runtime/left.py", "line": 2},
        {"anchor_id": "nearby", "path": "/runtime/left.py", "line": 5},
        {"anchor_id": "missing", "path": "/runtime/deleted.py", "line": 2, "symbol": "same"},
    ]
    packet = repository_observation(
        tmp_path,
        tool="correlate_evidence",
        query=json.dumps(request),
        capture_identity="capture",
    )["correlation"]
    anchors = {row["anchor_id"]: row for row in packet["bundles"][0]["anchors"]}
    assert anchors["ambiguous"]["resolution"]["state"] == "resolved-ambiguous"
    assert {row["path"] for row in anchors["ambiguous"]["resolution"]["candidates"]} == {
        "left.py",
        "right.py",
    }
    assert anchors["failure"]["resolution"]["symbol"]["name"] == "same"
    assert anchors["nearby"]["resolution"]["symbol"]["name"] == "unrelated"
    assert anchors["missing"]["resolution"]["state"] == "unresolved"
    assert all(row["source_equivalence"]["state"] == "unknown" for row in anchors.values())
    assert packet["causation"] == "not-inferred"


@pytest.mark.parametrize("query", ["{invalid", "[]", '{"unknown": true}', '{"bundles": null}'])
def test_invalid_diagnostic_request_is_rejected(tmp_path: Path, query: str) -> None:
    (tmp_path / "repo").mkdir()
    with pytest.raises(ValueError):
        repository_observation(
            tmp_path, tool="correlate_evidence", query=query, capture_identity="capture"
        )


@pytest.mark.process
def test_diagnostic_query_file_uses_existing_native_tool_transport(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "captured"
    (root / "repo").mkdir(parents=True)
    member = root / "repo" / "worker.py"
    member.write_text("def worker():\n    return 1\n")
    request = tmp_path / "diagnostic.json"
    request.write_text(json.dumps(_correlation_request("/runtime/worker.py")))
    with (
        (tmp_path / "correlation.json").open("w+b") as output,
        (tmp_path / "correlation-errors.txt").open("w+b") as errors,
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "oh_goon.integrations.hashmarks",
                "--root",
                str(root),
                "--state-directory",
                str(tmp_path / "state"),
                "--tool",
                "correlate_evidence",
                "--query-file",
                str(request),
                "--capture-identity",
                "original-capture",
            ],
            cwd=tmp_path,
            stdout=output,
            stderr=errors,
            timeout=30,
            check=False,
        )
        output.seek(0)
        data = output.read(1_048_577)
        errors.seek(0)
        diagnostics = errors.read(65_537)
    assert len(diagnostics) <= 65_536
    assert result.returncode == 0, diagnostics.decode("utf-8", errors="replace")
    assert len(data) <= 1_048_576
    packet = json.loads(data)
    assert packet["capture_identity"] == "original-capture"
    assert (
        packet["correlation"]["bundles"][0]["anchors"][0]["resolution"]["repository_path"]
        == "worker.py"
    )
    assert packet["presentation"] == present_repository_evidence(
        packet["correlation"], format="compact"
    )
    assert member.read_text() == "def worker():\n    return 1\n"
