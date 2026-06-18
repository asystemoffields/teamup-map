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
        evs, new_ts = await client.events_modified_since(token)
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
            print("[poller] subcalendars error:", exc)

        # initial sync (backfill); a failure here self-heals on the next pass
        try:
            await _sync_once(client, geo_client)
        except Exception as exc:  # noqa: BLE001
            print("[poller] initial sync error (will retry):", exc)
        publish({"type": "refresh"})

        while True:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=config.POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            _wake.clear()
            try:
                await _sync_once(client, geo_client)
            except Exception as exc:  # noqa: BLE001
                print("[poller] poll error (will retry):", exc)
    finally:
        await client.aclose()
        await geo_client.aclose()
