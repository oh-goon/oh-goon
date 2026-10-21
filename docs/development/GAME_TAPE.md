# Game Tape


Historical comparison, performance, and failure analysis receive already-projected `GameTape` values: `FleetRunControlPlane` projects each selected durable run once per request and reuses that exact projection rather than walking the same `FleetRunDetail` again.
Game Tape is oh-goon's read-only execution observability projection. `goon.yml` is the Game Plan; Game Tape shows the admitted plan and the durable facts produced by execution.

## Non-negotiable execution boundary

```text
goon.yml
  -> resolve once
  -> ResolvedFleetPlan
  -> FleetPlanTarget (durable admitted intent)
  -> executor
  -> FleetPlanResult / OperationReport
  -> canonical execution ledger projection
  -> FleetRunDetail.game_tape
  -> Summary | Flow | Timeline | Performance | Play-by-Play | Attempts | Logs/evidence | Search | Run Diff
```

Game Tape never schedules work. A Flow node is not schedulable, a Flow edge is not a dependency, visual grouping is not an execution phase, and there is no DAG/topological execution model behind the view. The order shown by Flow is the exact `ResolvedFleetPlan.steps` order already embedded in the admitted `FleetPlanTarget`.

The browser owns no parallel execution model. `FleetRunDetail.game_tape` is the API projection consumed by every Game Tape view.

## Stable live refresh

Live output is a bounded process-local projection, so the browser advances through it with the backend-owned `next_sequence` cursor instead of refetching the whole retained buffer on every poll. The client merges chunks monotonically by canonical sequence, retains already-rendered rows while they remain inside `retained_from_sequence`, and accepts eviction only when the backend says the retained window moved. Failure anchors still come from the newest immutable live-output cut; React never parses log text to rediscover them.

Read refresh is deliberately non-destructive. When durable failure history refreshes, already-visible data stays rendered until a newer read succeeds instead of flashing back to an empty/loading panel. Game Tape's feature-local tab, comparison baseline and selected-step state are keyed by `fleet_run_id`, so moving to another run cannot inherit an old inspector selection. Numeric execution facts use stable tabular layout and the live-log viewport reserves scrollbar space to reduce visual movement during ordinary polling. None of these presentation rules changes execution state or persistence.

## Failure focus and direct drill-down

The top of Game Tape exposes one bounded backend-owned `issue_focus` derived only from canonical run, repository and step failure facts. It is a navigation projection, not a new health model. Run failures remain run failures; repository failures remain repository failures; failed steps keep the exact `repository_execution_id` and `step_execution_id` already shared by Flow, Timeline, Play-by-Play and Logs/evidence. Step focus may also expose the existing reason code, exit code, failure signature and backend message when those facts are already canonical.

Ordering is deterministic rather than interpretive: run-level failure first, then repository/step plan order. At most five focus items are rendered at the top and the response reports how many additional failures are hidden. Game Tape does **not** infer severity, confidence, root cause, flakiness or importance scores. Selecting a step focus uses its exact `step_execution_id` to open the existing Logs/evidence view, so drill-down is direct without a second search, fuzzy matching or browser-owned failure index.

## Canonical execution ledger

Game Tape has one chronological execution-ledger **projection**, not another persisted event store. `project_execution_ledger()` orders facts already durably owned by the run lifecycle, admitted `FleetPlanTarget`, `OperationReport`, and `PlanStepExecution`. It does not append journal events, mutate execution, infer log state, or create a second source of truth.

The ledger vocabulary names observable facts such as run admission/start/terminal outcome, repository start/terminal outcome, and step reached/started/passed/failed/skipped/not-reached. Equal-timestamp ordering is deterministic and preserves resolved step order, so a previous step terminal event remains before the next step being reached.

`game_tape_lifecycle.py` is the one owner that maps durable run/repository/step facts to `GameTapeStatus`. Summary, Flow, Timeline, Play-by-Play, receipt, Search, and Run Diff consume that same meaning rather than reinterpreting statuses independently.

## Canonical identities

One optional `ResolvedExecutionReference` binds Plan ID/name, typed `FleetPlanSource`, source SHA and verified `ExecutionIdentity` as a single structural fact. Game Tape cannot carry an execution digest without the source identity that was admitted with it.

One stable `repository_execution_id` identifies a repository lane for a Fleet run. One stable `step_execution_id` identifies a resolved plan step within that repository execution. Flow, Timeline, Play-by-Play, Logs/evidence, Search, and the shared inspector all select the same `GameTapeStepExecution`; there are no separate graph, timeline, ledger-step, or log-step models.

## Lifecycle meanings

Game Tape uses explicit observability states:

- `PLANNED`: present in the admitted plan but the containing repository execution has not produced a durable result yet.
- `ADMITTED`: the Fleet run is queued/admitted.
- `RUNNING`: the Fleet run is active.
- `PASSED`: a defined successful execution result exists.
- `ATTENTION_REQUIRED`: the canonical operation completed with a non-failing attention verdict.
- `FAILED`: execution produced a defined failing result.
- `CANCELLED`: the run was explicitly cancelled.
- `INTERRUPTED`: recovery says the run ended without a valid normal terminal execution outcome.
- `NOT_RUN`: the step was in the admitted plan but no step execution result exists after the containing run/repository became terminal.
- `SKIPPED`: the executor durably recorded an explicit skip outcome.

Game Tape does not infer lifecycle state from log text.

## Timing

Fleet plan step evidence records `started_at` and `finished_at` at the step-sequence owner. Timeline projects those exact wall-clock facts. The durable shape change advances the pre-live database epoch to 69, so stale local history is rejected rather than silently decoded without timing. Planned/not-run steps can still legitimately have no timestamps; Game Tape never synthesizes fake ones.

## Provenance and secrets

The Configuration explanation disclosure renders the resolver's retained,
value-free decision trace. It preserves root versus imported occurrence scope,
input type/state, default/caller/binding/source selection, structural parent and
producer edges, source digests/marks and observed candidate outcomes. It never
reopens source or reads current environment values. The trace is bounded to 4096
entries with an explicit omitted count; presentation pages through the retained
entries without filtering admitted work. Older evidence without a trace is
explicitly unavailable. No raw input/default/environment values are added to this
view. `plan tape RUN_ID --json` and `dev explain-run RUN_ID --json` expose the
same backend projection with schema `oh-goon.game-tape/v1`, the verified read
cut's Journal revision and whether the canonical Run is terminal.

The shared inspector exposes command, working directory, exit code, attempt count, declared output names, and safe environment provenance. Host values are represented as `host:NAME`; step outputs as `step:STEP.OUTPUT`; literal sources are represented only as `literal`. Literal and host secret values are never copied into the Game Tape payload.

## Developer execution explorer

Game Tape now exposes the debugging facts that the engine already owns instead of creating a second interpretation path.

### Why did this run?

Every projected step carries a small `GameTapeWhy` fact: the admitted plan source/order, the previous projected step outcome when one exists, the declared condition when one exists, the admitted repository requirement count, whether durable execution evidence proves the step was reached, and a plain explanation. This is not a condition evaluator in the UI. An executed step is known to have passed the executor's reachability gates because canonical step evidence exists; a not-run step remains not-run.

The canonical source snapshot now records each authored YAML step mapping's 1-based line/column from the **same bytes already parsed and hashed**. Template expansion carries those positions into `ResolvedFleetPlan.step_sources`, using only safe plan-relative/package labels. Game Tape projects that fact as `source:line:column`; it never reopens or reparses YAML. Source position is explainability metadata and is deliberately excluded from semantic `plan_id`, so comments or blank-line movement cannot change execution identity.

### Plan vs Actual and execution receipt

Summary renders the admitted ordered steps beside their canonical actual outcome, making `PASSED`, `FAILED`, `SKIPPED`, and `NOT_RUN` differences visible without a separate workflow model. The receipt is derived from the same run/plan facts and records validation/admission, requirements planned/admitted, steps planned/executed/succeeded, failed step identities, and run timestamps. The product guarantees remain explicit: no hidden retries, no automatic reordering, and no undeclared steps. Explicit retry evidence, when configured by the plan, remains visible through the canonical attempt count.

### Environment fingerprint

Per-repository Game Tape fingerprints reuse the existing operation provenance and strict execution-input evidence: repository head/branch when captured, plan-source SHA, Fleet Plan contract version, execution-input identity, requirements validation identity, base-workspace/runtime-root hashes, host-toolchain identity, isolation backend/configuration/kernel-boundary facts, remote/experimental flags, declared host-environment names, managed/content input counts, and observed host-tool byte fingerprints.

Where the admitted plan itself declares a managed runtime version, Game Tape also exposes a small human-readable runtime fact alongside the cryptographic identities. Today this includes the managed Python/pre-commit versions already present on resolved pre-commit steps. Game Tape still does **not** probe the current host or convert executable hashes/backend IDs into invented version strings; unknown historical versions remain unavailable.

### Deterministic bottleneck insight

The repository projection identifies the slowest recorded step and its percentage of the sum of recorded step durations. This is a transparent arithmetic projection only; there is no opaque/AI performance interpretation.

### Historical performance baseline

The Performance view is an on-demand read projection over earlier durable Game Tape facts. It does not persist aggregates, create a metrics table, maintain a cache, or run an anomaly/AI model. `FleetRunControlPlane` reads the existing chronological run details and `project_game_tape_performance()` applies one explicit comparison rule:

- only earlier runs with the same resolved `plan_id` are candidates,
- run totals additionally require the same repository set,
- repository timing requires the same repository identity,
- step timing requires the same step identity,
- baseline timing samples must have a canonical `PASSED` outcome and a recorded duration,
- newest matching samples are used first, up to the requested sample limit,
- the response exposes the exact sample run IDs, sample count, median, minimum and maximum,
- `delta_ms` is current duration minus baseline median and `delta_percent` is that exact arithmetic ratio rounded to a whole percent.

A positive delta is labelled `SLOWER`, a negative delta `FASTER`, zero `SAME`, and missing current/baseline timing `NO_BASELINE`. There is deliberately no hidden threshold for deciding whether a slowdown is "significant". The largest repository slowdown is simply the step with the largest positive percentage against its own baseline.

The browser localizes and renders these facts but does not choose comparison runs or recalculate execution semantics. Selecting a Performance step reuses the same `step_execution_id` and shared inspector/evidence navigation as Flow, Timeline and Play-by-Play.

### Run Diff

`compare_game_tapes()` compares exactly two canonical Game Tape projections. It is deliberately narrow: run/plan identity and duration, repository commit/branch, per-step status/duration, exact-command change, and safe provenance-source change. The API does not expose a generic arbitrary JSON diff framework, and the browser does not recreate execution semantics to compare runs.

### Search

Run-level Search filters the authoritative Game Tape payload in memory. It covers step identity/kind/status, exact commands, safe environment source names, declared outputs, `why` facts, failure fingerprints, and repository requirements. While a run is active, it also searches the currently retained bounded transient output chunks directly. There is no persisted search index or second cache/source of truth. Historical raw-log text remains absent because transient stdout/stderr is discarded when execution ends.

### Failure fingerprint and evidence navigation

A failed step exposes its canonical exit code and operation reason code plus a preferred durable stdout/stderr evidence anchor: stream, SHA-256, line count, and byte count. When canonical physical-attempt evidence exists, that anchor comes from the actual final attempt and records the attempt number. One deterministic `game-tape-failure-*` signature identifies the exact exit/reason/stream/hash fact set; it is an evidence identity, not an inferred root-cause classification. The shared inspector can navigate directly to the selected step in Logs/evidence. This is a navigation/debugging fingerprint, not a replacement for raw logs. Because historical raw bytes and source-line extraction are not durable facts, Game Tape explicitly says they are unavailable instead of fabricating a first-failure line.

### Physical attempts and reproduction facts

Game Tape no longer reduces explicit retries to only an attempt count. `GameTapeStepExecution.attempts` reuses the canonical durable `PlanStepAttemptEvidence` objects already produced by execution. The Attempts view can therefore show, per physical attempt, exact canonical argv, return code, duration, stdout/stderr hashes, repository-state before/after, observed executable bytes, effective egress evidence, enforced/measured resource evidence, cache identities, retry disposition, and release facts where the underlying executor recorded them.

This remains observability, not replay. Game Tape does **not** create a reproduction recipe database, reconstruct environment values, fetch historical repository bytes, or execute the command again. The reproduction panel simply places the existing exact command/working-directory facts next to immutable repository/plan/workspace/runtime/toolchain/isolation identities and safe environment source names. A developer can use those facts to reproduce work manually and verify whether the important identities match; Game Tape never claims two executions are equivalent merely because a command string is equal.

Explicit retries remain explicit. Each physical attempt and its canonical retry disposition are visible, so “no hidden retries” means exactly that: Game Tape never invents an attempt, and configured retry behavior cannot disappear behind a single final status.

Adjacent retries are compared once in `game_tape_attempt_comparison.py`. `GameTapeAttemptTransition` reports only direct physical differences between attempt N and N+1: whether the recorded retry decision explicitly links them, duration/exit/argv changes, repository-state continuity, executable/egress/resource/cache changes, and stdout/stderr evidence-hash changes. The browser renders those facts; it does not derive retry semantics itself.

### Exact failure history

When a selected step has a deterministic failure signature, Game Tape can scan earlier durable run details on demand and answer whether that **exact evidence identity** was seen before. A historical occurrence is eligible only when the admitted `plan_id`, repository identity, step identity, and `game-tape-failure-*` signature are identical. The signature already binds exit code, operation reason, preferred evidence stream, and output SHA-256. There is no fuzzy text matching, log embedding, stack-trace normalization, or root-cause classifier.

Each exact occurrence also compares the current and historical reproduction context using three-state facts: `MATCH`, `DIFFERENT`, or `UNKNOWN`. Workspace, runtime-root, host-toolchain, isolation identity, and exact command are compared only when both sides recorded the relevant identity. Missing evidence remains `UNKNOWN`; two missing facts never become a claimed match. This gives developers a useful “same failure under the same recorded physical context?” check without replaying either run or reconstructing missing bytes.

Failure history uses the same chronological Journal read view as historical performance and is calculated on demand. It does not persist clusters, maintain a diagnostics/search database, or turn a repeated signature into a causal claim.

### Failure evolution and later-pass evidence

When a historical failed run is inspected, the same verified Journal read window can also contain later runs. Game Tape uses that existing cut to report exact recurrence facts without creating a lifecycle or incident model: exact occurrence count, prior/later occurrence counts, first-seen and last-seen run identities/timestamps, and the number of later comparable runs. The current failed run itself is always one exact occurrence.

A **later comparable pass** means only this: a later run has the same admitted `plan_id`, the same repository identity, the same step identity, and that step's canonical Game Tape status is `PASSED`. Game Tape may show the first such run and compare its recorded workspace/runtime/toolchain/isolation/command identities with the failed run. It may also report whether the exact failure signature appears again after that pass.

Those facts deliberately do **not** mean "fixed", "recovered", "flaky", or "root cause found". A later pass proves only that the comparable step later passed. An exact recurrence after a pass proves only that the same deterministic failure evidence identity appeared again. No causal or remediation statement is inferred.

### Reproduction-context evidence

`game_tape_reproduction_context.py` is the one owner for comparing the five durable reproduction dimensions used by failure history: base workspace, runtime root, host toolchain, isolation identity, and the admitted step command. Each dimension remains `MATCH`, `DIFFERENT`, or `UNKNOWN`; the backend then derives exactly one summary: `SAME_CONTEXT` only when all five dimensions are known and match, `CHANGED_CONTEXT` when at least one recorded dimension differs, and `INCOMPLETE_CONTEXT` when no difference is recorded but at least one dimension is missing.

Failure evolution aggregates those same backend-owned summaries for earlier exact failures, later exact failures, and later comparable passes. These counts answer whether the same deterministic failure or a later pass was observed under the same complete recorded context, under a recorded changed context, or with incomplete context evidence. The browser does not reclassify the five fields itself.

This is correlation evidence only. A later pass in a changed context does not prove the context change caused the pass; an exact recurrence in the same context does not prove a deterministic root cause. Missing identities stay incomplete rather than becoming a guessed match.

### Comparable failure sequence

Failure Evidence also exposes one bounded chronological sequence around the selected failed run. `game_tape_failure_analysis.py` owns one canonical analysis of the verified Journal window for the request: it validates the selected failure, selects same-plan/repository/step comparable entries once, and computes each selected-relative failure-signature relation and reproduction-context comparison once. Exact failure history, evolution, sequence and the selected-context window consume those same typed facts; they do not walk or reinterpret the historical window again. There is no separate incident/trend selector in the browser.

The visible sequence contains at most the nearest `sample_limit` comparable earlier runs, the selected run, and the nearest `sample_limit` comparable later runs, ordered chronologically. Every entry carries the canonical Game Tape step status, one explicit failure-signature relation (`EXACT_SIGNATURE`, `DIFFERENT_SIGNATURE`, or `NO_SIGNATURE`), duration when recorded, and the same backend-owned reproduction-context comparison used elsewhere. The summary may count the full verified Journal cut even when only the bounded nearest entries are rendered.

The summary additionally exposes evidence-only facts: complete versus incomplete context coverage, later passes in the same complete reproduction context, later exact failures in that same complete context, the first later same-context pass, whether the exact same-context failure subsequently reappeared, and the latest comparable outcome. These facts answer *what sequence was recorded under which known context*. They do not label a trend, prove a fix, identify flakiness, assign confidence, infer root cause, or recommend remediation.

`models/game_tape_reproduction_context.py` is the direct type owner for MATCH/DIFFERENT/UNKNOWN dimensions and SAME/CHANGED/INCOMPLETE summaries. Failure history no longer owns those shared types, so adding another historical Game Tape view must reuse the same context model rather than defining a local interpretation.

### Normalized comparable failure facts

`game_tape_failure_analysis.py` owns one typed comparable-window analysis for a failure-history request. Each comparable run becomes one normalized `GameTapeComparableStepFact` carrying the run/time identity, repository/step identity, canonical status and duration, physical attempt count/failure evidence, relation to the inspected deterministic signature, and the already-computed reproduction-context comparison to the selected run. Failure history and Failure Sequence consume those normalized fields directly instead of re-reading nested Game Tape step state.

The same analysis selects the bounded visible `GameTapeFailureSample` once from `sample_limit`. Only that visible sample receives adjacent pairwise comparison, producing one `GameTapeComparableTransitionFact` per chronological neighbor pair. This preserves the genuinely different neighbor-to-neighbor reproduction-context question without comparing every historical pair that the UI will not show. `game_tape_failure_transition.py` is consequently a public read-model projector/summarizer only; it does not call reproduction-context comparison itself.

### Adjacent outcome transitions and selected-context window

Failure Sequence also projects exact changes between each adjacent pair in its visible bounded sequence. `game_tape_failure_transition.py` is the one owner for these transition facts: previous/current canonical status, relation to the inspected deterministic failure signature, duration delta, and the existing five-dimension reproduction-context comparison between the two runs. It does not compare arbitrary objects and it does not create another historical population; its input is exactly the bounded sequence drawn from the canonical `GameTapeFailureAnalysis`.

The transition summary counts visible status changes by canonical context classification (`SAME_CONTEXT`, `CHANGED_CONTEXT`, or `INCOMPLETE_CONTEXT`) and counts signature-relation changes. These counts answer only *what changed between adjacent recorded runs*. A changed context followed by a passing run does not prove that the context change caused the pass, and an unchanged complete context followed by a different outcome is not labelled flakiness.

The same owner projects a selected-context window. Starting at the inspected run, it counts only contiguous nearest comparable entries that are a complete `SAME_CONTEXT` match to the selection and stops at the first recorded changed or incomplete context on each side. Those nearest boundary run IDs/classifications are shown explicitly. This is a continuity boundary in recorded evidence only; it is not persisted incident state, a recovery boundary, or causal segmentation.

### Retry physical-context summary

`game_tape_retry_context.py` summarizes adjacent canonical `GameTapeAttemptTransition` facts once. A step with no canonical attempt evidence is `NO_ATTEMPT_EVIDENCE`; one physical attempt is `NOT_RETRIED`. Fully linked retries are `SAME_PHYSICAL_CONTEXT` unless repository continuity, argv, executable identity, egress policy, resource policy, or cache input changed; if any of those facts changed, the summary is `CHANGED_PHYSICAL_CONTEXT`. Missing/broken canonical retry linkage yields `INCOMPLETE_RETRY_EVIDENCE`.

Duration, return code, stdout hash and stderr hash are deliberately excluded from the physical-context classification. They describe what happened during an attempt, not the identity/policy of the context in which it ran. The Attempts view renders the backend-owned summary and still shows those outcome differences separately.

### Play-by-Play

Play-by-Play renders the canonical execution-ledger projection in chronological order. It uses the same repository/step execution identities as every other Game Tape view, so selecting a step event opens the same inspector/evidence context. The event source is shown explicitly (`run_lifecycle`, `admitted_plan`, `operation_report`, or `step_execution`) so developers can see which durable owner supports the statement. Play-by-Play does not reconstruct a DAG or manufacture events from log text.

## Logs and durable evidence

Raw stdout/stderr remains transient execution data and is deliberately not persisted in run history. During an active Fleet run, command execution may project output through one bounded process-local `GameTapeLiveOutputStore`. Direct/bubblewrap execution streams chunks as they are read; transports that cannot prove incremental delivery may expose returned output only after that command returns rather than pretending it was live.

Host-backed step secret values are stream-redacted **before** text enters the store. The redactor carries a bounded suffix across transport reads, so an exact secret split across stdout/stderr transport chunks is still replaced before storage. The store retains at most 2,000 chunks per run with a 16,384-character chunk bound and records repository, step, physical attempt, stream, 1-based redacted line range, sequence and dropped-chunk count. Observability callback failure cannot stop or alter process execution.

While execution is active the resource is `ACTIVE`. When Fleet execution terminalizes, the store is **sealed**, not persisted: `SEALED` output remains process-local for one explicit 10-minute debugging handoff with an exposed expiry timestamp and then becomes `UNAVAILABLE` through lazy expiry. A process restart also loses it. Durable run history still contains only output SHA-256/byte/line evidence, never raw bytes.

The durable failure fingerprint already owns the failed physical attempt, authoritative stdout/stderr stream and recorded evidence line count. One `GameTapeLiveOutputStore.snapshot_cut()` acquires the live-output lock once and returns both the incremental batch requested by `after` and the complete currently retained chunk tuple. `game_tape_live_failure_anchor.py` maps durable failure facts to sequence identity only inside that immutable retained tuple; it never calls back into the mutable store after the batch is chosen. The browser receives that anchor and does not regex/parse logs or infer a failure line. If the target chunk was dropped or the sealed window expired, Game Tape says so and keeps the durable hash/count evidence available instead of manufacturing raw output.

The Logs view follows the newest output by default, stops following when the developer scrolls upward, reports newer chunks, and offers **Jump to latest** without yanking the viewport. **Open failure evidence** selects Logs and scrolls to the backend-owned sequence when retained. The same active/sealed transient chunks can be searched directly without a separate index. Never manufacture historical logs from hashes, summaries, or event text.

## Live and Team Goon behavior

The live Runs UI refreshes run detail whenever the existing run event stream advances. Repository completions still own the heavier Fleet snapshot invalidation. Team/shared-plan runs compose naturally because the projection iterates the already captured Fleet run targets and applies the same per-repository model; there is no second fleet observability architecture.

`game_tape_team.py` now projects one run-wide Team composition from those same repository execution facts. It reports complete non-zero repository status counts, the run-wide recorded repository span, observed peak repository concurrency, which repository execution identities actually overlapped, and the first deterministically timed failure focus when one exists. Overlap uses half-open recorded intervals (`start <= t < finish`), so one repository ending exactly when another starts is not reported as concurrent. Missing timing is preserved as missing evidence; Game Tape does not invent a chronological first failure when a competing failure lacks a timestamp.

The Team composition is reused in Summary and as a compact strip over the existing shared-clock Timeline. It does not create cross-repository edges, `depends_on`, phases, or schedulable nodes. Selecting the first failed step reuses its existing `step_execution_id` and therefore opens the same inspector/evidence identity as Flow, Timeline and Logs.

## Observability evidence receipt

Every Game Tape also carries one typed observability receipt. The receipt explicitly separates durable execution evidence (run/repository/step lifecycle, timing, exact command, provenance, output fingerprints and failure signatures) from transient evidence (`raw_stdout` / `raw_stderr`). It also states the no-persistence/no-search-index/no-UI-execution-authority guarantees and the exact bounded live-output policy.

The live-output limits have one source of truth in `core/game_tape_policy.py`: 2,000 retained chunks per run, 16,384 characters per chunk, and a 600-second terminal process-local handoff. `runtime/game_tape_live_output.py` and the observability receipt consume those same constants. Raw output still does not survive process restart and never becomes historical Journal state.

## Run Preview boundary

Plan preview remains non-effecting and uses the canonical Fleet plan owner. Shared Plan preview returns both the deterministic resolved `plan_id` and the digest of the canonical execution lock for the reviewed source plus inputs. Run and Schedule requests must carry those identities back as `expected_plan_id` and `expected_execution_digest`. The execution boundary re-resolves the current source plus inputs and rejects either identity if it changed before admission. This is a freshness fence, not a claim that two HTTP requests share one in-memory object. File-backed execution still requires its admitted `goon.lock`; saved Shared Plans use the explicit reviewed digest because their source lives outside Team repositories and one saved input-parametric Plan cannot own a single static source-adjacent lock for every input set. Execution authority still begins with the exact `ResolvedFleetPlan` embedded in `FleetPlanTarget`, and Game Tape reads that admitted object rather than resolving YAML itself.

## Invariants

Tests and architecture review should preserve these rules:

1. Flow order equals admitted `ResolvedFleetPlan.steps` order.
2. Game Tape cannot mutate or schedule execution.
3. Flow, Timeline, Play-by-Play, Attempts, Logs/evidence, Search, and inspector share one step execution identity/model.
4. `NOT_RUN` means planned but no durable step execution result exists.
5. Recovery interruption is not silently presented as ordinary failure.
6. Structured durable Game Tape provenance never exposes secret/literal values; host-backed secret bytes are also redacted before transient live output enters the store.
7. Wall-clock timing comes from execution evidence, never from UI estimation.
8. Team status is composed from the same repository execution facts.
9. Raw log payloads are bounded/process-local while a run is active, then discarded; they are never reconstructed or persisted as historical Game Tape state.
10. Frontend code does not re-derive plan order or operation outcome from logs.
11. Run Diff compares canonical Game Tape facts only; it is not a generic data-diff framework.
12. Search reads the Game Tape payload directly and creates no durable search/index authority.
13. Missing source lines, raw historical logs, or undeclared tool version strings are shown as unavailable rather than inferred.
14. Lifecycle meanings are derived only by the canonical Game Tape lifecycle owner.
15. Play-by-Play is a read projection over existing durable owners; it never appends a duplicate durable execution event.
16. Equal-timestamp ledger ordering remains deterministic and preserves admitted step order.
17. Physical attempts come directly from canonical `PlanStepAttemptEvidence`; Game Tape does not create a second retry/attempt model.
18. Failure fingerprints bind the final physical attempt when available and never infer root cause from log text.
19. Reproduction is evidence-only: Game Tape never replays a command or reconstructs secret/literal environment values.
20. Adjacent attempt comparisons are derived from canonical `PlanStepAttemptEvidence`; the browser does not invent retry linkage or physical changes.
21. Historical failure grouping requires the same plan, repository, step and exact deterministic failure signature; no fuzzy clustering or root-cause inference is allowed.
22. Reproduction-context comparisons are `MATCH`, `DIFFERENT`, or `UNKNOWN`; missing evidence never silently becomes a match.
23. Failure evolution uses the same verified Journal read window; first/last occurrence and later-pass facts are derived, never persisted.
24. A later comparable `PASSED` step is evidence only; Game Tape must not call it a fix, permanent recovery, flake classification or root cause.
25. Retry physical-context classification uses only repository continuity, argv, executable, egress, resources and cache input; output/exit/timing changes remain outcome evidence.
26. Reproduction-context comparison is owned once by the backend across workspace, runtime, toolchain, isolation and admitted command; the browser never reclassifies those dimensions.
27. `SAME_CONTEXT` requires all five dimensions to be known and equal; missing evidence remains `INCOMPLETE_CONTEXT`, and any recorded difference becomes `CHANGED_CONTEXT`.
28. Comparable failure history and failure sequence reuse one `GameTapeFailureAnalysis`; the same-plan/repository/step population plus selected-relative signature/context facts are interpreted once per request.
29. Adjacent-run transitions may compare neighboring reproduction contexts because that is a distinct pairwise question, but they must reuse the analyzed sequence and signature relations and must not rescan the Journal window.
30. The visible failure sequence is bounded to nearest comparable runs around the selected failure while summary facts may use the full verified Journal cut.
31. Same-context pass/recurrence sequence facts are correlation evidence only; Game Tape must not convert them into trend, confidence, flakiness, recovery or root-cause labels.
32. Adjacent failure-sequence transitions compare only neighboring entries from the bounded canonical sequence; they cannot create another history selector or generic diff model.
33. The selected-context window stops at the first changed or incomplete recorded context on either side; that boundary is evidence continuity only and carries no causal, incident, recovery or confidence meaning.
34. Each comparable historical run becomes one normalized `GameTapeComparableStepFact`; downstream projections must not re-read nested step/tape state to reinterpret status, signature relation, or selected-run context.
35. Failure Sequence consumes one `GameTapeFailureSample` selected from `sample_limit`; it must not independently slice the same prior/later history again.
36. Adjacent pairwise reproduction context is derived once only for the bounded visible sample and projected thereafter; Game Tape must not compare invisible history pairs or re-run pairwise comparison in the transition/UI layer.

37. YAML source positions come from the same source snapshot used for parsing/hashing, are carried through resolution once, never use absolute host paths, and do not participate in semantic `plan_id`.
38. Copy-command UX copies only the exact command already present in canonical Game Tape evidence; it never becomes replay/run-again execution authority.
39. Failure recovery remains navigation-only inside Game Tape. `EXECUTOR_PLACEMENT_LOST` points to Runs-owned `/readmit`; uncertain/partial repository effects point to Recovery; ordinary failures point to Runs-owned exact-target rerun. Game Tape never dispatches any of those writes and never treats `retryable` as replay authority.
39. Live stdout/stderr uses one bounded process-local store, stream-redacts host-backed secrets before storage, seals terminal bytes for exactly one 10-minute process-local handoff, then expires them; raw output never becomes durable history.
40. Search may inspect currently retained transient chunks directly but must never build a persistent raw-log index or imply historical raw output exists.
41. Human-readable runtime versions are shown only when already declared/admitted as durable plan facts; Game Tape never probes current-host versions to fill historical gaps.
42. Previous-comparable-run navigation means the newest earlier run with the same admitted plan and repository set; it is distinct from success-only timing baseline samples.
43. Failure-to-live-output navigation uses only durable failure attempt/stream/evidence-line facts plus retained store line ranges; React must never infer anchors from log text.
44. Transient line identities refer to the redacted stream seen by durable output evidence, so line navigation and durable line counts share one evidence representation.
45. Dropped chunks and sealed expiry are explicit observability facts; missing raw bytes stay missing and must not be reconstructed.
46. Secret redaction must remain stream-aware across transport chunk boundaries before any text enters the transient store.

43. Team composition is derived only from canonical repository execution identities/status/timestamps; observed overlap is not a dependency or scheduling relationship.
44. Repository overlap uses half-open recorded intervals so touching end/start timestamps do not create false concurrency.
45. A run-wide first failure is exposed only when its chronological position is deterministically supported by recorded timing; missing competing timestamps remain unknown.
46. The observability receipt is backend-owned and names durable versus transient evidence explicitly; React must not invent evidence-retention policy.
47. Live-output chunk/size/terminal-retention limits have one core policy owner shared by the runtime buffer and the observability receipt.
48. Team Summary and Timeline reuse existing repository/step execution identities; no second fleet observability model or cross-repository DAG semantics are allowed.

## Projection ownership after feature completion

Game Tape is feature-complete, so maintainability work must reduce interpretation paths rather than add new observability layers. `game_tape_projection.py` is now only the run/repository orchestrator. Ordered step/Why/attempt projection belongs to `game_tape_step_projection.py`; environment fingerprints, runtime facts, requirements, bottleneck arithmetic, receipt and summary projection belong to `game_tape_run_facts.py`. The orchestrator composes those owners with lifecycle, identity, ledger, Team composition and observability receipt facts.

The browser follows the same rule. `gameTapeSteps.ts` owns the feature-local repository→step context traversal and selected-step lookup used by the shell, Logs/Evidence, Attempts and Search. The shared `GameTapeStepInspector.tsx` owns exact-command copy, declaration provenance and failure-evidence navigation. Views render or select those facts; they do not recreate step lookup or inspector semantics.

This split is intentionally not another framework layer. These are narrow Game Tape owners around facts already required by the existing feature. `OBS-016` keeps the top-level projector small and prevents step/fingerprint/receipt interpretation or frontend step selection from drifting back into multiple views.
