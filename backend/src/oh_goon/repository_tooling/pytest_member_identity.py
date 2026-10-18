"""Canonical Oh-Goon identity for one exact pytest member selector.

This module owns only the semantic identity formula shared by durable correctness
receipts and run-local execution-membership evidence.  Those stores retain separate
lifecycle and durability responsibilities.
"""
from __future__ import annotations

import hashlib
import json

PYTEST_MEMBER_IDENTITY_SCHEMA = "oh-goon.pytest-member/v1"


def pytest_member_identity(member: str) -> str:
    """Return the canonical local identity for one exact pytest member selector."""

    encoded = json.dumps(
        {"schema": PYTEST_MEMBER_IDENTITY_SCHEMA, "member": member},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
