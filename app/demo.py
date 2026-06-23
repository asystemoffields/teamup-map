"""Bundled sample data so the app runs and shows pins with no Teamup creds.
Two demo calendars (Sales + Production) so the calendar switcher is exercised
offline. Coordinates are hard-coded (real landmarks) so demo needs no network."""
import datetime as dt

from app import store

# color = Teamup color id (1-48), same as the real API returns -> resolved to hex.
# (title, who, location, lat, lng, subcalendar_id, hour_offset, duration_h)

# ---- calendar 1: Sales (San Francisco) ----
_SALES_SUBS = [
    {"id": 1, "name": "Plumbing", "color": 2},     # red
    {"id": 2, "name": "Electrical", "color": 36},  # amber
    {"id": 3, "name": "HVAC", "color": 18},        # blue
]
_SALES_JOBS = [
    ("Burst pipe repair", "Dana", "Ferry Building, San Francisco, CA", 37.7955, -122.3937, 1, 1, 2),
    ("Water heater swap", "Dana", "Painted Ladies, San Francisco, CA", 37.7763, -122.4327, 1, 4, 3),
    ("Panel upgrade", "Reyes", "Coit Tower, San Francisco, CA", 37.8024, -122.4058, 2, 2, 2),
    ("Outlet install", "Reyes", "Oracle Park, San Francisco, CA", 37.7786, -122.3893, 2, 6, 1),
    ("AC compressor check", "Lin", "Golden Gate Park, San Francisco, CA", 37.7694, -122.4862, 3, 3, 2),
    ("Furnace inspection", "Lin", "Palace of Fine Arts, San Francisco, CA", 37.8029, -122.4484, 3, 5, 2),
    # one deliberately address-less job, to show the "unmapped" tray
    ("Phone consult (no site)", "Dana", "", None, None, 1, 7, 1),
]

# ---- calendar 2: Production (East Bay) — different crews, areas, work types ----
_PROD_SUBS = [
    {"id": 1, "name": "Framing", "color": 10},     # green
    {"id": 2, "name": "Install", "color": 22},     # teal/blue
    {"id": 3, "name": "Punch-list", "color": 5},   # orange
]
_PROD_JOBS = [
    ("Window install", "Crew A", "Lake Merritt, Oakland, CA", 37.8044, -122.2570, 2, 1, 4),
    ("Siding tear-off", "Crew A", "Jack London Square, Oakland, CA", 37.7949, -122.2776, 1, 2, 3),
    ("Roof framing", "Crew B", "Sather Tower, Berkeley, CA", 37.8721, -122.2578, 1, 3, 5),
    ("Trim & punch-list", "Crew B", "Oakland Museum of California, Oakland, CA", 37.7975, -122.2638, 3, 6, 2),
    ("Door install", "Crew C", "Berkeley Marina, Berkeley, CA", 37.8650, -122.3158, 2, 4, 3),
    ("Warranty walk-through", "Crew C", "", None, None, 3, 8, 1),
]


def _load_calendar(cal: str, subs, jobs) -> None:
    store.upsert_subcalendars(subs, cal)
    base = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    for title, who, loc, lat, lng, sub, off, dur in jobs:
        start = base + dt.timedelta(hours=off)
        end = start + dt.timedelta(hours=dur)
        eid = f"demo-{title}".replace(" ", "-").lower()   # store namespaces with `cal`
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
            },
            cal,
        )
        if loc and lat is not None:
            store.save_geocode(store.norm_addr(loc), lat, lng, "ok", "demo")


def load() -> None:
    _load_calendar("cal1", _SALES_SUBS, _SALES_JOBS)
    _load_calendar("cal2", _PROD_SUBS, _PROD_JOBS)
    print(f"[demo] loaded Sales ({len(_SALES_JOBS)}) + Production ({len(_PROD_JOBS)}) sample events")
