from __future__ import annotations

import copy
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    CommandSupervisorReceipt,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.repository_tooling import python_project_checks


def _stdlib_project(root: Path, name: str, body: str) -> Path:
    project = root / "templates/goons/general" / name
    (project / "tests").mkdir(parents=True)
    (project / "src/native_fixture").mkdir(parents=True)
    (project / "src/native_fixture/__init__.py").write_text("VALUE = 42\n")
    (project / "README.md").write_text(f"# general/{name}\n")
    (project / "goon.yml").write_text(
        "runtime: python\nsteps:\n- run: python --version\n"
    )
    (project / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="0.1.0"\nrequires-python=">=3.12"\ndependencies=[]\n'
        '[build-system]\nrequires=["hatchling==1.32.0"]\nbuild-backend="hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages=["src/native_fixture"]\n'
    )
    (project / "tests/test_native.py").write_text(
        "import unittest\nfrom native_fixture import VALUE\n" + body
    )
    locked = run_command(
        "uv",
        "lock",
        "--project",
        str(project),
        timeout=30,
        require_process_group_quiescence=True,
        parent_death_signal=True,
        max_capture_bytes_per_stream=8192,
    )
    assert locked.returncode == 0, locked.stderr
    return project


@pytest.mark.process
def test_native_shipped_stdlib_unittest_projects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for name in ("native-first", "native-second"):
        _stdlib_project(
            source,
            name,
            "class NativeChecks(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 42)\n"
            "    def test_second(self): self.assertTrue(True)\n",
        )
    reports = python_project_checks.check_shipped_stdlib_unittest_projects(source)
    assert {report["package"] for report in reports} == {
        "general/native-first",
        "general/native-second",
    }
    for report in reports:
        native = report["native_report"]
        assert (
            native["selected"]
            == native["started"]
            == native["passed"]
            == [
                "test_native.NativeChecks.test_second",
                "test_native.NativeChecks.test_value",
            ]
        )
        assert native["tests_run"] == 2
        assert not Path(native["environment"]).exists()
        assert all(
            row["returncode"] == 0 and row["process_group_quiescent"]
            for row in report["native_execution"]
        )
    # The same native proof consumes every eligible shipped project. New stdlib
    # Python Goons enter through the existing compiler census, without a name map.
    actual = python_project_checks.check_shipped_stdlib_unittest_projects()
    for report in actual:
        python_project_checks.require_unittest_project_pass(report["native_report"])


@pytest.mark.process
@pytest.mark.parametrize(
    "body",
    [
        "",
        "class NativeChecks(unittest.TestCase):\n    def test_value(self): self.fail('failure')\n",
        "class NativeChecks(unittest.TestCase):\n    @unittest.skip('unavailable')\n    def test_value(self): pass\n",
        "class NativeChecks(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_value(self): self.fail('expected')\n",
        "class NativeChecks(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_value(self): pass\n",
        "class NativeChecks(unittest.TestCase):\n    def test_value(self): pass\n"
        "def load_tests(loader, tests, pattern):\n"
        "    case = NativeChecks('test_value')\n"
        "    return unittest.TestSuite([case, case])\n",
    ],
)
def test_native_stdlib_project_cannot_pass_empty_or_nonpassing_suite(
    tmp_path: Path, body: str
) -> None:
    _stdlib_project(tmp_path, "native-negative", body)
    with pytest.raises(ValueError, match="Native (unittest|standalone project)"):
        python_project_checks.check_shipped_stdlib_unittest_projects(tmp_path)


@pytest.mark.parametrize("unsupported", ["runtime-dependency", "group", "uv-override"])
def test_other_native_project_contexts_do_not_start_stdlib_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsupported: str
) -> None:
    project = _stdlib_project(tmp_path, "native-other", "")
    metadata = project / "pyproject.toml"
    text = metadata.read_text()
    if unsupported == "runtime-dependency":
        text = text.replace("dependencies=[]", 'dependencies=["outside"]')
    elif unsupported == "group":
        text += '\n[dependency-groups]\ndev=["pytest"]\n'
    else:
        text += "\n[tool.uv]\nmanaged=false\n"
    metadata.write_text(text)
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *args, **kwargs: pytest.fail(
            "unsupported context started native execution"
        ),
    )
    assert python_project_checks.check_shipped_stdlib_unittest_projects(tmp_path) == ()


@pytest.mark.parametrize("kind", ["shell", "application"])
def test_physical_stdlib_project_requires_compiled_standalone_python_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    project = _stdlib_project(tmp_path, "native-wrong-runtime", "")
    if kind == "shell":
        (project / "goon.yml").write_text("steps:\n- run: python --version\n")
    else:
        (project / "goon.yml").write_text(
            "runtime: python\napplication: {path: application}\nsteps:\n- run: python --version\n"
        )
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *args, **kwargs: pytest.fail("invalid runtime started native execution"),
    )
    with pytest.raises(ValueError, match="requires a captured standalone Python Goon"):
        python_project_checks.check_shipped_stdlib_unittest_projects(tmp_path)


@pytest.mark.parametrize("alteration", ["omitted", "added", "duplicate", "wrong-count"])
def test_unittest_result_requires_exact_native_membership(alteration: str) -> None:
    native: dict[str, Any] = {
        "selected": ["example.Case.test_one", "example.Case.test_two"],
        "started": ["example.Case.test_one", "example.Case.test_two"],
        "passed": ["example.Case.test_one", "example.Case.test_two"],
        "tests_run": 2,
        **{
            name: []
            for name in (
                "loader_errors",
                "failures",
                "errors",
                "skipped",
                "expected_failures",
                "unexpected_successes",
            )
        },
    }
    python_project_checks.require_unittest_project_pass(native)
    if alteration == "omitted":
        native["passed"].pop()
    elif alteration == "added":
        native["passed"].append("example.Case.test_extra")
    elif alteration == "duplicate":
        native["selected"] *= 2
    else:
        native["tests_run"] = True
    with pytest.raises(ValueError, match="Native unittest membership"):
        python_project_checks.require_unittest_project_pass(native)


def test_required_project_checks_select_frozen_application_tests_without_airflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_GOON_PYTEST_REPORT_PATH", str(tmp_path / "parent.json"))
    monkeypatch.setenv(
        "OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY", str(tmp_path / "parent-projects")
    )
    monkeypatch.setenv(
        "OH_GOON_VITEST_JSON_REPORT", str(tmp_path / "parent-vitest.json")
    )
    monkeypatch.setenv("PYTEST_REPORT_PATH", "caller-owned")
    commands: list[tuple[str, ...]] = []

    def complete(*arguments: str, **_kwargs: Any) -> CommandResult:
        environment = _kwargs["env"]
        assert environment["PYTEST_REPORT_PATH"] == "caller-owned"
        assert not any(
            name in environment
            for name in (
                "OH_GOON_PYTEST_REPORT_PATH",
                "OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY",
                "OH_GOON_VITEST_JSON_REPORT",
            )
        )
        commands.append(arguments)
        return CommandResult(0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", complete)
    assert python_project_checks.run_project_tests(tmp_path) == 0
    synchronization = [command for command in commands if command[1] == "sync"]
    assert len(synchronization) == 2
    for command in synchronization:
        assert "--frozen" in command and "--locked" not in command
        assert command[command.index("--group") + 1] == "dev"
        assert "airflow" not in command
    orders_tests = next(
        command
        for command in commands
        if str(tmp_path / "templates/goons/general/orders-taskflow/tests") in command
    )
    assert (
        str(tmp_path / "templates/goons/general/orders-taskflow/tests") in orders_tests
    )
    assert not any("test_airflow_adapter" in argument for argument in orders_tests)


def test_failed_native_project_check_cleans_owned_environment_and_preserves_developer_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    developer = tmp_path / "templates/goons/ml/iris-train/.venv/developer"
    developer.parent.mkdir(parents=True)
    developer.write_text("preserved")
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))

    def failed(*_arguments: str, env: dict[str, str], **_kwargs: Any) -> CommandResult:
        environment = Path(env["UV_PROJECT_ENVIRONMENT"])
        assert environment.is_relative_to(scratch)
        environment.mkdir()
        (environment / "partial-install").write_text("owned")
        return CommandResult(7, "native failure", "")

    monkeypatch.setattr(python_project_checks, "run_command", failed)
    assert python_project_checks.run_project_tests(tmp_path) == 7
    assert not scratch.exists()
    assert developer.read_text() == "preserved"


@pytest.mark.parametrize("quiescent", [False, True])
def test_native_project_cancellation_respects_process_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    quiescent: bool,
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))

    def cancelled(
        *_arguments: str, process_observer: Any, **_kwargs: Any
    ) -> CommandResult:
        process_observer(CommandProcessLifecycleEvent("PROCESS_STARTING", "process"))
        (scratch / "partial-install").write_text("owned")
        raise CommandCancelledError(
            "cancelled",
            supervisor=CommandSupervisorReceipt(
                final_process_group_quiescent=quiescent
            ),
        )

    monkeypatch.setattr(python_project_checks, "run_command", cancelled)
    with pytest.raises(CommandCancelledError, match="cancelled"):
        python_project_checks.run_project_tests(tmp_path)
    assert scratch.exists() is not quiescent
    if not quiescent:
        assert "process cleanup is unproven" in capsys.readouterr().err
        shutil.rmtree(scratch)


def test_project_cleanup_failure_does_not_replace_native_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *_args, **_kwargs: CommandResult(7, "", ""),
    )

    def deny_cleanup(_path: Path) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(shutil, "rmtree", deny_cleanup)
    assert python_project_checks.run_project_tests(tmp_path) == 7
    assert "environment cleanup failed" in capsys.readouterr().err


@pytest.mark.parametrize("phase", ("sync", "pytest"))
def test_daily_projects_continue_independent_failures_and_preserve_native_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    commands: list[tuple[str, ...]] = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        commands.append(arguments)
        failed = "iris-train" in arguments[
            arguments.index("--project") + 1
        ] and arguments[1] == ("sync" if phase == "sync" else "run")
        return CommandResult(7 if failed else 0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", run)
    assert (
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
        == 7
    )
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["schema"] == python_project_checks.PROJECT_REPORT_SCHEMA
    assert summary["cleanup"] == "VERIFIED"
    assert summary["first_nonzero_exit_code"] == 7
    assert summary["projects"][0][phase + "_exit_code"] == 7
    assert summary["projects"][1]["pytest_exit_code"] == 0
    assert summary["projects"][1]["phase"] == "COMPLETE"
    assert len(commands) == (3 if phase == "sync" else 4)
    for command in commands:
        if command[1] == "run":
            assert "--maxfail=0" in command
            assert "xfail_strict=true" in command
            report = next(
                argument for argument in command if argument.startswith("--junitxml=")
            )
            assert Path(report.removeprefix("--junitxml=")).parent == reports


@pytest.mark.parametrize(
    "unsafe", ("inside-source", "not-private", "symlink", "existing")
)
def test_unsafe_report_destination_refuses_before_project_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = source / "reports" if unsafe == "inside-source" else tmp_path / "reports"
    if unsafe == "not-private":
        reports.mkdir(mode=0o755)
        reports.chmod(0o755)
    if unsafe == "symlink":
        original = tmp_path / "original"
        original.mkdir(mode=0o700)
        reports.symlink_to(original, target_is_directory=True)
    if unsafe == "existing":
        reports.mkdir(mode=0o700)
        (reports / "iris.junit.xml").write_text("previous native evidence")
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda **kw: pytest.fail("unsafe report started setup")
    )
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *a, **kw: pytest.fail("work started"),
    )
    with pytest.raises((OSError, RuntimeError, ValueError)):
        python_project_checks.run_project_tests(source, report_directory=reports)


@pytest.mark.parametrize("quiescent", (False, True))
def test_daily_timeout_continues_only_after_original_process_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiescent: bool,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(scratch))
    timeout = CommandTimeoutExpired(
        "uv",
        600,
        supervisor=CommandSupervisorReceipt(
            final_process_group_quiescent=quiescent,
        ),
    )
    calls = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        calls.append(arguments)
        if len(calls) == 1:
            raise timeout
        return CommandResult(0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", run)
    with pytest.raises(CommandTimeoutExpired) as raised:
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
    assert raised.value is timeout
    assert len(calls) == (3 if quiescent else 1)
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["projects"][0]["phase"] == "SYNC_TIMEOUT"
    assert summary["projects"][1]["phase"] == ("COMPLETE" if quiescent else "UNSTARTED")
    assert summary["cleanup"] == ("VERIFIED" if quiescent else "INCOMPLETE")
    if scratch.exists():
        shutil.rmtree(scratch)


def test_daily_user_cancel_preserves_uncovered_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    calls = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        calls.append(arguments)
        raise CommandCancelledError(
            "cancelled",
            supervisor=CommandSupervisorReceipt(
                final_process_group_quiescent=True,
            ),
        )

    monkeypatch.setattr(python_project_checks, "run_command", run)
    with pytest.raises(CommandCancelledError):
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
    assert len(calls) == 1
    summary = json.loads((reports / "summary.json").read_text())
    assert [project["phase"] for project in summary["projects"]] == [
        "CANCELLED",
        "UNSTARTED",
    ]


@pytest.mark.parametrize("native_exit,expected_exit", ((0, 1), (7, 7)))
def test_report_failure_never_rewrites_original_nonzero_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_exit: int,
    expected_exit: int,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *a, **kw: CommandResult(native_exit, "", ""),
    )

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(python_project_checks, "publish_text_file", fail)
    assert (
        python_project_checks.run_project_tests(
            source, report_directory=tmp_path / "reports"
        )
        == expected_exit
    )


@pytest.mark.process
@pytest.mark.skipif(shutil.which("uv") is None, reason="native uv required")
def test_native_tiny_projects_retain_failed_and_independent_passing_junit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    for name, relative, _tests in python_project_checks._PROJECTS:
        project = source / relative
        (project / "tests").mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            f'[project]\nname="tiny-{name}"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
            f'[dependency-groups]\ndev=["pytest=={pytest.__version__}"]\n'
        )
        # Installed pytest does not guarantee uv's offline resolution metadata.
        # Author the fixture lock natively; project execution below remains frozen.
        locked = run_command(
            "uv",
            "lock",
            "--project",
            str(project),
            timeout=30,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=8192,
        )
        assert locked.returncode == 0, locked.stderr
        if name == "iris":
            (project / "tests/test_failure.py").write_text(
                "import pytest\n"
                "def test_failure(): assert False\n"
                "@pytest.mark.airflow\ndef test_optional_airflow(): assert False\n"
                "@pytest.mark.daily\ndef test_daily(): assert False\n"
                "@pytest.mark.xfail(reason='expected native failure')\n"
                "def test_expected_failure(): assert False\n"
                "@pytest.mark.xfail(reason='unexpected native pass')\n"
                "def test_unexpected_pass(): assert True\n"
            )
        else:
            for test in ("test_operations", "test_cli"):
                (project / f"tests/{test}.py").write_text(
                    "def test_pass(): assert 1 + 1 == 2\n"
                )
    reports = tmp_path / "reports"
    assert (
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
        == 1
    )
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["cleanup"] == "VERIFIED"
    assert [project["pytest_exit_code"] for project in summary["projects"]] == [1, 0]
    for project, expected_count, expected_failure in zip(
        summary["projects"],
        (3, 2),
        (2, 0),
        strict=True,
    ):
        native = ET.parse(reports / project["junit_file"])
        assert len(native.findall(".//testcase")) == expected_count
        assert len(native.findall(".//failure")) == expected_failure
        if project["name"] == "iris":
            skipped = native.find(".//skipped")
            assert skipped is not None and skipped.get("type") == "pytest.xfail"
            assert any(
                failure.get("message", "").startswith("[XPASS(strict)]")
                for failure in native.findall(".//failure")
            )


@pytest.fixture(scope="module")
def native_project_context(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from oh_goon.repository_tooling.release.package_slim import (
        canonical_source_identity,
    )

    root = tmp_path_factory.mktemp("native-project-source")
    relative = "templates/goons/general/orders-taskflow"
    project = root / relative
    shutil.copytree(
        python_project_checks.ROOT / relative,
        project,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    (project / "tests/test_context.py").write_text(
        "import importlib.util\nimport pytest\nfrom orders_pipeline import __name__ as package\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_native(value):\n"
        "    assert package == 'orders_pipeline' and value > 0\n"
        "    assert importlib.util.find_spec('oh_goon') is None\n"
        "def test_skip(): pytest.skip('native skip')\n"
        "@pytest.mark.xfail(reason='native expected failure')\n"
        "def test_xfail(): assert False\n"
        "def test_failure(): assert False\n"
        "@pytest.mark.airflow\ndef test_optional_airflow(): assert False\n"
        "@pytest.mark.daily\ndef test_daily(): assert False\n"
    )
    candidate_collector = (
        root / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
    )
    candidate_collector.parent.mkdir(parents=True)
    shutil.copyfile(
        Path(python_project_checks.__file__).with_name("pytest_member_selection.py"),
        candidate_collector,
    )
    developer = project / ".venv/developer"
    developer.parent.mkdir()
    developer.write_text("caller-owned")
    source = canonical_source_identity(root)
    target = relative + "/tests/test_context.py"
    admission = python_project_checks.collect_project_pytest(
        root, [target + "::test_native"], marker=None, source_identity=source
    )
    from oh_goon.repository_tooling import pytest_member_selection

    raw: dict[str, Any] = {}
    original = pytest_member_selection.read_native_collection

    def observe(path: Path) -> dict[str, Any]:
        packet = original(path)
        if "phases" in packet:
            raw.update(copy.deepcopy(packet))
        return packet

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pytest_member_selection, "read_native_collection", observe)
        outcomes = python_project_checks.execute_project_pytest(
            root, admission, source_identity=source
        )
    return dict(
        root=root,
        project=relative,
        target=target,
        source=source,
        admission=admission,
        outcomes=outcomes,
        raw=raw,
        candidate_collector=candidate_collector,
        developer=developer,
    )


@pytest.mark.process
def test_native_project_selected_cases_use_frozen_project_not_backend(
    native_project_context: dict[str, Any],
) -> None:
    case = native_project_context
    files, members = python_project_checks.require_project_pytest_outcomes(
        case["root"],
        case["admission"],
        case["outcomes"],
        source_identity=case["source"],
    )
    assert files == {case["target"]}
    assert members == {
        case["target"] + "::test_native[1]",
        case["target"] + "::test_native[2]",
    }
    assert case["developer"].read_text() == "caller-owned"
    for row in case["admission"] + case["outcomes"]:
        assert not Path(row["environment"]).exists()
        assert row["cleanup"] == "VERIFIED"
    native = case["outcomes"][0]["native_report"]["membership_receipt"]
    assert set(native["selected"]) == members


@pytest.mark.process
@pytest.mark.parametrize("member", ["test_skip", "test_xfail", "test_failure"])
def test_native_project_nonpassing_members_never_qualify(
    native_project_context: dict[str, Any], member: str
) -> None:
    case = native_project_context
    admission = python_project_checks.collect_project_pytest(
        case["root"],
        [case["target"] + "::" + member],
        marker=None,
        source_identity=case["source"],
    )
    outcomes = python_project_checks.execute_project_pytest(
        case["root"], admission, source_identity=case["source"]
    )
    assert outcomes[0]["returncode"] != 0
    with pytest.raises(ValueError):
        python_project_checks.require_project_pytest_outcomes(
            case["root"], admission, outcomes, source_identity=case["source"]
        )
    assert not Path(outcomes[0]["environment"]).exists()


@pytest.mark.parametrize(
    "alteration",
    [
        "missing",
        "extra",
        "duplicate",
        "project",
        "context",
        "environment",
        "cleanup",
        "process",
        "status",
        "selected",
        "request",
    ],
)
def test_native_project_results_reject_changed_authority_or_membership(
    native_project_context: dict[str, Any], alteration: str
) -> None:
    case = native_project_context
    admissions, outcomes = (
        copy.deepcopy(case["admission"]),
        copy.deepcopy(case["outcomes"]),
    )
    # Every mutation starts from the same real, independently validated execution.
    python_project_checks.require_project_pytest_outcomes(
        case["root"], admissions, outcomes, source_identity=case["source"]
    )
    if alteration == "missing":
        outcomes.clear()
    elif alteration == "extra":
        outcomes.append(copy.deepcopy(outcomes[0]))
    elif alteration == "duplicate":
        admissions.append(copy.deepcopy(admissions[0]))
    elif alteration == "project":
        outcomes[0]["project"] = "outside"
    elif alteration == "context":
        outcomes[0]["context"]["uv_lock_sha256"] = "0" * 64
    elif alteration == "environment":
        outcomes[0]["runtime"]["prefix"] = str(case["root"])
    elif alteration == "cleanup":
        outcomes[0]["cleanup"] = "INCOMPLETE"
    elif alteration == "process":
        outcomes[0]["native_execution"][-1]["process_group_quiescent"] = False
    elif alteration == "status":
        outcomes[0]["native_report"]["status"] = "FAIL"
    elif alteration == "selected":
        outcomes[0]["native_report"]["membership_receipt"]["selected"].pop()
    else:
        admissions[0]["request"]["argv"].append("--override-ini=addopts=-x")
    with pytest.raises(ValueError):
        python_project_checks.require_project_pytest_outcomes(
            case["root"], admissions, outcomes, source_identity=case["source"]
        )


def test_native_project_census_survives_missing_metadata_and_refuses_unknown(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.release.package_slim import (
        canonical_source_identity,
    )

    project = "templates/goons/ml/iris-train"
    target = project + "/tests/test_missing.py::test_case[value]"
    assert python_project_checks.project_for_pytest_target(tmp_path, target) == project
    assert python_project_checks.configured_project_pytest_targets(
        tmp_path, project
    ) == (project + "/tests",)
    assert (
        python_project_checks.project_for_pytest_target(
            tmp_path, "outside/tests/test_other.py"
        )
        is None
    )
    assert (
        python_project_checks.project_for_pytest_target(tmp_path, "../" + target)
        is None
    )
    assert (
        python_project_checks.project_for_pytest_target(tmp_path, "/" + target) is None
    )
    with pytest.raises(ValueError, match="context"):
        python_project_checks.collect_project_pytest(
            tmp_path,
            [target],
            marker=None,
            source_identity=canonical_source_identity(tmp_path),
        )


@pytest.mark.process
def test_native_project_all_deselected_remains_observation_not_execution(
    native_project_context: dict[str, Any],
) -> None:
    case = native_project_context
    observed = python_project_checks.collect_project_pytest(
        case["root"],
        [case["target"] + "::test_native"],
        marker="native_absent_marker",
        source_identity=case["source"],
    )
    assert observed[0]["collection"]["selected"] == []
    assert len(observed[0]["collection"]["deselected"]) == 2
    with pytest.raises(ValueError, match="no exact selected"):
        python_project_checks.execute_project_pytest(
            case["root"], observed, source_identity=case["source"]
        )


@pytest.mark.parametrize(
    "alteration", ["setup", "call", "teardown", "duplicate-phase", "not-started"]
)
def test_project_transport_cannot_qualify_incomplete_native_phase_observations(
    native_project_context: dict[str, Any], tmp_path: Path, alteration: str
) -> None:
    from oh_goon.repository_tooling.verification_gate_control import (
        require_native_pytest_pass,
    )

    case = native_project_context
    raw = copy.deepcopy(case["raw"])
    members = tuple(
        row["selector"] for row in case["admission"][0]["collection"]["selected"]
    )
    baseline = python_project_checks._project_native_report(
        raw,
        members,
        tmp_path / "baseline",
        source_identity=case["source"],
        returncode=0,
    )
    require_native_pytest_pass(baseline, expected_source=case["source"])
    if alteration in {"setup", "call", "teardown"}:
        raw["phases"] = [row for row in raw["phases"] if row["when"] != alteration]
    elif alteration == "duplicate-phase":
        raw["phases"].append(copy.deepcopy(raw["phases"][0]))
    else:
        raw["started"].pop()
    with pytest.raises(ValueError):
        broken = python_project_checks._project_native_report(
            raw,
            members,
            tmp_path / "broken",
            source_identity=case["source"],
            returncode=0,
        )
        require_native_pytest_pass(broken, expected_source=case["source"])


def test_project_cleanup_failure_preserves_original_preparation_error(
    native_project_context: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = native_project_context
    scratch = tmp_path / "owned"
    scratch.mkdir()
    original_rmtree = shutil.rmtree

    def failed(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("original native preparation failure")

    def cleanup(path: Path) -> None:
        raise PermissionError("owned cleanup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(scratch))
        patch.setattr(python_project_checks, "run_command", failed)
        patch.setattr(shutil, "rmtree", cleanup)
        with pytest.raises(ValueError, match="original native preparation") as raised:
            python_project_checks.collect_project_pytest(
                case["root"],
                [case["target"]],
                marker=None,
                source_identity=case["source"],
            )
        assert any("cleanup failed" in note for note in raised.value.__notes__)
    assert scratch.exists()
    original_rmtree(scratch)


@pytest.mark.parametrize("changed", [None, "foreign-path", "wrong-bytes"])
def test_project_collector_identity_is_portable_only_between_trusted_and_candidate_source(
    native_project_context: dict[str, Any],
    tmp_path: Path,
    changed: str | None,
) -> None:
    case = native_project_context
    admissions, outcomes = (
        copy.deepcopy(case["admission"]),
        copy.deepcopy(case["outcomes"]),
    )
    collector = case["candidate_collector"]
    if changed == "foreign-path":
        collector = tmp_path / "foreign.py"
        shutil.copyfile(case["candidate_collector"], collector)
    for row in admissions + outcomes:
        previous = row["runtime"]["collector"]
        row["runtime"]["collector"] = str(collector)
        if "request" in row:
            for field in ("argv", "command_prefix"):
                row["request"][field] = [
                    str(collector) if part == previous else part
                    for part in row["request"][field]
                ]
        for native in row["native_execution"]:
            native["argv"] = [
                str(collector) if part == previous else part for part in native["argv"]
            ]
    if changed == "wrong-bytes":
        wrong = (
            tmp_path
            / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py"
        )
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(
            case["candidate_collector"].read_bytes() + b"\n# changed hook bytes\n"
        )
        with pytest.raises(ValueError, match="collector bytes"):
            python_project_checks._validated_project_collector(
                tmp_path, admissions[0]["context"], str(wrong)
            )
        return
    if changed:
        with pytest.raises(ValueError, match="collector bytes"):
            python_project_checks.require_project_pytest_outcomes(
                case["root"], admissions, outcomes, source_identity=case["source"]
            )
    else:
        files, members = python_project_checks.require_project_pytest_outcomes(
            case["root"], admissions, outcomes, source_identity=case["source"]
        )
        assert files == {case["target"]} and len(members) == 2


@pytest.mark.process
def test_project_additional_marker_preserves_native_pytest_config_and_cadence(
    native_project_context: dict[str, Any],
) -> None:
    case = native_project_context
    admission = python_project_checks.collect_project_pytest(
        case["root"],
        [case["target"]],
        marker="not daily and not certification and not slow",
        source_identity=case["source"],
    )
    packet = admission[0]
    excluded = {row["selector"] for row in packet["collection"]["deselected"]}
    assert excluded == {
        case["target"] + "::test_optional_airflow",
        case["target"] + "::test_daily",
    }
    selected = {row["selector"] for row in packet["collection"]["selected"]}
    assert case["target"] + "::test_native[1]" in selected
    assert (
        packet["runtime"]["marker_expression"]
        == "(not airflow) and (not daily and not certification and not slow)"
    )
    assert packet["request"]["marker"] is None
    assert (
        packet["request"]["additional_marker"]
        == "not daily and not certification and not slow"
    )


@pytest.mark.process
def test_changed_prepared_interpreter_refuses_before_selected_tests(
    native_project_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.file_digest import sha256_file

    case = native_project_context
    original_session = python_project_checks._project_session
    original_command = python_project_checks.run_command
    executed: list[tuple[str, ...]] = []
    environments: list[Path] = []

    @contextmanager
    def changed_interpreter(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        with original_session(*args, **kwargs) as session:
            environment = session["environment"]
            environments.append(environment)
            selected = environment / "bin/python"
            changed = session["scratch"] / "changed-python"
            shutil.copy2(selected.resolve(strict=True), changed)
            # The executable still runs native Python, but its selected bytes
            # no longer match the interpreter admitted during collection.
            with changed.open("ab") as stream:
                stream.write(b"\nchanged native interpreter identity\n")
            selected.unlink()
            selected.symlink_to(changed)
            assert (
                sha256_file(changed)
                != case["admission"][0]["runtime_identity"]["interpreter_sha256"]
            )
            yield session

    def observe_command(*args: str, **kwargs: Any) -> CommandResult:
        if args[0] != "uv":
            executed.append(args)
        return original_command(*args, **kwargs)

    monkeypatch.setattr(python_project_checks, "_project_session", changed_interpreter)
    monkeypatch.setattr(python_project_checks, "run_command", observe_command)
    with pytest.raises(ValueError, match="interpreter changed before execution"):
        python_project_checks.execute_project_pytest(
            case["root"], case["admission"], source_identity=case["source"]
        )
    assert not executed
    assert environments and all(not path.exists() for path in environments)
