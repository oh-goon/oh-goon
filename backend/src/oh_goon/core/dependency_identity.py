from __future__ import annotations

import re

_PACKAGE_NORMALIZE = re.compile(r"[-_.]+")
_PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_valid_package_name(value: str) -> bool:
    """Return whether *value* is a valid package identity before normalization."""

    candidate = value.strip()
    return bool(candidate and _PACKAGE_PATTERN.fullmatch(candidate))


def normalize_package_name(value: str) -> str:
    """Return the canonical PEP-503-like package identity used by fleet facts."""

    candidate = value.strip()
    if not is_valid_package_name(candidate):
        raise ValueError("Dependency package must be a valid Python package name")
    return _PACKAGE_NORMALIZE.sub("-", candidate).lower()
