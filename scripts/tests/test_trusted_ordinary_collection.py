"""Trusted native ordinary membership protects cases hidden by a candidate plan."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling import verification_gate_control as owner
from oh_goon.repository_tooling.pytest_process_environment import (
    resolve_pytest_invocation,
)
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from oh_goon.engine.command import run_command

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.process


@pytest.fixture
def ordinary_source(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    root = tmp_path / "repository"
    checks = root / "backend/checks"
    checks.mkdir(parents=True)
    (root / "scripts/tests").mkdir(parents=True)
    (root / "tests/integration").mkdir(parents=True)
    (root / "backend/pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["checks"]\n'
        'python_files=["behavior_*.py"]\npython_functions=["check_*"]\n'
        'markers=["daily: separate journey", "slow: exhaustive", "certification: strict"]\n'
    )
    case = "backend/checks/behavior_cases.py"
    helper = "backend/checks/behavior_helper.py"
    tooling = "scripts/tests/behavior_tooling.py"
    cross = "tests/integration/behavior_cross.py"
    (root / case).write_text(
        "import pytest\ndef check_answer():\n assert True\ndef check_new():\n assert True\n"
        '@pytest.mark.daily\ndef check_journey():\n raise AssertionError("not executed")\n'
    )
    (root / helper).write_text(
        '__test__=False\ndef check_helper():\n raise AssertionError("not a test")\n'
    )
    (root / tooling).write_text("def check_tooling():\n assert True\n")
    (root / cross).write_text("def check_cross():\n assert True\n")
    return root, (case, helper, tooling, cross)


def test_trusted_ordinary_membership_rejects_hidden_custom_case_and_allows_helper(
    ordinary_source: tuple[Path, tuple[str, ...]],
    tmp_path: Path,
) -> None:
    root, changed = ordinary_source
    source = canonical_source_identity(root)
    receipt = owner._trusted_ordinary_probe(
        root,
        trusted_root=ROOT,
        environment=ROOT / "backend/.venv",
        env=resolve_pytest_invocation(qualified=True).environment,
        changed_paths=changed,
        source_identity=source,
    )
    selected = [row for context in receipt["contexts"] for row in context["selected"]]
    deselected = [
        row for context in receipt["contexts"] for row in context["deselected"]
    ]
    assert [row["selector"] for row in selected] == [
        changed[0] + "::check_answer",
        changed[0] + "::check_new",
        changed[2] + "::check_tooling",
        changed[3] + "::check_cross",
    ]
    assert [row["selector"] for row in deselected] == [changed[0] + "::check_journey"]
    assert not any(row["file"] == changed[1] for row in selected + deselected)
    with pytest.raises(
        ValueError, match="Changed tests have no meaningful execution proof"
    ):
        owner._require_ordinary_changed_test_execution(
            receipt,
            changed_paths=changed,
            source_identity=source,
            executed_files=frozenset({changed[2], changed[3]}),
            executed_members=frozenset(
                {changed[2] + "::check_tooling", changed[3] + "::check_cross"}
            ),
        )
    owner._require_ordinary_changed_test_execution(
        receipt,
        changed_paths=changed,
        source_identity=source,
        executed_files=frozenset({changed[0], changed[2], changed[3]}),
        executed_members=frozenset(row["selector"] for row in selected),
    )
    with pytest.raises(
        ValueError, match="Changed tests have no meaningful execution proof"
    ):
        owner._require_ordinary_changed_test_execution(
            receipt,
            changed_paths=changed,
            source_identity=source,
            executed_files=frozenset(row["file"] for row in selected),
            executed_members=frozenset(
                row["selector"]
                for row in selected
                if row["selector"] != changed[0] + "::check_new"
            ),
        )
    report = tmp_path / "native-execution.json"
    executed = run_command(
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        "--qualified",
        "--pytest-config",
        str(root / "backend/pyproject.toml"),
        "--granularity",
        "tests",
        "--workers",
        "1",
        "--marker",
        receipt["contexts"][0]["request"]["marker"],
        "--report-path",
        str(report),
        *[str(root / path) for path in changed],
        cwd=root,
        env=resolve_pytest_invocation(qualified=True).environment,
        timeout=30,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    native_report = owner._read(report)
    # This native harness belongs to the installed repository; the temporary
    # fixture supplies executable members, not a replacement harness identity.
    passed_files = owner.require_native_pytest_pass(
        native_report, expected_source=canonical_source_identity(ROOT)
    )
    owner._require_ordinary_changed_test_execution(
        receipt,
        changed_paths=changed,
        source_identity=source,
        executed_files=frozenset(passed_files),
        executed_members=frozenset(native_report["membership_receipt"]["passed"]),
    )
    assert canonical_source_identity(root) == source


def test_candidate_python_startup_occurs_only_in_native_collector_children(
    ordinary_source: tuple[Path, tuple[str, ...]], tmp_path: Path
) -> None:
    root, changed = ordinary_source
    environment = tmp_path / "candidate-environment"
    selected_env = dict(resolve_pytest_invocation(qualified=True).environment)
    selected_env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    setup = run_command(
        "uv",
        "sync",
        "--offline",
        "--project",
        str(ROOT / "backend"),
        "--frozen",
        "--group",
        "dev",
        "--group",
        "quality",
        "--group",
        "build",
        "--python",
        sys.executable,
        env=selected_env,
        timeout=30,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    assert setup.returncode == 0, setup.stdout
    trace = tmp_path / "python-startups"
    site = next((environment / "lib").glob("python*/site-packages"))
    (site / "candidate-startup.pth").write_text(
        "import pathlib,sys; "
        f"pathlib.Path({str(trace)!r}).open('a').write(sys.argv[0] + '\\n')\n"
    )
    receipt = owner._trusted_ordinary_probe(
        root,
        trusted_root=ROOT,
        environment=environment,
        env=selected_env,
        changed_paths=changed,
        source_identity=canonical_source_identity(root),
    )
    collector = str(
        ROOT / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
    )
    startups = trace.read_text().splitlines()
    assert startups and set(startups) == {collector}
    assert all(
        context["runtime"]["prefix"] == str(environment)
        for context in receipt["contexts"]
    )


def test_candidate_source_change_during_native_collection_is_not_qualified(
    ordinary_source: tuple[Path, tuple[str, ...]],
) -> None:
    root, changed = ordinary_source
    target = root / changed[0]
    (root / "backend/conftest.py").write_text(
        "from pathlib import Path\n"
        "def pytest_sessionstart(session):\n"
        f" path=Path({str(target)!r})\n"
        " path.write_text(path.read_text() + '\\n# changed during collection\\n')\n"
    )
    with pytest.raises(ValueError, match="Source changed during ordinary collection"):
        owner._trusted_ordinary_probe(
            root,
            trusted_root=ROOT,
            environment=ROOT / "backend/.venv",
            env=resolve_pytest_invocation(qualified=True).environment,
            changed_paths=changed,
            source_identity=canonical_source_identity(root),
        )


def test_native_frontend_policy_does_not_turn_unconfigured_fixture_syntax_into_tests(
    tmp_path: Path,
) -> None:
    files = {
        "frontend/package.json": '{"name":"configured-ui","devDependencies":{"vitest":"1"}}',
        "frontend/src/component.test.ts": 'import {it} from "vitest"; it("works", () => {});',
        "templates/example/sample.test.ts": "export const fixture = 1;",
        "templates/example/sample_test.go": "package sample\nfunc TestRepresentation() {}\n",
    }
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    assert owner._trusted_other_changed_verifiers(tmp_path, tuple(files)) == frozenset(
        {"frontend/src/component.test.ts"}
    )


@pytest.mark.parametrize("fault", ["argv", "config", "environment"])
@pytest.mark.parametrize("context", [0, 2])
def test_receiver_rejects_changed_resolved_authority_before_collection(
    ordinary_source: tuple[Path, tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    context: int,
) -> None:
    root, changed = ordinary_source
    environment = ROOT / "backend/.venv"
    env = dict(os.environ)
    requests = owner._ordinary_collection_requests(
        root, trusted_root=ROOT, environment=environment, env=env
    )
    source = canonical_source_identity(root)
    if fault == "argv":
        requests[context]["request"]["argv"].append("--deselect=hidden")
    elif fault == "config":
        with (root / "backend/pyproject.toml").open("a") as stream:
            stream.write("\n# Changed after admission\n")
    else:
        env["OH_GOON_NATIVE_QUALIFICATION_PROFILE"] = "changed-after-admission"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("native collection started before authority verification")

    monkeypatch.setattr(
        "oh_goon.repository_tooling.pytest_member_selection.run_command",
        forbidden,
    )
    with pytest.raises(ValueError, match="resolved invocation authority"):
        owner._ordinary_candidate_collection(
            root,
            trusted_root=ROOT,
            environment=environment,
            changed_paths=changed,
            source_identity=source,
            requests=requests,
            env=env,
        )
