"""Read-only UX-A evidence from the EXISTING compiler/catalog; not a validator."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import TypedDict

from oh_goon.core.template_identity import template_classification, template_usage
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.runtime.template_discovery import template_readme_description

_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^\)]+)\)")
_LEXICAL = ("pass", "with", "from", "run", "uses", "inputs", "outputs", "goons", "steps")
_EXAMPLE_CLASSIFICATIONS = frozenset(
    {
        "runnable-with-prerequisites",
        "preview-only-fixture",
        "integration-fixture",
        "source-capture-fixture",
        "configuration-example",
    }
)


class ExampleInventory(TypedDict):
    name: str
    readme_path: str
    readme_present: bool
    documented_classification: str
    classification_recognized: bool
    selection_present: bool
    qualification: str


class CatalogInventory(TypedDict):
    schema: str
    source_revision: dict[str, str | bool | None]
    basis: str
    limitation: str
    metrics: dict[str, int]
    packages: list[dict[str, object]]
    examples: list[ExampleInventory]
    readme_links: list[dict[str, str]]


def _source_revision(root: Path) -> dict[str, str | bool | None]:
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {
        "commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "tracked_worktree_changed": dirty.returncode != 0 if revision.returncode == 0 else None,
        "qualification": "source census only; no execution or usability claim",
    }


def _example_inventory(root: Path) -> list[ExampleInventory]:
    records: list[ExampleInventory] = []
    for directory in sorted((root / "examples").iterdir()):
        if not directory.is_dir():
            continue
        readme = directory / "README.md"
        content = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        match = re.search(r"(?mi)^Classification:\s*([a-z-]+)\s*$", content)
        classification = match.group(1) if match else "unclassified"
        records.append(
            {
                "name": directory.name,
                "readme_path": readme.relative_to(root).as_posix(),
                "readme_present": readme.is_file(),
                "documented_classification": classification,
                "classification_recognized": classification in _EXAMPLE_CLASSIFICATIONS,
                "selection_present": (directory / "run.yml").is_file(),
                "qualification": "documentation classification; execution not established",
            }
        )
    return records


def collect(root: Path) -> CatalogInventory:
    root = root.resolve()
    records: list[dict[str, object]] = []
    links: list[dict[str, str]] = []
    totals: Counter[str] = Counter()
    for kind, store in (
        ("goon", GoonTemplateStore(root / "templates/goons")),
        ("plan", PlanTemplateStore(root / "templates/plans")),
    ):
        for key in store.keys():
            snap = store.source(key)  # original source reader and canonical compiler
            compiled = snap.compiled
            steps = [
                {
                    "id": step.step_id,
                    "kind": step.kind,
                    **({"import": step.goon} if step.kind == "goon" else {}),
                }
                for step in compiled.steps
            ]
            refs = [step["import"] for step in steps if "import" in step]
            outputs = [
                {"name": output.name, "source": output.source} for output in compiled.outputs
            ]
            lexical = {
                n: len(re.findall(rf"(?m)^\s*(?:-\s*)?{n}:\s*", snap.content)) for n in _LEXICAL
            }
            readme = snap.path.with_name("README.md")
            records.append(
                {
                    "kind": kind,
                    "key": key,
                    "classification": str(template_classification(key, kind=kind.title())),
                    "purpose": template_readme_description(snap.path.parent, kind=kind.title()),
                    "source_path": snap.path.relative_to(root).as_posix(),
                    "root_source_sha256": snap.sha256,
                    "inputs_defined": [x.name for x in compiled.inputs],
                    "inputs_projected": list(compiled.projected_inputs),
                    "ordered_steps": steps,
                    "outputs": outputs,
                    "declared_requirements": [
                        x.model_dump(mode="json") for x in compiled.requirements
                    ],
                    "execution": compiled.execution.model_dump(mode="json")
                    if compiled.execution
                    else None,
                    "step_capabilities": [
                        {
                            "id": step.step_id,
                            "capabilities": step.capabilities.model_dump(mode="json"),
                        }
                        for step in compiled.steps
                        if hasattr(step, "capabilities")
                    ],
                    **template_usage(template_classification(key, kind=kind.title())),
                    "repeated_import_packages": sorted(
                        x for x, c in Counter(refs).items() if c > 1
                    ),
                    "authored_lexical_counts": lexical,
                    "readme_path": readme.relative_to(root).as_posix(),
                    "readme_present": readme.is_file(),
                }
            )
            totals[kind + "_packages"] += 1
            totals["import_occurrences"] += len(refs)
            totals["public_output_mappings"] += len(outputs)
            totals["public_output_expression_bytes"] += sum(
                len(o["source"].encode()) for o in outputs
            )
            totals["repeated_import_keys"] += sum(c > 1 for c in Counter(refs).values())
            for n, count in lexical.items():
                totals["lexical_" + n] += count
            if not readme.is_file():
                links.append(
                    {
                        "source": readme.relative_to(root).as_posix(),
                        "target": "",
                        "status": "README_MISSING",
                    }
                )
                continue
            for raw in _LINK.findall(readme.read_text(encoding="utf-8")):
                target = raw.split("#", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                path = (readme.parent / target).resolve()
                try:
                    path.relative_to(root)
                    status = "OK" if path.exists() else "MISSING"
                except ValueError:
                    status = "OUTSIDE_DISTRIBUTION"
                links.append(
                    {"source": readme.relative_to(root).as_posix(), "target": raw, "status": status}
                )
    totals["unresolved_readme_links"] = sum(x["status"] != "OK" for x in links)
    examples = _example_inventory(root)
    totals["example_directories"] = len(examples)
    totals["unclassified_examples"] = sum(not x["classification_recognized"] for x in examples)
    return {
        "schema": "oh-goon.template-ux-a-inventory/v2",
        "source_revision": _source_revision(root),
        "basis": "canonical shipped template stores / compile snapshots",
        "limitation": "lexical occurrence counts are authoring measurements, not semantic parsing",
        "metrics": dict(sorted(totals.items())),
        "packages": sorted(records, key=lambda p: (str(p["kind"]), str(p["key"]))),
        "examples": examples,
        "readme_links": sorted(links, key=lambda p: (p["source"], p["target"])),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    data = collect(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data["metrics"], sort_keys=True))
    return (
        0
        if data["packages"]
        and data["metrics"].get("unresolved_readme_links") == 0
        and data["metrics"].get("unclassified_examples") == 0
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
