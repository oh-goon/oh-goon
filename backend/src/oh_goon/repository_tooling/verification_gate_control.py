"""Independent, inert validation of the admitted merge proof.

The fast workflow transports this file from its immutable base revision. It
does not import the candidate's selector or accept author-supplied skip hints.
It can be installed before the fast workflow without changing an existing gate.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import sysconfig
import tempfile
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


def non_executable_documentation(root: Path, path: str) -> bool:
    if is_verification_policy_path(path):
        return False
    markdown = Path(path).suffix == ".md" and (
        path.startswith("docs/") or path in {"README.md", "CONTRIBUTING.md"}
    )
    if path != "LICENSE" and not markdown:
        return False
    try:
        with (root / path).open("r", encoding="utf-8") as stream:
            content = stream.read(1024 * 1024 + 1)
    except OSError:
        return False
    return (
        len(content) <= 1024 * 1024
        and "\x00" not in content
        and "```" not in content
        and "~~~" not in content
    )


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
    removed_paths: Sequence[str] = (),
) -> None:
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
    plain_docs = {
        path
        for path in changed_paths
        if root is not None and non_executable_documentation(root, path)
    }
    evidence = plan.get("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError("Producer evidence has an invalid shape")
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
    contract_only = any(map(is_generated_contract_path, changed_paths)) and all(
        is_generated_contract_path(path) or path in plain_docs for path in changed_paths
    )
    behavior_required = plan.get("behavioral_status") == "REQUIRED"
    if not behavior_required:
        for path in changed_paths:
            if path not in plain_docs:
                raise ValueError("Executable inputs cannot claim behavioral NOT_APPLICABLE")
    if behavior_required and not (plan.get("pytest") or plan.get("frontend")) and not contract_only:
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
    executed_files: set[str] = set()
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
        if policy_change and not set(policy_tests) <= pytest_files:
            raise ValueError("Required verification-policy tests have no execution proof")
        if pytest_files != {path.split("::", 1)[0] for path in plan.get("pytest", [])}:
            raise ValueError("Admitted pytest files differ from native execution proof")
        executed_files.update(pytest_files)
    changed_tests = {
        observation["changed_path"]
        for observation in plan.get("evidence", {}).get("observations", [])
        if observation.get("state") == "CHANGED_TEST"
    }
    # Production owners can also use test_*.py names. Keep the independent
    # filename safeguard within test directories, including nested projects.
    changed_tests.update(
        path
        for path in changed_paths
        if (
            path.endswith(".py")
            and any(part in {"test", "tests"} for part in Path(path).parts[:-1])
            and (Path(path).name.startswith("test_") or path.endswith("_test.py"))
        )
        or any(
            path.endswith(suffix) for suffix in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
        )
    )
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
        if is_verification_policy_path(source) and not source.endswith(".py"):
            continue
        if source in plain_docs:
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


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        content = stream.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("Merge proof exceeds its transport bound")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Merge proof requires an object")
    return value


def _candidate_producer_identity(root: Path) -> str:
    """Observe the candidate's native locked package using only trusted code.

    uv owns dependency selection and artifact verification. Its fresh dependency-only
    environment is inspected as data; neither candidate Python nor project build hooks
    run. Hashmarks retains ownership of the implementation identity algorithm.
    """
    from oh_goon.engine.command import run_command
    from oh_goon.integrations.hashmarks import (
        native_producer_implementation_identity,
    )
    from oh_goon.repository_tooling._dependency_lock_portability import (
        backend_lock_portability_issues,
    )

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
        return native_producer_implementation_identity(package_root=package)


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
    args = parser.parse_args()
    try:
        plan = _read(args.plan)
        if args.control_base != args.base:
            raise ValueError("Independent controller is not bound to its base revision")
        # This branch runs only from the immutable base's separately
        # materialized environment. Candidate source is inspected as data.
        from oh_goon.engine import command as command_owner
        from oh_goon.repository_tooling import _dependency_lock_portability as lock_owner
        from oh_goon.repository_tooling.release import package_slim as identity_owner

        trusted = args.trusted_root.resolve(strict=True)
        if Path(__file__).resolve().parents[4] != trusted:
            raise ValueError("Controller source differs from the trusted checkout")
        _require_trusted_hashmarks_api(trusted)
        for module in (command_owner, lock_owner, identity_owner):
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
        validate_merge_proof(
            plan,
            _read(args.result),
            candidate=args.candidate,
            base=args.base,
            changed_paths=changed_paths,
            removed_paths=removed_paths,
            control_base=args.control_base,
            root=args.root,
        )
        producer = plan.get("evidence", {}).get("producer_implementation_identity")
        if producer is not None and producer != _candidate_producer_identity(args.root):
            raise ValueError("Producer implementation differs from trusted admission")
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
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"MERGE UNVERIFIED: {exc}")
        return 1
    print(
        "MERGE_VERIFIED: exact candidate and all admitted obligations satisfied; daily full is separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
