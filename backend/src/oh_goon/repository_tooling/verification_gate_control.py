"""Independent, inert validation of the admitted merge proof.

The fast workflow transports this file from its immutable base revision. It
does not import the candidate's selector or accept author-supplied skip hints.
It can be installed before the fast workflow without changing an existing gate.
"""

from __future__ import annotations

import argparse
import json
import runpy
from collections.abc import Mapping, Sequence
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


def is_verification_policy_path(path: str) -> bool:
    """The existing contributor, verification and result-authority boundaries."""
    return (
        path in REAL_LIFECYCLE_QA_PATHS
        or path.startswith((".github/workflows/", "backend/src/oh_goon/repository_tooling/"))
        or Path(path).name == "AGENTS.md"
        or path
        in {
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


def non_executable_documentation(root: Path, path: str) -> bool:
    if Path(path).suffix != ".md" or is_verification_policy_path(path):
        return False
    if not (path.startswith("docs/") or path in {"README.md", "CONTRIBUTING.md"}):
        return False
    try:
        with (root / path).open("r", encoding="utf-8") as stream:
            content = stream.read(1024 * 1024 + 1)
    except OSError:
        return False
    return len(content) <= 1024 * 1024 and "```" not in content and "~~~" not in content


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


def validate_merge_proof(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    candidate: str,
    base: str,
    changed_paths: Sequence[str],
    control_base: str | None,
    root: Path | None = None,
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
    evidence = plan.get("evidence", {})
    if (
        behavior_required
        and not contract_only
        and (
            not isinstance(evidence, Mapping)
            or not evidence.get("producer_implementation_identity")
            or evidence.get("owner_coverage") != "not-claimed"
        )
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
    changed_tests.update(
        path
        for path in changed_paths
        if Path(path).name.startswith("test_")
        and path.endswith(".py")
        or any(
            path.endswith(suffix) for suffix in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
        )
    )
    if not changed_tests <= executed_files:
        raise ValueError("Changed tests have no meaningful execution proof")
    observations = {row.get("changed_path"): row for row in evidence.get("observations", [])}
    for source in changed_paths:
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
        from oh_goon.integrations.hashmarks import runtime_contract as runtime_owner
        from oh_goon.repository_tooling.release import package_slim as identity_owner

        trusted = args.trusted_root.resolve(strict=True)
        if Path(__file__).resolve().parents[4] != trusted:
            raise ValueError("Controller source differs from the trusted checkout")
        for module in (command_owner, runtime_owner, identity_owner):
            filename = module.__file__
            if filename is None or not Path(filename).resolve().is_relative_to(
                trusted / "backend/src"
            ):
                raise ValueError("Controller imported an owner outside its trusted checkout")
        run_command = command_owner.run_command
        require_loaded_hashmarks_runtime = runtime_owner.require_loaded_hashmarks_runtime
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
        changed_paths = [
            path
            for path in git(
                args.root,
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                args.base,
                args.candidate,
            ).split("\0")
            if path
        ]
        if plan.get("source_identity") != canonical_source_identity(args.root):
            raise ValueError("Candidate contents differ from selected proof")
        producer = plan.get("evidence", {}).get("producer_implementation_identity")
        if producer is not None and producer != require_loaded_hashmarks_runtime():
            raise ValueError("Producer implementation differs from trusted admission")
        validate_merge_proof(
            plan,
            _read(args.result),
            candidate=args.candidate,
            base=args.base,
            changed_paths=changed_paths,
            control_base=args.control_base,
            root=args.root,
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"MERGE UNVERIFIED: {exc}")
        return 1
    print(
        "MERGE_VERIFIED: exact candidate and all admitted obligations satisfied; daily full is separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
