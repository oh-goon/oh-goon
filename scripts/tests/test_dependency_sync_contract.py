import os
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _target(name: str) -> str:
    return MAKEFILE.split(f"{name}:", 1)[1].split("\n\n", 1)[0]


def test_backend_uv_lock_is_committed_source_authority() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    policy = (ROOT / "backend/src/oh_goon/core/source_tree_policy.py").read_text(encoding="utf-8")
    assert "backend/uv.lock" not in ignore
    assert 'CANONICAL_BACKEND_LOCK = PurePosixPath("backend/uv.lock")' in policy


def test_init_is_direct_native_locked_materialization() -> None:
    init = _target("init")
    assert "uv sync --project backend --locked --group dev --group quality --group build" in init
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in init
    assert "$(DEPENDENCY_RESOLUTION_BOOTSTRAP) init" not in init
    assert "--offline" not in init


def test_make_sync_can_author_lock_before_environment_exists() -> None:
    sync = _target("sync")
    assert "$(MAKE) backend-package-build-check" in sync
    assert sync.index("backend-package-build-check") < sync.index("uv lock --project backend")
    assert "uv lock --project backend" in sync
    assert "$(DEPENDENCY_POLICY) public-lock-check" in sync
    assert "DEPENDENCY_RESOLUTION" not in sync


def test_backend_install_uses_native_uv_locked_consumption() -> None:
    backend_install = _target("backend-install")
    assert "uv sync --project backend --locked" in backend_install
    assert "--frozen" not in backend_install


def test_backend_lock_check_is_native_freshness_not_presence_only() -> None:
    check = _target("backend-lock-check")
    assert "uv lock --project backend --check" in check
    assert "--check-exists" not in check
    assert "$(DEPENDENCY_POLICY) public-lock-check" in check


def test_transport_cleanup_is_explicit_and_independent_of_build_index() -> None:
    cleanup = _target("source-transport-clean")
    assert "bash scripts/source_transport_clean.sh" in cleanup
    assert "uv pip install" not in cleanup
    assert "uv lock" not in cleanup
    assert "uv sync" not in cleanup
    assert "npm" not in cleanup
    script = (ROOT / "scripts/source_transport_clean.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    assert "SOURCE TRANSPORT CLEAN BLOCKED" in script
    assert "-name '*:Zone.Identifier'" in script
    assert "-name '*.patch.sha256'" in script


def test_backend_build_preflight_proves_editable_package_without_release_wheel() -> None:
    build = _target("backend-package-build-check")
    assert "uv venv --project backend --no-python-downloads" in build
    assert "uv pip install" in build
    assert '--python "$$tmp/venv/bin/python"' in build
    assert "--no-deps --editable backend" in build
    assert "--no-sources --no-python-downloads" in build
    assert '"$$tmp/venv/bin/oh-goon-source-layout" check' in build
    assert "uv build" not in build


def test_backend_build_preflight_invokes_only_editable_native_uv_mode(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$OH_GOON_TEST_UV_LOG"
if [ "$1" = "venv" ]; then
  eval "target=\\${$#}"
  mkdir -p "$target/bin"
  : > "$target/bin/python"
elif [ "$1" = "pip" ]; then
  python_path=""
  previous=""
  for argument in "$@"; do
    if [ "$previous" = "--python" ]; then python_path="$argument"; break; fi
    previous="$argument"
  done
  bin_dir="${python_path%/python}"
  cat > "$bin_dir/oh-goon-source-layout" <<'EOF'
#!/bin/sh
set -eu
printf '%s\n' "source-layout $*" >> "$OH_GOON_TEST_UV_LOG"
EOF
  chmod +x "$bin_dir/oh-goon-source-layout"
fi
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    env["OH_GOON_TEST_UV_LOG"] = str(log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    subprocess.run(
        ("make", "backend-package-build-check"),
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert calls[0].startswith("venv --project backend --no-python-downloads ")
    assert "pip install --python " in calls[1]
    assert " --no-deps --editable backend --no-sources --no-python-downloads" in calls[1]
    assert calls[2] == "source-layout check"
    assert all("build --" not in call for call in calls)


def test_make_sync_rejects_source_contamination_before_lock_or_npm_mutation(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_log = tmp_path / "uv.log"
    npm_log = tmp_path / "npm.log"
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$OH_GOON_TEST_UV_LOG"
if [ "$1" = "venv" ]; then
  eval "target=\\${$#}"
  mkdir -p "$target/bin"
  : > "$target/bin/python"
elif [ "$1" = "pip" ]; then
  python_path=""
  previous=""
  for argument in "$@"; do
    if [ "$previous" = "--python" ]; then python_path="$argument"; break; fi
    previous="$argument"
  done
  bin_dir="${python_path%/python}"
  cat > "$bin_dir/oh-goon-source-layout" <<'EOF'
#!/bin/sh
exit 1
EOF
  chmod +x "$bin_dir/oh-goon-source-layout"
fi
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$OH_GOON_TEST_NPM_LOG"
exit 99
""",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    env = os.environ.copy()
    env["OH_GOON_TEST_UV_LOG"] = str(uv_log)
    env["OH_GOON_TEST_NPM_LOG"] = str(npm_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    completed = subprocess.run(
        ("make", "sync"),
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("venv --project backend") for call in calls)
    assert any(call.startswith("pip install --python ") for call in calls)
    assert all(not call.startswith("lock ") for call in calls)
    assert not npm_log.exists()


def test_repository_dependency_policy_does_not_own_native_uv_semantics() -> None:
    pyproject = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    target = pyproject["project"]["scripts"]["oh-goon-dependency-policy"]
    module = target.partition(":")[0]
    script_path = ROOT / "backend/src" / Path(*module.split(".")).with_suffix(".py")
    script = script_path.read_text(encoding="utf-8")
    assert "uv sync" not in script
    assert "uv pip install" not in script
    assert "command_init" not in script
    assert "command_acquire" not in script
    assert "subprocess" not in script
    assert "_run(" not in script
    assert "subprocess" not in script
