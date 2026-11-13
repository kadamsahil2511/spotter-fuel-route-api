"""Name overlap used to accept a Photon hit as a specific truck stop."""

from __future__ import annotations

GENERIC = {
    "THE", "AND", "OF", "A", "AT", "TRAVEL", "CENTER", "CENTRE", "CENTERS",
    "STOP", "STOPS", "TRUCK", "TRUCKSTOP", "PLAZA", "FOOD", "FUEL", "SHOP",
    "STORE", "STORES", "INC", "LLC", "CO", "COMPANY", "DBA", "NUMBER",
}


def tokens(name: str) -> set[str]:
    cleaned = []
    for piece in (name or "").upper().replace("#", " ").replace("&", " ").replace("/", " ").replace("-", " ").split():
        piece = "".join(ch for ch in piece if ch.isalnum())
        if len(piece) >= 3 and piece not in GENERIC and not piece.isdigit():
            cleaned.append(piece)
    return set(cleaned)


def name_matches(station_name: str, result_name: str) -> bool:
    """True when the POI and the OPIS row share a distinctive word.

    A fuel amenity in the same city is not enough: several stops can share a
    town, and the nearest fuel POI may be a different brand.
    """
    return bool(tokens(station_name) & tokens(result_name))
