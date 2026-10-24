from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.fleet_plan_composition import bind_inputs
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import render_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _package(root: Path, key: str, body: str) -> None:
    category, name = key.split("/")
    directory = root / category / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "goon.yml").write_text(body, encoding="utf-8")
    (directory / "README.md").write_text(f"# {key}\n", encoding="utf-8")


def _resolver(tmp_path: Path, goon: str) -> FleetPlanResolver:
    root = tmp_path / "goons"
    _package(root, "general/leaf", goon)
    return FleetPlanResolver(goon_templates=GoonTemplateStore(root))


def _plan(tmp_path: Path, binding: str) -> Path:
    path = tmp_path / "plan.yml"
    path.write_text(
        f"""version: 1
goons:
- import: general/leaf
  with:
{binding}""",
        encoding="utf-8",
    )
    return path


def test_input_defaults_optional_and_starts_with_are_authoring_contract() -> None:
    compiled = parse_goon_template(
        """version: 1
inputs:
  image_name:
    type: string
    starts_with: my-image-storage/
  platform:
    type: choice
    choices: [linux/amd64, linux/arm64]
    default: linux/amd64
  note:
    type: string
    optional: true
steps:
- run: echo ok
"""
    )
    by_name = {item.name: item for item in compiled.inputs}
    assert by_name["image_name"].required is True
    assert by_name["image_name"].starts_with == "my-image-storage/"
    assert by_name["platform"].required is False
    assert by_name["platform"].default == "linux/amd64"
    assert by_name["note"].required is False
    assert by_name["note"].default is None


def test_authored_required_flag_is_rejected() -> None:
    with pytest.raises(FleetPlanInvalid, match="do not author required"):
        parse_goon_template(
            """version: 1
inputs:
  value:
    type: string
    required: true
steps:
- run: echo ok
"""
        )


def test_configuration_evidence_records_selection_without_reading_unused_sources(tmp_path: Path):
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  value:
    type: string
    default: private-default
steps:
- run: echo ok
""",
    )
    (tmp_path / "null.yml").write_text("value: null\n")
    (tmp_path / "unused.yml").write_text("broken: [not valid")
    path = _plan(
        tmp_path,
        """    value:
      from:
      - env: ABSENT
      - env: EMPTY
      - yaml: {path: null.yml, key: value}
      - env: CHOSEN
      - yaml: {path: unused.yml, key: value}
""",
    )
    plan = resolver.resolve(
        path, repository=tmp_path, environment={"EMPTY": "", "CHOSEN": "private-caller"}
    )
    evidence = plan.configuration
    assert evidence is not None
    entry = next(item for item in evidence.entries if item.name == "value")
    assert [(item.kind, item.state) for item in entry.binding.candidates] == [
        ("env", "missing"),
        ("env", "empty"),
        ("yaml", "null"),
        ("env", "selected"),
        ("yaml", "not_evaluated"),
    ]
    assert entry.binding.kind == "external_source"
    assert entry.line == 3
    assert entry.source_sha256 == plan.imports[0].source_sha256
    rendered = evidence.model_dump_json()
    assert "private-default" not in rendered and "private-caller" not in rendered
    assert "unused.yml" not in rendered


def test_configuration_distinguishes_optional_omission_default_null_and_caller_null(tmp_path):
    path = tmp_path / "goon.yml"
    path.write_text("""version: 1
inputs:
  omitted: {type: string, optional: true}
  null_default: {type: string, default: null}
  caller_null: {type: string, optional: true}
steps:
- run: echo ok
""")
    plan = FleetPlanResolver().resolve(path, inputs={"caller_null": None})
    assert plan.configuration is not None
    entries = {item.name: item for item in plan.configuration.entries}
    assert entries["omitted"].binding.kind == "optional_omission"
    assert entries["omitted"].default_declared is False
    assert entries["null_default"].binding.kind == "default"
    assert entries["null_default"].default_declared is True
    assert entries["caller_null"].binding.kind == "caller"
    assert all(entry.value_state == "null" for entry in entries.values())


def test_configuration_preserves_scalar_and_typed_defaults_without_exposing_values(tmp_path):
    path = tmp_path / "goon.yml"
    path.write_text("""version: 1
inputs:
  shorthand: private-scalar-default-canary
  empty: ''
  quoted_null: 'null'
  quoted_false: 'false'
  required_null: null
  declared_null: {type: string, default: null}
  typed_false: {type: boolean, default: false}
  typed_zero: {type: integer, default: 0}
steps:
- run: echo ok
""")
    resolver = FleetPlanResolver()
    with pytest.raises(FleetPlanInvalid, match="requires inputs: required_null"):
        resolver.resolve(path)
    plan = resolver.resolve(path, inputs={"required_null": "private-caller-canary"})
    assert plan.configuration is not None
    entries = {entry.name: entry for entry in plan.configuration.entries}
    for name in entries.keys() - {"required_null"}:
        assert entries[name].default_declared is True
        assert entries[name].required is False
        assert entries[name].binding.kind == "default"
    assert entries["required_null"].default_declared is False
    assert entries["required_null"].required is True
    assert entries["required_null"].binding.kind == "caller"
    assert entries["empty"].value_state == "empty"
    assert entries["declared_null"].value_state == "null"
    assert all(
        entries[name].value_state == "defined"
        for name in ("shorthand", "quoted_null", "quoted_false", "typed_false", "typed_zero")
    )
    assert "private-scalar-default-canary" not in plan.configuration.model_dump_json()
    assert "private-caller-canary" not in plan.configuration.model_dump_json()


@pytest.mark.parametrize(
    "scalar,expected,input_type",
    [
        ("false", False, "boolean"),
        ("true", True, "boolean"),
        ("0", 0, "integer"),
        ("12", 12, "integer"),
    ],
)
def test_scalar_shorthand_resolves_native_typed_defaults(tmp_path, scalar, expected, input_type):
    path = tmp_path / "goon.yml"
    path.write_text(
        f"version: 1\ninputs:\n  value: {scalar}\nsteps:\n- run: echo value-${{{{ inputs.value }}}}\n"
    )
    resolver = FleetPlanResolver()
    defaults = {}
    plan = resolver.resolve_source_graph(resolver.analyze_source(path), resolved_inputs=defaults)
    assert defaults == {"value": expected}
    assert type(defaults["value"]) is type(expected)
    assert plan.steps[0].argv == ("echo", f"value-{scalar}")
    assert plan.configuration is not None
    entry = next(item for item in plan.configuration.entries if item.name == "value")
    assert entry.input_type.value == input_type
    assert entry.default_declared is True
    assert entry.required is False
    assert entry.binding.kind == "default"


def test_unsupported_scalar_default_remains_rejected(tmp_path):
    path = tmp_path / "goon.yml"
    path.write_text("inputs: {value: 1.5}\nsteps: [{run: echo ok}]\n")
    with pytest.raises(
        FleetPlanInvalid, match="input definitions must be mappings, scalar defaults, or null"
    ):
        FleetPlanResolver().resolve(path)


def test_configuration_lineage_preserves_occurrences_and_parent_input_edges(tmp_path: Path):
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  value: {type: string, default: secret-default}
env:
  PRIVATE_LITERAL: {value: secret-env-value}
  EMPTY: {value: ''}
steps:
- run: echo ok
""",
    )
    path = tmp_path / "plan.yml"
    path.write_text("""version: 1
goons:
- import: general/leaf
  as: first
  with: {value: '${{ inputs.renamed }}'}
- import: general/leaf
  as: second
""")
    plan = resolver.resolve(path, inputs={"renamed": "secret-override"})
    assert plan.configuration is not None
    entries = {
        (item.scope, item.namespace.value, item.name): item for item in plan.configuration.entries
    }
    assert entries[(None, "input", "renamed")].binding.kind == "caller"
    assert entries[("first", "input", "value")].binding.parent_input == "renamed"
    assert entries[("first", "input", "value")].binding.parent_scope is None
    assert entries[("second", "input", "value")].binding.parent_input == "value"
    assert entries[(None, "input", "value")].binding.kind == "default"
    assert entries[("first", "env", "EMPTY")].value_state == "empty"
    assert all(
        secret not in plan.configuration.model_dump_json()
        for secret in (
            "secret-default",
            "secret-override",
            "secret-env-value",
        )
    )


def test_configuration_evidence_is_bounded_without_changing_workload_membership(tmp_path: Path):
    names = [f"value_{index}" for index in range(32)]
    definitions = "\n".join(f"  {name}: {{type: string, default: safe}}" for name in names)
    environment = "env:\n" + "".join(f"  ENV_{index}: {{value: safe}}\n" for index in range(32))
    resolver = _resolver(
        tmp_path, f"version: 1\ninputs:\n{definitions}\n{environment}steps:\n- run: echo ok\n"
    )
    _package(
        tmp_path / "goons",
        "general/group",
        "version: 1\ninputs: ["
        + ", ".join(names)
        + "]\n"
        + environment
        + "goons:\n- import: general/leaf\n  pass: ["
        + ", ".join(names)
        + "]\n",
    )
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\ngoons:\n"
        + "".join(f"- import: general/group\n  as: occurrence-{index}\n" for index in range(33))
    )
    plan = resolver.resolve(path)
    assert len(plan.steps) == 33
    assert plan.configuration is not None
    assert len(plan.configuration.entries) == 4096
    assert plan.configuration.omitted_entries == 128


def test_environment_fallback_uses_first_defined_then_goon_default(tmp_path: Path) -> None:
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  timeout:
    type: duration
    default: 5m
steps:
- run: echo ${{ inputs.timeout }}
""",
    )
    plan = _plan(
        tmp_path,
        """    timeout:
      from:
      - env: HELM_TIMEOUT_OVERRIDE
      - env: HELM_TIMEOUT
""",
    )

    assert resolver.resolve(
        plan, environment={"HELM_TIMEOUT_OVERRIDE": "20m", "HELM_TIMEOUT": "10m"}
    ).steps[0].argv == ("echo", "20m")
    assert resolver.resolve(plan, environment={"HELM_TIMEOUT": "10m"}).steps[0].argv == (
        "echo",
        "10m",
    )
    assert resolver.resolve(plan, environment={}).steps[0].argv == ("echo", "5m")


def test_environment_source_is_redacted_from_resolution_diagnostics(tmp_path: Path) -> None:
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  token:
    type: string
steps:
- run: echo ${{ inputs.token }}
""",
    )
    plan = _plan(
        tmp_path,
        """    token:
      from:
        env: RELEASE_TOKEN
""",
    )

    report = resolver.explain(plan, environment={"RELEASE_TOKEN": "top-secret-value"})

    value = next(item for item in report.values if item.name == "token")
    assert value.origin == "env:RELEASE_TOKEN"
    assert value.resolved == "<env:RELEASE_TOKEN:redacted>"
    assert value.contains_host_value is True
    assert "top-secret-value" not in report.model_dump_json()


def test_resolved_environment_value_changes_execution_identity_without_leaking_to_lock(
    tmp_path: Path,
) -> None:
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  token:
    type: string
steps:
- run: echo ${{ inputs.token }}
""",
    )
    plan = _plan(
        tmp_path,
        """    token:
      from:
        env: RELEASE_TOKEN
""",
    )

    first = build_execution_lock(
        resolver.resolve(plan, environment={"RELEASE_TOKEN": "secret-one"})
    )
    second = build_execution_lock(
        resolver.resolve(plan, environment={"RELEASE_TOKEN": "secret-two"})
    )

    assert first.digest != second.digest
    rendered = render_execution_lock(first)
    assert "secret-one" not in rendered
    assert "RELEASE_TOKEN" not in rendered


def test_invalid_resolved_environment_value_fails_before_step_resolution(tmp_path: Path) -> None:
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  image_name:
    type: string
    starts_with: my-image-storage/
steps:
- run: echo ${{ inputs.image_name }}
""",
    )
    plan = _plan(
        tmp_path,
        """    image_name:
      from:
        env: IMAGE_NAME
""",
    )
    with pytest.raises(FleetPlanInvalid, match="must start with 'my-image-storage/'"):
        resolver.resolve(plan, environment={"IMAGE_NAME": "other/image"})


def test_yaml_source_reads_repository_scalar_and_validates_it(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "project.yml").write_text(
        "project:\n  version: 1.2.3\n  image: my-image-storage/api\n", encoding="utf-8"
    )
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
  image:
    type: string
    starts_with: my-image-storage/
steps:
- run: echo ${{ inputs.version }} ${{ inputs.image }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
        yaml:
          path: project.yml
          key: project.version
    image:
      from:
        yaml:
          path: project.yml
          key: project.image
""",
    )
    resolved = resolver.resolve(plan, repository=repository)
    assert resolved.steps[0].argv == ("echo", "1.2.3", "my-image-storage/api")


def test_yaml_source_cannot_escape_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (tmp_path / "outside.yml").write_text("value: secret\n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  value:
    type: string
steps:
- run: echo ${{ inputs.value }}
""",
    )
    plan = _plan(
        tmp_path,
        """    value:
      from:
        yaml:
          path: ../outside.yml
          key: value
""",
    )
    with pytest.raises(FleetPlanInvalid, match="must not contain.*traversal"):
        resolver.resolve(plan, repository=repository)


def test_missing_yaml_key_falls_through_to_default(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "project.yml").write_text("project: {}\n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
    default: 1.0.0
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
        yaml:
          path: project.yml
          key: project.version
""",
    )
    assert resolver.resolve(plan, repository=repository).steps[0].argv == ("echo", "1.0.0")


def test_git_metadata_is_observed_by_backend_not_a_step(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "tracked.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()

    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  revision:
    type: git_revision
steps:
- run: echo ${{ inputs.revision }}
""",
    )
    plan = _plan(
        tmp_path,
        """    revision:
      from:
        git: commit_short_sha
""",
    )
    resolved = resolver.resolve(plan, repository=repository)
    assert resolved.steps[0].argv == ("echo", sha[:8])
    assert len(resolved.steps) == 1


def test_repository_sources_require_repository_bound_resolution(tmp_path: Path) -> None:
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
        yaml:
          path: project.yml
          key: project.version
""",
    )
    with pytest.raises(FleetPlanInvalid, match="requires repository-bound resolution"):
        resolver.resolve(plan)


def test_structural_workspace_input_rejects_free_text_spoof(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yml"
    plan.write_text(
        """version: 1
goons:
- import: oci/publish
  with:
    source_workspace: arbitrary-step-name
    publish_registry_hostname: registry.example.com
    publish_repository: team/app
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="structural workspace reference"):
        FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve(plan)


@pytest.mark.parametrize(
    ("input_type", "invalid"),
    [
        ("semver", "1.0"),
        ("semver", "1.0.0-01"),
        ("duration", "0m"),
        ("kubernetes_name", "UPPER"),
        ("oci_repository", "Team/App"),
        ("oci_reference", "registry.example.com/team/app:latest"),
    ],
)
def test_semantic_domain_inputs_reject_invalid_values(input_type: str, invalid: str) -> None:
    compiled = parse_goon_template(
        f"""version: 1
inputs:
  value:
    type: {input_type}
steps:
- run: echo ${{{{ inputs.value }}}}
"""
    )
    with pytest.raises(FleetPlanInvalid, match=f"invalid {input_type} syntax"):
        bind_inputs(compiled, {"value": invalid}, context="semantic-test")


def test_missing_yaml_file_falls_through_to_next_source(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
      - yaml:
          path: missing.yml
          key: project.version
      - env: VERSION
""",
    )

    assert resolver.resolve(plan, repository=repository, environment={"VERSION": "2.1.0"}).steps[
        0
    ].argv == ("echo", "2.1.0")


def test_unknown_input_source_does_not_create_expression_language() -> None:
    with pytest.raises(
        FleetPlanInvalid,
        match="source must use env, yaml, toml, text, or git",
    ):
        parse_plan_template(
            """version: 1
goons:
- import: general/leaf
  with:
    value:
      from:
        expression: concat(foo, bar)
"""
        )


def test_repository_composite_root_uses_explicit_projection_forwarding(tmp_path: Path) -> None:
    root = tmp_path / "goons"
    _package(
        root,
        "general/leaf",
        """version: 1
inputs:
  value:
    type: string
steps:
- run: echo ${{ inputs.value }}
""",
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    goon = repository / "goon.yml"
    goon.write_text(
        """version: 1
inputs:
- value
goons:
- import: general/leaf
  with:
    value: ${{ inputs.value }}
""",
        encoding="utf-8",
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(root))

    resolved = resolver.resolve(goon, inputs={"value": "ok"})

    assert resolved.steps[0].argv == ("echo", "ok")


def test_toml_source_reads_pyproject_scalar_and_validates_it(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "2.4.1"\n', encoding="utf-8"
    )
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
        toml:
          path: pyproject.toml
          key: project.version
""",
    )

    assert resolver.resolve(plan, repository=repository).steps[0].argv == (
        "echo",
        "2.4.1",
    )


def test_text_source_reads_one_trimmed_scalar_value(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "VERSION").write_text("  3.1.0  \n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
        text:
          path: VERSION
""",
    )

    assert resolver.resolve(plan, repository=repository).steps[0].argv == (
        "echo",
        "3.1.0",
    )


def test_missing_toml_key_falls_through_to_text_source(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (repository / "VERSION").write_text("4.2.0\n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  version:
    type: semver
steps:
- run: echo ${{ inputs.version }}
""",
    )
    plan = _plan(
        tmp_path,
        """    version:
      from:
      - toml:
          path: pyproject.toml
          key: project.version
      - text:
          path: VERSION
""",
    )

    assert resolver.resolve(plan, repository=repository).steps[0].argv == (
        "echo",
        "4.2.0",
    )


def test_toml_and_text_sources_cannot_escape_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (tmp_path / "outside.toml").write_text('value = "secret"\n', encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret\n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  value:
    type: string
steps:
- run: echo ${{ inputs.value }}
""",
    )

    for binding in (
        """    value:
      from:
        toml:
          path: ../outside.toml
          key: value
""",
        """    value:
      from:
        text:
          path: ../outside.txt
""",
    ):
        with pytest.raises(FleetPlanInvalid, match="must not contain.*traversal"):
            resolver.resolve(_plan(tmp_path, binding), repository=repository)


def test_text_source_rejects_multiline_content(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "VERSION.txt").write_text("1.0.0\nsecond-line\n", encoding="utf-8")
    resolver = _resolver(
        tmp_path,
        """version: 1
inputs:
  value:
    type: string
steps:
- run: echo ${{ inputs.value }}
""",
    )
    plan = _plan(
        tmp_path,
        """    value:
      from:
        text:
          path: VERSION.txt
""",
    )

    with pytest.raises(FleetPlanInvalid, match="exactly one non-empty line"):
        resolver.resolve(plan, repository=repository)
