# ADR AGENT-01B3L — Failed-test feedback belongs to original Fleet dataflow

**Historical recipient selection.** `local-model-skill-loop` is removed;
source-bound `local-model-proposal` retains the bounded failed-test feedback
capability. The [current contract](../AGENT_SKILLS.md#failed-test-feedback)
governs supported authoring and result semantics.

**Decision:** The original authored Goon selects a single earlier command Step's `outputs` diagnostic as optional `feedback` to a subsequent `local-model-skill-loop` Agent. Resolve the static Step reference with the same original Plan/Goon owner that resolves command argv; bind the scalar only in the original Step sequencer from published outputs, and disclose it to the originally selected local model as JSON-escaped **UNTRUSTED DATA**. No other Agent harness may receive it.

**Rationale:** AGENT-01B3K transports real failing test diagnostics but had no model recipient. A parallel model-runner, journal, mutable source capture, policy owner or hidden orchestration callback would fragment the original execution authority. The existing typed Agent Step and supervised model/tool calls already own the needed process lifecycle. Canonical ownership is preserved: original Resolver/handoff freeze source/task; original Step publisher owns actual failed test outputs; original sequence binds prior Step references and conditions; original supervisor owns all Ollama and `rg` processes; original Game Tape projects both Step outcomes.

**Contract:** Feedback is an exact prior `steps.<id>.outputs.<name>` reference, only under a matching `FAILED`-only status condition. The producer is operator-authored with `on_failure: continue`, a declared bounded file, and max attempts of one for failure publication. The model receives no diagnostics if the test succeeds, the diagnostic is absent, its value exceeds 2048 UTF-8 bytes, or the bound Step is canceled. All such refusals occur before any Ollama command is launched. Feedback does not change `handoff.task`, frozen source, `delivery_sha256`, admitted capabilities, model recipient or selected tool set. Only the new optional scalar field is bound for one attempt.

**Non-goals:** This is diagnostic interpretation and read-only inspection, not a new source revision, retest, adaptive model proposal, replacement algorithm, automatic retry or release certification. The Plan remains FAILED when an earlier physical test failed, regardless of the Agent's summary.
