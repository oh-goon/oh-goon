from __future__ import annotations

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.goon_static_admission import (
    REQUIREMENTS_FILE,
    compile_canonical_goons,
)


def check_goon_static_admission_contract() -> list[Violation]:
    payload = compile_canonical_goons(ROOT)
    violations: list[Violation] = []
    for issue in payload["issues"]:
        goon = issue.get("goon")
        path = (
            ROOT / "templates" / "goons" / str(goon) / "goon.yml"
            if goon
            else ROOT / REQUIREMENTS_FILE
        )
        violations.append(
            Violation(
                path,
                f"{issue['code']}: {issue['message']}"
                + (
                    f" missing_tools={','.join(issue['missing_tools'])}"
                    if issue.get("missing_tools")
                    else ""
                )
                + (
                    f" excess_tools={','.join(issue['excess_tools'])}"
                    if issue.get("excess_tools")
                    else ""
                ),
            )
        )
    return violations
