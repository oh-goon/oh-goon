# Real lifecycle QA

This guide and `REAL_LIFECYCLE_QA.yml` describe current coverage owners and
reproduction commands. Listed test references are not recorded passes; report
actual results against the exact source and environment that executed them.

## Before implementing a backlog item

An open qualification issue does not mean its feature is missing. First identify
the supported behavior in its current owner, then find the existing proof and
the specific evidence still needed. Change code only for a reproduced defect or
an explicitly missing capability. A test reference shows where to investigate;
it does not establish that the test passed on this source or host.

Keep two questions separate in review and issue updates:

1. **Implementation:** is the requested behavior implemented, partial, absent,
   or intentionally unsupported? Link its current code/contract owner.
2. **Qualification:** what did an actual check establish, for which source,
   host, harness and mode? Record its command and evidence in the existing issue
   or receipt owner. Unrun, blocked, incomplete and historical results cannot
   establish a current PASS.

These worked examples point to existing owners rather than define another
capability or result registry:

| Capability already present | Existing proof and its limits | Remaining question / next action |
| --- | --- | --- |
| Native Make CPU budget: [resource wrapper](../../scripts/development_test_resources.sh), with [worker admission/reporting](../../backend/src/oh_goon/repository_tooling/pytest_resource_budget.py). | [Resource regressions](../../scripts/tests/test_development_test_resources.py) protect quota/refusal and delegated constraints. Two CPUs are a CPU-time ceiling; the memory estimate is not a hard RAM/I/O limit. | [HUG-12](https://linear.app/hugloss/issue/HUG-12) needs actual Linux/WSL host proof; [HUG-90](https://linear.app/hugloss/issue/HUG-90) owns RAM/OOM/I/O qualification. Follow the [native budget commands](../DEVELOPMENT.md#local-test-resource-budget), preserving unsupported-host refusal. |
| Native pytest batches and exact member resume: [batch owner](../../backend/src/oh_goon/repository_tooling/test_batches.py) and [receipt owner](../../backend/src/oh_goon/repository_tooling/test_batch_receipts.py). | [Batch](../../scripts/tests/test_test_batches.py) and [receipt regressions](../../scripts/tests/test_test_batch_receipts.py) protect selected membership and reusable PASS evidence. Their existence is not proof of every environment or worker ordering. | [HUG-115](https://linear.app/hugloss/issue/HUG-115) owns semantic-input invalidation, [HUG-116](https://linear.app/hugloss/issue/HUG-116) worker isolation, and [HUG-91](https://linear.app/hugloss/issue/HUG-91) disposal. Run the relevant existing file through the bounded command below. |
| Process capture and local developer logging: [command capture](../../backend/src/oh_goon/engine/command_capture.py), [batch owner](../../backend/src/oh_goon/repository_tooling/test_batches.py), [logging wrapper](../../scripts/run_logged.sh). | Pytest execution and native collection select bounded diagnostic tails through the existing supervisor; batch reports retain raw-stream evidence and explicit omission/completeness. [Logging regressions](../../scripts/tests/test_run_logging.py) protect presentation/retention behavior. Path replacement does not promise arbitrary credential redaction. | [HUG-121](https://linear.app/hugloss/issue/HUG-121) owns noisy-output and aggregate-memory qualification; [HUG-127](https://linear.app/hugloss/issue/HUG-127) owns log privacy/unsafe-path proofs. Use the process command below and retain unrun host cells. |
| Native-agent execution and harness conformance: [runtime owners](AGENT_RUNTIME_OWNERS.md), [compatibility/evidence matrix](AGENT_HARNESS_COMPATIBILITY.md). | Qualification is specific to the installed harness, integration mode, model protocol and environment. Ephemeral Pi lifecycle/compaction evidence does not establish persistent cross-Run reopen. Service readiness is not task submission or continuation support. | [HUG-21](https://linear.app/hugloss/issue/HUG-21) owns native baseline reconciliation; [HUG-39](https://linear.app/hugloss/issue/HUG-39) owns Pi persistent-session qualification before execution coding. Select the exact installed-process proof in the [backend test guide](../../backend/tests/README.md), not a synthetic replacement for host behavior. |

After `make init`, a bounded investigation of one listed tooling file can use:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon-test-batches \
  --qualified --focused --next-check "make tooling-check" --show-output \
  --granularity tests --workers 1 \
  --batch-size 16 --batch-timeout 180 scripts/tests/test_test_batch_receipts.py
```

This is focused evidence only. Direct uv commands do not acquire the Make-owned
CPU scope. Use the [development loop and escalation rules](../DEVELOPMENT.md)
and [tool-session qualification](TOOL_SESSION_QUALIFICATION.md) for the remaining
owner checks. [Test isolation QA](TEST_SETUP_ISOLATION_QA.md) retains fixture
ownership. [HUG-133](https://linear.app/hugloss/issue/HUG-133) owns the local-test
closure matrix and [HUG-69](https://linear.app/hugloss/issue/HUG-69) CLI signoff;
this guide does not duplicate their result accounting.

When behavior changes, update its current explanation and proof references in
the same PR. Keep actual execution results with their existing evidence owner;
do not turn this guide into a perpetual PASS list. Older ADR/checkpoint results
describe their original source. Follow [documentation anti-drift](DOCUMENTATION_ANTI_DRIFT.md)
when current and historical explanations disagree.

## Source, resume and worker authority qualification

The existing source-layout owner supplies canonical authored-source identity;
native pytest supplies selection and outcomes, and the existing member receipt
store owns reuse. An earlier successful owner or receipt cannot prove a changed
source or an unqualified semantic input. Qualification must exercise the real
boundary rather than substitute a fake source identity or infer test dependencies.

For [HUG-114](https://linear.app/hugloss/issue/HUG-114), synchronize native
launcher children in disposable checkouts and mutate authored Python, tests,
frontend, generated contracts or scripts between owners and immediately before
aggregate success. Compare canonical identities at semantic-owner boundaries
as well as beginning/end, retaining an observed source change even if a later
edit restores the initial bytes. Stop admitting further owners after a change.
The source-layout identity command accepts an explicit `--root` for inspection;
repair commands retain their original root. Stable source,
ephemeral outputs, original failures, adjacent worktrees and symlink substitution
provide the controls. Tool-session units revalidate source before publishing a
new PASS receipt. Within-owner transient edits require that owner's proof or
explicit immutable-source qualification; these boundary checks are not a file
watcher. Certification retains its original source authority.

For [HUG-115](https://linear.app/hugloss/issue/HUG-115), first establish genuine
two-run reuse with unchanged source/member/toolchain, then change a controlled
semantic environment, configuration, plugin, native tool or external fixture
input. Record the supported input owner and require rerun or refusal when its
authority changes. Native HOME/XDG, PATH, Python import, temporary-directory and
pytest selectors bind the resolved invocation digest; `--resume-env NAME` declares additional
semantic environment inputs. Use the existing `--resume-context` with an owner's
actual input digest for mutable tool/fixture content, or native `no_resume` when
there is no cross-invocation repeatability authority. Its mark inhibits future
cached reuse, not current-run completed-member accounting. Unknown dependencies
remain unqualified: arbitrary ambient-state discovery, blanket environment
hashing and stored credentials are not valid substitutes. Context manifests
retain digests rather than raw context tokens. Worker, batching, timeout and
CPU-only changes must retain otherwise valid reuse.

For [HUG-116](https://linear.app/hugloss/issue/HUG-116), compare identical native
members across fresh/resumed, file/case and ordering variants. Exercise owned
SQLite files, HOME/XDG roots, sockets/ports and child lifetimes with both valid
isolation and a deliberate collision. Caller HOME/XDG selection remains intact;
test fixtures own temporary resources and environment restoration. Cancellation
must reach active native commands before the executor joins them, with no new
refinement admitted after cancellation. Preserve declared serial process work and
exact terminal accounting. Record requested and admitted workers; actual four-
worker qualification needs explicit native capacity, not a four-worker request
under a two-CPU ceiling. Missing physical-host cells remain NOT_RUN.

After native environment preparation, begin with the bounded existing owner files:

```bash
bash scripts/development_test_resources.sh \
  uv run --offline --frozen --no-sync --project backend oh-goon-test-batches \
  --qualified --focused --next-check "make tooling-check" \
  --granularity files --batch-size 1 --workers 1 --batch-timeout 180 \
  scripts/tests/test_development_check_contract.py \
  scripts/tests/test_development_test_runner.py \
  scripts/tests/test_application_verify_contract.py \
  scripts/tests/test_test_batch_receipts.py \
  scripts/tests/test_pytest_native_collection.py
```

Add the decisive native canaries to their existing owners after reproducing a
gap. Run process cases explicitly, then the applicable root escalation union on
stable source. Keep results with HUG-133 and the original receipts; these commands
are investigation entry points, not recorded passes or a new test authority.
The native [source-boundary proofs](../../scripts/tests/test_development_source_authority.py)
and [worker/resource proofs](../../scripts/tests/test_pytest_worker_isolation.py)
exercise the public launchers and existing batch runner alongside those owner files.

## Output and developer-log qualification

Pytest execution retains at most 1 MiB of merged stdout/stderr diagnostics per
native attempt through the command supervisor's bounded-tail mode. Existing
batch reports expose `output_captures`: the original raw-stream SHA-256 and
byte/line counts, retained size, limit, omission flag and completeness. A
timed-out attempt reports `complete: false`; its hash describes only observed
bytes. Native collection diagnostics are also bounded; selected membership
still comes from the native collector's separate structured output.
An explicit `--report-path` requires an owned output directory that other users
cannot write. Symlink parents/destinations are refused; reports publish
atomically with private permissions. An existing owned regular report may be
replaced.

Noisy output alone does not fail a complete native execution. Intentional
diagnostic omission can accompany success when omission is explicit and exact
member accounting remains intact. Timeout, cancellation and output-limit
failure are not successful attempt evidence. Only complete required membership
can support aggregate success after the existing recovery policy. Failure
formatting and refinement diagnostics remain bounded as well; truncating only
after full capture would not protect capture memory.

Start the shared [HUG-121](https://linear.app/hugloss/issue/HUG-121) /
[HUG-127](https://linear.app/hugloss/issue/HUG-127) investigation with the existing
capture, process and logging regressions:

```bash
bash scripts/development_test_resources.sh \
  uv run --offline --frozen --no-sync --project backend oh-goon-test-batches \
  --qualified --focused --next-check "make tooling-check" --show-output \
  --granularity tests --workers 1 --batch-size 16 --batch-timeout 180 \
  scripts/tests/test_test_batches.py \
  scripts/tests/test_test_batches_process.py \
  scripts/tests/test_run_logging.py
```

Use controlled real pytest success, failure and timeout cases with large
stdout/stderr, a very long newline-free line, malformed bytes and a decisive
traceback. Include cancellation and a descendant holding the output pipe.
Keep the measurement harness and its stored output bounded outside source.
Record source, host, workload, configured limits, native outcomes, exact
membership and peak aggregate memory; a single child's peak RSS is not an
aggregate measurement. Record requested and admitted workers separately. A
four-worker request under two-CPU admission does not qualify four workers;
select sufficient native capacity explicitly or record that cell NOT_RUN.

Developer logs are local diagnostics, separate from Run/Journal records and
agent secret stores. The logging wrapper enters the prepared uv environment
once, reuses its interpreter for bounded stream processors, omits command
arguments from generated headers, and acquires private logs through native
no-follow handles. When that environment or private location is unavailable,
it warns and preserves command execution with bounded console presentation.
See [private local check logs](../DEVELOPMENT.md#private-local-check-logs) for
configuration and the existing latest-log read interface.

Probe framework-generated metadata with fake-token canaries, private
permissions, unsafe-path refusal and long-line output. Path replacement does
not promise credential redaction for arbitrary text intentionally printed by
a user's tests. Preserve progress, original exit status, Ctrl-C, active-log
retention exclusions and completed-log ownership. Child text resembling a PASS
result cannot override the native exit or member receipt. Qualify real
filesystem permission behavior on each claimed host; portable fixtures do not
establish WSL or mounted-path behavior.

Run affected report, membership and receipt regressions when those boundaries
change. Process cases must run explicitly because the fast tooling target
excludes them. On stable source, run `make test-tooling`, `make architecture`
and `make tooling-check`; shared engine/runtime changes also require
`make backend-check`. Keep actual outcomes in the existing issue/receipt owners.
These commands describe qualification work, not recorded passes or a new gate.

Oh-Goon treats real-lifecycle proof as the highest-value regression layer for critical behavior.
Unit tests remain necessary, but they do not prove that independently correct owners compose correctly over time.

A critical journey is therefore tested through the production-shaped chain that actually matters:

```text
public entrypoint
  -> authority acquisition
  -> work / mutation
  -> durable publication
  -> owner teardown
  -> fresh owner reconstruction
  -> normal observed readback
```

Failure-oriented journeys extend that chain with interruption, cancellation, stale identity, ambiguous commit,
race-window, retry, or deliberate invariant mutation.

The machine-readable inventory is `docs/development/REAL_LIFECYCLE_QA.yml`. Proofs may name an exact pytest node or a public Make command when the evidence requires a real process/runtime boundary. It records the lifecycle,
entrypoint, durable observation, required proof modes, and the exact pytest nodes that protect it. The inventory
is itself tested: a proof cannot silently point at a renamed/deleted test, and the historical bootstrap and SQLite
lifetime regressions must retain mutation proof.

## Golden source gate

`make test-real-lifecycle` runs the compact high-risk suite. It intentionally selects lifecycle, boundary,
adversarial, and mutation tests instead of optimizing for raw test count. It covers bootstrap convergence,
terminal durable reload, failure-to-rerun, cancellation, repository-instance isolation, recovery reconciliation,
executor settlement/readmission, shared-plan source authority, Repository Understanding candidate grading,
Game Tape projection, SQLite preparation/execution lifetime, ambiguous/concurrent commit behavior, and the real `run_logged.sh` terminal/captured-output progress boundary.

The normal backend/tooling certification suites still own their complete partitions; the golden gate is an
investigation/release-confidence surface, not a second implementation or a replacement for those owners.


## SQLite real-usage gate

`make test-sqlite-real-usage` is the focused regression owner for SQLite behavior that only emerges when normal runtime phases compose. It uses a real SQLite state file through an active worker, foreground progress waiting, unrelated concurrent reads and immutable-input hashing, local terminal handoff, complete runtime teardown, fresh runtime reconstruction, and durable readback. A second scenario repeats runs across multiple runtime reconstructions and performs SQLite integrity/foreign-key checks throughout.

The correctness assertions are intentionally product-shaped: terminal status, event identity, operation identity, restart readback, absence of ghost active work, and SQLite integrity. Connection counters remain mutation/diagnostic proof rather than independent authority: foreground progress must perform zero SQLite reconnects while the local worker is active, then exactly one fresh Journal reconstruction after supervisor liveness retires. This preserves the post-1267.0.280 single durable terminal-truth owner without reintroducing a supervisor completion mailbox.

## Real-world full-stack gate

`make test-real-world` is intentionally slower than the source golden pytest gate. It launches the actual `make dev` backend and Vite processes, drives local-access authentication through the browser-facing proxy, executes a real repository Goon, validates Game Tape and Shared Plan behavior, then starts a long-running real repository Step and waits until both its physical child PID and durable `STEP_STARTED` evidence exist. While that child is alive, a wrong Run cancellation must return `RUN_NOT_FOUND` without disturbing it and a competing repository admission must return `EXECUTION_BUSY` without stealing execution authority. The exact public cancellation then has to kill the owned child, forbid its completion marker, settle canonical `CANCELLED`, release the lease, and allow the previously blocked repository to execute successfully. A later physical Step is then active when the entire process group is SIGKILLed without EXIT cleanup; restart on the same isolated durable state must preserve the cancellation/success histories, fail-close the interrupted Run with no ghost active owner, execute again, and tear down deterministically.

This gate exists because in-process dependency overrides cannot prove packaging, process ownership, launch-token, proxy, subprocess, restart, or durable-reconstruction behavior. The fast `make test` path remains independent; exact-artifact/native certification runs this gate before certification.

When a validated strict executor inventory is available, `make test-real-world-parallel` strengthens the same journey across the real Shared Plan concurrency boundary. It authors one CICD Plan with `repositories.mode=parallel` and `max_parallel=2`, discovers two real repositories, then waits until two physical `release-metadata` children for the same Fleet Run are simultaneously visible. Cancellation is issued only through `/api/fleet/runs/{run_id}/cancel`; both child processes must disappear, the Run must settle `CANCELLED`, no active Fleet owner may remain, and a fresh two-target Shared Plan must subsequently complete successfully. The fixture uses the existing canonical `python/release-metadata` Goon and a zero-network in-tree PEP-517 repository backend; it does not add an acceptance-only Goon or bypass strict executor placement. This target deliberately fails fast when `OH_GOON_EXECUTORS_FILE` is absent rather than replacing strict CI/CD authority with a fake non-strict path. The same native target now also proves sibling failure isolation: both physical target children start, one repository-owned executable fails deliberately while its sibling remains in flight and must finish `SUCCESS`, Game Tape and successful-operation evidence must remain repository-attributed, no ghost Fleet owner may remain, the failing repository is repaired at its own source boundary, and a fresh strict parallel Shared Plan must succeed for both repositories. A later application restart must preserve both the failed and repaired histories. The strict native journey also closes the preview-to-command race explicitly: the saved Shared Plan SHA is sent as preview authority, the previewed Plan SHA/ID and every Team `repository_instance_id` are sent back at run admission, then one repository checkout is replaced at the same canonical path. The stale command must return `FLEET_PLAN_SOURCE_STALE` before any physical repository process or Fleet Run exists. Only a refreshed preview with the new repository-instance authority may execute.

## Native exact-artifact gate

Source-only QA must not pretend to prove host behavior it cannot actually execute. The stdlib-only implementation exposed as the named project command
`oh-goon-native-real-lifecycle-artifact` accepts an exact retained source ZIP, extracts it to a new directory,
and runs the public operator lifecycle:

```text
make init
make local-state-prelaunch-check
make goon-static-admission
make test-real-lifecycle
make test-real-world
make certify
```

`--bootstrap-only` exists for focused bootstrap diagnosis, but the default remains the full certification path.
This gate is intended for the supported dependency-complete native environment (including the required uv/npm,
browser, and isolation supply). A source-only container that lacks those prerequisites must report this gate as
unverified, never passed.

### Deep dogfood boundary

`oh-goon-native-real-lifecycle-artifact --deep-dogfood --expected-sha256 <ARCHIVE_SHA256> <source.zip>` extends the exact-artifact journey with
clean-room self-hosting through the **installed public `oh-goon` CLI only**. After ordinary materialization and
real-lifecycle gates, the extracted artifact runs `oh-goon self verify`, takes the one Fleet Run ID printed by that
public command, reads the durable run back through `oh-goon dev explain-run <id>`, and finally runs
`oh-goon self certify` against itself. The preceding real-world gate already proves the Repository -> Goon -> Step ->
Game Tape -> production-browser journey, so deep mode composes that public product evidence with Fleet-on-itself and
fresh public readback rather than adding a private Game Tape inspector.
Before extraction, deep mode requires the exact retained `ARCHIVE_SHA256` receipt from package production and proves
that the supplied ZIP bytes match it. A missing or mismatched receipt fails before artifact execution; an arbitrary
ZIP cannot silently become the release dogfood subject merely because it is structurally valid.
The dogfood helper must not import private Oh-Goon implementation modules, inject `PYTHONPATH`, bypass normal
Plan/Fleet admission, or add a dogfood-only execution path.

Two invariants govern every deeper dogfood campaign:

> **Dogfood must cross a public boundary.** If a proof only works by importing private implementation code, it
> is an implementation test rather than product dogfood.

> **Dogfood may expose authority; dogfood must never become authority.** Dogfood results are diagnostic evidence.
> They do not authorize admission, certification, release promotion, dependency resolution, or source mutation.

Deep dogfood therefore stays opt-in and outside `make certify`. Product code must not branch on a dogfood flag,
special-case a dogfood repository/name/version, or maintain a separate dogfood cache/state owner. A defect exposed
by dogfood is repaired in the semantic owner with a focused regression; the campaign then replays the same public
boundary from a fresh exact artifact. Long dogfood campaigns follow `DURABLE_CONTINUATION.md`: after every meaningful
small closure, persist and independently read back the next generation before starting the next closure.

## Test efficacy rule

Executable mutation challenges here are reserved for selected high-risk
regressions where test efficacy itself is release-relevant evidence. They are
not a requirement for every ordinary test; see the local
[test policy](TEST_QUALITY.md) and its canonical review Skills.

For historical high-risk regressions, passing the regression test is not enough. A mutation proof deliberately
restores the protected defect shape and must observe failure. This currently applies at minimum to:

- clean-extraction `make init` independence from release staging (`generated/release/static` must remain unnecessary for Hatchling/native-uv editable materialization), and
- strict Fleet preparation/execution callback lifetime (preparation-only SQLite observers must not escape into execution).

The terminal presentation path follows the same rule: a pseudo-terminal proof exercises the real logging wrapper and requires a single in-place spinner, while a captured-output proof requires elapsed-time heartbeats to disappear and durable logs to remain free of transient progress.

The long-term QA metric is lifecycle coverage, boundary coverage, failure-transition coverage, restart coverage,
and mutation challenges detected. Raw pytest count is supporting evidence only.

## Delegated configuration authority

Real lifecycle QA applies the repository-wide invariant **Resolve once, transport once, execute once, report once**. A native acceptance override is resolved by one semantic owner before delegation, transported as immutable resolved authority through systemd/re-entry, consumed without re-reading the original environment, executed by one owner, and reported once by that execution owner. QA must reject silent fallback to defaults, duplicate execution/startup reporting, and delegated children that independently reconstruct caller configuration. Timeout evidence must distinguish a product failure from an observation bound: a still-running Run that has already reached `RUN_STARTED` but has not reached physical Step start is incomplete qualification evidence and must identify the next semantic preparation boundary rather than incorrectly blaming dispatch.
