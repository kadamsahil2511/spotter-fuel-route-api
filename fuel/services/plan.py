"""Resolve, route, choose fuel stops, and shape the API payload."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.cache import cache

from fuel.services.corridor import candidates
from fuel.services.geo import decode_polyline, douglas_peucker
from fuel.services.limiter import acquire_osrm_slot
from fuel.services.optimizer import plan_fuel
from fuel.services.resolve import LocationError, Place, get_places, parse_location
from fuel.services.routing import DrivingRoute, RoutingError, fetch_route
from fuel.services.stations import get_stations, station_path

METERS_PER_MILE = 1609.344


class PlanError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def build_plan(start, finish, range_miles: float, mpg: float, corridor_miles: float) -> dict:
    started = time.perf_counter()
    calls = {"geocode": 0, "route": 0}
    try:
        origin = parse_location(start, lambda query: _geocode(query, calls))
        destination = parse_location(finish, lambda query: _geocode(query, calls))
    except LocationError as exc:
        raise PlanError(str(exc), 400) from exc

    plan_key = _plan_key(origin, destination, range_miles, mpg, corridor_miles)
    cached_plan = cache.get(plan_key)
    if cached_plan:
        payload = dict(cached_plan)
        payload["cache"] = "hit"
        payload["external_calls"] = {"geocode": 0, "route": 0}
        payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return payload

    cache_status = "miss"
    try:
        route, cache_status, route_calls = _route(origin, destination, range_miles, mpg, corridor_miles)
        calls["route"] += route_calls
    except RoutingError as exc:
        raise PlanError(f"Routing failed: {exc}", 502) from exc

    pumps, distance_miles = candidates(route.coordinates, route.distance_miles, get_stations(), corridor_miles)
    fuel = plan_fuel(pumps, distance_miles, range_miles, mpg)
    simplified = douglas_peucker(route.coordinates)
    payload = _payload(origin, destination, route, simplified, fuel, range_miles, mpg, corridor_miles, calls, cache_status)
    cache.set(plan_key, {key: value for key, value in payload.items() if key != "elapsed_ms"}, timeout=None)
    payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return payload


def _coord_key(origin: Place, destination: Place, range_miles: float, mpg: float, corridor_miles: float) -> str:
    return "{:.4f},{:.4f}:{:.4f},{:.4f}:{}:{}:{}".format(
        origin.lat,
        origin.lng,
        destination.lat,
        destination.lng,
        range_miles,
        mpg,
        corridor_miles,
    )


def _plan_key(origin: Place, destination: Place, range_miles: float, mpg: float, corridor_miles: float) -> str:
    try:
        token = str(station_path().stat().st_mtime_ns)
    except OSError:
        token = "0"
    raw = _coord_key(origin, destination, range_miles, mpg, corridor_miles) + ":" + token
    return "plan.v2." + hashlib.sha256(raw.encode()).hexdigest()


def _geometry_key(origin: Place, destination: Place, range_miles: float, mpg: float, corridor_miles: float) -> str:
    raw = _coord_key(origin, destination, range_miles, mpg, corridor_miles)
    return "route.v1." + hashlib.sha256(raw.encode()).hexdigest()


def _geocode(query: str, calls: dict) -> tuple[float, float] | None:
    normalized = " ".join(query.upper().split())
    key = "geo.v1." + hashlib.sha256(normalized.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return None if cached == "miss" else (cached[0], cached[1])
    calls["geocode"] += 1
    found = _photon(query)
    cache.set(key, "miss" if found is None else [found[0], found[1]], timeout=None)
    return found


def _photon(query: str) -> tuple[float, float] | None:
    try:
        response = requests.get(
            f"{settings.PHOTON_BASE_URL.rstrip('/')}/api/",
            params={"q": query, "limit": 1, "lang": "en"},
            headers={"User-Agent": settings.ROUTING_USER_AGENT, "Accept": "application/json"},
            timeout=(3.05, 8),
        )
        response.raise_for_status()
        features = response.json().get("features") or []
    except (requests.RequestException, ValueError):
        return None
    if not features:
        return None
    coords = features[0]["geometry"]["coordinates"]
    return float(coords[1]), float(coords[0])


def _route(origin: Place, destination: Place, range_miles: float, mpg: float, corridor_miles: float):
    key = _geometry_key(origin, destination, range_miles, mpg, corridor_miles)
    cached = cache.get(key)
    if cached:
        return _from_cache(cached), "hit", 0
    acquire_osrm_slot()
    route = fetch_route(origin.lat, origin.lng, destination.lat, destination.lng)
    cache.set(
        key,
        {
            "distance_m": route.distance_miles * METERS_PER_MILE,
            "duration_s": route.duration_seconds,
            "polyline": route.polyline,
            "provider": route.provider,
        },
        timeout=None,
    )
    calls = 2 if route.provider == "ors" else 1
    return route, "miss", calls


def _from_cache(payload: dict) -> DrivingRoute:
    coords = decode_polyline(payload["polyline"])
    return DrivingRoute(
        distance_miles=float(payload["distance_m"]) / METERS_PER_MILE,
        duration_seconds=float(payload["duration_s"]),
        coordinates=coords,
        polyline=payload["polyline"],
        provider=payload["provider"],
    )


def _payload(origin, destination, route, simplified, fuel, range_miles, mpg, corridor_miles, calls, cache_status):
    stops = []
    for purchase in fuel.purchases:
        station = purchase.station
        stops.append(
            {
                "opis_id": station["opis_id"],
                "name": station["name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "price_per_gallon": round(float(station["price_per_gallon"]), 6),
                "location": {"lat": round(float(station["lat"]), 6), "lng": round(float(station["lng"]), 6)},
                "mile_marker": round(purchase.mile, 1),
                "cross_track_miles": round(float(station.get("cross_track_miles") or 0), 2),
                "gallons": round(purchase.gallons, 3),
                "cost": round(purchase.cost + 1e-9, 2),
                "geocode_quality": station.get("geocode_quality"),
            }
        )
    gap = None
    if not fuel.feasible:
        gap = {
            "from_mile": round(fuel.gap_from or 0, 1),
            "to_mile": round(fuel.gap_to or route.distance_miles, 1),
            "reason": "No station within 500 miles" if range_miles == 500 else f"No station within {range_miles:g} miles",
        }
    query = urlencode(
        {
            "start": origin.label,
            "finish": destination.label,
            "range_miles": range_miles,
            "mpg": mpg,
            "corridor_miles": corridor_miles,
        }
    )
    return {
        "feasible": fuel.feasible,
        "start": {"label": origin.label, "lat": round(origin.lat, 6), "lng": round(origin.lng, 6), "resolved_by": origin.resolved_by},
        "finish": {
            "label": destination.label,
            "lat": round(destination.lat, 6),
            "lng": round(destination.lng, 6),
            "resolved_by": destination.resolved_by,
        },
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_seconds": round(route.duration_seconds),
            "geometry": {"type": "LineString", "coordinates": [[round(lng, 5), round(lat, 5)] for lng, lat in simplified]},
            "provider": route.provider,
        },
        "assumptions": {
            "tank_range_miles": range_miles,
            "mpg": mpg,
            "start_tank": "full",
            "initial_fuel_charged_usd": 0,
            "corridor_miles": corridor_miles,
            "detour_fuel": "not charged",
        },
        "fuel_stops": stops,
        "total_gallons": round(fuel.total_gallons, 3),
        "total_fuel_cost": round(fuel.total_cost + 1e-9, 2),
        "gap": gap,
        "map_path": f"/api/v1/map?{query}",
        "external_calls": calls,
        "cache": cache_status,
    }


def places_loaded() -> bool:
    return bool(get_places())
