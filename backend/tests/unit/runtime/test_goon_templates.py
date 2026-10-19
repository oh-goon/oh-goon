from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.core.models.fleet_plan import CommandPlanStep, CompiledGoonImportStep
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.runtime.goon_templates import GoonTemplateStore


def _package(root: Path, key: str, content: str) -> Path:
    category, name = key.split("/")
    directory = root / category / name
    directory.mkdir(parents=True)
    (directory / "goon.yml").write_text(content, encoding="utf-8")
    (directory / "README.md").write_text(f"# {key}\n", encoding="utf-8")
    return directory


def _atomic() -> str:
    return """version: 1
steps:
- run: 'true'
"""


def test_shipped_goon_catalog_uses_canonical_category_name_keys() -> None:
    store = GoonTemplateStore()
    keys = store.keys()

    assert keys == tuple(sorted(keys))
    assert "python/quality" in keys
    assert "python/uv-audit" in keys
    assert "security/trivy-audit" in keys
    assert "security/cve-combined" in keys
    assert all(key.count("/") == 1 for key in keys)
    assert not (store.root / "catalog.yml").exists()


def test_shipped_goon_catalog_uses_readme_description_and_path_identity() -> None:
    store = GoonTemplateStore()
    source = store.source("oh-goon/tooling-tests")
    document = store.read("oh-goon/tooling-tests")

    assert source.compiled.name == "tooling-tests"
    assert source.compiled.description == ""
    assert document.description == "Run the complete canonical repository tooling test suite."
    assert document.classification.value == "internal-self-hosting"


def test_atomic_trivy_and_uv_audit_goons_keep_execution_yaml_small() -> None:
    store = GoonTemplateStore()
    trivy = store.source("security/trivy-audit").compiled
    uv = store.source("python/uv-audit").compiled

    assert trivy.requirements == ()
    assert len(trivy.steps) == 1
    assert isinstance(trivy.steps[0], CommandPlanStep)
    assert trivy.steps[0].step_id == "trivy"
    assert trivy.steps[0].argv[:2] == ("trivy", "fs")
    assert "--disable-telemetry" in trivy.steps[0].argv
    assert trivy.steps[0].retry.max_attempts == 1

    assert uv.requirements == ()
    assert len(uv.steps) == 1
    assert isinstance(uv.steps[0], CommandPlanStep)
    assert uv.steps[0].step_id == "uv"
    assert uv.steps[0].argv[:3] == ("uv", "--preview", "audit")
    assert "--frozen" in uv.steps[0].argv
    assert uv.steps[0].retry.max_attempts == 1


def test_composite_goon_compiles_only_ordered_goon_edges() -> None:
    template = GoonTemplateStore().source("security/cve-combined").compiled

    assert [type(step) for step in template.steps] == [
        CompiledGoonImportStep,
        CompiledGoonImportStep,
    ]
    assert [step.goon for step in template.steps] == ["python/uv-audit", "security/trivy-audit"]


def test_atomic_and_composite_forms_are_mutually_exclusive() -> None:
    with pytest.raises(FleetPlanInvalid, match="declare exactly one, never both"):
        parse_goon_template("""version: 1\ngoons:\n- python/quality\nsteps:\n- run: 'true'\n""")


def test_only_canonical_goon_composition_forms_are_accepted() -> None:
    with pytest.raises(FleetPlanInvalid, match="declare exactly one"):
        parse_goon_template("""version: 1\nimports: [python/quality]\n""")
    with pytest.raises(FleetPlanInvalid, match="unsupported typed executor: template"):
        parse_goon_template(
            """version: 1\nsteps:\n- uses: template\n  id: child\n  template: goon:python/quality\n"""
        )


def test_goon_catalog_key_is_exact_category_name(tmp_path: Path) -> None:
    store = GoonTemplateStore(tmp_path)
    for invalid in (
        "quality",
        "Python/quality",
        "python_quality/x",
        "python/quality/extra",
        "../escape",
    ):
        with pytest.raises((FleetPlanSourceNotFound, FleetPlanInvalid), match="category/name"):
            store.source(invalid)


def test_goon_package_is_exact_two_file_source(tmp_path: Path) -> None:
    directory = _package(tmp_path, "general/valid", _atomic())
    store = GoonTemplateStore(tmp_path)
    assert store.read("general/valid").template_key == "general/valid"

    (directory / "extra.yml").write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(FleetPlanSourceNotFound, match="unexpected extra.yml"):
        store.read("general/valid")


def test_goon_catalog_ignores_only_windows_zone_identifier_transport_sidecars(
    tmp_path: Path,
) -> None:
    directory = _package(tmp_path, "general/valid", _atomic())
    (tmp_path / "README.md:Zone.Identifier").write_text("[ZoneTransfer]\n", encoding="utf-8")
    (tmp_path / "general" / "valid:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (directory / "README.md:Zone.Identifier").write_text("[ZoneTransfer]\n", encoding="utf-8")
    (directory / "goon.yml:Zone.Identifier:$DATA").write_text("[ZoneTransfer]\n", encoding="utf-8")

    store = GoonTemplateStore(tmp_path)

    assert store.keys() == ("general/valid",)
    assert store.read("general/valid").template_key == "general/valid"

    (directory / "goon.yml:other-stream").write_text("not transport metadata\n", encoding="utf-8")
    with pytest.raises(FleetPlanSourceNotFound, match="unexpected goon.yml:other-stream"):
        store.read("general/valid")


def test_shipped_goon_rejects_authored_name_drift_from_package_identity(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "general/valid",
        """version: 1
name: other
steps:
- run: 'true'
""",
    )
    with pytest.raises(FleetPlanInvalid, match="must match package name 'valid'"):
        GoonTemplateStore(tmp_path).source("general/valid")


def test_goon_source_snapshot_is_exact_bytes_and_readme_is_human_only(tmp_path: Path) -> None:
    directory = _package(tmp_path, "general/byte-exact", _atomic())
    path = directory / "goon.yml"
    raw = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(raw)
    store = GoonTemplateStore(tmp_path)
    before = store.source("general/byte-exact")

    (directory / "README.md").write_text("# Rewritten docs\n", encoding="utf-8")
    after = store.source("general/byte-exact")

    assert before.content.encode("utf-8") == raw
    assert before.sha256 == hashlib.sha256(raw).hexdigest()
    assert after.sha256 == before.sha256
    assert after.compiled == before.compiled


def test_oh_goon_certification_goons_are_atomic_canonical_command_delegates() -> None:
    store = GoonTemplateStore()
    expected = {
        "oh-goon/release-locks": ("make", "release-locks-check"),
        "oh-goon/release-identity": (
            "uv",
            "run",
            "--offline",
            "--frozen",
            "--no-sync",
            "--project",
            "backend",
            "oh-goon-release-version",
            "check",
        ),
        "oh-goon/generated-contracts": ("make", "check-generated"),
        "oh-goon/architecture": ("make", "architecture"),
        "oh-goon/backend-tests": ("make", "test-python-complete"),
        "oh-goon/tooling-tests": ("make", "test-tooling-complete"),
        "oh-goon/integration-tests": ("make", "test-integration"),
        "oh-goon/frontend-functional": ("make", "frontend-functional-certification"),
        "oh-goon/browser-harness-preflight": ("make", "browser-harness-preflight"),
        "oh-goon/backend-p0": ("make", "verify-p0"),
        "oh-goon/backend-p1": ("make", "verify-p1"),
        "oh-goon/integration-contract": ("make", "test-integration"),
        "oh-goon/local-runtime": ("make", "local-runtime-verification"),
        "oh-goon/template-startability": ("make", "template-startability-certification"),
        "oh-goon/browser-engine-preflight": ("make", "browser-preflight"),
        "oh-goon/browser-runtime-preflight": ("make", "browser-runtime-preflight"),
        "oh-goon/browser-targeted": ("make", "browser-targeted-certification"),
        "oh-goon/browser-required": ("make", "browser-required-certification"),
    }

    initialized_repository_goons = {
        "oh-goon/backend-p0",
        "oh-goon/tooling-tests",
        "oh-goon/architecture",
        "oh-goon/generated-contracts",
        "oh-goon/integration-tests",
        "oh-goon/frontend-functional",
        "oh-goon/local-runtime",
        "oh-goon/template-startability",
    }
    for key, argv in expected.items():
        compiled = store.source(key).compiled
        assert len(compiled.steps) == 1
        step = compiled.steps[0]
        assert isinstance(step, CommandPlanStep)
        assert step.argv == argv
        assert step.retry.max_attempts == 1
        assert step.workspace.publish is False
        expected_source = "repository" if key in initialized_repository_goons else "step"
        assert step.workspace.source.kind == expected_source
        if key == "oh-goon/local-runtime":
            assert step.capabilities.nested_strict_execution is False


def test_app_verification_goons_do_not_reacquire_dependencies_or_network() -> None:
    root = Path(__file__).resolve().parents[4] / "templates" / "goons" / "oh-goon"
    assert not (root / "dependency-materialize").exists()
    app = {
        "backend-p0",
        "architecture",
        "template-startability",
        "frontend-functional",
        "local-runtime",
    }
    for name in app:
        text = (root / name / "goon.yml").read_text(encoding="utf-8")
        assert "host_unrestricted" not in text
        assert "make init" not in text


def test_shipped_oh_goon_make_goons_exactly_match_derived_workload_tool_authority() -> None:
    from oh_goon.integrations.workload_execution_requirements import (
        load_workload_execution_requirements,
        make_target_owner,
        required_tools_for_owner,
    )

    root = Path(__file__).resolve().parents[4]
    requirements = load_workload_execution_requirements(root)
    store = GoonTemplateStore()
    checked = 0

    for key in store.keys():
        if not key.startswith("oh-goon/"):
            continue
        compiled = store.source(key).compiled
        for step in compiled.steps:
            if not isinstance(step, CommandPlanStep):
                continue
            owner = make_target_owner(step.argv)
            if owner is None:
                continue
            required = required_tools_for_owner(requirements, owner)
            assert tuple(step.capabilities.tools) == required, (
                key,
                step.step_id,
                owner,
                tuple(step.capabilities.tools),
                required,
            )
            checked += 1

    assert checked > 0
