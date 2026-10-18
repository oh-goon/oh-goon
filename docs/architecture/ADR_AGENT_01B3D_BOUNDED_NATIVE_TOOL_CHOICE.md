# ADR — AGENT-01B3D: bounded native tool choice under original Fleet Step

**Decision:** Add an explicitly selected `local-model-tools` harness for useful read-only Agent Skills, without changing existing `local-search`, `local-model-search`, or `local-model-review` semantics.

## Why

A repository inspection task sometimes needs to discover paths **before** selecting a literal text search. Forcing every Agent to start with content search turns a useful Agent Skill into a hard-coded search demo. AGENT-01B3D gives the model a small, typed choice of native read-only actions, while preserving the existing Plan/Step execution model and exact frozen source delivery.

## Canonical owners and boundaries

- Resolver/AG-02 freezes repository and Skill source, task, harness, selected Ollama recipient/model, identity, and delivery digest. A harness change changes the execution lock and must be admitted afresh.
- The original Fleet Step executor owns one bounded iteration. It does **not** install a new process runner, journal, model gateway, or task engine. Both model and native tool calls use `process.command_runner`, cancellation and the admitted Step's deadline.
- `core/agent_model_selection.py` owns only the pure JSON grammar and output/prompt budgets. Its validated action is **data, not authority**. Duplicate keys, additional fields, nonprintable/larger queries and unsupported operations are rejected.
- `prepare_frozen_readonly_skill_search` is the existing frozen-source materialization owner, now projecting either native `rg --files -- repo` or native `rg --no-heading --line-number --fixed-strings -- <query> repo` from the *same* AG-02 packet. It does not open the live checkout. The selected harness still requires read-only repository access, zero egress and zero declared other tools.
- The original Game Tape/SQLite owner persists Step result and stdout evidence. A model final summary does not convert a failed native tool or failed model call into success.
- Hashmarks remains the required external repository identity producer; there is no local replacement. Hosted integration tests substitute only the unavailable identity leaf and a deterministic local Ollama reply executable; these are not native inference qualification.

## Contract (selected `local-model-tools` only)

1. First model response is exactly `{"tool":"list_paths"}` or `{"tool":"search_text","query":"literal"}`.
2. The native command runs under the original admitted Step against frozen repository bytes. Empty/no-match result is a valid observation; any unexpected nonzero native status is a failed Step.
3. The local model sees only the frozen Skill/task and at most 2,048 stdout / 512 stderr characters from the previous tool observation. The observation is explicitly untrusted data.
4. After the first observation the model must either return `{"decision":"finish","summary":"text"}` or request exactly one more valid tool via `{"decision":"tool","tool":"list_paths"}` or `{"decision":"tool","tool":"search_text","query":"literal"}`.
5. A second **identical** action is rejected; at most two native tool calls. After the second observation the model must finish. No open-ended loops.
6. Cancellation, failure or Step deadline uses the original lifecycle and results; output/provenance is projected by the original Game Tape.

`local-model-review` remains the older search-only protocol; there is no silent widening of existing workload grants.

## Explicitly not implemented

Arbitrary shell or file reading, path arguments, writes, external services, secrets, broader capabilities, nested strict execution, auto-installing models, high-security host qualification or a general long-running Agent loop. These require independently named workload needs and original-owner support, not reinterpretation of `SKILL.md` text.

## Verification criteria

Real original Fleet admission, frozen delivery, native `rg` commands and SQLite/Game Tape with deterministic local model subprocess replies; permitted one/two-tool sequences; invalid/duplicate/extra JSON refusal; stale lock refusal before running; failed model/tool processes; cancellation during review; observation truncation and no remote disclosure. Native Ollama and pinned Hashmarks Linux/WSL qualification remain **open**, not simulated as PASS.
