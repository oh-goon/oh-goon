"""Native producer discovery and path-scoped Git evidence for the showcase."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _run(arguments: list[str], repository: Path, environment: dict[str, str]) -> str:
    # Native output remains file-backed until its small fixture budget is proved.
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        result = subprocess.run(
            arguments,
            cwd=repository,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            timeout=30,
            check=False,
        )
        assert stdout.tell() <= 128 * 1024
        assert stderr.tell() <= 128 * 1024
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode("utf-8")
        error = stderr.read().decode("utf-8")
    assert result.returncode == 0, (output, error)
    return output


def test_catalog_discovery_retains_producer_qualifications_and_git_scope(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "sample-repo"
    shutil.copytree(ROOT / "examples/private-code-catalog/sample-repo", repository)
    environment = dict(os.environ)
    environment.update(
        HASHMARKS_NO_UPDATE_CHECK="1",
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(tmp_path / "no-global-git-config"),
        GIT_AUTHOR_NAME="Demo Service Contributor",
        GIT_AUTHOR_EMAIL="service-sentinel@example.invalid",
        GIT_AUTHOR_DATE="2026-01-01T10:00:00+00:00",
        GIT_COMMITTER_NAME="Demo Service Contributor",
        GIT_COMMITTER_EMAIL="service-sentinel@example.invalid",
        GIT_COMMITTER_DATE="2026-01-01T10:00:00+00:00",
    )

    def git(*arguments: str) -> str:
        return _run(["git", *arguments], repository, environment).strip()

    git("init", "-q", "--template=")
    git("add", ".")
    git("commit", "-qm", "Seed synthetic service")
    service_revision = git("rev-parse", "HEAD")
    environment.update(
        GIT_AUTHOR_NAME="Demo Client Contributor",
        GIT_AUTHOR_EMAIL="client-sentinel@example.invalid",
        GIT_AUTHOR_DATE="2026-01-02T11:00:00+00:00",
        GIT_COMMITTER_NAME="Demo Client Contributor",
        GIT_COMMITTER_EMAIL="client-sentinel@example.invalid",
        GIT_COMMITTER_DATE="2026-01-02T11:00:00+00:00",
    )
    client = repository / "web/client.ts"
    with client.open("a", encoding="utf-8") as stream:
        stream.write("\n// Synthetic client-only change.\n")
    git("add", "web/client.ts")
    git("commit", "-qm", "Change only the synthetic client")
    selected_revision = git("rev-parse", "HEAD")
    assert selected_revision != service_revision
    author_format = "--format=%H%n%an%n%aI"
    service_author = git(
        "log", "-1", author_format, selected_revision, "--", "src/service.py"
    )
    client_author = git(
        "log", "-1", author_format, selected_revision, "--", "web/client.ts"
    )
    assert service_author.splitlines()[:2] == [
        service_revision,
        "Demo Service Contributor",
    ]
    assert datetime.fromisoformat(service_author.splitlines()[2]) == datetime(
        2026, 1, 1, 10, tzinfo=UTC
    )
    assert client_author.splitlines()[:2] == [
        selected_revision,
        "Demo Client Contributor",
    ]
    assert datetime.fromisoformat(client_author.splitlines()[2]) == datetime(
        2026, 1, 2, 11, tzinfo=UTC
    )
    assert "@" not in service_author + client_author

    def hashmarks(*arguments: str) -> dict[str, Any]:
        output = _run(
            [
                str(Path(sys.executable).with_name("hashmarks")),
                "--workspace",
                str(repository),
                "--state-dir",
                str(tmp_path / "hashmarks-state"),
                *arguments,
            ],
            repository,
            environment,
        )
        decoded = json.loads(output)
        assert isinstance(decoded, dict)
        return decoded

    indexed = hashmarks("map", "sync")
    assert indexed["build_state"] == "COMPLETE"
    assert indexed["parse_errors"] == 0
    assert len(indexed["workspace_fingerprint"]) == 64
    coverage = indexed["preflight"]["observation_coverage"]
    assert coverage["repository_wide_completeness"] == "not-claimed"
    orientation = hashmarks("orient")
    assert orientation["workspace"] == str(repository)
    assert orientation["generation"] == indexed["generation"]
    assert orientation["languages"]["python"] >= 2
    assert orientation["languages"]["typescript"] == 1

    found = hashmarks("find", "submit_order", "--limit", "8")
    assert found["generation"] == indexed["generation"]
    assert found["truncated"] is False
    assert any(
        hit["path"] == "src/service.py"
        and hit["qualname"] == "submit_order"
        and hit["lines"][0] > 0
        and hit["lines"][1] >= hit["lines"][0]
        for hit in found["results"]
    )
    relationship = hashmarks("deps", "src/service.py")
    assert relationship["generation"] == indexed["generation"]
    assert any(
        edge["kind"] == "import"
        and edge["path"] == "src/service.py"
        and edge["confidence"] == "static"
        and "src/order_status.py" in edge["resolved_paths"]
        for edge in relationship["edges"]
    )

    missing = hashmarks("find", "definitely_absent_catalog_needle", "--limit", "8")
    assert missing["results"] == []
    # A synchronized CodeMap alone does not prove fresh repository-wide absence.
    assert missing["freshness"] == "unknown"
    assert missing["negative_evidence"] == "not-admissible"
    assert "repository-freshness-unknown" in missing["admissibility_reasons"]
    assert git("status", "--porcelain") == ""
    assert not (repository / ".hashmarks").exists()
