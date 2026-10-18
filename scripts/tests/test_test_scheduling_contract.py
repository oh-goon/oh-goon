from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _batch_invocations(target: str) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    completed = subprocess.run(
        [
            "make",
            "-n",
            target,
            "PYTEST_RESUME=",
            "TOOLING_TEST_RESUME_CONTEXT=",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    invocations: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for line in completed.stdout.splitlines():
        tokens = shlex.split(line)
        try:
            runner_index = next(
                index
                for index, token in enumerate(tokens)
                if token.endswith("/oh-goon-test-batches")
            )
        except StopIteration:
            continue
        arguments = tuple(tokens[runner_index + 1 :])
        option_index = next(
            (index for index, token in enumerate(arguments) if token.startswith("--")),
            len(arguments),
        )
        invocations.append((arguments[:option_index], arguments[option_index:]))
    return tuple(invocations)


def test_default_backend_scheduling_is_suite_rooted_not_filename_driven() -> None:
    fast = _batch_invocations("test-python")
    complete = _batch_invocations("test-python-complete")

    assert tuple(paths for paths, _ in fast) == (
        ("backend/tests/unit",),
        ("backend/tests/dependency_complete",),
    )
    assert tuple(paths for paths, _ in complete) == (
        ("backend/tests/unit",),
        ("backend/tests/unit",),
        ("backend/tests/dependency_complete",),
    )
    assert all("--exclude" not in options for _, options in (*fast, *complete))


def test_default_tooling_scheduling_is_suite_rooted_not_filename_driven() -> None:
    fast = _batch_invocations("test-tooling")
    complete = _batch_invocations("test-tooling-complete")

    assert tuple(paths for paths, _ in fast) == (("scripts/tests",),)
    assert tuple(paths for paths, _ in complete) == (("scripts/tests",),)
    assert all("--exclude" not in options for _, options in (*fast, *complete))


def test_distinct_backend_execution_class_is_declared_by_semantic_marker() -> None:
    complete = _batch_invocations("test-python-complete")
    unit_profiles = [options for paths, options in complete if paths == ("backend/tests/unit",)]

    assert len(unit_profiles) == 2
    marker_values = {
        options[options.index("--marker") + 1]
        for options in unit_profiles
    }
    assert marker_values == {"not process", "process"}
