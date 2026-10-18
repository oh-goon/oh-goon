from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[5]


def run_runtime_browser_acceptance(
    *,
    root: Path = ROOT,
    base_url: str,
    repository_path: str,
    run_id: str,
    access_token: str,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "OH_GOON_RUNTIME_URL": base_url,
            "OH_GOON_RUNTIME_REPOSITORY_PATH": repository_path,
            "OH_GOON_RUNTIME_RUN_ID": run_id,
            "OH_GOON_RUNTIME_ACCESS_TOKEN": access_token,
        }
    )
    subprocess.run(
        ("npm", "run", "browser:runtime"),
        cwd=root / "frontend",
        env=env,
        check=True,
    )
