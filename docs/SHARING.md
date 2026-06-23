# Sharing the app as a double-click Windows .exe

Goal: hand someone (e.g. the scheduler) a folder they can run with **nothing
installed** — no Python, no terminal. They double-click `TeamupDispatch.exe`, a
browser opens, the live map appears.

This is built by `run_app.py` (the frozen entrypoint) + PyInstaller, on a
Windows CI runner (`.github/workflows/build-windows.yml`). PyInstaller can't
cross-compile, so the `.exe` must be built on Windows — hence CI rather than the
Linux laptop.

## One-time build → send

1. **Push** the branch with `run_app.py`, the workflow, and `packaging/` to
   GitHub `main`. The workflow runs automatically (or trigger it: Actions tab →
   "Build Windows app" → Run workflow).
2. When it's green, open the run and download the **`TeamupDispatch-windows`**
   artifact. Unzip it → a `TeamupDispatch/` folder containing:
   - `TeamupDispatch.exe`
   - `teamup-config-EXAMPLE.txt`
   - `READ ME FIRST.txt`
3. **Add the credentials.** Copy `teamup-config-EXAMPLE.txt` to
   `teamup-config.txt` (same folder) and fill in `TEAMUP_API_KEY` +
   `TEAMUP_CALENDAR_ID`. A **read-only** key is enough for the map.
   - *Optional, recommended:* also drop a warm `teamup_dispatch.db` next to the
     exe (copy your own) so their first launch is instant and fully geocoded
     instead of re-geocoding the whole calendar at ~1/sec on first run.
4. **Zip the folder and send it.** They unzip, double-click the exe, click
   through SmartScreen ("More info" → "Run anyway"), and they're live.

The exe reads `teamup-config.txt` (or `.env`) **from its own folder** and writes
its SQLite cache there too, so everything stays self-contained in that folder and
survives restarts. No credentials live in this repo or in CI.

## Rotating the key later

Edit `teamup-config.txt` in their folder (or send a new one) — **no rebuild
needed.** Only changes to the code require a new CI build.

## Notes / gotchas

- **SmartScreen**: the exe is unsigned, so Windows warns once. Tell the
  recipient up front (the READ ME FIRST covers it). Code-signing needs a cert —
  out of scope for an internal hand-off.
- **Antivirus**: one-file PyInstaller exes occasionally trip AV heuristics. If a
  machine's AV quarantines it, allow-list the file. (A `--onedir` build trips AV
  less but is a folder, not a single file — switch the workflow's `--onefile` to
  `--onedir` if this bites.)
- **Updating the app**: re-run the workflow, send the new exe. The recipient
  keeps their `teamup-config.txt` and `teamup_dispatch.db`.

## Alternative: bake the key into the exe (zero files for them)

If you'd rather the recipient get a *single* `.exe` with no config file at all:
add `TEAMUP_API_KEY` / `TEAMUP_CALENDAR_ID` as GitHub Actions **repository
secrets**, and have the workflow write them into a bundled config before the
PyInstaller step (`--add-data`). Trade-offs: the key is then embedded in the
distributed binary (extractable — fine for an internal, read-only key), and
rotating it requires a rebuild. The sidecar-file default above avoids both.
