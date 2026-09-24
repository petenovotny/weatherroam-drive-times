"""Drive times from every origin cell to every destination town within reach.

Towns are grouped by 1 x 1 degree tile. For each group, one matrix request goes
from every origin cell within `prefilter_km` (straight line) of any town in the
group to those towns. With Valhalla's timedistancematrix, a request with more
sources than targets runs one reverse search per target, so the cost is about
one graph search per town rather than one per cell.

Two passes per group over the same tiles (config/costing.json):
- default: ordinary costing; this is the time that is stored;
- ferry: ferries made almost unusable. A pair that becomes more than
  `ferry_delta_minutes` slower, or unreachable, needs a ferry and is flagged.

Only pairs of at most `max_minutes` (default pass) are kept. Each group writes
one shard, so an interrupted run resumes where it stopped.

Usage: python pipeline/compute.py <region> [--limit-groups N]
Writes work/<region>/shards/<group>.npz and work/<region>/compute.json.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from multiprocessing import Pool

import numpy as np
from scipy.spatial import cKDTree

from common import CONFIG, actor, chord_for_km, load_region, location, log, read_tsv, unit_xyz, work_dir

_ACTOR = None
_REGION = None


def _init(region_name: str) -> None:
    global _ACTOR, _REGION
    _ACTOR = actor(region_name)
    _REGION = region_name


def _matrix(sources, targets, costing_opts, max_dist_m):
    req = {
        "sources": [location(a, b, centre) for a, b, centre in sources],
        "targets": [location(a, b) for a, b in targets],
        "costing": "auto",
        "costing_options": {"auto": costing_opts},
        "expansion_max_distance": max_dist_m,
        "verbose": False,
    }
    res = json.loads(_ACTOR.matrix(json.dumps(req)))["sources_to_targets"]
    dur = np.array(
        [[np.nan if x is None else x for x in row] for row in res["durations"]], dtype=np.float64
    )
    dist = np.array(
        [[np.nan if x is None else x for x in row] for row in res["distances"]], dtype=np.float64
    )
    return dur, dist


def _run_group(job):
    name, src_idx, dst_idx, src_pts, dst_pts, costing, max_minutes, out_path = job
    t0 = time.time()
    passes = costing["passes"]
    max_dist = costing["expansion_max_distance_m"]
    base_s, base_km = _matrix(src_pts, dst_pts, passes["default"], max_dist)
    ferry_s, _ = _matrix(src_pts, dst_pts, passes["ferry"], max_dist)

    base_min = base_s / 60.0
    keep = np.isfinite(base_min) & (np.round(base_min) <= max_minutes)
    si, di = np.nonzero(keep)
    minutes = np.round(base_min[si, di]).astype(np.uint16)
    ferry_min = ferry_s[si, di] / 60.0
    ferry = ~np.isfinite(ferry_min) | (ferry_min - base_min[si, di] > costing["ferry_delta_minutes"])
    np.savez_compressed(
        out_path,
        origin=np.asarray(src_idx, np.int32)[si],
        dest=np.asarray(dst_idx, np.int32)[di],
        minutes=minutes,
        ferry=ferry,
        km=base_km[si, di].astype(np.float32),
    )
    return name, len(src_idx), len(dst_idx), int(keep.sum()), time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--limit-groups", type=int, default=0, help="pilot: only the first N groups")
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args()
    region = load_region(args.region)
    costing = json.loads((CONFIG / "costing.json").read_text())
    wd = work_dir(args.region)
    shards = wd / "shards"
    shards.mkdir(exist_ok=True)

    origins = read_tsv(wd / "origins.tsv")
    dests = read_tsv(wd / "destinations.tsv")
    o_lat = np.array([float(r["lat"]) for r in origins])
    o_lng = np.array([float(r["lng"]) for r in origins])
    o_centre = np.array([r["geonameid"] == "0" for r in origins])
    d_lat = np.array([float(r["lat"]) for r in dests])
    d_lng = np.array([float(r["lng"]) for r in dests])
    tree = cKDTree(unit_xyz(o_lat, o_lng))
    radius = chord_for_km(region["prefilter_km"])

    groups: dict[str, list[int]] = defaultdict(list)
    for j in range(len(dests)):
        groups[f"{math.floor(d_lat[j])}_{math.floor(d_lng[j])}"].append(j)

    jobs = []
    names = sorted(groups)
    if args.limit_groups:
        names = names[: args.limit_groups]
    d_xyz = unit_xyz(d_lat, d_lng)
    for name in names:
        out = shards / f"{name}.npz"
        if out.exists():
            continue
        dst_idx = groups[name]
        near = tree.query_ball_point(d_xyz[dst_idx], radius)
        src_idx = sorted(set().union(*near))
        if not src_idx:
            continue
        jobs.append(
            (
                name,
                src_idx,
                dst_idx,
                list(zip(o_lat[src_idx], o_lng[src_idx], o_centre[src_idx])),
                list(zip(d_lat[dst_idx], d_lng[dst_idx])),
                costing,
                region["max_minutes"],
                out,
            )
        )

    workers = args.workers or region["workers"]
    log(f"{len(groups)} groups, {len(jobs)} to run, {workers} workers")
    t0 = time.time()
    done = 0
    pairs = 0
    with Pool(workers, initializer=_init, initargs=(args.region,)) as pool:
        for name, ns, nd, kept, secs in pool.imap_unordered(_run_group, jobs):
            done += 1
            pairs += kept
            log(f"[{done}/{len(jobs)}] {name}: {ns} sources x {nd} towns -> {kept} pairs in {secs:.1f}s")
    wall = time.time() - t0
    (wd / "compute.json").write_text(
        json.dumps(
            {"groups": len(groups), "ran": len(jobs), "pairs_this_run": pairs, "wall_seconds": round(wall, 1)},
            indent=2,
        )
        + "\n"
    )
    log(f"done in {wall:.1f}s, {pairs} pairs kept this run")


if __name__ == "__main__":
    main()
