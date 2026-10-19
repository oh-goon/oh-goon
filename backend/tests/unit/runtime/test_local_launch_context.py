from __future__ import annotations

import ast
from pathlib import Path

import pytest

from oh_goon.runtime import local_launch_context
from oh_goon.runtime.local_launch_context import (
    LOCAL_LAUNCH_BACKEND_GENERATION_ENV,
    LOCAL_LAUNCH_ID_ENV,
    LOCAL_LAUNCH_OWNER_GENERATION_ENV,
    LocalLaunchRuntimeContext,
)

SOURCE_ROOT = Path(local_launch_context.__file__).resolve().parents[1]
OWNER = Path(local_launch_context.__file__).resolve()


def test_context_round_trips_and_clears_only_its_own_values() -> None:
    environ: dict[str, str] = {}
    context = LocalLaunchRuntimeContext(
        launch_id="launch-a",
        owner_generation=2,
        backend_generation=3,
    )
    context.install(environ)
    assert LocalLaunchRuntimeContext.from_environment(environ) == context

    environ[LOCAL_LAUNCH_ID_ENV] = "launch-newer"
    context.clear(environ)
    assert environ[LOCAL_LAUNCH_ID_ENV] == "launch-newer"
    assert LOCAL_LAUNCH_OWNER_GENERATION_ENV not in environ
    assert LOCAL_LAUNCH_BACKEND_GENERATION_ENV not in environ


def test_context_rejects_partial_or_invalid_generation_environment() -> None:
    with pytest.raises(ValueError, match="incomplete local launch context"):
        LocalLaunchRuntimeContext.from_environment({LOCAL_LAUNCH_ID_ENV: "launch-a"})
    with pytest.raises(ValueError, match="must be integers"):
        LocalLaunchRuntimeContext.from_environment(
            {
                LOCAL_LAUNCH_ID_ENV: "launch-a",
                LOCAL_LAUNCH_OWNER_GENERATION_ENV: "one",
                LOCAL_LAUNCH_BACKEND_GENERATION_ENV: "1",
            }
        )


def test_local_launch_environment_identities_have_one_production_spelling() -> None:
    values = (
        LOCAL_LAUNCH_ID_ENV,
        LOCAL_LAUNCH_OWNER_GENERATION_ENV,
        LOCAL_LAUNCH_BACKEND_GENERATION_ENV,
    )
    for value in values:
        occurrences: list[tuple[Path, int]] = []
        for path in SOURCE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            occurrences.extend(
                (path, node.lineno)
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value == value
            )
        assert len(occurrences) == 1
        assert occurrences[0][0] == OWNER
