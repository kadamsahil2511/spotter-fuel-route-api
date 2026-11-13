"""Polyline, distance, and corridor geometry. No Django imports."""

from __future__ import annotations

import math
from collections import defaultdict

EARTH_MILES = 3958.7613


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(a)))


def decode_polyline(polyline: str, precision: int = 5) -> list[tuple[float, float]]:
    """Return GeoJSON (lng, lat) pairs."""
    inv = 10**precision
    coords: list[tuple[float, float]] = []
    lat = lng = 0
    index = 0
    length = len(polyline)
    while index < length:
        delta = _polyline_delta(polyline, index)
        lat += delta[0]
        index = delta[2]
        delta = _polyline_delta(polyline, index)
        lng += delta[0]
        index = delta[2]
        coords.append((lng / inv, lat / inv))
    return coords


def _polyline_delta(polyline: str, index: int) -> tuple[int, int, int]:
    shift = result = 0
    while True:
        chunk = ord(polyline[index]) - 63
        index += 1
        result |= (chunk & 0x1F) << shift
        shift += 5
        if chunk < 0x20:
            break
    delta = ~(result >> 1) if result & 1 else result >> 1
    return delta, 0, index


def encode_polyline(coords: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    parts: list[str] = []
    prev_lat = prev_lng = 0
    for lng, lat in coords:
        ilat = int(round(lat * factor))
        ilng = int(round(lng * factor))
        parts.append(_encode_value(ilat - prev_lat))
        parts.append(_encode_value(ilng - prev_lng))
        prev_lat, prev_lng = ilat, ilng
    return "".join(parts)


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def mile_markers(coords: list[tuple[float, float]]) -> list[float]:
    cumulative = [0.0]
    for (lng1, lat1), (lng2, lat2) in zip(coords, coords[1:]):
        cumulative.append(cumulative[-1] + haversine_miles(lat1, lng1, lat2, lng2))
    return cumulative


def scale_miles(cumulative: list[float], road_miles: float) -> list[float]:
    if len(cumulative) < 2 or cumulative[-1] <= 0 or road_miles <= 0:
        return cumulative
    if abs(cumulative[-1] - road_miles) / road_miles > 0.02:
        factor = road_miles / cumulative[-1]
        return [value * factor for value in cumulative]
    return cumulative


def _project(lat: float, lng: float, lat1: float, lng1: float, lat2: float, lng2: float) -> tuple[float, float]:
    """Cross-track miles and fraction t along the segment, t clamped to [0, 1]."""
    lat0 = (lat1 + lat2) / 2
    cos = math.cos(math.radians(lat0))
    scale = EARTH_MILES * math.pi / 180

    def xy(latitude: float, longitude: float) -> tuple[float, float]:
        return (longitude * cos * scale, latitude * scale)

    px, py = xy(lat, lng)
    ax, ay = xy(lat1, lng1)
    bx, by = xy(lat2, lng2)
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), t


class RouteIndex:
    """Segment grid so each station is tested against nearby pieces of the route."""

    def __init__(self, coords: list[tuple[float, float]], miles: list[float], cell_deg: float = 0.5):
        self.coords = coords
        self.miles = miles
        self.cell = cell_deg
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index in range(len(coords) - 1):
            lng1, lat1 = coords[index]
            lng2, lat2 = coords[index + 1]
            for key in self._keys_for_box(lat1, lng1, lat2, lng2):
                self.cells[key].append(index)

    def _key(self, lat: float, lng: float) -> tuple[int, int]:
        return (math.floor(lat / self.cell), math.floor(lng / self.cell))

    def _keys_for_box(self, lat1: float, lng1: float, lat2: float, lng2: float) -> set[tuple[int, int]]:
        keys = set()
        min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)
        min_lng, max_lng = min(lng1, lng2), max(lng1, lng2)
        lat = min_lat
        while lat <= max_lat + 1e-9:
            lng = min_lng
            while lng <= max_lng + 1e-9:
                keys.add(self._key(lat, lng))
                lng += self.cell
            lat += self.cell
        keys.add(self._key(max_lat, max_lng))
        return keys

    def project(self, lat: float, lng: float) -> tuple[float, float] | None:
        base = self._key(lat, lng)
        best: tuple[float, float] | None = None
        seen: set[int] = set()
        for dlat in (-1, 0, 1):
            for dlng in (-1, 0, 1):
                for index in self.cells.get((base[0] + dlat, base[1] + dlng), ()):
                    if index in seen:
                        continue
                    seen.add(index)
                    lng1, lat1 = self.coords[index]
                    lng2, lat2 = self.coords[index + 1]
                    cross, fraction = _project(lat, lng, lat1, lng1, lat2, lng2)
                    mile = self.miles[index] + fraction * (self.miles[index + 1] - self.miles[index])
                    if best is None or cross < best[0]:
                        best = (cross, mile)
        return best


def douglas_peucker(coords: list[tuple[float, float]], epsilon_miles: float = 0.31) -> list[tuple[float, float]]:
    if len(coords) <= 2:
        return list(coords)
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        lng1, lat1 = coords[start]
        lng2, lat2 = coords[end]
        farthest = -1.0
        farthest_at = None
        for index in range(start + 1, end):
            lng, lat = coords[index]
            distance, _fraction = _project(lat, lng, lat1, lng1, lat2, lng2)
            if distance > farthest:
                farthest = distance
                farthest_at = index
        if farthest_at is not None and farthest > epsilon_miles:
            keep[farthest_at] = True
            stack.append((start, farthest_at))
            stack.append((farthest_at, end))
    return [coord for coord, flagged in zip(coords, keep) if flagged]
