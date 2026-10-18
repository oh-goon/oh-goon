"""T3: exact occurrence routing is a presentation over one canonical resolved cut."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_input_routing import CallerFileRoutingOrigin, _binding_origin
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import AGENT_PLAN_TEMPLATES


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GoonTemplateStore())


def _source(key: str) -> Path:
    return PlanTemplateStore().source(key).path


def test_python_quality_projected_route_and_exact_source_identity():
    path = _source("python/quality")
    routed = _resolver().explain_routing(path, inputs={"typechecker": "ty"})
    assert routed["schema"] == "oh-goon.plan-input-routing/v1"
    assert routed["status"] == "resolved_not_executed"
    assert routed["root_source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert next(item for item in routed["inputs"] if item["name"] == "typechecker")[
        "owner_paths"
    ] == [["python/quality", "python/typecheck"]]
    assert next(item for item in routed["inputs"] if item["name"] == "typechecker")["origin"] == {
        "kind": "supplied"
    }
    assert [
        (x["import_instance_id"], x["input"])
        for x in next(item for item in routed["inputs"] if item["name"] == "typechecker")[
            "recipients"
        ]
    ] == [
        ("python-quality", "typechecker"),
        ("python-quality.python-typecheck", "typechecker"),
    ]
    assert all("value" not in binding for binding in routed["bindings"])
    assert "85" not in json.dumps(routed["inputs"] + routed["bindings"])


def test_default_origin_and_no_resolved_values_serialized():
    routed = _resolver().explain_routing(_source("python/quality"))
    assert next(item for item in routed["inputs"] if item["name"] == "typechecker")["origin"] == {
        "kind": "default"
    }
    assert "70" not in json.dumps(routed["inputs"] + routed["bindings"])


def test_file_origin_only_for_selected_file_keys():
    routed = _resolver().explain_routing(
        _source("python/quality"),
        inputs={"typechecker": "ty"},
        caller_file=CallerFileRoutingOrigin(
            relative_path="values/stage.yml",
            sha256="a" * 64,
            names=frozenset({"typechecker"}),
        ),
    )
    assert next(item for item in routed["inputs"] if item["name"] == "typechecker")["origin"] == {
        "kind": "inputs_file",
        "path": "values/stage.yml",
        "sha256": "a" * 64,
    }
    assert "85" not in json.dumps(routed["inputs"] + routed["bindings"])


def test_oci_workspace_producer_identity_is_exact_and_deferred():
    routed = _resolver().explain_routing(
        _source("oci/publish-image"),
        inputs={
            "base_registry_hostname": "example.com",
            "publish_registry_hostname": "example.com",
            "publish_repository": "example",
        },
    )
    assert len(routed["occurrences"]) == 3
    assert [(x["import_instance_id"], x["goon"]) for x in routed["occurrences"]] == [
        ("oci-publish-image", "oci/publish-image"),
        ("oci-publish-image.oci-image", "oci/image"),
        ("oci-publish-image.oci-publish", "oci/publish"),
    ]
    binding = next(x for x in routed["bindings"] if x["input"] == "source_workspace")
    assert binding["root_input"] is None
    assert binding["origin"] == {
        "kind": "producer_workspace",
        "producer_import_instance_id": "oci-publish-image.oci-image",
    }


def test_oci_kubernetes_producer_output_is_identity_not_runtime_value():
    routed = _resolver().explain_routing(
        _source("oci/release-deploy"),
        inputs={
            "base_registry_hostname": "example.com",
            "publish_registry_hostname": "example.com",
            "publish_repository": "example",
            "kubernetes_api_hostname": "example.com",
            "namespace": "staging",
            "deployment": "app",
            "container": "app",
        },
    )
    generation = next(x for x in routed["bindings"] if x["input"] == "expected_generation")
    assert generation["origin"] == {
        "kind": "deferred_goon_output",
        "producer_import_instance_id": "kubernetes-release.kubernetes-set-image",
        "output": "KUBERNETES_GENERATION",
    }
    image = next(
        x
        for x in routed["bindings"]
        if x["input"] == "image" and x["import_instance_id"] == "kubernetes-release"
    )
    assert image["origin"] == {
        "kind": "deferred_goon_output",
        "producer_import_instance_id": "oci-publish-image",
        "output": "OCI_REFERENCE",
    }
    # Root namespace is forwarded to both distinct Kubernetes child instances.
    recipients = routed["inputs"][[x["name"] for x in routed["inputs"]].index("namespace")][
        "recipients"
    ]
    assert {x["import_instance_id"] for x in recipients} == {
        "kubernetes-release",
        "kubernetes-release.kubernetes-set-image",
        "kubernetes-release.kubernetes-rollout-verify",
    }
    assert "staging" not in json.dumps(routed["inputs"] + routed["bindings"])


def test_no_parameter_plan_has_no_invented_input_routes():
    routed = _resolver().explain_routing(_source("security/cve-combined"))
    assert routed["inputs"] == []
    assert routed["occurrences"]
    assert routed["bindings"] == []


def _package(root: Path, key: str, source: str) -> None:
    category, name = key.split("/")
    base = root / category / name
    base.mkdir(parents=True, exist_ok=True)
    (base / "goon.yml").write_text(source, encoding="utf-8")
    (base / "README.md").write_text(key, encoding="utf-8")


def test_repeated_nested_goon_occurrences_and_no_ambient_fanout(tmp_path: Path):
    goons = tmp_path / "goons"
    _package(
        goons,
        "general/leaf",
        """version: 1
inputs:
  token:
    type: string
    default: safe
steps:
- id: check
  run: echo ${{ inputs.token }}
""",
    )
    _package(
        goons,
        "general/outer",
        """version: 1
inputs: [token]
goons:
- import: general/leaf
  pass: [token]
- import: general/leaf
  pass: [token]
- import: general/leaf
""",
    )
    plan = tmp_path / "plan.yml"
    plan.write_text(
        """version: 1
inputs: [token]
goons:
- import: general/outer
""",
        encoding="utf-8",
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    first = resolver.explain_routing(plan, inputs={"token": "known"})
    second = resolver.explain_routing(plan, inputs={"token": "known"})
    assert first == second
    assert [item["import_instance_id"] for item in first["occurrences"]] == [
        "general-outer",
        "general-outer.general-leaf",
        "general-outer.general-leaf-2",
        "general-outer.general-leaf-3",
    ]
    ids = [x["import_instance_id"] for x in first["inputs"][0]["recipients"]]
    assert ids == ["general-outer", "general-outer.general-leaf", "general-outer.general-leaf-2"]
    assert "general-outer.general-leaf-3" not in ids  # no implicit inheritance
    assert first["occurrences"][-1]["parent_import_instance_id"] == "general-outer"


def test_renamed_edge_identifies_original_public_input(tmp_path: Path):
    goons = tmp_path / "goons"
    _package(
        goons,
        "general/leaf",
        """version: 1
inputs:
  target:
    type: string
    default: safe
steps:
- run: echo ${{ inputs.target }}
""",
    )
    _package(
        goons,
        "general/outer",
        """version: 1
inputs: [public]
goons:
- import: general/leaf
  with:
    target: ${{ inputs.public }}
""",
    )
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ninputs: [public]\ngoons:\n- general/outer\n", encoding="utf-8")
    resolved = FleetPlanResolver(goon_templates=GoonTemplateStore(goons)).explain_routing(plan)
    assert resolved["inputs"][0]["recipients"][-1] == {
        "import_instance_id": "general-outer.general-leaf",
        "goon": "general/leaf",
        "input": "target",
    }


@pytest.mark.parametrize("payload", ["secret123", "${{ unknown.SECRET }}", 123, True, None])
def test_binding_origin_does_not_echo_literals_or_unknown_expressions(payload):
    result = _binding_origin(payload, (), "")
    assert "secret123" not in json.dumps(result)
    assert result["kind"] in {"literal_redacted", "expression_redacted"}


def test_bad_resolution_fails_before_route_claim(tmp_path: Path):
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ngoons:\n- missing/not-found\n", encoding="utf-8")
    with pytest.raises((FleetPlanInvalid, FleetPlanSourceNotFound)):
        _resolver().explain_routing(path)


def test_routing_analyzes_once_and_resolves_once(monkeypatch):
    resolver = _resolver()
    observed = {"analyze": 0, "resolve": 0}
    orig_analyze = resolver.analyze_source
    orig_resolve = resolver._resolve_graph

    def analyze(*args, **kwargs):
        observed["analyze"] += 1
        return orig_analyze(*args, **kwargs)

    def resolve(*args, **kwargs):
        observed["resolve"] += 1
        return orig_resolve(*args, **kwargs)

    monkeypatch.setattr(resolver, "analyze_source", analyze)
    monkeypatch.setattr(resolver, "_resolve_graph", resolve)
    resolver.explain_routing(_source("python/quality"))
    assert observed == {"analyze": 1, "resolve": 1}


def test_routing_report_json_bytes_are_stable_for_same_source_cut():
    resolver = _resolver()
    first = resolver.explain_routing(_source("python/quality"), inputs={"typechecker": "ty"})
    second = resolver.explain_routing(_source("python/quality"), inputs={"typechecker": "ty"})
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_bad_typed_input_fails_before_routing_is_reported():
    with pytest.raises(FleetPlanInvalid):
        _resolver().explain_routing(_source("python/quality"), inputs={"typechecker": 101})


def test_host_source_descriptor_omits_credential_values():
    from oh_goon.core.models.fleet_plan_composition import EnvInputSource

    source = EnvInputSource(kind="env", name="SECRET_ENV_NAME")
    description = _binding_origin(None, (source,), "")
    assert description == {"kind": "external_source", "source_kinds": ["env"]}
    assert "SECRET_ENV_NAME" not in json.dumps(description)


def test_with_and_pass_normalize_to_identical_occurrence_routes(tmp_path: Path):
    goons = tmp_path / "goons"
    _package(
        goons,
        "general/leaf",
        """version: 1
inputs:
  token:
    default: safe
steps:
- run: echo ${{ inputs.token }}
""",
    )
    base = """version: 1
inputs: [token]
goons:
- import: general/leaf
  {binding}
"""
    child_path = goons / "general" / "outer"
    _package(goons, "general/outer", base.format(binding="pass: [token]"))
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ninputs: [token]\ngoons:\n- general/outer\n", encoding="utf-8")
    r = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    shorthand = r.explain_routing(plan)
    (child_path / "goon.yml").write_text(
        base.format(binding="with: {token: '${{ inputs.token }}'}"), encoding="utf-8"
    )
    legacy = r.explain_routing(plan)
    assert shorthand["inputs"] == legacy["inputs"]
    assert shorthand["bindings"] == legacy["bindings"]
    assert [(x["import_instance_id"], x["goon"]) for x in shorthand["occurrences"]] == [
        (x["import_instance_id"], x["goon"]) for x in legacy["occurrences"]
    ]
    # The changed source bytes must NOT be presented as the same source identity.
    assert shorthand["occurrences"][0]["source_sha256"] != legacy["occurrences"][0]["source_sha256"]


def test_conventional_plan_routes_have_exact_resolved_import_occurrences():
    """Catalog-wide parity: real resolved import IDs, not synthesized name-only routes."""
    plan_store = PlanTemplateStore()
    resolver = _resolver()
    representative = {
        "hostname": "registry.example.com",
        "kubernetes_name": "staging",
        "kubernetes_deployment_name": "app",
        "oci_reference": "registry.example.com/team/app@sha256:" + "a" * 64,
        "oci_repository": "team/app",
        "digest": "sha256:" + "a" * 64,
        "git_revision": "a" * 40,
        "string": "v1",
    }
    checked: list[str] = []
    for key in plan_store.keys():
        if key in AGENT_PLAN_TEMPLATES:
            continue  # Captured Agent imports are exercised by the native Agent fixtures.
        path = plan_store.source(key).path
        canonical = resolver.inspect_inputs(path)
        values = {
            item.definition.name: representative[item.definition.input_type.value]
            for item in canonical
            if item.definition.required
        }
        graph = resolver.analyze_source(path)
        resolved = resolver.resolve_source_graph(graph, inputs=values)
        reported = resolver.explain_routing(path, inputs=values)
        assert [row["import_instance_id"] for row in reported["occurrences"]] == [
            item.import_id for item in resolved.imports
        ], key
        assert [row["name"] for row in reported["inputs"]] == [
            item.definition.name for item in canonical
        ], key
        assert all(
            row["import_instance_id"] in {item.import_id for item in resolved.imports}
            for row in reported["bindings"]
        ), key
        assert reported["status"] == "resolved_not_executed", key
        checked.append(key)
    assert set(checked).union(AGENT_PLAN_TEMPLATES) == set(plan_store.keys())
