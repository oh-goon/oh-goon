from __future__ import annotations

import os
import subprocess
from pathlib import Path

import oh_goon.repository_tooling.source_layout as source_layout
from oh_goon.core.source_tree_policy import CANONICAL_CERTIFICATION_FIXTURE_ROOT
from oh_goon.repository_tooling.source_layout import (
    CANONICAL_BACKEND_LOCK,
    CANONICAL_BACKEND_VENV,
    canonical_source_layout_issues,
    clean_generated_venvs,
    clean_source_transport_artifacts,
    prepared_source_tree_issues,
    source_transport_artifact_issues,
)

ROOT = Path(__file__).resolve().parents[2]


def _minimal_source(root: Path) -> None:
    (root / "backend").mkdir(parents=True)
    (root / "backend/uv.lock").write_text("version = 1\npackage = []\n", encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend/package-lock.json").write_text('{}\n', encoding="utf-8")


def test_live_canonical_venv_is_allowed_but_prepared_source_rejects_it(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    venv = tmp_path / Path(CANONICAL_BACKEND_VENV)
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("generated\n", encoding="utf-8")
    assert canonical_source_layout_issues(tmp_path, check_tracked_generated=False) == ()
    issues = prepared_source_tree_issues(tmp_path)
    assert any(issue.path == venv for issue in issues)




def test_vite_runtime_cache_is_transient_source_state(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    cache = tmp_path / "frontend/.oh-goon-vite-cache"
    cache.mkdir()
    (cache / "deps.json").write_text("{}\n", encoding="utf-8")
    assert canonical_source_layout_issues(tmp_path, check_tracked_generated=False) == ()
    issues = prepared_source_tree_issues(tmp_path)
    assert any(issue.path == cache for issue in issues)

def test_committed_backend_lock_must_be_a_regular_non_symlink_file(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    canonical = tmp_path / Path(CANONICAL_BACKEND_LOCK)
    outside = tmp_path / "actual.lock"
    outside.write_text('version = 1\npackage = []\n', encoding="utf-8")
    canonical.unlink()
    canonical.symlink_to(outside)
    issues = canonical_source_layout_issues(tmp_path, check_tracked_generated=False)
    assert any(issue.path == canonical and "regular non-symlink" in issue.reason for issue in issues)


def test_second_source_uv_lock_is_rejected(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    extra = tmp_path / "tools/uv.lock"
    extra.parent.mkdir()
    extra.write_text('version = 1\npackage = []\n', encoding="utf-8")
    issues = canonical_source_layout_issues(tmp_path, check_tracked_generated=False)
    assert any(
        issue.path == extra and "dependency authority belongs at" in issue.reason
        for issue in issues
    )



def test_certification_fixture_uv_lock_is_a_scoped_source_authority(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    fixture = tmp_path / Path(CANONICAL_CERTIFICATION_FIXTURE_ROOT) / "ml/iris"
    fixture.mkdir(parents=True)
    (fixture / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (fixture / "uv.lock").write_text('version = 1\npackage = []\n', encoding="utf-8")

    assert canonical_source_layout_issues(tmp_path, check_tracked_generated=False) == ()


def test_certification_fixture_uv_lock_requires_its_own_pyproject(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    fixture = tmp_path / Path(CANONICAL_CERTIFICATION_FIXTURE_ROOT) / "ml/iris"
    fixture.mkdir(parents=True)
    lock = fixture / "uv.lock"
    lock.write_text('version = 1\npackage = []\n', encoding="utf-8")

    issues = canonical_source_layout_issues(tmp_path, check_tracked_generated=False)

    assert any(
        issue.path == lock
        and "requires a sibling regular non-symlink pyproject.toml" in issue.reason
        for issue in issues
    )

def test_renamed_nested_venv_is_still_excluded_by_name(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    nested = tmp_path / "tools/.venv"
    nested.mkdir(parents=True)
    issues = prepared_source_tree_issues(tmp_path)
    assert any(issue.path == nested for issue in issues)


def test_venv_clean_removes_only_virtualenv_directories(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    canonical = tmp_path / "backend/.venv"
    nested = tmp_path / "tools/.venv"
    canonical.mkdir()
    nested.mkdir(parents=True)
    keep = tmp_path / "frontend/node_modules"
    keep.mkdir(parents=True)
    removed = clean_generated_venvs(tmp_path)
    assert {path.as_posix() for path in removed} == {"backend/.venv", "tools/.venv"}
    assert keep.is_dir()


def test_venv_clean_refuses_symlink_cleanup_target(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "backend/.venv"
    link.symlink_to(target, target_is_directory=True)
    try:
        clean_generated_venvs(tmp_path)
    except ValueError as exc:
        assert "refusing symlink virtualenv cleanup target" in str(exc)
    else:
        raise AssertionError("symlink venv cleanup target must fail closed")


def test_transport_clean_removes_zone_sidecars_and_patch_transport_files(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    zone = tmp_path / "templates/goons/python/quality/README.md:Zone.Identifier"
    zone.parent.mkdir(parents=True)
    zone.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")
    zone_data = tmp_path / "generated/frontend/models.ts:Zone.Identifier:$DATA"
    zone_data.parent.mkdir(parents=True)
    zone_data.write_text("[ZoneTransfer]\n", encoding="utf-8")
    patch_transport = tmp_path / "974.patch"
    patch_transport.write_text("transport-only patch\n", encoding="utf-8")
    patch_checksum = tmp_path / "974.patch.sha256"
    patch_checksum.write_text("abc  974.patch\n", encoding="utf-8")
    authored = tmp_path / "templates/goons/python/quality/README.md:review"
    authored.write_text("keep\n", encoding="utf-8")
    venv_zone = tmp_path / "backend/.venv/bin/tool:Zone.Identifier"
    venv_zone.parent.mkdir(parents=True)
    venv_zone.write_text("generated\n", encoding="utf-8")

    removed = clean_source_transport_artifacts(tmp_path)

    assert {path.as_posix() for path in removed} == {
        "974.patch",
        "974.patch.sha256",
        "generated/frontend/models.ts:Zone.Identifier:$DATA",
        "templates/goons/python/quality/README.md:Zone.Identifier",
    }
    assert authored.is_file()
    assert venv_zone.is_file()




def test_source_layout_rejects_transport_artifacts_until_cleaned(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    sidecar = tmp_path / "templates/goons/python/quality/README.md:Zone.Identifier"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("[ZoneTransfer]\n", encoding="utf-8")
    patch_transport = tmp_path / "phase.patch"
    patch_transport.write_text("transport-only patch\n", encoding="utf-8")
    authored = tmp_path / "templates/goons/python/quality/README.md:review"
    authored.write_text("keep\n", encoding="utf-8")

    issues = source_transport_artifact_issues(tmp_path)
    assert [issue.path for issue in issues] == [patch_transport, sidecar]
    assert all("transport-clean explicitly" in issue.reason for issue in issues)
    canonical = canonical_source_layout_issues(tmp_path, check_tracked_generated=False)
    assert any(issue.path == sidecar for issue in canonical)
    assert any(issue.path == patch_transport for issue in canonical)

    clean_source_transport_artifacts(tmp_path)
    assert source_transport_artifact_issues(tmp_path) == ()
    assert authored.is_file()


def test_git_import_keeps_product_source_and_excludes_local_history(tmp_path: Path) -> None:
    """Git, including directory anchoring, decides the initial import membership."""
    source = {
        ".gitignore",
        ".env.example",
        "backend/.env.sample",
        "frontend/.env.template",
        "backend/uv.lock",
        "frontend/package-lock.json",
        "backend/src/oh_goon/runtime/checkpoint.py",
        "backend/tests/fixtures/checkpoints/expected.json",
        "backend/tests/fixtures/history/HISTORY.md",
        "backend/tests/fixtures/docs/GROUP_A_PHASE_0.md",
        "backend/tests/fixtures/coverage-python.json",
        "scripts/tests/test_group_a_phase_0_inventory.py",
        "frontend/src/features/reports/Reports.tsx",
        "generated/openapi/openapi.json",
        "generated/frontend/models.ts",
        "templates/plans/python/quality/plan.yml",
        "templates/goons/python/quality/goon.yml",
        "template-certification/fixtures/ml/iris/uv.lock",
        "examples/standard-atomic/goon.lock",
        "docs/architecture/ADR_CURRENT.md",
        "docs/development/INSTALLATION.md",
        "docs/AGENT_SKILLS.md",
        "docs/RESPONSIBILITY_COMPLEXITY_REFACTORING.md",
        "docs/development/TEST_QUALITY.md",
        "docs/development/DEPENDENCY_MATERIALIZATION_CACHE_REVIEW.yml",
        "docs/development/EXECUTION_SCALABILITY_SCORECARD.yml",
        "docs/development/GOON_AUTHORING.md",
        "docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md",
        "docs/development/TEMPLATE_INPUT_INSPECTION.md",
        "backend/tests/fixtures/template_authoring/catalog.json",
        "backend/tests/fixtures/template_authoring/draft_evaluation.json",
        "docs/certification/answers/01-repository-ownership.yml",
    }
    local = {
        ".checkpoint/CURRENT_SOURCE.sha256",
        "OH_GOON_FORWARD_TODO.md",
        "CHECKPOINT_NOT_RELEASE.md",
        "CHANGELOG.md",
        "HISTORY.md",
        "handoffs/current.md",
        "docs/plans/previous.md",
        "docs/checkpoints/previous.md",
        "docs/reviews/previous.md",
        "docs/development/PHASE_1_2_3_PREVIOUS.md",
        "docs/development/GROUP_A_PHASE_0_INVENTORY.md",
        "docs/architecture/RUN_DATAFLOW_REDUCTION.md",
        "docs/architecture/RUN_DATAFLOW_REDUCTION_241_250.md",
        "docs/architecture/EXECUTION_AUTHORITY_CLOSURE_421_450.md",
        "docs/architecture/PLAN_ONE_CANONICAL_DATABASE_OWNER.md",
        "docs/development/EXECUTION_ENVIRONMENT_IMPLEMENTATION_CAMPAIGN.md",
        "docs/development/RISK_BACKLOG.md",
        "docs/development/TEMPLATE_AUTHORING_UX_A.md",
        "docs/development/TEMPLATE_T2S_SCHEDULED_CALLER_FILE_CUSTODY.md",
        "docs/development/UX_A_EVALUATION_BASELINE.md",
        "docs/development/UX_A_CATALOG_BASELINE.json",
        "docs/development/UX_D_EVALUATION_BASELINE.json",
        "docs/development/HASHMARKS_DOGFOOD_EVIDENCE_1267_0_965.json",
        "docs/development/TECHNICAL_DEBT_REVIEW.yml",
        "docs/development/tests/test_qualification_closure_cadence_docs.py",
        "docs/RESPONSIBILITY_COMPLEXITY_RC01.md",
        "backend/.env",
        "frontend/.env.local",
        "backend/.venv/pyvenv.cfg",
        "frontend/node_modules/example/index.js",
        "generated/release/checkpoint-parent.json",
        "frontend/playwright-report/index.html",
        "coverage-python.json",
        "logs/run.log",
        "README.md:Zone.Identifier",
        "backend/source.py:Zone.Identifier:$DATA",
        "repair.patch",
        "repair.patch.sha256",
    }
    for relative in source | local:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture\n")
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)

    completed = subprocess.run(
        [
            "git", "-c", f"core.excludesFile={os.devnull}", "-C", str(tmp_path),
            "ls-files", "--others", "--exclude-standard", "-z",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert set(completed.stdout.split("\0")) - {""} == source
    assert all((tmp_path / relative).read_bytes() == b"fixture\n" for relative in local)

def test_init_does_not_mutate_source_transport_state() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    init = makefile.split("init:", 1)[1].split("\n\n", 1)[0]
    assert "transport-clean" not in init
    assert "source-layout" not in init
    source = (ROOT / "backend/src/oh_goon/repository_tooling/source_layout.py").read_text(encoding="utf-8")
    assert 'choices=("check", "identity", "venv-clean", "transport-clean")' in source




def _write_transport_artifacts(root: Path, count: int = 3) -> tuple[Path, ...]:
    paths: list[Path] = []
    for index in range(count):
        path = (
            root / f"source-{index}.py:Zone.Identifier"
            if index % 2 == 0
            else root / f"transport-{index}.patch"
        )
        path.write_text("[ZoneTransfer]\n" if path.suffix != ".patch" else "patch\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def test_transport_clean_output_is_bounded_by_default_and_exact_in_debug(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _minimal_source(tmp_path)
    paths = _write_transport_artifacts(tmp_path)
    monkeypatch.setattr(source_layout, "ROOT", tmp_path)

    assert source_layout.main(["transport-clean"]) == 0
    normal = capsys.readouterr()
    assert normal.out.splitlines() == ["Removed 3 disposable source transport artifacts"]
    assert "Zone.Identifier" not in normal.out
    assert ".patch" not in normal.out
    assert all(not path.exists() for path in paths)

    paths = _write_transport_artifacts(tmp_path)
    assert source_layout.main(["transport-clean", "--debug"]) == 0
    debug = capsys.readouterr()
    assert debug.out.splitlines() == [
        "Removed 3 disposable source transport artifacts",
        *(path.relative_to(tmp_path).as_posix() for path in sorted(paths, key=lambda item: item.as_posix())),
    ]


def test_source_layout_check_aggregates_transport_noise_until_debug(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _minimal_source(tmp_path)
    paths = _write_transport_artifacts(tmp_path)
    monkeypatch.setattr(source_layout, "ROOT", tmp_path)

    assert source_layout.main(["check"]) == 1
    normal = capsys.readouterr()
    assert "3 disposable source transport artifacts contaminate source" in normal.err
    assert "Zone.Identifier" not in normal.err
    assert ".patch" not in normal.err

    assert source_layout.main(["check", "--debug"]) == 1
    debug = capsys.readouterr()
    assert "disposable source transport artifacts contaminate source" not in debug.err
    for path in paths:
        assert path.relative_to(tmp_path).as_posix() in debug.err


def test_transport_cleanup_refusal_hides_exact_sidecar_path_until_debug(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _minimal_source(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("keep\n", encoding="utf-8")
    sidecar = tmp_path / "source.py:Zone.Identifier"
    sidecar.symlink_to(target)
    monkeypatch.setattr(source_layout, "ROOT", tmp_path)

    assert source_layout.main(["transport-clean"]) == 2
    normal = capsys.readouterr()
    assert "refusing non-regular source transport cleanup target" in normal.err
    assert "Zone.Identifier" not in normal.err
    assert ".patch" not in normal.err

    assert source_layout.main(["transport-clean", "--debug"]) == 2
    debug = capsys.readouterr()
    assert sidecar.relative_to(tmp_path).as_posix() in debug.err


def test_source_layout_assertion_aggregates_transport_paths_until_debug(tmp_path: Path) -> None:
    _minimal_source(tmp_path)
    paths = _write_transport_artifacts(tmp_path)

    try:
        source_layout.assert_canonical_source_layout(tmp_path)
    except ValueError as exc:
        normal = str(exc)
    else:
        raise AssertionError("transport-artifact-contaminated source must fail closed")
    assert "3 disposable source transport artifacts contaminate source" in normal
    assert "Zone.Identifier" not in normal

    try:
        source_layout.assert_canonical_source_layout(tmp_path, debug=True)
    except ValueError as exc:
        debug = str(exc)
    else:
        raise AssertionError("transport-artifact-contaminated source must fail closed")
    for path in paths:
        assert path.relative_to(tmp_path).as_posix() in debug


def test_repository_tool_state_is_never_source_identity() -> None:
    from oh_goon.core.source_tree_policy import is_transient_source_path

    assert is_transient_source_path(".git")
    assert is_transient_source_path(".git/objects/abc")
    assert is_transient_source_path(".hashmarks")
    assert is_transient_source_path(".hashmarks/codemap.sqlite3")
    assert is_transient_source_path(".coverage")
    assert is_transient_source_path(".coverage.worker-123")
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".hashmarks/" in ignored
    assert ".coverage" in ignored
    assert ".coverage.*" in ignored
