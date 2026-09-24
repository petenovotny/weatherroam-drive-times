#!/usr/bin/env bash
# Build the Valhalla road graph for a region from its pinned OSM extract.
#
#   pipeline/build_graph.sh <region>
#
# 1. Drop ice roads and winter roads (the only filter; METHOD.md says why no
#    other filtering is done). Valhalla has no handling for these tags, so
#    without this they would be routable all year.
# 2. valhalla_build_config, with config/valhalla-overrides.json merged over it.
# 3. Admin boundaries (country crossings, driving side).
# 4. Tiles in stages: the parse stages single-threaded, then build/enhance at
#    BUILD_THREADS. Peak memory is in build/enhance and scales with threads,
#    so a big region can trade time for memory here.
# 5. One tar extract that every matrix worker memory-maps.
set -euo pipefail

REGION="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
W="$ROOT/work/$REGION"
BIN="$ROOT/.venv/bin"
PY="$BIN/python"
# pyvalhalla's wrappers look themselves up on PATH before running the binary.
export PATH="$BIN:$PATH"

PBF=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['osm']['path'])" "$W/inputs.json")
THREADS=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['build_threads'])" "$ROOT/config/region-$REGION.json")
THREADS="${BUILD_THREADS:-$THREADS}"

echo "== filter winter roads" >&2
osmium tags-filter --overwrite --invert-match -o "$W/filtered.osm.pbf" "$PBF" \
  w/ice_road=yes w/winter_road=yes
osmium tags-filter --overwrite -o "$W/removed-winter-roads.osm.pbf" "$PBF" \
  w/ice_road=yes w/winter_road=yes
REMOVED=$(osmium fileinfo -e -g data.count.ways "$W/removed-winter-roads.osm.pbf")
echo "removed $REMOVED winter/ice road ways" >&2

echo "== config" >&2
"$BIN/valhalla_build_config" \
  --mjolnir-tile-dir "$W/tiles" \
  --mjolnir-tile-extract "$W/tiles.tar" \
  --mjolnir-admin "$W/admins.sqlite" \
  --mjolnir-concurrency "$THREADS" \
  > "$W/valhalla.base.json"
"$PY" - "$W/valhalla.base.json" "$ROOT/config/valhalla-overrides.json" "$W/valhalla.json" <<'EOF'
import json, sys
base, overrides, out = sys.argv[1:4]
cfg = json.load(open(base))
def merge(a, b):
    for k, v in b.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            merge(a[k], v)
        else:
            a[k] = v
merge(cfg, json.load(open(overrides)))
json.dump(cfg, open(out, "w"), indent=2)
EOF

echo "== admins" >&2
rm -f "$W/admins.sqlite"
"$PY" -m valhalla valhalla_build_admins -c "$W/valhalla.json" "$W/filtered.osm.pbf"

echo "== tiles (staged)" >&2
rm -rf "$W/tiles" && mkdir -p "$W/tiles"
cd "$W"
"$PY" -m valhalla valhalla_build_tiles -c valhalla.json -s initialize -e constructedges -j 1 filtered.osm.pbf
"$PY" -m valhalla valhalla_build_tiles -c valhalla.json -s build -e build -j "$THREADS" filtered.osm.pbf
"$PY" -m valhalla valhalla_build_tiles -c valhalla.json -s enhance -e enhance -j "$THREADS" filtered.osm.pbf
"$PY" -m valhalla valhalla_build_tiles -c valhalla.json -s filter -e cleanup -j "$THREADS" filtered.osm.pbf

echo "== extract" >&2
"$BIN/valhalla_build_extract" -c valhalla.json -v --overwrite

"$PY" - "$W/valhalla.json" "$W/graph.json" "$REMOVED" <<'EOF'
import json, sys, hashlib, subprocess
cfg, out, removed = sys.argv[1], sys.argv[2], int(sys.argv[3])
version = subprocess.run([sys.executable, "-m", "valhalla", "--version"], capture_output=True, text=True).stdout.strip()
json.dump({
    "valhalla_version": version,
    "valhalla_config_sha256": hashlib.sha256(open(cfg, "rb").read()).hexdigest(),
    "winter_roads_removed": removed,
}, open(out, "w"), indent=2)
EOF
echo "graph built: $W/tiles.tar" >&2
