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
let subcalendars = {};          // id -> {name, color(hex)}
let subIndex = {};              // id -> position (palette fallback)
let selected = new Set();       // selected sub-calendar ids
let subsInitialized = false;
let colorEnabled = new Set();   // enabled hex colors (color filter)
let colorsInitialized = false;
let routeMode = false;
let prospective = null;         // {address,name,time,lat,lng,status}
let lastEvents = [];            // last server-filtered events (re-render without refetch)
let firstFit = true;

const PALETTE = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#46f0f0",
                 "#f032e6","#bcf60c","#fabebe","#008080","#9a6324","#800000"];
const PROSPECTIVE_COLOR = "#e8590c";

function colorFor(id) {
  const c = subcalendars[id] && subcalendars[id].color;
  if (c && /^#/.test(c)) return c;
  return PALETTE[(subIndex[id] || 0) % PALETTE.length];
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
function windowRange() {
  const sel = document.getElementById("window").value;
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (sel === "all") return { from: null, to: null };
  const days = { today: 1, "3d": 3, week: 7, "30d": 30 }[sel] || 7;
  return { from: start.toISOString(), to: new Date(start.getTime() + days * 864e5).toISOString() };
}

// ---- markers ----
function jobIcon(color, number, prospectiveFlag) {
  const cls = "job-marker" + (prospectiveFlag ? " prospective" : "") + (number != null ? " numbered" : "");
  const inner = number != null ? number : (prospectiveFlag ? "?" : "");
  return L.divIcon({
    className: "",
    html: `<div class="${cls}" style="--c:${color}">${inner}</div>`,
    iconSize: [24, 24], iconAnchor: [12, 12],
  });
}

// ---- sub-calendars (filter + legend) + color filter ----
async function loadSubcalendars() {
  const r = await fetch("/api/subcalendars");
  const data = await r.json();
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
      `<span class="swatch" style="background:${color}"></span>${s.name || "(unnamed)"}`;
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
  return [...new Set(Object.keys(subcalendars).map((id) => colorFor(id)))];
}

function buildColorFilter() {
  const box = document.getElementById("color-filter");
  box.innerHTML = "";
  const colors = distinctColors();
  if (!colorsInitialized) { colors.forEach((c) => colorEnabled.add(c)); colorsInitialized = true; }
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
  const { from, to } = windowRange();
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (selected.size) params.set("subcalendars", [...selected].join(","));
  const r = await fetch("/api/events?" + params.toString());
  const data = await r.json();
  render(data.events || []);
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
    const color = colorFor(sid);
    if (e.lat != null && e.lng != null) {
      if (colorEnabled.size && !colorEnabled.has(color)) return; // color filter
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
    const m = L.marker([s.lat, s.lng], { icon: jobIcon(s.color, num, s.kind === "prospective") }).addTo(markerLayer);
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
      `<div class="pm">${esc(s.location || "")}</div>`);
    if (s.kind === "job") {
      const li = document.createElement("li");
      li.style.borderLeftColor = s.color;
      li.innerHTML = (num != null ? `<span class="ln">${num}</span>` : "") +
        `<span class="t">${esc(s.title)}</span><span class="m">${when}${s.who ? " · " + esc(s.who) : ""}</span>`;
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
let routeReq = 0;
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
function connectStream() {
  const es = new EventSource("/api/stream");
  const dot = document.getElementById("live");
  es.onopen = () => dot.classList.add("on");
  es.onerror = () => dot.classList.remove("on");
  es.onmessage = (ev) => {
    try { if (JSON.parse(ev.data).type === "refresh") loadSubcalendars().then(loadEvents); } catch (_) {}
  };
}

// ---- wiring ----
document.getElementById("window").addEventListener("change", loadEvents);
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
document.getElementById("p-add").addEventListener("click", addProspective);
document.getElementById("p-copy").addEventListener("click", copyAddress);
document.getElementById("p-clear").addEventListener("click", clearProspective);
// pressing Enter in any prospective field adds the job (not just clicking the button)
["p-address", "p-time", "p-name"].forEach((id) =>
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addProspective(); }
  }));

(async function init() {
  await loadSubcalendars();
  await loadEvents();
  connectStream();
})();
