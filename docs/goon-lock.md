# goon.lock boundary

`goon.lock` is a generated fingerprint of one exact resolved Oh Goon execution. It is not another authoring format.

Canonical ownership stays deliberately narrow:

- `plan.yml` = human-authored intent; selects and orders Goons.
- `goon.yml` = human-authored executable behavior; owns composition, Steps, inputs and requirements.
- resolver = the one canonical interpretation of Plan/Goon execution semantics.
- `goon.lock` = compact frozen execution fingerprint.
- ecosystem dependency authority remains external to `goon.lock`: for Oh-Goon Python, `backend/pyproject.toml` owns intent and committed `backend/uv.lock` is native uv's exact resolution authority; `frontend/package-lock.json`, `Cargo.lock`, `go.sum`, etc. remain owned by their ecosystems according to each repository's policy.
- Enforcer/admission = authorization and policy.
- executor = executes the exact already-verified resolved identity.
- Game Tape = execution evidence; its `ResolvedExecutionReference` records the overall digest together with the exact typed Plan/source identity, never lock contents.

## Minimal format

The generated file contains only lock format version, resolved Plan/root digest, ordered top-level Goon reference/digest pairs, optional explicitly relevant external artifact path/digest pairs, and the overall digest.

It never contains Step definitions, commands, complete composition trees, resolved inputs, requirements, provenance trees, dependency inventories, lockfile contents, logs, outputs, runtime observations, repository targets, run IDs or timestamps.

If information can be verified through a digest without being duplicated, do not put it in `goon.lock`.

## Hash semantics

One canonical JSON representation with stable dictionary ordering is used for every semantic digest. Semantically significant sequence ordering remains significant. Platform path separators are normalized for external artifact paths. Comments, YAML formatting and display descriptions are not part of the semantic execution fingerprint.

An Atomic Goon digest covers its resolved execution-affecting Step semantics and requirements. A Composite Goon digest covers its resolved ordered child composition, transitive execution-affecting Step semantics and requirements. These structures exist only while hashing and are never copied into the lock file.

The Plan/root digest covers root-owned execution policy and executable semantics plus the ordered selected top-level Goon references. Repository-root `goon.yml` is supported as an execution root without inventing a fake Goon reference.

The overall digest covers the lock version, Plan/root digest, ordered Goon reference/digest pairs, and ordered external artifact path/digest pairs.

## External artifacts

External artifacts are never discovered by repository scanning and the lock is never allowed to decide which artifacts matter. The current 1112 execution model has no separate canonical source-side external-artifact declaration, so generated locks contain no external entries.

The lock format retains the minimal path/digest representation required for an explicitly owned external dependency once real execution semantics provide that fact. Until then, adding an external entry by hand makes the lock stale rather than creating a new source of truth.

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
oh-goon lock path/to/plan.yml
oh-goon lock path/to/goon.yml
```

Generation validates and resolves through the normal resolver and writes `goon.lock` atomically beside the entrypoint unless `--output` is explicitly supplied.

A valid lock proves execution identity/integrity. It does **not** prove the execution is authorized or safe; Enforcer/admission retains that ownership.

See `docs/execution-boundary.md` for the execution-family transport and durability invariants.
