from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Callable, Iterator
from importlib import metadata
from inspect import getsourcefile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oh_goon.engine import command as command_owner
from oh_goon.integrations import hashmarks as public_evidence
from oh_goon.integrations.hashmarks import development_verification as asset_owner
from oh_goon.integrations.hashmarks import runtime_contract
from oh_goon.repository_tooling import _dependency_lock_portability as lock_owner
from oh_goon.repository_tooling import verification_gate_control as gate
from oh_goon.repository_tooling import development_verification as admission_owner
from oh_goon.repository_tooling.release import package_slim
from oh_goon.repository_tooling.verification_gate_control import (
    DOCUMENTATION_ASSET_OBLIGATION,
    DOCUMENTATION_PROOF_OBLIGATION,
    DOCUMENTATION_PROOF_TEST,
    HASHMARKS_UPGRADE_OBLIGATION,
    HASHMARKS_UPGRADE_TESTS,
    REAL_LIFECYCLE_QA_PATHS,
    REAL_LIFECYCLE_QA_TEST,
    VERIFICATION_POLICY_TESTS,
    is_verification_policy_path,
    non_executable_documentation,
    required_static_obligations,
    validate_merge_proof,
)

_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XkAAAAASUVORK5CYII="
)


@pytest.fixture
def static_project_packet() -> dict[str, Any]:
    path = "frontend/src/contracts.ts"
    member = {"path": path, "state": "known-present", "member_revision": "source:1"}
    config = {
        "path": "frontend/tsconfig.app.json",
        "state": "known-present",
        "member_revision": "config:1",
    }
    return {
        "schema": "hashmarks.changed-set-verification.v1",
        "freshness": "current",
        "changes": [
            {
                "path": path,
                "classification": {
                    "path": path,
                    "kind": "source",
                    "source_member_revision": member["member_revision"],
                    "supporting_source_members": [member, config],
                    "static_project": {
                        "kind": "type-only-static-project",
                        "qualification": "qualified",
                        "syntax_completeness": "complete",
                        "value_syntax": "none-observed-within-complete-member",
                        "source_member": member,
                        "parser": {
                            "grammar": "typescript",
                            "signature": "parser:1",
                            "capability": {
                                "available": True,
                                "name": "tree-sitter-ranges",
                                "version": "0.13.0",
                            },
                        },
                        "compiler": "typescript-resolver:6.0.3:canonical-syntax.v2",
                        "compilers": [
                            {
                                "module": "frontend/node_modules/typescript/lib/typescript.js",
                                "version": "6.0.3",
                                "entry_file_sha256": "a" * 64,
                                "scope": "selected-compiler-entry-file-only",
                            }
                        ],
                        "projects": [{"root": "frontend", "config": config["path"]}],
                        "configuration_inputs": [config],
                        "runtime_execution": "not-established",
                        "negative_evidence_admissible": False,
                        "evidence_identity": "native:1",
                    },
                },
            }
        ],
    }


def _static_proof(packet: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = packet["changes"][0]["path"]
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": [path],
        "source_identity": "sha256:" + "c" * 64,
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "REQUIRED",
        "static": ["architecture", "frontend"],
        "pytest": [],
        "frontend": [],
        "obligations": [gate.STATIC_PROJECT_OBLIGATION],
        "evidence": {
            "producer_implementation_identity": "sha256:" + "d" * 64,
            "owner_coverage": "not-claimed",
            "static_project_evidence": [packet],
            "observations": [{"changed_path": path, "state": "UNAVAILABLE"}],
        },
    }
    context = {"source_identity": plan["source_identity"], "native": "context"}
    outcomes = [{"owner": "Architecture", "returncode": 0}]
    for script in ("typecheck", "lint", "architecture", "i18n:check"):
        outcomes.append(
            {
                "owner": f"Frontend {script}",
                "returncode": 0,
                "static_project_context": copy.deepcopy(context),
                "native_execution": {
                    "argv": ["npm", "run", script],
                    "process_group_quiescent": True,
                },
            }
        )
    return plan, {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": outcomes,
    }


def _validate_static(
    root: Path, plan: dict[str, Any], result: dict[str, Any], trusted_root: Path
) -> None:
    result["plan"] = copy.deepcopy(plan)
    validate_merge_proof(
        plan,
        result,
        candidate="a" * 40,
        base="b" * 40,
        changed_paths=plan["changed_paths"],
        control_base="b" * 40,
        root=root,
        trusted_root=trusted_root,
    )


def test_static_project_proof_uses_native_project_and_completed_existing_owners(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    static_project_packet: dict[str, Any],
) -> None:
    tmp_path = static_preparation_root
    plan, result = _static_proof(static_project_packet)
    observed: list[tuple[Path, tuple[str, ...]]] = []

    def observe(root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
        observed.append((root, paths))
        return static_project_packet

    monkeypatch.setattr(public_evidence, "observe_repository_assets", observe)
    monkeypatch.setattr(
        gate,
        "static_project_context",
        lambda *_args: result["outcomes"][1]["static_project_context"],
    )
    _validate_static(tmp_path, plan, result, reviewed_static_root)
    assert observed == [(tmp_path, ("frontend/src/contracts.ts",))]
    assert plan["frontend"] == []
    assert plan["evidence"]["observations"][0]["state"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    "missing_frontend",
    (
        "skipped",
        "missing-owner",
        "missing-collection",
        "duplicate-file",
        "missing-variant",
    ),
)
def test_static_project_and_runtime_frontend_keep_their_distinct_native_proofs(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    static_project_packet: dict[str, Any],
    missing_frontend: str,
) -> None:
    tmp_path = static_preparation_root
    plan, result = _static_proof(static_project_packet)
    runtime, test = "frontend/src/runtime.ts", "frontend/src/runtime.test.ts"
    plan["changed_paths"].append(runtime)
    plan["frontend"] = [test]
    plan["evidence"]["frontend_admission"] = {
        "collection": {
            "requested": [test],
            "selected": [{"file": test, "projectName": "node"}],
            "unavailable_advisory_targets": [],
            "native_project_root": str(tmp_path / "frontend"),
            "native_execution": {
                "argv": [
                    "npm",
                    "exec",
                    "--offline",
                    "--no",
                    "--",
                    "vitest",
                    "list",
                    "./src/runtime.test.ts",
                    "--filesOnly",
                    f"--json={tmp_path.parent / 'collection.json'}",
                ],
                "returncode": 0,
                "process_group_quiescent": True,
            },
        }
    }
    plan["evidence"]["observations"].append(
        {
            "changed_path": runtime,
            "state": "LINKED",
            "nominations": [
                {
                    "workspace_prefix": "frontend/",
                    "nomination": {
                        "available": True,
                        "verification_target": {"path": "src/runtime.test.ts"},
                    },
                }
            ],
        }
    )
    native: dict[str, Any] = {
        "success": True,
        "numTotalTests": 1,
        "numPassedTests": 1,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
        "numTotalTestSuites": 1,
        "numPassedTestSuites": 1,
        "numFailedTestSuites": 0,
        "numPendingTestSuites": 0,
        "testResults": [
            {"name": str(tmp_path / test), "assertionResults": [{"status": "passed"}]}
        ],
    }
    result["outcomes"].append(
        {
            "owner": "Frontend behavior",
            "returncode": 0,
            "native_project_root": str(tmp_path / "frontend"),
            "native_report": native,
        }
    )
    monkeypatch.setattr(
        public_evidence,
        "observe_repository_assets",
        lambda *_args: static_project_packet,
    )
    monkeypatch.setattr(
        gate,
        "static_project_context",
        lambda *_args: result["outcomes"][1]["static_project_context"],
    )
    _validate_static(tmp_path, plan, result, reviewed_static_root)
    if missing_frontend == "skipped":
        native["testResults"][0]["assertionResults"][0]["status"] = "skipped"
        with pytest.raises(ValueError, match="frontend proof is skipped"):
            _validate_static(tmp_path, plan, result, reviewed_static_root)
    elif missing_frontend == "missing-owner":
        result["outcomes"] = [
            row for row in result["outcomes"] if row["owner"] != "Frontend behavior"
        ]
        with pytest.raises(ValueError, match="check owners are missing"):
            _validate_static(tmp_path, plan, result, reviewed_static_root)
    elif missing_frontend == "missing-collection":
        del plan["evidence"]["frontend_admission"]
        with pytest.raises(
            ValueError, match="frontend collection proof is unavailable"
        ):
            _validate_static(tmp_path, plan, result, reviewed_static_root)
    elif missing_frontend == "duplicate-file":
        native["testResults"].append(copy.deepcopy(native["testResults"][0]))
        for field in (
            "numTotalTests",
            "numPassedTests",
            "numTotalTestSuites",
            "numPassedTestSuites",
        ):
            native[field] = 2
        with pytest.raises(ValueError, match="files are missing, duplicated or extra"):
            _validate_static(tmp_path, plan, result, reviewed_static_root)
    else:
        plan["evidence"]["frontend_admission"]["collection"]["selected"].append(
            {"file": test, "projectName": "dom"}
        )
        with pytest.raises(ValueError, match="files are missing, duplicated or extra"):
            _validate_static(tmp_path, plan, result, reviewed_static_root)


@pytest.mark.parametrize(
    "role", ["unknown", "verifier", "verification-support", "configuration"]
)
def test_native_non_source_role_cannot_take_static_project_source_route(
    static_project_packet: dict[str, Any], role: str
) -> None:
    static_project_packet["changes"][0]["classification"]["kind"] = role
    assert (
        gate.admitted_static_projects(
            ("frontend/src/contracts.ts",),
            {"static_project_evidence": [static_project_packet]},
        )
        == {}
    )


def test_static_project_source_revision_must_match_native_classification(
    static_project_packet: dict[str, Any],
) -> None:
    static_project_packet["changes"][0]["classification"]["source_member_revision"] = (
        "another-source"
    )
    assert (
        gate.admitted_static_projects(
            ("frontend/src/contracts.ts",),
            {"static_project_evidence": [static_project_packet]},
        )
        == {}
    )


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "recovered",
        "value",
        "runtime",
        "compiler",
        "config",
        "ledger",
        "parser",
        "parser-capability",
        "outside-project",
    ],
)
def test_incomplete_or_runtime_project_facts_keep_behavioral_proof_required(
    static_project_packet: dict[str, Any], fault: str
) -> None:
    classification = static_project_packet["changes"][0]["classification"]
    fact = classification["static_project"]
    if fault == "unknown":
        fact["qualification"] = "unknown"
    elif fault == "recovered":
        fact["syntax_completeness"] = "recovered"
    elif fault == "value":
        fact["value_syntax"] = "observed"
    elif fault == "runtime":
        fact["runtime_execution"] = "established"
    elif fault == "compiler":
        fact["compilers"] = []
    elif fault == "config":
        fact["configuration_inputs"] = []
    elif fault == "ledger":
        classification["supporting_source_members"] = []
    elif fault == "parser":
        fact["parser"] = {}
    elif fault == "parser-capability":
        fact["parser"]["capability"]["available"] = False
    else:
        fact["projects"][0]["root"] = "another-project"
    assert (
        gate.admitted_static_projects(
            ("frontend/src/contracts.ts",),
            {"static_project_evidence": [static_project_packet]},
        )
        == {}
    )


@pytest.mark.parametrize(
    "fault",
    [
        "fake-fact",
        "omitted-obligation",
        "omitted-fact",
        "skipped",
        "absent-execution",
        "wrong-command",
        "nonquiescent",
        "source",
        "context",
        "omitted-owner",
    ],
)
def test_static_project_gate_rejects_claim_and_execution_substitution(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    static_project_packet: dict[str, Any],
    fault: str,
) -> None:
    tmp_path = static_preparation_root
    observed = copy.deepcopy(static_project_packet)
    plan, result = _static_proof(static_project_packet)
    context = copy.deepcopy(result["outcomes"][1]["static_project_context"])
    monkeypatch.setattr(
        public_evidence, "observe_repository_assets", lambda *_args: observed
    )
    monkeypatch.setattr(gate, "static_project_context", lambda *_args: context)
    row = result["outcomes"][1]
    if fault == "fake-fact":
        static_project_packet["changes"][0]["classification"]["static_project"][
            "evidence_identity"
        ] = "forged"
    elif fault == "omitted-obligation":
        plan["obligations"] = []
    elif fault == "omitted-fact":
        plan["evidence"].pop("static_project_evidence")
    elif fault == "skipped":
        row["status"] = "SKIPPED"
    elif fault == "absent-execution":
        row.pop("native_execution")
    elif fault == "wrong-command":
        row["native_execution"]["argv"] = ["true"]
    elif fault == "nonquiescent":
        row["native_execution"]["process_group_quiescent"] = False
    elif fault == "source":
        row["static_project_context"]["source_identity"] = "other-source"
    elif fault == "context":
        row["static_project_context"]["native"] = "other-compiler"
    else:
        result["outcomes"].pop()
    with pytest.raises(ValueError):
        _validate_static(tmp_path, plan, result, reviewed_static_root)


@pytest.fixture
def static_preparation_root(tmp_path: Path) -> Iterator[Path]:
    project = tmp_path / "frontend"
    project.mkdir()
    repository = Path(__file__).resolve().parents[2]
    for name in ("package.json", "package-lock.json", ".npmrc"):
        shutil.copyfile(repository / "frontend" / name, project / name)
    (project / "src").mkdir()
    (project / "src/contracts.ts").write_text(
        "export type Contract = { value: string };\n"
    )
    try:
        yield tmp_path
    finally:
        installed = project / "node_modules"
        if installed.exists():
            shutil.rmtree(installed)


@pytest.fixture
def reviewed_static_root(
    static_preparation_root: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    trusted = tmp_path_factory.mktemp("reviewed-static-base")
    shutil.copytree(static_preparation_root / "frontend", trusted / "frontend")
    return trusted


@pytest.mark.parametrize("name", ["package.json", "package-lock.json"])
def test_changed_candidate_toolchain_is_refused_before_npm_and_native_observation(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    static_project_packet: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    root, trusted = static_preparation_root, reviewed_static_root
    path = root / "frontend" / name
    path.write_bytes(path.read_bytes() + b"\n")
    identity = package_slim.canonical_source_identity(root)
    monkeypatch.setattr(
        command_owner,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("npm must not start"),
    )
    monkeypatch.setattr(
        public_evidence,
        "observe_repository_assets",
        lambda *_args, **_kwargs: pytest.fail("unreviewed compiler must not load"),
    )
    with pytest.raises(ValueError, match="reviewed base authority"):
        gate.prepare_static_project(root, trusted, identity)
    with pytest.raises(ValueError, match="reviewed base authority"):
        gate._independent_static_projects(
            root,
            ("frontend/src/contracts.ts",),
            {"static_project_evidence": [static_project_packet]},
            trusted_root=trusted,
            source_identity=identity,
        )


def test_static_preparation_request_is_only_a_bounded_capability(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    static_project_packet: dict[str, Any],
) -> None:
    root = static_preparation_root
    plan, _result = _static_proof(static_project_packet)
    assert gate.static_project_capability_requested(
        root, reviewed_static_root, plan, plan["changed_paths"]
    )
    plan["obligations"] = []
    assert not gate.static_project_capability_requested(
        root, reviewed_static_root, plan, plan["changed_paths"]
    )
    plan["obligations"] = [gate.STATIC_PROJECT_OBLIGATION]
    plan["evidence"].pop("static_project_evidence")
    with pytest.raises(ValueError, match="no retained native obligation"):
        gate.static_project_capability_requested(
            root, reviewed_static_root, plan, plan["changed_paths"]
        )


@pytest.mark.parametrize(
    "fault",
    ["root-symlink", "lock-symlink", "package-shape", "lock-shape", "npm-config"],
)
def test_static_preparation_refuses_invalid_inputs_before_npm_effects(
    static_preparation_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    root = static_preparation_root
    trusted = tmp_path / "trusted"
    shutil.copytree(root / "frontend", trusted / "frontend")
    if fault == "root-symlink":
        (root / "frontend").rename(root / "other")
        (root / "frontend").symlink_to(root / "other", target_is_directory=True)
    elif fault == "lock-symlink":
        (root / "frontend/package-lock.json").rename(root / "lock")
        (root / "frontend/package-lock.json").symlink_to(root / "lock")
    elif fault == "package-shape":
        (root / "frontend/package.json").write_text("[]")
    elif fault == "lock-shape":
        (root / "frontend/package-lock.json").write_text('{"lockfileVersion": 1}')
    else:
        (root / "frontend/.npmrc").write_text("ignore-scripts=false\n")
    monkeypatch.setattr(
        command_owner,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("npm must not start"),
    )
    with pytest.raises(ValueError):
        gate.prepare_static_project(root, trusted, "sha256:" + "a" * 64)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "source",
        "lock",
        "failure",
        "nonquiescent",
        "tool-drift",
        "environment-drift",
    ],
)
def test_static_preparation_uses_native_npm_once_and_fences_its_source_inputs(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    root = static_preparation_root
    identity = package_slim.canonical_source_identity(root)
    invocations: list[tuple[str, ...]] = []
    monkeypatch.setenv("NODE_OPTIONS", "--untrusted-option")
    monkeypatch.setenv("NPM_CONFIG_IGNORE_SCRIPTS", "false")
    tool_state = {"selection": "before"}
    monkeypatch.setattr(
        gate,
        "_static_project_tool_context",
        lambda _root, env: {
            "selection": tool_state["selection"],
            "NODE_OPTIONS": env.get("NODE_OPTIONS"),
        },
    )

    def npm(*arguments: str, **options: Any) -> SimpleNamespace:
        invocations.append(arguments)
        assert options["cwd"] == root / "frontend"
        assert options["require_process_group_quiescence"] is True
        assert "NODE_OPTIONS" not in options["env"]
        assert "NPM_CONFIG_IGNORE_SCRIPTS" not in options["env"]
        if fault == "source":
            (root / "frontend/src/contracts.ts").write_text("export const value = 1;\n")
        if fault == "lock":
            (root / "frontend/package-lock.json").write_text("{}")
        if fault == "tool-drift":
            tool_state["selection"] = "after"
        if fault == "environment-drift":
            options["env"]["NODE_OPTIONS"] = "changed"
        return SimpleNamespace(
            returncode=1 if fault == "failure" else 0,
            supervisor=SimpleNamespace(
                final_process_group_quiescent=fault != "nonquiescent"
            ),
        )

    monkeypatch.setattr(command_owner, "run_command", npm)
    if fault is None:
        gate.prepare_static_project(root, reviewed_static_root, identity)
    else:
        with pytest.raises(ValueError):
            gate.prepare_static_project(root, reviewed_static_root, identity)
    assert len(invocations) == 1
    assert invocations[0][:-2] == (
        "npm",
        "--prefix",
        str(root / "frontend"),
        "--workspaces=false",
        "ci",
        "--prefer-offline",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    )
    user, global_config = (
        Path(argument.split("=", 1)[1]) for argument in invocations[0][-2:]
    )
    assert user != global_config and user.parent == global_config.parent
    assert not user.parent.exists()


@pytest.mark.process
@pytest.mark.daily
def test_native_static_preparation_stays_in_fixed_project_despite_ancestor_workspace(
    static_preparation_root: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    ancestor = static_preparation_root
    root = ancestor / "candidate"
    root.mkdir()
    (ancestor / "frontend").rename(root / "frontend")
    (ancestor / "package.json").write_text(
        '{"private":true,"workspaces":["candidate/frontend","sibling"]}'
    )
    (ancestor / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}')
    (ancestor / "sibling").mkdir()
    (ancestor / "sibling/package.json").write_text(
        '{"name":"sibling","dependencies":{"must-not-resolve":"file:missing"}}'
    )
    original_lock = (ancestor / "package-lock.json").read_bytes()
    package_path = root / "frontend/package.json"
    package = json.loads(package_path.read_text())
    package["scripts"]["preinstall"] = (
        "node -e \"require('fs').writeFileSync('../hook-ran','bad')\""
    )
    package_path.write_text(json.dumps(package))
    trusted = tmp_path_factory.mktemp("reviewed-npm-workspace-base")
    shutil.copytree(root / "frontend", trusted / "frontend")
    identity = package_slim.canonical_source_identity(root)
    try:
        gate.prepare_static_project(root, trusted, identity)
        assert (root / "frontend/node_modules/typescript/lib/typescript.js").is_file()
        assert not (ancestor / "node_modules").exists()
        assert not (ancestor / "sibling/node_modules").exists()
        assert not (root / "hook-ran").exists()
        assert (ancestor / "package-lock.json").read_bytes() == original_lock
        assert package_slim.canonical_source_identity(root) == identity
    finally:
        if (root / "frontend/node_modules").exists():
            shutil.rmtree(root / "frontend/node_modules")


def test_static_context_binds_selected_tool_bytes_and_detects_compiler_drift(
    static_preparation_root: Path,
    static_project_packet: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = static_preparation_root
    fact = static_project_packet["changes"][0]["classification"]["static_project"]
    compiler = root / fact["compilers"][0]["module"]
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"native selected compiler entry\n")
    binaries: dict[str, str] = {}
    for name in ("node", "npm"):
        path = root / "frontend" / name
        path.write_text(f"selected {name} entry\n")
        binaries[name] = str(path)
    monkeypatch.setattr(shutil, "which", lambda name, **_options: binaries.get(name))
    monkeypatch.setattr(
        command_owner,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="native-version\n",
            stdout_truncated=False,
            supervisor=SimpleNamespace(final_process_group_quiescent=True),
        ),
    )

    fact["compilers"][0]["entry_file_sha256"] = hashlib.sha256(
        compiler.read_bytes()
    ).hexdigest()
    projects = gate.admitted_static_projects(
        ("frontend/src/contracts.ts",),
        {"static_project_evidence": [static_project_packet]},
    )
    native_options = "--selected-mode=PRIVATE_CONFIGURATION"
    monkeypatch.setenv("NODE_OPTIONS", native_options)
    context = gate.static_project_context(
        root, package_slim.canonical_source_identity(root), projects
    )
    assert set(context["tools"]) == {"node", "npm"}
    assert all(
        row["version"] and len(row["entry_file_sha256"]) == 64
        for row in context["tools"].values()
    )
    assert (
        context["environment"]["NODE_OPTIONS"]
        == hashlib.sha256(native_options.encode()).hexdigest()
    )
    assert native_options not in json.dumps(context)
    monkeypatch.setenv("NODE_OPTIONS", "--changed-native-mode")
    assert (
        gate.static_project_context(root, context["source_identity"], projects)
        != context
    )
    compiler.write_bytes(b"replaced compiler entry\n")
    with pytest.raises(ValueError, match="compiler changed"):
        gate.static_project_context(root, context["source_identity"], projects)


@pytest.mark.parametrize(
    "fault", ["missing", "timeout", "failed", "truncated", "nonquiescent"]
)
def test_static_context_native_tool_observation_fails_closed(
    static_preparation_root: Path,
    static_project_packet: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    root = static_preparation_root
    selected = root / "frontend/node"
    selected.write_text("selected native tool entry\n")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _name, **_options: None if fault == "missing" else str(selected),
    )

    def version(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if fault == "timeout":
            raise subprocess.TimeoutExpired("node", 20)
        return SimpleNamespace(
            returncode=1 if fault == "failed" else 0,
            stdout="version\n",
            stdout_truncated=fault == "truncated",
            supervisor=SimpleNamespace(
                final_process_group_quiescent=fault != "nonquiescent"
            ),
        )

    monkeypatch.setattr(command_owner, "run_command", version)
    projects = gate.admitted_static_projects(
        ("frontend/src/contracts.ts",),
        {"static_project_evidence": [static_project_packet]},
    )
    with pytest.raises(ValueError, match="Static-project native tool"):
        gate.static_project_context(root, "sha256:" + "a" * 64, projects)


@pytest.mark.parametrize("drift", [False, True])
def test_independent_static_observation_fences_actual_selected_tool_bytes(
    static_preparation_root: Path,
    reviewed_static_root: Path,
    static_project_packet: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    drift: bool,
) -> None:
    root = static_preparation_root
    fact = static_project_packet["changes"][0]["classification"]["static_project"]
    compiler = root / fact["compilers"][0]["module"]
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"selected compiler entry\n")
    fact["compilers"][0]["entry_file_sha256"] = hashlib.sha256(
        compiler.read_bytes()
    ).hexdigest()
    tool_root = tmp_path_factory.mktemp("native-observation-tools")
    binaries: dict[str, str] = {}
    for name in ("node", "npm"):
        path = tool_root / name
        path.write_text(f"selected {name} entry\n")
        binaries[name] = str(path)
    monkeypatch.setattr(shutil, "which", lambda name, **_options: binaries.get(name))
    monkeypatch.setattr(
        command_owner,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="native-version\n",
            stdout_truncated=False,
            supervisor=SimpleNamespace(final_process_group_quiescent=True),
        ),
    )
    observations: list[tuple[str, ...]] = []

    def observe(_root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
        observations.append(paths)
        if drift:
            Path(binaries["node"]).write_text("replaced node entry\n")
        return static_project_packet

    monkeypatch.setattr(public_evidence, "observe_repository_assets", observe)
    identity = package_slim.canonical_source_identity(root)

    def reobserve() -> tuple[dict[str, Any], dict[str, Any] | None]:
        return gate._independent_static_projects(
            root,
            ("frontend/src/contracts.ts",),
            {"static_project_evidence": [static_project_packet]},
            trusted_root=reviewed_static_root,
            source_identity=identity,
        )

    if drift:
        with pytest.raises(
            ValueError, match="context changed during trusted observation"
        ):
            reobserve()
    else:
        projects, context = reobserve()
        assert set(projects) == {"frontend/src/contracts.ts"} and context is not None
    assert observations == [("frontend/src/contracts.ts",)]
    assert package_slim.canonical_source_identity(root) == identity


@pytest.mark.process
@pytest.mark.daily
def test_native_static_project_route_uses_locked_npm_and_independent_public_observation(
    static_preparation_root: Path,
    reviewed_static_root: Path,
) -> None:
    root = static_preparation_root
    (root / "frontend/tsconfig.json").write_text(
        '{"files":[],"references":[{"path":"./tsconfig.app.json"}]}'
    )
    (root / "frontend/tsconfig.app.json").write_text(
        '{"compilerOptions":{"noEmit":true,"strict":true,"skipLibCheck":true,"target":"ES2022",'
        '"module":"ESNext","moduleResolution":"Bundler"},"include":["src"]}'
    )
    source = root / "frontend/src/contracts.ts"
    source.write_text("export interface Contract { value: string }\n")
    identity = package_slim.canonical_source_identity(root)
    gate.prepare_static_project(root, reviewed_static_root, identity)
    packet = public_evidence.observe_repository_assets(
        root, ("frontend/src/contracts.ts",)
    )
    evidence = {"static_project_evidence": [packet]}
    projects = gate.admitted_static_projects(("frontend/src/contracts.ts",), evidence)
    assert set(projects) == {"frontend/src/contracts.ts"}
    assert (
        gate._independent_static_projects(
            root,
            ("frontend/src/contracts.ts",),
            evidence,
            trusted_root=reviewed_static_root,
            source_identity=identity,
        )[0]
        == projects
    )
    context = gate.static_project_context(root, identity, projects)
    checked = command_owner.run_command(
        "npm",
        "run",
        "typecheck",
        cwd=root / "frontend",
        timeout=30,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert checked.supervisor.final_process_group_quiescent is True
    assert gate.static_project_context(root, identity, projects) == context
    source.write_text("export const value = 1;\n")
    with pytest.raises(ValueError, match="differs from trusted native source"):
        gate._independent_static_projects(
            root,
            ("frontend/src/contracts.ts",),
            evidence,
            trusted_root=reviewed_static_root,
            source_identity=identity,
        )


def _write_documentation_asset(root: Path, path: str) -> None:
    asset = root / path
    asset.parent.mkdir(parents=True)
    asset.write_bytes(_IMAGE_BYTES)
    (root / "docs/guide.md").write_text(
        f"![Overview]({Path(path).relative_to('docs')})\n", encoding="utf-8"
    )


def _asset_proof(root: Path, path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = asset_owner.nominate_changed_verification(
        root, (path,), asset_paths=(path,)
    )
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": [path],
        "source_identity": package_slim.canonical_source_identity(root),
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "NOT_APPLICABLE",
        "static": ["architecture"],
        "pytest": [],
        "frontend": [],
        "evidence": evidence,
        "obligations": [DOCUMENTATION_ASSET_OBLIGATION],
    }
    return plan, {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [{"owner": "Architecture", "returncode": 0}],
    }


def _validate_asset(root: Path, plan: dict[str, Any], result: dict[str, Any]) -> None:
    validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        control_base=plan["base"],
        root=root,
    )


@pytest.mark.parametrize(
    "path", ("docs/assets/overview.png", "docs/assets/guide/figure.bin")
)
def test_native_asset_policy_is_independent_of_filename_and_suffix(
    tmp_path: Path, path: str
) -> None:
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)

    _validate_asset(tmp_path, plan, result)

    asset = gate.admitted_documentation_assets((path,), plan["evidence"])[path]
    assert asset["content"]["media_type"] == "image/png"
    assert asset["content"]["integrity"] == "not-validated"
    assert asset["consumer_scope"]["completeness"] == "unknown"
    assert asset["consumer_scope"]["negative_evidence_admissible"] is False
    assert not non_executable_documentation(tmp_path, path)


@pytest.mark.parametrize(
    ("consumer", "content"),
    (
        (
            "backend/src/asset_reader.py",
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
        ),
        (
            "tests/test_image.py",
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
        ),
        ("Makefile", "publish:\n\tcp docs/assets/overview.png output.png\n"),
        ("docs/notes.md", "The overview.png file is mentioned without a link.\n"),
    ),
)
def test_observed_product_test_build_and_unqualified_uses_refuse_asset_policy(
    tmp_path: Path, consumer: str, content: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    destination = tmp_path / consumer
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    plan, result = _asset_proof(tmp_path, path)

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "content",
    (
        b"not an image",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:"
        + b"a" * 64
        + b"\nsize 123\n",
        _IMAGE_BYTES + b"x" * 1_048_576,
    ),
    ids=("text-with-image-suffix", "lfs-pointer", "producer-read-bound"),
)
def test_unobserved_image_bytes_cannot_receive_documentation_policy(
    tmp_path: Path, content: bytes
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    (tmp_path / path).write_bytes(content)
    plan, result = _asset_proof(tmp_path, path)

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "fault", ("stale", "source-gap", "truncated", "absent-member", "no-use")
)
def test_uncertain_or_stale_asset_observations_do_not_admit_policy(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, _result = _asset_proof(tmp_path, path)
    packet = plan["evidence"]["asset_evidence"]
    asset = packet["changes"][0]["asset"]
    if fault == "stale":
        packet["freshness"] = "stale"
    elif fault == "source-gap":
        asset["consumer_scope"]["source_coverage"] = "incomplete"
    elif fault == "truncated":
        asset["consumer_scope"]["truncated"] = True
    elif fault == "absent-member":
        asset["supporting_source_members"][-1]["state"] = "known-absent"
    else:
        asset["consumers"] = []

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}


@pytest.mark.parametrize(
    "fault", ("content", "supporting-source", "missing-obligation")
)
def test_trusted_asset_observation_refuses_candidate_evidence_tampering(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)
    asset = plan["evidence"]["asset_evidence"]["changes"][0]["asset"]
    if fault == "content":
        asset["content"]["byte_count"] += 1
    elif fault == "supporting-source":
        asset["supporting_source_members"][-1]["member_revision"] = "f" * 64
    else:
        plan["obligations"] = []
    result["plan"] = copy.deepcopy(plan)

    with pytest.raises(
        ValueError, match="trusted source observation|explicitly admitted"
    ):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "fault", ("image-replaced", "new-runtime-reader", "changed-document")
)
def test_trusted_asset_reobservation_detects_candidate_source_drift(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)
    if fault == "image-replaced":
        (tmp_path / path).write_bytes(b"not image bytes")
    elif fault == "new-runtime-reader":
        reader = tmp_path / "backend/src/asset_reader.py"
        reader.parent.mkdir(parents=True)
        reader.write_text(
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
            encoding="utf-8",
        )
    else:
        (tmp_path / "docs/guide.md").write_text("Image removed.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="trusted source observation"):
        _validate_asset(tmp_path, plan, result)


def _proof(
    *,
    source: str = "source.py",
    test_file: str = "tests/test_source.py",
    additional_tests: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = "sha256:" + "a" * 64
    test_files = [test_file, *additional_tests]
    nodes = [path if "::" in path else f"{path}::test_result" for path in test_files]
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": [source, test_file],
        "source_identity": identity,
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "REQUIRED",
        "static": ["python", "architecture"],
        "pytest": test_files,
        "frontend": [],
        "evidence": {
            "producer_implementation_identity": "sha256:" + "b" * 64,
            "owner_coverage": "not-claimed",
            "observations": [
                {
                    "state": state,
                    "changed_path": path,
                    "nominations": [
                        {
                            "nomination": {
                                "available": True,
                                "verification_target": {"path": test_file},
                            }
                        }
                    ],
                }
                for state, path in (
                    ("LINKED", source),
                    ("CHANGED_TEST", test_file),
                )
            ],
        },
    }
    result: dict[str, Any] = {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [
            {
                "owner": "Native pytest",
                "returncode": 0,
                "native_report": {
                    "status": "PASS",
                    "source_identity": identity,
                    "native_collection": {
                        "discovered": list(nodes),
                        "deselected": [],
                    },
                    "membership_receipt": {
                        "selected": list(nodes),
                        "passed": list(nodes),
                        "terminal_success": True,
                        "effective_pytest_outcomes": [
                            {
                                "member": node,
                                "status": "PASSED",
                                "attempt_identity": "attempt-1",
                                "pytest_outcomes": [
                                    {
                                        "nodeid": node,
                                        "outcome": "PASSED",
                                    }
                                ],
                            }
                            for node in nodes
                        ],
                    },
                },
            }
        ],
    }
    result["outcomes"].extend(
        {"owner": name, "returncode": 0}
        for name in ("Ruff", "Format", "Types", "Architecture")
    )
    return plan, result


def _validate(
    plan: dict[str, Any], result: dict[str, Any], *, control: str | None = None
) -> None:
    validate_merge_proof(
        plan,
        result,
        candidate="a" * 40,
        base="b" * 40,
        changed_paths=["source.py", "tests/test_source.py"],
        control_base=control,
    )


def test_exact_fast_proof_does_not_claim_daily_full() -> None:
    _validate(*_proof())


def _documentation_proof(
    *, behavioral: bool = True, document_path: str = "docs/guide.md"
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan, result = _proof(
        source=document_path,
        test_file=DOCUMENTATION_PROOF_TEST,
        additional_tests=("tests/test_source.py",) if behavioral else (),
    )
    plan["evidence"]["observations"][0].update(state="UNAVAILABLE", nominations=[])
    plan["changed_paths"] = [document_path]
    plan["evidence"]["observations"] = plan["evidence"]["observations"][:1]
    if behavioral:
        plan["changed_paths"].append("source.py")
        plan["evidence"]["observations"].append(
            {
                "changed_path": "source.py",
                "state": "LINKED",
                "nominations": [
                    {
                        "nomination": {
                            "available": True,
                            "verification_target": {"path": "tests/test_source.py"},
                        }
                    }
                ],
            }
        )
    plan["evidence"]["documentation_policy"] = {
        "authority": "oh-goon-execution-policy",
        "paths": [document_path],
        "coverage": "not-claimed",
    }
    plan["obligations"] = [DOCUMENTATION_PROOF_OBLIGATION]
    result["plan"] = copy.deepcopy(plan)
    return plan, result


def _validate_declared(
    plan: dict[str, Any],
    result: dict[str, Any],
    root: Path | None = None,
    *,
    removed_paths: tuple[str, ...] = (),
) -> None:
    validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        removed_paths=removed_paths,
        control_base=plan["base"],
        root=root,
    )


@pytest.mark.parametrize(
    "document_path",
    (
        "docs/guide.md",
        "examples/sample/README.md",
        "templates/plans/general/sample/README.md",
    ),
)
def test_documentation_proof_handles_fenced_and_deleted_sources(
    tmp_path: Path, document_path: str
) -> None:
    document = tmp_path / document_path
    document.parent.mkdir(parents=True)
    document.write_text("# Guide\n```sh\nuv run pytest\n```\n")
    plan, result = _documentation_proof(behavioral=False, document_path=document_path)
    plan.update(
        changed_paths=[document_path],
        behavioral_status="NOT_APPLICABLE",
        static=["architecture"],
        evidence={"documentation_policy": plan["evidence"]["documentation_policy"]},
    )
    result["plan"] = copy.deepcopy(plan)
    result["outcomes"] = [
        outcome
        for outcome in result["outcomes"]
        if outcome["owner"] in {"Native pytest", "Architecture"}
    ]

    _validate_declared(plan, result, tmp_path)
    document.unlink()
    plan["removed_paths"] = [document_path]
    result["plan"] = copy.deepcopy(plan)
    _validate_declared(plan, result, tmp_path, removed_paths=(document_path,))


@pytest.mark.parametrize(
    "document_path",
    ("examples/sample/README.md", "templates/plans/general/sample/README.md"),
)
def test_readme_documentation_proof_requires_the_original_contract_receipt(
    document_path: str,
) -> None:
    plan, result = _documentation_proof(behavioral=False, document_path=document_path)
    plan["behavioral_status"] = "NOT_APPLICABLE"
    result["plan"] = copy.deepcopy(plan)
    _validate_declared(plan, result)

    plan["pytest"] = []
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Bounded documentation proof was omitted"):
        _validate_declared(plan, result)


@pytest.mark.parametrize(
    "source_path",
    (
        "examples/sample/SKILL.md",
        "examples/sample/run.yml",
        "examples/sample/providers.toml",
        "templates/goons/general/sample/goon.yml",
        "templates/goons/general/sample/check.py",
        "docs/certification/README.md",
        "examples/sample/AGENTS.md",
    ),
)
def test_readme_documentation_proof_cannot_claim_adjacent_machine_inputs(
    source_path: str,
) -> None:
    plan, result = _documentation_proof(
        behavioral=False, document_path="examples/sample/README.md"
    )
    plan["behavioral_status"] = "NOT_APPLICABLE"
    result["plan"] = copy.deepcopy(plan)
    _validate_declared(plan, result)

    plan["changed_paths"].append(source_path)
    plan["evidence"]["documentation_policy"]["paths"].append(source_path)
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError):
        _validate_declared(plan, result)


def test_documentation_check_does_not_substitute_for_required_behavior() -> None:
    plan, result = _documentation_proof()
    _validate_declared(plan, result)

    plan, result = _documentation_proof(behavioral=False)
    with pytest.raises(ValueError, match="Executable changes have no behavioral proof"):
        _validate_declared(plan, result)


def test_source_qualified_documentation_check_retains_its_behavioral_role() -> None:
    plan, result = _documentation_proof(behavioral=False)
    observation = {
        "changed_path": "source.py",
        "state": "LINKED",
        "nominations": [
            {
                "nomination": {
                    "available": True,
                    "verification_target": {"path": DOCUMENTATION_PROOF_TEST},
                }
            }
        ],
    }
    plan["changed_paths"].append("source.py")
    plan["evidence"]["observations"].append(observation)
    result["plan"] = copy.deepcopy(plan)
    _validate_declared(plan, result)

    plan["changed_paths"].remove("source.py")
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Executable changes have no behavioral proof"):
        _validate_declared(plan, result)


@pytest.mark.parametrize("fault", ["omit-contract", "claim-code", "failed-contract"])
def test_documentation_policy_cannot_omit_proof_or_claim_executable_source(
    fault: str,
) -> None:
    plan, result = _documentation_proof()
    _validate_declared(plan, result)
    if fault == "omit-contract":
        plan["pytest"] = []
    elif fault == "claim-code":
        plan["changed_paths"].append("source.py")
        plan["evidence"]["documentation_policy"]["paths"].append("source.py")
    else:
        result["outcomes"][0]["returncode"] = 1
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError):
        _validate_declared(plan, result)


def test_documentation_route_keeps_policy_and_certification_sources_protected() -> None:
    assert (
        gate.documentation_proof_paths(
            (
                "AGENTS.md",
                "docs/CI_CD_CONTRACT.md",
                "docs/certification/questions/exam.md",
            )
        )
        == ()
    )
    plan, result = _documentation_proof()
    plan["changed_paths"].append("other.py")
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="consumer evidence is unavailable"):
        _validate_declared(plan, result)


_BEFORE_HASHMARKS = (
    '[project]\nname = "proof"\nversion = "1"\nrequires-python = ">=3.12"\n'
    'dependencies = ["hashmarks @ https://example.invalid/old.whl", "stable>=1"]\n'
)
_AFTER_HASHMARKS = _BEFORE_HASHMARKS.replace("old.whl", "new.whl")


_HASHMARKS_BASE_LOCK = """version = 1
revision = 3
requires-python = ">=3.12"
[[package]]
name = "proof"
version = "1"
source = { editable = "." }
dependencies = [{ name = "hashmarks" }, { name = "stable" }]
[[package]]
name = "hashmarks"
version = "1"
source = { url = "https://example.invalid/old.whl" }
[[package]]
name = "stable"
version = "1"
source = { registry = "https://pypi.org/simple" }
"""


def _hashmarks_upgrade_proof(
    root: Path, *, before: str = _BEFORE_HASHMARKS, after: str = _AFTER_HASHMARKS
) -> tuple[dict[str, Any], dict[str, Any]]:
    (root / "backend").mkdir()
    (root / "backend/pyproject.toml").write_text(before)
    (root / "backend/uv.lock").write_text(_HASHMARKS_BASE_LOCK)
    for argv in (
        ("init", "-q"),
        ("add", "backend/pyproject.toml", "backend/uv.lock"),
        (
            "-c",
            "user.name=Proof",
            "-c",
            "user.email=proof@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "base",
        ),
    ):
        assert command_owner.run_command("git", "-C", str(root), *argv).returncode == 0
    base = command_owner.run_command(
        "git", "-C", str(root), "rev-parse", "HEAD"
    ).stdout.strip()
    (root / "backend/pyproject.toml").write_text(after)
    (root / "backend/uv.lock").write_text(
        _HASHMARKS_BASE_LOCK.replace("old.whl", "new.whl")
    )
    plan, result = _proof(
        source="backend/pyproject.toml",
        test_file=HASHMARKS_UPGRADE_TESTS[0],
        additional_tests=(*HASHMARKS_UPGRADE_TESTS[1:], *VERIFICATION_POLICY_TESTS),
    )
    plan.update(
        base=base,
        changed_paths=["backend/pyproject.toml", "backend/uv.lock"],
        policy_status="REVIEW_REQUIRED",
        obligations=["verification-policy protection", HASHMARKS_UPGRADE_OBLIGATION],
        static=["python", "architecture", "package"],
    )
    plan["evidence"].update(
        hashmarks_upgrade={
            "authority": "oh-goon-execution-policy",
            "scope": "mandatory-hashmarks-upgrade",
            "consumer_coverage": "not-claimed",
        },
        observations=[
            {"changed_path": path, "state": "UNAVAILABLE", "nominations": []}
            for path in plan["changed_paths"]
        ],
    )
    plan["evidence"]["hashmarks_native_artifacts"] = (
        admission_owner._native_hashmarks_artifact_comparison(root, base)
    )
    result["outcomes"].append({"owner": "Native package", "returncode": 0})
    result["plan"] = copy.deepcopy(plan)
    return plan, result


def test_hashmarks_upgrade_requires_native_non_target_artifact_equivalence(
    tmp_path: Path,
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path)
    _validate_declared(plan, result, tmp_path)


def test_hashmarks_upgrade_keeps_pytest_configuration_role_without_an_extra_consumer(
    tmp_path: Path,
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path)
    row = plan["evidence"]["observations"][0]
    row.update(
        state="LINKED",
        qualification={
            "completeness": "incomplete",
            "negative_evidence_admissible": False,
        },
        changed_set={
            "changed_paths": ["backend/pyproject.toml"],
            "changes": [
                {
                    "path": "backend/pyproject.toml",
                    "classification": {
                        "kind": "verification-support",
                        "reason": "pytest-configuration",
                    },
                }
            ],
        },
    )
    original = copy.deepcopy(row)
    result["plan"] = copy.deepcopy(plan)

    _validate_declared(plan, result, tmp_path)

    assert row == original
    assert "verification_support_admission" not in plan["evidence"]
    assert gate.VERIFICATION_SUPPORT_OBLIGATION not in plan["obligations"]


def test_hashmarks_upgrade_does_not_exempt_unrelated_verification_support(
    tmp_path: Path,
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path)
    _validate_declared(plan, result, tmp_path)
    support = "backend/tests/factories/unrelated.py"
    plan["changed_paths"].append(support)
    plan["evidence"]["observations"].append(
        {
            "changed_path": support,
            "state": "LINKED",
            "qualification": {"completeness": "incomplete"},
            "changed_set": {
                "changed_paths": [support],
                "changes": [
                    {
                        "path": support,
                        "classification": {"kind": "verification-support"},
                    }
                ],
            },
            "nominations": [
                {
                    "nomination": {
                        "available": True,
                        "verification_target": {"path": HASHMARKS_UPGRADE_TESTS[0]},
                    }
                }
            ],
        }
    )
    result["plan"] = copy.deepcopy(plan)

    with pytest.raises(ValueError, match="executed qualified consumer"):
        _validate_declared(plan, result, tmp_path)


@pytest.mark.parametrize("changed", ("wheel", "new-transitive"))
def test_hashmarks_upgrade_cannot_claim_equivalence_when_uv_exports_differ(
    tmp_path: Path, changed: str
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path)
    lock_path = tmp_path / "backend/uv.lock"
    lock = lock_path.read_text()
    if changed == "wheel":
        lock += (
            'wheels = [{ url = "https://example.invalid/stable-1-py3-none-any.whl", '
            'hash = "sha256:' + "1" * 64 + '" }]\n'
        )
    else:
        lock = lock.replace(
            'source = { url = "https://example.invalid/new.whl" }\n',
            'source = { url = "https://example.invalid/new.whl" }\n'
            'dependencies = [{ name = "new-transitive" }]\n',
        )
        lock += (
            '[[package]]\nname = "new-transitive"\nversion = "1"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        )
    lock_path.write_text(lock)
    with pytest.raises(ValueError, match="non-Hashmarks artifact equivalence"):
        _validate_declared(plan, result, tmp_path)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            _BEFORE_HASHMARKS,
            _BEFORE_HASHMARKS.replace("hashmarks @", "hashmarks[syntax] @"),
        ),
        (
            _BEFORE_HASHMARKS,
            _AFTER_HASHMARKS.replace("hashmarks @", "hashmarks[syntax] @"),
        ),
        (
            _BEFORE_HASHMARKS.replace("hashmarks @", "hashmarks[syntax] @"),
            _AFTER_HASHMARKS.replace("hashmarks @", "hashmarks[syntax,mcp] @"),
        ),
    ],
)
def test_required_hashmarks_extra_intent_keeps_the_existing_bounded_proof(
    tmp_path: Path, before: str, after: str
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path, before=before, after=after)
    _validate_declared(plan, result, tmp_path)


@pytest.mark.parametrize(
    "fault", ["mixed-intent", "lookalike", "duplicate", "optional"]
)
def test_hashmarks_extra_cannot_hide_unrelated_or_optional_dependency_intent(
    tmp_path: Path, fault: str
) -> None:
    after = _AFTER_HASHMARKS.replace("hashmarks @", "hashmarks[syntax] @")
    plan, result = _hashmarks_upgrade_proof(tmp_path, after=after)
    if fault == "mixed-intent":
        after = after.replace("stable>=1", "stable>=2")
    elif fault == "lookalike":
        after = after.replace("hashmarks[syntax]", "hashmarks-lookalike[syntax]")
    elif fault == "duplicate":
        after = after.replace(
            '"stable>=1"',
            '"stable>=1", "hashmarks @ https://example.invalid/other.whl"',
        )
    else:
        after = (
            '[project]\nname = "proof"\nversion = "1"\ndependencies = ["stable>=1"]\n'
            "[project.optional-dependencies]\n"
            'syntax = ["hashmarks[syntax] @ https://example.invalid/new.whl"]\n'
        )
    (tmp_path / "backend/pyproject.toml").write_text(after)
    with pytest.raises(ValueError):
        _validate_declared(plan, result, tmp_path)


@pytest.mark.parametrize(
    "fault", ["mixed-intent", "wrong-smoke", "package-failure", "no-root"]
)
def test_hashmarks_upgrade_cannot_forge_intent_or_omit_native_proof(
    tmp_path: Path, fault: str
) -> None:
    plan, result = _hashmarks_upgrade_proof(tmp_path)
    _validate_declared(plan, result, tmp_path)
    if fault == "mixed-intent":
        (tmp_path / "backend/pyproject.toml").write_text(
            _AFTER_HASHMARKS.replace("stable>=1", "stable>=2")
        )
    elif fault == "wrong-smoke":
        selector = HASHMARKS_UPGRADE_TESTS[2]
        wrong = selector.split("::", 1)[0] + "::test_another_result"
        native = result["outcomes"][0]["native_report"]
        result["outcomes"][0]["native_report"] = json.loads(
            json.dumps(native).replace(selector, wrong)
        )
    elif fault == "package-failure":
        result["outcomes"][-1]["returncode"] = 1
    with pytest.raises(ValueError):
        _validate_declared(plan, result, None if fault == "no-root" else tmp_path)


def test_native_git_removals_include_rename_source_but_not_copy_source() -> None:
    changed, removed = gate.native_changed_paths(
        "D\0old.py\0R100\0before.py\0after.py\0"
        "C100\0source.py\0copy.py\0M\0modified.py\0"
    )
    assert changed == (
        "after.py",
        "before.py",
        "copy.py",
        "modified.py",
        "old.py",
        "source.py",
    )
    assert removed == ("before.py", "old.py")


@pytest.mark.parametrize("observation", ("R100\0old.py\0", "D\0"))
def test_incomplete_native_git_change_cannot_admit_a_removal(observation: str) -> None:
    with pytest.raises(ValueError, match="incomplete changed-path evidence"):
        gate.native_changed_paths(observation)


@pytest.mark.parametrize("removed", ("obsolete.py", "tests/test_obsolete.py"))
def test_confirmed_removal_does_not_require_executing_the_absent_member(
    removed: str,
) -> None:
    plan, result = _proof()
    plan["changed_paths"].append(removed)
    plan["removed_paths"] = [removed]
    plan["evidence"]["observations"].append(
        {"changed_path": removed, "state": "UNAVAILABLE", "reason": "source-absent"}
    )
    result["plan"] = copy.deepcopy(plan)

    def validate() -> None:
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            removed_paths=[removed],
            control_base=None,
        )

    validate()
    plan["evidence"]["observations"][0]["state"] = "UNAVAILABLE"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Source-qualified consumer evidence"):
        validate()


@pytest.mark.parametrize(
    "recorded", (None, [], ["source.py"], ["old.py", "old.py"], "old.py")
)
def test_removal_metadata_cannot_omit_duplicate_or_replace_native_membership(
    recorded: object,
) -> None:
    plan, result = _proof()
    plan["changed_paths"].append("old.py")
    if recorded is not None:
        plan["removed_paths"] = recorded
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Removed membership"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            removed_paths=["old.py"],
            control_base=None,
        )


def test_candidate_cannot_call_a_present_member_removed() -> None:
    plan, result = _proof()
    plan["removed_paths"] = ["source.py"]
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Removed membership"):
        _validate(plan, result)


def test_removing_a_required_policy_test_does_not_waive_its_protection() -> None:
    removed = VERIFICATION_POLICY_TESTS[0]
    plan, result = _proof(
        source="backend/src/oh_goon/repository_tooling/development_verification.py",
        additional_tests=VERIFICATION_POLICY_TESTS[1:],
    )
    plan.update(
        policy_status="REVIEW_REQUIRED", obligations=["verification-policy protection"]
    )
    plan["changed_paths"].append(removed)
    plan["removed_paths"] = [removed]
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="verification-policy protection was omitted"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            removed_paths=[removed],
            control_base=plan["base"],
        )


def test_test_named_production_owner_qualifies_through_executed_consumer() -> None:
    plan, result = _proof(
        source="backend/src/oh_goon/repository_tooling/test_batch_receipts.py",
        test_file="scripts/tests/test_test_batch_receipts.py",
        additional_tests=VERIFICATION_POLICY_TESTS,
    )
    plan.update(
        policy_status="REVIEW_REQUIRED",
        obligations=["verification-policy protection"],
    )
    result["plan"] = copy.deepcopy(plan)

    def validate() -> None:
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=plan["base"],
        )

    validate()
    nomination = plan["evidence"]["observations"][0]["nominations"][0]["nomination"]
    nomination["verification_target"]["path"] = "scripts/tests/test_unexecuted.py"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="no executed qualified consumer proof"):
        validate()


@pytest.mark.parametrize(
    ("changed_test", "state"),
    (
        ("backend/tests/unit/runtime/test_source.py", "LINKED"),
        ("backend/tests/integration/engine/test_source.py", "LINKED"),
        ("backend/tests/dependency_complete/test_source.py", "LINKED"),
        ("backend/tests/native/test_source.py", "LINKED"),
        ("scripts/tests/test_source.py", "LINKED"),
        ("tests/integration/test_source.py", "LINKED"),
        ("templates/goons/general/orders-taskflow/tests/test_source.py", "LINKED"),
        ("templates/goons/ml/iris-train/tests/test_source.py", "LINKED"),
        (
            "template-certification/fixtures/python/standard/tests/test_source.py",
            "LINKED",
        ),
        (
            "template-certification/fixtures/advanced/isolated-dataflow/tests/test_source.py",
            "LINKED",
        ),
        ("backend/tests/unit/runtime/source_test.py", "LINKED"),
        ("test/test_source.py", "LINKED"),
        ("verification/behavior_cases.py", "CHANGED_TEST"),
    ),
)
def test_changed_test_requires_own_execution_despite_linked_passes(
    changed_test: str, state: str
) -> None:
    plan, result = _proof(test_file=changed_test)
    plan["evidence"]["observations"][1]["state"] = state
    result["plan"] = copy.deepcopy(plan)

    def validate() -> None:
        executed_files, executed_members = validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )
        # This seam supplies independently observed native membership. Its
        # authority is separate from candidate labels or filename spelling.
        gate._require_ordinary_changed_test_execution(
            {
                "source_identity": plan["source_identity"],
                "changed_paths": sorted(plan["changed_paths"]),
                "contexts": [
                    {
                        "name": "backend",
                        "selected": [
                            {
                                "file": changed_test,
                                "selector": changed_test + "::test_result",
                            }
                        ],
                    },
                    {"name": "tooling", "selected": []},
                    {"name": "cross-owner", "selected": []},
                ],
            },
            changed_paths=plan["changed_paths"],
            source_identity=plan["source_identity"],
            executed_files=executed_files,
            executed_members=executed_members,
        )

    validate()
    other_plan, result = _proof(test_file="tests/test_other.py")
    plan["pytest"] = other_plan["pytest"]
    for observation in plan["evidence"]["observations"]:
        observation["nominations"][0]["nomination"]["verification_target"]["path"] = (
            "tests/test_other.py"
        )
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(
        ValueError, match="Changed tests have no meaningful execution proof"
    ):
        validate()


@pytest.mark.parametrize("fault", ("omitted", "extra"))
def test_native_pytest_files_match_admitted_proof(fault: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    if fault == "omitted":
        plan["pytest"].append("tests/test_other.py")
        result["plan"] = copy.deepcopy(plan)
    else:
        native = result["outcomes"][0]["native_report"]
        node = "tests/test_other.py::test_other"
        native["native_collection"]["discovered"].append(node)
        receipt = native["membership_receipt"]
        receipt["selected"].append(node)
        receipt["passed"].append(node)
        receipt["effective_pytest_outcomes"].append(
            {
                "member": node,
                "status": "PASSED",
                "attempt_identity": "extra-attempt",
                "pytest_outcomes": [{"nodeid": node, "outcome": "PASSED"}],
            }
        )
    with pytest.raises(ValueError, match="Admitted pytest files"):
        _validate(plan, result)


@pytest.mark.parametrize(
    "fault",
    (
        "empty",
        "skip",
        "failure",
        "missing",
        "foreign-source",
        "foreign-sha",
        "omitted-test",
        "missing-owner",
        "cancelled",
    ),
)
def test_trusted_control_rejects_false_green(fault: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    native = result["outcomes"][0]["native_report"]
    if fault == "empty":
        native["membership_receipt"].update(selected=[], passed=[])
    elif fault == "skip":
        native["membership_receipt"]["effective_pytest_outcomes"][0]["pytest_outcomes"][
            0
        ]["outcome"] = "SKIPPED"
    elif fault == "failure":
        result["outcomes"][0]["returncode"] = 1
    elif fault == "missing":
        native["membership_receipt"]["missing"] = ["tests/test_source.py::test_result"]
    elif fault == "foreign-source":
        native["source_identity"] = "sha256:" + "c" * 64
    elif fault == "foreign-sha":
        plan["candidate"] = "c" * 40
        result["plan"] = copy.deepcopy(plan)
    elif fault == "omitted-test":
        native["membership_receipt"].update(
            selected=["tests/other.py::test_other"],
            passed=["tests/other.py::test_other"],
        )
    elif fault == "missing-owner":
        result["outcomes"] = []
    else:
        result["status"] = "CANCELLED"
    with pytest.raises(ValueError):
        _validate(plan, result)


def test_policy_change_cannot_approve_its_own_candidate_control() -> None:
    plan, result = _proof()
    plan["policy_status"] = "REVIEW_REQUIRED"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="CUTOVER_UNVERIFIED"):
        _validate(plan, result)
    with pytest.raises(ValueError, match="trusted-base"):
        _validate(plan, result, control=plan["candidate"])
    _validate(plan, result, control=plan["base"])


def test_candidate_cannot_hide_policy_change_or_new_test_in_its_flags() -> None:
    plan, result = _proof()
    plan["changed_paths"] = [".github/workflows/ci.yml", "tests/test_source.py"]
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="trusted-base"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )
    plan, result = _proof()
    plan["evidence"]["observations"] = []
    result["plan"] = copy.deepcopy(plan)
    result["outcomes"][0]["native_report"]["membership_receipt"].update(
        selected=["tests/other.py::test_other"], passed=["tests/other.py::test_other"]
    )
    result["outcomes"][0]["native_report"]["native_collection"]["discovered"] = [
        "tests/other.py::test_other"
    ]
    effective = result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]
    effective["member"] = "tests/other.py::test_other"
    effective["pytest_outcomes"][0]["nodeid"] = "tests/other.py::test_other"
    with pytest.raises(ValueError, match="Admitted pytest files"):
        _validate(plan, result)


def test_executable_change_cannot_claim_behavioral_na() -> None:
    plan, result = _proof()
    plan["behavioral_status"] = "NOT_APPLICABLE"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Executable inputs"):
        _validate(plan, result)


@pytest.mark.parametrize(
    "path",
    (
        "README.md",
        "AGENTS.md",
        "docs/CI_CD_CONTRACT.md",
        "docs/development/TEST_QUALITY.md",
        "docs/development/CHATGPT_ITERATION.md",
        "scripts/development_check.sh",
        "backend/src/oh_goon/repository_tooling/verification_gate_control.py",
        "backend/src/oh_goon/repository_tooling/test_batches.py",
        "backend/src/oh_goon/repository_tooling/test_batch_receipts.py",
        "backend/src/oh_goon/repository_tooling/pytest_batch_receipts.py",
        ".github/workflows/ci.yml",
        "backend/README.md",
        "backend/src/oh_goon/README.md",
        "scripts/README.md",
        "frontend/README.md",
        "frontend/src/README.md",
        "docs/development/REAL_LIFECYCLE_QA.md",
        "docs/development/REAL_LIFECYCLE_QA.yml",
        "docs/certification/answers/11-startup-certification.yml",
    ),
)
def test_policy_ingresses_cannot_self_approve_as_ordinary(path: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    assert is_verification_policy_path(path)
    plan["changed_paths"] = [path, "tests/test_source.py"]
    plan["static"] = sorted(required_static_obligations(plan["changed_paths"]))
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="trusted-base"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )


@pytest.mark.parametrize(
    "path",
    (
        "README.md",
        "backend/README.md",
        "backend/src/oh_goon/README.md",
        "scripts/README.md",
        "frontend/README.md",
        "frontend/src/README.md",
        "docs/development/REAL_LIFECYCLE_QA.md",
        "docs/development/REAL_LIFECYCLE_QA.yml",
        "docs/certification/answers/01-repository-ownership.yml",
        "docs/certification/answers/11-startup-certification.yml",
    ),
)
def test_governing_policy_admits_independently_bound_proof(path: str) -> None:
    plan, result = _proof()
    required = (
        VERIFICATION_POLICY_TESTS
        + ((REAL_LIFECYCLE_QA_TEST,) if path in REAL_LIFECYCLE_QA_PATHS else ())
        + (
            (gate.GRADER_ANSWER_MATERIAL_TEST,)
            if gate.is_grader_answer_section(path)
            else ()
        )
    )
    obligations = ["verification-policy protection"]
    if path in REAL_LIFECYCLE_QA_PATHS:
        obligations.append("real-lifecycle QA protection")
    if gate.is_grader_answer_section(path):
        obligations.append("grader answer-material protection")
    plan.update(
        changed_paths=[path],
        policy_status="REVIEW_REQUIRED",
        obligations=obligations,
        pytest=list(required),
        static=sorted(required_static_obligations([path])),
    )
    plan["evidence"]["observations"] = (
        []
        if gate.is_grader_answer_section(path)
        else [{"changed_path": path, "state": "UNAVAILABLE", "nominations": []}]
    )
    native = result["outcomes"][0]
    nodes = [f"{test_file}::test_policy" for test_file in required]
    native["native_report"]["native_collection"] = {
        "discovered": list(nodes),
        "deselected": [],
    }
    native["native_report"]["membership_receipt"] = {
        "selected": list(nodes),
        "passed": list(nodes),
        "terminal_success": True,
        "effective_pytest_outcomes": [
            {
                "member": node,
                "status": "PASSED",
                "attempt_identity": "policy-attempt",
                "pytest_outcomes": [{"nodeid": node, "outcome": "PASSED"}],
            }
            for node in nodes
        ],
    }
    owners = ["Architecture"]
    if path.startswith("backend/"):
        owners.extend(("Ruff", "Format", "Types"))
    if path.startswith("frontend/"):
        owners.extend(
            (
                "Frontend typecheck",
                "Frontend lint",
                "Frontend architecture",
                "Frontend i18n:check",
            )
        )
    result["outcomes"] = [native] + [
        {"owner": owner, "returncode": 0} for owner in owners
    ]
    result["plan"] = copy.deepcopy(plan)

    def validate(
        current_plan: dict[str, Any],
        current_result: dict[str, Any],
        control: str | None,
    ) -> None:
        validate_merge_proof(
            current_plan,
            current_result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=[path],
            control_base=control,
        )

    validate(plan, result, plan["base"])
    with pytest.raises(ValueError, match="trusted-base"):
        validate(plan, result, None)

    if gate.is_grader_answer_section(path):
        incomplete_result = copy.deepcopy(result)
        incomplete_result["outcomes"][0]["returncode"] = 1
        with pytest.raises(
            ValueError, match="Required owner is missing, skipped or failed"
        ):
            validate(plan, incomplete_result, plan["base"])

    for test_file in required:
        incomplete_plan, incomplete_result = copy.deepcopy((plan, result))
        incomplete_plan["pytest"].remove(test_file)
        incomplete_result["plan"] = copy.deepcopy(incomplete_plan)
        with pytest.raises(ValueError, match="policy protection was omitted"):
            validate(incomplete_plan, incomplete_result, plan["base"])

        incomplete_result = copy.deepcopy(result)
        report = incomplete_result["outcomes"][0]["native_report"]
        missing_node = f"{test_file}::test_policy"
        report["native_collection"]["discovered"].remove(missing_node)
        receipt = report["membership_receipt"]
        receipt["selected"].remove(missing_node)
        receipt["passed"].remove(missing_node)
        receipt["effective_pytest_outcomes"] = [
            row
            for row in receipt["effective_pytest_outcomes"]
            if row["member"] != missing_node
        ]
        with pytest.raises(ValueError, match="policy tests have no execution proof"):
            validate(plan, incomplete_result, plan["base"])

    if path in REAL_LIFECYCLE_QA_PATHS:
        incomplete_plan, incomplete_result = copy.deepcopy((plan, result))
        incomplete_plan["policy_status"] = "ORDINARY"
        incomplete_plan["obligations"].remove("real-lifecycle QA protection")
        incomplete_result["plan"] = copy.deepcopy(incomplete_plan)
        with pytest.raises(ValueError, match="policy protection was omitted"):
            validate(incomplete_plan, incomplete_result, plan["base"])

        incomplete_result = copy.deepcopy(result)
        receipt = incomplete_result["outcomes"][0]["native_report"][
            "membership_receipt"
        ]
        qa_outcome = next(
            row
            for row in receipt["effective_pytest_outcomes"]
            if row["member"] == f"{REAL_LIFECYCLE_QA_TEST}::test_policy"
        )
        qa_outcome["pytest_outcomes"][0]["outcome"] = "SKIPPED"
        with pytest.raises(ValueError, match="native category is skipped"):
            validate(plan, incomplete_result, plan["base"])

    extra_result = copy.deepcopy(result)
    report = extra_result["outcomes"][0]["native_report"]
    extra_file = (
        "scripts/tests/test_other_qa_contract.py"
        if path in REAL_LIFECYCLE_QA_PATHS
        else REAL_LIFECYCLE_QA_TEST
    )
    extra_node = f"{extra_file}::test_extra"
    report["native_collection"]["discovered"].append(extra_node)
    report["membership_receipt"]["selected"].append(extra_node)
    report["membership_receipt"]["passed"].append(extra_node)
    report["membership_receipt"]["effective_pytest_outcomes"].append(
        {
            "member": extra_node,
            "status": "PASSED",
            "attempt_identity": "extra-proof",
            "pytest_outcomes": [{"nodeid": extra_node, "outcome": "PASSED"}],
        }
    )
    with pytest.raises(ValueError, match="Admitted pytest files"):
        validate(plan, extra_result, plan["base"])


@pytest.mark.parametrize("value", ("node", {}, [True]))
def test_malformed_native_membership_cannot_qualify(value: Any) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"]["selected"] = value
    with pytest.raises(ValueError, match="shape"):
        _validate(plan, result)


@pytest.mark.parametrize(
    "category", ("FAILED", "SKIPPED", "XFAILED", "XPASSED", "UNREPORTED")
)
def test_effective_native_category_cannot_hide_behind_member_passed(
    category: str,
) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"][0]["outcome"] = category
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_generated_only_contract_repair_requires_native_freshness_and_consumers() -> (
    None
):
    plan, _result = _proof()
    plan.update(
        changed_paths=[
            "generated/openapi/openapi.json",
            "generated/frontend/models.ts",
        ],
        static=["architecture", "generated", "frontend"],
        pytest=[],
        frontend=[],
        evidence={},
    )
    result: dict[str, Any] = {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [
            {"owner": name, "returncode": 0}
            for name in (
                "Architecture",
                "Generated contracts",
                "Frontend typecheck",
                "Frontend lint",
                "Frontend architecture",
                "Frontend i18n:check",
            )
        ],
    }
    validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        control_base=None,
    )
    result["outcomes"][1]["returncode"] = 1
    with pytest.raises(ValueError, match="failed"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )


def test_historical_timeout_does_not_replace_the_original_effective_pass() -> None:
    plan, result = _proof()
    receipt = result["outcomes"][0]["native_report"]["membership_receipt"]
    receipt["attempt_records"] = [
        {
            "status": "STARTED",
            "pytest_outcomes": [
                {"nodeid": "tests/test_source.py::test_result", "outcome": "UNREPORTED"}
            ],
        }
    ]
    receipt["failed"] = ["tests/test_source.py::test_result"]
    receipt["non_passed"] = ["tests/test_source.py::test_result"]
    _validate(plan, result)


@pytest.mark.parametrize("nodeid", (None, "", 3))
def test_effective_native_case_requires_original_nonempty_identity(nodeid: Any) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"][0]["nodeid"] = nodeid
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_effective_native_case_cannot_duplicate_an_occurrence() -> None:
    plan, result = _proof()
    _validate(plan, result)
    outcomes = result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"]
    outcomes.append(copy.deepcopy(outcomes[0]))
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_legitimate_daily_deferral_is_not_failed_selected_membership() -> None:
    plan, result = _proof()
    collection = result["outcomes"][0]["native_report"]["native_collection"]
    collection["discovered"].append("tests/daily.py::test_full_corpus")
    collection["deselected"].append("tests/daily.py::test_full_corpus")
    _validate(plan, result)


def _candidate_gate_root(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        '[project]\nname = "oh-goon"\nversion = "0.0.0"\n'
        '[build-system]\nrequires = []\nbuild-backend = "hatch_build"\n'
        'backend-path = ["."]\n',
        encoding="utf-8",
    )
    (backend / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "0.0.0"\n'
        'source = { editable = "." }\n[[package]]\nname = "hashmarks"\n'
        'version = "0.32.1"\nsource = { url = '
        '"https://github.com/Hugloss/Hashmarks/releases/download/v0.32.1/'
        'hashmarks-0.32.1-py3-none-any.whl" }\n',
        encoding="utf-8",
    )
    return root


def _candidate_distribution(environment: Path, source: str) -> tuple[Path, Path]:
    site = Path(
        sysconfig.get_path(
            "purelib", vars={"base": str(environment), "platbase": str(environment)}
        )
    )
    package = site / "hashmarks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "implementation.py").write_text("VALUE = 1\n", encoding="utf-8")
    metadata = site / "hashmarks-0.32.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: hashmarks\nVersion: 0.32.1\n", encoding="utf-8"
    )
    (metadata / "RECORD").write_text(
        "hashmarks/__init__.py,,\nhashmarks/implementation.py,,\n"
        "hashmarks-0.32.1.dist-info/METADATA,,\n"
        "hashmarks-0.32.1.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    return package, metadata


def _stub_candidate_materialization(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str = '__version__ = "0.32.1"\n',
    alter: Callable[[Path, Path], None] | None = None,
    result: command_owner.CommandResult | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []

    def run(*argv: str, **options: Any) -> command_owner.CommandResult:
        if argv[:3] == ("uv", "workspace", "dir"):
            return command_owner.CommandResult(
                0, argv[argv.index("--project") + 1] + "\n", ""
            )
        assert argv[:2] == ("uv", "sync")
        environment = Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
        package, metadata = _candidate_distribution(environment, source)
        observations.append(
            {
                "argv": argv,
                "options": options,
                "environment": environment,
                "package": package,
                "identity": runtime_contract.native_producer_implementation_identity(
                    package
                ),
            }
        )
        if alter is not None:
            alter(package, metadata)
        return result or command_owner.CommandResult(0, "", "")

    monkeypatch.setattr(command_owner, "run_command", run)
    return observations


@pytest.fixture
def dependency_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """One real dependency replacement and its existing source/verifier chain."""
    root = tmp_path / "dependency-candidate"
    files = {
        "backend/pyproject.toml": (
            '[project]\nname="fixture-app"\nversion="1"\n'
            '[tool.setuptools.package-dir]\n""="src"\n'
            '[tool.pytest.ini_options]\ntestpaths=["tests"]\n'
        ),
        "backend/src/app/__init__.py": "",
        "backend/src/app/feature.py": (
            "from different_import.api import answer\ndef selected():\n    return answer()\n"
        ),
        "backend/tests/test_feature.py": (
            "from app.feature import selected\ndef test_selected():\n    assert selected() == 42\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    lock = (
        'version=1\nrevision=1\n[[package]]\nname="fixture-app"\nversion="1"\n'
        'source={editable="."}\ndependencies=[{name="sample-distribution"}]\n'
        '[[package]]\nname="sample-distribution"\nversion="1.0"\n'
        'source={registry="https://pypi.org/simple"}\n'
    )
    (root / "backend/uv.lock").write_text(lock, encoding="utf-8")
    for arguments in (
        ("init", "-q"),
        ("add", "backend"),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ),
    ):
        observed = command_owner.run_command("git", "-C", str(root), *arguments)
        assert observed.returncode == 0, observed.stderr
    base = command_owner.run_command(
        "git", "-C", str(root), "rev-parse", "HEAD"
    ).stdout.strip()
    (root / "backend/uv.lock").write_text(
        lock.replace('version="1.0"', 'version="2.0"'), encoding="utf-8"
    )
    info = tmp_path / "installed" / "sample_distribution-2.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Name: sample-distribution\nVersion: 2.0\n", encoding="utf-8"
    )
    (info / "RECORD").write_text(
        f"different_import/__init__.py,,\ndifferent_import/api.py,,\n{info.name}/METADATA,,\n{info.name}/RECORD,,\n",
        encoding="utf-8",
    )
    original = metadata.distributions

    def discover(*, name: str) -> Any:
        if name == "sample-distribution":
            yield metadata.Distribution.at(info)
        else:
            yield from original(name=name)

    monkeypatch.setattr(metadata, "distributions", discover)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return {
        "root": root,
        "base": base,
        "selected_files": ["backend/tests/test_feature.py"],
        "source_identity": package_slim.canonical_source_identity(root),
        "producer_identity": runtime_contract.require_loaded_hashmarks_runtime(),
        "metadata": info,
    }


def _observe_fresh_dependency(candidate: dict[str, Any]) -> dict[str, Any]:
    return gate._observe_dependency_candidate(
        candidate["root"],
        base=candidate["base"],
        selected_files=candidate["selected_files"],
        source_identity=candidate["source_identity"],
        producer_identity=candidate["producer_identity"],
    )


def test_independent_dependency_observation_requires_a_real_executed_consumer(
    dependency_candidate: dict[str, Any],
) -> None:
    receipt = _observe_fresh_dependency(dependency_candidate)
    assert receipt["source_identity"] == dependency_candidate["source_identity"]
    assert receipt["base"] == dependency_candidate["base"]
    assert (
        receipt["producer_implementation_identity"]
        == dependency_candidate["producer_identity"]
    )
    assert (
        receipt["before_observation_identity"] != receipt["after_observation_identity"]
    )
    assert receipt["executed_verifier_files"] == dependency_candidate["selected_files"]
    assert len(receipt["changed_node_ids"]) == len(receipt["metadata"]) == 1
    assert receipt["metadata"][0]["metadata_member_revision"]
    assert "distribution_root" not in receipt["metadata"][0]


@pytest.mark.parametrize(
    "fault",
    [
        "unexecuted",
        "source-before",
        "producer",
        "source-during",
        "metadata-during",
        "missing-chain",
        "wrong-import",
        "wrong-native-identity",
    ],
)
def test_independent_dependency_observer_rejects_missing_or_drifted_authority(
    dependency_candidate: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    candidate = dependency_candidate
    _observe_fresh_dependency(candidate)
    original = asset_owner.nominate_changed_verification
    original_source_file = getsourcefile

    def observe_then_change(*args: Any, **kwargs: Any) -> dict[str, Any]:
        evidence = original(*args, **kwargs)
        if fault == "source-during":
            (candidate["root"] / "backend/src/app/feature.py").write_text(
                "CHANGED = True\n", encoding="utf-8"
            )
        elif fault == "metadata-during":
            (candidate["metadata"] / "RECORD").write_text(
                "changed_namespace/__init__.py,,\n", encoding="utf-8"
            )
        elif fault == "missing-chain":
            evidence["dependency_changes"]["chains"] = []
        elif fault == "wrong-import":
            evidence["dependency_changes"]["chains"][0]["consumer"][
                "distribution_nodes"
            ] = ["unobserved-node"]
        elif fault == "wrong-native-identity":
            evidence["producer_implementation_identity"] = "sha256:" + "0" * 64
        return evidence

    def source_file(capability: Any) -> str | None:
        # Inject observation faults inside the simulated trusted callable.
        return original_source_file(
            original if capability is observe_then_change else capability
        )

    monkeypatch.setattr(gate, "getsourcefile", source_file)
    monkeypatch.setattr(
        asset_owner, "nominate_changed_verification", observe_then_change
    )
    if fault == "unexecuted":
        candidate["selected_files"] = []
    elif fault == "source-before":
        candidate["source_identity"] = "sha256:" + "0" * 64
    elif fault == "producer":
        candidate["producer_identity"] = "sha256:" + "0" * 64
    with pytest.raises((ValueError, RuntimeError)):
        _observe_fresh_dependency(candidate)


@pytest.mark.parametrize(
    "capability",
    (
        "nominate_changed_verification",
        "require_current_dependency_environment",
        "require_loaded_hashmarks_runtime",
    ),
)
def test_dependency_observation_binds_the_actual_public_callable(
    dependency_candidate: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
) -> None:
    _observe_fresh_dependency(dependency_candidate)

    def foreign_owner(*_args: Any, **_options: Any) -> Any:
        pytest.fail("foreign dependency implementation must not execute")

    monkeypatch.setattr(public_evidence, capability, foreign_owner)
    with pytest.raises(ValueError, match="outside its trusted source"):
        _observe_fresh_dependency(dependency_candidate)


def _dependency_probe_plan() -> dict[str, Any]:
    return {
        "base": "b" * 40,
        "source_identity": "sha256:" + "c" * 64,
        "pytest": ["backend/tests/test_feature.py"],
    }


def _dependency_probe_receipt(plan: dict[str, Any], producer: str) -> dict[str, Any]:
    return {
        "base": plan["base"],
        "source_identity": plan["source_identity"],
        "producer_implementation_identity": producer,
        "changed_node_ids": ["current-node"],
        "metadata": [
            {
                "node_id": "current-node",
                "metadata_member_revision": "metadata",
                "record_member_revision": "record",
            }
        ],
        "before_observation_identity": "sha256:" + "1" * 64,
        "after_observation_identity": "sha256:" + "2" * 64,
        "executed_verifier_files": plan["pytest"],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "invalid",
        "multiple",
        "non-object",
        "missing-metadata",
        "foreign-source",
        "foreign-producer",
        "identical-observations",
        "wrong-metadata-node",
        "unexecuted",
        "nonquiescent",
        "stdout-truncated",
        "stderr-limit",
        "nonzero",
        "oversized-request",
    ],
)
def test_independent_dependency_transport_keeps_reply_and_process_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, trusted, environment = (
        tmp_path / name for name in ("candidate", "trusted", "environment")
    )
    for directory in (root, trusted, environment):
        directory.mkdir()
    plan = _dependency_probe_plan()
    producer = "sha256:" + "d" * 64
    receipt = _dependency_probe_receipt(plan, producer)
    if fault == "missing-metadata":
        receipt["metadata"] = []
    elif fault == "foreign-source":
        receipt["source_identity"] = "sha256:" + "0" * 64
    elif fault == "foreign-producer":
        receipt["producer_implementation_identity"] = "sha256:" + "0" * 64
    elif fault == "identical-observations":
        receipt["before_observation_identity"] = receipt["after_observation_identity"]
    elif fault == "wrong-metadata-node":
        receipt["metadata"][0]["node_id"] = "unobserved-node"
    elif fault == "unexecuted":
        receipt["executed_verifier_files"] = ["backend/tests/unexecuted.py"]
    elif fault == "oversized-request":
        plan["pytest"] = ["x" * (1024 * 1024)]
    reply = json.dumps(receipt)
    if fault == "invalid":
        reply = "{"
    elif fault == "multiple":
        reply += "\n" + reply
    elif fault == "non-object":
        reply = "[]"
    calls = []

    def observe(*argv: str, **options: Any) -> command_owner.CommandResult:
        calls.append(argv)
        if fault == "stderr-limit":
            raise command_owner.CommandOutputLimitExceeded(
                stream="stderr",
                limit_bytes=256 * 1024,
                stdout="",
                stderr="bounded",
                supervisor=command_owner.CommandSupervisorReceipt(),
            )
        return command_owner.CommandResult(
            17 if fault == "nonzero" else 0,
            reply,
            "",
            stdout_truncated=fault == "stdout-truncated",
            supervisor=command_owner.CommandSupervisorReceipt(
                final_process_group_quiescent=fault != "nonquiescent"
            ),
        )

    monkeypatch.setattr(command_owner, "run_command", observe)
    if fault == "none":
        assert (
            gate._trusted_dependency_probe(
                root,
                trusted_root=trusted,
                environment=environment,
                env={},
                plan=plan,
                producer_identity=producer,
            )
            == receipt
        )
    else:
        with pytest.raises((ValueError, command_owner.CommandOutputLimitExceeded)):
            gate._trusted_dependency_probe(
                root,
                trusted_root=trusted,
                environment=environment,
                env={},
                plan=plan,
                producer_identity=producer,
            )
    assert len(calls) == int(fault != "oversized-request")


@pytest.mark.process
def test_independent_dependency_child_imports_only_its_explicit_base_source(
    tmp_path: Path, dependency_candidate: dict[str, Any]
) -> None:
    root = dependency_candidate["root"]
    (root / "backend/src/app/feature.py").write_text(
        "from hashmarks import CodeMap\ndef selected(root):\n    with CodeMap(root) as native:\n        return native.workspace\n",
        encoding="utf-8",
    )
    (root / "backend/tests/test_feature.py").write_text(
        "from app.feature import selected\ndef test_selected(tmp_path):\n    assert selected(tmp_path) == tmp_path\n",
        encoding="utf-8",
    )
    lock = (
        'version=1\nrevision=1\n[[package]]\nname="fixture-app"\nversion="1"\n'
        'source={editable="."}\ndependencies=[{name="hashmarks"}]\n'
        '[[package]]\nname="hashmarks"\nversion="0.0.0"\n'
        'source={registry="https://pypi.org/simple"}\n'
    )
    (root / "backend/uv.lock").write_text(lock, encoding="utf-8")
    for arguments in (
        ("add", "backend"),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "producer baseline",
        ),
    ):
        assert (
            command_owner.run_command("git", "-C", str(root), *arguments).returncode
            == 0
        )
    base = command_owner.run_command(
        "git", "-C", str(root), "rev-parse", "HEAD"
    ).stdout.strip()
    (root / "backend/uv.lock").write_text(
        lock.replace(
            'name="hashmarks"\nversion="0.0.0"',
            f'name="hashmarks"\nversion="{metadata.version("hashmarks")}"',
        ),
        encoding="utf-8",
    )
    malicious = root / "backend/src"
    marker = tmp_path / "candidate-executed"
    bomb = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('candidate import')\n"
    for relative in ("oh_goon/__init__.py", "sitecustomize.py", "usercustomize.py"):
        path = malicious / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bomb, encoding="utf-8")
    trusted = Path(gate.__file__).resolve().parents[4]
    environment = Path(sys.prefix)
    producer = runtime_contract.require_loaded_hashmarks_runtime()
    plan = {
        "base": base,
        "pytest": dependency_candidate["selected_files"],
        "source_identity": package_slim.canonical_source_identity(root),
    }
    observed = gate._trusted_dependency_probe(
        root,
        trusted_root=trusted,
        environment=environment,
        env={**os.environ, "PYTHONPATH": str(malicious), "PYTHONHOME": str(malicious)},
        plan=plan,
        producer_identity=producer,
    )
    assert observed["source_identity"] == plan["source_identity"]
    assert observed["producer_implementation_identity"] == producer
    assert observed["executed_verifier_files"] == dependency_candidate["selected_files"]
    assert (
        observed["metadata"]
        and observed["before_observation_identity"]
        != observed["after_observation_identity"]
    )
    assert not marker.exists()


@pytest.mark.parametrize(
    "failure", ["error", "cancel", "interrupt", "producer-mismatch"]
)
def test_dependency_probe_failure_destroys_its_fresh_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    plan: dict[str, Any] = {"evidence": {}}

    def bind(package: Path, _metadata: Path) -> None:
        plan["evidence"]["producer_implementation_identity"] = (
            "sha256:" + "0" * 64
            if failure == "producer-mismatch"
            else runtime_contract.native_producer_implementation_identity(package)
        )

    materialized = _stub_candidate_materialization(monkeypatch, alter=bind)
    probe_calls = []

    def refuse(*args: Any, **options: Any) -> dict[str, Any]:
        probe_calls.append(options["environment"])
        assert options["environment"].is_dir()
        if failure == "cancel":
            raise command_owner.CommandCancelledError(
                "cancelled", supervisor=command_owner.CommandSupervisorReceipt()
            )
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise ValueError("independent dependency observation refused")

    monkeypatch.setattr(gate, "_trusted_dependency_probe", refuse)
    receipt: dict[str, Any] = {}
    with pytest.raises(
        (ValueError, command_owner.CommandCancelledError, KeyboardInterrupt)
    ):
        gate._candidate_producer_identity(
            root,
            dependency_plan=plan,
            trusted_root=tmp_path,
            dependency_receipt=receipt,
        )
    assert len(materialized) == 1
    assert not materialized[0]["environment"].exists()
    assert len(probe_calls) == int(failure != "producer-mismatch")
    assert receipt == {}


def test_candidate_producer_is_native_locked_data_without_candidate_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    marker = tmp_path / "candidate-executed"
    bomb = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('candidate code must remain data')\n"
    )
    (root / "backend/hatch_build.py").write_text(bomb, encoding="utf-8")
    candidate_package = root / "backend/src/oh_goon"
    candidate_package.mkdir(parents=True)
    (candidate_package / "__init__.py").write_text(bomb, encoding="utf-8")
    for key, value in {
        "PYTHONPATH": str(root / "backend/src"),
        "PYTHONHOME": str(root),
        "VIRTUAL_ENV": str(root / "backend/.venv"),
        "UV_PROJECT_ENVIRONMENT": str(root / "backend/.venv"),
        "UV_PROJECT": str(root / "wrong-project"),
        "UV_PYTHON": str(root / "untrusted-python"),
        "UV_NO_BUILD": "false",
        "UV_NO_SYNC": "true",
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }.items():
        monkeypatch.setenv(key, value)
    observations = _stub_candidate_materialization(monkeypatch, source=bomb)

    identity = gate._candidate_producer_identity(root)

    assert len(observations) == 1
    observation = observations[0]
    assert identity == observation["identity"]
    assert identity != runtime_contract.require_loaded_hashmarks_runtime()
    argv = observation["argv"]
    assert argv[:2] == ("uv", "sync")
    assert {
        "--frozen",
        "--no-default-groups",
        "--no-install-project",
        "--no-install-workspace",
        "--no-build",
        "--no-config",
        "--no-python-downloads",
    } <= set(argv)
    assert argv[argv.index("--project") + 1] == str(root / "backend")
    assert argv[argv.index("--python") + 1] == sys.executable
    assert argv[argv.index("--link-mode") + 1] == "copy"
    environment = observation["environment"]
    assert not environment.is_relative_to(root)
    assert not environment.exists()
    child_environment = observation["options"]["env"]
    assert child_environment["UV_PROJECT_ENVIRONMENT"] == str(environment)
    assert child_environment["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "UV_PROJECT",
        "UV_PYTHON",
        "UV_NO_BUILD",
        "UV_NO_SYNC",
    ):
        assert key not in child_environment
    assert not marker.exists()
    assert not (root / "backend/.venv").exists()


@pytest.mark.parametrize(
    "fault", ("none", "candidate-source", "trusted-source", "producer", "nonquiescent")
)
def test_native_collection_preparation_follows_independent_dependency_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / "README.md").write_text("Immutable control source.\n")
    source_identity = package_slim.canonical_source_identity(root)
    observations = _stub_candidate_materialization(monkeypatch)
    materialize = command_owner.run_command
    events: list[str] = []
    plan: dict[str, Any] = {"evidence": {}}

    def prepare(*argv: str, **options: Any) -> command_owner.CommandResult:
        if "--no-build" in argv:
            result = materialize(*argv, **options)
            plan["evidence"]["producer_implementation_identity"] = observations[0][
                "identity"
            ]
            events.append("data-only")
            return result
        if argv[:3] == ("uv", "workspace", "dir"):
            return materialize(*argv, **options)
        assert argv[:2] == ("uv", "sync")
        assert events == ["data-only", "dependency-observation"]
        assert "--no-build" not in argv and "--no-install-project" not in argv
        assert [
            argv[index + 1] for index, arg in enumerate(argv) if arg == "--group"
        ] == ["dev", "quality", "build"]
        assert (
            Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
            == observations[0]["environment"]
        )
        events.append("collection-preparation")
        if fault == "candidate-source":
            (root / "README.md").write_text("Changed during native setup.\n")
        elif fault == "trusted-source":
            (trusted / "README.md").write_text("Changed control source.\n")
        elif fault == "producer":
            (observations[0]["package"] / "implementation.py").write_text("VALUE = 2\n")
        return command_owner.CommandResult(
            0,
            "",
            "",
            supervisor=command_owner.CommandSupervisorReceipt(
                final_process_group_quiescent=fault != "nonquiescent"
            ),
        )

    def observe_dependency(*_args: Any, **options: Any) -> dict[str, Any]:
        assert events == ["data-only"]
        assert options["environment"] == observations[0]["environment"]
        events.append("dependency-observation")
        return {"source_identity": source_identity}

    def collect(*_args: Any, **options: Any) -> dict[str, Any]:
        assert events == [
            "data-only",
            "dependency-observation",
            "collection-preparation",
        ]
        assert options["environment"] == observations[0]["environment"]
        assert options["trusted_root"] == trusted
        assert options["source_identity"] == source_identity
        events.append("native-collection")
        return {"source_identity": source_identity}

    monkeypatch.setattr(command_owner, "run_command", prepare)
    monkeypatch.setattr(gate, "_trusted_dependency_probe", observe_dependency)
    monkeypatch.setattr(gate, "_trusted_ordinary_probe", collect)
    dependency_receipt: dict[str, Any] = {}
    ordinary_receipt: dict[str, Any] = {}

    def inspect() -> str:
        return gate._candidate_producer_identity(
            root,
            dependency_plan=plan,
            trusted_root=trusted,
            dependency_receipt=dependency_receipt,
            ordinary_receipt=ordinary_receipt,
            ordinary_paths=("source.py",),
            source_identity=source_identity,
        )

    if fault == "none":
        assert inspect() == observations[0]["identity"]
        assert events[-1] == "native-collection"
        assert ordinary_receipt[
            "trusted_source_identity"
        ] == package_slim.canonical_source_identity(trusted)
    else:
        with pytest.raises(ValueError):
            inspect()
        assert "native-collection" not in events
        assert ordinary_receipt == {}
    assert dependency_receipt == {"source_identity": source_identity}
    assert not observations[0]["environment"].exists()


@pytest.mark.parametrize("missing", ("trusted-root", "source-identity"))
def test_native_collection_missing_authority_is_rejected_before_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    observations = _stub_candidate_materialization(monkeypatch)
    with pytest.raises(ValueError, match="requires trusted source authority"):
        gate._candidate_producer_identity(
            root,
            trusted_root=None if missing == "trusted-root" else tmp_path,
            source_identity=None
            if missing == "source-identity"
            else "sha256:" + "a" * 64,
            ordinary_receipt={},
        )
    assert observations == []


@pytest.mark.parametrize("expected", ("matching", "mismatched", "missing", "invalid"))
def test_ordinary_only_producer_authority_precedes_project_setup_and_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / "README.md").write_text("Immutable control source.\n")
    source_identity = package_slim.canonical_source_identity(root)
    observations = _stub_candidate_materialization(monkeypatch)
    actual = gate._candidate_producer_identity(root)
    assert len(observations) == 1
    materialize = command_owner.run_command
    events: list[str] = []

    def prepare(*argv: str, **options: Any) -> command_owner.CommandResult:
        if argv[:3] == ("uv", "workspace", "dir"):
            return materialize(*argv, **options)
        if "--no-build" in argv:
            events.append("data-only")
            return materialize(*argv, **options)
        assert argv[:2] == ("uv", "sync")
        assert events == ["data-only"]
        events.append("project-setup")
        return command_owner.CommandResult(
            0,
            "",
            "",
            supervisor=command_owner.CommandSupervisorReceipt(
                final_process_group_quiescent=True
            ),
        )

    def collect(*_args: Any, **options: Any) -> dict[str, Any]:
        assert events == ["data-only", "project-setup"]
        assert options["source_identity"] == source_identity
        assert options["trusted_root"] == trusted
        events.append("collection")
        return {"source_identity": source_identity}

    monkeypatch.setattr(command_owner, "run_command", prepare)
    monkeypatch.setattr(gate, "_trusted_ordinary_probe", collect)
    receipt: dict[str, Any] = {}
    claim = (
        actual
        if expected == "matching"
        else "sha256:" + "0" * 64
        if expected == "mismatched"
        else None
        if expected == "missing"
        else "unqualified"
    )

    def inspect() -> str:
        return gate._candidate_producer_identity(
            root,
            trusted_root=trusted,
            ordinary_receipt=receipt,
            ordinary_paths=("source.py",),
            source_identity=source_identity,
            expected_producer_identity=claim,
        )

    if expected == "matching":
        assert inspect() == actual
        assert events == ["data-only", "project-setup", "collection"]
        assert receipt["source_identity"] == source_identity
    else:
        with pytest.raises(ValueError, match="[Pp]roducer"):
            inspect()
        assert events == (["data-only"] if expected == "mismatched" else [])
        assert receipt == {}
    assert all(not row["environment"].exists() for row in observations)


@pytest.mark.process
def test_native_candidate_producer_probe_never_executes_candidate_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / "backend" / name, backend / name)
    original_lock = (backend / "uv.lock").read_bytes()
    marker = tmp_path / "candidate-executed"
    bomb = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('candidate project code must remain data')\n"
    )
    (backend / "hatch_build.py").write_text(bomb, encoding="utf-8")
    candidate_package = backend / "src/oh_goon"
    candidate_package.mkdir(parents=True)
    (candidate_package / "__init__.py").write_text(bomb, encoding="utf-8")
    monkeypatch.setenv("UV_OFFLINE", "1")

    identity = gate._candidate_producer_identity(root)

    assert identity == runtime_contract.require_loaded_hashmarks_runtime()
    assert not marker.exists()
    assert (backend / "uv.lock").read_bytes() == original_lock
    assert not (backend / ".venv").exists()


@pytest.mark.process
def test_native_candidate_parent_workspace_cannot_select_another_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / "backend" / name, backend / name)
    original_lock = (backend / "uv.lock").read_bytes()
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["backend"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("UV_OFFLINE", "1")
    native_run = command_owner.run_command
    commands: list[tuple[str, ...]] = []

    def observe(*argv: str, **options: Any) -> command_owner.CommandResult:
        commands.append(argv)
        return native_run(*argv, **options)

    monkeypatch.setattr(command_owner, "run_command", observe)

    with pytest.raises(ValueError, match="inspected backend lock"):
        gate._candidate_producer_identity(root)

    assert len(commands) == 1
    assert commands[0][:3] == ("uv", "workspace", "dir")
    assert (backend / "uv.lock").read_bytes() == original_lock
    assert not (root / "uv.lock").exists()
    assert not (backend / ".venv").exists()


@pytest.mark.parametrize("fault", ("missing", "mismatch", "truncated", "failure"))
def test_candidate_workspace_scope_must_be_complete_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    observations: list[dict[str, Any]] = []

    def workspace(*argv: str, **options: Any) -> command_owner.CommandResult:
        assert argv[:3] == ("uv", "workspace", "dir")
        observations.append({"argv": argv, "options": options})
        output = str(root / "backend") + "\n"
        if fault == "missing":
            output = ""
        elif fault == "mismatch":
            output = str(root) + "\n"
        return command_owner.CommandResult(
            17 if fault == "failure" else 0,
            output,
            "",
            stdout_truncated=fault == "truncated",
        )

    monkeypatch.setattr(command_owner, "run_command", workspace)

    with pytest.raises(ValueError, match="inspected backend lock"):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    argv = observations[0]["argv"]
    assert {"--no-config", "--offline", "--no-python-downloads"} <= set(argv)
    assert argv[argv.index("--project") + 1] == str(root / "backend")
    options = observations[0]["options"]
    assert not Path(options["env"]["UV_PROJECT_ENVIRONMENT"]).exists()
    assert not options["cwd"].exists()


def test_candidate_producer_identity_binds_bytes_even_when_version_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    first = _stub_candidate_materialization(
        monkeypatch, source='__version__ = "0.32.1"\nVALUE = "first"\n'
    )
    first_identity = gate._candidate_producer_identity(root)
    second = _stub_candidate_materialization(
        monkeypatch, source='__version__ = "0.32.1"\nVALUE = "second"\n'
    )
    second_identity = gate._candidate_producer_identity(root)

    assert first_identity == first[0]["identity"]
    assert second_identity == second[0]["identity"]
    assert first_identity != second_identity
    assert first[0]["environment"] != second[0]["environment"]


@pytest.mark.parametrize(
    "fault",
    (
        "missing",
        "duplicate",
        "missing-package",
        "package-symlink",
        "member-symlink",
        "metadata-symlink",
    ),
)
def test_candidate_producer_rejects_unbound_materialized_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(package: Path, metadata: Path) -> None:
        if fault == "missing":
            shutil.rmtree(metadata)
        elif fault == "duplicate":
            shutil.copytree(metadata, metadata.with_name("hashmarks-0.99.0.dist-info"))
        elif fault == "missing-package":
            shutil.rmtree(package)
        elif fault == "package-symlink":
            outside = tmp_path / "outside-package"
            package.rename(outside)
            package.symlink_to(outside, target_is_directory=True)
        elif fault == "member-symlink":
            outside = tmp_path / "outside-implementation.py"
            member = package / "implementation.py"
            member.rename(outside)
            member.symlink_to(outside)
        else:
            outside = tmp_path / "outside-metadata"
            metadata.rename(outside)
            metadata.symlink_to(outside, target_is_directory=True)

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)

    with pytest.raises((OSError, ValueError)):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


@pytest.mark.parametrize("name", ("METADATA", "RECORD"))
@pytest.mark.parametrize("fault", ("missing", "directory", "symlink", "oversized"))
def test_candidate_distribution_metadata_is_safe_before_importlib_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(_package: Path, metadata: Path) -> None:
        path = metadata / name
        if fault == "oversized":
            with path.open("r+b") as stream:
                stream.truncate(8 * 1024 * 1024 + 1)
        elif fault == "symlink":
            outside = tmp_path / "outside-distribution-metadata"
            path.rename(outside)
            path.symlink_to(outside)
        else:
            path.unlink()
            if fault == "directory":
                path.mkdir()

    def unexpected_metadata_read(**_options: Any) -> None:
        pytest.fail("Unsafe distribution metadata reached importlib inspection")

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)
    monkeypatch.setattr(metadata, "distributions", unexpected_metadata_read)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


def test_candidate_producer_rejects_non_native_egg_metadata_before_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(_package: Path, metadata: Path) -> None:
        legacy = metadata.with_name("hashmarks.egg-info")
        legacy.mkdir()
        shutil.copyfile(metadata / "METADATA", legacy / "PKG-INFO")

    def unexpected_metadata_read(**_options: Any) -> None:
        pytest.fail("Non-native distribution metadata reached importlib inspection")

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)
    monkeypatch.setattr(metadata, "distributions", unexpected_metadata_read)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


@pytest.mark.parametrize("fault", ("failure", "truncated"))
def test_candidate_producer_never_falls_back_after_native_materialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    result = command_owner.CommandResult(
        17 if fault == "failure" else 0,
        "partial native observation",
        "native failed" if fault == "failure" else "",
        stdout_truncated=fault == "truncated",
    )
    observations = _stub_candidate_materialization(monkeypatch, result=result)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


def test_candidate_producer_timeout_is_terminal_and_removes_partial_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    environments: list[Path] = []

    def timeout(*argv: str, **options: Any) -> command_owner.CommandResult:
        if argv[:3] == ("uv", "workspace", "dir"):
            return command_owner.CommandResult(0, str(root / "backend") + "\n", "")
        assert argv[:2] == ("uv", "sync")
        environment = Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
        environment.mkdir(parents=True)
        (environment / "partial-install").write_text("incomplete", encoding="utf-8")
        environments.append(environment)
        raise command_owner.CommandTimeoutExpired(
            argv,
            options["timeout"],
            supervisor=command_owner.CommandSupervisorReceipt(),
        )

    monkeypatch.setattr(command_owner, "run_command", timeout)

    with pytest.raises(command_owner.CommandTimeoutExpired):
        gate._candidate_producer_identity(root)

    assert len(environments) == 1
    assert not environments[0].exists()


@pytest.mark.parametrize(
    "relative", ("backend", "backend/pyproject.toml", "backend/uv.lock")
)
def test_candidate_dependency_input_symlink_rejected_before_native_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    path = root / relative
    outside = tmp_path / "outside-input"
    directory = path.is_dir()
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=directory)
    observations = _stub_candidate_materialization(monkeypatch)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert observations == []


@pytest.mark.parametrize(
    "local_source",
    ('{ path = "../candidate.whl" }', '{ editable = "../hashmarks" }'),
)
def test_candidate_producer_rejects_local_dependency_before_native_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_source: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    (root / "backend/uv.lock").write_text(
        'version = 1\n[[package]]\nname = "hashmarks"\nversion = "0.32.1"\n'
        f"source = {local_source}\n",
        encoding="utf-8",
    )
    observations = _stub_candidate_materialization(monkeypatch)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert observations == []


@pytest.mark.parametrize(
    "fault",
    (
        "none",
        "identity-mismatch",
        "invalid-plan",
        "unverified-plan",
        "native-timeout",
        "before-source-change",
        "after-source-change",
        "after-dirty-tree",
        "candidate-head-changed",
        "base-head-changed",
        "base-dirty-after",
        "foreign-asset-owner",
        "unsupported-dependency-intent",
        "dependency-intent-without-lock",
        "hashmarks-upgrade",
        "hashmarks-identity-mismatch",
        "generic-dependency-update",
        "unclaimed-hashmarks-upgrade",
    ),
)
def test_merge_gate_binds_candidate_producer_and_rechecks_source_after_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
) -> None:
    root = _candidate_gate_root(tmp_path)
    trusted = tmp_path / "trusted"
    trusted_sources = trusted / "backend/src/oh_goon"
    trusted_sources.mkdir(parents=True)
    monkeypatch.setattr(
        gate,
        "__file__",
        str(trusted_sources / "repository_tooling/verification_gate_control.py"),
    )
    for module, relative in (
        (command_owner, "engine/command.py"),
        (admission_owner, "repository_tooling/development_verification.py"),
        (lock_owner, "repository_tooling/_dependency_lock_portability.py"),
        (package_slim, "repository_tooling/release/package_slim.py"),
    ):
        monkeypatch.setattr(module, "__file__", str(trusted_sources / relative))

    def source_file(owner: object) -> str:
        if owner is public_evidence:
            relative = "integrations/hashmarks/__init__.py"
        elif owner is public_evidence.observe_repository_assets:
            if fault == "foreign-asset-owner":
                return str(tmp_path / "candidate-owned-observer.py")
            relative = "integrations/hashmarks/development_verification.py"
        else:
            assert owner is public_evidence.native_producer_implementation_identity
            relative = "integrations/hashmarks/runtime_contract.py"
        return str(trusted_sources / relative)

    monkeypatch.setattr(gate, "getsourcefile", source_file)
    plan, result = _proof()
    base_manifest = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    if fault in {"hashmarks-upgrade", "hashmarks-identity-mismatch"}:
        base_manifest = _BEFORE_HASHMARKS
        (root / "backend/pyproject.toml").write_text(_AFTER_HASHMARKS, encoding="utf-8")
        plan, result = _proof(
            source="backend/uv.lock",
            test_file=HASHMARKS_UPGRADE_TESTS[0],
            additional_tests=(*HASHMARKS_UPGRADE_TESTS[1:], *VERIFICATION_POLICY_TESTS),
        )
        plan.update(
            changed_paths=["backend/pyproject.toml", "backend/uv.lock"],
            policy_status="REVIEW_REQUIRED",
            obligations=[
                "verification-policy protection",
                HASHMARKS_UPGRADE_OBLIGATION,
            ],
        )
        plan["evidence"]["hashmarks_upgrade"] = {
            "authority": "oh-goon-execution-policy",
            "scope": "mandatory-hashmarks-upgrade",
            "consumer_coverage": "not-claimed",
        }
        plan["evidence"]["hashmarks_native_artifacts"] = {
            "schema": "oh-goon.native-uv-artifact-comparison/v1",
            "authority": "native-uv-export",
            "equivalent": True,
            "before_artifact_sha256": "a" * 64,
            "after_artifact_sha256": "a" * 64,
            "before_artifact_bytes": 25,
            "after_artifact_bytes": 25,
            "excluded_packages": ["hashmarks"],
            "scope": {
                "format": "pylock.toml",
                "groups": "all",
                "extras": "all",
                "workspace": "not-emitted",
                "exclusion": "native-no-emit-package",
            },
            "tool": {
                "name": "uv",
                "version": "uv synthetic",
                "executable": "/native/uv",
            },
        }
    elif fault in {"generic-dependency-update", "unclaimed-hashmarks-upgrade"}:
        plan["changed_paths"].append("backend/uv.lock")
        plan["evidence"]["observations"].append(
            {
                **copy.deepcopy(plan["evidence"]["observations"][0]),
                "changed_path": "backend/uv.lock",
            }
        )
        if fault == "unclaimed-hashmarks-upgrade":
            base_manifest = _BEFORE_HASHMARKS
            (root / "backend/pyproject.toml").write_text(
                _AFTER_HASHMARKS, encoding="utf-8"
            )
            plan["changed_paths"].append("backend/pyproject.toml")
            plan["pytest"].extend(VERIFICATION_POLICY_TESTS)
            plan.update(
                policy_status="REVIEW_REQUIRED",
                obligations=["verification-policy protection"],
            )
            _plan, result = _proof(additional_tests=VERIFICATION_POLICY_TESTS)
    if "backend/uv.lock" in plan["changed_paths"]:
        plan["static"].append("package")
        result["outcomes"].append({"owner": "Native package", "returncode": 0})
    plan["requested_head"] = "c" * 40
    if fault in {"unsupported-dependency-intent", "dependency-intent-without-lock"}:
        plan["changed_paths"].append("backend/pyproject.toml")
        current_manifest = base_manifest
        if fault == "unsupported-dependency-intent":
            current_manifest += (
                '\n[dependency-groups]\nquality=["unexpected-library"]\n'
            )
            plan["changed_paths"].append("backend/uv.lock")
        else:
            current_manifest = current_manifest.replace(
                'version = "0.0.0"', 'version = "0.0.0"\ndependencies=["new-library"]'
            )
        (root / "backend/pyproject.toml").write_text(current_manifest, encoding="utf-8")
    if fault == "invalid-plan":
        plan["schema_version"] = "unrecognized-plan/v1"
    elif fault == "unverified-plan":
        plan["unverified"] = ["changed source has unresolved scope"]
    result["plan"] = copy.deepcopy(plan)
    plan_file, result_file = tmp_path / "plan.json", tmp_path / "result.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    result_file.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oh-goon-verify-gate",
            "--plan",
            str(plan_file),
            "--result",
            str(result_file),
            "--candidate",
            plan["candidate"],
            "--base",
            plan["base"],
            "--control-base",
            plan["base"],
            "--head",
            plan["requested_head"],
            "--root",
            str(root),
            "--trusted-root",
            str(trusted),
        ],
    )
    observed: list[Path] = []
    base_intent_reads: list[str] = []

    def producer(candidate: Path, **options: Any) -> str:
        observed.append(candidate)
        expects_dependency_probe = (
            "backend/uv.lock" in plan["changed_paths"]
            and "hashmarks_upgrade" not in plan["evidence"]
        )
        expects_ordinary_probe = any(
            Path(path).suffix == ".py" for path in plan["changed_paths"]
        )
        assert (options["dependency_plan"] is not None) == expects_dependency_probe
        assert (options["ordinary_receipt"] is not None) == expects_ordinary_probe
        if expects_dependency_probe:
            assert options["dependency_plan"] == plan
            assert options["trusted_root"] == trusted
            options["dependency_receipt"].update(
                source_identity=plan["source_identity"]
            )
        if expects_ordinary_probe:
            assert options["trusted_root"] == trusted
            assert sorted(options["ordinary_paths"]) == sorted(plan["changed_paths"])
            assert options["source_identity"] == plan["source_identity"]
            options["ordinary_receipt"].update(
                source_identity=plan["source_identity"],
                changed_paths=sorted(plan["changed_paths"]),
                contexts=[
                    {"name": name, "selected": []}
                    for name in ("backend", "tooling", "cross-owner")
                ],
            )
        if fault == "native-timeout":
            raise command_owner.CommandTimeoutExpired(
                ("uv", "sync"), 120, supervisor=command_owner.CommandSupervisorReceipt()
            )
        return (
            "sha256:"
            + (
                "d"
                if fault in {"identity-mismatch", "hashmarks-identity-mismatch"}
                else "b"
            )
            * 64
        )

    def source_identity(candidate: Path) -> str:
        assert candidate == root
        if fault == "before-source-change" or (
            fault == "after-source-change" and observed
        ):
            return "sha256:" + "e" * 64
        return str(plan["source_identity"])

    def git(*argv: str, **_options: Any) -> command_owner.CommandResult:
        assert argv[:2] == ("git", "-C")
        directory, arguments = Path(argv[2]), argv[3:]
        if arguments == ("rev-parse", "HEAD"):
            output = plan["base"] if directory == trusted else plan["candidate"]
            if observed and (
                (directory == root and fault == "candidate-head-changed")
                or (directory == trusted and fault == "base-head-changed")
            ):
                output = "f" * 40
        elif arguments[0] == "status":
            output = (
                "?? introduced-after-probe\n"
                if observed
                and (
                    (directory == root and fault == "after-dirty-tree")
                    or (directory == trusted and fault == "base-dirty-after")
                )
                else ""
            )
        elif arguments[0] == "merge-base":
            output = ""
        elif arguments[0] == "show":
            base_intent_reads.append(arguments[-1])
            output = base_manifest
        else:
            assert arguments[0] == "diff"
            output = "".join("M\0" + path + "\0" for path in plan["changed_paths"])
        return command_owner.CommandResult(0, output, "")

    monkeypatch.setattr(gate, "_candidate_producer_identity", producer)
    monkeypatch.setattr(package_slim, "canonical_source_identity", source_identity)
    monkeypatch.setattr(command_owner, "run_command", git)
    monkeypatch.setattr(admission_owner, "run_command", git)
    if "hashmarks_native_artifacts" in plan["evidence"]:
        monkeypatch.setattr(
            admission_owner,
            "_native_hashmarks_artifact_comparison",
            lambda _root, _base: plan["evidence"]["hashmarks_native_artifacts"],
        )

    status = gate.main()

    if fault in {
        "hashmarks-upgrade",
        "hashmarks-identity-mismatch",
        "unclaimed-hashmarks-upgrade",
    }:
        assert base_intent_reads == [f"{plan['base']}:backend/pyproject.toml"]
    accepted = fault in {
        "none",
        "hashmarks-upgrade",
        "generic-dependency-update",
        "unclaimed-hashmarks-upgrade",
    }
    assert status == (0 if accepted else 1)
    assert observed == (
        []
        if fault
        in {
            "before-source-change",
            "invalid-plan",
            "unverified-plan",
            "foreign-asset-owner",
            "unsupported-dependency-intent",
            "dependency-intent-without-lock",
        }
        else [root]
    )
    output = capsys.readouterr().out
    assert ("MERGE_VERIFIED:" in output) == accepted
    assert ("MERGE UNVERIFIED:" in output) != accepted


@pytest.mark.parametrize(
    "capability",
    ("observe_repository_assets", "native_producer_implementation_identity"),
)
def test_trusted_public_api_binds_actual_function_origin(
    monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    gate._require_trusted_hashmarks_api(root)

    def foreign_owner(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("foreign implementation must never execute")

    monkeypatch.setattr(public_evidence, capability, foreign_owner)
    with pytest.raises(
        ValueError, match="Hashmarks owner outside its trusted checkout"
    ):
        gate._require_trusted_hashmarks_api(root)


@pytest.fixture
def bounded_owner_repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    files = {
        "backend/pyproject.toml": (
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
            '[project]\nname = "native-owner-fixture"\nversion = "0.0.0"\ndependencies = []\n'
            '[tool.pytest.ini_options]\naddopts = "-q"\n'
            '[tool.hatch.build]\npackages = ["src/native_owner_fixture"]\n'
        ),
        "backend/hatch_build.py": "def initialize():\n    return None\n",
        "backend/uv.lock": "version = 1\nrevision = 3\n",
        "backend/src/native_owner_fixture/__init__.py": "VALUE = 1\n",
        "templates/goons/general/native-fixture/src/native_owner_fixture/__init__.py": "VALUE = 1\n",
    }
    repository = Path(__file__).resolve().parents[2]
    for name in (
        "package.json",
        "package-lock.json",
        "vitest.config.ts",
        "playwright.config.ts",
    ):
        files[f"frontend/{name}"] = (repository / "frontend" / name).read_text()
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Synthetic Native Owner",
        "GIT_AUTHOR_EMAIL": "native-owner@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Native Owner",
        "GIT_COMMITTER_EMAIL": "native-owner@example.invalid",
    }
    for arguments in (
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "base",
        ),
    ):
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
    base = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=20
    ).strip()
    return root, base


def _bounded_owner_test(path: str, runner: str) -> dict[str, Any]:
    return {
        "changed_path": path,
        "state": "CHANGED_TEST",
        "stale": False,
        "nominations": [
            {
                "workspace_prefix": "",
                "nomination": {
                    "available": True,
                    "runner_evidence": runner,
                    "verification_target": {"path": path},
                },
            }
        ],
    }


def _bounded_owner_case(
    root: Path, base: str, kind: str
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    if kind == "build":
        source = root / "backend/pyproject.toml"
        source.write_text(
            source.read_text()
            + 'dev-mode-dirs = ["src", "../templates/goons/general/native-fixture/src"]\n'
        )
        paths = ["backend/pyproject.toml", "backend/hatch_build.py"]
    elif kind == "api":
        paths = [
            "backend/src/oh_goon/api/routes/native_fixture.py",
            "backend/tests/integration/api/test_native_fixture.py",
        ]
        for relative, content in (
            (paths[0], "def native_fixture():\n    return 'synthetic'\n"),
            (paths[1], "def test_native_fixture():\n    assert 'synthetic'\n"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        tests = [_bounded_owner_test(paths[1], "pytest")]
    elif kind == "frontend":
        paths = [
            "frontend/src/app/nativeStyles.ts",
            "frontend/src/styles/nativeWorkspace.css",
            "frontend/src/i18n/locales/en/translation.json",
            "frontend/src/app/NativeWorkspace.test.tsx",
            "frontend/e2e/native-workspace.spec.ts",
        ]
        for relative, content in zip(
            paths,
            (
                'import "../styles/nativeWorkspace.css";\n',
                ".native-workspace { display: grid; }\n",
                '{"nativeWorkspace": "Synthetic workspace"}\n',
                'import { test, expect } from "vitest";\ntest("synthetic", () => expect(true).toBe(true));\n',
                'import { test, expect } from "@playwright/test";\ntest("synthetic", () => expect(true).toBe(true));\n',
            ),
            strict=True,
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        tests = [
            _bounded_owner_test(paths[-2], "vitest"),
            _bounded_owner_test(paths[-1], "vitest"),
        ]
    else:
        assert kind == "template"
        project = "templates/goons/general/native-fixture"
        files = {
            f"{project}/pyproject.toml": '[project]\nname = "native-fixture"\nversion = "0.0.0"\ndependencies = []\n',
            f"{project}/goon.yml": "runtime: python\nuv: {lock_mode: frozen}\nsteps:\n  - id: prove\n    run: python -c 'print(1)'\n",
            f"{project}/uv.lock": "version = 1\nrevision = 3\n",
            f"{project}/src/native_fixture/__init__.py": "VALUE = 1\n",
            "templates/plans/general/native-fixture/plan.yml": "goons:\n  - general/native-fixture\n",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        paths = list(files)
    observed_tests = {row["changed_path"] for row in tests}
    evidence = {
        "owner_coverage": "not-claimed",
        "observations": [
            {
                "changed_path": path,
                "state": "UNAVAILABLE",
                "stale": False,
                "nominations": [],
                "qualification": {
                    "completeness": "unknown",
                    "negative_evidence_admissible": False,
                },
            }
            for path in paths
            if path not in observed_tests
        ]
        + tests,
    }
    proof = gate.get_bounded_owner_proofs(root, base, paths, evidence)
    return paths, evidence, proof


def _bounded_owner_execution(
    paths: list[str], evidence: dict[str, Any], proof: dict[str, Any]
) -> tuple[dict[str, Any], set[str], set[str], set[str]]:
    routes = proof["routes"]
    evidence["bounded_owner_proofs"] = copy.deepcopy(proof)
    plan = {
        "changed_paths": paths,
        "obligations": [row["obligation"] for row in routes],
        "static": sorted({name for row in routes for name in row["static"]}),
    }
    targets = {target for row in routes for target in row["pytest"]}
    files = {target.split("::", 1)[0] for target in targets}
    files.update(target for row in routes for target in row["frontend"])
    members = {
        target if "::" in target else target + "::test_native_result"
        for target in targets
    }
    browser = (
        {"frontend/e2e/native-workspace.spec.ts"}
        if any(row["browser"] for row in routes)
        else set()
    )
    return plan, files, members, browser


@pytest.mark.parametrize("kind", ("build", "api", "frontend", "template"))
def test_bounded_owner_proof_keeps_unknown_consumers_and_requires_its_native_owners(
    bounded_owner_repository: tuple[Path, str], kind: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, kind)
    assert len(proof["routes"]) == 1
    assert (
        proof["routes"][0]["obligation"]
        == {
            "build": gate.BOUNDED_BUILD_PROOF,
            "api": gate.BOUNDED_API_PROOF,
            "frontend": gate.BOUNDED_FRONTEND_PROOF,
            "template": gate.BOUNDED_TEMPLATE_PROOF,
        }[kind]
    )
    original_observations = copy.deepcopy(evidence["observations"])
    plan, files, members, browser = _bounded_owner_execution(paths, evidence, proof)

    admitted = gate.require_bounded_owner_proofs(
        plan, evidence, files, members, browser, expected=proof
    )

    assert admitted == set(proof["routes"][0]["paths"])
    assert proof["consumer_coverage"] == "not-claimed"
    assert evidence["observations"] == original_observations


@pytest.mark.parametrize(
    "fault", ("dependency", "pytest-config", "uv-policy", "builder", "lock")
)
def test_bounded_owner_build_mapping_cannot_hide_other_native_configuration(
    bounded_owner_repository: tuple[Path, str], fault: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "build")
    assert proof["routes"]
    manifest = root / "backend/pyproject.toml"
    content = manifest.read_text()
    if fault == "dependency":
        content = content.replace("dependencies = []", 'dependencies = ["unexpected"]')
    elif fault == "pytest-config":
        content = content.replace('addopts = "-q"', 'addopts = "--ignore=tests"')
    elif fault == "uv-policy":
        content += '\n[tool.uv]\nindex-url = "https://example.invalid/simple"\n'
    elif fault == "builder":
        content = content.replace(
            'build-backend = "hatchling.build"', 'build-backend = "other.build"'
        )
    else:
        paths.append("backend/uv.lock")
    manifest.write_text(content)

    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize("mapping", ("outside", "absolute", "generated", "symlink"))
def test_bounded_owner_build_mapping_requires_committed_contained_source(
    bounded_owner_repository: tuple[Path, str], mapping: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "build")
    assert proof["routes"]
    outside = root.parent / "outside"
    outside.mkdir()
    if mapping == "outside":
        value = "../../outside"
    elif mapping == "absolute":
        value = str(root / "backend/src")
    elif mapping == "generated":
        (root / "backend/.venv").mkdir()
        value = ".venv"
    else:
        (root / "backend/linked").symlink_to(outside, target_is_directory=True)
        value = "linked"
    manifest = root / "backend/pyproject.toml"
    content = manifest.read_text()
    content = (
        content[: content.index("dev-mode-dirs")]
        + "dev-mode-dirs = "
        + json.dumps([value])
        + "\n"
    )
    manifest.write_text(content)

    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize("kind", ("api", "frontend"))
def test_bounded_owner_boundary_requires_a_current_actually_changed_native_test(
    bounded_owner_repository: tuple[Path, str], kind: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, kind)
    assert proof["routes"]
    test = next(
        row for row in evidence["observations"] if row["state"] == "CHANGED_TEST"
    )
    paths.remove(test["changed_path"])
    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []
    paths.append(test["changed_path"])
    test["stale"] = True
    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize(
    "tool_input",
    ("package.json", "package-lock.json", "vitest.config.ts", "playwright.config.ts"),
)
def test_bounded_owner_frontend_refuses_changed_native_tool_authority(
    bounded_owner_repository: tuple[Path, str], tool_input: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "frontend")
    assert proof["routes"]
    path = root / "frontend" / tool_input
    path.write_text(path.read_text() + "\n")

    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize(
    "declaration",
    (
        'dependencies = ["unexpected"]',
        'dependencies = []\n[project.optional-dependencies]\nextra = ["unexpected"]',
        'dependencies = []\n[dependency-groups]\ndev = ["unexpected"]',
        "dependencies = []\n[tool.uv]\nmanaged = false",
    ),
)
def test_bounded_owner_stdlib_scope_does_not_admit_dependency_or_uv_policy(
    bounded_owner_repository: tuple[Path, str], declaration: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "template")
    assert proof["routes"]
    manifest = root / "templates/goons/general/native-fixture/pyproject.toml"
    manifest.write_text(manifest.read_text().replace("dependencies = []", declaration))

    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize(
    "fault", ("authority", "source", "obligation", "static", "native-test")
)
def test_bounded_owner_receipt_cannot_substitute_authority_source_or_execution(
    bounded_owner_repository: tuple[Path, str], fault: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "build")
    plan, files, members, browser = _bounded_owner_execution(paths, evidence, proof)
    gate.require_bounded_owner_proofs(
        plan, evidence, files, members, browser, expected=proof
    )
    if fault == "authority":
        evidence["bounded_owner_proofs"]["authority"] = "hashmarks"
    elif fault == "source":
        evidence["bounded_owner_proofs"]["routes"][0]["paths"] = [
            "backend/unrelated.py"
        ]
    elif fault == "obligation":
        plan["obligations"] = []
    elif fault == "static":
        plan["static"].remove("package")
    else:
        files.clear()
        members.clear()
        files.add("scripts/tests/test_unrelated.py")

    with pytest.raises(ValueError, match="Bounded native owner proof"):
        gate.require_bounded_owner_proofs(
            plan, evidence, files, members, browser, expected=proof
        )


@pytest.mark.parametrize(
    "kind,fault",
    (
        ("template", "native-project"),
        ("template", "compiler"),
        ("template", "run"),
        ("frontend", "component"),
        ("frontend", "browser"),
    ),
)
def test_bounded_owner_proof_cannot_replace_its_distinct_native_boundaries(
    bounded_owner_repository: tuple[Path, str], kind: str, fault: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, kind)
    plan, files, members, browser = _bounded_owner_execution(paths, evidence, proof)
    gate.require_bounded_owner_proofs(
        plan, evidence, files, members, browser, expected=proof
    )
    if fault == "native-project":
        members.remove(
            "scripts/tests/test_python_project_checks.py::test_native_shipped_stdlib_unittest_projects"
        )
        members.add("scripts/tests/test_python_project_checks.py::test_unrelated")
    elif fault == "compiler":
        files.remove(
            "backend/tests/integration/services/test_catalog_template_startability.py"
        )
    elif fault == "run":
        files.remove("tests/integration/test_private_code_catalog_example.py")
    elif fault == "component":
        files.remove("frontend/src/app/NativeWorkspace.test.tsx")
    else:
        browser.clear()

    with pytest.raises(ValueError, match="Bounded native owner proof"):
        gate.require_bounded_owner_proofs(
            plan, evidence, files, members, browser, expected=proof
        )


def _bounded_browser_receipt(
    project: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from oh_goon.repository_tooling.frontend_test_results import (
        native_playwright_arguments,
    )

    file = "frontend/e2e/native-workspace.spec.ts"
    report: dict[str, Any] = {
        "config": {
            "configFile": str(project / "playwright.config.ts"),
            "rootDir": str(project / "e2e"),
            "version": "native-browser-fixture",
            "forbidOnly": True,
            "projects": [
                {
                    "id": "chromium",
                    "name": "chromium",
                    "repeatEach": 1,
                    "retries": 0,
                    "testDir": str(project / "e2e"),
                }
            ],
        },
        "suites": [
            {
                "specs": [
                    {
                        "id": "original-native-boundary",
                        "title": "original native boundary",
                        "file": "native-workspace.spec.ts",
                        "line": 1,
                        "column": 1,
                        "ok": True,
                        "tests": [
                            {
                                "projectId": "chromium",
                                "projectName": "chromium",
                                "expectedStatus": "passed",
                                "status": "skipped",
                                "results": [],
                            }
                        ],
                    }
                ]
            }
        ],
        "errors": [],
        "stats": {"expected": 0, "skipped": 1, "unexpected": 0, "flaky": 0},
    }
    directory = project.parent.parent / "browser-proof"
    arguments = native_playwright_arguments(project, (file,))
    collection = {
        "requested": [file],
        "selected": [
            {
                "id": "original-native-boundary",
                "title": "original native boundary",
                "file": file,
                "line": 1,
                "column": 1,
                "project_id": "chromium",
                "project_name": "chromium",
                "occurrence": 0,
            }
        ],
        "unavailable_advisory_targets": [],
        "native_project_root": str(project),
        "native_report": copy.deepcopy(report),
        "native_execution": {
            "argv": [
                "npm",
                "exec",
                "--offline",
                "--no",
                "--",
                "playwright",
                "test",
                "--list",
                "--reporter=json",
                "--pass-with-no-tests",
                *arguments,
                f"--output={directory / 'artifacts'}",
            ],
            "returncode": 0,
            "process_group_quiescent": True,
            "report_path": str(directory / "collection.json"),
        },
    }
    test = report["suites"][0]["specs"][0]["tests"][0]
    test.update(
        status="expected", results=[{"status": "passed", "retry": 0, "errors": []}]
    )
    report["stats"].update(expected=1, skipped=0)
    execution = {
        "argv": [
            "npm",
            "run",
            "browser:test",
            "--",
            "--reporter=json",
            *arguments,
            f"--output={directory / 'artifacts'}",
        ],
        "process_group_quiescent": True,
    }
    return collection, report, execution


def _bounded_merge_proof(
    root: Path, base: str, kind: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    paths, evidence, proof = _bounded_owner_case(root, base, kind)
    targets = sorted(
        {target for route in proof["routes"] for target in route["pytest"]}
        | set(gate.required_policy_tests(paths))
    )
    plan, result = (
        _proof(test_file=targets[0], additional_tests=tuple(targets[1:]))
        if targets
        else _proof()
    )
    native_pytest = result["outcomes"][0]
    static = set(gate.required_static_obligations(paths))
    static.update(name for route in proof["routes"] for name in route["static"])
    evidence.update(
        producer_implementation_identity="sha256:" + "b" * 64,
        bounded_owner_proofs=copy.deepcopy(proof),
    )
    if kind == "build":
        evidence["observations"][0]["changed_set"] = {
            "changes": [{"classification": {"kind": "verification-support"}}]
        }
    plan.update(
        base=base,
        changed_paths=paths,
        static=sorted(static),
        pytest=targets,
        frontend=sorted(
            {target for route in proof["routes"] for target in route["frontend"]}
        ),
        browser=["frontend/e2e/native-workspace.spec.ts"] if kind == "frontend" else [],
        obligations=[
            *gate.required_policy_obligations(paths),
            *(route["obligation"] for route in proof["routes"]),
        ],
        policy_status="REVIEW_REQUIRED"
        if gate.required_policy_tests(paths)
        else "ORDINARY",
        evidence=evidence,
    )
    outcomes = [native_pytest] if targets else []
    for name in plan["static"]:
        for owner in {
            "python": ("Ruff", "Format", "Types"),
            "frontend": tuple(
                f"Frontend {part}"
                for part in ("typecheck", "lint", "architecture", "i18n:check")
            ),
            "architecture": ("Architecture",),
            "generated": ("Generated contracts",),
            "package": ("Native package",),
        }[name]:
            outcomes.append({"owner": owner, "returncode": 0})
    if kind == "frontend":
        component = plan["frontend"][0]
        project = root / "frontend"
        evidence["frontend_admission"] = {
            "collection": {
                "requested": [component],
                "selected": [{"file": component, "projectName": "dom"}],
                "unavailable_advisory_targets": [],
                "native_project_root": str(project),
                "native_execution": {
                    "argv": [
                        "npm",
                        "exec",
                        "--offline",
                        "--no",
                        "--",
                        "vitest",
                        "list",
                        "./" + component.removeprefix("frontend/"),
                        "--filesOnly",
                        f"--json={root.parent / 'vitest-collection.json'}",
                    ],
                    "returncode": 0,
                    "process_group_quiescent": True,
                },
            }
        }
        outcomes.append(
            {
                "owner": "Frontend behavior",
                "returncode": 0,
                "native_project_root": str(project),
                "native_report": {
                    "success": True,
                    "numTotalTests": 1,
                    "numPassedTests": 1,
                    "numFailedTests": 0,
                    "numPendingTests": 0,
                    "numTodoTests": 0,
                    "numTotalTestSuites": 1,
                    "numPassedTestSuites": 1,
                    "numFailedTestSuites": 0,
                    "numPendingTestSuites": 0,
                    "testResults": [
                        {
                            "name": str(root / component),
                            "assertionResults": [{"status": "passed"}],
                        }
                    ],
                },
            }
        )
        collection, report, execution = _bounded_browser_receipt(project)
        evidence["browser_admission"] = {"collection": collection}
        outcomes.append(
            {
                "owner": "Frontend browser",
                "returncode": 0,
                "native_project_root": str(project),
                "native_report": report,
                "native_execution": execution,
            }
        )
    result.update(plan=copy.deepcopy(plan), outcomes=outcomes)
    return plan, result


def _validate_bounded_merge(
    root: Path, plan: dict[str, Any], result: dict[str, Any]
) -> tuple[frozenset[str], frozenset[str]]:
    result["plan"] = copy.deepcopy(plan)
    return gate.validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        control_base=plan["base"],
        root=root,
    )


@pytest.mark.parametrize("kind", ("build", "template", "api", "frontend"))
def test_bounded_owner_merge_gate_uses_original_native_receipts_without_promoting_consumers(
    bounded_owner_repository: tuple[Path, str], kind: str
) -> None:
    root, base = bounded_owner_repository
    plan, result = _bounded_merge_proof(root, base, kind)
    original = copy.deepcopy(plan["evidence"]["observations"])

    files, _members = _validate_bounded_merge(root, plan, result)

    assert plan["evidence"]["observations"] == original
    if kind == "frontend":
        assert set(plan["browser"]) <= files


@pytest.mark.parametrize(
    "fault",
    ("missing-claim", "overclaimed-authority", "another-source", "source-drift"),
)
def test_bounded_owner_merge_gate_reobserves_source_before_accepting_claims(
    bounded_owner_repository: tuple[Path, str], fault: str
) -> None:
    root, base = bounded_owner_repository
    plan, result = _bounded_merge_proof(root, base, "build")
    _validate_bounded_merge(root, plan, result)
    if fault == "missing-claim":
        plan["evidence"].pop("bounded_owner_proofs")
    elif fault == "overclaimed-authority":
        plan["evidence"]["bounded_owner_proofs"]["consumer_coverage"] = "complete"
    elif fault == "another-source":
        plan["evidence"]["bounded_owner_proofs"]["routes"][0]["paths"] = [
            "backend/unrelated.py"
        ]
    else:
        manifest = root / "backend/pyproject.toml"
        manifest.write_text(
            manifest.read_text().replace(
                "dependencies = []", 'dependencies = ["unexpected"]'
            )
        )

    with pytest.raises(ValueError, match="Bounded native owner proof"):
        _validate_bounded_merge(root, plan, result)


def test_bounded_owner_merge_gate_does_not_waive_unrelated_support_sources(
    bounded_owner_repository: tuple[Path, str],
) -> None:
    root, base = bounded_owner_repository
    plan, result = _bounded_merge_proof(root, base, "build")
    _validate_bounded_merge(root, plan, result)
    support = "backend/tests/factories/unrelated.py"
    plan["changed_paths"].append(support)
    plan["evidence"]["observations"].append(
        {
            "changed_path": support,
            "state": "LINKED",
            "qualification": {"completeness": "unknown"},
            "changed_set": {
                "changes": [{"classification": {"kind": "verification-support"}}]
            },
            "nominations": [
                {
                    "nomination": {
                        "available": True,
                        "verification_target": {
                            "path": gate.BUILD_PROOF_TESTS[0].split("::", 1)[0]
                        },
                    }
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="executed qualified consumer"):
        _validate_bounded_merge(root, plan, result)


@pytest.mark.parametrize(
    "fault",
    (
        "missing-collection",
        "different-id",
        "skipped",
        "duplicate",
        "unsettled",
        "arguments",
        "no-membership",
        "missing-owner",
    ),
)
def test_bounded_owner_browser_gate_rejects_missing_or_substituted_native_proof(
    bounded_owner_repository: tuple[Path, str], fault: str
) -> None:
    root, base = bounded_owner_repository
    plan, result = _bounded_merge_proof(root, base, "frontend")
    _validate_bounded_merge(root, plan, result)
    browser = next(
        row for row in result["outcomes"] if row["owner"] == "Frontend browser"
    )
    spec = browser["native_report"]["suites"][0]["specs"][0]
    if fault == "missing-collection":
        plan["evidence"]["browser_admission"].pop("collection")
    elif fault == "different-id":
        spec["id"] = "another-native-case"
    elif fault == "skipped":
        spec["tests"][0]["results"][0]["status"] = "skipped"
    elif fault == "duplicate":
        browser["native_report"]["suites"][0]["specs"].append(copy.deepcopy(spec))
    elif fault == "unsettled":
        browser["native_execution"]["process_group_quiescent"] = False
    elif fault == "arguments":
        browser["native_execution"]["argv"][5] = "--project=firefox"
    elif fault == "no-membership":
        plan["browser"] = []
    else:
        result["outcomes"].remove(browser)

    with pytest.raises(ValueError):
        _validate_bounded_merge(root, plan, result)


def test_bounded_owner_native_collection_retains_whole_required_file_membership(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    required = "scripts/tests/test_native_boundary.py"
    changed = ("backend/src/native_boundary.py",)
    files = {
        "backend/pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        "backend/tests/test_backend.py": "def test_backend():\n    assert True\n",
        required: "def test_easy():\n    assert True\n\ndef test_original_boundary():\n    assert True\n",
        "tests/integration/test_cross_owner.py": "def test_cross_owner():\n    assert True\n",
        changed[0]: "VALUE = 1\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    trusted = Path(__file__).resolve().parents[2]
    environment = Path(sys.prefix)
    source_identity = package_slim.canonical_source_identity(root)
    requests = gate._ordinary_collection_requests(
        root, trusted_root=trusted, environment=environment, env=os.environ
    )

    receipt = gate._ordinary_candidate_collection(
        root,
        trusted_root=trusted,
        environment=environment,
        changed_paths=changed,
        source_identity=source_identity,
        requests=requests,
        env=os.environ,
        required_files=(required,),
    )

    assert receipt["changed_paths"] == list(changed)
    assert receipt["required_files"] == [required]
    assert [row["name"] for row in receipt["contexts"]] == [
        "backend",
        "tooling",
        "cross-owner",
    ]
    selected = [item for context in receipt["contexts"] for item in context["selected"]]
    assert {row["file"] for row in selected} == {required}
    members = frozenset(
        {required + "::test_easy", required + "::test_original_boundary"}
    )
    assert {row["selector"] for row in selected} == members
    gate._require_ordinary_changed_test_execution(
        receipt,
        changed_paths=changed,
        source_identity=source_identity,
        executed_files=frozenset({required}),
        executed_members=members,
        required_files=(required,),
    )

    with pytest.raises(ValueError, match="meaningful execution proof"):
        gate._require_ordinary_changed_test_execution(
            receipt,
            changed_paths=changed,
            source_identity=source_identity,
            executed_files=frozenset({required}),
            executed_members=frozenset({required + "::test_easy"}),
            required_files=(required,),
        )
    with pytest.raises(ValueError, match="another required owner scope"):
        gate._require_ordinary_changed_test_execution(
            receipt,
            changed_paths=changed,
            source_identity=source_identity,
            executed_files=frozenset({required}),
            executed_members=members,
        )


def test_bounded_owner_build_mapping_cannot_admit_committed_state_outside_source_identity(
    bounded_owner_repository: tuple[Path, str],
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "build")
    assert proof["routes"]
    state = root / "backend/.venv/state.py"
    state.parent.mkdir()
    state.write_text("VALUE = 1\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Synthetic Native Owner",
        "GIT_AUTHOR_EMAIL": "native-owner@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Native Owner",
        "GIT_COMMITTER_EMAIL": "native-owner@example.invalid",
    }
    for arguments in (
        ("add", "-f", "backend/.venv"),
        (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "state",
        ),
    ):
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
    assert subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-d", "HEAD", "--", "backend/.venv"],
        timeout=20,
    ).strip()
    manifest = root / "backend/pyproject.toml"
    content = manifest.read_text()
    manifest.write_text(
        content[: content.index("dev-mode-dirs")] + 'dev-mode-dirs = [".venv"]\n'
    )
    before = package_slim.canonical_source_identity(root)
    state.write_text("VALUE = 2\n")
    assert package_slim.canonical_source_identity(root) == before

    assert gate.get_bounded_owner_proofs(root, base, paths, evidence)["routes"] == []


@pytest.mark.parametrize("kind", ("api", "frontend"))
@pytest.mark.parametrize("stale", ("row", "packet"))
def test_bounded_owner_fallback_cannot_admit_stale_source_observations(
    bounded_owner_repository: tuple[Path, str], kind: str, stale: str
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, kind)
    source = next(
        row for row in evidence["observations"] if row["state"] == "UNAVAILABLE"
    )
    assert source["changed_path"] in {
        path for route in proof["routes"] for path in route["paths"]
    }
    if stale == "row":
        source["stale"] = True
    else:
        source["changed_set"] = {"freshness": "stale"}

    observed = gate.get_bounded_owner_proofs(root, base, paths, evidence)

    assert source["changed_path"] not in {
        path for route in observed["routes"] for path in route["paths"]
    }


def test_bounded_owner_qualified_component_inputs_do_not_gain_a_browser_fallback(
    bounded_owner_repository: tuple[Path, str],
) -> None:
    root, base = bounded_owner_repository
    paths, evidence, proof = _bounded_owner_case(root, base, "frontend")
    assert proof["routes"]
    component = next(
        row
        for row in evidence["observations"]
        if row["changed_path"].endswith(".test.tsx")
    )
    browser = next(
        row
        for row in evidence["observations"]
        if row["changed_path"].startswith("frontend/e2e/")
    )
    for row in evidence["observations"]:
        if row["state"] == "UNAVAILABLE":
            row.update(
                state="LINKED",
                nominations=copy.deepcopy(
                    component["nominations"] + browser["nominations"]
                ),
            )
    paths.remove(browser["changed_path"])
    evidence["observations"].remove(browser)
    original = copy.deepcopy(evidence)

    observed = gate.get_bounded_owner_proofs(root, base, paths, evidence)

    assert observed["routes"] == []
    assert evidence == original
