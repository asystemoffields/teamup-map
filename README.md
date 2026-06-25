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
- **Geocoder** (`app/geocode.py`): pluggable — `census` (free, no key, US-only,
  strong on rural addresses), `nominatim` (free OSM), `google`, or `mapbox`.
  `GEOCODER` can be a fallback chain (e.g. `census,nominatim`). Results cached in SQLite.
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
- **Weather** (`app/weather.py`): per-job risk badges on the map from the US
  **National Weather Service** (`api.weather.gov` — free, **no API key**, US-only).
  Because every job already has a lat/lng from the geocoder, each upcoming job gets
  its forecast and any active NWS alerts for that exact point. See below.

## Weather warnings

Exterior trades live and die by the weather, so each mapped job within the
forecast horizon (default 7 days) carries a small corner badge:

- 🔴 **red** — an official NWS **watch / warning / advisory** is active for that
  location during the job's time window (e.g. *Winter Storm Warning*, *High Wind
  Warning*). The alert name is shown verbatim.
- 🟡 **yellow** — a forecast **work-stopper** in the window: precipitation
  probability ≥ `WEATHER_RAIN_POP` (50%), **any snow**, or wind ≥ `WEATHER_WIND_MPH`
  sustained (20) / `WEATHER_GUST_MPH` gust (30). Optional cold/heat flags
  (`WEATHER_COLD_F` / `WEATHER_HEAT_F`) are off by default.
- 🟢 **green** — nothing notable. No badge, but the popup still shows the forecast.

Click a pin to see the full forecast for the job's window (conditions, temp, wind,
precip chance) plus any alert text. A glyph (❄ snow · 💨 wind · 🌧 rain · ⚠ other)
hints at the dominant condition. The **Weather warnings** checkbox in the sidebar
toggles the whole layer.

It's served at `GET /api/weather` (same `cal`/`from`/`to`/`subcalendars` filters as
`/api/events`, keyed by event id) and overlaid **after** the map renders, so a slow
lookup never delays the map and a failure just means no badge. NWS responses are
cached in SQLite — the point→grid mapping effectively forever, the forecast ~1h,
alerts ~10min — so repeat loads are instant and we stay polite to the free service.
NWS asks callers to identify themselves: set `WEATHER_CONTACT` (it falls back to
`NOMINATIM_EMAIL`). Set `WEATHER=0` to disable. US-only by nature of NWS.

## Easiest: double-click to launch

Cross-platform launchers handle the venv, install, demo-vs-live, and browser for you:

- **Windows** — double-click **`launch.bat`**.
- **Linux (KDE/Dolphin)** — run **`./launch.sh`** once from a terminal; it generates
  **`Launch Teamup Dispatch.desktop`** (with the correct path for *your* machine),
  which you can double-click thereafter. (The `.desktop` isn't committed because its
  `Exec=` path is absolute and per-machine.)
- **macOS / any shell** — run **`./launch.sh`**.

First run sets everything up; it opens the map in your browser and runs until you
close the window. With no `.env` it shows demo data; once `.env` has a key it goes live.

## Quick start (manual, demo — no Teamup account needed)

**Windows (PowerShell):**
```powershell
cd teamup-dispatch
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DEMO=1; $env:DB_PATH="demo.db"; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

**Linux / macOS:**
```bash
cd ~/teamup-dispatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DEMO=1 DB_PATH=demo.db uvicorn app.main:app --reload --port 8000
# open http://127.0.0.1:8000  -> sample jobs across SF on the map
# (DB_PATH=demo.db keeps the sample data out of your real teamup_dispatch.db)
```

## Run against your calendar

Copy `.env.example` to `.env` and set `TEAMUP_API_KEY` + `TEAMUP_CALENDAR_ID`
(`config.py` reads `.env` on both Windows and Linux), then start the server
(`uvicorn app.main:app --port 8000`, or just use the double-click launcher).

- **API key**: request at https://apidocs.teamup.com/ (read-only is enough for the map).
- **Calendar ID**: the code in your share URL, e.g. `teamup.com/ksABC123` -> `ksABC123`.

### Multiple calendars (optional)

Show several **entirely separate** Teamup calendars (e.g. a Dispatch calendar
and a Production-crew calendar) and switch the whole view between them with a
**Calendar dropdown** at the top of the sidebar. Add `TEAMUP_CALENDAR_ID_2`
(+ `TEAMUP_CALENDAR_NAME_2`, and `TEAMUP_API_KEY_2` only if it needs a different
key); `_3`, `_4`, … work too. Each calendar is namespaced (`cal1`, `cal2`, …):
its own poller and `modifiedSince` token, its own events and sub-calendars, but
a **shared geocode cache** (a given address is geocoded once across all of them).
With a single calendar configured, the dropdown is hidden and nothing changes.

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

## Running it for your team (2–10 people)

**Model: each person runs their own local copy.** Everyone double-clicks the
launcher on their own machine and reads the same Teamup calendar independently —
no shared server, no network setup. Simplest to operate.

What each person needs once:
- this repo + Python 3, and a `.env` with `TEAMUP_API_KEY` + `TEAMUP_CALENDAR_ID`
  (a read-only key is enough). The launcher sets up the venv + deps on first run.

How independent instances behave:
- Each has its own poller, geocode cache, and route cache — fully self-contained
  and self-consistent. Opening **several browser tabs** against your own instance
  is totally fine; the per-instance hardening below keeps that smooth.
- If everyone shares one read-only key, that's N independent pollers hitting Teamup
  (~one request per `POLL_INTERVAL` each). Fine for ~10; raise `POLL_INTERVAL` if you
  ever see Teamup throttling.
- Each instance geocodes/routes independently against the free Nominatim/OSRM
  servers. For heavy use, self-host OSRM (`OSRM_URL`) or use a paid geocoder
  (`GEOCODER=google`). Novel addresses geocode at ~1/sec (Nominatim policy); cached
  ones are instant.

Per-instance concurrency hardening (also helps multiple tabs): route cache,
debounced + jittered refresh, bounded SSE queues, shared HTTP client, SQLite
`busy_timeout`.

- **Never run an instance with `--workers >1`.** The poller and live-update bus are
  in-process; multiple workers would duplicate the poller (racing the `modifiedSince`
  token) and split SSE clients so some tabs stop getting live updates. The launchers
  already run single-process.
- **No login by design.** Each copy serves only `localhost`, so it's private to
  that machine. The server **fails closed** if exposed to a network: bound to a
  non-loopback address, any non-localhost request is refused (HTTP 403) unless
  `DASHBOARD_TOKEN` is set, so binding `--host 0.0.0.0` can never silently serve
  customer data. To run **one shared box** (everyone browses to it): set
  `DASHBOARD_TOKEN`, bind `--host 0.0.0.0`, and reach it once as
  `http://<box-ip>:8000/?token=<DASHBOARD_TOKEN>` (it drops a cookie thereafter).
  Better still, put it behind a reverse proxy / Cloudflare Access (the proxy is
  then the loopback client and does the auth). Move the pub/sub to Redis only if
  you scale past one process.

## Known limitations / next steps

- Time-window filtering compares wall-clock-local times as strings (the bounds are
  sent as local wall-clock, matching the event times). Correct for a single timezone;
  a calendar mixing very different UTC offsets could still be off at a DST/zone edge.
- Deletions rely on `modifiedSince` returning a `deleted`/`delete_dt` marker —
  verify the exact shape against your calendar and adjust `store.upsert_event`.
- No auth on the dashboard itself — put it behind your own access control before
  exposing it publicly.
- Recurring events: `modifiedSince` returns expanded instances within the polled
  window; very long-horizon recurrences past the backfill window need a wider
  `BACKFILL_DAYS_FUTURE` or a periodic forward re-backfill.
- Geocoder accuracy: Nominatim is weak on **rural US residential** addresses
  (it missed >half of a real Northern-Michigan calendar). For US calendars use
  `GEOCODER=census,nominatim` — the free US Census geocoder resolves rural
  addresses Nominatim can't, with OSM as backup; `google` is best at volume.
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
