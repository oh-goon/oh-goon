"""Public JSON has one bounded reply and preserves the original command result."""

from __future__ import annotations

import errno
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from oh_goon.cli import main
from oh_goon.cli_result import MAX_CLI_JSON_BYTES, CliTermination, invoke_cli


@pytest.mark.parametrize(
    "arguments",
    [
        ["private-input-canary", "--json"],
        ["doctor", "--json=private-input-canary"],
        ["runs", "show", "--json"],
        ["runs", "list", "--before-revision", "0", "--json"],
        ["runs", "logs", "run", "--after", "private-input-canary", "--json"],
    ],
)
def test_argument_refusal_is_one_safe_json_before_runtime(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid CLI arguments reached runtime construction")

    monkeypatch.setattr("oh_goon.cli.ApplicationRuntime.build", forbidden)
    assert main(arguments) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["code"] == "CLI_ARGUMENTS_INVALID"
    assert report["_cli"] == {
        "schema": "oh-goon.cli/v1",
        "exit_code": 2,
        "exit_kind": "COMMAND_ERROR",
    }
    assert "private-input-canary" not in captured.out + captured.err


@pytest.mark.parametrize(
    "arguments,code",
    [
        (["runs", "show", "--last", "--json"], "RUN_SCOPE_REQUIRED"),
        (["runs", "logs", "run", "--follow", "--json"], "JSON_FOLLOW_UNSUPPORTED"),
        (["runs", "show", "run", "--repository-id", "other", "--json"], "RUN_SCOPE_WITHOUT_LAST"),
    ],
)
def test_invalid_readback_combination_refuses_before_state(
    arguments: list[str],
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid readback combination opened application state")

    monkeypatch.setattr("oh_goon.cli.ApplicationRuntime.build", forbidden)
    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["code"] == code


def test_partial_private_output_is_discarded_when_command_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*args: Any, **kwargs: Any) -> int:
        print("private-provider-response-canary")
        raise ValueError("private-provider-response-canary")

    monkeypatch.setattr("oh_goon.cli._main", broken)
    assert main(["doctor", "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "CLI_COMMAND_FAILED"
    assert "private-provider-response-canary" not in captured.out + captured.err


@pytest.mark.parametrize("exit_code", [0, 1, 2, 3, 130, 143])
def test_transport_preserves_owner_facts_and_numeric_exit(
    exit_code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = {"schema": "original-owner/v1", "status": "FAILED", "fleet_run_id": "original"}

    def report() -> int:
        print(json.dumps(facts))
        return exit_code

    assert invoke_cli(report, possible_json=True) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert {name: result[name] for name in facts} == facts
    assert result["_cli"]["exit_code"] == exit_code


@pytest.mark.parametrize(
    "output",
    ["{}\n{}", "not-json", '{"value": NaN}', "[" * 100000 + "0" + "]" * 100000],
    ids=["multiple-values", "invalid-text", "nonfinite", "excessive-depth"],
)
def test_invalid_json_never_reaches_stdout_as_a_partial_reply(
    output: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def report() -> int:
        print(output)
        return 0

    assert invoke_cli(report, possible_json=True) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CLI_OUTPUT_INVALID"


def test_json_size_bound_fails_without_publishing_the_large_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def report() -> int:
        chunk = "private-large-canary" * 128
        print('{"payload":"', end="")
        for _ in range(MAX_CLI_JSON_BYTES // len(chunk) + 1):
            print(chunk, end="")
        print('"}')
        return 0

    assert invoke_cli(report, possible_json=True) == 2
    captured = capsys.readouterr()
    assert len(captured.out.encode()) < MAX_CLI_JSON_BYTES
    assert json.loads(captured.out)["code"] == "CLI_OUTPUT_LIMIT_EXCEEDED"
    assert "private-large-canary" not in captured.out


@pytest.mark.parametrize("failure_cut", [None, "read", "close"])
def test_private_staging_settles_before_one_public_reply(
    failure_cut: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import redirect_stdout

    class Staging(io.StringIO):
        def seek(self, offset: int, whence: int = 0) -> int:
            if failure_cut == "read":
                raise OSError(errno.EIO, "private-staging-canary")
            return super().seek(offset, whence)

        def close(self) -> None:
            super().close()
            if failure_cut == "close":
                raise OSError(errno.EIO, "private-staging-canary")

    staging = Staging()

    class PublicReply(io.StringIO):
        def write(self, value: str) -> int:
            assert staging.closed, "public reply preceded private staging settlement"
            return super().write(value)

    monkeypatch.setattr(
        "oh_goon.cli_result.tempfile.SpooledTemporaryFile", lambda **kwargs: staging
    )
    public = PublicReply()

    def command() -> int:
        print(
            json.dumps({"schema": "original-owner/v1", "status": "COMPLETED", "outcome": "SUCCESS"})
        )
        return 0

    with redirect_stdout(public):
        assert invoke_cli(command, possible_json=True) == (0 if failure_cut is None else 2)
    result = json.loads(public.getvalue())
    assert "private-staging-canary" not in public.getvalue()
    if failure_cut == "read":
        assert result["code"] == "CLI_OUTPUT_UNAVAILABLE" and result["command_exit_code"] == 0
    else:
        assert result["status"] == "COMPLETED" and result["outcome"] == "SUCCESS"
    if failure_cut == "close":
        assert result["_cli"]["command_exit_code"] == 0
        assert result["_cli"]["presentation_cleanup"] == {
            "status": "UNVERIFIED",
            "code": "CLI_OUTPUT_STAGING_CLEANUP_FAILED",
        }


def test_staging_cleanup_failure_does_not_mask_explicit_caller_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Staging(io.StringIO):
        def close(self) -> None:
            super().close()
            raise OSError(errno.EIO, "private-staging-cleanup-canary")

    monkeypatch.setattr(
        "oh_goon.cli_result.tempfile.SpooledTemporaryFile", lambda **kwargs: Staging()
    )

    def command() -> int:
        raise ValueError("original caller authority error")

    with pytest.raises(ValueError, match="original caller authority error"):
        invoke_cli(command, possible_json=True, propagate=(ValueError,))
    captured = capsys.readouterr()
    assert captured.out == "" and "CLI_OUTPUT_STAGING_CLEANUP_FAILED" in captured.err
    assert "private-staging-cleanup-canary" not in captured.err


@pytest.mark.parametrize("after_publication", [False, True])
def test_init_interruption_does_not_infer_publication_from_a_missing_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    after_publication: bool,
) -> None:
    from oh_goon.integrations import exact_file_write

    baseline = tmp_path / "baseline"
    arguments = ["init", "--template", "python/quality", "--draft", "--json", "--output"]
    assert main([*arguments, str(baseline)]) == 0
    assert (baseline / "run.yml").is_file()
    capsys.readouterr()
    publish = exact_file_write.publish_prepared_directory

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        if after_publication:
            publish(*args, **kwargs)
        raise CliTermination

    monkeypatch.setattr(exact_file_write, "publish_prepared_directory", interrupted)
    destination = tmp_path / "interrupted"
    assert main([*arguments, str(destination)]) == 143
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "CLI_INTERRUPTED"
    assert result["publication"] == "unverified_after_interruption"
    assert (destination / "run.yml").exists() is after_publication


def test_task_value_named_json_does_not_select_json_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def native(args: Any) -> int:
        assert args.prompt == "--json" and not args.as_json
        print("human task result")
        return 0

    monkeypatch.setattr("oh_goon.cli_ai.run_ai_command", native)
    assert main(["ai", "--prompt=--json"]) == 0
    assert capsys.readouterr().out == "human task result\n"


@pytest.mark.parametrize("arguments", [["doctor", "--help", "--json"], ["--version", "--json"]])
def test_help_and_version_json_are_single_versioned_values(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"].startswith("oh-goon.cli-") and result["_cli"]["exit_code"] == 0


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="native Linux signal owner required")
def test_resource_owner_can_take_over_the_invocation_default_signal_adapter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from oh_goon.cli_run_resources import original_run_resource_signals

    previous = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        def command() -> int:
            before = signal.getsignal(signal.SIGTERM)
            with original_run_resource_signals():
                assert signal.getsignal(signal.SIGTERM) is not before
            assert signal.getsignal(signal.SIGTERM) is before
            print(json.dumps({"owner": "original-resource-owner"}))
            return 0

        assert invoke_cli(command, possible_json=True) == 0
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        assert signal.getsignal(signal.SIGINT) is original_int
        assert json.loads(capsys.readouterr().out)["owner"] == "original-resource-owner"
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_resource_owner_still_refuses_explicit_caller_signal_policy() -> None:
    from oh_goon.cli_run_resources import original_run_resource_signals
    from oh_goon.core.errors import FleetPlanInvalid

    previous = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

        def command() -> int:
            with pytest.raises(FleetPlanInvalid, match="signal owner"):
                with original_run_resource_signals():
                    pytest.fail("resource ownership replaced explicit caller signal policy")
            assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
            return 0

        assert invoke_cli(command, possible_json=False) == 0
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.process
@pytest.mark.parametrize("termination,expected", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_native_signal_before_runtime_publishes_one_safe_reply(
    tmp_path: Path, termination: signal.Signals, expected: int
) -> None:
    script = (
        "import os, signal\nimport oh_goon.cli as cli\n"
        "def interrupted(*args, **kwargs):\n"
        "    print('private-partial-signal-canary')\n"
        f"    os.kill(os.getpid(), {int(termination)})\n"
        "    return 0\n"
        "cli._main = interrupted\n"
        "raise SystemExit(cli.main(['doctor', '--json']))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=15
    )
    assert completed.returncode == expected, completed.stderr
    report = json.loads(completed.stdout)
    assert report["code"] == "CLI_INTERRUPTED" and report["_cli"]["exit_code"] == expected
    assert "private-partial-signal-canary" not in completed.stdout + completed.stderr


@pytest.mark.process
def test_json_transport_works_with_ascii_stdout_and_unicode_values(tmp_path: Path) -> None:
    script = (
        "import json\nfrom oh_goon.cli_result import invoke_cli\n"
        "def report():\n    print(json.dumps({'value': '\\u00f6'}, ensure_ascii=False))\n    return 0\n"
        "raise SystemExit(invoke_cli(report, possible_json=True))\n"
    )
    environment = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.decode("ascii"))["value"] == "ö"
