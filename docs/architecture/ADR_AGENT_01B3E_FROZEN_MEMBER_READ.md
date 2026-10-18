# ADR — AGENT-01B3E: bounded captured-file reading through the original Fleet Step

**Decision:** Add opt-in `local-model-files`, preserving `local-model-tools` and all earlier Agent harness semantics.

## Why

An agent that can list filenames and search a token cannot inspect a selected file's contents as a bounded read-only operation. Add exactly one useful native operation without creating a new execution owner or acquiring the live repository again.

## Owner map

- **AG-01 / AG-02 existing source/handoff:** Captures and verifies all repository members, skill contents and requested effects; freezes digest-bound prework identity.
- **Existing Fleet Plan resolution/admission:** Validates the selected `local-model-files` harness, model recipient and read-only/no-egress effects. `local-model-tools` remains a separate, narrower selection.
- **Pure model-response parser (`core/agent_model_selection.py`):** Parses one strict JSON object, rejects duplicate/unexpected keys, validates a path of form `repo/<exact-member>` with no traversal, and returns inert structured intent. It does not execute or authorize tools.
- **Original Fleet Step execution owner:** Confirms selected path matches an *actual member* of the already-verified repository capture, materializes its immutable bytes via the existing prework context, and projects fixed `head -c 2048 -- repo/<member>` argv into the existing `ResolvedCommandStep` and Step supervisor. No subprocess outside that supervisor.
- **Original result publication/Game Tape:** Records physical exit/cancellation and model evaluation; model text alone cannot establish physical success.

## Contract

Selected harness: `local-model-files`; recipient: `ollama_local`; effects: explicit repository read-only, no network, no writes. New action: `{"tool":"read_file","path":"repo/code.txt"}` or in a review turn `{"decision":"tool","tool":"read_file","path":"repo/code.txt"}`. Existing `list_paths` / `search_text` actions remain available. The bound is **two total tool actions with a required model evaluation after each**, not two calls per tool. A third action or exact repeated action is refused.

The path must be an exact member in the frozen repository capture. Absolute/host paths, `.`/`..` path components, empty components, backslashes, control characters and nonmembers are refused before the file tool launches. No glob expansion or shell invocation occurs. A captured member whose name contains a literal shell metacharacter remains data, not code. There is no ambient repository reopening. `head` is the native text/byte authority, not a custom file-reader implementation.

Only the first **2048 bytes** are read by the process; the normal model observation also limits output and stderr. The captured contents and model reply remain untrusted. No new persistence schema, runner, journal, repository identity implementation or provider is added. Model inference is unchanged and requires installed local Ollama for native qualification.

## Acceptance and limits

Hosted integration uses real Fleet admission, original supervised `head` and `rg` processes, SQLite/Game Tape persistence and deterministic model CLI replies. Verify captured file read, list→read, wrong path, traversal, missing member, model protocol extras/duplicates, repeat budget, third action, and physical output cap. The inherited external Hashmarks test substitution does not qualify a real pinned Hashmarks installation. Real Ollama inference and Linux/WSL pinned-Hashmarks qualification remain open, independent host checks.

Not included: arbitrary commands, dynamic scripts, repository writes, remote recipient/provider, general agent loop, high-security containment or release certification.
