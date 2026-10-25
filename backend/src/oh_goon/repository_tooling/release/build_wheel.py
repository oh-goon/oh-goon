"""Stage admitted catalog source for one native standard-wheel build."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.source_layout_policy import source_transport_artifact_issues
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.integrations.python_goon_projects import python_goon_generated_paths
from oh_goon.integrations.source_snapshot import snapshot_source_directory
from oh_goon.runtime.template_discovery import discover_template_packages

ROOT = Path(__file__).resolve().parents[5]
CATALOG_ARCHIVE_ENV = "OH_GOON_RELEASE_CATALOG_ARCHIVE"
CATALOG_ARCHIVE_SHA256_ENV = "OH_GOON_RELEASE_CATALOG_ARCHIVE_SHA256"


@contextmanager
def staged_template_catalogs(root: Path) -> Iterator[Path]:
    """Capture authored catalog membership through the execution source owner."""

    if source_transport_artifact_issues(root):
        raise FleetPlanInvalid(
            "release source contains disposable transport artifacts; "
            "run oh-goon-source-layout transport-clean explicitly"
        )
    goons = root / "templates/goons"
    generated: list[str] = []
    yaml_identities: dict[str, str] = {}
    for key, package in discover_template_packages(
        goons, source_name="goon.yml", kind="Goon"
    ).items():
        source = package / "goon.yml"
        with source.open("r", encoding="utf-8", newline="") as stream:
            content = stream.read()
        yaml_identities[key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        compiled = parse_goon_template(content, source=str(source), expected_name=package.name)
        if compiled.runtime == "python":
            generated.extend(f"{key}/{path}" for path in python_goon_generated_paths(compiled))
    with tempfile.TemporaryDirectory(prefix="oh-goon-wheel-catalogs-") as directory:
        stage = Path(directory) / "templates"
        for name, exclusions in (("goons", tuple(generated)), ("plans", ())):
            source = root / "templates" / name
            snapshot_source_directory(
                source, stage / name, label=f"release {name} catalog", exclude_paths=exclusions
            )
        for key, expected in yaml_identities.items():
            if sha256_file(stage / "goons" / key / "goon.yml") != expected:
                raise FleetPlanInvalid("Goon changed while release catalog source was captured")
        yield stage


def _archive_catalogs(stage: Path) -> tuple[Path, str]:
    archive_path = stage.parent / "catalogs.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        for name in ("goons", "plans"):
            archive.add(stage / name, arcname=name)
    return archive_path, sha256_file(archive_path)


def build_wheel(root: Path = ROOT, *, offline: bool = False) -> int:
    """Delegate one build to uv/Hatch, then verify its exact admitted catalog bytes."""

    from oh_goon.repository_tooling.release.check_release_artifact import release_artifact_issues

    root = root.resolve()
    output = root / "generated/release/dist"
    with staged_template_catalogs(root) as stage:
        archive, digest = _archive_catalogs(stage)
        result = subprocess.run(
            (
                "uv",
                "build",
                "--wheel",
                "--no-build-isolation",
                "--no-sources",
                "--no-python-downloads",
                "--python",
                sys.executable,
                *(("--offline",) if offline else ()),
                "--out-dir",
                str(output),
            ),
            cwd=root / "backend",
            env={
                **os.environ,
                CATALOG_ARCHIVE_ENV: str(archive),
                CATALOG_ARCHIVE_SHA256_ENV: digest,
            },
            check=False,
        )
        if result.returncode:
            return result.returncode
        (output / ".gitignore").unlink(missing_ok=True)
        issues = release_artifact_issues(root, template_source=stage)
        if issues:
            for issue in issues:
                print(f"Release wheel verification failed: {issue}")
            return 1
    print("Release wheel identity, entry point and admitted catalog source are verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        return build_wheel(args.root, offline=args.offline)
    except (FleetDomainError, OSError, tarfile.TarError, ValueError) as exc:
        print(f"Release wheel build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
