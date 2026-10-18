"""T6 evidence cannot claim a PASS from wrong/missing/failed JUnit identity."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from importlib.util import module_from_spec, spec_from_file_location


def _load_evaluator():
    # Synthetic module identity is test-only and loader is confined to this function.
    spec = spec_from_file_location(
        "_oh_goon_test_t6_template_eval_report",
        Path(__file__).with_name("t6_template_eval_report.py"),
    )
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluation_report


evaluation_report = _load_evaluator()


@pytest.fixture
def evidence_inputs(tmp_path: Path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema": "oh-goon.template-t6-journey-corpus/v1",
                "journeys": [{"id": item} for item in ("one", "two", "three")],
                "required_named_tests": ["test_t6_revision"],
            }
        ),
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    suite = ElementTree.Element("testsuite")
    for item in ("one", "two", "three"):
        ElementTree.SubElement(
            suite,
            "testcase",
            name=f"test_t6_full_correction_journey_with_real_shipped_plan_and_goons[{item}]",
        )
    ElementTree.SubElement(suite, "testcase", name="test_t6_revision")
    ElementTree.ElementTree(suite).write(junit)
    source = tmp_path / "candidate.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("backend/uv.lock", b"frozen back-end")
        archive.writestr("frontend/package-lock.json", b"frozen front-end")
    checkout = tmp_path / "checkout"
    (checkout / "backend").mkdir(parents=True)
    (checkout / "frontend").mkdir(parents=True)
    (checkout / "backend/uv.lock").write_bytes(b"frozen back-end")
    (checkout / "frontend/package-lock.json").write_bytes(b"frozen front-end")
    return corpus, junit, source, checkout


def test_t6_evidence_follows_exact_junit_corpus_and_source_bytes(evidence_inputs):
    corpus, junit, source, checkout = evidence_inputs
    report = evaluation_report(
        corpus=corpus, junit=junit, source_zip=source, source_root=checkout
    )
    assert report["checks_passed"] == 4 and report["checks_total"] == 4
    assert report["case_count"] == 3
    assert report["source"]["source_members"] == 2
    assert report["authority"] == "read_only_evaluation_not_execution_or_admission"
    assert report == evaluation_report(
        corpus=corpus, junit=junit, source_zip=source, source_root=checkout
    )


@pytest.mark.parametrize(
    "corruption", ["failure", "skipped", "missing", "duplicate", "wrong"]
)
def test_t6_evidence_rejects_unearned_pass(evidence_inputs, corruption):
    corpus, junit, source, checkout = evidence_inputs
    root = ElementTree.parse(junit).getroot()
    children = root.findall("testcase")
    if corruption in ("failure", "skipped"):
        ElementTree.SubElement(children[0], corruption)
    elif corruption == "missing":
        root.remove(children[0])
    elif corruption == "duplicate":
        root.append(children[0])
    else:
        children[0].set("name", "unrelated-fake-test")
    ElementTree.ElementTree(root).write(junit)
    with pytest.raises(ValueError):
        evaluation_report(
            corpus=corpus, junit=junit, source_zip=source, source_root=checkout
        )


def test_t6_evidence_rejects_noncanonical_archive(evidence_inputs):
    corpus, junit, source, checkout = evidence_inputs
    with ZipFile(source, "a") as archive:
        archive.writestr("../injected", b"x")
    with pytest.raises(ValueError, match="noncanonical"):
        evaluation_report(
            corpus=corpus, junit=junit, source_zip=source, source_root=checkout
        )


def test_t6_evidence_rejects_changed_source_file(evidence_inputs):
    corpus, junit, source, checkout = evidence_inputs
    (checkout / "backend/uv.lock").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="differs"):
        evaluation_report(
            corpus=corpus, junit=junit, source_zip=source, source_root=checkout
        )
