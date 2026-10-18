from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.path_layouts import ManagedPreparationPathLayout

_NATIVE_UV_TRANSPORT_ENVIRONMENT = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSL_CLIENT_CERT",
)
_NATIVE_UV_DISCOVERY_ENVIRONMENT = ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "PATH")


@dataclass(frozen=True, slots=True, repr=False)
class NativeUvAuthority:
    """Ephemeral native uv configuration captured at one preparation boundary.

    Values are intentionally neither modeled nor represented: uv remains the owner of
    configuration, credentials, index, proxy and certificate semantics.
    """

    _transport: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "NativeUvAuthority(<opaque>)"

    def environment(
        self,
        preparation: ManagedPreparationPathLayout,
        *,
        environment: Path,
        cache: Path | None = None,
        uv_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        preparation.ensure()
        env = dict(self._transport)
        env.setdefault("HOME", str(preparation.home))
        env.setdefault("USERPROFILE", env["HOME"])
        env.setdefault("XDG_CONFIG_HOME", str(preparation.config))
        env.setdefault("PATH", os.defpath)
        env.update(
            {
                "TMPDIR": str(preparation.tmp),
                "TMP": str(preparation.tmp),
                "TEMP": str(preparation.tmp),
                "XDG_CACHE_HOME": str(preparation.cache),
                "UV_CACHE_DIR": str(cache or (preparation.cache / "uv")),
                "UV_PROJECT_ENVIRONMENT": str(environment),
                "PYTHONDONTWRITEBYTECODE": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
        )
        if uv_overrides:
            env.update(uv_overrides)
        return env


def capture_native_uv_authority(
    host_environment: Mapping[str, str] | None = None,
    *,
    owned_uv_names: frozenset[str] = frozenset(),
) -> NativeUvAuthority:
    source = os.environ if host_environment is None else host_environment
    transported = {
        name: value
        for name, value in source.items()
        if name.startswith("UV_") and name not in owned_uv_names
    }
    transported.update(
        {
            name: source[name]
            for name in (*_NATIVE_UV_TRANSPORT_ENVIRONMENT, *_NATIVE_UV_DISCOVERY_ENVIRONMENT)
            if name in source
        }
    )
    return NativeUvAuthority(tuple(sorted(transported.items())))
