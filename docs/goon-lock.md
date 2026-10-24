# goon.lock boundary

`goon.lock` is a generated fingerprint of one exact resolved Oh Goon execution. It is not another authoring format.

Canonical ownership stays deliberately narrow:

- `run.yml` = one selected source, target and typed caller/agent/repair selections.
- `plan.yml` = human-authored intent; selects and orders Goons.
- `goon.yml` = human-authored executable behavior; owns composition, Steps, inputs and requirements.
- resolver = the one canonical interpretation of Plan/Goon execution semantics.
- `goon.lock` = compact frozen execution fingerprint and exact remote Skill source commits.
- ecosystem dependency authority remains external to `goon.lock`: for Oh-Goon Python, `backend/pyproject.toml` owns intent and committed `backend/uv.lock` is native uv's exact resolution authority; `frontend/package-lock.json`, `Cargo.lock`, `go.sum`, etc. remain owned by their ecosystems according to each repository's policy.
- Enforcer/admission = authorization and policy.
- executor = executes the exact already-verified resolved identity.
- Game Tape = execution evidence; its `ResolvedExecutionReference` records the overall digest together with the exact typed Plan/source identity, never lock contents.

## Minimal format

The generated file contains only lock format version, resolved Plan/root digest, ordered top-level Goon reference/digest pairs, optional explicitly relevant external artifact path/digest pairs, optional remote Skill source pins, and the overall digest.

It never contains Step definitions, commands, complete composition trees, resolved inputs, requirements, provenance trees, dependency inventories, lockfile contents, logs, outputs, runtime observations, repository targets, run IDs or timestamps.

If information can be verified through a digest without being duplicated, do not put it in `goon.lock`.

## Hash semantics

One canonical JSON representation with stable dictionary ordering is used for every semantic digest. Semantically significant sequence ordering remains significant. Platform path separators are normalized for external artifact paths. Plan/Goon comments, YAML formatting and display descriptions are not part of their semantic execution fingerprint. Explicit captured external artifacts, including `run.yml`, bind exact bytes and therefore require relocking after any byte change.

An Atomic Goon digest covers its resolved execution-affecting Step semantics and requirements. A Composite Goon digest covers its resolved ordered child composition, transitive execution-affecting Step semantics and requirements. These structures exist only while hashing and are never copied into the lock file.

The Plan/root digest covers root-owned execution policy and executable semantics plus the ordered selected top-level Goon references. Repository-root `goon.yml` is supported as an execution root without inventing a fake Goon reference.

The overall digest covers the lock version, Plan/root digest, ordered Goon reference/digest pairs, ordered external artifact path/digest pairs, and declared remote Skill pins.

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

See `docs/execution-boundary.md` for the execution-family transport and durability invariants.
