# goon.lock boundary

`goon.lock` is a generated fingerprint of one exact resolved Oh Goon execution. It is not another authoring format.

Canonical ownership stays deliberately narrow:

- `run.yml` = one selected source, target and typed caller/agent/repair selections.
- `plan.yml` = human-authored intent; selects and orders Goons.
- `goon.yml` = human-authored executable behavior; owns composition, Steps, inputs and requirements.
- resolver = the one canonical interpretation of Plan/Goon execution semantics.
- `goon.lock` = compact frozen execution fingerprint and exact remote Goon, Plan, application and Skill source commits.
- ecosystem dependency authority remains external to `goon.lock`: for Oh-Goon Python, `backend/pyproject.toml` owns intent and committed `backend/uv.lock` is native uv's exact resolution authority; `frontend/package-lock.json`, `Cargo.lock`, `go.sum`, etc. remain owned by their ecosystems according to each repository's policy.
- Enforcer/admission = authorization and policy.
- executor = executes the exact already-verified resolved identity.
- Game Tape = execution evidence; its `ResolvedExecutionReference` records the overall digest together with the exact typed Plan/source identity, never lock contents.

## Minimal format

The current generated format is **`lock_version: 2`**. It contains the resolved
Plan/root digest (`plan`), ordered top-level Goon reference/digest pairs
(`execution`), optional explicitly relevant external artifact path/digest pairs
(`external`), optional Git execution-source pins (`execution_sources`), optional
Git Skill collection pins (`skill_sources`), and the overall `digest`.

`execution_sources` is one collection with `kind: goon`, `kind: plan` or
`kind: application`; there is no separate application lock. Earlier lock formats
and the retired `goon_sources` field are not accepted as aliases. Resolve and
lock the authored selection with the current runtime before new admission.

It never contains Step definitions, commands, complete composition trees, resolved inputs, requirements, provenance trees, dependency inventories, lockfile contents, logs, outputs, runtime observations, repository targets, run IDs or timestamps.

If information can be verified through a digest without being duplicated, do not put it in `goon.lock`.

The lock owner bounds generated/read lock files at 4 MiB, separately from the
64 KiB caller `run.yml` limit. This admits up to 128 execution-source pins and
128 Skill collection pins and ordered execution references without publishing
an unreadable next-run lock. Oversized or ambiguous YAML mappings refuse before source acquisition;
generated oversized locks refuse before publication.

## Hash semantics

One canonical JSON representation with stable dictionary ordering is used for every semantic digest. Semantically significant sequence ordering remains significant. Platform path separators are normalized for external artifact paths. Plan/Goon comments, YAML formatting and display descriptions are not part of their semantic execution fingerprint. Explicit captured external artifacts, including `run.yml`, bind exact bytes and therefore require relocking after any byte change.

An Atomic Goon digest covers its resolved execution-affecting Step semantics and requirements. A Composite Goon digest covers its resolved ordered child composition, transitive execution-affecting Step semantics and requirements. These structures exist only while hashing and are never copied into the lock file.

Captured project identity participates in Step semantics for both package-owned
Python Goons and independently selected applications. Implementation, resources,
and applicable native metadata and lockfiles therefore bind execution identity;
generated environments, caches and declared output locations do not. Source
selection alone does not select Python or uv. When managed uv is selected, its
project, dependency group and explicit `frozen` or `locked` mode also bind the
Step. Frozen mode consumes the native lock without adding a metadata-freshness
check; changed captured files still require relocking. This is distinct from
catalog `sha256`, which continues to describe YAML bytes only.

The Plan/root digest covers root-owned execution policy and executable semantics plus the ordered selected top-level Goon references. Repository-root `goon.yml` is supported as an execution root without inventing a fake Goon reference.

The overall digest covers the lock version, Plan/root digest, ordered Goon
reference/digest pairs, ordered external artifact path/digest pairs, and the
declared execution-source and Skill collection pins.

## External artifacts

External identity-bearing artifacts are selected by their original owner, never
discovered by scanning or made authoritative merely by editing the lock. The
captured `run.yml` contributes its exact bytes through the existing external
artifact digest representation. Its selected target, source and invocation
choices therefore change the lock identity without copying those values into
the lock. The config's captured file authority also travels through the original
source-freshness checks. A changed, replaced or missing config refuses before
effects; the receiver does not reinterpret it into a new selection.

## Remote Skills

The adjacent `.goonskills.yml` declares Git source URLs, authored branch/tag/commit
selectors and explicit Skill directories. Native Git resolves them. `skill_sources`
records one entry per collection with `name`, `source`, `ref`, `skills` and exact
`commit`. These small pins are needed to restore the same source after a cache
miss; captured Skill bytes and worktree locations are not stored in the lock.

The resolver transports those resolved pins in `ResolvedFleetPlan`. Lock hashing
consumes that exact representation, including through repair phase Plans. Normal
runs and lock regeneration retain pins. `oh-goon lock run.yml --update-skills` explicitly
resolves selectors again and atomically writes a new lock after successful capture.
Changed declarations require that explicit update. No cache miss can select a
new branch head. See [Agent Skills](AGENT_SKILLS.md) for the configuration.

## Remote Goons and complete examples

A Git import's `from: {git: URL, ref: REF, path: exact/category/name}` selects
one Goon package. A `run.yml` source with `kind: git-plan` additionally names its
authoring root in `path` and exact contained Plan in `plan`. Their
`execution_sources` entries record `kind: goon` or `kind: plan`, `git`, requested
`ref`, selected `path` and resolved `commit`. A Plan entry also records `plan`.
Native Git acquisition is shared with application and Skill sources; it does not
change their execution lifecycle.

The first `lock` explicitly acquires source. Subsequent locking and execution
retain its commit, even when a branch/tag moves or the disposable cache is lost.
Cache recovery may acquire that exact commit; unavailable content refuses and
never advances the pin. `oh-goon lock run.yml --update-goons` explicitly refreshes
declared Goon and Plan pins, subject to the shared-origin rule below.

## Independent application sources

An Atomic Goon can select `application: {path: LOCAL}` or
`application: {git: URL, ref: REF, path: exact/subdirectory}`. The application
needs no Goon files or SDK. Its captured content and provenance bind the same
resolved Step and execution lock. An ordinary local filesystem source binds
captured identity without a Git pin. A Git application adds a
`kind: application` entry to `execution_sources` with `git`,
requested `ref`, selected `path` and resolved `commit`. The path may be `.` or
an exact contained subdirectory, without a Goon catalog layout. A relative
application selection inside Git-owned authoring also retains its original Git
coordinates and commit rather than a disposable checkout path.

Ordinary locking and execution retain application pins after ref movement or
cache loss. `oh-goon lock run.yml --update-applications` explicitly refreshes
application pins. It does not refresh Goon/Plan or Skill pins. Captured source
does not grant effects on the original application, its acquisition cache or
another repository; the actual registered target retains its independent
admission. See [application authoring](development/GOON_AUTHORING.md#independently-maintained-application-source)
and the [public walkthrough](../examples/independent-application/README.md).

## Scoped refresh and shared-origin consistency

Every `execution_sources` selection sharing the same exact `git` origin and
requested `ref` must use one commit, including newly added imports, an explicitly
selected root Plan and application paths. Written import order does not choose
the revision. Duplicate selections, conflicting commits and more than one root
Git Plan are invalid.

Refresh flags select source kinds: `--update-goons` refreshes Goons and Plans,
`--update-applications` refreshes applications, and `--update-skills` refreshes
Skill collections independently. If Goon/Plan and application selections share
an origin/ref, refreshing only one kind refuses rather than advancing or
reinterpreting the retained kind. Deliberately refresh that execution-source
cohort with both flags:

```sh
oh-goon lock run.yml --update-goons --update-applications
```

This shared-commit rule applies to `execution_sources`; Skill collection pins
remain under their separate refresh scope. Changed Skill declarations require
`--update-skills`, and a cache miss never grants any refresh scope.

## Captured source delivery

Agent source identity binds Git-owned Plan and Goon files to their origin,
exact commit and repository-relative path. Disposable checkout locations do not
change that identity. Bounded repair captures each next candidate from fresh
local Plan, Goon and Skill sources, while Git sources retain the original pins.
An independently captured application verifier retains its original source
through candidate recapture rather than following candidate edits or branch
movement.
Native acquisition keeps a source lease through that capture; continuation can
restore an expired checkout or lost cache without reopening a retired path or
silently accepting changed local authoring.

Inspection/validation are cached-only and report acquisition-required source
without fetching. Acquisition captures source without executing project code,
installing dependencies or initializing submodules. The receiver consumes the
captured package rather than reopening an author checkout or refetching a ref.
Native Git inspects indexed attributes before materializing source. A selected
configured smudge/process filter or required filter refuses acquisition; Oh-Goon
does not execute it or substitute unfiltered source. Unrelated native filters,
credentials and configuration remain available to their original Git owner.
Acquisition disables hooks and filesystem-monitor callbacks for its native Git
commands without replacing the caller's credential or connection configuration.
Native Git supplies its repository-context environment-variable names;
acquisition removes inherited index/object/worktree context while preserving
native configuration parameters and credentials.

## Runtime invariant

Locked execution follows exactly this path:

```text
load + validate sources
        ↓
resolve once
        ↓
immutable ResolvedFleetPlan
        ↓
calculate and verify goon.lock identity
        ↓
ExecutionIdentity + FleetPlanTarget
        ↓
admission / Enforcer
        ↓
transport the same verified target through immediate/queued/scheduled lifecycle
        ↓
execute its same ResolvedFleetPlan
        ↓
Game Tape records one structural execution reference
```

Missing, malformed, unsupported, stale or tampered source-adjacent locks fail closed. Oh Goon never silently repairs, regenerates, or falls back to unlocked Plan execution.

Shared Plans are input-parametric control-plane documents, so one persisted source-adjacent lock cannot represent every reviewed invocation. Their preview returns the digest of the same canonical `ExecutionLock`; run and Schedule creation must carry that exact digest back. The backend resolves the same source revision and inputs once, recomputes the canonical lock, and rejects any digest mismatch before admission. This is an explicit reviewed-lock identity fence, not silent lock generation or an unlocked fallback.

Generate or refresh the lock with:

```text
oh-goon lock path/to/run.yml
```

Generation captures the selected `run.yml`, validates and resolves through the
normal resolver, and writes `goon.lock` beside that selection. The config and
selected source identities must still match at admission. Internal certification
and release store locks under their isolated state owner through the Make
frontdoor; that storage choice does not grant unlocked execution.

A valid lock proves execution identity/integrity. It does **not** prove the execution is authorized or safe; Enforcer/admission retains that ownership.

See the [execution boundary](execution-boundary.md) for the execution-family
transport and durability invariants.
