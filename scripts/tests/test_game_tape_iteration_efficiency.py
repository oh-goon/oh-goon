from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_game_tape_flow_avoids_quadratic_step_accumulation_and_lane_rescans() -> None:
    model = (ROOT / "frontend/src/features/game-tape/gameTapeFlowModel.ts").read_text(encoding="utf-8")
    flow = (ROOT / "frontend/src/features/game-tape/components/GameTapeFlow.tsx").read_text(encoding="utf-8")

    assert "previous.steps.push(step)" in model
    assert "previous.steps = [...previous.steps, step]" not in model
    assert "laneStartIndex" in model
    assert "group.laneStartIndex + groupStepIndex" in flow
    assert "laneSteps.findIndex" not in flow
    assert "group.statusCounts" in flow


def test_execution_timeline_bucket_indexes_are_memoized_outside_row_render_loop() -> None:
    source = (ROOT / "frontend/src/features/overview/components/ExecutionTimelineGrid.tsx").read_text(
        encoding="utf-8"
    )

    table_start = source.index("function TimelineTable")
    table_end = source.index("function TimelineCell")
    table = source[table_start:table_end]
    assert "const rows = useMemo(" in table
    assert "buckets: new Map(" in table
    render_start = table.index("return <div")
    assert "new Map(" not in table[render_start:]


def test_scout_run_reader_has_no_per_step_database_query() -> None:
    source = (ROOT / "backend/src/oh_goon/scout/run_reader.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Helper names and boundaries are private. The structural performance
    # contract is that module-level row/projector helpers cannot issue SQL;
    # database reads stay in the reader boundary and peer ranking is batched.
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assert not any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "execute"
            for child in ast.walk(node)
        )

    sql_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any("ROW_NUMBER() OVER" in value for value in sql_literals)
