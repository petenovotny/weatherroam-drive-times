"""Origin cells: where a Weekend Pick search can start from.

The grid is 0.1 degree cells. A cell is included when a GeoNames place of
1,000+ people (cities1000) is in it, plus one ring of neighbours around every
such cell, so a user just outside a town still lands on a covered cell.

Each cell is routed from one representative point:
- the most populous GeoNames place (cities500) inside the cell, because towns
  sit on the side of a lake or bay that people actually start from;
- otherwise the cell centre.

Every point is snapped to the road graph first; one that does not snap is
dropped (the cell simply has no table entry and the app falls back to its
straight-line estimate there).

Each cell also gets a country, used only to flag cross-border pairs: the
representative place's country, or for a centre point the country of the
nearest GeoNames place (an approximation right at a border).

Usage: python pipeline/origins.py <region>
Writes work/<region>/origins.tsv: cell_key, lat, lng, geonameid (0 = centre), country.
"""

from __future__ import annotations

import sys

from scipy.spatial import cKDTree

from common import (
    actor,
    cell_centre,
    cell_index,
    cell_key,
    geonames_dir,
    load_region,
    locate_ok,
    log,
    read_geonames,
    unit_xyz,
    work_dir,
    write_tsv,
)


def main(region_name: str) -> None:
    region = load_region(region_name)
    deg = region["grid_deg"]
    countries, admin1 = region["origin_countries"], region.get("admin1")
    gd = geonames_dir()

    occupied = {
        cell_index(p.lat, p.lng, deg)
        for p in read_geonames(gd / "cities1000.txt", countries, admin1)
    }
    cells = {(a + i, b + j) for a, b in occupied for i in (-1, 0, 1) for j in (-1, 0, 1)}

    best: dict[tuple[int, int], object] = {}
    places = list(read_geonames(gd / "cities500.txt", countries))
    nearest = cKDTree(unit_xyz([p.lat for p in places], [p.lng for p in places]))
    for p in places:
        c = cell_index(p.lat, p.lng, deg)
        if c in cells and (c not in best or p.population > best[c].population):
            best[c] = p

    ordered = sorted(cells, key=lambda c: cell_key(*c))
    points = []
    for c in ordered:
        p = best.get(c)
        if p:
            points.append((p.lat, p.lng, p.geonameid, p.country))
        else:
            lat, lng = cell_centre(*c, deg)
            _, k = nearest.query(unit_xyz([lat], [lng])[0])
            points.append((lat, lng, 0, places[k].country))

    ok = locate_ok(actor(region_name), [(lat, lng, gid == 0) for lat, lng, gid, _ in points])
    rows = [
        (cell_key(*c), f"{lat:.5f}", f"{lng:.5f}", gid, cc)
        for c, (lat, lng, gid, cc), good in zip(ordered, points, ok)
        if good
    ]
    write_tsv(
        work_dir(region_name) / "origins.tsv", ["cell_key", "lat", "lng", "geonameid", "country"], rows
    )
    placed = sum(1 for c in ordered if c in best)
    log(
        f"{len(cells)} cells ({len(occupied)} occupied + ring), {placed} with a place point, "
        f"{len(rows)} snapped, {len(cells) - len(rows)} dropped"
    )


if __name__ == "__main__":
    main(sys.argv[1])
