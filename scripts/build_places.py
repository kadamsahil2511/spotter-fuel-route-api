#!/usr/bin/env python3
"""Build data/us_places.json from the Census national places gazetteer."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = Path("/tmp/gaz.zip")
OUT = ROOT / "data" / "us_places.json"
SUFFIXES = (
    " CDP",
    " city",
    " town",
    " village",
    " borough",
    " municipality",
    " urban county",
    " comunidad",
    " zona urbana",
    " city and borough",
)


def place_name(raw: str) -> str:
    name = " ".join(raw.split())
    lower = name.lower()
    for suffix in SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def main() -> None:
    best: dict[str, tuple[int, float, float]] = {}
    with zipfile.ZipFile(ZIP_PATH) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode("latin-1")
    for line in text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        state, raw_name, aland, lat, lng = (
            parts[0].strip(),
            parts[3].strip(),
            parts[6].strip(),
            parts[10].strip(),
            parts[11].strip(),
        )
        if not state or not raw_name:
            continue
        try:
            area = int(aland or 0)
            latitude = float(lat)
            longitude = float(lng)
        except ValueError:
            continue
        key = f"{place_name(raw_name).upper()}|{state.upper()}"
        current = best.get(key)
        if current is None or area > current[0]:
            best[key] = (area, latitude, longitude)
    payload = {key: [lat, lng] for key, (_area, lat, lng) in sorted(best.items())}
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"places {len(payload)} -> {OUT} ({OUT.stat().st_size} bytes)")
    for sample in ("NEW YORK|NY", "LOS ANGELES|CA", "CHICAGO|IL", "HOUSTON|TX", "PHILADELPHIA|PA"):
        print(sample, payload.get(sample))


if __name__ == "__main__":
    main()
