from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TOOLS = {"pytest": "pytest", "ruff": "ruff", "mypy": "mypy"}
_HOST_PYTHON_ENV = ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")


def _clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in _HOST_PYTHON_ENV:
        env.pop(name, None)
    return env


def _require_bound_interpreter() -> Path:
    entrypoint = ROOT / "backend/.venv/bin/python"
    expected = entrypoint.resolve()
    observed = Path(sys.executable).resolve()
    if observed != expected:
        raise RuntimeError(
            "qualification execution refuses interpreter rediscovery: "
            f"expected {expected}, observed {observed}"
        )
    return entrypoint


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    try:
        interpreter = _require_bound_interpreter()
        if not args or args[0] not in TOOLS:
            print("usage: oh-goon-qualification-exec {pytest|ruff|mypy} [args...]", file=sys.stderr)
            return 2
        module = TOOLS[args.pop(0)]
        completed = subprocess.run(
            [str(interpreter), "-I", "-m", module, *args],
            cwd=Path.cwd(),
            env=_clean_environment(),
            check=False,
        )
        return completed.returncode
    except (OSError, RuntimeError) as exc:
        print(f"Qualification execution preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
