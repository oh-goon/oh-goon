from __future__ import annotations

import json
import sys
from pathlib import Path

from oh_goon.services.canonical_goon_tool_projection import (
    derive_canonical_goon_tool_projection,
)
from oh_goon.services.goon_execution_requirements import (
    load_goon_execution_requirements,
    workload_tool_permissions,
)

ROOT = Path(__file__).resolve().parents[5]
OUTPUT = ROOT / "backend" / "src" / "oh_goon" / "canonical_goon_tool_authority.json"
SCHEMA = 1


def render_goon_tool_authority(root: Path) -> str:
    policy, _identity, workloads = load_goon_execution_requirements(root.resolve())
    projection = derive_canonical_goon_tool_projection(
        root,
        workload_permissions=workload_tool_permissions(policy),
        workloads=workloads,
    )
    if projection.missing_permissions:
        item = projection.missing_permissions[0]
        raise ValueError(
            "canonical Make workload has no independent tool permission: "
            f"{item.goon}/{item.step_id} -> {item.owner}"
        )
    if projection.permission_violations:
        item = projection.permission_violations[0]
        raise ValueError(
            "canonical workload need exceeds permission ceiling for "
            f"{item.goon}/{item.step_id}: {', '.join(item.missing_tools)}"
        )
    if projection.stale_permission_owners:
        raise ValueError(
            "independent workload permission has no canonical Make consumer: "
            + ", ".join(projection.stale_permission_owners)
        )
    return (
        json.dumps(
            {
                "schema": SCHEMA,
                "goons": {
                    key: {step_id: list(tools) for step_id, tools in sorted(steps.items())}
                    for key, steps in sorted(projection.tools_by_goon.items())
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def write_goon_tool_authority(*, root: Path = ROOT, output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_goon_tool_authority(root), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: oh-goon-generate-goon-tool-authority", file=sys.stderr)
        return 2
    output = write_goon_tool_authority()
    print(f"Generated {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
