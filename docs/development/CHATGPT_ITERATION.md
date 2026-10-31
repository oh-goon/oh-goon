# ChatGPT / constrained-agent development loop

Use this guide to resume one cohesive Oh-Goon contribution after a chat reset.
The repository and its existing check owners supply authority; chat memory and
handoff notes help locate it. Contributor policy remains in
[AGENTS.md](../../AGENTS.md). This guide adds no runner, gate or certificate.

## Establish the current task

1. Read root and affected owner guidance. Inspect the working tree, branch and
   exact commit before editing; preserve existing uncommitted work. Fetch the
   current base and PR head when available, and reconcile differences before
   relying on an older handoff. If remote state is unavailable, report that gap.
2. Find the existing PR for the user-approved goal. Continue its coherent slice
   where practical; do not combine unrelated work just to reduce PR count.
3. Follow the [QA baseline](REAL_LIFECYCLE_QA.md#before-implementing-a-backlog-item)
   to distinguish implemented behavior from missing qualification. An open issue
   is not evidence that a mechanism needs rebuilding. Locate the current owner,
   existing regression and specific missing proof before choosing an edit.

## Iterate through existing owners

Use the [focused edit loop](../DEVELOPMENT.md#focused-edit-loop) for the concrete
selection and next required check. Diagnose its first failure before broadening.
Keep a reproducing regression when it adds defect-detection power, following
[Test quality](TEST_QUALITY.md); general development/review methods remain in
[agentsCookbook](../AGENT_SKILLS.md).

Initialize dependencies when needed through the existing `make init` frontdoor;
reuse valid environments during edits. Lock authoring and generated-contract
changes follow their existing owners, not a per-session refresh ritual.

The [local test resource budget](../DEVELOPMENT.md#local-test-resource-budget)
already covers supported Make gates. Direct uv/npm tests do not acquire that
scope automatically. Run major gates sequentially when measuring workstation
load; record the actual command, budget, host and outcome. Investigate a measured
resource defect in its existing owner rather than introducing another runner.

Start known-large surfaces with [tool-session qualification](TOOL_SESSION_QUALIFICATION.md)
or the existing resume-safe batch owner. Reuse only receipts accepted by that
owner for the exact source, selection and toolchain; an interrupted or timed-out
unit remains unverified. Follow the root relevant-proof policy for the final slice. Do not run complete
unrelated owners locally before a PR; they run daily. Focused PASS does not
establish merge, APP or release completion.

Publish coherent updates using the existing
[hosted-check contract](../CI_CD_CONTRACT.md#repository-checks-on-github).
Draft feedback is the cheap iteration lane; ready PRs require the admitted fast
proof for the effective candidate/base. Necessary critical regressions remain
required; unrelated complete owners run daily. Diagnose a failing
hosted check from its native failure evidence before changing code or retrying.

## Leave a usable handoff

Preserve work with normal Git commits and synchronization within the user's
authorized scope. When publication to an existing PR/issue or selected planning
task is authorized, place a compact handoff there; otherwise return it to the
user. This guide grants no permission to send messages or publish private data.
Do not put transcripts, status files, logs or recovery transport in source.

```text
Repository / base / PR: <repository, verified base SHA, existing PR URL>
Branch / head / working tree: <exact SHA; committed and uncommitted changes>
Goal / constraints: <approved scope and boundaries to preserve>
Owner / contract: <affected owners and current contract links>
Completed: <durably saved changes and where they can be recovered>
Next: <first concrete action, then remaining work>
Evidence: <commands, outcomes, source identity including any dirty changes>
Escalation / gaps: <required gates; FAIL, NOT_RUN, BLOCKED or interrupted>
Readiness: <draft or ready; hosted checks for the current head>
```

Report the six completion fields from root `AGENTS.md`; keep bounded evidence
with its existing owner and omit credentials, private paths and raw transcripts.
On restart, independently verify the handoff against source, working-tree changes,
PR head and check results. Old green CI and a list of tests are not current PASS.
Use [durable continuation](DURABLE_CONTINUATION.md) only for an explicitly
archive-managed campaign or requested portable checkpoint.
