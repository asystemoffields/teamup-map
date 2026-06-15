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


def request_poll() -> None:
    """Wake the poll loop now (called by the webhook handler)."""
    if _wake is not None:
        _wake.set()


async def _geocode_pending(geo_client: httpx.AsyncClient, budget: int = 20) -> int:
    pending = store.pending_addresses(limit=budget)
    for norm, raw in pending:
        lat, lng, status = await geocode.geocode(raw, geo_client)
        store.save_geocode(norm, lat, lng, status, config.GEOCODER)
    return len(pending)


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
            print("[poller] subcalendars error:", exc)

        # backfill on first run
        if not store.get_meta("modified_since"):
            today = dt.date.today()
            start = (today - dt.timedelta(days=config.BACKFILL_DAYS_PAST)).isoformat()
            end = (today + dt.timedelta(days=config.BACKFILL_DAYS_FUTURE)).isoformat()
            try:
                evs = await client.events(start, end)
                for e in evs:
                    store.upsert_event(e)
                store.set_meta("modified_since", int(time.time()))
                print(f"[poller] backfilled {len(evs)} events ({start}..{end})")
            except Exception as exc:  # noqa: BLE001
                print("[poller] backfill error:", exc)

        await _geocode_pending(geo_client)
        publish({"type": "refresh"})

        # main loop
        while True:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=config.POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            _wake.clear()

            token = store.get_meta("modified_since")
            try:
                evs, new_ts = await client.events_modified_since(token)
                for e in evs:
                    store.upsert_event(e)
                if new_ts:
                    store.set_meta("modified_since", int(new_ts))
                geocoded = await _geocode_pending(geo_client)
                if evs or geocoded:
                    publish({"type": "refresh", "changed": len(evs)})
                    print(f"[poller] {len(evs)} changed, {geocoded} geocoded")
            except Exception as exc:  # noqa: BLE001
                print("[poller] poll error:", exc)
    finally:
        await client.aclose()
        await geo_client.aclose()
