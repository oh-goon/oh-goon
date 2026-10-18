import json
from pathlib import Path

from oh_goon.repository_tooling.architecture import (
    external_development_tooling_contract as contract,
)


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "backend/src/oh_goon").mkdir(parents=True)
    (root / "frontend/src").mkdir(parents=True)
    (root / "docs/development").mkdir(parents=True)
    (root / "vendor").mkdir()
    (root / "backend/pyproject.toml").write_text(
        "[project]\ndependencies = []\n", encoding="utf-8"
    )
    (root / "frontend/package.json").write_text(
        '{"dependencies": {}}\n', encoding="utf-8"
    )
    (root / "frontend/package-lock.json").write_text(
        '{"packages": {}}\n', encoding="utf-8"
    )
    (root / ".gitignore").write_text(
        ".agents-cookbook/\n.agent-economics/\n", encoding="utf-8"
    )
    (root / contract._SOURCE_RECORD).write_text(
        json.dumps(
            {
                "tool": "agentsCookbook",
                "commit": "a" * 40,
                "production_dependency": False,
                "runtime_authority": False,
                "certification_authority": False,
                "repository_intelligence_authority": False,
            }
        ),
        encoding="utf-8",
    )
    (root / contract._BOUNDARY_DOC).write_text(
        "external development-only aid\n"
        "Do **not** carry a previous hotspot or filename forward as a predetermined target\n"
        "Hashmarks\n"
        "Oh-Goon's own verification and certification gates remain authoritative\n",
        encoding="utf-8",
    )
    return root


def test_external_development_tooling_contract() -> None:
    assert contract.check_external_development_tooling_contract() == []


def test_rejects_product_dependency(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "backend/pyproject.toml").write_text(
        '[project]\ndependencies = ["agentsCookbook"]\n', encoding="utf-8"
    )
    violations = contract.check_external_development_tooling_contract(root)
    assert any(
        "must not be a product or package dependency" in item.message
        for item in violations
    )


def test_manifest_comment_is_not_dependency_leak(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "backend/pyproject.toml").write_text(
        "# agentsCookbook remains external developer tooling\n[project]\ndependencies = []\n",
        encoding="utf-8",
    )
    assert contract.check_external_development_tooling_contract(root) == []


def test_rejects_runtime_import(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    runtime = root / "backend/src/oh_goon/runtime.py"
    runtime.write_text("import agent_economics\n", encoding="utf-8")
    violations = contract.check_external_development_tooling_contract(root)
    assert any(
        item.path == runtime and "developer-only" in item.message for item in violations
    )


def test_rejects_dynamic_runtime_import(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    runtime = root / "backend/src/oh_goon/runtime.py"
    runtime.write_text(
        'import importlib\nmodule = importlib.import_module("agent_economics")\n',
        encoding="utf-8",
    )
    violations = contract.check_external_development_tooling_contract(root)
    assert any(
        item.path == runtime and "developer-only" in item.message for item in violations
    )


def test_runtime_comment_and_docstring_are_not_leaks(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    runtime = root / "backend/src/oh_goon/runtime.py"
    runtime.write_text(
        '"""agentsCookbook is not runtime authority."""\n'
        "# agent_economics must remain external\n"
        "VALUE = 'agent-economics is developer-only documentation'\n",
        encoding="utf-8",
    )
    assert contract.check_external_development_tooling_contract(root) == []


def test_repository_tooling_forbidden_capability_reference_is_not_runtime_leak(
    tmp_path: Path,
) -> None:
    root = _fixture_root(tmp_path)
    tooling = root / "backend/src/oh_goon/repository_tooling/check.py"
    tooling.parent.mkdir(parents=True)
    tooling.write_text(
        'FORBIDDEN = {"hashmarks_agent_economics": "forbidden"}\n', encoding="utf-8"
    )
    assert contract.check_external_development_tooling_contract(root) == []


def test_frontend_comment_is_not_leak_but_import_is(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    frontend = root / "frontend/src/runtime.ts"
    frontend.write_text(
        '// import("agent-economics") is forbidden here\n', encoding="utf-8"
    )
    assert contract.check_external_development_tooling_contract(root) == []
    frontend.write_text('const probe = import("agent-economics");\n', encoding="utf-8")
    violations = contract.check_external_development_tooling_contract(root)
    assert any(
        item.path == frontend and "developer-only" in item.message
        for item in violations
    )


def test_rejects_promoted_tool_authority(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    record = root / contract._SOURCE_RECORD
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["certification_authority"] = True
    record.write_text(json.dumps(payload), encoding="utf-8")
    violations = contract.check_external_development_tooling_contract(root)
    assert any(
        "certification_authority must remain explicitly false" in item.message
        for item in violations
    )


def test_requires_disposable_checkout_ignores(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / ".gitignore").write_text(".agents-cookbook/\n", encoding="utf-8")
    violations = contract.check_external_development_tooling_contract(root)
    assert any(".agent-economics/" in item.message for item in violations)
