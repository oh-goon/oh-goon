"""The shared, source-free Backstage formatter used by preview and native Run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, get_args

CatalogComponentType = Literal["service", "website", "library"]
CatalogLifecycle = Literal["experimental", "production", "deprecated"]
MAX_METADATA_BYTES = 16_384
_FIELDS = frozenset({"name", "description", "owner", "type", "lifecycle", "languages"})
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_OWNER = re.compile(
    r"(?:group|user):(?P<namespace>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)\Z"
)


def _text(value: object, *, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} must be one nonempty, bounded printable string")
    return value


@dataclass(frozen=True, slots=True)
class CatalogCandidate:
    """Six reviewed values; field allowlisting never redacts their contents."""

    name: str
    description: str
    owner: str
    type: CatalogComponentType
    lifecycle: CatalogLifecycle
    languages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _SLUG.fullmatch(_text(self.name, field="name", limit=63)):
            raise ValueError("name is not a Backstage-safe slug")
        _text(self.description, field="description", limit=240)
        owner = _OWNER.fullmatch(_text(self.owner, field="owner", limit=80))
        if owner is None or any(len(part) > 63 for part in owner.groupdict().values()):
            raise ValueError(
                "owner must be an explicitly reviewed group or user reference"
            )
        if not isinstance(self.type, str) or self.type not in get_args(
            CatalogComponentType
        ):
            raise ValueError("type must be an approved Backstage component type")
        if not isinstance(self.lifecycle, str) or self.lifecycle not in get_args(
            CatalogLifecycle
        ):
            raise ValueError("lifecycle must be an approved Backstage lifecycle")
        if not isinstance(self.languages, tuple) or not 1 <= len(self.languages) <= 8:
            raise ValueError("languages must be a nonempty bounded list")
        if any(
            not _SLUG.fullmatch(_text(value, field="language", limit=40))
            for value in self.languages
        ) or len(set(self.languages)) != len(self.languages):
            raise ValueError("languages must contain unique safe tags")

    @classmethod
    def from_mapping(cls, payload: object) -> CatalogCandidate:
        if not isinstance(payload, dict) or set(payload) != _FIELDS:
            raise ValueError("metadata must contain exactly the six reviewed fields")
        languages = payload["languages"]
        if not isinstance(languages, (tuple, list)):
            raise ValueError("languages must be a nonempty bounded list")
        return cls(
            name=payload["name"],
            description=payload["description"],
            owner=payload["owner"],
            type=payload["type"],
            lifecycle=payload["lifecycle"],
            languages=tuple(languages),
        )


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for name, value in pairs:
        if name in fields:
            raise ValueError("metadata contains duplicate fields")
        fields[name] = value
    return fields


def candidate_from_json(raw: bytes | str) -> CatalogCandidate:
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
    if size > MAX_METADATA_BYTES:
        raise ValueError("reviewed metadata exceeds size budget")
    return CatalogCandidate.from_mapping(
        json.loads(raw, object_pairs_hook=_unique_fields)
    )


def render_catalog(candidate: CatalogCandidate) -> str:
    """Serialize an already validated candidate without reading source or environment."""
    if not isinstance(candidate, CatalogCandidate):
        raise TypeError("render_catalog requires a reviewed CatalogCandidate")
    # JSON string quoting is valid YAML scalar quoting. The output owns no
    # source references, substitutions, arbitrary keys or publication effects.
    lines = [
        "apiVersion: backstage.io/v1alpha1",
        "kind: Component",
        "metadata:",
        f"  name: {json.dumps(candidate.name)}",
        f"  description: {json.dumps(candidate.description, ensure_ascii=True)}",
        "  tags:",
        *(f"  - {json.dumps(language)}" for language in candidate.languages),
        "spec:",
        f"  type: {json.dumps(candidate.type)}",
        f"  lifecycle: {json.dumps(candidate.lifecycle)}",
        f"  owner: {json.dumps(candidate.owner)}",
    ]
    return "\n".join(lines) + "\n"


__all__ = (
    "CatalogCandidate",
    "CatalogComponentType",
    "CatalogLifecycle",
    "MAX_METADATA_BYTES",
    "candidate_from_json",
    "render_catalog",
)
