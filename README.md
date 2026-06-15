# Teamup Dispatch Map

A live aggregate map for a [Teamup](https://www.teamup.com/) calendar: every event
plotted as a pin, colored by sub-calendar, filterable by crew and time window,
updating in near-real-time as the calendar changes. Built for the "dispatcher
sees every job at once" use case Teamup has no native view for.

## The load-bearing fact

Teamup's API returns an event's location as a **plain text string** — there are
**no coordinates** in the event object (confirmed against the API event model and
the pyTeamUp wrapper: `location string`, nothing else). So this app geocodes the
address itself and **caches** the result. The geocode cache is core
infrastructure, not an optimization: each distinct address is geocoded once and
reused for every event that shares it, which keeps us within free-tier rate
limits.

## How it works

```
Teamup  --modifiedSince poll-->  upsert  -->  geocode (cached)  -->  SQLite
  (+ optional webhook = instant)                                       |
                                                          SSE "refresh" |
                                                                        v
                                                   Leaflet map in the browser
```

- **Sync** (`app/poller.py`): backfills a date window once, then polls
  `GET /events?modifiedSince=<ts>` every `POLL_INTERVAL` seconds, advancing the
  token from each response's `timestamp`. Reliable with no public URL.
- **Webhook** (`POST /webhook`): optional. Teamup change notifications just wake
  the poll loop early so updates feel instant. Polling already catches everything.
- **Geocoder** (`app/geocode.py`): pluggable — `nominatim` (free, default),
  `google`, or `mapbox`. Results cached in SQLite.
- **UI** (`web/`): Leaflet + OpenStreetMap tiles (no token). Big, shadowed pins
  in each sub-calendar's **real Teamup color**, each tagged with a **pill showing
  the customer name + appointment time** (accent dot pulses in the calendar color).
  Filter by sub-calendar + time window; click a pin or list row to zoom; an
  "Unmapped" tray lists events with no address or a failed geocode; live refresh
  via Server-Sent Events.
- **Colors** (`app/colors.py`): Teamup's API returns a sub-calendar `color` as an
  integer id (1-48), not a hex, so we resolve it through Teamup's official 48-color
  palette. The map therefore inherits each calendar's actual color. The pill's
  "name" comes from the event's `who` field (falls back to title) — change the one
  line in `web/app.js` (marked with a comment) if your customer name lives in the
  title or a custom field.

## Quick start (demo — no Teamup account needed)

```bash
cd ~/teamup-dispatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DEMO=1 uvicorn app.main:app --reload --port 8000
# open http://127.0.0.1:8000  -> sample jobs across SF on the map
```

## Run against your calendar

```bash
cp .env.example .env
# edit .env: set TEAMUP_API_KEY and TEAMUP_CALENDAR_ID (and geocoder if you like)
. .venv/bin/activate
uvicorn app.main:app --port 8000
```

- **API key**: request at https://apidocs.teamup.com/ (read-only is enough for the map).
- **Calendar ID**: the code in your share URL, e.g. `teamup.com/ksABC123` -> `ksABC123`.

### Going live with webhooks (optional)

The local box doesn't need a public URL for polling to work. If you want instant
push, expose the app (e.g. `cloudflared tunnel --url http://localhost:8000`) and
register the public `/webhook` URL in your Teamup calendar's webhook settings
(a paid-plan feature). Otherwise drop `POLL_INTERVAL` for snappier polling.

## Known limitations / next steps

- Time-window filtering compares ISO datetime strings; events spanning calendars
  in very different UTC offsets could be off by an edge case. Fine for typical use.
- Deletions rely on `modifiedSince` returning a `deleted`/`delete_dt` marker —
  verify the exact shape against your calendar and adjust `store.upsert_event`.
- No auth on the dashboard itself — put it behind your own access control before
  exposing it publicly.
- Recurring events: `modifiedSince` returns expanded instances within the polled
  window; very long-horizon recurrences past the backfill window need a wider
  `BACKFILL_DAYS_FUTURE` or a periodic forward re-backfill.
- Geocoder accuracy: Nominatim is great for full addresses, weaker on vague
  "business name only" locations — switch to `google` for those at volume.
- Pills are always-on (permanent). With many jobs packed into a small area they
  can overlap; if that bites, switch the tooltip to `permanent: false` (show on
  hover) or gate it on a zoom threshold in `web/app.js`.
