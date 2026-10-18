# Fail before work and anti-cucumber authority

## Status

This document is a **repository-wide normative architecture contract**. It is not a phase note, historical rationale, or optional cleanup preference.

The governing principle is:

> **It should be easy to do the right thing—but if we do the wrong thing, we fail hard and very quickly. We should not even begin.**

Operationally:

> **The valid path must be the simplest path. Any invalid state already knowable by the responsible authority must be rejected before workload execution begins. Once execution authority has been established, later layers transport and consume that authority; they do not rediscover or reinterpret it.**

This rule composes with the repository-wide delegation invariant:

> **Resolve once, transport once, execute once, report once.**

## What this principle does not mean

Fail-before-work is not permission to add a preflight for every future runtime failure, declare every executable at every layer, scan every shell command, or wrap every native tool.

The target is:

```text
ordinary things are effortless
meaningful exceptions are explicit
actual mistakes fail at their earliest legitimate owner
runtime-only failures remain runtime failures
```

### Read-only authoring before runtime

`oh-goon plan validate` compiles the Plan using the same canonical
`FleetPlanResolver` used for execution, without initializing the local
application database, Run journal or executor. This is read-only authoring
feedback and is **not** Enforcer admission, host preparation, physical LACE
qualification or permission to execute. `plan run` continues to use the
canonical stateful Fleet admission path; validation neither creates nor
silently repairs the execution environment. A failed validation does not
create `goon.lock` or run workload commands.

### Run output is presentation, not new execution authority

`oh-goon plan run --json` reports the single existing Fleet Run submission and
waited terminal facts. It does not reclassify outcomes, mint admission, start an
alternative executor, infer native custody, or promote a JSON success to APP
certification. Per-invocation progress-to-stderr selection keeps the machine
result separate from transient UI progress without redirecting process-global
stdout. Failed admission and post-admission errors are distinct; error output
must never claim that no native work occurred unless the original owner proves
it. The default human-readable Run path remains unchanged.

A repair that adds ceremony, duplicate authority, a wrapper, a second resolver, a shadow cache, a second native-tool model, or a new certification proxy must justify why the existing owner cannot enforce the real invariant directly. Convenience or a desire to make a gate green is not sufficient.

## Authority classes

Executable dependencies are classified by authority, not by whether a command name appears in source.

### Platform authority

Oh-Goon/executor platform machinery is established once by the supported runtime contract. Workloads do not repeatedly restate it.

Examples include process-control facilities, the supported shell/runtime contract, executor bootstrap machinery, and mandatory isolation primitives.

### Repository-native authority

Tools already implied by committed project authority are repository-native. They are derived from the repository's own native workflow and are not independently re-resolved by each Goon, Step, Make target, or launcher.

Examples include:

```text
pyproject.toml + uv.lock + native Python workflow -> uv
uv run pytest                                    -> pytest resolved by uv
package.json/package-lock + native Node workflow -> npm
Maven wrapper authority                          -> Maven wrapper
```

The exact mapping is owned by the repository/native-tool contract. The examples are not a global allowlist.

### Exceptional workload capability

An explicit capability is justified only when it adds meaningful information not already implied by platform or repository-native authority.

Examples may include Docker, Kubernetes, Terraform, AWS tooling, Chromium, ffmpeg, Java, or .NET when the selected execution mode does not already imply them.

The decision test is:

> **Does this introduce a meaningful capability or environment requirement that a normal developer could not reasonably infer from the already-selected execution authority?**

If not, another explicit declaration is probably duplicated authority.

## Repair order for hidden executable dependencies

Finding an executable does **not** automatically mean “add a declaration.” Use this order:

1. remove an unnecessary dependency;
2. use the already-selected native tool/workflow;
3. make implementation match the existing platform contract;
4. derive the tool from existing repository-native authority;
5. only then add explicit workload capability when it conveys genuinely new information.

A tactical explicit declaration may be used to close a reproducible execution gap in a development candidate. It must be recorded as tactical containment when the higher-level authority should ultimately imply the tool. Tactical containment is not precedent for multiplying declarations.

## Certification semantic boundary

`make certify` proves the exact already-initialized Oh-Goon product through the APP verification Plan. It does not acquire strict release/CI placement authority merely because later release execution may require it.

Certification may legitimately fail for semantic failures such as:

- invalid or unresolved execution authority;
- broken application/runtime behavior;
- broken isolation/security invariants;
- inability to execute required supported functionality;
- incorrect cancellation, ownership, or lifecycle semantics;
- semantic corruption of committed lock/native-project authority;
- source/package/release identity disagreement where exact identity is part of the certification claim;
- unresolved execution edges or required exceptional capability;
- other defects that mean the exact product cannot safely or correctly do what it claims.

Certification must **not** fail merely because implementation shape or repository aesthetics changed. The following are not APP certification authority by themselves:

- physical source-file or function line count;
- arbitrary complexity or maintainability thresholds;
- formatting;
- lint/debt budgets;
- comment placement;
- source line numbers or offsets;
- preferred decomposition/module shape;
- documentation shape;
- repository-quality scores or other quantitative proxies.

The one-question test for every proposed certification blocker is:

> **Can Oh-Goon remain semantically correct, safe, reproducible, and fully functional while violating this rule?**

If yes, the rule does not belong in APP certification.

A second test is:

> **Could adding a harmless comment, moving equivalent code, or splitting/combining equivalent implementation change this certification result?**

If yes, the check is probably coupled to source shape rather than product semantics.

## Source location is metadata, not semantic authority

Line ranges and source positions are useful for navigation and human explanation. They are not semantic identity unless exact position is itself the thing being certified.

Prefer stable semantic anchors such as:

- named invariant/test identity;
- owner identity;
- artifact/content identity where appropriate;
- execution receipt;
- semantic result;
- exact source digest when exact bytes are the actual authority.

A harmless line shift must not invalidate an otherwise unchanged semantic proof.

## Structural ownership must survive private renames

Architecture certification may bind public protocol/schema identities and real package/import boundaries. It must not bind private local-variable names, helper names, source expressions, or filename prefixes. A behavior-preserving private rename must leave the architecture result unchanged; if behavior must be proven, use an executable regression.

Scout ownership is the explicit `oh_goon.scout` package boundary, not whether a basename looks like `scout_*`. A module moved across that package boundary changes ownership; a rename inside the package does not.

A Git repository root beneath a configured League root defines a Team regardless of incidental ancestor directory names such as `build`, `dist`, `.hidden`, or `node_modules`. Traversal policy must not manufacture or erase Team identity.

Transient-source exclusions must identify an exact current semantic artifact class or owner. Exact `.hashmarks/` derived state is transient; an unknown `.hashmarks-*` name is ordinary source subject to normal ownership rules and must never disappear from canonical source identity merely because its name looks historical.

## Earliest legitimate failure owner

For each failure, ask when the fact first becomes knowable and who owns it.

```text
candidate
  -> resolve semantic authority once
  -> admission
  -> [placement/executor acquisition only when the selected purpose consumes it]
  -> authority becomes executable
  -> workload
  -> report
```

If a required authority cannot be established before workload and that fact is already knowable, reject before the first user Goon/Step starts.

Negative qualification for pre-execution invalidity should prove:

```text
Goons started = 0
Steps started = 0
workload side effects = 0
```

Do not create a new preflight stage when the existing admission, placement, executor, repository-native, or platform owner can make the decision at its natural boundary.


## One Fleet admission truth

`FleetAdmissionPlanner` owns the base repository + resolved-Plan admissibility decision for every Fleet Plan purpose. Execution purpose may add requirements; it must never select a weaker owner or delay common invalidity until after `RUN_ADMITTED`.

```text
resolve Plan
  -> common repository/Plan admission
  -> strict-only additions when purpose=cicd
  -> RUN_ADMITTED with compact repository-subject/Plan admission identity
  -> preparation revalidates that identity / freezes runtime inputs
  -> effects
```

Preparation may reject **staleness** when a previously admitted fact changed. It must not independently decide the initial static validity again. Retained cross-run cache inputs are strict-CI/CD-only because only strict preparation freezes and verifies those immutable retained bytes; direct/verification Plans must reject that source during resolved-Plan validation rather than discover unsupported retained authority later.

## Runtime-only failures remain native runtime failures

Failures that cannot legitimately be known before execution remain runtime failures. Examples include a network disappearing, a registry rejecting credentials, a dependency service crashing, disk exhaustion, or a test actually failing.

Do not build speculative shadow preflight systems to predict native runtime behavior. The native owner reports the failure; Oh-Goon stops promptly, attributes the failure correctly, and preserves the native semantics.

## Observation is not authority

Diagnostics may observe facts without acquiring decision authority.

```text
verify observes a large file
!=
certification rejects a large file

PATH observation finds an executable
!=
PATH observation authorizes that executable
```

Keep observation, decision, authority, execution, and reporting separate.

## Internal identity is provenance, not hidden dispatch

Plan IDs, Goon names, display labels, and historical aliases may identify what was selected or what produced evidence. They must not silently become scheduling, blocking, repair, capability, admission, or execution classifiers unless that exact identity is an explicitly documented semantic/public contract.

Use the rename challenge: if an internal Plan/Goon can be renamed while preserving its declared operation and semantic role, behavior must remain unchanged. When an identity-only special case is removed, prefer deletion over replacing it with a new allowlist, prefix test, classifier registry, or metadata field. Add semantic metadata only when an independent behavioral distinction actually exists and has an owner.

## Anti-wrapper and anti-preflight rule

Do not solve authority defects by introducing artifacts such as:

```text
oh-goon-uv-wrapper
oh-goon-npm-wrapper
oh-goon-pytest-wrapper
generic-native-tool-proxy
PATH-normalization shim
shadow package/dependency resolver
generic preflight framework
certification-only package universe
```

The expected architecture is:

```text
Oh-Goon orchestrates
native tools execute
Oh-Goon observes/reports
```

not:

```text
Oh-Goon partially reimplements or second-guesses native tools
```

## Tactical containment versus final architecture

A development repair is allowed to close a concrete failure without simultaneously redesigning the entire authority model. Such a repair must be classified explicitly:

```text
TACTICAL CONTAINMENT
FINAL AUTHORITY DESIGN
```

A tactical repair is not allowed to silently become the permanent contract merely because CI turns green.

Before closing the campaign, revisit every tactical declaration, exception, compatibility branch, duplicated check, and temporary owner. Either prove it conveys independent semantic information or remove/derive it from the real owner.

## Change-review gate

Any change that does one or more of the following requires an explicit owner analysis in the change rationale:

- adds a `make certify` dependency or blocker;
- adds a new executable/tool declaration;
- adds executable discovery or PATH-based selection;
- adds a wrapper, shim, resolver, cache, preflight, or compatibility layer;
- turns diagnostic evidence into admission/certification authority;
- binds authority to source position or source-shape metrics;
- duplicates a fact already established by platform or repository-native authority.

The rationale must answer:

```text
What semantic invariant is protected?
Who is the authoritative owner?
When does the fact first become knowable?
Why can the existing owner not enforce it directly?
Does this add a second resolver/decision owner?
Can a correct product violate this rule?
Can harmless source movement change the result?
Is this tactical containment or final architecture?
What obsolete machinery becomes deletable because of this change?
```

If those questions do not have crisp answers, do not add the mechanism.

## Anti-drift sweep

Before declaring an authority/certification campaign complete, verify all of the following:

```text
normal correct path is still the shortest path
ordinary native tools are not redundantly declared
no executable is independently resolved by multiple owners
known-invalid pre-execution authority cannot begin workload
runtime-only failures remain native runtime failures
certification cannot fail solely on source shape
source line movement cannot invalidate semantic authority
no wrapper/shim/resolver was added only to satisfy Oh-Goon
normal cancellation does not require ritual repair
new diagnostics have not silently become authority
cleanup leaves fewer independent decisions than it found
```

A change that produces more declarations, more wrappers, more preflight stages, more duplicate validators, or more certification gates than it removes should be presumed architectural drift until proven otherwise.

## Regression rule

Every reproducible defect that is fixed gets a focused regression, but the regression must test the real defect rather than a proxy.

Examples:

```text
wrong: assert file <= 600 lines
right: assert the lifecycle/ownership behavior that actually matters

wrong: assert bash is declared everywhere
right: assert implementation satisfies the promised shell contract

wrong: assert evidence line range remains 1-19
right: assert harmless source movement preserves semantic certification
```

The regression suite is the safety boundary that permits obsolete cucumber to be deleted rather than retained “just in case.”

## Permanent invariant

The intended end state is:

> **Easy to do right. Hard fail when genuinely wrong. Do not even begin when we already know it cannot be correct.**

And:

> **The cleanup must leave less authority machinery than it found.**
