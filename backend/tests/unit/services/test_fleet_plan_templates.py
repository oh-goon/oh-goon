from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.source_transport_metadata import authored_source_entries
from oh_goon.core.template_identity import TemplateClassification
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import analyze_fleet_plan_sources
from tests.factories.shipped_templates import GOON_TEMPLATES as GOONS
from tests.factories.shipped_templates import PLAN_TEMPLATES as PLANS


@pytest.mark.parametrize("catalog_name", ("goons", "workflow-catalog"))
def test_python_project_import_requires_explicit_runtime_independent_of_catalog_name(
    tmp_path: Path, catalog_name: str
) -> None:
    package = tmp_path / catalog_name / "general/check"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text("steps:\n- run: project-check\n")
    (package / "README.md").write_text("# general/check\n")
    (package / "pyproject.toml").write_text('[project]\nname = "project-check"\nversion = "1"\n')
    (package / "uv.lock").write_text("version = 1\n")
    plan = tmp_path / "plan.yml"
    plan.write_text(f"goons:\n- import: general/check\n  from: {catalog_name}\n")
    with pytest.raises(FleetPlanInvalid, match="select runtime: python"):
        FleetPlanResolver().resolve(plan)


def test_shipped_template_classification_is_category_owned() -> None:
    expected = {
        "agents": TemplateClassification.PRODUCTION,
        "advanced": TemplateClassification.TEACHING_EXAMPLE,
        "general": TemplateClassification.TEACHING_EXAMPLE,
        "kubernetes": TemplateClassification.PRODUCTION,
        "ml": TemplateClassification.TEACHING_EXAMPLE,
        "oci": TemplateClassification.PRODUCTION,
        "oh-goon": TemplateClassification.INTERNAL_SELF_HOSTING,
        "python": TemplateClassification.PRODUCTION,
        "security": TemplateClassification.PRODUCTION,
    }

    for catalog in (GOONS.catalog().templates, PLANS.catalog().templates):
        assert catalog
        for template in catalog:
            category = template.template_key.split("/", 1)[0]
            assert template.classification is expected[category], template.template_key


def test_every_shipped_plan_is_a_small_ordered_goon_roster() -> None:
    for key in PLANS.keys():
        source = PLANS.source(key)
        payload = yaml.safe_load(source.content)
        assert isinstance(payload, dict), key
        assert payload.get("goons"), key
        for forbidden in (
            "steps",
            "run",
            "command",
            "script",
            "imports",
            "requirements",
            "env",
            "variables",
            "outputs",
        ):
            assert forbidden not in payload, (key, forbidden)
        assert all(isinstance(step, CompiledGoonImportStep) for step in source.compiled.steps), key


def test_every_shipped_goon_is_atomic_or_composite_never_both() -> None:
    for key in GOONS.keys():
        source = GOONS.source(key)
        payload = yaml.safe_load(source.content)
        assert isinstance(payload, dict), key
        forms = [name for name in ("steps", "goons") if name in payload]
        assert forms in (["steps"], ["goons"]), (key, forms)
        assert "imports" not in payload, key
        if forms == ["goons"]:
            for index, item in enumerate(payload["goons"]):
                if isinstance(item, str):
                    assert item.count("/") == 1, (key, item)
                else:
                    # Recipient-local pass is the explicit same-name binding shorthand;
                    # do not mistake it for ambient propagation or Plan-level syntax.
                    assert set(item) <= {
                        "import",
                        "with",
                        "pass",
                        "failure_impact",
                        "as",
                        "from",
                        "phase",
                    }, (key, item)
                    passed = item.get("pass", ())
                    assert isinstance(passed, (list, tuple)), (key, item)
                    assert all(type(name) is str for name in passed), (key, item)
                    assert len(passed) == len(set(passed)), (key, item)
                    assert set(passed) <= set(payload.get("inputs", ())), (key, item)
                    assert not (set(passed) & set(item.get("with", {}))), (key, item)
                    compiled = source.compiled.steps[index]
                    actual = {binding.name: binding.value for binding in compiled.inputs}
                    for name in passed:
                        assert actual[name] == "${{ inputs." + name + " }}", (key, item)


def test_every_shipped_plan_builds_one_complete_goon_source_graph() -> None:
    for key in PLANS.keys():
        source = PLANS.source(key)
        graph = analyze_fleet_plan_sources(
            source.path,
            containment_root=PLANS.root,
            goon_templates=GOONS,
        )
        assert graph.root.path == source.path, key
        assert graph.root.compiled.execution is not None, key


def test_shipped_plan_imports_resolve_to_exact_canonical_goon_keys() -> None:
    known = set(GOONS.keys())
    for plan_key in PLANS.keys():
        payload = yaml.safe_load(PLANS.source(plan_key).content)
        imports = [item if isinstance(item, str) else item["import"] for item in payload["goons"]]
        compiled = [step.goon for step in PLANS.source(plan_key).compiled.steps]
        assert imports, plan_key
        assert compiled == imports, (plan_key, imports, compiled)
        assert all(value in known for value in imports), (plan_key, imports)
        assert all(value.count("/") == 1 for value in imports), (plan_key, imports)
        assert all(not value.startswith("goon:") for value in compiled), (plan_key, compiled)


def test_python_secure_preserves_composite_goon_ancestry_and_order() -> None:
    resolved = FleetPlanResolver(goon_templates=GOONS).resolve(PLANS.source("python/secure").path)

    assert [item.name for item in resolved.imports] == [
        "python/quality",
        "python/ruff",
        "python/typecheck",
        "python/test",
        "security/cve-combined",
        "python/uv-audit",
        "security/trivy-audit",
    ]
    assert all(not item.name.startswith("goon:") for item in resolved.imports)
    by_name = {item.name: item for item in resolved.imports}
    assert by_name["python/uv-audit"].parent_import_id == by_name["security/cve-combined"].import_id
    assert (
        by_name["security/trivy-audit"].parent_import_id
        == by_name["security/cve-combined"].import_id
    )


def test_shipped_packages_keep_complete_python_projects_and_small_plain_packages() -> None:
    for root in (PLANS.root, GOONS.root):
        for category in sorted(path for path in root.iterdir() if path.is_dir()):
            for package in sorted(path for path in category.iterdir() if path.is_dir()):
                entries = {path.name for path in authored_source_entries(package)}
                source_name = "goon.yml" if root == GOONS.root else "plan.yml"
                payload = yaml.safe_load((package / source_name).read_text(encoding="utf-8"))
                if payload.get("runtime") == "python":
                    assert {source_name, "README.md", "pyproject.toml", "uv.lock"} <= entries
                else:
                    assert entries == {source_name, "README.md"}


def test_production_plans_do_not_repeat_default_resource_envelopes() -> None:
    production_categories = {"python", "security", "oci", "kubernetes"}
    for key in PLANS.keys():
        category = key.split("/", 1)[0]
        if category not in production_categories:
            continue
        payload = yaml.safe_load(PLANS.source(key).content)
        execution = payload.get("execution", {})
        assert "resources" not in execution, key
        compiled = PLANS.source(key).compiled
        if not isinstance(compiled.execution, CicdExecutionPolicy):
            continue
        assert compiled.execution.resources.cpu_millis is None, key
        assert compiled.execution.resources.memory_mib is None, key
        assert compiled.execution.resources.max_processes is None, key


def test_core_python_goons_do_not_author_runtime_plumbing() -> None:
    for key in ("python/ruff", "python/typecheck", "python/test", "python/profile"):
        content = GOONS.source(key).content
        assert "requirements:" not in content, key
        assert "cache:" not in content, key
        assert "timeout_seconds:" not in content, key
        assert "uv lock --check" not in content, key
        assert "python -c" not in content, key


def test_ml_iris_training_uses_captured_package_and_base_dependencies() -> None:
    source = GOONS.source("ml/iris-train")
    goon = yaml.safe_load(source.content)
    plan_source = PLANS.source("ml/iris-training")

    assert "name" not in goon
    assert "description" not in goon
    assert source.compiled.name == "iris-train"
    assert source.compiled.description == ""
    assert PLANS.read("ml/iris-training").classification.value == "teaching-example"
    assert goon["runtime"] == "python"
    assert goon["uv"] == {"lock_mode": "frozen"}
    assert "requirements" not in goon
    assert len(goon["steps"]) == 1
    assert goon["steps"][0]["run"].startswith("iris-train ")
    assert "python -c" not in source.content

    resolved = FleetPlanResolver(goon_templates=GOONS).resolve(plan_source.path)
    assert resolved.imports[0].requirements == ()
    assert [item.name for item in resolved.imports] == ["ml/iris-train"]
    assert [step.step_id for step in resolved.steps] == ["ml-iris-train.iris-train"]
    command = resolved.steps[0]
    assert command.project_source is not None
    assert command.uv.group is None
    assert command.argv[:7] == (
        "uv",
        "run",
        "--frozen",
        "--no-default-groups",
        "--project",
        ".",
        "iris-train",
    )


def test_resolver_translates_resolved_plan_model_validation_to_domain_error(tmp_path: Path) -> None:
    from oh_goon.core.errors import FleetPlanInvalid

    package = tmp_path / "goons/general/restricted"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/restricted\n")
    (package / "goon.yml").write_text(
        "version: 1\nrequirements:\n- kind: file_exists\n  path: marker\nsteps:\n- run: 'true'\n"
    )
    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(
        "version: 1\nexecution: {purpose: verification}\ngoons:\n- import: general/restricted\n  from: goons\n"
    )
    with pytest.raises(
        FleetPlanInvalid,
        match="Verification Goon imports cannot carry repository admission requirements",
    ):
        FleetPlanResolver().resolve(plan_path)


def test_simple_core_goons_keep_the_small_authoring_contract() -> None:
    simple = (
        "python/ruff",
        "python/typecheck",
        "python/test",
        "python/profile",
        "python/uv-audit",
        "python/package-artifacts",
    )
    forbidden = (
        "python -c",
        "uv lock --check",
        "requirements: []",
        "cache:",
        "timeout_seconds:",
        "variables:",
        "pattern:",
        "&&",
        "||",
        "$(",
    )
    for key in simple:
        content = GOONS.source(key).content
        for token in forbidden:
            assert token not in content, (key, token)


def test_shipped_projected_plans_do_not_copy_goon_input_schemas() -> None:
    for key in PLANS.keys():
        payload = yaml.safe_load(PLANS.source(key).content)
        inputs = payload.get("inputs")
        if not isinstance(inputs, list):
            continue
        assert all(isinstance(name, str) and name for name in inputs), key
        for goon in payload["goons"]:
            if isinstance(goon, dict):
                bindings = goon.get("with", {})
                for name, value in bindings.items():
                    assert value != f"${{{{ inputs.{name} }}}}", (key, name)


def test_shipped_template_identity_and_description_have_single_owners() -> None:
    for key in PLANS.keys():
        payload = yaml.safe_load(PLANS.source(key).content)
        assert "name" not in payload, key
        assert "description" not in payload, key

    for key in GOONS.keys():
        source = GOONS.source(key)
        payload = yaml.safe_load(source.content)
        document = GOONS.read(key)
        assert "name" not in payload, key
        assert "description" not in payload, key
        assert source.compiled.name == key.rsplit("/", 1)[-1], key
        assert source.compiled.description == "", key
        assert document.description.strip(), key


def test_release_deploy_plan_projects_owned_contracts_instead_of_copying_schemas() -> None:
    payload = yaml.safe_load(PLANS.source("oci/release-deploy").content)
    assert "inputs" not in payload
    resolver = FleetPlanResolver(goon_templates=GOONS)
    assert {
        item.name for item in resolver.input_definitions(PLANS.source("oci/release-deploy").path)
    } == {
        "base_registry_hostname",
        "publish_registry_hostname",
        "publish_repository",
        "containerfile",
        "context",
        "platform",
        "kubernetes_api_hostname",
        "namespace",
        "deployment",
        "container",
        "project",
        "dependency_group",
        "typechecker",
    }
    bindings = payload["goons"][-1]["with"]
    assert bindings == {"image": "${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}"}


def test_composite_goons_expose_child_contracts_without_copying_schemas() -> None:
    for key in ("python/quality", "oci/publish-image", "kubernetes/release"):
        payload = yaml.safe_load(GOONS.source(key).content)
        assert isinstance(payload.get("inputs"), list), key
        assert all(isinstance(name, str) and name for name in payload["inputs"]), key


def test_release_deploy_resolves_through_goon_outputs_and_structural_workspace() -> None:
    plan = FleetPlanResolver(goon_templates=GOONS).resolve(
        PLANS.source("oci/release-deploy").path,
        inputs={
            "base_registry_hostname": "registry.example.com",
            "publish_registry_hostname": "registry.example.com",
            "publish_repository": "team/app",
            "containerfile": "Dockerfile",
            "context": ".",
            "platform": "linux/amd64",
            "kubernetes_api_hostname": "api.example.com",
            "namespace": "prod",
            "deployment": "api",
            "container": "api",
        },
    )
    by_id = {step.step_id: step for step in plan.steps}
    publish = by_id["oci-publish-image.oci-publish.publish"]
    build = by_id["oci-publish-image.oci-image.build"]
    commit = by_id["kubernetes-release.kubernetes-set-image.commit"]
    verify = by_id["kubernetes-release.kubernetes-rollout-verify.verify"]

    assert publish.workspace.source.step_id == build.step_id
    assert publish.policy.tls_verify is True
    assert publish.policy.registry_retries == 0
    assert publish.policy.parallel_copies == 1
    assert commit.image.startswith("${{ steps.oci-publish-image.oci-publish.publish.outputs.")
    assert verify.policy.tls_verify is True
    assert verify.policy.transport_retries == 0
    assert verify.policy.require_progress_deadline is True
    assert verify.policy.fail_if_superseded is True


def test_typed_executor_templates_do_not_repeat_fixed_backend_policy() -> None:
    for key in ("oci/publish", "kubernetes/rollout-verify"):
        payload = yaml.safe_load(GOONS.source(key).content)
        step = payload["steps"][0]
        assert "policy" not in step, key
        assert "timeout_seconds" not in step, key
    publish = yaml.safe_load(GOONS.source("oci/publish").content)["steps"][0]
    assert publish["target"]["registry"] == {"hostname": "${{ inputs.publish_registry_hostname }}"}
    assert "output" not in publish


def test_shipped_plan_template_descriptions_are_plain_text_for_browser_ui() -> None:
    description = PLANS.catalog().templates[0].description
    assert not description.startswith("**")
    assert not description.endswith("**")
