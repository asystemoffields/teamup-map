"""FastAPI app: serves the map UI, the JSON API, an SSE stream, and the webhook."""
import asyncio
import datetime as dt
import json
import secrets
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

from app import config, demo, geocode, poller, routing, store
from app.events_bus import publish, subscribe, unsubscribe

WEB = Path(__file__).resolve().parent.parent / "web"

# one shared HTTP client for the geocode/route endpoints (avoids per-request
# connection churn when several users hit them at once)
_http: "httpx.AsyncClient | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http
    _http = httpx.AsyncClient(timeout=30)
    store.conn()  # initialize schema
    poller_task = None
    if config.DEMO:
        demo.load()
        publish({"type": "refresh"})
        print("[startup] DEMO mode — sample data loaded, poller disabled")
    else:
        if not (config.API_KEY and config.CALENDAR_ID):
            print("[startup] WARNING: TEAMUP_API_KEY / TEAMUP_CALENDAR_ID not set. "
                  "Set them in .env, or run with DEMO=1.")
        # keep the handle so we can cancel it cleanly on shutdown (and so a crash
        # in poller setup isn't a silently-discarded task)
        poller_task = asyncio.create_task(poller.run_poller())
    yield
    if poller_task is not None:
        poller_task.cancel()
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
    """CSP on every response (defense-in-depth backstop for XSS), plus no-cache
    on the page/static assets so a stale app.js/index.html isn't served after an
    update."""
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB / "index.html").read_text()


@app.get("/api/subcalendars")
async def api_subcalendars():
    return {"subcalendars": store.get_subcalendars()}


@app.get("/api/events")
async def api_events(request: Request):
    qp = request.query_params
    subs = qp.get("subcalendars")
    sub_ids = [int(x) for x in subs.split(",") if x] if subs else None
    rows = store.query_events(qp.get("from"), qp.get("to"), sub_ids)
    slim = [{k: r.get(k) for k in _EVENT_FIELDS} for r in rows]
    return {"events": slim, "server_time": dt.datetime.now().isoformat()}


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
