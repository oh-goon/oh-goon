"""Read-only T0A projection; refuses unaccounted trust implementation drift."""
from __future__ import annotations

import sys

from oh_goon.repository_tooling.architecture.trust_authority_registry_contract import (
    check_trust_authority_registry,
    render_trust_census,
)


def main() -> int:
    issues = check_trust_authority_registry()
    if issues:
        for issue in issues:
            print(f"T0A BLOCKED {issue.path}: {issue.message}", file=sys.stderr)
        return 1
    sys.stdout.write(render_trust_census())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
