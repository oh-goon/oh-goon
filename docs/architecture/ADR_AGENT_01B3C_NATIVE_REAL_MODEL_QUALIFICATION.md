# ADR — AGENT-01B3C native real-model and Hashmarks qualification

**Decision:** Qualify the existing bounded `local-model-review` through its original
Fleet/SQLite/Game Tape path on ordinary Linux and WSL. Introduce **no new
runtime owner**, model abstraction, security host gate, or agentic orchestrator.

## Canonical ownership

- Resolver and AG-01/AG-02 own exact immutable repository and Skill capture,
  recipient/model selection, handoff and lock identity.
- Fleet admission owns the operator-authorized standard-local Run. Enforcer
  retains its original acceptance semantics.
- The original Step/process supervisor executes real `ollama show`, `ollama
  run`, frozen-source `rg`, bounded cancellation and timeout.
- External **Hashmarks is required** for production repository identity: use
  the exactly locked dependency and its existing adapter. No alternate identity
  code or fake producer is permitted during native qualification.
- SQLite and Game Tape are the only durable outcome and audit projection.
  Model output is untrusted text, never a PASS authority.
- The **new test and shell entrypoint** only orchestrate a qualification run on
  the host; neither participates in production admission or execution.

## Operator entrypoint

From a Linux or WSL shell in a checkout containing `backend/pyproject.toml`
and `backend/uv.lock`:

```bash
# Install/start Ollama and pull the chosen model separately, only when wanted.
ollama serve  # use a separate terminal if a server is not already running
ollama pull qwen3:0.6b

# In the project shell (not the Ollama server terminal):
bash scripts/qualify_agent_local_model.sh qwen3:0.6b
```

The entrypoint checks Linux, `uv`, `git`, `rg`, `ollama` and the explicitly
selected already-installed model. It uses `uv sync --locked --project backend
--group dev` to obtain the **real** Hashmarks wheel, then runs one native pytest
integration against real identity and a real model using original Fleet. It
never substitutes a response fixture, silently pulls a missing model, or treats
missing native tooling as a qualification pass.

To select an output folder, set `OH_GOON_AGENT_QUALIFICATION_OUT` to an absolute
path (default `.qualification/agent01b3c`). The entrypoint writes host/CLI
version context, a JUnit XML and a text test log. A failure is retained as
failure; the final PASS marker is printed only after the native integration
returns zero. The model is *not* assumed to obey the bounded JSON protocol;
malformed real replies are genuine qualification failures to investigate.

## Scope and limits

This checkpoint adds a reproducible native qualification path and does not
pretend that an installed Ollama model or the Hashmarks wheel is available in
a hosted environment. Tests with deterministic CLI model fixtures remain
functional integration tests, not live inference. The first native test proves
full model-selection/search/observation/evaluation lifecycle and persisted
Game Tape after reopening SQLite; it does **not** certify model quality across
prompts or additional tool families. This does not block continued independent
trusted-local functionality work. High-security mode remains opt-in and outside
this work.

## Verification contract

- Hosted: previous Agent integration suites remain passing; native-only test
  skips with explicit explanation; strict mode treats missing Ollama or
  Hashmarks as a **failure**; the shell entrypoint refuses missing Ollama
  before attempting dependency installation.
- Native Linux/WSL: run the exact script above with actual pinned Hashmarks and
  installed local Ollama. Preserve JUnit, version evidence, and journal
  observation. Only this completed run may claim real-model native PASS.
