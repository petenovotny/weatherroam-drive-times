#!/usr/bin/env bash
# Build, pack and validate a drive-time table end to end.
#
#   REGION=mn ./run.sh                 # laptop smoke run (Minnesota, ~2 min)
#   REGION=na VERSION=na-2026.10.1 ./run.sh   # production (North America VM)
#
# Every step is resumable: inputs are cached, and compute.py skips finished shards.
set -euo pipefail
cd "$(dirname "$0")"
REGION="${REGION:?set REGION}"
VERSION="${VERSION:-$REGION-dev}"
uv sync --frozen
cd pipeline
uv run python fetch_inputs.py "$REGION"
./build_graph.sh "$REGION"
uv run python origins.py "$REGION"
uv run python destinations.py "$REGION" ${DEST_IDS:+--ids "$DEST_IDS"}
uv run python compute.py "$REGION"
uv run python pack.py "$REGION" "$VERSION"
uv run python validate.py "$REGION" "$VERSION"
