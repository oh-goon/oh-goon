from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_development_test_runner_boundary,
)


def _write_runner(root: Path, lines: list[str]) -> None:
    path = root / "scripts/development_test.sh"
    path.parent.mkdir(parents=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        + "\n".join(
            [
                "whatever_private_helper() {",
                '  make --no-print-directory "$2"',
                "}",
                "renamed_check() {",
                '  make --no-print-directory "$2"',
                "}",
                *lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _base_lines() -> list[str]:
    return [
        'whatever_private_helper "renamed diagnostic label" test-python-core-diagnostic',
        'renamed_check "unit label can change" test-python',
        'renamed_check "tooling label can change" test-tooling',
        'renamed_check "integration label can change" test-integration',
        'renamed_check "frontend type label can change" frontend-typecheck',
        'renamed_check "frontend label can change" test-frontend',
        'renamed_check "late dependency check" test-python-dependency-complete',
        'renamed_check "generated label can change" check-generated',
    ]


def test_development_runner_architecture_ignores_private_helper_and_label_spelling(
    tmp_path: Path,
) -> None:
    _write_runner(tmp_path, _base_lines())
    assert _check_development_test_runner_boundary(tmp_path) == []


def test_development_runner_architecture_still_rejects_semantic_target_reordering(
    tmp_path: Path,
) -> None:
    lines = _base_lines()
    generated = lines.pop()
    lines.insert(0, generated)
    _write_runner(tmp_path, lines)
    assert any(
        "dependency-light diagnostics before" in i.message
        for i in _check_development_test_runner_boundary(tmp_path)
    )
