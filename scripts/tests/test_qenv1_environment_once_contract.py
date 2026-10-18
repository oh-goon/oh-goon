"""QENV1 is permanent architecture policy, not a WSL-specific launcher patch."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from oh_goon.repository_tooling.architecture.qualification_environment_contract import (  # noqa: E402
    check_qualification_environment_contract,
)

WITNESS = ROOT / "scripts/qualify_b12_development_wsl.sh"


def _fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy2(WITNESS, tmp_path / "scripts/qualify_b12_development_wsl.sh")
    shutil.copy2(ROOT / "Makefile", tmp_path / "Makefile")
    return tmp_path


def test_qenv1_is_registered_as_original_project_architecture_check():
    source = (ROOT / "backend/src/oh_goon/repository_tooling/check_architecture.py").read_text()
    assert "check_qualification_environment_contract," in source
    assert not check_qualification_environment_contract(ROOT)


@pytest.mark.parametrize("before,after", [
    ("uv sync --locked --group dev", "uv sync --locked --group dev && uv sync --locked --group dev"),
    ("uv sync --check --locked --offline --group dev", "uv sync --locked --offline --group dev"),
    ('"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor',
     'uv run --locked --group dev python -m oh_goon.repository_tooling.b11_ring_supervisor'),
    ('UV_NO_SYNC=1 UV_OFFLINE=1 PIP_NO_INDEX=1', 'UV_OFFLINE=1 PIP_NO_INDEX=1'),
    ('"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor',
     'pip install -r requirements.txt; "$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor'),
    ('"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor',
     'uv sync --group dev; "$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor'),
    ('"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor',
     'python -m pip install .; "$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor'),
    ('"$qualification_python" -c', 'python -c'),
    ('UV_ENV_VERIFIED.txt', 'FAKE_RECEIPT.txt'),
    ('-m oh_goon.repository_tooling.check_architecture', '-m unknown.architecture'),
])
def test_qenv1_rejects_environment_drift_before_any_ring(tmp_path, before, after):
    repo = _fixture_root(tmp_path)
    path = repo / "scripts/qualify_b12_development_wsl.sh"
    content = path.read_text()
    assert before in content
    path.write_text(content.replace(before, after, 1))
    issues = check_qualification_environment_contract(repo)
    assert issues, (before, after)
    assert any("QENV1" in issue.message for issue in issues)


def test_qenv1_make_frontdoor_cannot_reinstall(tmp_path):
    repo = _fixture_root(tmp_path)
    make = repo / "Makefile"
    make.write_text(make.read_text().replace(
        '\t@bash scripts/qualify_b12_development_wsl.sh',
        '\t@uv sync --locked --group dev\n\t@bash scripts/qualify_b12_development_wsl.sh',
    ))
    assert check_qualification_environment_contract(repo)


def _fragments(shell: str) -> tuple[str, str]:
    host = 'qualify_host_sample() {' + shell.split('qualify_host_sample() {', 1)[1].split(
        '# Canonical qualification environment transaction:', 1
    )[0]
    prep = '# Canonical qualification environment transaction:' + shell.split(
        '# Canonical qualification environment transaction:', 1
    )[1].split('# The ring is supervised', 1)[0]
    ring = 'run_ring() {' + shell.split('run_ring() {', 1)[1].split('\nlast_result=0', 1)[0]
    return host + prep, host + ring


def test_qenv1_simulated_one_prepare_verify_multiple_direct_interpreter_rings(tmp_path):
    """Exercise the real shell branches using fakes, without WSL or a dependency install."""
    script = WITNESS.read_text()
    setup, ring = _fragments(script)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    backend = tmp_path / "backend"
    exe = backend / ".venv/bin/python"
    exe.parent.mkdir(parents=True)
    (backend / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1"\n')
    (backend / "uv.lock").write_text('version=1\n')
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    uv = fakebin / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_UV_CALLS"\nexit 0\n')
    uv.chmod(0o755)
    # Both Python identity and the architecture phase are modeled as a prepared
    # interpreter, while each ring gets its own supervisor receipt.
    exe.write_text('''#!/bin/sh
if [ "$1" = "-c" ]; then echo 'python=3.14'; exit 0; fi
if [ "$2" = "oh_goon.repository_tooling.check_architecture" ]; then echo 'Architecture checks passed'; exit 0; fi
if [ "$2" = "oh_goon.repository_tooling.b11_ring_supervisor" ]; then
    shift 2
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--name" ]; then name="$2"; shift 2; continue; fi
      if [ "$1" = "--evidence" ]; then evidence="$2"; shift 2; continue; fi
      shift
    done
    printf '%s\\n' '{}' > "$evidence/$name.json"
    printf '%s\\n' "$name" >> "$FAKE_RING_CALLS"
    exit 0
fi
exit 99
''')
    exe.chmod(0o755)
    fixture = tmp_path / "simulate.sh"
    fixture.write_text('set -Eeuo pipefail\nroot="$PWD"\nevidence="$1"\n'
                       + setup + '\nlast_result=0\n' + ring
                       + '\nrun_ring CORE example.py\nrun_ring SECOND example.py\n')
    calls = tmp_path / "uv-calls.txt"
    rings = tmp_path / "rings.txt"
    run = subprocess.run(
        ['bash', str(fixture), str(evidence)], cwd=tmp_path,
        env={**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}',
             'FAKE_UV_CALLS': str(calls), 'FAKE_RING_CALLS': str(rings)},
        capture_output=True, text=True, timeout=20,
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert calls.read_text().splitlines() == [
        'sync --locked --group dev', 'sync --check --locked --offline --group dev'
    ]
    assert rings.read_text().splitlines() == ['CORE', 'SECOND']
    receipt = (evidence / 'UV_ENV_VERIFIED.txt').read_text()
    assert 'preparation_count=1' in receipt
    assert 'verification_count=1' in receipt
    assert 'rings_require_project_sync=NO' in receipt
    assert 'UV_ENV_VERIFY=0' in (evidence / 'RING_RESULTS.txt').read_text()


def test_qenv1_environment_check_failure_blocks_all_rings(tmp_path):
    script = WITNESS.read_text()
    setup, _ = _fragments(script)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    backend = tmp_path / 'backend'
    backend.mkdir()
    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir()
    uv = fakebin / 'uv'
    uv.write_text('#!/bin/sh\ncase "$*" in *--check*) exit 18 ;; *) exit 0 ;; esac\n')
    uv.chmod(0o755)
    fixture = tmp_path / 'check-fail.sh'
    fixture.write_text('set -Eeuo pipefail\nroot="$PWD"\nevidence="$1"\n'
                       + setup + '\necho BAD_RING_STARTED\n')
    run = subprocess.run(['bash', str(fixture), str(evidence)], cwd=tmp_path,
                         env={**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}'},
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 1, (run.stdout, run.stderr)
    assert 'BAD_RING_STARTED' not in run.stdout
    assert 'INFRA_BLOCKED=UV_ENV_VERIFY exit=18' in (evidence / 'INFRA_FAILURE.txt').read_text()
    assert not (evidence / 'UV_ENV_VERIFIED.txt').exists()
