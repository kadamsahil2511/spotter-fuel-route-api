#!/usr/bin/env python3
"""Resumable offline geocoder for the truck-stop CSV.

Writes:
  data/geocode/cities.json     city centroids (resume file)
  data/geocode/pois.json       per-OPIS POI hits (resume file)
  data/stations.json           loader file rebuilt after every batch

Photon is queried at a polite interval. Network failures are retried on the
next run; confirmed misses are stored so they are not repeated.

Usage:
  python3 scripts/geocode_stations.py
  python3 scripts/geocode_stations.py --cities-only
  python3 scripts/geocode_stations.py --poi-only
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "fuel-prices.csv"
PROGRESS = ROOT / "data" / "geocode"
CITIES_PATH = PROGRESS / "cities.json"
POIS_PATH = PROGRESS / "pois.json"
STATIONS_PATH = ROOT / "data" / "stations.json"
PHOTON = "https://photon.komoot.io"
USER_AGENT = "SpotterFuelRoute/1.0 (assessment; one-time offline station geocode)"

CANADA = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT"}
STATE_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NS": "Nova Scotia", "ON": "Ontario",
    "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}
# States on the two demo routes first, so a partial file can already answer them.
PRIORITY = [
    "IL", "TX", "MO", "OK", "AR", "LA", "NM", "AZ", "CA",
    "NY", "NJ", "PA", "OH", "IN", "IA", "NE", "KS", "CO",
    "WY", "UT", "NV", "TN", "MS", "KY", "AL", "GA",
]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fuel.services.poi_match import name_matches  # noqa: E402

EARTH_MILES = 3958.7613
INTERVAL = 0.4


def squash(value: str) -> str:
    return " ".join((value or "").split())


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(a)))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def collapse(csv_path: Path) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    with csv_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            groups[row["OPIS Truckstop ID"].strip()].append(row)
    stations = []
    for opis_id, rows in groups.items():
        names = Counter(squash(row["Truckstop Name"]) for row in rows)
        places = Counter(
            (squash(row["Address"]), squash(row["City"]), squash(row["State"]).upper())
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
    stations.sort(key=lambda item: (item["state"], item["city"], item["opis_id"]))
    return stations


def city_key(city: str, state: str) -> str:
    return f"{city.upper()}|{state.upper()}"


class Client:
    def __init__(self) -> None:
        self.interval = INTERVAL
        self._next = 0.0

    def get(self, path: str, params: dict, retries: int = 0) -> dict | None:
        url = f"{PHOTON}{path}?{urllib.parse.urlencode(params)}"
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
            self._next = time.monotonic() + self.interval
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 503) and retries < 6:
                self.interval = min(2.0, self.interval + 0.3)
                print(f"backoff {exc.code} interval={self.interval:.2f}s", flush=True)
                time.sleep(20)
                self._next = time.monotonic() + self.interval
                return self.get(path, params, retries + 1)
            print(f"http {exc.code} {url}", flush=True)
            self._next = time.monotonic() + self.interval
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"network {exc}", flush=True)
            time.sleep(5)
            self._next = time.monotonic() + self.interval
            return None
        return json.loads(body)


def feature_state(props: dict) -> str:
    raw = squash(props.get("state") or props.get("county") or "")
    upper = raw.upper()
    if upper in STATE_NAME:
        return upper
    for code, name in STATE_NAME.items():
        if name.upper() == upper:
            return code
    return upper


def accept_poi(station: dict, feature: dict, city_hit: dict) -> bool:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return False
    lng, lat = float(coords[0]), float(coords[1])
    country = (props.get("countrycode") or "").upper()
    expect = "CA" if station["country"] == "CA" else "US"
    if country and country != expect:
        return False
    state = feature_state(props)
    if state and state != station["state"] and len(state) == 2:
        return False
    if haversine(lat, lng, city_hit["lat"], city_hit["lng"]) > 25:
        return False
    return name_matches(station["name"], props.get("name") or "")


def _norm_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in value.upper())
    return " ".join(cleaned.split()).replace("SAINT ", "ST ")


def pick_city(features: list[dict], city: str, state: str) -> dict | None:
    want = _norm_name(city)
    for feature in features:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or feature_state(props) != state:
            continue
        if _norm_name(props.get("name") or "") != want:
            continue
        return {
            "lat": float(coords[1]),
            "lng": float(coords[0]),
            "label": props.get("name") or city,
            "source": "photon",
        }
    return None


def geocode_cities(stations: list[dict], client: Client) -> dict:
    done = load_json(CITIES_PATH)
    places = json.loads((ROOT / "data" / "us_places.json").read_text())
    pairs = []
    seen = set()
    for station in stations:
        key = city_key(station["city"], station["state"])
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key, station["city"], station["state"], station["country"]))
    for key, city, state, country in pairs:
        if country == "US" and key in places:
            lat, lng = places[key]
            done[key] = {"lat": lat, "lng": lng, "label": city, "source": "census"}
        else:
            current = done.get(key) or {}
            label_ok = _norm_name(current.get("label") or "") == _norm_name(city)
            if not (current.get("source") == "photon" and label_ok):
                done.pop(key, None)
    save_json(CITIES_PATH, done)
    write_stations(stations, done, load_json(POIS_PATH))
    rank = {code: index for index, code in enumerate(PRIORITY)}
    pairs.sort(key=lambda item: (rank.get(item[2], 100), item[2], item[1]))
    pending = [item for item in pairs if "lat" not in done.get(item[0], {})]
    print(
        f"cities {len(pairs)} census {sum(1 for item in pairs if done.get(item[0], {}).get('source') == 'census')} "
        f"pending {len(pending)}",
        flush=True,
    )
    for index, (key, city, state, country) in enumerate(pending, start=1):
        state_name = STATE_NAME.get(state, state)
        hit = None
        for params in (
            {"q": f"{city}, {state_name}", "limit": 5, "lang": "en", "layer": "city"},
            {"q": f"{city}, {state_name}", "limit": 5, "lang": "en"},
        ):
            payload = client.get("/api/", params)
            if payload is None:
                hit = None
                break
            hit = pick_city(payload.get("features") or [], city, state)
            if hit:
                break
        else:
            hit = None
        if payload is None and hit is None:
            print(f"skip city retry later {city}, {state}", flush=True)
            continue
        done[key] = hit or {"miss": True}
        if index % 25 == 0 or index == len(pending):
            save_json(CITIES_PATH, done)
            write_stations(stations, done, load_json(POIS_PATH))
            print(f"cities {index}/{len(pending)} {city}, {state}", flush=True)
    save_json(CITIES_PATH, done)
    return done


def geocode_pois(stations: list[dict], cities: dict, client: Client) -> dict:
    done = load_json(POIS_PATH)
    pending = []
    for station in stations:
        if station["opis_id"] in done:
            continue
        city_hit = cities.get(city_key(station["city"], station["state"])) or {}
        if city_hit.get("miss") or "lat" not in city_hit:
            done[station["opis_id"]] = {"miss": True, "reason": "no_city"}
            continue
        pending.append(station)
    print(f"pois pending {len(pending)} cached {len(done)}", flush=True)
    for index, station in enumerate(pending, start=1):
        city_hit = cities[city_key(station["city"], station["state"])]
        payload = client.get(
            "/api/",
            {
                "q": f"{station['name']}, {station['city']}, {STATE_NAME.get(station['state'], station['state'])}",
                "lat": f"{city_hit['lat']:.5f}",
                "lon": f"{city_hit['lng']:.5f}",
                "limit": 5,
                "lang": "en",
            },
        )
        if payload is None:
            print(f"skip poi retry later {station['opis_id']}", flush=True)
            continue
        chosen = None
        for feature in payload.get("features") or []:
            if accept_poi(station, feature, city_hit):
                coords = feature["geometry"]["coordinates"]
                props = feature.get("properties") or {}
                chosen = {
                    "lat": float(coords[1]),
                    "lng": float(coords[0]),
                    "label": props.get("name") or station["name"],
                }
                break
        done[station["opis_id"]] = chosen or {"miss": True}
        if index % 25 == 0 or index == len(pending):
            save_json(POIS_PATH, done)
            write_stations(stations, cities, done)
            print(f"pois {index}/{len(pending)} {station['name'][:40]} {station['city']}", flush=True)
    save_json(POIS_PATH, done)
    return done


def write_stations(stations: list[dict], cities: dict, pois: dict) -> None:
    records = []
    for station in stations:
        poi = pois.get(station["opis_id"]) or {}
        city_hit = cities.get(city_key(station["city"], station["state"])) or {}
        record = dict(station)
        if poi.get("lat") is not None:
            record.update(
                lat=poi["lat"],
                lng=poi["lng"],
                geocode_quality="poi",
                geocode_label=poi.get("label") or station["name"],
            )
        elif city_hit.get("lat") is not None:
            record.update(
                lat=city_hit["lat"],
                lng=city_hit["lng"],
                geocode_quality="city_centroid",
                geocode_label=city_hit.get("label") or station["city"],
            )
        else:
            record.update(
                lat=None,
                lng=None,
                geocode_quality="unresolved",
                geocode_label="",
            )
        records.append(record)
    save_json(STATIONS_PATH, {"stations": records})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities-only", action="store_true")
    parser.add_argument("--poi-only", action="store_true")
    args = parser.parse_args()
    stations = collapse(CSV_PATH)
    print(f"collapsed {len(stations)} stations", flush=True)
    client = Client()
    cities = load_json(CITIES_PATH) if args.poi_only else geocode_cities(stations, client)
    if not args.cities_only:
        geocode_pois(stations, cities, client)
    write_stations(stations, cities if not args.poi_only else load_json(CITIES_PATH), load_json(POIS_PATH))
    records = json.loads(STATIONS_PATH.read_text())["stations"]
    counts = Counter(item["geocode_quality"] for item in records)
    print("coverage", dict(counts), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
