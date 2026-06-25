"""SQLite store: events, geocode cache, subcalendars, and a small meta table.

The geocode cache is the load-bearing piece: Teamup only gives us a text
address per event (no coordinates), so we geocode each distinct address once
and reuse the result for every event that shares it.
"""
import datetime as dt
import json
import re
import sqlite3
import threading
import time

from app import config
from app.colors import resolve_color

_lock = threading.Lock()
_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")     # readers don't block the writer
        _conn.execute("PRAGMA busy_timeout=5000")    # wait, don't error, on transient locks
        _init(_conn)
        _migrate(_conn)
    return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """Bring a pre-multi-calendar DB up to the namespaced schema in place, so a
    shipped warm cache keeps its geocode results instead of re-geocoding."""
    ev_cols = [r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()]
    if ev_cols and "cal" not in ev_cols:
        c.execute("ALTER TABLE events ADD COLUMN cal TEXT DEFAULT 'cal1'")
        # Legacy rows hold raw Teamup ids; the poller now writes cal-prefixed ids
        # ('cal1:123'), so namespace the old ids too — otherwise the next backfill
        # would insert a second, duplicate copy of every existing event.
        c.execute("UPDATE events SET id = cal || ':' || id WHERE instr(id, ':') = 0")
    sc_cols = [r[1] for r in c.execute("PRAGMA table_info(subcalendars)").fetchall()]
    if sc_cols and "cal" not in sc_cols:
        # subcalendars is a derived cache (rebuilt from Teamup every startup), so
        # it's safe to recreate it with the (cal, id) composite key.
        c.execute("DROP TABLE subcalendars")
        c.execute("CREATE TABLE subcalendars (cal TEXT NOT NULL DEFAULT 'cal1', "
                  "id INTEGER, name TEXT, color TEXT, PRIMARY KEY (cal, id))")
    # now that `cal` is guaranteed to exist on events, index it
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_cal ON events(cal)")
    c.commit()


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id              TEXT PRIMARY KEY,   -- '<cal>:<teamup id>' (namespaced)
            cal             TEXT DEFAULT 'cal1',-- which configured calendar it came from
            subcalendar_id  INTEGER,
            subcalendar_ids TEXT,           -- json array
            title           TEXT,
            who             TEXT,
            location        TEXT,
            loc_norm        TEXT,           -- normalized address (cache key)
            notes           TEXT,
            start_dt        TEXT,
            end_dt          TEXT,
            all_day         INTEGER,
            version         TEXT,
            update_dt       TEXT,
            lat             REAL,
            lng             REAL,
            geo_status      TEXT,           -- none|pending|ok|notfound|error
            deleted         INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS geocode_cache (
            addr    TEXT PRIMARY KEY,        -- normalized address (SHARED across calendars)
            lat     REAL,
            lng     REAL,
            status  TEXT,
            source  TEXT,
            ts      INTEGER
        );
        CREATE TABLE IF NOT EXISTS subcalendars (
            cal    TEXT NOT NULL DEFAULT 'cal1',
            id     INTEGER,
            name   TEXT,
            color  TEXT,
            PRIMARY KEY (cal, id)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS weather_cache (
            key     TEXT PRIMARY KEY,   -- wx:point/fc/al:<coords-or-grid>
            payload TEXT,               -- json (grid mapping | forecast periods | alerts)
            expires INTEGER             -- unix ts; row ignored once past
        );
        CREATE INDEX IF NOT EXISTS idx_events_locnorm ON events(loc_norm);
        CREATE INDEX IF NOT EXISTS idx_events_geo ON events(geo_status);
        -- NB: the idx_events_cal index is created in _migrate(), AFTER the cal
        -- column is guaranteed to exist (an old DB gets it added there).
        """
    )
    c.commit()


def norm_addr(a: str) -> str:
    return re.sub(r"\s+", " ", (a or "").strip().lower())


# ---------------- meta ----------------

def set_meta(key: str, value) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        c.commit()


def get_meta(key: str, default=None):
    # reads take the lock too: the single shared connection must never be touched
    # concurrently (safe today on one event loop; future-proofs a threadpool).
    with _lock:
        row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ---------------- subcalendars ----------------

def upsert_subcalendars(subs, cal: str = "cal1") -> None:
    with _lock:
        c = conn()
        for s in subs:
            c.execute(
                "INSERT INTO subcalendars(cal,id,name,color) VALUES(?,?,?,?) "
                "ON CONFLICT(cal,id) DO UPDATE SET name=excluded.name, color=excluded.color",
                # Teamup gives `color` as an int id (1-48); store the resolved hex
                # so the map inherits the real calendar color.
                (cal, s["id"], s.get("name", ""), resolve_color(s.get("color")) or ""),
            )
        c.commit()


def get_subcalendars(cal: str = "cal1"):
    with _lock:
        return [dict(r) for r in conn().execute(
            "SELECT id,name,color FROM subcalendars WHERE cal=? ORDER BY name", (cal,)
        ).fetchall()]


# ---------------- events ----------------

def upsert_event(e, cal: str = "cal1") -> None:
    """Insert/update one Teamup event under calendar `cal`. The stored primary
    key is namespaced ('<cal>:<teamup id>') so the same event id in two separate
    calendars can't collide. If its address is new (cache miss), geo_status is
    set to 'pending' for the poller to resolve."""
    with _lock:
        c = conn()
        deleted = 1 if (e.get("deleted") or e.get("delete_dt")) else 0
        loc = (e.get("location") or "").strip()
        norm = norm_addr(loc)
        eid = f"{cal}:{e.get('id')}"

        lat = lng = None
        geo_status = "none"
        if norm:
            row = c.execute(
                "SELECT lat,lng,status FROM geocode_cache WHERE addr=?", (norm,)
            ).fetchone()
            if row:
                lat, lng, geo_status = row["lat"], row["lng"], row["status"]
            else:
                geo_status = "pending"

        subids = e.get("subcalendar_ids")
        if not subids:
            subids = [e["subcalendar_id"]] if e.get("subcalendar_id") else []

        c.execute(
            """INSERT INTO events
               (id,cal,subcalendar_id,subcalendar_ids,title,who,location,loc_norm,notes,
                start_dt,end_dt,all_day,version,update_dt,lat,lng,geo_status,deleted)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 subcalendar_id=excluded.subcalendar_id,
                 subcalendar_ids=excluded.subcalendar_ids,
                 title=excluded.title, who=excluded.who,
                 location=excluded.location, loc_norm=excluded.loc_norm,
                 notes=excluded.notes, start_dt=excluded.start_dt, end_dt=excluded.end_dt,
                 all_day=excluded.all_day, version=excluded.version, update_dt=excluded.update_dt,
                 lat=excluded.lat, lng=excluded.lng, geo_status=excluded.geo_status,
                 deleted=excluded.deleted""",
            (
                eid, cal, e.get("subcalendar_id"), json.dumps(subids),
                e.get("title", ""), e.get("who", ""), loc, norm, e.get("notes", ""),
                e.get("start_dt"), e.get("end_dt"), 1 if e.get("all_day") else 0,
                str(e.get("version", "")), e.get("update_dt", ""),
                lat, lng, geo_status, deleted,
            ),
        )
        c.commit()


def pending_addresses(cal: str = None, limit: int = 20):
    """Distinct addresses still needing a geocode (optionally limited to one
    calendar). Includes 'error' rows so a TRANSIENT failure (timeout/429) is
    retried next cycle rather than sticking forever; 'notfound' stays terminal."""
    q = ("SELECT loc_norm, MAX(location) AS loc FROM events "
         "WHERE geo_status IN ('pending','error') AND deleted=0 AND loc_norm<>''")
    args = []
    if cal:
        q += " AND cal=?"
        args.append(cal)
    q += " GROUP BY loc_norm LIMIT ?"
    args.append(limit)
    with _lock:
        rows = conn().execute(q, args).fetchall()
    return [(r["loc_norm"], r["loc"]) for r in rows]


def get_cached_geocode(norm: str):
    with _lock:
        row = conn().execute(
            "SELECT lat,lng,status,source FROM geocode_cache WHERE addr=?", (norm,)
        ).fetchone()
    return dict(row) if row else None


def save_geocode(norm: str, lat, lng, status: str, source: str) -> None:
    """Cache a geocode result and fan it out to every event with that address."""
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO geocode_cache(addr,lat,lng,status,source,ts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(addr) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,"
            "status=excluded.status,source=excluded.source,ts=excluded.ts",
            (norm, lat, lng, status, source, int(time.time())),
        )
        c.execute(
            "UPDATE events SET lat=?, lng=?, geo_status=? WHERE loc_norm=?",
            (lat, lng, status, norm),
        )
        c.commit()


# ---------------- weather cache ----------------

def get_weather_cache(key: str):
    """Cached JSON payload for `key`, or None if missing/expired. Note: a
    legitimately-empty payload (e.g. [] for 'no active alerts') is returned as
    [], distinct from None — callers use `is not None` to honor that cache hit."""
    with _lock:
        row = conn().execute(
            "SELECT payload, expires FROM weather_cache WHERE key=?", (key,)
        ).fetchone()
    if not row or (row["expires"] and row["expires"] < time.time()):
        return None
    try:
        return json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def set_weather_cache(key: str, payload, ttl: int) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO weather_cache(key,payload,expires) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, expires=excluded.expires",
            (key, json.dumps(payload), int(time.time()) + ttl),
        )
        c.commit()


def query_events(cal="cal1", dt_from=None, dt_to=None, subcalendar_ids=None):
    q = "SELECT * FROM events WHERE deleted=0 AND cal=?"
    args = [cal]
    if dt_from:
        q += " AND (end_dt >= ? OR end_dt IS NULL)"
        args.append(dt_from)
    if dt_to:
        q += " AND start_dt <= ?"
        args.append(dt_to)
    with _lock:
        rows = [dict(r) for r in conn().execute(q, args).fetchall()]
    for r in rows:
        r["subcalendar_ids"] = json.loads(r["subcalendar_ids"] or "[]")
    if subcalendar_ids:
        sset = set(subcalendar_ids)
        rows = [r for r in rows if sset & set(r["subcalendar_ids"])]
    return rows


def prune(retain_days: int = 90) -> int:
    """Drop tombstoned events and ones that ended well in the past. The
    incremental modifiedSince poll upserts every changed event regardless of
    date (far-past edits, deletions, far-future), so without this the table
    grows without bound on a long-running box. Deleted rows never legitimately
    reappear, and old finished jobs fall outside any map window, so this is safe.
    The geocode_cache is intentionally NOT pruned (it's the load-bearing cache
    and distinct real addresses are naturally bounded)."""
    cutoff = (dt.date.today() - dt.timedelta(days=retain_days)).isoformat()
    with _lock:
        c = conn()
        cur = c.execute(
            "DELETE FROM events WHERE deleted=1 "
            "OR (end_dt IS NOT NULL AND end_dt <> '' AND end_dt < ?)",
            (cutoff,),
        )
        c.commit()
        return cur.rowcount
