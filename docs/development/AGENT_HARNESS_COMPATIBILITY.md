# Native Agent harness compatibility

Source review: **2026-10-26**. This is the canonical maintenance inventory of
native compatibility adaptations and the conformance tests that can establish
their replacement. It does not resolve capabilities, select versions or admit
execution. The [runtime owner map](AGENT_RUNTIME_OWNERS.md),
[provider guide](../AGENT_PROVIDERS.md) and [contributor contract](../../AGENTS.md)
retain those responsibilities.

The tables map existing source and tests; a test reference is not a passing test
result. Installed qualification is scoped to the exact selected implementation,
mode, policy and environment. The bounded checks below ran on 2026-10-26; other
installed cases remain **NOT_RUN** in this review. Native executable facts remain
in the frozen selection and original execution evidence, never in a second
version database here.

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

## Temporary adaptations and removal decisions

### OPENCODE-CLI-SETTLEMENT — retain pending installed replacement proof

- **Harness/mode and owner:** OpenCode CLI;
  [NativeAgentEventParser.finish][native], with state accumulated from the same
  bounded event stream.
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
- **Last verification and disposition:** 2026-10-26, installed OpenCode 2.1.2:
  success emits `step_start` and final text without `step_finish`; the current
  completed-text adaptation is required. Streaming error fails with exit 1;
  cancellation/deadline retain original process settlement. **Retain**; no fixed
  upstream version is asserted.

### CODEX-SERVICE-RESERVED-DIRECTORY — retain; native replacement fails

- **Harness/mode and owner:** Codex owned service;
  [service_arguments][codex-execution].
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
- **Last verification and disposition:** 2026-10-26, installed Codex 0.164.0:
  current policy **PASS** for both states. A temporary direct-directory-deny
  replacement **FAILED** for the absent directory with native Bubblewrap's
  `Can't create file .../.codex: Is a directory`; it passed when populated.
  **Retain** the current mapping. The failed experiment is removal evidence,
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
| Permanent Pi CLI protocol mapping | [Native parser][native] requires `agent_settled`, rather than `agent_end`, and permits recovery after a tool error. Assistant error/abort/length and aborted settlement fail. Pi 1.3.0 emits `compaction_start`/`compaction_end` for manual, threshold and overflow compaction; retired `auto_compaction_*` mappings are removed. [Native tests][native-tests] prove compaction cannot establish settlement; [installed compaction][cli-compaction-tests] proves actual overflow recovery and retained context. |
| Permanent Codex CLI protocol mapping | [Native parser][native] distinguishes recoverable `error` diagnostics from `turn.failed` and `turn.completed`. Bounded tool observations retain recognized native command statuses, including `declined`, as untrusted claims. [Native tests][native-tests] cover command dispositions, recovered diagnostics, missing completion, explicit failure and nonzero physical exit. Command status does not determine Step success. |
| Permanent Codex service transport and session mapping | [CodexServiceClient][codex-service] binds requests, notifications, permission profile and frozen instructions to one native thread. Uncertain acceptance is nonretryable; cold resume requires explicit original instructions. [Transport tests][codex-service-tests] cover wrong thread/profile, replacement configuration, lost response/restart, cancellation, deadlines and cleanup. The native profile ID does not attest effective grants or a configuration generation. |
| Permanent instruction-delivery requirement | [Native receiver][receiver] consumes the frozen handoff and mandatory qualified Hashmarks/Skill context. [Native Step tests][receiver-tests] cover the receiver; [installed Codex context][codex-context-tests] and [service Run tests][codex-run-tests] cover selected real follow-up/compaction paths using local providers. This is required task delivery, not a temporary workaround. |
| Product-owned service settlement | [Codex execution][codex-execution] combines native terminal-turn evidence with the original daemon's actual requested-shutdown receipt. [Service execution tests][codex-execution-tests] and [canonical settlement tests][settlement-tests] reject missing closure, failed turns and caller cancellation. Normal daemon shutdown normalization must remain outside campaign policy. |
| Product-owned execution and evidence | The [runtime owner map](AGENT_RUNTIME_OWNERS.md) retains admission, source custody, process supervision, deadlines, cancellation, typed acceptance, independent verification, Journal and Game Tape. Harness-owned sessions, credentials, tools, reasoning and compaction do not acquire those authorities. |
| Obsolete implementation, already withdrawn | The [native-store contract](../AGENT_PROVIDERS.md#native-owned-authentication-and-configuration) rejects the former protected credential-file mount route. [Native tests][native-tests] and [resolver tests][resolver-tests] check refusal before native state/source access. Do not retain aliases or restore the removed refresh helper as compatibility. |
| Obsolete OpenCode CLI fixture, removed | The configured-MCP fixture selected the retired `configuration_directory` route and failed before admission. Disposition: **removed** both immediate/delayed MCP variants and obsolete configuration-byte assertions; [read-only review][cli-readonly-tests] now uses the current disposable managed bootstrap. Fixture removal changed no native execution path and restored no native configuration mount. |

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
`test_cross_harness_handoff_keeps_separate_runs_and_original_verification` in the
[native Run tests][native-run-tests] exercises Codex review, Pi repair and
OpenCode re-review as separately admitted Runs using real supervised protocol
fixtures, fresh source capture, independent verification and Journal readback.
That A-level test does not qualify the three installed harnesses together.

Remaining installed gaps include configuration-generation drift and restart
uncertainty, real-account native sign-in/refresh retention, cross-Run native
session adoption and complete private-store alias/API/plugin boundaries.
Linux proof does not qualify WSL2/container execution or strict custody.

## Upgrade and subtraction review

For a native integration change, review the applicable IDs and record an explicit
**retained**, **simplified**, **removed** or **unqualified** disposition with its
reason and focused evidence. Requalify changed external behavior with the installed
implementation using the existing test owners. Delete an obsolete adaptation,
its retired fixtures and its superseded documentation together; unknown evidence
remains unverified. Permanent product responsibilities remain with their current
owners.

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
