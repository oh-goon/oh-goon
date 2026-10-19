"""Generate read-only UX-C Plan/values text; never save a Run, Plan or Schedule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.template_plan_drafts import propose_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan")
    parser.add_argument("--expect-sha256")
    parser.add_argument("--catalog", action="store_true", help="Use the existing Goon catalog")
    parser.add_argument("--request", type=Path, help="Explicit local edits/values JSON")
    parser.add_argument("--output", type=Path, help="Diagnostic JSON output only")
    args = parser.parse_args()
    if args.catalog:
        if args.plan or args.expect_sha256 or args.request:
            parser.error("catalog is a read-only separate query")
        payload = (
            json.dumps(
                GoonTemplateStore().catalog().model_dump(mode="json"), sort_keys=True, indent=2
            )
            + "\n"
        )
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0
    if not args.plan or not args.expect_sha256:
        parser.error("a draft requires both --plan and --expect-sha256")
    request = json.loads(args.request.read_text(encoding="utf-8")) if args.request else {}
    if type(request) is not dict or set(request) - {"edits", "values", "projected_inputs"}:
        parser.error("request may contain only edits, values and projected_inputs")
    draft = propose_plan(
        key=args.plan,
        expected_sha256=args.expect_sha256,
        edits=request.get("edits", ()),
        values=request.get("values"),
        projected_inputs=request.get("projected_inputs"),
    )
    payload = (
        json.dumps(
            {
                "plan_yaml": draft.plan_yaml,
                "values_yaml": draft.values_yaml,
                "report": draft.report,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
