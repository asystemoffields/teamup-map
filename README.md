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
- **Routing** (`app/routing.py`): connects the day's stops into a line with distance
  + drive time. Default backend is **OSRM** (the OSM project's free router — real
  driving routes), with automatic **haversine straight-line fallback** if OSRM is
  unreachable. Configurable via `ROUTING` + `OSRM_URL` (point at a self-hosted OSRM
  or a paid provider without touching the frontend).

## Easiest: double-click to launch

Cross-platform launchers handle the venv, install, demo-vs-live, and browser for you:

- **Windows** — double-click **`launch.bat`**.
- **Linux (KDE/Dolphin)** — double-click **`Launch Teamup Dispatch.desktop`**.
- **macOS / any shell** — run **`./launch.sh`**.

First run sets everything up; it opens the map in your browser and runs until you
close the window. With no `.env` it shows demo data; once `.env` has a key it goes live.

## Quick start (manual, demo — no Teamup account needed)

**Windows (PowerShell):**
```powershell
cd teamup-dispatch
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DEMO=1; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

**Linux / macOS:**
```bash
cd ~/teamup-dispatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DEMO=1 uvicorn app.main:app --reload --port 8000
# open http://127.0.0.1:8000  -> sample jobs across SF on the map
```

## Run against your calendar

Copy `.env.example` to `.env` and set `TEAMUP_API_KEY` + `TEAMUP_CALENDAR_ID`
(`config.py` reads `.env` on both Windows and Linux), then start the server
(`uvicorn app.main:app --port 8000`, or just use the double-click launcher).

- **API key**: request at https://apidocs.teamup.com/ (read-only is enough for the map).
- **Calendar ID**: the code in your share URL, e.g. `teamup.com/ksABC123` -> `ksABC123`.

### Going live with webhooks (optional)

The local box doesn't need a public URL for polling to work. If you want instant
push, expose the app (e.g. `cloudflared tunnel --url http://localhost:8000`) and
register the public `/webhook` URL in your Teamup calendar's webhook settings
(a paid-plan feature). Otherwise drop `POLL_INTERVAL` for snappier polling.

## Route planning & prospective jobs

The sidebar gives a dispatcher three planning tools on top of the live map:

- **Filter by color** — colored chips toggle pins by their calendar color (independent
  of the sub-calendar checkboxes; handy when several calendars share a color).
- **Route (number & connect by time)** — sorts the visible mapped jobs by appointment
  time, numbers the pins `1, 2, 3, …`, and draws the connecting route with total
  distance + drive time. Set the time window to **Today** for a single day's route.
- **Prospective job ("test-fit")** — type an address (optionally a time + name) and
  *Add to route*. It geocodes the address and slots it into the route:
  - with a time → inserts at that point in the day;
  - without a time → finds the **least-detour gap** and suggests that slot + an
    approximate time.
  The candidate shows as a dashed marker, the route reflows to include it, and the
  panel reports the added distance. Nothing is written back to Teamup — **Copy address**
  puts it on your clipboard so you can paste it into Teamup yourself (this fits a
  read-only API key).

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
- Routing uses the **public OSRM demo server** by default — fine for a handful of
  stops, but it's a shared/best-effort service (no SLA, fair-use only). For
  production, self-host OSRM or point `OSRM_URL` at a paid provider. The route is a
  fixed time-order (not a travelling-salesman optimization); "best slot" for a
  prospective job is a least-detour *insertion*, computed with straight-line
  distance for speed.
- The route view assumes a single day's stops; spanning a multi-day window numbers
  every job in the range into one sequence. Use the **Today** window for daily routes.
- **Copy address** uses the modern clipboard API on a secure origin (localhost is
  fine) with a legacy fallback for plain-HTTP LAN access.
