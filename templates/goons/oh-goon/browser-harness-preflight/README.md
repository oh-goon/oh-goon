# oh-goon/browser-harness-preflight

Load the Playwright configuration and certification specs without running browser journeys, proving the harness is discoverable before runtime execution.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **configuration/spec discovery** without browser journeys; it does
not certify engine launch or clean runtime listeners. The native npm target
owns its transport prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
