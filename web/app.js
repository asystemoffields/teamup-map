"use strict";

// ---- map ----
const map = L.map("map").setView([37.7799, -122.4194], 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);     // the connecting line (under markers)
const markerLayer = L.featureGroup().addTo(map);  // job pins

// ---- state ----
let currentCal = null;          // active calendar key (cal1, cal2, …) or null = default
let subcalendars = {};          // id -> {name, color(hex)}
let subIndex = {};              // id -> position (palette fallback)
let selected = new Set();       // selected sub-calendar ids
let subsInitialized = false;
let colorEnabled = new Set();   // enabled hex colors (color filter)
let knownColors = new Set();    // colors we've seen (so new ones default to visible)
let crewEnabled = new Set();    // enabled crew names (crew filter)
let knownCrews = new Set();     // crews we've seen (so new ones default to visible)
let crewColors = {};            // crew name -> hex (used when coloring pins by crew)
let colorMode = "status";       // "status" (sub-calendar color) | "crew"
let routeMode = false;
let prospective = null;         // {address,name,time,lat,lng,status}
let lastEvents = [];            // last server-filtered events (re-render without refetch)
let firstFit = true;
let routeReq = 0;               // token to ignore stale /api/route responses
let weatherByEvent = {};        // event id -> NWS weather assessment (from /api/weather)
let weatherEnabled = true;      // "Weather warnings" toggle
let weatherReq = 0;             // token to ignore stale /api/weather responses

const PALETTE = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#46f0f0",
                 "#f032e6","#bcf60c","#fabebe","#008080","#9a6324","#800000"];
const PROSPECTIVE_COLOR = "#e8590c";

function colorFor(id) {
  const c = subcalendars[id] && subcalendars[id].color;
  // strict hex only — the value is interpolated into inline style="…"; anything
  // else falls back to the palette (closes any attribute-injection via color)
  if (c && /^#[0-9a-fA-F]{3,8}$/.test(c)) return c;
  return PALETTE[(subIndex[id] || 0) % PALETTE.length];
}

// crew = the text before the first " - ", " / ", or " : " in the title
// (e.g. "Will - Durling / TC / Roofing" -> "Will", "Will: Coates" -> "Will").
// Empty when no delimiter (most time-off/admin entries) -> treated as uncrewed.
function crewOf(title) {
  const m = (title || "").match(/^\s*([^/\-:]+?)\s*[-/:]/);
  return m ? m[1].replace(/\s+/g, " ").trim() : "";
}
function crewColorOf(crew) {
  if (!crew) return "#9aa3af";  // uncrewed -> neutral grey
  if (!(crew in crewColors)) crewColors[crew] = PALETTE[Object.keys(crewColors).length % PALETTE.length];
  return crewColors[crew];
}
// the color a pin/pill should use, honoring the "Color pins by" selector
function pinColor(e, sid) {
  return colorMode === "crew" ? crewColorOf(crewOf(e.title)) : colorFor(sid);
}

// ---- small helpers ----
function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}
function fmtClock(d) { return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function fmtDist(m) { return m == null ? "" : (m / 1609.344).toFixed(1) + " mi"; }
function fmtDur(s) { return s == null ? "" : Math.round(s / 60) + " min"; }
function havM(a, b) {
  const R = 6371000, toR = (x) => (x * Math.PI) / 180;
  const dLat = toR(b[0] - a[0]), dLng = toR(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a[0])) * Math.cos(toR(b[0])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function stamp() { document.getElementById("status").textContent = "updated " + new Date().toLocaleTimeString(); }

// ---- time window ----
// local wall-clock ISO (NO timezone Z). Event start/end are wall-clock strings,
// so the window bounds must be wall-clock too — using toISOString() (UTC) here
// shifted the whole window by the local offset and hid/leaked morning jobs.
function localISO(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function windowRange() {
  const sel = document.getElementById("window").value;
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (sel === "all") return { from: null, to: null };
  if (sel === "date") {
    // a single chosen calendar day (00:00 of that day -> 00:00 the next day)
    const v = document.getElementById("date-pick").value;
    if (!v) return { from: null, to: null };
    const d = new Date(v + "T00:00:00");
    return { from: localISO(d), to: localISO(new Date(d.getTime() + 864e5)) };
  }
  const days = { today: 1, week: 7, "30d": 30 }[sel] || 7;
  return { from: localISO(start), to: localISO(new Date(start.getTime() + days * 864e5)) };
}

// ---- markers ----
function jobIcon(color, number, prospectiveFlag, wx) {
  const cls = "job-marker" + (prospectiveFlag ? " prospective" : "") + (number != null ? " numbered" : "");
  const inner = number != null ? number : (prospectiveFlag ? "?" : "");
  const badge = wx && (wx.severity === "red" || wx.severity === "yellow")
    ? `<div class="wx-badge wx-${wx.severity}">${esc(wx.glyph || "⚠")}</div>` : "";
  return L.divIcon({
    className: "",
    html: `<div class="${cls}" style="--c:${color}">${inner}${badge}</div>`,
    iconSize: [24, 24], iconAnchor: [12, 12],
  });
}

// the weather block appended to a job's popup (forecast for its time window)
function weatherPopupHtml(e) {
  if (!e || !weatherEnabled) return "";
  const wx = weatherByEvent[e.id];
  if (!wx) return "";
  const parts = [];
  if (wx.short) parts.push(esc(wx.short));
  if (wx.temp != null) parts.push(esc(wx.temp + "°" + (wx.unit || "")));
  if (wx.wind) parts.push(esc(wx.wind));
  if (wx.pop != null) parts.push(esc(wx.pop + "% precip"));
  const alertLine = (wx.alerts && wx.alerts.length)
    ? wx.alerts.map((a) => `<b>${esc(a.event)}</b>`).join(", ") + "<br>" : "";
  return `<div class="pw wx-${wx.severity}">` +
    `<span class="pw-g">${esc(wx.glyph || (wx.severity === "green" ? "☀" : "⚠"))}</span>` +
    alertLine + parts.join(" · ") + `</div>`;
}

// ---- calendars (top-level switcher between separate Teamup calendars) ----
function calParam() { return currentCal ? "cal=" + encodeURIComponent(currentCal) + "&" : ""; }

async function loadCalendars() {
  // Retry a few times: a single transient failure on this (the very first) call
  // must not permanently hide the calendar switcher. cache:no-store so a stale
  // copy can't answer either.
  let data = null;
  for (let attempt = 0; attempt < 6 && !data; attempt++) {
    try {
      const r = await fetch("/api/calendars", { cache: "no-store" });
      if (r.ok) data = await r.json();
    } catch (e) { /* retry */ }
    if (!data) await new Promise((res) => setTimeout(res, 400));
  }
  if (!data) return; // truly unreachable: fall back to single-calendar view
  const cals = data.calendars || [];
  currentCal = data.default || (cals[0] && cals[0].key) || null;
  const sel = document.getElementById("calendar");
  sel.innerHTML = "";
  cals.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.key; opt.textContent = c.name;
    if (c.key === currentCal) opt.selected = true;
    sel.appendChild(opt);
  });
  // only surface the switcher when there's actually more than one calendar
  document.getElementById("calendar-control").classList.toggle("hidden", cals.length < 2);
  sel.onchange = (e) => switchCalendar(e.target.value);
}

// swap the whole view to another calendar: reset every per-calendar filter so
// the new calendar's sub-calendars/crews/colors start fresh, then reload.
async function switchCalendar(key) {
  if (key === currentCal) return;
  currentCal = key;
  subcalendars = {}; subIndex = {}; selected = new Set(); subsInitialized = false;
  colorEnabled = new Set(); knownColors = new Set();
  crewEnabled = new Set(); knownCrews = new Set(); crewColors = {};
  lastEvents = []; firstFit = true; prospective = null;
  weatherByEvent = {}; weatherReq++;   // drop the previous calendar's weather
  await loadSubcalendars();
  await loadEvents();
}

// ---- sub-calendars (filter + legend) + color filter ----
async function loadSubcalendars() {
  let data;
  try {
    const r = await fetch("/api/subcalendars?" + calParam());
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
  } catch (e) {
    document.getElementById("status").textContent = "can't reach server — retrying…";
    return;
  }
  const box = document.getElementById("subcalendars");
  box.innerHTML = "";
  data.subcalendars.forEach((s, idx) => {
    const isNew = !(s.id in subcalendars);
    subcalendars[s.id] = { name: s.name, color: s.color };
    subIndex[s.id] = idx;
    if (!subsInitialized || isNew) selected.add(s.id);
    const color = colorFor(s.id);
    const label = document.createElement("label");
    label.className = "sub";
    label.innerHTML =
      `<input type="checkbox" data-id="${s.id}" ${selected.has(s.id) ? "checked" : ""}>` +
      `<span class="swatch" style="background:${color}"></span>${esc(s.name || "(unnamed)")}`;
    label.querySelector("input").addEventListener("change", (e) => {
      const id = +e.target.dataset.id;
      e.target.checked ? selected.add(id) : selected.delete(id);
      loadEvents();
    });
    box.appendChild(label);
  });
  subsInitialized = true;
  buildColorFilter();
}

function distinctColors() {
  // the colors actually on the map right now, so "filter by color" always
  // matches what you see (status colors, or crew colors in crew mode)
  if (colorMode === "crew") return [...new Set([...knownCrews].map((c) => crewColorOf(c)))];
  return [...new Set(Object.keys(subcalendars).map((id) => colorFor(id)))];
}

// ---- crew filter (parsed from titles; mapped jobs only) ----
function crewsInEvents(events) {
  const s = new Set();
  events.forEach((e) => {
    if (e.lat != null && e.lng != null) { const c = crewOf(e.title); if (c) s.add(c); }
  });
  return [...s].sort((a, b) => a.localeCompare(b));
}

function buildCrewFilter(events) {
  const crews = crewsInEvents(events);
  // a newly-seen crew defaults to visible (mirrors sub-calendar / color filters)
  crews.forEach((c) => {
    if (!knownCrews.has(c)) { knownCrews.add(c); crewEnabled.add(c); }
    crewColorOf(c); // assign a stable color now (alpha order) for crew-color mode
  });
  const box = document.getElementById("crew-filter");
  box.innerHTML = "";
  if (!crews.length) { box.innerHTML = '<span class="muted">no crews detected</span>'; return; }
  crews.forEach((c) => {
    const label = document.createElement("label");
    label.className = "sub";
    const sw = colorMode === "crew"
      ? `<span class="swatch" style="background:${crewColorOf(c)}"></span>` : "";
    label.innerHTML = `<input type="checkbox" ${crewEnabled.has(c) ? "checked" : ""}>` + sw + esc(c);
    label.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? crewEnabled.add(c) : crewEnabled.delete(c);
      rerender();
    });
    box.appendChild(label);
  });
}

function buildColorFilter() {
  const box = document.getElementById("color-filter");
  box.innerHTML = "";
  const colors = distinctColors();
  // any colour we haven't seen before defaults to visible (mirrors how a new
  // sub-calendar auto-selects); a user-disabled colour stays disabled on refresh
  colors.forEach((c) => { if (!knownColors.has(c)) { knownColors.add(c); colorEnabled.add(c); } });
  colors.forEach((c) => {
    const chip = document.createElement("button");
    chip.className = "chip" + (colorEnabled.has(c) ? "" : " off");
    chip.style.background = c;
    chip.title = c;
    chip.addEventListener("click", () => {
      colorEnabled.has(c) ? colorEnabled.delete(c) : colorEnabled.add(c);
      chip.classList.toggle("off");
      rerender();
    });
    box.appendChild(chip);
  });
}

// ---- events ----
async function loadEvents() {
  // all sub-calendars deselected = show nothing (not "no filter = everything")
  if (subsInitialized && selected.size === 0) { render([]); return; }
  const { from, to } = windowRange();
  const params = new URLSearchParams();
  if (currentCal) params.set("cal", currentCal);
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (selected.size) params.set("subcalendars", [...selected].join(","));
  let data;
  try {
    const r = await fetch("/api/events?" + params.toString());
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
  } catch (e) {
    document.getElementById("status").textContent = "can't reach server — retrying…";
    return;
  }
  const events = data.events || [];
  buildCrewFilter(events);   // refresh crew chips from the current data
  render(events);
  if (weatherEnabled) loadWeather();  // async overlay; map already rendered
}

// Fetch per-job weather for the same window/calendar and repaint badges. Runs
// after the map is already drawn, so a slow NWS lookup never delays the map; a
// failure is swallowed (weather is best-effort). Uses the same server-side
// filters as /api/events so ids line up.
async function loadWeather() {
  const req = ++weatherReq;
  const { from, to } = windowRange();
  const params = new URLSearchParams();
  if (currentCal) params.set("cal", currentCal);
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (selected.size) params.set("subcalendars", [...selected].join(","));
  try {
    const r = await fetch("/api/weather?" + params.toString());
    if (!r.ok) return;
    const data = await r.json();
    if (req !== weatherReq) return;        // a newer load superseded this one
    weatherByEvent = data.weather || {};
    rerender();
  } catch (e) { /* best-effort: leave the map as-is */ }
}

function rerender() { render(lastEvents); }

// insertion of the prospective job into the time-sorted job list
function detour(jobs, i, p) {
  const prev = i > 0 ? [jobs[i - 1].lat, jobs[i - 1].lng] : null;
  const next = i < jobs.length ? [jobs[i].lat, jobs[i].lng] : null;
  if (prev && next) return havM(prev, p) + havM(p, next) - havM(prev, next);
  if (prev) return havM(prev, p);
  if (next) return havM(p, next);
  return 0;
}
function computeInsert(jobs, p) {
  const pll = [p.lat, p.lng];
  if (p.time) {
    const base = jobs.length ? new Date(jobs[0].time) : new Date();
    const [hh, mm] = p.time.split(":").map(Number);
    const pdt = new Date(base); pdt.setHours(hh, mm, 0, 0);
    let index = 0;
    for (const j of jobs) { if (new Date(j.time).getTime() <= pdt.getTime()) index++; else break; }
    const added = detour(jobs, index, pll);
    return { index, suggested: false, addedM: added,
      text: `Inserted at ${fmtClock(pdt)} (stop ${index + 1}) · adds +${fmtDist(added)}` };
  }
  let best = 0, bestC = Infinity;
  for (let i = 0; i <= jobs.length; i++) { const c = detour(jobs, i, pll); if (c < bestC) { bestC = c; best = i; } }
  const prev = best > 0 ? jobs[best - 1] : null, next = best < jobs.length ? jobs[best] : null;
  let tTxt = "";
  if (prev && next) { const mid = (new Date(prev.time).getTime() + new Date(next.time).getTime()) / 2; tTxt = "~" + fmtClock(new Date(mid)); }
  else if (prev) tTxt = "after " + fmtClock(new Date(prev.time));
  else if (next) tTxt = "before " + fmtClock(new Date(next.time));
  return { index: best, suggested: true, addedM: bestC,
    text: `Best slot: stop ${best + 1}${tTxt ? " (" + tTxt + ")" : ""} · adds +${fmtDist(bestC)}` };
}

function render(events) {
  lastEvents = events;
  routeReq++; // invalidate any in-flight route draw from a previous render
  markerLayer.clearLayers();
  routeLayer.clearLayers();
  const list = document.getElementById("event-list");
  const unmapped = document.getElementById("unmapped-list");
  list.innerHTML = "";
  unmapped.innerHTML = "";

  const jobs = [];
  let unmappedCount = 0;
  events.forEach((e) => {
    const sid = e.subcalendar_id || (e.subcalendar_ids && e.subcalendar_ids[0]);
    const color = pinColor(e, sid);
    if (e.lat != null && e.lng != null) {
      const crew = crewOf(e.title);
      if (crew && !crewEnabled.has(crew)) return; // crew filter (uncrewed jobs always show)
      if (!colorEnabled.has(color)) return; // color filter (empty set = show nothing)
      jobs.push({ kind: "job", e, name: e.who || e.title || "", title: e.title, who: e.who,
        time: e.start_dt, lat: e.lat, lng: e.lng, color, location: e.location });
    } else {
      const reason = !e.location ? "no address" :
        e.geo_status === "pending" ? "geocoding…" :
        e.geo_status === "notfound" ? "address not found" : "geocode error";
      const li = document.createElement("li");
      li.textContent = `${e.title} — ${reason}`;
      unmapped.appendChild(li);
      unmappedCount++;
    }
  });

  jobs.sort((a, b) => (a.time || "").localeCompare(b.time || ""));

  // build ordered stops, inserting the prospective job
  let ordered = jobs.slice();
  let pInfo = null;
  if (prospective && prospective.lat != null) {
    pInfo = computeInsert(jobs, prospective);
    ordered.splice(pInfo.index, 0, {
      kind: "prospective", name: prospective.name || "Prospective", title: "Prospective job",
      time: null, lat: prospective.lat, lng: prospective.lng, color: PROSPECTIVE_COLOR,
      location: prospective.address,
    });
  }

  const bounds = [];
  ordered.forEach((s, i) => {
    bounds.push([s.lat, s.lng]);
    const num = routeMode ? i + 1 : null;
    const wx = (weatherEnabled && s.e) ? weatherByEvent[s.e.id] : null;
    const m = L.marker([s.lat, s.lng], { icon: jobIcon(s.color, num, s.kind === "prospective", wx) }).addTo(markerLayer);
    const when = fmtTime(s.time);
    const pillTime = s.kind === "prospective"
      ? (pInfo && pInfo.suggested ? "suggested" : "prospective")
      : when;
    m.bindTooltip(
      (num != null ? `<span class="seq">${num}</span>` : `<span class="accent" style="background:${s.color};--c:${s.color}"></span>`) +
      `<span class="who">${esc(s.name || "(no name)")}</span><span class="time">${pillTime}</span>`,
      { permanent: true, direction: "right", offset: [13, 0],
        className: "job-pill" + (s.kind === "prospective" ? " prospective" : ""), interactive: false });
    m.bindPopup(
      `<div class="pt">${esc(s.title)}</div>` +
      (s.who ? `<div class="pm">${esc(s.who)}</div>` : "") +
      (when ? `<div class="pm">${when}</div>` : "") +
      `<div class="pm">${esc(s.location || "")}</div>` +
      weatherPopupHtml(s.e));
    if (s.kind === "job") {
      const wxLn = (wx && wx.severity !== "green")
        ? `<span class="wx-ln" title="${esc(wx.summary || "")}">${esc(wx.glyph || "⚠")}</span>` : "";
      const li = document.createElement("li");
      li.style.borderLeftColor = s.color;
      li.innerHTML = (num != null ? `<span class="ln">${num}</span>` : "") +
        `<span class="t">${esc(s.title)}</span><span class="m">${wxLn}${when}${s.who ? " · " + esc(s.who) : ""}</span>`;
      li.addEventListener("click", () => { map.setView([s.lat, s.lng], 15); m.openPopup(); });
      list.appendChild(li);
    }
  });

  document.getElementById("count").textContent = `(${jobs.length} mapped)`;
  document.getElementById("unmapped-count").textContent = `(${unmappedCount})`;
  document.getElementById("unmapped-wrap").classList.toggle("hidden", unmappedCount === 0);

  if (routeMode && ordered.length >= 2) {
    drawRoute(ordered.map((s) => [s.lat, s.lng]));
  } else {
    document.getElementById("route-stats").textContent =
      routeMode ? "Add at least 2 mapped jobs to draw a route." : "";
  }

  updateProspectiveResult(pInfo);
  if (bounds.length && firstFit) { map.fitBounds(bounds, { padding: [50, 50] }); firstFit = false; }
  stamp();
}

// ---- route line ----
async function drawRoute(points) {
  const id = ++routeReq;
  document.getElementById("route-stats").textContent = "routing…";
  try {
    const r = await fetch("/api/route", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points }),
    });
    const data = await r.json();
    if (id !== routeReq) return; // a newer request superseded this one
    routeLayer.clearLayers();
    if (data.geometry && data.geometry.length >= 2) {
      L.polyline(data.geometry, { color: "#2b3a55", weight: 4, opacity: 0.8 }).addTo(routeLayer);
    }
    const src = data.source === "osrm" ? "driving" : data.source === "haversine" ? "straight-line" : "";
    const parts = [`${points.length} stops`];
    if (data.distance_m != null) parts.push(fmtDist(data.distance_m));
    if (data.duration_s != null) parts.push(fmtDur(data.duration_s));
    document.getElementById("route-stats").textContent = parts.join(" · ") + (src ? ` (${src})` : "");
  } catch (e) {
    if (id === routeReq) document.getElementById("route-stats").textContent = "routing failed";
  }
}

// ---- prospective job ----
function updateProspectiveResult(pInfo) {
  const el = document.getElementById("p-result");
  if (!prospective) { el.textContent = ""; return; }
  if (prospective.status === "pending") { el.textContent = "geocoding…"; return; }
  if (prospective.status && prospective.status !== "ok") {
    el.textContent = `Could not locate "${prospective.address}".`; return;
  }
  el.innerHTML = `<b>${esc(prospective.name || "Prospective")}</b> — ${esc(prospective.address)}` +
    (pInfo ? `<br>${pInfo.text}` : "");
}

async function addProspective() {
  const address = document.getElementById("p-address").value.trim();
  if (!address) { document.getElementById("p-result").textContent = "Enter an address first."; return; }
  prospective = {
    address, name: document.getElementById("p-name").value.trim(),
    time: document.getElementById("p-time").value, lat: null, lng: null, status: "pending",
  };
  updateProspectiveResult(null);
  const rt = document.getElementById("route-toggle"); rt.checked = true; routeMode = true; // show the route
  try {
    const r = await fetch("/api/geocode?address=" + encodeURIComponent(address));
    const d = await r.json();
    prospective.status = d.status; prospective.lat = d.lat; prospective.lng = d.lng;
  } catch (e) { prospective.status = "error"; }
  rerender();
  if (prospective.lat != null) {
    try { map.fitBounds(markerLayer.getBounds(), { padding: [60, 60] }); } catch (e) {}
  }
}

async function copyAddress() {
  if (!prospective) { document.getElementById("p-result").textContent = "Add a prospective job first."; return; }
  const btn = document.getElementById("p-copy");
  const flashCopied = () => { btn.textContent = "Copied!"; setTimeout(() => (btn.textContent = "Copy address"), 1200); };
  // modern API (needs a secure context: localhost is fine, plain-http LAN IP is not)
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(prospective.address);
      flashCopied();
      return;
    }
  } catch (e) { /* fall through to legacy path */ }
  // legacy fallback (works on non-secure origins like http://192.168.x.x)
  try {
    const ta = document.createElement("textarea");
    ta.value = prospective.address;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (ok) flashCopied();
    else document.getElementById("p-result").textContent = "Copy failed — select the address manually.";
  } catch (e) {
    document.getElementById("p-result").textContent = "Copy failed — select the address manually.";
  }
}

function clearProspective() {
  prospective = null;
  ["p-address", "p-time", "p-name"].forEach((id) => (document.getElementById(id).value = ""));
  rerender();
}

// ---- live updates ----
let refreshTimer = null;
function scheduleRefresh() {
  if (refreshTimer) return; // coalesce bursts of refresh events into one refetch
  // jitter so N browsers don't all hit the server at the same instant
  const delay = 150 + Math.random() * 600;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    loadSubcalendars().then(loadEvents);
  }, delay);
}
function connectStream() {
  const es = new EventSource("/api/stream");
  const dot = document.getElementById("live");
  es.onopen = () => dot.classList.add("on");
  es.onerror = () => dot.classList.remove("on");
  es.onmessage = (ev) => {
    try { if (JSON.parse(ev.data).type === "refresh") scheduleRefresh(); } catch (_) {}
  };
}

// ---- wiring ----
document.getElementById("window").addEventListener("change", (e) => {
  const dp = document.getElementById("date-pick");
  const isDate = e.target.value === "date";
  dp.classList.toggle("hidden", !isDate);
  if (isDate && !dp.value) dp.value = localISO(new Date()).slice(0, 10); // prefill today
  loadEvents();
});
document.getElementById("date-pick").addEventListener("change", loadEvents);
document.getElementById("colorby").addEventListener("change", (e) => {
  colorMode = e.target.value;
  buildColorFilter();          // chips now reflect the active color dimension
  buildCrewFilter(lastEvents); // show/hide crew swatches
  rerender();
});
document.getElementById("crew-all").addEventListener("click", () => {
  const crews = crewsInEvents(lastEvents);
  const allOn = crews.every((c) => crewEnabled.has(c));
  crews.forEach((c) => (allOn ? crewEnabled.delete(c) : crewEnabled.add(c)));
  buildCrewFilter(lastEvents);
  rerender();
});
document.getElementById("toggle-all").addEventListener("click", () => {
  const ids = Object.keys(subcalendars).map(Number);
  const allOn = ids.every((id) => selected.has(id));
  selected = new Set(allOn ? [] : ids);
  document.querySelectorAll("#subcalendars input").forEach((cb) => (cb.checked = selected.has(+cb.dataset.id)));
  loadEvents();
});
document.getElementById("colors-all").addEventListener("click", () => {
  const colors = distinctColors();
  const allOn = colors.every((c) => colorEnabled.has(c));
  colorEnabled = new Set(allOn ? [] : colors);
  buildColorFilter();
  rerender();
});
document.getElementById("route-toggle").addEventListener("change", (e) => { routeMode = e.target.checked; rerender(); });
document.getElementById("weather-toggle").addEventListener("change", (e) => {
  weatherEnabled = e.target.checked;
  if (weatherEnabled) loadWeather();
  else { weatherByEvent = {}; weatherReq++; rerender(); }  // clear badges + ignore in-flight
});
document.getElementById("p-add").addEventListener("click", addProspective);
document.getElementById("p-copy").addEventListener("click", copyAddress);
document.getElementById("p-clear").addEventListener("click", clearProspective);
// pressing Enter in any prospective field adds the job (not just clicking the button)
["p-address", "p-time", "p-name"].forEach((id) =>
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addProspective(); }
  }));

(async function init() {
  await loadCalendars();
  await loadSubcalendars();
  await loadEvents();
  connectStream();
})();
