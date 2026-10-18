from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.operation import (
    ComparedGitRemote,
    GitRemoteComparisonUnavailable,
    GitRemoteWithoutUpstream,
    NoGitRemote,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.operations.readiness import _remote_observation


class _GitResponses:
    def __init__(self, *responses: CommandResult) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args: str, **_kwargs: object) -> CommandResult:
        self.calls.append(args)
        return self._responses.pop(0)


def _result(*, returncode: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(returncode=returncode, stdout=stdout, stderr="")


def test_remote_observation_records_no_remote_without_policy_state(tmp_path: Path) -> None:
    git = _GitResponses(_result(stdout=""))

    remote = _remote_observation(tmp_path, git, None)  # type: ignore[arg-type]

    assert isinstance(remote, NoGitRemote)
    assert git.calls == [("remote",)]


def test_remote_observation_reports_all_remotes_without_guessing_one_for_no_upstream(
    tmp_path: Path,
) -> None:
    git = _GitResponses(_result(stdout="upstream\norigin\n"), _result(returncode=1))

    remote = _remote_observation(tmp_path, git, None)  # type: ignore[arg-type]

    assert isinstance(remote, GitRemoteWithoutUpstream)
    assert remote.remote_names == ("origin", "upstream")


def test_remote_observation_binds_upstream_to_comparison_state(tmp_path: Path) -> None:
    git = _GitResponses(
        _result(stdout="origin\n"),
        _result(stdout="origin/main"),
        _result(stdout="1 2"),
    )

    remote = _remote_observation(tmp_path, git, None)  # type: ignore[arg-type]

    assert isinstance(remote, ComparedGitRemote)
    assert remote.state == "diverged"
    assert remote.remote_name == "origin"
    assert remote.upstream == "origin/main"


def test_remote_observation_uses_declared_git_upstream_instead_of_origin_preference(
    tmp_path: Path,
) -> None:
    git = _GitResponses(
        _result(stdout="origin\nupstream\n"),
        _result(stdout="upstream/main"),
        _result(stdout="0 0"),
    )

    remote = _remote_observation(tmp_path, git, None)  # type: ignore[arg-type]

    assert isinstance(remote, ComparedGitRemote)
    assert remote.remote_name == "upstream"
    assert remote.upstream == "upstream/main"


def test_remote_observation_fails_closed_on_unreadable_comparison(tmp_path: Path) -> None:
    git = _GitResponses(
        _result(stdout="origin\n"),
        _result(stdout="origin/main"),
        _result(stdout="not counts"),
    )

    remote = _remote_observation(tmp_path, git, None)  # type: ignore[arg-type]

    assert isinstance(remote, GitRemoteComparisonUnavailable)
    assert remote.upstream == "origin/main"
