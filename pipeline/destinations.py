"""Destination towns: the places Weekend Pick can suggest.

By default, every GeoNames place in the region's destination countries with at
least `dest_min_population` people. `--ids FILE` adds specific GeoNames ids
(one per line; e.g. WeatherRoam's list of smaller destination towns) on top.
The app joins its own town list to this table by GeoNames id only, so the table
never carries names or scores of its own (see METHOD.md, "Licences").

Towns that do not snap to the road graph are dropped and listed.

Usage: python pipeline/destinations.py <region> [--ids FILE]
Writes work/<region>/destinations.tsv: geonameid, name, country, lat, lng.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common import actor, geonames_dir, load_region, locate_ok, log, read_geonames, work_dir, write_tsv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", type=Path, help="extra GeoNames ids, one per line")
    args = ap.parse_args()
    region = load_region(args.region)

    extra: set[int] = set()
    if args.ids:
        extra = {int(x) for x in args.ids.read_text().split() if x.strip().isdigit()}

    towns = {}
    for p in read_geonames(geonames_dir() / "cities500.txt", region["dest_countries"], region.get("admin1")):
        if p.population >= region["dest_min_population"] or p.geonameid in extra:
            towns[p.geonameid] = p
    missing = extra - towns.keys()
    if missing:
        log(f"{len(missing)} extra ids are not in cities500 for this region, ignored: {sorted(missing)[:10]}")

    ordered = sorted(towns.values(), key=lambda p: p.geonameid)
    ok = locate_ok(actor(args.region), [(p.lat, p.lng, False) for p in ordered])
    dropped = [p for p, good in zip(ordered, ok) if not good]
    for p in dropped:
        log(f"dropped (does not snap): {p.geonameid} {p.name} {p.country}")
    rows = [
        (p.geonameid, p.name, p.country, f"{p.lat:.5f}", f"{p.lng:.5f}")
        for p, good in zip(ordered, ok)
        if good
    ]
    write_tsv(work_dir(args.region) / "destinations.tsv", ["geonameid", "name", "country", "lat", "lng"], rows)
    log(f"{len(rows)} destinations ({len(extra)} extra ids requested), {len(dropped)} dropped")


if __name__ == "__main__":
    main()
