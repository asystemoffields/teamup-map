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
    for line in p.read_text().splitlines():
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

# --- Geocoder ---
GEOCODER = os.environ.get("GEOCODER", "nominatim").lower()          # nominatim | google | mapbox
GEOCODER_API_KEY = os.environ.get("GEOCODER_API_KEY", "")
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "")              # courtesy contact per OSM policy

# --- Polling / backfill ---
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))          # seconds between change polls
BACKFILL_DAYS_PAST = int(os.environ.get("BACKFILL_DAYS_PAST", "7"))
BACKFILL_DAYS_FUTURE = int(os.environ.get("BACKFILL_DAYS_FUTURE", "60"))

# --- Server / storage ---
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DB_PATH", "teamup_dispatch.db")

# --- Demo mode: bundled sample data, no Teamup creds needed ---
DEMO = os.environ.get("DEMO", "0").lower() in ("1", "true", "yes", "on")
