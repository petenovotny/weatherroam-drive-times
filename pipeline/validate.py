"""Check a packed table before it is published. Exit status 1 on any failure.

Checks (numbering follows the build plan):
  V1 structure     header, counts, ordering, minutes within the cap, every
                   destination reachable from at least one cell
  V2 snap error    exact reference origins routed directly, against the table
                   entry for their cell: p50 <= 8 min, p90 <= 15 min
  V3 symmetry      forward one-to-many routes against the table's reverse
                   searches on random pairs: p95 <= 5 min, p99 <= 10 min.
                   The reverse error is one-sided (it only overstates) and
                   below the cell snap error V2 allows; METHOD.md has the data
  V4 physics       implied road speed: none above 137 km/h; slow long pairs listed
  V6 named cases   region-specific expectations (config/region-*.json)
Not run here: V5 (second routing engine), V8 (seasonal probe), V9 (known
trips); they are listed as skipped in the report.

Usage: python pipeline/validate.py <region> <version>
Writes out/<region>/validation-report-<version>.md.
"""

from __future__ import annotations

import json
import random
import struct
import sys
from dataclasses import dataclass

import numpy as np

from common import ROOT, actor, cell_index, cell_key, load_region, location, read_tsv, work_dir

MAX_SPEED_KMH = 137.0  # 85 mph, the highest posted limit in the US


@dataclass
class Table:
    header: dict
    dest_ids: np.ndarray
    cell_keys: np.ndarray
    cell_offs: np.ndarray
    pair_dest: np.ndarray
    pair_val: np.ndarray

    @property
    def minutes(self) -> np.ndarray:
        return self.pair_val & 0x3FF

    def cell(self, key: int):
        i = int(np.searchsorted(self.cell_keys, key))
        if i == len(self.cell_keys) or self.cell_keys[i] != key:
            return None
        a, b = int(self.cell_offs[i]), int(self.cell_offs[i + 1])
        return self.pair_dest[a:b], self.pair_val[a:b]


def read_table(path) -> Table:
    buf = path.read_bytes()
    assert buf[:4] == b"WRDT", "bad magic"
    version, flags = struct.unpack_from("<HH", buf, 4)
    assert version == 1, f"unknown format version {version}"
    n_cells, n_dest, n_pairs, hlen = struct.unpack_from("<IIII", buf, 8)
    header = json.loads(buf[24 : 24 + hlen].rstrip(b"\0"))
    off = 24 + hlen

    def take(dtype, n):
        nonlocal off
        arr = np.frombuffer(buf, dtype=dtype, count=n, offset=off)
        off += -(-(arr.nbytes) // 8) * 8
        return arr

    dest_ids = take("<u4", n_dest)
    cell_keys = take("<i4", n_cells)
    cell_offs = take("<u4", n_cells + 1)
    pair_dest = take("<u4" if flags & 1 else "<u2", n_pairs)
    pair_val = take("<u2", n_pairs)
    assert off == len(buf), f"trailing bytes: {len(buf) - off}"
    return Table(header, dest_ids, cell_keys, cell_offs, pair_dest, pair_val)


def _route_minutes(act, src, targets, centre=False):
    req = {
        "sources": [location(src[0], src[1], centre)],
        "targets": [location(a, b) for a, b in targets],
        "costing": "auto",
        "costing_options": {"auto": json.loads((ROOT / "config/costing.json").read_text())["passes"]["default"]},
        "verbose": False,
    }
    row = json.loads(act.matrix(json.dumps(req)))["sources_to_targets"]["durations"][0]
    return [None if x is None else x / 60 for x in row]


def main(region_name: str, version: str) -> None:
    region = load_region(region_name)
    wd = work_dir(region_name)
    out = ROOT / "out" / region_name
    t = read_table(out / f"drive-table-{version}.bin")
    dests = {int(r["geonameid"]): r for r in read_tsv(wd / "destinations.tsv")}
    origins = {int(r["cell_key"]): r for r in read_tsv(wd / "origins.tsv")}
    act = actor(region_name)
    results: list[tuple[str, bool, str]] = []

    # V1
    problems = []
    if np.any(np.diff(t.cell_keys.astype(np.int64)) <= 0):
        problems.append("cell keys not strictly ascending")
    if np.any(np.diff(t.dest_ids.astype(np.int64)) <= 0):
        problems.append("destination ids not strictly ascending")
    if t.cell_offs[0] != 0 or t.cell_offs[-1] != len(t.pair_val) or np.any(np.diff(t.cell_offs.astype(np.int64)) < 0):
        problems.append("cell offsets inconsistent")
    if t.minutes.max(initial=0) > t.header["maxMinutes"]:
        problems.append("minutes above the cap")
    for i in range(len(t.cell_keys)):
        a, b = t.cell_offs[i], t.cell_offs[i + 1]
        if np.any(np.diff(t.minutes[a:b].astype(np.int32)) < 0):
            problems.append(f"cell {t.cell_keys[i]} not sorted by minutes")
            break
    reached = np.bincount(t.pair_dest, minlength=len(t.dest_ids))
    unreached = [int(g) for g in t.dest_ids[reached == 0]]
    if unreached:
        problems.append(f"{len(unreached)} destinations unreachable from every cell: {unreached[:10]}")
    results.append(
        ("V1 structure", not problems, "; ".join(problems) or f"{len(t.cell_keys)} cells, {len(t.dest_ids)} towns, {len(t.pair_val)} pairs")
    )

    # V2
    diffs = []
    for name, lat, lng in region.get("reference_origins", []):
        hit = t.cell(cell_key(*cell_index(lat, lng, region["grid_deg"])))
        if hit is None:
            continue
        pd, pv = hit
        sample = list(range(len(pd)))[:: max(1, len(pd) // 40)]
        towns = [dests[int(t.dest_ids[pd[k]])] for k in sample]
        exact = _route_minutes(act, (lat, lng), [(float(x["lat"]), float(x["lng"])) for x in towns])
        diffs += [abs(e - int(pv[k] & 0x3FF)) for k, e in zip(sample, exact) if e is not None]
    if diffs:
        p50, p90 = np.percentile(diffs, 50), np.percentile(diffs, 90)
        results.append(("V2 snap error", p50 <= 8 and p90 <= 15, f"p50 {p50:.1f} min, p90 {p90:.1f} min over {len(diffs)} pairs"))
    else:
        results.append(("V2 snap error", True, "skipped: no reference origins configured"))

    # V3
    rng = random.Random(7)
    cells = rng.sample(range(len(t.cell_keys)), min(200, len(t.cell_keys)))
    sym = []
    for i in cells:
        a, b = int(t.cell_offs[i]), int(t.cell_offs[i + 1])
        if a == b:
            continue
        ks = rng.sample(range(a, b), min(5, b - a))
        o = origins[int(t.cell_keys[i])]
        towns = [dests[int(t.dest_ids[t.pair_dest[k]])] for k in ks]
        fwd = _route_minutes(
            act,
            (float(o["lat"]), float(o["lng"])),
            [(float(x["lat"]), float(x["lng"])) for x in towns],
            centre=o["geonameid"] == "0",
        )
        sym += [abs(f - int(t.minutes[k])) for k, f in zip(ks, fwd) if f is not None]
    p95 = float(np.percentile(sym, 95)) if sym else 0.0
    p99 = float(np.percentile(sym, 99)) if sym else 0.0
    results.append(
        ("V3 direction symmetry", p95 <= 5 and p99 <= 10, f"p95 {p95:.1f} min, p99 {p99:.1f} min over {len(sym)} pairs")
    )

    # V4
    shards = [np.load(p) for p in sorted((wd / "shards").glob("*.npz"))]
    km = np.concatenate([s["km"] for s in shards]).astype(np.float64)
    mins = np.concatenate([s["minutes"] for s in shards]).astype(np.float64)
    moving = mins >= 5
    speed = np.where(moving, km / np.maximum(mins, 1) * 60, 0)
    fast = int(np.sum(speed > MAX_SPEED_KMH))
    slow = int(np.sum((mins > 60) & (speed < 30)))
    results.append(("V4 physical bounds", fast == 0, f"{fast} pairs above {MAX_SPEED_KMH:.0f} km/h; {slow} pairs over 1 h below 30 km/h (review); median {np.median(speed[moving]):.0f} km/h"))

    # V6
    by_name = {}
    for gid, r in dests.items():
        by_name.setdefault(r["name"], gid)
    case_notes, case_ok = [], True
    for case in region.get("named_cases", []):
        lat, lng = case["from"]
        gid = by_name.get(case["to"])
        hit = t.cell(cell_key(*cell_index(lat, lng, region["grid_deg"])))
        entry = None
        if gid is not None and hit is not None:
            idx = int(np.searchsorted(t.dest_ids, gid))
            pd, pv = hit
            where = np.nonzero(pd == idx)[0]
            if len(where):
                entry = int(pv[where[0]])
        if entry is None:
            case_ok = False
            case_notes.append(f"{case['to']}: missing")
            continue
        minutes, ferry = entry & 0x3FF, bool(entry & (1 << 10))
        good = ("min" not in case or minutes >= case["min"]) and ("max" not in case or minutes <= case["max"])
        good = good and ("ferry" not in case or ferry == case["ferry"])
        case_ok &= good
        case_notes.append(f"{case['to']}: {minutes} min{' ferry' if ferry else ''} {'ok' if good else 'FAIL'}")
    results.append(("V6 named cases", case_ok, "; ".join(case_notes) or "none configured"))

    skipped = ["V5 second routing engine", "V7 ferry census (see V6 cases)", "V8 seasonal probe", "V9 known trips"]
    lines = [f"# Validation: drive-table-{version}", "", f"OSM `{t.header['osm']['file']}`, router {t.header['router']['name']} {t.header['router']['version']}.", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'pass' if ok else '**FAIL**'} | {d} |" for n, ok, d in results]
    lines += ["", "Not run: " + ", ".join(skipped) + "."]
    (out / f"validation-report-{version}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
