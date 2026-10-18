# Repository Path Isolation Hardening

Path handling is part of oh-goon's CI/CD trust boundary. The guarantee is not "the string looked absolute" or "`resolve()` said it was below a directory." Strict execution must prove which repository owns the bytes that cross admission, step, run, and retained-content boundaries.

## Trust statement

For normal CI/CD execution, oh-goon is designed so repository B cannot receive repository A's bytes merely by naming a path, learning a content hash/reference, exploiting a path alias, returning a crafted sandbox archive, or relying on ambient host path discovery.

The guarantee assumes the host kernel, privileged operator, configured isolation backend, and oh-goon-owned state root are trusted. It is not a claim against a malicious root administrator or a compromised kernel. A repository that is itself hostile must request an isolation threat model whose backend can enforce that stronger boundary.

## Phase 803 — Repository ownership travels with the path

Cross-run content-store APIs no longer accept a raw host `Path` plus an independent `owner_repository_id`. Publication and materialization require `RepositoryScopedPath`, which binds the repository owner, ownership root, and concrete path as one capability.

This removes a dangerous call shape:

```text
put_tree(path_from_repo_b, owner_repository_id=repo_a)
```

The owner cannot be re-paired at a call site. Publication proves the source tree before and after snapshotting. Materialization derives authorization from the destination capability, resolves only that repository's opaque `ref-...`, and proves the resulting destination tree before returning.

Physical objects may deduplicate globally by SHA-256. Authorization never does.

## Phase 804 — Re-prove the attempt before host-side reads

A sandbox path and a host path do not necessarily interpret a symlink the same way. Therefore strict execution does not trust the workspace merely because process launch was isolated.

Immediately after the child process exits, before host-side Git observation or output/artifact publication, the attempt re-proves its workspace and cache trees. Escaping symlinks, nested mounts, filesystem crossings, special entries, and multiply-linked regular files fail closed.

This ordering is deliberate:

```text
isolated process exits
        ↓
prove workspace/cache ownership
        ↓
host-side Git observation
        ↓
read declared outputs/artifacts
        ↓
publish explicit workspace/cache lineage
```

Host-side code never gets a chance to follow a path whose ownership changed during the step.

## Phase 805 — Remote archives are untrusted input until proven

OpenSandbox result archives are not extracted directly over a Fleet workspace. Archive member names first pass the central repository-relative grammar. Absolute names, `..`, platform-dependent separators, and duplicate paths are invalid.

Extraction happens into a private staging tree using Python's data filter. The completed tree then passes the same owned-tree proof used by local strict execution. Hard links, escaping symlinks, special entries, and nested mount edges are rejected. Only a fully accepted staging tree can replace the previous destination.

A malformed remote result therefore cannot partially overwrite the current workspace before validation fails.

## Phase 806 — Path syntax is compiled once

Resolved execution paths share one grammar from `RepositoryPathSpace` / `repository_relative_parts`:

- repository-relative only;
- POSIX `/` separators;
- no `..` traversal;
- no absolute/drive paths;
- no non-canonical aliases.

That grammar covers working directories, path conditions, artifact paths, and published-output paths before admission. Runtime resolves those already-validated values against the exact repository/attempt path space to catch live symlink topology.

Authoring navigation is intentionally a separate domain. A module in `plans/nightly.yml` may reference `../modules/quality.yml` only when `ContainedPathSpace` proves the target remains inside the declared authoring workspace. User-selected source locations may use the centralized `navigation_path()` convenience, including `~`; execution authority never does.

This prevents two common mistakes: making execution traversal too permissive because authoring needs sibling navigation, or making authoring unusably strict and then adding local bypasses.

## Phase 807 — Path rules are executable architecture

`scripts/architecture/path_isolation_contract.py` rejects drift back toward local path interpretation. It requires the two direct path owners—`core/path_authority.py` for trust/containment semantics and `core/path_layouts.py` for deterministic filesystem layout—plus the repository-scoped retained-content API, no-follow snapshot reads, ownership re-proof before host Git observation, exact deployment path authority, and regression tests for traversal, aliases, nested mounts, hard links, raw content-store paths, foreign retained references, and hostile archives.

The rule for future code is:

> If a path can influence execution or move bytes across an ownership boundary, do not accept/reconstruct a generic `Path` where a narrower path capability can carry the invariant instead.

Read-only source navigation may be convenient. Execution authority must be explicit and typed.

## Filesystem invariants for strict repository sources

Before `RUN_ADMITTED`, selected repository roots are proven as a set. Before the exact source snapshot, the tree is proven again. Strict source trees reject:

- overlapping, nested, or canonical-alias repository roots;
- escaping symlinks;
- nested Git repositories/submodules;
- nested mount points, including same-filesystem bind mounts visible through `/proc/self/mountinfo`;
- cross-filesystem entries;
- sockets, devices, FIFOs, and other unsupported special entries;
- multiply-linked regular files.

Linux mount topology is part of the proof. If oh-goon cannot read/interpret the required mount information for strict CI/CD, it fails closed rather than claiming an isolation guarantee it cannot establish.

## Retained-content authority

A retained cache/artifact has two identities with different meanings:

```text
tree-sha256-... / file-sha256-...   immutable physical/provenance identity
ref-<opaque id>                     repository-owned read authority
```

A plan names only the repository-owned reference. The store resolves that reference inside the current repository's reference namespace and acquires a repository-scoped lease before materialization. Knowing another repository's hash or opaque reference string is insufficient.

## What is intentionally forbidden

Strict Fleet Plan capabilities do not expose arbitrary host read paths, host Docker sockets, or host Docker client configuration. Those capabilities can escape the repository boundary by design and therefore cannot be made compatible with a categorical cross-repository path guarantee merely by making them explicit.

Logical host tools are deployment-owned exact mappings beneath configured non-root `trusted_host_roots`; managed runtimes are frozen and remapped into Fleet-owned `/managed/...` sandbox locations. Network authority remains a separate typed capability such as `cidr_allowlist` and does not grant host filesystem authority.

## Contract versions

Phases 794–802 introduced the durable/wire path-trust changes: Fleet Plan **v22**, executor inventory **v4**, database epoch **47**, external executor report **v3**. Phases 803–807 strengthen internal path capabilities, post-process ordering, archive acceptance, and architecture governance without changing those external contract shapes, so those versions remain unchanged.
