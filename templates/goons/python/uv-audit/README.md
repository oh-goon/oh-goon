# python/uv-audit

Audit the repository's locked Python resolution directly with `uv audit`.

The command's stdout, stderr, exit status, and execution details already belong to the Run and Game Tape, so the Goon does not wrap uv in Python just to duplicate that evidence into JSON bookkeeping files.

**Network authority:** `host_unrestricted` is an intentional outbound exception:
native `uv audit` may consult remote advisory data even with a frozen lock and
Python downloads disabled. The advisory service/redirect endpoints are not a
verified fixed hostname set, so a restrictive allowlist is not yet justified.
Operators must explicitly accept the host-network exposure. See the
[canonical template network authority](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md).
