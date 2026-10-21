# agents/native-review

Run one [read-only native review](../../../goons/agents/native-review/README.md).
Public inputs are `repo`, `skill`, `goal` and `acceptance`; the imported Goon owns
their defaults and typed result contract. A finding or incomplete review remains
an Agent claim and grants no write or verification authority.

Example values for your registered repository and local Skill package:

```yaml
inputs:
  repo: local:repo
  skill: local:skills/single-source-of-truth-review
```

Copy this Plan through `oh-goon templates show agents/native-review --kind plan`.
Use the same inputs, authorized source roots, acceptance and native selection
when locking and running. Select an isolated Bubblewrap profile with managed
inference. The default `isolated-managed` profile uses the catalog's explicit
`default_model`; a `local` profile cannot enforce the required broker boundary.
See [native setup and common CLI flags](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent).

This single review needs no phased repair budget. Pi and OpenCode can consume
the same authored Plan through their respective selected native implementations.
