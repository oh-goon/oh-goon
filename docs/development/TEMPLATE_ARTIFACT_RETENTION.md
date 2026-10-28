# Template artifact retention — one runtime owner

This is the canonical authoring explanation for `steps[].artifacts[]` in shipped
Goons. It documents the existing backend contract, not a new template-level
storage manager or an alternate artifact schema. An Atomic Goon declares the
**file** produced by its command; the production executor owns collection,
evidence and any content-store publication. Authored output and artifact paths
are relative to the command working directory. Target-project uv Goons default
to the selected project and compile paths into repository-relative execution
paths. Package-owned `runtime: python` Goons write into owned attempt working
state, separately from captured package source and the target repository. The
existing collector consumes those resolved roots; it does not reinterpret the
project selection. Retention and repository-scoped publication ownership are
the same for both runtime selections.

## Choose the retention boundary

| Declaration | Run/Step evidence | Durable file bytes | Appropriate use |
| --- | --- | --- | --- |
| `retention: evidence` | PRESENT path, SHA-256 and byte count, or MISSING path | Not retained by this declaration | Test reports, scanner diagnostics, manifests, checksums and other proof of a result |
| `retention: content_store` | The same artifact evidence, plus a repository-scoped retained-object reference for a present file | Yes, through the existing Fleet content store | Models, metrics for historical comparison, distribution archives or other files needed after Step workspace disposal |

`evidence` is also the model's default when `retention:` is omitted. Shipped
catalog examples spell it out where it explains the choice. Neither mode
creates an implicit Step, caches every artifact, or copies all Run logs into the
content store. Run logs, command exit status, execution receipts, Game Tape,
workspace/cache snapshots and scalar `outputs:` have their own existing
owners; an `artifacts:` declaration does not replace those authorities.

A `content_store` artifact is **also evidence**: one declaration already
carries both the file hash/size and a retained reference when published. Do
not duplicate the path with a second `evidence` declaration to get both.
A reported `MISSING` artifact has no file bytes to retain; do not mistake a
missing artifact for a successful retained publication. A successful
`content_store` publication requires an available `FleetContentStore` and one
of these execution boundaries:

- Strict `execution.purpose: cicd` uses its original prepared-run and sealed
  attempt authority.
- Trusted-local `execution.purpose: verification` may retain declared files
  from a captured package or independently selected application's owned attempt
  workspace. The original execution owner binds that workspace to the actual
  registered repository and Run scratch. It collects only after the process
  boundary is settled and before scratch disposal.

Resolution rejects retained publication from ordinary direct target-workspace
commands and other non-strict purposes. Missing store or repository ownership
refuses before native preparation. Collection re-proves the allocated scope;
source roots, other Steps' workspaces, escaping links and arbitrary host paths
do not become publication authority. Partial failed collection releases its new
references. Artifact evidence reports the retained file's raw byte hash and
size. Its retained-object reference separately carries the existing content
store's canonical Hashmarks identity; those are distinct identities.

Retained objects are content-addressed and references are repository-scoped:
an SHA alone is not retrieval authority. Retention proves stored file identity
and ownership; the Run's selected execution policy and original receipts own
its execution guarantees. Trusted-local retention does not issue strict
executor, isolation or certification evidence. Retained cache inputs and
automatic retries keep their existing strict-only requirements.

### Export a recorded artifact

Use the Run and Step printed by execution or `plan outputs`:

```sh
oh-goon plan export-artifact RUN_ID --step STEP_ID \
  --path ml-output/iris-training-metrics.json --output ./metrics.json
```

The command selects the exact declared retained artifact from the recorded
Run. Export requires a terminal, uncancelled Run and a completed selected Step
whose process settlement and cleanup are verified. Cancelled or interrupted
execution and unknown cleanup refuse before reading retained bytes. A failed
Step with verified cleanup may still supply its retained diagnostic artifacts;
another failed Step does not invalidate a completed producer's artifacts.
Export verifies repository ownership and file kind, then materializes under an
existing content-store read lease. The copied canonical object identity must
match the retained reference, and its raw byte hash and size must match the
artifact evidence. Private file-backed
staging is verified before exclusive final publication. The destination parent
must exist; an existing file or symlink alias is refused. In a Shared Run where
the same Step appears for several repositories, add `--repository-id ID` to
select the recorded repository. Completion prints a fully scoped export command
for each retained artifact shown in its bounded preview.

Export remains available after execution scratch and caller source disappear,
while the retained reference remains live. Missing, evidence-only, released or
inconsistent content refuses; export never falls back to a current workspace.
`plan outputs`, Game Tape and the Run API describe collection-time evidence:
`PRESENT` means the file was collected, and a recorded retained reference does
not prove that its bytes are still available. Export checks the live reference
and bytes under the read lease. A printed export command is a selection hint,
not a fresh availability observation. Releasing retained bytes preserves that
historical evidence and the original Run result.
Exported bytes are an explicitly promoted user file; exporting does not release
the retained reference or change the historical Run result.
If private staging cleanup fails after publication, the export still reports
the verified destination and `status: exported`, with `cleanup_status: failed`
and `cleanup_failure` describing the separate cleanup problem. Resolve that
problem rather than retrying onto the already-created destination. Cleanup
cannot replace a primary export error or rewrite the historical Run outcome.

### Concrete shipped choices

- [`general/orders-taskflow`](../../templates/goons/general/orders-taskflow/goon.yml):
  the extracted, transformed and final JSON files are evidence. Scalar outputs
  carry this Run's values into later Steps; these declarations do not retain
  downloadable bytes after attempt scratch is disposed.
- [`python/test`](../../templates/goons/python/test/goon.yml): the JUnit report
  provides test *evidence*; coverage policy and optional reports belong to native
  project configuration. No durable bytes are requested.
- [`python/profile`](../../templates/goons/python/profile/goon.yml): the profiling
  report is evidence. Ruff uses native diagnostics without a duplicate report.
- [`security/trivy-audit`](../../templates/goons/security/trivy-audit/goon.yml):
  the JSON scan report is deliberately content-store-retained for later reuse;
  this is not an automatic property of every audit report.
- [`python/package-artifacts`](../../templates/goons/python/package-artifacts/goon.yml):
  the distribution bundle is retained content while its manifest is evidence.
- [`ml/iris-train`](../../templates/goons/ml/iris-train/goon.yml): metrics and
  model parameters are retained for historical comparison and reuse. Its default
  Plan selects trusted-local verification; an explicit CI/CD Plan retains strict
  execution requirements.
- [`oh-goon/release-package-store`](../../templates/goons/oh-goon/release-package-store/goon.yml):
  the exact source ZIP is retained for later fresh materialization, while
  checksum and source manifest provide evidence. The retained ZIP **must still
  be independently verified** before release certification.
- [`oh-goon/repository-exam`](../../templates/goons/oh-goon/repository-exam/goon.yml):
  its certification manifest is evidence, not the package under test.

These decisions reflect producer/consumer semantics, not a blanket policy
based on extension (`.json` may be either class). Changing an existing mode
requires an explicit consumer/storage analysis; do not rewrite Trivy or Ruff
merely to equalize counts.

## Retention ownership, reference life and identity

`PlanArtifactDeclaration` owns the two-valued declaration contract.
`fleet_plan_step_publication.py` owns collection and qualified publication;
`PresentPlanArtifact` / `MissingPlanArtifact` and
`PlanRetainedContentEvidence` own the reported facts. The Fleet content store
owns file objects and repository-scoped references, including leased file
materialization. The original Run service selects recorded artifacts for CLI
export. Content maintenance
(`fleet_content_maintenance.py`, `fleet_content_retention.py`) owns garbage
collection by object/byte budgets, idle eligibility and protection for live
references/leases; templates do **not** author a TTL, lease, byte budget, or
retention schedule. An object with a live reference is protected even if a
capacity budget is exceeded. A reference may be explicitly released; loss of
a reference is not an assertion that historical run evidence disappears.
The run evidence's hash is a description of what happened, not a perpetual
promise of byte availability.

The `retention` value is part of the resolved Step's artifact declaration,
therefore changing it changes resolved Plan authority and its content-derived
Plan ID. It is **not** a runtime-derived artifact digest and does not inject
content-store reference IDs into the pre-execution Plan identity. Two runs
that produce byte-identical files have the same file-content digest even if
publication uses different repository-scoped references.
The **Run ID** and attempt evidence are separate execution/runtime identities:
do not use a retained content reference as a substitute for either identity.

## Audit checklist

For each shipped `steps[].artifacts[]` entry, verify (1) a real file is
produced; (2) a later independent consumer genuinely requires durable bytes
for `content_store`, otherwise evidence suffices; (3) the retention mode is
unambiguous; (4) no duplicate artifact path simulates two retention classes;
(5) Step dataflow/`outputs:` and workspace references remain independent; and
(6) missing files, unqualified direct workspaces, unavailable stores and
unsettled processes cannot accidentally count as successful retained artifacts.

See [Goon authoring](GOON_AUTHORING.md),
[template input resolution](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), and
[content-store implementation](../../backend/src/oh_goon/integrations/fleet_content_store.py).
