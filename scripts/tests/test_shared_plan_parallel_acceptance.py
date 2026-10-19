from __future__ import annotations

from typing import Any

import pytest

from pathlib import Path

from oh_goon.repository_tooling.acceptance import shared_plan_parallel


def test_parallel_repository_fixture_executes_the_canonical_locked_uv_command(
    tmp_path: Path,
) -> None:
    import subprocess
    import uuid

    repository = tmp_path / "repo"
    shared_plan_parallel.prepare_parallel_shared_repository(repository)
    revision = "n" + uuid.uuid4().hex + "abcdef0"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--offline",
            "--frozen",
            "--group",
            "dev",
            "release-metadata",
            "--channel",
            "beta",
            "--revision",
            revision,
            "--output-dir",
            ".fleet/release",
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (
        repository / ".fleet/release/PROJECT_VERSION"
    ).read_text().strip() == "1.0.0"
    assert (
        (repository / ".fleet/release/RELEASE_VERSION")
        .read_text()
        .strip()
        .endswith(revision[:7])
    )


def test_parallel_preview_to_run_carries_exact_plan_and_repository_instance_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    repository_ids = ["repo-left", "repo-right"]
    preview_payload = {
        "plan_id": "plan-reviewed",
        "sha256": "a" * 64,
        "execution_digest": "sha256:" + "d" * 64,
        "ready_count": 2,
        "blocked_count": 0,
        "teams": [
            {
                "state": "READY",
                "repository": {
                    "repository_id": "repo-left",
                    "name": "left",
                    "path": "/left",
                },
                "repository_instance_id": "instance-left",
                "preview": {
                    "imports": [{"applicability": "APPLICABLE", "reason": ""}],
                    "steps": [{"disposition": "READY"}],
                },
            },
            {
                "state": "READY",
                "repository": {
                    "repository_id": "repo-right",
                    "name": "right",
                    "path": "/right",
                },
                "repository_instance_id": "instance-right",
                "preview": {
                    "imports": [{"applicability": "APPLICABLE", "reason": ""}],
                    "steps": [{"disposition": "READY"}],
                },
            },
        ],
    }

    def request(
        method: str,
        url: str,
        *,
        access_token: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, url, payload))
        if url.endswith("/shared-plan-previews"):
            return preview_payload
        return {"fleet_run_id": "run-authority"}

    monkeypatch.setattr(shared_plan_parallel, "_request_json", request)
    preview = shared_plan_parallel._preview(
        "http://local",
        access_token="token",
        repository_ids=repository_ids,
        inputs={"release_channel": "beta", "revision": "r" * 40},
        expected_revision="b" * 64,
    )
    shared_plan_parallel._start(
        "http://local",
        access_token="token",
        repository_ids=repository_ids,
        preview=preview,
        inputs={"release_channel": "beta", "revision": "r" * 40},
        run_id="run-authority",
    )
    preview_request = calls[0][2]
    run_request = calls[1][2]
    assert (
        preview_request is not None and preview_request["expected_revision"] == "b" * 64
    )
    assert run_request is not None
    assert run_request["expected_plan_id"] == "plan-reviewed"
    assert run_request["expected_plan_revision"] == "a" * 64
    assert run_request["repository_instance_ids"] == {
        "repo-left": "instance-left",
        "repo-right": "instance-right",
    }
    from oh_goon.api.schemas.league import SharedPlanRunRequest

    SharedPlanRunRequest.model_validate(run_request)


def test_repository_replacement_keeps_stable_team_id_but_changes_instance_authority(
    tmp_path: Path,
) -> None:
    import shutil

    from oh_goon.core.product_identity import repository_id, repository_instance_id

    repository = tmp_path / "team"
    shared_plan_parallel.prepare_parallel_shared_repository(repository)
    stable = repository_id(repository)
    before = repository_instance_id(repository)
    shutil.rmtree(repository)
    shared_plan_parallel.prepare_parallel_shared_repository(repository)
    assert repository_id(repository) == stable
    assert repository_instance_id(repository) != before


def test_parallel_repository_owned_failure_and_sibling_success_are_real_processes(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys
    import uuid

    failing = tmp_path / "failing"
    sibling = tmp_path / "sibling"
    shared_plan_parallel.prepare_parallel_shared_repository(failing, behavior="fail")
    shared_plan_parallel.prepare_parallel_shared_repository(
        sibling, behavior="slow-success"
    )
    revision = "f" + uuid.uuid4().hex + "abcdef0"
    commands = []
    for repository in (failing, sibling):
        output = repository / "output"
        commands.append(
            subprocess.Popen(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                    "import acceptance_parallel; acceptance_parallel.main()",
                    str(repository),
                    "--channel",
                    "beta",
                    "--revision",
                    revision,
                    "--output-dir",
                    str(output),
                )
            )
        )
    failing_process, sibling_process = commands
    assert failing_process.wait(timeout=5) == 17
    assert sibling_process.wait(timeout=5) == 0
    assert (failing / f"parallel-failed-{revision}").is_file()
    assert not (failing / f"parallel-finished-{revision}").exists()
    assert (sibling / f"parallel-finished-{revision}").is_file()


def test_parallel_process_probe_observes_two_real_children_for_one_revision() -> None:
    import subprocess
    import uuid

    revision = "c" + uuid.uuid4().hex + "abcdef0"
    import sys

    children = [
        subprocess.Popen(
            (
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                "release-metadata",
                revision,
            )
        )
        for _ in range(2)
    ]
    try:
        deadline = __import__("time").monotonic() + 2.0
        observed: set[int] = set()
        while __import__("time").monotonic() < deadline:
            observed = shared_plan_parallel._processes_for_revision(revision)
            if all(child.pid in observed for child in children):
                break
            __import__("time").sleep(0.02)
        assert {child.pid for child in children} <= observed
    finally:
        for child in children:
            child.terminate()
        for child in children:
            child.wait(timeout=2)
