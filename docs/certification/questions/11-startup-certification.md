# Section 11 — Startup and certification boundaries

## Q61
From a fresh source extraction, what is the shortest supported path from dependency materialization to final certification, and why is no separate certification dependency setup required?

## Q62
What does `make init` own, and why must certification reuse that native materialization boundary instead of inventing a second dependency setup path?

## Q63
Why does lean certification deliberately avoid a separate offline dependency replay/sealing stage, and which native tools own dependency acquisition and cache reuse instead?

## Q64
If canonical `make init` cannot complete because required registry/TLS infrastructure is unavailable, how must Oh-Goon classify that initialization result and what alternate dependency proof must it avoid?

## Q65
Why may `backend/.venv` and `frontend/node_modules` exist in a developer checkout but never become source identity or certification evidence?

## Q66
Who proves that a strict CI/CD source is a Git repository root, and why must the sanitized immutable execution snapshot not be required to contain `.git`?

## Q67
A certification run fails after it starts. Which retained evidence should a developer use first, and what must diagnostic logging never become?

## Q68
A startup prerequisite looks wrong. What is the role of `make diagnostic-system-check` versus `make init`, and which one is allowed to materialize developer dependencies?

## Q69
Who owns the strict execution source-drift proof, and why must read-only source observation not re-hash the frozen workspace before attempt creation?

## Q70
When a sanitized source snapshot or retained content object is copied into an execution workspace, how many identity meanings and source-side hash proofs should exist? Explain the fast safe path for Fleet-owned frozen inputs.

## Q71
A strict run freezes the union of host tools and imported host-environment values needed by all active Steps. What authority may one physical Step attempt actually receive, and why must it not borrow another Step's declarations?

## Q72
A strict Make-based Step fails with `make: printf: No such file or directory`. Why is "PATH is over-sanitized; add baseline coreutils" an unsafe first diagnosis, and what should be checked before changing the sandbox environment?

## Q73
When `make certify` starts, what owns the human-facing Oh-Goon product/checkpoint version and how is the current-checkout runner selected without mirroring backend/frontend package versions?

## Q74
A strict Step generates `backend/.venv/bin/python3` as a symlink to a sandbox runtime path outside `/workspace`. Why must Fleet neither ignore that escape nor treat it as ordinary source escape, and what must happen before the workspace is published to later Steps?

## Q75
A patched source checkpoint is handed to another developer for another certification run. How must the product version and ZIP name change, and where should PENDING/BLOCKED/proof-state detail live?

## Q76
`generated/release/static` is excluded from source identity but required by release packaging. Why must ordinary `make init` and APP certification avoid creating release staging state?

## Q77
Why must APP certification consume the exact native uv project environment already established by `make init` instead of copying, rewriting, or recreating a certification venv?

## Q78
Who owns certification/setup command-heading color, and what TTY/`NO_COLOR` rules must every supply/setup/retry banner follow?


## Q79
Who owns backend lock validity and project-environment materialization, and why must Oh-Goon not hash, normalize, or reconstruct `uv.lock` into a second dependency identity?

## Q80
What is the repository-wide native-tool authority rule, how does it apply beyond package managers to tools such as Docker/BuildKit, Kubernetes, Git and operating-system process facilities, and how do you distinguish legitimate Oh-Goon orchestration from a ghost implementation of native semantics?

## Q81
How must Oh-Goon execute repository Python commands, and when must a repeated Python operation become a named `backend/pyproject.toml` script instead of a source-file, `PYTHONPATH`, or ambient `python` command?

## Q82
How must frontend/package operations be executed, and why must Make/Goons prefer `npm ci`, `npm version`, `npm install --package-lock-only`, and `npm run <script>` over direct `node`, `npx`, lockfile editing, or parallel npm semantics?

## Q83
When may Oh-Goon read or hash an ecosystem lockfile after the native tool has validated it, and what semantic conclusions must never be derived from those bytes?

## Q84
Why must Oh-Goon not wrap native package-manager retry semantics with its own retry algorithm, and how should native transport failure be reported?

## Q85
How must certification pass operator-owned native uv TLS configuration such as `UV_SYSTEM_CERTS`, and why must Oh-Goon neither clear that environment nor source `~/.bashrc` to reconstruct it?

## Q86
How must certification obtain an operator-required Python package index, and why must Oh-Goon neither hardcode an internal repository nor parse/copy uv configuration or cache internals?

## Q87
Which verification surface owns Playwright browser runtime diagnostics now that the lean APP certification Plan no longer replays the full browser matrix?

## Q88
What is the semantic difference between uv `--locked` and `--frozen`, which Oh-Goon operations consume or intentionally author the committed backend lock, and why is `backend/uv.lock` now exact portable dependency identity?

## Q89
Why must `make init` own dependency materialization exactly once before APP certification, and why must `make certify` consume that initialized authority instead of reacquiring packages or creating a certification-specific supply root?

## Q90
Who owns private/public Python registry configuration, and which repository, evidence, logging, and packaging surfaces must never disclose operator-private registry identity?

## Q91
Who owns Oh-Goon's local `backend/uv.lock`, where may it exist, which identities may use its SHA, and why must it never enter public source/package artifacts?

## Q92
Why is committed `backend/uv.lock` the sole exact Python resolution authority, how is it separated from `backend/pyproject.toml` dependency intent, and which registry/path identities are forbidden from public lock bytes?

## Q93
How must a fresh `make init` consume committed `backend/uv.lock` with frozen semantics without reconciling or rewriting it, and why is `make sync` the only ordinary dependency-authoring boundary?

## Q94
Why must dependency resolution evidence preserve universal Python/platform marker forks even though native uv, not Oh-Goon, owns marker and registry-version resolution?

## Q95
What distinguishes committed dependency resolution identity from a locally materialized project environment, and why are raw uv/npm caches not independent certification identity?

## Q96
Which dependency write/read authority belongs to `make init`, `diagnostic-dependency-check`, `sync`, `verify`, and `certify` under the lean native-tool model?

## Q97
Which machine-enforced source/privacy guards prevent a local lock, private registry identity, or package-manager cache state from leaking into public source, patches, logs, or generated evidence?


## Q98
Why is removing a private registry URL insufficient when the registry contains proprietary packages, and what publication rule applies to package names/versions retained in public dependency evidence?

## Q99
What dependency/environment facts are portable release inputs, which are host-local materialization facts, and why must exact `.venv` or `node_modules` tree bytes not become a cross-host package-manager identity?

## Q100
How does the public/private registry boundary apply to npm and Playwright as well as uv, and what must happen if a committed `package-lock.json` resolves packages through an operator-private npm registry?

## Q101
How must direct-origin metadata and persisted evidence be handled so local paths, VCS endpoints, registry hosts, or credentials cannot leak through `direct_url.json`, packaged source, handoffs, or retained public evidence?


## Q102
Why are single-version marker-rendering differences non-blocking evidence while marker-qualified multi-version forks remain hard bootstrap authority, and which owner evaluates PEP 508 markers?

## Q103
Why must Oh-Goon report native tool requirements without parsing or enforcing their version semantics itself, and which native operations remain authoritative for uv, Python, Node, and npm compatibility?


## Q104
How must long-running init/verify/certification steps report elapsed time without adding another execution owner or leaking recursive Make checkout paths?

## Q105
If native uv materialization creates a runtime edge that escapes the strict execution boundary, which Oh-Goon isolation owner must reject or capture it without rewriting uv environment semantics?

## Q106
What does `make frontend-install` own now that npm directly owns installation/cache semantics, and why must Oh-Goon not add a package-lock marker or graph cache to skip native `npm ci`?

## Q107
How should certification bind native tool/environment evidence without precomputing a separate dependency-supply snapshot or mutating the materialized project environment?

## Q108
How may Oh-Goon hide operator-specific absolute paths from normal certification output without changing native package-manager commands, cwd, environment, retry semantics, or machine-oriented exact-path diagnostics?

## Q109
How must immutable Fleet retained content be copied and verified generically, and why must that mechanism not become a certification-specific dependency supply path?

## Q110
Which identities belong to generic retained content and which dependency facts remain owned by native package managers under lean certification?

## Q111
Why must certification sibling-CLI lookup preserve the invoked workspace interpreter path instead of resolving a symlinked `.venv/bin/python` to the base uv-managed Python installation?


## Q112
Why must immutable/read-only retained-content directory modes be applied to a staging copy only after children are materialized, independent of certification dependency handling?

## Q113
Why may a completed read-only content-store staging directory root need temporary owner-write permission during Linux cross-parent publication, and how must Oh-Goon restore the prepared mode without weakening child identity?

## Q114
Why must Oh-Goon avoid a repository-owned developer `node_modules` cache even when mounted filesystems make npm slower, and what optimization authority remains with native npm?

## Q115
How must first-checkout `make init` present timing and success consistently, and how must later APP certification report that it consumes rather than repeats that completed materialization?

## Q116
Why is `make init` the single idempotent developer materialization frontdoor for both fresh and existing checkouts, and why must Oh-Goon not add a second developer setup/acquisition path?

## Q117
Why is there no certification-specific supply reuse decision anymore, and which native package-manager mechanisms own cache reuse across repeated `make init` runs?

## Q118
Why must normal `make certify` strip and never require strict executor delegation, when is `make strict-executor-setup` appropriate for explicit strict release/CI or strict-acceptance execution, and which owner decides that strict authority is actually required?

## Q119
How does `make certify` require dependency readiness before any APP Goon runs, and why must it consume the already-materialized native environment rather than run a second materializer or freshness resolver?

## Q120
How must `make certify` expose live progress through the existing run-log and Game Tape owners without buffering output, creating a second execution/logging path, or presenting transient observed activity as durable Run state?

## Q121
How must APP verification preserve the actual Python runtime already selected by the native project environment without selecting another interpreter or maintaining a certification Python supply contract?

## Q122
Why must developer startup use native `make init` as the single dependency-materialization boundary while APP certification only consumes that result, and what Oh-Goon-specific responsibilities remain afterward?

## Q123
Why must developer frontend materialization delegate directly to native `npm ci` rather than reusing an Oh-Goon-owned `node_modules` cache, symlink publisher, marker, or graph validator?

## Q124
Why should certification avoid repeated whole-tree dependency hashing/replay when native package managers already own materialization, and where is content hashing still legitimate?

## Q125
When may Fleet reuse an already-retained content-addressed tree, what must still be reverified, and why is this generic retained-content authority separate from package-manager dependency caches?

## Q126
Why must frontend execution preserve npm/Node `node_modules` semantics rather than renaming/mirroring the tree or adding Oh-Goon package-resolution workarounds?

## Q127
How must local APP certification prove enough effective memory for strict workload plus control-plane headroom without coupling that proof to dependency-manager state?

## Q128
How must strict Fleet execute native uv-generated Python launchers without rebuilding dependencies, mutating launchers, or widening PATH merely to satisfy certification?

## Q129
How must Fleet freeze the native shared-library closure of Python stdlib extension modules such as `_ssl`, and why is freezing only the interpreter binary's `ldd` dependencies insufficient?

## Q130
Why must certification-generated `goon.lock` bytes live in isolated certification state instead of the source checkout, how can `plan run` still prove the exact locked execution through an explicit lock path, and why should a raw storage `EIO` be treated as an environment/storage failure rather than a source defect?
## Q131
Why must first-checkout init invoke native uv/npm directly instead of placing an Oh-Goon filesystem/cache admission model in front of them, why must certification separately prove its owned mutable state before admission, how must mid-run host-storage loss such as EROFS/EIO/SQLite open failure be contained without being misreported as a source/test failure, and why must run-log/diagnostic persistence remain non-authoritative when storage disappears?

## Q132
Why must `oh-goon self verify` and `oh-goon self certify` select canonical shipped Oh-Goon Plans through the normal resolver/lock/admission/executor/evidence path, why is a public per-unit execution bypass forbidden, and how may diagnostic sharding or artifact packaging remain useful without becoming a second verification engine?

## Q133
When a strict Step command succeeds but teardown of its disposable private attempt fails, how must Oh-Goon preserve cleanup enforcement while avoiding an incomplete repair handoff, and when may the cleanup owner restore delete permission?


## Q134
In the Phase-0 Launch Manifest baseline, which components owned contributor and built local launch responsibilities, which duplicate/gap was identified for later convergence, and why did the first Launch Manifest model have to separate intent from provenance/generation-bound observed facts without gaining process or secret authority?

## Q135
How must the first Launch Manifest runtime bridge record contributor/built startup and readiness without creating a second runtime system, how is the startup-before-spawn race prevented, and what positive evidence is required before READY or terminal failure may be projected?

## Q136
At the 1267.0.38 deterministic-status boundary, how must `oh-goon status` resolve the current launch for one workspace without directory scans or recency heuristics, when may a workspace binding be replaced, and why did owner-proof uncertainty have to remain separate from the last authoritative lifecycle before process-birth proof landed?

## Q137
How must Launch Manifest prove a live local launch owner without confusing PID presence with process identity, why are launch-owner and service-process identities separate, and what must status do when process birth proof is unavailable or mismatched?

## Q138
How must repeated `oh-goon start` or contributor `make dev` decide whether to start a new local launch, reuse the exact existing launch, or report a conflict, and which ownership facts must be positively proven before reuse is safe?

## Q139
How must `oh-goon open` derive a safe browser target and local-access capability from the exact current launch without treating configured endpoint intent, a guessed port, a new token, or raw Launch Manifest serialization as runtime truth?

## Q140
Why must APP certification declare `ldd` as explicit backend-test Step tool authority, and how must long certification progress remain visibly live even when the durable sanitize/log pipeline or caller UI batches ordinary output without turning progress into a second evidence or execution owner?

## Q141
How must `oh-goon logs` and `oh-goon stop` consume Launch Manifest truth without becoming a second native logging or process/lifecycle owner, and why must stop use exact process-birth proof plus a stable pidfd rather than a PID signal alone?

## Q142
How must Oh-Goon statically validate every canonical Goon before workload execution while keeping dependency acquisition outside APP certification and preventing candidate-controlled requirements from self-authorizing new capabilities?

## Q143
How must pre-execution authority closure protect certification control state and SQLite identity without treating dependency-manager caches or materialized environments as certification control truth?

## Q144
How must strict Fleet admission prove executor/tool feasibility for the repository-applicable workload before `RUN_ADMITTED`, how must shared native workload boundaries own their tool requirements, and why must preparation still revalidate that authority afterward?

## Q145
How must certification static admission invoke its compiler when native uv may launch the console script through a base interpreter outside the project environment, and how must credentials, retained-input metadata, and immutable executor resource/isolation feasibility be rejected before queueing without turning cheap preflight into stale execution evidence?

## Q146
How must bootstrap delegate source-owned native-tool compatibility to the native tools while still failing early on their authoritative errors, how must long certification preparation remain visibly live before Step output exists, and how must the admitted SQLite journal distinguish path replacement from database-format/schema validation—including a transient SQLite format observation—without creating duplicate authorities or replaying execution?

## Q147
Which local byte classes remain durable Oh-Goon state, how are unclassified bytes below an Oh-Goon cache root treated, and why are native uv/npm caches outside Oh-Goon storage authority?

## Q198
How must pre-execution authority closure keep certification control state durable while the already-completed native dependency materialization remains outside certification control-state identity?

## Q199
Negative-boundary challenge: How must strict Fleet admission prove executor/tool feasibility for the repository-applicable workload before `RUN_ADMITTED`, how must shared native workload boundaries own their tool requirements, and why must preparation still revalidate that authority afterward? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q200
Change-impact challenge: How must certification static admission invoke its compiler when native uv may launch the console script through a base interpreter outside the project environment, and how must credentials, retained-input metadata, and immutable executor resource/isolation feasibility be rejected before queueing without turning cheap preflight into stale execution evidence? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q201
Failure-diagnosis challenge: How must bootstrap delegate source-owned native-tool compatibility to the native tools while still failing early on their authoritative errors, how must long certification preparation remain visibly live before Step output exists, and how must the admitted SQLite journal distinguish path replacement from database-format/schema validation—including a transient SQLite format observation—without creating duplicate authorities or replaying execution? Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q202
How must storage lifecycle distinguish durable certification state from unclassified Oh-Goon cache bytes and native package-manager caches?

## Q220
Why must built frontend release bytes have one lifecycle owner at the standard-wheel/release boundary, and why must developer init, source normalization, editable installation, and source ZIP construction avoid fabricating `generated/release/static` merely to satisfy packaging?
