"""UX-D reproducible, read-only template evidence; not a second resolver or certifier.

All YAML meaning comes from the existing template stores, compiler and UX-C
proposal service. No workload, Run, Schedule, credential or output is created.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.repository_tooling.template_ux_a_inventory import collect as collect_catalog
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.template_plan_drafts import propose_plan

GUIDES = (
    "templates/README.md",
    "docs/development/GOON_AUTHORING.md",
    "docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md",
    "docs/development/TEMPLATE_INPUT_INSPECTION.md",
    "docs/templates/TEMPLATE_UX_C_AUTHORING.md",
    "docs/templates/TEMPLATE_UX_D_EVALUATION.md",
)
_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def guide_links(root: Path) -> list[dict[str, str]]:
    """Inspect Markdown relative links, not authoring or execution semantics."""
    results: list[dict[str, str]] = []
    for guide in GUIDES:
        doc = root / guide
        if not doc.is_file():
            results.append({"source": guide, "target": "", "status": "MISSING_GUIDE"})
            continue
        for raw in _LINK.findall(doc.read_text(encoding="utf-8")):
            target = raw.strip().split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            if target.startswith(("/", "\\")):
                status = "ABSOLUTE_LINK"
            else:
                resolved = (doc.parent / target).resolve()
                try:
                    resolved.relative_to(root.resolve())
                except ValueError:
                    status = "OUTSIDE_SOURCE"
                else:
                    status = "OK" if resolved.exists() else "MISSING"
            results.append({"source": guide, "target": raw, "status": status})
    return sorted(results, key=lambda item: (item["source"], item["target"]))


def source_archive_closure(path: Path) -> dict:
    """Check whether a standalone source archive actually includes UX guides.

    This is a packaging presence audit, not release/source trust verification.
    """
    with ZipFile(path) as archive:
        entries = archive.infolist()
    seen: set[str] = set()
    unsafe: list[str] = []
    for info in entries:
        name = info.filename
        p = PurePosixPath(name)
        kind = (info.external_attr >> 16) & 0o170000
        if (name.startswith("/") or "\\" in name or ".." in p.parts or
                not name or name.startswith("./") or name in seen or
                kind == 0o120000):
            unsafe.append(name)
        seen.add(name)
    missing = sorted(set(GUIDES) - seen)
    return {"status": "PASS" if not unsafe and not missing else "FAIL",
            "guide_members_present": len(GUIDES) - len(missing),
            "guide_members_expected": len(GUIDES),
            "missing_guides": missing, "unsafe_names": unsafe}


def collect(root: Path) -> dict:
    """Deterministic complete catalog and no-op authoring evaluation."""
    root = root.resolve()
    plans = PlanTemplateStore(root / "templates/plans")
    goons = GoonTemplateStore(root / "templates/goons")
    inventory = collect_catalog(root)
    results = []
    for key in plans.keys():
        source = plans.source(key)
        try:
            draft = propose_plan(key=key, expected_sha256=source.sha256,
                                 plans=plans, goons=goons)
            parsed = parse_plan_template(draft.plan_yaml,
                                         expected_name=source.path.parent.name)
            consistent = (parsed.steps == source.compiled.steps and
                          draft.report["status"] == "DRAFT_ONLY_NOT_ADMITTED" and
                          draft.report["execution_permission"] == "NONE" and
                          draft.report["save_permission"] == "NONE" and
                          draft.report["original_root_sha256"] == source.sha256)
            results.append({"key": key, "status": "PASS" if consistent else "FAIL",
                            "source_sha256": source.sha256,
                            "source_bytes": len(source.content.encode("utf-8")),
                            "draft_bytes": len(draft.plan_yaml.encode("utf-8")),
                            "step_occurrences": len(parsed.steps),
                            "missing_required_names": draft.report["missing_required_inputs"],
                            "resolved_status": draft.report["resolved_comparison"]["status"],
                            "comparison_is_execution": False})
        except Exception as exc:  # One broken catalog member must not erase the rest.
            results.append({"key": key, "status": "FAIL", "error_type": type(exc).__name__})
    link_results = guide_links(root)
    invalid_links = [v for v in link_results if v["status"] != "OK"]
    package_results = inventory["packages"]
    catalog_good = (len(package_results) == 65 and
                    sum(p["kind"] == "goon" for p in package_results) == 41 and
                    sum(p["kind"] == "plan" for p in package_results) == 24 and
                    inventory["metrics"].get("unresolved_readme_links", 0) == 0)
    success = (catalog_good and len(results) == 24 and
               all(x["status"] == "PASS" for x in results) and not invalid_links)
    return {
        "schema": "oh-goon.template-ux-d-evidence/v1",
        "status": "PASS" if success else "FAIL",
        "authority": "existing catalog stores, canonical compiler, UX-C draft service",
        "execution_permission": "NONE", "save_permission": "NONE",
        "catalog": {"goons": 41, "plans": len(results),
                    "canonical_inventory_status": "PASS" if catalog_good else "FAIL",
                    "readme_missing_or_bad_links": inventory["metrics"].get("unresolved_readme_links")},
        "plan_drafts": results,
        "guide_links": link_results,
        "missing_or_bad_guide_links": invalid_links,
        "evaluation": {
            "method": "scripted deterministic no-op compilation and draft analysis",
            "passing_plan_drafts": sum(x["status"] == "PASS" for x in results),
            "human_median_authoring_time": "NOT_MEASURED",
            "agent_median_authoring_time": "NOT_MEASURED",
            "median_edit_count_improvement": "NOT_MEASURED",
            "reference_baseline_paired_trial": "NOT_RUN",
            "browser_e2e": "NOT_RUN",
            "native_wsl": "NOT_RUN",
            "actual_run_workload": "NOT_RUN",
            "usability_20_percent_target": "UNDETERMINED",
            "output_shorthand_25_percent_target": "NOT_APPLICABLE_NO_GO",
            "wheel_distribution": "NOT_BUILT",
            "compact_distribution": "NOT_PROVIDED",
            "handoff_distribution": "PENDING_ARTIFACT_AUDIT",
        },
        "gate_a": "BLOCKED", "gate_b": "BLOCKED",
        "app_pass": "BLOCKED", "release": "BLOCKED",
        "ux_d_final_exit_gate": "BLOCKED_PENDING_PAIRED_AND_NATIVE_EVIDENCE",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--source-archive", type=Path)
    args = ap.parse_args()
    payload = collect(args.root)
    if args.source_archive:
        payload["source_archive_closure"] = source_archive_closure(args.source_archive)
        if payload["source_archive_closure"]["status"] != "PASS":
            payload["status"] = "FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "catalog": payload["catalog"],
                      "drafts": payload["evaluation"]["passing_plan_drafts"]}, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
