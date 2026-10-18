# Enforcer run receipts

`enforcer.yml` is a deterministic, non-secret debugging projection for one admitted strict CI/CD run.
It is **not** pipeline configuration and it is **not** another durable source of truth. The append-only
Journal and canonical operation evidence remain authoritative; oh-goon regenerates the receipt from
those facts for any historical run.

The goal is deliberately ordinary: save two receipts and use a text diff to answer **what changed
between these runs?**

```bash
oh-goon enforcer compare before-enforcer.yml after-enforcer.yml
```

Runs also exposes `GET /api/fleet/runs/{run_id}/enforcer.yml` and a download action.

## Recorded facts

The receipt records facts useful for comparison without requiring authors to declare them twice:

- Fleet run identity, timestamps, status/outcome, and readmission lineage;
- resolved plan identity, contract version, one required structural `plan_source`, ordered step summary, and tools required by that plan;
- one canonical template import graph with kind/name, parent relationship, source SHA-256, owned requirements, and owned step IDs;
- per-repository parent-aware applicability for those exact import IDs, including explicit non-applicable reasons;
- exact structural Fleet placement (backend + capacity reservation), plus materialized inventory/placement/reservation/health IDs for stable human diffs;
- repository requirement admission ID/count plus the resolved requirement facts;
- exact resolved CI/CD execution policy, including repository execution mode, repository-trust choice, and CPU/memory/process envelope;
- one nested privacy-safe `execution_input` per repository when strict input evidence exists;
- canonical path-free repository/runtime snapshot identities;
- imported host **variable names**, never their values;
- hostname plus SHA-256 of the admitted resolution set, never raw resolved addresses;
- observed/frozen tool names, hashes, sizes, authority IDs, and the whole-toolchain ID;
- canonical managed snapshot and retained content-object identities;
- per-step status, duration, return code, repository-state fingerprints, artifact/output digests;
- per-attempt structural cache snapshots, privacy-safe egress/resource policy identity, tool identity, typed Trainer retry evidence, and Zamboni teardown status.

The format is `oh-goon/enforcer-run/v18`. Ordering is deterministic so ordinary unified diffs are
meaningful.

Receipt v18 groups run lifecycle provenance under one `lifecycle` fact. `status` uses the canonical `FleetRunStatus` vocabulary and legal timestamps are validated together: queued has no later timestamps, running requires `started_at`, cancelling requires `cancel_requested_at`, and terminal states require `finished_at` as appropriate. Semantic result is recorded separately as typed `FleetRunOutcome` because lifecycle state and result are not the same concept (`COMPLETED` may be `SUCCESS` or `ATTENTION_REQUIRED`). The receipt does not embed the whole `FleetRunState`; target, capability, repository scope, progress, and failure internals remain owned by Fleet/Journal.


Receipt v18 uses the same `FleetPlanSource` vocabulary as durable Fleet intent instead of three nullable source scalars. Source provenance remains separate from resolved plan shape: `plan_source` answers which exact repository-Goon/Shared-Plan source bytes were selected, while `plan` answers what privacy-safe resolved plan those bytes became. Every receipt therefore has exactly one `plan_source` object:

```yaml
# Single-repository Team run
plan_source:
  kind: team_goon
  sha256: <exact repository goon.yml SHA-256>
repositories:
  - repository_id: <the one owning Team>

# Shared Run
plan_source:
  kind: shared_plan
  plan_key: <selected Shared Plan key>
  sha256: <exact selected goon.yml SHA-256>
repositories:
  - repository_id: <selected Team A>
  - repository_id: <selected Team B>
```


Receipt v18 retains the structural plan/Fleet execution model introduced in v11. Resolved plan identity and privacy-safe shape are recorded once under `plan`. `plan.execution` uses the same `CicdExecutionPolicy` structure that execution consumes; `plan.requirements`, `plan.imports`, and `plan.steps` are privacy-safe projections that omit executable payloads. Physical Fleet authority is recorded once as `fleet_execution`: one strict placement (`isolation_backend` + `executor_capacity`) plus optional readmission lineage. Stable computed placement identities are materialized under `placement_ids` because they are useful for text diffs but are derived from the canonical placement rather than separate authority. There is no flat `executor` object and no top-level `execution_purpose`, `trust_repository_code`, or `resource_*` mirror.

Conceptually:

```yaml
plan:
  plan_id: ...
  contract_version: 38
  name: ...
    trust_repository_code: true
    resources: ...
    repositories: ...
  requirements: ...
  imports: ...
  steps: ...

fleet_execution:
  kind: strict
  placement:
    isolation_backend: ...
    executor_capacity: ...
  placement_ids:
    inventory_id: ...
    health_attestation_id: ...
    placement_id: ...
    reservation_id: ...
```

The receipt parser verifies that the materialized IDs still match the placement and that the placement belongs to the same `fleet_run_id`. Older flat execution/executor shapes are rejected rather than translated.


Receipt v18 also replaces the five nullable repository operation mirrors with one optional `operation` fact. When a Fleet-plan report exists it contains the stable `operation_id` and the canonical typed `OperationOutcome` (`state`, `verdict`, `returncode`, and `reason_code`) together. When no report exists the whole fact is absent. This prevents partial outcomes while adding no sensitive runtime data.

Receipt v18 also keeps repository execution-input evidence under one `execution_input` object. It does not embed `PlanExecutionInputEvidence` verbatim because that canonical fact contains raw DNS answers and repeats repository/backend authority owned elsewhere. Instead the receipt reuses only the safe structural pieces and materializes only the derived IDs/fingerprints needed for debugging:

```yaml
repositories:
  - repository_id: <Team>
    execution_input:
      input_id: <stable execution-input identity>
      admission: ...
      base_workspace: {sha256: ..., byte_count: ..., entry_count: ...}
      runtime_root: {sha256: ..., byte_count: ..., entry_count: ...}
      host_environment_names: [REGISTRY_USER]
      hostname_resolutions:
        - hostname: registry.example.com
          sha256: <digest of the frozen address set>
      host_toolchain_id: ...
      host_tools: ...
      managed_inputs: ...
      content_inputs: ...
      import_applicability: ...
```

Repository identity remains on the enclosing repository fact. The strict isolation backend remains under `fleet_execution.placement`. Raw IP addresses, environment values, host paths, argv, stdout and stderr are never serialized. The old sibling fields (`execution_input_id`, `base_workspace`, `runtime_root`, `hostname_resolution_fingerprints`, `managed_input_digests`, and similar mirrors) remain invalid compatibility shapes; historical receipts are not upgraded implicitly.

Receipt v18 applies the same structural/privacy rule to each physical attempt without introducing another wrapper. `EnforcerAttemptFact` remains the single attempt owner:

```yaml
attempts:
  - attempt: 1
    returncode: 1
    duration_ms: 420
    tool: ...
    cache_input: {sha256: ..., byte_count: ..., entry_count: ...}
    cache_output: {sha256: ..., byte_count: ..., entry_count: ...}
    retained_cache_reference_id: ...
    egress:
      mode: hostname_allowlist
      policy_id: ...
    resource_policy_id: ...
    retry:
      disposition: SCHEDULED
      exit_code: 1
      policy_reason: transient-test-failure
      delay_seconds: 1.0
      next_attempt: 2
```

Cache input/output reuse canonical path-free `PlanSnapshotEvidence`, and retry reuses canonical typed `PlanRetryDecisionEvidence`. Egress deliberately does **not** reuse `PlanEgressEvidence`: its destination rules may contain internal CIDRs, so the receipt keeps only mode + stable policy ID. Raw destination rules are rejected by the receipt schema. Tool identity, resource policy ID, and retained-cache reference remain compact rather than expanding into paths, values, or payloads. The old flat `cache_input_sha256`, `cache_output_sha256`, `egress_mode`, `egress_policy_id`, and scalar retry fields are not aliases; v13 receipts fail v14 validation instead of being upgraded.

Receipt v18 applies the same rule to each step result without embedding canonical `PlanStepExecution` wholesale. Canonical step execution also carries argv, worktree filenames, stdout/stderr evidence, and artifact paths, so it is intentionally not a receipt type. The receipt reuses only the safe structural pieces:

```yaml
steps:
  - step_id: tests
    status: COMPLETED
    teardown:
      status: COMPLETED
      attempt_roots_observed: 1
      step_attempts_root_absent: true
    artifact_digests:
      - ...
    outputs:
      - name: IMAGE_TAG
        sha256: ...
        byte_count: 17
```

`teardown` reuses canonical `PlanStepTeardownEvidence`, whose reason/count fields contain no host paths. `outputs` reuses canonical `PublishedPlanOutputEvidence`, which contains only the declared logical output name plus content identity. Artifact evidence deliberately remains digest-only: canonical `PlanArtifactEvidence` includes artifact paths, so the receipt keeps present-artifact SHA-256 values and omits missing-artifact paths entirely. The old `teardown_status` and `output_digests` fields are not compatibility aliases.

Source and execution scope stay independent. A Team receipt must contain exactly one repository. An internal/shared receipt records the selected Goon source once and then lists the selected execution repositories; it does not read or copy those repositories' own `goon.yml` identities into the receipt. Standalone plan files use the same structural field as `kind: plan_file` + exact SHA-256, without serializing a host path.

Receipt v18 deliberately keeps the same durable import names as the resolved plan: `imports`, `source_sha256`, and per-repository `import_applicability`. There are no `template_imports` / `template_sha256` compatibility fields, and there are no `plan_source_kind` / `plan_source_id` / `plan_source_sha256` compatibility fields.

## Explicitly excluded

A receipt must never become a secret dump or a substitute for raw logs. It excludes:

- imported/secret/resolved environment **values**;
- argv and raw command payloads;
- stdout/stderr payloads;
- repository/host filesystem paths;
- raw resolved IP addresses;
- ambient process environment or caller `PATH`.

Hashes and IDs are evidence for comparison; they are not permissions or executable authority.

## Why this belongs to Enforcer

Enforcer's runtime responsibility stays small: prove the resolved run is admissible before effects.
The receipt is a read-only projection of the proof and the later canonical facts. It gives the hockey
persona a useful debugging role without creating an Enforcer daemon, mutable state store, or second
validation engine.

Enforcer receipt v19 records Plan ID/name, selected root source, source SHA-256 and verified execution digest together through required `execution_reference`. One canonical Goon/Plan import fact per resolved package retains parent import, source SHA-256, owned requirements, and concrete step ownership. Repository facts record applicability for those same import IDs. There is no duplicate `plan_source` or `goon_imports` receipt section. Historical receipts therefore remain explainable even when the current shipped template bytes later change.


For typed `oci_build` steps, v8 also records the stable `repository_policy_id` for the exact resolved Containerfile/context/registry policy. This is preflight evidence, not a hidden repository requirement: the root requirement list remains exactly what the author declared.

The receipt also makes the simplified authoring contract fully inspectable. It records the canonical requirement set, requirement admission identity, and the resolved execution policy under `plan.execution`, including `trust_repository_code` and the final CPU/memory/process values. Normal Goons can therefore omit low-level boilerplate without making the effective run policy invisible.


## Resolved step policies in v18

Resolved-plan step evidence reuses `PlanStepWorkspace`, `PlanStepCache`, and `StepRetryPolicy` directly because they are path-free and value-safe. The receipt therefore preserves typed workspace/cache lineage and the declared retry schedule without separate `workspace_source`, `workspace_publish`, `cache_source`, `cache_publish`, `cache_retain`, or `retry_max_attempts` mirrors. Egress is intentionally represented only by its mode: canonical destination rules may contain CIDRs, hostnames, protocols, and ports and remain outside `enforcer.yml`. Executable payloads such as argv, working directories, conditions, env bindings, artifact paths, and extension arguments remain excluded.
