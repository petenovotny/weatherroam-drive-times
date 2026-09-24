"""Shared helpers: region config, work paths, GeoNames rows, the grid key, and
the Valhalla actor. Every pipeline step imports from here so the grid and the
file layout are defined exactly once."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

EARTH_KM = 6371.0088


def load_region(name: str) -> dict:
    return json.loads((CONFIG / f"region-{name}.json").read_text())


def work_dir(region: str) -> Path:
    d = ROOT / "work" / region
    d.mkdir(parents=True, exist_ok=True)
    return d


def geonames_dir() -> Path:
    d = ROOT / "work" / "geonames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


# --- grid -------------------------------------------------------------------
# 0.1 degree cells. The key is what the backend computes at lookup time, so
# this formula is part of the published format (header JSON "grid.key").


def cell_index(lat: float, lng: float, deg: float = 0.1) -> tuple[int, int]:
    inv = round(1 / deg)
    return math.floor(lat * inv + 1e-9), math.floor(lng * inv + 1e-9)


def cell_key(lat_idx: int, lng_idx: int) -> int:
    return (lat_idx + 900) * 3600 + (lng_idx + 1800)


def cell_centre(lat_idx: int, lng_idx: int, deg: float = 0.1) -> tuple[float, float]:
    return (lat_idx + 0.5) * deg, (lng_idx + 0.5) * deg


# --- geometry -----------------------------------------------------------------


def unit_xyz(lat, lng) -> np.ndarray:
    """Points on the unit sphere, for KD-tree radius queries."""
    lat = np.radians(np.asarray(lat, float))
    lng = np.radians(np.asarray(lng, float))
    return np.c_[np.cos(lat) * np.cos(lng), np.cos(lat) * np.sin(lng), np.sin(lat)]


def chord_for_km(km: float) -> float:
    return 2 * math.sin(km / EARTH_KM / 2)


def haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


# --- GeoNames -----------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    geonameid: int
    name: str
    lat: float
    lng: float
    country: str
    admin1: str
    population: int


def read_geonames(path: Path, countries, admin1=None):
    """Rows of a GeoNames citiesNNN.txt dump, filtered by country (and admin1)."""
    countries = set(countries)
    admin1 = set(admin1) if admin1 else None
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 19 or p[8] not in countries:
                continue
            if admin1 and p[10] not in admin1:
                continue
            yield Place(int(p[0]), p[1], float(p[4]), float(p[5]), p[8], p[10], int(p[14] or 0))


# --- TSV files between steps --------------------------------------------------


def write_tsv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")


def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        return [dict(zip(header, line.rstrip("\n").split("\t"))) for line in f if line.strip()]


# --- Valhalla -----------------------------------------------------------------


def actor(region: str):
    from valhalla import Actor

    return Actor(json.loads((work_dir(region) / "valhalla.json").read_text()))


# Cell-centre points (no town in the cell) snap only to tertiary or better
# roads. From a farm track or gravel road, Valhalla's reverse matrix searches
# overstate the drive time (7.5% of such pairs by more than 5 min in the
# Minnesota pilot, against 1.7% with this filter). See METHOD.md.
CENTRE_SNAP_FILTER = {"min_road_class": "tertiary"}


def location(lat: float, lng: float, centre: bool = False) -> dict:
    loc = {"lat": lat, "lon": lng}
    if centre:
        loc["search_filter"] = CENTRE_SNAP_FILTER
    return loc


def locate_ok(act, points: list[tuple[float, float, bool]], chunk: int = 1000) -> list[bool]:
    """Whether each (lat, lng, is_centre) snaps to the road graph.

    One unsnappable location makes a whole matrix request fail ("Locations are
    in unconnected regions"), so every point is checked before it is used.
    """
    ok: list[bool] = []
    for i in range(0, len(points), chunk):
        locs = [location(a, b, c) for a, b, c in points[i : i + chunk]]
        res = json.loads(act.locate(json.dumps({"locations": locs, "costing": "auto", "verbose": False})))
        ok.extend(bool(r.get("edges")) for r in res)
    return ok
