# Examples

Use [the template first-run guide](../templates/README.md) to register a target and initialize a `run.yml`. These directories demonstrate distinct boundaries; the status below does not assert a successful external Run.

| Example | Classification | What it demonstrates |
| --- | --- | --- |
| [Agent providers](agent-providers/README.md) | Configuration example | Provider catalog validation; requests need a real selected endpoint. |
| [Local Skill authoring](agent-skill-authoring/README.md) | Preview-only fixture | Typed local Skill/source capture; its `opencode` declaration is not executable. |
| [Pinned cookbook review](agent-skill-cookbook/README.md) | Runnable with prerequisites | Canonical `run.yml`, remote Skill registry and installed native agent/profile. |
| [Frozen local search](agent-skill-local-search/README.md) | Runnable with prerequisites | Recipient-free captured-source search through the CLI, Fleet and Game Tape. |
| [Private code catalog](private-code-catalog/README.md) | Runnable with prerequisites (synthetic) | Local Hashmarks/Git discovery, reviewed team metadata and an Oh-Goon-retained Backstage catalog artifact; no provider step. |
| [Offline Skill resources](agent-skill-offline/README.md) | Preview-only fixture | Local resource capture and relocation; scripts travel as data. |
| [Review–repair](agentic-review-repair-loop/README.md) | Runnable with prerequisites | Provider or native workflow, explicit phase composition and bounded repair. |
| [Standard Atomic](standard-atomic/README.md) | Runnable with prerequisites | Shell execution through the CLI, Fleet and Game Tape; separate native failure/cancellation checks. |
| [Git-selected orders TaskFlow](airflow-taskflow-showcase/README.md) | Runnable with prerequisites | Native development and ordered execution over a real application repository, pinned Git source, retained JSON export and fresh-process history; the same Python package has a separate Airflow adapter. |
| [Python CI](python-ci/README.md) | Runnable with prerequisites | Native lint, tests and wheel building through the public CLI; GitHub Actions/GitLab setup, retained wheel export and portable Run results. |
| [Python Goon project from Git](python-goon-project/README.md) | Runnable with prerequisites | A complete example's explicit Plan and local Python project captured at one pinned Git revision. |
| [Independent application](independent-application/README.md) | Runnable with prerequisites | Separately maintained Atomic Goon selecting local or pinned Git application bytes, native commands and original Run readback; actual registered target required. |

The per-domain Plan READMEs contain small configurations for Python, OCI, Kubernetes, security, ML and advanced teaching workflows. Native tools and services retain their command/session semantics beneath the admitted Step; they do not own Oh-Goon Run admission, recovery or results. Example infrastructure addresses and target IDs must be replaced deliberately; configuration inspection is not an external qualification receipt.

Every runnable Oh-Goon example uses the same public FleetRun lifecycle. The
[execution boundary](../docs/execution-boundary.md) distinguishes current
thread-based Run submission from HUG-180/HUG-181's required claimed-worker
custody. Neither a standalone application development command, external Airflow
deployment nor a low-level test probe establishes that future Run guarantee or
authorizes a special example/ML/CI runner.
