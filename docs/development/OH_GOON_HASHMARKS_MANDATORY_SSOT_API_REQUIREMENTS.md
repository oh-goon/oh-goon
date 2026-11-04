# OH-Goon ↔ Hashmarks Mandatory Repository-Intelligence SSOT API Requirements

**Status:** Requirements — drop-in replacement

**Scope:** OH-Goon and Hashmarks repository-intelligence integration, mandatory dependency ownership, public SSOT API, anti-drift architecture guards, and migration closure

**Normative stance:** Hashmarks is mandatory for OH-Goon repository intelligence. `oh_goon.integrations.hashmarks` is the one enforced OH-Goon SSOT API for all Hashmarks-backed repository-intelligence capabilities.

**Reviewed baselines:**
- OH-Goon `main`: `6483cf4b2699ec852f540e88b8ccb3517d5b285c`
- Hashmarks `main`: `6cb937974a8641333ceab64991dda33276f07f97`
- AgentsCookbook `main`: `ea7574e5772ba9d55a4537ebd59443bcd9d56cfb`

## Document authority and execution seam

This is the canonical normative repository-intelligence specification. It was
imported for [HUG-182](https://linear.app/hugloss/issue/HUG-182) from
`OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md`. The reviewed baselines above and the findings in §22
describe that review, not current implementation or release qualification.
Original source SHA-256: `b35eebd8f4a7366c22a62fe098c716618d7e17128137de2c8d0bed49d5c77cdc`.

The separate [canonical execution-lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
own Run admission, worker custody, drain, cancellation, recovery, containment and
terminalization. The execution-responsibility census in §12 does not authorize
re-resolving a frozen Run: execution consumes its already-admitted facts.
Repository/content observation identity and execution-custody/materialization
identity prove different scoped facts (§3.5 and §8.10).

**Hashmarks is mandatory. Missing producer precision fails closed or causes
conservative OH-Goon execution policy; it never authorizes a second repository-
intelligence implementation.** A bounded execution-policy fallback allowed by
§11.1 does not supply missing repository truth or turn unknown into complete.
The earlier lifecycle REV2's temporary-heuristic language is superseded by this
stronger rule (§26); no migration registry grants production semantic authority.

This document specifies required behavior. Its presence does not certify that
the migration is complete. Current implementation owners and proof remain in
[the Hashmarks execution handoff](HASHMARKS_EXECUTION_LAYER.md), the public
[`oh_goon.integrations.hashmarks` API](../../backend/src/oh_goon/integrations/hashmarks/__init__.py),
the [boundary guard](../../backend/src/oh_goon/repository_tooling/architecture/hashmarks_harness_boundary_contract.py),
its [negative regressions](../../scripts/tests/test_hashmarks_harness_boundary_contract.py),
and [public API conformance](../../backend/tests/unit/integrations/test_hashmarks_public_api.py).
Import protection is not proof of the complete semantic migration.

The review questions in §20 and §23 use existing review/admission owners; they do
not create another command, approval, registry or merge prerequisite.

## 1. Purpose

This document defines the required end state for the OH-Goon ↔ Hashmarks boundary.

The goal is not merely to move code. The goal is to establish one durable semantic owner for repository intelligence, one explicit composition boundary where Hashmarks evidence enters OH-Goon, one reviewable path from repository observation to OH-Goon admission and execution, and structural/behavioral guards that make a second repository-intelligence implementation difficult to create accidentally.

**Hashmarks is mandatory.** OH-Goon SHALL NOT provide an alternate repository-intelligence implementation, optional non-Hashmarks mode, compatibility fallback, emergency heuristic, or degraded local substitute for repository facts that belong to Hashmarks. Hashmarks SHALL be an ordinary required runtime dependency for repository-intelligence-capable OH-Goon installations and SHALL be present through the normal `pyproject.toml` + `uv.lock` dependency authority. If an operation requires repository intelligence and the pinned Hashmarks runtime is unavailable, incompatible, stale, or cannot prove the required fact, that operation SHALL fail closed rather than switching semantic owners.

**`oh_goon.integrations.hashmarks` is the mandatory OH-Goon SSOT API.** It is not merely the directory where Hashmarks happens to be imported. It is the only public OH-Goon composition API through which production code may obtain, validate, bind, translate, or expose Hashmarks-backed repository intelligence. Its public surface is the executable census of the cross-repository relationship.

The required architecture is:

```text
repository source
      │
      ▼
   Hashmarks
canonical repository-intelligence owner
      │
      │ producer-qualified evidence
      ▼
oh_goon.integrations.hashmarks
sole OH-Goon composition/admission boundary
      │
      │ OH-Goon-native boundary objects
      ▼
OH-Goon policy / admission / execution / certification
      │
      ▼
Game Tape / execution evidence / runtime observability
```

For one repository fact in one scope and generation, Hashmarks must establish the authoritative repository-intelligence truth. OH-Goon may validate, bind, transport, project, admit, or reject that truth, but must not independently establish an alternative repository truth.

This follows the AgentsCookbook principles applied in this review:

- **single-source-of-truth-review** — one semantic fact, scope, and generation has one authoritative owner;
- **canonical-consumer-contract-review** — OH-Goon must depend on the declared Hashmarks result contract, not historical packet shapes or private conventions;
- **dependency-surface-review** — OH-Goon should receive focused producer results, not inspect broad Hashmarks internals unnecessarily;
- **native-tool-authority-review** — OH-Goon must not reimplement semantics already owned by Hashmarks;
- **evidence-projection-preservation-review** — admitted producer facts must survive translation or have explicit omission/exclusion accounting;
- **plan-gap-scout** and **red-team-leftover-gate** — migration is incomplete while old imports, shadow derivations, stale fixtures, or alternate paths remain;
- **requirements-lifecycle-closure-review** — effectful execution, teardown, cancellation, recovery, and result authority remain explicitly owned by OH-Goon.

---

## 2. Problem Statement

OH-Goon already has an explicit Hashmarks integration package:

`backend/src/oh_goon/integrations/hashmarks/`

This is the correct architectural neighborhood. However, parts of that package and nearby OH-Goon development-verification code currently do more than adapt Hashmarks evidence.

Some paths:

- compose several low-level Hashmarks repository APIs;
- interpret Hashmarks ownership/relationship classifications;
- reconstruct verification relevance;
- interpret dependency graph internals;
- translate historical Hashmarks command advice such as `argv`;
- create OH-Goon-local repository semantic states from Hashmarks primitives.

This creates two risks.

### 2.1 Shadow repository intelligence

OH-Goon can gradually become a second repository-intelligence implementation even though Hashmarks is the intended canonical owner.

### 2.2 Consumer coupling to accidental Hashmarks shape

OH-Goon can become coupled to fields, enum values, internal packet organization, command advice, or low-level graph layout that are not the stable producer contract.

A legitimate Hashmarks improvement can then break OH-Goon even though the repository fact became more precise.

### 2.3 Harder future architecture review

Today a reviewer must inspect multiple files and call chains to answer:

- where Hashmarks is imported;
- where repository facts are interpreted;
- where OH-Goon adds its own policy;
- whether a result is producer truth or consumer policy;
- whether an apparent adapter is actually reconstructing semantics.

The target architecture must make this answer visible from one canonical integration surface.

### 2.4 Directory-boundary illusion

A repository may appear architecturally consolidated while still exposing private integration modules directly to unrelated consumers. An `integrations/hashmarks/` directory is not an SSOT by itself. If consumers import `integrations.hashmarks.dependencies`, `integrations.hashmarks.development_verification`, `integrations.hashmarks.identity`, or other implementation modules directly, the cross-repository capability graph remains distributed and hard to review.

The target state therefore requires an actual public composition API, not only an import neighborhood.

### 2.5 Semantic duplication without direct Hashmarks imports

Direct-import guards alone are insufficient. OH-Goon could create a second repository-intelligence implementation without importing Hashmarks at all—for example by independently inferring related tests, dependency consumers, diagnostic locality, ownership, freshness, negative evidence, source relationships, or repository completeness from filesystem/Git/test/runtime data.

The architecture must therefore protect **semantic ownership**, not merely Python dependency direction. Static guards SHALL be combined with behavioral authority tests and repository-wide review of ambiguous semantic neighborhoods.

### 2.6 Hidden requalification paths

A producer fact can be duplicated later even when initial acquisition is correct. Retained evidence may be reopened and reinterpreted during freshness checks, coverage calculations, persistence readback, Fleet aggregation, admission, or presentation. Requalification of a Hashmarks-owned repository fact remains Hashmarks-owned.

OH-Goon SHALL NOT preserve an initial producer observation correctly and then reconstruct its semantics later from raw producer internals.

---

## 3. Product Ownership Invariant

### 3.1 Hashmarks owns repository truth

Hashmarks is the canonical owner of repository-derived evidence and semantics, including:

- repository/content identity;
- source member identity and revision;
- repository freshness;
- repository admission and source visibility facts;
- symbols and declarations;
- repository relationships;
- dependency resolution evidence;
- dependency-consumer relationships;
- ownership evidence;
- impact/relevance evidence;
- verification relevance;
- verification nominations;
- exact verification membership;
- repository-negative evidence;
- diagnostic-to-source correlation;
- diagnostic changed-path locality;
- source provenance;
- repository evidence completeness;
- observation bounds and truncation;
- repository evidence presentation;
- producer evidence identity and provenance.

If OH-Goon requires one of these facts and Hashmarks cannot currently produce it with sufficient precision, that is a **Hashmarks product gap**.

OH-Goon must not repair such a gap by inventing a product-specific repository heuristic when the fact is reusable repository intelligence.

### 3.2 OH-Goon owns consumer and execution truth

OH-Goon remains the canonical owner of:

- whether available repository evidence is sufficient for an OH-Goon operation;
- merge admission;
- release admission;
- certification;
- Repository Understanding qualification;
- execution policy;
- static obligations that belong to OH-Goon policy;
- runtime/tool/environment selection owned by OH-Goon;
- process grouping;
- concurrency;
- scheduling;
- timeout;
- retries;
- cancellation;
- resume/recovery;
- isolation;
- capability grants;
- network/secret/git/publish authority;
- execution-result authority;
- verification-result authority;
- Step and Run lifecycle;
- Game Tape;
- execution provenance;
- execution observability;
- teardown and quiescence;
- runtime recovery.

Hashmarks evidence may inform these decisions, but Hashmarks must not obtain execution authority by supplying repository evidence.

### 3.3 Coding-agent ownership remains separate

The coding agent owns solution reasoning, editing strategy, hypotheses, and solution selection.

Neither Hashmarks nor OH-Goon repository evidence may decide which code change the agent should make.

### 3.4 Hashmarks is a mandatory dependency, not an optional provider

Hashmarks SHALL be the one repository-intelligence provider used by OH-Goon.

Requirements:

- Hashmarks SHALL be declared through OH-Goon's normal required dependency authority rather than an optional feature extra for repository intelligence.
- `uv.lock` SHALL pin the admitted producer implementation used by qualification and CI.
- repository-intelligence operations SHALL verify the loaded Hashmarks implementation identity where that identity is material to producer evidence.
- there SHALL be no environment variable, CLI flag, plugin selection, fallback implementation, test-only production bypass, or compatibility mode that changes the repository-intelligence semantic owner away from Hashmarks.
- tests may use fakes at explicit unit seams, but production authority SHALL remain Hashmarks and integration/qualification tests SHALL exercise the real pinned producer.
- missing or incompatible Hashmarks SHALL be an explicit dependency/runtime/admission failure, never a trigger for OH-Goon-local semantic inference.

This requirement does not grant Hashmarks execution authority. It makes Hashmarks mandatory **only for the repository-intelligence facts that Hashmarks owns**.

### 3.5 SSOT is scoped by semantic fact, scope and generation

Single source of truth does not mean that every hash, snapshot, observation, or provenance record in OH-Goon must disappear. Two identities may legitimately exist over the same underlying bytes when they prove different facts.

Examples:

- Hashmarks may own **repository/content observation identity**: what repository/source member was observed and at what producer generation.
- OH-Goon may own **execution-custody identity**: what exact bytes were frozen, admitted, transported, mounted, or executed by an OH-Goon lifecycle owner.

An identity is a duplicate only when it independently establishes the same semantic fact for the same scope and generation. Execution custody, source admission, sandbox materialization, process evidence, and Game Tape lineage SHALL NOT be deleted merely because Hashmarks also observed related source bytes.

---

## 4. Canonical OH-Goon Hashmarks Composition Root and Mandatory SSOT API

### 4.1 One package is the sole Hashmarks integration owner

`oh_goon.integrations.hashmarks` SHALL be the sole OH-Goon package permitted to import `hashmarks` or `hashmarks.*`.

No production module outside this package may import Hashmarks directly.

This package is not optional plumbing. It is OH-Goon's mandatory repository-intelligence composition root.

### 4.2 One public OH-Goon import surface

OH-Goon consumers SHALL import Hashmarks-backed capabilities only from the public package surface:

```python
from oh_goon.integrations.hashmarks import ...
```

Production consumers SHALL NOT import implementation submodules such as:

```python
from oh_goon.integrations.hashmarks.development_verification import ...
from oh_goon.integrations.hashmarks.dependencies import ...
from oh_goon.integrations.hashmarks.identity import ...
from oh_goon.integrations.hashmarks.repository_intelligence import ...
from oh_goon.integrations.hashmarks.runtime_contract import ...
from oh_goon.integrations.hashmarks.verification_selection import ...
from oh_goon.integrations.hashmarks.verification_selection_primitives import ...
from oh_goon.integrations.hashmarks.work_translation import ...
```

The same rule applies to any future implementation submodule.

### 4.3 The public package SHALL be a real API, not an empty package marker

An empty or effectively empty `oh_goon.integrations.hashmarks.__init__` does **not** satisfy this architecture.

The public package SHALL explicitly expose every production capability that other OH-Goon packages are permitted to consume. The implementation may use direct re-exports, narrow façade functions/types, or an internal bridge module re-exported from `__init__.py`, but the result must be inspectable from one package surface.

The public API SHOULD use explicit exports such as `__all__` or an equivalently reviewable mechanism. Wildcard discovery, implicit attribute forwarding, dynamic import tricks, or import-time scanning SHALL NOT define the authority surface.

### 4.4 Public API as executable capability census

The public `oh_goon.integrations.hashmarks` surface SHALL be the canonical executable census of the cross-repository relationship.

A reviewer inspecting that package SHALL be able to enumerate all supported classes of Hashmarks-backed capability, including as applicable:

- repository/content identity;
- repository-intelligence evidence binding;
- changed-source verification observation;
- verification selection/membership validation;
- dependency observation and requalification;
- diagnostic/source correlation evidence;
- producer presentation/disclosure helpers;
- producer runtime/implementation identity checks.

Do **not** create a second YAML/JSON/Markdown registry that independently lists the same public capabilities. Documentation may describe the API, but the public package surface plus its architecture tests are the executable SSOT.

### 4.5 Internal decomposition is allowed

The implementation may remain split into cohesive internal modules. A single source of truth does **not** require one giant file.

A representative structure may be:

```text
oh_goon/integrations/hashmarks/
    __init__.py                 # mandatory public OH-Goon-facing SSOT API
    _bridge.py                  # optional internal composition root
    _runtime.py
    _identity.py
    _repository.py
    _verification.py
    _dependencies.py
    _diagnostics.py
    _presentation.py
```

Exact filenames are not normative. Internal modules MAY remain non-underscored during migration, but they SHALL be treated as private to the package and SHALL NOT become alternate production entry points.

The normative requirements are:

1. one public package surface;
2. one dependency direction;
3. all direct Hashmarks imports remain inside the package;
4. all production consumers outside the package use only the public package surface;
5. internal modules do not become alternate public entry points;
6. repository semantics remain producer-owned;
7. no second Hashmarks client/adapter abstraction is introduced beside this package;
8. no dynamic bypass can acquire Hashmarks APIs outside the package.

### 4.6 No consumer-side producer contract knowledge

Code outside `oh_goon.integrations.hashmarks` SHALL NOT need to know:

- Hashmarks module paths;
- private Hashmarks classes;
- raw producer table/storage layout;
- historical producer schema aliases;
- producer-specific enum spellings unless intentionally retained in a boundary type;
- low-level graph row relationships;
- producer validation helper locations;
- producer runtime import choreography.

Consumer code should depend on narrow OH-Goon boundary types and capabilities exported by the public SSOT API.

### 4.7 Reviewability requirement

A reviewer must be able to answer all of the following from the public package surface and its tests:

1. Which Hashmarks-backed capabilities does OH-Goon consume?
2. Which OH-Goon boundary type does each capability produce?
3. Which identity/provenance is preserved?
4. Which producer facts are passed through without reinterpretation?
5. Which OH-Goon policy is applied after admission?
6. Which capabilities are repository intelligence versus execution custody/policy?

If answering those questions requires a repository-wide search merely to discover the integration API, the SSOT requirement is not satisfied.

### 4.8 Dependency/runtime availability is fail-closed

Because Hashmarks is mandatory for repository intelligence, the SSOT API SHALL fail explicitly when the required producer runtime is not available or does not match an admitted producer contract. It SHALL NOT silently return weaker OH-Goon-derived semantics.

---

## 5. Allowed Responsibilities of the OH-Goon Hashmarks Boundary

The boundary MAY:

- call supported public Hashmarks producer APIs;
- validate producer packets using Hashmarks validators;
- establish the loaded Hashmarks producer implementation identity;
- bind producer evidence to an admitted OH-Goon repository/source identity;
- reject stale producer evidence;
- reject source/repository/generation mismatches;
- preserve evidence identities;
- preserve producer qualifications;
- preserve completeness and negative-evidence state;
- preserve omission and truncation accounting;
- map producer data into narrow immutable OH-Goon boundary models;
- convert a producer-declared verification target into an OH-Goon structured execution member where that translation adds no repository semantics;
- apply OH-Goon disclosure/privacy rules at the consumer boundary;
- enforce consumer transport size limits;
- attach identity-only repository evidence metadata to Game Tape;
- classify producer-input failure separately from repository execution failure.
- expose a small stable OH-Goon public API that hides private Hashmarks module topology from consumers;
- request producer-owned requalification/freshness assessment of retained evidence without rederiving the fact locally;
- aggregate already-qualified repository facts across the OH-Goon Fleet when the aggregation itself is OH-Goon product policy/presentation.

---

## 6. Forbidden Responsibilities of the OH-Goon Hashmarks Boundary

The boundary MUST NOT independently:

- determine repository ownership;
- rank owners;
- infer verification relevance;
- infer that a test covers a changed source member;
- define repository relationship meaning;
- reinterpret `direct-reference`, `bounded-indirect-reference`, or similar producer classifications into a new repository truth;
- reconstruct dependency relationships from low-level Hashmarks graph tables when a producer projection can own that interpretation;
- infer dependency consumers;
- determine repository freshness from raw inputs;
- correlate diagnostics with source;
- infer changed-path locality;
- invent repository-negative evidence;
- infer repository completeness;
- infer source provenance;
- introduce an OH-Goon-specific impact graph;
- introduce an OH-Goon-specific repository selector;
- manufacture stronger owner coverage than Hashmarks proves;
- turn unknown/incomplete evidence into absence;
- turn repository evidence into execution authority;
- consume historical Hashmarks `argv`/working-directory advice as the canonical execution contract once a structured producer target exists;
- call Hashmarks execution/harness machinery;
- create fallback repository heuristics when Hashmarks cannot prove a reusable repository fact.
- expose production callers to private `oh_goon.integrations.hashmarks.*` implementation modules;
- maintain an optional non-Hashmarks repository-intelligence mode;
- requalify retained Hashmarks evidence by rejoining or reinterpreting raw producer internals;
- infer a Hashmarks-owned fact from Git, filesystem, test collection, runtime output, database rows, or cached projections merely because no direct `hashmarks` import is involved;
- create a second repository-intelligence client, bridge, registry, or adapter outside the canonical public SSOT API.

When such a fact is required, the required sequence is:

```text
OH-Goon exposes missing repository fact
        ↓
classify as reusable repository intelligence
        ↓
implement producer capability in Hashmarks
        ↓
qualify Hashmarks capability
        ↓
release Hashmarks
        ↓
adopt released dependency in OH-Goon
        ↓
delete OH-Goon shadow derivation
```

---

## 7. Hashmarks Product Requirements Exposed by OH-Goon

### 7.1 Changed-source verification observation

Hashmarks SHALL provide a producer-owned operation that can represent the repository-intelligence answer for a bounded changed source set without requiring OH-Goon to compose ownership graph semantics itself.

The exact API name is not prescribed.

Conceptually it must be capable of providing:

- repository identity;
- source identity;
- CodeMap generation;
- producer implementation identity;
- change-set identity;
- change-set completeness/qualification;
- changed member identities/revisions;
- verification relationships;
- relationship evidence and provenance;
- relationship completeness/bounds;
- verification targets/nominations;
- exact supporting source-member revisions;
- target qualification;
- target availability/unavailability reason;
- freshness;
- unresolved/unknown members;
- negative evidence;
- omissions/truncation;
- stable evidence identity.

It SHALL NOT provide execution authority.

It SHALL NOT require OH-Goon to interpret low-level ownership graph rows in order to decide what the producer is claiming.

### 7.2 Structured verification target, not command authority

Hashmarks SHALL expose verification as repository evidence through structured verification targets/nominations.

OH-Goon SHALL migrate away from using legacy Hashmarks verification `argv` or `working_directory` as the canonical producer-consumer contract.

Hashmarks may provide runner evidence where repository evidence legitimately supports it, but:

- runner evidence is not execution permission;
- physical process layout is external;
- process launch is external;
- timeout/retry/cancellation is external;
- test grouping is external.

### 7.3 Dependency observation projection

Hashmarks SHALL expose a stable consumer-facing dependency observation sufficient for OH-Goon without requiring the consumer to understand internal graph storage structure.

The producer-owned projection should preserve, where available:

- resolved component/package identity;
- version;
- source;
- dependency contexts;
- relationships;
- module/source consumers;
- repository inputs;
- source revisions;
- repository binding;
- freshness;
- completeness;
- negative-evidence qualification;
- observation identity;
- producer identity.

OH-Goon may map this projection into its own `DependencyObservation` model but SHALL NOT rederive Hashmarks dependency semantics from internal selections/components/inventory/relationship layout when a stable producer result can provide the required meaning.

The same ownership rule applies after persistence. When OH-Goon needs to determine whether retained dependency evidence is still current, complete, available, or sufficient to prove package absence, it SHALL call a producer-owned Hashmarks requalification/assessment operation over the retained producer contract. OH-Goon SHALL NOT reopen raw Hashmarks `selections`, `components`, `inventory`, `relationships`, or equivalent internals to reconstruct the current package set or absence semantics.

The stable producer result SHALL be sufficient for both initial mapping and later requalification. A correct acquisition followed by consumer-side semantic reconstruction during readback is still an SSOT violation.

### 7.4 Diagnostic observation remains Hashmarks-owned

Diagnostic repository semantics SHALL remain producer-owned, including:

- diagnostic source revisions;
- diagnostic repository correspondence;
- diagnostic deltas;
- changed-path locality;
- relationship annotations;
- qualification;
- provenance;
- causation limits.

OH-Goon may persist/admit/display these facts but must not reproduce the correlation algorithm.

### 7.5 Repository evidence presentation remains producer-owned

Hashmarks presentation APIs SHALL remain the canonical means of turning Hashmarks repository packets into producer-qualified compact/text/structured evidence.

OH-Goon SHALL NOT implement a second repository-evidence presentation semantics layer.

OH-Goon may:

- apply a transport budget;
- reject an over-budget producer presentation;
- redact/disclose according to OH-Goon policy;
- add consumer transport metadata.

A lossy OH-Goon projection may not silently become repository truth.

### 7.6 Agent repository evidence composition

The current OH-Goon `agent_tools` composition is lower priority than verification/dependency ownership, but it must obey the same rule.

OH-Goon may compose distinct user/tool operations such as task evidence, text search, or path discovery.

If OH-Goon must combine multiple low-level Hashmarks facts and interpret their meaning to create one repository semantic answer, the composition belongs upstream in Hashmarks.

---


## 7A. Semantic Classification of Similar-Looking OH-Goon Code

The migration SHALL distinguish repository intelligence from legitimate OH-Goon observability and policy. Similar names do not imply the same semantic owner.

### 7A.1 Repository intelligence — Hashmarks mandatory owner

These facts belong to Hashmarks when they are derived from repository/source structure or evidence:

- content/member identity and producer repository identity;
- source/member relationships;
- repository ownership relationships;
- impact/relevance relationships;
- verification relevance and nomination;
- dependency graph meaning and dependency-consumer relationships;
- repository diagnostic/source correlation and changed-path locality;
- repository freshness/completeness/negative-evidence qualification;
- repository-evidence presentation semantics.

### 7A.2 Consumer policy/admission — OH-Goon owner

OH-Goon may decide whether producer evidence is sufficient for merge, release, certification, Repository Understanding, security, execution, or other OH-Goon policies.

### 7A.3 Execution custody — OH-Goon owner

OH-Goon owns exact bytes frozen for execution, source transport, sandbox materialization, workspace binding, process inputs, and execution-custody identities. These may be related to Hashmarks source identity but prove a different fact.

### 7A.4 Execution observability — OH-Goon owner

The following remain OH-Goon-native and SHALL NOT move to Hashmarks merely because they are called evidence/observability:

- Fleet Run status/timeline observations;
- Game Tape lifecycle, timing, command, output, failure and runtime-authority evidence;
- stdout/stderr retention and live-output handling;
- process/resource observations;
- cancellation/retry/recovery/quiescence evidence;
- Scout projections over canonical OH-Goon execution facts;
- operational diagnostics emitted by OH-Goon lifecycle owners.

### 7A.5 Fleet aggregation — OH-Goon owner over producer-qualified facts

OH-Goon may aggregate already-qualified per-repository Hashmarks facts across admitted Fleet repositories—for example package/version counts or stale/missing evidence populations. Fleet aggregation SHALL NOT redefine the per-repository fact being aggregated.

### 7A.6 Repository discovery/scope — OH-Goon owner where operational

Filesystem traversal that discovers configured Git roots, applies Fleet inclusion/exclusion, or resolves operational target scope remains OH-Goon-owned when it does not establish reusable repository semantic relationships.

### 7A.7 Repository Understanding qualification — OH-Goon policy owner

OH-Goon's Repository Understanding exams, capability thresholds, architectural assertion grading, and evidence-authority policy remain OH-Goon-specific qualification policy. They may consume Hashmarks evidence but SHALL NOT become a second generic repository-intelligence implementation.

Any future module with ambiguous terms such as `repository`, `observation`, `evidence`, `diagnostic`, `graph`, `relationship`, `coverage`, `selection`, `source`, `impact`, or `ownership` SHALL be classified against these categories during review.

---

## 8. Required OH-Goon Migration

### 8.1 `integrations/hashmarks/development_verification.py`

This path SHALL stop reconstructing repository verification semantics from low-level ownership results.

In particular, OH-Goon SHALL NOT remain responsible for repository truth such as:

- whether a changed path has a linked verifier;
- which relationship classes establish the producer's verification relationship;
- whether an ownership graph row qualifies as verification relevance;
- producer-level completeness/freshness meaning.

It SHALL consume one Hashmarks producer result that already expresses those semantics.

### 8.2 `repository_tooling/development_verification.py`

This owner SHALL remain in OH-Goon for consumer policy.

It MAY continue to own:

- Git change capture where OH-Goon requires an exact candidate;
- OH-Goon static obligations;
- verification-policy protection requirements;
- merge eligibility;
- supported execution runner/environment;
- actual process execution;
- exact result accounting;
- fail-closed handling of insufficient producer evidence.

It MUST NOT independently reinterpret Hashmarks repository relationships.

The target split is:

```text
Hashmarks:
    "What repository verification evidence exists for this change?"

OH-Goon:
    "Is that evidence sufficient for this OH-Goon merge policy,
     and how will OH-Goon execute the admitted target?"
```

### 8.3 `verification_selection.py` and primitives

These files are not automatically deletion targets.

The current admission pattern is legitimate where it:

- validates the native Hashmarks consumer bundle;
- checks producer implementation identity;
- checks repository/source identity;
- rejects stale evidence;
- verifies `repository-intelligence-only`;
- verifies external execution layout;
- converts exact producer membership into structured OH-Goon execution members.

They SHALL remain thin consumer-boundary code.

Any repository-semantic derivation discovered there during implementation SHALL move upstream.

### 8.4 `work_translation.py`

The legacy path that consumes Hashmarks verification-plan `argv` as command intent SHALL be removed or redesigned.

The final implementation SHALL consume the current structured producer contract, such as a verification target/membership, and let OH-Goon's execution owners create the executable projection.

No compatibility shim SHALL preserve old command-bearing Hashmarks semantics merely to avoid updating internal OH-Goon callers unless a current public compatibility obligation is explicitly established.

### 8.5 `dependencies.py`

The OH-Goon dependency adapter SHALL be reduced toward:

```text
Hashmarks producer observation
    ↓
producer validation / source binding
    ↓
narrow OH-Goon DependencyObservation mapping
```

It SHALL NOT become a second dependency graph interpretation owner.

### 8.6 `repository_intelligence.py`

The existing pattern in this module is the desired direction:

- validate native producer evidence;
- bind producer identity;
- bind repository/source/generation;
- reject stale/mismatched evidence;
- retain canonical payload/producer projection;
- expose identity-only disclosure.

This module or its successor should remain a reference model for the adapter boundary.

### 8.7 `runtime_contract.py`

The loaded Hashmarks runtime identity check remains a legitimate OH-Goon trust-boundary responsibility.

It SHALL NOT grow repository semantic logic.

### 8.8 Public SSOT API migration

The migration SHALL make `oh_goon.integrations.hashmarks` an actual enforced public API.

At minimum:

1. define explicit public exports in `integrations/hashmarks/__init__.py`;
2. move every production consumer outside the package from private submodule imports to the public package API;
3. prevent future private submodule imports structurally;
4. ensure public exports are narrow capabilities/types rather than wholesale re-export of producer internals;
5. ensure the public API can be inspected to enumerate all Hashmarks-backed capabilities;
6. ensure no second façade/client is introduced elsewhere.

An empty `__init__.py` plus direct imports of implementation modules SHALL be treated as migration-incomplete.

### 8.9 Retained dependency evidence requalification

Any path that reopens retained dependency observations and reconstructs package identity, inventory membership, relationships, or admissible absence from raw producer internals SHALL migrate upstream.

The target is:

```text
retained Hashmarks dependency result
    ↓
Hashmarks producer requalification against current admitted source
    ↓
producer-qualified current/stale/unknown/completeness/absence result
    ↓
OH-Goon Fleet aggregation/admission
```

OH-Goon storage may retain the producer packet required for requalification, but the semantic assessment remains producer-owned.

### 8.10 Source snapshot/content identity audit

Source snapshot and content-snapshot paths SHALL be reviewed using the semantic-fact/scope/generation rule.

- Hashmarks remains the canonical repository/content identity producer where repository identity is required.
- OH-Goon may retain custody/materialization identities required to prove the exact bytes it froze, transported, mounted, or executed.
- those OH-Goon identities SHALL be documented as execution-custody facts, not alternative repository truth.
- duplicate hashing/identity code that establishes the same repository fact as Hashmarks SHALL be removed; custody checks over OH-Goon-owned materialized bytes may remain.

---

## 9. Public Boundary Model Requirements

OH-Goon-facing boundary types SHALL expose only facts required by OH-Goon policy/execution.

They SHOULD prefer producer-neutral names where the downstream behavior does not require Hashmarks-specific representation.

Boundary types SHALL preserve enough identity to prove:

- producer implementation;
- producer evidence identity;
- repository identity;
- source identity;
- relevant generation;
- exact selected membership/target identity where applicable.
- the semantic class of the boundary fact where ambiguity with execution custody/policy would otherwise exist.

Boundary types MUST NOT silently:

- collapse unknown to false;
- collapse incomplete to empty;
- strip producer qualification that changes meaning;
- replace producer identity with an OH-Goon-generated identity;
- discard omission/truncation state when the downstream consumer could otherwise misread absence.

---

## 10. Structural Architecture Guards

### 10.1 Direct Hashmarks import guard

Architecture checks SHALL fail when any production module outside `oh_goon.integrations.hashmarks` imports:

```text
hashmarks
hashmarks.*
```

### 10.2 Public package import guard

Architecture checks SHALL fail when a production module outside the integration package imports implementation submodules instead of the public package API.

Allowed:

```python
from oh_goon.integrations.hashmarks import observe_changed_verification
```

Rejected:

```python
from oh_goon.integrations.hashmarks.development_verification import ...
```

Test fixtures may use explicit test-only imports where necessary to test the package itself, but such imports must not become production authority.

### 10.3 No producer-execution import

The Hashmarks integration package SHALL continue to reject Hashmarks execution/harness machinery.

Hashmarks remains repository intelligence only.

### 10.4 No shadow semantic owners

Architecture/conformance tests SHALL protect against OH-Goon introducing alternative owners for:

- repository freshness;
- ownership;
- verification relevance;
- dependency-consumer relationships;
- diagnostic locality;
- repository evidence identity;
- repository evidence presentation.

Static checks may enforce dependency direction, but behavioral tests are required where semantics cannot be proven statically.

### 10.5 Canonical API census

The public `oh_goon.integrations.hashmarks` API SHALL be covered by a test or generated/static census that makes the supported cross-repository operations inspectable.

The guard must not freeze private helper choreography.

### 10.6 Mandatory public API guard

Architecture checks SHALL fail if `oh_goon.integrations.hashmarks.__init__` does not expose a non-empty explicit public capability surface required by production callers.

The guard SHALL verify that public production consumers can import the supported capabilities from:

```python
from oh_goon.integrations.hashmarks import ...
```

### 10.7 Private integration-submodule import guard

Architecture checks SHALL scan all production Python modules outside `oh_goon.integrations.hashmarks` and fail on imports of:

```text
oh_goon.integrations.hashmarks.<anything>
```

Imports internal to the package are allowed. Explicit tests of private implementation modules may be allowed only in integration-package tests; they must not establish production authority.

### 10.8 No optional/fallback repository-intelligence owner

Architecture and dependency checks SHALL prove that production repository-intelligence capability does not select between Hashmarks and another implementation.

The checks SHALL reject:

- optional provider registries for repository intelligence;
- feature flags selecting a local semantic fallback;
- `try/except ImportError` paths that reconstruct required repository facts locally;
- dynamic plugin/provider discovery for the facts owned by Hashmarks;
- test fakes wired into production configuration as alternate authority.

### 10.9 Behavioral anti-shadow authority tests

Not all semantic duplication can be detected statically. Behavioral tests SHALL therefore construct cases where raw/ambient information could tempt OH-Goon to infer a stronger or different repository fact than Hashmarks proves.

Required patterns include:

- Hashmarks says verification relationship `unknown`/unavailable while low-level rows resemble a direct reference → OH-Goon preserves unknown/unavailable;
- Hashmarks says dependency coverage is incomplete while raw inventory contains a matching package → OH-Goon does not promote the result to complete/absence-admissible;
- Hashmarks says diagnostic locality is unproven while runtime/file paths appear to match → OH-Goon does not create repository correlation;
- Hashmarks evidence is stale while Git/filesystem data appears unchanged by a consumer-local heuristic → OH-Goon does not relabel it current;
- producer omits/truncates material evidence → OH-Goon does not infer absence from the projection.

These tests SHALL validate authority behavior, not private helper names.

### 10.10 Ambiguous-neighborhood semantic sweep

Architecture review and migration tests SHALL explicitly inspect modules whose names or responsibilities can hide repository semantics, including neighborhoods such as:

- `repository_tooling/`;
- `services/`;
- `runtime/`;
- `scout/`;
- `core/projections/`;
- `integrations/` outside the Hashmarks package;
- tests/fixtures/factories that synthesize repository relationships.

The purpose is not to forbid these packages from observing OH-Goon execution. The purpose is to ensure they do not independently establish Hashmarks-owned repository facts.

### 10.11 No source-text-only SSOT proof

A guard that merely searches for known function names is insufficient as the sole protection. Static source checks MAY catch known anti-patterns, but completion requires behavioral tests that prove producer authority survives translation and readback.

---

## 11. Failure and Unknown-State Requirements

### 11.1 Fail closed on missing producer precision

If Hashmarks cannot establish repository evidence required by an OH-Goon policy, OH-Goon SHALL return an explicit incomplete/unverified/admission failure.

It SHALL NOT:

- silently broaden selection;
- silently run every test as semantic compensation unless an explicit OH-Goon bounded fallback policy already owns that behavior;
- invent a local relationship;
- treat missing evidence as no relationship;
- downgrade required evidence to advisory.

### 11.2 Producer failure is not execution failure

Malformed, stale, mismatched, unsupported, or incomplete Hashmarks evidence SHALL be classified as an input/evidence/admission problem rather than a repository test failure.

### 11.3 Source changes during admission

If repository/source identity changes between producer observation and OH-Goon admission, the evidence SHALL be rejected or reacquired through the canonical producer path.

OH-Goon SHALL NOT rebind stale evidence to the new source.

### 11.5 Missing Hashmarks runtime or capability

If the pinned mandatory Hashmarks runtime is unavailable, incompatible, or lacks a repository-intelligence capability required by the current operation, OH-Goon SHALL fail with an explicit dependency/producer-capability/admission error. It SHALL NOT switch to a local semantic implementation.

### 11.4 Unsupported future producer variants

Unknown authority-bearing producer schema variants SHALL fail closed.

A new Hashmarks producer result may not be treated as an old result merely because fields happen to overlap.

---

## 12. Execution Lifecycle Requirements

The repository-intelligence consolidation SHALL NOT change ownership of effectful lifecycle behavior.

After evidence admission, OH-Goon remains responsible for:

1. resolving its execution plan;
2. validating runtime authority;
3. binding workspace/environment;
4. launching the process;
5. capturing stdout/stderr and result evidence;
6. cancellation;
7. timeout;
8. retry/resume;
9. process-tree quiescence;
10. teardown;
11. recovery;
12. result accounting;
13. Game Tape publication.

Hashmarks producer evidence must not acquire these responsibilities indirectly through a richer repository observation API.

A producer observation may say:

```text
verification target = tests/test_x.py::test_y
```

It may not decide:

```text
workers = 8
timeout = 300s
retry = 2
network = allowed
secret = X
merge = approved
release = approved
```

---

## 13. Evidence Preservation Requirements

For every migrated producer packet, tests SHALL trace at least one admitted fact through:

```text
Hashmarks producer
→ OH-Goon Hashmarks boundary
→ OH-Goon policy/admission
→ execution or presentation consumer
```

The test must prove that:

- identity survives;
- freshness survives;
- completeness survives;
- uncertainty survives;
- negative evidence survives;
- omissions/truncation survive when material;
- repository evidence does not become execution authority.

If the OH-Goon boundary intentionally excludes a producer field, the exclusion must be either:

- irrelevant to the downstream contract; or
- explicitly accounted for.

No consumer may infer repository absence merely because a projection omitted a fact.

---

## 14. Required Removal and Cleanup

The migration is incomplete until all applicable leftovers are removed.

The implementation review SHALL search for and disposition:

- direct `hashmarks` imports outside the canonical package;
- production imports of Hashmarks integration submodules;
- old verification-plan `argv` consumption;
- old `working_directory` command advice consumption;
- duplicate verification relationship interpretation;
- duplicate repository freshness interpretation;
- dependency graph reconstruction that is superseded by producer projection;
- stale compatibility fixtures tied to old producer contracts;
- old schemas retained solely for internal convenience;
- product-specific special-case repository mappings added before upstream Hashmarks precision existed;
- obsolete comments/docs describing OH-Goon as repository-semantic owner;
- architecture exceptions permitting old paths;
- tests that assert accidental packet shape rather than the current producer contract;
- dead adapter wrappers left after caller migration.
- all production imports of private `oh_goon.integrations.hashmarks.*` modules;
- empty/public-package-marker-only SSOT surfaces;
- optional provider/fallback paths for repository intelligence;
- retained-evidence requalification that reconstructs Hashmarks semantics from raw producer tables;
- duplicate semantic-owner tests/fixtures that teach OH-Goon to infer repository truth independently;
- comments/docs that describe private integration modules as supported consumer entry points;
- duplicate capability registries that can drift from the public SSOT API.

A wrapper around two surviving semantic paths does not satisfy consolidation.

---

## 15. Documentation Requirements

### 15.1 OH-Goon

Update the canonical OH-Goon ownership documentation so it states:

> `oh_goon.integrations.hashmarks` is the sole OH-Goon composition boundary for Hashmarks. Hashmarks owns repository evidence semantics. OH-Goon owns evidence admission, execution, certification and runtime observability.

The documentation SHALL name the public package entry point and prohibit direct producer imports elsewhere.

It SHALL also state that Hashmarks is a mandatory repository-intelligence dependency; private `oh_goon.integrations.hashmarks.*` modules are implementation details; missing producer precision fails closed; and OH-Goon execution observability/custody remain distinct semantic owners rather than alternate repository-intelligence implementations.

### 15.2 Hashmarks

Update the Hashmarks semantic owner map if new producer operations are added.

The owner map SHALL make clear that the changed-source verification observation and dependency-consumer observation are repository-intelligence operations with external execution authority.

### 15.3 No duplicate normative owner maps

Documentation may explain the boundary in both repositories, but only the appropriate product's owner map may define its own internal semantic ownership.

Do not create a third standalone ownership registry that can drift from both codebases.

---

## 16. Migration Order

The implementation SHALL follow this dependency order.

### Phase A — Freeze the current boundary

1. Add/strengthen direct-Hashmarks-import and private-integration-submodule import guards.
2. Make `oh_goon.integrations.hashmarks` a real explicit public API and add its executable capability census test.
3. Add focused tests demonstrating current producer/consumer identity binding.
4. Record all current semantic derivations in the Hashmarks integration neighborhood.
5. Record all production callers that currently import private Hashmarks integration modules.
6. Sweep ambiguous non-Hashmarks neighborhoods for repository-semantic derivation, including services, runtime, repository tooling, Scout/projections, fixtures, and retained-evidence readback.
7. Add behavioral anti-shadow authority tests before migration so consumer-local inference cannot silently replace producer truth.
8. Prove the Hashmarks dependency is mandatory and no alternate production repository-intelligence provider/fallback exists.

**Exit condition:** no new boundary drift can be introduced unnoticed.

### Phase B — Add missing Hashmarks producer capabilities

Implement missing reusable repository-intelligence operations upstream first, beginning with:

1. changed-source verification observation;
2. dependency-consumer/stable dependency projection where current OH-Goon requires internal interpretation;
3. producer-owned dependency requalification/freshness/absence assessment over retained observations;
4. any remaining producer-level observation proved necessary by the OH-Goon migration.

Do not add OH-Goon-specific names or exceptions to Hashmarks.

**Exit condition:** Hashmarks can answer the required repository questions through supported public producer APIs with explicit uncertainty, provenance, freshness, and bounds.

### Phase C — Qualify and release Hashmarks

1. Run Hashmarks focused producer tests.
2. Run relevant public API/transport conformance.
3. Run architecture/semantic-owner checks.
4. Produce the normal released Hashmarks artifact.
5. Verify released producer identity and package readback.

OH-Goon SHALL adopt the released dependency through its normal `pyproject.toml` + `uv.lock` authority.

Do not consume an unreleased local Hashmarks implementation as the final OH-Goon production contract.

### Phase D — Shrink OH-Goon adapters

1. Migrate `development_verification` to the new producer observation.
2. Migrate legacy verification command translation away from producer `argv`.
3. Reduce dependency translation to stable producer projection.
4. Move dependency requalification/absence semantics to the producer-owned Hashmarks capability.
5. Migrate **all** production consumers to the public `oh_goon.integrations.hashmarks` surface.
6. Keep private integration modules private to the package and remove consumer-side producer-contract knowledge.
7. Preserve OH-Goon execution policy, execution custody, Fleet aggregation and result authority.

### Phase E — Delete leftovers

Delete superseded:

- repository semantic derivations;
- compatibility paths without a real obligation;
- direct submodule imports;
- stale fixtures;
- stale docs;
- architecture exceptions;
- duplicate tests.
- private integration-submodule production imports;
- optional/fallback repository-intelligence providers;
- consumer-side retained-evidence semantic reconstruction;
- duplicate public capability registries.

### Phase F — Cross-repository dogfood

Run the real OH-Goon flows that originally exposed the gaps, including representative cases for:

- ordinary changed Python source;
- changed test;
- nested Python project root after CodeMap reopen;
- repository document read by a structural verifier;
- dependency declaration/lock change;
- dependency consumer relationship;
- incomplete/unknown producer evidence;
- stale producer evidence;
- source mutation after observation;
- exact selected verification membership;
- execution result accounting.
- Hashmarks runtime unavailable/incompatible → explicit fail-closed dependency/admission result;
- private SSOT submodule import fixture → architecture failure;
- contradictory ambient/raw facts cannot override producer unknown/incomplete/stale state;
- retained dependency observation requalification remains producer-owned;
- execution-custody identity remains distinct from repository-intelligence identity.

**Exit condition:** OH-Goon consumes producer evidence without reconstructing repository semantics and retains exact execution ownership.

---

## 17. Acceptance Criteria

The work is complete only when all applicable criteria below are proven.

### AC-01 — One Hashmarks import owner

Repository-wide inspection proves that production `hashmarks` imports exist only under:

`backend/src/oh_goon/integrations/hashmarks/`

### AC-02 — One OH-Goon public integration surface

Production consumers import only the public `oh_goon.integrations.hashmarks` interface.

### AC-03 — No changed-source shadow verifier semantics

OH-Goon no longer independently determines Hashmarks repository verification relationships from ownership graph primitives.

### AC-04 — Producer-owned verification observation

Hashmarks provides one supported public producer result sufficient for the changed-source verification use case.

### AC-05 — Command-free producer contract

OH-Goon's current verification execution path does not rely on legacy Hashmarks `argv`/`working_directory` as canonical authority.

### AC-06 — Execution remains OH-Goon-owned

Tests prove that producer evidence cannot grant network, secret, publish, timeout, retry, process, or merge authority.

### AC-07 — Dependency semantics are producer-owned

OH-Goon consumes a stable producer dependency result and does not maintain a second interpretation of Hashmarks dependency graph semantics.

### AC-08 — Unknown remains unknown

Unavailable, incomplete, ambiguous, stale, unsupported, and missing evidence remain distinct from negative/absent evidence.

### AC-09 — Evidence identities survive

Producer, repository, source, generation, membership/target, and evidence identities remain bound through admission.

### AC-10 — Projection does not erase material evidence

Every material admitted producer fact is preserved or explicitly excluded/accounted for.

### AC-11 — No alternate repository fallback

A failed/missing Hashmarks repository fact does not cause OH-Goon to invoke a second repository-intelligence implementation.

### AC-12 — Real gap cases pass

The scenarios that exposed the current product gaps are validated through released Hashmarks plus the canonical OH-Goon adapter.

### AC-13 — Structural guards catch regressions

Negative architecture tests prove that:

- a direct Hashmarks import outside the package fails;
- a private integration submodule import fails;
- an attempted Hashmarks execution/harness import fails;
- an attempted shadow repository-semantic owner is rejected where statically enforceable.

### AC-14 — Old paths are deleted

Repository search plus focused tests prove old command-bearing and shadow-semantic paths are absent rather than merely unused.

### AC-15 — Documentation matches code

OH-Goon and Hashmarks current owner documentation describes the implemented boundary and points to the actual canonical owners.

### AC-16 — Hashmarks is mandatory

OH-Goon dependency/runtime configuration proves that repository intelligence has one production provider: the pinned Hashmarks runtime. No optional fallback or alternate semantic provider exists.

### AC-17 — Public SSOT API is real and non-empty

`oh_goon.integrations.hashmarks` exposes an explicit production API sufficient for every external OH-Goon consumer of Hashmarks-backed repository intelligence.

### AC-18 — No production private-submodule consumers

Repository-wide inspection and negative architecture tests prove that production code outside the package imports only `oh_goon.integrations.hashmarks`, never `oh_goon.integrations.hashmarks.*`.

### AC-19 — Public API is the executable capability census

A focused test can enumerate the supported public Hashmarks-backed capabilities from the package surface without consulting a second independently maintained registry.

### AC-20 — Dependency requalification remains producer-owned

OH-Goon does not rejoin raw producer selections/components/inventory/relationships to establish freshness, completeness, package membership, consumers, or admissible absence after persistence.

### AC-21 — Behavioral producer authority survives tempting ambient facts

Tests prove that producer unknown/incomplete/stale/unavailable results cannot be strengthened by consumer-local Git, filesystem, database, runtime, test-collection, or raw-packet heuristics.

### AC-22 — Legitimate OH-Goon observability remains separate

Execution observability, Game Tape, runtime diagnostics, execution custody and Fleet aggregation continue to use OH-Goon-native owners without becoming repository-intelligence fallbacks.

### AC-23 — Identity scopes remain explicit

Tests/documentation distinguish Hashmarks repository/content observation identity from OH-Goon execution-custody/materialization identity when both apply to related source bytes.

### AC-24 — No second façade/client/registry

There is exactly one OH-Goon public composition API for Hashmarks-backed repository intelligence; no second bridge/client/provider registry exists elsewhere.

### AC-25 — Semantic sweep is clean

A repository-wide review of ambiguous neighborhoods and representative behavioral tests finds no remaining independent OH-Goon implementation of Hashmarks-owned repository facts.

---

## 18. Required Test Matrix

At minimum, the final implementation SHALL include deterministic tests covering:

| Case | Required result |
| --- | --- |
| current producer, exact repository/source | admitted |
| different loaded producer identity | rejected |
| stale producer evidence | rejected |
| repository identity mismatch | rejected |
| source identity mismatch | rejected |
| generation mismatch | rejected |
| unknown producer schema | rejected |
| qualified changed-source verifier | producer relationship preserved |
| no qualified verifier | explicit unresolved/unverified |
| ambiguous relationship | ambiguity preserved |
| incomplete producer coverage | incompleteness preserved |
| changed test | exact producer target retained |
| source mutates after observation | old evidence rejected/reacquired |
| nested Python root after reopen | producer relationship retained when Hashmarks qualifies it |
| literal repository-file reader | producer structural relationship retained |
| dependency lock/declaration change | producer dependency observation consumed |
| dependency consumer unknown | unknown preserved |
| diagnostic locality | producer result transported without rederivation |
| producer target plus hostile authority fields | execution authority not granted |
| exact verification membership | no omitted/duplicate/extra executed member |
| direct Hashmarks import outside bridge fixture | architecture test fails |
| private Hashmarks adapter import fixture | architecture test fails |
| empty/no public Hashmarks SSOT API fixture | architecture/census test fails |
| private `oh_goon.integrations.hashmarks.*` production import fixture | architecture test fails |
| alternate repository-intelligence provider/fallback fixture | architecture/dependency test fails |
| producer verification state unknown but raw relationship looks direct | unknown preserved |
| producer dependency evidence incomplete but raw package appears present | incompleteness preserved; no stronger absence/coverage claim |
| retained dependency observation after source change | Hashmarks producer requalification decides current/stale/unknown |
| producer diagnostic locality unproven but paths appear similar | no OH-Goon repository correlation invented |
| Hashmarks runtime unavailable/incompatible | explicit dependency/producer capability failure; no local fallback |
| execution custody hash equals/relates to repository source hash | semantic identities remain separately typed/owned |
| Scout/Game Tape execution observation | remains OH-Goon execution observability, not repository-intelligence fallback |

---

## 19. Non-Goals

This work SHALL NOT:

- move OH-Goon execution observability into Hashmarks;
- move Game Tape into Hashmarks;
- move runtime retries/cancellation/recovery into Hashmarks;
- move certification/merge/release policy into Hashmarks;
- add a new generic plugin framework;
- add another repository graph in OH-Goon;
- add a second Hashmarks client abstraction solely for indirection;
- introduce broad backwards-compatibility wrappers for obsolete internal contracts without an established public obligation;
- change coding-agent solution ownership;
- add a new scheduler;
- make Hashmarks execute tests;
- make Hashmarks certify OH-Goon results;
- require a full repository rewrite.
- move OH-Goon execution-custody/materialization identity into Hashmarks merely because it hashes related source bytes;
- eliminate Fleet aggregation over already-qualified producer facts;
- make a second generic provider/plugin abstraction for repository intelligence;
- treat an empty integration package as sufficient SSOT architecture.

---

## 20. Review Checklist

Every future change in this neighborhood should answer these questions.

### Repository fact

1. What exact semantic fact is being established?
2. Is it repository truth or OH-Goon execution/policy truth?
3. What scope and generation does it apply to?
4. Who is the one authoritative owner?

### Hashmarks boundary

5. Does Hashmarks already expose a typed public result?
6. Is OH-Goon consuming that result or reconstructing its meaning?
7. Does the adapter preserve freshness, completeness, uncertainty, provenance, and identity?
8. Is the adapter reading a historical/private field rather than a declared producer contract?

### Execution boundary

9. Is Hashmarks evidence accidentally granting execution authority?
10. Does OH-Goon still own process launch, grouping, timeout, cancellation, retry, result accounting, and certification?

### Leftovers

11. Is there another direct Hashmarks import?
12. Is there another consumer importing a private adapter module?
13. Does an old semantic path still exist behind a fallback?
14. Do tests/fixtures/docs still teach the old contract?
15. Is the caller importing the public `oh_goon.integrations.hashmarks` API or a private submodule?
16. Could the same repository fact be established elsewhere without any Hashmarks import?
17. Is retained evidence being requalified by Hashmarks or reconstructed from producer internals?
18. Is a similarly named observation actually OH-Goon execution observability/custody/Fleet aggregation rather than repository intelligence?
19. Does any flag/provider/fallback permit repository intelligence without Hashmarks?
20. Can the public SSOT API enumerate this capability without adding a second registry?

If any reusable repository fact must be inferred in OH-Goon because Hashmarks lacks an adequate result, create/fix the Hashmarks capability first.

---

## 21. Definition of Done

This initiative is done when:

1. Hashmarks is the only repository-semantic owner for the defined facts.
2. Hashmarks is a mandatory OH-Goon dependency for repository intelligence, not an optional provider.
3. `oh_goon.integrations.hashmarks` is the only OH-Goon direct Hashmarks integration owner.
4. `oh_goon.integrations.hashmarks` exposes a real, explicit, non-empty public SSOT API.
5. That public API is the executable capability census of every Hashmarks-backed production capability OH-Goon consumes.
6. Other OH-Goon production code imports only that public package surface, never its implementation submodules.
7. The boundary validates, binds, translates and preserves evidence but does not reconstruct repository truth.
8. Missing reusable repository intelligence is implemented upstream in Hashmarks.
9. Changed-source verification relevance is producer-owned and no low-level ownership-graph interpretation remains in OH-Goon.
10. Dependency projection **and retained-evidence requalification** are producer-owned; OH-Goon does not rejoin raw producer graph/storage tables to recreate the fact.
11. Diagnostic/source repository correlation remains producer-owned while runtime/operational diagnostics remain OH-Goon-owned.
12. Legacy command-bearing producer consumption and superseded shadow semantics are deleted.
13. No optional/fallback/local repository-intelligence semantic owner exists.
14. Behavioral anti-shadow tests prove that ambient/raw consumer data cannot strengthen producer unknown/incomplete/stale/unavailable states.
15. Structural guards prevent direct Hashmarks imports, private integration-submodule imports, execution/harness imports and alternate provider paths.
16. OH-Goon execution observability, Game Tape, execution custody, Fleet aggregation and Repository Understanding policy remain correctly OH-Goon-owned and are not accidentally migrated upstream.
17. Repository/content observation identity and execution-custody/materialization identity are distinguished by semantic fact, scope and generation.
18. Cross-repository dogfood proves the released/pinned Hashmarks producer and OH-Goon consumer together.
19. No material old path, fallback, stale fixture, stale documentation, duplicate registry, private consumer import, or alternate semantic owner remains in the declared scope.
20. A future reviewer can inspect `oh_goon.integrations.hashmarks` and its tests and understand the complete Hashmarks/OH-Goon repository-intelligence relationship without performing an exploratory repository-wide search first.

The intended steady-state rule is:

> **Hashmarks is mandatory and resolves repository intelligence once → `oh_goon.integrations.hashmarks` exposes and admits that producer truth through one enforced public SSOT API → OH-Goon applies its policy once → OH-Goon executes once → OH-Goon reports execution evidence once.**

The prevention rule is:

> **No other OH-Goon path may independently establish, strengthen, requalify, or substitute a Hashmarks-owned repository fact—even if that path never imports Hashmarks.**

The scope rule is:

> **SSOT is defined by semantic fact + scope + generation. Hashmarks repository identity and OH-Goon execution-custody identity may coexist when they prove different facts.**

That is the single-source-of-truth boundary future reviews, architecture checks, tests, and migrations SHALL enforce.

---

## 22. Reviewed Baseline Findings That This Document Must Close

These findings are implementation evidence from the reviewed OH-Goon baseline and explain why the stronger requirements above are normative. They are not permission to freeze current filenames or helper choreography.

Every “current” or “currently” statement in this section describes the reviewed
`6483cf4b2699ec852f540e88b8ccb3517d5b285c` source. For example, the public API and
import guard have since been delivered; the original missing-API finding is
not a claim that today's package is empty. Its normative API, anti-shadow and
conformance obligations remain active, and current migration completion must be
established through the actual owners and HUG-179/HUG-183 evidence.

### 22.1 Changed-source verification currently reconstructs producer semantics

`backend/src/oh_goon/integrations/hashmarks/development_verification.py` currently consumes low-level verification ownership results and interprets coverage/edit-candidate relationships to decide linked versus unavailable verification state. This is a shadow repository-semantic owner and SHALL be replaced by one producer-owned Hashmarks changed-source verification observation.

### 22.2 Dependency adaptation currently reconstructs producer graph structures

`backend/src/oh_goon/integrations/hashmarks/dependencies.py` currently maps producer `selections`, `components`, `inventory`, and `relationships` into OH-Goon dependency observations. More importantly, retained evidence is later reopened and those internals are rejoined again to derive package membership/absence during assessment. The stable Hashmarks producer contract SHALL own both projection and requalification.

### 22.3 The intended public SSOT package is currently not an actual API

At the reviewed baseline, `backend/src/oh_goon/integrations/hashmarks/__init__.py` is empty while production callers import private implementation submodules directly. The repository therefore has a Hashmarks **directory boundary**, but not yet the required public SSOT **API boundary**. This document requires that gap to be closed.

### 22.4 Current architecture guards protect direct producer imports but not the complete SSOT rule

The existing Hashmarks harness boundary correctly rejects direct `hashmarks` imports outside the integration package and rejects Hashmarks execution/harness authority. It must be extended to reject private integration-submodule production imports and alternate semantic-provider/fallback paths.

### 22.5 Existing OH-Goon observability demonstrates the desired anti-duplication pattern

OH-Goon's canonical execution-observability architecture already protects a canonical run observation from being redefined by Scout, and diagnostic ownership checks keep diagnostic transport passive/source-owned. The Hashmarks boundary SHOULD adopt the same philosophy: one canonical semantic owner, read-only downstream projections, and architecture tests rejecting duplicate representations/owners.

### 22.6 Similar-looking OH-Goon owners that should remain

The reviewed baseline contains legitimate OH-Goon owners that SHALL remain distinct from Hashmarks repository intelligence, including:

- execution observability and execution timelines;
- Game Tape observability/evidence;
- runtime/operational diagnostics;
- Fleet repository discovery and operational scope;
- Fleet aggregation of producer-qualified dependency facts;
- execution source custody/materialization;
- Repository Understanding qualification/capability policy.

Future cleanups SHALL classify these by semantic fact rather than moving code solely because a filename contains `repository`, `evidence`, `observation`, `diagnostic`, `graph`, or `source`.

---

## 23. Future Change Gate

Every future OH-Goon change that establishes or consumes a repository-derived fact SHALL answer the following before merge:

1. What exact semantic fact is created or consumed?
2. Is that fact repository intelligence, OH-Goon policy, execution custody, execution observability, Fleet aggregation, or Repository Understanding qualification?
3. If it is repository intelligence, which public `oh_goon.integrations.hashmarks` capability supplies it?
4. If no public capability exists, what Hashmarks product gap must be implemented upstream first?
5. Does the change import only the public SSOT package?
6. Does any later persistence/readback/requalification path reconstruct the producer fact from raw internals?
7. Can contradictory ambient information cause OH-Goon to strengthen a producer unknown/incomplete/stale result?
8. Does the change create a second provider/fallback/registry/client?
9. Are identity, freshness, completeness, uncertainty, negative evidence, omissions and provenance preserved?
10. Does execution authority remain entirely OH-Goon-owned?

A change that cannot answer these questions cleanly SHALL NOT introduce a new repository-semantic path in OH-Goon. The missing producer capability SHALL be added to Hashmarks first.

---

## 24. Additional producer requirements retained from lifecycle REV2

These requirements retain the producer detail from the original lifecycle
document under this one normative owner. Section references identify source
provenance; historical “current” findings and PR #476 refer to the reviewed
2026-10-31 baseline, not a new release pin or current capability assertion.

### Hashmarks Requirement HM-CHANGE-001 — canonical changed-set verification intelligence (lifecycle REV2 §58)

Hashmarks SHALL expose a producer-owned changed-source verification view.

A consumer SHALL be able to supply a bounded admitted change set, for example:

```text
src/a.py
src/b.py
tests/helpers.py
frontend/src/x.ts
```

and receive one producer-qualified response describing verification relevance.

Hashmarks SHALL own determination of:

- changed source identity;
- verification owners;
- direct versus bounded-indirect relationship;
- changed verifier itself;
- shared support relationship;
- owner ambiguity;
- consumer-scope completeness;
- freshness;
- truncation;
- negative-evidence qualification;
- project/workspace association;
- verification nominations.

OH-Goon SHALL NOT reconstruct those decisions by composing lower-level Hashmarks responses.

### HM-CHANGE-002 — use existing Hashmarks semantic owners (lifecycle REV2 §59)

The new changed-set capability SHALL NOT become a second Hashmarks verification engine.

It SHALL compose existing owners such as:

- `RepositorySource`;
- `RepositoryTasks`;
- verification ownership;
- verification relevance;
- verification nomination;
- repository relationships;
- repository declarations;
- verification selection/membership;
- freshness.

Target:

```text
existing Hashmarks semantic owners
             ↓
one changed-set projection/facade
             ↓
consumer-ready evidence
```

Forbidden:

```text
existing verification engine
             +
downstream-consumer-specific verification engine
```

### HM-CHANGE-003 — no OH-Goon-specific semantics in Hashmarks (lifecycle REV2 §60)

Hashmarks SHALL NOT encode:

- `backend/tests/`;
- `frontend/`;
- OH-Goon Make targets;
- OH-Goon batch sizes;
- OH-Goon merge policy;
- OH-Goon execution commands.

Valid producer facts include:

- path;
- project/workspace identity;
- relationship type;
- verification target;
- runner evidence;
- scope;
- freshness;
- completeness;
- provenance.

Execution policy remains external.

### HM-CHANGE-004 — producer-owned project/workspace context (lifecycle REV2 §61)

Every verification nomination that depends on a nested project/workspace SHALL expose qualified context.

Conceptually:

```text
verification target:
    path: tests/test_x.py

project context:
    root: backend
    kind: python-project
    evidence: pyproject.toml
```

or:

```text
verification target:
    path: src/x.test.ts

project context:
    root: frontend
    kind: node-project
    evidence: package.json
```

Exact schema is implementation-owned.

OH-Goon MUST NOT reconstruct these roots from path prefixes once the producer exposes them.

### HM-CHANGE-005 — shared verification-support relationships (lifecycle REV2 §62)

Hashmarks SHALL represent shared verification-support relationships for supported evidence, including:

- pytest `conftest`;
- shared fixtures;
- test helper modules;
- test factories;
- repository documents read by tests;
- test configuration;
- shared setup files.

Hashmarks SHALL prefer structural/source evidence.

A permanent filename-only allowlist is insufficient as repository intelligence.

### HM-CHANGE-006 — consumer-scope qualification (lifecycle REV2 §63)

For shared verification-support changes, Hashmarks SHALL expose whether consumer scope is:

- complete;
- incomplete;
- unknown;
- truncated.

It SHALL also expose whether negative evidence is admissible.

OH-Goon may then decide whether to:

- use focused verification;
- widen verification;
- require broader gates;
- refuse a narrow merge selection.

That policy remains OH-Goon-owned.

### HM-CHANGE-007 — changed verifier classification (lifecycle REV2 §64)

Hashmarks SHALL distinguish at least:

- source change with linked verifier;
- verifier itself changed;
- shared verification infrastructure changed;
- no qualified verifier found;
- ambiguous verifier ownership;
- consumer scope incomplete.

OH-Goon SHALL not infer these classifications from low-level rows.

### HM-CHANGE-008 — one bounded producer response (lifecycle REV2 §65)

The preferred flow SHALL become conceptually:

```python
result = hashmarks.verification.changed_set(changed_paths)
```

or equivalent through existing CodeMap/query surfaces.

Exact API naming is not prescribed.

The requirement is:

```text
one Hashmarks read/decision scope
        ↓
one qualified response
```

rather than independent per-path calls whose semantics are reassembled downstream.

### HM-DEP-001 — Python dependency-consumer evidence remains upstream (lifecycle REV2 §66)

Hashmarks PR #476/HUG-176 is the prerequisite for current Python dependency-consumer evidence.

After released adoption, Hashmarks SHALL remain the owner of:

- dependency/distribution evidence;
- distribution/import evidence;
- dependency-consumer relationships;
- consumer-scope completeness;
- unknown/unsupported semantics.

OH-Goon SHALL not duplicate them.

### HM-DEP-002 — extend dependency-consumer evidence beyond Python where supported (lifecycle REV2 §67)

OH-Goon currently knows that:

```text
frontend/package-lock.json
```

may affect repository dependency consumers.

Hashmarks PR #476 focuses on Python/uv.

Therefore a genuine product gap remains for Node/npm-style dependency consumer evidence.

Hashmarks SHOULD eventually support qualified consumer evidence for supported Node/npm dependency declarations and resolution sources.

This SHALL use Hashmarks's repository/dependency relationship model.

It SHALL NOT be an OH-Goon-specific `package-lock.json` exception.

Until producer support exists, OH-Goon may conservatively widen/refuse scope.

It SHALL NOT claim the consumer scope is known.

### HM-DEP-003 — stable consumer-facing dependency projection (lifecycle REV2 §68)

Current OH-Goon dependency integration still joins Hashmarks internal structures such as:

- components;
- selections;
- inventory;
- relationships.

Some model translation is consumer-owned.

However OH-Goon SHOULD NOT depend on producer internal graph representation more than necessary.

Hashmarks SHOULD expose a stable consumer-facing dependency projection representing qualified resolved dependency facts, including where applicable:

- node identity;
- component/distribution identity;
- name;
- resolved version;
- source;
- contexts;
- qualified relationships;
- evidence source/provenance;
- conditionality;
- completeness.

Hashmarks still claims no execution authority.

### HM-DEP-004 — distribution names are not import names (lifecycle REV2 §69)

The current direction in Hashmarks PR #476 SHALL be preserved.

A distribution such as:

```text
some-package
```

must not automatically imply:

```text
import some_package
```

without qualified evidence.

Unknown mappings remain unknown.

Conditional imports remain qualified.

Namespaces and stubs retain their limits.

OH-Goon SHALL not compensate with naming heuristics.

### Repository-intelligence observability SHALL remain Hashmarks-owned (lifecycle REV2 §86F)

The word `observability` is overloaded and SHALL be separated into distinct ownership classes.

#### Repository-intelligence observability

Hashmarks owns evidence that explains repository structure and repository relevance, including supported forms of:

- source/declaration relationships;
- dependency consumer relationships;
- verification ownership;
- verification relevance;
- path mapping;
- source/runtime correlation;
- traceback-to-source correlation;
- runtime path correlation;
- file/line correlation;
- symbol correlation;
- timestamp-qualified repository evidence;
- provenance;
- freshness;
- completeness;
- truncation;
- uncertainty.

If OH-Goon needs these facts to understand what repository source a runtime observation refers to, that requirement SHALL first be evaluated as a Hashmarks producer capability.

OH-Goon SHALL NOT create a second repository-intelligence observability engine merely because the input originates at runtime.

### Runtime/source correlation is a cross-repository product seam (lifecycle REV2 §86O)

OH-Goon MAY surface runtime evidence such as:

- traceback frames;
- native command output;
- changed runtime paths;
- file/line references;
- symbol names;
- timestamps;
- execution environment path mappings.

When repository interpretation is required, the intended flow SHALL be:

```text
OH-Goon execution observation
        ↓
bounded correlation request
        ↓
Hashmarks repository-intelligence producer
        ↓
qualified correlation fact + provenance/freshness/completeness
        ↓
OH-Goon execution receipt / diagnostics
```

Hashmarks SHALL own the repository correlation semantics.

OH-Goon SHALL own capture of the execution observation and binding to Run/Step/Attempt identity.

This prevents:

```text
Hashmarks source correlation
+
OH-Goon source correlation
```

from becoming two competing interpretations.

### Correlation requests SHALL be bounded and source-bound (lifecycle REV2 §86P)

A runtime/source correlation request SHALL identify enough context to prevent accidental cross-repository interpretation.

Where applicable it SHOULD bind:

- repository/source identity;
- admitted source revision;
- workspace/project identity when already known;
- normalized runtime path evidence;
- line/symbol evidence;
- observation timestamp or execution identity;
- bounded candidate set when appropriate.

Hashmarks response SHALL retain:

- provenance;
- freshness;
- completeness;
- ambiguity;
- unsupported state.

OH-Goon SHALL NOT drop those qualifiers when presenting or consuming the result.

## 25. Producer and adapter conformance retained from lifecycle REV2

These tests supplement §10, §13 and §18 through their existing product owners;
they do not add another test-selection or execution owner.

### Maintained semantic call paths (lifecycle REV2 §86J)

The repository SHALL maintain a machine-checkable or mechanically reviewable
production call graph through existing architecture and review owners:
`production caller → oh_goon.integrations.hashmarks public API → producer invocation`.
It SHALL reject direct producer imports, local repository heuristics that decide
repository meaning, observability helpers that make repository-semantic decisions,
and raw producer rows used for downstream semantic reconstruction. Existing
AST/import/dependency analysis, package export checks and focused behavioral
contract tests may establish this evidence. The mechanism is implementation-owned;
no new standalone registry, scheduler or merge gate is required.

### Mandatory boundary conditions (lifecycle REV2 §102)

| Evidence condition | Required behavior |
| --- | --- |
| producer current and complete | consume |
| producer stale | reject current admission |
| ownership ambiguous | preserve ambiguity |
| consumer scope incomplete | do not infer completeness |
| verifier unavailable | do not invent verifier |
| project root unknown | do not infer from directory prefix |
| dependency consumer unknown | do not invent narrow scope |
| producer output malformed | adapter rejects |
| producer execution authority claimed | adapter rejects |
| producer contract identity changed incompatibly | fail admission |
| producer selection changes after source drift | old Run selection invalidated, not silently replaced |

### Mandatory Hashmarks producer tests (lifecycle REV2 §104)

The changed-set producer capability SHALL cover:

- ordinary source → directly linked test;
- source → bounded-indirect test;
- test file itself changed;
- shared fixture changed;
- factory/helper changed;
- repository document read by test;
- nested Python project;
- frontend/node project;
- ambiguous owner;
- stale evidence;
- incomplete bounded search;
- deleted changed source;
- renamed source;
- mixed project change set;
- same test nominated by multiple edited paths;
- duplicate nomination convergence;
- unknown consumer scope;
- shared-support scope incomplete;
- producer freshness change during observation;
- source/project context ambiguity.

### Mandatory OH-Goon adapter tests (lifecycle REV2 §105)

OH-Goon SHALL prove it does not reinterpret producer semantics.

Given producer states equivalent to:

- linked verifier;
- shared support;
- changed verifier;
- unknown scope;
- ambiguous owner;
- incomplete evidence;

OH-Goon SHALL map those states to its own admission/execution policy without reconstructing how Hashmarks derived them.

Mutation tests SHOULD demonstrate that changes to relationship evidence affect Hashmarks output, not independent OH-Goon path rules.

### Mandatory dependency migration tests (lifecycle REV2 §106)

After released Python dependency-consumer evidence is adopted:

```text
Hashmarks evidence available + complete
    → OH-Goon consumes exact scope

Hashmarks evidence incomplete
    → OH-Goon widens/refuses according to policy

Hashmarks evidence unavailable
    → OH-Goon does not invent a narrow scope
```

Removing the old consumer heuristic SHALL not weaken expected safety.

### Additional authority challenges (lifecycle REV2 §86R and §86S)

- Return raw lower-level rows to a downstream caller that must join/filter them
  to determine repository relevance: identify the contract boundary violation.
- Change producer-qualified relationship output while keeping consumer path names
  unchanged: consumer behavior follows the producer facts.
- Exercise development verification, dependency evidence, verification selection
  and runtime/source correlation through the supported public package API.
- Given traceback/path/line/symbol observations requiring repository meaning,
  consume producer correlation; preserve ambiguity and incomplete evidence.
- With unavailable producer project/workspace correlation, do not infer a root
  from `backend/`, `frontend/` or similar path prefixes.

### Migration evidence and semantic sweep (lifecycle REV2 §53–57, §73–74, §86I–N, §86T–U, §87–88, §114)

The reviewed source findings supplement §22: workspace splitting by `frontend/`,
`coverage_evidence` filtering, `edit_candidates` ownership reconstruction,
`conftest.py`/`tests/factories` scope rules, dependency-lock consumer heuristics
and raw producer dependency joins must be dispositioned and replaced through
§8 and §14. A path may remain an OH-Goon policy artifact only when its meaning
is product policy rather than a substitute repository relationship.

The original upstream PR #476 finding identifies native Python/uv dependency
consumers and METADATA/RECORD distribution-to-source evidence. Reuse that
producer work, qualify and release it normally, adopt the released dependency,
and delete replaced consumer derivations. No private wheel injection, copied
producer implementation or consumer-side fork substitutes for that sequence.

Review ambiguous source/runtime correlation and observability neighborhoods as
well as direct imports. The sweep includes changed paths, test selection,
verification relevance, ownership, impact, dependency consumers, workspace and
project roots, `conftest`, factories, package/uv locks, tracebacks, runtime/source
paths, line/symbol mappings, provenance, freshness, completeness, telemetry,
diagnostics and traces. Classify results by §7A: producer implementation,
boundary validation/translation, legitimate OH-Goon policy/custody/observability,
or a duplicate semantic owner that must be removed.

Retain a finite, reviewable disposition of pre-existing heuristics in the owning
migration issue or current owner document: identity, owning file/responsibility,
missing producer capability, scope/reason, safety behavior (widen/refuse/approximate),
non-authoritative status, upstream tracking, removal condition and safety
replacement proof. This is migration evidence under existing owners, not a new
production registry or permission to execute an alternate semantic provider.
An approximation cannot satisfy required repository evidence. Delete replaced
heuristics and prove retained safety after released adoption; do not retain a
catch-all exception fallback or strengthen producer unknown into certainty.

Dependency distribution names must not become guessed import names; namespace,
stub and conditional evidence keep their limits (§24). A missing Node/npm
producer capability remains explicit and cannot establish a narrow consumer
scope. Ordinary consumer policy may widen/refuse within its existing authority.

## 26. Source reconciliation and delivery traceability

The original Hashmarks requirements §§1–23 are retained in full. Additions in
§24 and §25 preserve details formerly specified by lifecycle REV2. The
[lifecycle source-section crosswalk](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md#source-section-crosswalk)
dispositions every original lifecycle section without maintaining a second
Hashmarks specification.

The stronger mandatory-SSOT prohibition in §1, §3.4, §6 and §11 governs the old
lifecycle REV2 §86E/§86M/§86T/§86U/§87/§88/§118/§123 references to temporary
heuristics or fallback. Their finite audit/removal and safety-proof obligations
are retained in §25; their weaker permission to substitute producer meaning is
not retained. Missing precision stays incomplete/unsupported until the producer
establishes it. No second registry, provider or release identity is introduced.

| Delivery owner | Requirement responsibility |
| --- | --- |
| [HUG-177](https://linear.app/hugloss/issue/HUG-177) / [HUG-178](https://linear.app/hugloss/issue/HUG-178) | Producer changed-set/workspace/correlation and dependency projection/requalification capabilities (§7, §24–25). |
| [HUG-179](https://linear.app/hugloss/issue/HUG-179) | Mandatory public API, adoption, anti-shadow migration, removal and consumer proof (§3–18, §21–25). |
| [HUG-180](https://linear.app/hugloss/issue/HUG-180) / [HUG-181](https://linear.app/hugloss/issue/HUG-181) | Execution custody and failure qualification in the separate lifecycle requirements; Hashmarks grants neither. |
| [HUG-182](https://linear.app/hugloss/issue/HUG-182) | This normative split and source traceability. |
| [HUG-184](https://linear.app/hugloss/issue/HUG-184) | Converge active contributor/operator/owner documentation with implemented boundaries. |
| [HUG-185](https://linear.app/hugloss/issue/HUG-185) | Reconcile execution-adjacent backlog scope with the canonical lifecycle. |
| [HUG-183](https://linear.app/hugloss/issue/HUG-183) | Full requirement-to-evidence audit, semantic leftover sweep and released-package/candidate qualification. |

The table allocates work; it does not establish implementation or test PASS.
Use the existing focused development checks while editing and the existing
coherent-candidate gate once the implementation is ready. Neither documentation
deduplication nor merged feature PRs replace HUG-183's evidence obligations.
