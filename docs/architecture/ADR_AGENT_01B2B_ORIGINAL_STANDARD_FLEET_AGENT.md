# ADR — AGENT-01B2B: original standard-local Fleet Agent delivery

**Status:** accepted for the narrow offline, read-only development candidate (not a release).

## Why

AGENT-01B2A could execute a frozen typed Agent Step directly through the original
Step supervisor, but no operator-selected Fleet run could resolve, admit, dispatch,
or persist that Step in the original run journal and Game Tape. Another Agent
runtime would duplicate canonical owners and create non-comparable results.

## Decision

The existing Fleet Plan Resolver has an explicit, operator-supplied frozen
`local-search` Agent resolution entrypoint. It walks **one** original source
graph, captures repository and Skill inputs via the existing AG-02 source owner,
and resolves the typed Agent Step via the original Step resolution pipeline.
Ordinary resolution still refuses Agent authoring; no SKILL.md grants authority.

The original `goon.lock` must match the complete resolved frozen source identity.
Fleet Plan Service requires an explicitly selected standard-local backend,
repository root, Skill root, acceptance selection, and one selected repository.
The canonical content store retains exact AG-02 packet bytes in a repository-
scoped file reference. The immutable Fleet target holds only that reference,
the Step ID, handoff identity, and packet SHA-256. It cannot omit or relabel a
reference required by a typed Agent Step.

The original Fleet admission lifecycle checks the selected backend, and the
original Fleet runner verifies the retained packet under the selected repository
owner. The original Step runtime requests it at execution time, decodes and
verifies the original AG-02 handoff and launches only the already supported
bounded read-only `rg` command. The existing process supervisor, cancellation,
result classifier, SQLite journal and Game Tape remain authoritative.

No separate Agent process runner, journal, backend selector, approval gate,
telemetry database, execution identity scheme or high-security requirement is
introduced. The original strict CI/CD path remains unsupported for this Agent
capability until explicitly implemented and qualified.

## Scope and consequences

- One operator-approved, offline `local-search` Step; query uses fixed-string
  `rg`, with return codes 0 and 1 both accepted. No arbitrary tools or model
  calls, write, egress, recipients, nested execution or agentic iterations.
- Original `goon.lock` fails closed after either authoring or captured repository
  input changes; new authorization requires a newly generated canonical lock.
- The external Hashmarks leaf can be test-injected for hosted tests. This is
  **not** a substitute for a real pinned-Hashmarks Linux or WSL qualification.
- Failed tools and cancellations retain their ordinary Fleet/Game Tape semantics;
  there is no Agent-specific result classification.
- Retained source packet references follow the original content-store retention
  lifecycle; no separate Agent cleanup or storage implementation is added.

## Verification

See `AGENT01B2B_PROOF.json`, JUnit outputs, new integration tests and
`AGENT_01B2B_README.md`. Tests prove a real admitted Fleet subprocess,
no-match, stale lock refusal, unauthorized/changed scope rejection, typed target
roundtrip, failure, cancellation and SQLite reopen/readback. Native host
qualification with installed Hashmarks and native WSL remains open.
