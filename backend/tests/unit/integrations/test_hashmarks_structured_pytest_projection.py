from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")
from hashmarks.verification_selection import (
    VerificationSelectionEnvelopeState,
    downstream_consumption_contract,
    verification_member,
    verification_membership,
    verification_selection_envelope,
)

from oh_goon.core.models.pytest_selection import StructuredPytestMember, StructuredPytestMemberError
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.hashmarks.verification_selection import (
    HashmarksVerificationSelectionInputError,
    load_hashmarks_verification_selection,
)
from oh_goon.repository_tooling.hashmarks_verification_execution import (
    VerificationExecutionGroupingPolicy,
    build_verification_execution_plan,
    build_verification_execution_receipt,
)
from oh_goon.runtime.execution import shards as shard_runtime
from oh_goon.runtime.execution.pytest_projection import project_pytest_argv
from tests.factories.runtime_authority import authorized_command_result
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
)

pytestmark = pytest.mark.dependency_complete


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _context() -> shard_runtime.ShardExecutionContext:
    return shard_runtime.ShardExecutionContext(_sha("1"), _sha("2"), _sha("3"))


def _admit(tmp_path: Path, specs: list[tuple[str, str | None]]):
    membership = verification_membership(
        [verification_member(path, test_symbol=symbol) for path, symbol in specs]
    )
    envelope = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity="git-tree:structured-projection",
            source_identity=_sha("a"),
            codemap_generation=1,
            identity_generation=1,
            stale=False,
            membership=membership,
            owner_evidence={"selected": "src/x.py"},
            evidence_hashes={"verification_plan": _sha("b")},
        )
    )
    contract = downstream_consumption_contract(envelope)
    contract_path = tmp_path / "contract.json"
    envelope_path = tmp_path / "envelope.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return load_hashmarks_verification_selection(
        contract_path,
        envelope_path,
        admitted_repository_identity="git-tree:structured-projection",
        admitted_source_identity=_sha("a"),
        expected_producer_artifact_identity=TEST_HASHMARKS_ARTIFACT_IDENTITY,
    )


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_x.py::test_x",
        "./tests/test_x.py",
        "tests/../test_x.py",
        r"tests\test_x.py",
        "/tmp/test_x.py",
    ],
)
def test_core_rejects_ambiguous_path_representations(path: str) -> None:
    with pytest.raises(ValueError):
        StructuredPytestMember(path, None, _sha("f"))


def test_hashmarks_normalized_relative_path_is_admitted_without_oh_goon_rewrite(
    tmp_path: Path,
) -> None:
    posix = _admit(tmp_path, [("./tests/test_x.py", None)])
    windows = _admit(tmp_path, [(r"tests\test_x.py", None)])
    assert posix.members[0].path == windows.members[0].path == "tests/test_x.py"
    assert posix.members[0].member_id == windows.members[0].member_id


@pytest.mark.parametrize("symbol", ["::test_x", "test_x::", "test_x[param", "test_x]param["])
def test_admission_rejects_ambiguous_node_id_representations(tmp_path: Path, symbol: str) -> None:
    with pytest.raises(HashmarksVerificationSelectionInputError, match="projected safely"):
        _admit(tmp_path, [("tests/test_x.py", symbol)])


@pytest.mark.parametrize(
    ("path", "symbol", "selector"),
    [
        ("-q", None, "-q"),
        ("--help", None, "--help"),
        ("tests/space name.py", "test spaced", "tests/space name.py::test spaced"),
        ("tests/ü.py", "test_å", "tests/ü.py::test_å"),
        (
            "tests/test_nested.py",
            "TestOuter::TestInner::test_x[param::value]",
            "tests/test_nested.py::TestOuter::TestInner::test_x[param::value]",
        ),
        (
            "tests/test_brackets.py",
            "test_x[a-b_[]!@# --help -q]",
            "tests/test_brackets.py::test_x[a-b_[]!@# --help -q]",
        ),
    ],
)
def test_adversarial_but_unambiguous_members_remain_exact_data(
    path: str, symbol: str | None, selector: str
) -> None:
    member = StructuredPytestMember(path, symbol, _sha("7"))
    assert member.executable_selector == selector
    projected_selector = f"./{selector}" if path.startswith("-") else selector
    assert project_pytest_argv(("pytest", "-q"), (member,)) == (
        "pytest",
        "-q",
        "--",
        projected_selector,
    )
    assert member.member_id == _sha("7")
    assert member.projection_identity != member.member_id


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_x.py::test_x",
        "/tmp/test_x.py",
        "C:/repo/tests/test_x.py",
        r"C:\\repo\\tests\\test_x.py",
        r"tests\\test_x.py",
        "./tests/test_x.py",
        "tests/./test_x.py",
        "tests/../test_x.py",
        "tests//test_x.py",
        "tests/\ttest_x.py",
        "tests/\x1btest_x.py",
        "tests/\x7ftest_x.py",
    ],
)
def test_core_rejects_path_forms_that_are_ambiguous_or_control_bearing(path: str) -> None:
    with pytest.raises(StructuredPytestMemberError):
        StructuredPytestMember(path, None, _sha("8"))


@pytest.mark.parametrize(
    "symbol",
    [
        "::test_x",
        "test_x::",
        "test_x::::nested",
        "test_x[param",
        "test_x]param[",
        "test_x\t[param]",
        "test_x\x1b[param]",
        "test_x\x7f[param]",
    ],
)
def test_core_rejects_ambiguous_or_control_bearing_symbols(symbol: str) -> None:
    with pytest.raises(StructuredPytestMemberError):
        StructuredPytestMember("tests/test_x.py", symbol, _sha("9"))


def test_structured_symbol_keeps_nested_and_parametrized_components_exact() -> None:
    member = StructuredPytestMember(
        path="tests/ü space.py",
        test_symbol="TestOuter::TestInner::test_x[param::value -q []!@#]",
        member_id=_sha("c"),
    )
    assert member.node_components == (
        "TestOuter",
        "TestInner",
        "test_x[param::value -q []!@#]",
    )
    assert member.executable_selector == (
        "tests/ü space.py::TestOuter::TestInner::test_x[param::value -q []!@#]"
    )


def test_projector_terminates_options_before_option_looking_members() -> None:
    members = (
        StructuredPytestMember("-q.py", None, _sha("d")),
        StructuredPytestMember("--.py", "-q", _sha("e")),
    )
    assert project_pytest_argv(("pytest", "-q"), members) == (
        "pytest",
        "-q",
        "--",
        "./-q.py",
        "./--.py::-q",
    )


def test_projector_rejects_duplicate_executable_projection() -> None:
    members = (
        StructuredPytestMember("tests/test_x.py", "test_x", _sha("d")),
        StructuredPytestMember("tests/test_x.py", "test_x", _sha("e")),
    )
    with pytest.raises(ValueError, match="duplicate executable projections"):
        project_pytest_argv(("pytest",), members)


def test_projector_rejects_duplicate_projection_identity_even_when_selectors_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = (
        StructuredPytestMember("tests/test_a.py", "test_a", _sha("a")),
        StructuredPytestMember("tests/test_b.py", "test_b", _sha("b")),
    )
    monkeypatch.setattr(
        StructuredPytestMember,
        "projection_identity",
        property(lambda _self: _sha("c")),
    )
    with pytest.raises(ValueError, match="duplicate projection identities"):
        project_pytest_argv(("pytest",), members)


@pytest.mark.parametrize("filename", ["-q.py", "--help.py"])
def test_real_pytest_execution_cannot_turn_option_looking_path_into_option(
    tmp_path: Path, filename: str
) -> None:
    (tmp_path / filename).write_text(
        "def test_option_guard():\n    assert True\n", encoding="utf-8"
    )
    selection = _admit(tmp_path, [(filename, "test_option_guard")])
    plan = build_verification_execution_plan(
        selection,
        command_prefix=(sys.executable, "-m", "pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    assert plan.shards[0].argv[-2:] == ("--", f"./{filename}::test_option_guard")
    report = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=20,
        execution_context=_context(),
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert report.passed is True
    assert receipt.terminal_success is True
    assert receipt.missing == receipt.duplicates == receipt.extras == ()


def test_structured_identity_survives_bisection_resume_and_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selection = _admit(
        tmp_path,
        [
            ("-q.py", None),
            ("tests/space name.py", "TestClass::test_method"),
            ("tests/ü.py", "test_unicode[param::x]"),
            ("tests/punctuation.py", "TestOuter::TestInner::test_x[a-b_[]!@#]"),
        ],
    )
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("fake-pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(4),
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        selected = args[args.index("--") + 1 :]
        if len(selected) == 4:
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(shard_runtime, "run_command", fake_run)
    state = tmp_path / "state"
    first = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    first_calls = tuple(calls)
    second = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    receipt = build_verification_execution_receipt(selection, second)

    assert first.passed and second.passed
    assert tuple(calls) == first_calls
    assert receipt.terminal_success is True
    assert receipt.missing == receipt.duplicates == receipt.extras == ()
    expected_projections = tuple(member.projection_identity for member in selection.members)
    assert receipt.selected_projection_identities == expected_projections
    assert receipt.terminal_projection_identities == expected_projections
    lineage_projections = {
        identity
        for row in receipt.retry_bisection_lineage
        for identity in row["pytest_projection_identities"]  # type: ignore[index]
    }
    assert lineage_projections == set(expected_projections)
    assert all("--" in call for call in calls)
    assert all(call.index("--") >= 1 for call in calls)


def test_refinement_children_explicitly_bind_original_admitted_member_identities(
    tmp_path: Path,
) -> None:
    selection = _admit(
        tmp_path,
        [
            ("tests/a.py", "test_a"),
            ("tests/b.py", "test_b"),
            ("tests/c.py", "test_c"),
            ("tests/d.py", "test_d"),
        ],
    )
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(4),
    )
    parent = plan.shards[0]
    left, right = shard_runtime._refine_children(parent)
    expected = tuple(member.member_id for member in selection.members)
    assert parent.original_admitted_member_identities == expected
    assert left.original_admitted_member_identities == expected[:2]
    assert right.original_admitted_member_identities == expected[2:]
    assert (
        left.original_admitted_member_identities + right.original_admitted_member_identities
        == expected
    )
    assert left.parent_work_identity == right.parent_work_identity == parent.work_identity


def _leaf_results(result: shard_runtime.ShardResult) -> tuple[shard_runtime.ShardResult, ...]:
    if not result.children:
        return (result,)
    return tuple(leaf for child in result.children for leaf in _leaf_results(child))


def _assert_recovery_identity_conservation(selection, plan, report) -> None:
    assert report.selected_membership_identity == selection.membership_identity
    assert report.producer_artifact_identity == selection.producer_artifact_identity
    assert report.producer_implementation_identity == selection.producer_implementation_identity
    assert report.repository_content_identity == selection.repository_identity
    assert report.verification_revision_identity == selection.source_identity
    leaves = tuple(leaf for root in report.results for leaf in _leaf_results(root))
    leaf_members = tuple(member for leaf in leaves for member in leaf.pytest_members)
    assert len(leaf_members) == len(selection.members)
    observed = {member.executable_selector: member for member in leaf_members}
    expected = {member.executable_selector: member for member in selection.members}
    assert observed.keys() == expected.keys()
    assert len(observed) == len(leaf_members)
    for selector, admitted in expected.items():
        recovered = observed[selector]
        assert recovered.member_id == admitted.member_id
        assert recovered.projection_identity == admitted.projection_identity
    assert all(
        leaf.parent_work_identity is not None for leaf in leaves if leaf.refinement_depth > 0
    )
    assert plan.selected_membership_identity == selection.membership_identity


@pytest.mark.parametrize(
    "specs",
    [
        [
            ("tests/fast_a.py", "test_fast_a"),
            ("tests/fast_b.py", "test_fast_b"),
            ("tests/hang.py", "test_hang"),
            ("tests/fast_c.py", "test_fast_c"),
        ],
        [
            ("tests/hang_a.py", "test_hang_a"),
            ("tests/fast.py", "test_fast"),
            ("tests/hang_b.py", "test_hang_b"),
        ],
    ],
)
def test_timeout_bisection_to_singletons_conserves_original_structured_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    specs: list[tuple[str, str | None]],
) -> None:
    selection = _admit(tmp_path, specs)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("fake-pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(len(specs)),
    )

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        selected = args[args.index("--") + 1 :]
        if any("hang" in selector for selector in selected):
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(shard_runtime, "run_command", fake_run)
    report = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        execution_context=_context(),
    )
    _assert_recovery_identity_conservation(selection, plan, report)
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.terminal_success is False
    assert set(receipt.missing) == {
        member.executable_selector
        for member in selection.members
        if "hang" in member.executable_selector
    }
    assert receipt.duplicates == receipt.extras == ()
    assert receipt.selected_projection_identities == tuple(
        member.projection_identity for member in selection.members
    )


def test_restart_after_partial_completion_resumes_only_terminal_members_and_keeps_identities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selection = _admit(
        tmp_path,
        [
            ("tests/fast.py", "test_fast"),
            ("tests/hang.py", "test_hang"),
        ],
    )
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("fake-pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    state = tmp_path / "state-partial"
    calls: list[tuple[str, ...]] = []
    allow_hang_to_finish = False

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        if "hang" in args[-1] and not allow_hang_to_finish:
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(shard_runtime, "run_command", fake_run)
    first = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    _assert_recovery_identity_conservation(selection, plan, first)
    first_calls = tuple(calls)
    first_status = {result.members[0]: result.status for result in first.results}
    assert first_status == {
        "tests/fast.py::test_fast": "PASS",
        "tests/hang.py::test_hang": "TIMEOUT",
    }

    allow_hang_to_finish = True
    second = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    _assert_recovery_identity_conservation(selection, plan, second)
    second_status = {result.members[0]: result.status for result in second.results}
    assert second_status == {
        "tests/fast.py::test_fast": "RESUMED_PASS",
        "tests/hang.py::test_hang": "PASS",
    }
    assert len(calls) == len(first_calls) + 1
    receipt = build_verification_execution_receipt(selection, second)
    assert receipt.terminal_success is True
    assert receipt.missing == receipt.duplicates == receipt.extras == ()
    assert receipt.terminal_projection_identities == tuple(
        member.projection_identity for member in selection.members
    )


def test_real_timeout_cleanup_preserves_structured_member_identity(
    tmp_path: Path,
) -> None:
    slow = tmp_path / "slow.py"
    slow.write_text(
        "import time\n\ndef test_hang():\n    time.sleep(5)\n",
        encoding="utf-8",
    )
    selection = _admit(tmp_path, [("slow.py", "test_hang")])
    plan = build_verification_execution_plan(
        selection,
        command_prefix=(sys.executable, "-m", "pytest", "-q"),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    report = shard_runtime.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=0.2,
        execution_context=_context(),
    )
    _assert_recovery_identity_conservation(selection, plan, report)
    result = report.results[0]
    assert result.status == "TIMEOUT"
    assert result.pytest_members == selection.members
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "TIMEOUT"
    assert result.attempts[0].physical_work_identity == result.work_identity


def test_structured_work_rejects_missing_or_reconstructed_original_member_identity(
    tmp_path: Path,
) -> None:
    selection = _admit(tmp_path, [("tests/a.py", "test_a")])
    member = selection.members[0]
    with pytest.raises(ValueError, match="original admitted member identities"):
        shard_runtime.ShardWork(
            shard_id="bad",
            argv=project_pytest_argv(("pytest",), (member,)),
            work_identity="sha256:" + "a" * 64,
            members=(member.executable_selector,),
            command_prefix=("pytest",),
            pytest_members=(member,),
            original_admitted_member_identities=("sha256:" + "f" * 64,),
        )
