"""FastAPI app: serves the map UI, the JSON API, an SSE stream, and the webhook."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config, demo, geocode, poller, routing, store
from app.events_bus import publish, subscribe, unsubscribe

app = FastAPI(title="Teamup Dispatch Map")
WEB = Path(__file__).resolve().parent.parent / "web"


@app.on_event("startup")
async def _startup() -> None:
    store.conn()  # initialize schema
    if config.DEMO:
        demo.load()
        publish({"type": "refresh"})
        print("[startup] DEMO mode — sample data loaded, poller disabled")
    else:
        if not (config.API_KEY and config.CALENDAR_ID):
            print("[startup] WARNING: TEAMUP_API_KEY / TEAMUP_CALENDAR_ID not set. "
                  "Set them in .env, or run with DEMO=1.")
        asyncio.create_task(poller.run_poller())


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
    return {"events": rows, "server_time": dt.datetime.now().isoformat()}


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
    async with httpx.AsyncClient(timeout=30) as client:
        lat, lng, status = await geocode.geocode(address, client)
    store.save_geocode(norm, lat, lng, status, config.GEOCODER)
    return {"status": status, "lat": lat, "lng": lng, "source": config.GEOCODER}


@app.post("/api/route")
async def api_route(request: Request):
    """Connect an ordered list of [lat,lng] stops into a line + distance/duration
    (real driving via OSRM, straight-line haversine fallback)."""
    body = await request.json()
    points = body.get("points", [])
    async with httpx.AsyncClient(timeout=30) as client:
        return await routing.route_through(points, client)


@app.post("/webhook")
async def webhook(request: Request):
    """Teamup change notifications land here and wake the poll loop early.
    (Polling already catches everything; this just makes it feel instant.)"""
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
