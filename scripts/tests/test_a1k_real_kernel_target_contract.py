"""A1k: native kernel custody has an explicit fail-before-test owner.

These static and subprocess checks are tooling regressions, not independent
Linux/WSL deployment attestation. Root-only native tests are in the process
lane and must not be called successful if an operator cannot run them.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_kernel_custody_is_explicit_and_keeps_m209_independent():
    makefile = (ROOT / "Makefile").read_text()
    assert ".PHONY: a1-native-kernel-custody-test " in makefile
    target = makefile.split("\na1-native-kernel-custody-test:\n", 1)[1].split(
        "\n\n", 1
    )[0]
    assert '"$$(id -u)" = 0' in target
    assert '"$$(uname -m)" = x86_64' in target
    assert "command -v setpriv" in target
    assert "test -x backend/.venv/bin/oh-goon-qualification-exec" in target
    assert "scripts/tests/test_a1h_real_linux_process_custody.py" in target
    # No extra native qualifier step is introduced: M209 retains the original
    # independent command proof contract and never implies A1k success.
    assert (
        "a1-native-kernel-custody-test"
        not in (ROOT / "scripts/qualify_native_wsl.sh").read_text()
    )


def test_unprivileged_host_blocks_before_invoking_native_test(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text("#!/bin/sh\necho 12345\n")
    (fake_bin / "id").chmod(0o755)
    result = subprocess.run(
        ["make", "--no-print-directory", "a1-native-kernel-custody-test"],
        cwd=ROOT,
        env={**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert "A1 KERNEL CUSTODY BLOCKED" in result.stderr
    assert "A1 NATIVE LINUX KERNEL CUSTODY" not in result.stdout
