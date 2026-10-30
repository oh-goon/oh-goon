from __future__ import annotations

import copy
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from oh_goon.integrations.hashmarks.development_verification import (
    nominate_changed_verification,
)
from oh_goon.repository_tooling import development_verification as owner
from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    owner._git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("Original explanation.\n", encoding="utf-8")
    owner._git(tmp_path, "add", "README.md")
    owner._git(
        tmp_path,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    monkeypatch.setattr(
        owner, "canonical_source_identity", lambda _root: "sha256:" + "a" * 64
    )
    return tmp_path


def _evidence(paths: tuple[str, ...], *, stale: bool = False) -> dict[str, Any]:
    return {
        "producer_implementation_identity": "sha256:" + "b" * 64,
        "owner_coverage": "not-claimed",
        "observations": [
            {
                "changed_path": path,
                "state": "LINKED",
                "edit_candidates": [path],
                "stale": stale,
                "qualification": {
                    "completeness": "incomplete",
                    "negative_evidence_admissible": False,
                },
                "nominations": [
                    {
                        "nomination": {
                            "available": True,
                            "runner_evidence": "pytest",
                            "verification_target": {"path": test},
                        },
                        "relationship": relationship,
                        "workspace_prefix": "",
                    }
                    for test, relationship in (
                        (
                            "backend/tests/unit/services/test_example.py",
                            "direct-reference",
                        ),
                        (
                            "backend/tests/integration/services/test_example.py",
                            "bounded-indirect-reference",
                        ),
                    )
                ],
            }
            for path in paths
        ],
    }


def test_native_git_change_capture_preserves_renames_deletions_and_untracked(
    repository: Path,
) -> None:
    base = owner._git(repository, "rev-parse", "HEAD").strip()
    owner._git(repository, "mv", "README.md", "RENAMED.md")
    (repository / "new.py").write_text("value = 1\n", encoding="utf-8")
    observed_base, _, paths = owner.capture_change(repository, base, None)
    assert observed_base == base
    assert paths == ("README.md", "RENAMED.md", "new.py")


def test_effective_candidate_refuses_local_source_drift(repository: Path) -> None:
    head = owner._git(repository, "rev-parse", "HEAD").strip()
    owner.capture_change(repository, head, head)
    (repository / "README.md").write_text("Changed.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted"):
        owner.capture_change(repository, head, head)


def test_prose_only_plan_does_not_start_intelligence_or_unrelated_toolchains(
    repository: Path,
) -> None:
    (repository / "README.md").write_text("Changed explanation.\n", encoding="utf-8")

    def forbidden(_root: Path, _paths: tuple[str, ...]) -> dict[str, Any]:
        raise AssertionError(
            "non-executable docs do not need repository behavior selection"
        )

    plan = owner.admit_change(repository, base="HEAD", nominator=forbidden)
    assert plan.behavioral_status == "NOT_APPLICABLE"
    assert plan.static == ("architecture",)
    assert not plan.pytest and not plan.frontend and not plan.unverified


def test_admission_retains_direct_and_indirect_proofs_without_coverage_upgrade(
    repository: Path,
) -> None:
    (repository / "behavior.py").write_text("answer = 1\n", encoding="utf-8")
    plan = owner.admit_change(
        repository, base="HEAD", nominator=lambda _root, paths: _evidence(paths)
    )
    assert plan.pytest == (
        "backend/tests/integration/services/test_example.py",
        "backend/tests/unit/services/test_example.py",
    )
    assert plan.evidence["owner_coverage"] == "not-claimed"
    assert not plan.evidence["observations"][0]["qualification"][
        "negative_evidence_admissible"
    ]
    assert plan.behavioral_status == "REQUIRED"
    assert not plan.unverified


@pytest.mark.parametrize(
    "cause", ("missing", "stale", "other-owner", "unavailable-test")
)
def test_unproven_selection_refuses_merge_before_commands(
    repository: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    (repository / "behavior.py").write_text("answer = 1\n", encoding="utf-8")

    def nominate(_root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
        evidence = _evidence(paths)
        row = evidence["observations"][0]
        if cause == "missing":
            evidence["observations"] = []
        elif cause == "stale":
            row["stale"] = True
        elif cause == "other-owner":
            row["edit_candidates"] = ["different.py"]
        else:
            row["nominations"] = []
        return evidence

    valid = owner.admit_change(
        repository, base="HEAD", nominator=lambda _root, paths: _evidence(paths)
    )
    assert not valid.unverified
    plan = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert plan.unverified
    monkeypatch.setattr(owner, "require_current_plan", lambda _root, _plan: None)

    def forbidden(_root: Path, _plan: owner.ChangeVerificationPlan) -> list[Any]:
        raise AssertionError("unverified selection must refuse before any check starts")

    monkeypatch.setattr(owner, "_commands", forbidden)
    with pytest.raises(ValueError, match="SELECTION_UNVERIFIED"):
        owner.execute_change(repository, plan, mode="merge", workers=1)


def test_source_change_invalidates_admitted_plan_before_work(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / "README.md").write_text("Changed.\n", encoding="utf-8")
    plan = owner.admit_change(repository, base="HEAD")
    owner.require_current_plan(repository, plan)
    monkeypatch.setattr(
        owner, "canonical_source_identity", lambda _root: "sha256:" + "c" * 64
    )
    with pytest.raises(ValueError, match="source changed"):
        owner.require_current_plan(repository, plan)


def test_source_change_stops_before_a_later_command_can_restore_old_bytes(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / "README.md").write_text("Changed explanation.\n", encoding="utf-8")
    plan = owner.admit_change(repository, base="HEAD")
    state = {"identity": plan.source_identity, "mutate": False}
    calls: list[str] = []
    monkeypatch.setattr(
        owner, "canonical_source_identity", lambda _root: state["identity"]
    )
    monkeypatch.setattr(
        owner,
        "_commands",
        lambda _root, _plan: [
            ("first", ("first",), repository),
            ("restore", ("restore",), repository),
        ],
    )
    native_command = run_command

    def command(*argv: str, **_kwargs: Any) -> Any:
        if argv[0] == "git":
            return native_command(*argv, **_kwargs)
        calls.append(argv[0])
        if state["mutate"]:
            state["identity"] = (
                "sha256:" + "d" * 64 if argv[0] == "first" else plan.source_identity
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(owner, "run_command", command)
    assert (
        owner.execute_change(repository, plan, mode="focused", workers=1)["status"]
        == "CHECKS_PASSED"
    )
    calls.clear()
    state["mutate"] = True
    assert (
        owner.execute_change(repository, plan, mode="focused", workers=1)["status"]
        == "INCOMPLETE"
    )
    assert calls == ["first"]


@pytest.mark.parametrize("outcome", ("missing", "duplicates", "extras", "resumed"))
def test_required_membership_cannot_be_qualified_by_pytest_exit_zero(
    outcome: str,
) -> None:
    valid: dict[str, Any] = {
        "status": "PASS",
        "native_collection": {"discovered": ["test.py::test_case"], "deselected": []},
        "membership_receipt": {
            "selected": ["test.py::test_case"],
            "passed": ["test.py::test_case"],
            "terminal_success": True,
            "effective_pytest_outcomes": [
                {
                    "member": "test.py::test_case",
                    "status": "PASSED",
                    "attempt_identity": "attempt-1",
                    "pytest_outcomes": [
                        {"nodeid": "test.py::test_case", "outcome": "PASSED"}
                    ],
                }
            ],
        },
    }
    owner.require_complete_membership(valid)
    invalid = copy.deepcopy(valid)
    invalid["membership_receipt"][outcome] = ["test.py::test_case"]
    with pytest.raises(ValueError, match="incomplete"):
        owner.require_complete_membership(invalid)


def test_supported_hashmarks_adapter_returns_real_source_qualified_links(
    tmp_path: Path,
) -> None:
    (tmp_path / "feature.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_feature.py").write_text(
        "from feature import answer\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8"
    )
    evidence = nominate_changed_verification(
        tmp_path, ("feature.py", "tests/test_feature.py")
    )
    assert evidence["owner_coverage"] == "not-claimed"
    changed = {row["changed_path"]: row for row in evidence["observations"]}
    assert changed["tests/test_feature.py"]["state"] == "CHANGED_TEST"
    assert changed["feature.py"]["state"] == "LINKED"
    linked = changed["feature.py"]["nominations"]
    assert any(row["relationship"] == "direct-reference" for row in linked)
    assert any(
        row["nomination"]["verification_target"]["path"] == "tests/test_feature.py"
        for row in linked
    )


def test_generated_contracts_use_their_producer_owner_without_per_shard_impact_queries(
    repository: Path,
) -> None:
    api = repository / "backend/src/oh_goon/api/example.py"
    api.parent.mkdir(parents=True)
    api.write_text("answer = 1\n", encoding="utf-8")
    generated = repository / "generated/frontend/models.ts"
    generated.parent.mkdir(parents=True)
    generated.write_text("export type Answer = number;\n", encoding="utf-8")
    seen: list[tuple[str, ...]] = []

    def nominate(_root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
        seen.append(paths)
        return _evidence(paths)

    plan = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert seen == [("backend/src/oh_goon/api/example.py",)]
    assert {"generated", "frontend"} <= set(plan.static)
    assert not plan.unverified
    api.unlink()
    seen.clear()
    derived_only = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert seen == []
    assert derived_only.behavioral_status == "REQUIRED"
    assert not derived_only.unverified


def test_standalone_project_nominations_never_use_the_backend_interpreter(
    repository: Path,
) -> None:
    (repository / "behavior.py").write_text("answer = 1\n", encoding="utf-8")

    def nominate(_root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
        evidence = _evidence(paths)
        for projection in evidence["observations"][0]["nominations"]:
            projection["nomination"]["verification_target"]["path"] = (
                "templates/goons/ml/iris-train/tests/test_training.py"
            )
        return evidence

    plan = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert not plan.pytest
    assert any(
        reason.startswith("NATIVE_PROJECT_CONTEXT_REQUIRED:")
        for reason in plan.unverified
    )


def test_execution_uses_original_native_batch_membership_without_full_targets() -> None:
    root = owner.ROOT
    head = owner._git(root, "rev-parse", "HEAD").strip()
    plan = owner.ChangeVerificationPlan(
        base=head,
        candidate=head,
        source_identity=canonical_source_identity(root),
        changed_paths=("backend/src/oh_goon/core/errors.py",),
        static=(),
        pytest=("backend/tests/unit/core/test_error_kinds.py",),
        frontend=(),
        unverified=(),
        evidence={},
        behavioral_status="REQUIRED",
        policy_status="ORDINARY",
    )
    result = owner.execute_change(root, plan, mode="merge", workers=1)
    assert result["status"] == "CHECKS_PASSED"
    receipt = result["outcomes"][0]["native_report"]["membership_receipt"]
    assert receipt["selected"] == receipt["passed"]
    assert not receipt["non_passed"]
