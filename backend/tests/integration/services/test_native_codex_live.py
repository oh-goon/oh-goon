"""Explicit live ChatGPT account refresh and coding through the original Run.

The installed Codex account owner signs in and refreshes credentials. This opt-in
proof uses real inference; the deterministic transport matrix remains separate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.integrations.agent_native_execution import (
    native_executable_sha256,
    parse_native_agent_events,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from tests.factories.native_codex_account import refresh_native_chatgpt_account

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


@pytest.mark.certification
def test_live_chatgpt_refresh_and_coding_use_original_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    source = repo / "ranges.py"
    source.write_text(_BROKEN_SOURCE, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    verifier = tmp_path / "independent-verification" / "verify.py"
    verifier.parent.mkdir()
    verifier.write_text(_VERIFICATION, encoding="utf-8")
    verifier_bytes = verifier.read_bytes()
    verification = (sys.executable, "-I", "-B", str(verifier), str(repo))
    baseline = run_command(*verification, cwd=repo)
    assert baseline.returncode == 1

    # The auth home is an explicit native input; all other application and host
    # state belongs to this isolated demonstration, outside repository source.
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_API_KEY", "must-not-replace-selected-chatgpt-login")
    account_facts = refresh_native_chatgpt_account(program, home)
    assert account_facts == {
        "managed_chatgpt": True,
        "refresh_advanced": True,
        "refresh_recorded_during_request": True,
        "account_identity_preserved": True,
        "auth_token_omitted": True,
    }

    task = (
        "Fix merge_ranges in ranges.py for inclusive integer ranges. Return sorted merged "
        "(start, end) tuples, combining overlapping or immediately adjacent ranges. "
        "Handle empty, unsorted, contained, duplicate and negative ranges without modifying "
        "the input list. Inputs have start <= end. Keep the public function and use only "
        "the Python standard library. Change ranges.py only. Independent verification "
        "runs after your coding Step."
    )
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="live-chatgpt",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            timeout_seconds=180,
            codex_login=CodexLoginConnection(home=str(home), model=model, reasoning_effort="low"),
        ),
        executable_sha256=native_executable_sha256(program),
    )
    native_facts: list[tuple[int, bool, bool]] = []
    native_occurrences: list[str] = []
    verification_launch_count = 0

    def command_runner(*argv: str, **kwargs: Any) -> CommandResult:
        nonlocal verification_launch_count
        if argv[0] == str(program):
            assert not native_occurrences, "The live coding Step must launch Codex once"
            prompt = kwargs["input_data"]
            assert isinstance(prompt, bytes)
            payload = json.loads(prompt)
            assert payload["task"] == task
            assert payload["source_authority"]
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
            # Never include native output, account details or RPC payloads in a
            # failing assertion. Keep only bounded typed execution facts.
            native_facts.append((result.returncode, events.settled, events.failed))
            return result
        if argv == verification:
            verification_launch_count += 1
        return run_command(*argv, **kwargs)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_process_isolation.run_command", command_runner
    )
    runtime = None
    try:
        runtime = ApplicationRuntime.build(standard_local=True)
        runtime.locations.add(repo)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repo,
            skill=None,
            task=task,
            verification=(verification,),
        )
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=210)
        assert native_facts == [(0, True, False)]
        assert verification_launch_count == 1
        outcome = detail.outcome
        assert outcome is FleetRunOutcome.SUCCESS
        steps = detail.game_tape.repositories[0].steps
        assert [step.status for step in steps] == [GameTapeStatus.PASSED, GameTapeStatus.PASSED]
        native = steps[0].native_agent_observations[0]
        observation = (
            native.settled,
            native.failed,
            native.agent,
            native.profile,
            native.occurrence,
        )
        assert observation == (True, False, "codex", "live-chatgpt", native_occurrences[0])
        assert verifier.read_bytes() == verifier_bytes
        assert source.read_text(encoding="utf-8") != _BROKEN_SOURCE
    finally:
        if runtime is not None:
            runtime.shutdown(wait_for_runs=True)
