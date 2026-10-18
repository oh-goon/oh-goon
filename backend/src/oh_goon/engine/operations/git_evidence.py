from __future__ import annotations

from oh_goon.core.models.operation import (
    ChangedGitHead,
    DetachedGitPosition,
    GitMutationResult,
    GitPosition,
    NamedGitPosition,
    PreservedGitHead,
    UnavailableGitPosition,
    ValidGitWorktreeInvariant,
    ViolatedGitWorktreeInvariant,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.vocabulary import GitBranchState, GitFinalState


def git_position(branch_state: GitBranchState, branch: str | None) -> GitPosition:
    if branch_state is GitBranchState.NAMED:
        if branch is None:
            raise ValueError("Named Git position requires a branch name")
        return NamedGitPosition(state="named", branch=branch)
    if branch_state is GitBranchState.DETACHED:
        return DetachedGitPosition(state="detached")
    return UnavailableGitPosition(state="unavailable")


def mutation_git_result(
    before: RepositoryStateFingerprint,
    after: RepositoryStateFingerprint,
    *,
    final_state: GitFinalState,
    unexpected_files: tuple[str, ...] = (),
) -> GitMutationResult:
    """Build one structurally valid durable Git mutation fact from coherent snapshots."""
    position = git_position(after.branch_state, after.branch)

    head = (
        PreservedGitHead(state="preserved", head=before.head_sha)
        if before.head_sha == after.head_sha
        else ChangedGitHead(state="changed", before=before.head_sha, after=after.head_sha)
    )
    worktree = (
        ViolatedGitWorktreeInvariant(state="failed", unexpected_files=unexpected_files)
        if unexpected_files
        else ValidGitWorktreeInvariant(state="passed")
    )
    return GitMutationResult(
        position=position,
        initial_state=before.worktree_state,
        final_state=final_state,
        head=head,
        worktree=worktree,
    )
