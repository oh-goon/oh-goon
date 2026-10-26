# Examples

Use [the template first-run guide](../templates/README.md) to register a target and initialize a `run.yml`. These directories demonstrate distinct boundaries; the status below does not assert a successful external Run.

| Example | Classification | What it demonstrates |
| --- | --- | --- |
| [Agent providers](agent-providers/README.md) | Configuration example | Provider catalog validation; requests need a real selected endpoint. |
| [Local Skill authoring](agent-skill-authoring/README.md) | Preview-only fixture | Typed local Skill/source capture; its `opencode` declaration is not executable. |
| [Pinned cookbook review](agent-skill-cookbook/README.md) | Runnable with prerequisites | Canonical `run.yml`, remote Skill registry and installed native agent/profile. |
| [Frozen local search](agent-skill-local-search/README.md) | Runnable with prerequisites | Recipient-free captured-source search through the CLI, Fleet and Game Tape. |
| [Offline Skill resources](agent-skill-offline/README.md) | Preview-only fixture | Local resource capture and relocation; scripts travel as data. |
| [Review–repair](agentic-review-repair-loop/README.md) | Runnable with prerequisites | Provider or native workflow, explicit phase composition and bounded repair. |
| [Standard Atomic](standard-atomic/README.md) | Runnable with prerequisites | Shell execution through the CLI, Fleet and Game Tape; separate native failure/cancellation checks. |
| [Git-selected orders TaskFlow](airflow-taskflow-showcase/README.md) | Runnable with prerequisites | Ordinary verification against an empty target, pinned Git source and fresh-process Game Tape; the canonical Python package also has a separate Airflow adapter. |
| [Python Goon project from Git](python-goon-project/README.md) | Runnable with prerequisites | A complete example's explicit Plan and local Python project captured at one pinned Git revision. |
| [Independent application](independent-application/README.md) | Runnable with prerequisites | Separately maintained Atomic Goon selecting local or pinned Git application bytes, native commands and original Run readback; actual registered target required. |

The per-domain Plan READMEs contain small configurations for Python, OCI, Kubernetes, security, ML and advanced teaching workflows. Native tools and services retain their configuration and execution authority. Example infrastructure addresses and target IDs must be replaced deliberately; configuration inspection is not an external qualification receipt.
