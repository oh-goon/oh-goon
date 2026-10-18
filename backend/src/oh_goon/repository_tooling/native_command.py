from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
_COMMAND_BANNER = _ROOT / "scripts" / "command_banner.sh"


def _sanitize_display_text(text: str, path_labels: tuple[tuple[str, str], ...]) -> str:
    sanitized = text
    for path, label in sorted(path_labels, key=lambda item: len(item[0]), reverse=True):
        if path:
            sanitized = sanitized.replace(path, label)
    return sanitized


def _path_labels(values: list[str]) -> tuple[tuple[str, str], ...]:
    labels: list[tuple[str, str]] = []
    for value in values:
        path, separator, label = value.rpartition("=")
        if not separator or not path or not label:
            raise ValueError("--path-label requires ABSOLUTE_PATH=DISPLAY_LABEL")
        if not Path(path).is_absolute():
            raise ValueError("--path-label path must be absolute")
        labels.append((path.rstrip("/"), label))
    return tuple(labels)


def _banner(label: str, detail: str = "", *, stderr: bool = False) -> None:
    command = ("sh", str(_COMMAND_BANNER), label, detail)
    if stderr:
        command = (*command, "stderr")
    subprocess.check_call(command)


def _native_uv_environment_evidence(env: dict[str, str]) -> None:
    raw = env.get("UV_SYSTEM_CERTS")
    if raw is None:
        print("Native uv TLS configuration: UV_SYSTEM_CERTS not set in inherited environment")
    else:
        print(f"Native uv TLS configuration: UV_SYSTEM_CERTS={raw} (inherited)")
    index_names = tuple(name for name in ("UV_DEFAULT_INDEX", "UV_INDEX") if env.get(name))
    if index_names:
        print(
            "Native uv index configuration: "
            + ", ".join(f"{name} set (inherited)" for name in index_names)
        )
    else:
        print(
            "Native uv index configuration: no inherited UV_DEFAULT_INDEX/UV_INDEX override; "
            "native uv config discovery remains authoritative"
        )
    if env.get("UV_CONFIG_FILE"):
        print("Native uv config discovery: UV_CONFIG_FILE set (inherited)")
    elif env.get("UV_NO_CONFIG"):
        print("Native uv config discovery: UV_NO_CONFIG set (inherited)")
    else:
        print("Native uv config discovery: enabled (user/system uv.toml may apply)")


def run_native_command(
    command: tuple[str, ...],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    owner: str = "native-tool",
    show_command_banner: bool = True,
    path_labels: tuple[tuple[str, str], ...] = (),
) -> int:
    if not command:
        raise ValueError("command is required")
    rendered = _sanitize_display_text(shlex.join(command), path_labels)
    if show_command_banner:
        _banner("NATIVE TOOL COMMAND", f"Owner: {owner}\nCommand: {rendered}")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(
            "NATIVE TOOL COMMAND FAILED\n"
            f"Owner: {owner}\nCommand: {rendered}\nFailure class: process_start\n"
            f"Error: {_sanitize_display_text(str(exc), path_labels)}",
            file=sys.stderr,
        )
        return 127
    if result.stdout:
        print(_sanitize_display_text(result.stdout, path_labels), end="")
    if result.stderr:
        print(_sanitize_display_text(result.stderr, path_labels), end="", file=sys.stderr)
    if result.returncode != 0:
        print(
            "NATIVE TOOL COMMAND FAILED\n"
            f"Owner: {owner}\nCommand: {rendered}\nExit code: {result.returncode}",
            file=sys.stderr,
        )
    return result.returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="oh-goon-native-command")
    root.add_argument("--cwd")
    root.add_argument("--owner", default="native-tool")
    root.add_argument("--env", action="append", default=[])
    root.add_argument("--path-label", action="append", default=[])
    root.add_argument("--no-command-banner", action="store_true")
    root.add_argument("command", nargs=argparse.REMAINDER)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = tuple(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser().error("a command after -- is required")
    try:
        path_labels = _path_labels(args.path_label)
    except ValueError as exc:
        parser().error(str(exc))
    child_env = os.environ.copy()
    child_env.pop("VIRTUAL_ENV", None)
    for assignment in args.env:
        key, separator, value = assignment.partition("=")
        if not separator or not key:
            parser().error("--env requires KEY=VALUE")
        child_env[key] = value
    if args.owner.casefold() == "uv":
        _native_uv_environment_evidence(child_env)
    return run_native_command(
        command,
        cwd=args.cwd,
        env=child_env,
        owner=args.owner,
        show_command_banner=not args.no_command_banner,
        path_labels=path_labels,
    )


if __name__ == "__main__":
    raise SystemExit(main())
