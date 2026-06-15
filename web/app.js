"use strict";

// ---- map ----
const map = L.map("map").setView([37.7799, -122.4194], 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
const markerLayer = L.layerGroup().addTo(map);

// ---- state ----
let subcalendars = {};       // id -> {name, color}
let selected = new Set();    // selected subcalendar ids
let subsInitialized = false; // first load selects all; later refreshes preserve choices
const PALETTE = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#46f0f0",
                 "#f032e6","#bcf60c","#fabebe","#008080","#9a6324","#800000"];
let firstFit = true;

function colorFor(id, idx) {
  const c = subcalendars[id] && subcalendars[id].color;
  if (c && /^#/.test(c)) return c;
  return PALETTE[idx % PALETTE.length];
}

// ---- time window ----
function windowRange() {
  const sel = document.getElementById("window").value;
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()); // local midnight
  let end = null;
  if (sel === "today") end = new Date(start.getTime() + 864e5);
  else if (sel === "3d") end = new Date(start.getTime() + 3 * 864e5);
  else if (sel === "week") end = new Date(start.getTime() + 7 * 864e5);
  else if (sel === "30d") end = new Date(start.getTime() + 30 * 864e5);
  else return { from: null, to: null };
  return { from: start.toISOString(), to: end.toISOString() };
}

// ---- sub-calendars (legend + filter) ----
async function loadSubcalendars() {
  const r = await fetch("/api/subcalendars");
  const data = await r.json();
  const box = document.getElementById("subcalendars");
  box.innerHTML = "";
  data.subcalendars.forEach((s, idx) => {
    const isNew = !(s.id in subcalendars);
    subcalendars[s.id] = { name: s.name, color: s.color };
    // select all on first load; on later refreshes keep the user's choices but
    // default any newly-appeared sub-calendar to visible
    if (!subsInitialized || isNew) selected.add(s.id);
    const color = colorFor(s.id, idx);
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

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

function render(events) {
  markerLayer.clearLayers();
  const list = document.getElementById("event-list");
  const unmapped = document.getElementById("unmapped-list");
  list.innerHTML = "";
  unmapped.innerHTML = "";
  const bounds = [];
  let mapped = 0;
  const idxOf = {};
  Object.keys(subcalendars).forEach((id, i) => (idxOf[id] = i));

  events
    .sort((a, b) => (a.start_dt || "").localeCompare(b.start_dt || ""))
    .forEach((e) => {
      const sid = e.subcalendar_id || (e.subcalendar_ids && e.subcalendar_ids[0]);
      const color = colorFor(sid, idxOf[sid] || 0);
      const when = fmtTime(e.start_dt);

      if (e.lat != null && e.lng != null) {
        mapped++;
        bounds.push([e.lat, e.lng]);
        const m = L.circleMarker([e.lat, e.lng], {
          radius: 11, color: "#ffffff", weight: 3, fillColor: color, fillOpacity: 0.95,
          className: "job-dot",
        }).addTo(markerLayer);

        // pill: customer name + appointment time, accent inherits calendar color.
        // "name" comes from the Teamup `who` field (fall back to title); swap to a
        // custom field here if your customer name lives elsewhere.
        const name = e.who || e.title || "(no name)";
        m.bindTooltip(
          `<span class="accent" style="background:${color};--c:${color}"></span>` +
          `<span class="who">${esc(name)}</span>` +
          `<span class="time">${when}</span>`,
          { permanent: true, direction: "right", offset: [12, 0],
            className: "job-pill", interactive: false }
        );

        m.bindPopup(
          `<div class="pt">${esc(e.title)}</div>` +
          (e.who ? `<div class="pm">${esc(e.who)}</div>` : "") +
          `<div class="pm">${when}</div>` +
          `<div class="pm">${esc(e.location || "")}</div>`
        );
        const li = document.createElement("li");
        li.style.borderLeftColor = color;
        li.innerHTML = `<div class="t">${esc(e.title)}</div>` +
          `<div class="m">${when}${e.who ? " · " + esc(e.who) : ""}</div>`;
        li.addEventListener("click", () => { map.setView([e.lat, e.lng], 15); m.openPopup(); });
        list.appendChild(li);
      } else {
        const li = document.createElement("li");
        const reason = !e.location ? "no address" :
          e.geo_status === "pending" ? "geocoding…" :
          e.geo_status === "notfound" ? "address not found" : "geocode error";
        li.textContent = `${e.title} — ${reason}`;
        unmapped.appendChild(li);
      }
    });

  document.getElementById("count").textContent = `(${mapped} mapped)`;
  const uw = document.getElementById("unmapped-wrap");
  const uc = unmapped.children.length;
  document.getElementById("unmapped-count").textContent = `(${uc})`;
  uw.classList.toggle("hidden", uc === 0);

  if (bounds.length && firstFit) {
    map.fitBounds(bounds, { padding: [40, 40] });
    firstFit = false;
  }
  stamp();
}

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function stamp() {
  document.getElementById("status").textContent = "updated " + new Date().toLocaleTimeString();
}

// ---- live updates ----
function connectStream() {
  const es = new EventSource("/api/stream");
  const dot = document.getElementById("live");
  es.onopen = () => dot.classList.add("on");
  es.onerror = () => dot.classList.remove("on");
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "refresh") { loadSubcalendars().then(loadEvents); }
    } catch (_) {}
  };
}

// ---- wiring ----
document.getElementById("window").addEventListener("change", loadEvents);
document.getElementById("toggle-all").addEventListener("click", () => {
  const ids = Object.keys(subcalendars).map(Number);
  const allOn = ids.every((id) => selected.has(id));
  selected = new Set(allOn ? [] : ids);
  document.querySelectorAll("#subcalendars input").forEach((cb) => {
    cb.checked = selected.has(+cb.dataset.id);
  });
  loadEvents();
});

(async function init() {
  await loadSubcalendars();
  await loadEvents();
  connectStream();
})();
