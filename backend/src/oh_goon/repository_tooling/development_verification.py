"""Admit relevant development proof, then delegate to the original check owners."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ConfigDict, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.engine.command import run_command
from oh_goon.integrations.exact_file_write import prepared_text_file, publish_prepared_file
from oh_goon.integrations.hashmarks.development_verification import nominate_changed_verification
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.repository_tooling.frontend_test_results import vitest_outcomes
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from oh_goon.repository_tooling.verification_gate_control import (
    VERIFICATION_POLICY_TESTS,
    is_generated_contract_path,
    is_verification_policy_path,
    non_executable_documentation,
    require_native_pytest_pass,
    required_static_obligations,
)

ROOT = Path(__file__).resolve().parents[4]
_SCHEMA: Literal["oh-goon.development-verification/v1"] = "oh-goon.development-verification/v1"
_MERGE_MARKER = "not certification and not slow and not daily"
_STATIC = Literal["python", "frontend", "architecture", "generated", "package"]


class ChangeVerificationPlan(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["oh-goon.development-verification/v1"] = _SCHEMA
    base: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    candidate: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    requested_head: str | None = None
    source_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    changed_paths: tuple[str, ...]
    static: tuple[_STATIC, ...]
    pytest: tuple[str, ...]
    frontend: tuple[str, ...]
    unverified: tuple[str, ...]
    evidence: dict[str, Any]
    behavioral_status: Literal["REQUIRED", "NOT_APPLICABLE"]
    policy_status: Literal["REVIEW_REQUIRED", "ORDINARY"]
    obligations: tuple[str, ...] = ()


def _git(root: Path, *arguments: str) -> str:
    result = run_command(
        "git",
        "-C",
        str(root),
        *arguments,
        timeout=20,
        max_capture_bytes_per_stream=4 * 1024 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode != 0 or result.stdout_truncated:
        raise ValueError("Git comparison is incomplete or unavailable")
    return result.stdout


def capture_change(
    root: Path, base: str, candidate: str | None
) -> tuple[str, str, tuple[str, ...]]:
    base_sha = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if (
        candidate is not None
        and head != _git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}").strip()
    ):
        raise ValueError("Effective checkout differs from the requested candidate")
    arguments = (base_sha, head) if candidate is not None else (base_sha,)
    tokens = _git(root, "diff", "--name-status", "-z", "--find-renames", *arguments).split("\0")
    paths: set[str] = set()
    cursor = 0
    while cursor < len(tokens) and tokens[cursor]:
        status = tokens[cursor]
        count = 2 if status[0] in {"R", "C"} else 1
        selected = tokens[cursor + 1 : cursor + 1 + count]
        if len(selected) != count or any(not path for path in selected):
            raise ValueError("Git returned incomplete changed-path evidence")
        paths.update(selected)
        cursor += count + 1
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    dirty = _git(root, "diff", "--name-only", "HEAD").strip()
    if candidate is not None and (dirty or any(untracked)):
        raise ValueError("Hosted candidate has uncommitted or untracked source")
    if candidate is None:
        paths.update(path for path in untracked if path)
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts or "\n" in path:
            raise ValueError("Git comparison contains an unsafe path")
    return base_sha, head, tuple(sorted(paths))


def admit_change(
    root: Path,
    *,
    base: str,
    candidate: str | None = None,
    requested_head: str | None = None,
    nominator: Callable[..., dict[str, Any]] = nominate_changed_verification,
) -> ChangeVerificationPlan:
    base_sha, head, changed = capture_change(root, base, candidate)
    if requested_head is not None:
        _git(root, "merge-base", "--is-ancestor", requested_head, head)
    before = canonical_source_identity(root)
    executable = tuple(path for path in changed if not non_executable_documentation(root, path))
    authored = tuple(path for path in executable if not is_generated_contract_path(path))
    static = cast(set[_STATIC], required_static_obligations(executable))
    policy_paths = tuple(path for path in authored if is_verification_policy_path(path))
    required = VERIFICATION_POLICY_TESTS if policy_paths else ()
    evidence = (
        nominator(root, authored, required=required)
        if required
        else nominator(root, authored)
        if authored
        else {}
    )
    pytest: set[str] = set()
    frontend: set[str] = set()
    unverified: list[str] = []
    if any(path in {"backend/uv.lock", "frontend/package-lock.json"} for path in executable):
        unverified.append("DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED")
    if "backend/pyproject.toml" in executable:
        previous = tomllib.loads(_git(root, "show", f"{base_sha}:backend/pyproject.toml"))
        with (root / "backend/pyproject.toml").open("r", encoding="utf-8") as stream:
            manifest = stream.read(4 * 1024 * 1024 + 1)
        if len(manifest) > 4 * 1024 * 1024:
            raise ValueError("Dependency declaration exceeds its transport bound")
        current = tomllib.loads(manifest)
        project_fields = ("dependencies", "optional-dependencies", "requires-python")
        if any(
            previous.get("project", {}).get(key) != current.get("project", {}).get(key)
            for key in project_fields
        ) or any(
            previous.get(key) != current.get(key) for key in ("dependency-groups", "build-system")
        ):
            unverified.append("DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED")
    observations = evidence.get("observations", [])
    policy_proofs = [
        projected
        for projected in evidence.get("required_checks", [])
        if projected.get("nomination", {}).get("available") is True
    ]
    if policy_paths:
        qualified_policy_paths = {
            projected["nomination"].get("verification_target", {}).get("path")
            for projected in policy_proofs
        }
        if not set(required) <= qualified_policy_paths:
            unverified.append("VERIFICATION_POLICY_PROTECTION_UNAVAILABLE")
        pytest.update(str(path) for path in qualified_policy_paths if path in required)
    observed_paths = {row.get("changed_path") for row in observations}
    for path in authored:
        if path not in observed_paths:
            unverified.append(f"NO_PRODUCER_OBSERVATION:{path}")
    for row in observations:
        path = str(row.get("changed_path", ""))
        if path not in authored:
            raise ValueError("Producer observation is outside the admitted change set")
        if row.get("state") not in {"LINKED", "CHANGED_TEST"} or row.get("stale") is True:
            if (
                path in policy_paths
                and not path.endswith(".py")
                and policy_proofs
                and row.get("stale") is not True
            ):
                row = {**row, "state": "POLICY_OBLIGATION", "nominations": policy_proofs}
            else:
                unverified.append(f"NO_QUALIFIED_VERIFIER:{path}")
                continue
        if row.get("state") == "LINKED" and path not in row.get("edit_candidates", []):
            unverified.append(f"PRODUCER_SELECTED_ANOTHER_OWNER:{path}")
            continue
        if (Path(path).name == "conftest.py" or "/tests/factories/" in path) and row.get(
            "qualification", {}
        ).get("completeness") != "complete":
            unverified.append(f"SHARED_FIXTURE_CONSUMER_SCOPE_UNVERIFIED:{path}")
        admitted = 0
        for projected in row.get("nominations", []):
            nomination = projected["nomination"]
            if nomination.get("available") is not True:
                continue
            target = nomination.get("verification_target", {})
            selected = str(projected.get("workspace_prefix", "")) + str(target.get("path", ""))
            if not selected or Path(selected).is_absolute() or ".." in Path(selected).parts:
                raise ValueError("Producer nomination has an unsafe test path")
            runner = nomination.get("runner_evidence")
            if runner == "pytest":
                if not selected.startswith(
                    ("backend/tests/", "scripts/tests/", "tests/integration/")
                ):
                    unverified.append(f"NATIVE_PROJECT_CONTEXT_REQUIRED:{selected}")
                    continue
                pytest.add(selected)
            elif runner == "vitest":
                frontend.add(selected)
                static.add("frontend")
            else:
                continue
            admitted += 1
        if not admitted:
            unverified.append(f"NO_EXECUTABLE_VERIFIER:{path}")
    if not changed:
        unverified.append("EMPTY_CHANGE_SET")
    if canonical_source_identity(root) != before:
        raise ValueError("Source changed while verification was being admitted")
    return ChangeVerificationPlan(
        base=base_sha,
        candidate=head,
        requested_head=requested_head,
        source_identity=before,
        changed_paths=changed,
        static=tuple(sorted(static)),
        pytest=tuple(sorted(pytest)),
        frontend=tuple(sorted(frontend)),
        unverified=tuple(unverified),
        evidence=evidence,
        behavioral_status="REQUIRED" if executable else "NOT_APPLICABLE",
        policy_status="REVIEW_REQUIRED" if policy_paths else "ORDINARY",
        obligations=("verification-policy protection",) if policy_paths else (),
    )


def require_current_plan(root: Path, plan: ChangeVerificationPlan) -> None:
    if _git(root, "rev-parse", "HEAD").strip() != plan.candidate:
        raise ValueError("Verification candidate changed after admission")
    _git(root, "cat-file", "-e", plan.base + "^{commit}")
    if canonical_source_identity(root) != plan.source_identity:
        raise ValueError("Verification source changed after admission")
    if plan.evidence and plan.evidence.get("producer_implementation_identity") != (
        require_loaded_hashmarks_runtime()
    ):
        raise ValueError("Loaded Hashmarks implementation changed after admission")


def _commands(root: Path, plan: ChangeVerificationPlan) -> list[tuple[str, tuple[str, ...], Path]]:
    python = sys.executable
    commands: list[tuple[str, tuple[str, ...], Path]] = []
    if "python" in plan.static:
        commands.extend(
            (
                (
                    "Ruff",
                    (
                        python,
                        "-m",
                        "ruff",
                        "check",
                        "backend/src",
                        "backend/tests",
                        "scripts",
                        "tests/integration",
                    ),
                    root,
                ),
                (
                    "Format",
                    (
                        python,
                        "-m",
                        "ruff",
                        "format",
                        "--check",
                        "backend/src",
                        "backend/tests",
                        "scripts",
                        "tests/integration",
                    ),
                    root,
                ),
                ("Types", (python, "-m", "mypy"), root / "backend"),
            )
        )
    if "architecture" in plan.static:
        commands.append(
            ("Architecture", (python, "-m", "oh_goon.repository_tooling.check_architecture"), root)
        )
    if "generated" in plan.static:
        commands.append(("Generated contracts", ("make", "check-generated"), root))
    if "package" in plan.static:
        commands.append(("Native package", ("make", "backend-package-build-check"), root))
    if "frontend" in plan.static:
        for script in ("typecheck", "lint", "architecture", "i18n:check"):
            commands.append((f"Frontend {script}", ("npm", "run", script), root / "frontend"))
    return commands


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Verification evidence exceeds its transport bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Verification evidence requires an object")
    return value


def require_complete_membership(
    payload: Mapping[str, Any], *, source_identity: str | None = None
) -> None:
    require_native_pytest_pass(payload, expected_source=source_identity)


def _retain_native_report(outcome: dict[str, Any], path: Path) -> dict[str, Any]:
    try:
        payload = _read_json(path)
    except (OSError, ValueError) as exc:
        outcome["native_evidence_error"] = str(exc)
        return {}
    outcome["native_report"] = payload
    return payload


def execute_change(
    root: Path, plan: ChangeVerificationPlan, *, mode: str, workers: int
) -> dict[str, Any]:
    require_current_plan(root, plan)
    if mode == "merge" and plan.unverified:
        raise ValueError("SELECTION_UNVERIFIED: " + "; ".join(plan.unverified))
    if (
        mode == "merge"
        and plan.behavioral_status == "REQUIRED"
        and not (plan.pytest or plan.frontend)
        and not all(
            is_generated_contract_path(path) or non_executable_documentation(root, path)
            for path in plan.changed_paths
        )
    ):
        raise ValueError("SELECTION_UNVERIFIED: executable changes have no behavioral proof")
    outcomes: list[dict[str, Any]] = []
    for label, command, cwd in _commands(root, plan):
        result = run_command(
            *command,
            cwd=cwd,
            timeout=300,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=64 * 1024,
            stream_output=lambda stream, text: print(
                text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
            ),
        )
        outcomes.append({"owner": label, "returncode": result.returncode})
        try:
            require_current_plan(root, plan)
        except ValueError as exc:
            return {
                "status": "FAIL" if result.returncode else "INCOMPLETE",
                "outcomes": outcomes,
                "reason": str(exc),
            }
        if result.returncode:
            return {"status": "FAIL", "outcomes": outcomes}
    if plan.frontend:
        with tempfile.TemporaryDirectory(prefix="oh-goon-change-frontend-") as directory:
            report = Path(directory) / "vitest.json"
            result = run_command(
                "npm",
                "run",
                "test",
                "--",
                *(path.removeprefix("frontend/") for path in plan.frontend),
                "--reporter=default",
                "--reporter=json",
                f"--outputFile={report}",
                cwd=root / "frontend",
                timeout=300,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                stream_output=lambda stream, text: print(
                    text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
                ),
            )
            outcomes.append(
                {
                    "owner": "Frontend behavior",
                    "returncode": result.returncode,
                    "native_project_root": str(root / "frontend"),
                }
            )
            native_frontend = _retain_native_report(outcomes[-1], report)
            if result.returncode:
                return {"status": "FAIL", "outcomes": outcomes}
            if not native_frontend:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native frontend evidence unavailable",
                }
            try:
                complete, satisfied, _counts = vitest_outcomes(native_frontend)
            except ValueError as exc:
                return {"status": "INCOMPLETE", "outcomes": outcomes, "reason": str(exc)}
            if not complete or not satisfied:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native frontend assertions are skipped, failed or incomplete",
                }
            outcomes[-1]["native_report"] = native_frontend
            require_current_plan(root, plan)
    if plan.pytest:
        with tempfile.TemporaryDirectory(prefix="oh-goon-change-result-") as directory:
            report = Path(directory) / "pytest.json"
            result = run_command(
                sys.executable,
                "-m",
                "oh_goon.repository_tooling.test_batches",
                "--pytest-config",
                "backend/pyproject.toml",
                "--qualified",
                "--show-output",
                "--granularity",
                "tests",
                "--workers",
                str(workers),
                "--batch-size",
                "64",
                "--batch-timeout",
                "180",
                "--marker",
                _MERGE_MARKER,
                "--report-path",
                str(report),
                *plan.pytest,
                cwd=root,
                timeout=None,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                stream_output=lambda stream, text: print(
                    text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
                ),
            )
            outcomes.append({"owner": "Native pytest", "returncode": result.returncode})
            native = _retain_native_report(outcomes[-1], report)
            if result.returncode:
                return {"status": "FAIL", "outcomes": outcomes}
            if not native:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native pytest evidence unavailable",
                }
            try:
                require_complete_membership(native, source_identity=plan.source_identity)
            except ValueError as exc:
                return {"status": "INCOMPLETE", "outcomes": outcomes, "reason": str(exc)}
            outcomes[-1]["native_report"] = native
    require_current_plan(root, plan)
    return {
        "status": "CHECKS_PASSED",
        "outcomes": outcomes,
        "daily_deferred": "Unrelated, exhaustive and daily-cadence instances; no full-regression claim",
        "performance": "PROVISIONAL",
    }


def _write_transport(path: Path, payload: Mapping[str, Any], root: Path) -> None:
    target = path.absolute()
    if target.is_relative_to(root) or target.parent.resolve(strict=True) != target.parent:
        raise ValueError("Verification transport must use canonical scratch outside source")
    metadata = target.parent.stat(follow_symlinks=False)
    if metadata.st_mode & 0o077:
        raise ValueError("Verification transport parent must be private")
    with prepared_text_file(target, json.dumps(payload, sort_keys=True) + "\n") as prepared:
        publish_prepared_file(prepared, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--candidate")
    parser.add_argument("--head")
    parser.add_argument("--mode", choices=("focused", "merge"), default="focused")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("workers must be positive")
    try:
        root = args.root.resolve(strict=True)
        plan = (
            ChangeVerificationPlan.model_validate(_read_json(args.execute))
            if args.execute
            else admit_change(
                root, base=args.base, candidate=args.candidate, requested_head=args.head
            )
        )
        if args.plan:
            _write_transport(args.plan, plan.model_dump(mode="json"), root)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as stream:
                    stream.write(f"frontend={'true' if 'frontend' in plan.static else 'false'}\n")
                    stream.write(f"pytest={'true' if plan.pytest else 'false'}\n")
            return 0
        result = execute_change(root, plan, mode=args.mode, workers=args.workers)
        payload = {"plan": plan.model_dump(mode="json"), **result}
        if result["status"] == "CHECKS_PASSED":
            payload["status"] = "FOCUSED / NOT_FULLY_VERIFIED"
            if args.mode == "merge":
                payload["status"] = "MERGE_PROOF_READY"
                payload["merge_authority"] = (
                    "UNVERIFIED: independent trusted-base aggregate required"
                )
        if args.report:
            _write_transport(args.report, payload, root)
        print(json.dumps(payload, sort_keys=True))
        return (
            0 if payload["status"] in {"MERGE_PROOF_READY", "FOCUSED / NOT_FULLY_VERIFIED"} else 1
        )
    except (ValueError, OSError) as exc:
        print(f"SELECTION_UNVERIFIED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
