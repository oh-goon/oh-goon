from __future__ import annotations

import copy
import json
import stat
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from oh_goon.integrations.hashmarks.development_verification import (
    nominate_changed_verification,
)
from oh_goon.repository_tooling import development_verification as owner
from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.pytest_member_selection import NativePytestCollection
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from oh_goon.repository_tooling.verification_gate_control import (
    VERIFICATION_POLICY_TESTS,
)


def test_private_json_transport_publishes_and_reopens_real_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    scratch = tmp_path / "control"
    scratch.mkdir(mode=0o700)
    target = scratch / "plan.json"
    payload = {"candidate": "a" * 40, "members": ["test.py::test_case"]}
    owner._write_transport(target, payload, source)
    assert owner._read_json(target) == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    owner._write_transport(target, {"status": "MERGE_PROOF_READY"}, source)
    assert owner._read_json(target) == {"status": "MERGE_PROOF_READY"}
    assert list(scratch.iterdir()) == [target]


@pytest.mark.parametrize("mode", ["focused", "merge"])
def test_plan_cli_publishes_after_actual_source_admission(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    owner._git(source, "init", "-q")
    readme = source / "docs/explanation.md"
    readme.parent.mkdir()
    readme.write_text("Original explanation.\n", encoding="utf-8")
    owner._git(source, "add", "docs/explanation.md")
    owner._git(
        source,
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
    readme.write_text("Updated explanation.\n", encoding="utf-8")
    scratch = tmp_path / "control"
    scratch.mkdir(mode=0o700)
    target = scratch / "plan.json"
    outputs = scratch / "outputs"
    assert (
        owner.main(
            [
                "--root",
                str(source),
                "--base",
                "HEAD",
                "--mode",
                mode,
                "--plan",
                str(target),
                "--github-output",
                str(outputs),
            ]
        )
        == 0
    )
    plan = owner.ChangeVerificationPlan.model_validate_json(target.read_bytes())
    assert plan.changed_paths == ("docs/explanation.md",)
    assert plan.source_identity == canonical_source_identity(source)
    assert plan.behavioral_status == "NOT_APPLICABLE"
    assert (
        outputs.read_text(encoding="utf-8")
        == "python=false\nfrontend=false\npytest=false\n"
    )
    assert (
        json.loads(target.read_text())["candidate"]
        == owner._git(source, "rev-parse", "HEAD").strip()
    )


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
    document = repository / "docs/ordinary.md"
    document.parent.mkdir(parents=True)
    document.write_text("Changed explanation.\n", encoding="utf-8")

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


@pytest.mark.parametrize("mode", ["focused", "merge"])
@pytest.mark.parametrize("cause", ["unproven-scope", "missing-behavior"])
@pytest.mark.parametrize("existing_outputs", [False, True])
def test_plan_cli_rejects_ineligible_merge_before_capability_outputs(
    repository: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    cause: str,
    existing_outputs: bool,
) -> None:
    (repository / "behavior.py").write_text("answer = 1\n", encoding="utf-8")
    valid = owner.admit_change(
        repository, base="HEAD", nominator=lambda _root, paths: _evidence(paths)
    )
    assert not valid.unverified and valid.pytest
    plan = valid.model_copy(
        update=(
            {"unverified": ("NO_QUALIFIED_VERIFIER:behavior.py",)}
            if cause == "unproven-scope"
            else {"pytest": (), "frontend": ()}
        )
    )
    admissions: list[Path] = []

    def admitted(root: Path, **_kwargs: Any) -> owner.ChangeVerificationPlan:
        admissions.append(root)
        return plan

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("plan inspection must never execute checks")

    monkeypatch.setattr(owner, "admit_change", admitted)
    monkeypatch.setattr(owner, "execute_change", forbidden)
    control = tmp_path_factory.mktemp("merge-plan-control")
    target, outputs = control / "plan.json", control / "outputs"
    previous = "existing=value\n" if existing_outputs else ""
    if existing_outputs:
        outputs.write_text(previous, encoding="utf-8")
    result = owner.main(
        [
            "--root",
            str(repository),
            "--base",
            "HEAD",
            "--mode",
            mode,
            "--plan",
            str(target),
            "--github-output",
            str(outputs),
        ]
    )
    assert admissions == [repository]
    assert owner.ChangeVerificationPlan.model_validate_json(target.read_bytes()) == plan
    if mode == "merge":
        assert result == 2
        assert "SELECTION_UNVERIFIED" in capsys.readouterr().err
        assert outputs.exists() == existing_outputs
        if existing_outputs:
            assert outputs.read_text(encoding="utf-8") == previous
    else:
        assert result == 0
        assert outputs.read_text(encoding="utf-8") == (
            previous
            + f"python=true\nfrontend=false\npytest={'true' if plan.pytest else 'false'}\n"
        )


@pytest.mark.parametrize("relative", ["behavior.py", "generated/frontend/models.ts"])
def test_merge_plan_cli_keeps_qualified_behavior_and_generated_contract_paths(
    repository: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("answer = 1\n", encoding="utf-8")
    plan = owner.admit_change(
        repository, base="HEAD", nominator=lambda _root, paths: _evidence(paths)
    )
    assert not plan.unverified
    assert bool(plan.pytest) == (relative == "behavior.py")
    monkeypatch.setattr(owner, "admit_change", lambda *_args, **_kwargs: plan)
    control = tmp_path_factory.mktemp("qualified-plan-control")
    outputs = control / "outputs"
    assert (
        owner.main(
            [
                "--root",
                str(repository),
                "--base",
                "HEAD",
                "--mode",
                "merge",
                "--plan",
                str(control / "plan.json"),
                "--github-output",
                str(outputs),
            ]
        )
        == 0
    )
    assert outputs.read_text(encoding="utf-8") == (
        f"python={'true' if relative == 'behavior.py' else 'false'}\n"
        f"frontend={'true' if 'frontend' in plan.static else 'false'}\n"
        f"pytest={'true' if plan.pytest else 'false'}\n"
    )


def test_source_change_invalidates_admitted_plan_before_work(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = repository / "docs/identity.md"
    document.parent.mkdir(parents=True)
    document.write_text("Changed.\n", encoding="utf-8")
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
    document = repository / "docs/source-change.md"
    document.parent.mkdir(parents=True)
    document.write_text("Changed explanation.\n", encoding="utf-8")
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


def test_native_package_proof_delegates_lock_runtime_and_build_to_existing_owners(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = repository / "docs/package-proof.md"
    document.parent.mkdir(parents=True)
    document.write_text("Package verification policy.\n", encoding="utf-8")
    plan = owner.admit_change(repository, base="HEAD").model_copy(
        update={"static": ("package",)}
    )
    commands: list[tuple[str, ...]] = []
    native_command = run_command

    def command(*argv: str, **options: Any) -> Any:
        if argv[0] == "git":
            return native_command(*argv, **options)
        assert options["cwd"] == repository
        commands.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(owner, "run_command", command)

    result = owner.execute_change(repository, plan, mode="focused", workers=1)

    assert result["status"] == "CHECKS_PASSED"
    assert result["outcomes"] == [{"owner": "Native package", "returncode": 0}]
    assert commands == [
        (
            "make",
            "backend-lock-check",
            "backend-python-capability-check",
            "backend-package-build-check",
        )
    ]


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


def test_supported_hashmarks_adapter_reconciles_stale_contributor_without_partial_rows(
    tmp_path: Path,
) -> None:
    (tmp_path / "feature.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    verifier = tests / "test_feature.py"
    verifier.write_text(
        "from feature import answer\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    (tests / "test_other.py").write_text(
        "def test_other():\n    assert True\n", encoding="utf-8"
    )
    (tests / "test_policy.py").write_text(
        "def test_policy():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8"
    )
    changed = ("tests/test_other.py", "feature.py")
    required = ("tests/test_policy.py",)
    baseline = nominate_changed_verification(tmp_path, changed, required=required)
    original = baseline["observations"][1]["nominations"]
    before = next(
        row["nomination"]["source_member_revision"]
        for row in original
        if row["nomination"]["verification_target"]["path"] == "tests/test_feature.py"
    )
    # The map is warm. A later nominated contributor now has different bytes,
    # after required proof and the first changed-test row have been observed.
    with verifier.open("a", encoding="utf-8") as stream:
        stream.write("\n# Current verifier source.\n")
    evidence = nominate_changed_verification(tmp_path, changed, required=required)
    assert (
        evidence["producer_implementation_identity"]
        == baseline["producer_implementation_identity"]
    )
    assert evidence["owner_coverage"] == "not-claimed"
    assert [row["changed_path"] for row in evidence["observations"]] == list(changed)
    assert len(evidence["required_checks"]) == 1
    assert (
        evidence["required_checks"][0]["nomination"]["verification_target"]["path"]
        == required[0]
    )
    observed = evidence["observations"][1]
    assert observed["state"] == "LINKED"
    assert observed["qualification"] == baseline["observations"][1]["qualification"]
    refreshed = next(
        row["nomination"]["source_member_revision"]
        for row in observed["nominations"]
        if row["nomination"]["verification_target"]["path"] == "tests/test_feature.py"
    )
    assert refreshed != before


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


@pytest.mark.parametrize("mode", ["focused", "merge"])
def test_execution_uses_original_native_batch_membership_without_full_targets(
    mode: str,
) -> None:
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
    result = owner.execute_change(root, plan, mode=mode, workers=1)
    assert result["status"] == "CHECKS_PASSED"
    native = result["outcomes"][0]["native_report"]
    assert native["fail_fast"] is True
    assert native["fail_fast_triggered"] is False
    assert native["stop_reason"] is None
    receipt = native["membership_receipt"]
    assert receipt["selected"] == receipt["passed"]
    assert not receipt["non_passed"]


@pytest.mark.parametrize(
    ("relative", "expected_static"),
    (
        ("README.md", {"architecture"}),
        ("backend/README.md", {"architecture", "python"}),
        ("backend/src/oh_goon/README.md", {"architecture", "python"}),
        ("scripts/README.md", {"architecture"}),
        ("frontend/README.md", {"architecture", "frontend"}),
        ("frontend/src/README.md", {"architecture", "frontend"}),
    ),
)
def test_owning_readme_uses_required_policy_proof_with_real_source_and_nominations(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    expected_static: set[str],
) -> None:
    monkeypatch.setattr(owner, "canonical_source_identity", canonical_source_identity)
    readme = repository / relative
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("Original contributor command guidance.\n", encoding="utf-8")
    for selected in VERIFICATION_POLICY_TESTS:
        test = repository / selected
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text(
            "def test_policy_fixture() -> None:\n    assert True\n", encoding="utf-8"
        )
    owner._git(repository, "add", ".")
    owner._git(
        repository,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "governing source fixture",
    )
    readme.write_text("Updated contributor command guidance.\n", encoding="utf-8")
    actual = nominate_changed_verification(
        repository, (), required=VERIFICATION_POLICY_TESTS
    )

    def nominate(
        _root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        assert paths == (relative,)
        assert required == VERIFICATION_POLICY_TESTS
        result = copy.deepcopy(actual)
        result["observations"] = [
            {"changed_path": relative, "state": "UNAVAILABLE", "stale": False}
        ]
        return result

    plan = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert plan.changed_paths == (relative,)
    assert plan.source_identity == canonical_source_identity(repository)
    assert plan.behavioral_status == "REQUIRED"
    assert plan.policy_status == "REVIEW_REQUIRED"
    assert plan.obligations == ("verification-policy protection",)
    assert set(plan.pytest) == set(VERIFICATION_POLICY_TESTS)
    assert set(plan.static) == expected_static
    assert not plan.unverified
    assert plan.evidence["owner_coverage"] == "not-claimed"

    def missing(
        _root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        result = nominate(_root, paths, required=required)
        result["required_checks"].pop()
        return result

    assert (
        "VERIFICATION_POLICY_PROTECTION_UNAVAILABLE"
        in owner.admit_change(repository, base="HEAD", nominator=missing).unverified
    )

    def stale(
        _root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        result = nominate(_root, paths, required=required)
        result["observations"][0]["stale"] = True
        return result

    assert (
        f"NO_QUALIFIED_VERIFIER:{relative}"
        in owner.admit_change(repository, base="HEAD", nominator=stale).unverified
    )


@pytest.mark.parametrize(
    "relative",
    (
        "docs/development/REAL_LIFECYCLE_QA.md",
        "docs/development/REAL_LIFECYCLE_QA.yml",
    ),
)
def test_qa_policy_change_admits_actual_required_producer_checks(
    repository: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    monkeypatch.setattr(owner, "canonical_source_identity", canonical_source_identity)
    selected = repository / relative
    selected.parent.mkdir(parents=True)
    selected.write_text("policy: original qualification guidance\n", encoding="utf-8")
    expected = (
        *VERIFICATION_POLICY_TESTS,
        "scripts/tests/test_real_lifecycle_qa_contract.py",
    )
    for path in expected:
        check = repository / path
        check.parent.mkdir(parents=True, exist_ok=True)
        check.write_text(
            "def test_policy_contract():\n    assert True\n", encoding="utf-8"
        )
    owner._git(repository, "add", ".")
    owner._git(
        repository,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "qualification policy fixture",
    )
    selected.write_text("policy: updated qualification guidance\n", encoding="utf-8")
    plan = owner.admit_change(repository, base="HEAD")
    assert plan.changed_paths == (relative,)
    assert plan.behavioral_status == "REQUIRED"
    assert plan.policy_status == "REVIEW_REQUIRED"
    assert plan.obligations == (
        "verification-policy protection",
        "real-lifecycle QA protection",
    )
    assert set(plan.pytest) == set(expected)
    assert not plan.unverified
    assert plan.source_identity == canonical_source_identity(repository)
    assert plan.evidence["owner_coverage"] == "not-claimed"
    checks = plan.evidence["required_checks"]
    assert len(checks) == len(expected)
    for projection in checks:
        nomination = projection["nomination"]
        assert nomination["available"] is True
        assert nomination["runner_evidence"] == "pytest"
        assert nomination["source_member_revision"]
        assert nomination["verification_target"]["path"] in expected

    def missing_qa(
        root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        evidence = nominate_changed_verification(root, paths, required=required)
        evidence["required_checks"] = [
            row
            for row in evidence["required_checks"]
            if row["nomination"]["verification_target"]["path"] != expected[-1]
        ]
        return evidence

    incomplete = owner.admit_change(repository, base="HEAD", nominator=missing_qa)
    assert "VERIFICATION_POLICY_PROTECTION_UNAVAILABLE" in incomplete.unverified


@pytest.mark.parametrize(
    ("relative", "reason"),
    (
        ("docs/ordinary.md", "NO_QUALIFIED_VERIFIER"),
        ("docs/development/REAL_LIFECYCLE_QA.extra.yml", "NO_QUALIFIED_VERIFIER"),
        ("backend/tests/factories/qa.py", "SHARED_FIXTURE_CONSUMER_SCOPE_UNVERIFIED"),
    ),
)
def test_qa_policy_protection_does_not_qualify_other_unknown_scope(
    repository: Path, relative: str, reason: str
) -> None:
    qa = repository / "docs/development/REAL_LIFECYCLE_QA.md"
    qa.parent.mkdir(parents=True)
    qa.write_text("Current qualification policy.\n", encoding="utf-8")
    unknown = repository / relative
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_text(
        "def qa_fixture():\n    return 42\n"
        if unknown.suffix == ".py"
        else "proof: unqualified\n"
        if unknown.suffix == ".yml"
        else "```sh\nprintf verify\n```\n",
        encoding="utf-8",
    )

    def nominate(
        _root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        evidence = _evidence(paths)
        for row in evidence["observations"]:
            if "/tests/factories/" not in row["changed_path"]:
                row.update(state="UNAVAILABLE", nominations=[])
        evidence["required_checks"] = [
            {"nomination": {"available": True, "verification_target": {"path": path}}}
            for path in required
        ]
        return evidence

    plan = owner.admit_change(repository, base="HEAD", nominator=nominate)
    assert "scripts/tests/test_real_lifecycle_qa_contract.py" in plan.pytest
    assert f"{reason}:{relative}" in plan.unverified


def _dependency_update(
    repository: Path, packages: tuple[str, ...] = ("ordinary-library",)
) -> tuple[str, bytes, str]:
    backend = repository / "backend"
    backend.mkdir(exist_ok=True)
    manifest = (
        '[project]\nname = "application"\nversion = "0"\nrequires-python = ">=3.12"\n'
        "dependencies = " + json.dumps([name + "==1" for name in packages]) + "\n"
    )
    lock = (
        'version = 1\nrevision = 1\n[[package]]\nname = "application"\nversion = "0"\n'
        'source = { editable = "." }\ndependencies = ['
        + ",".join("{ name = " + json.dumps(name) + " }" for name in packages)
        + "]\n"
        + "".join(
            "[[package]]\nname = "
            + json.dumps(name)
            + '\nversion = "1"\nsource = { registry = "https://pypi.org/simple" }\n'
            for name in packages
        )
    )
    (backend / "pyproject.toml").write_text(manifest, encoding="utf-8")
    (backend / "uv.lock").write_text(lock, encoding="utf-8")
    owner._git(repository, "add", "backend/pyproject.toml", "backend/uv.lock")
    owner._git(
        repository,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "dependency baseline",
    )
    base = owner._git(repository, "rev-parse", "HEAD").strip()
    (backend / "pyproject.toml").write_text(
        manifest.replace("==1", "==2"), encoding="utf-8"
    )
    (backend / "uv.lock").write_text(
        lock.replace('version = "1"', 'version = "2"'), encoding="utf-8"
    )
    return base, lock.encode("utf-8"), manifest


def _dependency_evidence(
    paths: tuple[str, ...],
    *,
    required: tuple[str, ...],
    dependency_base: dict[str, Any] | None,
    packages: tuple[str, ...] = ("ordinary-library",),
) -> dict[str, Any]:
    evidence = _evidence(paths)
    evidence["required_checks"] = [
        {
            "nomination": {
                "available": True,
                "runner_evidence": "pytest",
                "verification_target": {"path": path},
            }
        }
        for path in required
    ]
    if dependency_base is None:
        return evidence
    before_identity, after_identity = "before-observation", "after-observation"
    binding = {"repository_identity": "candidate-repository", "generation": 1}
    metadata: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for index, package in enumerate(packages):
        node, old_node, component = (
            f"node-{index}",
            f"old-node-{index}",
            f"component-{index}",
        )
        source = f"backend/src/oh_goon/dependency_user_{index}.py"
        target = f"backend/tests/unit/integrations/test_dependency_user_{index}.py"
        consumer = {
            "source_path": source,
            "source_member": {
                "path": source,
                "state": "known-present",
                "member_revision": "a" * 64,
            },
            "distribution_state": "resolved-unique",
            "distribution_nodes": [node],
            "import_claim_authority": "source-observed-static-import",
        }
        projected = {
            "workspace_prefix": "",
            "relationship": "direct-reference",
            "nomination": {
                "available": True,
                "runner_evidence": "pytest",
                "source_member_revision": "c" * 64,
                "verification_target": {"path": target},
            },
        }
        ownership = {
            "stale": False,
            "edit_candidates": [source],
            "verification_owners": [
                {
                    "path": target,
                    "covers_edit_candidates": [source],
                    "coverage_evidence": "direct-reference",
                }
            ],
            "verification_qualification": {
                "completeness": "incomplete",
                "truncation": "truncated",
                "qualified_identity_ambiguous": True,
                "verifier_identity_ambiguous": True,
                "search_bound_reasons": ["indirect-symbol-limit"],
            },
        }
        chains.append(
            {
                "node_id": node,
                "source_path": source,
                "consumer": consumer,
                "ownership": ownership,
                "nomination": projected,
            }
        )
        components.append(
            {"component_id": component, "name": package, "ecosystem": "pypi"}
        )
        selections.append({"node_id": node, "component_id": component, "version": "2"})
        transitions.append(
            {
                "component_id": component,
                "added": [{"node_id": node}],
                "removed": [{"node_id": old_node}],
                "changed": [],
            }
        )
        metadata.append(
            {
                "node_id": node,
                "component_id": component,
                "name": package,
                "version": "2",
                "discovery": "importlib.metadata.distributions",
                "distribution_root": "/native/environment/site-packages",
                "metadata_path": f"/native/environment/{package}.dist-info/METADATA",
                "record_path": f"/native/environment/{package}.dist-info/RECORD",
                "metadata_member_revision": "d" * 64,
                "record_member_revision": "e" * 64,
            }
        )
    nodes = [row["node_id"] for row in selections]
    source_paths = [chain["source_path"] for chain in chains]
    packet = {
        "observation_identity": after_identity,
        "repository_binding": binding,
        "selected_node_ids": nodes,
        "source_scope": {
            "paths": source_paths,
            "members": [chain["consumer"]["source_member"] for chain in chains],
            "unavailable": [],
            "completeness": "complete",
        },
        "consumers": [chain["consumer"] for chain in chains],
        "truncation": "complete",
    }
    bundle = {
        "schema": "oh-goon.changed-dependency-consumers/v1",
        "lock_path": "backend/uv.lock",
        "base_revision": dependency_base["base_revision"],
        "before_member_revision": "f" * 64,
        "before_observation": {"observation_identity": before_identity},
        "before_assessment": {"source_scope": {"source_equivalence": "mismatch"}},
        "after_observation": {
            "observation_identity": after_identity,
            "repository_binding": binding,
            "selections": selections,
            "components": components,
        },
        "after_assessment": {
            "source_scope": {
                "availability": "available",
                "freshness": "current",
                "source_equivalence": "proven",
            }
        },
        "delta": {
            "comparability": "comparable",
            "before_observation_identity": before_identity,
            "after_observation_identity": after_identity,
            "selections_added": nodes.copy(),
            "selections_removed": [f"old-node-{index}" for index in range(len(nodes))],
            "selections_changed": [],
            "components_added": [],
            "components_removed": [],
            "components_changed": [],
            "component_selection_transitions": transitions,
        },
        "changed_node_ids": nodes,
        "metadata": metadata,
        "environment": {
            "python_executable": "/native/environment/bin/python",
            "python_prefix": "/native/environment",
            "python_version": "3.12",
            "producer_implementation_identity": evidence[
                "producer_implementation_identity"
            ],
            "metadata_discovery": "importlib.metadata.distributions",
        },
        "consumer_packets": [packet],
        "consumer_scope": {
            "paths": source_paths,
            "completeness": "incomplete",
            "negative_evidence": "not-admissible",
            "reasons": ["explicit-source-scope"],
        },
        "source_selection": {
            "owner": "hashmarks.files.iter_files",
            "prefix": "backend/src",
            "suffix": ".py",
            "scope": "explicit-backend-runtime-python-sources",
            "consumer_completeness": "not-claimed",
        },
        "truncation": "complete",
        "chains": chains,
    }
    evidence["dependency_changes"] = bundle
    for row in evidence["observations"]:
        if row["changed_path"] == "backend/uv.lock":
            row.update(
                dependency_chain=chains,
                edit_candidates=source_paths,
                nominations=[chain["nomination"] for chain in chains],
            )
        elif row["changed_path"] == "backend/pyproject.toml":
            row.update(state="UNAVAILABLE", nominations=[])
    return evidence


def _synthetic_dependency_collection(
    paths: tuple[Path, ...], calls: list[tuple[str, ...]], **options: Any
) -> NativePytestCollection:
    invocation = options["invocation"]
    assert invocation.qualified
    assert invocation.config_file == invocation.cwd / "backend/pyproject.toml"
    assert options["marker"] == "not certification and not slow and not daily"
    files = tuple(path.relative_to(invocation.cwd).as_posix() for path in paths)
    calls.append(files)
    return NativePytestCollection(
        tuple({"file": file, "selector": file + "::test_behavior"} for file in files),
        (),
    )


@pytest.fixture
def dependency_collection(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def collect(paths: tuple[Path, ...], **options: Any) -> NativePytestCollection:
        return _synthetic_dependency_collection(paths, calls, **options)

    monkeypatch.setattr(owner, "collect_native_pytest", collect)
    monkeypatch.setattr(
        owner, "require_current_dependency_environment", lambda _bundle: None
    )
    monkeypatch.setattr(
        owner, "require_loaded_hashmarks_runtime", lambda: "sha256:" + "b" * 64
    )
    return calls


@pytest.mark.parametrize("package", ("ordinary-library", "hashmarks"))
def test_dependency_admission_preserves_incomplete_scope_and_native_base_provenance(
    repository: Path, dependency_collection: list[tuple[str, ...]], package: str
) -> None:
    base, before_lock, _manifest = _dependency_update(repository, (package,))
    captured: list[dict[str, Any]] = []

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        captured.append(options["dependency_base"])
        return _dependency_evidence(paths, packages=(package,), **options)

    plan = owner.admit_change(repository, base=base, nominator=nominate)

    assert not plan.unverified
    assert captured == [
        {"lock": before_lock, "base_revision": base, "lock_path": "backend/uv.lock"}
    ]
    assert len(dependency_collection) == 1
    assert set(VERIFICATION_POLICY_TESTS) <= set(plan.pytest)
    assert "backend/tests/unit/integrations/test_dependency_user_0.py" in plan.pytest
    bundle = plan.evidence["dependency_changes"]
    assert bundle["consumer_scope"]["completeness"] == "incomplete"
    assert bundle["consumer_scope"]["negative_evidence"] == "not-admissible"
    assert (
        bundle["before_assessment"]["source_scope"]["source_equivalence"] == "mismatch"
    )
    assert (
        bundle["chains"][0]["ownership"]["verification_qualification"]["truncation"]
        == "truncated"
    )
    lock = next(
        row
        for row in plan.evidence["observations"]
        if row["changed_path"] == "backend/uv.lock"
    )
    assert lock["edit_candidates"] == ["backend/src/oh_goon/dependency_user_0.py"]
    assert (
        plan.evidence["dependency_admission"]["authority"] == "oh-goon-execution-policy"
    )
    assert (
        "complete impact is not claimed"
        in plan.evidence["dependency_admission"]["rationale"]
    )
    owner.require_current_plan(repository, plan)


@pytest.mark.parametrize(
    "fault",
    (
        "stale-lock",
        "unknown-equivalence",
        "foreign-base",
        "foreign-consumers",
        "truncated-consumers",
        "source-unavailable",
        "missing-metadata",
        "foreign-producer",
        "unknown-consumer",
        "ambiguous-consumer",
        "stale-verifier",
        "unrelated-verifier",
        "missing-nomination",
        "changed-component",
        "empty-change",
    ),
)
def test_dependency_admission_refuses_unqualified_evidence_before_native_collection(
    repository: Path, dependency_collection: list[tuple[str, ...]], fault: str
) -> None:
    base, _lock, _manifest = _dependency_update(repository)

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        evidence = _dependency_evidence(paths, **options)
        bundle = evidence["dependency_changes"]
        chain = bundle["chains"][0]
        if fault == "stale-lock":
            bundle["after_assessment"]["source_scope"]["freshness"] = "stale"
        elif fault == "unknown-equivalence":
            bundle["after_assessment"]["source_scope"]["source_equivalence"] = "unknown"
        elif fault == "foreign-base":
            bundle["base_revision"] = "0" * 40
        elif fault == "foreign-consumers":
            bundle["consumer_packets"][0]["repository_binding"] = {
                "repository_identity": "other",
                "generation": 1,
            }
        elif fault == "truncated-consumers":
            bundle["consumer_packets"][0]["truncation"] = "truncated"
        elif fault == "source-unavailable":
            bundle["consumer_packets"][0]["source_scope"]["unavailable"] = [
                chain["source_path"]
            ]
        elif fault == "missing-metadata":
            bundle["metadata"] = []
        elif fault == "foreign-producer":
            bundle["environment"]["producer_implementation_identity"] = (
                "sha256:" + "0" * 64
            )
        elif fault == "unknown-consumer":
            chain["consumer"]["distribution_state"] = "unknown"
        elif fault == "ambiguous-consumer":
            chain["consumer"]["distribution_state"] = "resolved-ambiguous"
            chain["consumer"]["distribution_nodes"].append("another-node")
        elif fault == "stale-verifier":
            chain["ownership"]["stale"] = True
        elif fault == "unrelated-verifier":
            chain["ownership"]["verification_owners"][0]["covers_edit_candidates"] = [
                "different.py"
            ]
        elif fault == "missing-nomination":
            next(
                row
                for row in evidence["observations"]
                if row["changed_path"] == "backend/uv.lock"
            )["nominations"] = []
        elif fault == "changed-component":
            bundle["delta"]["components_changed"] = ["component-0"]
        else:
            bundle["changed_node_ids"] = []
        return evidence

    plan = owner.admit_change(repository, base=base, nominator=nominate)

    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
    assert dependency_collection == []
    with pytest.raises(ValueError, match="SELECTION_UNVERIFIED"):
        owner.require_merge_selection(repository, plan)


@pytest.mark.parametrize(
    "unsupported",
    (
        "requires-python",
        "dynamic",
        "optional-dependencies",
        "dependency-groups",
        "build-system",
        "uv-context",
        "hatch",
        "setuptools",
        "manifest-only",
        "frontend-lock",
    ),
)
def test_runtime_dependency_policy_does_not_admit_other_dependency_context_changes(
    repository: Path, dependency_collection: list[tuple[str, ...]], unsupported: str
) -> None:
    base, before_lock, _manifest = _dependency_update(repository)
    manifest = repository / "backend/pyproject.toml"
    text = manifest.read_text()
    if unsupported == "requires-python":
        text = text.replace(">=3.12", ">=3.13")
    elif unsupported == "dynamic":
        text += 'dynamic = ["version"]\n'
    elif unsupported == "optional-dependencies":
        text += '\n[project.optional-dependencies]\nextra = ["extra-library"]\n'
    elif unsupported == "dependency-groups":
        text += '\n[dependency-groups]\ndev = ["pytest"]\n'
    elif unsupported == "build-system":
        text += '\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    elif unsupported == "uv-context":
        text += "\n[tool.uv]\ndefault-groups = []\n"
    elif unsupported == "hatch":
        text += '\n[tool.hatch.build.targets.wheel]\npackages = ["other/src"]\n'
    elif unsupported == "setuptools":
        text += '\n[tool.setuptools.packages.find]\nwhere = ["other/src"]\n'
    elif unsupported == "manifest-only":
        (repository / "backend/uv.lock").write_bytes(before_lock)
    else:
        frontend = repository / "frontend"
        frontend.mkdir()
        (frontend / "package-lock.json").write_text("{}\n")
    manifest.write_text(text)

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        return _dependency_evidence(
            paths,
            dependency_base=options.get("dependency_base"),
            required=options.get("required", ()),
        )

    plan = owner.admit_change(repository, base=base, nominator=nominate)

    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
    assert dependency_collection == []


def _add_dependency_target(evidence: dict[str, Any], target: str) -> None:
    bundle = evidence["dependency_changes"]
    chain = copy.deepcopy(bundle["chains"][0])
    chain["nomination"]["nomination"]["verification_target"]["path"] = target
    chain["ownership"]["verification_owners"][0]["path"] = target
    bundle["chains"].append(chain)
    lock = next(
        row
        for row in evidence["observations"]
        if row["changed_path"] == "backend/uv.lock"
    )
    lock["nominations"].append(chain["nomination"])


@pytest.mark.parametrize("cadence", ("empty", "daily"))
def test_dependency_collection_preserves_raw_nominations_and_distinguishes_empty_from_deselected(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_collection: list[tuple[str, ...]],
    cadence: str,
) -> None:
    base, _lock, _manifest = _dependency_update(repository)
    advisory = "backend/tests/factories/dependency_support.py"

    def without_advisory(
        paths: tuple[Path, ...], **options: Any
    ) -> NativePytestCollection:
        result = _synthetic_dependency_collection(
            paths, dependency_collection, **options
        )
        omitted = tuple(row for row in result.selected if row["file"] == advisory)
        return NativePytestCollection(
            tuple(row for row in result.selected if row["file"] != advisory),
            omitted if cadence == "daily" else (),
        )

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        evidence = _dependency_evidence(paths, **options)
        _add_dependency_target(evidence, advisory)
        return evidence

    monkeypatch.setattr(owner, "collect_native_pytest", without_advisory)

    plan = owner.admit_change(repository, base=base, nominator=nominate)

    assert not plan.unverified
    assert len(dependency_collection) == 1
    assert advisory not in plan.pytest
    admission = plan.evidence["dependency_admission"]
    assert admission["targets_without_selected_members"] == [advisory]
    assert admission["empty_advisory_targets"] == (
        [advisory] if cadence == "empty" else []
    )
    assert bool(admission["collection"]["deselected"]) == (cadence == "daily")
    assert any(
        chain["nomination"]["nomination"]["verification_target"]["path"] == advisory
        for chain in plan.evidence["dependency_changes"]["chains"]
    )


def test_one_collected_dependency_chain_cannot_qualify_another_unselected_changed_node(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_collection: list[tuple[str, ...]],
) -> None:
    packages = ("first-library", "second-library")
    base, _lock, _manifest = _dependency_update(repository, packages)
    unselected = "backend/tests/unit/integrations/test_dependency_user_1.py"

    def native_selection(
        paths: tuple[Path, ...], **options: Any
    ) -> NativePytestCollection:
        result = _synthetic_dependency_collection(
            paths, dependency_collection, **options
        )
        return NativePytestCollection(
            tuple(row for row in result.selected if row["file"] != unselected),
            tuple(row for row in result.selected if row["file"] == unselected),
        )

    monkeypatch.setattr(owner, "collect_native_pytest", native_selection)
    plan = owner.admit_change(
        repository,
        base=base,
        nominator=lambda _root, paths, **options: _dependency_evidence(
            paths, packages=packages, **options
        ),
    )

    assert len(dependency_collection) == 1
    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
    assert unselected not in plan.pytest
    assert plan.evidence["dependency_admission"]["empty_advisory_targets"] == []


@pytest.mark.parametrize("missing", ("policy", "changed-test"))
def test_dependency_native_selection_cannot_drop_required_policy_or_changed_test(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_collection: list[tuple[str, ...]],
    missing: str,
) -> None:
    base, _lock, _manifest = _dependency_update(repository)
    target = VERIFICATION_POLICY_TESTS[0]
    if missing == "changed-test":
        target = "backend/tests/unit/integrations/test_changed.py"
        changed = repository / target
        changed.parent.mkdir(parents=True)
        changed.write_text("def test_changed():\n    assert True\n")

    def native_selection(
        paths: tuple[Path, ...], **options: Any
    ) -> NativePytestCollection:
        result = _synthetic_dependency_collection(
            paths, dependency_collection, **options
        )
        return NativePytestCollection(
            tuple(row for row in result.selected if row["file"] != target), ()
        )

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        evidence = _dependency_evidence(paths, **options)
        if missing == "changed-test":
            row = next(
                row for row in evidence["observations"] if row["changed_path"] == target
            )
            row["state"] = "CHANGED_TEST"
            row["nominations"] = [
                {
                    "workspace_prefix": "",
                    "nomination": {
                        "available": True,
                        "runner_evidence": "pytest",
                        "verification_target": {"path": target},
                    },
                }
            ]
        return evidence

    monkeypatch.setattr(owner, "collect_native_pytest", native_selection)
    plan = owner.admit_change(repository, base=base, nominator=nominate)

    reason = (
        "VERIFICATION_POLICY_PROTECTION_UNAVAILABLE"
        if missing == "policy"
        else f"CHANGED_TEST_HAS_NO_NATIVE_MEMBERS:{target}"
    )
    assert reason in plan.unverified


def test_retained_dependency_proof_cannot_omit_a_node_still_present_in_native_delta(
    repository: Path, dependency_collection: list[tuple[str, ...]]
) -> None:
    packages = ("first-library", "second-library")
    base, _lock, _manifest = _dependency_update(repository, packages)
    plan = owner.admit_change(
        repository,
        base=base,
        nominator=lambda _root, paths, **options: _dependency_evidence(
            paths, packages=packages, **options
        ),
    )
    assert not plan.unverified
    altered = copy.deepcopy(plan.evidence)
    bundle = altered["dependency_changes"]
    bundle["changed_node_ids"] = ["node-0"]
    bundle["metadata"] = bundle["metadata"][:1]
    bundle["chains"] = bundle["chains"][:1]
    for row in altered["observations"]:
        if row["changed_path"] == "backend/uv.lock":
            row["dependency_chain"] = bundle["chains"]
            row["nominations"] = row["nominations"][:1]
    for packet in bundle["consumer_packets"]:
        packet["selected_node_ids"] = ["node-0"]
    altered["dependency_admission"]["changed_node_ids"] = ["node-0"]

    with pytest.raises(ValueError, match="Bounded dependency proof changed"):
        owner.require_current_plan(
            repository, plan.model_copy(update={"evidence": altered})
        )


@pytest.mark.parametrize("retain_annotation", (True, False))
def test_dependency_metadata_drift_refuses_execution_before_any_native_owner(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_collection: list[tuple[str, ...]],
    retain_annotation: bool,
) -> None:
    base, _lock, _manifest = _dependency_update(repository)
    plan = owner.admit_change(
        repository,
        base=base,
        nominator=lambda _root, paths, **options: _dependency_evidence(
            paths, **options
        ),
    )
    assert not plan.unverified
    if not retain_annotation:
        evidence = copy.deepcopy(plan.evidence)
        evidence.pop("dependency_admission")
        plan = plan.model_copy(update={"evidence": evidence})

    def drift(_bundle: dict[str, Any]) -> None:
        raise ValueError("Installed dependency metadata changed")

    def forbidden(*_args: Any) -> list[Any]:
        raise AssertionError("metadata must be current before command planning")

    monkeypatch.setattr(owner, "require_current_dependency_environment", drift)
    monkeypatch.setattr(owner, "_commands", forbidden)
    with pytest.raises(ValueError, match="metadata changed"):
        owner.execute_change(repository, plan, mode="merge", workers=1)


def test_dependency_policy_does_not_weaken_unrelated_source_owner_selection(
    repository: Path, dependency_collection: list[tuple[str, ...]]
) -> None:
    base, _lock, _manifest = _dependency_update(repository)
    (repository / "behavior.py").write_text("value = 1\n")

    def nominate(_root: Path, paths: tuple[str, ...], **options: Any) -> dict[str, Any]:
        evidence = _dependency_evidence(paths, **options)
        next(
            row
            for row in evidence["observations"]
            if row["changed_path"] == "behavior.py"
        )["edit_candidates"] = ["another.py"]
        return evidence

    plan = owner.admit_change(repository, base=base, nominator=nominate)
    assert "PRODUCER_SELECTED_ANOTHER_OWNER:behavior.py" in plan.unverified
    assert dependency_collection == []


def test_native_base_lock_transport_never_relabels_normalized_newlines_as_original_bytes(
    repository: Path,
) -> None:
    _dependency_update(repository)
    lock = repository / "backend/uv.lock"
    lock.write_bytes(b"version = 1\r\nrevision = 1\r\n")
    owner._git(repository, "-c", "core.autocrlf=false", "add", "backend/uv.lock")
    owner._git(
        repository,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "CRLF historical lock",
    )
    base = owner._git(repository, "rev-parse", "HEAD").strip()
    lock.write_bytes(b"version = 1\nrevision = 2\n")

    def forbidden(*_args: Any, **_options: Any) -> dict[str, Any]:
        raise AssertionError("lossy base lock bytes cannot reach producer evidence")

    with pytest.raises(ValueError, match="exact source bytes"):
        owner.admit_change(repository, base=base, nominator=forbidden)
