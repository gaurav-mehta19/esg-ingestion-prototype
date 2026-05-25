"""
Great-circle distance between IATA airport codes.

Hardcoded mini-database covers only the airports that appear in the
prototype sample. Production should load a full IATA dataset (OurAirports
CSV is the standard source; ~70k rows, free). The function shape stays
the same — only `_AIRPORTS` would expand.

Why we do this at normalization time:
  Per research-notes §3.7, Concur and Navan only give you airport codes,
  not distances. The distance is a derived field on the activity_record
  (`computed_distance_km`), produced once at normalization and stored.
  Re-computing on every report query would be wasted work; storing it
  with the record provides an audit anchor ("which distance value was
  used for the emission calculation").
"""

from __future__ import annotations

import math


# (lat, lon) in degrees. Source: OurAirports public dataset.
_AIRPORTS: dict[str, tuple[float, float]] = {
    "JFK": (40.6413, -73.7781),
    "EWR": (40.6925, -74.1687),
    "LGA": (40.7769, -73.8740),
    "LHR": (51.4700, -0.4543),
    "LGW": (51.1537, -0.1821),
    "CDG": (49.0097, 2.5479),
    "FRA": (50.0379, 8.5622),
    "MUC": (48.3537, 11.7861),
    "AMS": (52.3105, 4.7683),
    "SFO": (37.6213, -122.3790),
    "LAX": (33.9425, -118.4081),
    "ORD": (41.9742, -87.9073),
    "ATL": (33.6407, -84.4277),
    "DXB": (25.2532, 55.3657),
    "SIN": (1.3644, 103.9915),
    "NRT": (35.7720, 140.3929),
    "HND": (35.5494, 139.7798),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, km. Standard haversine."""
    r = 6371.0  # earth radius km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


# DEFRA uplift factor for great-circle → operating distance (research-notes §3.7).
# Real flights don't fly straight lines — routing, holding, weather all add ~8%.
DEFRA_DISTANCE_UPLIFT = 1.08


def compute_flight_distance_km(origin_iata: str, destination_iata: str) -> tuple[float | None, str]:
    """
    Returns (distance_km, method_label) or (None, reason) if unresolvable.

    method_label is stored on the activity_record so an auditor can see
    how the distance was derived.
    """
    origin = (origin_iata or "").upper()
    dest = (destination_iata or "").upper()
    if origin not in _AIRPORTS:
        return None, f"airport code '{origin}' not in IATA database"
    if dest not in _AIRPORTS:
        return None, f"airport code '{dest}' not in IATA database"
    lat1, lon1 = _AIRPORTS[origin]
    lat2, lon2 = _AIRPORTS[dest]
    great_circle = haversine_km(lat1, lon1, lat2, lon2)
    operating = great_circle * DEFRA_DISTANCE_UPLIFT
    return operating, f"great_circle×{DEFRA_DISTANCE_UPLIFT}_uplift"
