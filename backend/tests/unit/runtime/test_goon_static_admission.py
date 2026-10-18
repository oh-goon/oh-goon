from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from oh_goon.repository_tooling.goon_static_admission import (
    REQUIREMENTS_FILE,
    compile_canonical_goons,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _copy_static_inputs(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(_root() / "templates" / "goons", root / "templates" / "goons")
    (root / "backend").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_root() / REQUIREMENTS_FILE, root / REQUIREMENTS_FILE)
    (root / "backend/src/oh_goon").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        _root() / "backend/src/oh_goon/canonical_goon_tool_authority.json",
        root / "backend/src/oh_goon",
    )
    shutil.copy2(_root() / "Makefile", root / "Makefile")
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_root() / "frontend/package.json", root / "frontend/package.json")
    shutil.copy2(_root() / "frontend/package-lock.json", root / "frontend/package-lock.json")
    for script in (_root() / "scripts").rglob("*.sh"):
        target = root / script.relative_to(_root())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(script, target)
    return root


def _regenerate_tool_authority(root: Path) -> None:
    from oh_goon.repository_tooling.generation.goon_tool_authority import (
        write_goon_tool_authority,
    )

    write_goon_tool_authority(
        root=root,
        output=root / "backend/src/oh_goon/canonical_goon_tool_authority.json",
    )


def test_missing_generated_tool_authority_fails_static_admission_as_source_error(
    tmp_path: Path,
) -> None:
    root = _copy_static_inputs(tmp_path)
    (root / "backend/src/oh_goon/canonical_goon_tool_authority.json").unlink()

    payload = compile_canonical_goons(root)

    assert payload["status"] == "FAIL"
    assert payload["issues"][0]["code"] == "GOON_TOOL_AUTHORITY_INVALID"
    assert "canonical_goon_tool_authority.json" in payload["issues"][0]["message"]


def test_every_shipped_canonical_goon_passes_static_admission_without_execution() -> None:
    payload = compile_canonical_goons(_root())
    assert payload["status"] == "PASS"
    assert payload["goon_count"] == len(tuple((_root() / "templates/goons").rglob("goon.yml")))
    assert payload["step_count"] > 0
    assert payload["issues"] == []
    assert payload["authority_closures_proven"] == payload["authority_closure_count"]
    assert payload["unresolved_execution_edge_count"] == 0
    assert str(payload["requirements_identity"]).startswith("sha256:")
    assert str(payload["execution_authority_identity"]).startswith("sha256:")


def test_missing_independent_tool_permission_fails_before_workload_execution(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["workload_tool_permissions"]["make:test-python-complete"].remove("ldd")
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    assert payload["status"] == "FAIL"
    issue = next(item for item in payload["issues"] if item["code"] == "TOOL_PERMISSION_MISSING")
    assert issue["goon"] == "oh-goon/backend-tests"
    assert issue["step_id"] == "make"
    assert issue["missing_tools"] == ["ldd"]


def test_canonical_goon_yaml_does_not_repeat_derived_native_tool_sets() -> None:
    for path in (_root() / "templates/goons/oh-goon").glob("*/goon.yml"):
        text = path.read_text(encoding="utf-8")
        assert "\ntools:\n" not in text, path
        assert "  allow:" not in text, path


def _materialize_makefile_uv_console_wrappers(root: Path) -> tuple[str, ...]:
    import re

    makefile = (root / "Makefile").read_text(encoding="utf-8")
    names = tuple(
        sorted(
            set(
                re.findall(
                    r"backend/\.venv/bin/(?P<name>oh-goon(?:-[A-Za-z0-9-]+)?)",
                    makefile,
                )
            )
        )
    )
    for name in names:
        wrapper = root / "backend/.venv/bin" / name
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(
            "#!/usr/bin/python3\n"
            "from oh_goon.cli import main\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return names


def test_complete_static_admission_is_invariant_after_uv_entrypoint_family_materialization(
    tmp_path: Path,
) -> None:
    root = _copy_static_inputs(tmp_path)
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_root() / "frontend/package.json", root / "frontend/package.json")
    shutil.copy2(_root() / "frontend/package-lock.json", root / "frontend/package-lock.json")
    before = compile_canonical_goons(root)

    materialized = _materialize_makefile_uv_console_wrappers(root)
    after = compile_canonical_goons(root)

    assert len(materialized) >= 10
    assert "oh-goon" in materialized
    assert "oh-goon-architecture" in materialized
    assert "oh-goon-check-generated" in materialized
    assert "oh-goon-test-batches" in materialized
    assert before == after
    assert after["status"] == "PASS"
    assert after["authority_closures_proven"] == after["authority_closure_count"]
    assert after["unresolved_execution_edge_count"] == 0


def test_backend_test_tool_authority_is_stable_after_uv_entrypoint_materialization(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

    root = _copy_static_inputs(tmp_path)
    expected = canonical_step_required_tools(
        root, goon="oh-goon/backend-tests", step_id="make"
    )
    wrapper = root / "backend/.venv/bin/oh-goon-test-batches"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "#!/usr/bin/python3\n"
        "from oh_goon.repository_tooling.test_batches import main\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    observed = canonical_step_required_tools(
        root, goon="oh-goon/backend-tests", step_id="make"
    )

    assert observed == expected
    assert observed == ("git", "ldd", "node", "npm", "sh", "true", "uv")


def test_make_workload_owner_is_shared_by_every_canonical_consumer() -> None:
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

    expected = ("sh",)
    assert canonical_step_required_tools(
        _root(), goon="oh-goon/integration-contract", step_id="make"
    ) == expected
    assert canonical_step_required_tools(
        _root(), goon="oh-goon/integration-tests", step_id="make"
    ) == expected


def test_certification_make_workloads_admit_transitive_shell_and_workspace_tools() -> None:
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

    assert canonical_step_required_tools(
        _root(), goon="oh-goon/tooling-tests", step_id="make"
    ) == ("bash", "dd", "git", "node", "sh", "uv")
    assert canonical_step_required_tools(
        _root(), goon="oh-goon/backend-p0", step_id="make"
    ) == ("git", "ldd", "sh", "uv")
    assert canonical_step_required_tools(
        _root(), goon="oh-goon/architecture", step_id="make"
    ) == ()


def test_architecture_does_not_require_package_manager_tool_authority() -> None:
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

    assert canonical_step_required_tools(
        _root(), goon="oh-goon/architecture", step_id="make"
    ) == ()

def test_shell_runtime_support_contract_is_execution_authority_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    import oh_goon.integrations.workload_execution_requirements as workload_requirements

    root = _copy_static_inputs(tmp_path)
    before = workload_requirements.load_workload_execution_requirements(root)
    monkeypatch.setattr(
        workload_requirements,
        "SHELL_RUNTIME_SUPPORT_TOOLS",
        (*workload_requirements.SHELL_RUNTIME_SUPPORT_TOOLS, "wc"),
    )
    after = workload_requirements.load_workload_execution_requirements(root)

    assert after.identity != before.identity


def test_tooling_complete_opaque_runtime_authority_is_explicit_and_proven(tmp_path: Path) -> None:
    from oh_goon.integrations.workload_execution_requirements import (
        load_workload_execution_requirements,
        required_tools_for_owner,
    )

    root = _copy_static_inputs(tmp_path)
    requirements = load_workload_execution_requirements(root)
    owner = "make:test-tooling-complete"

    assert required_tools_for_owner(requirements, owner) == ("bash", "dd", "git", "node", "sh", "uv")
    for tool in ("bash", "dd", "git", "node"):
        assert (
            tool,
            f"test-tooling-complete -> <opaque-owned-boundary> -> {tool}",
        ) in requirements.provenance_by_owner[owner]

    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8").replace(
            "# OH_GOON_OPAQUE_TOOLS: bash dd git node uv\ntest-tooling-complete:",
            "test-tooling-complete:",
        ),
        encoding="utf-8",
    )
    reduced = load_workload_execution_requirements(root)
    assert required_tools_for_owner(reduced, owner) == ("sh",)
    assert reduced.identity != requirements.identity


@pytest.mark.parametrize("tool", ("dd", "git"))
def test_tooling_runtime_need_must_fit_independent_permission(
    tmp_path: Path, tool: str
) -> None:
    root = _copy_static_inputs(tmp_path)
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["workload_tool_permissions"]["make:test-tooling-complete"].remove(tool)
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    issue = next(
        item
        for item in payload["issues"]
        if item["code"] == "TOOL_PERMISSION_MISSING"
        and item.get("goon") == "oh-goon/tooling-tests"
    )
    assert issue["missing_tools"] == [tool]
    assert issue["authority_paths"] == [
        f"test-tooling-complete -> <opaque-owned-boundary> -> {tool}"
    ]


def test_candidate_added_executable_requirement_cannot_self_authorize(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8").replace(
            "# OH_GOON_OPAQUE_TOOLS: git ldd uv\nverify-p0:",
            "# OH_GOON_OPAQUE_TOOLS: curl git ldd uv\nverify-p0:",
        ),
        encoding="utf-8",
    )

    payload = compile_canonical_goons(root)

    issue = next(
        item
        for item in payload["issues"]
        if item["code"] == "TOOL_PERMISSION_MISSING"
        and item.get("goon") == "oh-goon/backend-p0"
    )
    assert issue["missing_tools"] == ["curl"]
    assert issue["authority_paths"] == ["verify-p0 -> <opaque-owned-boundary> -> curl"]


def test_generated_tool_transport_must_match_current_derived_need(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8").replace(
            "# OH_GOON_OPAQUE_TOOLS: git ldd uv\nverify-p0:",
            "# OH_GOON_OPAQUE_TOOLS: curl git ldd uv\nverify-p0:",
        ),
        encoding="utf-8",
    )
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    permitted = registry["workload_tool_permissions"]["make:verify-p0"]
    registry["workload_tool_permissions"]["make:verify-p0"] = sorted([*permitted, "curl"])
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    issue = next(
        item
        for item in payload["issues"]
        if item["code"] == "TOOL_AUTHORITY_MISSING"
        and item.get("goon") == "oh-goon/backend-p0"
    )
    assert issue["missing_tools"] == ["curl"]


def test_make_recipe_shell_is_derived_without_handwritten_tool_allow() -> None:
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

    path = _root() / "templates/goons/oh-goon/tooling-tests/goon.yml"
    text = path.read_text(encoding="utf-8")
    assert "tools:" not in text
    assert canonical_step_required_tools(
        _root(), goon="oh-goon/tooling-tests", step_id="make"
    ) == ("bash", "dd", "git", "node", "sh", "uv")


def test_make_workload_requirement_change_invalidates_every_consumer_permission(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    makefile = root / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    text = text.replace(
        "test-integration:",
        "# OH_GOON_OPAQUE_TOOLS: bash\ntest-integration:",
    )
    makefile.write_text(text, encoding="utf-8")

    payload = compile_canonical_goons(root)

    affected = {
        (item.get("goon"), tuple(item.get("missing_tools", ())))
        for item in payload["issues"]
        if item["code"] == "TOOL_PERMISSION_MISSING"
    }
    assert ("oh-goon/integration-contract", ("bash",)) in affected
    assert ("oh-goon/integration-tests", ("bash",)) in affected


def test_make_workload_owner_is_derived_from_compiled_step_without_registry_mirror(
    tmp_path: Path,
) -> None:
    root = _copy_static_inputs(tmp_path)
    path = root / "templates/goons/oh-goon/integration-contract/goon.yml"
    text = path.read_text(encoding="utf-8").replace(
        "run: make test-integration", "run: make architecture"
    )
    path.write_text(text, encoding="utf-8")
    _regenerate_tool_authority(root)

    payload = compile_canonical_goons(root)

    assert payload["status"] == "PASS"
    assert payload["issues"] == []
    generated = json.loads(
        (root / "backend/src/oh_goon/canonical_goon_tool_authority.json").read_text(
            encoding="utf-8"
        )
    )
    assert generated["goons"]["oh-goon/integration-contract"]["make"] == []


def test_permission_ceiling_may_be_broader_without_broadening_runtime_step(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    permitted = registry["workload_tool_permissions"]["make:test-python-complete"]
    registry["workload_tool_permissions"]["make:test-python-complete"] = sorted(
        [*permitted, "curl"]
    )
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    assert payload["status"] == "PASS"
    from oh_goon.runtime.goon_templates import GoonTemplateStore

    compiled = GoonTemplateStore(root / "templates/goons").source("oh-goon/backend-tests").compiled
    assert "curl" not in compiled.steps[0].capabilities.tools
    assert compiled.steps[0].capabilities.tools == (
        "git", "ldd", "node", "npm", "sh", "true", "uv"
    )


def test_new_direct_canonical_goon_needs_no_mirrored_execution_policy(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    package = root / "templates/goons/general/new-check"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text("version: 1\nsteps:\n- run: 'true'\n", encoding="utf-8")
    (package / "README.md").write_text("# New check\n", encoding="utf-8")
    before_policy = (root / REQUIREMENTS_FILE).read_bytes()
    _regenerate_tool_authority(root)

    payload = compile_canonical_goons(root)

    assert payload["status"] == "PASS"
    assert (root / REQUIREMENTS_FILE).read_bytes() == before_policy


def test_policy_registry_contains_no_compiled_goon_step_mirrors() -> None:
    registry = json.loads((_root() / REQUIREMENTS_FILE).read_text(encoding="utf-8"))
    assert set(registry) == {"schema", "workload_tool_permissions"}
    assert "goons" not in registry
    text = json.dumps(registry)
    for mirrored_field in (
        "step_id",
        "kind",
        "required_host_environment",
        "required_egress_mode",
        "required_working_directory",
        "required_cache_source_kind",
        "required_artifact_count",
        "required_output_count",
        "required_tools_owner",
    ):
        assert mirrored_field not in text


def test_compiler_owned_non_tool_step_change_requires_no_policy_edit(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    path = root / "templates/goons/python/uv-audit/goon.yml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("mode: host_unrestricted", "mode: none")
    path.write_text(text, encoding="utf-8")
    before_policy = (root / REQUIREMENTS_FILE).read_bytes()

    payload = compile_canonical_goons(root)

    assert payload["status"] == "PASS"
    assert (root / REQUIREMENTS_FILE).read_bytes() == before_policy


def test_policy_shape_rejects_noncanonical_permission_list(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["workload_tool_permissions"]["make:test-python-complete"] = ["uv", "uv"]
    registry_path.write_text(json.dumps(registry, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    assert any(item["code"] == "NON_CANONICAL_TOOL_PERMISSION" for item in payload["issues"])


def test_policy_shape_rejects_non_make_owner(tmp_path: Path) -> None:
    root = _copy_static_inputs(tmp_path)
    registry_path = root / REQUIREMENTS_FILE
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["workload_tool_permissions"]["python:controller"] = []
    registry_path.write_text(json.dumps(registry, sort_keys=True) + "\n", encoding="utf-8")

    payload = compile_canonical_goons(root)

    assert any(item["code"] == "REQUIREMENTS_INVALID" for item in payload["issues"])
