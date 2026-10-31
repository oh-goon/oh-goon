from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.integrations.python_goon_projects import materialize_python_goon_project
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

SHOWCASE = Path(__file__).resolve().parents[4] / "templates/goons/general/orders-taskflow"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    selected = tmp_path / "project"
    shutil.copytree(
        SHOWCASE,
        selected,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    return selected


def test_capture_survives_relocation_and_transports_no_live_path(
    project: Path, tmp_path: Path
) -> None:
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(project / "goon.yml")
    plan = resolver.resolve_source_graph(graph)
    captured = graph.projects()[0]
    shutil.rmtree(project)
    destination = tmp_path / "receiver"
    materialize_python_goon_project(captured.archive, captured.identity, destination)
    assert (destination / "src/orders_pipeline/data/official.json").is_file()
    assert isinstance(plan.steps[0], ResolvedCommandStep)
    assert plan.steps[0].project_source == captured.identity
    assert "orders_pipeline.operations" not in plan.model_dump_json()
    assert str(project) not in plan.model_dump_json(exclude={"step_sources"})


@pytest.mark.process
def test_captured_dataset_relocates_without_exposing_values_in_public_identity(
    project: Path, tmp_path: Path
) -> None:
    dataset = Path("src/orders_pipeline/data/official.json")
    canary = "private-dataset-canary-should-not-be-public-metadata"
    (project / ".gitignore").write_text(dataset.as_posix() + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", "--template=", str(project)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "check-ignore", "--quiet", "--", dataset.as_posix()],
        check=True,
        capture_output=True,
    )
    (project / dataset).write_text(json.dumps({canary: "10.25"}), encoding="utf-8")
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(project / "goon.yml")
    original_plan = resolver.resolve_source_graph(graph)
    captured = graph.projects()[0]
    original_lock = build_execution_lock(original_plan)

    (project / dataset).write_text(json.dumps({canary: "30.10"}), encoding="utf-8")
    changed_graph = resolver.analyze_source(project / "goon.yml")
    changed_plan = resolver.resolve_source_graph(changed_graph)
    changed_capture = changed_graph.projects()[0]
    changed_lock = build_execution_lock(changed_plan)
    assert captured.identity.content_sha256 != changed_capture.identity.content_sha256
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(changed_lock, original_lock)
    for plan, lock in ((original_plan, original_lock), (changed_plan, changed_lock)):
        assert canary not in plan.model_dump_json()
        assert canary not in lock.model_dump_json()

    # The real Orders operation consumes each frozen delivery in a fresh native
    # process. This qualifies resource capture, not installed-CLI Run admission.
    reader = (
        "from pathlib import Path; import runpy, sys; "
        "operations = runpy.run_path(sys.argv[1]); "
        "print(operations['transform'](operations['extract'](Path(sys.argv[2]))))"
    )
    for index, (source, total) in enumerate(((captured, "10.25"), (changed_capture, "30.10"))):
        destination = tmp_path / f"delivery-{index}"
        materialize_python_goon_project(source.archive, source.identity, destination)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                reader,
                str(destination / "src/orders_pipeline/operations.py"),
                str(destination / dataset),
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout == total + "\n"
        assert result.stderr == ""
    assert json.loads((project / dataset).read_text(encoding="utf-8")) == {canary: "30.10"}


@pytest.mark.parametrize("imported", [False, True])
def test_explicit_untrusted_package_policy_preserves_required_isolation_during_resolution(
    project: Path, tmp_path: Path, imported: bool
) -> None:
    declaration: dict[str, Any]
    if imported:
        package = tmp_path / "goons/general/owned"
        shutil.copytree(project, package)
        source = tmp_path / "plan.yml"
        declaration = {"goons": [{"import": "general/owned", "from": "./goons"}]}
    else:
        source = project / "goon.yml"
        declaration = yaml.safe_load(source.read_text())
    resolver = FleetPlanResolver()
    for execution in (
        {"purpose": "verification"},
        {"purpose": "cicd"},
        {"purpose": "cicd", "trust_repository_code": True},
    ):
        declaration["execution"] = execution
        source.write_text(yaml.safe_dump(declaration))
        plan = resolver.resolve(source)
        assert plan.steps and all(
            isinstance(step, ResolvedCommandStep) and step.project_source is not None
            for step in plan.steps
        )
    declaration["execution"] = {"purpose": "cicd", "trust_repository_code": False}
    source.write_text(yaml.safe_dump(declaration))
    from oh_goon.core.models.fleet_plan_execution_policy import compile_lace_required_guarantees
    from oh_goon.core.models.fleet_plan_isolation import IsolationKernelBoundary

    plan = resolver.resolve(source)
    assert plan.steps and all(
        isinstance(step, ResolvedCommandStep) and step.project_source is not None
        for step in plan.steps
    )
    guarantees = compile_lace_required_guarantees(plan.execution).require_defined()
    assert guarantees.minimum_kernel_boundary is IsolationKernelBoundary.APPLICATION_KERNEL


def test_generated_state_does_not_change_authored_package_identity(project: Path) -> None:
    resolver = FleetPlanResolver()
    original = build_execution_lock(resolver.resolve(project / "goon.yml"))
    for name in (
        ".venv/bin/python",
        "__pycache__/demo.pyc",
        ".pytest_cache/state",
        ".showcase/result.json",
        "goon.lock",
    ):
        output = project / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("generated")
    assert build_execution_lock(resolver.resolve(project / "goon.yml")) == original


@pytest.mark.process
@pytest.mark.host_dns
def test_native_setuptools_sync_preserves_authored_package_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    implementation = project / "src/native_demo"
    implementation.mkdir(parents=True)
    (implementation / "__init__.py").write_text("def main():\n    print('native demo')\n")
    (project / "goon.yml").write_text("runtime: python\nsteps:\n- run: native-demo\n")
    (project / "README.md").write_text("# general/native-demo\n")
    (project / ".gitignore").write_text("*.egg-info/\n.venv/\nbuild/\ndist/\n")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "native-demo"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        '[project.scripts]\nnative-demo = "native_demo:main"\n'
        '[build-system]\nrequires = ["setuptools==80.9.0"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )
    for name in ("build/recipe.txt", "dist/fixture.json"):
        authored = project / name
        authored.parent.mkdir()
        authored.write_text("authored resource\n")

    def native_uv(*arguments: str) -> None:
        result = subprocess.run(
            ["uv", *arguments, "--project", str(project), "--python", sys.executable],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(project / ".venv")},
        )
        assert result.returncode == 0, result.stderr

    native_uv("lock", "--offline")
    resolver = FleetPlanResolver()
    before = resolver.analyze_source(project / "goon.yml").projects()[0]
    native_uv("sync", "--locked", "--no-default-groups")
    assert (project / "src/native_demo.egg-info/PKG-INFO").is_file()
    after = resolver.analyze_source(project / "goon.yml").projects()[0]
    assert after.identity == before.identity
    assert after.archive == before.archive
    receiver = tmp_path / "receiver"
    materialize_python_goon_project(after.archive, after.identity, receiver)
    assert not (receiver / "src/native_demo.egg-info").exists()
    assert (receiver / "build/recipe.txt").read_text() == "authored resource\n"
    assert (receiver / "dist/fixture.json").read_text() == "authored resource\n"
    assert (project / "src/native_demo.egg-info/PKG-INFO").is_file()


def test_nonroot_working_directory_generated_outputs_do_not_change_source_identity(
    project: Path,
) -> None:
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    for step in declaration["steps"]:
        step["working_directory"] = "scratch/results"
    goon.write_text(yaml.safe_dump(declaration))
    resolver = FleetPlanResolver()
    original = build_execution_lock(resolver.resolve(goon))
    for name in ("extracted.json", "total.txt", "result.json"):
        output = project / "scratch/results/.showcase" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("generated")
    assert build_execution_lock(resolver.resolve(goon)) == original


@pytest.mark.parametrize("field", ("working_directory", "artifacts", "outputs"))
def test_python_package_dynamic_source_membership_refuses_before_capture(
    project: Path, field: str
) -> None:
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["inputs"]["path"] = "result"
    step = declaration["steps"][0]
    expression = "${{ inputs.path }}"
    step[field] = (
        expression
        if field == "working_directory"
        else [{"path": expression}]
        if field == "artifacts"
        else {"DATA": expression}
    )
    goon.write_text(yaml.safe_dump(declaration))
    with pytest.raises(FleetPlanInvalid, match="file paths must be literal"):
        FleetPlanResolver().resolve(goon)


def test_imported_packages_share_the_original_source_graph_memory_budget(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.services import fleet_plan_source_graph

    catalog = tmp_path / "goons/general"
    for name in ("first", "second"):
        shutil.copytree(project, catalog / name)
    plan = tmp_path / "plan.yml"
    plan.write_text(
        "goons:\n- import: general/first\n  from: goons\n- import: general/second\n  from: goons\n"
    )
    resolver = FleetPlanResolver()
    baseline = resolver.analyze_source(plan)
    assert len(baseline.projects()) == 2
    budget = baseline.projects()[0].identity.byte_count + 1
    monkeypatch.setattr(fleet_plan_source_graph, "MAX_GRAPH_PROJECT_BYTES", budget)
    with pytest.raises(FleetPlanInvalid, match="source bounds"):
        resolver.analyze_source(plan)


def test_output_siblings_remain_captured_source_and_invalidate_execution_identity(
    project: Path, tmp_path: Path
) -> None:
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["steps"][0]["artifacts"] = [{"path": "resources/generated/result.json"}]
    goon.write_text(yaml.safe_dump(declaration))
    resource = project / "resources/settings.json"
    resource.parent.mkdir()
    resource.write_text('{"selected": "authored"}')
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(goon)
    original = build_execution_lock(resolver.resolve_source_graph(graph))
    captured = graph.projects()[0]
    destination = tmp_path / "receiver"
    materialize_python_goon_project(captured.archive, captured.identity, destination)
    assert (destination / "resources/settings.json").read_bytes() == resource.read_bytes()
    resource.write_text('{"selected": "changed"}')
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(build_execution_lock(resolver.resolve(goon)), original)


def test_generated_output_ancestors_are_pruned_but_empty_authored_siblings_survive(
    project: Path,
) -> None:
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["steps"][0]["artifacts"] = [{"path": "results/nested/generated.json"}]
    goon.write_text(yaml.safe_dump(declaration))
    resolver = FleetPlanResolver()
    original = build_execution_lock(resolver.resolve(goon))
    output = project / "results/nested/generated.json"
    output.parent.mkdir(parents=True)
    output.write_text("generated")
    assert build_execution_lock(resolver.resolve(goon)) == original
    (project / "results/authored-empty").mkdir()
    changed = build_execution_lock(resolver.resolve(goon))
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(changed, original)


@pytest.mark.parametrize(
    "member",
    [
        "src/orders_pipeline/cli.py",
        "uv.lock",
        "pyproject.toml",
        "src/orders_pipeline/data/official.json",
    ],
)
def test_changed_package_members_invalidate_locked_execution(project: Path, member: str) -> None:
    resolver = FleetPlanResolver()
    original = build_execution_lock(resolver.resolve(project / "goon.yml"))
    path = project / member
    path.write_text(path.read_text() + "\n")
    changed = build_execution_lock(resolver.resolve(project / "goon.yml"))
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(changed, original)


@pytest.mark.parametrize("member", ["uv.lock", "pyproject.toml", "README.md"])
def test_missing_package_authority_refuses_before_execution(project: Path, member: str) -> None:
    (project / member).unlink()
    with pytest.raises(FleetPlanInvalid, match="requires a regular"):
        FleetPlanResolver().resolve(project / "goon.yml")


def test_escaping_dependency_and_source_symlinks_refuse(project: Path, tmp_path: Path) -> None:
    metadata = project / "pyproject.toml"
    original = metadata.read_text()
    metadata.write_text(original + '\n[tool.uv.sources]\nprivate = {path = "../private"}\n')
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().resolve(project / "goon.yml")
    metadata.write_text(original)
    (project / "escape").symlink_to(tmp_path / "private")
    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        FleetPlanResolver().resolve(project / "goon.yml")


@pytest.mark.process
def test_native_virtual_workspace_must_relocate_with_captured_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    member = project / "member"
    member.mkdir(parents=True)
    (project / "goon.yml").write_text("runtime: python\nsteps:\n- run: inside\n")
    (project / "README.md").write_text("# general/inside\n")
    metadata = """[project]
name = "inside"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["outside"]
[project.scripts]
inside = "inside:main"
[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
[tool.uv.sources]
outside = {workspace = true}
[tool.uv.workspace]
members = ["member"]
"""
    (project / "pyproject.toml").write_text(metadata)
    (project / "src/inside").mkdir(parents=True)
    (project / "src/inside/__init__.py").write_text("def main():\n    print('inside')\n")
    (member / "pyproject.toml").write_text(
        '[project]\nname = "outside"\nversion = "0.1.0"\n[tool.uv]\npackage = false\n'
    )

    def native_lock(root: Path, *, check: bool = False) -> None:
        result = subprocess.run(
            [
                "uv",
                "lock",
                "--offline",
                "--project",
                str(root),
                "--python",
                sys.executable,
                *(["--check"] if check else []),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    native_lock(project)
    captured = FleetPlanResolver().analyze_source(project / "goon.yml").projects()[0]
    receiver = tmp_path / "receiver"
    materialize_python_goon_project(captured.archive, captured.identity, receiver)
    native_lock(receiver, check=True)

    # Native uv accepts this workspace, but it cannot relocate as one package.
    member.rename(tmp_path / "outside")
    (project / "pyproject.toml").write_text(metadata.replace('["member"]', '["../outside"]'))
    native_lock(project)
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")


@pytest.mark.parametrize("path", ("../outside", "C:/outside", r"C:\outside"))
def test_locked_virtual_dependency_cannot_escape_package(project: Path, path: str) -> None:
    resolver = FleetPlanResolver()
    assert resolver.resolve(project / "goon.yml").steps
    lock = project / "uv.lock"
    with lock.open("a") as stream:
        stream.write(
            '\n[[package]]\nname = "outside"\nversion = "0.1.0"\n'
            + "source = {virtual = "
            + repr(path)
            + "}\n"
        )
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        resolver.resolve(project / "goon.yml")


@pytest.mark.parametrize("target", (".showcase/result.json", ".showcase"))
def test_source_symlink_to_declared_generated_output_refuses_before_capture(
    project: Path, target: str
) -> None:
    generated = project / ".showcase/result.json"
    generated.parent.mkdir()
    generated.write_text("generated")
    (project / "generated-link").symlink_to(target)
    with pytest.raises(FleetPlanInvalid, match="symlink targets generated/operational state"):
        FleetPlanResolver().resolve(project / "goon.yml")


def test_corrupt_source_delivery_is_rejected(project: Path, tmp_path: Path) -> None:
    captured = FleetPlanResolver().analyze_source(project / "goon.yml").projects()[0]
    with pytest.raises(FleetPlanInvalid, match="delivery differs"):
        materialize_python_goon_project(
            captured.archive + b"changed", captured.identity, tmp_path / "receiver"
        )
    assert not (tmp_path / "receiver").exists()


@pytest.mark.parametrize("excess", ("entries", "bytes"))
def test_source_delivery_refuses_excess_archive_members_before_materialization(
    project: Path, tmp_path: Path, excess: str
) -> None:
    captured = FleetPlanResolver().analyze_source(project / "goon.yml").projects()[0]
    buffer = io.BytesIO()
    if excess == "bytes":
        member = tarfile.TarInfo("too-large")
        member.size = 64 * 1024 * 1024 + 1
        # An oversized advertised member must refuse before reading its payload.
        packet = gzip.compress(member.tobuf())
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for index in range(4097):
                archive.addfile(tarfile.TarInfo(f"member-{index}"))
        packet = buffer.getvalue()
    expected = captured.identity.model_copy(
        update={"archive_sha256": hashlib.sha256(packet).hexdigest()}
    )
    with pytest.raises(FleetPlanInvalid, match="exceeds source bounds"):
        materialize_python_goon_project(packet, expected, tmp_path / "receiver")
    assert not (tmp_path / "receiver").exists()
