# oh-goon/browser-runtime-preflight

Require clean browser/backend runtime port ownership and the canonical non-reuse browser runtime boundary.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **runtime port ownership and server non-reuse**, not browser engine
installation or Playwright spec discovery. Browser certification's native Make
targets own their runtime-preflight prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
