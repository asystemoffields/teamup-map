"""Per-job weather risk from the US National Weather Service (api.weather.gov).

Free, no API key, US-only — a perfect fit for a Northern-Michigan dispatch board.
It rides on the geocode pipeline: every job already has a lat/lng, so for each
upcoming job we pull (a) the official NWS alerts active at that point
(watch/warning/advisory) and (b) the point forecast for the job's time window,
then score a severity:

    red    = any official NWS alert overlapping the job window
    yellow = a forecast "work-stopper" in the window (rain >= POP, any snow,
             wind >= sustained/gust thresholds; optionally cold/heat)
    green  = nothing notable (the forecast is still shown in the popup)

NWS asks every caller to send a descriptive User-Agent with a contact
(WEATHER_CONTACT / NOMINATIM_EMAIL). Three small caches keep us polite and fast:
the point->grid mapping is effectively permanent, the forecast ~1h, alerts ~10m.
Everything is best-effort: any failure just means a job has no badge, never a
broken map.
"""
import asyncio
import datetime as dt
import re

import httpx

from app import config, store

_API = "https://api.weather.gov"

# cache TTLs (seconds). The grid mapping for a coordinate never really changes;
# the 12h forecast refreshes a few times a day; alerts are short-lived.
_TTL_GRID = 30 * 24 * 3600
_TTL_FORECAST = 3600
_TTL_ALERTS = 600

_NUM_RE = re.compile(r"\d+")
_GUST_RE = re.compile(r"gust[s]?[^.\d]*?(\d+)\s*mph", re.I)

_SNOW_WORDS = ("snow", "sleet", "flurr", "wintry", "blizzard", "freezing", "ice pellet")
_RAIN_WORDS = ("rain", "shower", "drizzle", "thunder", "storm")


def _headers() -> dict:
    contact = config.WEATHER_CONTACT or "no-contact-set"
    return {"User-Agent": f"teamup-dispatch/1.0 ({contact})",
            "Accept": "application/geo+json"}


def _parse_dt(s):
    """ISO string -> aware UTC datetime. Teamup/NWS send offsets; demo data is
    naive (interpreted as local). Returns None on anything unparseable."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return d.astimezone(dt.timezone.utc)


def _max_speed(text: str) -> int:
    """Largest mph integer in a windSpeed like '10 to 20 mph' (-> 20)."""
    nums = [int(n) for n in _NUM_RE.findall(text or "")]
    return max(nums) if nums else 0


def _max_gust(text: str) -> int:
    """Largest gust mph mentioned in a detailedForecast ('gusts as high as 45 mph')."""
    return max((int(m) for m in _GUST_RE.findall(text or "")), default=0)


async def _grid(lat: float, lng: float, client: httpx.AsyncClient):
    """Resolve a coordinate to its NWS forecast gridpoint (cached ~permanently).
    Returns {office, gridX, gridY, forecast_url} or None."""
    key = f"wx:point:{round(lat, 4)},{round(lng, 4)}"
    cached = store.get_weather_cache(key)
    if cached:
        return cached
    try:
        r = await client.get(f"{_API}/points/{round(lat, 4)},{round(lng, 4)}",
                             headers=_headers(), timeout=15)
        r.raise_for_status()
        p = r.json()["properties"]
        grid = {"office": p["gridId"], "gridX": p["gridX"], "gridY": p["gridY"],
                "forecast_url": p["forecast"]}
        store.set_weather_cache(key, grid, _TTL_GRID)
        return grid
    except Exception as exc:  # noqa: BLE001 - any failure -> no forecast, retried next pass
        print(f"[weather] points lookup failed ({type(exc).__name__})")
        return None


async def _forecast(grid, client: httpx.AsyncClient):
    """The 12-hour (day/night) forecast periods for a grid (cached ~1h)."""
    if not grid:
        return []
    key = f"wx:fc:{grid['office']}/{grid['gridX']},{grid['gridY']}"
    cached = store.get_weather_cache(key)
    if cached is not None:
        return cached
    try:
        r = await client.get(grid["forecast_url"], headers=_headers(), timeout=15)
        r.raise_for_status()
        periods = []
        for p in r.json()["properties"]["periods"]:
            pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
            periods.append({
                "start": p.get("startTime"), "end": p.get("endTime"),
                "temp": p.get("temperature"), "unit": p.get("temperatureUnit") or "",
                "wind": p.get("windSpeed") or "", "windDir": p.get("windDirection") or "",
                "short": p.get("shortForecast") or "",
                "detailed": p.get("detailedForecast") or "",
                "pop": pop, "isDaytime": p.get("isDaytime"),
            })
        store.set_weather_cache(key, periods, _TTL_FORECAST)
        return periods
    except Exception as exc:  # noqa: BLE001
        print(f"[weather] forecast failed ({type(exc).__name__})")
        return []


async def _alerts(lat: float, lng: float, client: httpx.AsyncClient):
    """Active NWS alerts at a point (cached ~10m; coarse key so neighbors share)."""
    key = f"wx:al:{round(lat, 3)},{round(lng, 3)}"
    cached = store.get_weather_cache(key)
    if cached is not None:
        return cached
    try:
        r = await client.get(f"{_API}/alerts/active",
                             params={"point": f"{round(lat, 4)},{round(lng, 4)}"},
                             headers=_headers(), timeout=15)
        r.raise_for_status()
        alerts = []
        for f in r.json().get("features", []):
            pr = f.get("properties", {})
            if pr.get("status") not in (None, "Actual"):
                continue  # drop Exercise/System/Test/Draft
            alerts.append({
                "event": pr.get("event") or "Weather alert",
                "severity": pr.get("severity") or "",
                "headline": pr.get("headline") or "",
                "onset": pr.get("onset") or pr.get("effective"),
                "ends": pr.get("ends") or pr.get("expires"),
            })
        store.set_weather_cache(key, alerts, _TTL_ALERTS)
        return alerts
    except Exception as exc:  # noqa: BLE001
        print(f"[weather] alerts failed ({type(exc).__name__})")
        return []


def _glyph(cats, alerts) -> str:
    """A single representative emoji, snow > wind > rain > generic."""
    ev = " ".join((a.get("event") or "").lower() for a in alerts)
    if "snow" in cats or any(w in ev for w in ("snow", "winter", "ice", "blizzard")):
        return "❄"   # snowflake
    if "wind" in cats or "wind" in ev:
        return "\U0001f4a8"  # dash / wind
    if "rain" in cats or any(w in ev for w in ("rain", "flood", "thunder", "storm")):
        return "\U0001f327"  # rain cloud
    return "⚠"  # warning


def _assess(start, end, periods, alerts):
    """Score one job's window against its forecast periods + active alerts.
    Returns None when we have no data at all for the point."""
    # alerts whose active span overlaps the job window
    hit_alerts = []
    for a in alerts:
        onset, ends = _parse_dt(a.get("onset")), _parse_dt(a.get("ends"))
        if ends is not None and ends < start:
            continue
        if onset is not None and onset > end:
            continue
        hit_alerts.append(a)

    # forecast periods overlapping the window; fall back to the next upcoming one
    match = [p for p in periods
             if (ps := _parse_dt(p.get("start"))) and (pe := _parse_dt(p.get("end")))
             and ps < end and pe > start]
    if not match and periods:
        future = [p for p in periods if (_parse_dt(p.get("end")) or start) > start]
        if future:
            match = [future[0]]

    if not match and not hit_alerts:
        return None

    headline_p = next((p for p in match if p.get("isDaytime")), match[0]) if match else None
    pops = [p["pop"] for p in match if p.get("pop") is not None]
    max_pop = max(pops) if pops else None
    temps = [p["temp"] for p in match if isinstance(p.get("temp"), (int, float))]
    blob = " ".join((p.get("short", "") + " " + p.get("detailed", "")) for p in match).lower()
    max_sustained = max((_max_speed(p.get("wind", "")) for p in match), default=0)
    max_gust = max((_max_gust(p.get("detailed", "")) for p in match), default=0)

    snow = any(w in blob for w in _SNOW_WORDS)
    rain = any(w in blob for w in _RAIN_WORDS)

    reasons, cats = [], []
    if snow:
        reasons.append("Snow" + (f" ({max_pop}%)" if max_pop else ""))
        cats.append("snow")
    if max_pop is not None and max_pop >= config.WEATHER_RAIN_POP and not snow:
        reasons.append(f"{'Rain' if rain else 'Precip'} {max_pop}%")
        cats.append("rain")
    if max_sustained >= config.WEATHER_WIND_MPH or max_gust >= config.WEATHER_GUST_MPH:
        reasons.append(f"Wind to {max(max_sustained, max_gust)} mph")
        cats.append("wind")
    if config.WEATHER_COLD_F is not None and temps and min(temps) <= config.WEATHER_COLD_F:
        reasons.append(f"Cold {min(temps)}°")
        cats.append("cold")
    if config.WEATHER_HEAT_F is not None and temps and max(temps) >= config.WEATHER_HEAT_F:
        reasons.append(f"Heat {max(temps)}°")
        cats.append("heat")

    if hit_alerts:
        severity = "red"
    elif reasons:
        severity = "yellow"
    else:
        severity = "green"

    alert_names = [a["event"] for a in hit_alerts]
    if alert_names:
        summary = alert_names[0] + (f" +{len(alert_names) - 1}" if len(alert_names) > 1 else "")
    elif reasons:
        summary = " · ".join(reasons)
    else:
        summary = (headline_p.get("short") if headline_p else "") or "Clear"

    return {
        "severity": severity,
        "glyph": _glyph(cats, hit_alerts) if severity != "green" else "",
        "summary": summary,
        "reasons": alert_names + reasons,
        "detail": headline_p.get("detailed") if headline_p else "",
        "short": headline_p.get("short") if headline_p else "",
        "temp": headline_p.get("temp") if headline_p else None,
        "unit": headline_p.get("unit") if headline_p else "",
        "wind": headline_p.get("wind") if headline_p else "",
        "pop": max_pop,
        "alerts": [{"event": a["event"], "headline": a["headline"], "ends": a["ends"]}
                   for a in hit_alerts],
    }


async def assess_events(events, client: httpx.AsyncClient) -> dict:
    """Map event id -> weather assessment for every geocoded job starting within
    the forecast horizon. Best-effort: points/forecasts/alerts are fetched with a
    small concurrency cap and cached; a failure for one point just omits it."""
    if not config.WEATHER:
        return {}
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=config.WEATHER_HORIZON_DAYS)

    todo = []
    for e in events:
        if e.get("lat") is None or e.get("lng") is None:
            continue
        s = _parse_dt(e.get("start_dt"))
        if s is None:
            continue
        en = _parse_dt(e.get("end_dt")) or (s + dt.timedelta(hours=3))
        if en < now or s > horizon:   # already over, or beyond forecast range
            continue
        todo.append((e, s, en))
    if not todo:
        return {}

    # distinct points (soonest jobs first so the cap, if hit, keeps what matters)
    todo.sort(key=lambda t: t[1])
    pts, capped = {}, 0
    for e, s, en in todo:
        k = (round(e["lat"], 4), round(e["lng"], 4))
        if k not in pts:
            if len(pts) >= config.WEATHER_MAX_POINTS:
                capped += 1
                continue
            pts[k] = (e["lat"], e["lng"])
    if capped:
        print(f"[weather] {capped} job(s) past the {config.WEATHER_MAX_POINTS}-location "
              "cap were not assessed this pass")

    sem = asyncio.Semaphore(5)

    async def _limited(coro):
        async with sem:
            return await coro

    grid_by_pt, alerts_by_pt = {}, {}

    async def load_point(k, latlng):
        lat, lng = latlng
        g, al = await asyncio.gather(_grid(lat, lng, client), _alerts(lat, lng, client))
        grid_by_pt[k] = g
        alerts_by_pt[k] = al

    await asyncio.gather(*(_limited(load_point(k, v)) for k, v in pts.items()))

    # one forecast fetch per distinct grid (many addresses share a town's grid)
    grids = {}
    for g in grid_by_pt.values():
        if g:
            grids[(g["office"], g["gridX"], g["gridY"])] = g
    fc_by_grid = {}

    async def load_fc(gk, g):
        fc_by_grid[gk] = await _forecast(g, client)

    await asyncio.gather(*(_limited(load_fc(gk, g)) for gk, g in grids.items()))

    out = {}
    for e, s, en in todo:
        k = (round(e["lat"], 4), round(e["lng"], 4))
        if k not in grid_by_pt:   # capped this pass
            continue
        g = grid_by_pt.get(k)
        periods = fc_by_grid.get((g["office"], g["gridX"], g["gridY"]), []) if g else []
        a = _assess(s, en, periods, alerts_by_pt.get(k) or [])
        if a:
            out[e["id"]] = a
    return out
