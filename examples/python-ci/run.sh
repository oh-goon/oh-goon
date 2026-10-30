#!/usr/bin/env bash
# CI setup and artifact promotion around the original public Oh-Goon CLI.
set -euo pipefail

fail() {
  printf '%s\n' "$*" >&2
  exit 2
}

usage() {
  fail 'Usage: run.sh run TOOL_SOURCE_ROOT WORKLOAD_GIT_ROOT RELATIVE_PLAN_PATH STATE_DIRECTORY | run.sh collect TOOL_SOURCE_ROOT STATE_DIRECTORY RESULT_DIRECTORY'
}

bind_tool() {
  task_tool=$(realpath -e -- "$1")
  task_cli="$task_tool/backend/.venv/bin/oh-goon"
  task_python="$task_tool/backend/.venv/bin/python"
  [[ -x "$task_cli" && -x "$task_python" ]] ||
    fail "Prepare the selected CLI source first: make -C $task_tool backend-install"
}

bind_state() {
  export OH_GOON_DB="$task_state/runtime.sqlite"
  export OH_GOON_LEAGUE_FILE="$task_state/league.yml"
  export XDG_CONFIG_HOME="$task_state/config"
  export XDG_DATA_HOME="$task_state/data"
  export XDG_STATE_HOME="$task_state/state"
}

[[ $# -gt 0 ]] || usage
task_mode=$1
shift
case "$task_mode" in
  run)
    [[ $# -eq 4 ]] || usage
    bind_tool "$1"
    task_repository=$(git -C "$2" rev-parse --show-toplevel)
    task_revision=$(git -C "$task_repository" rev-parse HEAD)
    task_plan=$3
    task_state=$(realpath -m -- "$4")
    case "$task_state" in
      "$task_repository"|"$task_repository"/*)
        fail 'Choose a fresh state directory outside the workload repository.' ;;
    esac
    [[ ! -e "$task_state" && ! -L "$task_state" ]] ||
      fail 'State directory already exists; choose a fresh job directory.'
    mkdir -m 700 -- "$task_state"
    mkdir -m 700 -- "$task_state/workflow"
    bind_state
    "$task_cli" league add-folder "$task_repository" >&2
    "$task_cli" league scan --json > "$task_state/league.json"
    "$task_python" -I - "$task_state/league.json" "$task_repository" "$task_plan" \
      "$task_revision" "$task_state/workflow/run.yml" <<'PY'
import json
import sys
from pathlib import Path

inventory_path, repository, plan, revision, destination = sys.argv[1:]
with Path(inventory_path).open(encoding="utf-8") as source:
    inventory = json.load(source)
root = Path(repository).resolve()
matches = [
    team["repository"]
    for team in inventory["teams"]
    if Path(team["repository"]["path"]).resolve() == root
]
if len(matches) != 1:
    raise SystemExit("Expected exactly one included registered workload repository")
selection = {
    "version": 2,
    "source": {
        "kind": "git-plan",
        "git": root.as_uri(),
        "ref": revision,
        "path": ".",
        "plan": plan,
    },
    "target": {"repository": str(root)},
}
with Path(destination).open("x", encoding="utf-8") as output:
    json.dump(selection, output, indent=2)
    output.write("\n")
PY
    "$task_cli" workflow prepare "$task_state/workflow/run.yml" >&2
    git -C "$task_tool" rev-parse HEAD > "$task_state/tool-commit.txt"
    git -C "$task_tool" status --porcelain=v1 --untracked-files=normal > "$task_state/tool-status.txt"
    "$task_cli" --version > "$task_state/tool-version.txt"
    uv --version > "$task_state/uv-version.txt"
    # No shell cancellation adapter: the CLI becomes the step entry process.
    # Its original Run owner handles native signals and determines the result.
    exec "$task_cli" run "$task_state/workflow/run.yml" --json > "$task_state/run.json"
    ;;
  collect)
    [[ $# -eq 3 ]] || usage
    bind_tool "$1"
    task_state=$(realpath -e -- "$2")
    task_results=$(realpath -m -- "$3")
    [[ ! -e "$task_results" && ! -L "$task_results" ]] ||
      fail 'Result directory already exists; choose a new promotion directory.'
    bind_state
    "$task_python" -I - "$task_cli" "$task_state" "$task_results" <<'PY'
import json
import shutil
import subprocess
import sys
from pathlib import Path

cli, state_text, result_text = sys.argv[1:]
state, results = Path(state_text), Path(result_text)
receipt = state / "run.json"
if not receipt.is_file() or not receipt.stat().st_size:
    raise SystemExit("No submitted Run receipt is available; inspect the original CLI failure")
if receipt.stat().st_size > 65536:
    raise SystemExit("Run receipt exceeds its bounded JSON contract")
with receipt.open(encoding="utf-8") as source:
    invocation = json.load(source)
run_id = invocation.get("fleet_run_id")
if not isinstance(run_id, str) or not run_id:
    raise SystemExit("No submitted Run is recorded; inspect the original CLI refusal")

for view in ("status", "tape", "outputs"):
    with (state / f"{view}.json").open("wb") as output:
        subprocess.run([cli, "runs", "show" if view == "status" else view, run_id, "--json"], stdout=output, check=True)
with (state / "tape.json").open(encoding="utf-8") as source:
    tape = json.load(source)
if not tape["terminal"]:
    raise SystemExit("Run is not terminal; completion and artifact promotion remain unverified")

results.mkdir(mode=0o700)
for name in ("run.json", "status.json", "tape.json", "outputs.json"):
    shutil.copyfile(state / name, results / name)
shutil.copyfile(state / "workflow/goon.lock", results / "goon.lock")

def observation(name):
    path = state / name
    if path.stat().st_size > 65536:
        raise SystemExit("Tool observation exceeds its bounded text contract")
    return path.read_text(encoding="utf-8").strip()

toolchain = {
    "oh_goon_revision": observation("tool-commit.txt"),
    "oh_goon_working_tree_dirty": bool(observation("tool-status.txt")),
    "oh_goon_version": observation("tool-version.txt"),
    "uv_version": observation("uv-version.txt"),
    "python_version": sys.version,
    "authority": "diagnostic toolchain observations; Run evidence retains execution authority",
}
with (results / "toolchain.json").open("x", encoding="utf-8") as output:
    json.dump(toolchain, output, indent=2)
    output.write("\n")

with (state / "outputs.json").open(encoding="utf-8") as source:
    outputs = json.load(source)
exports = []
wheel_exports = 0
for repository in outputs["repositories"]:
    for step in repository["steps"]:
        for artifact in step["artifacts"]:
            if artifact["status"] != "PRESENT" or artifact.get("retained_object") is None:
                continue
            # Different repositories/Steps may retain the same basename. Keep
            # their files separate from one another and from portable receipts.
            artifact_directory = results / "artifacts" / f"{len(exports) + 1:04d}"
            artifact_directory.mkdir(parents=True)
            destination = artifact_directory / Path(artifact["path"]).name
            # Keep transfer file-backed; the public CLI verifies bytes and refuses overwrite.
            export_receipt = state / "last-export.json"
            with export_receipt.open("wb") as output:
                subprocess.run(
                    [cli, "runs", "export-artifact", run_id,
                     "--repository-id", repository["repository"]["repository_id"],
                     "--step", step["step_id"], "--path", artifact["path"],
                     "--output", str(destination), "--json"],
                    stdout=output,
                    check=True,
                )
            with export_receipt.open(encoding="utf-8") as source:
                exports.append(json.load(source))
            if destination.suffix == ".whl":
                wheel_exports += 1
with (results / "exports.json").open("x", encoding="utf-8") as output:
    json.dump(exports, output, indent=2)
    output.write("\n")
if invocation.get("outcome") == "SUCCESS" and not wheel_exports:
    raise SystemExit("Successful CI example Run has no retained distribution to promote")
print(f"Recorded Run: {run_id}; exported files: {len(exports)}; results: {results}")
PY
    ;;
  *) usage ;;
esac
