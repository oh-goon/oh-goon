from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
PLAN = ROOT / "templates/plans/oh-goon/app-certification/plan.yml"
APP_CERTIFICATION = ROOT / "docs/development/APP_CERTIFICATION.md"


def _target_body(name: str) -> str:
    marker = f"{name}:"
    lines_all = MAKEFILE.splitlines()
    start_line = next(
        i
        for i, line in enumerate(lines_all)
        if line == marker or line.startswith(marker + " ")
    )
    rest = "\n".join(lines_all[start_line:])
    lines = rest.splitlines()
    body = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace() and ":" in line:
            break
        body.append(line)
    return "\n".join(body)


def _plan_goons() -> list[str]:
    data = yaml.safe_load(PLAN.read_text(encoding="utf-8"))
    return [item if isinstance(item, str) else item["import"] for item in data["goons"]]


def _goon_make_command(name: str) -> str:
    path = ROOT / "templates/goons/oh-goon" / name.removeprefix("oh-goon/") / "goon.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["steps"]) == 1
    run = data["steps"][0]["run"]
    assert isinstance(run, str) and run.startswith("make ")
    return run.removeprefix("make ")


def test_app_plan_is_one_small_release_critical_order_owner() -> None:
    assert _plan_goons() == [
        "oh-goon/backend-p0",
        "oh-goon/architecture",
        "oh-goon/template-startability",
        "oh-goon/frontend-functional",
        "oh-goon/local-runtime",
    ]


def test_app_plan_is_direct_verification_of_initialized_repository() -> None:
    data = yaml.safe_load(PLAN.read_text(encoding="utf-8"))
    assert data["execution"] == {"purpose": "verification"}


def test_make_certify_has_one_certification_frontdoor() -> None:
    body = _target_body("certify")
    assert "$(CERTIFICATION_FRONTDOOR)" in body
    assert "CERTIFICATION_RUNNER = backend/.venv/bin/oh-goon-workflow" in MAKEFILE
    assert "CERTIFICATION INITIALIZATION REQUIRED" in body
    assert "Run: make init" in body
    assert "app-certification" not in body
    for goon in _plan_goons():
        assert f"make {_goon_make_command(goon)}" not in body


def test_make_certify_rejects_stale_generated_authority_before_run_frontdoor() -> None:
    body = _target_body("certify")
    generated = "backend/.venv/bin/oh-goon-check-generated"
    assert generated in body
    assert body.index(generated) < body.index("$(CERTIFICATION_FRONTDOOR)")


def test_template_startability_gate_is_plan_owned_and_uses_exact_catalog_harness() -> (
    None
):
    body = _target_body("template-startability-certification")
    assert "tests/integration/services/test_catalog_template_startability.py" in body
    assert ".venv/bin/oh-goon-qualification-exec pytest -q" in body
    certify = _target_body("certify")
    assert "template-startability-certification" not in certify
    assert "oh-goon/template-startability" in _plan_goons()


def test_make_certify_stale_generated_failure_never_invokes_frontdoor(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "backend/.venv/bin"
    bin_dir.mkdir(parents=True)
    workflow = bin_dir / "oh-goon-workflow"
    workflow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    workflow.chmod(0o755)

    generated_marker = tmp_path / "generated-check-ran"
    generated_check = bin_dir / "oh-goon-check-generated"
    generated_check.write_text(
        f"#!/bin/sh\ntouch {generated_marker}\nexit 7\n",
        encoding="utf-8",
    )
    generated_check.chmod(0o755)

    frontdoor_marker = tmp_path / "frontdoor-ran"
    result = subprocess.run(
        [
            "make",
            "-f",
            str(ROOT / "Makefile"),
            "certify",
            f"CERTIFICATION_FRONTDOOR=touch {frontdoor_marker}",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert generated_marker.is_file()
    assert not frontdoor_marker.exists()


def test_app_certification_does_not_own_dependency_materialization() -> None:
    removed = ROOT / "templates/goons/oh-goon/dependency-materialize/goon.yml"
    assert not removed.exists()
    plan_text = PLAN.read_text(encoding="utf-8")
    assert "dependency-materialize" not in plan_text
    assert "make init" not in plan_text
    for goon in _plan_goons():
        path = (
            ROOT
            / "templates/goons/oh-goon"
            / goon.removeprefix("oh-goon/")
            / "goon.yml"
        )
        assert "make init" not in path.read_text(encoding="utf-8")
    for removed_target in (
        "certification-setup:",
        "certification-workspace-setup:",
        "certification-backend-workspace-setup:",
        "certification-dependencies-prepare:",
        "certification-dependencies-replay:",
        "certification-dependencies-seal:",
    ):
        assert removed_target not in MAKEFILE


def test_backend_p0_uses_repository_owned_native_test_launcher() -> None:
    target = _target_body("verify-p0")
    assert ".venv/bin/oh-goon-qualification-exec pytest" in target
    assert ".venv/bin/python -m pytest" not in target
    assert "oh-goon-check-generated" in target


def test_backend_p0_runs_local_import_global_sanity_and_keeps_ruff_advisory() -> None:
    target = _target_body("verify-p0")
    static = _target_body("python-static-sanity")

    assert target.splitlines()[0] == "verify-p0: python-static-sanity"
    assert "tests/unit/entrypoints/test_python_source_file_smoke.py" in target
    assert ".venv/bin/ruff check --select E9,F --output-format json" in static
    assert ".venv/bin/oh-goon-ruff-advisory" in static
    assert "--fix" not in static
    assert "src tests ../scripts ../tests/integration" in static
    assert "RUFF ADVISORY" in static
    assert "exit 0" in static


def test_backend_p0_owns_exact_nested_test_tool_authority() -> None:
    path = ROOT / "templates/goons/oh-goon/backend-p0/goon.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "tools" not in data
    assert data["execution"]["shell"] == "sh"
    assert "# OH_GOON_OPAQUE_TOOLS: git ldd uv\nverify-p0:" in MAKEFILE

    requirements = json.loads(
        (ROOT / "backend/goon-execution-requirements.json").read_text(encoding="utf-8")
    )
    assert requirements["workload_tool_permissions"]["make:verify-p0"] == [
        "git",
        "ldd",
        "sh",
        "uv",
    ]

    generated = json.loads(
        (ROOT / "backend/src/oh_goon/canonical_goon_tool_authority.json").read_text(
            encoding="utf-8"
        )
    )
    assert generated["goons"]["oh-goon/backend-p0"]["make"] == [
        "git",
        "ldd",
        "sh",
        "uv",
    ]


def test_app_certification_docs_match_executable_goon_authority() -> None:
    text = APP_CERTIFICATION.read_text(encoding="utf-8")
    for index, goon in enumerate(_plan_goons(), start=1):
        command = _goon_make_command(goon)
        assert f"| {index:02d} | `{goon}` | `make {command}` |" in text
    assert "one dependency materialization owner" in text
    assert "Broad backend/tooling/integration/P1/browser diagnostics" in text


def test_template_startability_goon_owns_exact_nested_tool_authority() -> None:
    requirements = json.loads(
        (ROOT / "backend/goon-execution-requirements.json").read_text(encoding="utf-8")
    )
    assert requirements["workload_tool_permissions"][
        "make:template-startability-certification"
    ] == ["ldd", "sh", "true", "uv"]

    generated = json.loads(
        (ROOT / "backend/src/oh_goon/canonical_goon_tool_authority.json").read_text(
            encoding="utf-8"
        )
    )
    assert generated["goons"]["oh-goon/template-startability"]["make"] == [
        "ldd",
        "sh",
        "true",
        "uv",
    ]


def test_certification_plan_has_no_release_or_browser_matrix_replay() -> None:
    goons = set(_plan_goons())
    assert (
        not {
            "oh-goon/backend-tests",
            "oh-goon/tooling-tests",
            "oh-goon/integration-tests",
            "oh-goon/backend-p1",
            "oh-goon/browser-targeted",
            "oh-goon/browser-required",
            "oh-goon/release-locks",
            "oh-goon/release-package-store",
        }
        & goons
    )


def test_help_separates_development_verify_and_lean_certification() -> None:
    assert "make check            Fast inner development feedback" in MAKEFILE
    assert "make verify           Exhaustive whole-app diagnosis" in MAKEFILE
    assert (
        "make certify          Lean APP verification of the initialized checkout; no strict executor required"
        in MAKEFILE
    )
    assert "make certification-setup" not in MAKEFILE


def test_app_local_runtime_verification_is_separate_from_strict_real_world_acceptance() -> (
    None
):
    body = _target_body("local-runtime-verification")
    assert "oh-goon-dev-runtime-acceptance verification" in body
    assert "strict-local-runtime-acceptance" not in body
    assert "local-access-runtime-acceptance:" not in MAKEFILE
    assert "$(MAKE) strict-local-runtime-acceptance" in _target_body("test-real-world")
    assert "$(MAKE) strict-local-runtime-acceptance" in _target_body(
        "test-real-world-parallel"
    )

    goon = yaml.safe_load(
        (ROOT / "templates/goons/oh-goon/local-runtime/goon.yml").read_text(
            encoding="utf-8"
        )
    )
    assert "execution" not in goon
    assert "tools" not in goon

    requirements = json.loads(
        (ROOT / "backend/goon-execution-requirements.json").read_text(encoding="utf-8")
    )
    assert requirements["workload_tool_permissions"][
        "make:local-runtime-verification"
    ] == [
        "node",
        "npm",
    ]
    assert "make:local-access-runtime-acceptance" not in json.dumps(requirements)

    generated = json.loads(
        (ROOT / "backend/src/oh_goon/canonical_goon_tool_authority.json").read_text(
            encoding="utf-8"
        )
    )
    assert generated["goons"]["oh-goon/local-runtime"]["make"] == ["node", "npm"]


def test_unknown_make_target_lists_lean_certification_frontdoor() -> None:
    result = subprocess.run(
        ["make", "certify2"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode != 0
    assert "Unknown Oh-Goon make target: certify2" in result.stdout
    assert (
        "make certify          Lean APP verification of the initialized checkout; no strict executor required"
        in result.stdout
    )
