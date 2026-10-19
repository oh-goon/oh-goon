# Local storage lifecycle

Oh-Goon treats local storage as owned state, not as a bag of files. Every declared class has one canonical owner, durability/retention semantics, and an owner-specific cleanup authority. A path being local, cached, temporary, generated, or disposable never by itself makes deletion safe.

Inspect the canonical inventory with:

```bash
oh-goon storage status
oh-goon storage status --measure
oh-goon storage status --json
```

`--measure` walks only declared paths and reports current entries/bytes. It does not change retention or cleanup authority.

The bounded generic maintenance entrypoint is:

```bash
oh-goon storage prune
oh-goon storage prune --yes
```

The first form is a dry refusal/confirmation surface. `--yes` delegates only to owners that can prove disposal is safe now. It may bound completed run logs, reclaim execution-idle run workspaces, ask `FleetContentStore` to remove process-birth-proven orphan staging and reference/lease-unreachable objects, and revoke launch capabilities backed by exact terminal-generation evidence.

It deliberately does **not** generically delete durable application control state, user configuration, executor recovery state, Launch Manifest facts/bindings, self-certification evidence, certification journal state, native uv/npm package-manager caches, or unclassified bytes. Native package-manager caches are not Oh-Goon-owned storage. The cache-specific owner may remove only recognized obsolete Oh-Goon dependency-cache layouts from pre-migration versions.

## Certification state is not dependency cache

Certification control state is a durable repository/schema journal under the certification state owner. uv/npm cache and materialization semantics remain package-manager-owned and outside Oh-Goon storage authority; they are not certification control state or independent certification identity.

## Race safety

Different storage classes require different positive proof:

- Run workspaces: SQLite execution admission must prove there is no active execution lease.
- Fleet content staging: the exact creating process-birth identity must be dead; missing or malformed owner metadata is refused.
- Retained content objects: references and leases remain authoritative for garbage collection.
- Launch access capability: the exact launch owner generation must be durably terminal.
- Completed run logs: the canonical count/byte retention policy owns deletion.

SQLite file correctness and logical execution ownership are separate layers. SQLite journal mode is selected conservatively; certification uses rollback `DELETE` journaling under its outer workflow lease. Execution ownership uses short SQLite write transactions plus the exact lease token and process-birth identity. A numeric PID alone, or a short wall-clock timeout, is not sufficient authority to steal or delete live work.

## Remote Skill source cache

`agent_skill_sources` owns `${XDG_CACHE_HOME:-~/.cache}/oh-goon/skill-sources`.
Native Git stores fetched objects and retains selected commits under its refs;
`goon.lock` owns the chosen revisions. Native Git worktree registration protects
objects during capture, and each temporary worktree is removed at last use.
Preparation holds an OS file lease while creating/fetching the shared Git store.
Generic pruning does not delete this cache. Explicit cache removal is safe while
no captures are active; the next capture fetches the locked commits again.
Cache location and contents are never receipts or dependency selectors.
