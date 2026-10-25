"""Native Git owns selected source objects; temporary worktrees live only through capture."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from functools import partial
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.git_sources import GoonGitSource, LockedGoonSource
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.engine.git import GitService


def git_source_cache(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get("XDG_CACHE_HOME", "").strip()
    return (
        (Path(base).expanduser() if base else Path.home() / ".cache") / "oh-goon" / "skill-sources"
    )


@contextmanager
def _git_source_scratch(
    *, prefix: str, quiescent: Callable[[], bool], parent: Path | None = None
) -> Iterator[Path]:
    directory = Path(mkdtemp(prefix=prefix, dir=parent))
    failure: BaseException | None = None
    try:
        yield directory
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if quiescent():
            try:
                shutil.rmtree(directory)
            except BaseException as cleanup_failure:
                if failure is None:
                    raise
                failure.add_note(
                    "Git source scratch cleanup also failed: " + type(cleanup_failure).__name__
                )
        elif failure is not None:
            failure.add_note("Git source scratch retained because process cleanup is incomplete")


@contextmanager
def prepare_git_source(
    source: str,
    ref: str,
    *,
    commit: str | None,
    cancel_requested: Callable[[], bool] | None = None,
    native_workspace: Path | None = None,
    allow_network: bool = True,
) -> Iterator[tuple[Path, str]]:
    """Fetch a selector only when authoring; locked use fetches the exact commit.

    Native Git refs retain cached objects. The OS lease covers preparation and
    capture; native worktree removal and the temporary directory close the
    checkout lifetime. Cache bytes are replaceable, never lock authority.
    """
    import fcntl

    try:
        if commit is None:
            GoonGitSource(git=source, ref=ref, path=".")
        else:
            LockedGoonSource(git=source, ref=ref, path=".", commit=commit)
    except ValidationError:
        raise FleetPlanInvalid("Invalid native Git source coordinates") from None
    if native_workspace is not None:
        _reject_native_workspace_source(source, native_workspace)
    cache = git_source_cache()
    if not allow_network and not cache.is_dir():
        raise FleetPlanInvalid("Git source requires explicit acquisition with lock")
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()
    repository = cache / (key + ".git")
    native_command = partial(
        run_command,
        require_process_group_quiescence=True,
        max_capture_bytes_per_stream=1024 * 1024,
    )
    git = GitService(default_timeout_seconds=60, command_runner=native_command)
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    quiescent = True

    def run(
        *args: str,
        check: bool = True,
        cleanup: bool = False,
        cwd: Path | None = None,
        input_data: bytes | None = None,
    ) -> CommandResult:
        nonlocal quiescent
        if not quiescent:
            raise FleetPlanInvalid("Source Git process group is not quiescent")
        try:
            result = git.run(
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "submodule.recurse=false",
                *args,
                cwd=cwd or cache,
                cancel_requested=None if cleanup else cancel_requested,
                env=environment,
                command_runner=partial(native_command, input_data=input_data),
            )
        except BaseException as exc:
            supervisor = getattr(exc, "supervisor", None)
            if supervisor is None:
                supervisor = getattr(exc.__cause__, "supervisor", None)
            if supervisor is not None:
                quiescent = supervisor.final_process_group_quiescent
            raise
        quiescent = result.supervisor.final_process_group_quiescent
        if not quiescent:
            raise FleetPlanInvalid("Source Git process group is not quiescent")
        if check and result.returncode:
            operation = next(argument for argument in args if not argument.startswith("-"))
            raise FleetPlanInvalid(f"Source Git {operation} failed (exit {result.returncode})")
        return result

    # Git owns its repository-context variable set. An enclosing Git process
    # must not redirect our index/object/worktree operations into another owner.
    # Native config/credential parameters retain the caller's selected values.
    for name in run("rev-parse", "--local-env-vars").stdout.splitlines():
        if name.startswith("GIT_") and not name.startswith("GIT_CONFIG"):
            environment.pop(name, None)

    with (cache / (key + ".lock")).open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        try:
            with ExitStack() as staging:
                stored = repository
                if not repository.exists():
                    if not allow_network:
                        raise FleetPlanInvalid("Git source requires explicit acquisition with lock")
                    directory = staging.enter_context(
                        _git_source_scratch(
                            prefix=".prepare-", parent=cache, quiescent=lambda: quiescent
                        )
                    )
                    stored = Path(directory) / "source.git"
                    run("clone", "--bare", "--no-tags", "--template=", "--", source, str(stored))
                git_directory = "--git-dir=" + str(stored)
                if (
                    commit is None
                    or run(
                        git_directory,
                        "cat-file",
                        "-e",
                        commit + "^{commit}",
                        check=False,
                    ).returncode
                ):
                    if not allow_network:
                        raise FleetPlanInvalid(
                            "Git source commit is unavailable locally; acquire it with lock"
                        )
                    run(git_directory, "fetch", "--no-tags", "--", source, commit or ref)
                selected = run(
                    git_directory,
                    "rev-parse",
                    "--verify",
                    (commit or "FETCH_HEAD") + "^{commit}",
                ).stdout.strip()
                if commit is not None and selected != commit:
                    raise FleetPlanInvalid("Source Git commit differs from goon.lock")
                run(git_directory, "update-ref", "refs/oh-goon/commits/" + selected, selected)
                if stored != repository:
                    os.rename(stored, repository)
            capture_failure: BaseException | None = None
            try:
                with _git_source_scratch(
                    prefix="oh-goon-git-", quiescent=lambda: quiescent
                ) as scratch:
                    checkout = Path(scratch) / "checkout"
                    try:
                        run(
                            "--git-dir=" + str(repository),
                            "worktree",
                            "add",
                            "--detach",
                            "--no-checkout",
                            str(checkout),
                            selected,
                        )
                        run("read-tree", "HEAD", cwd=checkout)
                        paths = run("ls-files", "--cached", "-z", cwd=checkout).stdout
                        if "\ufffd" in paths:
                            raise FleetPlanInvalid("Git source paths cannot be inspected exactly")
                        attribute_text = run(
                            "check-attr",
                            "--cached",
                            "--all",
                            "-z",
                            "--stdin",
                            cwd=checkout,
                            input_data=paths.encode("utf-8"),
                        ).stdout
                        attributes = attribute_text.split("\0")
                        if (
                            "\ufffd" in attribute_text
                            or attributes[-1] != ""
                            or (len(attributes) - 1) % 3
                        ):
                            raise FleetPlanInvalid("Git source filter attributes are unavailable")
                        drivers = {
                            attributes[index + 2]
                            for index in range(0, len(attributes) - 1, 3)
                            if attributes[index + 1] == "filter"
                        }
                        if len(drivers) > 128:
                            raise FleetPlanInvalid("Git source exceeds filter inspection bounds")
                        for driver in sorted(drivers):
                            for operation in ("smudge", "process", "required"):
                                setting = run(
                                    "config",
                                    *(["--type=bool"] if operation == "required" else []),
                                    "--get",
                                    f"filter.{driver}.{operation}",
                                    cwd=checkout,
                                    check=False,
                                )
                                if setting.returncode not in {0, 1}:
                                    raise FleetPlanInvalid(
                                        "Git source filter configuration is unavailable"
                                    )
                                if setting.returncode == 0 and (
                                    operation != "required" or setting.stdout == "true"
                                ):
                                    raise FleetPlanInvalid(
                                        "Git source requires a checkout filter; acquisition cannot execute project filters"
                                    )
                        run("checkout-index", "--all", cwd=checkout)
                        # Native worktree registration now retains the objects.
                        # Another declared version may use the same Git store.
                        fcntl.flock(lease, fcntl.LOCK_UN)
                        yield checkout, selected
                    except BaseException as exc:
                        capture_failure = exc
                        raise
                    finally:
                        fcntl.flock(lease, fcntl.LOCK_EX)
                        if checkout.exists() and quiescent:
                            run(
                                "--git-dir=" + str(repository),
                                "worktree",
                                "remove",
                                "--force",
                                str(checkout),
                                cleanup=True,
                            )
            except BaseException as cleanup_failure:
                if capture_failure is not None and cleanup_failure is not capture_failure:
                    capture_failure.add_note(
                        "Git source cleanup also failed: " + type(cleanup_failure).__name__
                    )
                    raise capture_failure from None
                raise
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def _reject_native_workspace_source(source: str, workspace: Path) -> None:
    """Deny agent-editable local Git acquisition without interpreting remote Git URLs.

    Path and file-URI normalization is used only for this denial fence. The
    original source is still passed unchanged to native Git when permitted.
    """
    local = Path(source) if Path(source).is_absolute() else None
    if source.partition(":")[0].lower() == "file":
        try:
            parsed = urlsplit(source)
        except ValueError:
            raise FleetPlanInvalid("Native Skill file-URI source is unsupported") from None
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise FleetPlanInvalid(
                "Native Skill acquisition cannot prove this file-URI source scope"
            )
        local = Path(url2pathname(parsed.path))
        if not local.is_absolute():
            raise FleetPlanInvalid("Native Skill file-URI source must be an absolute local path")
    if local is not None and local.resolve().is_relative_to(workspace.resolve(strict=True)):
        raise FleetPlanInvalid(
            "Native Skill Git source is inside the agent-editable workspace; scoped acquisition is required"
        )
