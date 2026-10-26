"""Native source helpers preserve cancellation and require terminal process evidence."""

import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations.git_source_preparation import git_source_cache, prepare_git_source


def _native_source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
    (source / "sample.txt").write_text("captured source\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "source",
        ],
        check=True,
    )
    with prepare_git_source(str(source), "main", commit=None) as (root, commit):
        assert (root / "sample.txt").read_text() == "captured source\n"
    assert not next(git_source_cache().glob("*.git")).joinpath("worktrees").exists()
    return source, commit


@pytest.mark.parametrize("error_type", [ValueError, OperationCancelledError])
def test_capture_error_survives_native_worktree_cleanup_failure(tmp_path, monkeypatch, error_type):
    source, commit = _native_source(tmp_path, monkeypatch)
    native = GitService.run
    cleanup = []

    def fail_remove(self, *arguments, **kwargs):
        if "worktree" in arguments and "remove" in arguments:
            cleanup.append(kwargs["cancel_requested"])
            return CommandResult(1, "", "private cleanup diagnostic")
        return native(self, *arguments, **kwargs)

    monkeypatch.setattr(GitService, "run", fail_remove)
    failure = error_type("capture stopped")
    with pytest.raises(error_type) as raised:
        with prepare_git_source(str(source), "main", commit=commit):
            raise failure
    assert raised.value is failure
    assert cleanup == [None]
    assert raised.value.__notes__ == ["Git source cleanup also failed: FleetPlanInvalid"]
    assert "private cleanup diagnostic" not in str(raised.value)


def test_gitlink_refuses_before_checkout_and_closes_original_worktree(tmp_path, monkeypatch):
    source, original = _native_source(tmp_path, monkeypatch)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{original},component",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "incomplete application closure",
        ],
        check=True,
    )
    native = GitService.run

    def no_checkout(self, *arguments, **kwargs):
        assert "checkout-index" not in arguments, (
            "Project bytes were materialized before closure refusal"
        )
        return native(self, *arguments, **kwargs)

    monkeypatch.setattr(GitService, "run", no_checkout)
    with pytest.raises(FleetPlanInvalid, match="submodules are unsupported"):
        with prepare_git_source(str(source), "main", commit=None):
            pytest.fail("Incomplete Git application reached source capture")
    assert not next(git_source_cache().glob("*.git")).joinpath("worktrees").exists()


def test_nonquiescent_native_result_stops_before_next_git_helper(tmp_path, monkeypatch):
    source, commit = _native_source(tmp_path, monkeypatch)
    run = Mock(
        return_value=CommandResult(
            0, "", "", supervisor=CommandSupervisorReceipt(final_process_group_quiescent=False)
        )
    )
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid, match="not quiescent"):
        with prepare_git_source(str(source), "main", commit=commit):
            pytest.fail("Nonquiescent Git source was delivered")
    assert run.call_count == 1
    assert not next(git_source_cache().glob("*.git")).joinpath("worktrees").exists()


def test_native_source_commands_request_owned_process_group_completion(tmp_path, monkeypatch):
    from oh_goon.integrations import git_source_preparation

    native = git_source_preparation.run_command
    calls = []

    def observe(*arguments, **kwargs):
        calls.append(kwargs)
        return native(*arguments, **kwargs)

    monkeypatch.setattr(git_source_preparation, "run_command", observe)
    _native_source(tmp_path, monkeypatch)
    assert calls
    assert all(call["require_process_group_quiescence"] is True for call in calls)


def test_nonquiescent_worktree_is_retained_without_followup_git_cleanup(tmp_path, monkeypatch):
    source, commit = _native_source(tmp_path, monkeypatch)
    native = GitService.run
    checkouts = []
    removals = []

    def incomplete(self, *arguments, **kwargs):
        if "worktree" in arguments and "remove" in arguments:
            removals.append(arguments)
        result = native(self, *arguments, **kwargs)
        if "worktree" in arguments and "add" in arguments:
            checkouts.append(Path(arguments[-2]))
            return replace(
                result,
                supervisor=CommandSupervisorReceipt(
                    final_process_group_quiescent=False,
                ),
            )
        return result

    monkeypatch.setattr(GitService, "run", incomplete)
    with pytest.raises(FleetPlanInvalid, match="not quiescent") as raised:
        with prepare_git_source(str(source), "main", commit=commit):
            pytest.fail("Nonquiescent source was delivered")
    assert not removals
    assert len(checkouts) == 1
    checkout = checkouts[0]
    assert checkout.is_dir()
    assert (checkout / ".git").is_file()
    assert not (checkout / "sample.txt").exists()
    assert "scratch retained" in raised.value.__notes__[0]
    # The simulated receipt leaves no real process. The test owns removal only
    # after proving that the production boundary retained the uncertain paths.
    repository = next(git_source_cache().glob("*.git"))
    subprocess.run(
        ["git", "--git-dir=" + str(repository), "worktree", "remove", "--force", str(checkout)],
        check=True,
    )
    checkout.parent.rmdir()


@pytest.mark.parametrize(
    "source",
    [
        "https://user:private-token@example.test/repository.git",
        "https://user@example.test/repository.git",
        "https://example.test/repository.git?token=private-token",
        "ssh://git:private-token@example.test/repository.git",
        "git:private-token@example.test:repository.git",
        "ext::sh -c private-token",
    ],
)
def test_credentials_or_external_helpers_fail_before_cache_or_native_git(
    tmp_path, monkeypatch, source
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    run = Mock(side_effect=AssertionError("Rejected coordinates reached native Git"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid) as raised:
        with prepare_git_source(source, "main", commit=None):
            pytest.fail("Unsupported source was prepared")
    assert "private-token" not in str(raised.value)
    run.assert_not_called()
    assert not git_source_cache().exists()


def _filter_source(tmp_path, monkeypatch, *, attributes="tracked"):
    source, _ = _native_source(tmp_path, monkeypatch)
    marker = tmp_path / "filter-executed"
    (source / ".fixture.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "Path(sys.argv[1]).write_text('executed')\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    definition = "sample.txt filter=fixture\n"
    if attributes == "macro":
        definition = "[attr]fixture-contents filter=fixture\nsample.txt fixture-contents\n"
    if attributes != "global":
        (source / ".gitattributes").write_text(definition)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "filter source",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    config = tmp_path / "native.gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    if attributes == "global":
        global_attributes = tmp_path / "native.attributes"
        global_attributes.write_text(definition)
        subprocess.run(
            ["git", "config", "--file", str(config), "core.attributesFile", str(global_attributes)],
            check=True,
        )
    # A missing filter is native passthrough. This valid baseline proves the
    # selected revision installs before configuring the executable mutation.
    with prepare_git_source(str(source), "main", commit=commit) as (root, selected):
        assert selected == commit
        assert (root / "sample.txt").read_text() == "captured source\n"
    return source, commit, marker, config


@pytest.mark.parametrize("operation", ["smudge", "process", "required"])
@pytest.mark.parametrize("attributes", ["tracked", "macro", "global"])
def test_selected_checkout_filter_refuses_before_project_code_or_materialization(
    tmp_path, monkeypatch, operation, attributes
):
    source, commit, marker, config = _filter_source(tmp_path, monkeypatch, attributes=attributes)
    import shlex
    import sys

    value = (
        "true"
        if operation == "required"
        else shlex.join([sys.executable, ".fixture.py", str(marker)])
    )
    subprocess.run(
        ["git", "config", "--file", str(config), f"filter.fixture.{operation}", value], check=True
    )
    calls = []
    native = GitService.run

    def observe(self, *arguments, **kwargs):
        calls.append(arguments)
        return native(self, *arguments, **kwargs)

    monkeypatch.setattr(GitService, "run", observe)
    with pytest.raises(FleetPlanInvalid, match="checkout filter"):
        with prepare_git_source(str(source), "main", commit=commit):
            pytest.fail("Acquisition executed a selected checkout filter")
    assert not marker.exists()
    assert not any("checkout-index" in call for call in calls)
    assert not next(git_source_cache().glob("*.git")).joinpath("worktrees").exists()


def test_native_non_checkout_filters_and_credentials_configuration_are_preserved(
    tmp_path, monkeypatch
):
    source, commit, marker, config = _filter_source(tmp_path, monkeypatch)
    import shlex
    import sys

    command = shlex.join([sys.executable, ".fixture.py", str(marker)])
    for operation, value in (("smudge", command), ("process", command), ("required", "true")):
        subprocess.run(
            ["git", "config", "--file", str(config), f"filter.unused.{operation}", value],
            check=True,
        )
    subprocess.run(
        ["git", "config", "--file", str(config), "filter.fixture.clean", command], check=True
    )
    subprocess.run(
        ["git", "config", "--file", str(config), "filter.fixture.required", "true"], check=True
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.fixture.required")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    subprocess.run(
        ["git", "config", "--file", str(config), "credential.helper", "fixture-auth"], check=True
    )
    with prepare_git_source(str(source), "main", commit=commit) as (root, _):
        assert (root / "sample.txt").read_text() == "captured source\n"
        helper = subprocess.check_output(
            ["git", "-C", str(root), "config", "--get", "credential.helper"], text=True
        ).strip()
        assert helper == "fixture-auth"
        assert (
            subprocess.check_output(
                ["git", "-C", str(root), "config", "--get", "filter.unused.smudge"], text=True
            ).strip()
            == command
        )
    assert not marker.exists()


def test_source_index_operations_disable_fsmonitor_without_replacing_native_configuration(
    tmp_path, monkeypatch
):
    import shlex
    import sys

    source, commit, marker, config = _filter_source(tmp_path, monkeypatch)
    command = shlex.join([sys.executable, str(source / ".fixture.py"), str(marker)])
    subprocess.run(["git", "config", "--file", str(config), "core.fsmonitor", command], check=True)
    # Prove this native setting is executable for the selected indexed source.
    subprocess.run(["git", "-C", str(source), "ls-files"], check=True, capture_output=True)
    assert marker.read_text() == "executed"
    marker.unlink()
    with prepare_git_source(str(source), "main", commit=commit) as (root, _):
        assert (root / "sample.txt").read_text() == "captured source\n"
        assert (
            subprocess.check_output(
                ["git", "-C", str(root), "config", "--get", "core.fsmonitor"], text=True
            ).strip()
            == command
        )
    assert not marker.exists()


def test_ambient_repository_selection_cannot_redirect_source_index_operations(
    tmp_path, monkeypatch
):
    source, commit = _native_source(tmp_path, monkeypatch)
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    (other / "untouched.txt").write_text("keep ambient worktree\n")
    subprocess.run(["git", "-C", str(other), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(other),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "ambient repository",
        ],
        check=True,
    )
    index = other / ".git/index"
    before = index.read_bytes()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    with prepare_git_source(str(source), "main", commit=commit) as (root, selected):
        assert selected == commit
        assert (root / "sample.txt").read_text() == "captured source\n"
    assert index.read_bytes() == before
    assert (other / "untouched.txt").read_text() == "keep ambient worktree\n"
