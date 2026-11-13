"""Project stations onto a route polyline and keep those inside the corridor."""

from __future__ import annotations

import math

from fuel.services.geo import RouteIndex, mile_markers, scale_miles
from fuel.services.optimizer import Pump

MILES_PER_LAT_DEGREE = 69.0


def candidates(
    coords: list[tuple[float, float]],
    road_miles: float,
    stations: list[dict],
    corridor_miles: float,
) -> tuple[list[Pump], float]:
    miles = scale_miles(mile_markers(coords), road_miles)
    if len(coords) < 2 or miles[-1] <= 0:
        return [], road_miles
    index = RouteIndex(coords, miles)
    min_lng = min(coord[0] for coord in coords)
    max_lng = max(coord[0] for coord in coords)
    min_lat = min(coord[1] for coord in coords)
    max_lat = max(coord[1] for coord in coords)
    pad_lat = corridor_miles / MILES_PER_LAT_DEGREE
    # A degree of longitude shrinks toward the poles and is widest near the
    # equator. Size the box from the route latitude closest to the equator.
    equator_lat = 0.0 if min_lat <= 0 <= max_lat else min(min_lat, max_lat, key=abs)
    cos_lat = max(0.2, math.cos(math.radians(equator_lat)))
    pad_lng = corridor_miles / (MILES_PER_LAT_DEGREE * cos_lat)
    pumps: list[Pump] = []
    for station in stations:
        if station.get("geocode_quality") == "unresolved":
            continue
        lat, lng = station.get("lat"), station.get("lng")
        if lat is None or lng is None:
            continue
        if not (min_lat - pad_lat <= lat <= max_lat + pad_lat and min_lng - pad_lng <= lng <= max_lng + pad_lng):
            continue
        projected = index.project(lat, lng)
        if projected is None:
            continue
        cross, mile = projected
        if cross > corridor_miles:
            continue
        if mile <= 0.05 or mile >= miles[-1] - 0.05:
            continue
        enriched = dict(station)
        enriched["cross_track_miles"] = cross
        enriched["mile_marker"] = mile
        pumps.append(Pump(mile=mile, price=float(station["price_per_gallon"]), station=enriched))
    return pumps, miles[-1]
