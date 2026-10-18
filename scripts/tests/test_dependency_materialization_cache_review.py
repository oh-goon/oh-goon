from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/development/DEPENDENCY_MATERIALIZATION_CACHE_REVIEW.yml"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _target(name: str) -> str:
    start = MAKEFILE.index(f"{name}:")
    tail = MAKEFILE[start:]
    end = tail.find("\n\n")
    return tail if end < 0 else tail[:end]


def test_phase23_review_covers_every_normative_expensive_operation_with_identity_and_negative_proof() -> (
    None
):
    payload = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    assert (
        payload["rule"]
        == "native_tools_own_package_materialization_and_cache_semantics"
    )
    operations = payload["operations"]
    assert {item["id"] for item in operations} == {
        "python-environment",
        "npm-graph",
        "browsers",
        "retained-content",
        "source-snapshots",
        "workspaces",
    }
    for item in operations:
        assert item["why"].strip()
        assert item["owner"].strip()
        assert item["reuse_identity"].strip()


def test_app_certification_consumes_native_init_without_second_materialization_owner() -> (
    None
):
    plan = (ROOT / "templates/plans/oh-goon/app-certification/plan.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert not (ROOT / "templates/goons/oh-goon/dependency-materialize").exists()
    assert "dependency-materialize" not in plan
    assert "make init" not in plan
    assert "$(MAKE) init" not in _target("certify")
    assert 'test -x "$(CERTIFICATION_RUNNER)"' in _target("certify")

    for removed in (
        "certification-setup:",
        "certification-workspace-setup:",
        "certification-backend-workspace-setup:",
    ):
        assert f"\n{removed}" not in f"\n{makefile}"
    assert not (ROOT / "scripts/certification_supply_import_authority.sh").exists()
    assert not (ROOT / "scripts/certification_workspace_setup.sh").exists()


def test_phase23_reuse_owners_reprove_identity_instead_of_trusting_presence() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    init = _target("init")
    frontend = _target("frontend-install")
    assert "uv sync --project backend --locked" in init
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in init
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in frontend
    assert "frontend_install.sh" not in makefile
    assert not (ROOT / "scripts/frontend_install.sh").exists()
