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

Resource-admission disposition: **retain** the protocol, settlement and
reserved-directory adaptations below. Capped standard execution now verifies
that native scope placement retains the caller's aggregate limits; see the
[resource boundary](AGENT_RUNTIME_OWNERS.md). Installed-harness qualification in
a changed resource context remains **unqualified** until separately exercised.
The earlier installed results retain their recorded mode and environment scope.

Whole-Run `run.yml` budgets currently exclude Agent and repair workflows.
Disposition: **retain** the existing harness adaptations; this CLI resource
boundary does not replace their protocols or qualify another harness context.

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
| Pi persistence/reload and OpenCode external service | Product Pi persistent sessions, cross-Run native-session adoption and extension hot reload remain **UNSUPPORTED/UNQUALIFIED**. OpenCode service supports readiness only, not session execution or background-task admission. | External servers retain their operator's lifetime. Transport closure does not kill them. Pi persistence and late restored-extension callbacks remain HUG-19/HUG-89 work, not another cleanup supervisor. |

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
| Another active Run in one runtime | `RunExecutionSupervisor` permits one active Run; a second admission refuses. A different harness/profile does not grant another slot. |
| Another runtime using the same SQLite database | The singleton execution lease remains authoritative across processes and repositories. A live owner's lease is not stolen or expired by elapsed time. |
| Parallel repository targets within one Run | Existing Fleet target scheduling follows the admitted Plan's parallel-safety/parallelism policy. Current Agent ingress requires one candidate repository and one frozen native selection; it is not a mixed-native parallel-target mode. |
| Independent databases/state roots | These are separate authority domains. Their existence does not qualify shared-database admission, aggregate capacity or concurrent same-workspace mutation. An unresolved writer still needs its original drain/fencing proof. |

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
| Same native session continuation | No OH-Goon continuation API; headless calls are ephemeral | No OH-Goon continuation API | No OH-Goon continuation API; headless calls use `--no-session` | A [fixed thread/profile][codex-service-tests]; B [persisted resume + explicit compaction][codex-installed-service]; cross-Run adoption unverified | Unsupported |
| Selected tool / private-store boundary | B [managed review][codex-managed-tests], [protected HTTP context][codex-context-tests]; no general native-store exclusion claim | B [read-only native shell][cli-readonly-tests]; no general native-store exclusion or MCP conformance claim | B [read-only native shell][cli-readonly-tests]; no general native-store exclusion claim | A [capability/policy refusal][codex-execution-tests]; B [synthetic stores, reserved-directory states and phase commands][codex-installed-service], [configured MCP campaign][codex-run-tests] | Protected execution explicitly refused |
| Independent verification / original durable evidence | B [original context Run][codex-context-tests], [lifecycle readback][cli-lifecycle-tests] | B [typed review + independent verifier][cli-readonly-tests], [cancelled receipt readback][cli-lifecycle-tests] | B [typed review + independent verifier][cli-readonly-tests], [lifecycle readback][cli-lifecycle-tests] | B [original Run and campaign][codex-run-tests] | Unsupported |

Same-session continuation, fresh-session handoff and choosing a harness for a new
Run are separate capabilities. A prior-Run handoff transports explicit untrusted
task context; it grants no permission and carries no native transcript. Native
session migration across harnesses is unsupported. An admitted Step cannot change
harness when one fails; another selection requires fresh resolution/admission.
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
| Correlated RPC `get_state` supplies session ID/file; `get_entries` supplies the active leaf. | **DG-01: select native RPC for pre-turn session and branch identity**, conditional on the existing supervised input-channel proof in HUG-139. The probe uses bounded one-shot requests; sustained bidirectional control remains unqualified. |
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

[HUG-71](https://linear.app/hugloss/issue/HUG-71) protects the currently supported
native invocation before persistent-session selection is enabled. Exercise the
selected installed parser, including its `--` terminator, positional tasks,
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
early refusals and valid caller options. Requalify framing on a native upgrade;
these results do not certify arbitrary extensions or new persistent modes.

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
