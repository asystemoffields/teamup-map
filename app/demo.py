"""Bundled sample data so the app runs and shows pins with no Teamup creds.
Coordinates are hard-coded (real SF landmarks) so demo mode needs no network."""
import datetime as dt

from app import store

# color = Teamup color id (1-48), same as the real API returns -> resolved to hex
SUBS = [
    {"id": 1, "name": "Plumbing", "color": 2},    # #cf2424 red
    {"id": 2, "name": "Electrical", "color": 36},  # #f6c811 amber
    {"id": 3, "name": "HVAC", "color": 18},        # #4770d8 blue
]

# (title, who, location, lat, lng, subcalendar_id, hour_offset, duration_h)
_JOBS = [
    ("Burst pipe repair", "Dana", "Ferry Building, San Francisco, CA", 37.7955, -122.3937, 1, 1, 2),
    ("Water heater swap", "Dana", "Painted Ladies, San Francisco, CA", 37.7763, -122.4327, 1, 4, 3),
    ("Panel upgrade", "Reyes", "Coit Tower, San Francisco, CA", 37.8024, -122.4058, 2, 2, 2),
    ("Outlet install", "Reyes", "Oracle Park, San Francisco, CA", 37.7786, -122.3893, 2, 6, 1),
    ("AC compressor check", "Lin", "Golden Gate Park, San Francisco, CA", 37.7694, -122.4862, 3, 3, 2),
    ("Furnace inspection", "Lin", "Palace of Fine Arts, San Francisco, CA", 37.8029, -122.4484, 3, 5, 2),
    # one deliberately address-less job, to show the "unmapped" tray
    ("Phone consult (no site)", "Dana", "", None, None, 1, 7, 1),
]


def load() -> None:
    store.upsert_subcalendars(SUBS)
    base = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    for title, who, loc, lat, lng, sub, off, dur in _JOBS:
        start = base + dt.timedelta(hours=off)
        end = start + dt.timedelta(hours=dur)
        eid = f"demo-{title}".replace(" ", "-").lower()
        store.upsert_event(
            {
                "id": eid,
                "subcalendar_id": sub,
                "subcalendar_ids": [sub],
                "title": title,
                "who": who,
                "location": loc,
                "start_dt": start.isoformat(),
                "end_dt": end.isoformat(),
                "all_day": False,
                "version": "1",
            }
        )
        if loc and lat is not None:
            store.save_geocode(store.norm_addr(loc), lat, lng, "ok", "demo")
    print(f"[demo] loaded {len(_JOBS)} sample events")
