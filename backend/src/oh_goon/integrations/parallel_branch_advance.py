"""Advance one dedicated local Git ref and synchronize a detached receiving checkout."""

from __future__ import annotations

import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandOutputLimitExceeded,
    CommandResult,
    CommandSupervisionTerminated,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.engine.git import (
    GitService,
    WorkspaceMetadataGitCommandRunner,
    workspace_metadata_git_environment,
)
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.parallel_candidate_patch import MAX_PARALLEL_PATCH_BYTES
from oh_goon.integrations.source_snapshot import source_snapshot_identity

_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_LOOSE_OBJECT_DIRECTORY = re.compile(r"[0-9a-f]{2}")
_OPERATION = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
_COMMIT_IDENT = {
    "GIT_AUTHOR_NAME": "Oh-Goon",
    "GIT_AUTHOR_EMAIL": "oh-goon@invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "Oh-Goon",
    "GIT_COMMITTER_EMAIL": "oh-goon@invalid",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


@dataclass(frozen=True, slots=True)
class PreparedBranchAdvance:
    ref: str
    expected_tip: str
    commit: str
    tree: str


class _BranchGit:
    def __init__(self, git: GitService) -> None:
        selected = git.command_runner
        if (
            type(selected) is WorkspaceMetadataGitCommandRunner
            and git.observation_runner is selected
        ):
            self.environment = cast(WorkspaceMetadataGitCommandRunner, selected).environment
        elif selected is None and git.observation_runner is None:
            self.environment = workspace_metadata_git_environment()
        else:
            raise FleetPlanInvalid("Branch advancement requires the local native Git owner")
        self.environment.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file")
        self.git = GitService(
            executable=git.executable,
            default_timeout_seconds=git.default_timeout_seconds,
        )

    def run(
        self,
        path: Path,
        *args: str,
        env: Mapping[str, str] | None = None,
        input_data: bytes | None = None,
    ) -> CommandResult:
        try:
            result = self.git.run(
                "--no-replace-objects",
                *args,
                cwd=path,
                env=self.environment if env is None else {**self.environment, **env},
                command_runner=partial(
                    run_command, require_process_group_quiescence=True, input_data=input_data
                ),
                max_capture_bytes_per_stream=64 * 1024,
            )
        except (
            CommandAuthorityTerminatedError,
            CommandOutputLimitExceeded,
            CommandSupervisionTerminated,
            CommandTimeoutExpired,
        ):
            raise StateConflict(
                "Local Git branch operation did not establish complete execution"
            ) from None
        if result.stdout_truncated or result.supervisor.final_process_group_quiescent is not True:
            raise StateConflict("Local Git branch operation did not establish complete execution")
        return result

    def require(self, path: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
        result = self.run(path, *args, env=env)
        if result.returncode != 0:
            raise StateConflict("Local Git branch operation did not complete")
        return result.stdout


def _require_oid(value: str) -> str:
    if _OID.fullmatch(value) is None:
        raise FleetPlanInvalid("Branch advancement requires full native Git object identities")
    return value


def _require_canonical_directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
        raise FleetPlanInvalid("Branch advancement requires a canonical local Git directory")


def _require_scratch(scratch: Path, *repositories: Path) -> None:
    _require_canonical_directory(scratch)
    if any(scratch.is_relative_to(repository) for repository in repositories):
        raise FleetPlanInvalid(
            "Branch advancement requires private scratch outside its repositories"
        )


def _require_storage(git_dir: Path, ref: str | None = None) -> None:
    objects = git_dir / "objects"
    logs = git_dir / "logs"
    for item in (
        objects,
        objects / "info",
        objects / "pack",
        git_dir / "refs",
        logs,
        logs / "HEAD",
        git_dir / "index",
        git_dir / "HEAD",
        git_dir / "config",
        git_dir / "packed-refs",
    ):
        if item.is_symlink():
            raise FleetPlanInvalid("Local Git storage aliases are unsupported")
    if not objects.is_dir() or not (git_dir / "HEAD").is_file():
        raise FleetPlanInvalid("Local Git storage is unavailable")
    if any(
        entry.is_symlink()
        for entry in objects.iterdir()
        if _LOOSE_OBJECT_DIRECTORY.fullmatch(entry.name)
    ):
        raise FleetPlanInvalid("Local Git object storage aliases are unsupported")
    alternates = objects / "info" / "alternates"
    if alternates.is_symlink() or alternates.exists():
        raise FleetPlanInvalid("Local Git storage must own its objects without alternates")
    if ref is not None:
        relative = Path(ref)
        if any(
            (root / part).is_symlink()
            for root in (git_dir, logs)
            for part in (relative, *relative.parents)
        ):
            raise FleetPlanInvalid("Local branch storage aliases are unsupported")
    reflogs = (logs / "HEAD",) if ref is None else (logs / "HEAD", logs / ref)
    for reflog in reflogs:
        try:
            information = reflog.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
            raise FleetPlanInvalid("Local Git reflog aliases are unsupported")


def _require_bare_owner(native: _BranchGit, repository: Path, ref: str) -> None:
    _require_canonical_directory(repository)
    if not ref.startswith("refs/heads/") or not 11 < len(ref) <= 256:
        raise FleetPlanInvalid("Branch advancement requires an exact local branch ref")
    if native.run(repository, "check-ref-format", ref).returncode != 0:
        raise FleetPlanInvalid("Branch advancement requires an exact local branch ref")
    layout = native.require(
        repository,
        "rev-parse",
        "--path-format=absolute",
        "--absolute-git-dir",
        "--git-common-dir",
        "--is-bare-repository",
    )
    if layout.splitlines() != [str(repository), str(repository), "true"]:
        raise FleetPlanInvalid("Branch advancement requires its dedicated bare Git repository")
    _require_storage(repository, ref)
    _require_no_worktrees(native, repository)


def _require_no_worktrees(native: _BranchGit, repository: Path) -> None:
    if native.require(repository, "worktree", "list", "--porcelain", "-z") != (
        f"worktree {repository}\0bare\0\0"
    ):
        raise FleetPlanInvalid("Branch advancement requires a bare owner without linked worktrees")


def _tip(native: _BranchGit, repository: Path, ref: str) -> str:
    symbolic = native.run(repository, "symbolic-ref", "--quiet", "--no-recurse", ref)
    if symbolic.returncode != 1:
        raise StateConflict("Selected local branch is not a direct Git ref")
    return _require_oid(native.require(repository, "show-ref", "--verify", "--hash", ref))


def _require_commit(native: _BranchGit, repository: Path, prepared: PreparedBranchAdvance) -> None:
    for identity in (prepared.expected_tip, prepared.commit, prepared.tree):
        _require_oid(identity)
    parent = native.require(repository, "rev-list", "--parents", "-n", "1", prepared.commit)
    tree = native.require(repository, "rev-parse", "--verify", prepared.commit + "^{tree}")
    if parent.split() != [prepared.commit, prepared.expected_tip] or tree != prepared.tree:
        raise StateConflict("Prepared commit differs from the verified Git tree or required parent")


def _publication_ref(prepared: PreparedBranchAdvance) -> str:
    return f"refs/oh-goon/branch-advances/{prepared.commit}"


def _published(native: _BranchGit, repository: Path, prepared: PreparedBranchAdvance) -> bool:
    witness = _publication_ref(prepared)
    _require_storage(repository, witness)
    result = native.run(repository, "show-ref", "--verify", "--quiet", witness)
    if result.returncode == 1:
        return False
    if result.returncode != 0 or _tip(native, repository, witness) != prepared.commit:
        raise StateConflict("Native branch publication evidence differs from the prepared commit")
    return True


def read_branch_tip(
    git: GitService, *, repository: Path, ref: str, require_current: Callable[[], None]
) -> str:
    native = _BranchGit(git)
    require_current()
    _require_bare_owner(native, repository, ref)
    tip = _tip(native, repository, ref)
    require_current()
    return tip


def _require_published_cut(
    native: _BranchGit, repository: Path, prepared: PreparedBranchAdvance
) -> None:
    _require_bare_owner(native, repository, prepared.ref)
    _require_commit(native, repository, prepared)
    if _tip(native, repository, prepared.ref) != prepared.commit or not _published(
        native, repository, prepared
    ):
        raise StateConflict("Published branch advanced beyond the selected receipt")


def require_published_branch_advance(
    git: GitService,
    *,
    repository: Path,
    prepared: PreparedBranchAdvance,
    require_current: Callable[[], None],
) -> None:
    """Re-prove a pinned local ref, exact commit, and its original publication witness."""
    native = _BranchGit(git)
    require_current()
    _require_published_cut(native, repository, prepared)
    require_current()


def prepare_branch_advance(
    git: GitService,
    *,
    repository: Path,
    ref: str,
    expected_tip: str,
    patch: Path,
    patch_sha256: str,
    verified_tree: str,
    operation_id: str,
    scratch: Path,
    require_current: Callable[[], None],
) -> PreparedBranchAdvance:
    """Build the exact prospective commit without moving the branch or using its index."""
    _require_oid(expected_tip)
    _require_oid(verified_tree)
    _require_scratch(scratch, repository)
    if _OPERATION.fullmatch(operation_id) is None:
        raise FleetPlanInvalid("Invalid branch advancement operation identity")
    native = _BranchGit(git)
    require_current()
    _require_bare_owner(native, repository, ref)
    if _tip(native, repository, ref) != expected_tip:
        raise StateConflict("Selected local branch no longer has the expected tip")
    if (
        native.require(repository, "rev-parse", "--verify", expected_tip + "^{commit}")
        != expected_tip
    ):
        raise StateConflict("Expected branch tip does not directly identify a Git commit")
    if (
        not stat.S_ISREG(patch.lstat().st_mode)
        or not 0 < patch.stat().st_size <= MAX_PARALLEL_PATCH_BYTES
    ):
        raise FleetPlanInvalid(
            "Verified branch advancement patch is unavailable or exceeds its bound"
        )
    digest, size = sha256_file_with_size(patch)
    if digest != patch_sha256 or not 0 < size <= MAX_PARALLEL_PATCH_BYTES:
        raise FleetPlanInvalid("Branch advancement patch differs from its verified digest")
    with TemporaryDirectory(prefix="branch-index-", dir=scratch) as staging:
        index = {"GIT_INDEX_FILE": str(Path(staging) / "index")}
        native.require(repository, "read-tree", expected_tip, env=index)
        native.require(
            repository, "apply", "--cached", "--whitespace=nowarn", "--", str(patch), env=index
        )
        tree = _require_oid(native.require(repository, "write-tree", env=index))
    if tree != verified_tree:
        raise StateConflict("Native patch replay differs from the verified Git tree")
    require_current()
    commit = _require_oid(
        native.require(
            repository,
            "-c",
            "i18n.commitEncoding=UTF-8",
            "commit-tree",
            tree,
            "-p",
            expected_tip,
            "--no-gpg-sign",
            "-m",
            f"Advance verified candidate\n\nOh-Goon operation: {operation_id}",
            env=_COMMIT_IDENT,
        )
    )
    prepared = PreparedBranchAdvance(ref, expected_tip, commit, tree)
    _require_commit(native, repository, prepared)
    require_current()
    return prepared


def publish_branch_advance(
    git: GitService,
    *,
    repository: Path,
    prepared: PreparedBranchAdvance,
    require_current: Callable[[], None],
) -> None:
    """Use native compare-and-swap after preparation is durably recorded by the caller."""
    native = _BranchGit(git)
    require_current()
    _require_bare_owner(native, repository, prepared.ref)
    _require_commit(native, repository, prepared)
    tip = _tip(native, repository, prepared.ref)
    if _published(native, repository, prepared):
        if tip != prepared.commit:
            raise StateConflict("Published local branch changed after its original advancement")
        require_current()
        return
    if tip != prepared.expected_tip:
        raise StateConflict("Selected local branch no longer has the expected tip")
    transaction = (
        "start\n"
        "option no-deref\n"
        f"update {prepared.ref} {prepared.commit} {prepared.expected_tip}\n"
        "option no-deref\n"
        f"create {_publication_ref(prepared)} {prepared.commit}\n"
        "prepare\n"
        "commit\n"
    ).encode()
    require_current()
    _require_no_worktrees(native, repository)
    native.run(repository, "update-ref", "--stdin", input_data=transaction)
    require_current()
    if _tip(native, repository, prepared.ref) != prepared.commit or not _published(
        native, repository, prepared
    ):
        raise StateConflict("Native Git did not publish the exact verified branch advancement")


def _require_detached_checkout(native: _BranchGit, checkout: Path) -> str:
    _require_canonical_directory(checkout)
    storage = checkout / ".git"
    if storage.is_symlink() or not storage.is_dir():
        raise FleetPlanInvalid("Branch advancement requires a separate owning receiving checkout")
    layout = native.require(
        checkout,
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
    )
    if layout.splitlines() != [str(checkout), str(storage), str(storage)]:
        raise FleetPlanInvalid("Branch advancement requires a separate owning receiving checkout")
    _require_storage(storage)
    config = native.run(
        checkout,
        "config",
        "--name-only",
        "--get-regexp",
        r"^(filter\..*\.(clean|smudge|process)|url\..*\.insteadof)$",
    )
    if config.returncode != 1 or config.stdout:
        raise FleetPlanInvalid("Receiving checkout has native filters or local transfer rewrites")
    if native.run(checkout, "symbolic-ref", "--quiet", "--no-recurse", "HEAD").returncode != 1:
        raise StateConflict("Receiving checkout is not detached")
    if native.require(checkout, "status", "--porcelain=v2", "--untracked-files=all", "-z"):
        raise StateConflict("Receiving checkout is not clean")
    if native.require(checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"):
        raise StateConflict("Receiving checkout contains ignored files")
    return _require_oid(native.require(checkout, "rev-parse", "--verify", "HEAD^{commit}"))


def _materialized_source(
    native: _BranchGit, checkout: Path, revision: str, staging: Path
) -> SnapshotIdentity:
    destination = staging / revision
    destination.mkdir()
    index = {"GIT_INDEX_FILE": str(staging / f"{revision}.index")}
    native.require(checkout, "read-tree", revision, env=index)
    native.require(
        checkout,
        "checkout-index",
        "--all",
        "--ignore-skip-worktree-bits",
        f"--prefix={destination}/",
        env=index,
    )
    return source_snapshot_identity(destination, reject_dotenv_state=True)


def require_branch_checkout(
    git: GitService,
    *,
    checkout: Path,
    allowed_tips: tuple[str, ...],
    require_current: Callable[[], None],
    scratch: Path | None = None,
) -> str:
    """Preflight the pinned receiving checkout before advancing the separate ref."""
    native = _BranchGit(git)
    require_current()
    current = _require_detached_checkout(native, checkout)
    if current not in allowed_tips:
        raise StateConflict("Receiving checkout has an unrelated Git revision")
    if scratch is not None:
        _require_scratch(scratch, checkout)
        with TemporaryDirectory(prefix="branch-preflight-", dir=scratch) as staging:
            expected = _materialized_source(native, checkout, current, Path(staging))
            if source_snapshot_identity(checkout, reject_dotenv_state=True) != expected:
                raise StateConflict("Receiving checkout has changes outside native clean status")
    require_current()
    return current


def sync_branch_checkout(
    git: GitService,
    *,
    repository: Path,
    checkout: Path,
    prepared: PreparedBranchAdvance,
    candidate_identity: SnapshotIdentity,
    scratch: Path,
    require_current: Callable[[], None],
    require_checkout_current: Callable[[], None],
) -> None:
    """Move only a separately pinned clean detached checkout to the published commit."""
    if checkout.is_relative_to(repository) or repository.is_relative_to(checkout):
        raise FleetPlanInvalid("Receiving checkout and bare branch owner must be separate")
    _require_scratch(scratch, repository, checkout)
    native = _BranchGit(git)
    require_current()
    require_checkout_current()
    _require_published_cut(native, repository, prepared)
    current = _require_detached_checkout(native, checkout)
    if current not in (prepared.expected_tip, prepared.commit):
        raise StateConflict("Receiving checkout has an unrelated Git revision")
    receiving = source_snapshot_identity(checkout, reject_dotenv_state=True)
    if current == prepared.expected_tip:
        with TemporaryDirectory(prefix="branch-receive-", dir=scratch) as temporary:
            staging = Path(temporary)
            if _materialized_source(native, checkout, current, staging) != receiving:
                raise StateConflict("Receiving checkout has changes outside native clean status")
            require_current()
            require_checkout_current()
            # Use the native local protocol without packfile URI negotiation.
            native.require(
                checkout,
                "-c",
                "fetch.writeCommitGraph=false",
                "-c",
                "protocol.version=0",
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-auto-maintenance",
                "--no-recurse-submodules",
                "--refmap=",
                "--",
                str(repository),
                prepared.commit,
            )
            _require_commit(native, checkout, prepared)
            if (
                _materialized_source(native, checkout, prepared.commit, staging)
                != candidate_identity
            ):
                raise StateConflict("Receiving Git checkout would change the verified source bytes")
        require_current()
        require_checkout_current()
        if _tip(native, repository, prepared.ref) != prepared.commit or (
            _require_detached_checkout(native, checkout) != current
            or source_snapshot_identity(checkout, reject_dotenv_state=True) != receiving
        ):
            raise StateConflict(
                "Receiving checkout or published branch changed before synchronization"
            )
        native.require(
            checkout,
            "switch",
            "--detach",
            "--no-guess",
            "--no-overwrite-ignore",
            "--no-recurse-submodules",
            prepared.commit,
        )
    require_current()
    require_checkout_current()
    _require_commit(native, checkout, prepared)
    if (
        _require_detached_checkout(native, checkout) != prepared.commit
        or source_snapshot_identity(checkout, reject_dotenv_state=True) != candidate_identity
        or _tip(native, repository, prepared.ref) != prepared.commit
        or not _published(native, repository, prepared)
    ):
        raise StateConflict(
            "Receiving checkout or branch could not establish the exact published source"
        )
