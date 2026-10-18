from __future__ import annotations

from collections.abc import Iterable, Mapping

MAX_TYPESCRIPT_LINES = 800
GENERATED_SHARD_TARGET_LINES = 650


def line_count(content: str) -> int:
    return len(content.splitlines())


def pack_blocks(
    blocks: Iterable[tuple[str, list[str]]],
    *,
    target_lines: int = GENERATED_SHARD_TARGET_LINES,
) -> list[list[tuple[str, list[str]]]]:
    """Pack indivisible generated blocks into deterministic line-bounded shards."""
    shards: list[list[tuple[str, list[str]]]] = []
    current: list[tuple[str, list[str]]] = []
    current_lines = 0
    for name, lines in blocks:
        block_lines = len(lines)
        if block_lines > target_lines:
            raise ValueError(
                f"Generated TypeScript block {name!r} is {block_lines} lines; "
                f"it cannot fit the {target_lines}-line shard target"
            )
        if current and current_lines + block_lines > target_lines:
            shards.append(current)
            current = []
            current_lines = 0
        current.append((name, lines))
        current_lines += block_lines
    if current:
        shards.append(current)
    return shards


def assert_typescript_file_limit(outputs: Mapping[str, str]) -> None:
    oversized = {
        path: line_count(content)
        for path, content in outputs.items()
        if path.endswith((".ts", ".tsx", ".mts", ".cts"))
        and line_count(content) > MAX_TYPESCRIPT_LINES
    }
    if oversized:
        details = ", ".join(
            f"{path}={lines}" for path, lines in sorted(oversized.items())
        )
        raise ValueError(
            f"Generated TypeScript exceeds {MAX_TYPESCRIPT_LINES} lines: {details}"
        )
