from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from oh_goon.repository_tooling.test_batches import (
    _bounded_failure_output,
    _resource_pressure_failure,
)


def _isolated_receipt_plugin():
    """Load fresh plugin globals so receipt state cannot bleed between unit tests."""
    root = Path(__file__).resolve().parents[4]
    path = root / "backend/src/oh_goon/repository_tooling/pytest_batch_receipts.py"
    spec = importlib.util.spec_from_file_location("_oh_goon_test_receipt_plugin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_receipt_plugin_loader_intentionally_isolates_module_global_state() -> None:
    first = _isolated_receipt_plugin()
    second = _isolated_receipt_plugin()

    assert first is not second
    assert first._STATE is not second._STATE
    first._STATE["sentinel"] = {"completed": 1}
    assert second._STATE == {}


def test_failure_output_is_bounded_but_keeps_head_and_tail() -> None:
    output = "\n".join(f"line-{index}" for index in range(300)) + "\n"
    bounded = _bounded_failure_output(output, max_lines=30)
    assert "line-0" in bounded
    assert "line-299" in bounded
    assert "diagnostic lines omitted" in bounded
    assert len(bounded.splitlines()) == 31


def test_resource_pressure_classifier_is_narrow() -> None:
    assert _resource_pressure_failure(
        1, "BlockingIOError: [Errno 11] Resource temporarily unavailable"
    )
    assert _resource_pressure_failure(1, "RuntimeError: can't start new thread")
    assert not _resource_pressure_failure(1, "AssertionError: expected 2 got 3")
    assert not _resource_pressure_failure(124, "Resource temporarily unavailable")


def test_timeout_bisection_recovers_multi_member_batch_without_weakening_leaf_timeout(
    monkeypatch,
) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    calls: list[tuple[str, ...]] = []

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        calls.append(members)
        if len(members) > 1:
            return 124, "group timed out\n", 1.0
        return 0, f"{members[0]} passed\n", 0.1

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=("a.py", "b.py", "c.py", "d.py"))
    rc, output, _elapsed, recovery_events = batches._run(batch, timeout=10, marker=None)
    assert rc == 0
    assert "BATCH TIMEOUT RECOVERY: bisecting 4 members into 2+2" in output
    assert recovery_events
    assert recovery_events[0].kind == "timeout_refinement"
    assert calls == [
        ("a.py", "b.py", "c.py", "d.py"),
        ("a.py", "b.py"),
        ("a.py",),
        ("b.py",),
        ("c.py", "d.py"),
        ("c.py",),
        ("d.py",),
    ]


def test_timeout_refinement_probe_preserves_full_leaf_timeout() -> None:
    import oh_goon.repository_tooling.test_batches as batches

    assert batches._refinement_probe_timeout(leaf_timeout=120, member_count=8) == 30
    assert batches._refinement_probe_timeout(leaf_timeout=120, member_count=4) == 40
    assert batches._refinement_probe_timeout(leaf_timeout=120, member_count=2) == 60
    assert batches._refinement_probe_timeout(leaf_timeout=120, member_count=1) == 120


def test_timeout_bisection_uses_probe_budgets_before_full_leaf_timeout(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    observed: list[tuple[tuple[str, ...], int]] = []

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        observed.append((members, timeout))
        return (124, "timeout\n", 0.1) if len(members) > 1 else (0, "pass\n", 0.1)

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=("a.py", "b.py", "c.py", "d.py"))
    rc, _output, _elapsed, _events = batches._run(batch, timeout=120, marker=None)
    assert rc == 0
    assert observed == [
        (("a.py", "b.py", "c.py", "d.py"), 120),
        (("a.py", "b.py"), 60),
        (("a.py",), 120),
        (("b.py",), 120),
        (("c.py", "d.py"), 60),
        (("c.py",), 120),
        (("d.py",), 120),
    ]


def test_initial_batch_receives_declared_timeout_before_any_refinement(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    observed: list[int] = []

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        observed.append(timeout)
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=tuple(f"f{i}.py" for i in range(8)))
    rc, _output, _elapsed, _events = batches._run(batch, timeout=120, marker=None)
    assert rc == 0
    assert observed == [120]


def test_timeout_bisection_does_not_hide_single_member_timeout(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    monkeypatch.setattr(
        batches,
        "_run_members",
        lambda members, *, timeout, marker: (124, "leaf timeout\n", 1.0),
    )
    batch = batches.Batch(index=1, total=1, members=("slow.py",))
    rc, output, _elapsed, recovery_events = batches._run(batch, timeout=10, marker=None)
    assert rc == 124
    assert output == "leaf timeout\n"
    assert recovery_events == ()


def test_timeout_refinement_history_reuses_known_split_on_retry(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    history = batches.RefinementHistory()
    calls: list[tuple[str, ...]] = []

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        calls.append(members)
        if len(members) > 1:
            return 124, "timeout\n", 0.1
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=("a.py", "b.py"))
    first = batches._run(batch, timeout=120, marker=None, refinement_history=history)
    assert first[0] == 0
    assert calls == [("a.py", "b.py"), ("a.py",), ("b.py",)]

    calls.clear()
    second = batches._run(batch, timeout=120, marker=None, refinement_history=history)
    assert second[0] == 0
    assert calls == [("a.py",), ("b.py",)]
    assert any(event.action == "reuse_bisection" for event in second[3])
    assert "reusing prior refinement" in second[1]


def test_timeout_refinement_stops_after_first_decisive_child_failure(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    calls: list[tuple[str, ...]] = []

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        calls.append(members)
        if len(members) > 1:
            return 124, "group timeout\n", 0.1
        if members == ("a.py",):
            return 124, "leaf timeout\n", 0.1
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=("a.py", "b.py"))
    rc, output, _elapsed, _events = batches._run(batch, timeout=120, marker=None)

    assert rc == 124
    assert calls == [("a.py", "b.py"), ("a.py",)]
    assert "decisive refined child failure" in output


def test_initial_batch_does_not_launch_without_full_declared_budget(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    calls: list[tuple[tuple[str, ...], int]] = []
    monkeypatch.setattr(batches.time, "monotonic", lambda: 100.0)

    def fake_run_members(members: tuple[str, ...], *, timeout: int, marker: str | None):
        calls.append((members, timeout))
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_members", fake_run_members)
    batch = batches.Batch(index=1, total=1, members=("a.py", "b.py"))
    rc, output, _elapsed, _events = batches._run(
        batch,
        timeout=120,
        marker=None,
        deadline=159.0,
    )

    assert rc == 124
    assert calls == []
    assert "insufficient suite budget for full refinement proof" in output
    assert "required=120s" in output


def test_run_members_disables_ambient_pytest_plugin_autoload(monkeypatch) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    captured: dict[str, object] = {}

    def fake_run_command(command, *, timeout: int, env=None):
        captured["command"] = command
        captured["env"] = env
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_command", fake_run_command)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")

    rc, _output, _elapsed = batches._run_members(
        ("tests/test_example.py",),
        timeout=30,
        marker=None,
    )

    assert rc == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_run_members_keeps_explicit_receipt_plugin_under_isolated_pytest(
    monkeypatch, tmp_path: Path
) -> None:
    import oh_goon.repository_tooling.test_batches as batches

    captured: dict[str, object] = {}

    def fake_run_command(command, *, timeout: int, env=None):
        captured["command"] = command
        captured["env"] = env
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_command", fake_run_command)
    rc, _output, _elapsed = batches._run_members(
        ("tests/test_example.py",),
        timeout=30,
        marker=None,
        receipt_directory=tmp_path / "receipts",
        receipt_suite_identity="sha256:" + "a" * 64,
    )

    assert rc == 0
    command = captured["command"]
    env = captured["env"]
    assert isinstance(command, list)
    assert ["-p", "oh_goon.repository_tooling.pytest_batch_receipts"] == command[
        command.index("-p") : command.index("-p") + 2
    ]
    assert isinstance(env, dict)
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_run_members_records_actual_physical_group_identity(monkeypatch, tmp_path: Path) -> None:
    import oh_goon.repository_tooling.test_batches as batches
    from oh_goon.repository_tooling.test_batch_receipts import batch_identity

    captured: dict[str, object] = {}

    def fake_run_command(command, *, timeout: int, env=None):
        captured["command"] = command
        captured["env"] = env
        return 0, "pass\n", 0.1

    monkeypatch.setattr(batches, "_run_command", fake_run_command)
    physical_members = ("tests/a.py::test_one", "tests/b.py::test_two")
    rc, _output, _elapsed = batches._run_members(
        physical_members,
        timeout=90,
        marker=None,
        receipt_directory=tmp_path / "receipts",
        receipt_suite_identity="sha256:" + "a" * 64,
        receipt_execution_plan_identity="sha256:" + "b" * 64,
    )

    assert rc == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY"] == batch_identity(physical_members)


def test_member_receipt_store_requires_exact_suite_and_member_identity_not_batch_grouping(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import MemberReceiptStore, batch_identity

    store = MemberReceiptStore(tmp_path / "receipts", suite_identity="sha256:" + "a" * 64)
    members = ("tests/test_example.py", "tests/test_other.py")
    path = store.record(
        members[0],
        outcome="PYTEST_MEMBER_PASSED",
        execution_plan_identity="sha256:" + "b" * 64,
        observed_batch_identity=batch_identity(members),
    )

    assert path.exists()
    assert store.reusable(members[0])
    assert not store.reusable(members[1])
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert payload["observed_batch_identity"] == batch_identity(members)
    assert payload["status"] == "PASSED"
    assert not list(path.parent.glob("*.tmp"))


def test_pytest_receipt_plugin_persists_completed_file_member_before_later_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from oh_goon.repository_tooling.test_batch_receipts import MemberReceiptStore, batch_identity

    plugin = _isolated_receipt_plugin()
    suite_identity = "sha256:" + "b" * 64
    members = ("a.py", "b.py")
    batch = batch_identity(members)
    receipt_dir = tmp_path / "receipts"
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_DIRECTORY", str(receipt_dir))
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_SUITE_IDENTITY", suite_identity)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY", batch)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_EXECUTION_PLAN_IDENTITY", "sha256:" + "c" * 64)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_MEMBERS", '["a.py","b.py"]')
    plugin.pytest_configure(None)
    plugin.pytest_collection_finish(
        SimpleNamespace(
            items=[
                SimpleNamespace(nodeid="a.py::test_one", path=Path.cwd() / "a.py"),
                SimpleNamespace(nodeid="a.py::test_two", path=Path.cwd() / "a.py"),
                SimpleNamespace(nodeid="b.py::test_three", path=Path.cwd() / "b.py"),
            ]
        )
    )

    def report(
        nodeid: str, when: str, *, failed: bool = False, skipped: bool = False
    ) -> SimpleNamespace:
        return SimpleNamespace(
            nodeid=nodeid,
            when=when,
            failed=failed,
            skipped=skipped,
            passed=not failed and not skipped,
        )

    for nodeid in ("a.py::test_one", "a.py::test_two"):
        for when in ("setup", "call", "teardown"):
            plugin.pytest_runtest_logreport(report(nodeid, when))
    for when in ("setup", "call", "teardown"):
        plugin.pytest_runtest_logreport(report("b.py::test_three", when, failed=(when == "call")))

    store = MemberReceiptStore(receipt_dir, suite_identity=suite_identity)
    assert store.reusable("a.py")
    assert not store.reusable("b.py")


def test_pytest_receipt_plugin_does_not_persist_skipped_or_xfailed_member(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from oh_goon.repository_tooling.test_batch_receipts import MemberReceiptStore, batch_identity

    plugin = _isolated_receipt_plugin()
    suite_identity = "sha256:" + "d" * 64
    member = "a.py::test_optional"
    batch = batch_identity((member,))
    receipt_dir = tmp_path / "receipts"
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_DIRECTORY", str(receipt_dir))
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_SUITE_IDENTITY", suite_identity)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY", batch)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_EXECUTION_PLAN_IDENTITY", "sha256:" + "e" * 64)
    monkeypatch.setenv("OH_GOON_PYTEST_RECEIPT_MEMBERS", '["a.py::test_optional"]')
    plugin.pytest_configure(None)
    plugin.pytest_collection_finish(
        SimpleNamespace(items=[SimpleNamespace(nodeid=member, path=Path.cwd() / "a.py")])
    )
    for when in ("setup", "call", "teardown"):
        plugin.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=member,
                when=when,
                failed=False,
                skipped=(when == "call"),
                passed=(when != "call"),
                wasxfail=("reason" if when == "call" else None),
            )
        )
    assert not MemberReceiptStore(receipt_dir, suite_identity=suite_identity).reusable(member)


def test_process_owning_batch_runner_proofs_stay_in_process_marked_module() -> None:
    import ast

    fast_path = Path(__file__)
    process_path = fast_path.with_name("test_test_batches_process.py")
    fast_tree = ast.parse(fast_path.read_text(encoding="utf-8"), filename=str(fast_path))
    process_tree = ast.parse(process_path.read_text(encoding="utf-8"), filename=str(process_path))

    def subprocess_run_tests(tree: ast.Module) -> set[str]:
        owned: set[str] = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "subprocess"
                and child.func.attr == "run"
                for child in ast.walk(node)
            ):
                owned.add(node.name)
        return owned

    assert not subprocess_run_tests(fast_tree)
    assert subprocess_run_tests(process_tree)
    assert "pytestmark = pytest.mark.process" in process_path.read_text(encoding="utf-8")
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "run"
        for node in ast.walk(fast_tree)
    )


def test_process_marker_selection_is_filename_invariant(tmp_path: Path) -> None:
    import oh_goon.repository_tooling.pytest_member_selection as selection

    original = tmp_path / "test_original.py"
    renamed = tmp_path / "test_renamed.py"
    source = "import pytest\npytestmark = pytest.mark.process\ndef test_process_owned(): pass\n"
    original.write_text(source, encoding="utf-8")
    original_member = f"{original.as_posix()}::test_process_owned"
    runnable, pruned = selection.statically_pruned_members((original_member,), marker="not process")
    assert runnable == ()
    assert pruned == (original_member,)

    original.rename(renamed)
    renamed_member = f"{renamed.as_posix()}::test_process_owned"
    runnable, pruned = selection.statically_pruned_members((renamed_member,), marker="not process")
    assert runnable == ()
    assert pruned == (renamed_member,)


def test_static_marker_pruning_parses_each_unique_file_once(monkeypatch, tmp_path: Path) -> None:
    import oh_goon.repository_tooling.pytest_member_selection as selection

    path = tmp_path / "test_many.py"
    path.write_text(
        "import pytest\n@pytest.mark.slow\ndef test_one(): pass\ndef test_two(): pass\n",
        encoding="utf-8",
    )
    real_parse = selection.ast.parse
    parses: list[str] = []

    def counted_parse(source: str, *, filename: str):
        parses.append(filename)
        return real_parse(source, filename=filename)

    monkeypatch.setattr(selection.ast, "parse", counted_parse)
    members = (
        f"{path.as_posix()}::test_one",
        f"{path.as_posix()}::test_two",
        f"{path.as_posix()}::test_one",
    )
    runnable, pruned = selection.statically_pruned_members(members, marker="not slow")

    assert runnable == (f"{path.as_posix()}::test_two",)
    assert pruned == (f"{path.as_posix()}::test_one", f"{path.as_posix()}::test_one")
    assert parses == [str(path)]


def test_suite_timeout_requires_startup_headroom(monkeypatch, tmp_path: Path) -> None:
    import sys

    import oh_goon.repository_tooling.test_batches as batches

    test_file = tmp_path / "test_one.py"
    test_file.write_text("def test_one(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test-batches",
            str(test_file),
            "--batch-timeout",
            "90",
            "--suite-timeout",
            "90",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        batches.main()


def test_static_marker_pruning_excludes_simple_file_when_every_test_is_excluded(
    tmp_path: Path,
) -> None:
    import oh_goon.repository_tooling.pytest_member_selection as selection

    path = tmp_path / "test_scale_only.py"
    path.write_text(
        "import pytest\n"
        "@pytest.mark.scale\n"
        "def test_one(): pass\n"
        "@pytest.mark.scale\n"
        "def test_two(): pass\n",
        encoding="utf-8",
    )
    runnable, pruned = selection.statically_pruned_members(
        (path.as_posix(),), marker="not slow and not process and not scale and not certification"
    )
    assert runnable == ()
    assert pruned == (path.as_posix(),)


def test_static_marker_pruning_keeps_file_when_one_test_remains_runnable(tmp_path: Path) -> None:
    import oh_goon.repository_tooling.pytest_member_selection as selection

    path = tmp_path / "test_mixed.py"
    path.write_text(
        "import pytest\n@pytest.mark.scale\ndef test_scale(): pass\ndef test_fast(): pass\n",
        encoding="utf-8",
    )
    runnable, pruned = selection.statically_pruned_members((path.as_posix(),), marker="not scale")
    assert runnable == (path.as_posix(),)
    assert pruned == ()


def test_static_marker_pruning_keeps_dynamic_collection_shapes(tmp_path: Path) -> None:
    import oh_goon.repository_tooling.pytest_member_selection as selection

    path = tmp_path / "test_class.py"
    path.write_text(
        "import pytest\n"
        "@pytest.mark.scale\n"
        "def test_scale(): pass\n"
        "class TestDynamic:\n"
        "    def test_fast(self): pass\n",
        encoding="utf-8",
    )
    runnable, pruned = selection.statically_pruned_members((path.as_posix(),), marker="not scale")
    assert runnable == (path.as_posix(),)
    assert pruned == ()
