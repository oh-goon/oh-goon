from __future__ import annotations

import ast
import json
from pathlib import Path


def _fix_source_identity(monkeypatch, value: str) -> None:
    from oh_goon.repository_tooling.release import package_slim

    monkeypatch.setattr(
        package_slim,
        "canonical_source_identity",
        lambda _root: "sha256:" + value * 64,
    )


def test_resume_context_changes_with_correctness_inputs_but_not_batch_grouping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import (
        build_resume_context,
        execution_plan_identity,
    )

    _fix_source_identity(monkeypatch, "a")
    root = Path(__file__).resolve().parents[4]
    test_file = tmp_path / "test_resume_identity.py"
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    base = dict(
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


def test_resume_context_manifest_is_atomic_and_auditable(tmp_path: Path, monkeypatch) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import build_resume_context

    _fix_source_identity(monkeypatch, "b")
    root = Path(__file__).resolve().parents[4]
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


def test_receipt_identity_module_keeps_heavy_product_imports_lazy() -> None:
    root = Path(__file__).resolve().parents[4]
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
