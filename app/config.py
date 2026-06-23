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
