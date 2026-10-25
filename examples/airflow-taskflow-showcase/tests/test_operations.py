import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from orders_pipeline.operations import extract, transform, load

DATA = Path(__file__).resolve().parents[1] / "src/orders_pipeline/data"

@pytest.mark.parametrize(("fixture", "result"), [("official","1236.70"),("changed","60.00")])
def test_complete_workflow(fixture, result):
    assert load(transform(extract(DATA / (fixture + ".json"))))["total_order_value"] == result

def test_invalid_input_causes_real_failure():
    with pytest.raises(ValueError, match="Invalid amount"):
        transform(extract(DATA / "invalid.json"))

def test_separate_real_commands_and_no_downstream_on_failure(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    def run(*args):
        return subprocess.run([sys.executable, "-m", "orders_pipeline.cli", *args], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run("extract","--fixture","changed").returncode == 0
    payload = (tmp_path / ".showcase/extracted.json").read_text().strip()
    assert run("transform","--data",payload).returncode == 0
    total = (tmp_path / ".showcase/total.txt").read_text().strip()
    assert run("load","--total",total).returncode == 0
    assert json.loads((tmp_path / ".showcase/result.json").read_text())["total_order_value"] == "60.00"
    (tmp_path / ".showcase/result.json").unlink()
    assert run("extract","--fixture","invalid").returncode == 0
    bad = (tmp_path / ".showcase/extracted.json").read_text().strip()
    assert run("transform","--data",bad).returncode != 0
    assert not (tmp_path / ".showcase/result.json").exists()
