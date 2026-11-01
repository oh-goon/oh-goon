# Contributing to OH-Goon

OH-Goon welcomes focused, evidence-backed improvements. This repository is active development source; the current source, tests, and owning documentation define its contracts.

## Start with ownership

Before changing code or documentation:

1. Read the [documentation map](README.md) and the repository guardrails in [`../AGENTS.md`](../AGENTS.md).
2. Read the [development guide](DEVELOPMENT.md) and the README or `AGENTS.md` owned by the area you will change.
3. Inspect the current implementation and its tests before deciding that work is missing. An open issue or qualification task is not evidence that the underlying mechanism needs to be rebuilt.
4. Keep the change inside one coherent owner or an explicitly described cross-owner contract.

## Preserve the product boundaries

Contributions must preserve these repository-wide rules:

- Repository and Shared Run inputs use one admission, execution, Journal, recovery, and Game Tape lifecycle. Do not add a second executor, scheduler, worker path, result store, or retry authority.
- Hashmarks is the required repository-intelligence dependency. Consume it through the canonical `backend/src/oh_goon/integrations/hashmarks/` boundary rather than creating another repository-identity, impact, or verification-selection implementation.
- Game Tape and Scout are read-only projections over canonical execution evidence. Presentation and observability must not become execution authority.
- Native tools own their own semantics. OH-Goon may select, bound, invoke, and report native operations, but must not reimplement their resolver, cache, retry, environment, or lifecycle models.
- Agents Cookbook owns reusable review methodology, and EvalShot owns evaluation suites, scoring, and reports. Do not create duplicate skill catalogs or evaluation authorities in OH-Goon.
- Compatibility behavior must be justified by a current support obligation. Historical names or code paths are not sufficient reasons to retain parallel behavior.

The detailed contracts remain in the linked architecture and development documents; this file is an entry point, not a second architecture authority.

## Prepare a source checkout

Use Linux or WSL with the tool versions documented by the repository. Run `make init`, followed by `make check`.

For CLI-only work that does not need the frontend, run `make backend-install`, then `uv run --offline --frozen --no-sync --project backend oh-goon --help`.

`make init` consumes the committed native locks. Use `make sync` only for an intentional dependency-authoring change.

## Use the focused verification loop

Run the smallest real proof while editing, then use the repository front doors. `make check` is the ordinary focused check. Before merge, run `VERIFY_BASE=origin/main make merge-check` against the intended base.

Follow the selector's reported reason when verification is unqualified. Do not replace a refused or incomplete selection with an empty green result, and do not describe focused proof as complete release qualification. Use broader owner, daily, browser, APP, or release checks only when their scope is actually required.

Generated contracts must be updated through their existing generator and checked with `make check-generated`. Dependency changes must preserve `backend/pyproject.toml` as intent and `backend/uv.lock` as exact native resolution authority.

## Open a pull request

Pull requests should be ready for review and contain one coherent change. Include:

- the problem and the owning contract;
- the implementation boundary and any cross-project ownership impact;
- exact validation commands and observed outcomes;
- evidence limits, skipped qualification, or remaining follow-up work;
- the related issue when one exists.

Do not commit credentials, private registry details, host-absolute paths, local run state, generated scratch data, or sensitive logs. Keep public documentation factual and project-focused rather than promotional.

Security vulnerabilities must follow [SECURITY.md](SECURITY.md) instead of the public issue or pull-request workflow.
