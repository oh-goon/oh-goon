# ADR CO-AG4 — One canonical execution lock authoring owner

## Context

Plan source inspection, validation and preview work outside Fleet. The direct `oh-goon workflow prepare` command still initialized the application runtime and SQLite before calling `FleetPlanService.lock`, even though `lock` has no Run membership or database-state requirement.

## Decision

Dispatch `lock` before runtime startup but reuse exactly the original `fleet_plans._generate_execution_lock` helper and `execution_lock_persistence.write_execution_lock`. The source graph is captured and resolved once; `build_execution_lock` is the sole digest authority; original atomic writer owns the only file effect. A shared presentation function handles existing service-backed dispatch and the ordinary CLI.

## Constraints

- Do **not** move the file writer into the pure resolver or implement another digest/parser/writer.
- Preserve default lock path and explicit output, error/exit semantics and original public Service API.
- Reject malformed input before any lock file mutation; input binding remains the canonical binder.
- This is file-authoring effect, not Run execution, Enforcer admission, or physical LACE capability.
- Keep stateful Plan preparation/schedule/Run and shared Plan flows on their original owners.
- No dependency/lockfile change and no Hashmarks-owned work.

## Test and non-goals

Compare exact lock bytes/digest with the service, test original owner called once, verify absence of application DB startup, bad-input fail-before-write, and service adapter compatibility. Real native/WSL, signer authority, Gate A/B/C, APP PASS and release are not qualified.
