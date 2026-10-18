from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_REPOSITORY_STATE_ENVIRONMENT = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_TEMPLATE_DIR",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class NativeGitAuthority:
    """Ephemeral operator Git transport authority for extension acquisition.

    Git owns authentication, credential-helper, SSH, proxy, certificate, URL
    rewriting, and other transport configuration. Fleet does not permit ambient
    repository state or template selection to redirect its staging/materialization
    boundary. Values remain opaque and are never modeled or represented durably.
    """

    _environment: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "NativeGitAuthority(<opaque>)"

    def transport_environment(self) -> dict[str, str]:
        environment = {
            name: value
            for name, value in self._environment
            if name not in _REPOSITORY_STATE_ENVIRONMENT
        }
        # Extension transport is intentionally non-interactive. Checkout and
        # verification are Fleet-owned local operations and do not consume this
        # native environment at all.
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment


def capture_native_git_authority(
    host_environment: Mapping[str, str] | None = None,
) -> NativeGitAuthority:
    source = os.environ if host_environment is None else host_environment
    return NativeGitAuthority(tuple(sorted(source.items())))
