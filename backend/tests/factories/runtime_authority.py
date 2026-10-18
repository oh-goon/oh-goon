from __future__ import annotations

from collections.abc import Sequence

from oh_goon.engine.command import CommandResult
from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt


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
