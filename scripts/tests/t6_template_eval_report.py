#!/usr/bin/env python3
"""Fail-closed evidence projection from actual T6 pytest/JUnit and source bytes.

This is a repository-tooling diagnostic, never execution/certification authority.
All oracles live in the T6 corpus. No user input values enter the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

_SCHEMA = "oh-goon.template-t6-evaluation/v1"
_CASE = "test_t6_full_correction_journey_with_real_shipped_plan_and_goons"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_evidence(source_zip: Path, source_root: Path) -> dict[str, object]:
    if not source_zip.is_file() or not source_root.is_dir():
        raise ValueError(
            "Exact source candidate and checked-out source root are required"
        )
    names: set[str] = set()
    lock_hashes: dict[str, str] = {}
    with ZipFile(source_zip) as archive:
        for info in archive.infolist():
            name = info.filename
            if (
                name in names
                or info.is_dir()
                or name.startswith("/")
                or ".." in Path(name).parts
                or "\\" in name
            ):
                raise ValueError("Archive contains noncanonical/duplicate member")
            path = source_root / name
            if (
                not path.is_file()
                or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).digest()
                != hashlib.sha256(archive.read(info)).digest()
            ):
                raise ValueError("Source checkout differs from candidate archive")
            names.add(name)
            if name in {"backend/uv.lock", "frontend/package-lock.json"}:
                lock_hashes[name] = hashlib.sha256(archive.read(info)).hexdigest()
    if set(lock_hashes) != {"backend/uv.lock", "frontend/package-lock.json"}:
        raise ValueError("Native dependency authority missing from source archive")
    return {
        "source_sha256": _digest(source_zip),
        "source_members": len(names),
        "locks": lock_hashes,
    }


def evaluation_report(
    *, corpus: Path, junit: Path, source_zip: Path, source_root: Path
) -> dict[str, object]:
    spec = json.loads(corpus.read_text(encoding="utf-8"))
    if spec.get("schema") != "oh-goon.template-t6-journey-corpus/v1":
        raise ValueError("Unexpected T6 evaluation corpus schema")
    journeys = spec["journeys"]
    ids = [item["id"] for item in journeys]
    if (
        len(ids) < 3
        or len(ids) != len(set(ids))
        or not all(re.fullmatch(r"[a-z0-9-]+", item) for item in ids)
    ):
        raise ValueError("T6 journey oracle is incomplete or ambiguous")
    expected = {_CASE + "[" + item + "]" for item in ids}
    expected |= set(spec["required_named_tests"])
    if len(expected) != len(ids) + len(spec["required_named_tests"]):
        raise ValueError("Duplicate T6 test identity in corpus")

    root = ElementTree.parse(junit).getroot()
    testcases = root.findall(".//testcase")
    actual = [item.get("name", "") for item in testcases]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(
            "Actual JUnit test identity differs from the frozen T6 evaluation corpus"
        )
    failures = [
        item.get("name")
        for item in testcases
        if any(item.find(tag) is not None for tag in ("failure", "error", "skipped"))
    ]
    if failures:
        raise ValueError(
            "T6 evaluation has failed, errored or skipped journeys: "
            + ",".join(failures)
        )
    # The JUnit report, not a caller-supplied pass count, owns observed outcomes.
    checks = [{"id": item, "outcome": "pass"} for item in sorted(actual)]
    evidence = {
        "schema": _SCHEMA,
        "status": "HOSTED_CORPUS_PASS_NATIVE_NOT_ESTABLISHED",
        "authority": "read_only_evaluation_not_execution_or_admission",
        "scope": "testclient_real_local_store_canonical_resolver_shipped_templates; no browser or subprocess execution",
        "corpus_sha256": _digest(corpus),
        "junit_sha256": _digest(junit),
        "case_count": len(journeys),
        "checks_passed": len(actual),
        "checks_total": len(expected),
        "checks": checks,
        "source": _source_evidence(source_zip, source_root),
        "limitations": [
            "No native WSL qualification asserted",
            "No browser Playwright, Vitest or TypeScript typecheck asserted",
            "No actual Fleet run, admission, APP PASS or release asserted",
            "Run/League are negative-call mocks; HTTP, saved file store and shipped resolver are real",
            "No latency, tool-token savings, user success rate or causal impact inferred",
        ],
    }
    evidence["evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluation_report(
            corpus=args.corpus,
            junit=args.junit,
            source_zip=args.source_zip,
            source_root=args.source_root,
        )
    except (ValueError, OSError, KeyError, TypeError, ElementTree.ParseError) as exc:
        print(f"T6 EVALUATION REJECTED: {type(exc).__name__}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"T6 EVALUATION: {report['checks_passed']}/{report['checks_total']} PASS; evidence {report['evidence_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
