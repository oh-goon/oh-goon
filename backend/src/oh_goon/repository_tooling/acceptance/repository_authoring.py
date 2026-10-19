from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import render_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

PROBE_GOON = """version: 1
inputs:
  version:
    type: semver
  image_name:
    type: string
    starts_with: my-image-storage/
  platform:
    type: choice
    choices:
    - linux/amd64
    - linux/arm64
    default: linux/amd64
  timeout:
    type: duration
    default: 5m
  revision:
    type: git_revision
steps:
- run: bash verify_values.sh "${{ inputs.version }}" "${{ inputs.image_name }}" "${{ inputs.platform }}" "${{ inputs.timeout }}" "${{ inputs.revision }}"
"""

PLAN = """version: 1
goons:
- import: general/authoring-probe
  with:
    version:
      from:
      - toml:
          path: pyproject.toml
          key: project.version
      - text:
          path: VERSION
    image_name:
      from:
        yaml:
          path: project.yml
          key: project.imagename
    timeout:
      from:
      - env: HELM_TIMEOUT_OVERRIDE
      - env: HELM_TIMEOUT
    revision:
      from:
        git: commit_short_sha
"""

VERIFY_VALUES = """#!/usr/bin/env bash
set -euo pipefail
printf "%s\\n" "$1" "$2" "$3" "$4" "$5" > verified-values.txt
"""


def _package(root: Path) -> None:
    directory = root / "general" / "authoring-probe"
    directory.mkdir(parents=True)
    (directory / "goon.yml").write_text(PROBE_GOON, encoding="utf-8")
    (directory / "README.md").write_text("# authoring-probe\n", encoding="utf-8")


def _git_repository(repository: Path) -> str:
    subprocess.run(("git", "init", "-q", str(repository)), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.email", "acceptance@example.com"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.name", "Oh Goon Acceptance"),
        check=True,
    )
    subprocess.run(("git", "-C", str(repository), "add", "."), check=True)
    subprocess.run(("git", "-C", str(repository), "commit", "-qm", "acceptance"), check=True)
    return subprocess.check_output(
        ("git", "-C", str(repository), "rev-parse", "HEAD"), text=True
    ).strip()


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repository"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname = 'portable-verification'\nversion = '2.4.1'\n",
        encoding="utf-8",
    )
    (repository / "VERSION").write_text("2.4.0\n", encoding="utf-8")
    (repository / "project.yml").write_text(
        "project:\n  imagename: my-image-storage/portable-verification\n",
        encoding="utf-8",
    )
    (repository / "verify_values.sh").write_text(VERIFY_VALUES, encoding="utf-8")
    sha = _git_repository(repository)
    return repository, sha


def _resolver(root: Path) -> tuple[FleetPlanResolver, Path]:
    templates = root / "templates" / "goons"
    _package(templates)
    plan = root / "plan.yml"
    plan.write_text(PLAN, encoding="utf-8")
    return FleetPlanResolver(goon_templates=GoonTemplateStore(templates)), plan


def certify_repository_authoring() -> None:
    with tempfile.TemporaryDirectory(prefix="oh-goon-repository-certification-") as temporary:
        root = Path(temporary)
        repository, sha = _repository(root)
        resolver, plan = _resolver(root)
        environment = {"HELM_TIMEOUT": "10m"}

        resolved = resolver.resolve(plan, repository=repository, environment=environment)
        step = resolved.steps[0]
        if not isinstance(step, ResolvedCommandStep):
            raise RuntimeError("repository authoring probe must resolve to a command step")
        expected = (
            "bash",
            "verify_values.sh",
            "2.4.1",
            "my-image-storage/portable-verification",
            "linux/amd64",
            "10m",
            sha[:8],
        )
        if step.argv != expected:
            raise RuntimeError(f"canonical resolved inputs differ: {step.argv!r}")

        subprocess.run(step.argv, cwd=repository, check=True, env=os.environ.copy())
        observed = (repository / "verified-values.txt").read_text(encoding="utf-8").splitlines()
        if observed != [
            "2.4.1",
            "my-image-storage/portable-verification",
            "linux/amd64",
            "10m",
            sha[:8],
        ]:
            raise RuntimeError(f"repository code received non-canonical values: {observed!r}")

        first_lock = build_execution_lock(resolved)
        overridden = resolver.resolve(
            plan,
            repository=repository,
            environment={"HELM_TIMEOUT_OVERRIDE": "20m", "HELM_TIMEOUT": "10m"},
        )
        second_lock = build_execution_lock(overridden)
        if first_lock.digest == second_lock.digest:
            raise RuntimeError("changed resolved input did not change execution identity")
        rendered = render_execution_lock(second_lock)
        if "20m" in rendered or "HELM_TIMEOUT" in rendered:
            raise RuntimeError("external input value/source leaked into goon.lock")

        (repository / "pyproject.toml").write_text(
            "[project]\nname = 'portable-verification'\n",
            encoding="utf-8",
        )
        fallback = resolver.resolve(plan, repository=repository, environment=environment)
        fallback_step = fallback.steps[0]
        if not isinstance(fallback_step, ResolvedCommandStep):
            raise RuntimeError("repository authoring fallback must resolve to a command step")
        if fallback_step.argv[2] != "2.4.0":
            raise RuntimeError("missing TOML value did not fall back to VERSION text source")

        (repository / "VERSION").write_text("not-semver\n", encoding="utf-8")
        try:
            resolver.resolve(plan, repository=repository, environment=environment)
        except FleetPlanInvalid as exc:
            if "semver" not in str(exc):
                raise RuntimeError(f"invalid source failed for wrong reason: {exc}") from exc
        else:
            raise RuntimeError("invalid resolved repository value reached execution")


def main() -> int:
    try:
        certify_repository_authoring()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"Repository authoring certification failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Repository authoring certification passed: TOML/text/YAML/env/Git -> validated canonical inputs -> repository code"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
