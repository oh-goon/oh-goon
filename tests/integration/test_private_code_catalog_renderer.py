"""Native process regressions for the checked-in independent catalog renderer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("valid", id="exact-descriptor"),
        pytest.param("unknown-field", id="unknown-field-refused"),
        pytest.param("invalid-owner", id="explicit-group-owner-required"),
        pytest.param("invalid-language", id="invalid-language-refused"),
        pytest.param("existing-output", id="existing-output-preserved"),
        pytest.param("at-budget", id="exact-size-budget-accepted"),
        pytest.param("over-budget", id="one-byte-over-budget-refused"),
        pytest.param(
            "memory-budget",
            id="large-input-refused-with-bounded-memory",
            marks=pytest.mark.skipif(
                sys.platform != "linux", reason="requires native RLIMIT_AS"
            ),
        ),
    ],
)
def test_private_code_catalog_renderer(tmp_path: Path, case: str) -> None:
    workspace = tmp_path / "renderer"
    shutil.copytree(ROOT / "examples/private-code-catalog/generator", workspace)
    source = workspace / "catalog-input.json"
    output = workspace / "catalog-info.yaml"
    expected = (
        ROOT / "examples/private-code-catalog/expected/catalog-info.yaml"
    ).read_bytes()

    if case in {"unknown-field", "invalid-owner", "invalid-language"}:
        data = json.loads(source.read_text(encoding="utf-8"))
        if case == "unknown-field":
            data["token"] = "synthetic-secret-must-never-publish"
        elif case == "invalid-owner":
            data["owner"] = "demo@example.invalid"
        else:
            data["languages"] = ["python--unsafe"]
        source.write_text(json.dumps(data), encoding="utf-8")
    elif case == "existing-output":
        output.write_text("existing candidate", encoding="utf-8")
    elif case in {"at-budget", "over-budget"}:
        raw = source.read_bytes()
        size = 16_384 if case == "at-budget" else 16_385
        source.write_bytes(raw + b" " * (size - len(raw)))
    elif case == "memory-budget":
        # Sparse input avoids a large allocation in the parent. The child must
        # refuse it before reading more bytes than its process budget permits.
        with source.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024)

    command = [sys.executable, "generate_catalog.py"]
    if case == "memory-budget":
        command = [
            sys.executable,
            "-c",
            "import resource, runpy; "
            "resource.setrlimit(resource.RLIMIT_AS, (67108864, 67108864)); "
            "runpy.run_path('generate_catalog.py', run_name='__main__')",
        ]
    result = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if case in {"valid", "at-budget"}:
        assert result.returncode == 0, result.stderr
        assert output.read_bytes() == expected
        assert b"Demo Contributor" not in expected
        assert b"src/service.py" not in expected
    else:
        assert result.returncode == 2, result.stderr
        if case == "existing-output":
            assert output.read_text(encoding="utf-8") == "existing candidate"
        else:
            assert not output.exists()
        if case == "unknown-field":
            assert "synthetic-secret" not in result.stderr
        if case in {"over-budget", "memory-budget"}:
            assert "approved metadata exceeds size budget" in result.stderr
