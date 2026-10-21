# Diagnostics — source ownership and passive sinks

Oh-Goon keeps **execution truth** and **diagnostic evidence** separate.

Journal/Fleet lifecycle, operation evidence and Game Tape remain authoritative for what executed, what state a Run reached, which attempt happened, and whether work succeeded, failed or was cancelled. Diagnostics exist only to explain failures in application/runtime behavior and in non-authoritative observability projections.

```text
semantic source owner
        |
        | emits one typed DiagnosticEvent
        v
  DiagnosticRouter
        |
        +--> StderrDiagnosticSink
        +--> JsonlDiagnosticSink
        +--> future passive sinks
```

## Ownership rule

The **source owns what happened**. The **sink owns only where/how that already-owned fact is represented**.

A source supplies the semantic owner, operation, failure class, affected diagnostic capability, execution impact, and any exact identity it owns: Run, repository, Step or HTTP request.

A sink may format, redact, persist, rotate, aggregate or project that event. It may never:

- change Run or Schedule lifecycle;
- decide retry or cancellation;
- settle an operation/effect;
- admit or schedule work;
- infer execution truth from diagnostics.

`make architecture` enforces the dependency side of this boundary.

## Current event contract

`DiagnosticEvent` is immutable and contains bounded structured fields rather than a prose-only logger call. Relevant fields include:

- event ID and UTC observation time;
- severity, source, owner and operation;
- whether execution itself was affected;
- optional degraded diagnostic capability and failure class;
- exception type plus bounded local traceback evidence;
- optional Fleet Run, repository, Step and server-owned request IDs;
- optional HTTP method/path.

Exception rendering is defensive: a broken exception `__str__` cannot turn diagnostics into a new failure path. Common bearer/auth/query credentials and local-user paths are redacted before message or traceback evidence reaches any sink.

## Current sinks

Production composition installs two passive sinks:

1. `StderrDiagnosticSink` — concise human-readable blocks for the active developer/operator session.
2. `JsonlDiagnosticSink` — private owner-only local history at `<application-state>/diagnostics/diagnostics.jsonl`, bounded by file rotation; directory mode is `0700` and log mode is forced to `0600`.

The JSONL history is **not** a database, event store, lifecycle source or Game Tape replacement. It is disposable debugging evidence. Diagnostic files and run logs are excluded from source identity. `make diagnostics-log` resolves the path through the canonical application-state layout owner and prints the retained JSONL; Make does not reconstruct that path.

The router is fail-open. If one sink fails, later sinks still receive the event and a bounded `DIAGNOSTIC_SINK_FAILED` fallback is attempted on stderr. A sink failure cannot alter the observed command/Run result.

## First migrated sources

The initial closure covers the debugging dead ends with the highest causality loss:

- Fleet live-output `open_run` / `seal_run` degradation;
- Step live stdout/stderr observer/redaction projection failures;
- Game Tape live failure-navigation projection failures;
- Fleet Run supervisor background exceptions;
- Schedule scheduler tick exceptions;
- unexpected HTTP exceptions, correlated to the same `X-Request-ID` returned to the client;
- certification dependency-release cleanup degradation, written into the existing certification session stream without changing the APP result.

These paths keep their existing behavior. For example, a live-output projection failure still leaves the command and Fleet lifecycle untouched; it now also leaves a typed explanation of why observability degraded.

## Relationship to command logs and Game Tape

Configuration explanation is a canonical read projection. The resolver captures
a bounded value-free trace in the target; Journal admission retains it. Game
Tape exposes it with the verified cut's `journal_revision`, canonical status,
terminal scope and existing native execution evidence. The JSON read format
identifies itself as `oh-goon.game-tape/v1`. CLI and UI render these facts; they
do not probe, repair, retry or infer a permission cause from stderr.

Read-only authoring diagnostics can accept or reject before a Run exists. They
create no synthetic Run, Step, process or teardown event. Optional export/sink
failures remain passive diagnostics. Failure of required canonical evidence
retains its lifecycle owner and cannot be swallowed as diagnostic degradation.
Neither JSONL diagnostics nor JSON exports become another authoritative store.

The strong setup/test/certification command logs remain useful and keep their current owner/command/failure-class semantics. They are not converted into execution truth. Saved `test`/`certify` sessions now record completion UTC and duration as well as start, exact command and exit result.

Game Tape remains the one-run execution debugger. It owns durable execution evidence and the short-lived live-output projection. Diagnostic events may point at a Run/repository/Step identity, but they cannot manufacture missing Step output or rewrite Game Tape lifecycle facts.

The next migrations should follow the same rule: replace silent `except ...: pass/return/continue` only when the exception is a real diagnostic degradation, emit from the semantic source, and keep the resulting sink passive.
