"""US state names and a coarse point-in-country check."""

from __future__ import annotations

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}
US_STATES = set(STATE_NAMES.values())
CANADA = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT", "NL", "PE", "NT", "NU"}
CANADA_NAMES = {
    "ALBERTA": "AB", "BRITISH COLUMBIA": "BC", "MANITOBA": "MB",
    "NEW BRUNSWICK": "NB", "NOVA SCOTIA": "NS", "ONTARIO": "ON",
    "QUEBEC": "QC", "SASKATCHEWAN": "SK", "YUKON": "YT",
}


def normalize_region(token: str) -> str | None:
    cleaned = " ".join(token.upper().split()).rstrip(".")
    if cleaned in US_STATES or cleaned in CANADA:
        return cleaned
    if cleaned in STATE_NAMES:
        return STATE_NAMES[cleaned]
    if cleaned in CANADA_NAMES:
        return CANADA_NAMES[cleaned]
    return None


def in_united_states(lat: float, lng: float) -> bool:
    if 24.5 <= lat <= 49.5 and -125.0 <= lng <= -66.5:
        return True
    if 51.0 <= lat <= 72.0 and -170.0 <= lng <= -129.0:
        return True
    if 18.5 <= lat <= 22.5 and -160.5 <= lng <= -154.5:
        return True
    return False
