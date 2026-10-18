"""Validate explicitly retained checkpoint archive evidence before archive sealing."""
from __future__ import annotations

import re
from pathlib import Path

from oh_goon.repository_tooling.release.seal_gate import require_checkpoint_version_freshness

ROOT = Path(__file__).resolve().parents[4]


def phase_evidence_issues(root: Path) -> list[str]:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    token = version.replace(".", "_")
    issues: list[str] = []
    try:
        require_checkpoint_version_freshness(root)
    except RuntimeError as exc:
        issues.append(str(exc))
    try:
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        issues.append(
            "phase evidence requires local archive metadata CHANGELOG.md; "
            "restore the verified checkpoint handoff before archive validation"
        )
        changelog = ""
    docs = list((root / "docs/development").glob(f"PHASE_{token}_*.md"))
    if len(docs) != 1:
        issues.append(f"expected exactly one current phase document, found {len(docs)}")
    elif not docs[0].read_text(encoding="utf-8").startswith(f"# {version} — "):
        issues.append("current phase document heading does not match VERSION")
    heading = re.compile(rf"^## {re.escape(version)} DEVELOPMENT\b", re.MULTILINE)
    if not heading.search(changelog):
        issues.append("CHANGELOG lacks current DEVELOPMENT heading")
    return issues


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    issues = phase_evidence_issues(ROOT)
    if issues:
        print("PHASE EVIDENCE CONVERGENCE: FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"PHASE EVIDENCE CONVERGENCE: PASS ({version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
