"""Check a packed table before it is published. Exit status 1 on any failure.

Checks (numbering follows the build plan):
  V1 structure     header, counts, ordering, minutes within the cap, every
                   destination reachable from at least one cell
  V2 snap error    exact reference origins routed directly, against the table
                   entry for their cell: p50 <= 8 min, p90 <= 15 min
  V3 consistency   the table against fresh one-to-many routes from the same
                   cell points on random pairs: p99 |diff| <= 2 min
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
from pathlib import Path

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


def check_structure(t: Table, region: dict, problems: list[str]) -> tuple[str, bool, str]:
    """V1, the part that needs only the table: every town reached from somewhere.

    A town with no road connection at all (Churchill, Manitoba: rail and air
    only) is legitimately unreached; such towns are listed with a reason in
    the region config's `expected_unreachable` and do not fail the check.
    """
    expected = {int(k): v for k, v in region.get("expected_unreachable", {}).items()}
    reached = np.bincount(t.pair_dest, minlength=len(t.dest_ids))
    unreached = [int(g) for g in t.dest_ids[reached == 0]]
    surprise = [g for g in unreached if g not in expected]
    known = [g for g in unreached if g in expected]
    if surprise:
        problems.append(f"{len(surprise)} destinations unreachable from every cell: {surprise[:10]}")
    detail = "; ".join(problems) or f"{len(t.cell_keys)} cells, {len(t.dest_ids)} towns, {len(t.pair_val)} pairs"
    if known:
        detail += "; expected unreachable: " + ", ".join(f"{g} ({expected[g]})" for g in known)
    return ("V1 structure", not problems, detail)


def check_named_cases(t: Table, dests: dict, region: dict) -> tuple[str, bool, str]:
    """V6: known trips land in their expected range.

    A case names its town by GeoNames id (`to_id`) where the name is not unique
    in the region (two Duluths in North America). A bare name that matches more
    than one town is a configuration error, reported as such, never resolved by
    picking whichever comes first.
    """
    by_name: dict[str, list[int]] = {}
    for gid, r in dests.items():
        by_name.setdefault(r["name"], []).append(gid)
    case_notes, case_ok = [], True
    for case in region.get("named_cases", []):
        lat, lng = case["from"]
        if "to_id" in case:
            gid = int(case["to_id"])
        else:
            matches = by_name.get(case["to"], [])
            if len(matches) > 1:
                case_ok = False
                case_notes.append(f"{case['to']}: ambiguous name ({len(matches)} towns), set to_id")
                continue
            gid = matches[0] if matches else None
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
    return ("V6 named cases", case_ok, "; ".join(case_notes) or "none configured")


def table_only(region_name: str, table_path: str, dests_path: str) -> None:
    """Re-run the table-only checks (V1 reachability, V6) on a published table,
    without the routing graph or shards the other checks need."""
    region = load_region(region_name)
    t = read_table(Path(table_path))
    dests = {int(r["geonameid"]): r for r in read_tsv(Path(dests_path))}
    results = [check_structure(t, region, []), check_named_cases(t, dests, region)]
    for n, ok, d in results:
        print(f"| {n} | {'pass' if ok else '**FAIL**'} | {d} |")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


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
    results.append(check_structure(t, region, problems))

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
        ("V3 consistency", p99 <= 2, f"p95 {p95:.1f} min, p99 {p99:.1f} min over {len(sym)} pairs")
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
    results.append(check_named_cases(t, dests, region))

    skipped = ["V5 second routing engine", "V7 ferry census (see V6 cases)", "V8 seasonal probe", "V9 known trips"]
    lines = [f"# Validation: drive-table-{version}", "", f"OSM `{t.header['osm']['file']}`, router {t.header['router']['name']} {t.header['router']['version']}.", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'pass' if ok else '**FAIL**'} | {d} |" for n, ok, d in results]
    lines += ["", "Not run: " + ", ".join(skipped) + "."]
    (out / f"validation-report-{version}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    if sys.argv[1] == "--table-only":
        table_only(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main(sys.argv[1], sys.argv[2])
