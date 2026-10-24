"""Legacy opt-in live coding baseline through the original local Run.

Native Codex owns its existing sign-in. This proof uses real inference and an
immutable independent verifier; it never inspects native credential contents or
requests forced refresh. It does not qualify native-service/private-store exclusion.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from oh_goon.cli_ai import main as ai_main
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.integrations.agent_native_execution import (
    parse_native_agent_events,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

_BROKEN_SOURCE = """def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start < result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result
"""

_VERIFICATION = """import pathlib, runpy, sys, unittest

merge_ranges = runpy.run_path(str(pathlib.Path(sys.argv[1]) / "ranges.py"))["merge_ranges"]

class MergeRangesContract(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(merge_ranges([]), [])
        self.assertEqual(merge_ranges([(4, 4)]), [(4, 4)])

    def test_unsorted_overlapping_and_contained(self):
        self.assertEqual(merge_ranges([(6, 9), (1, 4), (3, 7), (2, 2)]), [(1, 9)])

    def test_touching_adjacent_and_separate(self):
        self.assertEqual(merge_ranges([(8, 9), (3, 5), (1, 3), (6, 6)]), [(1, 6), (8, 9)])

    def test_negative_and_duplicate_ranges(self):
        self.assertEqual(merge_ranges([(0, 0), (-3, -1), (-3, -1), (2, 4)]), [(-3, 0), (2, 4)])

    def test_input_is_unchanged(self):
        ranges = [(8, 9), (1, 3), (2, 6)]
        before = list(ranges)
        self.assertEqual(merge_ranges(ranges), [(1, 6), (8, 9)])
        self.assertEqual(ranges, before)

unittest.main(argv=["live-codex-verification"])
"""

_EVENTLOG_SOURCES: dict[str, str] = {
    "eventlog/__init__.py": "",
    "eventlog/records.py": """import json


def iter_records(lines):
    for line in lines:
        yield json.loads(line)
""",
    "eventlog/summary.py": """def summarize(records, service=None):
    totals = {}
    for record in records:
        name = record["service"]
        if service and service not in name:
            continue
        item = totals.setdefault(name, {"service": name, "count": 0, "errors": 0, "total_ms": 0})
        item["count"] += 1
        item["errors"] += int(record["status"] == "error")
        item["total_ms"] = record["duration_ms"]
    return list(totals.values())
""",
    "eventlog/__main__.py": """import argparse
import json
import sys

from .records import iter_records
from .summary import summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?")
    parser.add_argument("--service")
    args = parser.parse_args()
    if args.input:
        with open(args.input, encoding="utf-8") as stream:
            records = list(iter_records(stream))
    else:
        records = list(iter_records(sys.stdin))
    print(json.dumps(summarize(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "README.md": """# Eventlog service usage report

Repair this small standard-library package without changing its public API.

`eventlog.records.iter_records(lines)` lazily consumes an iterable of NDJSON
text lines. Skip whitespace-only lines. Each remaining line must be a JSON
object containing a nonempty string `service`, a nonnegative integer
`duration_ms` (booleans are invalid), and `status` equal to `ok` or `error`.
Yield the validated dictionaries. Invalid JSON or a malformed record raises
`ValueError` whose message starts `line N:`, with the physical one-based line
number including skipped blank lines. Do not read ahead before the next record
is requested.

`eventlog.summary.summarize(records, service=None)` consumes an iterable once,
without modifying its records. Return a list sorted alphabetically by service,
with one dictionary per service containing exactly `service`, `count`, `errors`,
and `total_ms`. Count every record, count records with status `error`, and sum
all durations. If `service` is not None, include only that exact service name;
an absent service produces an empty list. Empty input also produces an empty
list.

`python -m eventlog [--service NAME] [INPUT]` reports NDJSON from stdin when
INPUT is omitted, or from a UTF-8 file. Apply the same exact service filter and
print one JSON list followed by a newline. Success exits zero with no stderr.
Invalid records or input-file errors exit 2, write a useful diagnostic to
stderr, and write nothing to stdout. A malformed record's diagnostic retains
its physical line number. Process the records as an iterable instead of loading
the entire input into a list.
""",
}

_EVENTLOG_TASK = (
    "Repair the eventlog NDJSON service usage package according to README.md. "
    "Fix the parser's lazy validation and line-numbered errors, aggregation and exact "
    "service filtering, and the CLI's stdin/file handling and exit behavior. Keep the "
    "public APIs and use only the Python standard library. Before editing, execute the "
    "exact repository_tools.list Hashmarks command from the supplied instructions. "
    "Change only eventlog/records.py, eventlog/summary.py and eventlog/__main__.py. "
    "Independent verification runs after your coding Step."
)

_EVENTLOG_VERIFICATION = r"""import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from eventlog.records import iter_records
from eventlog.summary import summarize


def event(service="api", duration_ms=3, status="ok"):
    return {"service": service, "duration_ms": duration_ms, "status": status}


def ndjson(*records):
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def cli(*arguments, text=""):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('eventlog',run_name='__main__')",
         str(repository), *arguments],
        input=text, text=True, capture_output=True, timeout=10,
    )


class EventlogContract(unittest.TestCase):
    def test_empty_and_blank_lines(self):
        self.assertEqual(list(iter_records([])), [])
        self.assertEqual(list(iter_records(["\n", "  \t\n"])), [])
        self.assertEqual(summarize(iter(())), [])
        self.assertEqual(list(iter_records(["\n", ndjson(event(duration_ms=0)), " \n"])),
                         [event(duration_ms=0)])

    def test_mixed_service_aggregation_and_order(self):
        records = [event("zeta", 4), event("zeta", 7, "error"),
                   event("api", 3, "error"), event("api", 2)]
        before = [dict(record) for record in records]
        self.assertEqual(summarize(records), [
            {"service": "api", "count": 2, "errors": 1, "total_ms": 5},
            {"service": "zeta", "count": 2, "errors": 1, "total_ms": 11},
        ])
        self.assertEqual(records, before)

    def test_lazy_parser_and_single_pass_summary(self):
        consumed = []
        def lines():
            for index, value in enumerate([ndjson(event()), "\n", ndjson(event("worker", 8))]):
                consumed.append(index)
                yield value
        parsed = iter(iter_records(lines()))
        self.assertEqual(consumed, [])
        self.assertEqual(next(parsed), event())
        self.assertEqual(consumed, [0])
        self.assertEqual(next(parsed), event("worker", 8))
        self.assertEqual(consumed, [0, 1, 2])
        with self.assertRaises(StopIteration):
            next(parsed)
        class Once:
            def __init__(self):
                self.used = False
            def __iter__(self):
                if self.used:
                    raise AssertionError("records must be consumed once")
                self.used = True
                return iter([event(), event(duration_ms=5, status="error")])
        self.assertEqual(summarize(Once()),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 8}])

    def test_duration_validation(self):
        for value in [-1, 1.5, True, False, None, "3"]:
            with self.subTest(duration=value), self.assertRaisesRegex(ValueError, r"^line 1:"):
                list(iter_records([ndjson(event(duration_ms=value))]))

    def test_invalid_records_use_physical_line_number(self):
        for invalid in ("{broken\n", ndjson(event(duration_ms=True))):
            with self.subTest(invalid=invalid):
                values = ["\n", ndjson(event()), " \t\n", invalid]
                parsed = iter(iter_records(values))
                self.assertEqual(next(parsed), event())
                with self.assertRaisesRegex(ValueError, r"^line 4:"):
                    next(parsed)

    def test_record_shape_service_and_status_validation(self):
        invalid = [[], None, {}, event(service=""), event(service=None), event(service=7),
                   event(status="timeout"), event(status=True)]
        invalid.extend({key: value for key, value in event().items() if key != missing}
                       for missing in ("service", "duration_ms", "status"))
        for record in invalid:
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, r"^line 1:"):
                list(iter_records([ndjson(record)]))

    def test_exact_service_filter(self):
        records = [event("api-v2", 99), event("api", 7, "error"), event("api", 1)]
        self.assertEqual(summarize(iter(records), service="api"),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 8}])
        self.assertEqual(summarize(iter(records), service="missing"), [])
        self.assertEqual(summarize(iter(records), service=""), [])

    def test_cli_stdin_filter_and_errors(self):
        result = cli("--service", "api", text=ndjson(event("api-v2", 99), event("api", 7)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout),
                         [{"service": "api", "count": 1, "errors": 0, "total_ms": 7}])
        for invalid in ("{broken\n", ndjson(event(duration_ms=True))):
            with self.subTest(invalid=invalid):
                failed = cli(text=ndjson(event()) + "\n" + invalid)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, "")
                self.assertRegex(failed.stderr, r"line 3:")

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_AS streaming proof requires Linux")
    def test_cli_streams_stdin_and_file_with_bounded_memory(self):
        count = 131072
        row = ndjson(event(status="error"))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "large-events.ndjson"
            with path.open("w", encoding="utf-8") as stream:
                for _ in range(count):
                    stream.write(row)
            for mode in ("stdin", "file"):
                with self.subTest(mode=mode), path.open("rb") as stream:
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", "-c",
                         "import resource,runpy,sys;"
                         "resource.setrlimit(resource.RLIMIT_AS,(64*1024*1024,64*1024*1024));"
                         "sys.path.insert(0,sys.argv.pop(1));"
                         "runpy.run_module('eventlog',run_name='__main__')",
                         str(repository), *([str(path)] if mode == "file" else [])],
                        stdin=stream, text=True, capture_output=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assertTrue(result.stdout.endswith("\n"))
                    self.assertEqual(json.loads(result.stdout),
                                     [{"service": "api", "count": count,
                                       "errors": count, "total_ms": count * 3}])

    def test_cli_utf8_file_and_input_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "events.ndjson"
            path.write_text(ndjson(event("caf\u00e9", 2), event("caf\u00e9", 5, "error")), encoding="utf-8")
            result = cli(str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout),
                             [{"service": "caf\u00e9", "count": 2, "errors": 1, "total_ms": 7}])
            path.write_text(ndjson(event()) + "\n{broken\n", encoding="utf-8")
            failed = cli(str(path))
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertRegex(failed.stderr, r"line 3:")
            missing = cli(str(path.with_name("missing.ndjson")))
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertTrue(missing.stderr.strip())
            path.write_bytes(b"\xff")
            invalid_encoding = cli(str(path))
            self.assertEqual(invalid_encoding.returncode, 2)
            self.assertEqual(invalid_encoding.stdout, "")
            self.assertTrue(invalid_encoding.stderr.strip())


unittest.main(argv=["live-codex-eventlog-verification"])
"""


@pytest.mark.certification
@pytest.mark.parametrize("scenario", ["ranges", "eventlog"])
def test_legacy_live_chatgpt_coding_uses_original_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    account_home = os.environ.get("OH_GOON_LIVE_CODEX_HOME")
    if not executable or not account_home:
        pytest.skip("Select both the installed Codex executable and a live ChatGPT account home")
    program = Path(executable).resolve(strict=True)
    home = Path(account_home).resolve(strict=True)
    assert home.is_dir()
    model = os.environ.get("OH_GOON_LIVE_CODEX_MODEL", "gpt-6-luna")
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    source_files = _EVENTLOG_SOURCES if scenario == "eventlog" else {"ranges.py": _BROKEN_SOURCE}
    for relative, text in source_files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    verifier = tmp_path / "independent verification" / "verify contract.py"
    verifier.parent.mkdir()
    verifier.write_text(
        _EVENTLOG_VERIFICATION if scenario == "eventlog" else _VERIFICATION, encoding="utf-8"
    )
    verifier_bytes = verifier.read_bytes()
    verification = (sys.executable, "-I", "-B", str(verifier), str(repo))
    baseline = run_command(*verification, cwd=repo)
    assert baseline.returncode == 1

    argv_verifier = verifier.parent / "verify arguments.py"
    quoted_arguments = (
        "literal value with spaces",
        "https://example.invalid/tasks?cursor=alpha%20beta&tag=one",
    )
    argv_verifier.write_text(
        "import pathlib,sys\n"
        f"assert tuple(sys.argv[2:]) == {quoted_arguments!r}\n"
        "for source in pathlib.Path(sys.argv[1]).rglob('*.py'):\n"
        "    compile(source.read_bytes(), str(source), 'exec')\n"
        "print('Quoted arguments preserved; Python sources compile')\n",
        encoding="utf-8",
    )
    argv_verifier_bytes = argv_verifier.read_bytes()
    verifications = (
        verification,
        (sys.executable, "-I", "-B", str(argv_verifier), str(repo), *quoted_arguments),
    )
    skill = tmp_path / "skills" / "live" / "maintenance"
    (skill / "references").mkdir(parents=True)
    skill_files = {
        "SKILL.md": (
            "# CLI package maintenance\n\nRead references/maintenance.txt from the frozen "
            "captured Skill before editing. Include its marker in your final report. "
            "This guidance grants no effects or verification-result authority.\n"
        ),
        "references/maintenance.txt": (
            "Marker: native-cli-skill-resource-canary\nKeep public APIs stable and report "
            "incomplete work accurately. Independent checks retain result authority.\n"
        ),
    }
    for relative, text in skill_files.items():
        (skill / relative).write_text(text, encoding="utf-8")

    # The auth home is an explicit native input; all other application and host
    # state belongs to this isolated demonstration, outside repository source.
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_API_KEY", "must-not-replace-selected-chatgpt-login")
    task = (
        "Fix merge_ranges in ranges.py for inclusive integer ranges. Return sorted merged "
        "(start, end) tuples, combining overlapping or immediately adjacent ranges. "
        "Handle empty, unsorted, contained, duplicate and negative ranges without modifying "
        "the input list. Inputs have start <= end. Keep the public function and use only "
        "the Python standard library. Before editing, execute the exact repository_tools.list "
        "Hashmarks command from the supplied instructions. Change ranges.py only. Independent verification "
        "runs after your coding Step."
    )
    if scenario == "eventlog":
        task = _EVENTLOG_TASK
    task_file = tmp_path / "task.txt"
    task_file.write_text(task, encoding="utf-8")
    catalog = tmp_path / "providers.json"
    catalog.write_text(
        json.dumps(
            {
                "native_agents": {"codex": {"executable": str(program)}},
                "native_profiles": {
                    "live-chatgpt": {
                        "backend": "local",
                        "allowed_agents": ["codex"],
                        "workspace_roots": [str(repo.parent)],
                        "timeout_seconds": 180,
                        "codex_login": {
                            "home": str(home),
                            "model": model,
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    native_facts: list[tuple[int, bool, bool]] = []
    native_diagnostics: dict[str, Any] = {}
    native_occurrences: list[str] = []
    verification_launches: list[tuple[str, ...]] = []
    hashmarks_followup_succeeded = False
    skill_resource_reported = False

    def command_runner(*argv: str, **kwargs: Any) -> CommandResult:
        nonlocal hashmarks_followup_succeeded, skill_resource_reported
        if argv[0] == str(program):
            assert not native_occurrences, "The live coding Step must launch Codex once"
            prompt = kwargs["input_data"]
            assert isinstance(prompt, bytes)
            payload = json.loads(prompt)
            assert payload["task"] == task
            assert payload["source_authority"]
            assert payload["skill_instructions"] == skill_files["SKILL.md"]
            for relative, text in skill_files.items():
                assert (Path(payload["captured_skill"]) / relative).read_bytes() == text.encode()
            evidence = json.loads(payload["repository_evidence"])
            assert evidence["producer"] == "Hashmarks"
            assert (
                evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
            )
            assert evidence["capture_identity"] == payload["identity"]["candidate_identity"]
            native_occurrences.append(payload["identity"]["reviewer"])
            settings: dict[str, Any] = {}
            for index, argument in enumerate(argv[:-1]):
                if argument == "-c":
                    settings.update(tomllib.loads(argv[index + 1]))
            assert Path(settings["model_instructions_file"]).read_bytes() == prompt
            assert settings["model"] == model
            assert settings["model_reasoning_effort"] == "low"
            assert "openai_base_url" not in settings and "chatgpt_base_url" not in settings
            native_environment_names = set(kwargs["env"])
            assert not {
                "CODEX_API_KEY",
                "OPENAI_API_KEY",
                "CODEX_ACCESS_TOKEN",
                "OPENAI_BASE_URL",
            }.intersection(native_environment_names)
            actual_home = kwargs["env"]["CODEX_HOME"]
            assert actual_home == str(home)
            result = run_command(*argv, **kwargs)
            events = parse_native_agent_events("codex", result.stdout, returncode=result.returncode)
            skill_resource_reported = "native-cli-skill-resource-canary" in events.final_text
            item_facts: list[dict[str, str | int]] = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line).get("item")
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if (
                    item_type == "command_execution"
                    and item.get("exit_code") == 0
                    and "oh_goon.integrations.hashmarks.agent_tools" in item.get("command", "")
                ):
                    for output_line in item.get("aggregated_output", "").splitlines():
                        try:
                            followup = json.loads(output_line)
                        except ValueError:
                            continue
                        if isinstance(followup, dict) and (
                            followup.get("producer") == "Hashmarks"
                            and followup.get("capture_identity")
                            == payload["identity"]["candidate_identity"]
                            and followup.get("producer_implementation_identity")
                            == require_loaded_hashmarks_runtime()
                        ):
                            hashmarks_followup_succeeded = True
                if len(item_facts) >= 32:
                    continue
                fact: dict[str, str | int] = {
                    "type": item_type
                    if item_type
                    in (
                        "agent_message",
                        "error",
                        "command_execution",
                        "mcp_tool_call",
                        "file_change",
                    )
                    else "other"
                }
                if item.get("status") in ("in_progress", "completed", "failed"):
                    fact["status"] = item["status"]
                if type(item.get("exit_code")) is int:
                    fact["exit_code"] = item["exit_code"]
                item_facts.append(fact)
            native_diagnostics.update(
                returncode=result.returncode,
                settled=events.settled,
                failed=events.failed,
                items=item_facts,
                observations=[item.summary for item in events.observations],
            )
            # Never include native output, account details or RPC payloads in a
            # failing assertion. Keep only bounded typed execution facts.
            native_facts.append((result.returncode, events.settled, events.failed))
            return result
        if argv in verifications:
            verification_launches.append(argv)
        return run_command(*argv, **kwargs)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_process_isolation.run_command", command_runner
    )
    try:
        result = ai_main(
            [
                "--agent",
                "codex",
                "--profile",
                "live-chatgpt",
                "--config",
                str(catalog),
                "--workspace",
                str(repo),
                "--skill",
                str(skill),
                "--prompt-file",
                str(task_file),
                "--headless",
                "--json",
                *(
                    value
                    for command in verifications
                    for value in ("--verify", shlex.join(command))
                ),
            ]
        )
        captured = capsys.readouterr()
        assert result == 0
        try:
            detail = json.loads(captured.out)
        except ValueError:
            pytest.fail("The CLI must emit one valid Run detail", pytrace=False)
        assert native_facts == [(0, True, False)]
        assert hashmarks_followup_succeeded
        assert skill_resource_reported
        assert verification_launches == list(verifications)
        assert detail["outcome"] == FleetRunOutcome.SUCCESS.value
        steps = detail["game_tape"]["repositories"][0]["steps"]
        assert [step["status"] for step in steps] == [GameTapeStatus.PASSED.value] * 3
        assert [tuple(step["command"]) for step in steps[1:]] == list(verifications)
        native = steps[0]["native_agent_observations"][0]
        observation = (
            native["settled"],
            native["failed"],
            native["agent"],
            native["profile"],
            native["occurrence"],
        )
        assert observation == (True, False, "codex", "live-chatgpt", native_occurrences[0])
        assert verifier.read_bytes() == verifier_bytes
        assert argv_verifier.read_bytes() == argv_verifier_bytes
        actual_paths = {
            path.relative_to(repo).as_posix()
            for path in repo.rglob("*")
            if not {".git", "__pycache__"}.intersection(path.relative_to(repo).parts)
            and (path.is_symlink() or path.is_file())
        }
        assert actual_paths == set(source_files)
        changed_paths = {
            relative
            for relative, text in source_files.items()
            if (repo / relative).read_bytes() != text.encode()
        }
        assert changed_paths
        assert changed_paths <= (
            {"eventlog/records.py", "eventlog/summary.py", "eventlog/__main__.py"}
            if scenario == "eventlog"
            else {"ranges.py"}
        )
        for relative, text in skill_files.items():
            assert (skill / relative).read_bytes() == text.encode()
    except AssertionError as error:
        native_diagnostics["verification_launches"] = len(verification_launches)
        for path, expected, key, invert in (
            (verifier, verifier_bytes, "independent_verifier_unchanged", False),
            (argv_verifier, argv_verifier_bytes, "argument_verifier_unchanged", False),
        ):
            same: bool | None = None
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is not None:
                try:
                    descriptor = os.open(path, os.O_RDONLY | nofollow | os.O_NONBLOCK)
                    with os.fdopen(descriptor, "rb") as stream:
                        state = os.fstat(stream.fileno())
                        if stat.S_ISREG(state.st_mode) and state.st_size <= 65536:
                            payload = stream.read(65537)
                            if len(payload) <= 65536:
                                same = payload == expected
                except OSError:
                    pass
            native_diagnostics[key] = (not same if invert else same) if same is not None else None
        error.add_note("Bounded native execution facts: " + json.dumps(native_diagnostics))
        raise
