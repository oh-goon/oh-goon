# python/test

Run pytest with coverage from the repository's locked `dev` dependency group and retain the real test reports as evidence.

Only the meaningful test input and produced artifacts are declared. Runtime logs, exit status, tool execution, cache lineage, and lock enforcement are already part of the Run and Game Tape.

**Artifact retention:** Coverage and JUnit reports are both `evidence`; the runtime already records test status and logs. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
