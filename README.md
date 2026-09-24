# WeatherRoam drive-time table

Approximate driving times from anywhere in the United States and Canada to
nearby towns, computed from OpenStreetMap. WeatherRoam uses this table for
Weekend Pick ("where is the weather good within a few hours' drive?").

This repository is the **method**: the scripts, router settings, grid and
inputs that produce the table. Each release also carries the **table itself**.
Both are offered here to meet the share-alike terms of the Open Database
Licence (ODbL) that OpenStreetMap data is under.

## What's in a release

| File | What it is | Licence |
|---|---|---|
| `drive-table-<version>.bin` | The table: for each ~11 x 8 km grid cell, the driving minutes to every town on the list within 7 hours, with flags for trips that need a ferry or cross a border | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) |
| `origins-<version>.tsv` | The grid cells and the point each is routed from | CC BY 4.0 (GeoNames) |
| `destinations-<version>.tsv` | The towns, by GeoNames id | CC BY 4.0 (GeoNames) |
| `manifest-<version>.json` | Exactly which OpenStreetMap extract, router version and settings produced it | -- |
| `validation-report-<version>.md` | The checks the table passed before release | -- |
| `SHA256SUMS` | Checksums | -- |

The file format is documented at the top of [`pipeline/pack.py`](pipeline/pack.py),
and [`pipeline/validate.py`](pipeline/validate.py) contains a complete reader.

## Rebuilding it

```bash
REGION=mn ./run.sh      # Minnesota: a few minutes on a laptop
REGION=na ./run.sh      # United States and Canada: a machine with ~256 GB RAM, several hours
```

Requirements: [uv](https://docs.astral.sh/uv/) and
[osmium-tool](https://osmcode.org/osmium-tool/). Everything else, including
the Valhalla router, installs from `pyproject.toml`. [METHOD.md](METHOD.md)
explains every step and setting in plain language.

## Attribution

Drive times © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
available under the Open Database Licence. Place data from
[GeoNames](https://www.geonames.org/), CC BY 4.0. Routing by
[Valhalla](https://github.com/valhalla/valhalla) (MIT).

## Licences

- Code in this repository: [MIT](LICENSE).
- Data: see [DATA-LICENSE.md](DATA-LICENSE.md).

Questions: support@weatherroam.com
