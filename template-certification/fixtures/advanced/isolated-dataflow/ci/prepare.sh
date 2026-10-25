#!/bin/sh
set -eu
exec uv run --frozen --no-sync dataflow-prepare "$@"
