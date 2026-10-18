"""Validate a locally retained CO-R01 roadmap archive when explicitly requested.

This archive check is not registered in ordinary repository architecture checks.
A Git checkout does not need project history to develop, test, or run Oh-Goon.
Neither the TODO nor this checker is an executable admission/release policy.
"""

from __future__ import annotations

import re
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

_ROADMAP = "OH_GOON_FORWARD_TODO.md"


def check_co_r01_roadmap_integrity(root: Path = ROOT) -> list[Violation]:
    path = root / _ROADMAP
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return [Violation(path, "CO-R01 sole roadmap authority missing")]
    issues: list[Violation] = []
    # CO-R3T moves historical CO-R01 checklist lines to a separate immutable
    # archive. Preserve those lines and one active roadmap; don't force stale
    # "CURRENT" section labels back into today's active TODO.
    if (root / ".checkpoint/CO_R3T_SOURCE.sha256").is_file():
        archive = root / "OH_GOON_FORWARD_TODO_HISTORY_CO_R3S.md"
        try:
            past = archive.read_text(encoding="utf-8")
        except OSError:
            issues.append(Violation(archive, "CO-R01 historical lifecycle checklist missing"))
            past = ""
        for checkpoint in ("CO-R0 + CO-R1", "CO-R2b", "CO-R2a", "CO-R2c", "CO-R3", "CO-R4"):
            pattern = rf"(?m)^- \[[ x]\] \*\*{re.escape(checkpoint)}(?:\s+—|\s+\()"
            if len(re.findall(pattern, past)) != 1:
                issues.append(
                    Violation(
                        archive, f"CO-R01 historical {checkpoint} invariant missing or duplicated"
                    )
                )
        for checkpoint in ("CO-R3T", "CO-R3U", "CO-R3V", "CO-R3W", "CO-R3X"):
            if len(re.findall(rf"(?m)^### \[[ x]\] {checkpoint} \u2014", content)) != 1:
                issues.append(
                    Violation(
                        path, f"CO-R01 active roadmap requires exactly one {checkpoint} work item"
                    )
                )
        for fact in ("Gate A", "Gate B", "Gate C", "CO-R2b", "P3C", "APP PASS", "release"):
            if fact not in content:
                issues.append(Violation(path, f"CO-R01 active gate missing: {fact}"))
        for doc in (
            "ADR_CANONICAL_OWNER_REPLACEABLE_IMPLEMENTATIONS.md",
            "CO_R01_CANONICAL_OWNER_CENSUS.md",
        ):
            if not (root / "docs/architecture" / doc).is_file() or doc not in past:
                issues.append(
                    Violation(archive, f"CO-R01 original owner decision/census missing: {doc}")
                )
        if (root / "docs/architecture/OH_GOON_FORWARD_TODO.md").exists():
            issues.append(Violation(path, "CO-R01 second authoritative TODO forbidden"))
        return issues
    issues: list[Violation] = []
    for checkpoint in ("CO-R0 + CO-R1", "CO-R2b", "CO-R2a", "CO-R2c", "CO-R3", "CO-R4"):
        pattern = rf"(?m)^- \[[ x]\] \*\*{re.escape(checkpoint)}(?:\s+—|\s+\()"
        if len(re.findall(pattern, content)) != 1:
            issues.append(
                Violation(
                    path, f"CO-R01 roadmap requires exactly one active {checkpoint} work item"
                )
            )
    if content.count("# CURRENT — CO-R01:") != 1:
        issues.append(Violation(path, "CO-R01 current checkpoint must remain uniquely recorded"))
    for doc in (
        "ADR_CANONICAL_OWNER_REPLACEABLE_IMPLEMENTATIONS.md",
        "CO_R01_CANONICAL_OWNER_CENSUS.md",
    ):
        if not (root / "docs/architecture" / doc).is_file() or doc not in content:
            issues.append(Violation(path, f"CO-R01 owner decision/census missing: {doc}"))
    # No second forward roadmap is admitted by the source at this checkpoint.
    if (root / "docs/architecture/OH_GOON_FORWARD_TODO.md").exists():
        issues.append(Violation(path, "CO-R01 second authoritative TODO forbidden"))
    return issues
