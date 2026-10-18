"""Native Step executable: apply one operator-selected source-bound proposal.

Use only from an explicitly write-authorized ordinary Fleet command Step.
"""

from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.agent_patch_apply import (
    BatchApplyIncomplete,
    apply_batch_proposal,
    apply_selected_proposal,
)


def run_selected_proposal(args) -> int:
    """Consume one explicitly authored Step request via the original oh-goon CLI."""
    try:
        if getattr(args, "write_roots", None):
            receipt = apply_batch_proposal(
                args.proposal,
                workspace=Path.cwd(),
                candidate_identity=args.expected_candidate_identity,
                write_roots=tuple(args.write_roots),
            )
            print("APPLIED " + ", ".join(receipt.affected_paths))
            return 0
        path, sha256 = apply_selected_proposal(
            args.proposal,
            workspace=Path.cwd(),
            expected_path=args.expected_path,
            expected_handoff_identity=args.expected_handoff_identity,
        )
    except BatchApplyIncomplete as exc:
        import sys

        print(str(exc), file=sys.stderr)
        return 2
    except (FleetPlanInvalid, OSError) as exc:
        import sys

        print(f"Agent proposal not applied: {exc}", file=sys.stderr)
        return 2
    print(f"APPLIED {path} sha256:{sha256}")
    return 0
