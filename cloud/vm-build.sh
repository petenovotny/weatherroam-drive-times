#!/bin/bash
# Startup script for a one-shot build VM (see cloud/launch_gcp.py).
#
# Builds the North America table from a pinned commit of this repo, uploads
# the results and the log to Cloud Storage, and deletes its own VM when done,
# whether it succeeds or not. The VM also has a hard maximum run duration set
# by the launcher, as a backstop if this script never reaches the end.
#
# A pilot of 100 origin-tile groups runs first. If the projected matrix time
# exceeds the max-hours metadata value, the build stops there (status
# "stopped-projection") before spending most of the VM time.
#
# Instance metadata: bucket, version, commit, max-hours.
set -uo pipefail

MD=http://metadata.google.internal/computeMetadata/v1/instance
md() { curl -sf -H 'Metadata-Flavor: Google' "$MD/$1"; }
BUCKET=$(md attributes/bucket)
VERSION=$(md attributes/version)
COMMIT=$(md attributes/commit)
MAX_HOURS=$(md attributes/max-hours)
ZONE=$(md zone | awk -F/ '{print $NF}')
NAME=$(md name)
DEST="gs://$BUCKET/$VERSION"
LOG=/var/log/wrdt-build.log
exec > >(tee -a "$LOG") 2>&1

push_log() { gcloud storage cp "$LOG" "$DEST/build.log" --quiet >/dev/null 2>&1 || true; }
finish() {
  echo "STATUS $1 at $(date -u +%FT%TZ)"
  echo "$1" > /tmp/STATUS
  gcloud storage cp /tmp/STATUS "$DEST/STATUS" --quiet || true
  push_log
  gcloud compute instances delete "$NAME" --zone "$ZONE" --quiet
  exit 0
}
trap 'finish failed' ERR
set -e
(while true; do sleep 120; push_log; done) &

echo "build $VERSION from commit $COMMIT on $NAME ($ZONE), $(nproc) vCPU, $(free -g | awk '/Mem/ {print $2}') GB"
echo "running" > /tmp/STATUS && gcloud storage cp /tmp/STATUS "$DEST/STATUS" --quiet

apt-get update -q
apt-get install -y -q osmium-tool git time
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
git clone https://github.com/petenovotny/weatherroam-drive-times /opt/wrdt
cd /opt/wrdt
git checkout "$COMMIT"
uv sync --frozen
cd pipeline

stage() { echo "=== $1 $(date -u +%FT%TZ)"; }
stage fetch;        uv run python fetch_inputs.py na
stage graph;        /usr/bin/time -v ./build_graph.sh na 2> >(tee /tmp/graph-time.txt >&2)
stage origins;      uv run python origins.py na
stage destinations; uv run python destinations.py na --ids ../config/dest-ids-na.txt

stage pilot
uv run python compute.py na --limit-groups 100
PROJECTED=$(uv run python - <<'EOF'
import json
c = json.load(open("../work/na/compute.json"))
print(round(c["wall_seconds"] * c["cells_total"] / max(1, c["cells_this_run"]) / 3600, 2))
EOF
)
echo "pilot projection: $PROJECTED h for the whole matrix (limit $MAX_HOURS h)"
cp ../work/na/compute.json /tmp/pilot.json && gcloud storage cp /tmp/pilot.json "$DEST/pilot.json" --quiet
if uv run python -c "import sys; sys.exit(0 if $PROJECTED > $MAX_HOURS else 1)"; then
  finish stopped-projection
fi

stage matrix;       uv run python compute.py na
stage pack;         uv run python pack.py na "$VERSION"
stage validate
if uv run python validate.py na "$VERSION"; then RESULT=ok; else RESULT=validation-failed; fi

stage upload
gcloud storage cp -r ../out/na "$DEST/out" --quiet
cp ../work/na/compute.json ../work/na/graph.json ../work/na/inputs.json /tmp/ 2>/dev/null || true
gcloud storage cp /tmp/compute.json /tmp/graph.json /tmp/inputs.json /tmp/graph-time.txt "$DEST/" --quiet || true
# Kept for adding towns later without rebuilding the graph (~20-30 GB).
gcloud storage cp ../work/na/tiles.tar "$DEST/tiles.tar" --quiet || true
finish "$RESULT"
