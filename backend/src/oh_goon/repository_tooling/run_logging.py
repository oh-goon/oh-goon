from __future__ import annotations

import os
import re
import stat
import sys
import time
from pathlib import Path


def _open_log_directory(base: Path, label: str, *, create: bool = True) -> int:
    """Acquire the selected private log directory through native directory handles."""
    if not base.is_absolute() or ".." in base.parts or not re.fullmatch(r"[A-Za-z0-9_-]+", label):
        raise ValueError("invalid run-log directory selection")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        parts = (*base.parts[1:], label)
        for index, part in enumerate(parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current
            )
            os.close(current)
            current = child
            observed = os.fstat(current)
            if observed.st_uid not in (0, os.getuid()):
                raise ValueError("run-log ancestor belongs to another user")
            shared_sticky_root = observed.st_uid == 0 and observed.st_mode & stat.S_ISVTX
            if observed.st_mode & 0o022 and not shared_sticky_root:
                raise ValueError("run-log ancestor is writable by another user")
            if index >= len(parts) - 2 and (
                observed.st_uid != os.getuid() or observed.st_mode & 0o077
            ):
                raise ValueError("run-log directories must be private to the current user")
        return current
    except BaseException:
        os.close(current)
        raise


def _bootstrap(args: list[str]) -> int:
    if len(args) < 4:
        print(
            "usage: oh-goon-run-log <repository> <log-base> <label> <command> [args...]",
            file=sys.stderr,
        )
        return 2
    root, base, label, *command = args
    env = dict(os.environ, OH_GOON_RUN_LOG_BOOTSTRAPPED="1", OH_GOON_RUN_LOG_PYTHON=sys.executable)
    directory: int | None = None
    log: int | None = None
    try:
        directory = _open_log_directory(Path(base), label)
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        active = f"{timestamp}-{os.getpid()}.log.inprogress"
        log = os.open(
            active,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        os.set_inheritable(directory, True)
        os.set_inheritable(log, True)
        env.update(
            OH_GOON_RUN_LOG_DIRECTORY_FD=str(directory),
            OH_GOON_RUN_LOG_FILE_FD=str(log),
            OH_GOON_RUN_LOG_ACTIVE_NAME=active,
            OH_GOON_RUN_LOG_TIMESTAMP=timestamp,
        )
    except (OSError, ValueError):
        if log is not None:
            os.close(log)
            log = None
        if directory is not None:
            os.close(directory)
            directory = None
        env["OH_GOON_RUN_LOG_UNAVAILABLE"] = "1"
    try:
        os.execvpe(
            "bash", ["bash", str(Path(root) / "scripts/run_logged.sh"), label, *command], env
        )
    except OSError:
        print(
            "Oh-Goon logging wrapper could not start the selected command shell.", file=sys.stderr
        )
        return 127
    finally:
        if log is not None:
            os.close(log)
        if directory is not None:
            os.close(directory)


class _PathSanitizer:
    def __init__(self, root: str, base: str, home: str) -> None:
        replacements = {
            os.fsencode(root): b"<repo>",
            os.fsencode(base): b"<state>/run-logs",
            os.fsencode(home): b"<home>",
        }
        self._replacements = {key: value for key, value in replacements.items() if key}
        self._patterns = re.compile(
            b"|".join(re.escape(key) for key in self._replacements) + rb"|/tmp/tmp\.[A-Za-z0-9._-]+"
        )
        self._overlap = max(9, *(len(key) for key in self._replacements))
        self._pending = b""
        self._temporary_suffix = False

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        data = self._pending + data
        self._pending = b""
        if self._temporary_suffix:
            suffix = re.match(rb"[A-Za-z0-9._-]*", data)
            assert suffix is not None
            data = data[suffix.end() :]
            if not data:
                return b""
            self._temporary_suffix = False
        boundary = len(data) if final else max(0, len(data) - self._overlap)
        if all(b"\n" not in path for path in self._replacements):
            boundary = max(boundary, data.rfind(b"\n") + 1)
        pieces: list[bytes] = []
        cursor = 0
        for match in self._patterns.finditer(data):
            if match.start() >= boundary:
                break
            pieces.append(data[cursor : match.start()])
            matched = match.group()
            replacement = self._replacements.get(matched)
            pieces.append(replacement if replacement is not None else b"<tmp>")
            cursor = match.end()
            boundary = max(boundary, cursor)
            if replacement is None and cursor == len(data):
                self._temporary_suffix = not final
        pieces.append(data[cursor:boundary])
        self._pending = data[boundary:]
        return b"".join(pieces)


def _stream(args: list[str]) -> int:
    root, base, home, raw_descriptor = args
    descriptor = int(raw_descriptor)
    sanitizer = _PathSanitizer(root, base, home)
    persistence_ok = True
    while True:
        chunk = os.read(0, 8192)
        clean = sanitizer.feed(chunk, final=not chunk)
        sys.stdout.buffer.write(clean)
        sys.stdout.buffer.flush()
        if descriptor >= 0 and persistence_ok:
            try:
                remaining = memoryview(clean)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written == 0:
                        raise OSError("run-log write made no progress")
                    remaining = remaining[written:]
            except OSError:
                persistence_ok = False
        if not chunk:
            break
    return 0 if persistence_ok else 1


def _read(args: list[str]) -> int:
    if len(args) != 2:
        print("usage: oh-goon-run-log --read <log-base> <label>", file=sys.stderr)
        return 2
    base, label = args
    directory: int | None = None
    log: int | None = None
    try:
        directory = _open_log_directory(Path(base).absolute(), label, create=False)
        log = os.open(
            "latest.log",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        observed = os.fstat(log)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.getuid()
            or observed.st_mode & 0o077
        ):
            raise ValueError("latest run log is not a private regular file")
        while chunk := os.read(log, 8192):
            sys.stdout.buffer.write(chunk)
        return 0
    except (OSError, ValueError):
        print(
            "No readable private Oh-Goon log is available at the selected location.",
            file=sys.stderr,
        )
        return 2
    finally:
        if log is not None:
            os.close(log)
        if directory is not None:
            os.close(directory)


def _verify_location(args: list[str]) -> int:
    base, label, descriptor = args
    acquired: int | None = None
    try:
        acquired = _open_log_directory(Path(base), label, create=False)
        observed = os.fstat(acquired)
        held = os.fstat(int(descriptor))
        return 0 if (observed.st_dev, observed.st_ino) == (held.st_dev, held.st_ino) else 1
    except (OSError, ValueError):
        return 1
    finally:
        if acquired is not None:
            os.close(acquired)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--interpreter"]:
        print(sys.executable)
        return 0
    if len(args) == 5 and args[0] == "--stream":
        return _stream(args[1:])
    if args and args[0] == "--read":
        return _read(args[1:])
    if len(args) == 4 and args[0] == "--verify-location":
        return _verify_location(args[1:])
    return _bootstrap(args)


if __name__ == "__main__":
    raise SystemExit(main())
