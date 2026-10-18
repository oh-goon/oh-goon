# ADR — AGENT-01B3G: opt-in bounded read-only Skill loop

## Motivation
A two-action Agent Skill could list the captured checklist and read it, but
would have no remaining tool action to inspect the repository it was asked to
review. A small practical multi-turn loop is useful before enabling writes.

## Decision
Add `local-model-skill-loop` as a distinct immutable, explicitly selected
harness in the existing resolved Step and frozen AG-02 handoff. Reuse the same
canonical model protocol parser, source materialization, command supervisor,
Fleet admission, SQLite journal and Game Tape; do **not** construct a separate
Agent runner. This new selection permits at most four **distinct** read-only
actions with one final evaluation. Earlier selections retain their previous
two-action ceiling and identical tools.

After each tool result, provide the recipient-local model with a bounded
observation plus only 350-character excerpts from earlier tool outputs.
Both current and earlier tool outputs are untrusted data; selected `SKILL.md`
is context, not permission. A fifth action, unsupported action, duplicate action,
non-member path, excessive prompt, malformed model response or tool failure
cannot produce a successful Step. Deadline and cancellation remain with the
existing command runner; a final model summary is an untrusted report.

## Tradeoffs and follow-up
This is **one admitted original Fleet Step**, not a durable multi-Step agent
campaign with independent resume points. For later controlled write/apply/test
work, use explicit authorization and original sequential Steps rather than
broadening this read-only harness into shell access. Real Ollama + pinned
Hashmarks Linux/WSL qualification remains external and must not be fabricated
with hosted model fixtures. No special high-security host gate is introduced.
