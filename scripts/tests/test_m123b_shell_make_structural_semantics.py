from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_host_python_selector_contract,
)
from oh_goon.repository_tooling.architecture._structural_authority_workflow import (
    check_make_single_path,
)
from oh_goon.repository_tooling.architecture._structural_project_tooling_contract import (
    _check_make_command_owners,
    _check_operator_uv_environment,
    _check_precommit,
)
from oh_goon.repository_tooling.architecture.shell_semantics import make_semantic_facts


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _selector_messages(tmp_path: Path, text: str) -> list[str]:
    path = _write(tmp_path, "host_python.sh", text)
    return [
        violation.message for violation in _check_host_python_selector_contract(path)
    ]


def _tooling_repo(tmp_path: Path, makefile: str) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    source = _write(
        root,
        "backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py",
        "def main() -> int:\n    return 0\n",
    )
    assert source.is_file()
    pyproject_text = (
        "[project]\nname='oh-goon'\nversion='0.0.0'\n"
        "[project.scripts]\n"
        "oh-goon-dependency-policy='oh_goon.repository_tooling.dependency_resolution_bootstrap:main'\n"
    )
    _write(root, "backend/pyproject.toml", pyproject_text)
    path = _write(root, "Makefile", makefile)
    return root, path, pyproject_text


def _tooling_messages(tmp_path: Path, makefile: str) -> list[str]:
    root, path, pyproject_text = _tooling_repo(tmp_path, makefile)
    facts = make_semantic_facts(makefile)
    return [
        violation.message
        for violation in _check_make_command_owners(root, path, facts, pyproject_text)
    ]


def _workflow_makefile() -> str:
    return """\
DEV = bash scripts/development_check.sh
RESOURCES = bash scripts/development_test_resources.sh
FLOW = backend/.venv/bin/oh-goon-workflow
LOGGER = bash scripts/run_logged.sh
TEST_GATE = OH_GOON_RUN_LOG_DIR=/tmp/log $(LOGGER) test $(RESOURCES) $(DEV)
CERT_GATE = OH_GOON_RUN_LOG_DIR=/tmp/log $(LOGGER) certify $(RESOURCES) $(FLOW)

test:
\t@$(TEST_GATE)

certify:
\t@test -x "$(FLOW)" || { printf '%s\\n' 'Run: make init'; exit 2; }
\t@backend/.venv/bin/oh-goon-check-generated
\t@$(CERT_GATE)

prepare-release:
\t@$(FLOW) release

certification-explain:
\t@$(FLOW) explain "$(RUN_ID)"

release-version-set:
\tbackend/.venv/bin/oh-goon-release-version set "$(OH_GOON_RELEASE_VERSION)"
"""


def _workflow_root(tmp_path: Path, makefile: str | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        "backend/pyproject.toml",
        "[project]\nname='oh-goon'\nversion='0.0.0'\n"
        "[project.scripts]\noh-goon-workflow='oh_goon.services.certification_frontdoor:main'\n",
    )
    _write(root, "Makefile", makefile or _workflow_makefile())
    return root


def test_selector_comment_decoy_is_representation_only(tmp_path: Path) -> None:
    assert (
        _selector_messages(
            tmp_path,
            "select_host_python() {\n  # uv sync is forbidden here; this is only documentation\n  printf '%s\\n' python3\n}\n",
        )
        == []
    )


def test_selector_scalar_alias_cannot_hide_dependency_acquisition(
    tmp_path: Path,
) -> None:
    messages = _selector_messages(
        tmp_path,
        'select_host_python() {\n  tool=uv\n  action=sync\n  "$tool" "$action" --project backend\n}\n',
    )
    assert any("must never acquire dependencies" in message for message in messages)


def test_selector_helper_extraction_cannot_hide_dependency_acquisition(
    tmp_path: Path,
) -> None:
    messages = _selector_messages(
        tmp_path,
        'acquire() {\n  native=uv\n  operation=lock\n  "$native" "$operation" --project backend\n}\n'
        "select_host_python() { printf '%s\\n' python3; }\n",
    )
    assert any("must never acquire dependencies" in message for message in messages)


def test_selector_array_alias_cannot_hide_dependency_acquisition(
    tmp_path: Path,
) -> None:
    messages = _selector_messages(
        tmp_path,
        'select_host_python() {\n  operation=(uv sync)\n  "${operation[@]}" --project backend\n}\n',
    )
    assert any("must never acquire dependencies" in message for message in messages)


def test_make_comments_do_not_create_python_or_frontend_authority(
    tmp_path: Path,
) -> None:
    messages = _tooling_messages(
        tmp_path,
        "POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py\n"
        "check:\n\t@$(POLICY) public-lock-check\n\t# python3 ghost.py; npx playwright test\n",
    )
    assert not any("direct python/python3" in message for message in messages)
    assert not any("node/npx" in message for message in messages)


def test_make_command_alias_cannot_hide_direct_python(tmp_path: Path) -> None:
    messages = _tooling_messages(
        tmp_path,
        "POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py\n"
        "PY = python3\n"
        "check:\n\t@$(POLICY) public-lock-check\n\t@$(PY) scripts/ghost.py\n",
    )
    assert any("direct python/python3 execution" in message for message in messages)


def test_make_command_alias_cannot_hide_direct_node(tmp_path: Path) -> None:
    messages = _tooling_messages(
        tmp_path,
        "POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py\n"
        "JS = npx\n"
        "check:\n\t@$(POLICY) public-lock-check\n\t@$(JS) playwright test\n",
    )
    assert any("node/npx" in message for message in messages)


def test_dependency_policy_private_variable_name_is_representation_only(
    tmp_path: Path,
) -> None:
    messages = _tooling_messages(
        tmp_path,
        "NATIVE = uv\nACTION = run\n"
        "CAP = $(NATIVE) $(ACTION) --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py\n"
        "check:\n\t@$(CAP) public-lock-check\n",
    )
    assert not any("dependency-policy capability" in message for message in messages)
    assert not any("source-execute" in message for message in messages)


def test_renamed_unowned_source_execution_still_fails(tmp_path: Path) -> None:
    messages = _tooling_messages(
        tmp_path,
        "POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py\n"
        "OTHER = backend/src/oh_goon/repository_tooling/shadow.py\n"
        "check:\n\t@$(POLICY) public-lock-check\n\t@uv run --no-project python $(OTHER)\n",
    )
    assert any(
        "only the explicit pre-environment dependency-policy capability" in message
        for message in messages
    )


def test_operator_uv_comment_decoy_is_inert(tmp_path: Path) -> None:
    root, path, _ = _tooling_repo(
        tmp_path,
        "check:\n\t# UV_CONCURRENT_DOWNLOADS=99 uv --default-index https://example.invalid\n\t@uv --version\n",
    )
    del root
    facts = make_semantic_facts(path.read_text(encoding="utf-8"))
    assert _check_operator_uv_environment(path, facts) == []


def test_operator_uv_environment_assignment_remains_semantic(tmp_path: Path) -> None:
    root, path, _ = _tooling_repo(
        tmp_path, "UV_CONCURRENT_DOWNLOADS = 99\ncheck:\n\t@uv --version\n"
    )
    del root
    facts = make_semantic_facts(path.read_text(encoding="utf-8"))
    assert any(
        "download concurrency" in item.message
        for item in _check_operator_uv_environment(path, facts)
    )


def test_precommit_comment_decoy_is_representation_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        ".pre-commit-config.yaml",
        "# entry: bash -lc 'PYTHONPATH=backend/src python3 -m oh_goon'\n"
        "repos:\n- repo: local\n  hooks:\n  - id: clean\n    name: clean\n"
        "    language: system\n    entry: uv run --frozen --project backend oh-goon-architecture\n",
    )
    assert _check_precommit(root) == []


def test_precommit_shell_alias_cannot_hide_direct_python(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        ".pre-commit-config.yaml",
        "repos:\n- repo: local\n  hooks:\n  - id: bad\n    name: bad\n"
        "    language: system\n"
        "    entry: bash -lc 'launcher=python3; \"$launcher\" -m oh_goon'\n",
    )
    assert any(
        "direct python/python3" in item.message for item in _check_precommit(root)
    )


def test_workflow_make_private_variable_names_are_representation_only(
    tmp_path: Path,
) -> None:
    root = _workflow_root(tmp_path)
    assert check_make_single_path(root) == []


def test_workflow_make_helper_variable_extraction_is_representation_only(
    tmp_path: Path,
) -> None:
    root = _workflow_root(tmp_path)
    assert check_make_single_path(root) == []

    makefile = (
        _workflow_makefile()
        .replace(
            "LOGGER = bash scripts/run_logged.sh",
            "SHELL = bash\nLOG_SCRIPT = scripts/run_logged.sh\nLOGGER = $(SHELL) $(LOG_SCRIPT)",
        )
        .replace(
            "RESOURCES = bash scripts/development_test_resources.sh",
            "RESOURCE_SCRIPT = scripts/development_test_resources.sh\nRESOURCES = $(SHELL) $(RESOURCE_SCRIPT)",
        )
    )
    _write(root, "Makefile", makefile)
    assert check_make_single_path(root) == []


def test_workflow_make_comment_cannot_create_direct_plan_path(tmp_path: Path) -> None:
    makefile = (
        _workflow_makefile()
        + "\n# oh-goon run is forbidden outside the workflow owner\n"
    )
    root = _workflow_root(tmp_path, makefile)
    assert not any(
        "direct Plan execution path" in item.message
        for item in check_make_single_path(root)
    )


def test_workflow_make_cannot_restore_the_old_broad_default(tmp_path: Path) -> None:
    root = _workflow_root(tmp_path)
    assert check_make_single_path(root) == []
    _write(
        root,
        "Makefile",
        _workflow_makefile().replace(
            "DEV = bash scripts/development_check.sh",
            "DEV = bash scripts/development_test.sh",
        ),
    )
    assert any(
        "one-line delegation" in item.message for item in check_make_single_path(root)
    )


def test_workflow_make_alias_cannot_hide_direct_plan_path(tmp_path: Path) -> None:
    makefile = (
        _workflow_makefile()
        + "\nCLI = backend/.venv/bin/oh-goon\nshadow:\n\t@$(CLI) run run.yml\n"
    )
    root = _workflow_root(tmp_path, makefile)
    assert any(
        "direct Plan execution path" in item.message
        for item in check_make_single_path(root)
    )


def test_workflow_make_extra_test_choreography_still_fails(tmp_path: Path) -> None:
    makefile = _workflow_makefile().replace(
        "test:\n\t@$(TEST_GATE)",
        "test:\n\t@$(TEST_GATE)\n\t@$(MAKE) test-python",
    )
    root = _workflow_root(tmp_path, makefile)
    assert any(
        "one-line delegation" in item.message for item in check_make_single_path(root)
    )
