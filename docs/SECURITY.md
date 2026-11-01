# Security Policy

## Supported scope

OH-Goon is an active development repository. Security reports should target the current default branch or the latest explicitly published release when one exists.

Relevant security boundaries include workflow admission and execution, process supervision and cancellation, path and source isolation, local access tokens, credentials and secret handling, API authorization, persistence and recovery, templates and generated artifacts, dependency acquisition, and release-package integrity.

## Report vulnerabilities privately

Do **not** disclose vulnerability details in a public issue, pull request, discussion, log, or screenshot.

Use GitHub's **Security** tab for `oh-goon/oh-goon` and select **Report a vulnerability** to create a private report. When GitHub does not expose that private form, open a public issue titled `Private security contact requested` and include no technical details, reproduction steps, logs, paths, identifiers, or secrets. A maintainer can then establish a private channel before any sensitive information is shared.

Regular bugs, feature requests, documentation problems, and non-sensitive hardening suggestions belong in the public issue tracker.

## What to include

A useful private report contains:

- the affected commit, version, or release artifact;
- the environment and required preconditions;
- the security boundary that is crossed and the practical impact;
- a minimal, sanitized reproduction or evidence trail;
- whether the issue affects default behavior or an explicitly selected high-security mode;
- any known mitigation or safe workaround.

Never include real credentials, provider tokens, private repository source, personal data, or unrelated customer information. Replace sensitive values with synthetic examples and describe only the evidence required to reproduce the boundary failure.

## Safe research

Test only systems and repositories you own or are explicitly authorized to assess. Do not use a report as authorization to access third-party systems, retain persistence, exfiltrate data, disrupt service, or run destructive or resource-exhaustion tests.

OH-Goon distinguishes demonstrated security impact from theoretical concerns and ordinary product defects. A report may still be valuable when some evidence is unavailable, but unknowns and assumptions must remain explicit.

## Handling and disclosure

Reports are triaged according to demonstrated impact, reachability, and reproducibility. Maintainers may request additional bounded evidence and will coordinate disclosure when a correction or mitigation is ready. Submission does not establish a public-disclosure date or a fixed response-time commitment.
