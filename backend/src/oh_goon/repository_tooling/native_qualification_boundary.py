"""Fail closed when make verify blurs deterministic and native qualification ownership."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REGISTRY = ROOT / "scripts/native_qualification_boundary.json"
VERIFY = ROOT / "scripts/application_verify.sh"
MAKEFILE = ROOT / "Makefile"

ROW_RE = re.compile(r'^\s*"[^|]+\|[^|]+\|([^"|]+)"\s*$', re.MULTILINE)


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    verify_text = VERIFY.read_text(encoding="utf-8")
    make_text = MAKEFILE.read_text(encoding="utf-8")
    rows = ROW_RE.findall(verify_text.split('if [[ "${1:-}" == "--list" ]]', 1)[0])
    native = set(data["native_boundary_targets"])
    portable = set(data["portable_exhaustive_targets"])
    expected = native | portable
    actual = set(rows)
    issues: list[str] = []
    if actual != expected:
        issues.append(
            f"verify target classification mismatch: missing={sorted(actual - expected)} stale={sorted(expected - actual)}"
        )
    if native & portable:
        issues.append(f"targets have dual authority: {sorted(native & portable)}")
    preflight_block = make_text.split("candidate-convergence-preflight:", 1)[1].split("\n\n", 1)[0]
    required_commands = {
        "release-locks": "release-locks-check",
        "release-version": "oh-goon-release-version check",
        "generated-contracts": "oh-goon-check-generated",
        "architecture": "oh-goon-architecture",
        "repository-understanding": "oh-goon-repository-understanding-exam verify",
    }
    if set(data["deterministic_preflight"]) != set(required_commands):
        issues.append("deterministic preflight registry differs from checker-owned required set")
    for name, command in required_commands.items():
        if command not in preflight_block:
            issues.append(f"deterministic preflight missing {name}: {command}")
    if "candidate-convergence-preflight" not in verify_text.split("for row in", 1)[0]:
        issues.append("verify does not run candidate convergence before plan projections")
    if issues:
        print("NATIVE QUALIFICATION BOUNDARY: FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(
        f"NATIVE QUALIFICATION BOUNDARY: PASS ({len(native)} native, {len(portable)} portable, {len(required_commands)} deterministic preflight owners)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
