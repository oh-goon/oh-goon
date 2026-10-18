from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.integrations.fleet_plan_private_root import (
    prepare_attempt_private_root,
    remove_attempt_private_root,
)


@dataclass(frozen=True)
class OciRegistryAttemptConfig:
    """Attempt-private registry state shared by typed OCI remote operations."""

    authfile: Path
    registries_dir: Path
    registries_conf: Path
    tmpdir: Path
    root: Path


def prepare_oci_registry_config(
    paths: AttemptPathLayout,
    *,
    namespace: str,
    image_authority: str,
    username_host_env: str,
    password_host_env: str,
    host_environment: Mapping[str, str],
) -> OciRegistryAttemptConfig:
    """Materialize only the named registry credential under one attempt-private config root."""

    username = host_environment[username_host_env]
    password = host_environment[password_host_env]
    if not username or not password:
        raise ValueError("Fleet OCI registry credentials must be non-empty at admitted execution")
    root = prepare_attempt_private_root(paths.config / namespace)
    try:
        authfile = root / "auth.json"
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        publish_text_file(
            authfile,
            json.dumps(
                {"auths": {image_authority: {"auth": encoded}}},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        authfile.chmod(0o600)
        registries_dir = root / "registries.d"
        registries_dir.mkdir(mode=0o700)
        registries_conf = root / "registries.conf"
        publish_text_file(registries_conf, "unqualified-search-registries = []\n")
        registries_conf.chmod(0o600)
        return OciRegistryAttemptConfig(
            authfile=authfile,
            registries_dir=registries_dir,
            registries_conf=registries_conf,
            tmpdir=paths.tmp,
            root=root,
        )
    except BaseException as exc:
        try:
            remove_attempt_private_root(root)
        except OSError as cleanup_exc:
            raise OSError(
                f"Fleet OCI private config cleanup failed after preparation error: {cleanup_exc}"
            ) from exc
        raise


def cleanup_oci_registry_config(config: OciRegistryAttemptConfig) -> None:
    """Remove the whole attempt-private registry config, including all credential material."""

    remove_attempt_private_root(config.root)


def oci_registry_environment(
    environment: Mapping[str, str],
    config: OciRegistryAttemptConfig,
) -> dict[str, str]:
    """Remove ambient containers/image overrides and bind the private registry config."""

    result = dict(environment)
    for name in (
        "CONTAINERS_REGISTRIES_CONF_OVERRIDE",
        "REGISTRIES_CONFIG_PATH",
        "REGISTRY_AUTH_FILE",
    ):
        result.pop(name, None)
    result["CONTAINERS_REGISTRIES_CONF"] = str(config.registries_conf)
    return result
