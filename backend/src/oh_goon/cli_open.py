from __future__ import annotations

import argparse
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from oh_goon.core.launch_navigation import safe_launch_navigation_target
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_binding import (
    LocalLaunchBindingStore,
    require_binding_matches_manifest,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

BrowserOpener = Callable[[str], bool]


def run_open_command(
    args: argparse.Namespace,
    *,
    browser_opener: BrowserOpener = webbrowser.open,
) -> int | None:
    if args.command != "open":
        return None
    workspace = Path.cwd().resolve()
    bindings = LocalLaunchBindingStore.configured()
    events = LocalLaunchEventStore.configured()
    access = LocalLaunchAccessStore.configured()
    try:
        binding = bindings.resolve(workspace)
        manifest = events.manifest(binding.launch_id)
        require_binding_matches_manifest(binding, manifest)
        target = safe_launch_navigation_target(manifest)
        token = access.token(
            launch_id=binding.launch_id,
            owner_generation=binding.owner_generation,
        )
        secret_url = f"{target.endpoint}/#access={quote(token, safe='')}"
        opened = browser_opener(secret_url)
    except (OSError, ValueError) as exc:
        print("OH-GOON OPEN UNAVAILABLE")
        print(f"Workspace: {workspace}")
        print(f"Reason: {exc}")
        print("Inspect: oh-goon app status")
        return 1
    if not opened:
        print("OH-GOON OPEN FAILED")
        print(f"Launch:   {target.launch_id}")
        print(f"Endpoint: {target.endpoint}")
        print("Reason: system browser opener did not accept the URL")
        return 1
    print("OH-GOON OPEN")
    print(f"Launch:   {target.launch_id}")
    print(f"Service:  {target.service}")
    print(f"Endpoint: {target.endpoint}")
    print("Browser:  opened")
    return 0
