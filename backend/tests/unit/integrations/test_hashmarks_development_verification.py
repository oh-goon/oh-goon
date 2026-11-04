from __future__ import annotations

from pathlib import Path
from shutil import copytree
from typing import Any, cast

import pytest
from hashmarks import CodeMap

from oh_goon.integrations.hashmarks.development_verification import (
    nominate_changed_verification,
)


@pytest.fixture
def nested_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repository"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    files = {
        "backend/pyproject.toml": (
            '[project]\nname = "nested-project"\nversion = "0.1.0"\n'
            '[tool.setuptools.package-dir]\n"" = "src"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        ),
        "backend/src/sample/__init__.py": "",
        "backend/src/sample/feature.py": "def answer():\n    return 42\n",
        "backend/src/sample/other.py": "def answer():\n    return 0\n",
        "backend/tests/test_feature.py": (
            "from sample.feature import answer\ndef test_answer():\n    assert answer() == 42\n"
        ),
        "backend/tests/test_other.py": (
            "from sample.other import answer\ndef test_answer():\n    assert answer() == 0\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_nested_python_qualified_nominations_survive_closed_map_reopen(
    nested_repository: Path,
) -> None:
    changed = ("backend/src/sample/feature.py", "backend/tests/test_feature.py")
    cold = nominate_changed_verification(nested_repository, changed)
    # Each public adapter call owns and closes its native map. Reuse the same
    # unchanged Git-free repository and cache without forcing reconciliation.
    reopened = nominate_changed_verification(nested_repository, changed)

    target = {"path": changed[1], "test_symbol": None}
    for packet in (cold, reopened):
        assert packet["owner_coverage"] == "not-claimed"
        assert [row["changed_path"] for row in packet["observations"]] == list(changed)
        linked, changed_test = packet["observations"]
        assert linked["state"] == "LINKED"
        assert linked["edit_candidates"] == [changed[0]]
        assert linked["qualification"]["qualified_identity_ambiguous"] is False
        assert linked["qualification"]["authority"] == (
            "bounded-index-evidence-not-verification-execution"
        )
        assert changed_test["state"] == "CHANGED_TEST"
        assert [row["relationship"] for row in linked["nominations"]] == ["direct-reference"]
        for observation in (linked, changed_test):
            nominations = observation["nominations"]
            # Equal symbol names in sample.other must not add its verifier, and
            # file-level evidence must not invent collected pytest selectors.
            assert [row["nomination"]["verification_target"] for row in nominations] == [target]
            nomination = nominations[0]["nomination"]
            assert nominations[0]["workspace_prefix"] == ""
            assert nomination["available"] is True
            assert nomination["scope"] == "test-file"
            assert nomination["authority"] == "repository-evidence-only"
            assert nomination["execution_authority"] == "external"
        assert (
            linked["nominations"][0]["nomination"] == changed_test["nominations"][0]["nomination"]
        )
    assert reopened == cold


def test_nested_manifest_change_rebinds_verifier_to_current_source_root(
    nested_repository: Path,
) -> None:
    copytree(nested_repository / "backend/src", nested_repository / "backend/alternate")
    original = "backend/src/sample/feature.py"
    alternate = "backend/alternate/sample/feature.py"
    initial = nominate_changed_verification(nested_repository, (original, alternate))
    before = {row["changed_path"]: row for row in initial["observations"]}
    assert before[original]["state"] == "LINKED"
    assert before[alternate]["state"] == "UNAVAILABLE"
    assert before[alternate]["nominations"] == []

    manifest = nested_repository / "backend/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"" = "src"', '"" = "alternate"'),
        encoding="utf-8",
    )
    # Only the declaring manifest changes. The adapter must use the current
    # qualified module owner when reopening the retained native map.
    refreshed = nominate_changed_verification(nested_repository, (original, alternate))
    after = {row["changed_path"]: row for row in refreshed["observations"]}

    assert refreshed["owner_coverage"] == "not-claimed"
    assert after[original]["state"] == "UNAVAILABLE"
    assert after[original]["nominations"] == []
    assert after[alternate]["state"] == "LINKED"
    nominations = after[alternate]["nominations"]
    assert [row["relationship"] for row in nominations] == ["direct-reference"]
    assert [row["nomination"]["verification_target"] for row in nominations] == [
        {"path": "backend/tests/test_feature.py", "test_symbol": None}
    ]
    assert (
        nominations[0]["nomination"]["source_member_revision"]
        == before[original]["nominations"][0]["nomination"]["source_member_revision"]
    )
    assert after[alternate]["qualification"]["authority"] == (
        "bounded-index-evidence-not-verification-execution"
    )


def test_public_nominations_keep_native_bounded_verifiers_and_residual_qualifications(
    nested_repository: Path,
) -> None:
    source = "backend/src/sample/feature.py"
    for suffix in ("secondary", "additional"):
        (nested_repository / f"backend/tests/test_feature_{suffix}.py").write_text(
            f"from sample.feature import answer\ndef test_{suffix}():\n    assert answer() == 42\n",
            encoding="utf-8",
        )
    with CodeMap(nested_repository) as native:
        native_primary = native.tasks.verification_ownership_graph(
            f"Verify {source}", limit=20, candidate_limit=1
        )
        native_candidates = native.tasks.verification_relevance(
            f"Verify {source}", limit=20, candidate_limit=16
        )
        native_graph = native.tasks.verification_ownership_graph(
            f"Verify {source}", limit=20, candidate_limit=2
        )
    native_owners = cast(list[dict[str, Any]], native_graph["verification_owners"])
    assert len(native_owners) == 2
    primary = cast(list[dict[str, Any]], native_primary["verification_owners"])[0]
    first_reference = native_candidates["candidates"][0]
    assert {primary["path"], first_reference["path"]} <= {row["path"] for row in native_owners}
    for row in native_owners:
        assert row["covers_edit_candidates"] == [source]
        assert row["coverage_evidence"] in {"direct-reference", "bounded-indirect-reference"}
    qualification = cast(dict[str, Any], native_graph["verification_qualification"])
    assert qualification["candidate_count"] > len(native_owners)

    packet = nominate_changed_verification(nested_repository, (source,))

    linked = packet["observations"][0]
    assert linked["state"] == "LINKED"
    assert linked["edit_candidates"] == native_graph["edit_candidates"]
    assert linked["qualification"] == qualification
    assert packet["owner_coverage"] == "not-claimed"
    assert len(linked["nominations"]) == 2
    assert [
        (row["nomination"]["verification_target"]["path"], row["relationship"])
        for row in linked["nominations"]
    ] == [(row["path"], row["coverage_evidence"]) for row in native_owners]
    assert all(
        row["nomination"]["execution_authority"] == "external" for row in linked["nominations"]
    )
