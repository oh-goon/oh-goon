# ADR — AGENT-01B3B bounded local model review through original Fleet Step

**Decision:** Extend the existing `ResolvedLocalSearchAgentStep` protocol with
an explicit `local-model-review` harness; retain AGENT-01B3A semantics for
`local-model-search` and original offline semantics for `local-search`.

## Owners and authority boundaries

- **AG-01/AG-02 source and Resolver:** Freeze the exact repository/Skill/task,
  selected recipient/model/harness, packet identity and digests once. Changing
  the harness changes the bound handoff and Plan identity.
- **Fleet admission/Enforcer:** Select the repository and approved effects and
  execute only with a matching lock through original standard-local Fleet.
  A model response cannot widen approved scope or authorize an invocation.
- **Original Step supervisor/LACE/backend:** Own every `ollama show`, `ollama
  run` and `rg` physical process, cancellation, timeout, output collection and
  resource cleanup. The bounded iteration is a pure orchestration path within
  this owner. No second subprocess backend or Agent-specific journal.
- **Canonical model protocol (`core/agent_model_selection.py`):** Parse JSON
  strictly, reject duplicate keys/extra effects, bound selected fixed strings,
  final summaries and observation disclosure. No process, source or journal I/O.
- **Game Tape:** Projects original Fleet/SQLite status and stdout evidence;
  model summary is informational and cannot turn tool failure into success.
- **Hashmarks:** External repository-identity producer; no duplicate identity
  algorithm is added to the production path. Hosted qualification substitutes
  the external identity leaf only to exercise all other real owners.

## Contract

- At most two repository searches within one selected Step/attempt.
- Initial reply: exact `{"query":"literal"}` only.
- Review reply: exact `{"decision":"finish","summary":"text"}` or, after the
  first tool only, exact `{"decision":"search","query":"literal"}`.
- Failure from an underlying search/model subprocess or rejected model reply
  stops the Step. Search no-match (exit 1) is a valid observation, not failure.
- The model sees frozen `SKILL.md` + task in its initial selection prompt.
  Observation prompts also disclose at most 2,048 stdout / 512 stderr chars
  from a read-only search of **captured**, not live, repository bytes.
- Source observation is untrusted data; no instructions within it may be
  elevated to authority. The local recipient must be selected explicitly.
- Every subprocess is invoked through original `process.command_runner` and
  uses its existing `runtime_supervision`, cancellation and attempt paths.
  The existing Fleet runtime deadline governs the full Step on admitted runs.

## Deliberate non-goals

No arbitrary shell invocation, general multi-tool agent, third search, separate
loop scheduler, remote model access, write authority, extra journal, new host
security gates, automatic model installation, or live neural inference claim.
Optional high-security execution remains separately selected and unsupported
by this trusted-local Agent cut.

## Regression obligations

Cover one/two-search durable Fleet execution, summary stdout evidence hash,
no-match, malformed or duplicate action keys, repeated/third search denial,
physical search failure, missing model, model subprocess failure, source/lock
identity binding, cancellation during review, prompt truncation and forbidden
recipient disclosure. Compare architecture-check findings with the immutable
parent rather than claiming historical unrelated debt was fixed.
