"""Independent, inert validation of the admitted merge proof.

The fast workflow transports this file from its immutable base revision. It
does not import the candidate's selector or accept author-supplied skip hints.
It can be installed before the fast workflow without changing an existing gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from importlib import metadata
from inspect import getsourcefile
from pathlib import Path
from typing import Any

VERIFICATION_POLICY_TESTS = (
    "scripts/tests/test_verification_gate_control.py",
    "scripts/tests/test_development_verification.py",
)
REAL_LIFECYCLE_QA_PATHS = (
    "docs/development/REAL_LIFECYCLE_QA.md",
    "docs/development/REAL_LIFECYCLE_QA.yml",
)
REAL_LIFECYCLE_QA_TEST = "scripts/tests/test_real_lifecycle_qa_contract.py"

_MAX_PUBLIC_DOCUMENTATION_ASSET_BYTES = 1024 * 1024
DOCUMENTATION_ASSET_OBLIGATION = "bounded documentation-asset policy"
DOCUMENTATION_PROOF_TEST = "scripts/tests/test_documentation_authority_contract.py"
DOCUMENTATION_PROOF_OBLIGATION = "bounded documentation proof"
HASHMARKS_UPGRADE_TESTS = (
    "backend/tests/unit/entrypoints/test_application_hashmarks_requirement.py",
    "backend/tests/unit/integrations/test_hashmarks_same_version_rebase.py",
    "backend/tests/unit/integrations/test_hashmarks_downstream_interoperability.py::test_native_collection_handoff_preserves_exact_selectors_and_missing_revision_claims",
    "scripts/tests/test_dependency_minimization.py::test_hashmarks_upgrade_is_normal_uv_dependency_without_oh_goon_release_client",
)
HASHMARKS_UPGRADE_OBLIGATION = "mandatory Hashmarks upgrade proof"
STATIC_PROJECT_OBLIGATION = "native type-only static-project proof"
STATIC_PROJECT_OWNERS = frozenset(
    {
        "Frontend typecheck",
        "Frontend lint",
        "Frontend architecture",
        "Frontend i18n:check",
        "Generated contracts",
    }
)


def static_project_candidates(paths: Sequence[str]) -> tuple[str, ...]:
    """The existing authored frontend project; filenames alone prove nothing."""
    return tuple(
        path
        for path in paths
        if path.startswith("frontend/src/") and Path(path).suffix in {".ts", ".tsx"}
    )


def _source_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and not Path(value).is_absolute()
        and ".." not in Path(value).parts
        and Path(value).as_posix() == value
        and "\n" not in value
    )


def admitted_static_projects(
    paths: Sequence[str], evidence: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    """Retain positive complete native syntax/project facts, never runtime coverage."""
    packets = evidence.get("static_project_evidence")
    if not isinstance(packets, list):
        return {}
    accepted: dict[str, Mapping[str, Any]] = {}
    for packet in packets:
        if not isinstance(packet, Mapping):
            return {}
        projected = _admitted_static_project_packet(paths, packet)
        if accepted.keys() & projected.keys():
            raise ValueError("Producer static-project packets duplicate a changed path")
        accepted.update(projected)
    return accepted


def _admitted_static_project_packet(
    paths: Sequence[str], packet: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    if (
        not isinstance(packet, Mapping)
        or packet.get("schema") != "hashmarks.changed-set-verification.v1"
    ):
        return {}
    if packet.get("freshness") not in {"current", "unknown"}:
        return {}
    candidates = set(static_project_candidates(paths))
    accepted: dict[str, Mapping[str, Any]] = {}
    changes = packet.get("changes")
    if not isinstance(changes, list):
        return {}
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, Mapping) or change.get("path") not in candidates:
            continue
        path = change["path"]
        if path in seen:
            raise ValueError("Producer static-project evidence duplicates a changed path")
        seen.add(path)
        classification = change.get("classification")
        if not isinstance(classification, Mapping):
            continue
        fact = classification.get("static_project")
        ledger = classification.get("supporting_source_members")
        if not isinstance(fact, Mapping) or not isinstance(ledger, list):
            continue
        member, parser = fact.get("source_member"), fact.get("parser")
        compilers, projects = fact.get("compilers"), fact.get("projects")
        configuration = fact.get("configuration_inputs")
        compiler = fact.get("compiler")
        if (
            classification.get("path") != path
            or classification.get("kind") != "source"
            or fact.get("kind") != "type-only-static-project"
            or fact.get("qualification") != "qualified"
            or fact.get("syntax_completeness") != "complete"
            or fact.get("value_syntax") != "none-observed-within-complete-member"
            or fact.get("runtime_execution") != "not-established"
            or fact.get("negative_evidence_admissible") is not False
            or not isinstance(fact.get("evidence_identity"), str)
            or not fact["evidence_identity"]
            or not isinstance(member, Mapping)
            or not _present_asset_member(member)
            or member.get("path") != path
            or classification.get("source_member_revision") != member.get("member_revision")
            or member not in ledger
            or not all(_present_asset_member(row) for row in ledger)
            or not isinstance(parser, Mapping)
            or not all(
                isinstance(parser.get(key), str) and parser[key] for key in ("grammar", "signature")
            )
            or not isinstance(parser.get("capability"), Mapping)
            or parser["capability"].get("available") is not True
            or not all(
                isinstance(parser["capability"].get(key), str) and parser["capability"][key]
                for key in ("name", "version")
            )
            or not isinstance(compiler, str)
            or not compiler.startswith("typescript-resolver:")
            or not compiler.endswith(":canonical-syntax.v2")
            or not isinstance(compilers, list)
            or not compilers
            or any(
                not isinstance(row, Mapping)
                or not _source_path(row.get("module"))
                or not str(row["module"]).startswith("frontend/node_modules/")
                or not isinstance(row.get("version"), str)
                or not row["version"]
                or row.get("scope") != "selected-compiler-entry-file-only"
                or not isinstance(row.get("entry_file_sha256"), str)
                or len(row["entry_file_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef" for character in row["entry_file_sha256"]
                )
                for row in compilers
            )
            or not isinstance(projects, list)
            or not projects
            or any(
                not isinstance(row, Mapping)
                or row.get("root") != "frontend"
                or not _source_path(row.get("config"))
                or not str(row["config"]).startswith("frontend/")
                for row in projects
            )
            or not isinstance(configuration, list)
            or not configuration
            or any(not _present_asset_member(row) or row not in ledger for row in configuration)
            or any(not _source_path(row.get("path")) for row in ledger)
            or not {row["config"] for row in projects} <= {row["path"] for row in configuration}
        ):
            continue
        accepted[path] = {"static_project": fact, "supporting_source_members": ledger}
    return accepted


def _independent_static_projects(
    root: Path,
    paths: Sequence[str],
    evidence: Mapping[str, Any],
    *,
    trusted_root: Path,
    source_identity: str,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any] | None]:
    from oh_goon.integrations.hashmarks import observe_repository_assets

    accepted = admitted_static_projects(paths, evidence)
    if not accepted:
        return {}, None
    _static_project_preparation_inputs(root, trusted_root)
    context = static_project_context(root, source_identity, accepted)
    observed = observe_repository_assets(root, tuple(sorted(accepted)))
    trusted = admitted_static_projects(paths, {"static_project_evidence": [observed]})
    if accepted != trusted:
        raise ValueError("Static-project evidence differs from trusted native source observation")
    _static_project_preparation_inputs(root, trusted_root)
    if context != static_project_context(root, source_identity, trusted):
        raise ValueError("Static-project native context changed during trusted observation")
    return accepted, context


def _static_project_tool_context(root: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """Observe native selections under the exact receiving process environment."""
    from oh_goon.engine.command import run_command

    tools: dict[str, Any] = {}
    for name in ("node", "npm"):
        selected = shutil.which(name, path=env.get("PATH", os.defpath))
        if selected is None:
            raise ValueError("Static-project native toolchain is unavailable")
        entry = Path(selected).resolve(strict=True)
        if not entry.is_file() or not 0 < entry.stat().st_size <= 256 * 1024 * 1024:
            raise ValueError("Static-project native executable is unbounded")
        with entry.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        try:
            version = run_command(
                selected,
                "--version",
                cwd=root / "frontend",
                env=dict(env),
                timeout=20,
                max_capture_bytes_per_stream=4096,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Static-project native tool observation timed out") from exc
        if (
            version.returncode
            or version.stdout_truncated
            or version.supervisor.final_process_group_quiescent is not True
        ):
            raise ValueError("Static-project native tool observation is incomplete")
        tools[name] = {"version": version.stdout.strip(), "entry_file_sha256": digest}
    return {
        "tools": tools,
        "environment": {
            key: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for key, value in sorted(env.items())
            if key in {"NODE_OPTIONS", "NODE_PATH"} or key.startswith("TS_NODE_")
        },
    }


def static_project_context(
    root: Path, source_identity: str, projects: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Bind existing static owner execution to its actual native tools and inputs."""
    native = _static_project_tool_context(root, dict(os.environ))
    for projection in projects.values():
        for compiler in projection["static_project"]["compilers"]:
            path = root / compiler["module"]
            resolved = path.resolve(strict=True)
            if (
                not resolved.is_relative_to(root / "frontend/node_modules")
                or not resolved.is_file()
            ):
                raise ValueError("Static-project selected compiler escaped its native installation")
            with resolved.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != compiler["entry_file_sha256"]:
                raise ValueError("Static-project selected compiler changed")
    return {
        "source_identity": source_identity,
        "project_root": "frontend",
        "projects": dict(projects),
        **native,
    }


def _static_project_preparation_inputs(root: Path, trusted_root: Path) -> dict[str, str]:
    """Admit fixed source inputs before native npm effects, without resolving them."""
    project = root / "frontend"
    if project.is_symlink() or project.resolve(strict=True) != project or not project.is_dir():
        raise ValueError("Static-project frontend root is unsafe")
    inputs: dict[str, str] = {}
    documents: dict[str, Any] = {}
    for name in ("package.json", "package-lock.json", ".npmrc"):
        path = project / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= 8 * 1024 * 1024
        ):
            raise ValueError("Static-project native package inputs are unsafe or unbounded")
        with path.open("rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Static-project native package input grew beyond its bound")
        inputs[name] = hashlib.sha256(raw).hexdigest()
        if raw != (trusted_root / "frontend" / name).read_bytes():
            raise ValueError("Static-project toolchain inputs differ from reviewed base authority")
        if name == ".npmrc":
            continue
        else:
            documents[name] = json.loads(raw)
    package, lock = documents["package.json"], documents["package-lock.json"]
    if (
        not isinstance(package, Mapping)
        or package.get("name") != "oh-goon-frontend"
        or package.get("private") is not True
        or package.get("type") != "module"
        or not isinstance(lock, Mapping)
        or lock.get("lockfileVersion") != 3
        or not isinstance(lock.get("packages"), Mapping)
        or not isinstance(lock["packages"].get(""), Mapping)
    ):
        raise ValueError("Static-project native frontend package shape is unsupported")
    return inputs


def static_project_capability_requested(
    root: Path, trusted_root: Path, plan: Mapping[str, Any], paths: Sequence[str]
) -> bool:
    projects = admitted_static_projects(paths, plan.get("evidence", {}))
    if STATIC_PROJECT_OBLIGATION not in plan.get("obligations", []):
        return False
    if not projects or "frontend" not in plan.get("static", []):
        raise ValueError("Static-project preparation request has no retained native obligation")
    _static_project_preparation_inputs(root, trusted_root)
    return True


def prepare_static_project(root: Path, trusted_root: Path, source_identity: str) -> None:
    """Prepare once with normal locked npm, separately from producer inspection."""
    from oh_goon.engine.command import run_command
    from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

    before = _static_project_preparation_inputs(root, trusted_root)
    if canonical_source_identity(root) != source_identity:
        raise ValueError("Static-project source changed before native preparation")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.lower().startswith("npm_config_") and key not in {"NODE_OPTIONS", "NODE_PATH"}
    }
    native_context = _static_project_tool_context(root, env)
    with tempfile.TemporaryDirectory(prefix="oh-goon-merge-npm-") as temporary:
        user_config, global_config = (
            Path(temporary) / name for name in ("user.npmrc", "global.npmrc")
        )
        user_config.write_text("")
        global_config.write_text("")
        result = run_command(
            "npm",
            "--prefix",
            str(root / "frontend"),
            "--workspaces=false",
            "ci",
            "--prefer-offline",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            f"--userconfig={user_config}",
            f"--globalconfig={global_config}",
            cwd=root / "frontend",
            env=env,
            timeout=180,
            max_capture_bytes_per_stream=64 * 1024,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
    if result.returncode or result.supervisor.final_process_group_quiescent is not True:
        raise ValueError("Static-project native preparation did not complete")
    if (
        before != _static_project_preparation_inputs(root, trusted_root)
        or canonical_source_identity(root) != source_identity
    ):
        raise ValueError("Static-project source or package inputs changed during preparation")
    if native_context != _static_project_tool_context(root, env):
        raise ValueError("Static-project native tool context changed during preparation")


VERIFICATION_SUPPORT_OBLIGATION = "bounded verification-support proof"


def bounded_verification_support_targets(observation: Mapping[str, Any]) -> frozenset[str]:
    """Bind positive native support relationships without claiming consumer completeness."""
    source = observation.get("changed_path")
    packet = observation.get("changed_set", {})
    changes = packet.get("changes", [])
    if (
        not isinstance(source, str)
        or observation.get("state") != "LINKED"
        or observation.get("stale") is True
        or packet.get("changed_paths") != [source]
        or packet.get("freshness") not in {"current", "unknown"}
        or packet.get("negative_evidence_admissible") is not False
        or len(changes) != 1
    ):
        return frozenset()
    change = changes[0]
    role = change.get("classification", {})
    member = change.get("source", {}).get("member", {})
    if (
        change.get("path") != source
        or role.get("kind") != "verification-support"
        or member.get("path") != source
        or member.get("state") != "known-present"
        or not member.get("member_revision")
        or role.get("source_member_revision") != member["member_revision"]
        or observation.get("qualification") != change.get("consumer_scope")
        or change.get("consumer_scope", {}).get("negative_evidence_admissible") is not False
    ):
        return frozenset()
    targets: set[str] = set()
    for projected in observation.get("nominations", []):
        nomination = projected.get("nomination", {})
        target = nomination.get("verification_target", {}).get("path")
        if nomination.get("available") is not True:
            continue
        if (
            not isinstance(target, str)
            or not target
            or projected.get("workspace_prefix") != ""
            or nomination.get("runner_evidence") not in {"pytest", "vitest"}
            or not nomination.get("source_member_revision")
            or not any(
                row.get("path") == target and row.get("nomination") == nomination
                for row in packet.get("targets", [])
            )
        ):
            return frozenset()
        relationships = projected.get("relationships", [])
        if not relationships or any(
            relation not in change.get("relationships", [])
            or relation.get("source") != source
            or relation.get("target") != target
            or relation.get("relation_kind")
            not in {"direct-reference", "bounded-indirect-reference"}
            or relation.get("reference_evidence", {}).get(
                "direct_reference"
                if relation.get("relation_kind") == "direct-reference"
                else "indirect_reference"
            )
            is not True
            or not any(
                row.get("path") == path
                and row.get("state") == "known-present"
                and row.get("member_revision") == revision
                for row in relation.get("supporting_source_members", [])
            )
            for relation in relationships
            for path, revision in (
                (source, member["member_revision"]),
                (target, nomination["source_member_revision"]),
            )
        ):
            return frozenset()
        targets.add(target)
    return frozenset(targets)


def require_verification_support_proof(
    plan: Mapping[str, Any], evidence: Mapping[str, Any], executed_files: set[str]
) -> None:
    bounded = {
        row["changed_path"]: bounded_verification_support_targets(row)
        for row in evidence.get("observations", [])
        for change in row.get("changed_set", {}).get("changes", [])
        if change.get("classification", {}).get("kind") == "verification-support"
        and row.get("qualification", {}).get("completeness") != "complete"
        and row.get("changed_path") not in plan.get("removed_paths", [])
    }
    if not bounded:
        if "verification_support_admission" in evidence:
            raise ValueError("Bounded verification-support proof names another source")
        return
    expected = {
        "authority": "oh-goon-execution-policy",
        "scope": "positive-source-qualified-consumers",
        "consumer_coverage": "not-claimed",
        "paths": sorted(bounded),
    }
    if (
        evidence.get("verification_support_admission") != expected
        or VERIFICATION_SUPPORT_OBLIGATION not in plan.get("obligations", [])
        or any(not targets.intersection(executed_files) for targets in bounded.values())
    ):
        raise ValueError("Bounded verification-support proof lacks an executed qualified consumer")


def is_verification_policy_path(path: str) -> bool:
    """The existing contributor, verification and result-authority boundaries."""
    return (
        path in REAL_LIFECYCLE_QA_PATHS
        or path.startswith((".github/workflows/", "backend/src/oh_goon/repository_tooling/"))
        or Path(path).name == "AGENTS.md"
        or path
        in {
            "README.md",
            "Makefile",
            "backend/pyproject.toml",
            ".pre-commit-config.yaml",
            "scripts/development_check.sh",
            "scripts/development_test.sh",
            "scripts/development_test_resources.sh",
            "scripts/application_verify.sh",
            "scripts/run_logged.sh",
            "scripts/ci_native_runtime.sh",
            "docs/CI_CD_CONTRACT.md",
            "docs/DEVELOPMENT.md",
            "docs/development/CHATGPT_ITERATION.md",
            "docs/development/TEST_QUALITY.md",
            "docs/development/TEST_SETUP_ISOLATION_QA.md",
            "docs/development/HASHMARKS_EXECUTION_LAYER.md",
            "docs/development/MAKE_TARGET_SURFACE.yml",
            "backend/README.md",
            "backend/src/oh_goon/README.md",
            "scripts/README.md",
            "frontend/README.md",
            "frontend/src/README.md",
        }
        or path.startswith("backend/src/oh_goon/integrations/hashmarks/")
    )


def required_policy_tests(paths: Sequence[str]) -> tuple[str, ...]:
    """Keep qualification-ledger proof conditional on its governing source."""
    if not any(map(is_verification_policy_path, paths)):
        return ()
    return VERIFICATION_POLICY_TESTS + (
        (REAL_LIFECYCLE_QA_TEST,) if any(path in REAL_LIFECYCLE_QA_PATHS for path in paths) else ()
    )


def required_policy_obligations(paths: Sequence[str]) -> tuple[str, ...]:
    """Name the protection independently re-derived from actual changed paths."""
    if not any(map(is_verification_policy_path, paths)):
        return ()
    return ("verification-policy protection",) + (
        ("real-lifecycle QA protection",)
        if any(path in REAL_LIFECYCLE_QA_PATHS for path in paths)
        else ()
    )


def documentation_asset_candidates(paths: Sequence[str]) -> tuple[str, ...]:
    """Name this repository's declared documentation-asset authoring surface."""
    return tuple(
        path
        for path in paths
        if path.startswith("docs/assets/") and not is_verification_policy_path(path)
    )


def _documentation_asset_consumer(consumer: object) -> bool:
    if not isinstance(consumer, Mapping):
        return False
    domains = consumer.get("domains")
    member = consumer.get("source_member")
    return (
        isinstance(domains, list)
        and "doc" in domains
        and all(domain in {"doc", "architecture"} for domain in domains)
        and consumer.get("qualification") == "syntax-qualified"
        and consumer.get("relation_kind") in {"markdown-inline-image", "markdown-inline-link"}
        and _present_asset_member(member)
        and isinstance(member, Mapping)
        and member.get("path") == consumer.get("path")
    )


def _present_asset_member(member: object) -> bool:
    return (
        isinstance(member, Mapping)
        and member.get("state") == "known-present"
        and isinstance(member.get("member_revision"), str)
        and bool(member.get("member_revision"))
    )


def _bounded_documentation_asset(path: str, asset: Mapping[str, Any]) -> bool:
    """Apply project policy without promoting bounded observations to absence.

    The producer's global consumer scope deliberately remains unknown. We accept
    this declared asset surface with positive documentation uses and no observed
    conflicting or unqualified use; this is not repository-negative evidence.
    """
    content = asset.get("content", {})
    scope = asset.get("consumer_scope", {})
    consumers = asset.get("consumers")
    if not isinstance(content, Mapping) or not isinstance(scope, Mapping):
        return False
    member = content.get("source_member", {})
    supporting = asset.get("supporting_source_members")
    size = content.get("byte_count")
    media = content.get("media_type")
    return (
        asset.get("schema") == "hashmarks.repository-asset.v1"
        and asset.get("path_domains") == ["doc"]
        and content.get("schema") == "hashmarks.source-content.v1"
        and content.get("state") == "observed"
        and content.get("kind") == "binary"
        and type(size) is int
        and 0 < size <= _MAX_PUBLIC_DOCUMENTATION_ASSET_BYTES
        and isinstance(media, str)
        and media.startswith("image/")
        and content.get("media_type_basis") == "signature-only"
        and content.get("integrity") == "not-validated"
        and content.get("lfs") is None
        and isinstance(member, Mapping)
        and member.get("path") == path
        and _present_asset_member(member)
        and isinstance(supporting, list)
        and member in supporting
        and all(_present_asset_member(value) for value in supporting)
        and scope.get("completeness") == "unknown"
        and scope.get("source_coverage") == "complete"
        and scope.get("truncated") is False
        and scope.get("negative_evidence_admissible") is False
        and isinstance(consumers, list)
        and bool(consumers)
        and all(_documentation_asset_consumer(consumer) for consumer in consumers)
    )


def admitted_documentation_assets(
    paths: Sequence[str], evidence: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    """Project only the unchanged producer asset facts accepted by local policy."""
    packet = evidence.get("asset_evidence")
    if not isinstance(packet, Mapping):
        return {}
    # Repository-wide freshness and verifier truncation are separate from the
    # asset's source-read scope. Unknown global freshness is retained; the caller
    # binds the exact candidate before/after admission and the gate reobserves it.
    if packet.get("schema") != "hashmarks.changed-set-verification.v1" or packet.get(
        "freshness"
    ) not in {"current", "unknown"}:
        return {}
    candidates = set(documentation_asset_candidates(paths))
    changes = packet.get("changes")
    if not isinstance(changes, list):
        return {}
    accepted: dict[str, Mapping[str, Any]] = {}
    for change in changes:
        if not isinstance(change, Mapping):
            continue
        path, asset = change.get("path"), change.get("asset")
        if isinstance(path, str) and path in candidates and isinstance(asset, Mapping):
            if path in accepted:
                raise ValueError("Producer asset evidence duplicates a changed path")
            if _bounded_documentation_asset(path, asset):
                accepted[path] = asset
    return accepted


def _independent_documentation_assets(
    root: Path, paths: Sequence[str], evidence: Mapping[str, Any]
) -> set[str]:
    from oh_goon.integrations.hashmarks import observe_repository_assets

    accepted = admitted_documentation_assets(paths, evidence)
    if not accepted:
        return set()
    observed = observe_repository_assets(root, tuple(sorted(accepted)))
    trusted = admitted_documentation_assets(paths, {"asset_evidence": observed})
    if accepted != trusted:
        raise ValueError("Documentation asset evidence differs from trusted source observation")
    return set(accepted)


def non_executable_documentation(_root: Path, path: str) -> bool:
    return path == "LICENSE" or bool(documentation_proof_paths((path,)))


def documentation_proof_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """The existing authored-doc owner; machine inputs keep their own proof."""
    return tuple(
        path
        for path in paths
        if not is_verification_policy_path(path)
        and (
            path == "CONTRIBUTING.md"
            or (
                path.startswith("docs/")
                and not path.startswith("docs/certification/")
                and Path(path).suffix == ".md"
            )
        )
    )


def hashmarks_upgrade_intent(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Compare native TOML intent without resolving versions or lock contents."""
    previous = _without_hashmarks_requirement(before)
    current = _without_hashmarks_requirement(after)
    return (
        previous is not None
        and current is not None
        and previous[1] != current[1]
        and previous[0] == current[0]
    )


def _without_hashmarks_requirement(
    document: Mapping[str, Any],
) -> tuple[dict[str, Any], str] | None:
    project = document.get("project")
    if not isinstance(project, Mapping):
        return None
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    selected = [
        (index, value)
        for index, value in enumerate(dependencies)
        if isinstance(value, str)
        and (
            value.startswith("hashmarks @ ")
            or (value.startswith("hashmarks[") and value.partition("]")[2].startswith(" @ "))
        )
    ]
    if len(selected) != 1:
        return None
    index, requirement = selected[0]
    remainder = dict(document)
    remainder["project"] = {
        **project,
        "dependencies": dependencies[:index] + dependencies[index + 1 :],
    }
    return remainder, requirement


def _independent_hashmarks_upgrade(
    root: Path | None,
    base: str,
    paths: Sequence[str],
    evidence: Mapping[str, Any],
    *,
    observed_hashmarks_upgrade: bool | None = None,
) -> bool:
    if "hashmarks_upgrade" not in evidence:
        return False
    claimed = evidence["hashmarks_upgrade"]
    if claimed != {
        "authority": "oh-goon-execution-policy",
        "scope": "mandatory-hashmarks-upgrade",
        "consumer_coverage": "not-claimed",
    }:
        raise ValueError("Hashmarks upgrade policy is invalid or overclaimed")
    if root is None or "backend/pyproject.toml" not in paths:
        raise ValueError("Hashmarks upgrade requires candidate and native base intent")
    if observed_hashmarks_upgrade is None:
        from oh_goon.engine.command import run_command

        observed = run_command(
            "git",
            "-C",
            str(root),
            "show",
            "--no-ext-diff",
            "--no-textconv",
            f"{base}:backend/pyproject.toml",
            cwd=root,
            timeout=20,
            max_capture_bytes_per_stream=8 * 1024 * 1024,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
        if observed.returncode or observed.stdout_truncated:
            raise ValueError("Native base Hashmarks intent is unavailable")
        before = tomllib.loads(observed.stdout)
        after = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
        observed_hashmarks_upgrade = hashmarks_upgrade_intent(before, after)
    if not observed_hashmarks_upgrade:
        raise ValueError("Hashmarks upgrade changes unrelated dependency or project intent")
    return True


def _require_documentation_proof(
    paths: Sequence[str], plan: Mapping[str, Any], evidence: Mapping[str, Any]
) -> set[str]:
    documentation = documentation_proof_paths(paths)
    expected = {
        "authority": "oh-goon-execution-policy",
        "paths": list(documentation),
        "coverage": "not-claimed",
    }
    if documentation:
        if (
            evidence.get("documentation_policy") != expected
            or DOCUMENTATION_PROOF_OBLIGATION not in plan.get("obligations", [])
            or DOCUMENTATION_PROOF_TEST not in plan.get("pytest", [])
        ):
            raise ValueError("Bounded documentation proof was omitted or overclaimed")
    elif "documentation_policy" in evidence:
        raise ValueError("Documentation proof names another authoring surface")
    return set(documentation)


def behavioral_pytest_targets(
    pytest: Sequence[str], evidence: Mapping[str, Any], current_paths: set[str]
) -> set[str]:
    """Retain an automatic doc check's independently nominated behavioral role."""
    targets = set(pytest)
    if "documentation_policy" not in evidence:
        return targets
    for observation in evidence.get("observations", []):
        if (
            observation.get("changed_path") not in current_paths
            or observation.get("state") not in {"LINKED", "CHANGED_TEST"}
            or observation.get("stale") is True
        ):
            continue
        for projected in observation.get("nominations", []):
            nomination = projected.get("nomination", {})
            target = projected.get("workspace_prefix", "") + nomination.get(
                "verification_target", {}
            ).get("path", "")
            if (
                nomination.get("available") is True
                and target.split("::", 1)[0] == DOCUMENTATION_PROOF_TEST
            ):
                return targets
    return {target for target in targets if target.split("::", 1)[0] != DOCUMENTATION_PROOF_TEST}


def _require_hashmarks_smoke_membership(native: Mapping[str, Any]) -> None:
    passed = native.get("membership_receipt", {}).get("passed", [])
    for selector in HASHMARKS_UPGRADE_TESTS:
        if "::" not in selector:
            satisfied = any(member.split("::", 1)[0] == selector for member in passed)
        else:
            satisfied = any(
                member == selector or member.startswith(selector + "[") for member in passed
            )
        if not satisfied:
            raise ValueError("Mandatory Hashmarks smoke has no native execution proof")


def is_generated_contract_path(path: str) -> bool:
    """The existing one-way OpenAPI/frontend generator owns these artifacts."""
    return path == "generated/openapi/openapi.json" or path.startswith("generated/frontend/")


def required_static_obligations(paths: Sequence[str]) -> set[str]:
    """Existing static/contract authorities, independent of test relevance."""
    obligations = {"architecture"}
    if any(path.endswith(".py") or path.startswith("backend/") for path in paths):
        obligations.add("python")
    if any(
        path.startswith(("frontend/", "generated/frontend/", "generated/openapi/"))
        for path in paths
    ):
        obligations.add("frontend")
    if any(
        path.startswith(
            (
                "backend/src/oh_goon/api/",
                "backend/src/oh_goon/core/models/",
                "backend/src/oh_goon/core/vocabulary/",
                "backend/src/oh_goon/repository_tooling/generation/",
                "generated/frontend/",
                "generated/openapi/",
            )
        )
        for path in paths
    ):
        obligations.update(("generated", "frontend"))
    if any(path in {"backend/pyproject.toml", "backend/uv.lock"} for path in paths):
        obligations.add("package")
    return obligations


def require_native_pytest_pass(
    native: Mapping[str, Any], *, expected_source: str | None = None
) -> set[str]:
    """Admit only the original owner's effective final categories, never attempt history."""
    receipt = native.get("membership_receipt", {})
    selected, passed = receipt.get("selected", []), receipt.get("passed", [])
    if any(
        not isinstance(values, list)
        or any(not isinstance(node, str) or not node for node in values)
        for values in (selected, passed)
    ):
        raise ValueError("Native membership has an invalid shape")
    if (
        native.get("status") != "PASS"
        or (expected_source is not None and native.get("source_identity") != expected_source)
        or not selected
        or len(selected) != len(set(selected))
        or sorted(selected) != sorted(passed)
        or receipt.get("terminal_success") is not True
        or any(receipt.get(field) for field in ("missing", "duplicates", "extras", "resumed"))
    ):
        raise ValueError("Native proof is stale, skipped, failed or incomplete")
    effective = receipt.get("effective_pytest_outcomes")
    if (
        not isinstance(effective, list)
        or len(effective) != len(selected)
        or sorted(row.get("member", "") for row in effective) != sorted(selected)
    ):
        raise ValueError("Effective native outcomes are missing or mismatched")
    for record in effective:
        outcomes = record.get("pytest_outcomes")
        if (
            record.get("status") != "PASSED"
            or not isinstance(outcomes, list)
            or not outcomes
            or any(
                not isinstance(row, Mapping)
                or set(row) != {"nodeid", "outcome"}
                or not isinstance(row.get("nodeid"), str)
                or not row["nodeid"]
                or row.get("outcome") != "PASSED"
                for row in outcomes
            )
            or len({row["nodeid"] for row in outcomes}) != len(outcomes)
        ):
            raise ValueError("Required native category is skipped, xfailed, xpassed or incomplete")
    collection = native.get("native_collection", {})
    discovered, deselected = collection.get("discovered", []), collection.get("deselected", [])
    if any(
        not isinstance(values, list)
        or any(not isinstance(node, str) or not node for node in values)
        for values in (discovered, deselected)
    ):
        raise ValueError("Native collection membership has an invalid shape")
    if (
        len(discovered) != len(set(discovered))
        or set(discovered) - set(deselected) != set(selected)
        or not set(deselected) <= set(discovered)
    ):
        raise ValueError("Native collection and selected membership differ")
    return {node.split("::", 1)[0] for node in selected}


def native_changed_paths(name_status: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Project changed and removed members from one native Git diff observation."""
    tokens = name_status.split("\0")
    changed: set[str] = set()
    removed: set[str] = set()
    cursor = 0
    while cursor < len(tokens) and tokens[cursor]:
        status = tokens[cursor]
        count = 2 if status[0] in {"R", "C"} else 1
        paths = tokens[cursor + 1 : cursor + 1 + count]
        if len(paths) != count or any(not path for path in paths):
            raise ValueError("Git returned incomplete changed-path evidence")
        changed.update(paths)
        if status[0] in {"D", "R"}:
            removed.add(paths[0])
        cursor += count + 1
    return tuple(sorted(changed)), tuple(sorted(removed))


def validate_merge_proof(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    candidate: str,
    base: str,
    changed_paths: Sequence[str],
    control_base: str | None,
    root: Path | None = None,
    trusted_root: Path | None = None,
    removed_paths: Sequence[str] = (),
    observed_hashmarks_upgrade: bool | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    if plan.get("schema_version") != "oh-goon.development-verification/v1":
        raise ValueError("Verification plan schema is unsupported")
    if plan.get("candidate") != candidate or plan.get("base") != base:
        raise ValueError("Merge proof belongs to another candidate/base")
    if sorted(plan.get("changed_paths", [])) != sorted(changed_paths):
        raise ValueError("Merge proof omitted or added changed inputs")
    if result.get("plan") != plan or result.get("status") != "MERGE_PROOF_READY":
        raise ValueError("Missing, failed, cancelled or partial merge proof")
    if plan.get("unverified"):
        raise ValueError("Selection remains unverified")
    if plan.get("behavioral_status") not in {"REQUIRED", "NOT_APPLICABLE"} or plan.get(
        "policy_status"
    ) not in {"ORDINARY", "REVIEW_REQUIRED"}:
        raise ValueError("Verification admission state is unknown")
    if not required_static_obligations(changed_paths) <= set(plan.get("static", [])):
        raise ValueError("Applicable static or contract authority was omitted")
    policy_tests = required_policy_tests(changed_paths)
    policy_change = bool(policy_tests)
    if (policy_change or plan.get("policy_status") == "REVIEW_REQUIRED") and control_base != base:
        raise ValueError("CUTOVER_UNVERIFIED: policy changes require trusted-base controls")
    if policy_change and (
        not set(required_policy_obligations(changed_paths)) <= set(plan.get("obligations", []))
        or not set(policy_tests) <= set(plan.get("pytest", []))
    ):
        raise ValueError("Required independent verification-policy protection was omitted")
    evidence = plan.get("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError("Producer evidence has an invalid shape")
    documentation = _require_documentation_proof(changed_paths, plan, evidence)
    plain_docs = documentation | ({"LICENSE"} if "LICENSE" in changed_paths else set())
    hashmarks_upgrade = _independent_hashmarks_upgrade(
        root,
        base,
        changed_paths,
        evidence,
        observed_hashmarks_upgrade=observed_hashmarks_upgrade,
    )
    if hashmarks_upgrade and (
        HASHMARKS_UPGRADE_OBLIGATION not in plan.get("obligations", [])
        or not set(HASHMARKS_UPGRADE_TESTS) <= set(plan.get("pytest", []))
        or not {"python", "package"} <= set(plan.get("static", []))
    ):
        raise ValueError("Mandatory Hashmarks upgrade proof was omitted")
    recorded_removals = plan.get("removed_paths", [])
    if (
        not isinstance(recorded_removals, list)
        or any(not isinstance(path, str) for path in recorded_removals)
        or len(recorded_removals) != len(set(recorded_removals))
        or sorted(recorded_removals) != sorted(removed_paths)
        or not set(removed_paths) <= set(changed_paths)
    ):
        raise ValueError("Removed membership differs from independent native Git evidence")
    removed = set(removed_paths)
    assets = _independent_documentation_assets(root, changed_paths, evidence) if root else set()
    if assets and DOCUMENTATION_ASSET_OBLIGATION not in plan.get("obligations", []):
        raise ValueError("Bounded documentation-asset policy was not explicitly admitted")
    plain_docs.update(assets)
    if admitted_static_projects(changed_paths, evidence) and trusted_root is None:
        raise ValueError("Native static-project proof requires reviewed base toolchain authority")
    static_projects, static_context = (
        _independent_static_projects(
            root,
            changed_paths,
            evidence,
            trusted_root=trusted_root,
            source_identity=plan["source_identity"],
        )
        if root is not None and trusted_root is not None
        else ({}, None)
    )
    if static_projects and STATIC_PROJECT_OBLIGATION not in plan.get("obligations", []):
        raise ValueError("Native static-project obligation was omitted")
    if STATIC_PROJECT_OBLIGATION in plan.get("obligations", []) and not static_projects:
        raise ValueError("Native static-project source authority is unavailable")
    contract_only = any(map(is_generated_contract_path, changed_paths)) and all(
        is_generated_contract_path(path) or path in plain_docs for path in changed_paths
    )
    static_only = bool(static_projects) and all(
        path in plain_docs
        or path in static_projects
        or path in removed
        or is_generated_contract_path(path)
        for path in changed_paths
    )
    behavior_required = plan.get("behavioral_status") == "REQUIRED"
    if not behavior_required:
        for path in changed_paths:
            if path not in plain_docs:
                raise ValueError("Executable inputs cannot claim behavioral NOT_APPLICABLE")
    behavioral_pytest = behavioral_pytest_targets(
        plan.get("pytest", []), evidence, set(changed_paths) - plain_docs - removed
    )
    if (
        behavior_required
        and not (behavioral_pytest or plan.get("frontend"))
        and not (contract_only or static_only)
    ):
        raise ValueError("Executable changes have no behavioral proof")
    if (assets or (behavior_required and not contract_only)) and (
        not isinstance(evidence, Mapping)
        or not evidence.get("producer_implementation_identity")
        or evidence.get("owner_coverage") != "not-claimed"
    ):
        raise ValueError("Required producer evidence is missing or overclaimed")
    outcomes = result.get("outcomes")
    if (
        not isinstance(outcomes, list)
        or not outcomes
        or any(not isinstance(row, Mapping) or row.get("returncode") != 0 for row in outcomes)
    ):
        raise ValueError("Required owner is missing, skipped or failed")
    owners = [row.get("owner") for row in outcomes]
    expected: list[str] = []
    for name in plan.get("static", []):
        expected.extend(
            {
                "python": ["Ruff", "Format", "Types"],
                "frontend": [
                    f"Frontend {part}"
                    for part in ("typecheck", "lint", "architecture", "i18n:check")
                ],
                "architecture": ["Architecture"],
                "generated": ["Generated contracts"],
                "package": ["Native package"],
            }[name]
        )
    if plan.get("frontend"):
        expected.append("Frontend behavior")
    if plan.get("pytest"):
        expected.append("Native pytest")
    if sorted(owners) != sorted(expected):
        raise ValueError("Required check owners are missing, duplicated or extra")
    if static_projects:
        assert static_context is not None
        for row in outcomes:
            if row["owner"] in STATIC_PROJECT_OWNERS:
                command = (
                    ["make", "check-generated"]
                    if row["owner"] == "Generated contracts"
                    else ["npm", "run", row["owner"].removeprefix("Frontend ")]
                )
                native = row.get("native_execution", {})
                if (
                    row.get("static_project_context") != static_context
                    or row.get("status") not in {None, "PASSED"}
                    or native.get("argv") != command
                    or native.get("process_group_quiescent") is not True
                ):
                    raise ValueError(
                        "Static-project owner has no matching source/native context proof"
                    )
    executed_files: set[str] = set()
    executed_members: set[str] = set()
    for row in outcomes:
        if row["owner"] == "Frontend behavior":
            classifier = runpy.run_path(str(Path(__file__).with_name("frontend_test_results.py")))[
                "vitest_outcomes"
            ]
            native_frontend = row.get("native_report", {})
            complete, satisfied, _counts = classifier(native_frontend)
            if not complete or not satisfied or root is None:
                raise ValueError("Native frontend proof is skipped, failed or incomplete")
            producer_project = Path(row.get("native_project_root", ""))
            if not producer_project.is_absolute() or ".." in producer_project.parts:
                raise ValueError("Native frontend invocation root is unavailable")
            frontend_files: set[str] = set()
            for suite in native_frontend["testResults"]:
                suite_path = Path(suite["name"])
                suite_path = (
                    suite_path if suite_path.is_absolute() else producer_project / suite_path
                )
                if not suite_path.is_relative_to(producer_project):
                    raise ValueError("Native frontend proof names another workspace")
                frontend_files.add(
                    "frontend/" + suite_path.relative_to(producer_project).as_posix()
                )
            if set(plan.get("frontend", [])) != frontend_files:
                raise ValueError("Selected frontend proof is missing or has extra files")
            executed_files.update(frontend_files)
        if row["owner"] != "Native pytest":
            continue
        pytest_files = require_native_pytest_pass(
            row.get("native_report", {}), expected_source=plan.get("source_identity")
        )
        if documentation and DOCUMENTATION_PROOF_TEST not in pytest_files:
            raise ValueError("Bounded documentation check has no native execution proof")
        if hashmarks_upgrade:
            _require_hashmarks_smoke_membership(row.get("native_report", {}))
        if policy_change and not set(policy_tests) <= pytest_files:
            raise ValueError("Required verification-policy tests have no execution proof")
        if pytest_files != {path.split("::", 1)[0] for path in plan.get("pytest", [])}:
            raise ValueError("Admitted pytest files differ from native execution proof")
        executed_files.update(pytest_files)
        executed_members.update(row["native_report"]["membership_receipt"]["passed"])
    changed_tests = {
        observation["changed_path"]
        for observation in plan.get("evidence", {}).get("observations", [])
        if observation.get("state") == "CHANGED_TEST"
    }
    if not changed_tests - removed <= executed_files:
        raise ValueError("Changed tests have no meaningful execution proof")
    observations = {row.get("changed_path"): row for row in evidence.get("observations", [])}
    for source in changed_paths:
        # An absent member cannot execute. Git proves only its removal, never
        # absence of remaining consumers; surviving sources retain their proof.
        if source in removed:
            continue
        if is_generated_contract_path(source):
            continue
        if hashmarks_upgrade and source in {"backend/pyproject.toml", "backend/uv.lock"}:
            continue
        if is_verification_policy_path(source) and not source.endswith(".py"):
            continue
        if source in plain_docs:
            continue
        if source in static_projects:
            continue
        observation = observations.get(source, {})
        if observation.get("stale") is True or observation.get("state") not in {
            "LINKED",
            "CHANGED_TEST",
        }:
            raise ValueError("Source-qualified consumer evidence is unavailable or stale")
        nominated = {
            projected.get("workspace_prefix", "")
            + projected.get("nomination", {}).get("verification_target", {}).get("path", "")
            for projected in observation.get("nominations", [])
            if projected.get("nomination", {}).get("available") is True
        }
        if not nominated & executed_files:
            raise ValueError("Changed behavior has no executed qualified consumer proof")
    require_verification_support_proof(plan, evidence, executed_files)
    return frozenset(executed_files), frozenset(executed_members)


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        content = stream.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("Merge proof exceeds its transport bound")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Merge proof requires an object")
    return value


def _ordinary_collection_requests(
    root: Path, *, trusted_root: Path, environment: Path, env: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Resolve the existing native Make contexts once before child execution."""
    from types import MappingProxyType

    from oh_goon.core.file_digest import sha256_file
    from oh_goon.repository_tooling.development_verification import _MERGE_MARKER
    from oh_goon.repository_tooling.pytest_member_selection import native_collection_request
    from oh_goon.repository_tooling.pytest_process_environment import (
        PytestInvocation,
        isolated_pytest_environment,
    )

    collector = trusted_root / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
    config = root / "backend/pyproject.toml"
    config_identity = sha256_file(config)
    resolved_environment = dict(env)
    resolved_environment.pop("UV_PROJECT_ENVIRONMENT", None)
    resolved_environment = isolated_pytest_environment(resolved_environment, qualified=True)
    requests: list[dict[str, Any]] = []
    for name, cwd, paths in (
        ("backend", root / "backend", ()),
        ("tooling", root, (root / "scripts/tests",)),
        ("cross-owner", root, (root / "tests/integration",)),
    ):
        invocation = PytestInvocation(
            cwd=cwd,
            source_root=root,
            config_file=config,
            pytest_args=(),
            command_prefix=(str(environment / "bin/python"), "-I", str(collector)),
            environment=MappingProxyType(resolved_environment),
            qualified=True,
            config_identity=config_identity,
        )
        requests.append(
            {
                "name": name,
                "request": native_collection_request(
                    paths,
                    marker=_MERGE_MARKER,
                    invocation=invocation,
                    plugin_module="__main__",
                    expected_environment=environment,
                ),
            }
        )
    return requests


def _ordinary_candidate_collection(
    root: Path,
    *,
    trusted_root: Path,
    environment: Path,
    changed_paths: Sequence[str],
    source_identity: str,
    requests: Sequence[Mapping[str, Any]],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Observe existing ordinary pytest contexts once; never execute their tests."""
    from types import MappingProxyType

    from oh_goon.core.file_digest import sha256_file
    from oh_goon.repository_tooling.pytest_execution_membership import logical_membership_identity
    from oh_goon.repository_tooling.pytest_member_selection import (
        collect_native_pytest,
        native_collection_request,
    )
    from oh_goon.repository_tooling.pytest_process_environment import (
        PytestInvocation,
        isolated_pytest_environment,
    )
    from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

    for owner in (
        collect_native_pytest,
        native_collection_request,
        PytestInvocation,
        logical_membership_identity,
    ):
        filename = getsourcefile(owner)
        if filename is None or not Path(filename).resolve().is_relative_to(
            trusted_root / "backend/src"
        ):
            raise ValueError("Ordinary collection imported control code outside its trusted base")
    root = root.resolve(strict=True)
    collector = trusted_root / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
    collector_identity = sha256_file(collector)
    child_environment = dict(env)
    child_environment.pop("UV_PROJECT_ENVIRONMENT", None)
    child_environment = isolated_pytest_environment(child_environment, qualified=True)
    changed = set(changed_paths)
    contexts: list[dict[str, Any]] = []
    admitted: list[tuple[str, Mapping[str, Any], PytestInvocation, tuple[Path, ...]]] = []
    for resolved in requests:
        query = resolved["request"]
        invocation = PytestInvocation(
            cwd=Path(query["cwd"]),
            source_root=Path(query["source_root"]),
            config_file=Path(query["config_file"]),
            pytest_args=tuple(query["pytest_args"]),
            command_prefix=tuple(query["command_prefix"]),
            environment=MappingProxyType(child_environment),
            qualified=query["qualified"],
            config_identity=sha256_file(Path(query["config_file"])),
            resume_environment=tuple(query["resume_environment"]),
        )
        paths = tuple(Path(path) for path in query["paths"])
        verified_request = native_collection_request(
            paths,
            invocation=invocation,
            excludes=tuple(query["excludes"]),
            marker=query["marker"],
            plugin_module=query["plugin_module"],
            expected_environment=Path(query["expected_environment"]),
        )
        if (
            verified_request != query
            or invocation.source_root != root
            or invocation.command_prefix != (str(environment / "bin/python"), "-I", str(collector))
            or query["plugin_module"] != "__main__"
            or query["qualified"] is not True
            or query["expected_environment"] != str(environment)
        ):
            raise ValueError("Ordinary collection changed its resolved invocation authority")
        admitted.append((resolved["name"], query, invocation, paths))
    for name, query, invocation, paths in admitted:
        if (
            sha256_file(collector) != collector_identity
            or canonical_source_identity(root) != source_identity
        ):
            raise ValueError("Source changed before ordinary collection")
        native = collect_native_pytest(
            paths,
            marker=query["marker"],
            excludes=tuple(query["excludes"]),
            invocation=invocation,
            plugin_module=query["plugin_module"],
            expected_environment=environment,
        ).relative_to_source(cwd=invocation.cwd, source_root=root)
        if (
            sha256_file(collector) != collector_identity
            or canonical_source_identity(root) != source_identity
        ):
            raise ValueError("Source changed during ordinary collection")
        contexts.append(
            {
                "name": name,
                "request": dict(native.request or {}),
                "runtime": dict(native.runtime or {}),
                "projection_scope": "changed-source-files-after-complete-native-collection",
                "selected": [row for row in native.selected if row["file"] in changed],
                "deselected": [row for row in native.deselected if row["file"] in changed],
                "observed_selected_count": len(native.selected),
                "observed_deselected_count": len(native.deselected),
                "selected_membership_identity": logical_membership_identity(
                    row["selector"] for row in native.selected
                ),
                "deselected_membership_identity": logical_membership_identity(
                    row["selector"] for row in native.deselected
                ),
            }
        )
    return {
        "source_identity": source_identity,
        "changed_paths": sorted(changed),
        "environment": str(environment),
        "collector_source": str(collector),
        "collector_sha256": collector_identity,
        "contexts": contexts,
        "execution": "not-performed",
        "repository_negative_evidence": "not-claimed",
    }


def _require_ordinary_changed_test_execution(
    receipt: Mapping[str, Any],
    *,
    changed_paths: Sequence[str],
    source_identity: str,
    executed_files: frozenset[str],
    executed_members: frozenset[str],
) -> None:
    """Require native ordinary changed cases independently of a candidate plan."""
    if receipt.get("source_identity") != source_identity or receipt.get("changed_paths") != sorted(
        set(changed_paths)
    ):
        raise ValueError("Ordinary collection belongs to another captured source")
    contexts = receipt.get("contexts")
    if not isinstance(contexts, list) or [row.get("name") for row in contexts] != [
        "backend",
        "tooling",
        "cross-owner",
    ]:
        raise ValueError("Ordinary collection omitted an existing native context")
    selected = [item for context in contexts for item in context.get("selected", [])]
    if any(item.get("file") not in changed_paths for item in selected):
        raise ValueError("Ordinary changed-source projection names another input")
    if (
        not {item["file"] for item in selected} <= executed_files
        or not {item["selector"] for item in selected} <= executed_members
    ):
        raise ValueError("Changed tests have no meaningful execution proof")


def _trusted_other_changed_verifiers(root: Path, paths: Sequence[str]) -> frozenset[str]:
    """Apply the configured frontend's existing positive logical-proof policy.

    Syntax roles and nominations do not establish actual native membership or
    owner completeness. Other projects/fixtures retain their source-consumer
    obligation; this does not introduce another frontend or Go test runner.
    """
    from oh_goon.integrations.hashmarks import nominate_changed_verification

    selected = tuple(
        path
        for path in paths
        if Path(path).suffix
        in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".go"}
    )
    if not selected:
        return frozenset()
    observed = nominate_changed_verification(root, selected, required=selected)
    self_nominations = {
        row["nomination"]
        .get("verification_target", {})
        .get("path", row["nomination"].get("path")): row["nomination"]
        for row in observed["required_checks"]
    }
    required: set[str] = set()
    for observation in observed["observations"]:
        packet = observation["changed_set"]
        if packet["freshness"] == "stale":
            raise ValueError("Independent changed-source role is stale")
        change = packet["changes"][0]
        context = change["context"]
        configured_frontend = any(
            row.get("kind") == "package.json"
            and row.get("state") == "known-present"
            and row.get("root") == "frontend"
            for row in context.get("manifests", [])
        )
        if not configured_frontend:
            continue
        role = change["classification"]
        if role["kind"] == "unknown":
            raise ValueError("Independent producer could not qualify a frontend source role")
        nomination = self_nominations.get(change["path"], {})
        if (
            role["kind"] == "verifier"
            and nomination.get("available") is True
            and nomination.get("runner_evidence") == "vitest"
            and nomination.get("scope") == "test-file"
        ):
            required.add(change["path"])
    return frozenset(required)


def _observe_dependency_candidate(
    root: Path,
    *,
    base: str,
    selected_files: Sequence[str],
    source_identity: str,
    producer_identity: str,
) -> dict[str, Any]:
    """Establish fresh dependency proof under immutable base admission code."""
    from oh_goon.integrations.hashmarks import (
        nominate_changed_verification,
        require_current_dependency_environment,
        require_loaded_hashmarks_runtime,
    )
    from oh_goon.repository_tooling import development_verification as admission_owner
    from oh_goon.repository_tooling.release import package_slim as source_owner

    trusted_source = Path(__file__).resolve().parents[1]
    for owner in (
        admission_owner,
        source_owner,
    ):
        if owner.__file__ is None or not Path(owner.__file__).resolve().is_relative_to(
            trusted_source
        ):
            raise ValueError("Dependency observer imported code outside its trusted source")
    for capability in (
        nominate_changed_verification,
        require_current_dependency_environment,
        require_loaded_hashmarks_runtime,
    ):
        filename = getsourcefile(capability)
        if filename is None or not Path(filename).resolve().is_relative_to(trusted_source):
            raise ValueError("Dependency observer imported code outside its trusted source")
    if source_owner.canonical_source_identity(root) != source_identity:
        raise ValueError("Candidate changed before independent dependency observation")
    require_loaded_hashmarks_runtime(expected_implementation_identity=producer_identity)
    evidence = nominate_changed_verification(
        root,
        ("backend/uv.lock",),
        dependency_base={
            "lock": admission_owner._base_lock_bytes(root, base),
            "base_revision": base,
            "lock_path": "backend/uv.lock",
        },
    )
    if evidence.get("producer_implementation_identity") != producer_identity or not (
        admission_owner._bounded_dependency_scope(
            evidence, base, selected_files=set(selected_files)
        )
    ):
        raise ValueError("Independent dependency chains do not reach executed verifiers")
    bundle = evidence["dependency_changes"]
    require_current_dependency_environment(bundle)
    if source_owner.canonical_source_identity(root) != source_identity:
        raise ValueError("Candidate changed during independent dependency observation")
    return {
        "source_identity": source_identity,
        "producer_implementation_identity": producer_identity,
        "base": base,
        "changed_node_ids": bundle["changed_node_ids"],
        "before_observation_identity": bundle["before_observation"]["observation_identity"],
        "after_observation_identity": bundle["after_observation"]["observation_identity"],
        "metadata": [
            {
                key: row[key]
                for key in (
                    "node_id",
                    "metadata_member_revision",
                    "record_member_revision",
                )
            }
            for row in bundle["metadata"]
        ],
        "executed_verifier_files": sorted(set(selected_files)),
    }


def _trusted_dependency_probe(
    root: Path,
    *,
    trusted_root: Path,
    environment: Path,
    env: Mapping[str, str],
    plan: Mapping[str, Any],
    producer_identity: str,
) -> dict[str, Any]:
    """Run base code with the already materialized, candidate-locked producer."""
    from oh_goon.engine.command import run_command

    request = {
        "trusted_root": str(trusted_root.resolve(strict=True)),
        "environment": str(environment.resolve(strict=True)),
        "root": str(root.resolve(strict=True)),
        "base": plan["base"],
        "selected_files": plan["pytest"],
        "source_identity": plan["source_identity"],
        "producer_identity": producer_identity,
    }
    encoded = json.dumps(request).encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise ValueError("Independent dependency request exceeds its transport bound")
    # The only source path added is the separately checked-out immutable base.
    # -I excludes the candidate cwd and ambient Python path configuration. Native
    # dependency site semantics remain owned by the selected Python environment.
    bootstrap = (
        "import contextlib,json,sys\n"
        "from pathlib import Path\n"
        "raw=sys.stdin.buffer.read(1048577)\n"
        "if len(raw)>1048576: raise ValueError('dependency request exceeds bound')\n"
        "request=json.loads(raw)\n"
        "trusted=Path(request['trusted_root']).resolve(strict=True)\n"
        "if Path(sys.prefix).resolve()!=Path(request['environment']): "
        "raise ValueError('dependency environment mismatch')\n"
        "sys.path.insert(0,str(trusted/'backend/src'))\n"
        "from oh_goon.repository_tooling import verification_gate_control as owner\n"
        "expected=trusted/'backend/src/oh_goon/repository_tooling/verification_gate_control.py'\n"
        "if Path(owner.__file__).resolve()!=expected: "
        "raise ValueError('dependency observer origin mismatch')\n"
        "with contextlib.redirect_stdout(sys.stderr):\n"
        " receipt=owner._observe_dependency_candidate(Path(request['root']),"
        "base=request['base'],selected_files=request['selected_files'],"
        "source_identity=request['source_identity'],"
        "producer_identity=request['producer_identity'])\n"
        "print(json.dumps(receipt,sort_keys=True,allow_nan=False))\n"
    )
    observed = run_command(
        str(environment / "bin/python"),
        "-I",
        "-c",
        bootstrap,
        cwd=environment.parent,
        env=dict(env),
        input_data=encoded,
        timeout=180,
        max_capture_bytes_per_stream=256 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if (
        observed.returncode
        or observed.stdout_truncated
        or observed.supervisor is None
        or observed.supervisor.final_process_group_quiescent is not True
    ):
        raise ValueError("Independent dependency observation did not complete")
    try:
        receipt = json.loads(observed.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError(
            "Independent dependency observation is not a single bounded reply"
        ) from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("source_identity") != request["source_identity"]
        or receipt.get("producer_implementation_identity") != producer_identity
        or receipt.get("base") != request["base"]
        or receipt.get("executed_verifier_files") != sorted(set(plan["pytest"]))
        or not receipt.get("changed_node_ids")
        or not receipt.get("metadata")
        or not receipt.get("before_observation_identity")
        or not receipt.get("after_observation_identity")
    ):
        raise ValueError("Independent dependency observation differs from admitted authority")
    nodes, captured = receipt["changed_node_ids"], receipt["metadata"]
    if (
        not isinstance(nodes, list)
        or not 1 <= len(nodes) <= 64
        or any(not isinstance(node, str) or not node for node in nodes)
        or len(nodes) != len(set(nodes))
        or not isinstance(captured, list)
        or len(captured) != len(nodes)
        or any(
            not isinstance(row, Mapping)
            or any(
                not isinstance(row.get(key), str) or not row[key]
                for key in ("node_id", "metadata_member_revision", "record_member_revision")
            )
            for row in captured
        )
        or {row["node_id"] for row in captured} != set(nodes)
        or not isinstance(receipt["before_observation_identity"], str)
        or not isinstance(receipt["after_observation_identity"], str)
        or receipt["before_observation_identity"] == receipt["after_observation_identity"]
    ):
        raise ValueError("Independent dependency observation has inconsistent member bindings")
    return receipt


def _trusted_ordinary_probe(
    root: Path,
    *,
    trusted_root: Path,
    environment: Path,
    env: Mapping[str, str],
    changed_paths: Sequence[str],
    source_identity: str,
) -> dict[str, Any]:
    """Run base-owned collection control with candidate-native dependencies."""
    from oh_goon.core.file_digest import sha256_file

    trusted = trusted_root.resolve(strict=True)
    request: dict[str, Any] = {
        "root": str(root.resolve(strict=True)),
        "trusted_root": str(trusted),
        "environment": str(environment),
        "changed_paths": sorted(set(changed_paths)),
        "source_identity": source_identity,
    }
    request["contexts"] = _ordinary_collection_requests(
        root.resolve(strict=True), trusted_root=trusted, environment=environment, env=env
    )
    if (
        Path(__file__).resolve()
        != trusted / "backend/src/oh_goon/repository_tooling/verification_gate_control.py"
    ):
        raise ValueError("Ordinary collection controller is not loaded from its trusted base")
    # Candidate environments may contain executable .pth hooks after normal
    # project installation. Never start the control parent with that Python.
    # Only the existing native collector child receives its interpreter/env.
    receipt = _ordinary_candidate_collection(
        root,
        trusted_root=trusted,
        environment=environment,
        changed_paths=changed_paths,
        source_identity=source_identity,
        requests=request["contexts"],
        env=env,
    )
    collector = trusted / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
    if (
        not isinstance(receipt, dict)
        or receipt.get("environment") != str(environment)
        or receipt.get("source_identity") != source_identity
        or receipt.get("changed_paths") != request["changed_paths"]
        or receipt.get("collector_source") != str(collector)
        or receipt.get("collector_sha256") != sha256_file(collector)
        or receipt.get("execution") != "not-performed"
    ):
        raise ValueError("Ordinary collection differs from its trusted source/environment")
    contexts = receipt.get("contexts")
    if (
        not isinstance(contexts, list)
        or len(contexts) != len(request["contexts"])
        or any(
            observed.get("name") != expected["name"]
            or observed.get("request") != expected["request"]
            for observed, expected in zip(contexts, request["contexts"], strict=True)
        )
    ):
        raise ValueError("Ordinary collection changed its resolved native request")
    return receipt


def _candidate_producer_identity(
    root: Path,
    *,
    dependency_plan: Mapping[str, Any] | None = None,
    trusted_root: Path | None = None,
    dependency_receipt: dict[str, Any] | None = None,
    ordinary_receipt: dict[str, Any] | None = None,
    ordinary_paths: Sequence[str] = (),
    source_identity: str | None = None,
    expected_producer_identity: str | None = None,
) -> str:
    """Observe the candidate's native locked package using only trusted code.

    uv owns dependency selection and artifact verification. The initial
    dependency-only phase does not install/build candidate project code.
    Dependency admission uses immutable base code there when requested.
    Explicit ordinary collection then prepares the native dev/project environment
    and runs candidate imports only in existing collector children; control stays
    in the independent base process. Hashmarks owns implementation identity.
    """
    from oh_goon.engine.command import run_command
    from oh_goon.integrations.hashmarks import (
        native_producer_implementation_identity,
    )
    from oh_goon.repository_tooling._dependency_lock_portability import (
        backend_lock_portability_issues,
    )

    if ordinary_receipt is not None and (trusted_root is None or source_identity is None):
        raise ValueError("Native ordinary collection requires trusted source authority")
    if ordinary_receipt is not None and (
        (expected_producer_identity is None and dependency_plan is None)
        or (
            expected_producer_identity is not None
            and (
                not isinstance(expected_producer_identity, str)
                or not expected_producer_identity.startswith("sha256:")
                or len(expected_producer_identity) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_producer_identity[7:]
                )
            )
        )
    ):
        raise ValueError("Native ordinary collection requires an expected producer identity")

    project = root.resolve(strict=True) / "backend"
    if project.is_symlink() or not project.is_dir():
        raise ValueError("Candidate native dependency project is unavailable or escaped")
    for name in ("pyproject.toml", "uv.lock"):
        path = project / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Candidate native dependency inputs are unavailable or oversized")
    if backend_lock_portability_issues(project / "uv.lock"):
        raise ValueError("Candidate native dependency sources are not publishable")
    with tempfile.TemporaryDirectory(prefix="oh-goon-merge-producer-") as temporary:
        environment = Path(temporary).resolve() / "environment"
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("UV_", "PYTHON")) and key != "VIRTUAL_ENV"
        }
        for key in ("UV_CACHE_DIR", "UV_OFFLINE"):
            if key in os.environ:
                env[key] = os.environ[key]
        env["UV_PROJECT_ENVIRONMENT"] = str(environment)
        workspace = run_command(
            "uv",
            "workspace",
            "dir",
            "--project",
            str(project),
            "--no-config",
            "--offline",
            "--no-python-downloads",
            cwd=Path(temporary),
            env=env,
            timeout=20,
            max_capture_bytes_per_stream=4096,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
        if (
            workspace.returncode
            or workspace.stdout_truncated
            or workspace.stdout.rstrip("\r\n") != str(project)
        ):
            raise ValueError("Candidate native workspace does not own the inspected backend lock")
        observed = run_command(
            "uv",
            "sync",
            "--project",
            str(project),
            "--frozen",
            "--no-default-groups",
            "--no-install-project",
            "--no-install-workspace",
            "--no-build",
            "--no-config",
            "--no-python-downloads",
            "--python",
            sys.executable,
            "--link-mode",
            "copy",
            cwd=Path(temporary),
            env=env,
            timeout=120,
            max_capture_bytes_per_stream=1024 * 1024,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
        if observed.returncode or observed.stdout_truncated:
            raise ValueError("Independent native dependency materialization is incomplete")
        site = Path(
            sysconfig.get_path(
                "purelib", vars={"base": str(environment), "platbase": str(environment)}
            )
        )
        if not site.is_dir() or site.resolve() != site or not site.is_relative_to(environment):
            raise ValueError("Candidate native dependency environment is unavailable or escaped")
        for entry in site.iterdir():
            if entry.is_symlink() or entry.name.endswith(".egg-info"):
                raise ValueError("Candidate native dependency metadata origin is unverified")
            if entry.name.endswith(".dist-info"):
                for name in ("METADATA", "RECORD"):
                    path = entry / name
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or not 0 < path.stat().st_size <= 8 * 1024 * 1024
                    ):
                        raise ValueError(
                            "Candidate native dependency metadata is unbounded or unsafe"
                        )
        producers = [
            distribution
            for distribution in metadata.distributions(path=[str(site)])
            if distribution.metadata.get("Name", "").lower() == "hashmarks"
        ]
        if len(producers) != 1:
            raise ValueError("Candidate native Hashmarks distribution is missing or ambiguous")
        producer = producers[0]
        package = Path(str(producer.locate_file("hashmarks")))
        if (
            package != site / "hashmarks"
            or not package.is_dir()
            or package.resolve() != package
            or not (package / "__init__.py").is_file()
            or not any(str(path) == "hashmarks/__init__.py" for path in producer.files or ())
        ):
            raise ValueError("Candidate native Hashmarks package origin is unverified")
        total = 0
        for count, member in enumerate(package.rglob("*"), start=1):
            if count > 20_000:
                raise ValueError("Candidate native Hashmarks package exceeds its inspection bound")
            if member.is_symlink() or not (member.is_file() or member.is_dir()):
                raise ValueError("Candidate native Hashmarks package contains an unsafe member")
            if member.is_file():
                total += member.stat().st_size
                if total > 64 * 1024 * 1024:
                    raise ValueError(
                        "Candidate native Hashmarks package exceeds its inspection bound"
                    )
        identity = native_producer_implementation_identity(package_root=package)
        if expected_producer_identity is not None and identity != expected_producer_identity:
            raise ValueError("Producer implementation differs from trusted admission")
        if dependency_plan is not None:
            if trusted_root is None or dependency_receipt is None:
                raise ValueError("Independent dependency observation requires trusted authority")
            if (
                dependency_plan.get("evidence", {}).get("producer_implementation_identity")
                != identity
            ):
                raise ValueError("Producer implementation differs from trusted admission")
            dependency_receipt.update(
                _trusted_dependency_probe(
                    root,
                    trusted_root=trusted_root,
                    environment=environment,
                    env=env,
                    plan=dependency_plan,
                    producer_identity=identity,
                )
            )
        if ordinary_receipt is not None:
            if trusted_root is None or source_identity is None:
                raise ValueError("Native ordinary collection requires trusted source authority")
            from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

            trusted_source = canonical_source_identity(trusted_root)
            # The data-only dependency observation above remains no-build and
            # does not import candidate code. Actual native collection is a
            # separate, explicit execution phase using the normal frozen dev
            # environment/project setup; collection itself performs no repair.
            prepared = run_command(
                "uv",
                "sync",
                "--project",
                str(project),
                "--frozen",
                "--group",
                "dev",
                "--group",
                "quality",
                "--group",
                "build",
                "--no-config",
                "--no-python-downloads",
                "--python",
                sys.executable,
                "--link-mode",
                "copy",
                cwd=Path(temporary),
                env=env,
                timeout=120,
                max_capture_bytes_per_stream=1024 * 1024,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
            if (
                prepared.returncode
                or prepared.stdout_truncated
                or prepared.supervisor is None
                or prepared.supervisor.final_process_group_quiescent is not True
            ):
                raise ValueError("Candidate native collection environment was not prepared")
            if native_producer_implementation_identity(package_root=package) != identity:
                raise ValueError("Native collection preparation changed the admitted producer")
            if (
                canonical_source_identity(trusted_root) != trusted_source
                or canonical_source_identity(root) != source_identity
            ):
                raise ValueError("Source changed during native collection environment preparation")
            ordinary_receipt.update(
                _trusted_ordinary_probe(
                    root,
                    trusted_root=trusted_root,
                    environment=environment,
                    env=env,
                    changed_paths=ordinary_paths,
                    source_identity=source_identity,
                )
            )
            if canonical_source_identity(trusted_root) != trusted_source:
                raise ValueError("Trusted source changed during native ordinary collection")
            ordinary_receipt["trusted_source_identity"] = trusted_source
        return identity


def _require_trusted_hashmarks_api(trusted: Path) -> None:
    """Bind public composition and the actual imported implementations to base."""
    from oh_goon.integrations import hashmarks as repository_evidence
    from oh_goon.integrations.hashmarks import (
        native_producer_implementation_identity,
        observe_repository_assets,
    )

    for owner in (
        repository_evidence,
        observe_repository_assets,
        native_producer_implementation_identity,
    ):
        filename = getsourcefile(owner)
        if filename is None or not Path(filename).resolve().is_relative_to(trusted / "backend/src"):
            raise ValueError("Controller imported a Hashmarks owner outside its trusted checkout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--control-base")
    parser.add_argument("--head")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--trusted-root", type=Path, required=True)
    parser.add_argument("--static-project-capability-output", type=Path)
    parser.add_argument("--prepare-static-project", action="store_true")
    args = parser.parse_args()
    try:
        plan = _read(args.plan)
        if args.control_base != args.base:
            raise ValueError("Independent controller is not bound to its base revision")
        # Control stays in the immutable base's separately materialized runtime.
        # Candidate imports are confined to explicitly selected native collection
        # children after the independent data-only admission phase.
        from oh_goon.engine import command as command_owner
        from oh_goon.repository_tooling import _dependency_lock_portability as lock_owner
        from oh_goon.repository_tooling import development_verification as admission_owner
        from oh_goon.repository_tooling.release import package_slim as identity_owner

        trusted = args.trusted_root.resolve(strict=True)
        if Path(__file__).resolve().parents[4] != trusted:
            raise ValueError("Controller source differs from the trusted checkout")
        _require_trusted_hashmarks_api(trusted)
        for module in (
            command_owner,
            admission_owner,
            lock_owner,
            identity_owner,
        ):
            filename = module.__file__
            if filename is None or not Path(filename).resolve().is_relative_to(
                trusted / "backend/src"
            ):
                raise ValueError("Controller imported an owner outside its trusted checkout")
        run_command = command_owner.run_command
        canonical_source_identity = identity_owner.canonical_source_identity

        def git(root: Path, *arguments: str) -> str:
            observed = run_command(
                "git",
                "-C",
                str(root),
                *arguments,
                timeout=20,
                max_capture_bytes_per_stream=4 * 1024 * 1024,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
            if observed.returncode or observed.stdout_truncated:
                raise ValueError("Independent native Git observation is incomplete")
            return observed.stdout

        if (
            git(args.trusted_root, "rev-parse", "HEAD").strip() != args.base
            or git(args.root, "rev-parse", "HEAD").strip() != args.candidate
        ):
            raise ValueError("Independent checkouts differ from their admitted revisions")
        if git(trusted, "status", "--porcelain", "--untracked-files=normal").strip():
            raise ValueError("Trusted controller checkout is not clean")
        if git(args.root, "status", "--porcelain", "--untracked-files=normal").strip():
            raise ValueError("Candidate checkout is not clean")
        if args.head is None or plan.get("requested_head") != args.head:
            raise ValueError("Independent requested head differs from selected proof")
        git(args.root, "merge-base", "--is-ancestor", args.head, args.candidate)
        changed_paths, removed_paths = native_changed_paths(
            git(
                args.root,
                "diff",
                "--name-status",
                "--find-renames",
                "-z",
                args.base,
                args.candidate,
            )
        )
        if plan.get("source_identity") != canonical_source_identity(args.root):
            raise ValueError("Candidate contents differ from selected proof")
        if args.static_project_capability_output or args.prepare_static_project:
            requested = static_project_capability_requested(args.root, trusted, plan, changed_paths)
            if args.static_project_capability_output:
                with args.static_project_capability_output.open("a", encoding="utf-8") as stream:
                    stream.write(f"frontend={'true' if requested else 'false'}\n")
            if args.prepare_static_project:
                if not requested:
                    raise ValueError("Native static-project preparation was not admitted")
                prepare_static_project(args.root, trusted, plan["source_identity"])
            return 0
        dependency_intent, unsupported_intent, hashmarks_upgrade_intent = (
            admission_owner._backend_dependency_intent(args.root, args.base, tuple(changed_paths))
        )
        if unsupported_intent or (dependency_intent and "backend/uv.lock" not in changed_paths):
            raise ValueError("Dependency declarations lack an admitted native lock scope")
        executed_files, executed_members = validate_merge_proof(
            plan,
            _read(args.result),
            candidate=args.candidate,
            base=args.base,
            changed_paths=changed_paths,
            removed_paths=removed_paths,
            control_base=args.control_base,
            root=args.root,
            trusted_root=trusted,
            observed_hashmarks_upgrade=hashmarks_upgrade_intent,
        )
        producer = plan.get("evidence", {}).get("producer_implementation_identity")
        dependency_receipt: dict[str, Any] = {}
        ordinary_receipt: dict[str, Any] = {}
        # This chooses the existing Python execution capability, not which
        # paths are tests. Only actual ordinary native membership decides that.
        ordinary_required = any(
            Path(path).suffix == ".py" for path in set(changed_paths) - set(removed_paths)
        )
        other_verifiers = _trusted_other_changed_verifiers(
            args.root, tuple(path for path in changed_paths if path not in removed_paths)
        )
        if producer is not None:
            dependency_required = (
                "backend/uv.lock" in changed_paths
                and "hashmarks_upgrade" not in plan.get("evidence", {})
            )
            observed_producer = _candidate_producer_identity(
                args.root,
                dependency_plan=plan if dependency_required else None,
                trusted_root=trusted if dependency_required or ordinary_required else None,
                dependency_receipt=dependency_receipt if dependency_required else None,
                ordinary_receipt=ordinary_receipt if ordinary_required else None,
                ordinary_paths=changed_paths if ordinary_required else (),
                source_identity=plan["source_identity"] if ordinary_required else None,
                expected_producer_identity=producer,
            )
            if producer != observed_producer:
                raise ValueError("Producer implementation differs from trusted admission")
        if ordinary_required:
            _require_ordinary_changed_test_execution(
                ordinary_receipt,
                changed_paths=changed_paths,
                source_identity=plan["source_identity"],
                executed_files=executed_files,
                executed_members=executed_members,
            )
        if not other_verifiers <= executed_files:
            raise ValueError("Changed tests have no meaningful execution proof")
        if (
            git(args.root, "rev-parse", "HEAD").strip() != args.candidate
            or git(trusted, "rev-parse", "HEAD").strip() != args.base
            or git(trusted, "status", "--porcelain", "--untracked-files=normal").strip()
        ):
            raise ValueError("Independent checkouts changed during trusted producer admission")
        if (
            plan.get("source_identity") != canonical_source_identity(args.root)
            or git(args.root, "status", "--porcelain", "--untracked-files=normal").strip()
        ):
            raise ValueError("Candidate contents changed during trusted producer admission")
        static_projects = admitted_static_projects(changed_paths, plan.get("evidence", {}))
        if static_projects:
            context = static_project_context(args.root, plan["source_identity"], static_projects)
            for row in _read(args.result).get("outcomes", []):
                if row["owner"] in STATIC_PROJECT_OWNERS:
                    if row.get("static_project_context") != context:
                        raise ValueError("Static-project context changed during trusted admission")
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"MERGE UNVERIFIED: {exc}")
        return 1
    if dependency_receipt:
        print("DEPENDENCY_REOBSERVED: " + json.dumps(dependency_receipt, sort_keys=True))
    if ordinary_receipt:
        print("ORDINARY_COLLECTION_REOBSERVED: " + json.dumps(ordinary_receipt, sort_keys=True))
    print(
        "MERGE_VERIFIED: exact candidate and all admitted obligations satisfied; daily full is separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
