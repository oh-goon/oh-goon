# agents/verification

Run the project's explicitly selected verification executable.

Required input: `verification_command`, a canonical repository-relative path such as `scripts/verification`. The Goon executes `./scripts/verification`; make the script executable. Native tools own check semantics and exit codes. For arguments or several checks, select a project-owned Goon instead. No universal test command is assumed.
