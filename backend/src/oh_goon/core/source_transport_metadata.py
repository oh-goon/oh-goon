from __future__ import annotations

from pathlib import Path

WINDOWS_ZONE_IDENTIFIER_SUFFIXES = (
    ":Zone.Identifier",
    ":Zone.Identifier:$DATA",
)
DISPOSABLE_TRANSPORT_SUFFIXES = (".patch", ".patch.sha256")


def is_disposable_source_transport_artifact_name(name: str) -> bool:
    """Return whether one filename is disposable transport state, not authored source.

    Windows/WSL copy paths may materialize NTFS Zone.Identifier alternate data
    streams as sibling files. Agent/tool workflows may likewise copy patch transport
    files into a checkout before application. Neither is repository source identity.
    Keep the rule deliberately narrow: known MOTW suffixes plus ``*.patch`` only.
    """

    if any(
        len(name) > len(suffix) and name.endswith(suffix)
        for suffix in WINDOWS_ZONE_IDENTIFIER_SUFFIXES
    ):
        return True
    return any(
        len(name) > len(suffix) and name.endswith(suffix)
        for suffix in DISPOSABLE_TRANSPORT_SUFFIXES
    )


def authored_source_entries(directory: Path) -> tuple[Path, ...]:
    """Return deterministic authored children under one source directory.

    This is the canonical directory-membership view for repository source semantics.
    Validators and runtime discovery must not invent a second definition by reading raw
    ``Path.iterdir()`` names directly, because known transport sidecars are deliberately
    outside source identity.
    """

    return tuple(
        sorted(
            (
                entry
                for entry in directory.iterdir()
                if not is_disposable_source_transport_artifact_name(entry.name)
            ),
            key=lambda path: path.name,
        )
    )
