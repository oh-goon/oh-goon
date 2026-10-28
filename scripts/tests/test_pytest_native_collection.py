from __future__ import annotations

import subprocess
import sys
import json
import os
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling.pytest_member_selection import collect_native_pytest
from oh_goon.repository_tooling.pytest_process_environment import (
    resolve_pytest_invocation,
)

pytestmark = pytest.mark.process


def test_resume_tracks_unqualified_native_python_import_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    first_path = tmp_path / "first-imports"
    second_path = tmp_path / "second-imports"
    for path, allowed in ((first_path, True), (second_path, False)):
        path.mkdir()
        (path / "selected_state.py").write_text(f"ALLOWED = {allowed!r}\n")
    trace = tmp_path / "trace"
    case = tmp_path / "test_import_state.py"
    case.write_text(
        "from selected_state import ALLOWED\nfrom pathlib import Path\n"
        "def test_imported_state():\n"
        f"    with Path({str(trace)!r}).open('a') as stream: stream.write('run\\n')\n"
        "    assert ALLOWED\n"
    )
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        str(case),
        "--granularity",
        "tests",
        "--marker",
        "",
        "--workers",
        "1",
        "--batch-size",
        "1",
        "--batch-timeout",
        "20",
        "--resume-state-root",
        str(tmp_path / "receipts"),
    ]

    def run(
        path: Path, number: int, *, resume: bool = True
    ) -> tuple[int, dict[str, Any]]:
        report = tmp_path / f"report-{number}.json"
        result = subprocess.run(
            [*command, *(["--resume"] if resume else []), "--report-path", str(report)],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(path)},
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert report.is_file(), result.stdout + result.stderr
        assert (
            str(first_path) not in report.read_text()
            and str(second_path) not in report.read_text()
        )
        return result.returncode, json.loads(report.read_text())

    baseline_code, baseline = run(first_path, 1)
    same_code, same = run(first_path, 2)
    assert baseline_code == same_code == 0
    assert baseline["resume_suite_identity"] == same["resume_suite_identity"]
    assert same["membership_receipt"]["resumed"] == [f"{case}::test_imported_state"]
    assert trace.read_text().splitlines() == ["run"]
    changed_code, changed = run(second_path, 3)
    fresh_code, fresh = run(second_path, 4, resume=False)
    assert (
        baseline["source_identity"]
        == changed["source_identity"]
        == fresh["source_identity"]
    )
    assert baseline["test_plan_identity"] == changed["test_plan_identity"]
    assert baseline["toolchain_identity"] == changed["toolchain_identity"]
    assert changed_code == fresh_code == 1
    assert changed["resume_suite_identity"] != baseline["resume_suite_identity"]
    assert changed["membership_receipt"]["resumed"] == []
    assert changed["membership_receipt"]["failed"] == [f"{case}::test_imported_state"]
    assert trace.read_text().splitlines() == ["run", "run", "run"]


@pytest.mark.parametrize("selector", ("XDG_STATE_HOME", "CUSTOM_TEST_INPUT"))
def test_resume_binds_native_and_declared_environment_without_budget_or_value_leaks(
    tmp_path: Path, selector: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    allowed = str(tmp_path / "allowed")
    denied = str(tmp_path / "denied")
    trace = tmp_path / "trace"
    case = tmp_path / "test_semantic_environment.py"
    case.write_text(
        "import os\nfrom pathlib import Path\n"
        "def test_value():\n"
        "    with Path(os.environ['RESUME_PROBE_TRACE']).open('a') as stream: stream.write('run\\n')\n"
        f"    assert os.environ[{selector!r}] == {allowed!r}\n"
    )
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        str(case),
        "--qualified",
        "--granularity",
        "tests",
        "--marker",
        "",
        "--resume",
        "--resume-state-root",
        str(tmp_path / "receipts"),
        "--resume-context",
        "native-owner=private-context-canary",
    ]
    if selector == "CUSTOM_TEST_INPUT":
        command += ["--resume-env", selector]

    def run(
        value: str, number: int, regroup: bool = False
    ) -> tuple[int, dict[str, Any]]:
        environment = dict(os.environ)
        environment.update({selector: value, "RESUME_PROBE_TRACE": str(trace)})
        environment["OH_GOON_TEST_CPUS"] = "4" if regroup else "2"
        report = tmp_path / f"report-{number}.json"
        result = subprocess.run(
            [
                *command,
                "--workers",
                "2" if regroup else "1",
                "--batch-size",
                "4" if regroup else "1",
                "--batch-timeout",
                "30" if regroup else "20",
                "--report-path",
                str(report),
            ],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert report.is_file(), result.stdout + result.stderr
        payload = json.loads(report.read_text())
        assert allowed not in report.read_text() and denied not in report.read_text()
        return result.returncode, payload

    first_code, first = run(allowed, 1)
    unchanged_code, unchanged = run(allowed, 2, regroup=True)
    assert first_code == unchanged_code == 0
    assert first["source_identity"] == unchanged["source_identity"]
    assert first["resume_suite_identity"] == unchanged["resume_suite_identity"]
    assert unchanged["membership_receipt"]["resumed"] == [f"{case}::test_value"]
    assert trace.read_text().splitlines() == ["run"]

    changed_code, changed = run(denied, 3)
    assert changed["source_identity"] == first["source_identity"]
    assert changed["test_plan_identity"] == first["test_plan_identity"]
    assert changed["toolchain_identity"] == first["toolchain_identity"]
    assert changed["resume_suite_identity"] != first["resume_suite_identity"]
    assert changed_code != 0
    assert changed["membership_receipt"]["resumed"] == []
    assert changed["membership_receipt"]["failed"] == [f"{case}::test_value"]
    again_code, again = run(denied, 4)
    assert again_code != 0 and again["membership_receipt"]["resumed"] == []
    assert trace.read_text().splitlines() == ["run", "run", "run"]
    manifests = list((tmp_path / "receipts").rglob("context.json"))
    assert manifests
    assert all("private-context-canary" not in path.read_text() for path in manifests)


@pytest.mark.parametrize("granularity", ("files", "tests"))
def test_native_no_resume_declaration_reruns_external_state_without_disabling_other_reuse(
    tmp_path: Path, granularity: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.pytest.ini_options]\naddopts = "--strict-markers"\n')
    external = tmp_path / "external-state"
    external.write_text("allowed")
    trace = tmp_path / "trace"
    case = tmp_path / "test_external_state.py"
    case.write_text(
        "import pytest\nfrom pathlib import Path\n"
        f"TRACE = Path({str(trace)!r})\n"
        "def record(name):\n"
        "    with TRACE.open('a') as stream: stream.write(name+'\\n')\n"
        "@pytest.mark.no_resume\n"
        "def test_external():\n"
        "    record('external')\n"
        f"    assert Path({str(external)!r}).read_text() == 'allowed'\n"
        "def test_stable(): record('stable')\n"
    )
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        str(case),
        "--qualified",
        "--pytest-config",
        str(config),
        "--granularity",
        granularity,
        "--marker",
        "",
        "--workers",
        "1",
        "--batch-size",
        "1",
        "--batch-timeout",
        "20",
        "--resume",
        "--resume-state-root",
        str(tmp_path / "receipts"),
    ]
    observations = []
    for number in range(3):
        if number == 2:
            external.write_text("changed")
        report = tmp_path / f"report-{number}.json"
        result = subprocess.run(
            [*command, "--report-path", str(report)],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert report.is_file(), result.stdout + result.stderr
        payload = json.loads(report.read_text())
        observations.append(payload)
        assert (result.returncode == 0) is (number != 2)
        expected_reuse = (
            [f"{case}::test_stable"] if number and granularity == "tests" else []
        )
        assert payload["membership_receipt"]["resumed"] == expected_reuse
        assert not payload["membership_receipt"]["missing"]
        assert not payload["membership_receipt"]["duplicates"]
        assert not payload["membership_receipt"]["extras"]
    assert len({item["source_identity"] for item in observations}) == 1
    assert len({item["resume_suite_identity"] for item in observations}) == 1
    lines = trace.read_text().splitlines()
    assert lines.count("external") == 3
    assert lines.count("stable") == (3 if granularity == "files" else 1)


def test_qualified_empty_paths_match_native_defaults_flags_plugins_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    config = tmp_path / "pyproject.toml"
    config.write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["chosen"]\nmarkers = ["wanted"]\n',
        encoding="utf-8",
    )
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    (chosen / "test_choice.py").write_text(
        "from pathlib import Path\n"
        "def test_selected(): Path('executed').write_text('yes')\n"
        "def test_other(): pass\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_unselected.py").write_text(
        "raise AssertionError('expanded scope')\n"
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_configure(config):\n"
        "    assert config.pluginmanager.hasplugin('pytest_cov'), 'native autoload missing'\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        if item.name == 'test_selected': item.add_marker(pytest.mark.wanted)\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    invocation = resolve_pytest_invocation(
        qualified=True,
        pytest_config=Path("pyproject.toml"),
        pytest_args=("-k", "selected"),
    )
    native_packet = tmp_path / "native.json"
    environment = dict(invocation.environment)
    environment["OH_GOON_PYTEST_COLLECTION_OUTPUT"] = str(native_packet)
    native = subprocess.run(
        [
            *invocation.command_prefix,
            "--collect-only",
            "-qq",
            "-c",
            str(invocation.config_file),
            "-p",
            "oh_goon.repository_tooling.pytest_member_selection",
            *invocation.pytest_args,
            "-m",
            "wanted",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert native.returncode == 0, native.stdout + native.stderr
    assert collect_native_pytest(
        (), marker="wanted", invocation=invocation
    ).payload() == json.loads(native_packet.read_text(encoding="utf-8"))
    report = tmp_path / "report.json"
    execution = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            "--qualified",
            "--pytest-config",
            "pyproject.toml",
            "--pytest-arg=-k",
            "--pytest-arg=selected",
            "--marker",
            "wanted",
            "--workers",
            "1",
            "--report-path",
            str(report),
            "--resume-state-root",
            str(tmp_path / "receipts"),
        ],
        cwd=tmp_path,
        env=dict(invocation.environment),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert execution.returncode == 0, execution.stdout + execution.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["membership_receipt"]["selected"] == ["chosen/test_choice.py"]
    assert payload["membership_receipt"]["terminal_success"] is True
    assert (tmp_path / "executed").read_text() == "yes"
    assert payload["diagnostics"]["qualified"] is True


def test_mixed_explicit_selectors_keep_exact_scope_and_show_native_per_batch_durations(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    first = tmp_path / "test_first.py"
    first.write_text(
        "def test_selected(): pass\n"
        "def test_unselected(): raise AssertionError('expanded file selector')\n",
        encoding="utf-8",
    )
    second = tmp_path / "test_second.py"
    second.write_text("def test_one(): pass\ndef test_two(): pass\n", encoding="utf-8")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            f"{first}::test_selected",
            str(second),
            "--granularity",
            "files",
            "--pytest-arg=--durations=40",
            "--pytest-arg=--durations-min=0.0",
            "--show-output",
            "--marker",
            "",
            "--workers",
            "1",
            "--report-path",
            str(report),
            "--resume-state-root",
            str(tmp_path / "receipts"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["granularity"] == "tests"
    assert payload["membership_receipt"]["selected"] == [
        f"{first}::test_selected",
        f"{second}::test_one",
        f"{second}::test_two",
    ]
    assert payload["membership_receipt"]["terminal_success"] is True
    assert "NATIVE OUTPUT (per batch)" in result.stdout
    assert "slowest 40 durations" in result.stdout


@pytest.mark.parametrize("inside_ignored_root", [False, True])
def test_native_options_and_config_bytes_bind_correctness_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside_ignored_root: bool
) -> None:
    from oh_goon.repository_tooling import pytest_process_environment

    config = tmp_path / "pyproject.toml"
    if inside_ignored_root:
        monkeypatch.setattr(pytest_process_environment, "ROOT", tmp_path)
        config = tmp_path / ".venv/pyproject.toml"
        config.parent.mkdir()
    config.write_text('[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    first = resolve_pytest_invocation(pytest_config=config, pytest_args=("-k", "one"))
    second = resolve_pytest_invocation(pytest_config=config, pytest_args=("-k", "two"))
    identity = first.correctness_context()
    assert identity != second.correctness_context()
    config.write_text('[tool.pytest.ini_options]\naddopts = "-v"\n', encoding="utf-8")
    assert identity == first.correctness_context()
    changed = resolve_pytest_invocation(pytest_config=config, pytest_args=("-k", "one"))
    assert identity != changed.correctness_context()


@pytest.mark.parametrize("selected_branch", [None, "test_true"])
def test_native_coverage_union_preserves_membership_when_floor_passes_or_fails(
    tmp_path: Path, selected_branch: str | None
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n'
        '[tool.coverage.run]\nbranch = true\nsource = ["subject"]\n'
        "[tool.coverage.report]\nfail_under = 100\n",
        encoding="utf-8",
    )
    (tmp_path / "subject.py").write_text(
        "def value(flag):\n    if flag:\n        return 1\n    return 2\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_true.py").write_text(
        "from subject import value\ndef test_true(): assert value(True) == 1\n",
        encoding="utf-8",
    )
    (tests / "test_false.py").write_text(
        "from subject import value\ndef test_false(): assert value(False) == 2\n",
        encoding="utf-8",
    )
    report = tmp_path / "batch.json"
    coverage_json = tmp_path / "coverage.json"
    arguments = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        "--qualified",
        "--pytest-config",
        "pyproject.toml",
        "--marker",
        "",
        "--batch-size",
        "1",
        "--workers",
        "2",
        "--batch-timeout",
        "30",
        "--coverage-source",
        "subject",
        "--coverage-data-file",
        str(tmp_path / ".coverage"),
        f"--coverage-report=json:{coverage_json}",
        "--coverage-fail-under",
        "100",
        "--report-path",
        str(report),
        "--resume-state-root",
        str(tmp_path / "receipts"),
    ]
    if selected_branch is not None:
        arguments += ["--pytest-arg=-k", f"--pytest-arg={selected_branch}"]
    result = subprocess.run(
        arguments, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == (0 if selected_branch is None else 1), (
        result.stdout + result.stderr
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == ("PASS" if selected_branch is None else "FAIL")
    membership = payload["membership_receipt"]
    assert len(membership["selected"]) == (2 if selected_branch is None else 1)
    assert membership["passed"] == membership["selected"]
    assert membership["terminal_success"] is True
    assert membership["missing"] == []
    assert membership["duplicates"] == []
    assert membership["extras"] == []
    assert coverage_json.is_file()
    assert (tmp_path / ".coverage").is_file()


@pytest.mark.parametrize(
    ("filtered", "coverage"), [(False, False), (True, False), (True, True)]
)
def test_qualified_empty_native_selection_exits_five_without_pass_or_coverage(
    tmp_path: Path, filtered: bool, coverage: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    if filtered:
        (tests / "test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")
    report = tmp_path / "report.json"
    data = tmp_path / ".coverage"
    arguments = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        "--qualified",
        "--pytest-config",
        "pyproject.toml",
        "--marker",
        "",
        "--report-path",
        str(report),
        "--resume-state-root",
        str(tmp_path / "receipts"),
    ]
    if filtered:
        arguments += ["--pytest-arg=-k", "--pytest-arg=not_present"]
    if coverage:
        arguments += ["--coverage-source=subject", "--coverage-data-file", str(data)]
    result = subprocess.run(
        arguments, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 5, result.stdout + result.stderr
    assert "PYTEST BATCH RESULT: PASS" not in result.stdout
    assert not report.exists()
    assert not data.exists()


def test_native_collection_preserves_classes_parameters_and_overwritten_markers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test_native.py"
    path.write_text(
        "import pytest\npytestmark = pytest.mark.slow\npytestmark = []\n"
        "def test_top(): pass\nclass TestGroup:\n"
        "    @pytest.mark.parametrize('value', [1, 2])\n"
        "    def test_values(self, value): pass\n"
        "@pytest.mark.slow\ndef test_slow(): pass\n"
    )
    selected = collect_native_pytest((path,), marker="not slow")
    assert [row["selector"].split("::", 1)[1] for row in selected.selected] == [
        "test_top",
        "TestGroup::test_values[1]",
        "TestGroup::test_values[2]",
    ]
    assert len(selected.groups("files")) == 1
    assert len(selected.groups("tests")) == 3
    assert selected.deselected[0]["selector"].endswith("::test_slow")
    renamed = path.with_name("test_renamed.py")
    path.rename(renamed)
    assert len(collect_native_pytest((renamed,), marker="not slow").selected) == 3


def test_native_collection_respects_plugins_and_reports_collection_errors(
    tmp_path: Path,
) -> None:
    (tmp_path / "conftest.py").write_text(
        "import pytest\ndef pytest_collection_modifyitems(items):\n"
        "    for item in items:\n        if 'excluded' in item.name: item.add_marker(pytest.mark.slow)\n"
    )
    path = tmp_path / "test_plugin.py"
    path.write_text("def test_run(): pass\ndef test_excluded(): pass\n")
    result = collect_native_pytest((path,), marker="not slow or certification")
    assert len(result.selected) == 1
    assert result.selected[0]["selector"].endswith("::test_run")
    path.write_text("raise RuntimeError('collection failed before work')\n")
    with pytest.raises(ValueError, match="Native pytest collection failed"):
        collect_native_pytest((path,))


def test_batch_rejects_changed_native_membership_before_running_tests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test_changed.py"
    marker = tmp_path / "executed"
    path.write_text(
        f"from pathlib import Path\ndef test_one(): Path({str(marker)!r}).touch()\n"
    )
    (tmp_path / "conftest.py").write_text(
        "def pytest_sessionfinish(session):\n"
        "    if session.config.option.collectonly:\n"
        f"        with open({str(path)!r}, 'a') as stream:\n"
        "            stream.write('def test_added(): pass\\n')\n"
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(path),
            "--workers",
            "1",
            "--batch-timeout",
            "20",
            "--marker",
            "",
            "--resume-state-root",
            str(tmp_path / "receipts"),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "collection changed after batch admission" in result.stdout + result.stderr
    assert not marker.exists()
