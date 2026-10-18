from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon.services.goon_static_admission import (
    REQUIREMENTS_FILE,
    SCHEMA,
    StaticAdmissionIssue,
    canonical_step_required_tools,
    compile_canonical_goons,
)

__all__ = [
    "REQUIREMENTS_FILE",
    "SCHEMA",
    "StaticAdmissionIssue",
    "canonical_step_required_tools",
    "compile_canonical_goons",
    "main",
]


def _print_issue(issue: dict[str, object]) -> None:
    location = str(issue.get("goon") or "catalog")
    if issue.get("step_id"):
        location += f"/{issue['step_id']}"
    print(f"- {issue['code']} {location}: {issue['message']}")
    if issue.get("missing_tools"):
        print("  Missing tools: " + ", ".join(issue["missing_tools"]))
    if issue.get("excess_tools"):
        print("  Excess tools: " + ", ".join(issue["excess_tools"]))
    for path in issue.get("authority_paths") or ():
        print("  Required by: " + path)
    for edge in issue.get("unresolved_edges") or ():
        print("  Unresolved: " + edge)


def _print_human(payload: dict[str, object]) -> None:
    print("CANONICAL GOON STATIC ADMISSION")
    print(f"Status: {payload['status']}")
    print(f"Goons: {payload['goon_count']}")
    print(f"Steps: {payload['step_count']}")
    print(f"Requirements: {payload['requirements_identity']}")
    print(f"Execution authority: {payload['execution_authority_identity']}")
    print(
        "Authority closures: "
        f"{payload['authority_closures_proven']}/{payload['authority_closure_count']} proven"
    )
    print(f"Unresolved execution edges: {payload['unresolved_execution_edge_count']}")
    print(
        f"Locked package-local executables: {payload['package_executable_authority_count']} proven"
    )
    issues = payload["issues"]
    if not issues:
        print("All canonical Goons compile with closed declared execution authority.")
        return
    print("Issues:")
    for issue in issues:
        _print_issue(issue)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Statically validate every canonical Goon without executing workloads."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    payload = compile_canonical_goons(args.root)
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
