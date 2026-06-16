"""Routing: connect an ordered list of stops into a line + distance/duration.

Default backend is OSRM (the OpenStreetMap project's router, same free ecosystem
as our Nominatim geocoder) for real driving routes. Falls back to straight-line
haversine automatically if OSRM is unreachable or errors, so the map always draws
something. Configurable via ROUTING + OSRM_URL (point OSRM_URL at a self-hosted
instance or a paid provider later without touching the frontend).
"""
import math

from app import config


def haversine_m(a, b) -> float:
    """Great-circle distance in metres between (lat, lng) tuples."""
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _straight(pts):
    dist = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    return {
        "source": "haversine",
        "distance_m": dist,
        "duration_s": None,
        "geometry": [[la, ln] for la, ln in pts],
    }


# Routes are deterministic for a given (backend, ordered stops), so cache them.
# This dedupes identical routes across concurrent users and across re-renders,
# which keeps load off the shared OSRM server. Bounded, FIFO-evicted.
_route_cache: dict = {}
_ROUTE_CACHE_MAX = 256


def _cache_key(pts):
    return (config.ROUTING, tuple((round(la, 5), round(ln, 5)) for la, ln in pts))


def _cache_put(key, result):
    _route_cache[key] = result
    if len(_route_cache) > _ROUTE_CACHE_MAX:
        _route_cache.pop(next(iter(_route_cache)))


async def route_through(points, client):
    """points: ordered [[lat, lng], ...]. Returns {source, distance_m,
    duration_s, geometry:[[lat,lng],...]}. geometry follows roads when OSRM is
    used, else is the straight segments between stops."""
    pts = [(float(la), float(ln)) for la, ln in points if la is not None and ln is not None]
    if len(pts) < 2:
        return {"source": "none", "distance_m": 0.0, "duration_s": None,
                "geometry": [[la, ln] for la, ln in pts]}

    key = _cache_key(pts)
    if key in _route_cache:
        return _route_cache[key]

    if config.ROUTING == "osrm":
        try:
            coords = ";".join(f"{ln},{la}" for la, ln in pts)
            url = f"{config.OSRM_URL.rstrip('/')}/route/v1/driving/{coords}"
            r = await client.get(url, params={"overview": "full", "geometries": "geojson"})
            r.raise_for_status()
            data = r.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                geom = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]  # lon,lat -> lat,lng
                result = {
                    "source": "osrm",
                    "distance_m": route.get("distance"),
                    "duration_s": route.get("duration"),
                    "geometry": geom,
                }
                _cache_put(key, result)  # cache real OSRM results only
                return result
            print("[routing] OSRM returned", data.get("code"), "- falling back to haversine")
        except Exception as exc:  # noqa: BLE001
            print("[routing] OSRM error, falling back to haversine:", exc)
        # OSRM unavailable: return haversine but DON'T cache, so it retries OSRM next time
        return _straight(pts)

    # ROUTING=haversine: deterministic, safe to cache
    result = _straight(pts)
    _cache_put(key, result)
    return result
