# oh-goon/browser-engine-preflight

Launch the configured Playwright browser engines and report missing host dependencies before browser certification.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **engine launch and host libraries**, not harness discovery, clean
runtime port ownership or the basic Node localhost TCP boundary. All four
preflights retain distinct result/evidence identities. The native npm target
owns its transport prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
