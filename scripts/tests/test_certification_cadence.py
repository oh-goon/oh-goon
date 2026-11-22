"""Real native membership keeps qualification deferral separate from execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from oh_goon.repository_tooling import development_verification as owner
from oh_goon.repository_tooling import verification_gate_control as gate
from oh_goon.repository_tooling.pytest_process_environment import (
    PytestInvocation,
    resolve_pytest_invocation,
)


@pytest.fixture
def qualification_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    root = tmp_path / "repository"
    files = {
        "backend/pyproject.toml": (
            '[project]\nname="cadence-fixture"\nversion="0.1.0"\n'
            '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n'
            'markers=["certification", "daily", "slow"]\n'
        ),
        "backend/tests/__init__.py": "",
        "backend/tests/factories/__init__.py": "",
        "backend/tests/factories/native_support.py": "def value():\n    return 41\n",
        "backend/tests/test_native.py": (
            "import pytest\nfrom tests.factories.native_support import value\n"
            "@pytest.mark.certification\ndef test_native():\n"
            "    assert value() == 41\n    raise AssertionError('qualification must not execute')\n"
        ),
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    owner._git(root, "init", "-q")
    owner._git(root, "add", ".")
    owner._git(
        root,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "baseline",
    )
    base = owner._git(root, "rev-parse", "HEAD").strip()
    for name in (
        "backend/tests/factories/native_support.py",
        "backend/tests/test_native.py",
    ):
        path = root / name
        path.write_text(path.read_text().replace("41", "42"), encoding="utf-8")
    return root, base


def _admit_qualification(root: Path, base: str) -> owner.ChangeVerificationPlan:
    plan = owner.admit_change(root, base=base)
    owner.require_merge_selection(root, plan)
    return plan


def test_real_certification_and_qualified_support_are_not_run(
    qualification_repository: tuple[Path, str],
) -> None:
    root, base = qualification_repository
    plan = _admit_qualification(root, base)
    assert plan.pytest == () and not plan.unverified
    deferred = plan.evidence["certification_deferred"]
    assert deferred == {
        "status": "NOT_RUN",
        "marker": owner._MERGE_MARKER,
        "changed_tests": ["backend/tests/test_native.py"],
        "support_consumers": {
            "backend/tests/factories/native_support.py": [
                "backend/tests/test_native.py"
            ]
        },
        "consumer_coverage": "not-claimed",
    }
    assert "verification_support_admission" not in plan.evidence
    support = next(
        row
        for row in plan.evidence["observations"]
        if "/factories/" in row["changed_path"]
    )
    assert support["qualification"]["completeness"] == "incomplete"
    assert support["qualification"]["negative_evidence_admissible"] is False
    assert gate.require_certification_deferred_scope(
        plan.changed_paths, plan.evidence
    ) == set(plan.changed_paths)


@pytest.mark.parametrize("remaining", ["daily", "slow", "empty", "filter", "mixed"])
def test_real_noncertification_exclusions_do_not_defer_changed_proof(
    qualification_repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    remaining: str,
) -> None:
    root, base = qualification_repository
    _admit_qualification(root, base)
    path = root / "backend/tests/test_native.py"
    contents = path.read_text()
    if remaining in {"daily", "slow"}:
        contents = contents.replace("mark.certification", "mark." + remaining)
    elif remaining == "empty":
        contents = "from tests.factories.native_support import value\nVALUE = value\n"
    elif remaining == "filter":
        contents = contents.replace("@pytest.mark.certification\n", "")
    else:
        contents += "\ndef test_ordinary():\n    assert True\n"
    path.write_text(contents, encoding="utf-8")
    if remaining in {"filter", "mixed"}:

        def filtered(**options: Any) -> PytestInvocation:
            return resolve_pytest_invocation(
                **options, pytest_args=("-k", "never_selected")
            )

        monkeypatch.setattr(owner, "resolve_pytest_invocation", filtered)
    plan = owner.admit_change(root, base=base)
    assert "certification_deferred" not in plan.evidence
    assert plan.unverified
    with pytest.raises(ValueError, match="SELECTION_UNVERIFIED"):
        owner.require_merge_selection(root, plan)


def test_changed_native_marker_is_refused_before_qualification_can_run(
    qualification_repository: tuple[Path, str],
) -> None:
    root, base = qualification_repository
    _admit_qualification(root, base)
    (root / "backend/tests/conftest.py").write_text(
        "def pytest_configure(config):\n    config.option.markexpr = 'not daily'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="changed its marker"):
        owner.admit_change(root, base=base, focused_feedback=True)
