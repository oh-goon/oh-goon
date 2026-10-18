from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.process

ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(ROOT / "Makefile", repo / "Makefile")
    (repo / "backend").mkdir()
    shutil.copy2(ROOT / "backend/uv.lock", repo / "backend/uv.lock")
    shutil.copy2(ROOT / "backend/pyproject.toml", repo / "backend/pyproject.toml")
    (repo / "frontend").mkdir()
    shutil.copy2(ROOT / "frontend/package.json", repo / "frontend/package.json")
    shutil.copy2(
        ROOT / "frontend/package-lock.json", repo / "frontend/package-lock.json"
    )

    tools = tmp_path / "tools"
    tools.mkdir()
    _write_executable(
        tools / "uv",
        """#!/bin/sh
set -eu
[ "${1:-}" = sync ]
if [ -n "${FAIL_UV_ONCE:-}" ] && [ -f "$FAIL_UV_ONCE" ]; then rm "$FAIL_UV_ONCE"; exit 73; fi
mkdir -p backend/.venv/bin
cat > backend/.venv/bin/oh-goon <<'SCRIPT'
#!/bin/sh
exit 0
SCRIPT
chmod +x backend/.venv/bin/oh-goon
""",
    )
    _write_executable(
        tools / "npm",
        """#!/bin/sh
set -eu
[ "${1:-}" = ci ]
if [ -n "${FAIL_NPM_ONCE:-}" ] && [ -f "$FAIL_NPM_ONCE" ]; then rm "$FAIL_NPM_ONCE"; exit 74; fi
mkdir -p node_modules
""",
    )
    _write_executable(
        tools / "run-step",
        """#!/bin/sh
set -eu
while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
shift
exec "$@"
""",
    )
    _write_executable(tools / "banner", "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env["PATH"] = f"{tools}:{env['PATH']}"
    env["TEST_RUN_STEP"] = str(tools / "run-step")
    env["TEST_COMMAND_BANNER"] = str(tools / "banner")
    return repo, env


def _run_init(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "init",
            f"RUN_STEP={env['TEST_RUN_STEP']}",
            f"COMMAND_BANNER={env['TEST_COMMAND_BANNER']}",
        ],
        cwd=repo,
        env={key: value for key, value in env.items() if not key.startswith("TEST_")},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_make_init_delegates_materialization_to_native_package_managers(
    tmp_path: Path,
) -> None:
    repo, env = _fixture(tmp_path)
    before = (repo / "backend/uv.lock").read_bytes()
    result = _run_init(repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "backend/uv.lock").read_bytes() == before
    assert (repo / "backend/.venv/bin/oh-goon").is_file()
    assert (repo / "frontend/node_modules").is_dir()
    assert not (repo / "generated/release/static").exists()


def test_make_init_is_idempotent_without_oh_goon_cache_or_transport_state(
    tmp_path: Path,
) -> None:
    repo, env = _fixture(tmp_path)
    assert _run_init(repo, env).returncode == 0
    assert _run_init(repo, env).returncode == 0
    assert not (repo / "generated").exists()
    assert not (repo / ".oh-goon-cache").exists()


@pytest.mark.parametrize("owner", ["uv", "npm"])
def test_make_init_retries_only_when_operator_reinvokes_failed_native_owner(
    tmp_path: Path, owner: str
) -> None:
    repo, env = _fixture(tmp_path)
    marker = repo / f".fail-{owner}-once"
    marker.write_text("fail\n", encoding="utf-8")
    env[f"FAIL_{owner.upper()}_ONCE"] = str(marker)
    failed = _run_init(repo, env)
    assert failed.returncode != 0
    recovered = _run_init(repo, env)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


def test_make_init_does_not_depend_on_release_static_or_source_cleanup(
    tmp_path: Path,
) -> None:
    repo, env = _fixture(tmp_path)
    makefile = (repo / "Makefile").read_text(encoding="utf-8")
    init_block = makefile.split("\ninit:\n", 1)[1].split("\ninit-doctor:", 1)[0]
    assert "release-static-dir" not in init_block
    assert "transport-clean" not in init_block
    assert "backend-python-capability-check" not in init_block
    assert "frontend_install.sh" not in init_block
    result = _run_init(repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
