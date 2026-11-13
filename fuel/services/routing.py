"""One driving-route call. OSRM first, OpenRouteService only if that call fails."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings

from fuel.services.geo import decode_polyline, encode_polyline


class RoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DrivingRoute:
    distance_miles: float
    duration_seconds: float
    coordinates: list[tuple[float, float]]
    polyline: str
    provider: str


def fetch_route(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> DrivingRoute:
    try:
        return _osrm(origin_lat, origin_lng, dest_lat, dest_lng)
    except RoutingError:
        if not settings.ORS_API_KEY:
            raise
        return _ors(origin_lat, origin_lng, dest_lat, dest_lng)


def _osrm(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> DrivingRoute:
    url = (
        f"{settings.OSRM_BASE_URL.rstrip('/')}/route/v1/driving/"
        f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
    )
    payload = _get(
        url,
        params={"overview": "full", "geometries": "polyline", "alternatives": "false"},
    )
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(payload.get("code") or "NoRoute")
    route = payload["routes"][0]
    polyline = route.get("geometry") or ""
    coords = decode_polyline(polyline)
    if len(coords) < 2:
        raise RoutingError("OSRM returned an empty geometry")
    return DrivingRoute(
        distance_miles=float(route["distance"]) / 1609.344,
        duration_seconds=float(route["duration"]),
        coordinates=coords,
        polyline=polyline,
        provider="osrm",
    )


def _ors(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> DrivingRoute:
    url = f"{settings.ORS_BASE_URL.rstrip('/')}/v2/directions/driving-car/geojson"
    payload = _get(
        url,
        params={"start": f"{origin_lng},{origin_lat}", "end": f"{dest_lng},{dest_lat}"},
        headers={"Authorization": settings.ORS_API_KEY},
    )
    features = payload.get("features") or []
    if not features:
        raise RoutingError("OpenRouteService returned no route")
    geometry = features[0].get("geometry") or {}
    raw = geometry.get("coordinates") or []
    coords = [(float(pair[0]), float(pair[1])) for pair in raw]
    if len(coords) < 2:
        raise RoutingError("OpenRouteService returned an empty geometry")
    summary = (features[0].get("properties") or {}).get("summary") or {}
    return DrivingRoute(
        distance_miles=float(summary.get("distance") or 0) / 1609.344,
        duration_seconds=float(summary.get("duration") or 0),
        coordinates=coords,
        polyline=encode_polyline(coords),
        provider="ors",
    )


def _get(url: str, params: dict, headers: dict | None = None) -> dict:
    request_headers = {"User-Agent": settings.ROUTING_USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        response = requests.get(url, params=params, headers=request_headers, timeout=(3.05, 10))
    except requests.RequestException as exc:
        raise RoutingError(str(exc)) from exc
    if response.status_code >= 400:
        raise RoutingError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise RoutingError("routing provider returned invalid JSON") from exc
