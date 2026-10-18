"""Development-only, nondestructive Windows-extraction metadata audit.

This does not authenticate the source, modify files, or grant Fleet authority.
Known ADS sidecars are inventoried separately; all other files remain source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

_PRUNED = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "coverage",
        ".oh-goon-data",
        "test-results",
        "release-artifacts",
    }
)
_IGNORED_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
_ADS_SUFFIXES = (":Zone.Identifier", ":Zone.Identifier:$DATA")


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _metadata_target(name: str) -> str | None:
    for suffix in _ADS_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def scan(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    source: list[tuple[str, Path]] = []
    sidecars: list[dict[str, str | int]] = []
    unsafe: list[str] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in sorted(dirs):
            p = parent / name
            if p.is_symlink() or not p.is_dir():
                unsafe.append(str(p.relative_to(root)))
        dirs[:] = sorted(d for d in dirs if d not in _PRUNED and not (parent / d).is_symlink())
        for name in sorted(files):
            p = parent / name
            rel = "./" + p.relative_to(root).as_posix()
            mode = p.lstat().st_mode
            if not stat.S_ISREG(mode) or p.stat(follow_symlinks=False).st_nlink != 1:
                unsafe.append(rel)
                continue
            target_name = _metadata_target(name)
            if target_name is not None:
                target = p.with_name(target_name)
                if (
                    target.is_symlink()
                    or not (target.is_file() or target.is_dir())
                    or (target.is_file() and target.stat().st_nlink != 1)
                ):
                    unsafe.append(rel)
                    continue
                sidecars.append(
                    {
                        "path": rel,
                        "target": "./" + target.relative_to(root).as_posix(),
                        "sha256": _hash(p),
                        "bytes": p.stat().st_size,
                        "content_authority": "NONE",
                    }
                )
            elif name.endswith(_IGNORED_SUFFIXES) or name.startswith(".coverage"):
                continue
            else:
                if "\n" in rel or "\r" in rel or "\\" in rel:
                    unsafe.append(rel)
                    continue
                source.append((rel, p))
    source.sort(key=lambda item: item[0].encode())
    sidecars.sort(key=lambda item: str(item["path"]).encode())
    return {
        "schema": "oh-goon.source-transport-audit/v1",
        "classification": "DEVELOPMENT_ONLY",
        "source_members": len(source),
        "sidecar_count": len(sidecars),
        "windows_sidecars": sidecars,
        "unsafe_entries": sorted(unsafe),
        "inventory": "".join(f"{_hash(p)}  {rel}\n" for rel, p in source),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only OH-goon extraction source audit")
    parser.add_argument("action", choices=["inventory", "audit"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    result = scan(args.root)
    if args.baseline is not None:
        if args.baseline.is_symlink() or not args.baseline.is_file():
            result["baseline"] = "UNREADABLE"
        else:
            expected = args.baseline.read_text(encoding="utf-8")
            # A manifest is allowed to omit itself to avoid circular hashes.
            own = (
                "./" + args.baseline.resolve().relative_to(args.root.resolve()).as_posix()
                if args.baseline.resolve().is_relative_to(args.root.resolve())
                else None
            )
            actual = result["inventory"]
            if own:
                actual = "".join(
                    line
                    for line in actual.splitlines(keepends=True)
                    if not line.endswith("  " + own + "\n")
                )
            result["baseline"] = "MATCH" if actual == expected else "MISMATCH"
    if args.action == "inventory":
        if result["unsafe_entries"]:
            print(
                "BLOCKED: unsafe source transport entries: " + repr(result["unsafe_entries"][:8]),
                file=sys.stderr,
            )
            return 3
        sys.stdout.write(str(result["inventory"]))
    else:
        result.pop("inventory")
        print(json.dumps(result, indent=2, sort_keys=True))
    return (
        3 if result["unsafe_entries"] or result.get("baseline") in ("UNREADABLE", "MISMATCH") else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
