"""B20: observer ring cannot pass by omitting physical-observation files."""

import sys

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.b11_ring_supervisor import supervise


def _junit_writer(test_class: str) -> list[str]:
    code = """from pathlib import Path
import sys
path=Path(sys.argv[1])
classname=sys.argv[2]
path.write_text('<testsuite tests="1" errors="0" failures="0" skipped="0">'
 '<testcase classname="unit.repository_tooling.'+classname+'" name="test_native" />'
 '</testsuite>')
"""
    return [sys.executable, "-c", code]


def test_green_junit_omitting_required_observer_fails(tmp_path):
    result = supervise(
        name="PHYSICAL_OBSERVER_01",
        command=[
            *_junit_writer("test_b13_local_physical_preflight"),
            str(tmp_path / "PHYSICAL_OBSERVER_01.xml"),
            "test_b13_local_physical_preflight",
        ],
        directory=tmp_path,
        timeout_seconds=10,
        required_modules=["test_b13_local_physical_preflight", "test_b19_visible_mount_fd_holders"],
    )
    assert result["process_exit_code"] == 0
    assert result["junit"]["valid"] is True
    assert result["verdict"] == "REQUIRED_TEST_MODULES_MISSING"
    assert result["accepted"] is False
    assert result["junit"]["missing_required_modules"] == ["test_b19_visible_mount_fd_holders"]


def test_exact_required_observer_module_accepts_clean_process(tmp_path):
    module = "test_b19_visible_mount_fd_holders"
    result = supervise(
        name="PHYSICAL_OBSERVER_01",
        command=[*_junit_writer(module), str(tmp_path / "PHYSICAL_OBSERVER_01.xml"), module],
        directory=tmp_path,
        timeout_seconds=10,
        required_modules=[module],
    )
    assert result["accepted"] is True
    assert result["junit"]["missing_required_modules"] == []
    assert not result["lingering_processes_after_teardown"]


def test_b20_selection_is_explicit_and_source_clean_first():
    script = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    make = (ROOT / "Makefile").read_text()
    assert (
        "if [[ -f .checkpoint/B20_SOURCE.sha256 ]]; then embedded='.checkpoint/B20_SOURCE.sha256'; fi"
        in script
    )
    assert 'run_ring "PHYSICAL_OBSERVER${suffix}"' in script
    modules = [
        "test_b13_local_physical_preflight",
        "test_b15_cgroup_subtree_evidence",
        "test_b16_tracked_namespace_evidence",
        "test_b17_namespace_fd_holders",
        "test_b18_foreign_mount_targets",
        "test_b19_visible_mount_fd_holders",
        "test_b20_linux_proc_observer_witness",
    ]
    for module in modules:
        assert script.count(module) >= 2  # required JUnit identity AND exact test file selection
    assert '"${required_modules[@]}"' in script
    recipe = make.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")
    assert script.index("run_ring CORE") < script.index('run_ring "PHYSICAL_OBSERVER${suffix}"')
    assert script.index('run_ring "B7_RACES${suffix}"') < script.index(
        'run_ring "PHYSICAL_OBSERVER${suffix}"'
    )


def test_non_b20_candidates_do_not_run_new_ring():
    script = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "if [[ -f .checkpoint/B20_SOURCE.sha256 ]]; then" in script
    assert "PHYSICAL_OBSERVER${suffix}" in script
