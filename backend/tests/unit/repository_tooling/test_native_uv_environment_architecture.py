from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_tooling_contract import (
    _check_native_uv_environment_ownership,
)


def _write_consumer(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _canonical_tree(root: Path) -> tuple[Path, Path]:
    owner = root / "backend/src/oh_goon/engine/native_uv_environment.py"
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text("def capture_native_uv_authority(): ...\n", encoding="utf-8")
    imported = "from oh_goon.engine.native_uv_environment import capture_native_uv_authority\n"
    tools = _write_consumer(root, "backend/src/oh_goon/engine/fleet_tools.py", imported)
    extensions = _write_consumer(root, "backend/src/oh_goon/engine/fleet_extensions.py", imported)
    _write_consumer(
        root,
        "backend/src/oh_goon/integrations/fleet_plan_managed_runtime.py",
        imported,
    )
    return tools, extensions


def test_managed_uv_consumers_must_use_shared_transport_owner(tmp_path: Path) -> None:
    tools, _extensions = _canonical_tree(tmp_path)
    assert _check_native_uv_environment_ownership(tmp_path) == []

    tools.write_text("def helper(): pass\n", encoding="utf-8")
    issues = _check_native_uv_environment_ownership(tmp_path)
    assert any("must use the shared native uv transport owner" in issue.message for issue in issues)


def test_managed_uv_consumers_cannot_reintroduce_environment_policy_under_new_names(
    tmp_path: Path,
) -> None:
    tools, extensions = _canonical_tree(tmp_path)
    with tools.open("a", encoding="utf-8") as stream:
        stream.write('SUPPORTED_NATIVE_INPUTS = ("UV_INDEX", "UV_CONFIG_FILE")\n')
    with extensions.open("a", encoding="utf-8") as stream:
        stream.write("def _runtime_environment(): return {}\n")

    issues = _check_native_uv_environment_ownership(tmp_path)
    messages = {issue.message for issue in issues}
    assert "managed uv consumer must not own an independent UV_* environment policy" in messages
    assert (
        "managed uv consumer must not own a second uv runtime-environment implementation"
        in messages
    )
