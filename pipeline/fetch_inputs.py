"""Download and pin the build inputs.

- The OpenStreetMap extract from Geofabrik. "<region>-latest" redirects to a
  dated file (e.g. north-america-260923.osm.pbf); the dated name, Geofabrik's
  md5 and the replication timestamp inside the file are recorded, because that
  date is what identifies the OSM data the table was derived from.
- GeoNames cities1000 and cities500 (CC BY 4.0), which define the origin grid
  and the destination towns. GeoNames dumps are not versioned, so their sha256
  is recorded instead.

Usage: python pipeline/fetch_inputs.py <region>
Writes work/<region>/inputs.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile

import osmium

from common import geonames_dir, load_region, log, sha256_file, work_dir

GEOFABRIK = "https://download.geofabrik.de"
GEONAMES = "https://download.geonames.org/export/dump"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_dated(path: str) -> str:
    """The dated filename that <path>-latest.osm.pbf currently points to."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"{GEOFABRIK}/{path}-latest.osm.pbf", method="HEAD")
    try:
        opener.open(req)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307) and e.headers.get("Location"):
            return e.headers["Location"].rsplit("/", 1)[1]
        raise
    raise SystemExit(f"{path}-latest did not redirect to a dated file; cannot pin the extract")


def download(url: str, dest) -> None:
    if dest.exists():
        log(f"have {dest.name}")
        return
    log(f"downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        while block := r.read(1 << 22):
            f.write(block)
    tmp.rename(dest)


def md5_file(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def main(region_name: str) -> None:
    region = load_region(region_name)
    wd = work_dir(region_name)
    osm_dir = wd.parent / "osm"
    osm_dir.mkdir(exist_ok=True)

    dated = resolve_dated(region["geofabrik"])
    folder = region["geofabrik"].rsplit("/", 1)[0] if "/" in region["geofabrik"] else ""
    base = f"{GEOFABRIK}/{folder + '/' if folder else ''}{dated}"
    pbf = osm_dir / dated
    download(base, pbf)
    expected_md5 = urllib.request.urlopen(base + ".md5").read().decode().split()[0]
    actual_md5 = md5_file(pbf)
    if actual_md5 != expected_md5:
        pbf.unlink()
        raise SystemExit(f"md5 mismatch for {dated}: {actual_md5} != {expected_md5}")
    header = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header()
    replication = header.get("osmosis_replication_timestamp")

    gd = geonames_dir()
    geonames = {}
    for name in ("cities1000", "cities500"):
        z = gd / f"{name}.zip"
        download(f"{GEONAMES}/{name}.zip", z)
        with zipfile.ZipFile(z) as zf:
            zf.extract(f"{name}.txt", gd)
        geonames[name] = {"sha256": sha256_file(gd / f"{name}.txt")}

    inputs = {
        "osm": {
            "file": dated,
            "url": base,
            "md5": actual_md5,
            "replication_timestamp": replication,
            "path": str(pbf),
        },
        "geonames": geonames,
    }
    (wd / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    log(f"OSM {dated} (replication {replication}), GeoNames {', '.join(geonames)}")


if __name__ == "__main__":
    main(sys.argv[1])
