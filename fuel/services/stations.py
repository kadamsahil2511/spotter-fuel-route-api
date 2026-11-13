"""Load the committed station file once per process."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from django.conf import settings

_lock = threading.Lock()
_stations: list[dict] | None = None
_mtime: float | None = None
_override = False


def station_path() -> Path:
    return Path(settings.STATIONS_PATH)


def load_stations(path: Path | None = None) -> list[dict]:
    source = path or station_path()
    if not source.exists():
        return []
    payload = json.loads(source.read_text())
    rows = payload["stations"] if isinstance(payload, dict) else payload
    usable = []
    for row in rows:
        if row.get("lat") is None or row.get("lng") is None:
            continue
        if row.get("geocode_quality") == "unresolved":
            continue
        usable.append(row)
    return usable


def get_stations() -> list[dict]:
    global _stations, _mtime
    if _override:
        return _stations or []
    path = station_path()
    mtime = path.stat().st_mtime if path.exists() else 0
    if _stations is not None and mtime == _mtime:
        return _stations
    with _lock:
        mtime = path.stat().st_mtime if path.exists() else 0
        if _stations is None or mtime != _mtime:
            _stations = load_stations(path)
            _mtime = mtime
    return _stations or []


def set_stations(stations: list[dict] | None) -> None:
    """Test hook. None reloads from disk on the next read."""
    global _stations, _override, _mtime
    with _lock:
        _override = stations is not None
        _stations = stations
        _mtime = None
