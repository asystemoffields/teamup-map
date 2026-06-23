#!/usr/bin/env python3
"""Standalone entrypoint for the bundled (PyInstaller) Teamup Dispatch Map.

This is what the double-click .exe runs. It differs from `uvicorn app.main:app`
only in ways that matter once frozen into a single self-contained executable:

  1. Persistence. One-file PyInstaller unpacks the program to a fresh temp dir on
     every launch, so a relative DB/config path would be recreated (and the
     geocode cache lost) each run. We anchor the working directory to the folder
     the .exe lives in, so the config file and the SQLite cache sit right next to
     the .exe and survive across runs.
  2. No terminal. We open the browser ourselves once the server is answering.
  3. Demo fallback. A bare double-click with no credentials shows sample data
     instead of an empty live map (mirrors launch.sh's demo-vs-live choice).

The recipient needs nothing installed: no Python, no pip, no terminal.
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# --- 1. anchor persistent files (config + *.db) beside the executable --------
# When frozen, sys.executable is the .exe itself; its folder is the only stable,
# writable location we can count on. In a source checkout we leave cwd alone.
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).resolve().parent)

PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"

# Config file names we accept next to the .exe, first match wins. ".env" works
# too, but Windows Explorer hides the leading dot and fights you when you try to
# create one, so a plainly-named "teamup-config.txt" is the friendly default.
_CONFIG_NAMES = ("teamup-config.txt", ".env", "teamup.env")


def _load_sidecar_config() -> "str | None":
    """Parse the first config file found beside the .exe into the environment,
    BEFORE app.config is imported (its module-level globals read os.environ at
    import time). Same forgiving format as app.config._load_dotenv: KEY=value,
    '#' comments, optional quotes, tolerant of a Notepad BOM."""
    for name in _CONFIG_NAMES:
        p = Path(name)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.split(" #")[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)
        return name
    return None


_loaded_from = _load_sidecar_config()

# Import config only after the sidecar file is in the environment.
from app import config  # noqa: E402  (intentionally after _load_sidecar_config)

# --- 2. demo-vs-live ---------------------------------------------------------
if config.API_KEY:
    print(f"[startup] credentials found ({_loaded_from}) — going LIVE")
else:
    # No key: show the bundled sample data so the map isn't empty, and keep that
    # sample data out of the live cache file (same policy as launch.sh).
    config.DEMO = True
    if config.DB_PATH == "teamup_dispatch.db":
        config.DB_PATH = "demo.db"
    print("[startup] no TEAMUP_API_KEY next to the app — showing DEMO data. "
          "Add credentials to teamup-config.txt to go live.")

from app.main import app  # noqa: E402  (config is mutated above first)


def _open_when_ready() -> None:
    """Wait for the port to accept connections (up to ~60s), then open the
    default browser. Runs in a background thread so it doesn't block the server."""
    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    webbrowser.open(URL)


def main() -> None:
    import uvicorn

    print()
    print("  ================================================")
    print(f"   Teamup Dispatch Map  ->  {URL}")
    print("   Close this window to stop.")
    print("  ================================================")
    print()

    threading.Thread(target=_open_when_ready, daemon=True).start()

    # Pass the app OBJECT (not the "app.main:app" import string) and run a single
    # process: reload/workers would re-import app.main (fragile when frozen) and
    # would duplicate the in-process poller + SSE bus. host is localhost-only by
    # design — the same fail-closed posture as the launchers.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
