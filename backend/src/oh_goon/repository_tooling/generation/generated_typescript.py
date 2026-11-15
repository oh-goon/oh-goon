from __future__ import annotations

from collections.abc import Iterable

GENERATED_SHARD_TARGET_LINES = 650


def pack_blocks(
    blocks: Iterable[tuple[str, list[str]]],
    *,
    target_lines: int = GENERATED_SHARD_TARGET_LINES,
) -> list[list[tuple[str, list[str]]]]:
    """Pack indivisible blocks deterministically, using size only as a layout target."""
    shards: list[list[tuple[str, list[str]]]] = []
    current: list[tuple[str, list[str]]] = []
    current_lines = 0
    for name, lines in blocks:
        block_lines = len(lines)
        if current and current_lines + block_lines > target_lines:
            shards.append(current)
            current = []
            current_lines = 0
        current.append((name, lines))
        current_lines += block_lines
    if current:
        shards.append(current)
    return shards
