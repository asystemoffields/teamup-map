"""Pluggable geocoder: turns an address string into (lat, lng, status).

Providers: nominatim (default, free, OSM), google, mapbox.
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


async def geocode(address: str, client: httpx.AsyncClient):
    provider = config.GEOCODER
    try:
        if provider == "google":
            return await _google(address, client)
        if provider == "mapbox":
            return await _mapbox(address, client)
        return await _nominatim(address, client)
    except Exception as exc:  # noqa: BLE001 - any failure -> mark error, retry later
        print(f"[geocode] {provider} error for {address!r}: {exc}")
        return (None, None, "error")


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
