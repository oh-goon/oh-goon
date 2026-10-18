# ADR — AGENT-01B3A: one local model request within original Fleet Step

**Status:** accepted for narrow standard-local development candidate only.

## Decision and rationale

The original Fleet Plan already admits and journals the typed frozen Agent
Step. To demonstrate a useful model/tool turn without creating a new engine,
add `harness: local-model-search` as an explicitly selected variant of the
existing frozen read-only Agent Step, with `requested_effects.recipients:
[ollama_local]` and an exact named `model`. The immutable AG-02 packet identity,
resolved Plan, goon.lock and stored delivery digest carry the selection.

The pure core adapter defines only a limited model protocol: a prompt from
frozen task + frozen SKILL.md, and a JSON object selecting one fixed-string
search. The existing original Step executor launches `ollama show`, `ollama
run`, then the existing read-only ripgrep through its **same** command process
supervisor and cancellation lifecycle. The local Ollama recipient is bound to
loopback rather than taking an ambient `OLLAMA_HOST`. A missing local model
fails without pulling it. The model process is not a separate Run/Step or an
authority for outcomes. The Step records one bounded selection and tool result
in original Game Tape; errors and cancellation are ordinary non-PASS outcomes.

This first cut never discloses frozen repository bytes to the model, never
interprets model output as a command, and never infers permissions from
SKILL.md. Model selection does not grant filesystem writes or arbitrary
egress. The local executable and Ollama daemon are trusted operator software;
this is intentionally **not** a hostile-code sandbox.

The existing offline `local-search` path remains available and unchanged in
semantics. Strict/high-security mode continues to refuse these Agent Steps.

## Deferred

- Actual installed-model inference and pinned-Hashmarks native qualification.
- Multi-round model/observation turns, arbitrary tool catalog and write effects.
- General model recipient registry, network endpoint credentials and remote model
  provider support (these require separate typed effects and real admission).

## Regression proof

See `backend/tests/integration/test_agent01b3a_original_fleet_model_turn.py`
for persisted real Fleet tool output, frozen task/Skill disclosure only,
read-only no-match, unapproved/malformed model actions, missing local model,
model identity/lock binding and cancellation while the supervised model
process is alive. The mock CLI has a real PID/process lifecycle but does not
perform neural inference; this boundary is reported unqualified.
