"""FastAPI app: serves the map UI, the JSON API, an SSE stream, and the webhook."""
import asyncio
import datetime as dt
import json
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# fields returned by /api/events kept to what the map renders — keeps PII the UI
# never shows (notes) and internal columns (loc_norm/version/update_dt) off the wire
_EVENT_FIELDS = (
    "id", "subcalendar_id", "subcalendar_ids", "title", "who", "location",
    "start_dt", "end_dt", "all_day", "lat", "lng", "geo_status",
)

from app import config, demo, geocode, poller, routing, store, weather
from app.events_bus import publish, subscribe, unsubscribe

# Static assets live in web/. In a normal checkout that's next to app/; when
# frozen by PyInstaller (the double-click .exe) the bundle is unpacked under
# sys._MEIPASS, so resolve from there if present.
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
WEB = _BASE / "web"

# the configured calendars (DEMO ships two; otherwise from the env). Resolved
# once at import — by now run_app/launch have set DEMO + loaded the config file.
CALS = config.active_calendars()
CAL_KEYS = {c["key"] for c in CALS}
DEFAULT_CAL = CALS[0]["key"] if CALS else "cal1"


def _cal_param(request: Request) -> str:
    """The calendar a request is asking for, validated against what's configured
    (falls back to the first calendar, so old single-calendar clients still work)."""
    c = request.query_params.get("cal")
    return c if c in CAL_KEYS else DEFAULT_CAL


# one shared HTTP client for the geocode/route endpoints (avoids per-request
# connection churn when several users hit them at once)
_http: "httpx.AsyncClient | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http
    _http = httpx.AsyncClient(timeout=30)
    store.conn()  # initialize schema
    poller_tasks = []
    if config.DEMO:
        demo.load()
        publish({"type": "refresh"})
        print(f"[startup] DEMO mode — sample data for {len(CALS)} calendars, pollers disabled")
    else:
        if not CALS:
            print("[startup] WARNING: no calendars configured. Set TEAMUP_CALENDAR_ID "
                  "(and TEAMUP_CALENDAR_ID_2 for a second) in the config, or run with DEMO=1.")
        # one poller per calendar; keep the handles so we can cancel cleanly on
        # shutdown (and so a crash in poller setup isn't a silently-dropped task)
        for c in CALS:
            poller_tasks.append(asyncio.create_task(poller.run_poller(c)))
        if CALS:
            print("[startup] polling: " + ", ".join(f'{c["name"]} ({c["key"]})' for c in CALS))
    yield
    for t in poller_tasks:
        t.cancel()
    if _http is not None:
        await _http.aclose()


app = FastAPI(title="Teamup Dispatch Map", lifespan=lifespan)

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", ""}


@app.middleware("http")
async def _gate_nonloopback(request: Request, call_next):
    """Fail closed for anything that isn't localhost. The dashboard has no login
    by design (it's a per-machine localhost tool), so if it's ever bound to a
    network address it must NOT silently serve customer PII: non-loopback callers
    are refused unless DASHBOARD_TOKEN is set and presented (?token=, the
    x-dispatch-token header, or the dt cookie). Behind a trusted local reverse
    proxy / Cloudflare Access, the proxy is the loopback client and does the auth."""
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK:
        if not config.DASHBOARD_TOKEN:
            return JSONResponse(
                {"error": "This dashboard is localhost-only. Set DASHBOARD_TOKEN "
                          "to allow authenticated network access."}, status_code=403)
        supplied = (request.query_params.get("token")
                    or request.cookies.get("dt")
                    or request.headers.get("x-dispatch-token") or "")
        if not secrets.compare_digest(supplied, config.DASHBOARD_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        resp = await call_next(request)
        if request.query_params.get("token"):  # remember it so deep links work once
            resp.set_cookie("dt", config.DASHBOARD_TOKEN, httponly=True,
                            samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp
    return await call_next(request)


# Content-Security-Policy: locks script execution to our own origin + the pinned
# Leaflet CDN (which is also SRI-checked in index.html), so even a stored-XSS
# string can't run inline. 'unsafe-inline' is needed only for style (the map sets
# inline style attrs for marker colors); img allows OSM tiles + Leaflet assets.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com; "
    "img-src 'self' data: https://*.tile.openstreetmap.org https://unpkg.com; "
    "connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


@app.middleware("http")
async def _security_and_cache_headers(request: Request, call_next):
    """CSP on every response (defense-in-depth backstop for XSS), plus no-store
    on the page/static assets so a browser can NEVER show a stale app.js/index.html
    after the app is updated. (no-cache only forces revalidation; some browsers
    still re-displayed an old in-memory/back-forward page after a new build, which
    looked like a missing feature — no-store removes the copy entirely.)"""
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"           # HTTP/1.0 caches
        resp.headers["Expires"] = "0"                 # proxies / very old browsers
    return resp


def _calendars_payload() -> dict:
    return {"calendars": [{"key": c["key"], "name": c["name"]} for c in CALS],
            "default": DEFAULT_CAL}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    # Bake the calendar list straight into the page so the switcher never depends
    # on a separate /api/calendars fetch succeeding in the browser (that fetch was
    # silently failing on at least one machine, hiding the second calendar). The
    # page loads => the calendars are present. /api/calendars stays as a fallback.
    html = (WEB / "index.html").read_text()
    inject = f"<script>window.__CALENDARS__ = {json.dumps(_calendars_payload())};</script>\n"
    return html.replace("</head>", inject + "</head>", 1)


@app.get("/api/calendars")
async def api_calendars():
    """The configured calendars for the UI's calendar switcher (fallback; the
    page also embeds this via window.__CALENDARS__)."""
    return _calendars_payload()


@app.get("/api/subcalendars")
async def api_subcalendars(request: Request):
    return {"subcalendars": store.get_subcalendars(_cal_param(request))}


@app.get("/api/events")
async def api_events(request: Request):
    qp = request.query_params
    subs = qp.get("subcalendars")
    sub_ids = [int(x) for x in subs.split(",") if x] if subs else None
    rows = store.query_events(_cal_param(request), qp.get("from"), qp.get("to"), sub_ids)
    slim = [{k: r.get(k) for k in _EVENT_FIELDS} for r in rows]
    return {"events": slim, "server_time": dt.datetime.now().isoformat()}


@app.get("/api/weather")
async def api_weather(request: Request):
    """Per-job weather risk for the same window/calendar the map is showing,
    keyed by event id. Best-effort and cached server-side, so the map renders
    instantly and the browser overlays badges when this returns."""
    if not config.WEATHER:
        return {"enabled": False, "weather": {}}
    qp = request.query_params
    subs = qp.get("subcalendars")
    sub_ids = [int(x) for x in subs.split(",") if x] if subs else None
    rows = store.query_events(_cal_param(request), qp.get("from"), qp.get("to"), sub_ids)
    assessments = await weather.assess_events(rows, _http)
    return {"enabled": True, "weather": assessments}


@app.get("/api/geocode")
async def api_geocode(request: Request):
    """Geocode a prospective address (cache-aware). Used by the 'add a
    prospective job' panel; results are cached like any other address."""
    address = (request.query_params.get("address") or "").strip()
    if not address:
        return {"status": "empty", "lat": None, "lng": None}
    norm = store.norm_addr(address)
    cached = store.get_cached_geocode(norm)
    if cached and cached["status"] == "ok":
        return {"status": "ok", "lat": cached["lat"], "lng": cached["lng"], "source": "cache"}
    lat, lng, status = await geocode.geocode(address, _http)
    store.save_geocode(norm, lat, lng, status, config.GEOCODER)
    return {"status": status, "lat": lat, "lng": lng, "source": config.GEOCODER}


@app.post("/api/route")
async def api_route(request: Request):
    """Connect an ordered list of [lat,lng] stops into a line + distance/duration
    (real driving via OSRM, straight-line haversine fallback)."""
    body = await request.json()
    points = body.get("points", [])
    return await routing.route_through(points, _http)


@app.post("/webhook")
async def webhook(request: Request):
    """Teamup change notifications land here and wake the poll loop early.
    (Polling already catches everything; this just makes it feel instant.)
    Gated by WEBHOOK_SECRET if set, so a stray caller can't spam the poll loop
    into burning the Teamup quota — register the URL as .../webhook?t=<secret>."""
    if config.WEBHOOK_SECRET and not secrets.compare_digest(
            request.query_params.get("t") or "", config.WEBHOOK_SECRET):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    poller.request_poll()
    return {"ok": True}


@app.get("/api/stream")
async def stream(request: Request):
    q = subscribe()

    async def gen():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
