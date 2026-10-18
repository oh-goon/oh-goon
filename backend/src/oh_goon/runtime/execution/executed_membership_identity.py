"""Canonical identity for executed verification membership."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

EXECUTED_VERIFICATION_MEMBERSHIP_SCHEMA = (
    "oh-goon.executed-verification-membership/v1"
)


def executed_verification_membership_identity(members: Iterable[str]) -> str:
    """Return the canonical identity for the exact executed member set."""
    payload = {
        "schema": EXECUTED_VERIFICATION_MEMBERSHIP_SCHEMA,
        "members": sorted(members),
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
