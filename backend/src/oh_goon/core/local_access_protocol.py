"""Identity-only protocol vocabulary for process-local API access transport.

This module owns only the environment-key identity. Token generation, storage,
validation, launch ownership, and authorization decisions remain with their
existing owners.
"""

LOCAL_ACCESS_ENV = "OH_GOON_LOCAL_ACCESS_TOKEN"
