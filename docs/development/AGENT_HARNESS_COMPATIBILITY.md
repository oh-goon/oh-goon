# Native Agent harness compatibility

Baseline source review: **2026-10-26**; later scoped observations are dated
separately below. This is the canonical maintenance inventory of
native compatibility adaptations and the conformance tests that can establish
their replacement. It does not resolve capabilities, select versions or admit
execution. The [runtime owner map](AGENT_RUNTIME_OWNERS.md),
[provider guide](../AGENT_PROVIDERS.md) and [contributor contract](../../AGENTS.md)
retain those responsibilities.

The [final scoped qualification](AGENT_RUNTIME_OWNERS.md#final-scoped-native-qualification)
links the requirement/vector results and cumulative subtraction review. Each
dated observation below keeps its original source, policy, mode and environment.

The tables map existing source and tests; a test reference is not a passing test
result. Installed qualification is scoped to the exact selected implementation,
mode, policy and environment. The first installed table retains the 2026-10-26
observations; later dated observations update only their stated scopes. Other
installed cases remain **NOT_RUN** in those reviews. Native executable facts remain
in the frozen selection and original execution evidence, never in a second
version database here.

Native protocol adaptations operate below the common FleetRun/Step lifecycle;
owned native daemons and sessions do not become Run custody or recovery owners.
The [Run-worker boundary](EXECUTION_SUPERVISOR.md) uses a fresh process and atomic
SQLite claim; HUG-181/HUG-183 retain broader qualification.
The historical installed observations below do not qualify the worker cutover
or create a CLI/service compatibility route around its required atomic claim.

Frozen-source diagnostic correlation uses the same private-query-file Hashmarks
command already available to native Agent prompts. Disposition: **retain** the
existing context, protocol, settlement and filesystem adaptations; the command
adds no native protocol or mount authority. Real producer and subprocess checks
qualify its source/uncertainty/privacy handoff. Installed-harness use of the new
follow-up remains **unqualified** until exercised for that selected mode; earlier
installed results do not establish it.

Resource-admission disposition: **retain** the protocol, settlement and
reserved-directory adaptations below. Capped standard execution now verifies
that native scope placement retains the caller's aggregate limits; see the
[resource boundary](AGENT_RUNTIME_OWNERS.md). Installed-harness qualification in
a changed resource context remains **unqualified** until separately exercised.
The earlier installed results retain their recorded mode and environment scope.

Whole-Run `run.yml` budgets currently exclude Agent and repair workflows.
Disposition: **retain** the existing harness adaptations; this CLI resource
boundary does not replace their protocols or qualify another harness context.

Native prompt admission uses the same compact UTF-8 JSON encoding for the frozen
task/Skill lower bound and the complete delivered prompt. A definite lower-bound
overflow rejects before evidence materialization or producer work; fitting that
subset does not waive the final complete-input check. The original whole-Step
deadline still covers producer preparation and native execution. Disposition:
**retain** protocol, context-delivery and settlement adaptations; this receiving
input guard adds no installed-harness or version qualification.

Grader-source privacy disposition: **retain** the existing protocol, settlement
and reserved-directory adaptations. The common source-admission owner refuses
private-bearing native workspaces before harness invocation; it does not add a
native permission flag or infer a filesystem deny from a filtered prompt.
Existing installed evidence retains its original environment and mode scope.

| Installed check on this Linux host | Result and precise limit |
| --- | --- |
| OpenCode 2.1.2, [service readiness and stdin lease][opencode-installed-service] | **PASS**, two tests: bounded authenticated readiness/PID, client closure preserving the server, original-owner cancellation, plus actual `--stdio` endpoint discovery and parent stdin closure producing native exit 0 and process quiescence. No service-session execution qualification. |
| OpenCode 2.1.2 and Pi 1.3.0, [common CLI read-only review][cli-readonly-tests] | **PASS**, two cases through disposable managed bootstraps and local providers: exact task/Skill/qualified Hashmarks delivery on every request, native shell follow-up, complete typed review, selected source/Git/host boundaries, independent verification Step and quiescence. Pi selects its exact public release entrypoint/root; its user launcher is not qualified. No MCP conformance is claimed. |
| Codex 0.164.0, OpenCode 2.1.2 and Pi 1.3.0, [CLI lifecycle][cli-lifecycle-tests] | **PASS**, twelve cases: success, streaming error after completed-looking text, caller cancellation and deadline for each implementation. Pi's terminal error fails despite native exit 0. Cancellation retains its original process receipt/settlement through independent Journal reopen without new inference. Interrupted Bubblewrap supplies no final isolation acknowledgement, which remains absent/unverified. |
| OpenCode 2.1.2 and Pi 1.3.0, [CLI compaction][cli-compaction-tests] | **PASS**, two cases: native streamed context overflow, actual native summarization and continued typed review through the original Run. Every request retains the exact task, complete Skill and qualified Hashmarks packet; continuation carries frozen output identity and the returned summary. Independent verification and original process quiescence pass. No real-account or model-quality qualification. |
| Codex 0.164.0, [installed service suite][codex-installed-service] | **PASS**, three cases: model-free phase/store boundaries plus native follow-up/resume/compaction with absent and populated workspace `.codex`, using the production policy. Reserved-content operations are denied even with a writable repository; normal repository writes succeed. No real-account or cross-Run session-adoption qualification. |
| Codex 0.164.0, [CLI context][codex-context-tests] | **PASS**, five local-fixture cases: synthetic API-key/ChatGPT authentication × HTTP/WebSocket context follow-up and compaction, plus protected managed Code Mode. The headless sign-in path sends exact task-only stdin and retains the complete frozen privileged payload. Its separate eight-case baseline/candidate comparison also preserves native tools, response chains and compaction resets. No live-account or model-quality qualification from these fixtures. |
| Codex 0.164.0, GPT-6.1 Sol at low effort, [live coding][codex-live-tests] | **PASS**, one full-stdin baseline and one task-only candidate through the original local Run and existing native sign-in. Both edit the inclusive-range sample and pass two unchanged independent verifiers with verified cleanup. Privileged payloads remain intact; stdin changes from 8,109 to 530 bytes. This is bounded coding equivalence, without a token-cost, refresh, private-store or universal model-quality claim. See [issue #22](https://github.com/oh-goon/oh-goon/issues/22). |
| Codex 0.164.0, [managed review][codex-managed-tests] | **PASS**, three local-fixture entrypoints: original native Run, initialized catalog Plan and initialized catalog Goon, preserving the selected source/tool boundary and complete typed review. No live-account or model-quality qualification. |

The recurring native review uses the public Pi 1.3.0 release entrypoint on Linux
with Bubblewrap 0.11.1 and a controlled Chat Completions endpoint. Disposition:
**retain** the ephemeral `--no-session` selection, managed broker disclosure,
typed review parsing and original Step settlement. The public CLI and API proof
uses two wall-clock occurrences across an app restart. Each reopens the exact
approved source and captured Skill in a separate Run and Step process. Peer notes
stay on their existing task-scoped tools, and a selected Markdown export adds no
native extension. Other hosts, persistent sessions and live models need their
own evidence. [Scheduled review proof](../../backend/tests/integration/services/test_native_review_schedule_cli.py) · [Live note proof](../../backend/tests/integration/services/test_native_parallel_agent_notes.py)

## Frozen context continuity — 2026-11-05

[HUG-36](https://linear.app/hugloss/issue/HUG-36) requalified the context paths
with Codex 0.164.0, OpenCode 2.1.2 and Pi 1.3.0 on Linux, using explicit public
runtime roots and synthetic local model providers. The complete source/candidate,
native identity, commands and per-mode results are recorded with that issue's PR.
These observations supersede the older context rows only within these scopes:

| Selected context path | Fresh proof and limit |
| --- | --- |
| Pi/OpenCode managed CLI, Chat Completions | Four installed cases passed: read-only native tool follow-up and actual overflow compaction for each implementation. Every upstream privileged context retains the exact original task outside the complete Skill/producer packet; independent verification and original cleanup remain required. Pi uses its exact public release entrypoint, not its user launcher. |
| Codex CLI, Responses | Five installed cases passed: synthetic API-key/ChatGPT authentication × HTTP/WebSocket follow-up and compaction, plus protected managed HTTP Code Mode. HTTP fallback and native tool controls remain scoped to the exercised modes. |
| Codex service | Four installed cases passed: original Run automatic compaction with successful and failed independent verification, plus native same-thread resume/explicit compaction with absent/populated reserved directories. Native protocol resume does not qualify product cross-Run session adoption. |

The managed broker previously restored Skill/evidence while relying on native
history for the task. Four protocol regressions reproduced task loss after a
task-free follow-up; the receiver now supplies the frozen task explicitly and
the broker restores all three components on every request. Missing task, Skill
or evidence refuses before channel creation. The full 95-case broker owner
passed, including 13 new task-free/invalid-context cases. A separate real-CLI
handoff case keeps forged success/permission/configuration claims untrusted and
still executes the failing independent verifier.

The same context owners retain six ordinary controlled-process counterparts
through the real receiving Step and broker. The four existing report/verifier
controls were moved from the general native Run tests and strengthened, with
two read-only role controls added. Dropping task restoration is detected even
when the native fixture and independent verification otherwise succeed. These
bounded A-level cases preserve ordinary merge proof without changing the
installed certification markers or claiming a compaction implementation.

The first strengthened protected Codex case exposed a fixture comparison to the
old whole-message wording. Its replacement checks exact full evidence/Skill and
the task in the privileged channel; the isolated rerun passed. The other eight
CLI cases and four service cases passed. This is eventual scoped qualification,
not a claim that every initial invocation passed.

Disposition: **retain** the existing protocol, settlement and reserved-directory
adaptations. Their removal gates were not selected or satisfied by this task.
Task delivery is a permanent product responsibility, not a new version-specific
workaround. No installed Anthropic/Ollama parity, OpenCode service execution,
Pi persistent-session integration, live accounts, other hosts or Run-worker
custody is qualified by these results. Native harnesses still own compaction.

## Frozen selection and native session identity — 2026-11-05

[HUG-28](https://linear.app/hugloss/issue/HUG-28) checks configuration changes
through the [claimed Run-worker boundary](EXECUTION_SUPERVISOR.md). Native
selection remains one captured catalog, resolved executable, profile and model
connection. A later catalog/default/provider update requires a fresh selection
and admission; it cannot replace an already running Step's configuration.
The existing scoped Git command owner is transported as resolved facts and
restored inside the worker. Arbitrary injected callbacks remain refused.

Native service clients retain their original endpoint, thread and permission
profile. Foreign-thread progress is filtered; wrong-turn observations cannot
borrow an owned turn's terminal result. Closing one client closes its transport,
without granting cancellation or cleanup authority over another client or an
operator-owned server. These are separate accepting boundaries from physical
transport-peer attestation, which was unqualified at that review. The later
HUG-38 section below updates only the selected owned Linux peer-binding scope.

Product cross-Run native-session adoption, persistent Pi sessions and OpenCode
service-session execution remain unsupported. Cold resume cannot supply missing
original instructions or silently change the model/profile. Native data is not
patched, migrated or deleted to make continuation succeed. The native harness
retains compaction and session storage; no generation manager, alternate worker
or transcript interpreter is introduced.

The selected Codex 0.164.0 generated protocol describes native RPC failures with
an integer code, message and untyped data; it provides no exhaustive typed
model-missing/session-missing distinction. Opaque native refusal remains
unclassified, without inferring that a known thread disappeared. A model-turn
refusal retains the original thread/instructions for explicit native resume;
cold resume without original instructions refuses locally before an RPC. No
model catalogue fallback or resumed-model migration is implemented.

Focused evidence covers the following scopes; these rows do not replace the
historical full requirements crosswalk:

| Boundary | Executed proof |
| --- | --- |
| Catalog/default/provider/executable change (T09, T35–36) | The 60-case native selection owner passed, including catalog replacement after frozen selection and alias retarget with original-executable loss. |
| Worker transport | The 17-case worker configuration owner and 28-case native isolation owner passed. Arbitrary callbacks, changed workspace pin/backend/observation and weakened transport bindings refuse. An equal-looking foreign callable cannot impersonate the canonical workspace pin. |
| Actual Run selection (T09) | A controlled native process runs under a real claimed worker while its provider turn is held across catalog replacement. The old frozen connection remains selected; B uses its own newly selected runtime and fresh Run. Independent verification and physical cleanup remain separate. This preserves the one-active-Run limit. |
| Native service identity (T10–12) | Fifteen focused portable service cases passed: interleaved original/new clients, foreign-thread filtering, stale-turn agent/tool/compaction rejection with valid controls, original cleanup, cold resume/restart refusal and diagnostic-sink invariance. OpenCode delayed readiness remains readiness-only. |
| Existing refusal/identity contracts (T20, T35–36) | Ninety-six bounded existing CLI/native/core cases passed. Unsupported Pi continuation refuses before original native access; changed source/selection and old-cycle completion cannot acquire fresh authority. |
| Context protection retained | All six ordinary context counterparts passed through the new worker. Their observer reads the actual bounded worker command input while the controlled request is live; task-free message history is unchanged. Erasing only broker task restoration still fails the original privileged-task assertion; restoring exact source passes. The real-CLI forged-handoff regression also passes. |
| Installed native protocol | Codex 0.164.0 same-thread follow-up, explicit resume and compaction with absent reserved directories passed on Linux with synthetic state/provider in a loopback-only namespace. Original protected-state and process-quiescence checks remain required. This native protocol slice is distinct from the controlled Run-worker proof. |

Disposition: **retain** the existing protocol, settlement and reserved-directory
adaptations. This change selects no native upgrade or adaptation removal.
Entrypoint-byte verification does not seal the complete imported native runtime,
and this scoped identity proof does not qualify live accounts, arbitrary native
reload, other hosts, strict custody or parallel mixed-harness Runs.

## Protected native tools and owned peer binding — 2026-11-05

[HUG-38](https://linear.app/hugloss/issue/HUG-38) binds the owned Linux Codex
service connection to the daemon launched by its original Step command owner.
The existing process lifecycle callback supplies a `ProcessBirthIdentity`; the
client checks the actual WebSocket's Unix peer, matching birth and positive
process liveness before initialization and every request, after receiving data
and before returning queued notifications. A
pathname, readiness reply or native-reported PID cannot substitute for that
connection evidence. A zombie or unobservable process cannot retain authority
from the kernel's connection-time credentials. This is not continuous
per-message sender or descriptor-delegation attestation. Unknown introspection
or changed identity refuses before model work; the original command owner still
supplies cancellation and cleanup.
Status-only connections remain explicitly unqualified.

Controlled real-process peers reproduce replacement before connection and
pathname rebind after readiness. A valid original connection keeps its peer;
another process at the same pathname cannot inherit the old authority. Missing
or unusable daemon-birth observations and missing socket introspection also
refuse, with valid controls and the original process-quiescence receipt retained.
No second probe socket, generic session lease or supervisor is introduced.

The selected native Codex parser also accepts attached `-m`, `-c` and `-C`
arguments. Existing managed-model/configuration and owned-workspace guards now
reject those forms before producer/native work; ordinary native arguments and
Pi's opaque option values retain their existing behavior. The framing evidence
uses Codex 0.164.0 valid-help and unknown-option controls, not inference from
OpenCode's different help behavior.

Private repository/Skill hardlinks and a Skill-root alias into the selected
native home refuse before private content opens or a Run is admitted. Public
Skill aliases and ordinary source capture remain valid controls. Seven ordinary
Run-entry privacy cases now live beside their actual service Run owner; the
previous standalone file is removed with its case bodies and protection
conserved. Native stores, plugins and MCP remain native-owned; no generic
credential/configuration API is offered to model tools.

Fresh focused proof is scoped as follows:

| Boundary | Executed result and limit |
| --- | --- |
| Owned peer (SEC-04/T23) | Two real Unix-socket cases pass after reproducing the old replacement gap. Valid peers initialize and receive instructions; a foreign peer refuses before initialization, a rebound pathname preserves the original connection, and later birth or false/unknown liveness refuses without another RPC. Twelve existing native process-state cases and the unknown-introspection negative pass. Missing/invalid original daemon observations refuse with original cleanup receipts. |
| Source privacy (SEC-01/T21) | All seven ordinary Run-entry privacy cases pass at their new owner, including hardlinks/native-home aliases and successful public-source controls. The old-to-new case census and source-preservation review retain the removed file's protection. |
| Native argument authority (T24) | Attached Codex model/configuration/workspace forms each reproduced a missing refusal. Focused receiver/command checks pass after repair, with valid original inputs, opaque native options and non-echoing errors retained. |
| Installed native phase/store policy | Codex 0.164.0 on Linux: three cases pass for the model-free review/repair policy and production-policy continuation with absent/populated reserved directories. All four synthetic store classes deny read/write/rename/delete and in-Run hardlink creation; allowed scratch/scoped repair and a scratch hardlink succeed. Traversal, private CWD, symlink/subprocess denial and unchanged canaries remain checked. A random canary exists in the exact owned daemon's synthetic environment, while each phase's tool environment omits its key and cannot read that daemon's `/proc/PID/environ`; the value is never supplied as a probe argument. The actual registered native MCP RPC is refused with an empty workload catalog and no tool call reaching the synthetic server. |
| Installed original worker/tool paths | Two cases pass: protected managed Code Mode and the configured-MCP review/repair campaign. The latter retains ten original Step execution facts/empty catalogs and ten real Code Mode exclusion probes, unchanged synthetic native config and no MCP `CALLED` event. Exact RPC refusal is the separate model-free native proof, not an inert parent-process callback or an invented Code Mode API. |

These five installed cases use the selected complete public runtime and
synthetic state in a loopback-only namespace; inference uses local fixture
providers. Worker-aware
fixture observations consume existing wire data and bounded command input;
they do not fill in missing Task/Skill/Hashmarks content or inject callbacks
through the worker handoff. Source/typed-result, independent verification and
original physical cleanup assertions remain in place. No live account or model
reasoning quality is qualified by the fixtures.

Disposition: **retain** the existing protocol, settlement and reserved-directory
adaptations. This is peer/argument enforcement and scoped qualification, without
a native upgrade or compatibility removal. Native MCP discovery may initialize
registered servers before a workload thread; MCP-startup isolation remains
unsupported under HUG-124. OpenCode service execution, Pi persistent sessions,
cross-Run native-session adoption, live accounts, other hosts and strict custody
remain outside this proof.

## Shared conformance and compatibility subtraction — 2026-11-05

[HUG-8](https://linear.app/hugloss/issue/HUG-8),
[HUG-9](https://linear.app/hugloss/issue/HUG-9) and
[HUG-49](https://linear.app/hugloss/issue/HUG-49) reconcile the existing matrix
and registry after the claimed-worker cutover. The selected public implementations
are Codex 0.164.0, OpenCode 2.1.2 and Pi 1.3.0 on this Linux host. Pi uses its
exact released child entrypoint and complete public release root; the user
launcher/authentication root is not a qualified runtime. Executable digests and
loaded Hashmarks identity accompany the bounded native receipts, without
claiming a seal of every imported runtime byte.

The existing native pytest matrix supplies these automated results. **PASS**
means the stated behavioral assertions passed, not that an intentionally failed,
cancelled or timed-out Run succeeded. **REFUSED** is an exercised product
boundary; **NOT QUALIFIED** is not promotion of historical or optional evidence.

| Current scope | Codex 0.164.0 | Pi 1.3.0 | OpenCode 2.1.2 |
| --- | --- | --- | --- |
| Headless managed CLI success, later native error, caller cancel and deadline | PASS, four installed cases | PASS, four installed cases | PASS, four installed cases |
| Native shell, read-only typed review and independent verifier | Lifecycle/source proof above; protected Code Mode has the separate HUG-38 scope | PASS, installed read-only case | PASS, installed read-only case |
| Native context-overflow compaction and retained task/Skill/Hashmarks | Separate HUG-36/HUG-38 Codex context scopes, not rerun as new results here | PASS, installed overflow case | PASS, installed overflow case |
| Owned service follow-up, same-thread resume/compaction, reserved directory absent/populated | PASS, two installed production-policy cases; original peer/liveness and cleanup retained | No product service mode | REFUSED execution; authenticated readiness is not session authority |
| Fresh cross-harness Run handoff | PASS A-level Codex → Pi → OpenCode process fixture, fresh scoped runtime per selection, original failed verifier/readback preserved | Same fixture; ephemeral `--no-session` | Same fixture; separate original admission |
| Product cross-Run native-session adoption | REFUSED cold identity/instruction replacement; no adoption workflow | REFUSED persistent session/continuation arguments | REFUSED service execution/continuation |
| Live accounts/model quality, installed wheel, other hosts, overlapping mixed-harness Runs | NOT QUALIFIED by this bundle | NOT QUALIFIED by this bundle | NOT QUALIFIED by this bundle |

The main installed lane passed **16/16** admitted cases: four read-only/overflow
cases plus twelve lifecycle cases, with zero failed, skipped, reused, missing,
duplicate or extra members. Two additional Codex reserved-directory cases pass.
The lifecycle cases preserve distinct SUCCESS/FAILED/CANCELLED/TIMED_OUT
settlement, actual native exit/cleanup evidence and unchanged Journal readback
without another inference request. All selected model endpoints and native state
are synthetic in a loopback-only namespace. This is not account or reasoning
quality evidence.

The mixed-harness fixture uses the fresh scoped runtime per selection landed in
PR #150, preserving the same original database/Journal and the admission guard
against reusing another selection's Git owner. This bundle additionally checks
predecessor status, exit, settlement and native claims after each new Run.
Existing three-Run source, typed-role, failed-verifier and untrusted-handoff
checks remain. This A-level process chain does not become an installed
three-harness repair demonstration.

HUG-48 covers the A-level fresh-Run handoff chain above. A combined installed
mixed-harness chain remains unqualified and needs separate B-level evidence
before that portability is claimed. Other extensions have bounded owners:
HUG-19/39/54/57 cover product Pi persistence and restored
configuration; HUG-106 covers provider-owned history; HUG-107/108 cover detached
work and control-stream origin; HUG-109/110 cover real accounts and host parity;
HUG-122 covers actual overlap; HUG-96/123 cover release artifacts. HUG-181 owns
worker crash/drain qualification. Their limits are not inherited from this
matrix or silently implemented here.

Provider-history disposition: **retain** current native request shapes and
context/header owners, and **remove** unconditional history-reference forwarding.
The selected model policy defaults to denying provider-held context selectors;
explicit authorization is frozen with each admission. HUG-106 checks Run
isolation and safe refusal, not historical reference compatibility, migration or
cross-version preservation. IDs stay opaque and no registry infers their owning
Run. Same-Run server chaining also needs opt-in. Live provider account/storage
isolation still requires its own native qualification; no installed-protocol
adaptation or new history infrastructure is introduced.
Earlier installed-context results retain their recorded source and policy scope;
this A-level security proof does not qualify the new default for every installed
harness or provider.

**Retain** the current native broker, request/header and session adaptations.
The managed connection owner resolves an explicit `api_key_file` into the same
immutable authenticated model snapshot used by environment credentials.
Credential-file proof uses synthetic files and local HTTP endpoints. Its scope
covers managed inference credential loading and the existing broker transport.

HUG-49's source subtraction was limited to two unreachable assignments in
`native_agent_command`: Codex/OpenCode `configuration_directory` values were
already rejected before prepared-source access, yet later branches still tried
to assign `CODEX_HOME`/`OPENCODE_CONFIG_DIR`. Those branches are deleted.
The refusal remains, Pi's public configuration remains opaque and supported,
and the managed disposable bootstrap remains the receiving owner. There is no
compatibility alias or fallback left in their place. No protective test is
deleted; positive Pi and before-source-access negative controls pass.

Both active temporary adaptations below are **RETAIN**. Current installed
counterfactual removal probes fail their gates: OpenCode terminal-only handling
turns the valid success case into ATTENTION_REQUIRED; native direct-directory
deny fails Codex's absent-directory startup with the placeholder collision.
The populated Codex counterpart and all restored controls pass. Exact original
source bytes were restored after each experiment. These are maintenance probes,
not alternate production paths. No native version, runtime support bit, registry,
execution route or qualification gate is introduced.

## MCP startup and preflight child lifetime — 2026-11-06

[HUG-124](https://linear.app/hugloss/issue/HUG-124) retains the current service
implementation and its explicit MCP-startup-isolation limitation. The selected
Codex 0.164.0/Linux fixtures use synthetic native configuration, MCP and model
endpoints in a loopback-only test namespace. Exact source, executable identity,
commands and outcomes are recorded with the issue's native receipts.

| Existing test owner | Selected observation |
| --- | --- |
| [Model-free native policy](../../backend/tests/integration/services/test_native_codex_service.py) | Phase checkpoints bracket native connection/readiness and threadless inventory before other tool RPCs. Startup-canary/initialize/tools-list events are observed in the inventory interval. Effective exclusion, direct native MCP-call refusal, private canaries and original daemon cancellation remain checked. |
| [Original service Runs](../../backend/tests/integration/services/test_native_codex_service_run.py) | Three cases cover normal discovery, caller cancellation during held MCP initialization, and a genuine native RPC deadline/unknown-acceptance failure. Original claimed-worker/Step custody precedes the effect. Failed/cancelled preflight makes zero model requests and cannot publish successful service execution. |

The configured MCP child has a different process group from the daemon. Its
birth, parent and PIDFD are independently observed; child trace data remains
untrusted context. The component child had exited by client closure while the
original daemon remained alive. Each Run checks MCP-child and daemon exit
separately from the original supervisor's process-group receipt. This does not
attribute child exit to a particular native disposal action or claim that the
daemon's group contains every native child.

Native startup may author project trust. The synthetic byte-stability control
predeclares the selected workspace's trust and retains stored MCP registrations;
it does not establish whole-process configuration immutability. The failure case
uses supported native startup timing beyond the existing client RPC deadline.
Controlled tests separately retain malformed/rejected inventory and disable
refusal; that entire installed fault space is not implied by one deadline case.

Disposition: **retain** the native protocol, original Step/process custody and
existing temporary adaptations. No new runtime behavior or owner is introduced.
No-start/effect-free preflight, arbitrary descendant or group-escape containment,
real account/store/network isolation, other native versions/hosts and strict
custody remain unsupported or unqualified. This scoped lifetime evidence adds no
ordinary startup or qualification gate.

## Native detached-work boundary

[HUG-107](https://linear.app/hugloss/issue/HUG-107) separates native inner-task
lifetime from physical Step closure (LIFE-01/03/04/07/08, Harness T38 and SP-02).
A native terminal event is a role claim; the original command/backend receipt,
typed acceptance and independent verification retain their separate authority.
The claimed Run worker and existing Step/Attempt remain the execution path.

| Selected mode | Native feature scope and disposition | Physical lifetime boundary |
| --- | --- | --- |
| Managed Codex/Pi/OpenCode CLI through Bubblewrap | **RETAIN** native tool/configuration semantics. Disposable broker configuration does not attest every native background feature. Pi's `--no-session` excludes product persistence; it does not disable extensions, MCP or background commands. Explicit public Pi configuration and nonconflicting extension arguments remain native-owned. | The original backend launches a private PID namespace and the original command owner retains exit, cancellation and cleanup evidence. A child changing its process group is distinct from escaping that namespace. No optional extension's internal session/disposal semantics are qualified merely by this boundary. |
| Local Codex sign-in CLI | **RETAIN** the strict owned configuration with disabled hooks, plugins, Apps and native delegation settings. Delegation denial combines `agents.enabled=false` with the legacy/V2 feature exclusions. These settings are specific to this profile, not the general managed CLI configuration or universal native behavior proof. | The original local command owns its process group. Arbitrary daemonization or descendants escaping that group are **UNQUALIFIED**; a clean group receipt does not attest their absence. |
| Owned Codex service | **RETAIN** effective feature exclusion before the workload turn, including the native `agents.enabled=false` override alongside delegation feature flags, plugins and per-thread MCP tools. Status discovery may still initialize configured MCP servers; startup isolation remains **UNSUPPORTED** under HUG-124. | The original local command owns daemon birth, process group, cancellation and cleanup; closing the client or receiving a turn/abort acknowledgement is insufficient. Group-escaped work remains **UNQUALIFIED**. No separately owned external daemon is adopted. |
| Protected Pi resume and OpenCode external service | Pi read-only managed resume has its scoped [installed evidence](#exact-read-only-pi-reopen--hug-43). Restored extensions, MCP, and dynamic reload remain **UNQUALIFIED**. OpenCode owned-service readiness remains **UNQUALIFIED** for session execution. | Pi retains its original isolated Step and command owner. External servers retain their operator's lifetime. |

The pinned ThinkRail [subagent specification](https://github.com/JetBrains/thinkrail/blob/f93fd03319293b3a19e201c274b00c898d7bf976/packages/pi-subagents/SPEC.md)
allows background children to survive parent-turn abort. Its
[background-command specification](https://github.com/JetBrains/thinkrail/blob/f93fd03319293b3a19e201c274b00c898d7bf976/packages/pi-background-commands/SPEC.md)
likewise separates parent-turn stop from session/embedder disposal and explicitly
limits abrupt-host and escaped-descendant guarantees. These optional Pi packages
are reference test ideas, not built-in Oh-Goon integrations or qualification evidence.
No subagent registry, background scheduler or native session owner is added.

The review found that the promised Codex delegation denial was incomplete.
Codex [0.164.0's native configuration](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/core/src/config/mod.rs)
can select delegation from model metadata before falling back to false legacy/V2
feature flags. Both owned modes now supply the native `agents.enabled=false`
override while retaining the false V2 flag; the sign-in CLI also supplies the
legacy false flag. This corrects the existing permission policy without replacing
native capability selection or disabling ordinary shell tools. The policy and
protocol mappings are retained; neither temporary adaptation's removal gate is
changed by this correction.

The controlled child cut also exposed a command cleanup race. A successful
parent could leave a TERM-resistant child with closed stdio; the command owner
sent KILL and returned before the kernel reported group quiescence. The guard
then correctly refused retirement. The same command owner now waits for native
group quiescence after KILL within its existing five-second cleanup bound,
including leader reaping. Guard retirement and final cleanup checks are unchanged;
surviving or unknown membership still fails that proof. Escalation remains a
cleanup fact alongside the original exit, never a fabricated child result.

### Scoped lifetime qualification — 2026-11-06

Four [controlled native lifetime cases](../../backend/tests/integration/services/test_native_detached_lifetime.py)
passed through the original claimed Run: same-group completion, escaped-session
completion, cancellation and deadline. The exact descendant was alive at the cut
and its pidfd reported exit at original Run terminal readback. Original
Step/attempt process events precede settlement; reopening the same Journal
preserves the result without replay. Five coupled [engine controls](../../backend/tests/unit/engine/test_commands.py)
passed, including the guarded TERM-resistant child with closed stdio. A separate
valid escaped-completion baseline passed; removing only `--unshare-pid` then
failed the exact surviving-child assertion. The flag was restored byte-for-byte,
and the mutation's owned child was cleaned up.

Installed Pi 1.3.0 and OpenCode 2.1.2 each passed the strengthened
[read-only native shell case](../../backend/tests/integration/services/test_native_cli_readonly.py)
after the engine correction, with zero skips. A child with closed stdio and a new
session was alive before the final model reply and dead by original Run terminal
readback. This proves those foreground native shell paths and the selected
Bubblewrap boundary, not an optional background-task API or extension join.

The Codex 0.164.0 [delegation policy matrix](../../backend/tests/integration/services/test_native_codex_service.py)
passed eight cases: sign-in CLI/owned service, V1/V2 model metadata, and the exact
policy/master-override-removed control. The four protected cases then passed a
stronger check excluding all nine delegation tool names; these reruns are not
additional unique coverage. Enabled controls expose native delegation tools;
protected cases omit them while an ordinary native `exec_command` still succeeds.
The native model-free production sign-in permission/feature probe also passed,
and all 215 portable translator cases passed. The synthetic model catalog uses
direct tools and full Responses; this does not qualify lite/Code Mode parity,
live accounts or model reasoning quality. Bounded receipts retain source, binary,
catalog and cleanup facts with HUG-107.

The earlier 27 command, one direct Bubblewrap and four recovery baseline cases
passed before the fixes. They are baseline evidence, not the final candidate gate.

HUG-181's worker crash/drain/containment evidence remains scoped to its original
Linux process and SQLite cases. It does not qualify arbitrary native extensions,
reload, another host or continuous activity outside the selected physical boundary.
Unknown child terminal evidence, cleanup or escaped resources cannot be filled by
native success text, a stored PID or a previous Run's receipt. The dated installed
results above do not establish Harness T38 for an unexercised feature.

## Native protocol-origin boundary

[HUG-108](https://linear.app/hugloss/issue/HUG-108) reviews Harness T39/SP-03:
control-looking tool text must remain data, while a native terminal claim still
needs original physical closure, typed role acceptance and independent
verification. The [existing CLI parser][native] consumes bounded LF-framed root
records from the selected executable. It does not recursively parse JSON strings
inside tool output, messages or command results. Valid JSON shape alone cannot
attest which in-process producer wrote a root record to that stdout stream.

CLI tool/item/session identifiers are native protocol facts, not authentication.
The adapter must not invent a turn/session identifier absent from the selected
protocol, or promote an observed tool ID into outer Run/Step custody. The
[Codex service client][codex-service] has a different boundary: exact request
correspondence, selected thread, owned turn and connected process peer. Foreign
thread notifications are filtered and wrong-turn evidence refuses; these checks
do not turn nested native command output into control or result authority.

Pi 1.3.0 [takes over ordinary stdout](https://github.com/earendil-works/pi/blob/v1.3.0/packages/coding-agent/src/core/output-guard.ts)
before extension loading: [native JSON output](https://github.com/earendil-works/pi/blob/v1.3.0/packages/coding-agent/src/modes/print-mode.ts)
keeps its captured raw writer, while `process.stdout.write` and console output
are redirected to stderr. An in-process extension can still bypass that guard
with a raw descriptor write such as `fs.writeSync(1, ...)`. Origin separation
against such an extension remains **UNQUALIFIED**. Pi `--no-session` and
Bubblewrap isolation do not authenticate a stdout producer. Explicit public Pi
configuration and extension arguments retain their existing native ownership.
Codex sign-in/service plugin exclusions are specific to those owned policies;
general managed CLI/project configuration does not inherit those exclusions.
Arbitrary plugin root-stdout behavior is outside the qualified envelope scope.

The observed CLI identity shapes remain native-owned. Codex 0.164.0 emits
`thread_id` on `thread.started` and item IDs, but its turn start/completion frames
have no turn ID. Pi 1.3.0 supplies a session-header ID and tool-call IDs;
`agent_settled` has only type/aborted fields. OpenCode 2.1.2 supplies root
`sessionID` and nested tool-part/message IDs. These observations do not establish
CLI-side ID authentication. The native [Codex JSONL emitter](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
and [OpenCode noninteractive emitter](https://github.com/anomalyco/opencode/blob/v2.1.2/packages/cli/src/run/noninteractive.ts)
serialize tool output within their typed native envelopes.

### Scoped protocol-origin qualification — 2026-11-06

All 16 selected ordinary [original-Run lifecycle cases][cli-lifecycle-tests]
passed: eight retained and eight new controls. The new
cases pair nested-only fake terminal/role text with a genuine current role and
terminal for all three protocols, then separately retain failed physical exit
and a failed independent verifier. Nested fake roles use valid captured identity;
refusal is not an accidental schema mismatch. Original Step/attempt process
receipts precede one terminal publication, and reopening the Journal preserves
that result. All 218 [parser-owner cases][native-tests] passed, including three
new byte-fragment cases beyond the 215-case baseline. Nine retained service
correlation controls passed without changing the service protocol.

The final installed cohort passed four cases with zero failures, errors or skips.
Three are actual tool-origin checks: Pi 1.3.0 and OpenCode 2.1.2
[read-only CLI cases][cli-readonly-tests], plus Codex 0.164.0's protected managed
[Code Mode context case][codex-context-tests]. Whole fake JSONL frames, valid-bound
role claims, failure text, ANSI controls and split writes remain nested in native
tool envelopes; known forged root assistant/item/error records are absent. The
genuine review, frozen context, existing privacy/child-lifetime controls and an
independent verification Step remain required. Fresh-worker observers record the
actual original stream without replacing the unchanged parser. Their private
128-KiB bounded witnesses do not change product retention.

The fourth case is a separate model-free Pi extension counterexample: ordinary
console text reaches stderr, while `fs.writeSync(1, ...)` emits a valid root
`agent_settled` canary. Its test passes by establishing **UNQUALIFIED** extension
origin, not by qualifying that extension for protected result acceptance. Bounded
promoted witnesses retain exact source/native identities and root/chunk facts
with HUG-108. No live-account, arbitrary-extension, model-quality or whole-host
qualification is claimed.

Disposition: **retain** existing protocol mappings, failure latching, byte bounds,
physical settlement and role/verification owners. No production defect was found;
the parser and runtime remain unchanged. Neither temporary adaptation's removal
gate is changed, and no protocol router, signed envelope or second parser is
introduced. A skipped or empty installed invocation cannot qualify these scopes.

## Native concurrency boundary

[HUG-122](https://linear.app/hugloss/issue/HUG-122) reviews Harness T42/SP-06
against the existing [Run submission and durable authority](EXECUTION_SUPERVISOR.md#run-submission-and-durable-authority).
Sequential handoff, independent targets and concurrently admitted native Runs are
different scopes. The current shared-database mixed-native overlap mode is
**UNSUPPORTED**; the original admission owners refuse it rather than adding a
queue, silently serializing native work or widening worker capacity.

| Requested concurrency | Current owner and scope |
| --- | --- |
| Another active Run in one runtime | The default supervisor/lease policy permits one active Run. The explicit two-Run candidate policy below uses its original admission and resource owners; another harness/profile alone grants no slot. |
| Another runtime using the same SQLite database | Existing execution-lease reservations remain authoritative across processes and repositories. Another controller cannot borrow a selected runtime's budget; live ownership is never expired by elapsed time. |
| Parallel repository targets within one Run | Existing Fleet target scheduling follows the admitted Plan's parallel-safety/parallelism policy. Current Agent ingress requires one candidate repository and one frozen native selection; it is not a mixed-native parallel-target mode. |
| Independent databases/state roots | These are separate authority domains. Their existence does not qualify shared-database admission, aggregate capacity or concurrent same-workspace mutation. An unresolved writer still needs its original drain/fencing proof. |

The explicit [two-Run decision](ARCHITECTURE_DECISIONS.md#explicit-capacity-for-two-independent-native-runs)
selects two fresh headless owned Pi/Bubblewrap candidates, with a shared bounded
native slice and distinct worker scopes. It retains the current Pi protocol,
context, input/privacy and native session adaptations. No harness upgrade or
cross-Run session sharing is implied. Qualification is tied to the actual
public implementation, source, task and native environment exercised;
mixed-harness overlap and unavailable environments remain unqualified. The
default HUG-122/HUG-130 refusal evidence retains its original scope.

Busy refusal is at atomic admission, not a new early native guard. Supported
preflight/capture and source transport may precede it. No second admitted native
worker, model turn or Step follows from the refusal; the existing source owner
still cleans up unadmitted delivery resources. A later Run requires fresh
admission after the original lifecycle releases its authority.

Resource evidence stays within the selected owner. Bubblewrap's explicit native
workload caps, when selected, are not a whole-application or shared-Run aggregate
budget. Omitted caps inherit the native environment; they do not establish a new
limit. Local Codex login/service profiles refuse requested local caps. Owned-PID
CPU/memory/process snapshots are observations, not global peaks, cgroup-wide
attribution or proof that another application's work was contained. Agent/repair
workflows remain outside the ordinary aggregate `run.yml` resource contract.
Parallel mixed-native resource qualification is **NOT_RUN** because that mode is
unsupported, not a new ordinary-workflow gate. HUG-90/HUG-12 retain resource-policy
qualification.

### Scoped native admission qualification — 2026-11-06

Both ordinary [native admission cases](../../backend/tests/integration/services/test_native_agent_concurrency.py)
passed with zero selected skips. Two valid scoped runtimes share one database
and use preregistered distinct repositories. One controlled-process case keeps
the complete selected profile identical and lets A finish; the other selects
Pi/OpenCode protocol fixtures and cancels A. While A is held at its actual
provider request, B refuses with `ExecutionBusy`, creates no Run or worker and
issues no native model request. A's exact lease, live process birth, frozen
context and original Journal events remain unchanged. Allowed source/Git
preparation is not claimed absent.

The same B selection succeeds through fresh admission after A settles, retaining
its own task, Skill, Hashmarks packet and independent verifier. In the canceled
case, A's held provider response is released only after B becomes terminal;
both original readbacks remain unchanged. Original physical receipt partitions,
Step/attempt lineage and Journal reopen are checked rather than reconstructing a
new Run from late text.

One installed Pi 1.3.0 → OpenCode 2.1.2 case passed with zero failures, errors or
skips. A valid scoped native baseline prevents configuration refusal from
masquerading as the busy boundary. The actual Pi request is held while the live
original worker owns the shared lease; OpenCode refuses without another
Run/worker/model, then succeeds with its own native review and verifier after
Pi completes. Each original Run has 15 unique physical execution IDs, disjoint
from the other Run, with exact exit/quiescence accounting and unchanged Journal
reopen. This is refusal and fresh reuse, not overlapping native execution.

Both installed profiles leave CPU, memory and process caps `None`. Two
birth-bound snapshots record only A's worker-process CPU time, RSS and process
high-water memory; they do not measure the whole native workload, application or
global peak. Parallel resource qualification remains **NOT_RUN** for the
unsupported mode. Nine retained custody controls also passed: four actual-worker
restart/unknown cuts and five runtime/execution-lease/workspace-lease controls.
Bounded private HUG-122 receipts retain exact source/native identity and the
original resource/Run observations without adding another runtime registry.

Disposition: **retain** original admission, custody, cancellation, native identity,
context and Journal owners. This scope does not expand capacity, add a worker
pool or qualify restart, endpoint/callback reuse or service-peer inheritance
between overlapping native Runs. Existing sequential handoff and selected
native/service proofs retain their stated limits. No production defect or
production-code change was required, and no parallel-capacity claim follows from
passing refusal tests.

## Temporary adaptations and removal decisions

### OPENCODE-CLI-SETTLEMENT — retain pending installed replacement proof

- **Harness/mode and owner:** OpenCode CLI;
  [NativeAgentEventParser.finish][native], with state accumulated from the same
  bounded event stream.
- **Live caller and supported scope:** headless
  `execute_native_agent_step` → `parser.finish(returncode=...)` in the
  [native receiver][receiver]. The batch `parse_native_agent_events` helper
  delegates to this same parser and remains useful to tests/installed probes;
  it is not another settlement owner. Current installed qualification is 2.1.2
  in the selected managed Linux mode; other versions need their own evidence.
- **Reason and current adaptation:** the implementation records OpenCode 2.1.2's
  `session.wait()`/reconciliation behavior, which can finish without `step_finish`.
  At physical EOF, completed nonblank text may supply the native completion claim
  only with zero runner exit, no native failure and no unfinished text/tool part.
- **Invariant:** partial output, pending tools, missing physical exit, nonzero
  exit and a previous step's text cannot establish successful Step execution.
  Native settlement still requires canonical process settlement and typed role
  acceptance; it is not independent verification.
- **Focused proof:** [native event tests][native-tests], especially
  `test_opencode_v2_reconciled_completed_text_settles_only_after_zero_process_exit`,
  `test_opencode_unfinalized_or_blank_text_and_zero_exit_are_incomplete`,
  `test_opencode_requires_the_runner_exit` and
  `test_native_cycle_cannot_inherit_previous_claim_or_override_failed_process`.
  The [installed read-only CLI case][cli-readonly-tests] exercises native shell
  follow-up, typed review and cleanup through the current managed bootstrap. The
  retired configuration-directory fixture failed before admission and supplies
  no current native completion proof.
  [Installed lifecycle cases][cli-lifecycle-tests] cover successful exit,
  native terminal error after text, cancellation and timeout.
- **Removal condition:** a newly selected installed protocol supplies an
  authoritative terminal event across successful, failed, interrupted and
  cancelled invocations. Prove those cases through the original command/Step
  owner, then delete the completed-text alternative and obsolete fixtures
  together. The existing installed happy-path cases alone do not prove that
  removal condition.
- **Last verification and disposition:** 2026-11-05, installed OpenCode 2.1.2:
  success emits `step_start` and final text without `step_finish`; the current
  completed-text adaptation is required. Streaming error fails with exit 1;
  cancellation/deadline retain original process settlement. Disabling only the
  completed-text alternative fails the installed success case; restoring it
  passes. **Retain**; no fixed upstream version is asserted.

### OpenCode ACP evaluation — HUG-224

**Product enablement is a no-go.** Retain the managed OpenCode
CLI and its [settlement adaptation](#opencode-cli-settlement--retain-pending-installed-replacement-proof).
The selected ACP peer can complete a typed review in isolation. The experiment
found no reduction in model requests on the matched task. Shipping this mode
requires a duplex controller in the existing relay, exact Run and Step binding
for the dialogue, and explicit authority for any client callbacks.
[HUG-224](https://linear.app/hugloss/issue/HUG-224)

The 2026-11-24 evaluation used OpenCode 2.1.2 on Linux 7.0.0-43-generic with
Bubblewrap 0.11.1 at Oh-Goon base `553ea77a`. The raw OpenCode executable digest
was `cf5841e6666aeb469abec8b39ca494cef07a637756454a34a742b2f4996e1675`.
The [installed managed CLI case][cli-readonly-tests] also passed through the
original claimed Run and Agent Step with independent verification and cleanup.

In the matched component probe, the CLI and ACP reviewed the same two frozen
files using the same scripted model and managed inference broker. Both read
both files, produced a `CLEAN` record accepted by the original typed review
owner, passed a separate native verifier, and reached process-group quiescence.
Each made three tool-bearing requests and one context-only request. The ACP
probe selected protocol v1, kept one fresh native session, and correlated its
single prompt's `end_turn` response before accepting that result. The read-only
workspace and private configuration were enforced by the existing Bubblewrap
boundary. The probe used synthetic inference and disposable state.
[ACP initialization](https://agentclientprotocol.com/protocol/v1/initialization) ·
[OpenCode ACP mode](https://opencode.ai/docs/cli/acp/)

The shipped immutable-stdin relay, given an ACP initialize frame, reached EOF
without a negotiated reply or a model call. The test-only duplex controller
sent a `session/cancel` notification, and the original `session/prompt` reply
carried `stopReason: cancelled`. Discarding that terminal reply in a separate
completed turn, or closing stdin during a live turn, left the component
observation incomplete. Process retirement was verified in both cases. A separate
probe sent the same `session/new` request ID twice and observed two distinct
native sessions. An RPC ID is correlation, so a missing acknowledgement cannot
authorize replay. [ACP transport](https://agentclientprotocol.com/protocol/v1/transports) ·
[ACP cancellation](https://agentclientprotocol.com/protocol/v1/prompt-turn#cancellation)

The component probes used the existing command, broker, isolation, and typed
result owners. Product qualification still requires ACP inside the original
claimed Agent Step, canonical cancellation and observer-disconnect evidence,
and a test of client filesystem, terminal and permission callbacks against the
admitted grants. The probe advertised no client capabilities and supplied no
MCP servers. Session load, resume, fork, and native credential stores remain
outside this disposition. OpenCode HTTP service keeps its readiness-only scope.

### CODEX-SERVICE-RESERVED-DIRECTORY — retain; native replacement fails

- **Harness/mode and owner:** Codex owned service;
  [service_arguments][codex-execution].
- **Live caller and supported scope:** `execute_codex_service_run` calls
  `service_arguments` for the original selected Step. Installed tests also call
  the same policy owner directly. Current native qualification is 0.164.0 on the
  selected Linux host; no broader version/environment range is inferred.
- **Reason and current adaptation:** native Codex reserves workspace `.codex`
  read-only even when absent. The generated native policy grants the directory
  `read` while denying `.codex/**`; its source comment identifies a Linux
  missing-directory file-placeholder collision avoided by that representation.
- **Invariant:** native setup must not make workspace configuration contents
  available to tools or make the reserved directory writable. Authentication and
  configuration remain native-owned; controller cleanup must not rewrite them.
- **Focused proof:**
  `test_service_policy_keeps_review_git_and_control_roots_protected` in the
  [service execution tests][codex-execution-tests] checks the emitted policy;
  `test_installed_codex_service_preserves_followup_resume_and_compaction` in the
  [installed service tests][codex-installed-service] uses the production policy
  for absent/populated `.codex`, native startup, read/write/rename/delete denials
  and unchanged contents. A writable-workspace canary is the positive control;
  the same test preserves follow-up, resume, compaction and physical cleanup.
- **Removal condition:** qualify a native policy representation that rejects
  configuration reads and mutations for both absent and populated reserved
  directories, without the placeholder collision, while permitting required
  native startup. Replace the mapping and its obsolete fixtures together.
- **Last verification and disposition:** 2026-11-05, installed Codex 0.164.0:
  current policy **PASS** for both states. A temporary direct-directory-deny
  replacement **FAILED** for the absent directory with native Bubblewrap's
  `Can't create file .../.codex: Is a directory`; it passed when populated.
  Fresh baseline/restored cases pass for both states. **Retain** the current
  mapping. The failed experiment is removal evidence,
  not a second runtime path or a required fallback.

## Versioned qualification constraints

| ID and mode | Existing authority and evidence | Upgrade disposition |
| --- | --- | --- |
| CODEX-SERVICE-PROTOCOL | [Owned execution][codex-execution] requires the original runner's settled `codex-cli 0.164.0` version result before daemon/model work. [Version rejection and interrupted-preflight tests][codex-execution-tests] cover refusal. [Setup recipes][native] describe the native advertised socket/proxy interface. | Unqualified versions fail before daemon launch. Requalify selected protocol, native policy, instruction delivery and cleanup before changing the constraint. Readiness metadata alone does not qualify execution. |
| OPENCODE-SERVICE-READINESS | [OpenCodeServiceClient][opencode-service] accepts bounded authenticated numeric-loopback `/api/info` identity for 2.1.2. [Transport tests][opencode-service-tests] cover version/PID refusal, redirect refusal, total deadlines and deterministic closure. | Requalify the actual endpoint contract before changing the accepted version. Readiness remains `connected_not_qualified`; session execution/continuation are unavailable. |
| OPENCODE-SERVICE-STDIN-LEASE | [Setup recipe][native] describes 2.1.2 `serve --stdio`, a parent-held stdin lease and bounded first-stdout-JSON endpoint discovery. [Native recipe tests][native-tests] and `test_installed_opencode_stdio_discovers_endpoint_and_stops_after_stdin_lease` in the [installed service tests][opencode-installed-service] prove those separate scopes. | Recheck on upgrade. Preparation implements no parent lifecycle. Installed proof observes the actual recipe, bounded URL, authenticated version/PID, and native exit 0/quiescence after the original parent closes stdin. |
| NATIVE-CLI-SELECTION | [Native resolver][resolver] freezes explicit public executable/configuration selections; [native translation][native] rechecks executable bytes without rediscovery. [Resolver tests][resolver-tests] cover changed bytes and absent/disallowed selections. | CLI modes have no shared runtime version allowlist. Successful resolution does not establish installed protocol or optional capability conformance. Re-run the applicable installed cases after an implementation change. |

## Permanent mappings and product responsibilities

These are retained contracts. Upstream improvements may simplify their adapter
implementation; they do not erase their invariants.

| Classification | Existing owner, mapping and focused evidence |
| --- | --- |
| Product-owned candidate execution | **Retain** Pi's existing ephemeral input/event/inference adapter and the original Run/Step/process owners. The explicit candidate route adds durable workspace/materialization authority and a gated private-PID-namespace lifetime observation to the existing Bubblewrap backend. It masks linked Git metadata and leaves protected developer-checkout report refusals intact. [Native scope cases](../../backend/tests/unit/integrations/test_candidate_bubblewrap_writer_namespace.py) exercise actual Linux Bubblewrap 0.11.1 handshake, detached writers and descriptor closure; [candidate lifecycle](../../backend/tests/integration/services/test_native_candidate_workspace.py) uses a synthetic Pi protocol program to prove operational admission, checkpoint, retention and retirement. These scopes do not qualify installed Pi/model behavior, private session adoption, foreign namespace custody or another backend/environment. |
| Product-owned fresh campaign continuation | **Retain** the existing harness adapters, claimed Run worker, native instruction delivery and independent verifier. [Continuation](../../backend/src/oh_goon/services/fleet_plan_continuation.py) treats recorded feedback as untrusted context and resolves a fresh selection and source admission. [Lifecycle proof](../../backend/tests/integration/services/test_agent_continuation.py) exercises a stopped failed regression, explicit feedback, new native protocol-fixture process, verification, exact duplicate readback and immutable history. Its synthetic Codex login mode establishes wiring only; installed mode/environment qualification remains unchanged. No native session adoption or adapter removal is qualified by this feature. |
| Permanent Pi CLI protocol mapping | [Native parser][native] requires `agent_settled`, rather than `agent_end`, after the latest message, tool or compaction activity, and permits recovery after a tool error. A prior settlement cannot authorize a later assistant payload; its error/abort/length stop reason remains failure even if a fresh terminal event is missing. Aborted settlement also fails. Pi 1.3.0 emits `compaction_start`/`compaction_end` for manual, threshold and overflow compaction; retired `auto_compaction_*` mappings are removed. [Native tests][native-tests] prove compaction cannot establish settlement; [installed compaction][cli-compaction-tests] proves actual overflow recovery and retained context. |
| Permanent Codex CLI protocol mapping | [Native parser][native] distinguishes recoverable `error` diagnostics from `turn.failed` and `turn.completed`. A terminal claim must follow the latest item activity; later message/tool items cannot borrow an earlier turn's completion. Duplicate terminal frames add no result authority. Bounded tool observations retain recognized native command statuses, including `declined`, as untrusted claims. [Native tests][native-tests] cover command dispositions, recovered diagnostics, missing completion, explicit failure and nonzero physical exit. Command status does not determine Step success. |
| Permanent Codex service transport and session mapping | [CodexServiceClient][codex-service] binds requests, notifications, permission profile and frozen instructions to one native thread. Uncertain acceptance is nonretryable; cold resume requires explicit original instructions. [Transport tests][codex-service-tests] cover wrong thread/profile, replacement configuration, lost response/restart, cancellation, deadlines and cleanup. The native profile ID does not attest effective grants or a configuration generation. |
| Permanent instruction-delivery requirement | [Native receiver][receiver] consumes the frozen handoff. For broker-managed native modes, the existing broker receives the original task, complete selected Skill and qualified Hashmarks evidence from that input and restores them in a privileged channel on every request, independent of native history. Missing context fails before socket creation; request bounds include the complete context. [Broker negatives][broker-tests] cover task-free tool follow-up/compaction and incomplete context; [installed Codex context][codex-context-tests], [CLI compaction][cli-compaction-tests] and [service Run tests][codex-run-tests] cover selected real native paths. Codex sign-in/service modes retain their complete frozen native base instructions. This is required delivery, not a temporary workaround or another compaction owner. |
| Product-owned service settlement | [Codex execution][codex-execution] combines native terminal-turn evidence with the original daemon's actual requested-shutdown receipt. **Retain** the original Step cancellation predicate before daemon birth, between preflight RPCs and before accepting notifications. [Service execution tests][codex-execution-tests] cover cancellation at preflight, turn/tool/compaction/terminal and shutdown boundaries with actual command-owner cleanup; [canonical settlement tests][settlement-tests] reject missing closure and failed turns. Normal daemon shutdown normalization stays outside campaign policy. These controlled boundaries do not extend installed-version or other-host qualification. |
| Product-owned execution and evidence | The [runtime owner map](AGENT_RUNTIME_OWNERS.md) retains admission, source custody, process supervision, deadlines, cancellation, typed acceptance, independent verification, Journal and Game Tape. Harness-owned sessions, credentials, tools, reasoning and compaction do not acquire those authorities. |
| Obsolete implementation, withdrawn | The [native-store contract](../AGENT_PROVIDERS.md#native-owned-authentication-and-configuration) rejects the former protected credential-file mount route. On 2026-11-05 its two unreachable Codex/OpenCode configuration-directory environment assignments were also deleted. [Native tests][native-tests] retain before-source-access refusal and Pi's legitimate public configuration. Managed disposable bootstraps and explicit native-owned login/service homes remain active. Do not restore credential-file aliases or the removed refresh helper as compatibility. |
| Obsolete OpenCode CLI fixture, removed | The configured-MCP fixture selected the retired `configuration_directory` route and failed before admission. Disposition: **removed** both immediate/delayed MCP variants and obsolete configuration-byte assertions; [read-only review][cli-readonly-tests] now uses the current disposable managed bootstrap. Fixture removal changed no native execution path and restored no native configuration mount. |

Codex sign-in source disposition (2026-11-04): **simplify** the existing
[canonical source owner](../../backend/src/oh_goon/repository_tooling/release/package_slim.py)
to treat genuinely empty workspace-root `.agents`, `.aws` and `.codex` directories
as operational scaffolding, without following symlinks. Codex 0.164.0 exposes
absent or empty entries as read-only empty directories inside its tool namespace;
their names must not exempt content or symlinks from ordinary source rejection.
The existing `test_installed_codex_enforces_the_selected_native_tool_boundary` in
the [native Run tests][native-run-tests] requires equal host/tool source identity
and unchanged host entries alongside private-canary/alias, read-only evidence,
Hashmarks follow-up and loopback denials. [Source-owner regressions](../../scripts/tests/test_package_slim.py)
retain populated, file, symlink and unknown-directory rejection. On 2026-11-04,
the installed test **passed** with Codex 0.164.0 on Linux; separate native probes
also preserved populated/unknown rejection and symlink startup refusal. This
selected model-free proof supplies
no model-quality, general private-store or other-OS qualification, and does not
replace independent project verification through the original `--verify` owner.

For the Codex 0.164.0 direct Unix-socket mode, **retain** the existing protocol,
native policy and reserved-directory adaptation. Linux socket admission now rejects
paths above 107 filesystem-encoded bytes before owned private state or native work;
the [transport tests][codex-service-tests] cover exact ASCII/UTF-8 boundaries and
unrestricted non-socket directory fields, while the [execution tests][codex-execution-tests]
prove refusal before private directories, version probing and daemon/model effects.
This admission check adds no proxy, path fallback or authentication authority.

## Conformance matrix

**A** identifies deterministic fixture coverage, including real child processes
where referenced. **B** identifies an opt-in installed-harness test with synthetic
state/local providers or model-free commands. **Gap** means no installed proof
was identified for that cell; **unsupported** is the current capability boundary.
Both A and B describe test scope, not a result for the current checkout. See the
[backend test guide](../../backend/tests/README.md) for exact native selections,
markers and environment requirements. No paid inference is needed for the B
cases linked here.

| Behavior | Codex CLI | OpenCode CLI | Pi CLI | Codex owned service | OpenCode service |
| --- | --- | --- | --- | --- | --- |
| Frozen executable / endpoint identity | A [resolver][resolver-tests] | A [resolver][resolver-tests] | A [resolver][resolver-tests] | A [resolver][resolver-tests], [version preflight][codex-execution-tests]; B [owned Run][codex-run-tests] | A [authority/version][opencode-service-tests]; B [PID matches owner birth][opencode-installed-service] |
| Exact task / Skill / producer delivery | A [receiver][receiver-tests]; B [context chains][codex-context-tests] | A [receiver][receiver-tests]; B [read-only review][cli-readonly-tests] | A [real-child Run][native-run-tests]; B [read-only review][cli-readonly-tests] | A [receiver][receiver-tests]; B [follow-up/compaction Run][codex-run-tests] | Unsupported submission |
| Terminal completion and failed process | A [native events][native-tests], [Step settlement][settlement-tests]; B [CLI success/error/deadline][cli-lifecycle-tests] | A [native events][native-tests]; B [CLI success/error/deadline][cli-lifecycle-tests] | A [native events][native-tests], [real-child Run][native-run-tests]; B [CLI success/error/deadline][cli-lifecycle-tests] | A [terminal/cleanup refusals][codex-execution-tests]; B [owned Run][codex-run-tests] | Unsupported session execution |
| Cancellation / resource cleanup | A [shared command cancellation][command-tests]; B [original Run cancellation/deadline][cli-lifecycle-tests] | A [shared cancellation][command-tests], [Step timeout][receiver-tests]; B [original Run cancellation/deadline][cli-lifecycle-tests] | A [shared cancellation][command-tests]; B [original Run cancellation/deadline][cli-lifecycle-tests] | A [cancellation][codex-execution-tests]; B [campaign cancellation][codex-run-tests] | A [transport closure][opencode-service-tests]; B [client close preserves server; owner cancels; stdin lease][opencode-installed-service] |
| Compaction / complete context | B [synthetic auth × HTTP/WebSocket][codex-context-tests]; protected HTTP has separate scope | B [native overflow/summary/continuation][cli-compaction-tests] | A [current protocol events][native-tests]; B [native overflow/summary/continuation][cli-compaction-tests] | A [transport][codex-service-tests]; B [automatic compaction Run][codex-run-tests] | Unsupported |
| Same native session continuation | No OH-Goon continuation API; headless calls are ephemeral | No OH-Goon continuation API | B [exact managed read-only resume](#exact-read-only-pi-reopen--hug-43) and [operator journey](#managed-pi-operator-journey--hug-53) | A [fixed thread/profile][codex-service-tests]; B [persisted resume + explicit compaction][codex-installed-service]; cross-Run adoption unverified | Unsupported |
| Selected tool / private-store boundary | B [managed review][codex-managed-tests], [protected HTTP context][codex-context-tests]; no general native-store exclusion claim | B [read-only native shell][cli-readonly-tests]; no general native-store exclusion or MCP conformance claim | B [read-only native shell][cli-readonly-tests] and [explicit ephemeral Pi tools and protected restore](#selected-pi-configuration-and-native-tools--hug-54) | A [capability/policy refusal][codex-execution-tests]; B [synthetic stores, reserved-directory states and phase commands][codex-installed-service], [configured MCP campaign][codex-run-tests] | Protected execution explicitly refused |
| Independent verification / original durable evidence | B [original context Run][codex-context-tests], [lifecycle readback][cli-lifecycle-tests] | B [typed review + independent verifier][cli-readonly-tests], [cancelled receipt readback][cli-lifecycle-tests] | B [typed review + independent verifier][cli-readonly-tests], [lifecycle readback][cli-lifecycle-tests] | B [original Run and campaign][codex-run-tests] | Unsupported |

Same-session continuation, fresh-session handoff and choosing a harness for a new
Run are separate capabilities. A prior-Run handoff transports explicit untrusted
task context; it grants no permission and carries no native transcript. Native
session migration across harnesses is unsupported. An admitted Step cannot change
harness when one fails; another selection requires fresh resolution/admission.
The canonical Run control projection reports the frozen harness, mode and backend
alongside separate fresh handoff, exact resume, cross-harness fresh handoff,
steering, user follow-up, abort and detach availability. Fresh handoff routes still
require fresh resolution/admission; abort refers only to the original Run's
cancellation owner. Native RPC presence and historical installed observations do
not qualify a product continuation route or another environment. Missing complete
environment qualification remains explicit. Disposition: **retain** the native
protocol, settlement and protected-store adaptations; this read-only projection
does not satisfy their removal gates or enable another native control.
The protocol-fixture cases in the [CLI lifecycle tests][cli-lifecycle-tests]
exercise current typed review acceptance through real supervised children and
Journal reopen. They reject stale terminal claims, later payloads without fresh
completion, native failure omitted from bounded observations and nonzero physical
exit. Repeated terminal frames still produce one Step and Run terminal event.
These fixtures do not qualify an installed implementation; the separate installed
cases select disposable native state and a local model endpoint.
`test_cross_harness_handoff_keeps_separate_runs_and_original_verification` in the
[native Run tests][native-run-tests] exercises Codex review, Pi repair and
OpenCode re-review as separately admitted Runs using real supervised protocol
fixtures, fresh source capture, independent verification and Journal readback.
That A-level test does not qualify the three installed harnesses together.

Remaining installed gaps include configuration-generation drift and restart
uncertainty, real-account native sign-in/refresh retention, cross-Run native
session adoption and complete private-store alias/API/plugin boundaries.
Linux proof does not qualify WSL2/container execution or strict custody.

## Pi persistent-session protocol QA — HUG-39

[HUG-39](https://linear.app/hugloss/issue/HUG-39) qualifies the selected installed
Pi protocol before any persistent-session execution feature is implemented.
Existing Pi Runs remain ephemeral (`--no-session`); lifecycle and compaction
checks above do not establish cross-Run session adoption.

Start with an explicitly selected public Pi installation and disposable native
configuration, a local synthetic provider and a separate session directory.
Never inspect personal authentication or history. Use the original command
supervisor and physical cleanup evidence; exercise the real Bubblewrap mount
boundary independently of temporary model/configuration teardown.

The investigation must distinguish native session identity, selected leaf,
first persisted file creation, normal process close and exact reopen in a fresh
process. Test missing, renamed, empty and malformed resources and interruption
during a turn. A fallback to a new session is an unsafe exact-open result,
even when the native command exits successfully. Observe native behavior without
adding an Oh-Goon transcript parser, repair algorithm or session store.

Record whether headless JSON supplies an unambiguous opened-existing identity
acknowledgment. Select native RPC only if that proof requires it; any required
bidirectional channel must remain within the existing Step/process owner and
be qualified separately in [HUG-139](https://linear.app/hugloss/issue/HUG-139).
Native persistence observations do not establish power-loss durability,
exclusive access against unmanaged writers, new Run admission or independent
verification. Keep unsupported modes and unrun hosts explicit. Actual outcomes
belong to the issue and exact-source evidence; these instructions are not PASS.

### Installed Pi 1.3.0 disposition

The [installed session probe][pi-session-tests] characterizes the native protocol
under Linux Bubblewrap using a test-owned extension that supplies synthetic
model replies. Each launch has a distinct physical execution ID, IP networking
denied, no inference broker and independently disposed private configuration.
The writable session directory is inside the disposable qualification workspace;
this does not grant product authority for an external persistent session mount.

| Native observation | Qualification consequence |
| --- | --- |
| JSON emits the session header before the turn; fresh-process reopen preserves its ID and delivers the earlier conversation to the synthetic provider. | JSON supplies session identity, but no active-leaf or opened-existing acknowledgment. |
| Correlated RPC `get_state` supplies session ID/file; `get_entries` supplies the active leaf. | **DG-01: select native RPC for pre-turn session and branch identity.** HUG-39's one-shot queries did not qualify a sustained channel; HUG-139's separately selected ephemeral transport and current evidence are recorded below. |
| A new native identity and setup entries can exist without a session file after normal close. First user conversation creates the file. | Identity, close and persistence are separate facts. |
| Missing/renamed explicit paths create fresh in-memory identities; an absent `--session-id` may create that very ID. Empty files are rewritten. | Neither selector is an exact-open refusal mechanism. Matching an ID alone cannot prove an old resource was opened. |
| Wholly malformed nonempty files refuse; a valid session with a torn tail can load and gain a newline. A saved session can be selected from another requested cwd. | Format acceptance and header identity do not establish complete history, project authorization or unchanged bytes. No controller repair is added. |
| Cancellation after native assistant generation starts retains the user prompt without a successful assistant turn; the original command owner proves process-group quiescence. | Retained conversation does not establish successful work. Interrupted isolation acknowledgment and power-loss durability remain unproven. |

**Retain** Pi's existing ephemeral command and terminal/compaction mappings.
**Unqualified:** persistent Oh-Goon Runs, fail-before-effect exact open,
exclusive/migration-safe custody, complete package sealing, WSL2/containers and
power-loss durability. Neither JSON nor RPC supplies an opened-old flag or a
durable-save acknowledgment. RPC is the minimum identity transport selected for
the next work package, not permission to enable resume or a second process loop.
Existing Run/Step/Journal admission, independent verification and the dependent
HUG-40/41/43 work packages still own product enablement.

### Ephemeral Pi RPC transport — HUG-139

**Disposition:** retain default JSON and native terminal behavior; add the
explicit `pi_rpc: true` capability only for a Pi-only, broker-backed Bubblewrap
profile. Existing JSON qualification remains distinct. Persistent session
selection/adoption stays refused; this transport always uses `--no-session`.

The existing command owner supports bounded nonblocking input alongside its
continuous output readers. Its same supervisor handles partial writes, native
backpressure, EOF, cancellation, deadlines and process retirement. The existing
inference relay correlates Pi RPC responses before submitting one frozen prompt.
Native `agent_settled`, prompt acceptance and final unchanged session identity
must all be observed before EOF. Pi owns disposal on EOF; no second process
manager, native transcript format, retry loop or session store is introduced.
Control responses remain private to this bounded transport. Correlated IDs are
not producer authentication; the raw-fd extension stdout caveat above remains
unqualified.

Pi 1.3.0's selected public bundle and complete runtime were exercised on Linux
with disposable configuration and synthetic providers. Four installed duplex
probes cover literal Unicode/framing, native failure despite exit 0, active
turn cancellation and a killed native child. Six original Run cases cover
success/error/cancel/timeout,
read-only native tools, independent verification, mandatory task/Skill/Hashmarks
delivery and native overflow compaction. The prior JSON cases are retained as
separate parameters. These scoped results do not qualify live accounts, another
installed implementation, WSL/container modes, exact old-session refusal,
unmanaged writers or persistent custody. Exact source, commands, physical
receipts and remaining acceptance belong to HUG-139's retained evidence.

After native backend setup, select the complete public release and effective
entrypoint explicitly, outside personal authentication/configuration:

```bash
OH_GOON_NATIVE_PI=/public/pi-release/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js \
OH_GOON_NATIVE_PI_RUNTIME_ROOT=/public/pi-release \
uv run --offline --frozen --no-sync --project backend pytest -q -s \
  -m certification backend/tests/integration/services/test_native_pi_session_protocol.py
```

The fixture verifies Pi 1.3.0 and records entrypoint SHA-256, host and original
process receipts. The entrypoint hash does not seal Node or its imported package
bytes; changed implementations require requalification (HUG-137). No live
account, paid inference, transcript parser or product command change is involved.

## Pi argument ownership QA — HUG-71

[HUG-71](https://linear.app/hugloss/issue/HUG-71) protects typed session and
transport ownership. [PR #95](https://github.com/oh-goon/oh-goon/pull/95) established
the framing guard. Exercise the selected installed parser, including its
`--` terminator, positional tasks,
`@file` inputs, native session selectors and long equals forms that may reach
extension flags. An appended `--mode json --no-session` does not protect the
invocation when earlier arguments terminate option parsing or consume those
owned flags as a missing option value.

Before changing admission, reproduce these routes with the installed parser
without model or personal-store access. Require refusal through the existing
native argument owner before executable/configuration reads, Run construction,
lock publication or model effects. Cover the public CLI and authored Plan paths,
and retain positive controls for nonconflicting native model, thinking, tool,
display and extension options. Argument values must never appear in refusals.

This is an invocation-ownership check, not a replacement native option resolver.
Native Pi continues to interpret option values and extension semantics. Reserved
names cannot become caller authority through an extension's equals form;
arbitrary extension code still requires the separate effective-session checks in
[HUG-136](https://linear.app/hugloss/issue/HUG-136). Typed creation/continuation,
mount custody and same-process RPC qualification remain with HUG-40/41/70/139.

**Disposition: retain** the adapter's task/session/transport reservation and
qualify its option-boundary framing against installed Pi 1.3.0. The existing
[argument owner][native] distinguishes value-taking native options, switches and
unknown long extension flags without interpreting values or rewriting argv.
`--use-theme` and `--tui-mode` have native flag-leading-value refusal, unlike
ordinary value-taking options. Their native fatal diagnostics are not evidence
of reachable model work. [Installed parser probes][pi-session-tests] establish
these distinctions in a read-only, denied-network Bubblewrap process; they do
not start Pi or load an extension. [Native unit tests][native-tests],
[resolver tests][resolver-tests] and the
[CLI/Plan tests](../../backend/tests/unit/entrypoints/test_cli_ai.py) protect
early refusals and valid caller options.

The existing compatibility check uses the same framing to identify explicit
model options. A flag-shaped prompt value remains opaque when Pi consumes it
as a value. Actual model options retain the public-configuration requirement.
The installed Pi 1.3.0 cases preserve the owned mode and session flags with
these literal values in JSON and RPC. The shipped parser and the selected
bundled parser agree on the observed framing. Current CLI and authored-workflow
tests also exercise
the persistent profile boundary. Both `new` and `continue` require their
dedicated managed selection before any caller configuration or argv effects.
Use the [installed operator journey](#managed-pi-operator-journey--hug-53) for
actual persistent execution evidence. Requalify framing on a native upgrade.
Arbitrary extension behavior remains HUG-136 scope.

### Pi session intent — HUG-40

Retain the Pi 1.3.0 task and option framing. The installed parser probes cover
exact and short native selectors, duplicate selectors, equals forms, the
terminator, missing values and positional task inputs. Explicit ephemeral
intent selects headless Pi and travels with its resolved profile, executable
and original Run Step. The JSON and RPC headless launchers continue to select
`--no-session` for ephemeral Runs. HUG-40 defined both persistent choices
and fenced them at admission. The native parser proof covers argument
ownership. Extension code that changes the effective session remains in
HUG-136's scope.

### Private Pi session storage — HUG-41

The application's private state layout selects the `pi-sessions` root.
Storage is created exclusively for the original Run, repository admission,
Step and attempt, with pinned repository and directory identity. The selected
Linux Bubblewrap backend binds just the open directory descriptor at
`/run/oh-goon-pi-session`. Pi owns session file creation and writes through its
native `--session-dir` interface. The directory survives disposable native
configuration and sandbox cleanup. Metadata observation is limited to an exact
native file name.

The [installed storage test][pi-session-tests] covers Pi 1.3.0 with Bubblewrap
0.11.1 on Linux 7.0.0-43, one fresh directory, and a read-only direct Git
repository. Pi reported the same session and exact file before and after a
settled turn. The file remained after process quiescence, configuration cleanup
and descriptor close. Pi's native shell could read the selected file. The
sibling session, original host path, synthetic personal store and synthetic
Journal remained outside the namespace. The installed ephemeral RPC Run also
passed through the claimed worker with its managed inference broker.

This scope establishes native storage and command cleanup. Resistance to
unmanaged host writers and aliases they create remains unqualified.

### Retained Pi storage and disposable bootstrap — HUG-72

**Disposition: retain** the native writer, original scoped mount, managed
broker, and disposable native configuration. Application startup, claimed
worker preparation, and the final Bubblewrap scratch owner reject an effective
temporary base inside retained Pi storage. Only the native transcript belongs
in a qualified slot. Persistence, reopen and retirement share that exact
directory rule. Extra entries leave the save unresolved and stay with the
original owner.

The [installed compaction and restart proof][cli-compaction-tests] exercised
Pi 1.3.0 in claimed workers on Linux 7.0.0-43 and Bubblewrap 0.11.1. A Run
created a saved session, a resumed Run compacted it, and a fresh public
`oh-goon ai` process resumed the compacted conversation under new source,
Skill and verifier authority. The selected session retained its identity and
file across those Runs. Observed host control, bootstrap, captured inputs
and broker socket paths were gone after teardown. The retained slot contained
only the verified transcript and lacked the synthetic host credential, local
broker key and bootstrap copies. A separate failing-verifier case checked
the same cleanup independently of its Run result.

The original Journal and private storage owner retain partial or unconfirmed
saves and fence unqualified reopen. Automatic scratch cleanup leaves these
slots to their original custody. Exact retirement requires the qualified
settled source and explicit operator authority. Native bind or storage
qualification failure refuses the selected session rather than reconstructing
it. Pi can access its own admitted session bytes. Unmanaged host writes,
interrupted power-loss durability and other host modes remain unqualified.

### Private Pi session and repository identity — HUG-73

**Disposition**. Retain the Pi 1.3.0 RPC exact-open gate, the native transcript
format, and the existing claimed writer. Reopen and retirement compare the
pinned file's device, inode and size with the Journal receipt before opening a
readable descriptor. The bounded digest and stable metadata checks still
qualify the bytes. Private storage rejects overlap with the lexical and
resolved default Pi home and agent storage locations.

At Pi workload launch, Bubblewrap mounts both repository aliases from the
workspace handle checked against the admitted repository subject. The
read-only Git metadata aliases use the checked Git handle. Each alias borrows
its own descriptor within the existing launch lifetime. A fresh writer keeps
its admitted repository access mode. A continuing writer has a read-only
repository and receives the exact retained file descriptor.

Linked worktrees, ambiguous mount aliases and mounts nested below the
repository are unsupported for persistent Pi. The [installed storage
proof][pi-session-tests] checks that both guest aliases expose the same
repository and Git metadata, with the repository read-only and sibling,
personal and application-control paths absent. The [installed claimed-worker
journey](../../backend/tests/integration/services/test_native_pi_session_journey.py)
covers foreign-repository and missing-file refusals without fallback. Live
changes to repository descendants by unmanaged host actors, concurrent
privileged mount changes, and non-Linux filesystems remain outside this proof.

### Original Pi writer custody — HUG-42

The existing claimed worker can admit a fresh managed Pi RPC session for one
direct Git repository and one native Agent Step in a serial local Run. Pi uses
the original scoped storage owner and a fixed private session file. Native
extensions and caller-owned native configuration are outside this mode.

The original Step attempt records its writer reservation before exposing the
writable directory. The original lease and recorded attempt fence allocation,
launch, and post-exit observation. A duplicate reservation refuses. File
metadata and bounded native identity can be recorded only after the original
process tree settles. Missing or interrupted evidence leaves the reservation
unconfirmed, even after Run recovery. The file stays with the original Run.
The observed metadata is separate from the verifier result. HUG-43 extends
this observation with a bounded file digest for eligible session predecessors.

### Pi writer reservation and orphan recovery — HUG-74

**Disposition**. Retain the original claimed Step, SQLite Journal and recovery
owners. A separate Journal reader observes the committed writer reservation
at private directory allocation. Recovery preserves that reservation as the
lineage head through interrupted allocation and an unaccepted native save.

The [installed claimed-worker test](../../backend/tests/integration/services/test_native_pi_session_journey.py)
uses Pi 1.3.0 and Bubblewrap on Linux with a synthetic provider. It interrupts
the original worker after the native file appears and before the Agent result
is accepted. Original recovery preserves the file, records the Run failure,
and releases custody after proving process cleanup. A separate Journal read
reports the orphan and refuses continuation. The accepted writer and verifier
evidence remain separate from filesystem presence.

After canonical reconciliation, inspection checks only the exact reserved
slot and reports regular-file presence or proven absence. Ambiguous storage
and unresolved process cleanup remain unverified. The fixture exercises
process interruption. Power-loss durability and other host environments
require separate qualification.

### One Pi writer through cancellation and cleanup — HUG-75

**Disposition**. Retain the claimed worker, SQLite writer reservation and
Bubblewrap process ownership. When cancellation commits before the writer
receipt, the same Journal transaction retains only an unknown persistence
observation tied to verified descendant quiescence. A receipt committed
before cancellation remains historical evidence. Continuation and retirement
both require settled resources for the original repository operation.

The [installed Pi test](../../backend/tests/integration/services/test_native_pi_session_journey.py)
pauses a continuation in its claimed worker. A second controller receives the
busy refusal while the original lease and Journal revision remain current.
Original cancellation records termination, exit and descendant quiescence
for that same writer before recording unknown persistence. Fresh continuation
of the cancelled writer is refused.

The [Journal cases](../../backend/tests/unit/runtime/test_pi_session_writer.py)
exercise the cancellation and publication order, stale callbacks after a new
lease is acquired, and unresolved cleanup after an accepted Agent Step. The
[claimed-worker process cases](../../backend/tests/integration/services/test_native_detached_lifetime.py)
exercise descendant cleanup at completion, cancellation and deadline with a
controlled Linux Bubblewrap workload. The installed proof uses the managed Pi
1.3.0 profile. Custom persistent extensions and other host environments remain
outside this qualification.

### Exact read-only Pi reopen — HUG-43

**Disposition: retain** the Pi 1.3.0 RPC relay, managed inference bootstrap,
owned settlement and isolated native storage. A fresh Run can select the
latest accepted Agent writer in the same original repository subject and
native implementation. The Journal rechecks predecessor custody when
reserving the new writer. A reservation becomes the lineage head even if the
worker is interrupted. The selected storage is a regular file bound by
descriptor. Its saved SHA-256 is checked before exposing it to Pi. Files over
16 MiB or without qualified saved bytes cannot supply continuation authority.

The relay checks the exact native file, session ID and active leaf, current
managed provider/model/wire endpoint, and idle state before submitting the
fresh prompt. Pi's `get_entries` query is anchored at the prior leaf. It
does not transfer the old transcript out of the relay. Explicit native model
arguments and the fixed read-only built-in tools prevent restoration of
previous tool grants. The current repository is physically read-only inside
Bubblewrap. Native project approval, extensions, MCP, Skills, context files
and prompt templates are disabled for the continuing process. The original
broker supplies current captured task, Skill and Hashmarks evidence to each
request, including native follow-up, compaction and retry.

Native settlement, its post-turn file observation, command exit, descendant
quiescence, the accepted Step result and independent verifier retain their
respective owners. This mode does not qualify migration of native files,
writes by other host processes, restored extensions, disk-full or power-loss
durability, or portability to other runtimes.

### Native Pi save evidence and verifier independence — HUG-76

**Disposition**. Retain Pi 1.3.0's RPC and JSONL save owners. The final
correlated entries response supplies a private ordered record digest and
count. After the claimed worker proves process cleanup, the storage owner
checks the matching complete records through the pinned file. A continuing
save must retain the exact predecessor bytes and the newly observed suffix.
The file and its directory must synchronize before the Journal accepts the
save receipt. The transcript stays in its original slot.

Incomplete records, missing entries, changed predecessor bytes, and sync or
close errors leave persistence unknown. If Pi 1.3.0's final entries response
exceeds the framing bound or ends before its newline, its ordered RPC
envelope identifies it as the outstanding save query. The relay discards its
contents and still requires the correlated final state and physical exit.
The Agent and independent verifier retain their own results. Historical
digest-only and metadata-only receipts remain
readable with unverified save eligibility. New reopen and retirement
requests require qualified evidence. Previously committed retirement
requests retain their exact removal authority.

The [installed torn-save test](../../backend/tests/integration/services/test_native_pi_session_journey.py)
exercises a successful Pi turn and verifier with malformed retained bytes,
then checks a fresh continuation refusal. The
[installed compaction test](../../backend/tests/integration/services/test_native_cli_compaction.py)
reopens a qualified session after a failed independent verifier and checks
the original failure after restart. The installed save fault cases inject
file-sync ENOSPC and file-handle-close EIO after Pi writes a complete
transcript. They also kill the claimed worker after file and directory sync
and before Journal publication. Recovery preserves the original bytes and
refuses continuation.

The installed append ENOSPC case fills a private 8 MiB tmpfs during the held
second inference, after one native tool effect. On native Linux with Pi
1.3.0 and Bubblewrap 0.11.1, the syscall trace identifies ENOSPC on the
Pi transcript and the file retains a short final append. The process exit
is zero and Pi reports a settled failure. The original Run completes with
an attention-required outcome, a failed Agent Step, verified descendant
cleanup, and an unstarted verifier. Fresh CLI readback preserves these
facts and reports unknown session persistence. Continuation is refused,
the bytes remain intact, and the tool effect occurs once. Power-loss
durability remains unqualified.

### Fresh Pi context after exact reopen — HUG-77

**Disposition**. Retain the existing claimed Run worker, frozen prompt
delivery, and managed Pi RPC. The resumed Run admits its current repository,
Skill, task, and tool policy. Pi restores its native history and owns its
automatic compaction and retry. History and its summary remain model input
with no new tool grants.

The [installed reopen and compaction cases][cli-compaction-tests] change
the captured source, Skill, and task between Runs under the same inference recipient. The
synthetic provider checks fresh Hashmarks evidence through the initial
reopen, compaction requests, and tool follow-up. Pi reads the newer source.
The earlier prompt's request to write a fixture file survives in the
native summary and the resumed read-only tools refuse it. The original
Run's recorded state, evidence, and outcome survive fresh readback.

The [installed session cases](../../backend/tests/integration/services/test_native_pi_session_journey.py)
repeat the fresh source and Skill check after a transient provider error.
Current typed output reaches an independent verifier that reads the revised
source. A length stop or a completed review tied to the prior candidate
fails the Agent Step before the verifier is started. The evidence is for
Pi 1.3.0, managed RPC, Bubblewrap 0.11.1, and synthetic inference on native
Linux. Explicit manual compaction through Oh-Goon remains unsupported in
this mode. Provider-hosted history qualification remains HUG-106.

### Effective Pi policy after exact reopen — HUG-78

**Disposition**. Retain the dedicated managed Pi RPC profile, private broker,
and original Bubblewrap Step. Before releasing the prompt, the RPC gate checks
the reopened session's provider, model, API, and broker address against the
current Run. The broker uses the current upstream selection and credentials.
The reopened child receives the built-in read tools and a read-only repository.

The [installed configuration journey][pi-native-config-tests] overwrites a
fixture file in a writable original Run while retaining the repository
directory identity. The next Run resumes the same session with the
original inference recipient. Attempts to use write, shell, a project extension, and project MCP
are refused. Reads of the synthetic personal store and the extension and MCP
startup markers fail. The current verifier and original cleanup succeed.
Explicit native configuration directories and model or extension overrides
produce a redacted typed refusal with no new Run or inference.

The [installed protocol probe](../../backend/tests/integration/services/test_native_pi_session_protocol.py)
uses actual Pi state to check model, API, and broker-address mismatch. The
typed refusal leaves the prompt undelivered and the retained bytes intact.
This qualifies Pi 1.3.0 managed RPC on native Linux with Bubblewrap 0.11.1
and synthetic endpoints. Optional extension and MCP execution is qualified
separately for the selected ephemeral profile. Persistent optional tools
remain outside this configuration, and real-account identity and historical
disclosure are separately tracked in HUG-109 and HUG-131.

### Pi history disclosure after exact reopen — HUG-131

**Disposition**. Retain native Pi's conversation construction, the original
claimed Run writer, and the existing inference broker. The Journal checks
the frozen recipient policy of the exact prior writer before admitting a
continuation. Before a new writer reservation, the worker verifies a private
keyed proof made from the same resolved recipient and headers consumed by the
broker. Older records without that proof remain readable. A new Run cannot
authorize their native history by inspecting or rewriting the transcript.
Custom CA files are loaded later by the upstream client, so the selected
mode leaves this trust scope unqualified for reopen.

The [installed configuration journey][pi-native-config-tests] retains a
synthetic conversation marker in the original Run. A different upstream
endpoint cannot receive a request. Rotating the credential under its
unchanged configuration reference also refuses before the Pi writer and
broker start. Restoring the original authorized scope permits a fresh
read-only Run to recover the genuine native history and reach its independent
verifier. Historical availability and current tool grants remain separate
from this disclosure boundary.

### Native Pi settlement and typed acceptance — HUG-79

**Disposition**. Retain Pi 1.3.0's RPC control and the existing native event
parser. The prompt acknowledgement confirms prompt acceptance. `agent_end`
closes a native attempt that can lead to retry or compaction. The parser
requires `agent_settled` with a boolean abort status. Later tool progress
invalidates earlier settlement. A new turn, unfinished assistant message,
retry, or tool activity clears earlier role text. A recovered native retry can
supply current role output.

Original command exit, descendant quiescence, typed acceptance, independent
verification, and save evidence retain separate owners. A nonzero Pi process
exit fails the Step while retaining the native terminal disposition. A proposal
still requires a separately admitted apply before it can modify source.
Strict Agent execution remains unsupported under the current admission policy.

The [original-Run lifecycle tests][cli-lifecycle-tests] cover agent-end-only,
late tool progress, aborted assistant output and nonzero exit after a native
completion claim. The [installed Pi continuation tests](../../backend/tests/integration/services/test_native_pi_session_journey.py)
exercise the original worker and Journal with a local provider. They cover
provider error, length truncation, malformed or stale typed review, and
native retry. The installed failed-exit case uses a test launcher that
loads Pi 1.3.0 in the same Node process and injects a nonzero exit during
shutdown of the reopened session. Pi emits a settled review and appends it
to the transcript. The original process exit still fails the Agent Step,
the verifier remains unstarted, and persistence is unknown. Fresh CLI
readback retains the failure and verified cleanup without the private
review text. The seed Run retains its original evidence.
The [installed RPC probe](../../backend/tests/integration/services/test_native_pi_rpc.py)
checks native assistant abort with zero process exit using a synthetic
provider. The [installed compaction tests][cli-compaction-tests] exercise recovery, a
successful typed review with a failed independent verifier, and subsequent
reopen. Resumed Pi's built-in read tool carries a control-shaped line as
tool data. The [protocol-origin qualification](#native-protocol-origin-boundary)
continues to bound optional shell, MCP, and in-process extension claims.

### Native Pi incomplete tool calls — HUG-80

**Disposition**. Retain the existing claimed-worker and Pi RPC owners.
Before producing a qualified save observation, the relay requires closure of
the original live tool executions and matching tool results in the bounded
native `get_entries` reply. A missing result or unresolved execution leaves
the save unqualified. The original Step, verifier, and process receipts retain
their own dispositions. A recorded tool error closes that native call without
establishing external-effect success or granting replay authority.

Pi 1.3.0 can format an unresolved tool call for a provider by supplying an
error placeholder in memory. The [installed formatter probe][pi-session-tests]
shows that this leaves the input history unchanged. The placeholder provides
no evidence of physical completion, and automatic repair remains unsupported.

The [installed claimed-worker crash cases](../../backend/tests/integration/services/test_native_pi_session_journey.py)
kill the controller and worker after one synthetic repository append, both
before and after Pi records its tool result. Original recovery retains the
reservation, process lifetime, failed Run, and private bytes. The fresh
continuation refuses before admission or inference, and the append count
remains one. The [Journal writer tests](../../backend/tests/unit/runtime/test_pi_session_writer.py)
cover stale callbacks after a successor reserves the same session or claims a
replacement Run. The installed proof is scoped to Pi 1.3.0 on Linux Bubblewrap
with the built-in shell tool and synthetic inference.

### Native Pi readback and availability — HUG-87

**Disposition**. Retain the existing Journal, Game Tape, and CLI readback
owners. The recorded save proof accompanies the native identity, writer
lineage, and original Step settlements. A historical read leaves availability
unchecked. Explicit inspection checks the selected slot and reports a bounded
reason and safe next action. Pi launch and transcript loading are outside
historical readback.

Public Run detail and all embedded Fleet Snapshot copies apply the same
private Pi projection. Task excerpts, native payloads, and free-form typed
review records stay private. The original typed acceptor retains the record
and its Step settlement. Pi continuation refusals report the prior Run and
handoff command when a prior Run was selected.

The [installed operator journey](../../backend/tests/integration/services/test_native_pi_session_journey.py)
checks successful continuation across fresh CLI processes and the public
Fleet API. The [installed compaction case][cli-compaction-tests] checks a
qualified session after an independent verifier fails, an unavailable
predecessor that retains its accepted Run, and historical readback with the
synthetic session slot absent.

### Installed exact reopen and refusal — HUG-70

**Disposition: retain** the qualified Pi RPC exact-open gate. HUG-39's native
JSON probe established the session header. The installed RPC observations add
the actual session file, native ID and active leaf. The original Journal binds
the repository subject and latest accepted writer. Its claimed Step pins the
qualified saved bytes and checks the live native identity and branch before
sending the prompt.

| Boundary | Applicable proof |
| --- | --- |
| Same saved conversation and exact active leaf | [Installed restore and compaction][cli-compaction-tests] and the [initialized operator journey](../../backend/tests/integration/services/test_native_pi_session_journey.py) exercise separate claimed Runs, retained native context, fresh authority and verified cleanup. |
| Native fallback or wrong branch | [Installed protocol probes][pi-session-tests] put real Pi replies through the exact-open gate. A fresh fallback identity, foreign session ID, older leaf and missing leaf fail before prompt delivery. A correlated refusal of the admitted history query records `PI_SESSION_HISTORY_UNQUALIFIED`. Invalid framing remains a protocol failure. |
| Missing saved file or foreign repository | The [claimed-worker journey](../../backend/tests/integration/services/test_native_pi_session_journey.py) checks a wrong repository and a renamed original file. Both refuse native work and preserve the observed saved bytes. |
| Personal stores | The [scoped storage proof](#private-pi-session-storage--hug-41) and [standard mount checks](#selected-pi-configuration-and-native-tools--hug-54) cover exclusion of other sessions and the default personal Pi store. |

This proof selects Pi 1.3.0 with its public release entrypoint
SHA-256 `e79626f2dd6f94aa45d30f3fa63cd84319a6eefcd150b353cfaf274366926774`,
Bubblewrap 0.11.1 and Linux 7.0.0-43-generic. Repository ownership comes from
admission. Native RPC supplies file, session and leaf facts. The effective
imported-runtime upgrade boundary remains HUG-137.

### Native Pi open and legacy migration — HUG-132

**Disposition**. Retain the exact file receipt and original claimed Step
reservation. Managed reopen checks the qualified native version-3 header
while verifying the exact predecessor bytes. This check precedes the
writable file mount and native launch. Historical readback and retirement
still use their existing byte receipts. Managed cross-version migration
remains unsupported.

The [installed, model-free protocol probes][pi-session-tests] show that
Pi 1.3.0 initializes an empty file, rejects wholly malformed nonempty
content, and rewrites valid older formats during open. Version-2 migration
preserves the session and original entry IDs. The probe's explicit thinking
selection then advances the active leaf. Interrupting a disposable
version-2 fixture after native truncation leaves it empty before any prompt
or rewritten entry.

The [installed managed journey](../../backend/tests/integration/services/test_native_pi_session_journey.py)
refuses empty, malformed, legacy, and foreign-header bytes in the selected
slot of a previously qualified Run. Its original Step records a typed
failure before native launch, with no new inference or save receipt.
The slot bytes and earlier Run evidence remain intact.

### Managed Pi operator journey — HUG-53

**Disposition: retain** the exact Pi RPC reopen, original writer custody
and fresh managed inference checks. The relay returns a bounded refusal
code only for an observed protocol refusal after child-process cleanup.
The accepted Step and independent verifier evidence remain under the
original execution owners.

The [installed operator proof](../../backend/tests/integration/services/test_native_pi_session_journey.py)
uses separate CLI processes with Pi 1.3.0 and Bubblewrap 0.11.1 on Linux.
It creates a saved session, continues it through an initialized Plan and
authored read-only Goon, then continues it through `ai` with new original
Run IDs and verifiers. A further explicit handoff starts a separate
OpenCode 2.1.2 Run. That handoff carries bounded Journal evidence.
History commands report prior writer, the observed opaque native identity,
persistence grade, current source and Skill identity, cleanup, and verifier
outcome. Readback leaves availability unchecked and omits the native
prompt, response, tool payload and private process arguments. The provider
in this proof is synthetic.

### Selected Pi configuration and native tools — HUG-54

**Disposition: retain** Pi's native settings and resource discovery, the
explicit public copy-on-write configuration, the managed inference broker,
and the original claimed Run and Step process owner. The standard Bubblewrap
mount check refuses the default personal store, including direct aliases and
enclosing roots. Explicit read-only mounts of public installed releases remain
available. This check does not inspect native credential or configuration bytes.

| Selected scope | Installed evidence |
| --- | --- |
| Ephemeral managed Pi with explicit public configuration | B [native config and tool proof][pi-native-config-tests]. The selected native Skill, system instructions and configured thinking reached the synthetic provider alongside fresh Oh-Goon Skill and Hashmarks evidence. Approved extension and stdio MCP tools ran under the read-only repository policy and wrote only to the private config overlay. Disabled tools refused invocation. The host config retained its original bytes and the synthetic personal store stayed outside the child. |
| Exact managed read-only reopen | B [native config and tool proof][pi-native-config-tests] and [installed restore and compaction][cli-compaction-tests]. Pi reopened the saved session under the fresh selected model, endpoint, task and native built-in read grants. It refused previous write grants and excluded valid project extension, MCP and native Skill inputs. Public configuration and explicit extension arguments remained ineligible in this mode. A failed independent verifier retained its own Run outcome. |
| Delayed selected native startup | B [installed startup cancellation][pi-startup-cancel-tests]. The original worker cancelled a stalled MCP initializer and a stalled extension startup. Birth-bound process observations verified the reaped owners and helpers with zero provider connections. |
| Upgrade selection | A [writer admission regression](../../backend/tests/unit/runtime/test_pi_session_writer.py) refuses a different admitted executable identity. B cases bind exact observed release, public entrypoint and selected mode and require requalification for another release. |

The selected runtime was Pi 1.3.0, Node 22.22.1 and Bubblewrap 0.11.1 on
Linux 7.0.0-43-generic with synthetic inference. The public `dist/bundle/cli.js`
SHA-256 was `e79626f2dd6f94aa45d30f3fa63cd84319a6eefcd150b353cfaf274366926774`.
Its loaded `cli-runtime.js` SHA-256 was
`f9811fa28c52accb8e2433499a85e857b714a002956005b7859ad00e5e35f380`.
These hashes record the selected proof inputs. Full transitive runtime sealing
remains HUG-137.

In-flight settings reload, persistent extension/MCP execution, untrusted
extension protocol origin, relocated personal stores, unqualified detached
workers and other runtime environments remain **UNQUALIFIED**. Retain the
protected restore exclusions until an installed case proves the effective
configuration and tool policy before any unauthorized activity and verifies
original teardown with the same owner.

[pi-native-config-tests]: ../../backend/tests/integration/services/test_native_pi_configuration.py
[pi-startup-cancel-tests]: ../../backend/tests/integration/services/test_native_pi_startup_cancellation.py

### Pi session retirement and fork boundary — HUG-55

**Disposition: retain** the exact-file Pi RPC adapter, scoped private
storage, and original Journal writer authority. Inspection reads the
original Journal cut and, for a currently eligible save, verifies exact
bounded file bytes through the scoped descriptor. It presents opaque
native session and leaf metadata. Public history uses the same privacy
projection as the original Run detail and events.

Retirement requires explicit authorization and the latest terminal qualified
writer. The original Journal appends a revocation before authorizing removal.
An interrupted retirement keeps the lineage unavailable. Retry proves the
same retained private directory, verifies absence and synchronizes the
directory before recording completion. Original Run and verifier outcomes
remain distinct from retirement evidence. The empty directory remains as
the exact retry binding.

The [installed component probe][pi-session-tests] on Pi 1.3.0 exercised separate read-only
parent and writable child mounts. Native `fork` refused the saved assistant
leaf. Native `clone` copied the current active leaf to a fresh native ID
with parent provenance, while the parent bytes and inode metadata matched
their pre-clone state. The generated child name differs from the product's
exact saved-file contract. Product fork remains **UNSUPPORTED**. A future
qualified fork would need to bind the admitted parent branch and the
native-created child resource through the same Journal and claimed Step.

The [installed operator journey](../../backend/tests/integration/services/test_native_pi_session_journey.py)
covers native Linux restart and retirement with synthetic inference. Its
selected scope uses the public Pi 1.3.0 release and Bubblewrap 0.11.1.
WSL2, containers, other platforms, concurrent unmanaged writers, physical
disk erasure and backup deletion remain **UNQUALIFIED**.

### Managed Pi retirement recovery — HUG-101

**Disposition**. Retain the original Journal revocation and exact Linux
file-removal owners. Pi 1.3.0 headless native deletion is **UNSUPPORTED**.
Oh-Goon verifies removal only from the admitted private slot, after checking
the latest terminal writer and its cleanup and save receipts.

The [Journal retirement tests](../../backend/tests/unit/runtime/test_pi_session_writer.py)
cover the race where another caller revokes or completes retirement while a
stale caller is acquiring its file pin. A completed operation returns the
original receipt. A pending operation verifies the exact recorded directory
and synchronizes it before accepting absence. The
[storage tests](../../backend/tests/unit/runtime/test_pi_session_storage.py)
refuse late directory and symlink substitution without removing either file.

The [installed Pi journey](../../backend/tests/integration/services/test_native_pi_session_journey.py)
exercises active-writer refusal and restart after unlink but before final
receipt publication. Retry preserves the original Run and verifier, and a
fresh continuation remains unavailable. Inspection provides the exact
authorized retry command for the original runtime. The public outputs carry
bounded metadata from the Journal.

### Pi task notes for parallel candidates — HUG-225

**Disposition**. Retain the original headless Pi command, explicit native
configuration, and managed inference broker. A note-enabled candidate loads a
single Oh-Goon extension while Pi's ambient extension discovery stays disabled.
The broker requires the extension's ready operation before delivering model
requests in this mode. The two note tools use the broker's existing private
per-Run transport and revalidate original Step authority on each operation.

Game Tape records the author, attempt, note identity and observed delivery
state. The selected mode keeps note text out of general transcript readback.
The [installed parallel notes case](../../backend/tests/integration/services/test_native_parallel_agent_notes.py)
exercises the Pi 1.3.0 tool path, Bubblewrap and the native Linux Run scopes
against a synthetic managed inference endpoint. [Parallel work requirements](PARALLEL_AGENT_WORK_REQUIREMENTS.md)

### Pi persistent-session host qualification — HUG-102

**Disposition**. Retain the existing managed Pi RPC, Bubblewrap, and claimed
Run-worker owners. This matrix adds Pi persistence evidence to the host
qualification tracked in [HUG-110](https://linear.app/hugloss/issue/HUG-110).

| Host and mode | Result | Boundary exercised |
| --- | --- | --- |
| Native Ubuntu 26.04.1 x86-64, Linux 7.0.0-43-generic, Bubblewrap 0.11.1 | **PASS** for the selected Linux persistent RPC path | Direct Git checkout and private state on tmpfs. A fresh CLI process reopens the original native session and leaf after the previous Run exits. Cancellation and deadline retain original physical cleanup and independent Step evidence in the Journal. |
| Restricted Linux execution environment | **UNSUPPORTED** for the selected Bubblewrap profile | The direct user-namespace probe was denied. The supervised preflight also failed at descriptor-based execution. The selected Pi Run refused before inference or session allocation and identifies the Bubblewrap preflight prerequisite. |
| WSL2 | **NOT_RUN** | No WSL2 environment was available. A separate installed case must prove the selected filesystem, descriptor mounts, and process lifetime. |
| Shipped Ubuntu and openSUSE application containers | **UNSUPPORTED** for managed Pi persistence as shipped | The runtime images omit Pi and Bubblewrap. An installed test of a separately configured container mode is **NOT_RUN**. |

The installed executable was Pi 1.3.0 with entrypoint SHA-256
`e79626f2dd6f94aa45d30f3fa63cd84319a6eefcd150b353cfaf274366926774`.
Native affinity exposed 16 CPUs and the observed cgroup v2 imposed no CPU or
memory ceiling. The selected cases kept their existing process deadlines.
This qualifies process restart on the tested tmpfs mount. Power-loss durability
and persistence on other filesystems are **NOT_RUN** in this host check.

Set `OH_GOON_NATIVE_PI` to the selected executable and
`OH_GOON_NATIVE_PI_RUNTIME_ROOT` to its public runtime root. Run
`uv run --offline --frozen --no-sync --project backend pytest -q -m certification`
and the following installed test selectors in the
[Pi session journey](../../backend/tests/integration/services/test_native_pi_session_journey.py).

- `test_installed_pi_session_reopens_via_initialized_plan_and_cli`.
- `test_installed_pi_active_continuation_fences_second_controller_through_cancel`.
- `test_installed_pi_continuation_deadline_preserves_original_settlement`.
- `test_installed_pi_torn_save_stays_unqualified_after_successful_verifier` and
  `test_installed_pi_save_fault_preserves_original_run_facts`.

The deadline case follows the original process through termination, exit and
descendant quiescence. The Step retains a timeout with verified cleanup,
session persistence remains unknown, and the verifier never starts. Fresh
CLI readback reports the same settlement and lineage. The save fault cases
inject ENOSPC, EIO, and interruption at the existing storage and Journal cuts.
The [storage owner tests](../../backend/tests/unit/runtime/test_pi_session_storage.py)
cover the depth bound, permissions, symlinks, injected mount topology, and
foreign source authority. The
[installed component probe][pi-session-tests] checks the external source root,
private writable session mount, and original command cleanup.

On the `e064bc08` source tree, `/usr/bin/time` measured two invocations of
`test_installed_pi_private_session_survives_original_command_and_config_cleanup`
used fresh test state on the same prepared environment. The first took 7.64 s
elapsed and 6.53 s CPU. The immediate repeat took 8.09 s elapsed and 6.86 s CPU.
Both proved process-group quiescence. OS caches were left in place, so these
figures cover first and repeated invocations under the observed host load.

## Native model usage observations — HUG-222, 2026-11-22

**Disposition: retain** the existing Codex service transport, scoped native
thread/turn, process custody, permission and private-store adaptations. The
[service execution owner][codex-execution] additionally consumes the selected
0.164.0 `thread/tokenUsage/updated` notification only for its exact owned thread
and turn. It retains one latest native `total` snapshot, with hashed scope
handles, frozen executable identity, nullable counters and explicit uncertainty.
Repeated callbacks and the notification's `last` field are not additive work.
Malformed later telemetry preserves the preceding coherent snapshot with an
incompleteness mark; failure or cancellation retains observation without changing
the original process or result authority.

The [native usage regressions](../../backend/tests/dependency_complete/test_native_model_usage.py)
exercise absent, zero, bounded/invalid, cumulative/duplicate and foreign-scope
observations, plus original cancellation and failed shutdown. One selected
installed [original service Run][codex-run-tests] passed on Linux with the public
Codex 0.164.0 release, disposable synthetic native configuration and a local
Responses fixture: four model requests, native tool edits and compaction,
independent verification, exact nonzero usage provenance in the receiving
Attempt, and original process cleanup. This qualifies protocol observation, not
live-account consumption, monetary cost, model quality or another environment.

Pi CLI/RPC, OpenCode and Codex CLI usage remain unqualified/unknown in this
delivery. In the selected Codex version, the CLI's terminal usage projects a
thread total and can supply default zeros when no native usage notification was
observed; it is not an independent per-request measurement. The selected
[native schema](https://raw.githubusercontent.com/openai/codex/rust-v0.164.0/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json)
and [CLI projection](https://raw.githubusercontent.com/openai/codex/rust-v0.164.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
retain those scope distinctions. No token total, reported model, cache/reasoning
inclusion relation, or price is inferred from absent fields, request caps, text,
native catalog prices or snapshots from different turns/Attempts.

## Upgrade and subtraction review

For a native integration change, review the applicable IDs and record an explicit
**retained**, **simplified**, **removed** or **unqualified** disposition with its
reason and focused evidence. Requalify changed external behavior with the installed
implementation using the existing test owners. Delete an obsolete adaptation,
its retired fixtures and its superseded documentation together; unknown evidence
remains unverified. Permanent product responsibilities remain with their current
owners.

For each affected record, verify the selected implementation/mode/environment,
actual production callers, protected invariant, positive and adversarial proof,
and falsifiable replacement/removal condition. Run a valid baseline before a
removal experiment; a failed replacement is a retain decision, not permission
to weaken the invariant. Preserve still-applicable refusal and failure tests,
and account for any deleted source/test protection at its accepting owner.
Native personal configuration, authentication and session data are not cleanup
targets. Review methods may use the pinned external cookbook; that checkout and
its prose never become an additional product or merge gate.

Keep this inventory's owner/test links current through ordinary tooling review.
Reference checks establish navigability only; document wording, row count or a
version label never becomes runtime admission or APP certification authority.
Ordinary backend/integration checks retain their existing marker exclusions;
environment-specific and privileged qualification runs only when selected.

[native]: ../../backend/src/oh_goon/integrations/agent_native_execution.py
[native-tests]: ../../backend/tests/unit/integrations/test_agent_native_execution.py
[resolver]: ../../backend/src/oh_goon/runtime/agent_providers.py
[resolver-tests]: ../../backend/tests/unit/runtime/test_agent_native_providers.py
[receiver]: ../../backend/src/oh_goon/integrations/fleet_plan_native_steps.py
[receiver-tests]: ../../backend/tests/dependency_complete/test_fleet_plan_native_steps.py
[broker-tests]: ../../backend/tests/dependency_complete/test_agent_inference_broker.py
[native-run-tests]: ../../backend/tests/integration/services/test_native_agent_run.py
[settlement-tests]: ../../backend/tests/unit/integrations/test_fleet_step_settlement.py
[command-tests]: ../../backend/tests/unit/engine/test_commands.py
[codex-service]: ../../backend/src/oh_goon/integrations/agent_codex_service.py
[codex-service-tests]: ../../backend/tests/dependency_complete/test_agent_codex_service.py
[codex-execution]: ../../backend/src/oh_goon/integrations/agent_codex_service_execution.py
[codex-execution-tests]: ../../backend/tests/dependency_complete/test_agent_codex_service_execution.py
[codex-installed-service]: ../../backend/tests/integration/services/test_native_codex_service.py
[codex-run-tests]: ../../backend/tests/integration/services/test_native_codex_service_run.py
[codex-context-tests]: ../../backend/tests/integration/services/test_native_codex_context.py
[codex-live-tests]: ../../backend/tests/integration/services/test_native_codex_live.py
[codex-managed-tests]: ../../backend/tests/integration/services/test_native_codex_managed_review.py
[opencode-service]: ../../backend/src/oh_goon/integrations/agent_opencode_service.py
[opencode-service-tests]: ../../backend/tests/dependency_complete/test_agent_opencode_service.py
[opencode-installed-service]: ../../backend/tests/integration/services/test_native_opencode_service.py
[cli-readonly-tests]: ../../backend/tests/integration/services/test_native_cli_readonly.py
[cli-lifecycle-tests]: ../../backend/tests/integration/services/test_native_cli_lifecycle.py
[cli-compaction-tests]: ../../backend/tests/integration/services/test_native_cli_compaction.py
[pi-session-tests]: ../../backend/tests/integration/services/test_native_pi_session_protocol.py
