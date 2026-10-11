/* Stub właściciela (/share/schedule, /share/stats) — tylko GET, zero sterowania. */
"use strict";

const DEFAULT_QUEUE = "default";

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toMin(hhmm) {
  if (!hhmm || typeof hhmm !== "string") return 0;
  return Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
}

function fmtDur(s) {
  s = Math.max(0, Number(s) || 0);
  if (s < 90) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  return (Math.round((s / 3600) * 10) / 10) + " h";
}

function counterOf(map, key) {
  return Number((map || {})[key] || 0) || 0;
}

function fmtUsd(n) {
  const v = Number(n) || 0;
  if (!v) return "0";
  return "$" + v.toFixed(v < 0.01 ? 4 : 2);
}

async function api(path) {
  const r = await fetch(path);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail || r.statusText || r.status;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function fillSelect(sel, values, preferred) {
  if (!sel) return;
  const current = [...sel.options].map((o) => o.value).filter(Boolean);
  const same = current.length === values.length && current.every((v, i) => v === values[i]);
  if (same) {
    if (preferred && values.includes(preferred)) sel.value = preferred;
    return;
  }
  const prev = sel.value;
  sel.innerHTML = values.length
    ? values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("")
    : `<option value="">—</option>`;
  sel.value = values.includes(prev) ? prev
    : (preferred && values.includes(preferred) ? preferred : (values[0] || ""));
}

function showBanner(id, text, kind) {
  const el = $(id);
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.className = "banner" + (kind ? " " + kind : "");
  el.textContent = text;
}

/** schedule | stats — ścieżka /share/... albo ?view= */
function currentShareView() {
  const q = (new URLSearchParams(location.search).get("view") || "").toLowerCase();
  if (q === "stats") return "stats";
  if (q === "schedule") return "schedule";
  if (location.pathname.indexOf("/share/stats") === 0) return "stats";
  return "schedule";
}

function applyShareView() {
  const view = currentShareView();
  document.querySelectorAll("main .view").forEach((el) => {
    el.hidden = el.dataset.view !== view;
  });
  document.querySelectorAll("#share-nav a[data-view]").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === view);
  });
  if (view === "stats") refreshShareStats().catch(() => {});
  else refreshShareWeek().catch(() => {});
}

function renderKpi(box, agg, live) {
  const c = (agg && agg.counters) || {};
  const running = !!(live && live.session_running);
  const cost = (agg && agg.ai && agg.ai.cost_usd) || 0;
  const items = [
    { k: "Sesje (24h)", v: String((agg && agg.sessions_count) || 0) },
    { k: "Polubienia", v: String(counterOf(c, "liked")) },
    { k: "Obejrzane", v: String(counterOf(c, "watched")) },
    { k: "Wizyty", v: String(counterOf(c, "visited")) },
    { k: "Obserwacje", v: String(counterOf(c, "followed")) },
    { k: "Koszt AI", v: fmtUsd(cost) },
    { k: "Live", v: running ? "jedzie" : "cisza", cls: running ? "ok" : "dim" },
  ];
  box.innerHTML = items.map((it) => `
    <article class="kpi-card">
      <div class="k">${escapeHtml(it.k)}</div>
      <div class="v${it.cls ? " " + it.cls : ""}">${escapeHtml(it.v)}</div>
    </article>`).join("");
}

function renderWeek(cycle) {
  const week = $("sh-week");
  const days = (cycle && cycle.days) || [];
  if (!days.length) {
    week.innerHTML = `<div class="empty">Pusty wzorzec — brak bloków na ten tydzień.</div>`;
    return;
  }
  const dayHtml = days.map((day) => {
    const blocks = (day.blocks || []).slice()
      .sort((a, b) => toMin(a.start) - toMin(b.start));
    const isToday = cycle.today_index === day.index;
    const label = day.label || `Dzień ${day.index + 1}`;
    const blockHtml = blocks.length
      ? blocks.map((b) => {
          const endMin = toMin(b.start) + (b.duration_s || 0) / 60;
          const hh = String(Math.floor(endMin / 60) % 24).padStart(2, "0");
          const mm = String(Math.round(endMin % 60)).padStart(2, "0");
          const lock = b.locked ? " · przypięty" : "";
          return `<div class="tl-block">
            <div class="when">${escapeHtml(b.start)}–${hh}:${mm} · ${escapeHtml(fmtDur(b.duration_s))}${lock}</div>
            <div class="who">${escapeHtml(b.account)} × ${escapeHtml(b.scenario || DEFAULT_QUEUE)}</div>
          </div>`;
        }).join("")
      : `<div class="tl-empty">Brak bloków.</div>`;
    return `<article class="tl-day${isToday ? " today" : ""}">
      <h2>${escapeHtml(label)}</h2>
      <div class="sub">${blocks.length} blok(ów)${isToday ? " · dziś" : ""}</div>
      ${blockHtml}
    </article>`;
  });
  week.innerHTML = dayHtml.join("");
}

async function refreshShareWeek() {
  const sel = $("sh-device");
  const week = $("sh-week");
  try {
    const list = await api("/api/timeline-devices").catch(() => []);
    const names = (Array.isArray(list) ? list : []).map((d) => d.device);
    fillSelect(sel, names, sel && sel.value);
    const dev = sel && sel.value;
    if (!dev) {
      week.innerHTML = `<div class="empty">Brak urządzeń w planie.</div>`;
      showBanner("sh-tl-banner", "");
      return;
    }
    const cycle = await api(`/api/timeline/${encodeURIComponent(dev)}`);
    renderWeek(cycle);
    showBanner("sh-tl-banner", "");
  } catch (e) {
    showBanner("sh-tl-banner", "Nie udało się wczytać osi: " + (e.message || e), "err");
  }
}

async function refreshShareStats() {
  const sel = $("sh-account");
  const kpi = $("sh-kpi");
  try {
    const accs = await api("/api/accounts");
    const names = (Array.isArray(accs) ? accs : []).map((a) =>
      typeof a === "string" ? a : a.alias).filter(Boolean);
    fillSelect(sel, names, sel && sel.value);
    const acc = sel && sel.value;
    if (!acc) {
      kpi.innerHTML = `<div class="empty">Brak kont.</div>`;
      showBanner("sh-st-banner", "");
      return;
    }
    const [live, agg] = await Promise.all([
      api(`/api/stats/${encodeURIComponent(acc)}`),
      api(`/api/stats/${encodeURIComponent(acc)}/aggregate?range=24h`),
    ]);
    renderKpi(kpi, agg, live);
    showBanner("sh-st-banner", "");
  } catch (e) {
    showBanner("sh-st-banner", "Nie udało się wczytać liczb: " + (e.message || e), "err");
  }
}

const devSel = $("sh-device");
if (devSel) {
  devSel.addEventListener("change", () => refreshShareWeek().catch(() => {}));
}
const accSel = $("sh-account");
if (accSel) {
  accSel.addEventListener("change", () => refreshShareStats().catch(() => {}));
}

applyShareView();
