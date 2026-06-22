"""Pluggable geocoder: turns an address string into (lat, lng, status).

Providers: nominatim (free, OSM), census (free, US-only, no key), google, mapbox.
`GEOCODER` may be a comma-separated FALLBACK CHAIN tried left-to-right until one
returns a hit, e.g. `census,nominatim` (best for US addresses: the US Census
geocoder has far better rural-residential coverage than OSM, with OSM as backup).
Nominatim is rate-limited to <=1 req/s per OSM usage policy; results are
cached by the store so we only ever call the provider once per address.
"""
import asyncio
import time
import urllib.parse

import httpx

from app import config

_rl_lock = asyncio.Lock()
_last_call = 0.0


async def _rate_limit(min_interval: float) -> None:
    global _last_call
    async with _rl_lock:
        wait = min_interval - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


_PROVIDERS = {
    "google": lambda a, c: _google(a, c),
    "mapbox": lambda a, c: _mapbox(a, c),
    "census": lambda a, c: _census(a, c),
    "nominatim": lambda a, c: _nominatim(a, c),
}


async def geocode(address: str, client: httpx.AsyncClient):
    """Try each provider in the GEOCODER chain until one returns a hit.
    Returns 'error' (transient, retried later) if every provider failed and at
    least one failed transiently; 'notfound' (terminal) only if all said notfound."""
    chain = [p.strip() for p in config.GEOCODER.split(",") if p.strip()] or ["nominatim"]
    saw_error = False
    for provider in chain:
        fn = _PROVIDERS.get(provider, _PROVIDERS["nominatim"])
        try:
            lat, lng, status = await fn(address, client)
        except Exception as exc:  # noqa: BLE001 - any failure -> try next, retry later
            print(f"[geocode] {provider} error for {address!r}: {exc}")
            saw_error = True
            continue
        if status == "ok":
            return (lat, lng, "ok")
        if status == "error":
            saw_error = True
    return (None, None, "error" if saw_error else "notfound")


async def _nominatim(address, client):
    await _rate_limit(1.1)
    ua = f"teamup-dispatch/1.0 ({config.NOMINATIM_EMAIL or 'no-contact-set'})"
    r = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": ua},
    )
    r.raise_for_status()
    arr = r.json()
    if not arr:
        return (None, None, "notfound")
    return (float(arr[0]["lat"]), float(arr[0]["lon"]), "ok")


async def _google(address, client):
    r = await client.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": config.GEOCODER_API_KEY},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        return (None, None, "notfound")
    loc = data["results"][0]["geometry"]["location"]
    return (loc["lat"], loc["lng"], "ok")


async def _mapbox(address, client):
    q = urllib.parse.quote(address)
    r = await client.get(
        f"https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json",
        params={"access_token": config.GEOCODER_API_KEY, "limit": 1},
    )
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return (None, None, "notfound")
    lng, lat = feats[0]["center"]
    return (lat, lng, "ok")


async def _census(address, client):
    """US Census Bureau geocoder: free, no key, US-only, strong on rural
    residential addresses. Returns coordinates as {x: lng, y: lat}.

    The endpoint intermittently returns 200 + an EMPTY match list under bursty
    load (not a real 'no such address'), so we retry empties/transient errors a
    few times before concluding. A true miss after all attempts -> 'notfound';
    a transient HTTP failure is re-raised so geocode() marks it retryable."""
    last_exc = None
    for attempt in range(4):
        await _rate_limit(0.5)
        try:
            r = await client.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            )
            r.raise_for_status()
            matches = r.json().get("result", {}).get("addressMatches", [])
        except Exception as exc:  # noqa: BLE001 - transient; back off and retry
            last_exc = exc
            await asyncio.sleep(0.8 * (attempt + 1))
            continue
        if matches:
            coord = matches[0]["coordinates"]  # x = longitude, y = latitude
            return (coord["y"], coord["x"], "ok")
        await asyncio.sleep(0.8 * (attempt + 1))  # false-empty? give it another go
    if last_exc is not None:
        raise last_exc  # all attempts errored -> let geocode() mark 'error' (retried later)
    return (None, None, "notfound")  # consistently empty -> genuine miss
