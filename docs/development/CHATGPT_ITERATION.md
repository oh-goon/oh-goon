# ChatGPT / constrained-agent development loop

This document preserves the practical iteration contract for ChatGPT and other
short-lived agent/tool sessions. It is **contributor guidance**, not a new
runtime path, test gate, state database, or certification authority. Root
[`AGENTS.md`](../../AGENTS.md) owns repository-wide policy;
[`TOOL_SESSION_QUALIFICATION.md`](TOOL_SESSION_QUALIFICATION.md) owns bounded
qualification; [`CI_CD_CONTRACT.md`](../CI_CD_CONTRACT.md) owns hosted checks.

## Begin each session from repository authority

1. Read root `AGENTS.md` and the affected owner-local guidance. Fetch the
   current `main` and existing PR branch/head before treating prior chat context
   as current. ChatGPT memory, an old handoff, or a Linear issue is **a lead**,
   not source, implementation, test, or merge truth.
2. Check for an existing active PR covering the same owner and intent. Continue
   that PR when practical; rebase or reconcile against current `main`. Prefer
   one cohesive, reviewable batch over one PR per tiny fix. Do not mix unrelated
   changes only to reduce PR count.
3. Capture the intended contract, affected owner, explicit out-of-scope
   boundaries, and focused negative/positive checks before editing. For an
   observed bug, establish a failing regression when practical; removing a
   failing test is not a repair unless the requirement is obsolete, incorrect,
   or demonstrably covered elsewhere.
4. Respect the product boundary: Hashmarks supplies repository evidence;
   the selected coding agent reasons/edits; Oh-Goon owns admission, execution,
   recovery, and verification. Do not invent another runner, scheduler,
   repository graph, harness session store, or generated-contract authority.

## Fast feedback without reducing required proof

- Develop in the smallest affected owner. Run its focused test first and
  diagnose the **first real failure**. Fix the owner, extend a regression, and
  rerun that focused test before escalating according to the root
  `AGENTS.md` matrix. For backend edits, use the existing
  `uv run --offline --frozen --no-sync pytest ...` command after `make init`.
- Keep the local Python/Node environments and native package caches when
  source/dependency authority permits. Do not rerun `make init`, regenerate
  contracts, rebuild wheels, or re-resolve locks on every edit. Execute
  `make sync` only for an intentional dependency-intent change.
- Test execution must remain resource-bounded on a developer/WSL host:
  avoid overlapping heavyweight pytest/process suites or unconstrained
  concurrent tool sessions. Use the existing test owners/batch runner, and
  verify actual child-process/cgroup limits rather than assuming a parent
  setting constrains descendants. A machine becoming CPU-saturated is a
  performance defect to measure, not a reason to skip coverage.
- When the outer ChatGPT/tool call may expire, **start** known-large suites
  with `make tool-session-qualification` or existing
  `oh-goon-test-batches --resume`. Do not first launch a known-monolithic
  command and split only after it times out. Run
  `make tool-session-qualification-status` for progress. Reuse only exact
  source/plan/toolchain-bound PASS receipts, not stale or partial output.
  A completed small owner test may run directly to avoid pytest startup churn.
- Keep `make check`/owner gates, `make verify` diagnostics,
  `make certify` and release proof separate under their documented authority.
  A diagnostic, older commit's green CI, truncated output, timed-out test,
  or unfinished batch is **not** final-source PASS.

## Publishing and reviewing one coherent batch

1. Group related source fix, regressions, and current-owner docs into the
   existing PR where possible. Keep draft PR feedback cheap; publish fewer,
   meaningful head updates rather than repeatedly triggering broad CI.
2. Locally run the focused checks and the union of required escalation rows.
   Do not classify a regression as `slow` merely because it is expensive;
   retain process, isolation, cancellation, and negative-boundary proof.
3. Mark the PR ready for review only when its intended slice is reviewable.
   All seven hosted routine check names belong to ready PR/current head.
   Rerun only failed jobs for an unchanged commit; changed head requires fresh
   evidence. Green CI is not proof that GitHub branch protection is active.
4. Review tests that were removed or relaxed: identify the requirement they
   enforced and equivalent observable proof after the change. Review source
   behavior as well as test changes. General review methodology belongs in
   agentsCookbook; keep only Oh-Goon-specific decisions here.
5. Report the six completion fields required by root `AGENTS.md`.
   Name every gate that was NOT_RUN, BLOCKED, or interrupted rather than
   projecting success from partial evidence.

## Portable continuation across chat resets

Before losing a chat session, publish the durable state to the **existing**
GitHub PR/issue or its linked review discussion (and to the tracked Linear
task when that is the selected planning owner). Do not commit transcript
history, chat memory dumps, private paths, credentials, archive checkpoints,
or phase/status files to the source tree. A useful handoff includes:

- Repository, branch, current exact commit SHA and PR URL; base `main` SHA
  when available.
- User-approved goal, constraints and explicit non-goals; implementation
  owners touched; related issues/requirements.
- Changes already committed versus remaining work, with the first concrete
  next action. State whether PR is draft or ready.
- Focused test command/results, escalation check names and exact commit
  bound to each result, known failures and unverified gaps.
- Relevant retained bounded evidence/receipts with their identity and
  freshness; whether native qualification remains outstanding.

On restart, read that persisted handoff **and independently verify** branch,
source, PR head, CI and required contract state. ChatGPT memory can help find
a prior decision but must never silently override current code. Reuse the
same run/batch receipts only when the qualification identity still matches.
For explicitly archive-managed campaigns, use
[`DURABLE_CONTINUATION.md`](DURABLE_CONTINUATION.md), not this ordinary
GitHub-based handoff, for checkpoint authority.

## What not to optimize away

Never turn a shorter chat, faster draft job, cached receipt, fixture-only test,
or passing test count into an execution or certification claim. Maintain
correctness, source identity, permissions, native ownership, durable recovery,
negative tests, and exact verification membership. Improvement proposals such
as global CPU budgets or reduced repeated pytest collection require measured
baseline, an existing-owner implementation, and regression evidence; this
guide does not assert they have been implemented.
