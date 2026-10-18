"""UX-D quality gates: no alternate compiler, no made-up human trial, no work."""
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.repository_tooling.template_ux_d_evidence import (
    GUIDES, collect, guide_links, source_archive_closure,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.template_plan_drafts import propose_plan


ROOT = Path(__file__).resolve().parents[4]


def test_real_catalog_compile_and_draft_equivalence_without_execution():
    proof = collect(ROOT)
    assert proof["status"] == "PASS", proof["missing_or_bad_guide_links"]
    assert proof["catalog"]["goons"] == 41
    assert proof["catalog"]["plans"] == 24
    assert len(proof["plan_drafts"]) == 24
    assert all(r["status"] == "PASS" for r in proof["plan_drafts"])
    assert all(r["comparison_is_execution"] is False for r in proof["plan_drafts"])
    assert proof["evaluation"]["human_median_authoring_time"] == "NOT_MEASURED"
    assert proof["evaluation"]["usability_20_percent_target"] == "UNDETERMINED"
    assert proof["execution_permission"] == "NONE"
    assert proof["gate_b"] == "BLOCKED"


def test_real_canonical_stores_are_unchanged_across_full_catalog_evaluation():
    before = [(k, PlanTemplateStore().source(k).sha256) for k in PlanTemplateStore().keys()]
    collect(ROOT)
    after = [(k, PlanTemplateStore().source(k).sha256) for k in PlanTemplateStore().keys()]
    assert after == before


@pytest.mark.parametrize("key", ["python/standard", "oci/image", "kubernetes/set-image", "oh-goon/self-verification"])
def test_real_golden_domain_drafts_are_unadmitted(key):
    source = PlanTemplateStore().source(key)
    result = propose_plan(key=key, expected_sha256=source.sha256)
    assert result.report["status"] == "DRAFT_ONLY_NOT_ADMITTED"
    assert result.report["closure_digest"] is None
    assert result.report["execution_permission"] == "NONE"
    assert result.report["before"] == result.report["after"]


@pytest.mark.parametrize("edits", [
    [{"op": "add", "at": 1, "import": "general/hello-world"}],
    [{"op": "add", "at": 1, "import": "missing/unknown"}],
    [{"op": "remove", "at": 100}],
    [{"op": "move", "from": 99, "to": 0}],
    [{"op": "add", "at": 0, "import": {"import": "python/ruff", "as": "python-quality"}}],
    [{"op": "add", "at": 0, "import": {"import": "python/ruff", "run": "sh -c evil"}}],
    [{"op": "add", "at": 0, "import": {"import": "python/ruff", "as": "bad*name"}}],
    [{"op": "remove", "at": 0}],
])
def test_hostile_authoring_never_mutates_original_source(edits):
    source = PlanTemplateStore().source("python/standard")
    with pytest.raises(FleetPlanInvalid):
        propose_plan(key="python/standard", expected_sha256=source.sha256, edits=edits)
    assert PlanTemplateStore().source("python/standard").sha256 == source.sha256


@pytest.mark.parametrize("values", [
    {"unknown_value": "x"},
    {"release_channel": "all"},
    {"release_channel": "${{ env.SECRET }}"},
    {"revision": ["not-a-scalar"]},
    {"coverage_threshold": True},
    {"coverage_threshold": 99999},
])
def test_hostile_values_fail_before_any_work(values):
    source = PlanTemplateStore().source("python/standard")
    with pytest.raises(FleetPlanInvalid):
        propose_plan(key="python/standard", expected_sha256=source.sha256, values=values)


def test_stale_source_revision_rejected():
    with pytest.raises(FleetPlanInvalid, match="stale"):
        propose_plan(key="python/standard", expected_sha256="0" * 64,
                     edits=[{"op": "move", "from": 0, "to": 1}])


def test_distribution_links_present_and_resolve():
    assert not [r for r in guide_links(ROOT) if r["status"] != "OK"]


def test_archive_closure_catches_missing_and_unsafe(tmp_path):
    source = tmp_path / "source.zip"
    with ZipFile(source, "w") as zipfile:
        zipfile.writestr(GUIDES[0], "OK")
        zipfile.writestr("../escape", "BAD")
    result = source_archive_closure(source)
    assert result["status"] == "FAIL"
    assert "../escape" in result["unsafe_names"]
    assert len(result["missing_guides"]) == len(GUIDES) - 1


def test_archive_closure_complete(tmp_path):
    source = tmp_path / "source.zip"
    with ZipFile(source, "w") as zipfile:
        for name in GUIDES:
            zipfile.writestr(name, "guide")
    assert source_archive_closure(source)["status"] == "PASS"


def test_evidence_can_be_serialized_without_caller_values():
    proof = collect(ROOT)
    out = json.dumps(proof)
    assert '"save_permission": "NONE"' in out
    assert '"release": "BLOCKED"' in out


def test_development_qualifier_selects_ux_d_exact_manifest_after_ux_c():
    source = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    c = "if [[ -f .checkpoint/UX_C_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_C_SOURCE.sha256'; fi"
    d = "if [[ -f .checkpoint/UX_D_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_D_SOURCE.sha256'; fi"
    assert source.count(c) == source.count(d) == 1
    assert source.index(c) < source.index(d) < source.index('if [[ -f "$embedded" ]]')
    make = (ROOT / "Makefile").read_text()
    assert 'qualify-development-wsl' in make
    assert 'source-transport-clean' in make


def test_ux_d_evaluator_never_creates_a_second_authority():
    source = (ROOT / "backend/src/oh_goon/repository_tooling/template_ux_d_evidence.py").read_text()
    assert "propose_plan(" in source and "PlanTemplateStore(" in source
    assert "source_archive_closure" in source
    for banned in ("FleetRunLifecycle", "RUN_ADMITTED", "RESOURCE_INTENT",
                   "start_run(", "yaml.safe_load", "subprocess.run", "os.system"):
        assert banned not in source


def test_frozen_ux_d_corpus_uses_exact_current_sources():
    expected = json.loads((ROOT / "backend/tests/fixtures/template_authoring/draft_evaluation.json").read_text())
    actual = collect(ROOT)
    assert actual == expected
    assert len(actual["plan_drafts"]) == 24
    assert actual["ux_d_final_exit_gate"] == "BLOCKED_PENDING_PAIRED_AND_NATIVE_EVIDENCE"


def test_archive_closure_rejects_symlink_entry(tmp_path):
    from zipfile import ZipInfo
    path = tmp_path / "symlink.zip"
    with ZipFile(path, "w") as archive:
        for name in GUIDES:
            archive.writestr(name, "doc")
        link = ZipInfo("docs/ESCAPE")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "../../../../tmp")
    result = source_archive_closure(path)
    assert result["status"] == "FAIL"
    assert "docs/ESCAPE" in result["unsafe_names"]
