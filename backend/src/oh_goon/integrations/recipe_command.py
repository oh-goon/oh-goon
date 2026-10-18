from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from oh_goon.core.capability_model import CapabilityDefinition
from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.target import UvRecipeTarget


@dataclass(frozen=True, slots=True)
class PreparedRecipeCommand:
    argv: tuple[str, ...]
    temporary_output: tempfile.TemporaryDirectory[str] | None = None

    def close(self) -> None:
        if self.temporary_output is not None:
            self.temporary_output.cleanup()


@contextmanager
def prepare_recipe_command(
    target: UvRecipeTarget, capability: CapabilityDefinition
) -> Iterator[PreparedRecipeCommand]:
    """Render one capability-owned uv recipe command and own temporary output lifetime."""
    if capability.recipe is not target.recipe:
        raise CapabilityUnavailable(
            f"Recipe capability does not match target recipe: {capability.capability_id}"
        )
    execution = capability.recipe_execution
    if execution is None:
        raise CapabilityUnavailable(
            f"Recipe capability has no execution policy: {capability.capability_id}"
        )
    temporary = (
        tempfile.TemporaryDirectory(prefix="oh-goon-build-") if execution.temporary_output else None
    )
    values = {"tempdir": temporary.name if temporary is not None else ""}
    argv = [token.format(**values) for token in execution.argv]
    if execution.append_task_argv:
        assert target.task is not None
        argv.extend([target.task, *target.args])
    prepared = PreparedRecipeCommand(argv=tuple(argv), temporary_output=temporary)
    try:
        yield prepared
    finally:
        prepared.close()


def decode_recipe_payload(enabled: bool, stdout: str) -> dict[str, Any] | list[Any] | None:
    """Decode optional structured recipe output without making malformed tool output authoritative."""
    if not enabled or not stdout.strip():
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, (dict, list)) else None
