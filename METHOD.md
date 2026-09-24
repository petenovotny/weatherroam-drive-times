# Method

How the drive-time table is made from OpenStreetMap. Under the ODbL (section
4.6), this document with the scripts in `pipeline/` is "the method of making
the alterations to the Database". Every number below comes from the config
files in `config/`, so the files, not this text, are authoritative.

## 1. Inputs

- **OpenStreetMap:** the Geofabrik extract for the region (North America for
  the production table). Geofabrik's "latest" link redirects to a dated file;
  that file name, its md5 and the replication timestamp inside it are recorded
  in the manifest. (`pipeline/fetch_inputs.py`)
- **GeoNames** `cities1000` and `cities500`: they define the grid and the
  towns. Their sha256 is recorded, since GeoNames dumps are not versioned.

## 2. Road graph

- **Router:** [Valhalla](https://github.com/valhalla/valhalla), from the
  `pyvalhalla` package at the version pinned in `pyproject.toml`.
- **The only change made to the OpenStreetMap data:** ways tagged
  `ice_road=yes` or `winter_road=yes` are removed before the graph is built.
  Valhalla does not understand these tags, so without this they would count
  as open all year. Nothing else is filtered.
- Settings that differ from Valhalla's defaults are in
  `config/valhalla-overrides.json`: the one-to-many matrix algorithm, larger
  matrix limits, and cache size.
- The graph is built in stages so that peak memory can be limited on large
  regions. (`pipeline/build_graph.sh`)

## 3. Where trips start (the grid)

- The region is divided into 0.1 degree cells (about 11 km north-south and 8 km
  east-west at 45 degrees north).
- A cell is included if a GeoNames place of 1,000+ people is in it, plus one
  ring of neighbouring cells around each such cell.
- Each cell is routed from one point: the most populous GeoNames place in the
  cell, or, if the cell has none, the cell centre.
- **Centre points snap only to tertiary or larger roads,** so a trip starts
  from a road that goes somewhere rather than a farm track or private drive.
- A point that does not snap to any road is left out; that cell has no entry.
- Each cell's country, used only for the border flag, is its place's country,
  or for a centre point the country of the nearest GeoNames place.
  (`pipeline/origins.py`)

## 4. Where trips go (the towns)

- GeoNames places in the destination countries with at least the configured
  population. A release can add specific GeoNames ids on top (WeatherRoam's
  list of smaller destination towns), passed with `--ids`.
- Towns that do not snap to a road are left out. (`pipeline/destinations.py`)

## 5. Drive times

- Grid cells are grouped by 1 x 1 degree tile. For each group, one matrix
  request routes from those cells to every town within 720 km in a straight
  line. Valhalla runs one forward search from each cell, which stops at 780 km
  of road (`expansion_max_distance_m` in `config/costing.json`).
- Why forward: searching backwards from each town would need about five
  times fewer searches, but in testing it overstated times compared with
  Valhalla's own route between the same two points (Washington: 5% of trips
  by more than 5.3%, worst 52%). Forward searches match the route.
- Both cuts are only there to save computing. No road trip within 6 hours
  covers more than 720 km in a straight line, and none covers more than 780
  km of road (that would be a 130 km/h average). The road cut matters most:
  on the full continental graph a search otherwise keeps expanding long after
  the last reachable town, and the first North America run projected 40 hours
  without it.
- Starting points whose every destination is on an unconnected road network
  (another Hawaiian island, say) are retried one destination at a time;
  towns they cannot reach are simply absent.
- **Two passes, same graph** (`config/costing.json`):
  1. **Default:** ordinary car costing, 5 minutes added per ferry crossing and
     10 per border crossing. This is the time that is stored.
  2. **Ferry check:** ferries made almost unusable (12 hours each). A trip that
     becomes more than 10 minutes slower, or impossible, needs a ferry, and is
     flagged.
  
  Turning ferries off alone is not enough: Valhalla still uses a ferry when
  there is no other way, so islands would not be flagged.
- **Kept:** trips of 6 hours (360 minutes) or less by the default pass, the
  largest radius the app offers.
- Times are free-flow: no traffic, no time of day, no seasonal closures.
  (`pipeline/compute.py`)

## 6. The table file

- Minutes are rounded to the nearest minute and stored per cell, sorted from
  nearest to farthest.
- Each entry has a flag for "needs a ferry" and for "crosses a national border".
- Towns in `config/exclusions.tsv` (for example car-free islands) are removed.
- The file holds only GeoNames ids and times. (`pipeline/pack.py` documents
  the byte layout.)

## 7. Checks before release

`pipeline/validate.py` must pass before a table is published:

- the file's structure;
- the error from starting at a cell's point rather than an exact address (p50
  at most 8 minutes, p90 at most 15);
- agreement with fresh routes from the same points (p99 within 2 minutes);
- no implied speeds above 137 km/h (85 mph);
- named sanity cases per region, such as ferry islands and known routes.

Its report is published with each release.

## Known limits

- Free-flow times: real trips with traffic or stops take longer.
- Seasonal roads tagged with conditional access are treated as open.
- A cell's point stands in for everywhere in the cell. Typical error is a few
  minutes; the validation report gives the measured figures.
