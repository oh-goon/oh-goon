# agents/native-coding

Run the [native coding Goon](../../../goons/agents/native-coding/README.md), then
the project's existing [verification Goon](../../../goons/agents/verification/README.md).
Written order is preserved. Verification keeps the same selected native profile
and execution boundary after the Agent edits the workspace.

Public inputs are `repo`, `skill`, `goal`, `acceptance` and
`verification_command`. Select an executable repository-relative script such as
`scripts/verify`; it owns the native check commands and their configuration.
There is no universal test command or inferred verification result.

Example values for your registered repository and local Skill package:

```yaml
inputs:
  repo: local:repo
  skill: local:skills/repair
  verification_command: check
```

Copy this Plan through `oh-goon templates show agents/native-coding --kind plan`.
Lock and run it with the same caller inputs, authorized Agent source roots,
acceptance and explicit native selection flags. See [native setup and common CLI flags](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent).
Select an isolated profile with managed inference; `isolated-managed` is the
native default and uses the catalog's explicit `default_model`. The selected
Agent implementation can change without rewriting this Plan; refresh its lock
when changing the selection.
