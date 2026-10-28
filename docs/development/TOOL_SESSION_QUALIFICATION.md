# Tool-session qualification

Long development and immutable-artifact qualification must not depend on one monolithic
shell invocation surviving an outer IDE/agent/tool execution window.

`make tool-session-qualification` executes at most one authoritative owner unit and then
returns. Re-run the same target until it prints `TOOL SESSION QUALIFICATION COMPLETE`.
`make tool-session-qualification-status` reports the current exact-identity progress.

Both Make entrypoints use `uv run --offline --frozen --no-sync --project backend`
to select the prepared project environment. The runner records that toolchain,
then invokes each Python module with the same `sys.executable`. Project and
interpreter selection happen once; unit commands show the selected interpreter
and consume the explicit backend pytest configuration.

This follows the [Python launch boundary](NATIVE_TOOL_AUTHORITY.md): uv selects,
prepares, or enters the project environment; child processes reuse its resolved
interpreter. Host-only diagnostics remain separate from authoritative checks.

PASS receipts are reusable only when the canonical packageable source identity, the exact
ordered qualification plan, and the Python toolchain identity are unchanged. A failed,
interrupted, or timed-out unit receives no PASS receipt and is retried on the next call.
The helper does not create certification truth: each unit delegates to existing pytest,
architecture, generated-contract, release-identity, Goon-admission, or compile owners.
Large pytest surfaces remain delegated to `oh-goon-test-batches --resume`; already-bounded owner files run as one owner unit to avoid unnecessary pytest process churn.

This is the required qualification style for constrained tool sessions. Do not first run
a known-large combined command and split it only after an outer timeout; start with the
resume-safe owner batches instead.
