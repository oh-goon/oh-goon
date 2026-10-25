from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.common import Violation

from oh_goon.repository_tooling.architecture import (
    backend_boundary_contract as backend_boundary,
)
from oh_goon.repository_tooling.architecture import (
    frontend_base_contract as frontend_base,
)
from oh_goon.repository_tooling.architecture import (
    source_layout_contract as source_layout_contract,
)
from oh_goon.repository_tooling.architecture import (
    template_layout_contract as template_layout,
)


def _template_tree(root: Path) -> tuple[Path, Path, Path]:
    templates = root / "templates"
    goons = templates / "goons"
    plans = templates / "plans"
    for base, source in ((goons, "goon.yml"), (plans, "plan.yml")):
        pkg = base / "python" / "quality"
        pkg.mkdir(parents=True)
        (pkg / source).write_text("version: 1\n", encoding="utf-8")
        (pkg / "README.md").write_text("# python/quality\n", encoding="utf-8")
        (base / "README.md").write_text("index\n", encoding="utf-8")
    (templates / "README.md").write_text(
        "Composite Goon README.md run:\n", encoding="utf-8"
    )
    backend = root / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[tool.hatch.build.targets.wheel.hooks.custom]\npath = "hatch_build.py"\n',
        encoding="utf-8",
    )
    return templates, goons, plans


def _bind_template_roots(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    templates, goons, plans = _template_tree(root)
    monkeypatch.setattr(template_layout, "TEMPLATES_ROOT", templates)
    monkeypatch.setattr(template_layout, "GOON_TEMPLATES_ROOT", goons)
    monkeypatch.setattr(template_layout, "PLAN_TEMPLATES_ROOT", plans)
    monkeypatch.setattr(template_layout, "BACKEND_ROOT", root / "backend")


def test_template_layout_accepts_minimal_canonical_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    assert template_layout.check_template_layout_contract() == []


def test_template_layout_accepts_complete_python_goon_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    package = tmp_path / "templates/goons/python/quality"
    (package / "goon.yml").write_text("version: 1\nruntime: python\n", encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock", ".gitignore", "data/input.csv"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("authored source\n", encoding="utf-8")

    assert template_layout.check_template_layout_contract() == []

    (package / "uv.lock").unlink()
    assert any(
        "missing uv.lock" in issue.message
        for issue in template_layout.check_template_layout_contract()
    )


def test_template_layout_keeps_plan_packages_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    package = tmp_path / "templates/plans/python/quality"
    (package / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert any(
        "must contain exactly plan.yml and README.md (unexpected pyproject.toml)"
        in issue.message
        for issue in template_layout.check_template_layout_contract()
    )


@pytest.mark.parametrize(
    "filename", ["goon.yml", "README.md", "pyproject.toml", "uv.lock"]
)
def test_template_layout_rejects_python_project_authority_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    package = tmp_path / "templates/goons/python/quality"
    for name in ("pyproject.toml", "uv.lock"):
        (package / name).write_text("[project]\n", encoding="utf-8")
    assert template_layout.check_template_layout_contract() == []
    outside = tmp_path / "outside"
    outside.write_bytes((package / filename).read_bytes())
    (package / filename).unlink()
    (package / filename).symlink_to(outside)

    assert any(
        f"requires a regular non-symlink {filename}" in issue.message
        for issue in template_layout.check_template_layout_contract()
    )


def test_template_layout_fixture_models_semantic_wheel_force_include(
    tmp_path: Path,
) -> None:
    _template_tree(tmp_path)
    violations: list[Violation] = []
    template_layout._check_wheel_force_include(
        tmp_path / "backend" / "pyproject.toml",
        violations,
    )
    assert violations == []


def test_template_layout_uses_canonical_authored_source_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    templates = tmp_path / "templates"
    goon_package = templates / "goons" / "python" / "quality"
    plan_package = templates / "plans" / "python" / "quality"

    (templates / "README.md:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (templates / "copied-hotfix.patch").write_text("transport only\n", encoding="utf-8")
    (templates / "goons" / "python" / "quality:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (goon_package / "README.md:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (goon_package / "goon.yml:Zone.Identifier:$DATA").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (plan_package / "README.md:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (plan_package / "plan.yml:Zone.Identifier:$DATA").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (plan_package / "applied-fix.patch.sha256").write_text(
        "deadbeef\n", encoding="utf-8"
    )

    assert template_layout.check_template_layout_contract() == []


def test_template_layout_rejects_noncanonical_template_root_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    (tmp_path / "templates" / "extra").mkdir()
    issues = template_layout.check_template_layout_contract()
    messages = {issue.message for issue in issues}
    assert "templates/ must contain only README.md, goons/, and plans/" in messages


def test_template_layout_rejects_catalog_bad_category_and_bad_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    goons = tmp_path / "templates" / "goons"
    (goons / "catalog.yml").write_text("items: []\n", encoding="utf-8")
    (goons / "Bad_Name").mkdir()
    (goons / "python" / "Bad_Name").mkdir()
    issues = template_layout.check_template_layout_contract()
    text = "\n".join(issue.message for issue in issues)
    assert "template discovery must not use authored catalog.yml" in text
    assert "template root may contain only canonical category directories" in text
    assert "template category may contain only canonical package directories" in text


def test_template_layout_rejects_bad_members_yaml_and_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    package = tmp_path / "templates" / "goons" / "python" / "quality"
    (package / "extra.txt").write_text("x", encoding="utf-8")
    issues = template_layout.check_template_layout_contract()
    assert any(
        "must contain exactly goon.yml and README.md (unexpected extra.txt)"
        in issue.message
        for issue in issues
    )
    (package / "extra.txt").unlink()
    (package / "goon.yml").write_text("- not-a-map\n", encoding="utf-8")
    (package / "README.md").write_text("wrong\n", encoding="utf-8")
    issues = template_layout.check_template_layout_contract()
    assert any("YAML root must be a mapping" in issue.message for issue in issues)
    (package / "goon.yml").write_text("version: 1\n", encoding="utf-8")
    issues = template_layout.check_template_layout_contract()
    assert any(
        "template README must start with canonical heading" in issue.message
        for issue in issues
    )


def test_template_layout_rejects_missing_packaging_and_start_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_template_roots(monkeypatch, tmp_path)
    (tmp_path / "backend" / "pyproject.toml").write_text(
        "[project]\n", encoding="utf-8"
    )
    (tmp_path / "templates" / "README.md").write_text("README only\n", encoding="utf-8")
    issues = template_layout.check_template_layout_contract()
    text = "\n".join(issue.message for issue in issues)
    assert "wheel packaging requires its admitted-source custom hook" in text
    assert "template start page missing 'Composite Goon'" in text
    assert "template start page missing 'run:'" in text


def test_architecture_source_layout_aggregates_transport_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    for name in (
        "README.md:Zone.Identifier",
        "copied.patch",
        "nested.patch.sha256",
    ):
        (tmp_path / name).write_text("transport\n", encoding="utf-8")
    monkeypatch.setattr(source_layout_contract, "ROOT", tmp_path)

    issues = source_layout_contract.check_source_layout_contract()

    assert len(issues) == 1
    assert issues[0].path == tmp_path.resolve()
    assert (
        "3 disposable source transport artifacts contaminate source"
        in issues[0].message
    )
    assert "transport-clean explicitly" in issues[0].message
    assert "Zone.Identifier" not in issues[0].message


def _python(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_backend_boundaries_detect_layer_and_repository_tooling_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "oh_goon"
    _python(package / "core" / "bad.py", "import oh_goon.runtime.workspaces\n")
    _python(package / "integrations" / "bad.py", "from oh_goon.services import x\n")
    _python(package / "runtime" / "bad.py", "import oh_goon.api\n")
    _python(package / "services" / "bad.py", "from oh_goon.api import x\n")
    _python(
        package / "other.py",
        "import scripts\nimport oh_goon.repository_tooling.local_state_source\n",
    )
    _python(
        package / "repository_tooling" / "owned.py",
        "import oh_goon.repository_tooling.local_state_source\n",
    )
    _python(
        package / "workflow.py",
        "import oh_goon.repository_tooling.source_normalization\n",
    )
    _python(
        package / "cli_self.py",
        "import oh_goon.repository_tooling.self_certification_plan\n",
    )
    monkeypatch.setattr(backend_boundary, "BACKEND_PACKAGE_ROOT", package)
    issues = backend_boundary.check_backend_boundaries()
    text = "\n".join(issue.message for issue in issues)
    assert "core layer must not import oh_goon.runtime.workspaces" in text
    assert "integrations layer must not import oh_goon.services" in text
    assert "runtime layer must not import oh_goon.api" in text
    assert "services layer must not import oh_goon.api" in text
    assert text.count("installed product code must not import repository tooling") == 2


def test_backend_boundaries_allow_explicit_repository_tooling_bridges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "oh_goon"
    for layer in ("core", "integrations", "runtime", "services", "repository_tooling"):
        _python(package / layer / "ok.py", "import pathlib\n")
    _python(
        package / "workflow.py",
        "import oh_goon.repository_tooling.source_normalization\n",
    )
    _python(
        package / "cli_self.py",
        "import oh_goon.repository_tooling.self_certification_plan\n",
    )
    monkeypatch.setattr(backend_boundary, "BACKEND_PACKAGE_ROOT", package)
    assert backend_boundary.check_backend_boundaries() == []


def test_frontend_local_only_detects_every_forbidden_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    _python(
        source / "feature.ts",
        "sessionStorage; indexedDB; document.cookie; cookieStore; navigator.sendBeacon; localStorage; fetch('/x'); const u='https://example.test';\n",
    )
    _python(
        source / "generated" / "ignored.ts", "sessionStorage; fetch('https://x');\n"
    )
    monkeypatch.setattr(frontend_base, "FRONTEND_SOURCE_ROOT", source)
    issues = frontend_base.check_frontend_local_only()
    text = "\n".join(issue.message for issue in issues)
    for token in frontend_base.FORBIDDEN_BROWSER_APIS:
        assert f"unsupported browser persistence/telemetry: {token}" in text
    assert "localStorage is owned only by app/preferences.ts" in text
    assert "frontend source must not contain an external URL" in text
    assert "raw fetch is owned only by app/api/httpTransport.ts" in text


def test_frontend_local_only_allows_owned_storage_and_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    _python(source / "app" / "preferences.ts", "localStorage.getItem('x');\n")
    _python(source / "app" / "api" / "httpTransport.ts", "fetch('/api');\n")
    monkeypatch.setattr(frontend_base, "FRONTEND_SOURCE_ROOT", source)
    assert frontend_base.check_frontend_local_only() == []


def test_frontend_e2e_fixture_integrity_handles_absent_and_bad_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = tmp_path / "frontend"
    monkeypatch.setattr(frontend_base, "FRONTEND_ROOT", frontend)
    assert frontend_base.check_frontend_e2e_fixture_type_integrity() == []
    _python(frontend / "e2e" / "fixture.ts", "const x = value as unknown as Thing;\n")
    (frontend / "e2e" / "note.txt").write_text("as unknown as", encoding="utf-8")
    issues = frontend_base.check_frontend_e2e_fixture_type_integrity()
    assert len(issues) == 1
    assert "double-cast 'as unknown as' is forbidden" in issues[0].message


def test_full_repository_architecture_contract_passes_in_process(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from oh_goon.repository_tooling import check_architecture

    assert check_architecture.main() == 0
    assert "Architecture checks passed" in capsys.readouterr().out
