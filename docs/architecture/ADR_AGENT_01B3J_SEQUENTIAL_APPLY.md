# ADR — AGENT-01B3J sequential apply ownership

**Decision:** Reuse one Fleet Step-output publisher and one `oh-goon` CLI command owner.

## Problem

A valid model patch suggestion recorded in Step stdout did not give the next,
separately authorized ordinary Step a type-checked way to consume the bytes.
Storing an Agent proposal in a second journal, adding an Agent runner with
write permission or directly mutating source inside a read-only Agent Step
would duplicate authority and falsely attribute model intent as an effect grant.

## Contract

1. Only a successful `local-model-proposal` Step with the original source-bound
   receipt publishes the reserved `proposal` output. The original attempt
   publication owner records the output digest and strict producer execution
   identity. A skipped/failed/nonproposal Step publishes no such output.
2. The resolved Plan dataflow validator permits the reserved output only for
   that exact typed harness; its existing scalar Step-output binder transports
   the unchanged receipt as one argument of the subsequent explicit command.
3. `oh-goon agent apply` is a subcommand of the sole existing CLI. It does **not**
   consume privileges from the model or run a second Agent. Its caller is an
   ordinary explicit Step whose workspace, backend and write scope were
   already resolved and authorized through the normal Plan/Fleet path.
4. The operation validates strict JSON keys, schema, bound identity, target
   path, size and source digest. Within the existing exact-file cooperating
   lock it checks the actual current revision and commits a prepared file via
   the existing durable exact-file publication mechanism.
5. Later Step(s) run normal tests and evaluations. Only their real process exit
   results determine success. Failure halts downstream Steps by ordinary policy.

## Limits

Whole-file text replacements are bounded at 2 KiB and are restricted to an
already fully inspected frozen file. Proposal publication and apply success
are distinct recorded physical Steps. This checkpoint does not invent an
automatic multi-attempt agent loop, privilege certification or remote provider.
Hashmarks remains canonical for external repository identity; its native wheel
must be present for native qualification. No alternate Hashmarks identity is
introduced in production.
