# Hacker Hockey product theme

> **Skate hard. Run clean. Fail closed.**
>
> The joke is in the presentation. The guarantees are not.

oh-goon is for operators who care what actually ran, what authority it had, what evidence it produced,
and whether the same declared inputs will lead to the same understood behavior. The hockey theme gives
that mindset a memorable team vocabulary without creating a second execution model.

## Theme contract

The hockey team is a **product-language projection over canonical oh-goon facts**.

It must never create:

- a second scheduler, lifecycle, event stream, retry engine, security watcher, or validation engine;
- a persona-named runtime state that disagrees with the canonical domain state;
- an implied capability that the current runtime cannot prove;
- secret or variable-value dumping for the sake of a colorful status panel;
- automatic behavior merely because a persona sounds like it should act;
- a new synonym in plans, APIs, durable storage, or evidence where the technical term is clearer.

Technical diagnostics always keep the canonical names: admission, preparation, cache lineage, retry
policy, execution attempt, isolation boundary, quarantine, publication, provenance, and teardown.
The personas explain those facts to humans.

A repository/project may be called a **team** in theme copy. Saved local discovery roots are **League folders**, and the League map derives Teams from Git roots plus their exact root `goon.yml` status. IDs, APIs, plans, evidence, and diagnostics
continue to call it a repository.

## Visual tuning owner

The browser theme is deliberately easy to tune without spreading raw colors through feature CSS.
`frontend/src/app/theme/themeTokens.ts` is the single visual tuning surface for the snow/ice/hockey look.
It owns semantic surfaces, text, borders, actions, status colors, the ice canvas/panel/navigation treatments,
typography primitives, shadows, radii, spacing, motion, and shared layout dimensions.

Focused CSS under `frontend/src/styles/` consumes only those variables. Raw colors in CSS are rejected by
the architecture gate, and every `var(--...)` used by authored CSS must exist in `themeTokens.ts`. This means
a later visual pass should normally change token values first and component CSS only when the layout or
interaction itself changes. Do not add a second theme object, CSS color palette, compatibility aliases, or
feature-local brand colors.

The current default should remain recognizably **snow + arena ice + dark rink**: bright readable text, cold
cyan focus/action cues, glassy dark panels, very subtle rink geometry, restrained green/amber/red only for
meaningful status. The theme can be tuned, but product state must never be encoded by decoration alone.

## Design tone

The visual language is **night rink + terminal**, not retro green-screen cosplay:

- dark graphite and midnight surfaces;
- ice-cyan actions and focus states;
- restrained green for a clean pass;
- amber for calls that need attention;
- red only for a real stop/failure;
- monospace for evidence, IDs, commands, and roster labels, not for long reading text;
- subtle rink-line geometry instead of decorative noise;
- dense enough for experienced operators, but never cramped or cryptic.

Humor should reward understanding. It must not replace the reason a run stopped.

Good:

> **Linesman call — boundary denied**  
> Step requested host path authority that was not admitted. No process started.

Bad:

> **OFFSIDE!!! 🏒**

The first tells an operator what happened. The second only performs a theme.

## The roster

### The Enforcer — admission

**Catch bad intent before the skates touch the ice.**

The Enforcer is the product persona for canonical pre-execution admission: plan resolution,
repository baseline checks, declared requirements, executor/isolation/resource authority, and other
facts that can invalidate the run before `RUN_ADMITTED`.

It is not a job scheduled at step 1. `oh-goon enforcer run` remains a read-only local view over the
same canonical repository admission checker.

**Hard rule:** rejected admission means no pipeline effect has started.

### The Equipment Manager — declared toolchain authority

**No mystery sticks from somebody's garage.**

The Equipment Manager explains the admitted execution environment, observed required tools, prepared
runtimes, pinned images/digests, and other concrete toolchain facts. Pipeline authors do not approve
each tool or consumer separately: the resolved step says what it needs, while the executor image/environment
owns what is installed. The Game Sheet may show the observed logical name, exact digest/size, stable
authority ID, and whole-toolchain identity.

It maintains no runtime loop. Enforcer proves required tools are available before effects; the local
strict backend may freeze exact bytes as internal evidence. No persona inherits caller PATH, installs a
tool, substitutes a version, or re-resolves the executable during the run.

### Warm-Up — explicit preparation and cache restore

**Fast because we prepared, not because yesterday leaked into today.**

Warm-Up explains explicit preparation plus declared cache lineage. A step may start with an empty
private cache, one exact earlier-step cache, or one repository-owned retained reference when the plan
says so.

There is no ambient cache inheritance, branch-key fallback, latest-cache lookup, or shared workspace
left behind by another team.

### The Red Team — declared security checks

**Find known bad gear before Game Start.**

The Red Team explains typed security/audit checks that are actually declared and available, such as
the existing uv audit recipe. Policy decides whether findings block.

The theme must not claim that every run has a universal CVE scanner unless that scanner is represented
as an admitted, typed capability/step in the resolved plan. Allowlisting, expiry, image scanning, and
severity thresholds are product capabilities only when their contracts exist.

### The Scout — cross-run evidence, never control

**Watches the league. Never changes the play.**

The Scout is Oh Goon's optional cross-run workload and resource observability surface. It aggregates
canonical execution evidence across repositories, Plans, Goons, Steps, attempts, versions, and time so
operators can find heavy resource consumers, unusual execution intensity, run-frequency changes,
failed-resource waste, and shared Goon regressions.

The Scout does not execute, admit, schedule, retry, cancel, place, throttle, or modify work. It does not
own single-run logs or debugging; when an investigation reaches one execution, Scout hands it to Game
Tape. Missing telemetry remains unavailable rather than becoming zero, and peer comparisons are shown
only for explicitly compatible cohorts with enough samples and coverage. Scout does not read source
code, secret values, stdout/stderr payloads, or resolved environment-variable values for these analyses.

### The Trainer — declared retry policy

**One more shift only when the playbook already allowed it.**

The Trainer is the human name for the step's immutable retry policy **and the canonical decision fact
produced from that policy after a known completed failure**. A retry-enabled step names the exact
known-transient exit codes, explains why replay is safe, and declares every delay between attempts.
There is no autonomous actor classifying stderr, inventing exponential backoff, adding jitter, or
deciding to try again from ambient runtime conditions.

One attempt remains the conservative baseline unless retry is explicitly declared. Generic Trainer
replay is allowed only for fresh attempt-private work with network egress denied. A networked generic
command is not replayed merely because an exit code looks transient; typed integrations must own any
transport retry semantics explicitly inside their own bounded contract.

### The Coach — advisory projection

**Can suggest. Cannot overrule the game.**

The Coach is reserved for non-authoritative guidance derived from canonical facts: optimization hints,
trend summaries, or review suggestions. Coach output must be visibly advisory and must never alter
admission, step order, retry policy, mutation authority, or run settlement.

If cross-run analysis or PR comments are added later, they need their own explicit data/provider
contracts. The persona is not permission to add hidden network calls.

### The Linesman — compiled boundaries

**The boundary was decided before the puck dropped.**

The Linesman explains capabilities and isolation compiled into each admitted step attempt: filesystem,
network, host tools, secret sources, process limits, and related authority.

There is no continuously polling Linesman process and no second violation state machine. A boundary
violation is enforced by the selected isolation/runtime owner and reported through canonical failure
evidence.

### The Penalty Box — quarantine

**When the outcome is unsafe to guess, nobody gets another shift.**

The Penalty Box explains the existing repository-effect quarantine/recovery boundary. It is appropriate
for serious or ambiguous effect outcomes where automatic replay could duplicate or compound a mutation.

It is not a generic bucket for normal non-zero exits. Known failures settle as known failures.

### The Goalie — explicit egress/publication gates

**Nothing leaves the rink on vibes.**

The Goalie explains the exact typed boundary that lets bytes or external state leave the isolated run:
artifact retention, content-addressed OCI publication, tag assertion, immutable Kubernetes image
mutation, and future signing/attestation capabilities when they are actually implemented.

The Goalie does **not** imply a universal final stage. Each external effect keeps its own typed
preconditions and evidence. Signing, provenance attestation, final image scanning, or rollout readiness
must not be displayed as completed unless the corresponding capability exists and produced evidence.

### The Zamboni — attempt teardown and isolation reset

**Fresh ice for the next team.**

The Zamboni explains cleanup of attempt-private workspace, HOME/tmp/cache/config, credentials, sandbox
resources, and other ephemeral authority after success or failure. Cross-run persistence is limited to
explicit durable facts/content references owned by the correct repository.

A retained Warm-Up cache is not leftover ice. It is explicit repository-owned dataflow.

### The Mascot — celebration only

**Never gets a vote.**

The Mascot is ASCII/product chrome. It may appear at app startup, after a clean pass, or as a distinct
failure/recovery illustration. It must never trigger work, hide diagnostics, alter exit status, or turn a
failed run into a friendly-looking success.

Suggested startup/clean-pass mascot:

```text
       ______________
    __/  OH-GOON  #1 \__
   /   [o]      [o]     \
  |          ^           |
  |       \_____/        |
   \____________________/
        /|  ||  |\
   ____/ |__||__| \____        o
  /_____/   /  \   \_____\=====/
          _/____\_

      SKATE HARD. RUN CLEAN.
```

Keep it ASCII in terminals. The web UI may render the same lines as text rather than introducing an
image dependency.

## The game plan

This is the **mental model**, not a second executable DAG:

```text
PRE-GAME
  Enforcer          admission must prove the run can exist
  Equipment Manager declared toolchain/executor authority is resolvable
  Warm-Up           explicit preparation/cache lineage
  Red Team          declared security checks, when present

GAME START
  Team steps        the resolved plan executes exactly as declared
    Trainer          declared retry policy only
    Coach            advisory projection only
    Linesman         compiled boundary enforcement
    Penalty Box      quarantine only for qualifying unsafe/ambiguous effects

EGRESS
  Goalie            exact typed publication/external-effect boundary

POST-GAME
  Zamboni           attempt-private teardown
  Mascot            presentation only

LEAGUE OBSERVATION (outside the execution path)
  The Scout         cross-run workload/resource patterns → Game Tape handoff
```

The product may render this sequence compactly, but it must mark optional personas as optional and must
not fabricate a live status for a persona unless that status is derived from canonical run facts.

## Copy rules

1. **Canonical technical fact first, hockey interpretation second.**
2. Never replace a failure reason with a joke.
3. Never call an advisory item a pass/fail gate.
4. Never say “secured”, “signed”, “scanned”, “isolated”, or “cleaned” without evidence for that exact claim.
5. Never expose a secret to make any observability output look impressive.
6. Never imply retries, cache reuse, network access, or publication happened unless declared evidence says so.
7. Keep the fast language for safe paths: **“Clean ice — Game Start.”**
8. Keep stop language unambiguous: **“Admission rejected — nothing ran.”**

## Product phrases

Use sparingly:

- **Skate hard. Run clean. Fail closed.**
- **No magic. No soft ice.**
- **Clean ice. Predictable shift.**
- **Admission rejected. Nobody left the bench.**
- **Linesman call: authority denied.**
- **Penalty Box: outcome unknown; replay blocked.**
- **Goalie save: publication blocked before egress.**
- **Zamboni complete: fresh attempt state.**

Avoid turning every button into hockey slang. `Run`, `Cancel`, `Runs`, `Recovery`, repository names,
reason codes, and exact technical actions stay literal.

## Live evidence projection

Phases **873–882** move the theme from a static roster into useful operational presentation without
creating a persona runtime.

### Rink Board

Repositories and Runs may render a **Rink Board**, but every field comes directly from canonical Fleet
facts:

- Enforcer copy points to the `RUN_ADMITTED` timestamp exposed as `accepted_at`;
- run status is the canonical `FleetRunStatus`;
- progress is the canonical completed/total repository projection;
- Game Tape evidence count is the number of loaded canonical `OperationReport` records for the same run;
- start/finish times are the canonical run timestamps.

The board does not infer Zamboni completion, Goalie success, Linesman activity, or any other persona
state that is not represented by evidence.

### Game Tape step evidence

Historical Fleet Plan results may render **Game Tape step evidence** per resolved step. It intentionally
owns only the shift timeline:

- step id/kind and recorded step outcome;
- attempt count and per-attempt return code/duration;
- exact tool name/origin when evidenced.

Admission, runtime authority, cache lineage, egress/resource boundaries, artifacts, and published
outputs have one separate owner in the **Game Sheet trust receipt** below. This avoids two themed
components presenting the same fact with subtly different wording.

The Game Tape step-evidence view intentionally does **not** render command argv, stdout/stderr payloads, secret values,
resolved variable values, host-environment bytes, or resolved network addresses. Trainer copy comes
only from canonical `attempt.retry` evidence. It may explain that a retry was scheduled, an exit code
was not declared transient, or attempts were exhausted; it never infers retry intent from the number
of attempts.

### Penalty Box

Recovery may show a **Penalty Box** callout only when canonical `effect_quarantines` are present. The
count is projected from RecoveryStatus. Normal failed steps do not enter this presentation path.
Reconciliation and acknowledgement remain owned by the existing recovery contracts.

### Persona spotlights

Security may visually introduce **The Red Team**, and advanced runtime/tool health may introduce
**The Equipment Manager**. These are explanatory headers only. The underlying controls, findings,
tool status, commands, and failure reasons retain their technical names.

### Executable anti-magic rule

The UX architecture guard rejects a separate hockey Fleet-run projector entirely. The Rink Board renders
canonical `FleetRunState` and same-run `FleetRunDetail` fields directly and may reuse only generic
lifecycle/tone helpers. Hockey presentation code may not add polling, timers, browser persistence,
React-owned lifecycle state, a persona-specific status/state type, or mirrored progress/evidence/authority
objects. If themed UI needs new facts, the correct path is to add or expose a canonical domain/evidence
contract first.

## Game Sheet trust receipts

Phases **883–892** add a second operational projection with a narrower purpose than the Game Tape step timeline:
**prove what authority and evidence the runtime actually recorded for a Fleet Plan operation.** The
Game Sheet is not a new receipt format and is never persisted. It is a pure allowlisted projection of
canonical `FleetRunDetail` / `FleetPlanResult` evidence.

### Rink authority receipt

The live/history Rink Board may expose same-run `isolation_backend` and `executor_capacity` facts from
`FleetRunDetail`:

- exact backend/configuration and kernel boundary;
- whether execution was remote;
- executor/pool identity and capacity source;
- admitted vs requested parallelism;
- executor inventory and health-attestation identifiers.

The same-run guard remains mandatory. Detail from another run is ignored rather than mixed into the
board.

### Enforcer — admission receipt

When `PlanExecutionInputEvidence.repository_admission` exists, the Game Sheet shows the current requirements-based admission contract: canonical validation ID, `requirements_id`, and `requirement_count`. Legacy pyproject/pre-commit digest and hook-count fields are not invented because they are no longer part of current admission evidence. If admission evidence is absent, the UI says **not recorded here**.

### Equipment Manager — runtime/tool authority

The Game Sheet shows the exact isolation backend plus the observed/frozen host-toolchain ID already
captured by execution input evidence. Each tool may show logical name, digest/size, and authority ID;
there is no per-step consumer permission model. Declared host environment **names** may be shown; their
values never may.
Hostname-resolution evidence is represented only as a count. Resolved addresses are intentionally not
projected because the theme has no reason to widen network metadata exposure.

### Warm-Up — explicit lineage

Base-workspace/runtime-root digests, managed/content input counts, attempt cache input/output digests,
and retained-cache reference/owner facts are projected directly. There is no fallback-key, “latest”,
or ambient-cache interpretation in the view.

### Linesman — boundary sheet

For each recorded attempt the Game Sheet exposes the exact egress mode/policy id, resource-policy id
when present, and before/after repository-state fingerprints. These are execution facts, not a live
watcher. The UI never claims which files were “touched” merely from a post-attempt worktree listing.

### Goalie — release ledger and egress manifest

Artifact and published-output entries have one themed owner in the Game Sheet. Presence, byte count,
and digest are shown exactly as evidenced. Release-capable attempts additionally carry typed canonical
release-boundary facts: OCI publish records the immutable registry/repository/digest reference;
Kubernetes image mutation records the exact desired image plus before/after resourceVersion/generation
and distinguishes `COMMITTED` from `ALREADY_DESIRED`. The latter clears the release requirement but
does not claim this run mutated the cluster.

The Goalie never parses workspace metadata JSON to decide that a release happened. A known release
fact survives a later local output/evidence failure, so `run failed` and `external effect happened` can
be true at the same time. Missing release evidence remains an evidence gap. A digest does not mean
“signed”, “scanned”, “rollout healthy”, “rolled back”, or “provenanced” unless a separate canonical
capability produced that evidence.

### Zamboni — canonical attempt teardown receipt

Phases **893–902** close the earlier evidence gap. Strict Fleet Plan execution now records one typed
`PlanStepTeardownEvidence` for every step after artifact/output collection and before another step may
start. The receipt contains only bounded facts: teardown status, the number of attempt roots observed,
whether the step attempt root is absent, and a small path-free reason code when cleanup fails.

A failed teardown is **not** an exception that disappears behind the effect boundary. It is durable
known evidence, makes the Fleet Plan result unsuccessful, and stops all later steps. This prevents a
run from continuing on contaminated attempt state while preserving the known outcome of any external
effect that already completed.

Zamboni still makes a deliberately narrow claim: it proves removal of the step's attempt-private
filesystem roots (workspace, HOME, tmp, cache, config, etc. beneath that root). It does **not** claim
that repository-owned retained cache/content was deleted, that unrelated host state was scrubbed, or
that an external system was rolled back.

The **Final Whistle** is a backend-owned derived read fact. `cleanPass` requires no failed plan step and canonical completed teardown evidence for every strict step. The clean-pass Mascot renders only from that generated receipt; React does not calculate the state. It is not a signing, vulnerability, rollout-readiness, or external-system
guarantee.

### Redaction contract

The backend Game Sheet trust projector is an allowlist rather than a generic object renderer. Its production source must not expose:

- command argv;
- stdout/stderr payload evidence;
- secret bytes or resolved environment values;
- resolved network addresses.

The executable frontend architecture guard checks these forbidden accesses and also keeps polling,
timers, browser persistence, React-owned persona lifecycle state, and persona-specific runtime state out
of all hockey evidence owners.

> **Historical roadmap numbering.** The 913–932 numbers in the two sections below are retained only as historical design-sequence labels. The described rollout-verification and deterministic Trainer-replay capabilities are already present in current source; these numbers are **not** future Oh-Goon checkpoint/version assignments and must not be used as a continuation plan.

## Historical design sequence 913–922 — Explicit rollout verification, no hidden watcher

### Historical design item 913 — Fleet Plan v29 adds `kubernetes_rollout_verify`
Rollout readiness becomes a first-class read-only plan primitive. It is a separate step from
`kubernetes_set_image`; mutation never gains an implicit wait flag or background watcher.

### Historical design item 914 — One Kubernetes transport authority
Set-image and rollout observation share the same attempt-private HTTPS credential/config owner,
ambient proxy/TLS stripping, exact endpoint authority, and zero hidden transport retries.

### Historical design item 915 — Verification binds to the exact release
The rollout step must consume `KUBERNETES_GENERATION` and `KUBERNETES_IMAGE` from exactly one earlier
`kubernetes_set_image` step. Target and credential authority must match that source step or resolution
fails before admission.

### Historical design item 916 — Observation policy is finite and declared
The rollout step owns one explicit timeout, poll interval, and request timeout. Transport retries are
zero and outer Trainer retries are forbidden. Polling exists only inside this admitted bounded step;
there is no daemon, timer service, or automatic replay.

### Historical design item 917 — Readiness means exact generation + image + replicas
Every authoritative GET must still name the admitted Deployment/container. `READY` requires the exact
expected generation/image, observedGeneration at least that generation, and all desired replicas
current, updated, and available with zero unavailable replicas.

### Historical design item 918 — Superseded/deadline/timeout fail closed
A changed generation/image is `SUPERSEDED`; Kubernetes `ProgressDeadlineExceeded` terminates
immediately; exhaustion of the declared observation window is `TIMEOUT`. None are rewritten as success.

### Historical design item 919 — Rollout verification is canonical attempt evidence
`PlanStepAttemptEvidence.release_verification` records a bounded immutable receipt: exact target,
expected/final generation and image, replica counts, final resourceVersion, observation count,
Progressing condition, and declared timing policy. No response body, argv, secret, or resolved address
is persisted in the themed projection.

### Historical design item 920 — Goalie gets a separate readiness ledger
The Game Sheet reports Release Verdict and Rollout Verification independently. A release may have
crossed the boundary while readiness fails; an already-desired release gate may be followed by a
successful read-only readiness proof. Missing verification remains an explicit evidence gap.

### Historical design item 921 — Shipped Kubernetes release template stays two-step
`templates/plans/python-kubernetes-release-module/plan.yml` commits the immutable image first and then verifies
that exact output generation/image. The root OCI release/deploy template consumes this module. Users can
see both operations in the resolved linear plan.

### Historical design item 922 — Contract accounting and anti-magic governance
The admitted authoring contract advances **Fleet Plan v28 → v29** because a new runtime-significant
primitive exists. Canonical operation evidence gains rollout verification, so the pre-live database
advances **epoch 49 → 50**. Executor inventory remains **v4** and external executor report remains
**v3**. There is no v28 plan decoder, epoch-49 compatibility reader, hidden rollout wait, or workspace-
JSON readiness fallback.

## Core rink before deployment adapters

The Hacker Hockey product identity is primarily about the universal CI/CD core: admission, exact tools,
cache/dataflow, deterministic execution, bounded retry, security policy, evidence, isolation, and
teardown. Deployment adapters such as Kubernetes remain supported integrations, but they must not drive
the product model or roadmap ahead of those universal guarantees. A persona should first make the core
run easier to understand and trust; provider-specific depth comes later and stays behind typed adapters.


## Historical design sequence 923–932 — Core rink: deterministic Trainer replay

### Historical design item 923 — Core rink first
The roadmap explicitly prioritizes universal CI/CD guarantees over deeper deployment-provider features.
Admission, tool authority, dataflow, retry, security, isolation, evidence, and teardown are the product
core; Kubernetes remains a typed adapter rather than the model the rest of oh-goon grows around.

### Historical design item 924 — Known-transient retry vocabulary
The old raw retry-code/single-delay shape is removed. Retry-enabled steps declare
`known_transient_exit_codes`, a mandatory replay-safety `reason`, and bounded `max_attempts`.

### Historical design item 925 — Full delay schedule, no hidden backoff
`delays_seconds` contains exactly one admitted delay between every possible pair of attempts. The runtime
does not synthesize exponential backoff, add jitter, parse logs, or change timing from ambient state.

### Historical design item 926 — One Trainer decision owner
`core/fleet_plan_retry.py` is the single pure owner that converts an admitted policy plus one completed
failed attempt into retry evidence. Step execution consumes that decision; it does not reimplement retry
classification.

### Historical design item 927 — Replay-safe generic boundary
Automatic outer retries are rejected for generic steps with network egress. Trainer replay is limited to
fresh attempt-private, network-denied work; typed integrations may own separate explicitly bounded
transport semantics rather than inheriting generic replay.

### Historical design item 928 — Contradictory result codes fail admission
A code cannot be both `success_codes` and `known_transient_exit_codes`. Ambiguous/dead retry intent is an
invalid resolved plan instead of a runtime precedence rule.

### Historical design item 929 — Canonical Trainer decisions
Failed completed attempts record structural `SCHEDULED`, `EXIT_CODE_NOT_DECLARED`, or
`ATTEMPTS_EXHAUSTED` facts. Scheduled replay records the exact policy reason, delay, exit code, and next
attempt number. Absence of retry configuration remains absence rather than a fake status.

### Historical design item 930 — Attempt-chain integrity
Fleet result validation requires consecutive attempt numbers and proves every extra physical attempt is
explained by the immediately preceding `SCHEDULED` fact. An unexplained replay cannot be persisted as a
valid result.

### Historical design item 931 — Game Tape/Trainer evidence UX
Runs shows the canonical Trainer disposition and, when present, its admitted reason, delay, and next
attempt. It does not infer retry cause from attempt count, logs, duration, or error wording.

### Historical design item 932 — Contract accounting and anti-magic governance
Fleet Plan advances **v29 → v30** and canonical retry-decision evidence advances the pre-live database
**epoch 50 → 51**. Executor inventory remains **v4** and external executor report remains **v3**.
Architecture checks require the new retry vocabulary/owner and reject restoration of the old policy
shape. There is no compatibility decoder or migration shim.

## Phases 943–952 — Enforcer records the game, Equipment Manager stops doing paperwork

The previous consumer-scoped tool authority was useful evidence but too much CI/CD ceremony. The
Hacker Hockey rule is now simpler: **the execution environment owns installed tools; the resolved play
says what it needs.** A team writes `uv run pytest`, not a second list saying `uv` is permitted.

Equipment Manager therefore presents observed execution-environment/toolchain facts, not per-step
permissions. Enforcer checks required tool availability before Game Start and leaves a deterministic
`enforcer.yml` game sheet that can be downloaded for any historical strict run. The receipt is derived
from canonical facts and can be compared with `oh-goon enforcer compare`.

The receipt is intentionally useful without becoming a leak: names, hashes, identities, policy IDs,
retry reasons/delays, cache lineage, teardown outcomes, and result fingerprints are fair game; secret
values, argv/stdout/stderr, host repository paths, and raw resolved addresses are not.

This advances Fleet Plan to **v31**, executor inventory to **v5**, and the pre-live database to
**epoch 53**. The theme still owns no runtime state machine.
