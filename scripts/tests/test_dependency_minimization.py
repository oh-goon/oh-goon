from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_runtime_has_one_http_client_owner_and_lean_default_dev_group() -> None:
    payload = tomllib.loads(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    runtime = set(payload["project"]["dependencies"])
    groups = payload["dependency-groups"]

    assert not any(item.startswith("httpx>=") for item in runtime)
    declared = [
        *runtime,
        *groups["dev"],
        *groups["quality"],
        *groups["build"],
        *payload["build-system"]["requires"],
    ]
    assert all(
        "==" not in requirement and ",<" not in requirement for requirement in declared
    )


def test_oh_goon_http_calls_use_lazy_httpx2_leaf_transport() -> None:
    product = (
        ROOT / "backend/src/oh_goon/integrations/gitlab_ci_component.py"
    ).read_text(encoding="utf-8")
    normal_test = (
        ROOT / "backend/tests/unit/entrypoints/test_gitlab_goon_import_cli_and_fetch.py"
    ).read_text(encoding="utf-8")
    late_test = (
        ROOT / "backend/tests/dependency_complete/test_gitlab_httpx2_transport.py"
    ).read_text(encoding="utf-8")
    assert "        import httpx2" in product
    assert "httpx2.get(" in product
    assert "httpx2.HTTPError" in product
    assert "\nimport httpx2\n" not in product
    assert "import httpx\n" not in product
    assert "import httpx2" not in normal_test
    assert "import httpx2" in late_test


def test_normal_backend_install_delegates_one_materialization_operation_to_native_uv() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    backend_install = makefile.split("backend-install:", 1)[1].split("\n\n", 1)[0]
    init = makefile.split("\ninit:\n", 1)[1].split("\ninit-doctor:", 1)[0]
    native_sync = (
        "uv sync --project backend --locked --group dev --group quality --group build"
    )
    assert native_sync in backend_install
    assert native_sync in init
    assert "--no-install-project" not in backend_install
    assert "uv pip install" not in backend_install
    assert "python_install" not in backend_install
    assert "uv run --isolated" not in backend_install

    for command in ("ruff check", "ruff format", "mypy "):
        lines = [line for line in makefile.splitlines() if command in line]
        assert lines
        for line in lines:
            if "uv run" in line:
                assert "--group quality" in line
                continue
            assert command.startswith("ruff ")
            assert ".venv/bin/ruff" in line
    coverage_targets = (
        "coverage-python",
        "test-sqlite-coverage",
        "test-journal-coverage",
    )
    coverage_blocks = {
        target: makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        for target in coverage_targets
    }
    assert "--coverage-source=src/oh_goon" in coverage_blocks["coverage-python"]
    for block in coverage_blocks.values():
        assert "oh-goon-test-batches --qualified" in block
        assert "--group quality" not in block

    for target in ("test-integration", "test-real-lifecycle"):
        block = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        assert "oh-goon-test-batches --qualified" in block
        assert ".venv/bin/python -m pytest" not in block


def test_api_and_goon_imports_do_not_require_httpx2_transport_at_module_import(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "httpx2.py"
    blocker.write_text(
        'raise RuntimeError("httpx2 imported too early")\n', encoding="utf-8"
    )
    env = os.environ.copy()
    pythonpath = [
        str(tmp_path),
        str(ROOT / "backend/src"),
    ]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    result = subprocess.run(
        (
            sys.executable,
            "-c",
            "from oh_goon.api import dependencies; "
            "from oh_goon.services.goon import GoonService; "
            "from oh_goon.integrations.gitlab_ci_component import GitLabComponentFetcher; "
            "GitLabComponentFetcher()",
        ),
        cwd=ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_direct_runtime_reference_is_admitted_by_hatch_metadata() -> None:
    payload = tomllib.loads(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    direct_runtime = [
        item for item in payload["project"]["dependencies"] if " @ " in item
    ]

    assert direct_runtime
    assert payload["build-system"]["build-backend"] == "hatchling.build"
    assert payload["tool"]["hatch"]["metadata"]["allow-direct-references"] is True
    assert "sources" not in payload["tool"]["uv"]


def test_hashmarks_upgrade_is_normal_uv_dependency_without_oh_goon_release_client() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    project_path = ROOT / "backend/pyproject.toml"
    project = project_path.read_text(encoding="utf-8")
    payload = tomllib.loads(project)
    hashmarks_dependencies = [
        item
        for item in payload["project"]["dependencies"]
        if item.startswith("hashmarks @ ")
    ]

    assert len(hashmarks_dependencies) == 1
    release_url = hashmarks_dependencies[0].split(" @ ", 1)[1]
    assert release_url.startswith(
        "https://github.com/Hugloss/Hashmarks/releases/download/v"
    )
    assert "/hashmarks-" in release_url
    assert release_url.endswith("-py3-none-any.whl")
    assert "hashmarks-release-acquire:" not in makefile
    assert "gh release download" not in makefile
    assert "vendor/hashmarks-" not in project
    assert "[tool.uv.sources]" not in project
    assert not (ROOT / "vendor/hashmarks-main-source.json").exists()
    assert not tuple((ROOT / "vendor").glob("hashmarks-*.whl"))


def test_ruff_repair_is_explicit_safe_mutation_and_qualification_stays_read_only() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    repair = makefile.split("\nruff-repair:\n", 1)[1].split("\n\n", 1)[0]
    static = makefile.split("\npython-static-sanity:\n", 1)[1].split("\n\n", 1)[0]

    assert ".venv/bin/ruff check --fix-only --select E9,F" in repair
    assert ".venv/bin/ruff format" in repair
    assert repair.index("check --fix-only") < repair.index("ruff format")
    assert "--unsafe-fixes" not in repair
    assert "--fix" not in static
    assert "--output-format json" in static
    assert ".venv/bin/oh-goon-ruff-advisory" in static
