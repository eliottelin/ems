"""Open-Meteo/GeoNames place search, independent of the Streamlit UI."""
from math import isfinite
import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def search_places(query: str, count: int = 10) -> list[dict]:
    query = query.strip()
    if len(query) < 2:
        raise ValueError("Enter at least two characters for a place name or postal code")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError("Geocoding result count must be between 1 and 100")
    response = requests.get(GEOCODING_URL, params={"name": query, "count": count,
                            "language": "en", "format": "json"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise ValueError("Open-Meteo geocoding: " + payload.get("reason", "request failed"))
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("Open-Meteo geocoding returned an invalid results list")
    places, seen = [], set()
    for item in results:
        if not {"id", "name", "latitude", "longitude"}.issubset(item):
            raise ValueError("Open-Meteo geocoding returned a place without required fields")
        lat, lon = float(item["latitude"]), float(item["longitude"])
        if not isfinite(lat) or not -90 <= lat <= 90 or not isfinite(lon) or not -180 <= lon <= 180:
            raise ValueError("Open-Meteo geocoding returned invalid coordinates")
        if item["id"] not in seen:
            places.append({**item, "latitude": lat, "longitude": lon})
            seen.add(item["id"])
    return places


def place_label(place: dict) -> str:
    names = [str(place.get(key, "")).strip() for key in ("name", "admin1", "country")]
    names = list(dict.fromkeys(name for name in names if name))
    return ", ".join(names) + f" ({place['latitude']:.4f}, {place['longitude']:.4f})"
