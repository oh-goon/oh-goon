from __future__ import annotations

import os
from pathlib import Path

from oh_goon.api.local_access import ensure_local_access_token
from oh_goon.api.ui import built_ui_available
from oh_goon.cli_local_state import check_local_state_for_startup
from oh_goon.core.errors import LeaguePreferencesInvalid
from oh_goon.runtime.league_folders import LeagueFolderStore

CONTAINER_BIND_HOST = "0.0.0.0"
CONTAINER_PORT = 8787


def container_configuration_issues() -> tuple[str, ...]:
    issues: list[str] = []
    if os.environ.get("OH_GOON_CONTAINER") != "1":
        issues.append("OH_GOON_CONTAINER=1 is required for the container-only public bind")
    database = os.environ.get("OH_GOON_DB")
    if not database:
        issues.append("OH_GOON_DB must point to the bind-mounted persistent container state")
    else:
        database_path = Path(database)
        if not database_path.is_absolute():
            issues.append("OH_GOON_DB must be an absolute container path")
        else:
            state_dir = database_path.parent
            if not state_dir.is_dir():
                issues.append(f"container state directory does not exist: {state_dir}")
            elif not os.access(state_dir, os.W_OK | os.X_OK):
                issues.append(f"container state directory is not writable: {state_dir}")
    if not built_ui_available():
        issues.append("the container image must contain the built Oh Goon UI")
    return tuple(issues)


def main() -> int:
    issues = container_configuration_issues()
    if issues:
        print("Oh Goon container startup refused:")
        for issue in issues:
            print(f"  - {issue}")
        return 2
    if check_local_state_for_startup() != 0:
        return 2
    folders = LeagueFolderStore()
    try:
        saved = folders.list()
    except LeaguePreferencesInvalid as exc:
        print(str(exc))
        print(f"League preferences: {folders.path}")
        return 2
    database = Path(os.environ["OH_GOON_DB"]).resolve()
    access_token = ensure_local_access_token()
    print("oh-goon container rink")
    print(f"Persistent state: {database.parent}")
    print(f"League folders:   {len(saved)} saved")
    print(f"League file:      {folders.path}")
    print(f"Listen:           {CONTAINER_BIND_HOST}:{CONTAINER_PORT}")
    print(f"Local access token: {access_token}")
    import uvicorn

    uvicorn.run(
        "oh_goon.api.app:app",
        host=CONTAINER_BIND_HOST,
        port=CONTAINER_PORT,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
