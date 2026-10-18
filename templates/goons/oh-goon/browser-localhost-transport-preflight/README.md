# oh-goon/browser-localhost-transport-preflight

Fail closed before browser qualification unless the same Node TCP stack used by Playwright can connect to a live IPv4 localhost endpoint and promptly observe `ECONNREFUSED` after that exact endpoint closes.

The probe allocates an OS-selected ephemeral port. It does not hard-code Vite ports, inspect WSL routing internals, or require a particular WSL networking mode. WSL mirrored networking is diagnostic guidance only when the observable localhost transport contract fails.

This proves **Node localhost TCP connect/closed-endpoint refusal**, not browser
engine launch, harness listing or runtime port ownership. The other browser
preflights invoke the transport check through native npm prerequisites, but this
remains an independently identifiable failure boundary. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
