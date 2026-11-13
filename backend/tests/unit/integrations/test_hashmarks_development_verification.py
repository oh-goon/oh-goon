from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
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
        assert linked["changed_set"]["changed_paths"] == [changed[0]]
        assert linked["qualification"]["qualified_identity_ambiguous"] is False
        assert linked["changed_set"]["authority"] == "repository-evidence-only"
        assert linked["qualification"]["negative_evidence_admissible"] is False
        assert changed_test["state"] == "CHANGED_TEST"
        assert [
            row["relation_kind"] for row in linked["changed_set"]["changes"][0]["relationships"]
        ] == ["direct-reference"]
        for observation in (linked, changed_test):
            nominations = observation["nominations"]
            # Equal symbol names in sample.other must not add its verifier, and
            # file-level evidence must not invent collected pytest selectors.
            assert [row["nomination"]["verification_target"] for row in nominations] == [target]
            nomination = nominations[0]["nomination"]
            assert "context" not in nominations[0] and "relationships" not in nominations[0]
            native_target = observation["changed_set"]["targets"][0]
            assert native_target["nomination"] == nomination and native_target["context"]
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
    assert [
        row["relation_kind"]
        for row in after[alternate]["changed_set"]["changes"][0]["relationships"]
    ] == ["direct-reference"]
    assert [row["nomination"]["verification_target"] for row in nominations] == [
        {"path": "backend/tests/test_feature.py", "test_symbol": None}
    ]
    assert (
        nominations[0]["nomination"]["source_member_revision"]
        == before[original]["nominations"][0]["nomination"]["source_member_revision"]
    )
    assert after[alternate]["changed_set"]["authority"] == "repository-evidence-only"


def test_native_multi_edit_rename_preserves_shared_verifier_and_member_qualifications(
    nested_repository: Path,
) -> None:
    from oh_goon.integrations.hashmarks import (
        capture_source_member,
    )
    from oh_goon.integrations.hashmarks import (
        nominate_changed_verification as observe_verification,
    )

    original = "backend/src/sample/feature.py"
    renamed = "backend/src/sample/renamed.py"
    other = "backend/src/sample/other.py"
    shared = "backend/tests/test_combined.py"
    (nested_repository / shared).write_text(
        "from sample.feature import answer as first\n"
        "from sample.other import answer as second\n"
        "def test_combined():\n    assert first() + second() == 42\n",
        encoding="utf-8",
    )

    before = observe_verification(nested_repository, (original, other))
    initial = {row["changed_path"]: row for row in before["observations"]}
    shared_target = {"path": shared, "test_symbol": None}
    previous_verifier = capture_source_member(nested_repository, shared).packet()
    for path in (original, other):
        observation = initial[path]
        change = observation["changed_set"]["changes"][0]
        assert observation["state"] == "LINKED"
        assert change["source"]["member"]["state"] == "known-present"
        assert observation["qualification"] == change["consumer_scope"]
        assert observation["qualification"]["negative_evidence_admissible"] is False
        assert shared_target in [
            row["nomination"]["verification_target"] for row in observation["nominations"]
        ]
        assert [
            row["nomination"]["source_member_revision"]
            for row in observation["nominations"]
            if row["nomination"]["verification_target"] == shared_target
        ] == [previous_verifier["member_revision"]]

    (nested_repository / original).write_text("def answer():\n    return 40\n", encoding="utf-8")
    (nested_repository / other).write_text("def answer():\n    return 2\n", encoding="utf-8")
    (nested_repository / original).rename(nested_repository / renamed)
    (nested_repository / "backend/tests/test_feature.py").write_text(
        "from sample.renamed import answer\ndef test_answer():\n    assert answer() == 40\n",
        encoding="utf-8",
    )
    (nested_repository / "backend/tests/test_other.py").write_text(
        "from sample.other import answer\ndef test_answer():\n    assert answer() == 2\n",
        encoding="utf-8",
    )
    shared_verifier = nested_repository / shared
    shared_verifier.write_text(
        shared_verifier.read_text(encoding="utf-8").replace("sample.feature", "sample.renamed"),
        encoding="utf-8",
    )

    after = observe_verification(nested_repository, (original, renamed, other))
    current = {row["changed_path"]: row for row in after["observations"]}
    assert list(current) == [original, renamed, other]
    assert (
        current[original]["changed_set"]["changes"][0]["source"]["member"]["state"]
        == "known-absent"
    )
    assert current[original]["state"] == "UNAVAILABLE"
    assert current[original]["nominations"] == []
    current_verifier = capture_source_member(nested_repository, shared).packet()
    assert current_verifier["member_revision"] != previous_verifier["member_revision"]
    shared_nominations = []
    for path in (renamed, other):
        observation = current[path]
        change = observation["changed_set"]["changes"][0]
        assert observation["state"] == "LINKED"
        assert change["source"]["member"]["state"] == "known-present"
        previous = initial[original if path == renamed else path]
        assert (
            change["source"]["member"]["member_revision"]
            != previous["changed_set"]["changes"][0]["source"]["member"]["member_revision"]
        )
        assert observation["qualification"] == change["consumer_scope"]
        assert observation["qualification"]["negative_evidence_admissible"] is False
        nominations = [
            row["nomination"]
            for row in observation["nominations"]
            if row["nomination"]["verification_target"] == shared_target
        ]
        assert len(nominations) == 1
        assert nominations[0]["authority"] == "repository-evidence-only"
        assert nominations[0]["execution_authority"] == "external"
        assert nominations[0]["source_member_revision"] == current_verifier["member_revision"]
        shared_nominations.append(nominations[0])
    assert shared_nominations[0] == shared_nominations[1]
    assert before["owner_coverage"] == after["owner_coverage"] == "not-claimed"
    assert observe_verification(nested_repository, (original, renamed, other)) == after


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
        expected = cast(dict[str, Any], native.verification.changed_set((source,), limit=2))
    assert expected["truncated"] is True
    assert expected["changes"][0]["consumer_scope"]["omitted_reference_candidates"] > 0
    packet = nominate_changed_verification(nested_repository, (source,))
    linked = packet["observations"][0]
    assert linked["state"] == "LINKED"
    assert linked["changed_set"] == expected
    assert linked["qualification"]["completeness"] == "incomplete"
    assert linked["qualification"]["negative_evidence_admissible"] is False
    assert packet["owner_coverage"] == "not-claimed"
    assert [row["nomination"] for row in linked["nominations"]] == [
        row["nomination"] for row in expected["targets"]
    ]
    assert all(
        row["nomination"]["execution_authority"] == "external" for row in linked["nominations"]
    )


def test_verification_support_keeps_its_needed_relationship_view_and_intact_packet(
    nested_repository: Path,
) -> None:
    manifest = nested_repository / "backend/pyproject.toml"
    manifest.write_text(manifest.read_text() + 'pythonpath = ["."]\n')
    source = "backend/tests/factories/support.py"
    member = nested_repository / source
    member.parent.mkdir(parents=True)
    member.write_text("def build_value():\n    return 42\n")
    (nested_repository / "backend/tests/test_support.py").write_text(
        "from tests.factories.support import build_value\n"
        "def test_support():\n    assert build_value() == 42\n"
    )

    evidence = nominate_changed_verification(nested_repository, (source,))

    observation = evidence["observations"][0]
    change = observation["changed_set"]["changes"][0]
    assert observation["state"] == "LINKED"
    assert change["classification"]["kind"] == "verification-support"
    assert observation["qualification"] == change["consumer_scope"]
    assert observation["qualification"]["completeness"] in {"incomplete", "unknown"}
    assert observation["qualification"]["negative_evidence_admissible"] is False
    for projected in observation["nominations"]:
        target = projected["nomination"]["verification_target"]["path"]
        assert "context" not in projected
        assert projected["relationships"] == [
            relation for relation in change["relationships"] if relation["target"] == target
        ]
        assert projected["relationships"]
        assert any(
            row["path"] == target and row["context"]
            for row in observation["changed_set"]["targets"]
        )


def test_native_task_preparation_refreshes_contributors_before_composite_observation(
    nested_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "backend/src/sample/feature.py"
    verifier = "backend/tests/test_feature.py"
    with CodeMap(nested_repository) as native:
        baseline = native.verification.changed_set((source,), limit=2)
        assert baseline["changed_paths"] == [source]
        assert native.index.is_current(source)
        (nested_repository / source).write_text("def answer():\n    return 43\n", encoding="utf-8")
        assert not native.index.is_current(source)
        prepare = native.tasks.prepare
        observed: list[tuple[str, int, bool, bool]] = []

        def track_preparation(
            task: str, limit: int, *, probe_on_miss: bool = True
        ) -> tuple[str, ...]:
            before = native.index.is_current(source)
            depth = native.reads.depth
            result = prepare(task, limit, probe_on_miss=probe_on_miss)
            observed.append((task, depth, before, native.index.is_current(source)))
            return result

        def selected_map(workspace: Path, **_options: object) -> AbstractContextManager[CodeMap]:
            assert workspace == nested_repository
            return nullcontext(native)

        monkeypatch.setattr(native.tasks, "prepare", track_preparation)
        monkeypatch.setattr("hashmarks.CodeMap", selected_map)
        evidence = nominate_changed_verification(
            nested_repository, (verifier, source), required=(verifier,)
        )
        assert observed and all(depth == 0 and after for _task, depth, _before, after in observed)
        assert native.index.is_current(source)
    assert [row["state"] for row in evidence["observations"]] == ["CHANGED_TEST", "LINKED"]
    assert [row["changed_path"] for row in evidence["observations"]] == [verifier, source]
    assert len(evidence["required_checks"]) == 1
    assert evidence["owner_coverage"] == "not-claimed"
