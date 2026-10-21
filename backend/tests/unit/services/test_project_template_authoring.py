"""Project composition keeps exact sources, recipient contracts and native selections."""

import re
from pathlib import Path

import pytest
import yaml

from oh_goon.cli_plan_input_files import read_caller_input_file
from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.agent_review import agent_review_case
from tests.factories.shipped_templates import AGENT_PLAN_TEMPLATES


def package(root: Path, key: str, content: str) -> Path:
    directory = root / key
    directory.mkdir(parents=True)
    path = directory / "goon.yml"
    path.write_text(content)
    (directory / "README.md").write_text(f"# {key}\n\nProject check.\n")
    return path


def leaf(value="default"):
    return f"""version: 1
inputs:
  value:
    type: string
    default: {value}
steps:
- run: echo ${{{{ inputs.value }}}}
"""


def test_plain_plan_import_exposes_composite_interface_and_preserves_defaults(tmp_path):
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ngoons:\n- python/quality\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    inspected = {item.definition.name: item for item in resolver.inspect_inputs(path)}
    assert set(inspected) == {"project", "dependency_group", "typechecker"}
    assert inspected["typechecker"].definition.default == "pyright"
    assert inspected["typechecker"].owner_paths == (("python/quality", "python/typecheck"),)
    default = resolver.resolve(path)
    selected = resolver.resolve(path, inputs={"typechecker": "ty", "project": "backend"})
    assert any("pyright" in step.argv for step in default.steps)
    assert any("ty" in step.argv for step in selected.steps)
    assert not any("pyright" in step.argv for step in selected.steps)
    assert all(step.working_directory == "backend" for step in selected.steps)


def test_fixed_import_value_is_not_an_automatic_cli_input(tmp_path):
    package(tmp_path / "goons", "general/check", leaf())
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
goons:
- import: general/check
  from: goons
  with: {value: fixed}
""")
    resolver = FleetPlanResolver()
    assert resolver.inspect_inputs(path) == ()
    assert resolver.resolve(path).steps[0].argv == ("echo", "fixed")
    with pytest.raises(FleetPlanInputRejected) as rejected:
        resolver.resolve(path, inputs={"value": "caller"})
    assert rejected.value.diagnostic_code == "UNKNOWN_INPUT"


def test_shared_inputs_keep_independent_defaults_until_explicitly_routed(tmp_path):
    package(tmp_path / "goons", "general/first", leaf("first-default"))
    package(tmp_path / "goons", "general/second", leaf("second-default"))
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
goons:
- import: general/first
  from: goons
- import: general/second
  from: goons
""")
    resolver = FleetPlanResolver()
    assert resolver.inspect_inputs(path) == ()
    assert [step.argv[-1] for step in resolver.resolve(path).steps] == [
        "first-default",
        "second-default",
    ]
    with pytest.raises(FleetPlanInputRejected) as rejected:
        resolver.resolve(path, inputs={"value": "both"})
    assert rejected.value.diagnostic_code == "AMBIGUOUS_PROJECTED_INPUT"
    path.write_text(
        path.read_text().replace(
            "- import: general/first", "- import: general/first\n  pass: [value]"
        )
    )
    resolved = resolver.resolve(path, inputs={"value": "selected"})
    assert [step.argv[-1] for step in resolved.steps] == ["selected", "second-default"]


def test_plan_does_not_expose_private_composite_inputs(tmp_path):
    package(tmp_path / "goons", "general/leaf", leaf())
    package(
        tmp_path / "goons",
        "general/bundle",
        """version: 1
goons:
- import: general/leaf
  from: ../..
""",
    )
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ngoons:\n- import: general/bundle\n  from: goons\n")
    resolver = FleetPlanResolver()
    assert resolver.inspect_inputs(path) == ()
    assert resolver.resolve(path).steps[0].argv[-1] == "default"
    with pytest.raises(FleetPlanInputRejected):
        resolver.resolve(path, inputs={"value": "private"})


def test_local_and_shipped_same_key_are_distinct_ordered_sources(tmp_path):
    package(tmp_path / "shipped", "general/check", leaf("shipped"))
    local = package(tmp_path / "goons", "general/check", leaf("local"))
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
goons:
- import: general/check
  as: shipped
- import: general/check
  from: ./goons
  as: local
- import: general/check
  as: repeated
""")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "shipped"))
    before = resolver.resolve(path)
    assert [step.argv[-1] for step in before.steps] == ["shipped", "local", "shipped"]
    assert before.step_sources[1].source == "local:goons/general/check/goon.yml"
    assert [item.import_id for item in before.imports] == ["shipped", "local", "repeated"]
    local.write_text(leaf("changed"))
    after = resolver.resolve(path)
    assert build_execution_lock(before).digest != build_execution_lock(after).digest
    assert before.imports[0].source_sha256 == after.imports[0].source_sha256


def test_nested_local_import_reuses_one_source_observation(tmp_path, monkeypatch):
    selected = package(tmp_path / "goons", "general/check", leaf())
    package(
        tmp_path / "goons",
        "general/bundle",
        """version: 1
inputs: [value]
goons:
- import: general/check
  from: ../..
""",
    )
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
inputs: [value]
goons:
- import: general/bundle
  from: goons
  pass: [value]
- import: general/check
  from: goons
""")
    original = Path.read_bytes
    observed = []

    def read(p):
        observed.append(p.resolve())
        return original(p)

    monkeypatch.setattr(Path, "read_bytes", read)
    resolver = FleetPlanResolver()
    resolved = resolver.resolve(path, inputs={"value": "selected"})
    assert [step.argv[-1] for step in resolved.steps] == ["selected", "default"]
    assert observed.count(selected.resolve()) == 1
    assert resolver.inspect_inputs(path)[0].owner_paths == (("general/bundle", "general/check"),)


@pytest.mark.parametrize("source", ["../outside", "/tmp", "${{ inputs.root }}"])
def test_local_source_escape_or_dynamic_selection_fails(tmp_path, source):
    path = tmp_path / "plan.yml"
    path.write_text(f"version: 1\ngoons:\n- import: general/check\n  from: {source}\n")
    with pytest.raises(FleetPlanInvalid):
        FleetPlanResolver().resolve(path)


def test_local_symlink_escape_and_cycle_fail(tmp_path):
    outside = tmp_path / "outside"
    package(outside, "general/check", leaf())
    root = tmp_path / "project"
    root.mkdir()
    (root / "goons").symlink_to(outside, target_is_directory=True)
    path = root / "plan.yml"
    path.write_text("version: 1\ngoons:\n- import: general/check\n  from: goons\n")
    with pytest.raises(FleetPlanInvalid, match="escapes"):
        FleetPlanResolver().resolve(path)
    (root / "goons").unlink()
    package(
        root / "goons",
        "general/check",
        "version: 1\ngoons:\n- import: general/check\n  from: ../..\n",
    )
    with pytest.raises(FleetPlanInvalid, match="cycle"):
        FleetPlanResolver().resolve(path)


@pytest.mark.parametrize(
    "reference", ["${{ inputs.shared }}", "${{inputs.shared}}", "${{  inputs.shared  }}"]
)
def test_plan_explicit_fanout_and_renaming_preserve_input_owner(tmp_path, reference):
    package(tmp_path / "goons", "general/check", leaf())
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
goons:
- import: general/check
  from: goons
  as: first
  with: {value: '${{ inputs.shared }}'}
- import: general/check
  from: goons
  as: second
  with: {value: '${{ inputs.shared }}'}
- import: general/check
  from: goons
  as: third
  with: {value: '${{ inputs.independent }}'}
""")
    resolver = FleetPlanResolver()
    path.write_text(path.read_text().replace("${{ inputs.shared }}", reference))
    resolved = resolver.resolve(path, inputs={"shared": "two", "independent": "one"})
    assert [step.argv[-1] for step in resolved.steps] == ["two", "two", "one"]
    assert len(resolver.inspect_inputs(path)[0].owner_paths) == 2
    routing = resolver.explain_routing(path, inputs={"shared": "two", "independent": "one"})
    assert (
        len(next(item for item in routing["inputs"] if item["name"] == "shared")["recipients"]) == 2
    )


def test_reordering_aliased_imports_preserves_independent_caller_values(tmp_path):
    path = tmp_path / "plan.yml"
    document = {
        "version": 1,
        "goons": [
            {
                "import": "python/typecheck",
                "as": "api",
                "with": {"project": "backend", "typechecker": "${{ inputs.api_checker }}"},
            },
            {
                "import": "python/typecheck",
                "as": "tooling",
                "with": {"project": "tools", "typechecker": "${{ inputs.tooling_checker }}"},
            },
        ],
    }
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    values = {"api_checker": "ty", "tooling_checker": "pyright"}
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    before = resolver.resolve(path, inputs=values)
    document["goons"].reverse()
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    after = resolver.resolve(path, inputs=values)
    assert [step.step_id for step in before.steps] == ["api.ty", "tooling.pyright"]
    assert [step.step_id for step in after.steps] == ["tooling.pyright", "api.ty"]
    assert {step.step_id: (step.working_directory, step.argv) for step in before.steps} == {
        step.step_id: (step.working_directory, step.argv) for step in after.steps
    }


@pytest.mark.parametrize("resource", ["output", "workspace"])
def test_consumer_before_producer_fails_with_ordering_guidance(tmp_path, resource):
    path = tmp_path / "plan.yml"
    if resource == "output":
        imports = [
            {"import": "oci/publish-image", "as": "publisher"},
            {
                "import": "kubernetes/release",
                "as": "deployer",
                "with": {"image": "${{ goons.publisher.outputs.OCI_REFERENCE }}"},
            },
        ]
    else:
        imports = [
            {"import": "oci/image", "as": "builder"},
            {
                "import": "oci/publish",
                "as": "publisher",
                "with": {"source_workspace": "${{ goons.builder.workspace }}"},
            },
        ]
    document = {"version": 1, "goons": imports}
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    values = {
        "base_registry_hostname": "base.example.com",
        "publish_registry_hostname": "publish.example.com",
        "publish_repository": "team/app",
    }
    if resource == "output":
        values.update(
            kubernetes_api_hostname="kube.example.com",
            namespace="production",
            deployment="app",
            container="app",
        )
    resolved = resolver.resolve(path, inputs=values)
    assert [item.import_id for item in resolved.imports if item.parent_import_id is None] == [
        item["as"] for item in imports
    ]
    imports.reverse()
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    with pytest.raises(FleetPlanInvalid, match="place the producer before this consumer") as error:
        resolver.resolve(path, inputs=values)
    assert f"Goon {resource}" in str(error.value)
    assert "unavailable from earlier imports" in str(error.value)
    assert "does not publish" not in str(error.value)


def test_uv_inputs_bind_environment_working_directory_reports_and_requirements(tmp_path):
    path = tmp_path / "goon.yml"
    path.write_text("""version: 1
inputs:
  project: {type: string, non_empty: true, default: .}
  group: {type: string, default: dev}
uv:
  project: ${{ inputs.project }}
  group: ${{ inputs.group }}
steps:
- run: uv run pytest --junitxml=junit.xml
  artifacts: [{path: junit.xml, retention: evidence}]
""")
    plan = FleetPlanResolver().resolve(path, inputs={"project": "my backend", "group": "quality"})
    step = plan.steps[0]
    assert step.working_directory == "my backend"
    assert step.uv.project == "my backend" and step.uv.group == "quality"
    assert step.argv[step.argv.index("--project") + 1] == "."
    assert step.artifacts[0].path == "my backend/junit.xml"
    assert plan.requirements[0].project == "my backend"
    assert plan.requirements[0].group == "quality"
    with pytest.raises((FleetPlanInvalid, ValueError)):
        FleetPlanResolver().resolve(path, inputs={"group": "--invalid"})


@pytest.mark.parametrize("output", ["/tmp/report.xml", "../report.xml", "reports//junit.xml"])
def test_uv_report_binding_rejects_invalid_path_before_project_prefix(tmp_path, output):
    path = tmp_path / "goon.yml"
    path.write_text("""version: 1
inputs:
  report: {type: string}
uv: {project: backend, group: dev}
steps:
- run: uv run pytest
  artifacts: [{path: '${{ inputs.report }}'}]
""")
    with pytest.raises(FleetPlanInvalid):
        FleetPlanResolver().resolve(path, inputs={"report": output})


def test_documented_plan_values_resolve_with_the_actual_catalog(tmp_path):
    plans = PlanTemplateStore()
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    for key in plans.keys():
        source = plans.source(key)
        guide = source.path.with_name("README.md").read_text()
        example = re.search(r"```yaml\n(inputs:.*?\n)```", guide, re.S)
        if example is None:
            assert not resolver.inspect_inputs(source.path), key
            resolver.resolve(source.path)
            continue
        values = tmp_path / "values.yml"
        values.write_text(example.group(1))
        snapshot = read_caller_input_file(values, root=tmp_path)
        if key in AGENT_PLAN_TEMPLATES:
            agent_resolver, path, repository, options = agent_review_case(
                tmp_path / key, plan_template=key
            )
            options["inputs"] = snapshot.values()
            from oh_goon.core.models.agent_providers import (
                NativeAgentDefinition,
                NativeAgentProfile,
                ResolvedNativeAgent,
            )
            from oh_goon.core.models.fleet_plan import AgentPlanStep

            graph = agent_resolver.analyze_source(path)
            if any(
                isinstance(step, AgentPlanStep) and step.harness == "native"
                for owner, identifier in graph.agent_step_sources()
                for step in graph.node(owner).compiled.steps
                if step.step_id == identifier
            ):
                connection = options.pop("connection")
                options.update(
                    native_agent=ResolvedNativeAgent(
                        agent="pi",
                        profile_name="isolated-managed",
                        definition=NativeAgentDefinition(executable="/native/pi"),
                        profile=NativeAgentProfile(
                            backend="bubblewrap", inference_profile=connection.profile
                        ),
                        executable_sha256="a" * 64,
                        connection=connection,
                    ),
                    repository=repository,
                )
                options["allowed_acceptance"] = ("agentic-review", "native-coding")
            resolved, _, _ = agent_resolver.resolve_frozen_agents(path, **options)
        else:
            resolved = resolver.resolve(source.path, inputs=snapshot.values())
        assert resolved.steps, key


def test_complete_developer_guide_plan_examples_resolve_with_the_catalog(tmp_path):
    plans = PlanTemplateStore()
    guide = (plans.root.parent / "README.md").read_text()
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    path = tmp_path / "plan.yml"
    for example in re.findall(r"```yaml\n(.*?)\n```", guide, re.S):
        document = yaml.safe_load(example)
        if not isinstance(document, dict) or "version" not in document or "goons" not in document:
            continue
        path.write_text(example)
        assert resolver.resolve(path).steps
