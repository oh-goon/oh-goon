"""Native command fixtures for repository-tooling behavior tests."""

from pathlib import Path
import shlex
import shutil

import pytest

from tests.fakes.process_identity import stable_test_process_identity_without_procfs  # noqa: F401


@pytest.fixture
def non_wsl_grep(tmp_path: Path) -> Path:
    """Control only the kernel query so the denial can run on Linux and WSL."""
    executable = shutil.which("grep")
    assert executable is not None
    directory = tmp_path / "non-wsl-bin"
    directory.mkdir()
    command = directory / "grep"
    command.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        '  case "$argument" in /proc/sys/kernel/osrelease|/proc/version) exit 1;; esac\n'
        "done\n"
        f'exec {shlex.quote(executable)} "$@"\n',
        encoding="utf-8",
    )
    command.chmod(0o755)
    return directory
