"""Read-only 'going live' validator for a real Teamup calendar.

Makes ONLY GET calls (never writes to Teamup). Confirms the field mappings the
README flags as 'verify against your calendar' before we trust the live map:

  1. subcalendars  -> id, name, raw color int, resolved hex (colors.py)
  2. events sample -> who vs title (customer-name source), location geocodability
  3. raw event     -> full field shape (so we see custom fields, who format, etc.)
  4. modifiedSince -> response shape + how a deletion is marked

Usage (reads .env via app.config, same as the app):
    .venv/bin/python -m scripts.golive_check
"""
import asyncio
import json
from collections import Counter

from app import config
from app.colors import resolve_color
from app.teamup import TeamupClient
from app.store import norm_addr


def _looks_geocodable(loc: str) -> str:
    """Cheap heuristic: does this look like a street address vs a bare name?"""
    if not loc or not loc.strip():
        return "EMPTY"
    has_number = any(ch.isdigit() for ch in loc)
    has_comma = "," in loc
    if has_number and has_comma:
        return "good"        # "123 Main St, Traverse City, MI"
    if has_number:
        return "ok"          # has a number, no comma
    return "weak"            # business-name only -> Nominatim struggles


async def main():
    if not config.API_KEY or not config.CALENDAR_ID:
        print("MISSING CREDS: set TEAMUP_API_KEY and TEAMUP_CALENDAR_ID in .env")
        return

    c = TeamupClient()
    print(f"calendar: {config.CALENDAR_ID}\n")

    # 1. subcalendars + colors -------------------------------------------------
    subs = await c.subcalendars()
    print(f"=== SUBCALENDARS ({len(subs)}) ===")
    for s in subs:
        raw = s.get("color")
        print(f"  id={s['id']:>4}  raw_color={str(raw):>4} -> {resolve_color(raw)}  "
              f"{s.get('name','')}")

    # 2. events over a live window --------------------------------------------
    import datetime as _dt  # stdlib only; fine in a one-shot script
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=7)).isoformat()
    end = (today + _dt.timedelta(days=30)).isoformat()
    evs = await c.events(start, end)
    print(f"\n=== EVENTS {start}..{end}  ({len(evs)}) ===")

    who_filled = sum(1 for e in evs if (e.get("who") or "").strip())
    title_filled = sum(1 for e in evs if (e.get("title") or "").strip())
    geo = Counter(_looks_geocodable(e.get("location") or "") for e in evs)
    print(f"  who populated   : {who_filled}/{len(evs)}")
    print(f"  title populated : {title_filled}/{len(evs)}")
    print(f"  location quality: {dict(geo)}   (EMPTY/weak = won't map well)")

    print("\n  --- sample (first 12) ---")
    for e in evs[:12]:
        loc = (e.get("location") or "").strip()
        print(f"   [{_looks_geocodable(loc):>5}] who={e.get('who','')!r:<22} "
              f"title={e.get('title','')!r:<28} loc={loc!r}")

    # 3. one full raw event ----------------------------------------------------
    if evs:
        print("\n=== RAW EVENT[0] (all fields Teamup returns) ===")
        print(json.dumps(evs[0], indent=2, default=str)[:2500])

    # 4. modifiedSince shape ---------------------------------------------------
    # ts=1 -> 'everything modified since the epoch' = the full current set, which
    # lets us see the exact event shape the poller consumes (incl. any tombstones).
    ms_events, ts = await c.events_modified_since(1)
    print(f"\n=== modifiedSince(1) -> {len(ms_events)} events, timestamp={ts} ===")
    tomb = [e for e in ms_events if e.get("deleted") or e.get("delete_dt")]
    print(f"  tombstoned (deleted/delete_dt present): {len(tomb)}")
    if tomb:
        print("  tombstone keys:", sorted(tomb[0].keys()))

    await c.aclose()
    print("\nDONE (no writes were made to Teamup).")


if __name__ == "__main__":
    asyncio.run(main())
