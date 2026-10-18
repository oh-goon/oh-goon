from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from oh_goon.repository_tooling import repository_understanding_exam as exam


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["repository-understanding-exam", *argv])
    return exam.main()


def _json_file(path: Path, payload: object | None = None) -> Path:
    path.write_text(json.dumps({} if payload is None else payload), encoding="utf-8")
    return path


def test_prepare_and_verify_commands_route_to_material_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "exam"
    monkeypatch.setattr(exam, "prepare", lambda path, force: f"prepared:{path.name}:{force}")
    assert _run(monkeypatch, "prepare", str(destination), "--force") == 0
    assert capsys.readouterr().out.strip() == "prepared:exam:True"

    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(exam, "verify", lambda manifest_path: {"manifest": str(manifest_path)})
    assert _run(monkeypatch, "verify", "--manifest", str(manifest)) == 0
    assert json.loads(capsys.readouterr().out) == {"manifest": str(manifest)}


def test_grade_template_command_persists_exact_observation_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    submission = _json_file(tmp_path / "submission.json")
    configuration = _json_file(tmp_path / "configuration.json")
    output = tmp_path / "nested" / "grades.json"
    observed: dict[str, object] = {}

    def fake_grade_template(**kwargs):
        observed.update(kwargs)
        return {"artifact_type": "grader", "ok": True}

    monkeypatch.setattr(exam, "grade_template", fake_grade_template)
    profile = exam.DEFAULT_QUALIFICATION_PROFILE
    assert (
        _run(
            monkeypatch,
            "grade-template",
            "--submission",
            str(submission),
            "--candidate",
            "candidate-a",
            "--mode",
            "release",
            "--candidate-configuration",
            str(configuration),
            "--candidate-run-identity",
            "run-1",
            "--profile",
            profile,
            "--output",
            str(output),
        )
        == 0
    )
    assert observed["submission"] == submission
    assert observed["candidate_configuration"] == configuration
    assert observed["qualification_profile"] == profile
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert capsys.readouterr().out.strip() == str(output)


@pytest.mark.parametrize(
    ("command", "owner", "args"),
    [
        ("stability", "stability_report", ["r1.json", "r2.json", "r3.json"]),
        (
            "capability-certificate",
            "capability_certificate",
            [
                "r1.json",
                "r2.json",
                "r3.json",
                "--integrity",
                "i1.json",
                "i2.json",
                "i3.json",
                "--adversarial",
                "a1.json",
                "a2.json",
                "a3.json",
                "--issued-at",
                "2026-09-15T00:00:00Z",
            ],
        ),
        (
            "capability-check",
            "capability_check",
            [
                "certificate.json",
                "--repository-identity",
                "sha256:repo",
                "--candidate-configuration-identity",
                "sha256:candidate",
                "--requested-capability",
                exam.CAPABILITY_ACTIONS[0],
            ],
        ),
        (
            "recertification-plan",
            "differential_recertification_plan",
            ["certificate.json", "result.json"],
        ),
        ("adversarial", "adversarial_report", ["challenge.json", "observations.json"]),
        ("score", "score", ["grades.json", "--submission", "submission.json"]),
        ("regrade", "score", ["result.json"]),
    ],
)
def test_json_commands_route_arguments_and_optional_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    owner: str,
    args: list[str],
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_owner(*positional, **kwargs):
        calls.append((positional, kwargs))
        return {"command": command, "ok": True}

    monkeypatch.setattr(exam, owner, fake_owner)
    if command in {"recertification-plan", "adversarial"}:
        monkeypatch.setattr(exam, "exam_snapshot", lambda: {"repository_identity": "repo"})
    if command == "adversarial":
        monkeypatch.setattr(exam, "benchmark_registry", lambda: {"registry": True})
    output = tmp_path / command / "result.json"
    argv = [
        command,
        *[str(tmp_path / value) if value.endswith(".json") else value for value in args],
        "--output",
        str(output),
    ]
    assert _run(monkeypatch, *argv) == 0
    assert calls
    assert json.loads(output.read_text(encoding="utf-8")) == {"command": command, "ok": True}
    assert json.loads(capsys.readouterr().out) == {"command": command, "ok": True}


def test_calibrate_and_human_baseline_bind_current_exam_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observations = _json_file(tmp_path / "observations.json")
    snapshot = {
        "source_version": "1267.0.883",
        "repository_identity": "sha256:repo",
        "snapshot_sha256": "sha256:snapshot",
        "questions": [
            {"id": "q1", "public": {"difficulty": "STRONG"}},
            {"id": "q2", "public": {"difficulty": "MID"}},
        ],
    }
    monkeypatch.setattr(exam, "exam_snapshot", lambda: snapshot)
    calibration_calls: list[dict[str, object]] = []
    baseline_calls: list[dict[str, object]] = []

    def calibration(_path, **kwargs):
        kwargs["question_ids"] = tuple(kwargs["question_ids"])
        calibration_calls.append(kwargs)
        return {"kind": "calibration"}

    def baseline(_path, **kwargs):
        kwargs["question_ids"] = tuple(kwargs["question_ids"])
        baseline_calls.append(kwargs)
        return {"kind": "baseline"}

    monkeypatch.setattr(exam, "calibration_report", calibration)
    monkeypatch.setattr(exam, "human_baseline_report", baseline)
    assert _run(monkeypatch, "calibrate", str(observations)) == 0
    assert json.loads(capsys.readouterr().out) == {"kind": "calibration"}
    assert calibration_calls[0]["question_ids"] == ("q1", "q2")
    assert calibration_calls[0]["authored_difficulty"] == {"q1": "STRONG", "q2": "MID"}

    output = tmp_path / "baseline" / "report.json"
    assert _run(monkeypatch, "human-baseline", str(observations), "--output", str(output)) == 0
    assert baseline_calls[0]["question_ids"] == ("q1", "q2")
    assert json.loads(output.read_text(encoding="utf-8")) == {"kind": "baseline"}
    capsys.readouterr()


def test_lifecycle_transition_loads_optional_evidence_and_routes_current_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    request = _json_file(tmp_path / "request.json")
    calibration = _json_file(tmp_path / "calibration.json", {"calibration": True})
    human = _json_file(tmp_path / "human.json", {"human": True})
    monkeypatch.setattr(exam, "question_lifecycle", lambda: {"lifecycle": True})
    monkeypatch.setattr(exam, "question_audit", lambda: {"audit": True})
    calls: list[dict[str, object]] = []

    def authorize(_request, **kwargs):
        calls.append(kwargs)
        return {"authorized": True}

    monkeypatch.setattr(exam, "authorize_question_transition", authorize)
    assert (
        _run(
            monkeypatch,
            "lifecycle-transition",
            str(request),
            "--calibration-report",
            str(calibration),
            "--human-baseline-report",
            str(human),
        )
        == 0
    )
    assert calls[0]["calibration_report"] == {"calibration": True}
    assert calls[0]["human_baseline"] == {"human": True}
    assert json.loads(capsys.readouterr().out) == {"authorized": True}


def test_challenge_and_integrity_commands_bind_current_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = {
        "source_version": "1267.0.883",
        "repository_identity": "sha256:repo",
        "snapshot_sha256": "sha256:snapshot",
    }
    monkeypatch.setattr(exam, "exam_snapshot", lambda: snapshot)
    monkeypatch.setattr(exam, "benchmark_registry", lambda: {"registry": True})
    monkeypatch.setattr(exam, "load_exam", lambda: ["question"])
    challenge_calls: list[dict[str, object]] = []
    integrity_calls: list[dict[str, object]] = []

    def challenge(registry, **kwargs):
        challenge_calls.append({"registry": registry, **kwargs})
        return {"kind": "challenge"}

    def integrity(path, **kwargs):
        integrity_calls.append({"path": path, **kwargs})
        return {"kind": "integrity"}

    monkeypatch.setattr(exam, "build_challenge_set", challenge)
    monkeypatch.setattr(exam, "benchmark_integrity_report", integrity)
    assert _run(monkeypatch, "challenge-set", "--seed", "seed-1") == 0
    assert challenge_calls[0]["repository_identity"] == "sha256:repo"
    assert json.loads(capsys.readouterr().out) == {"kind": "challenge"}

    evidence = tmp_path / "evidence.json"
    challenge_path = tmp_path / "challenge.json"
    assert (
        _run(
            monkeypatch,
            "integrity",
            str(evidence),
            "--challenge-set",
            str(challenge_path),
        )
        == 0
    )
    assert integrity_calls[0]["challenge_instance_path"] == challenge_path
    assert integrity_calls[0]["exam_snapshot_sha256"] == "sha256:snapshot"
    assert json.loads(capsys.readouterr().out) == {"kind": "integrity"}


def test_game_tape_and_human_reports_route_without_mutating_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(exam, "game_tape", lambda *args, **kwargs: {"game": "tape"})
    output = tmp_path / "game.json"
    assert (
        _run(
            monkeypatch,
            "game-tape",
            str(tmp_path / "r1.json"),
            str(tmp_path / "r2.json"),
            str(tmp_path / "r3.json"),
            "--integrity",
            str(tmp_path / "i1.json"),
            str(tmp_path / "i2.json"),
            str(tmp_path / "i3.json"),
            "--adversarial",
            str(tmp_path / "a1.json"),
            str(tmp_path / "a2.json"),
            str(tmp_path / "a3.json"),
            "--certificate",
            str(tmp_path / "certificate.json"),
            "--recertification",
            str(tmp_path / "retest.json"),
            "--output",
            str(output),
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {"game": "tape"}
    capsys.readouterr()

    game = _json_file(tmp_path / "game-tape.json", {"game": True})
    monkeypatch.setattr(exam, "human_game_tape_report", lambda payload: f"game={payload['game']}\n")
    assert _run(monkeypatch, "game-tape-report", str(game)) == 0
    assert capsys.readouterr().out == "game=True\n"

    result = _json_file(tmp_path / "result.json", {"score": 1})
    monkeypatch.setattr(exam, "human_report", lambda payload: f"score={payload['score']}\n")
    assert _run(monkeypatch, "report", str(result)) == 0
    assert capsys.readouterr().out == "score=1\n"


def test_report_rejects_non_object_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _json_file(tmp_path / "result.json", [1, 2, 3])
    with pytest.raises(ValueError, match="result input must be a JSON object"):
        _run(monkeypatch, "report", str(result))
