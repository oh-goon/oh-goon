from __future__ import annotations

from email.message import Message


from typing import Any
from typing import Self
from collections.abc import Callable
from collections.abc import Iterable

import time
import urllib.request

import io
from pathlib import Path
import urllib.error
import zipfile

import pytest

from oh_goon.repository_tooling.release import check_release_artifact as artifact
from oh_goon.repository_tooling.release import runtime_journey


def _managed_tree(root: Path, version: str = "1.2.3") -> dict[str, bytes]:
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "backend" / "src" / "oh_goon").mkdir(parents=True)
    (root / "backend" / "src" / "oh_goon" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (root / "generated" / "release" / "static").mkdir(parents=True)
    (root / "generated" / "release" / "static" / "index.html").write_text(
        "ok", encoding="utf-8"
    )
    payload = {
        "oh_goon/__init__.py": b"VALUE = 1\n",
        "oh_goon/static/index.html": b"ok",
    }
    for area, source, content in (
        ("goons", "goon.yml", b"version: 1\nsteps:\n- run: uv --version\n"),
        ("plans", "plan.yml", b"version: 1\ngoons:\n- python/quality\n"),
    ):
        package = root / "templates" / area / "python" / "quality"
        package.mkdir(parents=True)
        (package / source).write_bytes(content)
        (package / "README.md").write_text("# python/quality\n", encoding="utf-8")
        prefix = f"oh_goon/templates/{area}/python/quality"
        payload[f"{prefix}/{source}"] = content
        payload[f"{prefix}/README.md"] = b"# python/quality\n"
    backend = root / "backend"
    (backend / "pyproject.toml").write_text(
        "[project]\nname='oh-goon'\nversion='1.2.3'\n[project.scripts]\noh-goon='oh_goon.cli:main'\n",
        encoding="utf-8",
    )
    return payload


def _wheel(
    root: Path, *, version: str = "1.2.3", mutate: dict[str, bytes] | None = None
) -> Path:
    payload = _managed_tree(root, version)
    payload.update(mutate or {})
    dist = root / "generated" / "release" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / f"oh_goon-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.1\nName: oh-goon\nVersion: {version}\n\n".encode()
    entries = "[console_scripts]\noh-goon = oh_goon.cli:main\n".encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
        archive.writestr(f"oh_goon-{version}.dist-info/METADATA", metadata)
        archive.writestr(f"oh_goon-{version}.dist-info/entry_points.txt", entries)
    return wheel


def test_release_artifact_accepts_exact_managed_wheel(tmp_path: Path) -> None:
    _wheel(tmp_path)
    assert artifact.release_artifact_issues(tmp_path) == ()


def test_release_artifact_rejects_version_and_dist_shape(tmp_path: Path) -> None:
    assert artifact.release_artifact_issues(tmp_path) == (
        "VERSION must be a contained regular non-symlink file",
    )
    (tmp_path / "VERSION").write_text("not-semver\n", encoding="utf-8")
    assert "canonical x.y.z" in artifact.release_artifact_issues(tmp_path)[0]
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert artifact.release_artifact_issues(tmp_path) == (
        "release dist must be a contained regular non-symlink directory",
    )
    dist = tmp_path / "generated" / "release" / "dist"
    dist.mkdir(parents=True)
    assert "exactly one wheel" in artifact.release_artifact_issues(tmp_path)[0]
    (dist / "a.whl").write_bytes(b"x")
    (dist / "extra.txt").write_text("x", encoding="utf-8")
    assert "only the release wheel" in artifact.release_artifact_issues(tmp_path)[0]


def test_release_artifact_rejects_wrong_filename_and_bad_zip(tmp_path: Path) -> None:
    _managed_tree(tmp_path)
    dist = tmp_path / "generated" / "release" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / "wrong.whl"
    wheel.write_bytes(b"not-a-zip")
    issues = artifact.release_artifact_issues(tmp_path)
    assert any("filename" in item for item in issues)
    assert any("cannot be inspected" in item for item in issues)


def test_archive_issues_detect_managed_payload_metadata_entries_and_stale_files(
    tmp_path: Path,
) -> None:
    _managed_tree(tmp_path)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("oh_goon/__init__.py", b"wrong")
        archive.writestr("oh_goon/stale.py", b"stale")
        archive.writestr(
            "oh_goon-1.2.3.dist-info/METADATA", b"Name: wrong\nVersion: 9.9.9\n\n"
        )
        archive.writestr(
            "oh_goon-1.2.3.dist-info/entry_points.txt",
            b"[console_scripts]\nother = x:y\n",
        )
    raw.seek(0)
    with zipfile.ZipFile(raw) as archive:
        issues = artifact._archive_issues(archive, tmp_path, "1.2.3")
    text = "\n".join(issues)
    assert "managed payload differs" in text
    assert "missing managed payload" in text
    assert "stale managed payload" in text
    assert "missing required payload: oh_goon/static/index.html" in text
    assert "metadata Name" in text and "metadata Version" in text
    assert "console_scripts do not match" in text


def test_expected_payload_rejects_symlinked_source_and_members(tmp_path: Path) -> None:
    _managed_tree(tmp_path)
    package = tmp_path / "backend" / "src" / "oh_goon"
    target = tmp_path / "outside"
    target.mkdir()
    link = package / "linked"
    link.symlink_to(target, target_is_directory=True)
    file_target = tmp_path / "outside.txt"
    file_target.write_text("x", encoding="utf-8")
    (package / "linked.py").symlink_to(file_target)
    _payload, issues = artifact._expected_payload(tmp_path)
    text = "\n".join(issues)
    assert "managed release source contains symlink" in text


def test_project_scripts_rejects_missing_project_and_bad_scripts(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    path = backend / "pyproject.toml"
    path.write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing \[project\]"):
        artifact._project_scripts(tmp_path)
    path.write_text("[project]\nname='x'\nscripts=[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="string table"):
        artifact._project_scripts(tmp_path)


def test_entry_point_issues_reports_invalid_metadata(tmp_path: Path) -> None:
    _managed_tree(tmp_path)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("entry_points.txt", b"[broken")
    raw.seek(0)
    with zipfile.ZipFile(raw) as archive:
        issues = artifact._entry_point_issues(archive, "entry_points.txt", tmp_path)
    assert issues and "cannot be verified" in issues[0]


def test_runtime_request_json_wraps_http_and_rejects_non_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = urllib.error.HTTPError(
        "http://x", 409, "conflict", Message(), io.BytesIO(b"boom")
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )
    with pytest.raises(RuntimeError, match="HTTP 409"):
        runtime_journey._request_json("GET", "http://x", access_token="token")

    class Response:
        def read(self) -> bytes:
            return b"[]"

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    response = Response()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: response)
    with pytest.raises(RuntimeError, match="expected JSON object"):
        runtime_journey._request_json("GET", "http://x", access_token="token")


def _league_team(
    path: str, *, pipeline_sha256: str = "a" * 64, instance_id: str = "instance-a"
) -> dict[str, object]:
    return {
        "repository": {"path": path},
        "repository_instance_id": instance_id,
        "pipeline": {"state": "CONFIGURED", "sha256": pipeline_sha256},
    }


def test_team_run_authority_reads_displayed_pipeline_and_repository_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_path = str(Path("/repos/a").resolve())
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {
            "teams": [
                _league_team(
                    expected_path, pipeline_sha256="b" * 64, instance_id="repo-instance"
                )
            ]
        },
    )

    assert runtime_journey.team_run_authority(
        base_url="http://x", server_repository_path="/repos/a", access_token="t"
    ) == ("b" * 64, "repo-instance")


def test_team_run_authority_fails_closed_for_missing_or_invalid_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_path = str(Path("/repos/a").resolve())
    for payload, match in (
        ({"teams": []}, "was not present"),
        (
            {
                "teams": [
                    {**_league_team(expected_path), "pipeline": {"state": "MISSING"}}
                ]
            },
            "not CONFIGURED",
        ),
        (
            {"teams": [_league_team(expected_path, pipeline_sha256="not-a-sha")]},
            "missing freshness authority",
        ),
        (
            {"teams": [_league_team(expected_path, instance_id="")]},
            "missing freshness authority",
        ),
    ):
        monkeypatch.setattr(
            runtime_journey,
            "_request_json",
            lambda *_a, _payload=payload, **_k: _payload,
        )
        with pytest.raises(RuntimeError, match=match):
            runtime_journey.team_run_authority(
                base_url="http://x", server_repository_path="/repos/a", access_token="t"
            )


def test_start_acceptance_registers_parent_and_submits_displayed_freshness_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    expected_path = str(Path("/repos/a").resolve())

    def request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, url, kwargs.get("payload")))
        if url.endswith("/api/locations") and method == "GET":
            return {"locations": []}
        if url.endswith("/api/league") and method == "GET":
            return {
                "teams": [
                    _league_team(
                        expected_path,
                        pipeline_sha256="c" * 64,
                        instance_id="instance-c",
                    )
                ]
            }
        return {}

    monkeypatch.setattr(runtime_journey, "_request_json", request)
    run_id = runtime_journey.start_acceptance_goon(
        base_url="http://x", server_repository_path="/repos/a", access_token="t"
    )
    assert run_id.startswith("acceptance-")
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "POST"]
    submitted = calls[-1][2]
    assert submitted is not None
    assert submitted["expected_pipeline_sha256"] == "c" * 64
    assert submitted["expected_repository_instance_id"] == "instance-c"

    calls.clear()

    def already_registered(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, url, kwargs.get("payload")))
        if url.endswith("/api/locations") and method == "GET":
            return {"locations": ["/repos"]}
        if url.endswith("/api/league") and method == "GET":
            return {"teams": [_league_team(expected_path)]}
        return {}

    monkeypatch.setattr(runtime_journey, "_request_json", already_registered)
    runtime_journey.start_acceptance_goon(
        base_url="http://x", server_repository_path="/repos/a", access_token="t"
    )
    assert [call[0] for call in calls] == ["GET", "GET", "POST"]


def _clock(values: Iterable[float]) -> Callable[[], float]:
    iterator = iter(values)
    return lambda: next(iterator)


def test_wait_for_run_status_success_wrong_terminal_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "RUNNING"}},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1, 2.0]))
    monkeypatch.setattr(time, "sleep", lambda _x: None)
    with pytest.raises(RuntimeError, match="did not reach"):
        runtime_journey.wait_for_acceptance_run_status(
            base_url="x",
            run_id="r",
            access_token="t",
            expected_status="COMPLETED",
            timeout_seconds=1.0,
        )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "FAILED"}},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    with pytest.raises(RuntimeError, match="reached 'FAILED'"):
        runtime_journey.wait_for_acceptance_run_status(
            base_url="x", run_id="r", access_token="t", expected_status="COMPLETED"
        )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    assert (
        runtime_journey.wait_for_acceptance_run_status(
            base_url="x", run_id="r", access_token="t", expected_status="COMPLETED"
        )["outcome"]
        == "SUCCESS"
    )


def test_wait_interruptible_step_success_terminal_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def started(_method: str, url: str, **_kwargs: object) -> dict[str, Any]:
        if url.endswith("/events?after=0"):
            return {"events": [{"event_type": "PROCESS_STARTED", "pid": 123}]}
        return {"run": {"status": "RUNNING"}}

    monkeypatch.setattr(runtime_journey, "_request_json", started)
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    assert (
        runtime_journey.wait_for_interruptible_step(
            base_url="x", run_id="r", repository=tmp_path, access_token="t"
        )
        == 123
    )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "FAILED"}},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    with pytest.raises(RuntimeError, match="terminalized before crash point"):
        runtime_journey.wait_for_interruptible_step(
            base_url="x", run_id="r", repository=tmp_path, access_token="t"
        )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "RUNNING"}},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1, 2.0]))
    monkeypatch.setattr(time, "sleep", lambda _x: None)
    with pytest.raises(
        RuntimeError, match="START_NOT_OBSERVED_WITHIN_ACCEPTANCE_BOUND"
    ) as exc_info:
        runtime_journey.wait_for_interruptible_step(
            base_url="x",
            run_id="r",
            repository=tmp_path,
            access_token="t",
            timeout_seconds=1.0,
        )
    assert "run_status': 'RUNNING" in str(exc_info.value)
    assert (
        "next_diagnostic=inspect canonical lifecycle predecessor/owner before PROCESS_STARTED"
        in str(exc_info.value)
    )


def test_run_acceptance_goon_success_failure_missing_tape_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_journey, "start_acceptance_goon", lambda **_k: "run")
    monkeypatch.setattr(time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": ["acceptance-step"],
        },
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    assert (
        runtime_journey.run_acceptance_goon(
            base_url="x", server_repository_path="/r", access_token="t"
        )
        == "run"
    )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "FAILED"}, "outcome": "FAIL"},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    with pytest.raises(RuntimeError, match="did not pass"):
        runtime_journey.run_acceptance_goon(
            base_url="x", server_repository_path="/r", access_token="t"
        )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {
            "run": {"status": "COMPLETED"},
            "outcome": "SUCCESS",
            "game_tape": [],
        },
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1]))
    with pytest.raises(RuntimeError, match="does not contain"):
        runtime_journey.run_acceptance_goon(
            base_url="x", server_repository_path="/r", access_token="t"
        )
    monkeypatch.setattr(
        runtime_journey,
        "_request_json",
        lambda *_a, **_k: {"run": {"status": "RUNNING"}},
    )
    monkeypatch.setattr(time, "monotonic", _clock([0.0, 0.1, 2.0]))
    with pytest.raises(RuntimeError, match="did not finish"):
        runtime_journey.run_acceptance_goon(
            base_url="x",
            server_repository_path="/r",
            access_token="t",
            timeout_seconds=1.0,
        )


def test_acceptance_repository_uses_uv_owned_python_instead_of_ambient_python(
    tmp_path: Path,
) -> None:
    runtime_journey.prepare_acceptance_repository(tmp_path)
    goon = (tmp_path / "goon.yml").read_text(encoding="utf-8")
    assert "run: uv run --no-project python acceptance_step.py" in goon
    assert "run: python acceptance_step.py" not in goon
