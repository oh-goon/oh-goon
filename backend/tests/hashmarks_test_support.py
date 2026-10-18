"""Native Hashmarks test identities derived from the materialized dependency.

Tests use a synthetic artifact provenance value because artifact location is caller
provenance, not a copied Oh-Goon release authority. Producer compatibility is
bound to the actually loaded Hashmarks implementation.
"""
from oh_goon.integrations.hashmarks.runtime_contract import (
    loaded_hashmarks_producer_identity,
    loaded_hashmarks_version,
)

TEST_HASHMARKS_ARTIFACT_IDENTITY = "sha256:" + "a" * 64
TEST_HASHMARKS_VERSION = loaded_hashmarks_version()
TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY = loaded_hashmarks_producer_identity()
