"""A few-kilobyte slice of a packed table, for the backend's contract test.

Keeps the first N cells of a table (with all their pairs) and every
destination, and writes the expected lookups next to it as JSON, computed by
the Python reader in validate.py. The backend test reads both and must agree.

Usage: python pipeline/make_fixture.py <table.bin> <out-dir> [N]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from pack import write_table
from validate import read_table


def main(src: str, out_dir: str, n: int = 12) -> None:
    t = read_table(Path(src))
    keys = t.cell_keys[:n]
    end = int(t.cell_offs[n])
    offs = t.cell_offs[: n + 1].copy()
    header = dict(t.header, version=t.header["version"] + "-fixture")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_table(out / "mini.bin", header, t.dest_ids, keys, offs, t.pair_dest[:end].copy(), t.pair_val[:end].copy())

    expected = []
    for i, key in enumerate(keys):
        a, b = int(offs[i]), int(offs[i + 1])
        expected.append(
            {
                "cellKey": int(key),
                "entries": [
                    {
                        "geonameid": int(t.dest_ids[t.pair_dest[k]]),
                        "minutes": int(t.pair_val[k] & 0x3FF),
                        "ferry": bool(t.pair_val[k] & (1 << 10)),
                        "border": bool(t.pair_val[k] & (1 << 11)),
                    }
                    for k in range(a, b)
                ],
            }
        )
    # One cell per line keeps the fixture reviewable in a diff.
    lines = ",\n".join(json.dumps(c, separators=(",", ":")) for c in expected)
    (out / "mini.expected.json").write_text("[\n" + lines + "\n]\n")
    print(f"mini.bin: {len(keys)} cells, {end} pairs, {(out / 'mini.bin').stat().st_size} bytes")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(int(x) for x in sys.argv[3:]))
