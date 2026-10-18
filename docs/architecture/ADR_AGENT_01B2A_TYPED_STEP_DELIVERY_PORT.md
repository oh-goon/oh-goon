# ADR — AGENT-01B2A: Typed Agent Step and separate original delivery port

Status: Development decision, not Fleet admission/release.

## Why

AG-02 captured source and AGENT-01B1 transported its immutable packet to the
existing Step adapter, but there was no typed resolved Agent Step execution.
Converting an Agent to `kind: command` would hide the real effect intent.
Embedding the entire frozen source in the resolved Plan/SQLite journal would
also duplicate source transport and expand unnecessary disclosure.

## Decision

* `ResolvedLocalSearchAgentStep` remains `kind: agent` in the one original
  `ResolvedPlanStep` wire union and Plan identity. Inherited command lifecycle
  fields are constrained to a fixed, offline, read-only ripgrep tool.
* The immutable resolved Step carries **only** AG-02 handoff identity and SHA-256
  of the canonical delivery packet. The original admitted Step/attempt will
  supply exact packet bytes through `StepRuntime.agent_delivery_for` once.
  This is a transport port, **not an effect authorization**.
* The original `_execute_typed_step` checks SHA-256, AG-02 decoder, handoff
  identity, exact Step, and timeout, then invokes the **same** `_execute_command_step`
  / process supervisor with materialized frozen source. No live source reread,
  no command from SKILL.md, no second runner or signer.
* Explicit strict `execute_strict_step` calls refuse this incomplete Agent
  backend until it has been positively integrated through original admission.
* Original Fleet Resolver refusal remains intact pending AGENT-01B2B. Future
  standard-local Agent admission must be explicit operator-selected, read-only,
  offline and based on existing authority; unsupported requests fail early.

## Evidence and limits

Real process PASS/no match/CANCEL; forged/corrupt/mismatched bytes/identities;
resolved Plan round-trip retains agent kind without serialized source bytes;
existing Agent/Fleet regressions green. Game Tape and full Fleet admission not
implemented. Native Hashmarks/WSL qualification pending separately.

No high-security gate/host certification added. Optional high-security
backend remains a different capability of the same original lifecycle.
