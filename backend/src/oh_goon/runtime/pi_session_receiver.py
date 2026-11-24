"""Private receiver proof for managed Pi history disclosure."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from oh_goon.core.models.agent_providers import PiSessionHistoryUnqualified, ResolvedAgentModel
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_sibling_directory,
    publish_prepared_file,
)

_KEY_NAME = "pi-history-receiver.key"
_KEY_SIZE = 32
_PROOF_PREFIX = "pi-history-receiver-v1:"
_PROOF_DOMAIN = b"oh-goon/pi-history-receiver/v1\x00"


@contextmanager
def _open_control_directory(path: Path, *, create: bool) -> Iterator[int]:
    """Resolve the selected control directory without following host aliases."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Noncanonical control directory")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as owned:
        parent = os.open(path.anchor, flags)
        owned.callback(os.close, parent)
        for component in path.parent.parts[1:]:
            parent = os.open(component, flags, dir_fd=parent)
            owned.callback(os.close, parent)
        if create:
            try:
                os.mkdir(path.name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            else:
                os.fsync(parent)
        directory = os.open(path.name, flags, dir_fd=parent)
        owned.callback(os.close, directory)
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Unqualified control directory")
        yield directory


def _read_key(directory: int) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(_KEY_NAME, flags, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size != _KEY_SIZE
        ):
            raise ValueError("Unqualified receiver key")
        key = source.read(_KEY_SIZE + 1)
        if len(key) != _KEY_SIZE:
            raise ValueError("Unqualified receiver key length")
    return key


def _receiver_key(layout: ApplicationStatePathLayout, *, create: bool) -> bytes:
    target = layout.executor_control / _KEY_NAME
    with _open_control_directory(target.parent, create=create) as directory:
        with exact_file_write_lock(target):
            try:
                return _read_key(directory)
            except FileNotFoundError:
                if not create:
                    raise

            with prepared_sibling_directory(target) as staging:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
                descriptor = os.open(_KEY_NAME, flags, 0o600, dir_fd=staging.descriptor)
                with os.fdopen(descriptor, "wb") as output:
                    os.fchmod(descriptor, 0o600)
                    output.write(secrets.token_bytes(_KEY_SIZE))
                staging.require_current()
                publish_prepared_file(staging.path / _KEY_NAME, target)
            return _read_key(directory)


def pi_history_receiver_proofs(
    layout: ApplicationStatePathLayout,
    model: ResolvedAgentModel,
    *,
    run_id: str,
    prior_run_id: str | None,
) -> tuple[str | None, str | None]:
    """Bind the exact broker receiver to this Run and its selected predecessor."""
    if model.provider.ca_file is not None:
        # The broker loads this mutable file when it starts the upstream request.
        if prior_run_id is None:
            return None, None
        raise PiSessionHistoryUnqualified("Selected Pi history receiver cannot be proven")

    try:
        key = _receiver_key(layout, create=prior_run_id is None)
        receiver = {
            "provider": model.provider_name,
            "api": model.provider.api,
            "endpoint": model.provider.base_url,
            "proxy": model.provider.proxy_url,
            "model": model.model,
            "parameters": json.loads(model.parameters_json),
            "allow_provider_history": model.allow_provider_history,
            "headers": sorted(
                (name.lower(), value.get_secret_value()) for name, value in model.headers
            ),
        }

        def proof(identity: str) -> str:
            payload = json.dumps(
                {"run_id": identity, "receiver": receiver},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            return _PROOF_PREFIX + hmac.digest(key, _PROOF_DOMAIN + payload, "sha256").hex()

        return proof(run_id), proof(prior_run_id) if prior_run_id is not None else None
    except (OSError, ValueError, RecursionError):
        raise PiSessionHistoryUnqualified("Selected Pi history receiver cannot be proven") from None
