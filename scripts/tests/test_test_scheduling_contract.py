from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _batch_invocations(
    target: str,
) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
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
        ("backend/tests/dependency_complete",),
    )
    assert all("--exclude" not in options for _, options in (*fast, *complete))
    assert all(
        options[options.index("--marker") + 1]
        == "not slow and not process and not scale and not certification"
        for _, options in fast
    )
    assert tuple(
        invocation
        for invocation in complete
        if invocation[0] == ("backend/tests/dependency_complete",)
    ) == _batch_invocations("test-python-dependency-complete")


def test_default_tooling_scheduling_is_suite_rooted_not_filename_driven() -> None:
    fast = _batch_invocations("test-tooling")
    complete = _batch_invocations("test-tooling-complete")

    assert tuple(paths for paths, _ in fast) == (("scripts/tests",),)
    assert tuple(paths for paths, _ in complete) == (("scripts/tests",),)
    assert all("--exclude" not in options for _, options in (*fast, *complete))


def test_distinct_backend_execution_class_is_declared_by_semantic_marker() -> None:
    complete = _batch_invocations("test-python-complete")
    for suite in ("backend/tests/unit", "backend/tests/dependency_complete"):
        profiles = [options for paths, options in complete if paths == (suite,)]
        assert len(profiles) == 2
        by_marker = {
            options[options.index("--marker") + 1]: options for options in profiles
        }
        assert set(by_marker) == {"not process", "process"}
        process = by_marker["process"]
        assert process[process.index("--batch-size") + 1] == "1"
        assert process[process.index("--workers") + 1] == "1"
        assert process[process.index("--batch-timeout") + 1] == "180"
        assert process[process.index("--suite-timeout") + 1] == "600"
