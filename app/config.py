"""Configuration, loaded from environment (and a .env file if present).

No third-party dotenv dependency — we parse .env ourselves so the only
runtime deps are fastapi/uvicorn/httpx.
"""
import os
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    # utf-8-sig tolerates a BOM (Windows Notepad), which would otherwise corrupt
    # the first key (e.g. ﻿TEAMUP_API_KEY)
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # strip inline comments and surrounding quotes
        val = val.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


_load_dotenv()

# --- Teamup ---
API_KEY = os.environ.get("TEAMUP_API_KEY", "")
CALENDAR_ID = os.environ.get("TEAMUP_CALENDAR_ID", "")

# --- Access control (only matters when bound to a non-loopback address) ---
# When the server is reachable from anything other than localhost, requests must
# present this token (?token=, an `x-dispatch-token` header, or the `dt` cookie).
# Empty => non-loopback requests are refused outright (fail closed), so binding
# to 0.0.0.0 without a token can never silently expose customer PII.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
# Optional shared secret for the /webhook accelerator: register the webhook URL
# as .../webhook?t=<secret>. Empty => webhook accepts any caller (it can only
# wake the poll loop; it never trusts the request body).
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# --- Geocoder ---
GEOCODER = os.environ.get("GEOCODER", "nominatim").lower()          # nominatim | google | mapbox
GEOCODER_API_KEY = os.environ.get("GEOCODER_API_KEY", "")
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "")              # courtesy contact per OSM policy

# --- Routing (connecting line + distance/duration) ---
ROUTING = os.environ.get("ROUTING", "osrm").lower()                 # osrm | haversine
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")

# --- Polling / backfill ---
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))          # seconds between change polls
BACKFILL_DAYS_PAST = int(os.environ.get("BACKFILL_DAYS_PAST", "7"))
BACKFILL_DAYS_FUTURE = int(os.environ.get("BACKFILL_DAYS_FUTURE", "60"))

# --- Storage ---
# (Host is localhost-only by design; port is read from the shell env by the
#  launchers, e.g. `PORT=9000 ./launch.sh`, not from here.)
DB_PATH = os.environ.get("DB_PATH", "teamup_dispatch.db")

# --- Demo mode: bundled sample data, no Teamup creds needed ---
DEMO = os.environ.get("DEMO", "0").lower() in ("1", "true", "yes", "on")


# --- Weather warnings (US National Weather Service: free, NO API key) ---------
# A per-job risk badge on the map from api.weather.gov: official NWS alerts
# (watch/warning/advisory -> red) plus a forecast for the job's time window
# (rain/snow/wind work-stoppers -> yellow). US-only, which fits N. Michigan + UP.
WEATHER = os.environ.get("WEATHER", "1").lower() in ("1", "true", "yes", "on")
# NWS asks every caller to send a contact (email or site) in the User-Agent.
# Falls back to the Nominatim contact you already set, then a placeholder.
WEATHER_CONTACT = os.environ.get("WEATHER_CONTACT", "").strip() or NOMINATIM_EMAIL
# Only assess jobs starting within this many days (NWS forecasts run ~7 days).
WEATHER_HORIZON_DAYS = int(os.environ.get("WEATHER_HORIZON_DAYS", "7"))
# Forecast work-stopper thresholds, flagged 🟡 when exceeded during the window:
WEATHER_RAIN_POP = int(os.environ.get("WEATHER_RAIN_POP", "50"))   # precip probability %
WEATHER_WIND_MPH = int(os.environ.get("WEATHER_WIND_MPH", "20"))   # sustained wind mph
WEATHER_GUST_MPH = int(os.environ.get("WEATHER_GUST_MPH", "30"))   # wind gust mph


def _int_or_none(v):
    v = (v or "").strip()
    return int(v) if v else None


# Optional temperature flags (blank = off, the balanced default). Set e.g.
# WEATHER_COLD_F=20 / WEATHER_HEAT_F=90 for the "aggressive" material-cure heads-up.
WEATHER_COLD_F = _int_or_none(os.environ.get("WEATHER_COLD_F", ""))
WEATHER_HEAT_F = _int_or_none(os.environ.get("WEATHER_HEAT_F", ""))
# Backstop on a cold-cache pass so a huge window can't fan out to NWS without
# bound; soonest jobs are kept first. Distinct job locations per /api/weather call.
WEATHER_MAX_POINTS = int(os.environ.get("WEATHER_MAX_POINTS", "80"))


# --- Multi-calendar support --------------------------------------------------
# The app can show several entirely separate Teamup calendars and let the user
# switch between them. Calendar 1 uses the unsuffixed vars above; calendars 2..N
# use _2.._N suffixes (TEAMUP_CALENDAR_ID_2, TEAMUP_CALENDAR_NAME_2, and an
# optional TEAMUP_API_KEY_2 — else they reuse API_KEY). Each calendar gets a
# stable internal key (cal1, cal2, …) used to namespace its data + route the UI.
_MAX_CALENDARS = 8


def _load_calendars():
    cals = []
    for slot in range(1, _MAX_CALENDARS + 1):
        suffix = "" if slot == 1 else f"_{slot}"
        cid = os.environ.get(f"TEAMUP_CALENDAR_ID{suffix}", "").strip()
        if not cid:
            continue
        cals.append({
            "key": f"cal{slot}",
            "id": cid,
            "token": os.environ.get(f"TEAMUP_API_KEY{suffix}", "").strip() or API_KEY,
            "name": os.environ.get(f"TEAMUP_CALENDAR_NAME{suffix}", "").strip() or f"Calendar {slot}",
        })
    return cals


def active_calendars():
    """The calendars to show. DEMO ships two so the switcher is exercised
    offline; otherwise read them from the environment. Evaluated at call time so
    a late DEMO flip (e.g. the frozen .exe with no creds) is honored."""
    if DEMO:
        return [
            {"key": "cal1", "id": "", "token": "", "name": "Sales"},
            {"key": "cal2", "id": "", "token": "", "name": "Production"},
        ]
    return _load_calendars()
