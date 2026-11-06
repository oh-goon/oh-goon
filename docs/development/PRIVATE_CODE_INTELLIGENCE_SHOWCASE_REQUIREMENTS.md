# OH-GOON — Private Code Intelligence & Backstage Catalog Showcase Requirements

**Status:** Proposed developer handoff; this document specifies intended behavior, not completed/qualified product behavior.
**Priority:** P0 demonstrable product story; P1 guided product UX; P2 optional provider-backed extensions.
**Primary owner:** OH-Goon product/demo orchestration and acceptance.
**Upstream evidence owner:** Hashmarks. **Methodology owner:** Agents Cookbook. **Format owner:** Backstage.
**Starting fixture:** [OH-Goon PR #170](https://github.com/oh-goon/oh-goon/pull/170), an illustrative synthetic local-discovery/catalog example. Verify PR status and exact source revision before building upon it.
**Normative precedence:** Existing OH-Goon repository identity, Hashmarks mandatory SSOT API, privacy, execution, artifact-retention, generated-contract, and Game Tape contracts win over this feature proposal. Any necessary contract change must go through its real owner; this document does not grant alternate authority.

## 1. Executive decision and motivation

### Problem

OH-Goon has significant control-plane capabilities, but unfamiliar engineers can encounter its terminology before seeing an immediately useful result. A lengthy architecture explanation or a CI-only example does not quickly answer the buyer's question: **what can I accomplish with my private code, under whose authority, and can I trust what happened?**

The showcase must turn that abstract value into one ordinary engineer's journey:

> "Find the code that handles an order, see which languages are used, who last changed it, which team owns it and what depends on it; then create a reviewable Backstage `catalog-info.yaml` without giving a remote model the repository or automatically publishing anything."

It must show that OH-Goon provides **controlled action and durable accountability**, while Hashmarks provides **bounded, provenance-backed local repository understanding**. The same codebase facts must not be rediscovered by an OH-Goon-specific scanner.

### Why this comparison, and what it does **not** mean

Spotify describes Xirp as a vendor-neutral, multi-agent development environment and Portal as its connected organizational context layer (services, dependencies, ownership, decisions). This proposal is inspired by the resulting **context-first engineering experience**, not a commitment to replicate Xirp, Portal, multi-agent worktrees, organizational sync, or Spotify's claimed performance results.

OH-Goon's differentiator in this example is **local-first evidence + bounded execution + Game Tape + explicit export**, not "we built another source-code search engine" and not "we have Spotify's whole product."

References: [Spotify on Xirp and Portal](https://portal.spotify.com/blog/introducing-xirp), [Spotify on the context problem](https://portal.spotify.com/blog/the-hidden-tax-on-your-ai-agents), [Backstage catalog descriptor](https://backstage.io/docs/features/software-catalog/descriptor-format/).

### Success in one sentence

A developer unfamiliar with OH-Goon can complete a **five-minute demonstration after dependencies are prepared**, answer all five questions with appropriately qualified evidence, preview a safe Backstage descriptor, deliberately execute the generator as one OH-Goon Run, inspect Game Tape, and export the exact retained artifact, with **no external AI model and no accidental external disclosure**.

The five-minute figure is a **product target to measure**, not an observed result or a release-hardening gate. Accuracy, authorization, no unintended effects, and proof outrank the time target.

## 2. Current baseline vs requested work

| Surface | Inspected baseline / safe statement | Required direction |
| --- | --- | --- |
| Repository intelligence | Hashmarks has local CodeMap, orientation, search, source/relationship evidence and public OH-Goon integration. | Reuse its released producer and public `oh_goon.integrations.hashmarks` exports. Confirm precise language, ownership, and completeness APIs before promising them. |
| Standalone local search | Existing `examples/agent-skill-local-search/` demonstrates captured-source, recipient-free search and Game Tape. | Reuse the same principles; do not create another search runtime. |
| Catalog fixture | PR #170 proposes a synthetic Python/TypeScript repo, native Git author check, reviewed ownership, independent renderer, Plan/Goon and artifact export. | Validate against current main; repair genuine gaps in that PR or a cohesive follow-up, without claiming its standalone steps are one integrated product UI. |
| OH-Goon runtime | Existing registered-target `run.yml` → Plan → Goon → Step/FleetRun → Game Tape/artifacts. | Any execution must follow this existing lifecycle; no demo runner or second database. |
| Privacy | Local-first server and no built-in telemetry/source upload are documented; provider calls are explicit, and native tools may use networks. | Distinguish "no model selected" from independently **enforced and measured** no-external-egress. Do not claim blanket zero egress from the default trusted-local mode. |
| Catalog publication | Backstage supports Component descriptor files. | Local candidate generation/export only. Commit, push, registration, or external publication are separately authorized, out of P0. |
| Product UI | No inspected evidence of a completed OH-Goon Xirp-like single-screen catalog assistant. | P0 may be a cohesive CLI+Game Tape demo. A guided UI is a P1 feature, not current capability. |

**No inferred implementations:** Missing producer features or unverified Run/protection behaviors remain gaps or `UNKNOWN`. The developer must inspect the exact pinned Hashmarks and OH-Goon revisions before naming new APIs or marking acceptance complete.

## 3. Users and jobs to be done

1. **New engineer:** "Show me where the behavior lives, which language it uses and what to read next, without learning the whole repository."
2. **Tech lead / reviewer:** "Show commit provenance separately from declared responsibility, and surface ambiguity instead of fabricating an owner."
3. **Platform engineer:** "Turn approved facts into a Backstage Component descriptor under one auditable execution lifecycle."
4. **Security reviewer:** "Prove what stayed local, what data could have been exported, which external endpoints were allowed, and that denial cases actually fail."
5. **Potential adopter:** "See an easy-to-follow product result, not a slideshow of subsystems."

A successful first-run demo requires no paid provider, private token, organization integration, running Backstage server, or access to any real confidential source.

## 4. Required end-to-end story / operator script

The reference fixture is a **synthetic service repository** with Python order submission, a TypeScript client, a hypothetical CODEOWNERS declaration, deliberately controlled Git commits and at least one dependency or import relation where the producer can report it.

| Stage | Presenter action / question | Visible result | Authoritative owner |
| --- | --- | --- | --- |
| 0. Prepare | Start from a preinstalled local checkout; select a registered synthetic repository explicitly. | Exact target and mode ("local, no AI provider") visibly identified. | OH-Goon subject/admission; native source-selection owner. |
| 1. Orient | "What is in this repository?" | Languages/projects/files with source scope, freshness and observed coverage. | Hashmarks. |
| 2. Locate | "Where is order submission implemented?" | Bounded path/symbol/line evidence, not an invented answer or unbounded source dump. | Hashmarks. |
| 3. Author | "Who last changed this file?" | Commit ID, commit author and date, labeled **last matching commit**, not original author or team owner; mask contact details in shareable screenshots. | Native Git. |
| 4. Team | "Who maintains this component?" | Declared CODEOWNERS evidence **if supported and qualified by Hashmarks**; otherwise explicitly shown as an inspected human-provided declaration needing review. | Hashmarks for repository-derived interpretation; human for catalog ownership decision. |
| 5. Relation | "Which components/imports may be affected?" | Native producer-bound relationship evidence with ambiguity/coverage; no claim of complete dependency knowledge from an incomplete observation. | Hashmarks. |
| 6. Review | "Create catalog entry." | Bounded preview of permitted metadata, evidence references and the explicitly selected Backstage owner. Not a write operation. | OH-Goon presentation/approval; Backstage format authority. |
| 7. Execute | "Generate local catalog-info.yaml." | One admitted existing Plan/Goon/Step Run, terminal result, retained candidate artifact and Run ID. | OH-Goon FleetRun, Step, Journal, Game Tape. |
| 8. Export | "Export the approved result." | Exact retained bytes saved to a chosen local destination, with no automatic Git commit, Backstage registration or upload. | Existing OH-Goon artifact export with explicit user effect. |
| 9. Explain | "How do we know no private code was sent to a model?" | No provider step plus independent, scoped egress/provider evidence; disclosed/retained fields can be reviewed. | OH-Goon execution evidence + independent network test; never a self-certified marketing assertion. |

**Demonstration language:** Say "Hashmarks found this source at revision X", "Git records Y as the last commit author", "the team declaration suggests Z, which a human approved", "OH-Goon executed a bounded generator and retained its result." Do not say "the AI knows the owner", "no network traffic is possible", or "catalog published" unless independent evidence proves the stronger claim.

## 5. Functional requirements (P0 = required for the first persuasive showcase)

Every `SHALL` is a normative proposed behavior. A requirement is **not implemented** until the corresponding acceptance evidence below exists.

| ID | Priority | Requirement | Observable acceptance |
| --- | --- | --- | --- |
| PCI-001 | P0 | Provide one self-contained **synthetic** example, including source, Git setup instructions, owner declaration, offline-ready metadata, workflow, expected YAML, native tests and a short demo script. | A new contributor reproduces it without credentials, real private repositories or an external provider. |
| PCI-002 | P0 | Resolve an explicit, registered exact repository target; show the selected root/revision and avoid accidental parent/sibling discovery, symlink escape, or implicit repo substitution. | Wrong/outside/unregistered target is refused before source exposure or effects. |
| PCI-003 | P0 | Use Hashmarks as the sole repository evidence producer for language/project overview, code search, paths, symbols, references, ownership declarations where supported, and relationships. | All consumed repository facts trace to public `oh_goon.integrations.hashmarks` or to an explicitly standalone Hashmarks CLI demonstration; no independent OH-Goon index/parser/fallback. |
| PCI-004 | P0 | Return source-localized search results within explicit limits and show evidence generation, freshness, source identity, coverage/truncation and ambiguity where the producer supports them. | Positive hit links to real fixture source; stale/incomplete/no-match result is not represented as proven repository-wide absence. |
| PCI-005 | P0 | Answer language questions from producer-qualified observations; distinguish languages actually observed from runtime/platform guesses. | Python and TypeScript fixture facts are supported; unknown or unexamined source remains unknown, not a fabricated 100% language inventory. |
| PCI-006 | P0 | Use native Git to answer precisely "who authored the last commit touching this path" with commit/revision scope. Do not infer "first author", "sole author", "maintainer", or person responsible. | Fixture commit lookup agrees with native Git; non-Git/no-history path reports unavailable; author email is excluded from shareable output. |
| PCI-007 | P0 | Separate CODEOWNERS or other declared owner evidence from Backstage `spec.owner`; require an explicit reviewer-selected, valid Group/User reference before artifact generation. | Missing, conflicting or unqualified declaration is marked for human resolution; no fallback from Git username, author email or repository name. |
| PCI-008 | P0 | Show a bounded dependency/import/relationship example only when producer evidence exists and carries appropriate qualifications. | Correct relation shown for fixture or labeled unsupported/incomplete; no fabricated impact graph. |
| PCI-009 | P0 | Require review of **all metadata to be exported**, including apparently harmless names/descriptions/teams; do not pretend field allowlisting is content redaction. | Candidate preview shows exact values; reviewer approval binds to this candidate and exact observed source identity. |
| PCI-010 | P0 | Generate only the minimal Backstage `Component` descriptor from a strict field allowlist and approved values. Validate schema, types, escaping and existing destination conflicts. | Byte-deterministic valid `catalog-info.yaml`, correct `metadata.name`/`description`/`tags` and `spec.type`/`lifecycle`/`owner`; unknown fields/invalid values rejected. |
| PCI-011 | P0 | Generator shall **not read source files, Git history, CODEOWNERS, credentials or Hashmarks internals** to rederive approved facts; it only serializes reviewed metadata. | Test with deliberately out-of-scope secret/source fixture proves no extraction; no extra parser/index authority is introduced. |
| PCI-012 | P0 | Perform generation using **the existing** registered target → source resolution/locking → FleetRun admission → Plan/Goon/Step → result/evidence lifecycle. | Real installed CLI Run succeeds; no alternative runner, target-free subject, side DB or ad-hoc persistence. |
| PCI-013 | P0 | Retain exact output under existing artifact policy; Game Tape displays admitted source/run/result identity and export procedure. | Fresh process can inspect Run and export byte-identical retained result; later worktree edits do not rewrite history. |
| PCI-014 | P0 | Export only on explicit request to a selected local destination; never auto-write target `catalog-info.yaml` or commit/push/register/publish to Backstage. | Run success leaves original source checkout unchanged; explicit export refuses overwrite and requires independent approval for future publication. |
| PCI-015 | P0 | Default showcase shall have **no model or agent-provider call** and no provider credential requirement. Disable optional interactive update checks and do not allow implicit network acquisition during the measured demo. | Provider invocation count is zero and the offline-ready fixture completes under the specified external-egress test boundary. |
| PCI-016 | P0 | Distinguish local-by-design properties from proven no-egress properties; provide a reproducible network-observation/denial protocol with scope and exclusions. | Demo docs state precise measured phase, allowed loopback, bootstrap exclusions, denied external endpoints and observed result; without enforcement report `NOT QUALIFIED`. |
| PCI-017 | P0 | Provide a failure-first, 1-page operator guide with exact checked commands and expected outcome, and label what is implemented vs proposed. | A new engineer follows it without correcting undocumented placeholders or guessing API flags. |
| PCI-018 | P0 | Keep accepted data and display/caches scoped to selected repository, captured generation, run and caller visibility; no source/PII in shareable catalog or browser persistent storage. | Cross-repository/cross-run and browser storage tests detect no denied source or identity bleed. |
| PCI-019 | P1 | Offer an easy, guided OH-Goon view for the **same** evidence/questions, approvals and original Game Tape Run—not an alternate frontend-owned repository catalog. | New user completes journey with minimal CLI knowledge; frontend consumes canonical backend projection and generated contracts. |
| PCI-020 | P1 | Produce a short reproducible video/screenshot set and evidence-backed before/after narration based on the actual qualified fixture. | Public assets reveal no secret, private path, email, unauthorized team detail or invented timing claim. |
| PCI-021 | P2 | Consider an optional model-powered natural-language layer only behind explicit provider selection and disclosure policy. | Separate requirements and qualification prove exact prompt data, recipient, history scopes and consequences; not a dependency of P0/P1. |

### P0 design rules

- The **discovery presentation** may initially be multiple native commands followed by one OH-Goon Run. If a command is external to OH-Goon, say so; do not advertise a single integrated interface.
- A new UI may be valuable, but it is **not permission** to add a second repository database, duplicate search index, owner heuristic, lexical parser, Git-history reimplementation, or runtime lifecycle.
- The canonical catalog candidate need not be stored in a new database: use existing approved metadata input and retained Step artifact. Do not create a persistent authoritative "service catalog" inside OH-Goon.
- Do not claim a human-verified `spec.owner` is implied by Hashmarks findings. Language/relationship evidence and owner approval are different authorities.
- Source preview and Git author identity are **sensitive display data**, even when not included in catalog output.
- For P0, no Backstage instance is needed. Catalog YAML compatibility is proved by schema checks and optionally an independent Backstage validation environment; *publication* is not in scope.

## 6. Ownership and anti-duplication contract

| Fact/action | Canonical owner | OH-Goon may | OH-Goon shall not |
| --- | --- | --- | --- |
| Source member/revision, language, symbol, imports, repository-declared owner interpretation, relationship, freshness, absence/completeness | **Hashmarks** | Consume producer evidence via its public OH-Goon integration; preserve provenance/qualifiers; apply consumer policy. | Reparse languages, CODEOWNERS, imports or dependencies to manufacture competing repository truth. |
| Commit author/date/commit identity for a path | **Native Git** | Invoke/transport verified native facts in the correct selected repository; label what they establish. | Pretend commit history establishes team responsibility, or fabricate an author when Git metadata is absent. |
| Catalog owner choice and export permission | **Human reviewer / explicitly configured policy** | Show proposals and require approval bound to exact input. | Automatically promote Git author or unqualified inferred owner into `spec.owner`. |
| Component YAML schema | **Backstage format** | Produce and validate a deterministic compatible descriptor. | Implement an independent Backstage registry/ownership authorization system. |
| Run admission, workspace isolation, process/network policy, supervision, cancellation, recovery, artifact retention, Game Tape | **OH-Goon current lifecycle owners** | Apply existing policy and reuse original retained receipts/commands. | Create "showcase-run", independent scheduler/DB/cache, fake Run IDs, synthetic success or a special bypass. |
| Review methodology | **Agents Cookbook** | Follow existing portable skills and tests. | Fork canonical skills into a second editable skill owner. |
| Independent comparison/scoring, if later requested | **EvalShot** | Supply exact frozen workload and evidence. | Claim evaluator scores based on anecdotes or demo appearance. |

Existing authority references:
- [Hashmarks / OH-Goon boundary](../architecture/hashmarks-oh-goon-authority-boundary.md) and [mandatory SSOT public integration](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md).
- [Repository contributor contract](../../AGENTS.md), [Goon authoring](GOON_AUTHORING.md), [Game Tape](GAME_TAPE.md), [privacy](../PRIVACY.md), [documentation anti-drift](DOCUMENTATION_ANTI_DRIFT.md).
- [Existing frozen local search](../../examples/agent-skill-local-search/README.md) and [independent application example](../../examples/independent-application/README.md).

If Hashmarks does not expose the required typed language, CODEOWNERS/ownership or relationship fact through the **pinned public producer**, file one **bounded upstream Hashmarks gap** with the evidence needed. It is not acceptable to introduce a temporary production OH-Goon parser while waiting. For the synthetic P0 walkthrough, explicit manual declaration inspection can be shown as a separate **human step**, not as automated Hashmarks/OH-Goon ownership inference.

## 7. Candidate contract and Backstage output

### Approved candidate: illustrative data, not a new execution API

The renderer accepts exactly these reviewable **input values**: `name`, `description`, `owner`, `type`, `lifecycle` and `languages`. If the product later supports additional Backstage kinds/fields, update the explicit contract and tests rather than passing arbitrary fields through. The source evidence, its identity, reviewer approval and execution receipts belong to **their existing owners**; this JSON shape is **only a fixture**.

```json
{
  "name": "order-gateway",
  "description": "Synthetic order gateway for a local code-discovery demonstration.",
  "owner": "group:default/platform-team",
  "type": "service",
  "lifecycle": "experimental",
  "languages": ["python", "typescript"]
}
```

Expected descriptor (example values, not a claim about a real organization):

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: order-gateway
  description: Synthetic order gateway for a local code-discovery demonstration.
  tags: [python, typescript]
spec:
  type: service
  lifecycle: experimental
  owner: group:default/platform-team
```

**Binding:** A reviewer approving candidate C for source generation G must not authorize a different candidate or generation. The existing source/admission and Run identity owners must provide the required binding and refusal if the source changes before admission. Do **not** invent a duplicate candidate authority, persistent approval database or extra YAML signing requirement merely for the demo. If current OH-Goon cannot carry the needed binding through its legitimate input/Run owners, identify that actual contract gap explicitly.

**Information sensitivity:** A safe field name is **not** proof its value is public. Real team names, repository paths, descriptions, internal URLs, employee names and embedded secrets may all be sensitive. For real deployment, export follows operator-selected disclosure policy; the synthetic showcase must never contain such information.

## 8. Privacy, security and disclosure requirements

### Required data-flow boundary

```text
Explicit local repository selection
  -> Hashmarks local repository evidence (generation, limits, source references)
  -> local display / native Git commit facts / human team approval
  -> explicitly reviewed bounded metadata candidate
  -> original OH-Goon admitted Plan/Goon/Step
  -> retained artifact + Game Tape in existing local authority
  -> explicit local export
  -> STOP (no automatic Git remote, catalog registration or provider upload)
```

The browser may render **authorized** source excerpts but must not independently store repository/business state in `localStorage`, `sessionStorage` or IndexedDB. The existing loopback access capability is an API authorization mechanism, **not a tenancy/role-based organization ACL**. Do not advertise multi-user enterprise authorization without a separate design and proof.

### Threats, controls, evidence

| Threat | Mandatory control | Deterministic verification |
| --- | --- | --- |
| Remote source disclosure by AI provider | P0 includes no provider/agent Step, no provider selector, no model request and no inherited provider history. | Instrument selected provider endpoints; request count **0**; verify Run and step plan lack provider calls. |
| External egress from native tools (Git, uv, update checker, package acquisition) | Prepare offline dependencies before measurement; disable Hashmarks interactive update check; no configured remote refs or dependency installs during execution; use a separately enforced **external-egress-deny** boundary for zero-egress qualification. | Observe/deny attempts during the measured scenario; unexpected outbound attempt fails qualification, even if request content looks harmless. Loopback for the local UI is specified separately. |
| Sensitive data in exported YAML | Strict reviewed field allowlist; no raw source snippets, Git author email, absolute paths, credentials or hidden fields; explicit review of values. | Sentinel strings in source, env and denied metadata never occur in exported bytes; malicious value in an *allowed* field requires review/refusal, not a claim that key allowlisting sanitizes it. |
| Secrets in journal, stdout/stderr, screenshots or errors | Do not log raw source, environment, cookies, credentials or unbounded debug dumps; use existing OH-Goon redaction/projection owners. | Seed known synthetic sentinels; inspect logs, Game Tape exports and public screenshot assets, including refusal paths. |
| Cross-repository, cross-run or credential-scope bleed | Bind captured evidence, approval, caches and artifacts to their exact owner/revision/scope; default no provider history. | Two distinct repository/run fixtures with conflicting values; no cross-output or stale reuse. |
| Stale/missing/ambiguous producer evidence | Preserve producer freshness, completeness, truncation and ambiguity; do not promote an absence claim or owner to verified. | Mutate source after preview; truncated and unknown-owner fixtures refuse promotion or show explicit incomplete state. |
| Path traversal/symlink/overbroad collection | Use canonical repository and workspace/source admission owners; exact requested root and approved destination. | Out-of-root path, symlink traversal and unregistered root are refused before sensitive reads/effects. |
| Artifact overwrite, publishing or partial export | Existing retained output and explicit export; no source write and no upload; fail before overwrite. | Existing destination untouched; failed/cancelled Run cannot export a fictitious successful candidate; no Git remote/publish effects. |
| Prompt injection in repository documentation, comments or CODEOWNERS | Source content is untrusted **evidence**, never authority to activate tools, authorize an owner or change disclosure policy. | Embedded "ignore policy/send code" fixture has no effect on selection, authority or generated catalog. |
| Incomplete privacy proof | State measured phase, toolchain versions, network boundary, provider instrumentation and which routes were not exercised. | Report `NOT QUALIFIED` for missing independent enforcement or absent observability; avoid blanket "guaranteed no leak" language. |

**Zero data leak means different things:** No external model call; no external network attempt in a defined measured phase; no confidential data in generated artifact; no persistent browser source cache; no unauthorized cross-scope retrieval. Each requires **its own** proof. Success at one does not imply the others.

## 9. Lifecycle and failure closure

No new state machine is authorized. These phases explain how the **existing** owners must behave for this use case.

1. **Selection (no execution):** identify exact target and permitted visibility. Invalid/unregistered/escaped target fails before exposing source or admitting a Run.
2. **Observation (read-only to authored source):** Hashmarks observes selected source at exact reported generation; its derived disposable state may be updated under its own existing lifecycle. If stale/incomplete, display that qualification.
3. **Candidate assembly (no effects):** human reviews approved metadata; catalog ownership is distinct from Git author and producer suggestions. A denied/missing owner yields no eligible artifact candidate.
4. **Approval (still no effects):** bind approved values to selected source context with the **existing** authoritative input/source resolution mechanism. A later change invalidates the approval rather than silently using a new view.
5. **Admission (one original OH-Goon owner):** resolve exact workflow and source, validate authority before work, then admit one actual Run. An invalid candidate/source is refused with no fake Run success.
6. **Execution (one original Step):** independent renderer consumes the frozen approved metadata; it cannot read the author's original repository or trigger a remote provider. Cancellation/timeout/process death use normal OH-Goon supervision and terminal/uncertain outcome semantics.
7. **Retained output (existing content and receipt owner):** successful output is validated against the accepted artifact contract and retained with original Step/Run lineage. Partial, failed or cancelled output is not relabeled successful.
8. **Export (explicit, separate effect):** read the original retained receipt; refuse overwrite and scope mismatch; if an export fails mid-effect, expose the failure rather than inventing completed export. Cleanup remains with the existing artifact/export owner.
9. **Later inspection/recovery:** Game Tape and fresh-process commands read the **original** Run. A newer repository checkout or repeated run must not rewrite prior receipts or borrow prior approvals. Unresolved original execution remains unresolved until its existing recovery owner can prove a result.

### Concrete failure cuts to test

- Source changes between orientation and approval; source changes between approval and Run admission.
- User selects an owner but changes description afterwards; candidate digest/approval is no longer the same approval.
- Run cancellation/timeout occurs after output file creation but before sealed artifact retention.
- Run finishes but terminal/artifact receipt cannot be read by a new CLI process.
- Export destination already exists; export is denied before overwrite.
- Network is unavailable; correct offline fixture still completes; unexpected network attempt is recorded as qualification failure.
- Two Runs from different registered repositories finish in reverse order; neither exposes the other's evidence or artifact.
- Runtime crashes during a Step: existing Run recovery classifies original outcome; no automatic replay/publish under a stale approval.

No speculative custom retry, extra session-history database, compensating "success" checker, or demo-specific recovery handler is permitted. When the original lifecycle owner cannot establish the outcome, report its original unresolved/failure state with sufficient evidence rather than silently retrying.

## 10. Test and acceptance matrix

Use **real public CLI/Run paths** for the end-to-end assertions. Pure renderer unit tests are valuable but cannot alone prove FleetRun, source identity, artifact retention, or privacy.

| Test | Primary requirements | Fixture / action | Pass / failure oracle |
| --- | --- | --- | --- |
| AT-01 Happy path discovery | 001–005 | Synthetic Python + TS project; Hashmarks orient/find. | Source-backed correct hit and language facts with source/revision qualifications. |
| AT-02 Git author semantics | 006 | Two controlled commits by different synthetic identities; query one exact file. | Native Git last matching commit author is shown, not "service owner". |
| AT-03 Owner resolution | 007, 009 | Valid, absent and conflicting CODEOWNERS/declared owner variants. | Only explicit approved group enters `spec.owner`; ambiguous variants wait for review. |
| AT-04 Relations | 008 | One known import plus unresolved target. | Producer relationship reported; unsupported relation not invented. |
| AT-05 Deterministic descriptor | 009–011 | Same approved candidate twice in fresh isolated workspaces. | Byte-identical valid Backstage Component YAML and expected field set. |
| AT-06 Content rejection | 009–011 | Unknown input field, empty owner, malformed tag, invalid lifecycle, oversize metadata. | Nonzero failure, no published artifact; no sensitive input echoed to logs. |
| AT-07 Actual Run | 012–013 | `oh-goon workflow inspect`, `validate`, `prepare`, `run` on exact registered target. | Original Run completed, actual Step success, Game Tape/outputs show truthful lineage. |
| AT-08 Durable readback | 013 | New CLI process; edit/delete later working copy where safe; export prior Run's artifact. | Original retained output still byte-identical or explicit retention-unavailable result; never substitute current source. |
| AT-09 No source mutation | 014 | Record Git/worktree content before/after successful Run. | No added `catalog-info.yaml` in target checkout and no source writes. |
| AT-10 No overwrite/no publish | 014 | Pre-existing export target; instrument Git remote/Backstage endpoints. | Existing bytes unchanged; no commit, push, remote call or registration. |
| AT-11 No model/provider | 015 | No provider set; synthetic and normal user config variants. | Measured provider calls **0**; no ambient default/provider-history request. |
| AT-12 Egress boundary | 015–016 | Offline-prepared fixture under independently enforced/tested external-egress-deny boundary, allow only specified local loopback. | No unauthorized external attempt; otherwise not qualified, even if output succeeds. |
| AT-13 Cross-scope isolation | 002, 018 | Two repositories/Run IDs with conflicting sentinel metadata. | No cross-repository facts, cache payloads, output or approval reuse. |
| AT-14 Stale/truncated evidence | 003–008, 009 | Modify source, bound result, missing owner, incomplete discovery. | Provenance/unknown state surfaced; false "no match/complete/approved" refused. |
| AT-15 Cancellation/crash | 012–014 | Kill/cancel before retention and exercise existing recovery/inspection. | No phantom success/export; original terminal or unresolved state preserved. |
| AT-16 Path and browser | 002, 018 | Out-of-root symbolic link; try browser persistence of source/Run state. | Denial before source exposure; browser non-persistence contract holds. |
| AT-17 Source-instruction attack | 009, 011, 015 | Add malicious instruction text inside source docs/comments. | No tool authority change, no provider call, no unauthorized metadata export. |
| AT-18 Presenter dry-run | 001, 017, 019–020 | Engineer unfamiliar with project follows actual demo guide and explains the output. | Completes real path and can state "Hashmarks evidence vs Git author vs approved owner vs OH-Goon execution"; record real time and blockers. |

**Test quality:** Use real native Git history and installed producer for contract-critical cases; keep fakes only at narrow unit seams. Freeze fixture source identity. Prove meaningful counterfactuals rather than only asserting happy-path JSON keys. Run focused tests early (`python -m pytest -q --maxfail=1` where that matches the selected native suite) and existing relevant architecture/CI checks; do not add a new global merge gate to enforce a promotional example. Nightly/release owners keep comprehensive verification.

A network test without enforcement/instrumentation is **insufficient** to establish no egress. A standalone renderer unit test is **insufficient** to establish a finished OH-Goon Run. A successful Run is **insufficient** to prove the exported metadata was authorized.

## 11. Delivery plan, dependencies and concrete ownership

### M0 — Ground the starting fixture (required first)

- Inspect PR #170 against **current `main`**, ``examples/private-code-catalog/``, ``examples/agent-skill-local-search/``, actual CLI help, Goon schema, lock/capture behavior, Hashmarks pinned APIs and retention contracts. Fix any genuine command/API/path issue; do not treat PR prose as runtime proof.
- Establish a source revision, installed platform/runtime versions and quick native reproduction log. Confirm what is implemented vs merely narrated.
- Cross-check requirement IDs against the existing owners. If language/owner/relations are not available with sufficient producer evidence, file a Hashmarks-owned task with a failing fixture and desired typed proof.
- **Exit:** documented exact baseline and testable delta; no architectural conflict, no unsupported assertion.

### M1 — Ship a truthful P0 runnable showcase (first product milestone)

- Keep one synthetic sample and one independent catalog renderer; review/refine PR #170 or a coherent successor.
- Prove all **P0** discovery, native author, ownership review, candidate validation, single original Run, durable Game Tape, explicit local export and negative cases.
- Supply an offline-ready, no-provider mode; test the specified external-egress boundary separately and document restrictions.
- Write one short script that a presenter can read without explaining OH-Goon internals first.
- **Exit:** AT-01…AT-17 decisive evidence at exact source/tool versions; no false privacy/feature claims; ready for unfamiliar-engineer AT-18.

### M2 — Guided OH-Goon experience (P1, contingent on M1 evidence)

- Prototype a coherent "Explore → Review → Generate → Inspect" flow in the **existing** frontend/backend layers, reusing `oh_goon.integrations.hashmarks` and the current read/Run/admission/retention owners.
- Never make the UI its own search, owner or catalog authority. Any new backend route flows into authored backend schema → generated OpenAPI → generated frontend clients → authored React. Respect `docs/PRIVACY.md` browser storage prohibitions.
- Provide evidence labels ("Source: Hashmarks / Git / Reviewed owner / OH-Goon Run") and empty/error states at every stage; do not hide uncertainty for visual polish.
- Capture clean synthetic screenshots and optionally a short video from **actual** qualified behavior.
- **Exit:** AT-18 under intended UI; no API/SSOT/privacy regression.

### M3 — Optional expansions (not required for this demonstration)

- Optional local-model Q&A or provider-backed session, explicit disclosure prompts, permission scopes and provider-history denial; requirements and test campaign **separate**.
- A real Backstage registration/publish flow, explicit operator authorization, external API credentials, idempotency/retry and irreversible-effect recovery; **separate** feature contract.
- Multi-repository enterprise catalog, SSO/RBAC, knowledge ingestion, architectural-decision indexing and persistent cross-team context; **not promised** by P0/P1.

**PR packaging:** Prefer one cohesive PR for fixture/runtime fixes and acceptance tests, one for a genuinely independent frontend feature when needed, and targeted upstream Hashmarks changes for proven producer gaps. Avoid a stream of tiny PRs or parallel implementations. Documentation and checked demo evidence must travel with the behavior they describe.

## 12. Agents Cookbook quality method for the developer team

These are **review methods**, not additional Oh-Goon runtime plugins or a new mandatory PR gate. Load their canonical sources from [Agents Cookbook](https://github.com/Hugloss/agentsCookbook/tree/main/skills).

| Skill | Specific question for this implementation | Expected reviewer output |
| --- | --- | --- |
| [`fact-grounding-auditor`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/fact-grounding-auditor/SKILL.md) | Does the pinned current source actually expose the APIs, file paths, tool permissions and CLI commands claimed in the demo? | Supported/contradicted/unresolved claim with source revision and disconfirmation test. |
| [`single-source-of-truth-review`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/single-source-of-truth-review/SKILL.md) | Is any OH-Goon code **independently establishing accepted repository truth** also owned by Hashmarks? | Trace accepted producer/consumer paths, not an accusation based only on repeated checks. |
| [`evidence-visibility-enforcement-review`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/evidence-visibility-enforcement-review/SKILL.md) | Can denied, stale or wrong-scope evidence influence a query, preview, cache, Run or export? | Exact reachable leak/influence and earliest responsible enforcement boundary, or explicit insufficient evidence. |
| [`requirements-lifecycle-closure-review`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/requirements-lifecycle-closure-review/SKILL.md) | What happens at every **effectful cut** from approval to artifact export on failure, cancellation or crash? | Deterministic owner/state/teardown/recovery contract and targeted test. |
| [`validation-gap-finder`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/validation-gap-finder/SKILL.md) | Which minimal real-path and negative cases prove every P0 obligation? | Requirement → observable oracle, and what remains unqualified. |
| [`implementation-dry-run`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/implementation-dry-run/SKILL.md) | Can an engineer execute the plan in order without guessing source/owner/command/permission? | Concrete blocked step and smallest missing prerequisite, or bounded feasible path. |
| [`plan-gap-scout`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/plan-gap-scout/SKILL.md) | Did implementation forget command fixes, caller paths, docs, output retention, cleanup or privacy tests? | Verified necessary gaps only, not generic improvement suggestions. |
| [`plan-contract-guard`](https://github.com/Hugloss/agentsCookbook/blob/main/skills/plan-contract-guard/SKILL.md) | Is the final handoff scoped, executable, testable and honest about unresolved evidence? | Go/no-go for the declared P0 scope, never "fully qualified" from a complete-looking document. |

### Five calibration questions before raising an issue

1. **What exact question or assertion is being made?** "Last Git author" is not "code owner"; "a search returned no hits" is not "no code exists."
2. **Who owns that semantic fact and at which revision/scope?** Do not add Oh-Goon source parsing when Hashmarks owns repository meaning.
3. **What real production path accepts it as truth?** A misleading file name, duplicated hash, or fixture-only parser is not evidence of duplicate accepted authority.
4. **What counterfactual would disprove the concern?** Test alternate author, conflicting CODEOWNERS, stale index, cross-run scope, denied external egress, and old artifact readback.
5. **Does the proposed fix remove a real defect with the smallest owner-aligned change?** Preserve native Git, source custody, existing Game Tape and the original artifact export path.

QA must classify claims as **PROVED**, **CONTRADICTED**, **UNKNOWN/INCOMPLETE**, or **OUT OF SCOPE**. Lower-thinking agents must not turn uncertain evidence into confident architecture findings; proof is a prerequisite to severity, refactoring and marketing.

## 13. Definition of done and publication rules

**P0 complete only when all are true:**

- [ ] One synthetic sample and exact setup/Run/inspect/export commands have been executed on an explicitly recorded OH-Goon commit and pinned Hashmarks implementation; unexecuted steps are labeled so.
- [ ] Search, languages and relations come from Hashmarks with source/freshness/coverage qualifiers; Git author is correctly distinct from approved catalog owner.
- [ ] Human-reviewed metadata produces a valid deterministic Backstage Component YAML; unknown/unreviewed fields or values do not silently pass.
- [ ] The generator executes in the **original** admitted OH-Goon lifecycle, uses the legitimate artifact retention owner and is inspectable/exportable from a separate CLI process.
- [ ] The selected real source stays unchanged; target-existing files are not overwritten; no automatic commit, push, provider call or Backstage registration occurs.
- [ ] Scoped security tests cover browser state, trace/log/screenshot content, out-of-scope paths, stale source, cross-run isolation, crash/cancel and artifact export refusal.
- [ ] Separate no-provider and no-external-egress qualifications record independent evidence. If the latter cannot be run, documentation explicitly says **not egress-qualified**.
- [ ] Relevant native tests and repository quality checks pass; their executed scope, source revision and omissions are reported truthfully. No new broad merge gate is created.
- [ ] A new engineer completes the demonstration and explains the four owners (Hashmarks, native Git, human catalog approval, OH-Goon) without relying on verbal corrections to the guide.
- [ ] README/example navigation and source-of-truth documentation are updated in the same implementation PR, without duplicating normative architecture or claiming a feature already shipped.
- [ ] Public content says "inspired by the Xirp + Portal context-first experience", not "equivalent to Xirp", and includes only measured performance/privacy assertions.

**Public message (after proof):**

> "Explore a private repository with local, source-backed evidence. Distinguish code authors from accountable owners. Review a Backstage catalog entry. Execute its generation as one bounded OH-Goon Run and inspect the original Game Tape—all without requiring an external AI provider."

If a stronger network claim is desired, add **exactly** the measured platform, boundary and qualification receipt; do not replace "without requiring an external provider" with the unqualified assertion "nothing can ever leave the machine."

## 14. Explicit non-goals and blocked assumptions

**Not in scope:** another indexing/search service; automatic ownership inference as fact; undocumented backward compatibility; a new source/workflow store; an agent to "decide" the catalog owner; remote catalog registration; automatic Git push; project-wide RBAC; a full Spotify Xirp/Portal clone; source-code uploads; multi-provider context sync; performance benchmark verdicts; global CI policy changes.

**Check before implementation:** pinned Hashmarks public evidence shapes and freshness flags; whether CODEOWNERS/language observations are sufficient; native Git availability/history state; exact OH-Goon Goon/Plan schema and executable permission for the Python renderer; artifact identity/export CLI; current source/approval binding. These are **audit obligations**, not permission to guess an API or invent a parallel owner.

**Decision default for unresolved details:** keep the demo local and synthetic, present unknown evidence honestly, require manual owner approval, refuse unsafe generation, and route real producer or lifecycle gaps to their canonical project owner. No uncertainty is resolved by adding a hidden fallback.

---

### Source and methodology index

**OH-Goon:** [root requirements and ownership](../../AGENTS.md) · [Hashmarks integration](../architecture/hashmarks-oh-goon-authority-boundary.md) · [privacy](../PRIVACY.md) · [Goon authoring](GOON_AUTHORING.md) · [Game Tape](GAME_TAPE.md) · [QA](REAL_LIFECYCLE_QA.md) · [example PR #170](https://github.com/oh-goon/oh-goon/pull/170).

**Hashmarks:** [getting started](https://github.com/Hugloss/Hashmarks/blob/main/docs/GETTING_STARTED.md) · [public API](https://github.com/Hugloss/Hashmarks/blob/main/docs/reference/API_STABILITY.md).

**Agents Cookbook:** [skills catalog](https://github.com/Hugloss/agentsCookbook/blob/main/skills/README.md) and the eight specific skill contracts in `12.

**External reference only:** [Spotify: Xirp + Portal](https://portal.spotify.com/blog/introducing-xirp) · [Backstage: descriptor format](https://backstage.io/docs/features/software-catalog/descriptor-format/).
