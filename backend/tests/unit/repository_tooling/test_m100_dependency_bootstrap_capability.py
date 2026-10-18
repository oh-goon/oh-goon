from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_certification_storage_loss_boundary,
)
from oh_goon.repository_tooling.architecture._structural_project_tooling_contract import (
    check_project_tooling_contract,
)


def _repo(tmp_path: Path, *, module: str = "policy_bootstrap_impl") -> Path:
    root = tmp_path / "repo"
    source = root / "backend/src/oh_goon/repository_tooling" / f"{module}.py"
    source.parent.mkdir(parents=True)
    source.write_text("def main():\n    return 0\n", encoding="utf-8")
    pyproject = root / "backend/pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "probe"\n'
        'version = "0.0.0"\n'
        "[project.scripts]\n"
        f'oh-goon-dependency-policy = "oh_goon.repository_tooling.{module}:main"\n',
        encoding="utf-8",
    )
    makefile = root / "Makefile"
    makefile.write_text(
        "DEPENDENCY_POLICY = uv run --no-project python "
        f"backend/src/oh_goon/repository_tooling/{module}.py\n"
        "dependency-policy:\n"
        "\t@$(DEPENDENCY_POLICY) public-lock-check\n",
        encoding="utf-8",
    )
    return root


def _make_bootstrap_messages(root: Path) -> list[str]:
    needles = (
        "bootstrap capability",
        "dependency policy",
        "pre-environment",
        "source-file path",
        "source-execute",
    )
    return [
        violation.message
        for violation in check_project_tooling_contract(root)
        if any(needle in violation.message for needle in needles)
    ]


def _storage_bootstrap_violations(root: Path) -> list[str]:
    result: list[str] = []
    for violation in _check_certification_storage_loss_boundary(root):
        path_text = violation.path.as_posix()
        if (
            "dependency bootstrap capability" in violation.message
            or "public-lock policy" in violation.message
            or "dependency_resolution_bootstrap.py" in path_text
        ):
            result.append(f"{path_text}: {violation.message}")
    return result


def test_private_bootstrap_module_rename_is_architecture_invariant(tmp_path: Path) -> None:
    root = _repo(tmp_path, module="renamed_policy_bootstrap")

    assert _make_bootstrap_messages(root) == []
    assert _storage_bootstrap_violations(root) == []


def test_make_cannot_source_execute_a_module_other_than_the_capability_owner(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    other = root / "backend/src/oh_goon/repository_tooling/arbitrary_tool.py"
    other.write_text("def main():\n    return 0\n", encoding="utf-8")
    (root / "Makefile").write_text(
        "DEPENDENCY_POLICY = uv run --no-project python "
        "backend/src/oh_goon/repository_tooling/arbitrary_tool.py\n"
        "dependency-policy:\n"
        "\t@$(DEPENDENCY_POLICY) public-lock-check\n",
        encoding="utf-8",
    )

    messages = _make_bootstrap_messages(root)
    assert any("pre-environment dependency-policy capability" in message or "only the explicit pre-environment" in message for message in messages)


def test_missing_semantic_bootstrap_capability_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "backend/pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.0.0"\n', encoding="utf-8"
    )

    messages = _make_bootstrap_messages(root)
    assert any("bootstrap capability must map" in message for message in messages)


def test_bootstrap_capability_cannot_escape_repository_tooling(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = root / "backend/src/oh_goon/arbitrary_bootstrap.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("def main():\n    return 0\n", encoding="utf-8")
    (root / "backend/pyproject.toml").write_text(
        "[project]\n"
        'name = "probe"\n'
        'version = "0.0.0"\n'
        "[project.scripts]\n"
        'oh-goon-dependency-policy = "oh_goon.arbitrary_bootstrap:main"\n',
        encoding="utf-8",
    )

    messages = _make_bootstrap_messages(root)
    assert any("bootstrap capability must map" in message for message in messages)


def test_other_make_source_file_python_remains_forbidden(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with (root / "Makefile").open("a", encoding="utf-8") as handle:
        handle.write(
            "OTHER_TOOL = uv run --no-project python "
            "backend/src/oh_goon/repository_tooling/arbitrary_tool.py\n"
            "other-tool:\n"
            "\t@$(OTHER_TOOL) --version\n"
        )

    messages = _make_bootstrap_messages(root)
    assert any("only the explicit pre-environment" in message for message in messages)


def test_architecture_does_not_authorize_the_private_bootstrap_filename() -> None:
    root = Path(__file__).resolve().parents[4]
    architecture = root / "backend/src/oh_goon/repository_tooling/architecture"
    offenders = [
        path.relative_to(root).as_posix()
        for path in architecture.glob("*.py")
        if "dependency_resolution_bootstrap.py" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
