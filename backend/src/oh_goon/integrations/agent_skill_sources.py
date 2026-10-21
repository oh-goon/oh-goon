"""Native Git owns Skill objects; temporary worktrees live only through capture."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import url2pathname

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService


@dataclass(frozen=True, slots=True)
class PreparedSkill:
    root: Path
    path: str
    source: LockedSkillSource

    @property
    def reference(self) -> str:
        return f"git:{self.source.source}@{self.source.commit}:{self.path}"


def skill_source_cache(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get("XDG_CACHE_HOME", "").strip()
    return (
        (Path(base).expanduser() if base else Path.home() / ".cache") / "oh-goon" / "skill-sources"
    )


@contextmanager
def prepare_skill_source(
    source: str,
    ref: str,
    *,
    commit: str | None,
    cancel_requested: Callable[[], bool] | None = None,
    native_workspace: Path | None = None,
) -> Iterator[tuple[Path, str]]:
    """Fetch a selector only when authoring; locked use fetches the exact commit.

    Native Git refs retain cached objects. The OS lease covers preparation and
    capture; native worktree removal and the temporary directory close the
    checkout lifetime. Cache bytes are replaceable, never lock authority.
    """
    import fcntl

    if native_workspace is not None:
        _reject_native_workspace_source(source, native_workspace)
    cache = skill_source_cache()
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()
    repository = cache / (key + ".git")
    git = GitService(default_timeout_seconds=60)

    def run(*args: str, check: bool = True, cleanup: bool = False) -> CommandResult:
        result = git.run(
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "submodule.recurse=false",
            *args,
            cwd=cache,
            cancel_requested=None if cleanup else cancel_requested,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if check and result.returncode:
            operation = next(argument for argument in args if not argument.startswith("-"))
            raise FleetPlanInvalid(
                f"Skill source Git {operation} failed (exit {result.returncode})"
            )
        return result

    with (cache / (key + ".lock")).open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        try:
            with ExitStack() as staging:
                stored = repository
                if not repository.exists():
                    directory = staging.enter_context(
                        TemporaryDirectory(prefix=".prepare-", dir=cache)
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
                    run(git_directory, "fetch", "--no-tags", "--", source, commit or ref)
                selected = run(
                    git_directory,
                    "rev-parse",
                    "--verify",
                    (commit or "FETCH_HEAD") + "^{commit}",
                ).stdout.strip()
                if commit is not None and selected != commit:
                    raise FleetPlanInvalid("Skill source Git commit differs from goon.lock")
                run(git_directory, "update-ref", "refs/oh-goon/commits/" + selected, selected)
                if stored != repository:
                    os.rename(stored, repository)
            with TemporaryDirectory(prefix="oh-goon-skills-") as scratch:
                checkout = Path(scratch) / "checkout"
                try:
                    run(
                        "--git-dir=" + str(repository),
                        "worktree",
                        "add",
                        "--detach",
                        str(checkout),
                        selected,
                    )
                    # Native worktree registration now retains the objects.
                    # Another declared version may use the same Git store.
                    fcntl.flock(lease, fcntl.LOCK_UN)
                    yield checkout, selected
                finally:
                    fcntl.flock(lease, fcntl.LOCK_EX)
                    if checkout.exists():
                        run(
                            "--git-dir=" + str(repository),
                            "worktree",
                            "remove",
                            "--force",
                            str(checkout),
                            cleanup=True,
                        )
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
