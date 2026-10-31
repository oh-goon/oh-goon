### Refactor promotion evidence
### Fleet plan pipeline values and isolated subprocesses

`inputs + explicit env + direct Step outputs -> Python plan resolver -> CI/CD target-set admission -> resolved Fleet Plan v40 -> repository ownership proof -> run preparation -> frozen run authority -> repeated private attempts -> one isolated process`. `ApplicationRuntime.build()` materializes its private application wiring once; foundation wiring composes executor configuration, Git/network/tool/extension/isolation/content-store owners and the database-adjacent `ApplicationStatePathLayout`. Lower services consume those owners; they do not rediscover deployment state or invent filesystem roots.

Pipeline-global values are model data, not ambient process environment. Strict CI/CD target admission requires an explicit repository set, an accepted repository baseline, disjoint/non-aliased canonical repository roots, and one executor whose admitted isolation/resource guarantees cover the whole requested run. Repository ownership is rechecked immediately before snapshotting. The source tree rejects nested mounts, nested Git repositories/submodules, cross-filesystem entries, special files, escaping symlinks, and multiply-linked regular files. Snapshot copy/hash opens regular files without following a replacement symlink, so the byte-capture operation itself preserves the admitted repository boundary rather than trusting a prior check.

Path authority is typed by domain. `ContainedPathSpace` owns plan/module source navigation; `RepositoryPathSpace` and `RepositoryScopedPath` own repository-relative execution/data paths; `RunPathLayout` and `AttemptPathLayout` own Fleet private state; `ContentStorePathLayout` owns retained storage; `SandboxPathLayout` owns stable in-sandbox destinations; and `StrictHostPathAuthority` owns deployment-granted host executables. Absolute host paths, repository paths, sandbox paths, and storage keys are not interchangeable `Path` conventions.

Canonical template composition is resolved before this execution dataflow begins. `FleetPlanSourceGraph` reads/links the declared source tree once, derives static extension/pre-commit acquisition facts, and reuses unique canonical/local module nodes. Direct package discovery validates names/files, canonical Goon/Plan imports are expanded once, and the resulting `ResolvedFleetPlan` retains one canonical import fact per package with parent, owned requirements/steps, and SHA-256. Preview receives that same resolved object; execution does not rediscover templates or parse authoring YAML again. Imported Goon applicability is parent-aware and evaluated by one shared owner from repository-source facts that survive sanitization. Managed-source preflight uses the same predicate only to avoid acquiring resources for imports already known to be inapplicable; the frozen snapshot repeats the applicability decision and must match before execution.

Template provenance is byte-exact rather than path-later identity: canonical imports and the root plan are each read once, YAML semantics are parsed from those bytes, and SHA-256 is computed from those same bytes. repository-Goon/Shared-Plan/file source facts must match the root digest before FleetRun admission. Every durable Fleet-plan target requires one source fact. Team-source metadata stores the digest only; its owning Team is derived from the sole captured target, while a Shared Plan keeps its local plan key separate from the explicit Fleet repository scope. Shared Plan storage also hashes the exact stored bytes rather than decoded/re-encoded text.

Strict plan authors cannot grant arbitrary read-only host paths, host Docker socket/config paths, or absolute host argv. Tool requirements come from the resolved step itself rather than a duplicated plan capability list. Process-start-captured executor inventory **v6** provides the local strict backend with exact `runtime_inspector`, ordered `tool_search_paths`, and non-root `trusted_host_roots`; Enforcer checks required names are available, then local preparation freezes the exact observed bytes. Image-backed executor configuration remains digest-pinned; tool availability belongs to the executor environment rather than pipeline YAML. Managed runtimes are snapshotted before execution and mounted at Fleet-owned destinations such as `/managed/000`; original host topology never becomes sandbox semantics. Preparation uses Fleet-owned HOME/tmp/cache/config directories and a minimal environment rather than inheriting the oh-goon process environment.

Cross-run retained content separates physical identity from authorization. Physical objects may deduplicate globally by `tree-sha256-...` / `file-sha256-...`, but a hash is provenance only. A plan consumes an opaque repository-owned `ref-...`; reference and lease metadata are repository-scoped, and materialization proves the destination belongs to the same repository owner. Knowing another repository's content hash or retained reference cannot authorize reading its bytes. Managed state/content/run/preparation directories are owner-only (`0700`) and durable filename components use collision-resistant `storage_component()` identities instead of lossy sanitization.

Explicit dataflow still governs step state: workspaces, caches, imports, outputs, artifacts, egress, retries, deadlines, tools, and managed runtimes cross boundaries only through resolved declarations. Conservative denial defaults may remain when they only reduce authority and are visible in resolved intent. Executor placement may choose among equivalent eligible operator-configured executors before admission, but the resulting `FleetExecutorPlacement` freezes the exact backend evidence and capacity reservation as one fact. Strict admission embeds that placement unchanged; execution may revalidate it but cannot reconstruct, widen, or substitute it after admission.

Current Fleet Plan contract **v41** keeps capability-specific `requirements` explicit while repository validity stays owned by the execution target/admission boundary, retains the optional `trust_repository_code` switch, optional author resource caps with executor-capacity inheritance when omitted, and the `network: true` shorthand while preserving canonical typed egress after resolution. Executor inventory is **v6** and the current pre-live database schema is **epoch 78**; stale older state is recreated rather than decoded. External executor report remains **v3** and Enforcer receipt is **v18**. `enforcer.yml` is a deterministic projection of existing durable facts, not another persistent state model. There are no historical plan/inventory/database compatibility readers; oh-goon is pre-live, so old ambiguous authority shapes are deleted rather than emulated.

### Training-data source authority

Training data uses the existing source, workspace and retained-content owners.
[Captured Python projects and applications](../architecture/ADR_PYTHON_GOON_PROJECTS.md)
retain their original source contract.
There is no independent dataset selector, dataset registry or implicit download.
The following matrix describes current input authority; it does not qualify a
new training workflow or grant access from a pathname or content hash.

| Selected input | Current support and authority | Refresh and limits |
| --- | --- | --- |
| Data bundled in a captured Python Goon project | Supported as authored project resources. `capture_project_source` uses the existing source snapshot and Hashmarks identity; the original Step consumes the verified captured project. | Changing included data changes project and execution-lock identity. Capture again and explicitly prepare the new lock before a new Run. |
| Data bundled in an independent local or pinned Git application | Supported inside the exact selected application closure, through the same project capture and delivery. Git origin/ref/commit/subdirectory remain application provenance. | Local data changes require fresh capture/lock. Git data changes follow the application pin; only explicit application refresh advances that pin. Private Git credential qualification remains separate. |
| A file in the selected registered target repository | Available only through the already admitted repository workspace and effect policy. An input string or output declaration does not select, freeze or authorize that file. | A mutable workspace file is not automatically attributable to the captured application's digest. Use a captured resource for immutable dataset provenance, or separately prove the selected workspace source and policy. |
| A previous Run's retained artifact file | Explicit authorized artifact export is supported by the original Run/content-store owners. To use the result as project data, deliberately place it in a selected application/project and capture it for a new Run. | A file reference or SHA alone is not a dataset binding. Export rechecks recorded producer settlement, repository ownership and leased bytes; subsequent source admission is separate. |
| An exact retained cache tree | Supported by the existing strict CI/CD `cache.source.reference_id` contract, with repository-scoped `private_copy` or `read_only` access. | This is a retained tree, not an artifact-file selector. Original preflight/freezing and `/cache` isolation apply; direct/verification execution cannot claim these guarantees. |
| An arbitrary host file, remote URL or object-store dataset | No first-class dataset intake contract. Automatic acquisition, dataset credentials, freshness and arbitrary host mounts are deferred. | Do not interpret an ordinary string as an authorized source. A workload needing access must use its existing explicit environment/network/workspace authority or be refused by that selected policy. |

Trusted local code retains the caller's operating-system permissions. The absence
of a dataset input feature is not a sandbox denial of every host read or network
request. Stronger access refusal belongs to the selected isolation/effect owner;
neither application capture nor a typed string adds that guarantee.

#### Bounded capture and application validation

The project capture owner bounds each source closure to 64 MiB and 4,096 entries,
with a 96 MiB archive transport limit and the source graph's aggregate project
budget. Source copying is streamed. These are whole-project limits, not a new
per-dataset quota or a live disk-pressure controller. Data larger than the supported closure needs an explicitly
admitted existing input mechanism or remains deferred; do not put it into YAML
or scalar Step outputs to evade the boundary.

Authored resources participate even when Git ignores them. Declared generated
output locations and operational/cache paths remain excluded by the existing
source-membership policy, so do not use an excluded output path as input data.
Contained relative symlinks follow the existing source policy; escaping aliases,
links into excluded state and special filesystem entries are refused. Missing
required project authority, changed delivery or exceeded bounds cannot become a
successful capture. A copy I/O failure, including ENOSPC, propagates without
returning a successful capture identity; partial files cannot become admitted
source. Cleanup remains the original snapshot owner's responsibility.

Dataset schema, encoding, missing records and training/evaluation validation
belong to the independently runnable application. Capture treats a regular data
file as bytes; it does not parse CSV/JSON or certify those bytes as a valid
dataset. Applications should bound/stream their reads and return non-success
without printing private rows when their own validation fails.

#### Privacy and evidence

Selected dataset bytes intentionally travel in the private captured source and
its retained delivery. Public captured-project metadata contains hashes, counts
and application provenance; the compact execution lock contains identities and
pins rather than the dataset payload. Historical identity does not establish
current artifact availability. Repository-scoped content references remain
required even when identical bytes deduplicate across repositories.

Git ignore rules are not a privacy boundary. Independent application capture
refuses live dotenv state, but source capture is not a general secret scanner.
Keep acquisition credentials in existing explicit native/environment authority.
Dataset values deliberately printed by application code or placed in arguments,
scalar outputs or artifacts are not automatically redacted. Metadata-only
canary checks therefore cannot establish general application-output privacy.

Focused proof is owned by the existing
[project capture tests](../../backend/tests/unit/integrations/test_python_goon_projects.py),
[source snapshot tests](../../backend/tests/unit/integrations/test_source_snapshot.py),
[repository-scoped content tests](../../backend/tests/unit/integrations/test_fleet_content_store.py)
and [retained-cache isolation tests](../../backend/tests/unit/integrations/test_fleet_plan_cache_isolation.py).
Two separate installed-CLI training Runs, dataset-specific failure/privacy
handling, private remote credentials and deployment/host-specific isolation still
need their own actual qualification; these owner checks do not establish them.
