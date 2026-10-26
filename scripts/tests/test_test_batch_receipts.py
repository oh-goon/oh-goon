from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TypedDict
from unittest.mock import call, patch

import pytest


class _ResumeArguments(TypedDict):
    root: Path
    members: tuple[str, ...]
    resume_key: str
    state_root: Path
    granularity: str
    marker: str


def _fix_source_identity(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from oh_goon.repository_tooling.release import package_slim

    monkeypatch.setattr(
        package_slim,
        "canonical_source_identity",
        lambda _root: "sha256:" + value * 64,
    )


def test_plan_identity_reads_each_unique_file_once(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import test_plan_identity

    first = tmp_path / "test_first.py"
    second = tmp_path / "test_second.py"
    first.write_text("def test_first(): pass\n", encoding="utf-8")
    second.write_text("def test_second(): pass\n", encoding="utf-8")
    members = (
        "test_first.py::test_first[one]",
        f"{first}::TestGroup::test_other",
        "test_second.py::test_second",
        "test_first.py::test_first[one]",
    )

    with patch.object(Path, "open", autospec=True, side_effect=Path.open) as opened:
        test_plan_identity(members, root=tmp_path)

    assert opened.call_args_list == [call(first, "rb"), call(second, "rb")]


def test_plan_identity_preserves_order_duplicates_and_current_file_bytes(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import test_plan_identity

    first = tmp_path / "test_first.py"
    second = tmp_path / "test_second.py"
    first.write_text("def test_first(): pass\n", encoding="utf-8")
    second.write_text("def test_second(): pass\n", encoding="utf-8")
    members = (
        "test_first.py::test_first",
        "test_second.py::test_second",
        "test_first.py::test_first",
    )
    identity = test_plan_identity(members, root=tmp_path)

    assert test_plan_identity(iter(members), root=tmp_path) == identity
    assert (
        test_plan_identity((members[1], members[0], members[2]), root=tmp_path)
        != identity
    )
    assert test_plan_identity(members[:-1], root=tmp_path) != identity

    first.write_text("def test_first(): True\n", encoding="utf-8")
    assert test_plan_identity(members, root=tmp_path) != identity


@pytest.mark.process
@pytest.mark.parametrize("parameter_ids", [("a", "a][b"), ("a][b", "a")])
def test_native_parameter_ids_cannot_prefix_collide_in_completed_receipts(
    tmp_path: Path, parameter_ids: tuple[str, str]
) -> None:
    import os
    import subprocess
    import sys

    from oh_goon.repository_tooling.pytest_execution_membership import (
        ExecutionMembershipStore,
    )
    from oh_goon.repository_tooling.pytest_process_environment import (
        isolated_pytest_environment,
    )
    from oh_goon.repository_tooling.test_batch_receipts import (
        MemberReceiptStore,
        batch_identity,
    )

    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_parameter_ids.py"
    test_file.write_text(
        "import pytest\nclass TestGroup:\n"
        f"    @pytest.mark.parametrize('value', [1, 2], ids={parameter_ids!r})\n"
        "    def test_value(self, value): assert value in (1, 2)\n",
        encoding="utf-8",
    )
    members = tuple(
        f"{test_file}::TestGroup::test_value[{parameter_id}]"
        for parameter_id in parameter_ids
    )
    suite_identity = "sha256:" + "a" * 64
    plan_identity = "sha256:" + "b" * 64
    run_identity = "sha256:" + "c" * 64
    receipt_directory = tmp_path / "receipts"
    environment = isolated_pytest_environment(
        {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("OH_GOON_PYTEST_RECEIPT_")
            and name not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
        }
    )
    environment.update(
        {
            "OH_GOON_PYTEST_RECEIPT_DIRECTORY": str(receipt_directory),
            "OH_GOON_PYTEST_RECEIPT_SUITE_IDENTITY": suite_identity,
            "OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY": batch_identity(members),
            "OH_GOON_PYTEST_RECEIPT_EXECUTION_PLAN_IDENTITY": plan_identity,
            "OH_GOON_PYTEST_RECEIPT_RUN_IDENTITY": run_identity,
            "OH_GOON_PYTEST_RECEIPT_ATTEMPT_IDENTITY": "sha256:" + "d" * 64,
            "OH_GOON_PYTEST_RECEIPT_MEMBERS": json.dumps(members),
            "OH_GOON_PYTEST_RECEIPT_ROOT": str(tmp_path),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(root / "backend/pyproject.toml"),
            "-p",
            "oh_goon.repository_tooling.pytest_batch_receipts",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    correctness = MemberReceiptStore(receipt_directory, suite_identity=suite_identity)
    assert all(correctness.reusable(member) for member in members)
    membership, _ = ExecutionMembershipStore(
        receipt_directory,
        suite_identity=suite_identity,
        execution_plan_identity=plan_identity,
        run_identity=run_identity,
    ).finalize(selected=members, resumed=(), suite_passed=True, recovery_lineage=())
    assert membership.completed == tuple(sorted(members))
    assert membership.missing == ()
    assert membership.duplicates == ()
    assert membership.extras == ()
    assert membership.terminal_success


def test_resume_context_changes_with_correctness_inputs_but_not_batch_grouping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import (
        build_resume_context,
        execution_plan_identity,
    )

    _fix_source_identity(monkeypatch, "a")
    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_resume_identity.py"
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    base: _ResumeArguments = dict(
        root=root,
        members=(str(test_file),),
        resume_key="unit-resume",
        state_root=tmp_path / "state",
        granularity="files",
        marker="",
    )
    first = build_resume_context(**base)
    regrouped = build_resume_context(**base)
    first_execution = execution_plan_identity(
        suite_identity=first.suite_identity,
        batch_size=1,
        batch_timeout=10,
        timeout_bisection=False,
        workers=1,
        suite_timeout=60,
        environment_retries=0,
        selected_batch=None,
        scheduled_members=(str(test_file),),
        reused_members=(),
    )
    regrouped_execution = execution_plan_identity(
        suite_identity=regrouped.suite_identity,
        batch_size=8,
        batch_timeout=90,
        timeout_bisection=True,
        workers=2,
        suite_timeout=60,
        environment_retries=0,
        selected_batch=None,
        scheduled_members=(str(test_file),),
        reused_members=(),
    )
    assert first.suite_identity == regrouped.suite_identity
    assert first_execution != regrouped_execution
    assert first.receipt_directory == regrouped.receipt_directory

    test_file.write_text("def test_value(): assert 1 == 1\n", encoding="utf-8")
    changed = build_resume_context(**base)
    assert first.source_identity == changed.source_identity
    assert first.toolchain_identity == changed.toolchain_identity
    assert first.test_plan_identity != changed.test_plan_identity
    assert first.suite_identity != changed.suite_identity


def test_resume_context_manifest_is_atomic_and_auditable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import build_resume_context

    _fix_source_identity(monkeypatch, "b")
    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_manifest.py"
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    context = build_resume_context(
        root=root,
        members=(str(test_file),),
        resume_key="audit-resume",
        state_root=tmp_path / "state",
        granularity="files",
        marker="not slow",
        extra_context=("node=v24.20.0",),
    )
    path = context.ensure_manifest()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == context.manifest_payload()
    assert payload["source_identity"] == context.source_identity
    assert payload["test_plan_identity"] == context.test_plan_identity
    assert payload["toolchain_identity"] == context.toolchain_identity
    assert payload["extra_context"] == ["node=v24.20.0"]
    assert not list(path.parent.glob("*.tmp"))


def test_authored_report_name_mutation_invalidates_current_receipt_reuse(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import (
        MemberReceiptStore,
        build_resume_context,
    )

    root = tmp_path / "source"
    authored = root / "docs/test-results/analysis.md"
    authored.parent.mkdir(parents=True)
    authored.write_text("original\n", encoding="utf-8")
    test_file = root / "backend/tests/test_value.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    member = test_file.relative_to(root).as_posix()
    arguments: _ResumeArguments = dict(
        root=root,
        members=(member,),
        resume_key="source-reuse",
        state_root=tmp_path / "state",
        granularity="files",
        marker="",
    )
    original = build_resume_context(**arguments)
    history = MemberReceiptStore(
        original.receipt_directory, suite_identity=original.suite_identity
    )
    receipt = history.record(member, outcome="PYTEST_MEMBER_PASSED")
    assert history.reusable(member)

    authored.write_text("changed source\n", encoding="utf-8")
    current = build_resume_context(**arguments)
    current_receipts = MemberReceiptStore(
        current.receipt_directory, suite_identity=current.suite_identity
    )

    assert original.source_identity != current.source_identity
    assert original.test_plan_identity == current.test_plan_identity
    assert not current_receipts.reusable(member)
    assert receipt.is_file() and history.reusable(member)


def test_receipt_identity_module_keeps_heavy_product_imports_lazy() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "backend/src/oh_goon/repository_tooling/test_batch_receipts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.add(node.module)
    assert not any(name.startswith("oh_goon") for name in top_level_imports)
    source = path.read_text(encoding="utf-8")
    assert (
        "from oh_goon.repository_tooling.release.package_slim import canonical_source_identity"
        in source
    )
