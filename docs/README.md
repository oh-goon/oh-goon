# Documentation

Start with the current operator and developer guides. The linked owners define behavior and supported commands; this page is a navigation map.

## Operator guides

1. [Quickstart](QUICKSTART.md), [GUI guide](GETTING_STARTED.md), and [Troubleshooting](TROUBLESHOOTING.md) — initialize, run, and investigate a source checkout.
2. [CI/CD contract](CI_CD_CONTRACT.md) and [Automation model](AUTOMATION_MODEL.md) — repository runs, Shared Runs, and Schedules.
3. [Observability](OBSERVABILITY.md) and [Game Tape](development/GAME_TAPE.md) — inspect recorded Run outcomes, logs, and outputs.
4. [Safety](SAFETY.md), [Policy](POLICY.md), and [Privacy](PRIVACY.md) — operator boundaries and local data handling.
5. [Agent Skills](AGENT_SKILLS.md) — captured Skills, bounded local model work, failed-test feedback, and caller-authorized repair/retest loops.
6. [Agent providers](AGENT_PROVIDERS.md) — configure local or remote model endpoints and make explicit text requests without installing provider servers.

## Developer guides

1. [Development](DEVELOPMENT.md), [AGENTS.md](../AGENTS.md), [Architecture](ARCHITECTURE.md), and [SSOT](development/SSOT.md) — contribution workflow and ownership. Use the [backend](../backend/README.md), [frontend](../frontend/README.md), and [repository tooling](../scripts/README.md) maps to find an implementation.
2. [Execution first](architecture/ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md) and [replaceable implementations](architecture/ADR_CANONICAL_OWNER_REPLACEABLE_IMPLEMENTATIONS.md) — product direction and canonical responsibility boundaries.
3. [Fail-before-work](development/FAIL_BEFORE_WORK.md) and [Native tool authority](development/NATIVE_TOOL_AUTHORITY.md) — resolve and validate once, then consume the original native tool contract.
4. [Test quality](development/TEST_QUALITY.md) and [Generated files](development/GENERATED_FILES.md) — focused regression proof and the backend-to-frontend contract generation path.
5. [Template developer journey](../templates/README.md), [Template input resolution](development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), and [read-only template inspection](development/TEMPLATE_INPUT_INSPECTION.md) — author and inspect executable Plans and Goons.

## Execution contracts

Use topic guides for current behavior. Specialized deployment and high-security contracts apply when selected; they are not additional startup steps for ordinary Linux/WSL work.

- [Architecture](ARCHITECTURE.md) and [current architecture decisions](development/ARCHITECTURE_DECISIONS.md) — execution environments, admission, SQLite transactions, Journal and recovery ownership.
- [Native engine](development/ENGINE.md), [execution supervisor](development/EXECUTION_SUPERVISOR.md), and [launch ownership](development/LAUNCH_OWNERSHIP.md) — command execution, cancellation and local process lifetime.
- [Tool authority](development/FROZEN_TOOL_AUTHORITY.md) and [Enforcer receipts](development/ENFORCER_RECEIPTS.md) — selected tool permissions and comparison of recorded execution facts.
- [Hashmarks execution handoff](development/HASHMARKS_EXECUTION_LAYER.md) — admitted repository evidence, exact verification membership and Oh-Goon execution results.
- [Agent runtime ownership](development/AGENT_RUNTIME_OWNERS.md) — live model/Agent call paths, outer workflow ownership, current evidence limits and the contract questions for replaceable coding agents.

Retained ADRs explain architectural choices and their original evidence limits. Checkpoint status and historical test results in an ADR describe that source; they do not establish current qualification or select the next development task.

## Template and maintainer references

- [Artifact retention](development/TEMPLATE_ARTIFACT_RETENTION.md) and [network authority](development/TEMPLATE_NETWORK_AUTHORITY.md) — explicit output retention and egress contracts.
- [Self-hosting boundaries](development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) and [catalog determinism](development/TEMPLATE_CATALOG_DETERMINISM.md) — repository verification templates and exact template source identity.
- [Canonical workflow](development/CANONICAL_WORKFLOW.md), [APP test run](development/APP_TEST_RUN.md), and [Package run](development/PACKAGE_RUN.md) — supported commands for explicit certification and release work.
- [Durable continuation](development/DURABLE_CONTINUATION.md) — recovery and portable working-state handoff when that workflow is needed.

Hashmarks-derived navigation helps locate repository evidence; it does not define Oh-Goon operating commands, admission, execution, or release policy. See the [Hashmarks integration boundary](architecture/hashmarks-oh-goon-authority-boundary.md). Do not mirror `make help`; link the current command owners.
