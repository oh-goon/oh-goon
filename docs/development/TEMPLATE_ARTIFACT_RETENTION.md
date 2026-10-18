# Template artifact retention — one runtime owner

This is the canonical authoring explanation for `steps[].artifacts[]` in shipped
Goons. It documents the existing backend contract, not a new template-level
storage manager or an alternate artifact schema. An Atomic Goon declares the
repository-relative **file** produced by its command; the production executor
owns collection, evidence and any content-store publication.

## Choose the retention boundary

| Declaration | Run/Step evidence | Durable file bytes | Appropriate use |
| --- | --- | --- | --- |
| `retention: evidence` | PRESENT path, SHA-256 and byte count, or MISSING path | Not retained by this declaration | Test reports, scanner diagnostics, manifests, checksums and other proof of a result |
| `retention: content_store` | The same artifact evidence, plus a repository-scoped retained-object reference for a present file | Yes, through strict Fleet content store | Distribution archives, models or other actual files that must be independently retrieved after Step workspace disposal |

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
`content_store` publication requires strict execution and an available
`FleetContentStore`. Direct execution rejects content-store retention instead
of silently downgrading it to evidence. Retained objects are content-addressed
and references are repository-scoped: an SHA alone is not retrieval authority.

### Concrete shipped choices

- [`python/test`](../../templates/goons/python/test/goon.yml): coverage and JUnit
  reports provide test *evidence*; no durable bytes are requested.
- [`python/ruff`](../../templates/goons/python/ruff/goon.yml) and
  [`python/profile`](../../templates/goons/python/profile/goon.yml): lint
  diagnostics and a profiling report are evidence.
- [`security/trivy-audit`](../../templates/goons/security/trivy-audit/goon.yml):
  the JSON scan report is deliberately content-store-retained for later reuse;
  this is not an automatic property of every audit report.
- [`python/package-artifacts`](../../templates/goons/python/package-artifacts/goon.yml):
  the distribution bundle is retained content while its manifest is evidence.
- [`ml/iris-train`](../../templates/goons/ml/iris-train/goon.yml): metrics are
  evidence; model parameters are retained reusable content.
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
`fleet_plan_step_publication.py` owns collection and strict publication;
`PresentPlanArtifact` / `MissingPlanArtifact` and
`PlanRetainedContentEvidence` own the reported facts. The Fleet content store
owns file objects and repository-scoped references. Content maintenance
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
(6) a missing file, direct-mode use of content-store retention, and unavailable
strict store cannot accidentally count as a successful retained artifact.

See [Goon authoring](GOON_AUTHORING.md),
[template input resolution](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), and
[content-store implementation](../../backend/src/oh_goon/integrations/fleet_content_store.py).
