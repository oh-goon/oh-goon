"""Bounded CLI presentation; command owners retain execution and result authority."""

from __future__ import annotations

import argparse
import io
import json
import signal
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from contextvars import ContextVar
from types import FrameType
from typing import TYPE_CHECKING, Any, NoReturn, TextIO

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

MAX_CLI_JSON_BYTES = 1024 * 1024
_REPLY: ContextVar[JsonReply | None] = ContextVar("oh_goon_cli_reply", default=None)
_EXIT_KINDS = {
    0: "SUCCESS",
    1: "NON_SUCCESS",
    2: "COMMAND_ERROR",
    3: "EVIDENCE_UNAVAILABLE",
    130: "INTERRUPTED",
    143: "TERMINATED",
}


class CliTermination(KeyboardInterrupt):
    """A default SIGTERM; the active Run owner still owns cancellation/settlement."""


class CliUsageRejected(Exception):
    def __init__(
        self,
        code: str = "CLI_ARGUMENTS_INVALID",
        action: str = "Use the selected command's --help and correct its arguments.",
    ) -> None:
        self.code, self.action = code, action
        super().__init__(code)


class CliOutputLimitExceeded(Exception):
    pass


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        if json_requested():
            # argparse messages may contain caller values, including private tasks.
            raise CliUsageRejected()
        super().error("Invalid arguments. Use this command's --help for accepted options.")

    def print_help(self, file: SupportsWrite[str] | None = None) -> None:
        if json_requested() and file is None:
            print(json.dumps({"schema": "oh-goon.cli-help/v1", "usage": self.format_help()}))
        else:
            super().print_help(file)


class CliVersionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(
            json.dumps({"schema": "oh-goon.cli-version/v1", "version": self.const})
            if json_requested()
            else self.const
        )
        parser.exit()


def json_requested() -> bool:
    reply = _REPLY.get()
    return reply is not None and reply.enabled


def select_json_output(as_json: bool) -> None:
    """Use argparse's resolved flag, rather than a task value spelling --json."""
    reply = _REPLY.get()
    if reply is not None:
        reply.enabled = as_json


def record_cli_cleanup_failure(code: str) -> None:
    """Transport a cleanup failure already observed by its original owner."""
    reply = _REPLY.get()
    if reply is not None:
        reply.cleanup_code = code


def is_default_cli_sigterm(handler: object) -> bool:
    """Recognize only native default policy or this invocation's default adaptor."""
    if handler == signal.SIG_DFL:
        return True
    reply = _REPLY.get()
    return reply is not None and reply.default_sigterm_owned and handler is _terminate


def _failure(code: str, action: str) -> dict[str, Any]:
    return {
        "schema": "oh-goon.cli-error/v1",
        "status": "error",
        "code": code,
        "safe_next_action": action,
    }


class JsonReply(io.TextIOBase):
    """Stage legacy owner JSON privately, then publish one validated bounded value.

    This checks transport shape only. It neither derives a Run status from text
    nor changes the original payload fields or numeric command outcome.
    """

    def __init__(self, output: TextIO, enabled: bool) -> None:
        self.output, self.enabled = output, enabled
        self.size = 0
        self.cleanup_code: str | None = None
        self.default_sigterm_owned = False
        self._staging_finished = False
        self._staging_cleanup_failed = False
        self._closing = False
        self.staging = tempfile.SpooledTemporaryFile(
            max_size=64 * 1024, mode="w+t", encoding="utf-8"
        )

    def write(self, text: str) -> int:
        if not self.enabled:
            return self.output.write(text)
        self.size += len(text.encode("utf-8"))
        if self.size > MAX_CLI_JSON_BYTES:
            raise CliOutputLimitExceeded
        return self.staging.write(text)

    def flush(self) -> None:
        if not self.closed and not self._closing:
            if self.enabled:
                if not self._staging_finished:
                    self.staging.flush()
            else:
                self.output.flush()

    def isatty(self) -> bool:
        return False if self.enabled else self.output.isatty()

    def fileno(self) -> int:
        if self.enabled:
            raise io.UnsupportedOperation("JSON output uses the bounded reply owner")
        return self.output.fileno()

    def close(self) -> None:
        if not self.closed:
            cleanup_was_reported = self._staging_cleanup_failed
            self._finish_staging()
            if self._staging_cleanup_failed and not cleanup_was_reported:
                print("CLI_OUTPUT_STAGING_CLEANUP_FAILED", file=sys.stderr)
            self._closing = True
            super().close()

    def _finish_staging(self) -> None:
        if not self._staging_finished:
            try:
                self.staging.close()
            except (OSError, UnicodeError):
                self._staging_cleanup_failed = True
            finally:
                self._staging_finished = True

    def publish(self, result: int, failure: dict[str, Any] | None) -> int:
        if not self.enabled:
            self._finish_staging()
            if failure is not None:
                print(f"{failure['code']}: {failure['safe_next_action']}", file=sys.stderr)
            if self._staging_cleanup_failed:
                print("CLI_OUTPUT_STAGING_CLEANUP_FAILED", file=sys.stderr)
                return result if result != 0 else 2
            return result
        if failure is None:
            try:
                self.staging.seek(0)
                payload = json.load(self.staging)
            except OSError:
                payload = {
                    **_failure(
                        "CLI_OUTPUT_UNAVAILABLE",
                        "The private reply could not be read. Inspect retained Run/state evidence before retrying execution.",
                    ),
                    "command_exit_code": result,
                }
                result = 2
            except (ValueError, UnicodeError, RecursionError):
                if self.size == 0 and result != 0:
                    payload = _failure(
                        "CLI_INTERRUPTED" if result in {130, 143} else "CLI_COMMAND_FAILED",
                        "Inspect retained Run/state evidence before retrying; this response grants no repair or replay authority.",
                    )
                else:
                    payload = _failure(
                        "CLI_OUTPUT_INVALID",
                        "The command did not produce one JSON value. Preserve retained Run evidence and report the CLI presentation failure.",
                    )
                    payload["command_exit_code"] = result
                    result = 2
        else:
            payload = failure
        self._finish_staging()
        if not isinstance(payload, dict):
            payload = {"schema": "oh-goon.cli-result/v1", "result": payload}
        command_result = result
        if self._staging_cleanup_failed and result == 0:
            result = 2
        payload["_cli"] = {
            "schema": "oh-goon.cli/v1",
            "exit_code": result,
            "exit_kind": _EXIT_KINDS.get(result, "COMMAND_NON_SUCCESS"),
        }
        if self.cleanup_code is not None:
            payload["_cli"]["cleanup"] = {"status": "UNVERIFIED", "code": self.cleanup_code}
        if self._staging_cleanup_failed:
            payload["_cli"]["command_exit_code"] = command_result
            payload["_cli"]["presentation_cleanup"] = {
                "status": "UNVERIFIED",
                "code": "CLI_OUTPUT_STAGING_CLEANUP_FAILED",
            }
        try:
            rendered = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)
        except (ValueError, RecursionError):
            return self.publish(
                2,
                {
                    **_failure("CLI_OUTPUT_INVALID", "The command returned unsupported JSON data."),
                    "command_exit_code": result,
                },
            )
        if len(rendered) + 1 > MAX_CLI_JSON_BYTES:
            return self.publish(
                2,
                {
                    **_failure(
                        "CLI_OUTPUT_LIMIT_EXCEEDED",
                        "Select a smaller page or bounded handoff. The original Run/evidence outcome remains unchanged.",
                    ),
                    "command_exit_code": result,
                },
            )
        self.output.write(rendered + "\n")
        self.output.flush()
        return result


def _terminate(_signum: int, _frame: FrameType | None) -> NoReturn:
    raise CliTermination


@contextmanager
def _default_sigterm() -> Iterator[None]:
    if (
        threading.current_thread() is not threading.main_thread()
        or signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
    ):
        yield
        return
    previous = signal.signal(signal.SIGTERM, _terminate)
    reply = _REPLY.get()
    if reply is not None:
        reply.default_sigterm_owned = True
    try:
        yield
    finally:
        if signal.getsignal(signal.SIGTERM) is _terminate:
            signal.signal(signal.SIGTERM, previous)
        if reply is not None:
            reply.default_sigterm_owned = False


def invoke_cli(
    command: Callable[[], int],
    *,
    possible_json: bool,
    propagate: tuple[type[Exception], ...] = (),
) -> int:
    failure = None
    with JsonReply(sys.stdout, possible_json) as reply:
        token = _REPLY.set(reply)
        try:
            with redirect_stdout(reply), _default_sigterm():
                try:
                    result = command()
                except CliUsageRejected as exc:
                    result, failure = 2, _failure(exc.code, exc.action)
                except KeyboardInterrupt as exc:
                    result = 143 if isinstance(exc, CliTermination) else 130
                    failure = _failure(
                        "CLI_INTERRUPTED",
                        "Inspect retained Run/state evidence; interruption alone does not establish execution settlement or cleanup.",
                    )
                except CliOutputLimitExceeded:
                    result, failure = (
                        2,
                        _failure(
                            "CLI_OUTPUT_LIMIT_EXCEEDED",
                            "Select a smaller page or bounded handoff; inspect the original Run before retrying execution.",
                        ),
                    )
                except SystemExit as exc:
                    if not reply.enabled:
                        raise
                    result = exc.code if isinstance(exc.code, int) else 2
                    failure = (
                        None
                        if result == 0
                        else _failure("CLI_ARGUMENTS_INVALID", "Use the selected command's --help.")
                    )
                except Exception as exc:
                    if isinstance(exc, propagate):
                        raise
                    # No arbitrary exception text, argv, prompt, source or dotenv
                    # values enter the final diagnostic or an automatic traceback.
                    result, failure = (
                        2,
                        _failure(
                            "CLI_COMMAND_FAILED",
                            "Inspect the selected configuration and retained Run/state evidence; this failure grants no repair or replay authority.",
                        ),
                    )
            return reply.publish(result, failure)
        finally:
            _REPLY.reset(token)
