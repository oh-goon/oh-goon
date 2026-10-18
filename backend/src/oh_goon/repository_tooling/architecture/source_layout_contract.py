from __future__ import annotations

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.source_layout import (
    canonical_source_layout_issues,
    summarize_source_layout_issues,
)


def check_source_layout_contract() -> list[Violation]:
    issues = summarize_source_layout_issues(
        ROOT,
        canonical_source_layout_issues(ROOT),
        debug=False,
    )
    return [Violation(path=issue.path, message=issue.reason) for issue in issues]
