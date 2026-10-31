# Local storage lifecycle

Oh-Goon treats local storage as owned state, not as a bag of files. Every declared class has one canonical owner, durability/retention semantics, and an owner-specific cleanup authority. A path being local, cached, temporary, generated, or disposable never by itself makes deletion safe.

Inspect the canonical inventory with:

```bash
oh-goon storage status
oh-goon storage status --measure
oh-goon storage status --json
```

`--measure` walks only declared paths and reports logical entry sizes, including
directory and symlink metadata. It streams directory entries and closes traversal
handles on success or error. An unreadable entry or incomplete directory walk
fails measurement rather than returning a smaller apparently complete total.
An absent declared path remains distinct from an unreadable one. Concurrent writes
or cleanup can invalidate an observation; retry after the owning operation settles.

These figures are not allocated filesystem blocks, hardlink-deduplicated physical
usage, or a promise of reclaimable bytes. Classes can be nested:
`application.retained-content` includes its separately listed `application.content-staging`
subtree. Do not add those measurements together as independent physical usage.
Measurement never changes retention or cleanup authority.

The bounded generic maintenance entrypoint is:

```bash
oh-goon storage prune
oh-goon storage prune --yes
```

The first form is a dry refusal/confirmation surface. `--yes` delegates only to owners that can prove disposal is safe now. It may bound completed run logs, reclaim execution-idle run workspaces, ask `FleetContentStore` to remove process-birth-proven orphan staging and reference/lease-unreachable objects, and revoke launch capabilities backed by exact terminal-generation evidence.

It deliberately does **not** generically delete durable application control state, user configuration, executor recovery state, Launch Manifest facts/bindings, self-certification evidence, certification journal state, native uv/npm package-manager caches, or unclassified bytes. Native package-manager caches are not Oh-Goon-owned storage. The cache-specific owner may remove only recognized obsolete Oh-Goon dependency-cache layouts from pre-migration versions.

## Certification state is not dependency cache

Certification control state is a durable repository/schema journal under the certification state owner. uv/npm cache and materialization semantics remain package-manager-owned and outside Oh-Goon storage authority; they are not certification control state or independent certification identity.

## Repeated Runs and retained history

Repeated Runs can reuse identical content-addressed source or model bytes while
still adding distinct Run, reference and immutable Journal records. Changed source
or model content can add new objects. This is different from disposable attempt
scratch: each native environment, home and workspace belongs to its original
preparation/execution lifetime and is removed only after that owner proves settlement.

After Runs settle, inspect the measured inventory and use the confirmed prune
entrypoint for eligible scratch, orphan staging and unreachable content. Referenced
source/model objects, live read leases and durable history remain protected; an
unchanged retained-content footprint can be the correct result. Generic maintenance
does not release historical Run references merely to reach a disk-size target.
If the retained class dominates, its owner needs an explicit retention decision;
removing arbitrary reference files or native cache directories is not that decision.

## Race safety

Different storage classes require different positive proof:

- Run workspaces: SQLite execution admission must prove there is no active execution lease.
- Fleet content staging: the exact creating process-birth identity must be dead; missing or malformed owner metadata is refused.
- Retained content objects: references and leases remain authoritative for garbage collection.
- Launch access capability: the exact launch owner generation must be durably terminal.
- Completed run logs: the canonical count/byte retention policy owns deletion.

If candidate run workspaces are present, their execution-idle check happens before
unrelated log cleanup. An active execution refusal therefore does not first delete
completed logs. Maintenance is not one filesystem transaction: later owner-specific
I/O failures can still leave already completed safe work, so a failed command must
not be interpreted as a rollback or an exact freed-space receipt.

Execution leases and content cleanup distinguish an observed process-birth mismatch or confirmed
kernel process absence from unavailable observation. Permission denial, malformed
process data, a missing/restricted proc view with an existing kernel PID, and an
uncertain kernel lookup do not establish death. Staging with unknown ownership is
refused. An unknown read lease prevents content garbage collection or retention
eviction rather than being silently discarded. A live reference or read lease
continues to protect its object. This is native Linux evidence in the selected
host/process namespace; it does not qualify cross-container or foreign-namespace
process visibility.

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
