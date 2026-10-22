from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from oh_goon.engine.command import CommandResult
from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt


def native_invocation_executable(directory: Path) -> Path:
    """Build a native utility that requires its kernel and invocation names to agree."""
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("native C compiler is unavailable")
    source = directory / "native-invocation.c"
    source.write_text("""#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    char path[4096];
    ssize_t n = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (n < 0) return 70;
    path[n] = 0;
    char *actual = strrchr(path, '/');
    char *invoked = strrchr(argv[0], '/');
    if (strcmp(actual ? actual + 1 : path, invoked ? invoked + 1 : argv[0])) {
        fprintf(stderr, "native invocation name changed: %s\\n", path);
        return 71;
    }
    puts(argc > 1 ? argv[1] : "native invocation retained");
    return 0;
}
""")
    executable = directory / "native-invocation"
    subprocess.run([compiler, str(source), "-o", str(executable)], check=True, capture_output=True)
    return executable


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def exact_runtime_authority_receipt(
    *, executable_path: str = "/test/bin/authorized-command"
) -> RuntimeAuthorityReceipt:
    """Test-only exact execution receipt for fakes that stand in for a launched process."""

    return RuntimeAuthorityReceipt(
        executable_path=executable_path,
        executable_identity=_sha("1"),
        executable_byte_count=1,
        path_authority_identity=_sha("2"),
        environment_identity=_sha("3"),
        execution_materialization_identity=_sha("4"),
    )


def authorized_command_result(
    argv: Sequence[str] = (),
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 0,
) -> CommandResult:
    """Return a realistic process result when a test fakes successful execution."""

    return CommandResult(
        physical_execution_id="physical-test-authorized",
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        runtime_authority=exact_runtime_authority_receipt(),
    )
