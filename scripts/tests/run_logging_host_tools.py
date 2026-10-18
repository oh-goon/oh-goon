from __future__ import annotations

import os
import sys
from pathlib import Path

_HOST_TOOL_NAMES = ("ln", "mkdir", "mv", "rm")

_SHIM = r'''#!__PYTHON__
from __future__ import annotations

import os
import sys
from pathlib import Path


def _tee(args: list[str]) -> int:
    streams = [open(path, "wb") for path in args]
    try:
        while True:
            chunk = sys.stdin.buffer.readline()
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            for stream in streams:
                stream.write(chunk)
                stream.flush()
    finally:
        for stream in streams:
            stream.close()
    return 0


def main() -> int:
    name = Path(sys.argv[0]).name
    args = sys.argv[1:]
    if name == "ln":
        paths = [Path(arg) for arg in args if not arg.startswith("-")]
        if len(paths) != 2:
            raise SystemExit("ln shim requires source and destination")
        os.link(paths[0], paths[1])
        return 0
    if name == "mkdir":
        parents = "-p" in args
        for path in (arg for arg in args if arg != "-p"):
            Path(path).mkdir(parents=parents, exist_ok=parents)
        return 0
    if name == "mv":
        paths = [Path(arg) for arg in args if not arg.startswith("-")]
        if len(paths) != 2:
            raise SystemExit("mv shim requires source and destination")
        os.replace(paths[0], paths[1])
        return 0
    if name == "rm":
        for path in (arg for arg in args if not arg.startswith("-")):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
        return 0
    raise SystemExit(f"unsupported logging host tool: {name}")


if __name__ == "__main__":
    raise SystemExit(main())
'''.replace("__PYTHON__", sys.executable)


def logging_host_env(tmp_path: Path) -> dict[str, str]:
    """Provide host-frontdoor services without widening strict Goon authority."""

    tool_dir = tmp_path / "logging-host-tools"
    tool_dir.mkdir()
    for name in _HOST_TOOL_NAMES:
        path = tool_dir / name
        path.write_text(_SHIM, encoding="utf-8")
        path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tool_dir}:{env['PATH']}"
    return env


def logging_host_tool_names() -> tuple[str, ...]:
    return _HOST_TOOL_NAMES
