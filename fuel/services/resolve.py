"""Turn a start/finish value into a US coordinate."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from fuel.services.regions import CANADA, in_united_states, normalize_region

_LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_lock = threading.Lock()
_places: dict[str, list[float]] | None = None


class LocationError(ValueError):
    pass


@dataclass(frozen=True)
class Place:
    label: str
    lat: float
    lng: float
    resolved_by: str


def get_places() -> dict[str, list[float]]:
    global _places
    if _places is None:
        with _lock:
            if _places is None:
                path = Path(settings.PLACES_PATH)
                _places = json.loads(path.read_text()) if path.exists() else {}
    return _places


def set_places(places: dict[str, list[float]] | None) -> None:
    global _places
    with _lock:
        _places = places


def parse_location(value, geocode) -> Place:
    if isinstance(value, dict):
        try:
            lat = float(value["lat"])
            lng = float(value.get("lng", value["lon"]) if "lng" not in value else value["lng"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocationError("lat and lng are required") from exc
        return _coordinate_place(lat, lng, f"{lat:.4f},{lng:.4f}")

    if not isinstance(value, str) or not value.strip():
        raise LocationError("start and finish are required")
    text = " ".join(value.split())
    match = _LATLNG.match(text)
    if match:
        return _coordinate_place(float(match.group(1)), float(match.group(2)), text)

    region = _trailing_region(text)
    if region in CANADA:
        raise LocationError("start and finish must be in the United States")
    key = _gazetteer_key(text, region)
    hit = get_places().get(key) if key else None
    if hit:
        lat, lng = float(hit[0]), float(hit[1])
        _require_us(lat, lng)
        return Place(text, lat, lng, "gazetteer")

    found = geocode(text)
    if not found:
        raise LocationError(f"Could not resolve '{text}'")
    lat, lng = float(found[0]), float(found[1])
    _require_us(lat, lng)
    return Place(text, lat, lng, "photon")


def _coordinate_place(lat: float, lng: float, label: str) -> Place:
    _require_us(lat, lng)
    return Place(label, lat, lng, "coordinates")


def _require_us(lat: float, lng: float) -> None:
    if not in_united_states(lat, lng):
        raise LocationError("start and finish must be in the United States")


def _trailing_region(text: str) -> str | None:
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    return normalize_region(parts[-1])


def _gazetteer_key(text: str, region: str | None) -> str | None:
    if not region or region in CANADA:
        return None
    city = ",".join(part.strip() for part in text.split(",")[:-1])
    city = " ".join(city.split()).upper()
    if not city or "," in city:
        return None
    return f"{city}|{region}"
