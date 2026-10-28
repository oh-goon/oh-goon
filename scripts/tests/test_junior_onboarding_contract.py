import ast
from pathlib import Path

from oh_goon.services.repository_discovery import RepositoryDiscoveryService

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
APP_RUNBOOK = (ROOT / "docs/development/APP_TEST_RUN.md").read_text(encoding="utf-8")


def _target_body(name: str) -> str:
    marker = f"{name}:"
    lines_all = MAKEFILE.splitlines()
    start_line = next(i for i, line in enumerate(lines_all) if line == marker)
    rest = "\n".join(lines_all[start_line:])
    lines = rest.splitlines()
    body = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace() and ":" in line:
            break
        body.append(line)
    return "\n".join(body)


def test_general_fleet_discovery_accepts_git_repo_without_root_pyproject(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()

    entries = RepositoryDiscoveryService().discover((repository,), {})

    assert [entry.repository.path for entry in entries] == [repository.resolve()]


def test_junior_development_entrypoint_is_make_test() -> None:
    body = _target_body("test")
    assert "$(DEVELOPMENT_TEST_FRONTDOOR)" in body
    assert "$(CERTIFICATION_RUNNER)" not in body
    assert "app-certification" not in body
    assert "repository-id" not in body
    assert "goon.lock" not in body
    assert "plan run" not in body


def test_app_test_runner_owns_bootstrap_not_goon_order() -> None:
    runner = (ROOT / "backend/src/oh_goon/workflow.py").read_text(encoding="utf-8")
    assert "oh_goon.core.product_identity" not in runner
    assert "DATABASE_SCHEMA_VERSION" not in runner
    assert "oh_goon.runtime." not in runner
    assert "observe_local_executor_capacity" not in runner
    assert '"league", "add-folder"' in runner
    assert '["dev", "_lock-plan", plan, "--output", str(lock_path)]' in runner
    assert '"--lock-file",' in runner
    assert any(
        isinstance(node, (ast.List, ast.Tuple))
        and len(node.elts) >= 3
        and [ast.dump(item) for item in node.elts[:3]]
        == [
            ast.dump(ast.Constant(value="dev")),
            ast.dump(ast.Constant(value="_run-plan")),
            ast.dump(ast.Name(id="plan", ctx=ast.Load())),
        ]
        for node in ast.walk(ast.parse(runner))
    )
    assert "repository_tooling.source_normalization" not in runner
    assert "OH_GOON_COPY_VENV" not in runner
    assert "import-certification-dependencies" not in runner
    assert "test-python" not in runner
    assert "browser-targeted-certification" not in runner


def test_junior_docs_have_no_explicit_self_repository_id_path() -> None:
    docs = APP_RUNBOOK + (ROOT / "docs/development/APP_CERTIFICATION.md").read_text(
        encoding="utf-8"
    )
    assert "OH_GOON_SELF_REPOSITORY_ID=repo-" not in docs
    assert "make certification-explain OH_GOON_RUN_ID=" in (
        ROOT / "backend/src/oh_goon/cli_runtime_plans.py"
    ).read_text(encoding="utf-8")


def test_make_test_frontdoor_has_no_certification_repo_or_plan_semantics() -> None:
    body = _target_body("test")
    assert "$(DEVELOPMENT_TEST_FRONTDOOR)" in body
    assert "repository-id" not in body
    assert "goon.lock" not in body
    assert "plan run" not in body
