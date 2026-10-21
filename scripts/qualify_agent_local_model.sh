#!/usr/bin/env bash
# Native real-model and pinned-Hashmarks qualification of the bounded Agent Skill harnesses.
# Does not install/pull Ollama models or substitute an external identity producer.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
MODEL="${1:-${OH_GOON_AGENT_MODEL:-}}"
if [[ "$(uname -s)" != Linux ]]; then
  printf 'UNQUALIFIED: AGENT-01B3C requires Linux or WSL\n' >&2; exit 2
fi
for tool in uv git rg ollama; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'UNQUALIFIED: required native executable missing: %s\n' "$tool" >&2; exit 2
  fi
done
if [[ -z "$MODEL" || "$MODEL" == -* || "$MODEL" =~ [[:space:]] ]]; then
  printf 'UNQUALIFIED: pass an explicitly installed Ollama model, e.g. bash scripts/qualify_agent_local_model.sh qwen3:0.6b\n' >&2
  exit 2
fi
export OLLAMA_HOST=http://127.0.0.1:11434
if ! ollama show "$MODEL" >/dev/null 2>&1; then
  printf 'UNQUALIFIED: selected Ollama model is not available locally; start Ollama and install the model yourself: %s\n' "$MODEL" >&2
  exit 2
fi
printf 'Materializing authoritative locked backend dependencies, including real Hashmarks ...\n'
if ! uv sync --locked --project backend --group dev --group quality --group build; then
  printf 'UNQUALIFIED: pinned dependencies unavailable; do not substitute Hashmarks or claim a native pass\n' >&2
  exit 2
fi
OUT="${OH_GOON_AGENT_QUALIFICATION_OUT:-${XDG_STATE_HOME:-$HOME/.local/state}/oh-goon/qualification/agent-local-model}"
mkdir -p "$OUT"
{
  printf 'Model: %s\n' "$MODEL"
  printf 'Host: '; uname -a
  printf 'Ollama: '; ollama --version
  printf 'uv: '; uv --version
  printf 'Selection: original Fleet standard-local, pinned Hashmarks and installed local Ollama\n'
} > "$OUT/environment.txt"
printf 'Running real Hashmarks, real model inference, original Fleet / Game Tape from fresh native source ...\n'
export OH_GOON_REQUIRE_NATIVE_AGENT_MODEL=1
export OH_GOON_AGENT_MODEL="$MODEL"
export OH_GOON_REQUIRE_NATIVE_HASHMARKS=1
uv run --locked --no-sync --project backend --group dev --group quality --group build \
  python -m pytest -q backend/tests/integration/test_agent01b3c_native_model_review.py \
  backend/tests/integration/test_agent01b3h_native_skill_cycle.py \
  --junitxml="$OUT/native-agent-model.xml" 2>&1 | tee "$OUT/native-agent-model.log"
printf 'AGENT-01B3H NATIVE PASS: installed model, pinned Hashmarks, frozen Skill resources, Fleet and reopened Game Tape validated\n'
