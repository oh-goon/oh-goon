# ADR — AGENT-01B1 Immutable Agent delivery consumed by original Step preparation

**Decision:** Reuse AG-02's canonical byte delivery and identity verifier at the original AGENT-01A Step preparation boundary. Keep the existing original resolver's Agent refusal until the real admitted Fleet Step integration is implemented.

**Why:** The first real search tool previously took an in-memory `FrozenAgentStepHandoff`. A Fleet worker must eventually receive the original frozen bytes, not reopen a live authoring tree. Reusing `FrozenAgentStepHandoff.decode_prework_delivery` preserves transport integrity, including exact source member hashes and the source/Step identity. No parallel encoding/decoding authority should be added.

**Implementation:** A single context-manager adapter validates `expected_step_id` and decodes the preexisting AG-02 packet against `expected_handoff_identity` before invoking `prepare_frozen_readonly_skill_search`. The projection and subprocess supervisor remain unchanged. Errors occur before staging or launching a process.

**Rejected options:** (1) Defining a second Agent JSON/archive format; (2) rereading live repositories; (3) turning a caller-provided `authorized=True` boolean into a grant; (4) suppressing original Agent Fleet refusal; (5) adding security certification or separate signers. None is necessary for this Step transport integration.

**Not proven:** Actual original Fleet Agent Run admission, selected standard backend consumption, durable Game Tape, source publication/restart recovery, model tool loops, native WSL qualification. AGENT-01B2 owns the next functional integration.
