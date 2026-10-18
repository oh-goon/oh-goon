# agents/regression

Run the project's explicitly selected regression executable.

Required input: `regression_command`, a canonical repository-relative path such as `scripts/regression`. The Goon executes `./scripts/regression`; make the script executable. Native tools own check semantics and exit codes. For arguments or several checks, select a project-owned Goon instead. No universal test command is assumed.
