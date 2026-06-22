"""Background loop that keeps the local store in sync with Teamup.

  1. On first run with no token: backfill a date window, then remember `now`.
  2. Each cycle: GET /events?modifiedSince=<token>, upsert changes, advance the
     token to the response's `timestamp`, geocode any new addresses, and nudge
     connected browsers over SSE.

A webhook POST simply wakes this loop early (request_poll), so webhooks are an
optional accelerator on top of reliable polling — not a hard dependency.
"""
import asyncio
import datetime as dt
import time

import httpx

from app import config, geocode, store
from app.events_bus import publish
from app.teamup import TeamupClient

_wake: asyncio.Event | None = None

# run housekeeping (prune dead rows, refresh sub-calendars) every N poll cycles
_MAINT_EVERY = 180  # ~1h at the default 20s POLL_INTERVAL


def request_poll() -> None:
    """Wake the poll loop now (called by the webhook handler)."""
    if _wake is not None:
        _wake.set()


def _safe_err(exc: Exception) -> str:
    """A log-safe one-line summary of an exception WITHOUT the request URL.
    httpx formats its messages as '... for url <URL>', and our Teamup/geocoder
    URLs carry the API key in the query string — so never log the raw exception."""
    req = getattr(exc, "request", None)
    if req is not None:
        code = getattr(getattr(exc, "response", None), "status_code", "")
        return f"{type(exc).__name__} {code} {req.method} {req.url.host}{req.url.path}".strip()
    return f"{type(exc).__name__}: {exc}"


async def _geocode_pending(geo_client: httpx.AsyncClient, budget: int = 20) -> int:
    pending = store.pending_addresses(limit=budget)
    for norm, raw in pending:
        lat, lng, status = await geocode.geocode(raw, geo_client)
        store.save_geocode(norm, lat, lng, status, config.GEOCODER)
    return len(pending)


async def _sync_once(client: TeamupClient, geo_client: httpx.AsyncClient) -> None:
    """One sync pass. With no token (first run OR recovery after a failed
    backfill) it backfills a window; otherwise it pulls changes since the token.
    The token is captured BEFORE the fetch and stored only AFTER success, so:
      - a transient failure leaves token=None and the NEXT pass retries the
        backfill instead of wedging on modifiedSince=None, and
      - edits made mid-backfill aren't skipped by the next poll window."""
    token = store.get_meta("modified_since")
    if not token:
        ts = int(time.time())  # capture before the fetch
        today = dt.date.today()
        start = (today - dt.timedelta(days=config.BACKFILL_DAYS_PAST)).isoformat()
        end = (today + dt.timedelta(days=config.BACKFILL_DAYS_FUTURE)).isoformat()
        evs = await client.events(start, end)
        for e in evs:
            store.upsert_event(e)
        store.set_meta("modified_since", ts)
        print(f"[poller] backfilled {len(evs)} events ({start}..{end})")
    else:
        try:
            evs, new_ts = await client.events_modified_since(token)
        except httpx.HTTPStatusError as exc:
            # Teamup rejects a modifiedSince older than 30 days with HTTP 400
            # ("out_of_bounds_modified_since"). After downtime >30d the stored
            # token is stale and would 400 forever without ever re-backfilling.
            # Clear it so the next pass takes the (token is None) backfill path.
            if exc.response is not None and exc.response.status_code == 400:
                store.set_meta("modified_since", "")
                print("[poller] modifiedSince stale (>30d) — cleared; will re-backfill")
                return
            raise
        for e in evs:
            store.upsert_event(e)
        if new_ts:
            store.set_meta("modified_since", int(new_ts))

    geocoded = await _geocode_pending(geo_client)
    if evs or geocoded:
        publish({"type": "refresh", "changed": len(evs)})


async def run_poller() -> None:
    global _wake
    _wake = asyncio.Event()
    client = TeamupClient()
    geo_client = httpx.AsyncClient(timeout=30)

    try:
        # sub-calendars (names + colors for the legend/filter)
        try:
            store.upsert_subcalendars(await client.subcalendars())
        except Exception as exc:  # noqa: BLE001
            print("[poller] subcalendars error:", _safe_err(exc))

        # initial sync (backfill); a failure here self-heals on the next pass
        try:
            await _sync_once(client, geo_client)
        except Exception as exc:  # noqa: BLE001
            print("[poller] initial sync error (will retry):", _safe_err(exc))
        publish({"type": "refresh"})

        cycles = 0
        while True:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=config.POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            _wake.clear()
            try:
                await _sync_once(client, geo_client)
            except Exception as exc:  # noqa: BLE001
                print("[poller] poll error (will retry):", _safe_err(exc))

            # periodic housekeeping: prune dead/old rows so the table can't grow
            # without bound, and pick up renamed/added sub-calendars
            cycles += 1
            if cycles % _MAINT_EVERY == 0:
                try:
                    removed = store.prune()
                    if removed:
                        print(f"[poller] pruned {removed} stale/deleted events")
                    store.upsert_subcalendars(await client.subcalendars())
                except Exception as exc:  # noqa: BLE001
                    print("[poller] maintenance error (will retry):", _safe_err(exc))
    finally:
        await client.aclose()
        await geo_client.aclose()
