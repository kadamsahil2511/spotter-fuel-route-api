"""Collapse the OPIS CSV to one row per truck stop. No Django imports."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

CANADA = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT"}


def collapse_csv(path: Path) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            groups[row["OPIS Truckstop ID"].strip()].append(row)
    stations = []
    for opis_id, rows in groups.items():
        names = Counter(_squash(row["Truckstop Name"]) for row in rows)
        places = Counter(
            (_squash(row["Address"]), _squash(row["City"]), _squash(row["State"]).upper())
            for row in rows
        )
        address, city, state = places.most_common(1)[0][0]
        stations.append(
            {
                "opis_id": opis_id,
                "name": names.most_common(1)[0][0],
                "address": address,
                "city": city,
                "state": state,
                "country": "CA" if state in CANADA else "US",
                "price_per_gallon": min(float(row["Retail Price"]) for row in rows),
            }
        )
    return stations


def _squash(value: str) -> str:
    return " ".join((value or "").split())
