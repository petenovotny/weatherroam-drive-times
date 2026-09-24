"""Pack the shards into the published table file, its manifest and checksums.

Binary format v1 (little-endian; every section starts on an 8-byte boundary so
a reader can map typed arrays over the file without copying):

    0   "WRDT"                      magic
    4   u16 formatVersion = 1
    6   u16 flags                   bit0: pairDest is u32 (else u16)
    8   u32 nCells, u32 nDest, u32 nPairs, u32 headerJsonLen
    24  header JSON (UTF-8), zero-padded to 8
        destIds   u32[nDest]        GeoNames ids, ascending
        cellKeys  i32[nCells]       ascending; key = (floor(lat*10)+900)*3600 + (floor(lng*10)+1800)
        cellOffs  u32[nCells+1]     pairs of cell i are [cellOffs[i], cellOffs[i+1])
        pairDest  u16|u32[nPairs]   index into destIds; within a cell, sorted by minutes
        pairVal   u16[nPairs]       bits 0-9 minutes (0-1023), bit 10 needs a ferry,
                                    bit 11 crosses a national border, 12-15 reserved

The file holds only integer GeoNames ids and drive times: no names, no
coordinates, no scores. That keeps it a separate database from the GeoNames
town data it is joined to (METHOD.md, "Licences").

Usage: python pipeline/pack.py <region> <version>
Writes out/<region>/drive-table-<version>.bin, manifest-<version>.json,
origins-<version>.tsv, destinations-<version>.tsv and SHA256SUMS.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from common import CONFIG, ROOT, load_region, log, read_tsv, sha256_file, work_dir

MAGIC = b"WRDT"
FORMAT_VERSION = 1
FERRY_BIT = 1 << 10
BORDER_BIT = 1 << 11


def _pad8(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 8)


def main(region_name: str, version: str) -> None:
    region = load_region(region_name)
    wd = work_dir(region_name)
    out = ROOT / "out" / region_name
    out.mkdir(parents=True, exist_ok=True)

    origins = read_tsv(wd / "origins.tsv")
    dests = read_tsv(wd / "destinations.tsv")
    excluded = {int(r["geonameid"]) for r in read_tsv(CONFIG / "exclusions.tsv")}

    dest_ids = np.array([int(r["geonameid"]) for r in dests], np.uint32)
    assert np.all(np.diff(dest_ids.astype(np.int64)) > 0), "destinations must be sorted by id"
    keep_dest = ~np.isin(dest_ids, list(excluded))
    # Old dest index -> new index after exclusions (-1 = excluded).
    remap = np.full(len(dest_ids), -1, np.int64)
    remap[keep_dest] = np.arange(keep_dest.sum())
    dest_ids = dest_ids[keep_dest]
    dest_country = np.array([r["country"] for r in dests])[keep_dest]

    cell_keys = np.array([int(r["cell_key"]) for r in origins], np.int32)
    assert np.all(np.diff(cell_keys.astype(np.int64)) > 0), "origins must be sorted by cell key"
    origin_country = np.array([r["country"] for r in origins])

    parts = [np.load(p) for p in sorted((wd / "shards").glob("*.npz"))]
    if not parts:
        raise SystemExit("no shards; run compute.py first")
    o = np.concatenate([p["origin"] for p in parts])
    d = remap[np.concatenate([p["dest"] for p in parts])]
    m = np.concatenate([p["minutes"] for p in parts]).astype(np.uint16)
    f = np.concatenate([p["ferry"] for p in parts])
    live = d >= 0
    o, d, m, f = o[live], d[live], m[live], f[live]
    if m.max(initial=0) > 1023:
        raise SystemExit("minutes overflow the 10-bit field")
    border = origin_country[o] != dest_country[d]

    order = np.lexsort((m, o))  # by cell, then by minutes
    o, d, m, f, border = o[order], d[order], m[order], f[order], border[order]
    counts = np.bincount(o, minlength=len(cell_keys))
    offs = np.zeros(len(cell_keys) + 1, np.uint32)
    np.cumsum(counts, out=offs[1:])

    wide = len(dest_ids) > 0xFFFF
    pair_dest = d.astype(np.uint32 if wide else np.uint16)
    pair_val = (m | (f * FERRY_BIT) | (border * BORDER_BIT)).astype(np.uint16)

    inputs = json.loads((wd / "inputs.json").read_text())
    graph = json.loads((wd / "graph.json").read_text())
    costing = json.loads((CONFIG / "costing.json").read_text())
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except subprocess.CalledProcessError:
        commit = "uncommitted"
    header = {
        "region": region_name,
        "version": version,
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "maxMinutes": region["max_minutes"],
        "grid": {"deg": region["grid_deg"], "key": "(floor(lat*10)+900)*3600+(floor(lng*10)+1800)"},
        "osm": {k: inputs["osm"][k] for k in ("file", "md5", "replication_timestamp")},
        "router": {
            "name": "valhalla",
            "version": graph["valhalla_version"],
            "configSha256": graph["valhalla_config_sha256"],
            "costing": costing["passes"],
            "ferryDeltaMinutes": costing["ferry_delta_minutes"],
        },
        "winterRoadsRemoved": graph["winter_roads_removed"],
        "originsSha256": sha256_file(wd / "origins.tsv"),
        "destinationsSha256": sha256_file(wd / "destinations.tsv"),
        "excludedDestinations": sorted(excluded & set(int(r["geonameid"]) for r in dests)),
        "pipelineCommit": commit,
        "bits": {"minutes": "0-9", "ferry": 10, "border": 11},
        "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "licenceUrl": "https://opendatacommons.org/licenses/odbl/1-0/",
    }
    header_bytes = _pad8(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode())

    table = out / f"drive-table-{version}.bin"
    with open(table, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<HH", FORMAT_VERSION, 1 if wide else 0))
        fh.write(struct.pack("<IIII", len(cell_keys), len(dest_ids), len(pair_val), len(header_bytes)))
        fh.write(header_bytes)
        for arr in (dest_ids, cell_keys, offs, pair_dest, pair_val):
            fh.write(_pad8(arr.tobytes()))

    shutil.copy(wd / "origins.tsv", out / f"origins-{version}.tsv")
    shutil.copy(wd / "destinations.tsv", out / f"destinations-{version}.tsv")
    manifest = dict(header, counts={"cells": len(cell_keys), "destinations": len(dest_ids), "pairs": len(pair_val)})
    (out / f"manifest-{version}.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    files = sorted(p for p in out.iterdir() if p.name != "SHA256SUMS" and version in p.name)
    (out / "SHA256SUMS").write_text("".join(f"{sha256_file(p)}  {p.name}\n" for p in files))
    log(
        f"{table.name}: {len(cell_keys)} cells, {len(dest_ids)} destinations, {len(pair_val)} pairs, "
        f"{table.stat().st_size / 1e6:.1f} MB; ferry {int(f.sum())}, cross-border {int(border.sum())}"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
