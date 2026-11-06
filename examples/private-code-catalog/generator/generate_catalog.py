"""Render reviewed metadata into Backstage YAML; never inspect repository source.

This independent example application is intentionally not a repository intelligence
provider. Hashmarks, Git and human review establish the input facts elsewhere.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

INPUT = Path("catalog-input.json")
OUTPUT = Path("catalog-info.yaml")
REQUIRED = frozenset({"name", "description", "owner", "type", "lifecycle", "languages"})
TYPES = frozenset({"service", "website", "library"})
LIFECYCLES = frozenset({"experimental", "production", "deprecated"})
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
TAG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
OWNER = re.compile(r"group:default/[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _valid_string(value: object, *, label: str, limit: int = 240) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must be one nonempty, bounded printable string")
    return value


def render_catalog(payload: object) -> str:
    """Use only a reviewed allowlist, not code snippets or guessed ownership."""
    if not isinstance(payload, dict) or set(payload) != REQUIRED:
        raise ValueError("metadata must contain exactly the six approved fields")

    name = _valid_string(payload["name"], label="name", limit=63)
    if not SLUG.fullmatch(name):
        raise ValueError("name is not a Backstage-safe slug")
    description = _valid_string(payload["description"], label="description")
    owner = _valid_string(payload["owner"], label="owner", limit=80)
    if not OWNER.fullmatch(owner):
        raise ValueError("owner must be an explicitly approved group:default/slug")

    kind = payload["type"]
    lifecycle = payload["lifecycle"]
    if not isinstance(kind, str) or kind not in TYPES:
        raise ValueError("type must be an approved Backstage component type")
    if not isinstance(lifecycle, str) or lifecycle not in LIFECYCLES:
        raise ValueError("lifecycle must be an approved Backstage lifecycle")

    languages = payload["languages"]
    if not isinstance(languages, list) or not (1 <= len(languages) <= 8):
        raise ValueError("languages must be a nonempty bounded list")
    if any(
        not isinstance(language, str)
        or len(language) > 40
        or not TAG.fullmatch(language)
        for language in languages
    ) or len(set(languages)) != len(languages):
        raise ValueError("languages must contain unique safe tags")

    # JSON string quoting is also valid YAML scalar quoting. No YAML parser,
    # network client, repository walker, or model is required for this renderer.
    lines = [
        "apiVersion: backstage.io/v1alpha1",
        "kind: Component",
        "metadata:",
        f"  name: {json.dumps(name)}",
        f"  description: {json.dumps(description, ensure_ascii=True)}",
        "  tags:",
        *(f"  - {json.dumps(language)}" for language in languages),
        "spec:",
        f"  type: {json.dumps(kind)}",
        f"  lifecycle: {json.dumps(lifecycle)}",
        f"  owner: {json.dumps(owner)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        with INPUT.open("rb") as source:
            raw = source.read(16_384 + 1)
        if len(raw) > 16_384:
            raise ValueError("approved metadata exceeds size budget")
        candidate = render_catalog(json.loads(raw))
        # Exclusive creation never overwrites a prior output, and the fixed
        # relative path keeps artifact publication inside the Step workspace.
        with OUTPUT.open("x", encoding="utf-8", newline="\n") as output:
            output.write(candidate)
    except (OSError, ValueError, TypeError) as error:
        print(f"Catalog candidate refused: {error}", file=sys.stderr)
        return 2
    print("Created local catalog-info.yaml from explicitly reviewed metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
