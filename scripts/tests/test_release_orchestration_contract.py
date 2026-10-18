import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
PREPARE_PLAN = ROOT / "templates/plans/oh-goon/prepare-release/plan.yml"
PREPARE_README = ROOT / "templates/plans/oh-goon/prepare-release/README.md"
APP_README = ROOT / "templates/plans/oh-goon/app-certification/README.md"
APP_DOC = ROOT / "docs/development/APP_CERTIFICATION.md"
LOCAL_DOC = ROOT / "docs/LOCAL_VERIFICATION.md"
PACKAGE_RUNBOOK = ROOT / "docs/development/PACKAGE_RUN.md"
RELEASE_REPRODUCIBILITY = ROOT / "docs/development/RELEASE_REPRODUCIBILITY.md"
ROOT_README = ROOT / "README.md"
PACKAGE_SCRIPT = ROOT / "backend/src/oh_goon/repository_tooling/release/package_source_checkpoint.py"
BYTE_BUILDER = ROOT / "backend/src/oh_goon/repository_tooling/release/package_slim.py"
RELEASE_VERSION_OWNER = ROOT / "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py"

EXPECTED_GOONS = ["oh-goon/release-package-store"]


def _target_body(name: str) -> str:
    marker = f"{name}:"
    start = MAKEFILE.index(marker)
    lines = MAKEFILE[start:].splitlines()
    body = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace() and ":" in line:
            break
        body.append(line)
    return "\n".join(body)


def _plan_goons() -> list[str]:
    data = yaml.safe_load(PREPARE_PLAN.read_text(encoding="utf-8"))
    return [item if isinstance(item, str) else item["import"] for item in data["goons"]]


def _goon_run(name: str) -> str:
    path = ROOT / "templates/goons/oh-goon" / name.removeprefix("oh-goon/") / "goon.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = data["steps"]
    assert len(steps) == 1, f"release Goon {name} must own exactly one step"
    run = steps[0]["run"]
    assert isinstance(run, str) and run.strip()
    return run


def test_prepare_release_plan_is_the_only_release_orchestrator() -> None:
    assert _plan_goons() == EXPECTED_GOONS
    body = _target_body("prepare-release")
    assert "$(CERTIFICATION_RUNNER) release" in body
    assert "OH_GOON_RELEASE_VERSION" not in body
    assert "oh-goon plan run" not in body
    assert "oh-goon lock" not in body
    assert "repository_id(Path.cwd())" not in body
    assert "$(MAKE) " not in body
    for goon in EXPECTED_GOONS:
        run = _goon_run(goon)
        delegated = run.replace("make ", "$(MAKE) ", 1) if run.startswith("make ") else run
        assert delegated not in body


def test_release_goon_executes_package_command_through_native_uv_project_authority() -> None:
    assert _goon_run("oh-goon/release-package-store") == "uv run oh-goon-package-source"



def test_no_parallel_direct_zip_make_producer_exists() -> None:
    assert "package-slim:" not in MAKEFILE
    assert "make package-slim" not in MAKEFILE
    assert "package-slim" not in MAKEFILE.split(".PHONY:", 1)[1].splitlines()[0]


def test_package_publication_has_one_script_owner() -> None:
    body = _target_body("release-source-package-artifact")
    assert (
        "uv run --project backend --locked --no-default-groups --group build "
        "oh-goon-package-source"
    ) in body
    assert "backend/.venv/" not in body
    assert "package_slim.py" not in body
    # Package behavior and source-seal/architecture enforcement are exercised
    # by their owning package tests. This contract only protects publication
    # ownership: the public Make target delegates to one package command and
    # the lower-level byte builder cannot become a second CLI producer.
    # The internal byte builder remains an implementation detail, not an
    # independently advertised release producer.
    assert BYTE_BUILDER.is_file()
    builder_source = BYTE_BUILDER.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' not in builder_source
    assert "def main(" not in builder_source
    assert "argparse" not in builder_source


def test_release_package_goon_never_depends_on_live_checkout_materialization() -> None:
    goon = (ROOT / "templates/goons/oh-goon/release-package-store/goon.yml").read_text(
        encoding="utf-8"
    )
    assert "project: backend" in goon
    assert "group: build" in goon
    assert "run: uv run oh-goon-package-source" in goon
    assert "backend/.venv/" not in goon
    assert "release-source-package-artifact" not in goon


def test_persistent_artifact_policy_consumes_retained_bytes_and_reuses_app_plan() -> None:
    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PREPARE_README, APP_README, APP_DOC, LOCAL_DOC)
    )
    assert "retained" in docs
    assert "fresh" in docs or "new repository" in docs
    assert "oh-goon/app-certification" in docs
    assert "same `oh-goon/app-certification` Plan" in docs
    assert "make package-slim" not in docs
    assert "make package-slim" not in ROOT_README.read_text(encoding="utf-8")


def test_release_acceptance_requires_current_repository_understanding_material() -> None:
    body = _target_body("acceptance-release")
    assert "$(MAKE) repository-understanding-exam" in body
    assert "acceptance-retained-supply" not in body
    assert body.index("repository-understanding-exam") < body.index("release-accept")


def test_artifact_certification_does_not_have_a_second_app_test_policy() -> None:
    plan_files = list((ROOT / "templates/plans/oh-goon").rglob("plan.yml"))
    names = {path.parent.name for path in plan_files}
    assert "artifact-app-certification" not in names
    assert "zip-app-certification" not in names
    assert "persistent-app-certification" not in names


def test_junior_package_runbook_exposes_one_plan_entrypoint() -> None:
    runbook = (ROOT / "docs/development/PACKAGE_RUN.md").read_text(encoding="utf-8")
    assert "make prepare-release" in runbook
    assert "OH_GOON_RELEASE_REPOSITORY_ID=repo-<exact-id>" not in runbook
    assert "make prepare-release\n" in runbook
    assert "make prepare-release OH_GOON_RELEASE_VERSION" not in runbook
    assert "exactly one Atomic Goon" in runbook
    assert "oh-goon-package-source` directly" in runbook
    assert "internal byte builder" in runbook
    for goon in EXPECTED_GOONS:
        assert f"`{_goon_run(goon)}`" not in runbook


def test_live_junior_docs_do_not_recreate_old_release_or_browser_workflow() -> None:
    live = [
        ROOT / "README.md",
        ROOT / "docs/LOCAL_VERIFICATION.md",
        ROOT / "docs/development/APP_TEST_RUN.md",
        ROOT / "docs/development/PACKAGE_RUN.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in live)
    assert "make package-slim" not in text
    assert "make frontend-browser-release" not in text
    assert "make app-certification-goons" not in text
    assert "make app-certification-resume" not in text
    assert "package_slim.py --" not in text


def test_reference_docs_warn_junior_not_to_use_them_as_orchestration() -> None:
    expected = {
        ROOT / "docs/development/ACCEPTANCE.md": "not a junior release workflow",
        ROOT / "docs/development/RELEASE_REPRODUCIBILITY.md": "not a release entrypoint",
        ROOT / "docs/development/BROWSER_QUALITY_MATRIX.md": "not an APP or release orchestration path",
        ROOT / "docs/development/FRONTEND_STYLE_TUNING.md": "do not replace or redefine the canonical APP Plan",
    }
    for path, marker in expected.items():
        assert marker in path.read_text(encoding="utf-8")


def test_checkpoint_restore_keeps_recovery_transport_outside_source() -> None:
    durable = (ROOT / "docs/development/DURABLE_CONTINUATION.md").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "Never extract an exported delta/archive directly into canonical source" in durable
    assert "external staging directory" in durable
    assert "apply only the source payload paths declared by the verified manifest" in durable
    assert "top-level `payload/` namespace" in durable
    assert "must never expose a runnable checkout root" in durable
    init = makefile.split("init:", 1)[1].split("\n\n", 1)[0]
    assert "transport-clean" not in init


def test_prepare_release_refreshes_exact_lock_before_locked_plan_run() -> None:
    runner = (ROOT / "backend/src/oh_goon/workflow.py").read_text(encoding="utf-8")
    assert 'RELEASE_PLAN = "templates/plans/oh-goon/prepare-release/plan.yml"' in runner
    assert 'plan=RELEASE_PLAN' in runner
    assert '_run_plan(' in runner
    assert "inputs={}" in runner
    release_body = runner[runner.index("def run_release(") : runner.index("def run_explain(")]
    assert "release_version" not in release_body
    runbook = PACKAGE_RUNBOOK.read_text(encoding="utf-8")
    assert "Do not generate the release Plan lock manually" in runbook


def test_prepare_release_is_zero_knowledge_and_uses_shared_workflow_owner() -> None:
    body = _target_body("prepare-release")
    runner = (ROOT / "backend/src/oh_goon/workflow.py").read_text(encoding="utf-8")
    assert "OH_GOON_RELEASE_REPOSITORY_ID" not in body + runner
    assert "$(CERTIFICATION_RUNNER) release" in body
    assert "OH_GOON_RELEASE_VERSION" not in body
    assert 'require_strict_executor: bool = False' in runner
    assert "repository_tooling.source_normalization" not in runner
    assert '"--wait",' in runner and '"--repair-handoff",' in runner


def test_prepare_release_requires_current_app_pass_and_does_not_mutate_source() -> None:
    runner = (ROOT / "backend/src/oh_goon/workflow.py").read_text(encoding="utf-8")
    release_body = runner[runner.index("def run_release(") : runner.index("def run_explain(")]
    assert "_require_current_app_pass" in release_body
    assert "inputs={}" in release_body
    assert "release_version" not in release_body
    assert "release-version-set" not in release_body
    assert "release-source-clean" not in release_body
    assert ".app-passed" not in runner
    assert "app_pass_marker" not in runner


def test_release_version_set_is_the_only_public_effectful_version_authoring_command() -> None:
    assert "release-stamp:" not in MAKEFILE
    assert "release-stamp" not in MAKEFILE.split(".PHONY:", 1)[1].splitlines()[0]
    surface = (ROOT / "docs/development/MAKE_TARGET_SURFACE.yml").read_text(encoding="utf-8")
    assert "release-stamp" not in surface
    owner = RELEASE_VERSION_OWNER.read_text(encoding="utf-8")
    assert 'choices=("set", "check")' in owner


def test_release_version_stamping_is_pre_certification_native_owner_not_release_goon() -> None:
    body = _target_body("release-version-set")
    stamp_argv = [
        "backend/.venv/bin/oh-goon-release-version",
        "set",
        "$(OH_GOON_RELEASE_VERSION)",
    ]
    assert any(shlex.split(line.lstrip("@-+ ")) == stamp_argv for line in body.splitlines())
    assert "backend/.venv/bin/oh-goon-export-openapi" not in body
    assert "backend/.venv/bin/oh-goon-generate-api-types" not in body
    assert "backend/.venv/bin/oh-goon-generate-goon-tool-authority" not in body
    assert body.count("backend/.venv/bin/") == 1
    assert not (ROOT / "templates/goons/oh-goon/release-set-version").exists()
    assert not (ROOT / "templates/goons/oh-goon/release-clean-source").exists()
    assert _plan_goons() == ["oh-goon/release-package-store"]


def test_release_version_reference_docs_match_native_uv_authority() -> None:
    owner = RELEASE_VERSION_OWNER.read_text(encoding="utf-8")
    docs = RELEASE_REPRODUCIBILITY.read_text(encoding="utf-8")

    assert '("uv", "version", "--frozen", version)' in owner
    assert '("uv", "lock", "--check")' not in owner
    assert "`uv version --frozen`" in docs
    assert "byte-identical" in docs
    assert "must not run `uv lock --check`" in docs
