from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.repository_tooling.pytest_member_selection import collect_native_pytest

pytestmark = pytest.mark.process


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
