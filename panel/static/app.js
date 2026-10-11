/* Pulpit operatora — te same API co klasyczny panel (devices, status, run-queue, stop). */
"use strict";

const DEFAULT_QUEUE = "default";
const POLL_MS = 3000;
const PREVIEW_MS = 2500;
const LIVE_PREVIEW_MS = 1000;   // fallback 1 Hz gdy brak fps urządzenia
const PREVIEW_UI_FPS_MIN = 1;   // JPEG/poll — 60 stopiłby dysk
const PREVIEW_UI_FPS_MAX = 15;
const SCRCPY_FPS_MIN = 1;
const SCRCPY_FPS_MAX = 60;
const LIVE_STATS_MS = 2000;
const LIVE_LOG_MAX = 120;
const LIVE_PREVIEW_KEY = "__live__";
const LIVE_FOCUS_KEY = "bot_v2_live_focus";  // sessionStorage: focus Na żywo
const VIEWS = [
  "pulpit", "harmonogram", "runy", "tresci", "lejek", "na-zywo",
  "statystyki", "konta", "flota", "ustawienia",
];
// Aliasy hashy: stare nazwy + warianty Na żywo + ogonek #treści. Redirect URL tylko dla konto/system.
const VIEW_ALIASES = {
  nazywo: "na-zywo",
  sesja: "na-zywo",
  konto: "konta",
  system: "flota",
  "treści": "tresci",
};
const VIEW_REDIRECTS = { konto: "konta", system: "flota" };
const VIEW_LABELS = {
  pulpit: "Pulpit",
  harmonogram: "Harmonogram",
  runy: "Runy",
  tresci: "Treści",
  lejek: "Lejek",
  "na-zywo": "Na żywo",
  statystyki: "Statystyki",
  konta: "Konta",
  flota: "Flota",
  ustawienia: "Ustawienia",
};

const $ = (id) => document.getElementById(id);

// Stan z API — odświeżany w pętli pulpitu
let devices = [];
let adbRows = [];
let accounts = [];
let status = {};
let schedulerOn = false;
let timelines = {};          // alias urządzenia → GET /api/timeline/{dev}
let funnelPending = 0;       // suma pending z /api/profile-filter/{acc}/status
let alertCount = 0;          // stub / złożenie; brak /api/alerts jest OK
let liveFocusDevice = "";    // preselect z Pulpitu → #na-zywo
let pulpitFocusDevice = "";  // kliknięta karta = oś „dziś”
const presetsCache = {};     // alias konta → nazwy kolejek
const previewUrls = {};      // alias → object URL klatki
const visibleCards = new Set();

const io = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const alias = e.target.dataset.alias;
    if (!alias) continue;
    if (e.isIntersecting) visibleCards.add(alias);
    else visibleCards.delete(alias);
  }
}, { threshold: 0.2, rootMargin: "40px" });

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function deepClone(o) {
  return JSON.parse(JSON.stringify(o ?? {}));
}

/** Głęboki merge — override wygrywa, tablice zastępują (jak store.resolve_tasks). */
function deepMerge(base, over) {
  if (over == null || typeof over !== "object" || Array.isArray(over)) {
    return over === undefined ? base : over;
  }
  const out = (base && typeof base === "object" && !Array.isArray(base))
    ? { ...base } : {};
  for (const [k, v] of Object.entries(over)) {
    const prev = out[k];
    if (v && typeof v === "object" && !Array.isArray(v)
        && prev && typeof prev === "object" && !Array.isArray(prev)) {
      out[k] = deepMerge(prev, v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

function getPath(obj, path) {
  let cur = obj;
  for (const p of path || []) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

function setPath(obj, path, value) {
  const parts = (path || []).slice();
  if (!parts.length) return obj;
  let cur = obj;
  while (parts.length > 1) {
    const k = parts.shift();
    if (cur[k] === undefined || typeof cur[k] !== "object" || Array.isArray(cur[k])) {
      cur[k] = {};
    }
    cur = cur[k];
  }
  if (value === undefined) delete cur[parts[0]];
  else cur[parts[0]] = value;
  return obj;
}

function newTaskId() {
  const hex = Math.random().toString(16).slice(2, 8).padEnd(6, "0");
  return "t" + hex;
}

/** Głęboka kopia JSON — wstawienie presetu do runu nie dzieli referencji. */
function cloneJson(v) {
  if (v == null) return v;
  return JSON.parse(JSON.stringify(v));
}

function toMin(hhmm) {
  if (!hhmm || typeof hhmm !== "string") return 0;
  return Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
}

function accountsOnDevice(devAlias) {
  return accounts.filter((a) => a.device_alias === devAlias).map((a) => a.alias);
}

function isOnline(dev) {
  if (dev.connected) return true;
  const serial = dev.adb_id;
  if (!serial) return false;
  const row = adbRows.find((d) => d.serial === serial);
  return !!(row && row.state === "device");
}

/** Żywe sloty z GET /api/status — nie globalne `running` (klasyk / focus). */
function sessionsList() {
  const raw = (status && status.sessions) || [];
  return Array.isArray(raw) ? raw : [];
}

/** Sesja na TYM telefonie albo null. Start innych kart nie patrzy tutaj. */
function sessionOnDevice(devAlias) {
  return sessionsList().find((s) =>
    s && s.device_alias === devAlias && (s.running || s.stopping)
  ) || null;
}

/** Alias czytelny dla Na żywo — ten sam slot co karta Pulpitu. */
function sessionSlot(devAlias) {
  return sessionOnDevice(devAlias);
}

function sessionRunningOn(devAlias) {
  const slot = sessionOnDevice(devAlias);
  return !!(slot && slot.running);
}

/** Global ON + scheduler_enabled tego device (brak klucza = true) → 423. */
function schedulerBlocksManual(dev) {
  if (!schedulerOn) return false;
  return !dev || dev.scheduler_enabled !== false;
}

function fmtClock(ts) {
  if (!ts) return "";
  const d = (typeof ts === "number") ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return String(d.getHours()).padStart(2, "0") + ":"
    + String(d.getMinutes()).padStart(2, "0");
}

/** Następny (albo właśnie jadący) blok dzisiejszego dnia osi. */
function nextBlock(cycle) {
  if (!cycle) return null;
  const day = (cycle.days || []).find((d) => d.index === cycle.today_index);
  if (!day) return null;
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const runtime = cycle.runtime || {};
  const blocks = (day.blocks || []).slice()
    .sort((a, b) => toMin(a.start) - toMin(b.start));
  for (const b of blocks) {
    if ((runtime[b.id] || {}).status === "running") return { ...b, status: "running" };
  }
  for (const b of blocks) {
    const st = (runtime[b.id] || {}).status || "planned";
    if (st === "done" || st === "failed" || st === "missed") continue;
    const end = toMin(b.start) + (b.duration_s || 0) / 60;
    if (end >= nowMin) return { ...b, status: st };
  }
  return null;
}

function currentView() {
  // decodeURIComponent — wklejony #treści bywa %C5%9Bci w location.hash.
  // NFC: ś z ogonkiem vs s+combining nie mogą rozjechać klucza VIEW_ALIASES.
  const encoded = (location.hash || "#pulpit").replace(/^#/, "") || "pulpit";
  let raw = encoded;
  try { raw = decodeURIComponent(encoded); } catch (_) { raw = encoded; }
  try { raw = raw.normalize("NFC"); } catch (_) { /* stare silniki bez normalize */ }
  const mapped = VIEW_ALIASES[raw] || raw;
  return VIEWS.includes(mapped) ? mapped : "pulpit";
}

/** Pulpit Plan → #harmonogram + preselect urządzenia (nie siatka tygodnia). */
function goPlan(alias) {
  try { sessionStorage.setItem(TL_PRESELECT_KEY, alias || ""); } catch (_) {}
  tlFocusAlias = alias || "";
  tlDevice = alias || tlDevice;
  pulpitFocusDevice = alias || pulpitFocusDevice;
  if (currentView() === "harmonogram") {
    refreshHarmonogram().catch(() => {});
  }
  location.hash = "harmonogram";
}

/** Pulpit → #na-zywo; focus przez setLiveFocus (sessionStorage + zmienna). */
function goLive(alias) {
  if (typeof setLiveFocus === "function") setLiveFocus(alias);
  else liveFocusDevice = alias || "";
  pulpitFocusDevice = alias || pulpitFocusDevice;
  const already = currentView() === "na-zywo";
  location.hash = "na-zywo";
  // Ten sam hash nie odpala hashchange — połącz SSE ręcznie.
  if (already && typeof refreshLive === "function") refreshLive();
}

/** Stare hashe #konto / #system znikają z URL (replaceState, bez hashchange). */
function canonicalizeHash() {
  const raw = (location.hash || "").replace(/^#/, "");
  const target = VIEW_REDIRECTS[raw];
  if (!target) return;
  history.replaceState(null, "", location.pathname + location.search + "#" + target);
}

function tickClock() {
  const el = $("canvas-clock");
  if (!el) return;
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  el.textContent = hh + ":" + mm;
  el.dateTime = d.toISOString();
  if (currentView() === "harmonogram" && typeof paintPlayhead === "function") {
    paintPlayhead();
  }
}

function applyView() {
  // Sekcja musi stracić hidden ZANIM paint — throw w Treści/Live nie chowa widoku.
  let view = "pulpit";
  try {
    canonicalizeHash();
    view = currentView();
  } catch (_) { /* zły hash → pulpit, i tak pokaż sekcję */ }
  document.querySelectorAll(".view").forEach((el) => {
    el.hidden = el.dataset.view !== view;
  });
  try {
    document.querySelectorAll(".rail a[data-view]").forEach((a) => {
      a.classList.toggle("active", a.dataset.view === view);
    });
    const label = VIEW_LABELS[view] || view;
    const titleEl = $("canvas-title");
    if (titleEl) titleEl.textContent = label;
    document.title = "Bot v2 — " + label;
    // Runy i Harmonogram ładują się przy wejściu — pulpit ma własny poll
    if (view === "runy") refreshRuny().catch(() => {});
    if (view === "harmonogram") refreshHarmonogram().catch(() => {});
    if (view === "flota") refreshFlota().catch(() => {});
    if (view === "konta" && typeof refreshKonta === "function") {
      refreshKonta({ force: true }).catch(() => {});
    }
    if (view === "na-zywo") {
      // Wejście w widok: reset IO (hidden → false potrafi zostawić „off-screen”).
      livePreviewOnScreen = true;
      // Jeden throw w paint nie może uciąć polli klatki/postępu.
      try { refreshLive(); } catch (_) { /* widok zostaje widoczny */ }
      pollLivePreview().catch(() => {});
      pollLiveStats().catch(() => {});
    }
    if (view === "pulpit") {
      updateSessionLine();
      renderTodayAxis();
    }
    if (view === "ustawienia") refreshUstawienia().catch(() => {});
    if (view === "lejek") refreshLejek({ reloadForm: !ljFormBound }).catch(() => {});
    if (view === "tresci") {
      // Jak Live: brak refreshTresci / throw w fillSelect nie chowa #view-tresci.
      try { refreshTresci().catch(() => {}); } catch (_) { /* widok zostaje widoczny */ }
    }
  } catch (_) { /* chrome/refresh — sekcja już widoczna */ }
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail || r.statusText || r.status;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = r.status;
    throw err;
  }
  return data;
}

function fillSelect(sel, values, preferred) {
  // Brak węzła (merge HTML) nie może wywrócić applyView / refreshTresci.
  if (!sel) return;
  // Nie przebudowuj <select>, jeśli lista się nie zmieniła — inaczej zamyka się
  // otwarta lista przy pollu co 3 s.
  const current = [...sel.options].map((o) => o.value).filter(Boolean);
  const same = current.length === values.length && current.every((v, i) => v === values[i]);
  if (same) {
    // Komentarz: puste value + ta sama lista (reset <form method="dialog">) — weź preferred albo pierwszy alias, inaczej preview nie ruszy.
    if (!sel.value && values.length) {
      sel.value = (preferred && values.includes(preferred)) ? preferred : values[0];
    }
    return;
  }
  const keep = preferred && values.includes(preferred) ? preferred : (values[0] || "");
  const prev = sel.value;
  sel.innerHTML = values.length
    ? values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("")
    : `<option value="">—</option>`;
  sel.value = values.includes(prev) ? prev : keep;
}

async function loadPresets(account) {
  if (!account) return [DEFAULT_QUEUE];
  if (presetsCache[account]) return presetsCache[account];
  try {
    const names = await api(`/api/queue-presets/${encodeURIComponent(account)}`);
    const list = Array.isArray(names) && names.length ? names : [DEFAULT_QUEUE];
    presetsCache[account] = list;
    return list;
  } catch (_) {
    return [DEFAULT_QUEUE];
  }
}

function buildCard(dev) {
  const el = document.createElement("article");
  el.className = "device-card";
  el.dataset.alias = dev.alias;
  el.innerHTML = `
    <div class="card-head">
      <div class="card-id">
        <h2></h2>
        <span class="card-model"></span>
      </div>
      <div class="card-badges">
        <span class="badge offline">OFFLINE</span>
      </div>
    </div>
    <div class="card-body">
      <div class="preview">
        <img alt="" hidden />
        <div class="preview-ph">brak obrazu</div>
      </div>
      <div class="card-side">
        <div class="chip-label">Konta</div>
        <div class="acc-chips"></div>
        <div class="phase-line"></div>
        <div class="clone-dots" aria-label="Klony"></div>
        <div class="card-fields">
          <label>Konto
            <select class="sel-account"></select>
          </label>
          <label>Run
            <select class="sel-queue"></select>
          </label>
        </div>
        <div class="card-actions">
          <button type="button" class="primary btn-start">Start</button>
          <button type="button" class="danger btn-stop">Stop</button>
          <button type="button" class="btn-ghost btn-live">Live</button>
          <button type="button" class="btn-ghost btn-plan">Plan</button>
        </div>
      </div>
    </div>
    <div class="card-offline-banner" hidden></div>
    <div class="card-msg"></div>`;
  el.querySelector(".sel-account").addEventListener("change", () => onAccountPicked(el));
  el.querySelector(".btn-start").addEventListener("click", () => startQueue(el));
  el.querySelector(".btn-stop").addEventListener("click", () => stopSession(el));
  el.querySelector(".btn-live").addEventListener("click", () => goLive(dev.alias));
  el.querySelector(".btn-plan").addEventListener("click", () => goPlan(dev.alias));
  // Klatka albo puste miejsce karty → oś dziś + (klatka) preselect Live
  el.querySelector(".preview").addEventListener("click", () => goLive(dev.alias));
  el.addEventListener("click", (ev) => {
    if (ev.target.closest("button, select, a, .preview")) return;
    pulpitFocusDevice = dev.alias;
    renderTodayAxis();
  });
  return el;
}

async function onAccountPicked(card) {
  const acc = card.querySelector(".sel-account").value;
  const names = await loadPresets(acc);
  fillSelect(card.querySelector(".sel-queue"), names, DEFAULT_QUEUE);
}

function setCardMsg(card, text, kind) {
  const el = card.querySelector(".card-msg");
  el.textContent = text || "";
  el.className = "card-msg" + (kind ? " " + kind : "");
}

function paintCardBadges(card, online, sess) {
  const box = card.querySelector(".card-badges");
  if (!box) return;
  const bits = [];
  if (online) bits.push(`<span class="badge online">ONLINE</span>`);
  else bits.push(`<span class="badge offline">OFFLINE</span>`);
  if (sess && sess.stopping) bits.push(`<span class="badge stopping">STOPUJE</span>`);
  else if (sess) bits.push(`<span class="badge running">JEDZIE</span>`);
  box.innerHTML = bits.join("");
}

function paintAccountChips(card, accs, liveAccount) {
  const box = card.querySelector(".acc-chips");
  if (!box) return;
  if (!accs.length) {
    box.innerHTML = `<span class="acc-chip"><span class="dot"></span>—</span>`;
    return;
  }
  box.innerHTML = accs.map((name) => {
    const on = liveAccount && name === liveAccount;
    return `<span class="acc-chip${on ? " on" : ""}"><span class="dot"></span>`
      + `${escapeHtml(name)}</span>`;
  }).join("");
}

function paintCloneDots(card, clones) {
  const box = card.querySelector(".clone-dots");
  if (!box) return;
  const list = Array.isArray(clones) ? clones : [];
  if (!list.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = list.map((c) => {
    const claimed = !!(c && c.claimed_by);
    const title = claimed
      ? `${c.package || "klon"} · ${c.claimed_by}`
      : (c.package || "klon");
    return `<span class="clone-dot${claimed ? " claimed" : ""}" title="${escapeHtml(title)}"></span>`;
  }).join("");
}

function paintPhaseLine(sess, nxt) {
  if (sess) {
    const phase = sess.phase || sess.queue || "kolejka";
    const eta = fmtClock(sess.eta);
    const acc = sess.account || "";
    return `${escapeHtml(acc)}${acc ? " · " : ""}${escapeHtml(phase)}`
      + (eta ? ` · ETA ${escapeHtml(eta)}` : "");
  }
  if (nxt) {
    return `<span class="dim">następny blok ${escapeHtml(nxt.start)} `
      + `${escapeHtml(nxt.scenario || DEFAULT_QUEUE)}</span>`;
  }
  return `<span class="dim">brak na dziś</span>`;
}

async function updateCard(dev) {
  const card = document.querySelector(`.device-card[data-alias="${CSS.escape(dev.alias)}"]`);
  if (!card) return;
  const online = isOnline(dev);
  // Tylko slot TEGO aliasu — inny telefon jedzie → Start zostaje.
  const sess = sessionOnDevice(dev.alias);
  const nxt = nextBlock(timelines[dev.alias]);
  const accs = accountsOnDevice(dev.alias);

  card.classList.toggle("live", !!sess);
  card.querySelector("h2").textContent = dev.alias;
  const modelEl = card.querySelector(".card-model");
  if (modelEl) modelEl.textContent = dev.model || "";
  paintCardBadges(card, online, sess);
  paintAccountChips(card, accs, sess && sess.account);
  paintCloneDots(card, dev.clones);
  const phaseEl = card.querySelector(".phase-line");
  if (phaseEl) phaseEl.innerHTML = paintPhaseLine(sess, nxt);

  const accSel = card.querySelector(".sel-account");
  const prefer = (sess && sess.account) || (nxt && nxt.account) || accSel.value;
  fillSelect(accSel, accs, prefer);

  const qSel = card.querySelector(".sel-queue");
  const acc = accSel.value;
  const names = await loadPresets(acc);
  const preferQ = (sess && sess.queue)
    || (nxt && nxt.account === acc && nxt.scenario)
    || qSel.value || DEFAULT_QUEUE;
  fillSelect(qSel, names, preferQ);

  // Start: OFFLINE / sesja na TYM device / scheduler ON na TYM device / brak konta.
  // NIE disable bo status.running (inny telefon).
  const startOk = online && !sess && !schedulerBlocksManual(dev) && !!acc;
  card.querySelector(".btn-start").disabled = !startOk;
  card.querySelector(".btn-stop").disabled = !sess || !!(sess && sess.stopping);

  const ban = card.querySelector(".card-offline-banner");
  if (ban) {
    if (!online) {
      ban.hidden = false;
      ban.textContent = `ADB offline · ${dev.adb_id || "?"}`;
    } else {
      ban.hidden = true;
      ban.textContent = "";
    }
  }
}

function syncGrid() {
  const grid = $("device-grid");
  if (!devices.length) {
    visibleCards.clear();
    grid.innerHTML = `<div class="empty">Brak urządzeń w config/devices/.</div>`;
    return;
  }
  const empty = grid.querySelector(".empty");
  if (empty) empty.remove();

  const want = new Set(devices.map((d) => d.alias));
  for (const el of [...grid.querySelectorAll(".device-card")]) {
    if (!want.has(el.dataset.alias)) {
      io.unobserve(el);
      visibleCards.delete(el.dataset.alias);
      revokePreview(el.dataset.alias);
      el.remove();
    }
  }
  const have = new Set([...grid.querySelectorAll(".device-card")].map((el) => el.dataset.alias));
  for (const d of devices) {
    if (have.has(d.alias)) continue;
    const card = buildCard(d);
    grid.appendChild(card);
    io.observe(card);
  }
}

function revokePreview(alias) {
  if (previewUrls[alias]) {
    URL.revokeObjectURL(previewUrls[alias]);
    delete previewUrls[alias];
  }
}

function showPreviewPlaceholder(card) {
  card.querySelector(".preview").classList.remove("has-frame");
  const img = card.querySelector("img");
  img.hidden = true;
  img.removeAttribute("src");
}

async function refreshPreview(alias) {
  const card = document.querySelector(`.device-card[data-alias="${CSS.escape(alias)}"]`);
  if (!card) return;
  // JPEG tylko gdy TEN telefon ma sesję — 204 / brak sesji = pusty, nie uiautodev.
  if (!sessionOnDevice(alias)) {
    showPreviewPlaceholder(card);
    return;
  }
  const url = `/api/devices/${encodeURIComponent(alias)}/preview?t=${Date.now()}`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (r.status !== 200) {
      showPreviewPlaceholder(card);
      return;
    }
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    if (!ct.startsWith("image/")) {
      showPreviewPlaceholder(card);
      return;
    }
    const blob = await r.blob();
    if (!blob.size) {
      showPreviewPlaceholder(card);
      return;
    }
    revokePreview(alias);
    const obj = URL.createObjectURL(blob);
    previewUrls[alias] = obj;
    const img = card.querySelector("img");
    img.alt = `Podgląd ${alias}`;
    img.src = obj;
    img.hidden = false;
    card.querySelector(".preview").classList.add("has-frame");
  } catch (_) {
    showPreviewPlaceholder(card);
  }
}

async function pollPreviews() {
  if (document.hidden || currentView() !== "pulpit") return;
  // Widoczne karty bez sesji czyścimy; fetch tylko dla jadących.
  await Promise.all([...visibleCards].map((alias) => refreshPreview(alias)));
}

function updateSessionLine() {
  const el = $("session-line");
  if (!el) return;
  // Lejek: ścieżka konta, nie stan sesji telefonu.
  if (currentView() === "lejek") {
    const acc = ($("lj-account") && $("lj-account").value) || ljAccount || "konto";
    el.textContent = `${acc} → surowy → filtr → kandydaci → wizyty`;
    return;
  }
  // Na żywo: kontekst focusa (telefon · konto · runa), nie globalne running.
  if (currentView() === "na-zywo") {
    const alias = resolveLiveFocus();
    const slot = sessionSlot(alias);
    const run = (slot && (slot.queue || slot.phase)) || "";
    const parts = [alias, slot && slot.account, run ? "runa " + run : ""]
      .filter(Boolean);
    el.textContent = parts.join(" · ") || "brak sesji";
    return;
  }
  if (currentView() === "harmonogram" && tlWindowHint) {
    el.textContent = tlWindowHint;
    return;
  }
  if (currentView() === "flota") {
    const port = (devices || []).map((d) => d.uiautodev_port).find((p) => p);
    el.textContent = port ? `ADB · uiautodev :${port}` : "ADB · flota";
    return;
  }
  if (currentView() === "konta") {
    el.textContent = (typeof kontaCtxLine === "function") ? kontaCtxLine() : "konta";
    return;
  }
  // Statystyki: podtytuł = wybrane konto (mock: jobsaver pod tytułem).
  if (currentView() === "statystyki") {
    const acc = $("st-account") && $("st-account").value;
    el.textContent = acc || "wybierz konto";
    return;
  }
  // Pulpit: flota z status.sessions, nie globalne running/focus.
  const n = devices.length;
  const jedzie = sessionsList().filter((s) => s.running).length;
  const tel = n === 1 ? "telefon" : "telefony";
  const ses = jedzie === 1 ? "sesja jedzie" : "sesji jedzie";
  el.textContent = n ? `${n} ${tel} · ${jedzie} ${ses}` : "brak urządzeń";
}

function updateSchedulerBanner() {
  const el = $("scheduler-banner");
  if (!el) return;
  // Nie kłamać, że wszystkie Starty padają — tylko device z scheduler_enabled.
  if (schedulerOn) {
    el.hidden = false;
    el.className = "banner warn";
    el.textContent = "Scheduler globalny ON — ręczny Start zablokowany na telefonach z scheduler_enabled (423). Inne zostają wolne.";
  } else {
    el.hidden = true;
    el.textContent = "";
  }
}

function countTodaySessions() {
  let planned = 0;
  let done = 0;
  for (const cycle of Object.values(timelines || {})) {
    if (!cycle) continue;
    const day = (cycle.days || []).find((d) => d.index === cycle.today_index);
    const runtime = cycle.runtime || {};
    for (const b of (day && day.blocks) || []) {
      planned += 1;
      const st = (runtime[b.id] || {}).status || b.status || "";
      if (st === "done" || st === "failed" || st === "missed") done += 1;
    }
  }
  return { done, planned };
}

function paintPulpitKpi() {
  const onlineN = devices.filter(isOnline).length;
  const jedzie = sessionsList().filter((s) => s.running).length;
  const { done, planned } = countTodaySessions();
  if ($("kpi-online")) $("kpi-online").textContent = `${onlineN}/${devices.length}`;
  if ($("kpi-running")) $("kpi-running").textContent = String(jedzie);
  if ($("kpi-today")) $("kpi-today").textContent = `${done}/${planned} sesji`;
  if ($("kpi-funnel")) $("kpi-funnel").textContent = `${funnelPending} pending`;
  if ($("kpi-alerts")) $("kpi-alerts").textContent = String(alertCount);
  const bell = $("alert-bell");
  if (bell) {
    bell.dataset.count = String(alertCount);
    bell.classList.toggle("has-alerts", alertCount > 0);
  }
}

/** Oś dziś z mocka Pulpitu: okno cyklu, kolor konta, ikona stanu. */
function renderTodayAxis() {
  const wrap = $("pulpit-today");
  const axis = $("pulpit-today-axis");
  const title = $("pulpit-today-title");
  if (!wrap || !axis) return;
  const running = sessionsList().filter((s) => s.running).map((s) => s.device_alias);
  let alias = pulpitFocusDevice;
  if (!alias || !devices.some((d) => d.alias === alias)) {
    alias = running[0] || (devices[0] && devices[0].alias) || "";
  }
  document.querySelectorAll(".device-card").forEach((el) => {
    el.classList.toggle("sel", el.dataset.alias === alias);
  });
  const cycle = timelines[alias];
  if (!alias || !cycle) {
    wrap.hidden = true;
    return;
  }
  const day = (cycle.days || []).find((d) => d.index === cycle.today_index);
  const hours = cycle.active_hours || [8, 20];
  const startH = Number(hours[0] ?? 8);
  const endH = Number(hours[1] ?? 20);
  const startMin = startH * 60;
  const endMin = Math.max(endH * 60, startMin + 60);
  const span = endMin - startMin;
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const runtime = cycle.runtime || {};
  const blocks = ((day && day.blocks) || []).slice()
    .sort((a, b) => toMin(a.start) - toMin(b.start));
  const lab = title && title.querySelector(".pulpit-today-lab");
  if (lab) lab.textContent = `dziś na ${alias}`;
  else if (title) title.textContent = `dziś na ${alias}`;
  wrap.hidden = false;
  // Etykiety i kreski w tych samych %, żeby godzina siedziała nad tickiem.
  const hourMarks = [];
  for (let h = startH; h <= endH; h++) {
    const left = ((h * 60 - startMin) / span) * 100;
    const hh = String(h).padStart(2, "0") + ":00";
    hourMarks.push({ left, hh });
  }
  const hoursHtml = hourMarks.map((t) =>
    `<span class="axis-hour" style="left:${t.left}%">${t.hh}</span>`).join("");
  const ticksHtml = hourMarks.map((t) =>
    `<span class="axis-tick" style="left:${t.left}%"></span>`).join("");
  const icoCheck = '<span class="axis-ico" aria-hidden="true"><svg viewBox="0 0 14 14" width="10" height="10"><path fill="currentColor" d="M5.4 10.2 2.6 7.4l1.1-1.1 1.7 1.7 4-4 1.1 1.1z"/></svg></span>';
  const icoPlay = '<span class="axis-ico" aria-hidden="true"><svg viewBox="0 0 14 14" width="10" height="10"><path fill="currentColor" d="M5 3.4v7.2l5.4-3.6z"/></svg></span>';
  const icoMiss = '<span class="axis-ico" aria-hidden="true"><svg viewBox="0 0 14 14" width="10" height="10"><path fill="currentColor" d="M4.1 3.4 7 6.3l2.9-2.9 1.1 1.1L8.1 7.4l2.9 2.9-1.1 1.1L7 8.5l-2.9 2.9-1.1-1.1 2.9-2.9-2.9-2.9z"/></svg></span>';
  const blockHtml = blocks.map((b) => {
    const bStart = toMin(b.start);
    const bEnd = bStart + (Number(b.duration_s) || 0) / 60;
    if (bEnd <= startMin || bStart >= endMin) return "";
    const leftPct = ((bStart - startMin) / span) * 100;
    const rightPct = ((bEnd - startMin) / span) * 100;
    const left = Math.max(0, leftPct);
    const width = Math.max(Math.min(100, rightPct) - left, 2.2);
    const st = (runtime[b.id] || {}).status || b.status || "planned";
    let cls = "plan";
    let ico = "";
    if (st === "running") { cls = "run"; ico = icoPlay; }
    else if (st === "done") { cls = "done"; ico = icoCheck; }
    else if (st === "failed" || st === "missed") { cls = "miss"; ico = icoMiss; }
    const color = accColor(b.account);
    const tip = `${b.account || ""} · ${b.scenario || DEFAULT_QUEUE} ${b.start || ""}`;
    return `<div class="axis-block ${cls}" style="left:${left}%;width:${width}%;--acc:${color}" title="${escapeHtml(tip)}">`
      + `<span class="when">${escapeHtml(b.start || "")}</span>`
      + `<span class="who">${escapeHtml(b.account || "")}</span>`
      + ico
      + `</div>`;
  }).join("");
  const play = (nowMin >= startMin && nowMin <= endMin)
    ? `<div class="axis-now" style="left:${((nowMin - startMin) / span) * 100}%"></div>`
    : "";
  axis.innerHTML = `<div class="axis-hours">${hoursHtml}</div>`
    + `<div class="axis-body"><div class="axis-rail"></div>${ticksHtml}${blockHtml}${play}</div>`;
}

async function sumFunnelPending(accList) {
  const rows = Array.isArray(accList) ? accList : [];
  const nums = await Promise.all(rows.map(async (a) => {
    try {
      const st = await api(`/api/profile-filter/${encodeURIComponent(a.alias)}/status`);
      return Number(st.pending || 0);
    } catch (_) {
      return 0;
    }
  }));
  return nums.reduce((s, n) => s + n, 0);
}

async function loadAlertCount() {
  // Brak /api/alerts = stub z ADB offline + sesje w stopping.
  try {
    const data = await api("/api/alerts");
    if (typeof data.count === "number") return data.count;
    if (Array.isArray(data.alerts)) return data.alerts.length;
  } catch (_) { /* stub OK */ }
  const offline = devices.filter((d) => !isOnline(d)).length;
  const stopping = sessionsList().filter((s) => s.stopping).length;
  return offline + stopping;
}

let pulpitGen = 0;

async function refreshPulpit() {
  const gen = ++pulpitGen;
  const errBox = $("pulpit-error");
  try {
    const [dev, adb, acc, st, sched, tlDevs] = await Promise.all([
      api("/api/devices"),
      api("/api/adb-devices").catch(() => []),
      api("/api/accounts"),
      api("/api/status"),
      api("/api/scheduler").catch(() => ({ enabled: false })),
      api("/api/timeline-devices").catch(() => []),
    ]);
    devices = Array.isArray(dev) ? dev : [];
    adbRows = Array.isArray(adb) ? adb : [];
    accounts = Array.isArray(acc) ? acc : [];
    status = st || {};
    schedulerOn = !!(sched && sched.enabled);

    const aliases = devices.map((d) => d.alias);
    const fromTl = (Array.isArray(tlDevs) ? tlDevs : []).map((x) => x.device);
    const needTl = [...new Set([...aliases, ...fromTl])];
    const loaded = {};
    await Promise.all(needTl.map(async (alias) => {
      try {
        loaded[alias] = await api(`/api/timeline/${encodeURIComponent(alias)}`);
      } catch (_) { /* brak cyklu — karta pokaże „brak na dziś” */ }
    }));
    timelines = loaded;
    if (gen !== pulpitGen) return;

    errBox.hidden = true;
    // Live hydratuje się ze status.sessions — zanim KPI/karty zdążą rzucić.
    if (typeof refreshLive === "function") {
      try { refreshLive(); } catch (_) { /* Na żywo nie blokuje Pulpitu */ }
    }
    const [pending, alerts] = await Promise.all([
      sumFunnelPending(accounts),
      loadAlertCount(),
    ]);
    if (gen !== pulpitGen) return;
    funnelPending = pending;
    alertCount = alerts;
    syncGrid();
    updateSessionLine();
    updateSchedulerBanner();
    paintPulpitKpi();
    await Promise.all(devices.map((d) => updateCard(d)));
    renderTodayAxis();
    // Drugi pass: SSE/Stop po syncGrid (karty już znają aliasy).
    if (typeof refreshLive === "function") {
      try { refreshLive(); } catch (_) { /* ignore */ }
    }
    if (currentView() === "flota" && typeof paintFlota === "function") paintFlota();
  } catch (e) {
    errBox.hidden = false;
    errBox.textContent = "Nie udało się wczytać pulpitu: " + (e.message || e);
  }
}

async function startQueue(card) {
  const account = card.querySelector(".sel-account").value;
  const queueName = card.querySelector(".sel-queue").value || DEFAULT_QUEUE;
  if (!account) {
    setCardMsg(card, "Wybierz konto.", "err");
    return;
  }
  setCardMsg(card, "Uruchamiam…");
  try {
    await api("/api/run-queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account, queue_name: queueName }),
    });
    setCardMsg(card, "Kolejka uruchomiona.", "ok");
    await refreshPulpit();
  } catch (e) {
    setCardMsg(card, e.message || "nie uruchomiono", "err");
  }
}

async function stopSession(card) {
  const alias = card.dataset.alias;
  setCardMsg(card, "Zatrzymuję…");
  try {
    // Tylko ten serial — body {device_alias}, nie globalny stop floty.
    await api("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_alias: alias }),
    });
    setCardMsg(card, "Wysłano Stop.", "ok");
    await refreshPulpit();
  } catch (e) {
    setCardMsg(card, e.message || "stop nieudany", "err");
  }
}

window.addEventListener("hashchange", applyView);

const ACTION_LABELS = { likes: "Lajki", follows: "Follow", comments: "Komentarze" };
const RUN_NAME_RE = /^[a-zA-Z0-9._-]{1,40}$/;
const BASIC_FIELD_CAP = 7;
const GROUP_LABELS = {
  warmup: "Rozgrzewka",
  engagement: "Interakcja",
  discovery: "Pozyskiwanie",
  config: "Konfiguracja",
};
/** Biblioteka Runy — tylko queueable z planu; niche_discover poza pickerem. */
const RUNY_LIB_IDS = [
  "feed_browser", "reels_warmup", "reels_niche", "content_search",
  "profile_interact", "mass_stories", "target_scraper", "publish_media",
];
const RUNY_LIB_SHORT = {
  feed_browser: "Feed",
  reels_warmup: "Reels warmup",
  reels_niche: "Reels nisza",
  content_search: "Szukaj",
  profile_interact: "Profile",
  mass_stories: "Relacje masowe",
  target_scraper: "Scraper",
  publish_media: "Treści",
};
/** Ikony 8 pluginów — home, pulse, star, lupa, user, samolot, tacka, obraz. */
const PLUGIN_ICONS = {
  feed_browser: {
    tone: "warmup",
    svg: '<path fill="currentColor" d="M12 3.4 3.2 11.2H5.5V21h5.2v-6.2h2.6V21h5.2v-9.8h2.3z"/>',
  },
  reels_warmup: {
    tone: "warmup",
    svg: '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M3 12.2h3.2l2-5.4 3.2 10.4 2.2-5h7.2"/>',
  },
  reels_niche: {
    tone: "star",
    svg: '<path fill="currentColor" d="M12 2.8 14.7 9h6.6l-5.3 4.1 2 6.4L12 15.8 6 19.5l2-6.4L2.7 9h6.6z"/>',
  },
  content_search: {
    tone: "search",
    svg: '<path fill="none" stroke="currentColor" stroke-width="1.8" d="M10.6 17.4a6.8 6.8 0 1 1 0-13.6 6.8 6.8 0 0 1 0 13.6z"/><path fill="none" stroke="currentColor" stroke-width="1.8" d="m15.6 15.6 5 5"/>',
  },
  profile_interact: {
    tone: "engagement",
    svg: '<path fill="currentColor" d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 1.8c-3.8 0-8 1.9-8 4.8V20h16v-1.4c0-2.9-4.2-4.8-8-4.8z"/>',
  },
  mass_stories: {
    tone: "engagement",
    svg: '<path fill="currentColor" d="M3.2 11.6 21 3.4l-7.4 17.2-2.5-7z"/>',
  },
  target_scraper: {
    tone: "discovery",
    svg: '<path fill="none" stroke="currentColor" stroke-width="1.7" d="M4.4 13.6v5h15.2v-5M4.4 13.6 7.2 7h9.6l2.8 6.6M9 16.6h6"/>',
  },
  publish_media: {
    tone: "publish",
    svg: '<path fill="currentColor" d="M4 5h16v14H4zm2 2v10h12V7zm2.2 7.4 2.4-3.1 1.9 2.4 2.6-3.6 3.1 4.3H8.2zM9 9.4a1.2 1.2 0 1 0 0-2.4 1.2 1.2 0 0 0 0 2.4z"/>',
  },
};
const SAVED_ACTION_ICON = '<path fill="currentColor" d="M7.2 3.2h9.6v17.2L12 16.8 7.2 20.4z"/>';
const CS_SOURCE_GROUPS = [
  { label: "Lista fraz", opts: [
    { value: "hashtag", label: "Hashtagi" },
    { value: "keyword", label: "Słowa kluczowe" },
    { value: "location", label: "Lokalizacje" },
  ]},
  { label: "Znaczniki / mix", opts: [
    { value: "tag_variants", label: "Znaczniki z mapy" },
    { value: "hashtag_plus_tags", label: "# + znaczniki" },
    { value: "keyword_plus_tags", label: "Słowa + znaczniki" },
    { value: "all_random", label: "Wszystko (#, słowa, znaczniki)" },
  ]},
];
const CS_USES_MAP = new Set([
  "tag_variants", "hashtag_plus_tags", "keyword_plus_tags", "all_random",
]);
const CS_SRC_LABEL = {
  hashtag: "hashtag", keyword: "słowa", location: "lokalizacja",
  tag_variants: "znaczniki", hashtag_plus_tags: "# + znaczniki",
  keyword_plus_tags: "słowa + znaczniki", all_random: "mix",
};

let pluginCatalog = [];
let pluginAccountSettings = {};  // GET /api/plugins/{acc} — tylko merge, nigdy POST
let runyAccount = "";
let runyActiveName = DEFAULT_QUEUE;
let runySelectedTaskId = "";
let runyQueues = {};       // nazwa → draft kolejki
let runyDirty = {};        // nazwa → bool
let runyEst = {};          // nazwa → odpowiedź estimate-actions
let runyEstTimer = {};
let runyGen = 0;
let runyDragFrom = null;
let runyLibTab = "base";       // bazowe w górnym pasie; „saved” = tacka
let runyActions = [];          // presety kroku bieżącego konta
let runyActionEditId = "";     // slug edytowany inline przy zapisanej akcji
let runyLibDrag = null;        // {kind:"plugin"|"action", id|action} — drop z tacki

const TL_PRESELECT_KEY = "botv2.harmonogram.device";
const TL_WD = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"];
const TL_WD_FULL = ["Poniedziałek", "Wtorek", "Środa", "Czwartek",
  "Piątek", "Sobota", "Niedziela"];
// Paleta kont — indeks na liście floty, nie hash (podobne aliasy nie zlewają się).
const ACC_PALETTE = [
  "#4ea1ff", "#2ec4b6", "#f0a03c", "#9b7bff",
  "#3ecf7d", "#ef6a6d", "#f0b942", "#c47cff",
];

let tlWeekOffset = 0;
let tlDevice = "";           // preselect z Pulpitu (Plan) — stary select osi
let tlFocusAlias = "";
let tlSel = null;            // {device, dayIndex, dateIso, block}
let tlCycles = {};           // alias → GET /api/timeline
let tlAccountsByDev = {};    // alias → GET /api/schedule-accounts
let tlSched = null;
let tlEstCache = {};         // klucz acc|scenario → estimate
let tlGen = 0;
let tlPxPerMin = 1;
let tlWin = { from: 8 * 60, to: 20 * 60 };
let tlWindowHint = "";
let tlDrag = null;

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

function fmtDur(s) {
  s = Math.max(0, Number(s) || 0);
  if (s < 90) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  return (Math.round((s / 3600) * 10) / 10) + " h";
}

function queueablePlugins() {
  return (pluginCatalog || []).filter((p) => p.queueable !== false && !p.ui_hidden);
}

/** Chippy biblioteki w stałej kolejności planu — bez niche_discover. */
function libraryPlugins() {
  const byId = {};
  for (const p of queueablePlugins()) byId[p.id] = p;
  return RUNY_LIB_IDS.map((id) => byId[id]).filter(Boolean);
}

/** ETA w stylu mocka (`~2h 28m`); 0 / brak → myślnik. */
function fmtEtaClock(s) {
  s = Math.max(0, Number(s) || 0);
  if (!s) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h) return `~${h}h ${String(m).padStart(2, "0")}m`;
  if (s < 90) return `~${Math.round(s)}s`;
  return `~${m} min`;
}

/** Suma max_duration_min włączonych kroków + przerwy — fallback gdy eta akcji = 0. */
function draftWallS(draft) {
  const q = ((draft && draft.queue) || []).filter((t) => t.enabled !== false);
  let sec = 0;
  for (const t of q) {
    const mins = Number(getPath(resolvedForTask(t), ["budget", "max_duration_min"]));
    if (Number.isFinite(mins) && mins > 0) sec += mins * 60;
  }
  const gapMax = Number((draft && draft.between_tasks_s && draft.between_tasks_s.max) || 0);
  if (q.length > 1 && gapMax > 0) sec += (q.length - 1) * gapMax;
  return sec;
}

function runEtaSeconds(name) {
  const est = runyEst[name] || {};
  const fromApi = est.eta && est.eta.total_s && Number(est.eta.total_s.max);
  if (fromApi > 0) return fromApi;
  return draftWallS(runyQueues[name] || { queue: [] });
}

function pluginUnitShort(pid) {
  const pl = pluginById(pid);
  const all = ((pl && pl.sections) || []).flatMap((s) => s.fields || []);
  const f = all.find((x) => (x.key || []).join(".") === "budget.max_items");
  const lab = (f && f.label) || (f && f.unit) || "";
  return lab.replace(/^Max\s+/i, "").trim() || "szt.";
}

function pluginItemsDefault(pid) {
  const p = (pluginCatalog || []).find((x) => x.id === pid);
  const d = p && p.defaults && p.defaults.budget && p.defaults.budget.max_items;
  if (!d) return null;
  if (typeof d === "object") return { min: d.min, max: d.max };
  const n = Number(d);
  return Number.isFinite(n) ? { min: n, max: n } : null;
}

function itemsRange(budget) {
  const raw = budget && budget.max_items;
  if (raw == null) return null;
  if (typeof raw === "object") {
    const lo = Number(raw.min);
    const hi = Number(raw.max);
    if (!Number.isFinite(lo) && !Number.isFinite(hi)) return null;
    return { min: Number.isFinite(lo) ? lo : hi, max: Number.isFinite(hi) ? hi : lo };
  }
  const n = Number(raw);
  return Number.isFinite(n) ? { min: n, max: n } : null;
}

/** Tokeny progu wykorzystania — interpolacja ok → warn → err. */
const LIM_OK = "#3ecf7d";
const LIM_WARN = "#f0b942";
const LIM_ERR = "#ef6a6a";

function _hexRgb(hex) {
  const h = String(hex).replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function _lerpHex(a, b, t) {
  const ca = _hexRgb(a);
  const cb = _hexRgb(b);
  const u = Math.max(0, Math.min(1, t));
  const rgb = ca.map((v, i) => Math.round(v + (cb[i] - v) * u));
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

/** Kolor `max/limit`: 0–50% ok→warn, 50–90% warn→err, ≥90% lub over → err. */
function limitShareColor(max, limit, over) {
  if (over || (limit > 0 && max > limit)) return LIM_ERR;
  if (!(limit > 0)) return "var(--muted)";
  const r = max / limit;
  if (r <= 0.5) return _lerpHex(LIM_OK, LIM_WARN, r / 0.5);
  if (r < 0.9) return _lerpHex(LIM_WARN, LIM_ERR, (r - 0.5) / 0.4);
  return LIM_ERR;
}

/** Mały donut obok `90/100` — wypełnienie = max/limit (cap 100%). */
function renderLimitDonut(pct, color) {
  const r = 14;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, Number(pct) || 0));
  const dash = (c * p).toFixed(2);
  return `<svg class="tl-lim-donut" viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">
    <circle class="tl-lim-donut-track" cx="18" cy="18" r="${r}" fill="none" stroke-width="4"/>
    <circle class="tl-lim-donut-arc" cx="18" cy="18" r="${r}" fill="none"
      stroke="${color}" stroke-width="4" stroke-linecap="round"
      stroke-dasharray="${dash} ${c.toFixed(2)}" transform="rotate(-90 18 18)"/>
  </svg>`;
}

/** Harmonogram: kompaktowe `90/100` + donut — bez full-width paska. */
function renderDayLimitPills(util, opts) {
  opts = opts || {};
  const usedMap = opts.usedToday || {};
  const showUsed = !!opts.showUsed;
  if (!util) return `<div class="tl-lim-acc">brak szacunku</div>`;
  return `<div class="tl-lim-pills">${["likes", "follows", "comments"].map((k) => {
    const u = util[k] || { min: 0, max: 0, limit: 0 };
    const lim = u.limit || 0;
    const max = u.max || 0;
    const min = u.min || 0;
    const over = !!u.over || (lim > 0 && max > lim);
    const used = Number(usedMap[k] != null ? usedMap[k] : (u.used_today || 0)) || 0;
    const color = limitShareColor(max, lim, over);
    const frac = lim > 0 ? `${max}/${lim}` : `${max}/—`;
    const main = (showUsed && used > 0) ? `plan ${frac}` : frac;
    const usedHtml = (showUsed && used > 0)
      ? `<span class="tl-lim-used">dziś ${used} ·</span>` : "";
    const range = min !== max
      ? `<span class="tl-lim-range">${min}–${max}</span>` : "";
    const pct = lim > 0 ? max / lim : 0;
    const title = [
      `${ACTION_LABELS[k]}: ${frac}`,
      min !== max ? `szacunek ${min}–${max}, do limitu liczymy ${max}` : "",
      used > 0 ? `zużyte dziś ${used}` : "",
      over ? "OVER — max > limit" : "",
    ].filter(Boolean).join(" · ");
    return `<div class="tl-lim-pill${over ? " over" : ""}" title="${escapeHtml(title)}">
      <span class="tl-lim-lbl">${ACTION_LABELS[k]}</span>
      ${renderLimitDonut(pct, color)}
      ${usedHtml}
      <span class="tl-lim-frac" style="color:${color}">${escapeHtml(main)}</span>
      ${range}
    </div>`;
  }).join("")}</div>`;
}

async function ensureCatalog() {
  if (pluginCatalog.length) return;
  try {
    const data = await api("/api/plugins");
    pluginCatalog = Array.isArray(data) ? data : [];
  } catch (_) {
    pluginCatalog = [];
  }
}

async function ensureAccounts() {
  if (accounts.length) return accounts;
  try {
    accounts = await api("/api/accounts");
  } catch (_) {
    accounts = [];
  }
  return accounts;
}

// --- Runy: kreator (biblioteka + kroki + formularz per task.id) -----------

function runySelectedAccount() {
  const sel = $("runy-account");
  return (sel && sel.value) || runyAccount || "";
}

function setRunyMsg(text, kind) {
  showBanner("runy-msg", text, kind);
}

function pluginById(pid) {
  return (pluginCatalog || []).find((p) => p.id === pid) || null;
}

function accountPluginVals(pid) {
  const fromAcc = pluginAccountSettings && pluginAccountSettings[pid];
  const fromCat = (pluginById(pid) || {}).defaults || {};
  return deepMerge(fromCat, fromAcc || {});
}

/** Defaults konta ← overrides zadania ← budżet karty — jak store.resolve_tasks. */
function resolvedForTask(task) {
  let resolved = deepMerge(accountPluginVals(task.plugin), task.overrides || {});
  if (task.budget && Object.keys(task.budget).length) {
    resolved = deepMerge(resolved, { budget: task.budget });
  }
  return resolved;
}

function ensureTaskIds(draft) {
  if (!draft || !Array.isArray(draft.queue)) return draft;
  const seen = new Set();
  for (const t of draft.queue) {
    if (!t.id || seen.has(t.id)) t.id = newTaskId();
    seen.add(t.id);
    if (!t.overrides || typeof t.overrides !== "object") t.overrides = {};
    if (!t.budget || typeof t.budget !== "object") t.budget = {};
    if (t.from_action && typeof t.from_action !== "string") delete t.from_action;
  }
  return draft;
}

function findTask(name, taskId) {
  const q = (runyQueues[name] || {}).queue || [];
  return q.find((t) => t.id === taskId) || null;
}

function currentDraft() {
  return runyQueues[runyActiveName] || null;
}

function markRunDirty() {
  runyDirty[runyActiveName] = true;
  paintLimitsChrome();
}

function likeDecisionChanceKeys(fields) {
  const keys = new Set();
  for (const f of fields) {
    if (f.type === "like_decision" && f.chance_key) {
      keys.add(JSON.stringify(f.chance_key));
    }
  }
  return keys;
}

function skipCatalogField(f, allFields, pid) {
  if (!f || f.hidden) return true;
  const key = f.key || [];
  if (key[0] === "enabled" && key.length === 1) return true;
  if (key[0] === "budget") return true;
  if (key[0] === "_ui" && f.type !== "like_decision") return true;
  if (["raw_profiles_preview", "target_candidates_preview",
       "tag_variants_map"].includes(f.type)) return true;
  if (pid === "content_search" && key.length === 1 && key[0] === "source") return true;
  // Własny picker miniatur — nie surowe tagi id.
  if (pid === "publish_media" && key[0] === "item_ids") return true;
  if (f.type === "chance" && likeDecisionChanceKeys(allFields).has(JSON.stringify(f.key))) {
    return true;
  }
  return false;
}

function catalogFields(pl) {
  const all = (pl.sections || []).flatMap((s) => s.fields || []);
  return all.filter((f) => !skipCatalogField(f, all, pl.id));
}

function fieldPriority(f) {
  const k = (f.key || []).join(".");
  if (k === "source" || (k.endsWith(".kind") && k.includes("source"))) return 100;
  if (k.endsWith("harvest.enabled")) return 96;
  if (f.type === "source_phrases") return 90;
  if (k.includes("like") && k.endsWith("enabled")) return 86;
  if (f.type === "like_decision") return 84;
  if (k.includes("per_session") || k.includes("per_phrase.items")) return 72;
  if (k === "content_types" || k === "sources") return 68;
  if (k.includes("follow.allow")) return 64;
  return 20;
}

function splitInspectorFields(pl) {
  const fields = catalogFields(pl);
  const basic = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);
  basic.sort((a, b) => fieldPriority(b) - fieldPriority(a));
  const primary = basic.slice(0, BASIC_FIELD_CAP);
  const extra = basic.slice(BASIC_FIELD_CAP);
  return { primary, extra, advanced };
}

function _csSameSet(a, b) {
  const sa = [...(a || [])].map(String).sort().join("\0");
  const sb = [...(b || [])].map(String).sort().join("\0");
  return sa === sb;
}

function csSourceValue(task) {
  const ov = task.overrides || {};
  if (ov.run_scope === "mix") {
    const from = ov.mix_from || [];
    if (_csSameSet(from, ["keyword", "tag_variants"])) return "keyword_plus_tags";
    if (_csSameSet(from, ["hashtag", "keyword", "tag_variants"])) return "all_random";
    if (_csSameSet(from, ["hashtag", "tag_variants"])) return "hashtag_plus_tags";
    return "all_random";
  }
  if (ov.run_scope === "tag_variants") return "tag_variants";
  if (ov.source) return ov.source;
  return (accountPluginVals("content_search").source) || "hashtag";
}

function csDefaultSeedKind(value) {
  if (value === "hashtag_plus_tags") return "hashtag";
  if (value === "keyword_plus_tags") return "keyword";
  return "both";
}

function csApplySource(task, value) {
  task.overrides = Object.assign({}, task.overrides || {});
  const prevTvb = task.overrides.tag_variants_budget || {};
  const prevKind = prevTvb.map_seed_kind;
  delete task.overrides.mix_from;
  if (value === "hashtag" || value === "keyword" || value === "location") {
    task.overrides.source = value;
    task.overrides.run_scope = "phrases";
    delete task.overrides.tag_variants_budget;
    return;
  }
  delete task.overrides.source;
  const tvb = Object.assign({}, prevTvb, { source: "from_map" });
  tvb.map_seed_kind = prevKind || csDefaultSeedKind(value);
  if (value === "tag_variants") task.overrides.run_scope = "tag_variants";
  else if (value === "hashtag_plus_tags") {
    task.overrides.run_scope = "mix";
    task.overrides.mix_from = ["hashtag", "tag_variants"];
  } else if (value === "keyword_plus_tags") {
    task.overrides.run_scope = "mix";
    task.overrides.mix_from = ["keyword", "tag_variants"];
  } else {
    task.overrides.run_scope = "mix";
    task.overrides.mix_from = ["hashtag", "keyword", "tag_variants"];
  }
  task.overrides.tag_variants_budget = tvb;
}

function inferLikeDecisionMode(aiOn, chance, fallback) {
  if (!aiOn) return "chance_only";
  if (chance && chance.mode === "p" && Number(chance.value) >= 0.999) return "ai_only";
  if (chance && (chance.mode === "formula" || Number(chance.value) < 0.999)) return "ai_then_p";
  return fallback || "ai_then_p";
}

function harvestOn(resolved) {
  const paths = [
    ["likers", "harvest", "enabled"],
    ["comments", "harvest", "enabled"],
    ["harvest", "enabled"],
  ];
  let seen = false;
  let any = false;
  for (const p of paths) {
    const v = getPath(resolved, p);
    if (v === undefined) continue;
    seen = true;
    if (v) any = true;
  }
  return seen ? any : null;
}

function taskTitle(task) {
  const note = (task.note || "").trim();
  if (note) return note;
  if (task.plugin === "content_search") {
    const v = csSourceValue(task);
    const labels = {
      hashtag: "Wyszukiwanie: hashtagi",
      keyword: "Wyszukiwanie: słowa kluczowe",
      location: "Wyszukiwanie: lokalizacje",
      tag_variants: "Wyszukiwanie: znaczniki",
      hashtag_plus_tags: "Wyszukiwanie: # + znaczniki",
      keyword_plus_tags: "Wyszukiwanie: słowa + znaczniki",
      all_random: "Wyszukiwanie: mix",
    };
    return labels[v] || "Wyszukiwanie";
  }
  const pl = pluginById(task.plugin);
  return (pl && pl.label) || task.plugin;
}

function taskSub(task) {
  const bits = [];
  const resolved = resolvedForTask(task);
  if (task.plugin === "content_search") {
    bits.push(CS_SRC_LABEL[csSourceValue(task)] || "źródło");
  }
  const h = harvestOn(resolved);
  if (h === true) bits.push("harvest on");
  if (h === false) bits.push("harvest off");
  const bud = itemsRange(task.budget);
  if (bud) bits.push(`budżet ${bud.min}–${bud.max}`);
  return bits.join(" · ");
}

function writeOverride(task, path, value) {
  task.overrides = task.overrides || {};
  setPath(task.overrides, path, value);
  markRunDirty();
}

function paintLimitsChrome() {
  const name = runyActiveName;
  const est = runyEst[name] || {};
  const util = est.utilization || {};
  const over = ["likes", "follows", "comments"].some((k) => util[k] && util[k].over);
  const warn = ["likes", "follows", "comments"].some((k) => util[k] && util[k].over_remaining);
  const bar = $("runy-limits");
  if (bar) {
    // Ten sam układ co Harmonogram: 93/100 + donut, kolor z limitShareColor.
    bar.innerHTML = renderDayLimitPills(util, {
      usedToday: est.used_today,
      showUsed: true,
    });
    bar.className = "run-limits-bar"
      + (over ? " over" : (warn ? " warn" : ""))
      + (runyDirty[name] ? " dirty" : "");
  }
  const w = $("runy-over");
  if (w) w.hidden = !over;
  const del = $("runy-del");
  if (del) del.disabled = name === DEFAULT_QUEUE;
  paintRunEta();
  paintRunList();
}

function paintRunEta() {
  const el = $("runy-eta");
  if (!el) return;
  el.textContent = fmtEtaClock(runEtaSeconds(runyActiveName));
}

function runNames() {
  const sel = $("runy-select");
  if (sel && sel.options.length) {
    return [...sel.options].map((o) => o.value).filter(Boolean);
  }
  return Object.keys(runyQueues);
}

/** Lista run konta — nie sam <select>; kłódka na default. */
function paintRunList() {
  const box = $("runy-list");
  if (!box) return;
  const names = runNames();
  box.innerHTML = "";
  if (!names.length) {
    box.innerHTML = `<div class="insp-empty">Brak runów.</div>`;
    return;
  }
  for (const name of names) {
    const draft = runyQueues[name] || { queue: [] };
    const n = (draft.queue || []).length;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "run-list-item"
      + (name === runyActiveName ? " sel" : "")
      + (runyDirty[name] ? " dirty" : "");
    const lock = name === DEFAULT_QUEUE
      ? `<span class="run-list-lock" title="default jest zarezerwowany">🔒</span>` : "";
    btn.innerHTML = `<span class="run-list-name">${escapeHtml(name)}${lock}</span>
      <span class="run-list-meta">${n} ${n === 1 ? "krok" : "kroków"} · ${escapeHtml(fmtEtaClock(runEtaSeconds(name)))}</span>`;
    btn.addEventListener("click", () => selectRun(name));
    box.appendChild(btn);
  }
}

function selectRun(name) {
  if (!name || name === runyActiveName) return;
  runyActiveName = name;
  runySelectedTaskId = "";
  const runSel = $("runy-select");
  if (runSel) runSel.value = name;
  renderLibrary();
  renderRunEditor();
  estimateRun(runyActiveName);
}

async function loadPluginAccountSettings(acc) {
  try {
    pluginAccountSettings = await api(`/api/plugins/${encodeURIComponent(acc)}`);
  } catch (_) {
    pluginAccountSettings = {};
  }
}

async function refreshRuny() {
  await ensureCatalog();
  await ensureAccounts();
  const sel = $("runy-account");
  const names = accounts.map((a) => a.alias);
  const prefer = runyAccount && names.includes(runyAccount) ? runyAccount : names[0];
  if (sel) fillSelect(sel, names, prefer);
  const acc = (sel && sel.value) || prefer || "";
  if (!acc) {
    $("runy-lib").innerHTML = "";
    const saved = $("runy-lib-saved");
    if (saved) saved.innerHTML = "";
    const listEl = $("runy-list");
    if (listEl) listEl.innerHTML = "";
    $("runy-steps").innerHTML = `<div class="insp-empty">Brak kont.</div>`;
    $("runy-insp").innerHTML = "";
    return;
  }
  if (acc !== runyAccount) {
    runyAccount = acc;
    runyQueues = {};
    runyDirty = {};
    runyEst = {};
    runySelectedTaskId = "";
    pluginAccountSettings = {};
    runyActions = [];
    runyActionEditId = "";
  }
  const gen = ++runyGen;
  try {
    await loadPluginAccountSettings(acc);
    const [presets, actions] = await Promise.all([
      api(`/api/queue-presets/${encodeURIComponent(acc)}`),
      api(`/api/accounts/${encodeURIComponent(acc)}/actions`).catch(() => []),
    ]);
    runyActions = Array.isArray(actions) ? actions : [];
    const list = Array.isArray(presets) && presets.length ? presets : [DEFAULT_QUEUE];
    presetsCache[acc] = list;
    await Promise.all(list.map(async (name) => {
      if (runyDirty[name] && runyQueues[name]) return;
      let data;
      try {
        data = await api(
          `/api/queue/${encodeURIComponent(acc)}?name=${encodeURIComponent(name)}`);
      } catch (_) {
        data = {
          queue: [],
          between_tasks_s: { min: 60, max: 240 },
          on_task_error: "continue",
        };
      }
      if (gen !== runyGen) return;
      runyQueues[name] = ensureTaskIds(data);
    }));
    for (const name of Object.keys(runyQueues)) {
      if (!list.includes(name) && !runyDirty[name]) delete runyQueues[name];
    }
    if (gen !== runyGen) return;
    if (!list.includes(runyActiveName)) runyActiveName = list[0] || DEFAULT_QUEUE;
    const runSel = $("runy-select");
    fillSelect(runSel, list, runyActiveName);
    // fillSelect trzyma prev, gdy nadal jest na liście — po „Utwórz” to stara nazwa.
    // W kreatorze Runy aktywna nazwa jest źródłem prawdy: wymuś selected i załaduj ten run.
    if (runSel && list.includes(runyActiveName)) runSel.value = runyActiveName;
    runyActiveName = (runSel && runSel.value) || runyActiveName;
    renderLibrary();
    renderRunEditor();
    // ETA na liście: każda runa osobno (estimate-actions).
    for (const name of list) estimateRun(name);
  } catch (e) {
    if (gen !== runyGen) return;
    setRunyMsg("Nie udało się wczytać runów: " + (e.message || e), "err");
  }
}

function pluginIconHtml(pid) {
  const spec = PLUGIN_ICONS[pid] || PLUGIN_ICONS.feed_browser;
  return `<span class="lib-ico t-${spec.tone}" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22">${spec.svg}</svg></span>`;
}

function bindLibChipDrag(el, payload) {
  el.draggable = true;
  el.addEventListener("dragstart", (ev) => {
    runyLibDrag = payload;
    ev.dataTransfer.effectAllowed = "copy";
    ev.dataTransfer.setData("text/plain", payload.kind === "plugin" ? payload.id : "action");
    el.classList.add("dragging");
  });
  el.addEventListener("dragend", () => {
    el.classList.remove("dragging");
    runyLibDrag = null;
  });
}

function paintLibTabs() {
  // Plakietki bazowe zawsze w .run-lib-base; tacka to zakładka „Zapisane akcje”.
  document.querySelectorAll(".run-lib-tab").forEach((btn) => {
    btn.classList.add("on");
    if (btn.dataset.libTab === "saved") btn.setAttribute("aria-selected", "true");
  });
  const hint = $("runy-lib-hint");
  if (hint) {
    hint.innerHTML = "Klik albo przeciągnij — nowy krok z własnym <code>id</code>. Zapisane wstawiają <b>kopię</b> override’ów.";
  }
}

function renderLibrary() {
  const box = $("runy-lib");
  if (!box) return;
  paintLibTabs();
  box.innerHTML = "";
  for (const p of libraryPlugins()) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "run-lib-chip run-lib-card g-" + (p.group || "inne");
    btn.title = (p.label || p.id) + " — kliknij albo przeciągnij do kolejki";
    const short = RUNY_LIB_SHORT[p.id] || p.label || p.id;
    btn.innerHTML = `${pluginIconHtml(p.id)}<span class="chip-name">${escapeHtml(short)}</span>`;
    btn.addEventListener("click", () => addRunTask(p.id));
    bindLibChipDrag(btn, { kind: "plugin", id: p.id });
    box.appendChild(btn);
  }
  renderSavedLibrary($("runy-lib-saved"));
}

function renderSavedLibrary(box) {
  if (!box) return;
  box.innerHTML = "";
  if (!runyActions.length) {
    box.innerHTML = `<div class="insp-empty">Brak zapisanych akcji — ustaw krok i kliknij „Zapisz jako akcję” w inspectorze.</div>`;
    return;
  }
  for (const action of runyActions) {
    const row = document.createElement("div");
    row.className = "run-lib-chip saved" + (runyActionEditId === action.id ? " editing" : "");
    const pl = pluginById(action.plugin);
    const typeLabel = (pl && pl.label) || action.plugin || "?";
    if (runyActionEditId === action.id) {
      row.className = "run-lib-item editing";
      row.innerHTML = `<div class="run-lib-edit">
        <input type="text" class="a-name" maxlength="80" placeholder="Nazwa" value="${escapeHtml(action.name || "")}" />
        <textarea class="a-comment" maxlength="240" placeholder="Komentarz">${escapeHtml(action.comment || "")}</textarea>
        <div class="lib-edit-row">
          <button type="button" class="primary a-save">Zapisz</button>
          <button type="button" class="a-cancel">Anuluj</button>
        </div>
      </div>`;
      row.querySelector(".a-save").addEventListener("click", () => {
        saveActionMeta(action.id, row.querySelector(".a-name").value,
          row.querySelector(".a-comment").value);
      });
      row.querySelector(".a-cancel").addEventListener("click", () => {
        runyActionEditId = "";
        renderLibrary();
      });
      box.appendChild(row);
      continue;
    }
    const comment = (action.comment || "").trim();
    row.innerHTML = `<span class="lib-ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="14" height="14">${SAVED_ACTION_ICON}</svg></span>
      <div class="lib-meta">
        <div class="chip-name">${escapeHtml(action.name || action.id)}</div>
        ${comment ? `<div class="lib-desc">${escapeHtml(comment)}</div>` : ""}
        <div class="chip-plugin">${escapeHtml(typeLabel)}</div>
      </div>
      <div class="lib-btns">
        <button type="button" class="a-add">Dodaj</button>
        <button type="button" class="a-edit">Edytuj</button>
        <button type="button" class="danger a-del">Usuń</button>
      </div>`;
    row.querySelector(".a-add").addEventListener("click", (ev) => {
      ev.stopPropagation();
      addRunTaskFromAction(action);
    });
    row.querySelector(".a-edit").addEventListener("click", (ev) => {
      ev.stopPropagation();
      runyActionEditId = action.id;
      renderLibrary();
    });
    row.querySelector(".a-del").addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteSavedAction(action);
    });
    row.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      addRunTaskFromAction(action);
    });
    bindLibChipDrag(row, { kind: "action", action });
    box.appendChild(row);
  }
}

function addRunTask(pid, extra) {
  extra = extra || {};
  const name = runyActiveName;
  const draft = runyQueues[name] || {
    queue: [], between_tasks_s: { min: 60, max: 240 }, on_task_error: "continue",
  };
  draft.queue = draft.queue || [];
  const task = {
    id: newTaskId(),
    plugin: pid,
    note: extra.note || "",
    budget: extra.budget && typeof extra.budget === "object" ? extra.budget : {},
    overrides: extra.overrides && typeof extra.overrides === "object" ? extra.overrides : {},
    enabled: true,
  };
  if (extra.from_action) task.from_action = extra.from_action;
  // content_search: od razu własne źródło, żeby nie dziedziczyć cicho z konta
  // (pomijamy, gdy preset już wniósł overrides — kopia ma wygrać)
  if (pid === "content_search" && !Object.keys(task.overrides).length) {
    const src = (accountPluginVals("content_search").source) || "hashtag";
    task.overrides.source = src;
    task.overrides.run_scope = "phrases";
  }
  draft.queue.push(task);
  runyQueues[name] = draft;
  runySelectedTaskId = task.id;
  markRunDirty();
  renderRunEditor();
  estimateRun(name, true);
}

function addRunTaskFromAction(action) {
  // Kopia, nie referencja live — drugi content_search może mieć inne opcje.
  addRunTask(action.plugin, {
    note: (action.note || action.name || "").trim(),
    budget: cloneJson(action.budget || {}),
    overrides: cloneJson(action.overrides || {}),
    from_action: action.id,
  });
}

async function reloadActions() {
  const acc = runySelectedAccount();
  if (!acc) {
    runyActions = [];
    return;
  }
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(acc)}/actions`);
    runyActions = Array.isArray(data) ? data : [];
  } catch (_) {
    runyActions = [];
  }
}

async function saveActionMeta(slug, name, comment) {
  const acc = runySelectedAccount();
  if (!acc || !slug) return;
  const n = (name || "").trim();
  if (!n) {
    setRunyMsg("Nazwa zapisanej akcji nie może być pusta.", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(acc)}/actions/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: n, comment: (comment || "").trim() }),
    });
    runyActionEditId = "";
    setRunyMsg(`Zaktualizowano „${n}”.`, "ok");
    await reloadActions();
    renderLibrary();
  } catch (e) {
    setRunyMsg(e.message || "nie zapisano nazwy", "err");
  }
}

async function deleteSavedAction(action) {
  const acc = runySelectedAccount();
  if (!acc || !action || !action.id) return;
  const label = action.name || action.id;
  if (!window.confirm(`Usunąć zapisaną akcję „${label}”? Runy zostają — to tylko preset.`)) return;
  try {
    await api(`/api/accounts/${encodeURIComponent(acc)}/actions/${encodeURIComponent(action.id)}`, {
      method: "DELETE",
    });
    setRunyMsg(`Usunięto zapisaną akcję „${label}”.`, "ok");
    await reloadActions();
    renderLibrary();
    renderRunInspector();
  } catch (e) {
    setRunyMsg(e.message || "usuwanie nieudane", "err");
  }
}

async function saveTaskAsAction(task, { updateExisting } = {}) {
  const acc = runySelectedAccount();
  if (!acc || !task) return;
  const nameEl = $("insp-action-name");
  const commentEl = $("insp-action-comment");
  const name = ((nameEl && nameEl.value) || task.note || "").trim();
  const comment = ((commentEl && commentEl.value) || "").trim();
  if (!name) {
    setRunyMsg("Podaj nazwę zapisanej akcji.", "err");
    return;
  }
  const body = {
    name,
    comment,
    plugin: task.plugin,
    overrides: cloneJson(task.overrides || {}),
    budget: cloneJson(task.budget || {}),
    note: (task.note || "").trim(),
  };
  try {
    let saved;
    if (updateExisting && task.from_action) {
      saved = await api(
        `/api/accounts/${encodeURIComponent(acc)}/actions/${encodeURIComponent(task.from_action)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      setRunyMsg(`Zaktualizowano zapisaną akcję „${name}”.`, "ok");
    } else {
      saved = await api(`/api/accounts/${encodeURIComponent(acc)}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (saved && saved.id) task.from_action = saved.id;
      setRunyMsg(`Zapisano akcję „${name}”.`, "ok");
    }
    runyLibTab = "saved";
    await reloadActions();
    renderLibrary();
    renderRunInspector();
  } catch (e) {
    setRunyMsg(e.message || "nie zapisano akcji", "err");
  }
}

function renderRunEditor() {
  // Klucz formularza = task.id — przy braku zaznaczenia bierz pierwszy krok.
  const q = ((currentDraft() || {}).queue) || [];
  if (!runySelectedTaskId || !q.some((t) => t.id === runySelectedTaskId)) {
    runySelectedTaskId = q[0] ? q[0].id : "";
  }
  const title = $("runy-queue-title");
  if (title) title.textContent = "Kolejka " + (runyActiveName || "");
  renderSteps();
  renderRunInspector();
  syncQueueFoot();
  paintLimitsChrome();
}

function switchHtml(checked, extraClass) {
  return `<label class="run-switch ${extraClass || ""}">
    <input type="checkbox" class="t-on" ${checked ? "checked" : ""} title="krok włączony" />
    <i></i>
  </label>`;
}

function taskCardMeta(task) {
  const resolved = resolvedForTask(task);
  const bud = itemsRange(resolved.budget) || itemsRange(task.budget);
  const mins = Number(getPath(resolved, ["budget", "max_duration_min"]));
  const bits = [];
  if (bud) bits.push(`${bud.max} ${pluginUnitShort(task.plugin)}`);
  if (Number.isFinite(mins) && mins > 0) bits.push(`${mins} min`);
  return bits.join(" / ");
}

function applyLibDrop(atIndex) {
  const drag = runyLibDrag;
  if (!drag) return;
  if (drag.kind === "plugin") {
    addRunTask(drag.id);
    if (atIndex != null) {
      const draft = currentDraft();
      if (draft && draft.queue && draft.queue.length) {
        const last = draft.queue.pop();
        const i = Math.max(0, Math.min(atIndex, draft.queue.length));
        draft.queue.splice(i, 0, last);
        renderRunEditor();
      }
    }
  } else if (drag.kind === "action" && drag.action) {
    addRunTaskFromAction(drag.action);
    if (atIndex != null) {
      const draft = currentDraft();
      if (draft && draft.queue && draft.queue.length) {
        const last = draft.queue.pop();
        const i = Math.max(0, Math.min(atIndex, draft.queue.length));
        draft.queue.splice(i, 0, last);
        renderRunEditor();
      }
    }
  }
  runyLibDrag = null;
}

function syncQueueFoot() {
  const draft = currentDraft() || {};
  const gap = draft.between_tasks_s || {};
  const minI = $("runy-gap-min");
  const maxI = $("runy-gap-max");
  if (minI && document.activeElement !== minI) minI.value = gap.min != null ? gap.min : 60;
  if (maxI && document.activeElement !== maxI) maxI.value = gap.max != null ? gap.max : 240;
  const err = draft.on_task_error || "continue";
  document.querySelectorAll('input[name="runy-onerror"]').forEach((r) => {
    r.checked = r.value === err;
  });
}

function applyQueueFoot() {
  const draft = currentDraft();
  if (!draft) return;
  const minI = $("runy-gap-min");
  const maxI = $("runy-gap-max");
  const lo = Number(minI && minI.value);
  const hi = Number(maxI && maxI.value);
  draft.between_tasks_s = {
    min: Number.isFinite(lo) ? lo : 60,
    max: Number.isFinite(hi) ? hi : 240,
  };
  const picked = document.querySelector('input[name="runy-onerror"]:checked');
  draft.on_task_error = (picked && picked.value) || "continue";
  markRunDirty();
}

/** Jedyna ścieżka usunięcia kroku — X na wierszu i #insp-task-del. */
function removeRunTask(taskId) {
  const draft = currentDraft();
  if (!draft || !draft.queue) return;
  const i = draft.queue.findIndex((t) => t.id === taskId);
  if (i < 0) return;
  draft.queue.splice(i, 1);
  if (runySelectedTaskId === taskId) {
    runySelectedTaskId = (draft.queue[i] || draft.queue[i - 1] || {}).id || "";
  }
  markRunDirty();
  renderRunEditor();
  estimateRun(runyActiveName, true);
}

function renderSteps() {
  const box = $("runy-steps");
  if (!box) return;
  const draft = currentDraft() || { queue: [] };
  const tasks = draft.queue || [];
  box.innerHTML = "";
  box.className = "run-steps";
  if (!tasks.length) {
    box.innerHTML = `<div class="insp-empty">Pusty run — kliknij albo upuść akcję z biblioteki.</div>`;
  }
  tasks.forEach((task, i) => {
    if (!task.id) task.id = newTaskId();
    const row = document.createElement("div");
    row.className = "run-step"
      + (task.enabled === false ? " off" : "")
      + (task.id === runySelectedTaskId ? " sel" : "");
    row.dataset.tid = task.id;
    const from = (task.from_action || "").trim();
    const linked = from && runyActions.find((a) => a.id === from);
    const badges = from
      ? `<div class="step-badges"><span class="step-badge">from saved action</span>
         ${linked ? `<span class="step-badge-name">${escapeHtml(linked.name || from)}</span>` : ""}</div>`
      : "";
    const meta = taskCardMeta(task);
    row.innerHTML = `
      <span class="drag-handle" title="Przeciągnij, aby zmienić kolejność" draggable="true">⠿</span>
      <span class="step-num">${i + 1}</span>
      <div class="step-text">
        <div class="step-title">${escapeHtml(taskTitle(task))}</div>
        <div class="step-sub">${escapeHtml(task.plugin)}</div>
        ${badges}
      </div>
      <span class="step-meta">${escapeHtml(meta)}</span>
      ${switchHtml(task.enabled !== false)}
      <button type="button" class="step-del" title="Usuń krok" aria-label="Usuń krok">×</button>`;
    row.addEventListener("click", (ev) => {
      if (ev.target.closest(".t-on") || ev.target.closest(".run-switch")
          || ev.target.closest(".drag-handle") || ev.target.closest(".step-del")) return;
      runySelectedTaskId = task.id;
      renderRunEditor();
    });
    // Ten sam helper co #insp-task-del — bez drugiej ścieżki usuwania.
    row.querySelector(".step-del").addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      removeRunTask(task.id);
    });
    row.querySelector(".t-on").addEventListener("change", (ev) => {
      task.enabled = ev.target.checked;
      markRunDirty();
      renderSteps();
      estimateRun(runyActiveName, true);
    });
    const handle = row.querySelector(".drag-handle");
    handle.addEventListener("dragstart", (ev) => {
      row.classList.add("dragging");
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", String(i));
      runyDragFrom = i;
    });
    handle.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      box.querySelectorAll(".run-step.drag-over").forEach((n) => n.classList.remove("drag-over"));
      runyDragFrom = null;
    });
    row.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = runyLibDrag ? "copy" : "move";
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      row.classList.remove("drag-over");
      if (runyLibDrag) {
        applyLibDrop(i);
        return;
      }
      const fromIdx = runyDragFrom;
      if (fromIdx == null || fromIdx === i) return;
      const [moved] = draft.queue.splice(fromIdx, 1);
      draft.queue.splice(i, 0, moved);
      markRunDirty();
      renderSteps();
    });
    box.appendChild(row);
  });
  const drop = $("runy-drop");
  if (drop) {
    drop.ondragover = (ev) => {
      if (!runyLibDrag) return;
      ev.preventDefault();
      drop.classList.add("drag-over");
    };
    drop.ondragleave = () => drop.classList.remove("drag-over");
    drop.ondrop = (ev) => {
      ev.preventDefault();
      drop.classList.remove("drag-over");
      applyLibDrop(null);
    };
  }
}

function renderRunInspector() {
  const box = $("runy-insp");
  if (!box) return;
  const task = findTask(runyActiveName, runySelectedTaskId);
  if (!task) {
    box.innerHTML = `<div class="insp-empty">Wybierz krok albo dodaj akcję z biblioteki.</div>`;
    return;
  }
  const pl = pluginById(task.plugin);
  const resolved = resolvedForTask(task);
  box.innerHTML = "";
  box.dataset.taskId = task.id;

  const q = ((currentDraft() || {}).queue) || [];
  const idx = q.findIndex((t) => t.id === task.id);
  const head = document.createElement("div");
  head.className = "insp-head";
  head.innerHTML = `<div class="insp-title-row">
      <div class="insp-title">Krok ${idx >= 0 ? idx + 1 : "?"} · ${escapeHtml(taskTitle(task))}</div>
      ${switchHtml(task.enabled !== false, "insp-en")}
    </div>
    <div class="insp-pid">${escapeHtml(task.plugin)} · ${escapeHtml(task.id)}</div>
    ${pl && pl.desc ? `<div class="insp-desc">${escapeHtml(pl.desc)}</div>` : ""}`;
  head.querySelector(".t-on").addEventListener("change", (ev) => {
    task.enabled = ev.target.checked;
    markRunDirty();
    renderSteps();
    estimateRun(runyActiveName, true);
  });
  box.appendChild(head);

  if (task.plugin === "content_search") {
    box.appendChild(renderCsSourceBlock(task));
  }
  if (task.plugin === "publish_media") {
    box.appendChild(renderPmPicksBlock(task));
  }

  if (pl) {
    const { primary, extra, advanced } = splitInspectorFields(pl);
    for (const f of primary) box.appendChild(renderCatalogField(task, f, resolved));
    if (extra.length) {
      const det = document.createElement("details");
      det.className = "adv-box";
      det.innerHTML = `<summary>Więcej opcji</summary>`;
      const inner = document.createElement("div");
      inner.className = "adv-inner";
      for (const f of extra) inner.appendChild(renderCatalogField(task, f, resolved));
      det.appendChild(inner);
      box.appendChild(det);
    }
    if (advanced.length) {
      const det = document.createElement("details");
      det.className = "adv-box";
      det.innerHTML = `<summary>Zaawansowane</summary>`;
      const inner = document.createElement("div");
      inner.className = "adv-inner";
      for (const f of advanced) inner.appendChild(renderCatalogField(task, f, resolved));
      det.appendChild(inner);
      box.appendChild(det);
    }
    wireShowWhen(box, resolved);
  }

  box.appendChild(renderBudgetBlock(task));
  box.appendChild(renderNoteBlock(task));
  box.appendChild(renderSaveAsActionBlock(task));
  box.appendChild(renderInspActions(task));
}

function _pmPickIds(task) {
  const raw = ((task.overrides || {}).item_ids);
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  if (typeof raw === "string" && raw.trim()) return [raw.trim()];
  return [];
}

function renderPmPicksBlock(task) {
  // Picker Treści tego konta — nie surowy input id, nie „następny ready”.
  const wrap = document.createElement("div");
  wrap.className = "f-row pm-picks";
  wrap.innerHTML = `<div class="f-label">Który plik opublikować</div>
    <div class="f-desc">Z Treści tego konta. Puste zaznaczenie = plugin nic nie wrzuci (nie zgaduje kolejki).</div>
    <div class="pm-picks-list" id="pm-picks-list">Ładuję kolejkę Treści…</div>`;
  const list = wrap.querySelector(".pm-picks-list");
  const acc = runySelectedAccount();
  if (!acc) {
    list.textContent = "Wybierz konto w Runach.";
    return wrap;
  }
  api("/api/publish?account=" + encodeURIComponent(acc)).then((data) => {
    const items = ((data && data.items) || []).filter((it) => {
      const st = it.status || "ready";
      return st !== "published";
    });
    const chosen = new Set(_pmPickIds(task));
    if (!items.length) {
      list.innerHTML = `<div class="insp-empty">Brak plików w Treściach tego konta — wrzuć w zakładce Treści.</div>`;
      return;
    }
    list.innerHTML = items.map((it) => {
      const name = ((it.files || [])[0] || {}).canonical_name || it.id;
      const on = chosen.has(it.id) ? " on" : "";
      const thumb = it.is_image && it.file_url
        ? `<img src="${escapeHtml(it.file_url)}" alt="" />`
        : (it.is_video ? "wideo" : "plik");
      const kind = it.kind === "story" ? "Relacja" : "Post";
      return `<button type="button" class="pm-pick${on}" data-pm-id="${escapeHtml(it.id)}">
        <span class="pm-pick-thumb">${thumb}</span>
        <span class="pm-pick-meta">
          <span class="pm-pick-name">${escapeHtml(name)}</span>
          <span class="pm-pick-kind">${kind}</span>
        </span>
      </button>`;
    }).join("");
    list.querySelectorAll(".pm-pick").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.pmId;
        let next = _pmPickIds(task);
        if (next.includes(id)) next = next.filter((x) => x !== id);
        else next = next.concat([id]);
        writeOverride(task, ["item_ids"], next);
        renderSteps();
        renderRunInspector();
        estimateRun(runyActiveName, true);
      });
    });
  }).catch((e) => {
    list.textContent = (e && e.message) || "nie udało się wczytać Treści";
  });
  return wrap;
}

function renderCsSourceBlock(task) {
  const wrap = document.createElement("div");
  wrap.className = "f-row";
  const cur = csSourceValue(task);
  const lab = document.createElement("div");
  lab.className = "f-label";
  lab.textContent = "Źródło tego kroku";
  const sel = document.createElement("select");
  for (const g of CS_SOURCE_GROUPS) {
    const og = document.createElement("optgroup");
    og.label = g.label;
    for (const o of g.opts) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      if (o.value === cur) opt.selected = true;
      og.appendChild(opt);
    }
    sel.appendChild(og);
  }
  sel.addEventListener("change", () => {
    csApplySource(task, sel.value);
    markRunDirty();
    renderRunEditor();
    estimateRun(runyActiveName, true);
  });
  const desc = document.createElement("div");
  desc.className = "f-desc";
  desc.textContent = "Tylko to zadanie — drugi content_search w runie ma własne źródło.";
  wrap.append(lab, sel, desc);
  if (CS_USES_MAP.has(cur)) {
    const kindLab = document.createElement("div");
    kindLab.className = "f-label";
    kindLab.textContent = "Znaczniki z mapy";
    const kindSel = document.createElement("select");
    const ovKind = ((task.overrides || {}).tag_variants_budget || {}).map_seed_kind
      || ((accountPluginVals("content_search").tag_variants_budget) || {}).map_seed_kind
      || "both";
    for (const o of [
      { value: "hashtag", label: "Kluczy #" },
      { value: "keyword", label: "Kluczy keywords" },
      { value: "both", label: "Z # i keywords" },
    ]) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      if (o.value === ovKind) opt.selected = true;
      kindSel.appendChild(opt);
    }
    kindSel.addEventListener("change", () => {
      task.overrides = task.overrides || {};
      const prev = task.overrides.tag_variants_budget || {};
      task.overrides.tag_variants_budget = Object.assign({}, prev, {
        source: "from_map", map_seed_kind: kindSel.value,
      });
      markRunDirty();
      renderSteps();
    });
    wrap.append(kindLab, kindSel);
  }
  return wrap;
}

function renderBudgetBlock(task) {
  const wrap = document.createElement("div");
  wrap.className = "insp-budget";
  const def = pluginItemsDefault(task.plugin);
  const cur = itemsRange(task.budget);
  const resolved = resolvedForTask(task);
  const dur = task.budget && task.budget.max_duration_min != null
    ? task.budget.max_duration_min
    : (getPath(resolved, ["budget", "max_duration_min"]) || "");
  wrap.innerHTML = `<div class="f-label">Budżet (min–max jednostek)</div>
    <div class="f-desc">Puste = z ustawień pluginu konta. Do limitu liczymy max.</div>
    <div class="range-inputs">
      <input type="number" class="b-min" min="0" placeholder="${escapeHtml(def ? String(def.min) : "min")}"
        value="${cur ? cur.min : ""}" />
      <span class="range-sep">–</span>
      <input type="number" class="b-max" min="0" placeholder="${escapeHtml(def ? String(def.max) : "max")}"
        value="${cur ? cur.max : ""}" />
    </div>
    <div class="f-label" style="margin-top:8px">Max czas</div>
    <div class="range-inputs">
      <input type="number" class="b-dur" min="1" placeholder="min"
        value="${dur === "" ? "" : escapeHtml(String(dur))}" />
      <span class="range-unit">min</span>
    </div>`;
  const apply = () => {
    task.budget = task.budget || {};
    const a = wrap.querySelector(".b-min").value === ""
      ? null : Number(wrap.querySelector(".b-min").value);
    const b = wrap.querySelector(".b-max").value === ""
      ? null : Number(wrap.querySelector(".b-max").value);
    if (a == null && b == null) delete task.budget.max_items;
    else {
      const lo = a != null ? a : b;
      const hi = b != null ? b : a;
      task.budget.max_items = { min: Math.min(lo, hi), max: Math.max(lo, hi) };
    }
    const d = wrap.querySelector(".b-dur").value;
    if (d === "") delete task.budget.max_duration_min;
    else task.budget.max_duration_min = Number(d);
    markRunDirty();
    renderSteps();
    estimateRun(runyActiveName, true);
  };
  wrap.querySelector(".b-min").addEventListener("input", apply);
  wrap.querySelector(".b-max").addEventListener("input", apply);
  wrap.querySelector(".b-dur").addEventListener("input", apply);
  return wrap;
}

function renderNoteBlock(task) {
  const wrap = document.createElement("div");
  wrap.className = "insp-note";
  wrap.innerHTML = `<div class="f-label">Notatka</div>
    <input type="text" class="t-note" maxlength="80" placeholder="np. hashtagi + harvest"
      value="${escapeHtml(task.note || "")}" />`;
  wrap.querySelector(".t-note").addEventListener("input", (ev) => {
    task.note = ev.target.value;
    markRunDirty();
    renderSteps();
  });
  return wrap;
}

function renderSaveAsActionBlock(task) {
  const wrap = document.createElement("div");
  wrap.className = "insp-save-action";
  const from = (task.from_action || "").trim();
  const linked = from && runyActions.some((a) => a.id === from);
  const linkedAction = linked ? runyActions.find((a) => a.id === from) : null;
  const defaultName = (task.note || (linkedAction && linkedAction.name) || taskTitle(task) || "").trim();
  const defaultComment = (linkedAction && linkedAction.comment) || "";
  wrap.innerHTML = `<div class="f-label">Zapisz jako akcję</div>
    <div class="f-desc">Nazwany preset kroku (plugin + overrides). Trafi do zakładki „Zapisane akcje” — to nie jest nowy run.</div>
    <input type="text" id="insp-action-name" maxlength="80" placeholder="Nazwa, np. Hashtagi + harvest"
      value="${escapeHtml(defaultName)}" />
    <input type="text" id="insp-action-comment" maxlength="240" placeholder="Komentarz (opcjonalnie)"
      value="${escapeHtml(defaultComment)}" />
    <div class="insp-save-btns">
      <button type="button" class="btn-outline" id="insp-action-save">Zapisz jako akcję</button>
      ${linked ? `<button type="button" id="insp-action-update">Zaktualizuj zapisaną</button>` : ""}
    </div>`;
  wrap.querySelector("#insp-action-save").addEventListener("click", () => {
    saveTaskAsAction(task, { updateExisting: false });
  });
  const upd = wrap.querySelector("#insp-action-update");
  if (upd) {
    upd.addEventListener("click", () => saveTaskAsAction(task, { updateExisting: true }));
  }
  return wrap;
}

function renderInspActions(task) {
  const wrap = document.createElement("div");
  wrap.className = "insp-actions";
  wrap.innerHTML = `
    <button type="button" class="btn-outline danger" id="insp-task-del">Usuń krok</button>
    <button type="button" id="insp-task-test" title="Uruchom plugin z budżetem 3 (jak w klasyku)">Testuj (3)</button>
    <div class="f-desc">Zapisana akcja to kopia override’ów — edycja kroku nie nadpisuje presetu, dopóki nie klikniesz „Zapisz jako akcję”.</div>`;
  wrap.querySelector("#insp-task-del").addEventListener("click", () => removeRunTask(task.id));
  wrap.querySelector("#insp-task-test").addEventListener("click", () => testRunPlugin(task.plugin));
  return wrap;
}

async function testRunPlugin(pid) {
  const acc = runySelectedAccount();
  if (!acc || !pid) return;
  setRunyMsg("Uruchamiam test (3)…");
  try {
    await api("/api/run-plugin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: acc, plugin: pid, max_items: 3 }),
    });
    setRunyMsg("Uruchomiono test (max 3) — podgląd na Na żywo.", "ok");
  } catch (e) {
    setRunyMsg(e.message || "test nieudany", "err");
  }
}

async function dryRunQueue() {
  const acc = runySelectedAccount();
  const out = $("runy-dry-out");
  if (!acc) {
    setRunyMsg("Wybierz konto.", "err");
    return;
  }
  if (out) {
    out.hidden = false;
    out.textContent = "liczę…";
  }
  try {
    const d = await api(
      `/api/queue/${encodeURIComponent(acc)}/resolved?name=${encodeURIComponent(runyActiveName)}`);
    const text = (Array.isArray(d) ? d : []).map((t) =>
      `── ${t.id}  ${t.plugin}`
      + (t.enabled ? "" : "  (WYŁĄCZONE w kolejce)")
      + (t.plugin_enabled_for_account ? "" : "  (plugin WYŁĄCZONY dla konta)")
      + (t.note ? `  — ${t.note}` : "")
      + "\n" + JSON.stringify(t.resolved, null, 1)
    ).join("\n\n") || "(kolejka pusta — zapisz ją najpierw)";
    if (out) out.textContent = text;
    setRunyMsg("Dry-run z zapisanego pliku (nie draftu).", "ok");
  } catch (e) {
    if (out) out.textContent = e.message || "dry-run nieudany";
    setRunyMsg(e.message || "dry-run nieudany", "err");
  }
}

function fieldVisible(f, resolved) {
  const sw = f.show_when;
  if (!sw || !sw.key) return true;
  return getPath(resolved, sw.key) === (sw.equals !== undefined ? sw.equals : true);
}

function wireShowWhen(box, resolved) {
  box.querySelectorAll("[data-show-when]").forEach((row) => {
    try {
      const spec = JSON.parse(row.dataset.showWhen);
      row.hidden = getPath(resolved, spec.key) !== spec.equals;
    } catch (_) { /* zostaw widoczne */ }
  });
}

function renderCatalogField(task, f, resolved) {
  const row = document.createElement("div");
  row.className = "f-row" + (f.type === "bool" ? " checkbox" : "");
  if (f.show_when && f.show_when.key) {
    row.dataset.showWhen = JSON.stringify(f.show_when);
    if (!fieldVisible(f, resolved)) row.hidden = true;
  }
  const cur = getPath(resolved, f.key || []);
  const afterChange = () => {
    renderSteps();
    estimateRun(runyActiveName, true);
    const fresh = resolvedForTask(task);
    const box = $("runy-insp");
    if (box) wireShowWhen(box, fresh);
  };

  if (f.type === "bool") {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!cur;
    cb.addEventListener("change", () => {
      writeOverride(task, f.key, cb.checked);
      afterChange();
    });
    const meta = document.createElement("div");
    meta.innerHTML = `<div class="f-label">${escapeHtml(f.label || "")}</div>
      ${f.desc ? `<div class="f-desc">${escapeHtml(f.desc)}</div>` : ""}`;
    row.append(cb, meta);
    return row;
  }

  row.innerHTML = `<div class="f-label">${escapeHtml(f.label || "")}</div>
    ${f.desc ? `<div class="f-desc">${escapeHtml(f.desc)}</div>` : ""}`;

  if (f.type === "num") {
    const inp = document.createElement("input");
    inp.type = "number";
    if (f.min !== undefined) inp.min = f.min;
    if (f.max !== undefined) inp.max = f.max;
    if (f.step !== undefined) inp.step = f.step;
    inp.value = cur == null ? "" : cur;
    inp.addEventListener("input", () => {
      writeOverride(task, f.key, inp.value === "" ? undefined : Number(inp.value));
      afterChange();
    });
    row.appendChild(inp);
  } else if (f.type === "text") {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = cur == null ? "" : String(cur);
    inp.addEventListener("input", () => {
      writeOverride(task, f.key, inp.value);
      afterChange();
    });
    row.appendChild(inp);
  } else if (f.type === "textarea") {
    const ta = document.createElement("textarea");
    ta.rows = f.rows || 3;
    ta.value = cur == null ? "" : String(cur);
    ta.addEventListener("input", () => {
      writeOverride(task, f.key, ta.value);
      afterChange();
    });
    row.appendChild(ta);
  } else if (f.type === "select") {
    const sel = document.createElement("select");
    const labels = f.option_labels || {};
    for (const opt of (f.options || [])) {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = labels[opt] || opt;
      if (opt === cur) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => {
      writeOverride(task, f.key, sel.value);
      afterChange();
    });
    row.appendChild(sel);
  } else if (f.type === "range") {
    const step = (f.step !== undefined && f.step !== null) ? f.step : 1;
    const wrap = document.createElement("div");
    wrap.className = "range-inputs";
    const minI = document.createElement("input");
    const maxI = document.createElement("input");
    minI.type = "number"; maxI.type = "number";
    minI.step = step; maxI.step = step;
    minI.placeholder = "min"; maxI.placeholder = "max";
    if (cur && typeof cur === "object") {
      minI.value = cur.min != null ? cur.min : "";
      maxI.value = cur.max != null ? cur.max : "";
    }
    const apply = () => {
      const a = minI.value === "" ? null : Number(minI.value);
      const b = maxI.value === "" ? null : Number(maxI.value);
      if (a == null && b == null) writeOverride(task, f.key, undefined);
      else writeOverride(task, f.key, { min: a != null ? a : b, max: b != null ? b : a });
      afterChange();
    };
    minI.addEventListener("input", apply);
    maxI.addEventListener("input", apply);
    wrap.append(minI, document.createTextNode("–"), maxI);
    if (f.unit) {
      const u = document.createElement("span");
      u.className = "range-unit";
      u.textContent = f.unit;
      wrap.appendChild(u);
    }
    row.appendChild(wrap);
  } else if (f.type === "percent") {
    const wrap = document.createElement("div");
    wrap.className = "range-inputs";
    const sl = document.createElement("input");
    sl.type = "range"; sl.min = 0; sl.max = 100; sl.step = 1;
    sl.value = Number(cur ?? 0);
    const out = document.createElement("input");
    out.type = "number"; out.min = 0; out.max = 100;
    out.value = Number(cur ?? 0);
    const apply = () => {
      writeOverride(task, f.key, Number(out.value || 0));
      afterChange();
    };
    sl.addEventListener("input", () => { out.value = sl.value; apply(); });
    out.addEventListener("input", () => { sl.value = out.value; apply(); });
    wrap.append(sl, out);
    row.appendChild(wrap);
  } else if (f.type === "multiselect") {
    const box = document.createElement("div");
    box.className = "multiselect";
    const chosen = Array.isArray(cur) ? cur : [];
    const labels = f.option_labels || {};
    for (const opt of (f.options || [])) {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = chosen.includes(opt);
      cb.addEventListener("change", () => {
        const next = [...box.querySelectorAll("input")]
          .filter((c) => c.checked).map((c) => c.dataset.opt);
        writeOverride(task, f.key, next);
        afterChange();
      });
      cb.dataset.opt = opt;
      lab.append(cb, document.createTextNode(" " + (labels[opt] || opt)));
      box.appendChild(lab);
    }
    row.appendChild(box);
  } else if (f.type === "likes_range") {
    const maxPath = f.max_key || [...(f.key || []).slice(0, -1), "max_likes"];
    const wrap = document.createElement("div");
    wrap.className = "range-inputs";
    const minI = document.createElement("input");
    const maxI = document.createElement("input");
    minI.type = "number"; maxI.type = "number";
    minI.value = cur ?? 0;
    maxI.value = getPath(resolved, maxPath) ?? 0;
    const apply = () => {
      writeOverride(task, f.key, Number(minI.value || 0));
      writeOverride(task, maxPath, Number(maxI.value || 0));
      afterChange();
    };
    minI.addEventListener("input", apply);
    maxI.addEventListener("input", apply);
    wrap.append(minI, document.createTextNode("–"), maxI);
    row.appendChild(wrap);
  } else if (f.type === "like_decision") {
    row.appendChild(renderLikeDecision(task, f, resolved, afterChange));
  } else if (f.type === "chance") {
    // Komentarz: rozgrzewka = tylko suwak %; formuła (score/100) tylko gdy jest AI
    row.appendChild(renderChanceField(task, f, cur, afterChange));
  } else if (f.type === "lines") {
    const ta = document.createElement("textarea");
    ta.rows = 5;
    ta.placeholder = f.placeholder || "jedna pozycja na linię";
    ta.value = Array.isArray(cur) ? cur.join("\n") : "";
    ta.addEventListener("input", () => {
      writeOverride(task, f.key, ta.value.split("\n").map((s) => s.trim()).filter(Boolean));
      afterChange();
    });
    row.appendChild(ta);
  } else if (f.type === "source_phrases") {
    row.appendChild(renderSourcePhrases(task, f, resolved, afterChange));
  } else if (f.type === "tags") {
    row.appendChild(renderTagsField(Array.isArray(cur) ? cur : [], (next) => {
      writeOverride(task, f.key, next);
      afterChange();
    }));
  } else if (f.type === "json") {
    const ta = document.createElement("textarea");
    ta.rows = f.rows || 3;
    ta.value = (cur && typeof cur === "object") ? JSON.stringify(cur, null, 1) : "";
    ta.addEventListener("input", () => {
      const raw = ta.value.trim();
      if (!raw) { writeOverride(task, f.key, {}); afterChange(); return; }
      try { writeOverride(task, f.key, JSON.parse(raw)); afterChange(); } catch (_) { /* czekaj */ }
    });
    row.appendChild(ta);
  } else {
    const skip = document.createElement("div");
    skip.className = "f-desc";
    skip.textContent = "To pole edytujesz w klasycznym panelu (Pluginy konta).";
    row.appendChild(skip);
  }
  return row;
}

function chancePctFrom(cur) {
  const n = Number(cur && cur.value);
  if (!Number.isFinite(n)) return 15;
  // Komentarz: zapis to 0–1; gdy ktoś wpisał 15 zamiast 0.15 — nie rób 1500%
  if (n > 1) return Math.max(0, Math.min(100, Math.round(n)));
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

function renderChanceField(task, f, cur, afterChange) {
  // Komentarz: brak flagi (stary API) = tylko %; formuła tylko gdy katalog ją włączy
  const allowF = f.allow_formula === true;
  const wrap = document.createElement("div");
  wrap.className = "chance-field";
  const extra = document.createElement("div");
  extra.className = "range-inputs";
  let modeSel = null;
  const apply = () => {
    const useF = allowF && modeSel && modeSel.value === "formula";
    if (useF) {
      writeOverride(task, f.key, { mode: "formula", value: "score/100" });
    } else {
      const pct = Number(extra.querySelector(".ch-pct")?.value ?? chancePctFrom(cur));
      writeOverride(task, f.key, {
        mode: "p",
        value: Math.max(0, Math.min(100, pct)) / 100,
      });
    }
    afterChange();
  };
  const rebuild = () => {
    extra.innerHTML = "";
    const useF = allowF && modeSel && modeSel.value === "formula";
    if (useF) {
      const hint = document.createElement("p");
      hint.className = "f-desc";
      hint.textContent = "Szansa = ocena AI / 100. Rolka 80 pkt → 80% szansy na lajk. To nie jest stały procent.";
      extra.appendChild(hint);
      return;
    }
    const sl = document.createElement("input");
    sl.type = "range"; sl.min = 0; sl.max = 100; sl.step = 1;
    sl.className = "ch-pct";
    sl.value = String(chancePctFrom(cur));
    const out = document.createElement("input");
    out.type = "number"; out.min = 0; out.max = 100; out.step = 1;
    out.className = "ch-pct-num";
    out.value = sl.value;
    const unit = document.createElement("span");
    unit.className = "range-unit";
    unit.textContent = "%";
    sl.addEventListener("input", () => { out.value = sl.value; apply(); });
    out.addEventListener("input", () => { sl.value = out.value; apply(); });
    extra.append(sl, out, unit);
  };
  if (allowF) {
    modeSel = document.createElement("select");
    for (const [v, t] of [["p", "stała %"], ["formula", "z wyniku AI"]]) {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      modeSel.appendChild(o);
    }
    modeSel.value = (cur && cur.mode === "formula") ? "formula" : "p";
    modeSel.addEventListener("change", () => { rebuild(); apply(); });
    wrap.appendChild(modeSel);
  }
  rebuild();
  wrap.appendChild(extra);
  return wrap;
}

function renderLikeDecision(task, f, resolved, afterChange) {
  const wrap = document.createElement("div");
  const aiPath = f.ai_key || ["ai", "enabled"];
  const chPath = f.chance_key || ["like_reel", "chance"];
  const aiOn = !!getPath(resolved, aiPath);
  const chance = getPath(resolved, chPath) || { mode: "p", value: 0.5 };
  const mode = inferLikeDecisionMode(aiOn, chance, f.default_mode);
  const sel = document.createElement("select");
  for (const [v, t] of [
    ["ai_only", "Tylko AI"],
    ["chance_only", "Tylko % (bez AI)"],
    ["ai_then_p", "AI, potem %"],
  ]) {
    const o = document.createElement("option");
    o.value = v; o.textContent = t;
    sel.appendChild(o);
  }
  sel.value = mode;
  const extra = document.createElement("div");
  extra.className = "range-inputs";
  const apply = () => {
    const m = sel.value;
    if (m === "ai_only") {
      writeOverride(task, aiPath, true);
      writeOverride(task, chPath, { mode: "p", value: 1.0 });
    } else if (m === "chance_only") {
      const pct = Number(extra.querySelector(".ld-pct")?.value ?? 50);
      writeOverride(task, aiPath, false);
      writeOverride(task, chPath, { mode: "p", value: Math.max(0, Math.min(100, pct)) / 100 });
    } else {
      const useF = extra.querySelector(".ld-formula")?.checked;
      writeOverride(task, aiPath, true);
      if (useF) writeOverride(task, chPath, { mode: "formula", value: "score/100" });
      else {
        const pct = Number(extra.querySelector(".ld-pct")?.value ?? 60);
        writeOverride(task, chPath, { mode: "p", value: Math.max(0, Math.min(100, pct)) / 100 });
      }
    }
    afterChange();
  };
  const rebuild = () => {
    extra.innerHTML = "";
    const m = sel.value;
    if (m === "ai_only") return;
    if (m === "ai_then_p") {
      const useF = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "ld-formula";
      cb.checked = chance.mode === "formula";
      cb.addEventListener("change", apply);
      useF.append(cb, document.createTextNode(" szansa = ocena AI / 100"));
      extra.appendChild(useF);
    }
    if (m === "chance_only" || (m === "ai_then_p" && chance.mode !== "formula")) {
      const sl = document.createElement("input");
      sl.type = "range"; sl.min = 0; sl.max = 100; sl.className = "ld-pct";
      sl.value = chance.mode === "p" ? Math.round(Number(chance.value || 0) * 100) : 50;
      sl.addEventListener("input", apply);
      extra.append(sl, document.createTextNode(" %"));
    }
  };
  sel.addEventListener("change", () => { rebuild(); apply(); });
  rebuild();
  wrap.append(sel, extra);
  return wrap;
}

function renderSourcePhrases(task, f, resolved, afterChange) {
  const wrap = document.createElement("div");
  const phrases = getPath(resolved, f.key || ["phrases"]) || {};
  const buckets = [
    { key: "hashtags", label: "Hashtagi" },
    { key: "keywords", label: "Słowa kluczowe" },
    { key: "locations", label: "Lokalizacje" },
  ];
  for (const b of buckets) {
    const lab = document.createElement("div");
    lab.className = "f-label";
    lab.textContent = b.label;
    const ta = document.createElement("textarea");
    ta.rows = 3;
    ta.placeholder = "jedna fraza na linię";
    const list = Array.isArray(phrases[b.key]) ? phrases[b.key] : [];
    ta.value = list.join("\n");
    ta.addEventListener("input", () => {
      const next = ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
      writeOverride(task, [...(f.key || ["phrases"]), b.key], next);
      afterChange();
    });
    wrap.append(lab, ta);
  }
  return wrap;
}

function renderTagsField(items, onChange) {
  const box = document.createElement("div");
  box.className = "tags-input";
  const tags = items.slice();
  const inp = document.createElement("input");
  inp.type = "text";
  inp.placeholder = "wpisz i Enter";
  const paint = () => {
    box.querySelectorAll(".tag").forEach((el) => el.remove());
    tags.forEach((t, i) => {
      const chip = document.createElement("span");
      chip.className = "tag";
      chip.appendChild(document.createTextNode(t + " "));
      const rm = document.createElement("span");
      rm.className = "rm";
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        tags.splice(i, 1);
        paint();
        onChange(tags.slice());
      });
      chip.appendChild(rm);
      box.insertBefore(chip, inp);
    });
  };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && inp.value.trim()) {
      e.preventDefault();
      tags.push(inp.value.trim());
      inp.value = "";
      paint();
      onChange(tags.slice());
    }
  });
  box.appendChild(inp);
  paint();
  return box;
}

function estimateRun(name, debounce) {
  const acc = runySelectedAccount();
  if (!acc) return;
  const fire = async () => {
    try {
      const d = await api("/api/estimate-actions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account: acc,
          scenario: name,
          queue_override: runyQueues[name] || { queue: [] },
        }),
      });
      runyEst[name] = d;
      if (name === runyActiveName) paintLimitsChrome();
      else paintRunList();
    } catch (_) { /* pasek zostaje */ }
  };
  if (!debounce) {
    fire();
    return;
  }
  clearTimeout(runyEstTimer[name]);
  runyEstTimer[name] = setTimeout(fire, 400);
}

async function saveRun(name) {
  const acc = runySelectedAccount();
  const draft = runyQueues[name];
  if (!acc || !draft) return;
  try {
    await api(`/api/queue/${encodeURIComponent(acc)}?name=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    runyDirty[name] = false;
    delete presetsCache[acc];
    setRunyMsg(`Zapisano „${name}”.`, "ok");
    paintLimitsChrome();
  } catch (e) {
    setRunyMsg(e.message || "zapis nieudany", "err");
  }
}

async function deleteRun(name) {
  if (name === DEFAULT_QUEUE) return;
  const acc = runySelectedAccount();
  if (!acc) return;
  if (!window.confirm(`Usunąć run „${name}”? Plik scenariusza zniknie.`)) return;
  try {
    await api(`/api/queue/${encodeURIComponent(acc)}/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    delete runyQueues[name];
    delete runyDirty[name];
    delete runyEst[name];
    delete presetsCache[acc];
    runyActiveName = DEFAULT_QUEUE;
    runySelectedTaskId = "";
    setRunyMsg(`Usunięto „${name}”.`, "ok");
    await refreshRuny();
  } catch (e) {
    setRunyMsg(e.message || "usuwanie nieudane", "err");
  }
}

async function createRun() {
  const acc = runySelectedAccount();
  const raw = ($("runy-new-name").value || "").trim();
  if (!acc) {
    setRunyMsg("Wybierz konto.", "err");
    return;
  }
  if (!RUN_NAME_RE.test(raw)) {
    setRunyMsg("Nazwa: 1–40 znaków, litery/cyfry/._- (bez spacji).", "err");
    return;
  }
  if (raw === DEFAULT_QUEUE) {
    setRunyMsg("„default” jest zarezerwowany — nie twórz drugiego.", "err");
    return;
  }
  try {
    await api(`/api/queue/${encodeURIComponent(acc)}?name=${encodeURIComponent(raw)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        queue: [],
        between_tasks_s: { min: 60, max: 240 },
        on_task_error: "continue",
      }),
    });
    $("runy-new-name").value = "";
    delete presetsCache[acc];
    runyActiveName = raw;
    runySelectedTaskId = "";
    setRunyMsg(`Utworzono „${raw}”.`, "ok");
    await refreshRuny();
  } catch (e) {
    setRunyMsg(e.message || "nie utworzono", "err");
  }
}

// --- Klon IG: tożsamość → APK (appcloner) → adb install (job panelu) ------
let cloneJobBusy = false;
let clonePollTimer = null;
let cloneFocusAlias = "";
let pendingClonePrefill = null;  // po sukcesie: {pkg, device, identity}
let cloneHandledOk = false;      // nie otwieraj dialogu dwa razy przy tym samym OK
let cloneWaiters = [];           // Promise z waitForCloneJob()

function deviceHasAdb(dev) {
  const adb = ((dev && dev.adb_id) || "").trim();
  return !!(adb && adb !== "REPLACE");
}

/** Alias z device + sufiks APPID, bez kolizji z istniejącymi kontami. */
function suggestedAliasForClone(device, pkg) {
  const suf = ((pkg || "").split(".").pop() || "clone").replace(/[^A-Za-z0-9_]+/g, "_");
  const dev = (device || "clone").replace(/[^A-Za-z0-9_]+/g, "_");
  const base = `${dev}_${suf}`;
  const taken = new Set((accounts || []).map((a) => a.alias));
  if (!taken.has(base)) return base;
  for (let i = 2; i < 80; i++) {
    if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
  }
  return base;
}

function setCloneLog(text) {
  const el = $("kn-clone-log");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

function setCloneResult(text) {
  const el = $("kn-clone-result");
  if (el) el.textContent = text || "";
}

function paintKnDeviceModel(dev) {
  // Komentarz: ten sam fmtModel co karty Floty (_ → -); puste → hint na Odśwież
  const el = $("kn-device-model");
  if (!el) return;
  if (!dev) {
    el.textContent = "";
    return;
  }
  const raw = (dev.model != null ? String(dev.model) : "").trim();
  if (!raw || raw === "REPLACE") {
    el.textContent = "brak modelu — Odśwież, żeby dociągnąć z telefonu";
    return;
  }
  el.textContent = `model ${fmtModel(raw)}`;
}

function syncCloneCreateEnabled() {
  const kn = $("kn-ok");
  const cancel = $("kn-clone-cancel");
  const refresh = $("kn-refresh");
  const alias = ($("kn-device") && $("kn-device").value) || "";
  const dev = (devices || []).find((d) => d.alias === alias);
  const adbOk = deviceHasAdb(dev);
  const created = !!(typeof knReadyAlias !== "undefined" && knReadyAlias);
  if (kn) kn.disabled = cloneJobBusy || created || !adbOk || !alias;
  if (cancel) cancel.disabled = !cloneJobBusy;
  if (refresh) refresh.disabled = cloneJobBusy;
  const meta = $("kn-clone-meta");
  if (meta) {
    if (!dev) meta.textContent = "brak urządzenia";
    else if (!adbOk) {
      const raw = (dev.model != null ? String(dev.model) : "").trim();
      const modelBit = (raw && raw !== "REPLACE")
        ? ` · model ${fmtModel(raw)}`
        : " · model: brak";
      meta.textContent = `${alias} — brak adb_id (nie da się zainstalować klona)${modelBit}`;
    } else {
      const n = (dev.clones || []).length;
      const raw = (dev.model != null ? String(dev.model) : "").trim();
      const modelBit = (raw && raw !== "REPLACE")
        ? ` · model ${fmtModel(raw)}`
        : " · model: brak";
      meta.textContent = `${alias} · adb ${dev.adb_id} · ${n} klon(ów)${modelBit}`;
    }
  }
  paintKnDeviceModel(dev);
}

function applyCloneSuccessToForm(pkg, device, identity) {
  pendingClonePrefill = { pkg, device, identity };
  if (typeof openNewAccount === "function") openNewAccount();
}

function notifyCloneWaiters(st, err) {
  const list = cloneWaiters;
  cloneWaiters = [];
  list.forEach((w) => (err ? w.reject(err) : w.resolve(st)));
}

function waitForCloneJob() {
  return new Promise((resolve, reject) => {
    cloneWaiters.push({ resolve, reject });
  });
}

async function pollCloneJob() {
  try {
    const st = await api("/api/clones/status");
    setCloneLog(st.log || (st.log_tail || []).join("\n"));
    if (st.running) {
      cloneHandledOk = false;
      cloneJobBusy = true;
      syncCloneCreateEnabled();
      showBanner("kn-msg", "Klonowanie w toku…", "ok");
      return true;
    }
    if (clonePollTimer) {
      clearInterval(clonePollTimer);
      clonePollTimer = null;
    }
    cloneJobBusy = false;
    syncCloneCreateEnabled();
    if (st.state === "ok" && st.package) {
      const id = st.identity || {};
      const extra = `APPID ${st.package} · model ${id.model || "—"} · android-id ${id.android_id || "—"} · MAC ${id.mac || "—"} · IMEI ${id.imei || "—"}`;
      setCloneResult(extra);
      showBanner("kn-msg", `Zainstalowano ${st.package}`, "ok");
      notifyCloneWaiters(st);
      if (!cloneHandledOk) {
        cloneHandledOk = true;
        await refreshFlota();
        if (typeof refreshKonta === "function") await refreshKonta({ force: true });
      }
    } else if (st.state === "error") {
      const err = new Error(st.error || "clone job");
      showBanner("kn-msg", "Błąd klona: " + err.message, "err");
      notifyCloneWaiters(null, err);
    }
    return false;
  } catch (e) {
    showBanner("kn-msg", "Status klona: " + (e.message || e), "err");
    return true;
  }
}

async function startCloneJob(deviceAlias, identity) {
  if (!deviceAlias) throw new Error("wybierz urządzenie");
  cloneFocusAlias = deviceAlias;
  cloneHandledOk = false;
  cloneJobBusy = true;
  syncCloneCreateEnabled();
  setCloneResult("");
  const done = waitForCloneJob();
  try {
    const body = { device_alias: deviceAlias };
    if (identity) body.identity = identity;
    const st = await api("/api/clones/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setCloneLog(st.log || "");
  } catch (e) {
    cloneJobBusy = false;
    syncCloneCreateEnabled();
    notifyCloneWaiters(null, e);
    throw e;
  }
  if (clonePollTimer) clearInterval(clonePollTimer);
  clonePollTimer = setInterval(() => { pollCloneJob(); }, 1500);
  await pollCloneJob();
  return done;
}

async function cancelCloneJob() {
  await api("/api/clones/cancel", { method: "POST" });
  await pollCloneJob();
}

async function scanDeviceClones(deviceAlias) {
  if (!deviceAlias) throw new Error("wybierz urządzenie");
  const d = await api("/api/clones/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_alias: deviceAlias }),
  });
  await refreshFlota();
  if (typeof refreshKonta === "function") await refreshKonta({ force: true });
  if (typeof fillNewAccountPkgs === "function") fillNewAccountPkgs();
  const n = (d.added || []).length;
  return n ? `dopisano ${n} pakiet(ów)` : "brak nowych pakietów com.instagram.*";
}

async function resumeCloneJobPoll() {
  try {
    const st = await api("/api/clones/status");
    if (!st.running) {
      syncCloneCreateEnabled();
      return;
    }
    cloneJobBusy = true;
    cloneFocusAlias = st.device_alias || cloneFocusAlias;
    syncCloneCreateEnabled();
    if (clonePollTimer) clearInterval(clonePollTimer);
    clonePollTimer = setInterval(() => { pollCloneJob(); }, 1500);
    await pollCloneJob();
  } catch (_) { /* brak joba / panel świeży */ }
}

// --- Flota: karty urządzeń, ADB, hydrate, lock (bez diagnostyki ekranu) ----

let flotaGen = 0;
let flotaCalibAlias = "";

function calibOf(dev) {
  // Komentarz: GET /api/devices → calibrate.status (idle gdy brak pliku)
  return (dev && dev.calibrate) || { status: "idle" };
}

function calibBtnLabel(dev) {
  const st = (calibOf(dev).status || "idle");
  if (st === "needs_agent") return "Agent czeka";
  if (st === "running") return "Kalibracja…";
  if (st === "done") return "Kalibruj ponownie";
  if (st === "failed") return "Kalibruj ponownie";
  return "Kalibruj";
}

function paintFlotaCalib() {
  const idle = $("flota-calib-idle");
  const body = $("flota-calib-body");
  if (!body) return;
  let alias = flotaCalibAlias;
  if (!alias) {
    const waiting = (devices || []).find((d) => {
      const st = calibOf(d).status;
      return st === "needs_agent" || st === "running";
    });
    alias = waiting ? waiting.alias : "";
    if (alias) flotaCalibAlias = alias;
  }
  const dev = (devices || []).find((d) => d.alias === alias);
  const cal = calibOf(dev);
  if (!dev || cal.status === "idle") {
    if (idle) idle.hidden = false;
    body.hidden = true;
    body.innerHTML = "";
    return;
  }
  if (idle) idle.hidden = true;
  body.hidden = false;
  const exp = Array.isArray(cal.expected) ? cal.expected.join(", ") : "";
  const shot = cal.has_shot
    ? `<img class="flota-calib-shot" alt="screen kroku" src="/api/devices/${encodeURIComponent(alias)}/calibrate/shot?t=${Date.now()}" />`
    : "";
  const q = cal.status === "needs_agent"
    ? `<p class="flota-calib-q">${escapeHtml(cal.question || "Sprawdź screen.")}</p>
       <div class="flota-calib-actions">
         <button type="button" class="primary" data-calib-act="ok">OK — ten ekran</button>
         <button type="button" data-calib-act="skip">Pomiń krok</button>
         <button type="button" data-calib-act="retry">Ponów dump</button>
         <button type="button" class="danger" data-calib-act="abort">Przerwij</button>
       </div>`
    : (cal.status === "done"
      ? `<p class="flota-calib-q">Gotowe — mapa zapisana.</p>`
      : (cal.status === "failed"
        ? `<p class="flota-calib-q">Przerwane${cal.note ? ": " + escapeHtml(cal.note) : ""}.</p>`
        : `<p class="flota-calib-q">Idzie krok <code>${escapeHtml(cal.step || "?")}</code>…</p>`));
  body.innerHTML = `<p class="flota-calib-meta">${escapeHtml(alias)} · ${escapeHtml(cal.account || "—")} · ${escapeHtml(cal.status)}
    <br>krok <code>${escapeHtml(cal.step || "?")}</code>
    ${exp ? `<br>oczekiwano: ${escapeHtml(exp)}` : ""}
    ${cal.actual ? `<br>widzę: <code>${escapeHtml(cal.actual)}</code>` : ""}</p>
    ${shot}${q}`;
}

async function startFlotaCalib(alias) {
  flotaCalibAlias = alias;
  const dev = (devices || []).find((d) => d.alias === alias);
  const st = calibOf(dev).status;
  if (st === "running" || st === "needs_agent") {
    paintFlotaCalib();
    setFlotaMsg(`Kalibracja ${alias}: ${st === "needs_agent" ? "czekam na agenta" : "w toku"}`, "ok");
    return;
  }
  setFlotaMsg(`Start kalibracji ${alias}…`);
  try {
    await api(`/api/devices/${encodeURIComponent(alias)}/calibrate`, { method: "POST" });
    setFlotaMsg(`Kalibracja ${alias} wystartowała — kroki + dump.`, "ok");
  } catch (e) {
    setFlotaMsg(e.message || "kalibracja nie wystartowała", "err");
  }
  await refreshFlota();
}

async function replyFlotaCalib(action) {
  const alias = flotaCalibAlias;
  if (!alias) return;
  try {
    await api(`/api/devices/${encodeURIComponent(alias)}/calibrate/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    setFlotaMsg(`Odpowiedź: ${action}`, "ok");
  } catch (e) {
    setFlotaMsg(e.message || "nie zapisano odpowiedzi", "err");
  }
  await refreshFlota();
}

async function refreshFlotaCalib() {
  // Komentarz: lekki poll — bez przebudowy kart (preview nie miga)
  if (currentView() !== "flota") return;
  try {
    const dev = await api("/api/devices");
    if (!Array.isArray(dev)) return;
    devices = dev;
    paintFlotaCalib();
    for (const d of devices) {
      const card = document.querySelector(`.flota-card[data-alias="${CSS.escape(d.alias)}"]`);
      if (!card) continue;
      const btn = card.querySelector(".btn-calib");
      if (btn && !btn.disabled) btn.textContent = calibBtnLabel(d);
      card.classList.toggle("needs-agent", calibOf(d).status === "needs_agent");
    }
  } catch (_) { /* poll */ }
}

function setFlotaMsg(text, kind) {
  showBanner("flota-msg", text, kind);
}

function fmtRes(r) {
  return Array.isArray(r) && r.length === 2 ? `${r[0]}×${r[1]}` : "—";
}

function fmtModel(m) {
  return m ? String(m).replace(/_/g, "-") : "—";
}

function cloneShort(pkg) {
  if (!pkg) return "?";
  const m = String(pkg).match(/\.([^.]+)$/);
  return m ? m[1] : pkg;
}

function paintFlotaAdbRaw(box) {
  if (!box) return;
  const rows = adbRows || [];
  if (!rows.length) {
    box.innerHTML = `<div class="empty">Brak telefonów w <code>adb devices</code>.</div>`;
    return;
  }
  box.innerHTML = rows.map((d) => {
    const st = d.state || "?";
    const ok = st === "device";
    const serial = d.serial || "(brak)";
    return `<div class="flota-adb-row" data-serial="${escapeHtml(d.serial || "")}">
      <span class="flota-adb-serial">${escapeHtml(serial)}</span>
      <span class="badge ${ok ? "online" : "offline"}">${escapeHtml(st)}</span>
    </div>`;
  }).join("");
}

function flotaCardHtml(dev) {
  const alias = dev.alias || "";
  const online = isOnline(dev);
  const slot = sessionOnDevice(alias);
  const lockOwner = dev.claimed_by || (dev.lock && dev.lock.owner) || "";
  const locked = !!dev.locked;
  const accs = (Array.isArray(dev.accounts) && dev.accounts.length)
    ? dev.accounts
    : accountsOnDevice(alias);
  const clones = dev.clones || [];
  const ph = online ? "brak sesji" : "Urządzenie offline";
  const statusHtml = !online
    ? `<div class="flota-status warn">ADB nie widzi urządzenia</div>`
    : locked
      ? `<div class="flota-status ok">Hydrate OK · ${escapeHtml(lockOwner || "?")} (zablokowany)</div>`
      : `<div class="flota-status ok">Hydrate OK</div>`;
  const schedOn = dev.scheduler_enabled !== false;
  const cal = calibOf(dev);
  const calibBtn = online
    ? `<button type="button" class="btn-calib">${escapeHtml(calibBtnLabel(dev))}</button>`
    : `<button type="button" class="btn-calib" disabled>Kalibruj</button>`;
  const actions = online
    ? `<button type="button" class="primary btn-preview">Podgląd</button>
       <button type="button" class="btn-hydrate">Hydrate</button>
       ${calibBtn}`
    : `<button type="button" class="primary btn-connect">Podłącz</button>
       <button type="button" class="btn-hydrate" disabled>Hydrate</button>
       ${calibBtn}`;
  const cloneChips = clones.map((c) => {
    const on = !!(c.claimed_by && (
      c.claimed_by === lockOwner || (slot && c.claimed_by === slot.account)
    ));
    return `<span class="flota-chip${on ? " on" : ""}" title="${escapeHtml(c.package || "")}">${escapeHtml(cloneShort(c.package))}</span>`;
  }).join("") || `<span class="flota-chip">—</span>`;
  const accChips = accs.map((name) => {
    const on = (slot && slot.account === name) || (lockOwner && name === lockOwner);
    return `<span class="flota-chip${on ? " on" : ""}">${escapeHtml(name)}</span>`;
  }).join("") || `<span class="flota-chip">—</span>`;
  const port = dev.scrcpy_port != null ? `:${dev.scrcpy_port}` : "—";
  const fps = (dev.scrcpy_max_fps != null && Number.isFinite(Number(dev.scrcpy_max_fps)))
    ? Number(dev.scrcpy_max_fps) : 15;
  return `<article class="flota-card ${online ? "online" : "offline"}${cal.status === "needs_agent" ? " needs-agent" : ""}" data-alias="${escapeHtml(alias)}">
    <div class="card-head">
      <div>
        <h2>${escapeHtml(alias)}</h2>
        <div class="flota-model">${escapeHtml(fmtModel(dev.model))}</div>
      </div>
      <span class="badge ${online ? "online" : "offline"}">${online ? "ONLINE" : "OFFLINE"}</span>
    </div>
    <div class="flota-body">
      <div class="flota-specs">
        <div class="meta-row"><span class="k">Android</span><span class="v">${escapeHtml(dev.android_version || "—")}</span></div>
        <div class="meta-row"><span class="k">Rozdzielczość</span><span class="v">${escapeHtml(fmtRes(dev.resolution))}</span></div>
        <div class="meta-row"><span class="k">Serial</span><span class="v">${escapeHtml(dev.adb_id || "—")}</span></div>
        <div class="meta-row"><span class="k">scrcpy</span><span class="v">${escapeHtml(port)}</span></div>
      </div>
      <div class="preview">
        <img alt="" hidden />
        <div class="preview-ph">${escapeHtml(ph)}</div>
      </div>
    </div>
    <div class="flota-chips-block">
      <div class="k">Clone sloty</div>
      <div class="flota-chips">${cloneChips}</div>
    </div>
    <div class="flota-chips-block">
      <div class="k">Konto (przypisane)</div>
      <div class="flota-chips">${accChips}</div>
    </div>
    ${statusHtml}
    <label class="flota-sched">
      <input type="checkbox" class="flota-sched-cb" ${schedOn ? "checked" : ""} />
      Scheduler ${schedOn ? "ON" : "OFF"}
    </label>
    <label class="flota-fps">
      <span class="k">FPS podglądu</span>
      <span class="v">${escapeHtml(String(fps))}</span>
      <span class="hint">zmień na Na żywo</span>
    </label>
    <div class="card-actions">${actions}</div>
  </article>`;
}

function paintFlota() {
  const grid = $("flota-grid");
  const rawBox = $("flota-adb-raw");
  const liveEl = $("flota-adb-live");
  if (!grid) return;
  const onlineN = devices.filter(isOnline).length;
  if (liveEl) {
    liveEl.textContent = `Live ADB: ${onlineN} online / ${devices.length - onlineN} offline`;
  }
  if (!devices.length) {
    grid.innerHTML = `<div class="empty">Brak urządzeń w config/devices/ — zarejestruj pierwsze.</div>`;
  } else {
    grid.innerHTML = devices.map(flotaCardHtml).join("");
  }
  paintFlotaAdbRaw(rawBox);
  paintFlotaCalib();
  syncCloneCreateEnabled();
  if (currentView() === "flota") updateSessionLine();
}

async function refreshFlota() {
  const gen = ++flotaGen;
  try {
    const [dev, adb, acc, st] = await Promise.all([
      api("/api/devices"),
      api("/api/adb-devices").catch(() => []),
      api("/api/accounts"),
      api("/api/status"),
    ]);
    if (gen !== flotaGen) return;
    devices = Array.isArray(dev) ? dev : [];
    adbRows = Array.isArray(adb) ? adb : [];
    accounts = Array.isArray(acc) ? acc : [];
    status = st || {};
    paintFlota();
  } catch (e) {
    setFlotaMsg("Nie udało się wczytać floty: " + (e.message || e), "err");
  }
}

async function refreshFlotaPreview(alias) {
  const card = document.querySelector(`.flota-card[data-alias="${CSS.escape(alias)}"]`);
  if (!card) return;
  const box = card.querySelector(".preview");
  const img = card.querySelector("img");
  const url = `/api/devices/${encodeURIComponent(alias)}/preview?t=${Date.now()}`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (r.status !== 200) {
      box.classList.remove("has-frame");
      img.hidden = true;
      img.removeAttribute("src");
      return;
    }
    const blob = await r.blob();
    if (!blob.size) return;
    const obj = URL.createObjectURL(blob);
    img.alt = `Podgląd ${alias}`;
    img.src = obj;
    img.hidden = false;
    box.classList.add("has-frame");
  } catch (_) { /* placeholder zostaje */ }
}

async function hydrateFlotaDevice(alias) {
  setFlotaMsg(`Hydrate ${alias}…`);
  try {
    await api(`/api/devices/${encodeURIComponent(alias)}/hydrate`, { method: "POST" });
    setFlotaMsg(`Hydrate ${alias} OK.`, "ok");
  } catch (e) {
    setFlotaMsg(e.message || "hydrate nieudany", "err");
  }
  await refreshFlota();
}

async function connectFlotaDevice(alias) {
  // OFFLINE: Podłącz = odśwież ADB + spróbuj hydrate.
  setFlotaMsg(`Podłączam ${alias}…`);
  try {
    await api(`/api/devices/${encodeURIComponent(alias)}/hydrate`, { method: "POST" });
    setFlotaMsg(`Podłączono ${alias}.`, "ok");
  } catch (e) {
    setFlotaMsg("ADB nie widzi urządzenia: " + (e.message || e), "err");
  }
  await refreshFlota();
}

async function toggleFlotaScheduler(alias, enabled) {
  try {
    await api(`/api/devices/${encodeURIComponent(alias)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scheduler_enabled: enabled }),
    });
    setFlotaMsg(`Scheduler ${alias}: ${enabled ? "ON" : "OFF"}`, "ok");
  } catch (e) {
    setFlotaMsg(e.message || "nie zapisano scheduler", "err");
  }
  await refreshFlota();
}

/* FPS edytuje Na żywo (setLiveScrcpyFps) — Flota tylko podgląd wartości. */

async function registerFlotaDevice() {
  const alias = (($("flota-alias") || {}).value || "").trim();
  const adbId = (($("flota-adb-id") || {}).value || "").trim();
  if (!alias || !adbId) {
    setFlotaMsg("Wymagane: alias i adb_id.", "err");
    return;
  }
  try {
    const d = await api("/api/devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias, adb_id: adbId }),
    });
    if ($("flota-alias")) $("flota-alias").value = "";
    if ($("flota-adb-id")) $("flota-adb-id").value = "";
    if (d.hydrated) setFlotaMsg(`Zarejestrowano '${alias}'.`, "ok");
    else setFlotaMsg(`Zapisano '${alias}', ADB nie zczytał hardware: ${d.warning || "?"}`, "err");
    await refreshFlota();
  } catch (e) {
    setFlotaMsg(e.message || "rejestracja nieudana", "err");
  }
}

function onFlotaGridClick(ev) {
  const card = ev.target.closest(".flota-card");
  if (!card) return;
  const alias = card.dataset.alias;
  if (ev.target.closest(".btn-preview")) {
    goLive(alias);
    return;
  }
  if (ev.target.closest(".btn-hydrate") && !ev.target.closest(".btn-hydrate").disabled) {
    hydrateFlotaDevice(alias);
    return;
  }
  if (ev.target.closest(".btn-connect")) {
    connectFlotaDevice(alias);
    return;
  }
  if (ev.target.closest(".btn-calib") && !ev.target.closest(".btn-calib").disabled) {
    startFlotaCalib(alias);
  }
}

function onFlotaGridChange(ev) {
  const card = ev.target.closest(".flota-card");
  if (!card) return;
  if (ev.target.classList.contains("flota-sched-cb")) {
    toggleFlotaScheduler(card.dataset.alias, ev.target.checked);
  }
}

function bindFlotaOnce() {
  const grid = $("flota-grid");
  if (grid && !grid.dataset.bound) {
    grid.dataset.bound = "1";
    grid.addEventListener("click", onFlotaGridClick);
    grid.addEventListener("change", onFlotaGridChange);
  }
  const calibBox = $("flota-calib-panel");
  if (calibBox && !calibBox.dataset.bound) {
    calibBox.dataset.bound = "1";
    calibBox.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-calib-act]");
      if (!btn) return;
      replyFlotaCalib(btn.getAttribute("data-calib-act"));
    });
  }
  const raw = $("flota-adb-raw");
  if (raw && !raw.dataset.bound) {
    raw.dataset.bound = "1";
    raw.addEventListener("click", (ev) => {
      const row = ev.target.closest(".flota-adb-row");
      if (!row || !row.dataset.serial) return;
      if ($("flota-adb-id")) $("flota-adb-id").value = row.dataset.serial;
    });
  }
  const focusBtn = $("flota-reg-focus");
  if (focusBtn && !focusBtn.dataset.bound) {
    focusBtn.dataset.bound = "1";
    focusBtn.addEventListener("click", () => {
      const panel = $("flota-reg-panel");
      if (panel) panel.scrollIntoView({ block: "nearest" });
      if ($("flota-alias")) $("flota-alias").focus();
    });
  }
  const refreshBtn = $("flota-adb-refresh");
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "1";
    refreshBtn.addEventListener("click", () => refreshFlota());
  }
  const submit = $("flota-reg-submit");
  if (submit && !submit.dataset.bound) {
    submit.dataset.bound = "1";
    submit.addEventListener("click", () => registerFlotaDevice());
  }
}

// --- Harmonogram ----------------------------------------------------------
// Siatka floty: wiersz = telefon, kolumny = Pn–Nd z datami tygodnia.
// API cyklu (1/3/7) mapujemy na kalendarz jak cycle_index w device_planner.

function setTlMsg(text, kind) {
  showBanner("tl-msg", text, kind);
}

function tlEstKey(acc, scenario) {
  return `${acc}|${scenario || DEFAULT_QUEUE}`;
}

async function fetchTlEst(acc, scenario, override) {
  const key = tlEstKey(acc, scenario) + (override ? "|" + JSON.stringify(override) : "");
  if (tlEstCache[key]) return tlEstCache[key];
  const body = { account: acc, scenario: scenario || DEFAULT_QUEUE };
  if (override) body.queue_override = override;
  const d = await api("/api/estimate-actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  tlEstCache[key] = d;
  return d;
}

function tlHHMM(min) {
  const m = Math.max(0, Math.round(Number(min) || 0));
  const hh = String(Math.floor(m / 60) % 24).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return hh + ":" + mm;
}

function fmtDurLong(s) {
  s = Math.max(0, Math.round(Number(s) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h && m) return h + "h " + m + "m";
  if (h) return h + "h";
  if (m) return m + "m";
  return s + "s";
}

function mondayOf(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12, 0, 0);
  const js = x.getDay();
  x.setDate(x.getDate() + (js === 0 ? -6 : 1 - js));
  return x;
}

function weekDates(offset) {
  const mon = mondayOf(new Date());
  mon.setDate(mon.getDate() + (Number(offset) || 0) * 7);
  const out = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(mon);
    d.setDate(mon.getDate() + i);
    out.push(d);
  }
  return out;
}

function isoDate(d) {
  return d.getFullYear() + "-"
    + String(d.getMonth() + 1).padStart(2, "0") + "-"
    + String(d.getDate()).padStart(2, "0");
}

function fmtDdMm(d) {
  return String(d.getDate()).padStart(2, "0") + "."
    + String(d.getMonth() + 1).padStart(2, "0");
}

/** Jak `device_planner.cycle_index`: 7 = weekday (Pn=0), krótszy = ordinal % N. */
function cycleIndexForDate(date, cycleDays) {
  const d = (date instanceof Date) ? date : new Date(String(date) + "T12:00:00");
  const n = Math.max(Number(cycleDays) || 1, 1);
  if (n === 7) {
    const js = d.getDay();
    return js === 0 ? 6 : js - 1;
  }
  // Python date.toordinal(): 1970-01-01 = 719163
  const unixDays = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000;
  const ordinal = unixDays + 719163;
  return ((ordinal % n) + n) % n;
}

function cycleDayForDate(cycle, date) {
  if (!cycle) return null;
  const idx = cycleIndexForDate(date, cycle.cycle_days || 1);
  return (cycle.days || []).find((x) => x.index === idx) || null;
}

function tlWindowFrom(sched, cycles) {
  let hours = (sched && sched.active_hours) || null;
  if (!hours) {
    const first = Object.values(cycles || {})[0];
    hours = first && first.active_hours;
  }
  const fromH = Number((hours && hours[0]) ?? 8);
  const toH = Number((hours && hours[1]) ?? 20);
  return { from: fromH * 60, to: Math.min(toH * 60, 24 * 60) };
}

function tlCycleLabel(n) {
  const d = Number(n) || 1;
  if (d === 1) return "1 dzień";
  return d + " dni";
}

function accColor(acc) {
  const list = (accounts || []).map((a) => a.alias);
  let idx = list.indexOf(acc);
  if (idx < 0) {
    let h = 0;
    for (const ch of String(acc || "")) h = (h * 37 + ch.charCodeAt(0)) % 360;
    idx = Math.floor(h / (360 / ACC_PALETTE.length)) % ACC_PALETTE.length;
  }
  return ACC_PALETTE[idx % ACC_PALETTE.length];
}

function consumeTlPreselect() {
  let stored = "";
  try { stored = sessionStorage.getItem(TL_PRESELECT_KEY) || ""; } catch (_) {}
  if (stored) {
    tlFocusAlias = stored;
    try { sessionStorage.removeItem(TL_PRESELECT_KEY); } catch (_) {}
  }
  return tlFocusAlias;
}

function highlightTlPreselect() {
  if (!tlFocusAlias) return;
  const row = document.querySelector(".tl-row[data-alias=\"" + CSS.escape(tlFocusAlias) + "\"]");
  if (!row) return;
  row.classList.add("preselect");
  row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function fillWeekSelector() {
  const sel = $("tl-week-sel");
  if (!sel) return;
  const cur = weekDates(0);
  const prevW = weekDates(-1);
  const nextW = weekDates(1);
  const opts = [
    { v: "-1", t: "Poprzedni " + fmtDdMm(prevW[0]) + "–" + fmtDdMm(prevW[6]) },
    { v: "0", t: "Ten tydzień " + fmtDdMm(cur[0]) + "–" + fmtDdMm(cur[6]) },
    { v: "1", t: "Następny " + fmtDdMm(nextW[0]) + "–" + fmtDdMm(nextW[6]) },
  ];
  sel.innerHTML = opts.map((o) =>
    `<option value="${escapeHtml(o.v)}">${escapeHtml(o.t)}</option>`).join("");
  sel.value = String(tlWeekOffset);
}

function renderSchedToggles() {
  const box = $("tl-sched-toggles");
  if (!box) return;
  box.innerHTML = (devices || []).map((d) => {
    const on = d.scheduler_enabled !== false;
    return `<label class="sched-tog${on ? " on" : " off"}">
      <input type="checkbox" data-sched-alias="${escapeHtml(d.alias)}" ${on ? "checked" : ""}/>
      <span class="tog-name">${escapeHtml(d.alias)}</span>
      <span class="tog-state">scheduler ${on ? "ON" : "OFF"}</span>
    </label>`;
  }).join("");
}

async function setDeviceScheduler(alias, enabled) {
  await api(`/api/devices/${encodeURIComponent(alias)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scheduler_enabled: !!enabled }),
  });
  const row = (devices || []).find((d) => d.alias === alias);
  if (row) row.scheduler_enabled = !!enabled;
  renderSchedToggles();
}

function paintPlayhead() {
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const todayIso = isoDate(new Date());
  const visible = nowMin >= tlWin.from && nowMin <= tlWin.to;
  const top = (nowMin - tlWin.from) * tlPxPerMin;
  document.querySelectorAll(".tl-lane.today").forEach((lane) => {
    let ph = lane.querySelector(".tl-playhead");
    if (!visible || lane.dataset.date !== todayIso) {
      if (ph) ph.remove();
      return;
    }
    if (!ph) {
      ph = document.createElement("div");
      ph.className = "tl-playhead";
      lane.appendChild(ph);
    }
    ph.style.top = top + "px";
  });
}

function blockHtml(device, dayIndex, dateIso, b, selected) {
  const end = tlHHMM(toMin(b.start) + (Number(b.duration_s) || 0) / 60);
  const top = (toMin(b.start) - tlWin.from) * tlPxPerMin;
  const height = Math.max((Number(b.duration_s) || 0) / 60 * tlPxPerMin, 18);
  const sel = selected ? " selected" : "";
  const lock = b.locked
    ? `<span class="tl-lock" title="Zablokowana">🔒</span>` : "";
  return `<div class="tl-block${sel}" data-id="${escapeHtml(b.id)}"
      data-device="${escapeHtml(device)}" data-day="${dayIndex}" data-date="${escapeHtml(dateIso)}"
      style="top:${top}px;height:${height}px;background:${accColor(b.account)}">
    <div class="when">${escapeHtml(b.start)} – ${end}</div>
    <div class="who">${escapeHtml(b.account)} · ${escapeHtml(b.scenario || DEFAULT_QUEUE)}</div>
    ${lock}
  </div>`;
}

function renderLegend() {
  const box = $("tl-legend");
  if (!box) return;
  const names = [];
  for (const a of accounts || []) {
    if (a.alias && !names.includes(a.alias)) names.push(a.alias);
  }
  for (const cycle of Object.values(tlCycles)) {
    for (const day of cycle.days || []) {
      for (const b of day.blocks || []) {
        if (b.account && !names.includes(b.account)) names.push(b.account);
      }
    }
  }
  box.innerHTML = names.map((n) =>
    `<span class="leg"><i style="background:${accColor(n)}"></i>${escapeHtml(n)}</span>`
  ).join("");
}

function renderExtras() {
  const box = $("tl-extras-body");
  if (!box) return;
  box.innerHTML = (devices || []).map((dev) => {
    const rows = tlAccountsByDev[dev.alias] || [];
    const accHtml = rows.map((a, i) => {
      const spd = a.sessions_per_day || { min: 1, max: 1 };
      return `<div class="tl-spd-row${a.enabled ? "" : " off"}">
        <span class="tl-acc-dot" style="background:${accColor(a.account)}"></span>
        <label><input type="checkbox" data-en="${escapeHtml(dev.alias)}" data-i="${i}"
          ${a.enabled ? "checked" : ""}/> ${escapeHtml(a.account)}</label>
        <span>sesji/dzień</span>
        <span class="range-inputs">
          <input type="number" min="0" max="20" data-smin="${escapeHtml(dev.alias)}" data-i="${i}"
            value="${Number(spd.min) || 0}" ${a.enabled ? "" : "disabled"}/>
          <span class="range-sep">–</span>
          <input type="number" min="0" max="20" data-smax="${escapeHtml(dev.alias)}" data-i="${i}"
            value="${Number(spd.max) || 0}" ${a.enabled ? "" : "disabled"}/>
        </span>
      </div>`;
    }).join("") || `<div class="tl-empty">Brak kont na ${escapeHtml(dev.alias)}.</div>`;
    const accOpts = rows.filter((a) => a.enabled).map((a) =>
      `<option value="${escapeHtml(a.account)}">${escapeHtml(a.account)}</option>`).join("");
    return `<div class="tl-extras-dev" data-dev="${escapeHtml(dev.alias)}">
      <h4>${escapeHtml(dev.alias)}</h4>
      ${accHtml}
      <div class="tl-acc-row">
        <button type="button" data-spd-save="${escapeHtml(dev.alias)}">Zapisz sesje/udział</button>
        <button type="button" class="danger" data-tl-clear="${escapeHtml(dev.alias)}">Wyczyść oś</button>
      </div>
      <div class="tl-acc-row">
        <select data-add-acc="${escapeHtml(dev.alias)}">${accOpts || "<option value=\"\">—</option>"}</select>
        <input type="time" step="300" data-add-start="${escapeHtml(dev.alias)}" value="12:00"/>
        <select data-add-day="${escapeHtml(dev.alias)}">${
          weekDates(tlWeekOffset).map((d) => {
            const cycle = tlCycles[dev.alias];
            const day = cycleDayForDate(cycle, d);
            const idx = day ? day.index : cycleIndexForDate(d, (cycle && cycle.cycle_days) || 1);
            return `<option value="${idx}">${escapeHtml(TL_WD[d.getDay() === 0 ? 6 : d.getDay() - 1])} ${fmtDdMm(d)}</option>`;
          }).join("")
        }</select>
        <button type="button" data-add-block="${escapeHtml(dev.alias)}">Dodaj blok</button>
      </div>
    </div>`;
  }).join("");
}

async function renderWeek() {
  const week = $("tl-week");
  if (!week) return;
  const dates = weekDates(tlWeekOffset);
  const todayIso = isoDate(new Date());
  const span = Math.max(tlWin.to - tlWin.from, 60);
  tlPxPerMin = Math.max(0.7, Math.min(1.25, 640 / span));
  const laneH = span * tlPxPerMin;
  const step = 120 * tlPxPerMin;

  if (!(devices || []).length) {
    week.innerHTML = `<div class="empty">Brak urządzeń w config/devices/.</div>`;
    renderInspector();
    return;
  }

  const marks = [];
  const startMark = Math.ceil(tlWin.from / 120) * 120;
  for (let m = startMark; m <= tlWin.to; m += 120) marks.push(m);
  if (marks[0] !== tlWin.from) marks.unshift(tlWin.from);

  const head = `<div class="tl-cols-head">
    <div></div><div></div>
    ${dates.map((d) => {
      const iso = isoDate(d);
      const wd = d.getDay() === 0 ? 6 : d.getDay() - 1;
      return `<div class="tl-col-h${iso === todayIso ? " today" : ""}">
        <strong>${escapeHtml(TL_WD[wd])}</strong>${fmtDdMm(d)}</div>`;
    }).join("")}
  </div>`;

  const rows = (devices || []).map((dev) => {
    const online = isOnline(dev);
    const cycle = tlCycles[dev.alias];
    const gutter = marks.map((m) => {
      const top = (m - tlWin.from) * tlPxPerMin;
      return `<span class="tl-hour" style="top:${top}px">${tlHHMM(m)}</span>`;
    }).join("");
    const lanes = dates.map((d) => {
      const iso = isoDate(d);
      const day = cycleDayForDate(cycle, d);
      const idx = day ? day.index : cycleIndexForDate(d, (cycle && cycle.cycle_days) || 1);
      const blocks = ((day && day.blocks) || []).filter((b) => {
        const st = toMin(b.start);
        const en = st + (Number(b.duration_s) || 0) / 60;
        return en > tlWin.from && st < tlWin.to;
      });
      const selId = (tlSel && tlSel.device === dev.alias && tlSel.block)
        ? tlSel.block.id : "";
      const html = blocks.map((b) =>
        blockHtml(dev.alias, idx, iso, b, selId === b.id)).join("");
      const todayCls = iso === todayIso ? " today" : "";
      return `<div class="tl-lane${todayCls}" data-device="${escapeHtml(dev.alias)}"
        data-day="${idx}" data-date="${iso}" style="height:${laneH}px">${html}</div>`;
    }).join("");
    const pre = tlFocusAlias === dev.alias ? " preselect" : "";
    return `<div class="tl-row${online ? " online" : " offline"}${pre}"
        data-alias="${escapeHtml(dev.alias)}" style="--tl-step:${step}px">
      <div class="tl-row-meta">
        <span class="dev-name">${escapeHtml(dev.alias)}</span>
        <span class="tl-row-dot">${online ? "online" : "offline"}</span>
      </div>
      <div class="tl-gutter" style="height:${laneH}px">${gutter}</div>
      ${lanes}
    </div>`;
  }).join("");

  week.innerHTML = head + rows;
  paintPlayhead();
  renderLegend();
  renderExtras();
  renderInspector();
  highlightTlPreselect();
}

async function refreshHarmonogram() {
  const gen = ++tlGen;
  consumeTlPreselect();
  try {
    const [devs, adb, acc, sched] = await Promise.all([
      api("/api/devices"),
      api("/api/adb-devices").catch(() => []),
      api("/api/accounts").catch(() => accounts),
      api("/api/scheduler").catch(() => ({ enabled: false, active_hours: [8, 20], cycle_days: 1 })),
    ]);
    devices = Array.isArray(devs) ? devs : [];
    adbRows = Array.isArray(adb) ? adb : [];
    if (Array.isArray(acc)) accounts = acc;
    tlSched = sched || {};
    schedulerOn = !!tlSched.enabled;

    const loaded = {};
    const accMap = {};
    await Promise.all((devices || []).map(async (d) => {
      const alias = d.alias;
      try { loaded[alias] = await api(`/api/timeline/${encodeURIComponent(alias)}`); }
      catch (_) { loaded[alias] = { days: [], cycle_days: tlSched.cycle_days || 1, active_hours: tlSched.active_hours }; }
      try { accMap[alias] = await api(`/api/schedule-accounts/${encodeURIComponent(alias)}`); }
      catch (_) { accMap[alias] = []; }
    }));
    if (gen !== tlGen) return;
    tlCycles = loaded;
    tlAccountsByDev = accMap;
    tlWin = tlWindowFrom(tlSched, tlCycles);
    const cyc = tlSched.cycle_days || (Object.values(tlCycles)[0] || {}).cycle_days || 1;
    tlWindowHint = `Okno ${tlHHMM(tlWin.from)}–${tlHHMM(tlWin.to)} · cykl ${tlCycleLabel(cyc)}`;
    updateSessionLine();

    const ban = $("tl-banner");
    if (ban) {
      if (schedulerOn) {
        ban.hidden = false;
        ban.className = "banner warn";
        ban.textContent = "Harmonogram włączony — plan jest egzekwowany (per telefon w toolbarze).";
      } else {
        ban.hidden = true;
        ban.textContent = "";
      }
    }
    fillWeekSelector();
    renderSchedToggles();
    await renderWeek();
    bindTlGridEvents();
  } catch (e) {
    if (gen !== tlGen) return;
    setTlMsg("Nie udało się wczytać osi: " + (e.message || e), "err");
  }
}

async function regenWeek() {
  const aliases = (devices || []).map((d) => d.alias);
  if (!aliases.length) {
    setTlMsg("Brak telefonów.", "err");
    return;
  }
  const who = tlFocusAlias && aliases.includes(tlFocusAlias) ? [tlFocusAlias] : aliases;
  const msg = who.length === 1
    ? `Ułożyć wzorzec od nowa na ${who[0]}? Bloki z kłódką zostają.`
    : `Ułożyć wzorzec od nowa na ${who.length} telefonach? Bloki z kłódką zostają.`;
  if (!window.confirm(msg)) return;
  try {
    for (const a of who) {
      await api(`/api/timeline/${encodeURIComponent(a)}/regenerate`, { method: "POST" });
    }
    tlEstCache = {};
    setTlMsg("Wzorzec ułożony od nowa.", "ok");
    await refreshHarmonogram();
  } catch (e) {
    setTlMsg(e.message || "regeneracja nieudana", "err");
  }
}

function findTlBlock(device, dayIndex, blockId) {
  const cycle = tlCycles[device];
  const day = ((cycle && cycle.days) || []).find((d) => d.index === dayIndex);
  return ((day && day.blocks) || []).find((b) => b.id === blockId) || null;
}

function selectTlBlock(device, dayIndex, dateIso, block) {
  tlSel = { device, dayIndex, dateIso, block };
  tlFocusAlias = device;
  document.querySelectorAll(".tl-lane .tl-block").forEach((el) => {
    el.classList.toggle("selected", el.dataset.id === block.id && el.dataset.device === device);
  });
  document.querySelectorAll(".tl-row").forEach((row) => {
    row.classList.toggle("preselect", row.dataset.alias === device);
  });
  renderInspector();
}

async function renderInspector() {
  const box = $("tl-insp");
  if (!box) return;
  if (!tlSel || !tlSel.block) {
    box.innerHTML = `<div class="tl-insp-empty">Kliknij blok na osi.</div>`;
    return;
  }
  const { device, dayIndex, dateIso, block } = tlSel;
  const date = dateIso ? new Date(dateIso + "T12:00:00") : new Date();
  const wd = date.getDay() === 0 ? 6 : date.getDay() - 1;
  const end = tlHHMM(toMin(block.start) + (Number(block.duration_s) || 0) / 60);
  const frozen = ((((tlCycles[device] || {}).runtime) || {})[block.id] || {}).status === "running";
  const rows = tlAccountsByDev[device] || [];
  let presets = (rows.find((a) => a.account === block.account) || {}).presets || [];
  if (!presets.length) {
    try { presets = await loadPresets(block.account); } catch (_) { presets = [DEFAULT_QUEUE]; }
  }
  const runOpts = (presets.length ? presets : [block.scenario || DEFAULT_QUEUE]).map((n) =>
    `<option value="${escapeHtml(n)}" ${n === block.scenario ? "selected" : ""}>${escapeHtml(n)}</option>`
  ).join("");

  let est = null;
  try { est = await fetchTlEst(block.account, block.scenario, block.queue_override || null); }
  catch (_) { est = null; }
  const util = (est && est.utilization) || {};
  const likes = util.likes || {};
  const used = Number(likes.used_today || (est && est.used_today && est.used_today.likes) || 0);
  const lim = Number(likes.limit || 0);
  const mx = Number(likes.max || 0);
  const remaining = lim > 0 ? Math.max(0, lim - used) : null;
  const overRem = remaining != null && (used + mx) > remaining;
  const overCap = !!likes.over || (lim > 0 && mx > lim);
  const pct = lim > 0 ? Math.min(100, Math.round(100 * used / lim)) : 0;
  const barCls = overCap ? " over" : (overRem ? " warn" : "");
  const resetH = tlHHMM(tlWin.from);
  const otherKinds = ["follows", "comments"].map((k) => {
    const u = util[k] || {};
    const warn = u.over_remaining || u.over;
    return `<div class="insp-lim-row${warn ? " warn" : ""}">
      <span>${escapeHtml(ACTION_LABELS[k])}</span>
      <span>max ${u.max || 0}${u.limit ? " / " + u.limit : ""} · dziś ${u.used_today || 0}</span>
    </div>`;
  }).join("");

  box.innerHTML = `
    <h3>Wybrana sesja</h3>
    <p class="insp-run">Run: ${escapeHtml(block.scenario || DEFAULT_QUEUE)}</p>
    <div class="kv"><span class="k">Konto</span><span class="v">${escapeHtml(block.account)}</span></div>
    <div class="kv"><span class="k">Telefon</span><span class="v">${escapeHtml(device)}</span></div>
    <div class="kv"><span class="k">Dzień</span>
      <span class="v">${escapeHtml(TL_WD_FULL[wd])} (${fmtDdMm(date)})</span></div>
    <div class="kv"><span class="k">Czas</span>
      <span class="v">${escapeHtml(block.start)} – ${end}</span></div>
    <div class="kv"><span class="k">Czas trwania</span>
      <span class="v">${escapeHtml(fmtDurLong(block.duration_s))}</span></div>
    <label class="insp-field">Run
      <select id="insp-run" ${frozen ? "disabled" : ""}>${runOpts}</select>
    </label>
    <label class="tl-switch">Zablokowana
      <input type="checkbox" id="insp-locked" ${block.locked ? "checked" : ""} ${frozen ? "disabled" : ""}/>
    </label>
    <div class="insp-lim">
      <h4>Limity konta</h4>
      <div class="insp-bar${barCls}"><i style="width:${pct}%"></i></div>
      <div class="insp-lim-meta">
        Wykonane ${used}${lim ? " / " + lim : ""}
        ${remaining != null ? ` · Pozostało ${remaining}` : ""}
        <div class="rest">Plan max ${mx} (do limitu liczymy MAX)${overRem ? " · żółto: used+max &gt; remaining" : ""}</div>
        <div class="rest">Reset jutro, ${resetH}</div>
      </div>
      ${otherKinds}
    </div>
    <div class="insp-actions">
      <button type="button" id="insp-move" ${frozen ? "disabled" : ""}>Przesuń</button>
      <button type="button" id="insp-swap" ${frozen ? "disabled" : ""}>Zamień run</button>
      <button type="button" class="danger" id="insp-del">Usuń</button>
    </div>`;

  const runSel = $("insp-run");
  if (runSel) runSel.addEventListener("change", () => swapTlRun(runSel.value));
  const lockEl = $("insp-locked");
  if (lockEl) lockEl.addEventListener("change", () => setTlLocked(!!lockEl.checked));
  const mv = $("insp-move");
  if (mv) mv.addEventListener("click", () => promptTlMove());
  const sw = $("insp-swap");
  if (sw) sw.addEventListener("click", () => {
    if (runSel) { runSel.focus(); swapTlRun(runSel.value); }
  });
  const del = $("insp-del");
  if (del) del.addEventListener("click", () => deleteTlBlock());
}

async function moveTlBlock(device, dayIndex, blockId, start, force) {
  const url = `/api/timeline/${encodeURIComponent(device)}/day/${dayIndex}/block/${encodeURIComponent(blockId)}/move`;
  try {
    await api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start, force: !!force }),
    });
    setTlMsg(force ? "Blok przesunięty (force)." : "Blok przesunięty.", "ok");
    await refreshHarmonogram();
    return true;
  } catch (e) {
    if (e.status === 409 && !force) {
      if (window.confirm((e.message || "Kolizja z innym blokiem.") + "\n\nPrzesunąć mimo to?")) {
        return moveTlBlock(device, dayIndex, blockId, start, true);
      }
      setTlMsg("Przesunięcie anulowane.", "warn");
      await refreshHarmonogram();
      return false;
    }
    setTlMsg(e.message || "nie przesunięto", "err");
    await refreshHarmonogram();
    return false;
  }
}

function promptTlMove() {
  if (!tlSel || !tlSel.block) return;
  const next = window.prompt("Nowa godzina startu (HH:MM)", tlSel.block.start);
  if (!next) return;
  moveTlBlock(tlSel.device, tlSel.dayIndex, tlSel.block.id, next);
}

async function swapTlRun(scenario) {
  if (!tlSel || !tlSel.block) return;
  const { device, dayIndex, block } = tlSel;
  try {
    await api(`/api/timeline/${encodeURIComponent(device)}/day/${dayIndex}/block/${encodeURIComponent(block.id)}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: scenario || DEFAULT_QUEUE }),
    });
    tlEstCache = {};
    setTlMsg("Run zamieniony.", "ok");
    const keep = block.id;
    await refreshHarmonogram();
    const again = findTlBlock(device, dayIndex, keep);
    if (again) selectTlBlock(device, dayIndex, tlSel.dateIso, again);
  } catch (e) {
    setTlMsg(e.message || "nie zamieniono runy", "err");
  }
}

async function setTlLocked(locked) {
  if (!tlSel || !tlSel.block) return;
  const { device, dayIndex, block } = tlSel;
  try {
    await api(`/api/timeline/${encodeURIComponent(device)}/day/${dayIndex}/block/${encodeURIComponent(block.id)}/lock`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locked: !!locked }),
    });
    const keep = block.id;
    await refreshHarmonogram();
    const again = findTlBlock(device, dayIndex, keep);
    if (again) selectTlBlock(device, dayIndex, tlSel && tlSel.dateIso, again);
  } catch (e) {
    setTlMsg(e.message || "nie zapisano kłódki", "err");
  }
}

async function deleteTlBlock() {
  if (!tlSel || !tlSel.block) return;
  if (!window.confirm("Usunąć ten blok z wzorca?")) return;
  const { device, dayIndex, block } = tlSel;
  try {
    await api(`/api/timeline/${encodeURIComponent(device)}/day/${dayIndex}/block/${encodeURIComponent(block.id)}`, {
      method: "DELETE",
    });
    tlSel = null;
    setTlMsg("Blok usunięty.", "ok");
    await refreshHarmonogram();
  } catch (e) {
    setTlMsg(e.message || "nie usunięto", "err");
  }
}

async function saveScheduleAccounts(alias) {
  const rows = tlAccountsByDev[alias] || [];
  const body = rows.map((a, i) => {
    const en = document.querySelector(`[data-en="${CSS.escape(alias)}"][data-i="${i}"]`);
    const mn = document.querySelector(`[data-smin="${CSS.escape(alias)}"][data-i="${i}"]`);
    const mx = document.querySelector(`[data-smax="${CSS.escape(alias)}"][data-i="${i}"]`);
    return {
      account: a.account,
      enabled: en ? !!en.checked : !!a.enabled,
      scenarios: a.scenarios || [],
      sessions_per_day: {
        min: Number((mn && mn.value) || (a.sessions_per_day || {}).min || 1),
        max: Number((mx && mx.value) || (a.sessions_per_day || {}).max || 1),
      },
    };
  });
  try {
    await api(`/api/schedule-accounts/${encodeURIComponent(alias)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    tlEstCache = {};
    setTlMsg("Sesje/dzień zapisane — wzorzec ułożony od nowa.", "ok");
    await refreshHarmonogram();
  } catch (e) {
    setTlMsg(e.message || "nie zapisano udziału", "err");
  }
}

async function clearTlDevice(alias) {
  if (!window.confirm(`Wyczyścić całą oś ${alias}? Wzorzec i zmaterializowane dni znikną.`)) return;
  try {
    await api(`/api/timeline/${encodeURIComponent(alias)}`, { method: "DELETE" });
    setTlMsg("Oś wyczyszczona.", "ok");
    await refreshHarmonogram();
  } catch (e) {
    setTlMsg(e.message || "nie wyczyszczono", "err");
  }
}

async function addTlBlock(alias) {
  const acc = (document.querySelector(`[data-add-acc="${CSS.escape(alias)}"]`) || {}).value;
  const start = (document.querySelector(`[data-add-start="${CSS.escape(alias)}"]`) || {}).value || "12:00";
  const day = Number((document.querySelector(`[data-add-day="${CSS.escape(alias)}"]`) || {}).value || 0);
  if (!acc) {
    setTlMsg("Wybierz konto (włączone w udziale).", "err");
    return;
  }
  try {
    await api(`/api/timeline/${encodeURIComponent(alias)}/day/${day}/block`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: acc, scenario: DEFAULT_QUEUE, start }),
    });
    setTlMsg("Dodano blok (od razu z kłódką).", "ok");
    await refreshHarmonogram();
  } catch (e) {
    setTlMsg(e.message || "nie dodano", "err");
  }
}

function bindTlGridEvents() {
  const week = $("tl-week");
  if (week && !week.dataset.dragBound) {
    week.dataset.dragBound = "1";
    week.addEventListener("pointerdown", onTlPointerDown);
  }
  const extras = $("tl-extras-body");
  if (extras && !extras.dataset.bound) {
    extras.dataset.bound = "1";
    extras.addEventListener("click", onTlExtrasClick);
  }
  const toggles = $("tl-sched-toggles");
  if (toggles && !toggles.dataset.bound) {
    toggles.dataset.bound = "1";
    toggles.addEventListener("change", onTlSchedToggle);
  }
  const weekSel = $("tl-week-sel");
  if (weekSel && !weekSel.dataset.bound) {
    weekSel.dataset.bound = "1";
    weekSel.addEventListener("change", () => {
      tlWeekOffset = Number(weekSel.value || 0);
      renderWeek();
    });
  }
}

function onTlSchedToggle(ev) {
  const inp = ev.target.closest("input[data-sched-alias]");
  if (!inp) return;
  setDeviceScheduler(inp.dataset.schedAlias, inp.checked).catch((e) => {
    setTlMsg(e.message || "nie zapisano schedulera", "err");
    inp.checked = !inp.checked;
  });
}

function onTlExtrasClick(ev) {
  const save = ev.target.closest("[data-spd-save]");
  if (save) { saveScheduleAccounts(save.dataset.spdSave); return; }
  const clr = ev.target.closest("[data-tl-clear]");
  if (clr) { clearTlDevice(clr.dataset.tlClear); return; }
  const add = ev.target.closest("[data-add-block]");
  if (add) { addTlBlock(add.dataset.addBlock); }
}

function onTlPointerDown(ev) {
  const el = ev.target.closest(".tl-lane .tl-block");
  if (!el) return;
  const device = el.dataset.device;
  const dayIndex = Number(el.dataset.day);
  const block = findTlBlock(device, dayIndex, el.dataset.id);
  if (!block) return;
  const rt = (((tlCycles[device] || {}).runtime) || {})[block.id] || {};
  const movable = (rt.status || "planned") !== "running";
  tlDrag = {
    el, device, dayIndex, dateIso: el.dataset.date, block, movable,
    startY: ev.clientY, startMin: toMin(block.start), moved: false,
  };
  try { el.setPointerCapture(ev.pointerId); } catch (_) { /* ignore */ }
}

window.addEventListener("pointermove", (ev) => {
  if (!tlDrag || !tlDrag.movable) return;
  const dy = (ev.clientY - tlDrag.startY) / tlPxPerMin;
  if (Math.abs(ev.clientY - tlDrag.startY) > 3) {
    tlDrag.moved = true;
    tlDrag.el.classList.add("dragging");
  }
  const durMin = (Number(tlDrag.block.duration_s) || 0) / 60;
  let m = Math.round((tlDrag.startMin + dy) / 5) * 5;
  m = Math.max(tlWin.from, Math.min(m, tlWin.to - durMin));
  tlDrag.newMin = m;
  tlDrag.el.style.top = ((m - tlWin.from) * tlPxPerMin) + "px";
});

window.addEventListener("pointerup", async () => {
  if (!tlDrag) return;
  const d = tlDrag;
  tlDrag = null;
  d.el.classList.remove("dragging");
  if (!d.moved || d.newMin == null || d.newMin === d.startMin) {
    selectTlBlock(d.device, d.dayIndex, d.dateIso, d.block);
    return;
  }
  await moveTlBlock(d.device, d.dayIndex, d.block.id, tlHHMM(d.newMin));
});

// --- Na żywo: telefon | postęp (faza / kafelek / fraza) | log ludzki --------

const PHASE_LABELS = {
  phrase: "Fraza", tile: "Kafelek", reel: "Rolka",
  target: "Target", visit: "Wizyta",
};
const LIVE_PLUGIN_LABELS = {
  feed_browser: "Przeglądanie strony głównej",
  content_search: "Wyszukiwanie",
  profile_interact: "Interakcja profili",
  reels_warmup: "Grzanie rolek",
  reels_niche: "Rolki niszy",
  mass_stories: "Mass stories",
  target_scraper: "Scraper targetów",
  niche_discover: "Discovery niszy",
  login: "Logowanie",
};
const LIVE_COUNTER_LABELS = {
  liked: "Lajki", followed: "Follow", visited: "Profile",
  items: "Kafelki", phrases: "Frazy", watched: "Rolki",
};

function emptyLiveBits() {
  return { phase: "", label: "", detail: "", phrase: "", tile: "", current: null, total: null };
}

let liveEs = null;
let liveEsAccount = "";       // klucz strumienia: d:alias albo a:konto
let liveFollowAccount = "";
let liveSessionId = "";
let livePreviewOnScreen = true;
let liveStats = null;
let liveBits = emptyLiveBits();
let liveBitsPlugin = "";   // reset faza/kafelek/fraza przy zmianie pluginu
let liveQueueDraft = null; // GET /api/queue/{acc} — lista kroków runy
let liveQueueKey = "";
let liveAutoscroll = true;
let liveLogFilter = "";
let liveTermFilter = "";
const liveLog = [];       // { t, text, kind } — log ludzki
const liveTermLog = [];   // to samo + skróty kind (zwijany Terminal)

function livePluginLabel(pid) {
  if (!pid) return "—";
  const fromCat = (pluginCatalog || []).find((p) => p.id === pid);
  return (fromCat && fromCat.label) || LIVE_PLUGIN_LABELS[pid] || pid;
}

function formatXy(p) {
  if (!p || p.current == null) return "";
  return p.total != null ? `${p.current}/${p.total}` : String(p.current);
}

function deviceAliasForAccount(acc) {
  const row = (accounts || []).find((a) => a.alias === acc);
  return (row && row.device_alias) || "";
}

/** Czyści bufory logu/postępu przy zmianie focusa (inny telefon). */
function resetLiveSessionState() {
  liveLog.length = 0;
  liveTermLog.length = 0;
  liveBits = emptyLiveBits();
  liveBitsPlugin = "";
  liveSessionId = "";
  liveStats = null;
  liveQueueDraft = null;
  liveQueueKey = "";
  liveFollowAccount = "";
  closeLiveSse();
}

/** Focus Na żywo: zmienna JS + sessionStorage (Pulpit woła goLive). */
function setLiveFocus(alias) {
  const next = alias || "";
  const prev = liveFocusDevice;
  liveFocusDevice = next;
  try {
    if (next) sessionStorage.setItem(LIVE_FOCUS_KEY, next);
    else sessionStorage.removeItem(LIVE_FOCUS_KEY);
  } catch (_) { /* tryb prywatny / zablokowany storage */ }
  if (next !== prev) {
    resetLiveSessionState();
    // Nowy telefon = inny max_fps → inny interwał polla JPEG.
    if (typeof armLivePreviewPoll === "function") armLivePreviewPoll();
  }
}

/** Alias focusa: sessionStorage / zmienna, inaczej jadący slot, inaczej pierwszy telefon. */
function resolveLiveFocus() {
  const aliases = (devices || []).map((d) => d.alias);
  let stored = liveFocusDevice;
  if (!stored) {
    try { stored = sessionStorage.getItem(LIVE_FOCUS_KEY) || ""; } catch (_) { stored = ""; }
  }
  if (stored && (!aliases.length || aliases.includes(stored))) {
    liveFocusDevice = stored;
    return stored;
  }
  const running = sessionsList().find((s) => s && s.running && s.device_alias);
  if (running) {
    liveFocusDevice = running.device_alias;
    return running.device_alias;
  }
  if (aliases[0]) {
    liveFocusDevice = aliases[0];
    return aliases[0];
  }
  return stored || "";
}

function ingestLiveProgress(p, plugin) {
  if (plugin && plugin !== liveBitsPlugin) {
    liveBits = emptyLiveBits();
    liveBitsPlugin = plugin;
  }
  if (!p || typeof p !== "object") return;
  liveBits.phase = p.phase || "";
  liveBits.label = p.label || p.phase || "";
  liveBits.detail = p.detail || "";
  // current/total z announce_progress (reel / tile / …) — nie tylko phase===tile
  if (p.current != null) liveBits.current = p.current;
  if (p.total != null) liveBits.total = p.total;
  if (p.phase === "phrase") {
    liveBits.phrase = p.detail || liveBits.phrase;
    liveBits.tile = "";
  } else if (p.phase === "tile") {
    liveBits.tile = formatXy(p);
  }
  const sub = p.sub;
  if (sub && sub.phase === "tile") liveBits.tile = formatXy(sub);
  if (sub && sub.phase === "phrase" && sub.detail) liveBits.phrase = sub.detail;
  // Scal SSE do liveStats — paint nie czeka na poll (2 s)
  if (!liveStats || typeof liveStats !== "object") liveStats = {};
  if (!liveStats.live || typeof liveStats.live !== "object") liveStats.live = {};
  const prev = (liveStats.live.progress && typeof liveStats.live.progress === "object")
    ? liveStats.live.progress : {};
  liveStats.live.progress = {
    phase: p.phase != null ? p.phase : prev.phase,
    label: p.label != null ? p.label : prev.label,
    detail: p.detail != null ? p.detail : prev.detail,
    current: p.current != null ? p.current : prev.current,
    total: p.total != null ? p.total : prev.total,
  };
  if (p.sub) liveStats.live.progress.sub = p.sub;
}

/** session.progress: {src:event,data:{kind,payload}} albo płaskie pola na evencie. */
function sessionProgressBits(p) {
  if (!p || typeof p !== "object") return null;
  const d = p.src === "event" ? (p.data || {}) : p;
  if ((d.kind || p.kind) !== "session.progress") return null;
  const payload = (d.payload && typeof d.payload === "object")
    ? d.payload
    : ((p.payload && typeof p.payload === "object") ? p.payload : null);
  return payload ? Object.assign({}, d, payload) : d;
}

/** Stdout: zdejmij ANSI i tag pluginu — zostaje linia dla operatora. */
function formatStdoutText(raw) {
  let s = String(raw == null ? "" : raw);
  s = s.replace(/\u001b\[[0-9;]*m/g, "");
  const m = s.match(/^(\s*)(reels_niche|reels_warmup|profile_interact|target_scraper|content_search|login)(\s+)([\s\S]*)$/);
  return m ? (m[1] + m[4]) : s;
}

/** Z pakietu SSE bierzemy ludzką linię albo nic (bez dumpów kind=step / JSON). */
function humanLiveLine(p) {
  if (!p || typeof p !== "object") return null;
  if (p.src === "stdout") {
    const text = formatStdoutText(p.data).trim();
    return text ? { text, kind: "stdout" } : null;
  }
  // session.progress aktualizuje panel postępu, nie dubluje say() w logu
  const prog = sessionProgressBits(p);
  if (prog) {
    ingestLiveProgress(prog);
    return null;
  }
  if (p.src !== "event") return null;
  const d = p.data || {};
  const kind = d.kind || "";
  if (kind === "session.start") return { text: "Start sesji", kind: "session" };
  if (kind === "session.end") return { text: "Koniec sesji", kind: "session" };
  return null;
}

function liveLogLineClass(row) {
  if (row.kind === "session") return " session";
  const t = (row.text || "").toLowerCase();
  if (/\bskip\b|pomijam|poza nisz/.test(t)) return " skip";
  if (/\blike\b|polubiono|nisza ok/.test(t)) return " ok";
  if (/klatka\s+\d/.test(t)) return " frame";
  return "";
}

function appendLiveLog(entry) {
  if (!entry || !entry.text) return;
  const row = {
    t: new Date().toLocaleTimeString(),
    text: entry.text,
    kind: entry.kind || "",
  };
  liveLog.push(row);
  while (liveLog.length > LIVE_LOG_MAX) liveLog.shift();
  if (currentView() === "na-zywo") paintLiveLog();
}

function appendLiveTerm(entry) {
  if (!entry || !entry.text) return;
  liveTermLog.push({
    t: new Date().toLocaleTimeString(),
    text: entry.text,
    kind: entry.kind || "",
  });
  while (liveTermLog.length > LIVE_LOG_MAX) liveTermLog.shift();
  if (currentView() === "na-zywo") paintLiveTerminal();
}

/** Kind z SSE poza logiem ludzkim — krótka etykieta, nie dump JSONL. */
function extraTermLine(p) {
  if (!p || p.src !== "event") return null;
  const kind = (p.data || {}).kind || "";
  if (!kind || kind === "session.progress") return null;
  return { text: kind, kind: "event" };
}

function paintLiveLog() {
  const box = $("live-log-body");
  if (!box) return;
  const q = liveLogFilter.trim().toLowerCase();
  const rows = q
    ? liveLog.filter((r) => (r.text || "").toLowerCase().includes(q))
    : liveLog;
  if (!rows.length) {
    box.innerHTML = `<div class="live-log-empty">${
      liveLog.length ? "Filtr nic nie złapał." : "Brak linii — ruszy, gdy sesja coś powie."
    }</div>`;
    return;
  }
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  box.innerHTML = rows.map((row) =>
    `<div class="live-log-line${liveLogLineClass(row)}">`
    + `<span class="ts">${escapeHtml(row.t)}</span>${escapeHtml(row.text)}</div>`
  ).join("");
  if (liveAutoscroll || nearBottom || liveLog.length < 8) {
    box.scrollTop = box.scrollHeight;
  }
}

function paintLiveTerminal() {
  const box = $("live-terminal-body");
  if (!box) return;
  let re = null;
  const raw = liveTermFilter.trim();
  if (raw) {
    try { re = new RegExp(raw, "i"); } catch (_) { re = null; }
  }
  const rows = re ? liveTermLog.filter((r) => re.test(r.text)) : liveTermLog;
  if (!rows.length) {
    box.innerHTML = `<div class="live-log-empty">${
      liveTermLog.length ? "Regex nic nie złapał." : "Terminal pusty."
    }</div>`;
    return;
  }
  box.innerHTML = rows.map((row) =>
    `<div class="live-log-line"><span class="ts">${escapeHtml(row.t)}</span>`
    + `${escapeHtml(row.text)}</div>`
  ).join("");
  if (liveAutoscroll) box.scrollTop = box.scrollHeight;
}

function closeLiveSse() {
  if (liveEs) {
    try { liveEs.close(); } catch (_) { /* ignore */ }
    liveEs = null;
  }
  liveEsAccount = "";
}

/** SSE focusa: `?device=` gdy znamy alias, inaczej `?account=` ze slotu. */
function connectLiveStream(alias) {
  const slot = sessionSlot(alias);
  const account = (slot && slot.account) || "";
  let url = "";
  let key = "";
  if (alias && account) {
    url = `/api/events/stream?device=${encodeURIComponent(alias)}`;
    key = "d:" + alias;
  } else if (account) {
    url = `/api/events/stream?account=${encodeURIComponent(account)}`;
    key = "a:" + account;
  } else {
    closeLiveSse();
    return;
  }
  if (liveEs && liveEsAccount === key) return;
  closeLiveSse();
  if (typeof EventSource !== "function") return;
  liveEsAccount = key;
  try {
    liveEs = new EventSource(url);
  } catch (_) {
    liveEs = null;
    liveEsAccount = "";
    return;
  }
  liveEs.onmessage = (ev) => {
    let parsed;
    try { parsed = JSON.parse(ev.data); } catch (_) { return; }
    const line = humanLiveLine(parsed);
    if (line) {
      appendLiveLog(line);
      appendLiveTerm(line);
    } else {
      const term = extraTermLine(parsed);
      if (term) appendLiveTerm(term);
      // session.progress: lista Bieżąca sesja od razu (nie czekaj na poll 2 s)
      if (sessionProgressBits(parsed) && currentView() === "na-zywo") {
        paintLiveProgress();
      }
    }
  };
  liveEs.onerror = () => {
    closeLiveSse();
    if (sessionRunningOn(alias)) {
      setTimeout(() => connectLiveStream(alias), 3000);
    }
  };
}

/** JPEG/poll: 1–15 Hz. scrcpy zostaje 1–60 (od następnej sesji). */
function previewUiFps(scrcpyFps) {
  const n = Number(scrcpyFps);
  if (!Number.isFinite(n)) return PREVIEW_UI_FPS_MIN;
  return Math.max(PREVIEW_UI_FPS_MIN, Math.min(PREVIEW_UI_FPS_MAX, Math.round(n)));
}

/** Wyższe fps → krótszy interwał: livePreviewIntervalMs(15) < livePreviewIntervalMs(1). */
function livePreviewIntervalMs(scrcpyFps) {
  return Math.round(1000 / previewUiFps(scrcpyFps));
}

function focusedScrcpyFps() {
  const alias = resolveLiveFocus();
  const dev = (devices || []).find((d) => d.alias === alias);
  const n = dev && Number(dev.scrcpy_max_fps);
  return Number.isFinite(n) ? n : 15;
}

function paintLiveCaption() {
  const cap = $("live-caption");
  const input = $("live-fps-input");
  const fps = focusedScrcpyFps();
  const ui = previewUiFps(fps);
  if (input && document.activeElement !== input) input.value = String(fps);
  if (!cap) return;
  const scrcpyBit = fps !== ui ? `; scrcpy ${fps}` : "";
  cap.textContent = `Podgląd ${ui} fps (JPEG z FB${scrcpyBit}). `
    + `scrcpy: od następnej sesji; podgląd panelu: od razu. `
    + `Stop gasi tylko ten telefon.`;
}

async function setLiveScrcpyFps(fps) {
  const alias = resolveLiveFocus();
  if (!alias) return;
  const n = Math.max(SCRCPY_FPS_MIN, Math.min(SCRCPY_FPS_MAX, Math.round(Number(fps) || 15)));
  try {
    const out = await api(`/api/devices/${encodeURIComponent(alias)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scrcpy_max_fps: n }),
    });
    const saved = (out && out.scrcpy_max_fps != null) ? out.scrcpy_max_fps : n;
    const row = (devices || []).find((d) => d.alias === alias);
    if (row) row.scrcpy_max_fps = saved;
    paintLiveCaption();
    armLivePreviewPoll();
  } catch (e) {
    showBanner("live-msg", e.message || "nie zapisano FPS", "err");
  }
}

let livePreviewTimer = null;
function armLivePreviewPoll() {
  if (livePreviewTimer != null) clearTimeout(livePreviewTimer);
  const ms = livePreviewIntervalMs(focusedScrcpyFps()) || LIVE_PREVIEW_MS;
  livePreviewTimer = setTimeout(() => {
    pollLivePreview().catch(() => {}).finally(() => armLivePreviewPoll());
  }, ms);
}

function showLivePreviewPlaceholder(reason) {
  const box = $("live-preview");
  if (!box) return;
  box.classList.remove("has-frame");
  const img = box.querySelector("img");
  if (img) {
    img.hidden = true;
    img.removeAttribute("src");
  }
  const ph = box.querySelector(".preview-ph");
  if (!ph) return;
  // 204 / no_session / idle = „brak sesji” — nie uiautodev, nie drugi scrcpy.
  if (reason === "offline") ph.textContent = "telefon offline";
  else ph.textContent = "brak sesji";
}

async function refreshLivePreview(alias) {
  const box = $("live-preview");
  if (!box || !alias) return;
  const url = `/api/devices/${encodeURIComponent(alias)}/preview?t=${Date.now()}`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (r.status !== 200) {
      showLivePreviewPlaceholder(r.headers.get("X-Preview-Reason") || "no_session");
      return;
    }
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    if (!ct.startsWith("image/")) {
      showLivePreviewPlaceholder("no_session");
      return;
    }
    const blob = await r.blob();
    if (!blob.size) {
      showLivePreviewPlaceholder("no_session");
      return;
    }
    revokePreview(LIVE_PREVIEW_KEY);
    const obj = URL.createObjectURL(blob);
    previewUrls[LIVE_PREVIEW_KEY] = obj;
    const img = box.querySelector("img");
    if (!img) return;
    img.alt = `Podgląd ${alias}`;
    img.src = obj;
    img.hidden = false;
    box.classList.add("has-frame");
  } catch (_) {
    showLivePreviewPlaceholder("no_session");
  }
}

function liveThumbKey(alias) {
  return "live-thumb-" + alias;
}

function showThumbPlaceholder(alias) {
  const el = document.querySelector(`.live-thumb[data-alias="${CSS.escape(alias)}"]`);
  if (!el) return;
  const box = el.querySelector(".thumb-preview");
  if (!box) return;
  box.classList.remove("has-frame");
  const img = box.querySelector("img");
  if (img) {
    img.hidden = true;
    img.removeAttribute("src");
  }
  const ph = box.querySelector(".preview-ph");
  if (ph) ph.textContent = "brak obrazu";
}

async function refreshLiveThumb(alias) {
  const el = document.querySelector(`.live-thumb[data-alias="${CSS.escape(alias)}"]`);
  if (!el) return;
  const box = el.querySelector(".thumb-preview");
  if (!box) return;
  const url = `/api/devices/${encodeURIComponent(alias)}/preview?t=${Date.now()}`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (r.status !== 200) {
      showThumbPlaceholder(alias);
      return;
    }
    const blob = await r.blob();
    if (!blob.size) {
      showThumbPlaceholder(alias);
      return;
    }
    revokePreview(liveThumbKey(alias));
    const obj = URL.createObjectURL(blob);
    previewUrls[liveThumbKey(alias)] = obj;
    const img = box.querySelector("img");
    img.alt = `Podgląd ${alias}`;
    img.src = obj;
    img.hidden = false;
    box.classList.add("has-frame");
  } catch (_) {
    showThumbPlaceholder(alias);
  }
}

async function pollLivePreview() {
  if (document.hidden || currentView() !== "na-zywo") return;
  const alias = resolveLiveFocus();
  if (!alias || !sessionRunningOn(alias)) {
    showLivePreviewPlaceholder("idle");
  } else {
    await refreshLivePreview(alias);
  }
  const others = (devices || []).filter((d) => d.alias !== alias);
  await Promise.all(others.map((d) => {
    if (!sessionRunningOn(d.alias)) {
      showThumbPlaceholder(d.alias);
      return Promise.resolve();
    }
    return refreshLiveThumb(d.alias);
  }));
}

async function ensureLiveQueue(account, name) {
  const key = (account || "") + "|" + (name || DEFAULT_QUEUE);
  if (liveQueueKey === key && liveQueueDraft) return liveQueueDraft;
  if (!account) {
    liveQueueDraft = null;
    liveQueueKey = "";
    return null;
  }
  try {
    liveQueueDraft = await api(
      `/api/queue/${encodeURIComponent(account)}?name=${encodeURIComponent(name || DEFAULT_QUEUE)}`);
    liveQueueKey = key;
  } catch (_) {
    liveQueueDraft = null;
    liveQueueKey = "";
  }
  return liveQueueDraft;
}

async function pollLiveStats() {
  if (currentView() !== "na-zywo") return;
  if (document.hidden) return;
  const alias = resolveLiveFocus();
  const slot = sessionSlot(alias);
  const account = slot && slot.account;
  if (!account) return;
  try {
    const data = await api(`/api/stats/${encodeURIComponent(account)}`);
    liveStats = data;
    const live = data && data.live;
    if (live && live.session_id && live.session_id !== liveSessionId) {
      liveSessionId = live.session_id;
    }
    // Poll i SSE w sync: skopiuj live.progress → liveBits (current/total)
    if (live) ingestLiveProgress(live.progress, live.plugin);
    const qName = (live && live.queue && live.queue.name) || slot.queue || DEFAULT_QUEUE;
    await ensureLiveQueue(account, qName);
    try { paintLiveProgress(); } catch (_) { /* ignore */ }
    try { paintLiveChrome(); } catch (_) { /* ignore */ }
  } catch (_) { /* zostaw ostatni postęp */ }
}

function livePercent(live, running) {
  if (!running) return null;
  const p = (live && live.progress) || {};
  if (p.total) {
    return Math.max(0, Math.min(100, Math.round(100 * (Number(p.current) || 0) / p.total)));
  }
  const b = (live && live.budget) || {};
  if (b.max_items) {
    return Math.max(0, Math.min(100,
      Math.round(100 * (Number(b.items_done) || 0) / b.max_items)));
  }
  const q = (live && live.queue) || {};
  if (q.task_n) {
    return Math.max(0, Math.min(100,
      Math.round(100 * (Number(q.task_i) || 0) / q.task_n)));
  }
  return null;
}

function liveEtaClock(slot, live) {
  if (slot && slot.eta) {
    const c = fmtClock(slot.eta);
    if (c) return c;
  }
  const rates = (live && live.timing && live.timing.rates) || {};
  const b = (live && live.budget) || {};
  if (rates.sec_per_item && b.max_items != null) {
    const left = Math.max(0, Number(b.max_items) - Number(b.items_done || 0));
    return fmtClock(Date.now() / 1000 + left * Number(rates.sec_per_item));
  }
  return "";
}

function fmtPauseRange(bts) {
  if (!bts || typeof bts !== "object") return "";
  if (bts.min == null && bts.max == null) return "";
  return `przerwa ${bts.min ?? "?"}–${bts.max ?? "?"}s`;
}

function paintLiveProgress() {
  const box = $("live-progress-body");
  if (!box) return;
  const alias = resolveLiveFocus();
  const slot = sessionSlot(alias);
  const running = !!(slot && slot.running);
  const live = (liveStats && liveStats.live) || {};
  const q = live.queue || {};
  const budget = live.budget || {};
  const plugin = live.plugin || "";
  const phaseName = liveBits.label
    || PHASE_LABELS[liveBits.phase]
    || (running ? "w toku" : "—");
  const phaseXy = formatXy(liveBits) || formatXy(live.progress);
  const counters = live.counters || {};
  const chips = Object.keys(LIVE_COUNTER_LABELS)
    .filter((k) => Number(counters[k] || 0) > 0)
    .map((k) => `<span class="live-chip">${escapeHtml(LIVE_COUNTER_LABELS[k])} `
      + `<strong>${escapeHtml(counters[k])}</strong></span>`)
    .join("");
  const rawQueue = liveQueueDraft && liveQueueDraft.queue;
  const tasks = (Array.isArray(rawQueue) ? rawQueue : [])
    .filter((t) => t && t.enabled !== false);
  const taskI = Number(q.task_i) || 0;
  const title = $("live-run-title");
  if (title) title.textContent = "Bieżąca sesja";
  const sub = $("live-run-sub");
  if (sub) {
    const runName = q.name || (slot && slot.queue) || DEFAULT_QUEUE;
    sub.textContent = runName ? `runa ${runName}` : "";
    sub.hidden = !sub.textContent;
  }
  const stepHtml = tasks.map((t, i) => {
    const n = i + 1;
    const current = running && taskI === n;
    const done = taskI > n || (!running && taskI >= n && taskI > 0);
    const rng = itemsRange(t.budget) || pluginItemsDefault(t.plugin);
    let xy = "";
    if (current) {
      // items_done === 0 nie jest licznikiem live, gdy live.progress / liveBits.current ma liczby
      const fromLive = formatXy(liveBits) || formatXy(live.progress);
      if (fromLive) {
        xy = fromLive;
      } else if (budget.items_done != null && budget.items_done !== 0) {
        xy = `${budget.items_done}/${budget.max_items != null ? budget.max_items : "∞"}`;
      } else if (budget.max_items != null) {
        xy = `0/${budget.max_items}`;
      } else if (rng) {
        xy = `0/${rng.max}`;
      }
    } else if (done && rng) {
      xy = `${rng.max}/${rng.max}`;
    } else if (rng) {
      xy = `0/${rng.max}`;
    }
    const liveCur = liveBits.current != null ? liveBits.current : (live.progress || {}).current;
    const liveTot = liveBits.total != null ? liveBits.total : (live.progress || {}).total;
    const pct = current && liveTot
      ? Math.max(0, Math.min(100, Math.round(100 * (Number(liveCur) || 0) / liveTot)))
      : (current && budget.max_items
        ? Math.max(0, Math.min(100, Math.round(100 * (Number(budget.items_done) || 0) / budget.max_items)))
        : (done ? 100 : 0));
    const note = t.note || livePluginLabel(t.plugin);
    const cls = current ? " current" : (done ? " done" : " todo");
    const mark = current
      ? `<span class="live-step-dot current"><span class="live-step-spin" aria-hidden="true"></span><span>${n}</span></span>`
      : (done
        ? `<span class="live-step-dot done" aria-hidden="true">✓</span>`
        : `<span class="live-step-dot">${n}</span>`);
    return `<div class="live-step${cls}">
      ${mark}
      <div class="live-step-main">
        <div class="live-step-top">
          <div>
            <div class="live-step-name">${n}. ${escapeHtml(note)}</div>
            <div class="live-step-plugin">${escapeHtml(t.plugin)}</div>
          </div>
          <div class="live-step-meta">
            ${current ? `<span class="live-jedzie">JEDZIE</span>` : ""}
            <span class="live-step-xy">${escapeHtml(xy)}</span>
          </div>
        </div>
        ${current ? `<div class="live-step-bar"><i style="width:${pct}%"></i></div>` : ""}
      </div>
    </div>`;
  }).join("");
  const kv = `
    <div class="live-kv"><span class="k">Faza</span>
      <span class="v">${running ? escapeHtml(phaseName) + (phaseXy ? " · " + escapeHtml(phaseXy) : "") : "—"}</span></div>
    <div class="live-kv"><span class="k">Kafelek</span>
      <span class="v">${escapeHtml((running && liveBits.tile) || "—")}</span></div>
    <div class="live-kv"><span class="k">Fraza</span>
      <span class="v">${escapeHtml((running && liveBits.phrase) || "—")}</span></div>`;
  const eta = liveEtaClock(slot, live);
  const pause = fmtPauseRange(liveQueueDraft && liveQueueDraft.between_tasks_s);
  const footBits = [
    (q.task_i != null && q.task_n != null) ? `krok ${q.task_i}/${q.task_n}` : "",
    eta ? `ETA ${eta}` : "",
    pause,
  ].filter(Boolean);
  const fallback = `
    <div class="live-kv"><span class="k">Plugin</span>
      <span class="v">${escapeHtml(livePluginLabel(plugin))}</span></div>
    <div class="live-kv"><span class="k">Zadanie</span>
      <span class="v">${escapeHtml((q.task_i != null && q.task_n != null)
        ? `${q.task_i}/${q.task_n}` : "—")}</span></div>
    <div class="live-kv"><span class="k">Budżet</span>
      <span class="v">${escapeHtml(budget.items_done != null
        ? `${budget.items_done}/${budget.max_items != null ? budget.max_items : "∞"}`
        : "—")}</span></div>`;
  box.innerHTML = (!running && !tasks.length)
    ? ""
    : (stepHtml || fallback) + (chips ? `<div class="live-counters">${chips}</div>` : "");
  const kvBox = $("live-progress-kv");
  if (kvBox) kvBox.innerHTML = kv;
  const foot = $("live-progress-foot");
  if (foot) foot.textContent = footBits.join(" · ");
}

function deviceStatusKind(dev) {
  const sess = sessionOnDevice(dev.alias);
  if (sess && sess.stopping) return { text: "STOPUJE", cls: "stopping" };
  if (sess && sess.running) return { text: "JEDZIE", cls: "running" };
  if (isOnline(dev)) return { text: "ONLINE", cls: "online" };
  return { text: "OFFLINE", cls: "offline" };
}

function buildLiveThumb(dev) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "live-thumb";
  btn.dataset.alias = dev.alias;
  btn.innerHTML = `
    <div class="thumb-head">
      <span class="thumb-alias"></span>
      <span class="badge offline">OFFLINE</span>
    </div>
    <div class="thumb-preview preview">
      <img alt="" hidden />
      <div class="preview-ph">brak obrazu</div>
    </div>
    <div class="thumb-acc"></div>`;
  btn.addEventListener("click", () => {
    setLiveFocus(dev.alias);
    refreshLive();
    pollLivePreview().catch(() => {});
    pollLiveStats().catch(() => {});
  });
  return btn;
}

function updateLiveThumb(dev) {
  const el = document.querySelector(`.live-thumb[data-alias="${CSS.escape(dev.alias)}"]`);
  if (!el) return;
  const st = deviceStatusKind(dev);
  el.querySelector(".thumb-alias").textContent = dev.alias;
  const badge = el.querySelector(".badge");
  badge.textContent = st.text;
  badge.className = "badge " + st.cls;
  const sess = sessionOnDevice(dev.alias);
  const accs = accountsOnDevice(dev.alias);
  el.querySelector(".thumb-acc").textContent = (sess && sess.account)
    || accs[0]
    || "";
}

function paintLiveFleet() {
  const box = $("live-fleet");
  if (!box) return;
  const focus = resolveLiveFocus();
  const others = (devices || []).filter((d) => d.alias !== focus);
  const head = $("live-fleet-head");
  if (head) {
    head.textContent = others.length
      ? `Inne telefony (${others.length})`
      : "Inne telefony";
  }
  if (!others.length) {
    for (const el of [...box.querySelectorAll(".live-thumb")]) {
      revokePreview(liveThumbKey(el.dataset.alias));
      el.remove();
    }
    box.innerHTML = `<div class="live-fleet-empty">Brak innych telefonów w rejestrze.</div>`;
    return;
  }
  const empty = box.querySelector(".live-fleet-empty");
  if (empty) empty.remove();
  const want = new Set(others.map((d) => d.alias));
  for (const el of [...box.querySelectorAll(".live-thumb")]) {
    if (!want.has(el.dataset.alias)) {
      revokePreview(liveThumbKey(el.dataset.alias));
      el.remove();
    }
  }
  const have = new Set([...box.querySelectorAll(".live-thumb")].map((el) => el.dataset.alias));
  for (const d of others) {
    if (!have.has(d.alias)) box.appendChild(buildLiveThumb(d));
    updateLiveThumb(d);
  }
}

function paintLiveChrome() {
  const alias = resolveLiveFocus();
  const slot = sessionSlot(alias);
  const running = !!(slot && slot.running);
  const stopping = !!(slot && slot.stopping);
  const live = (liveStats && liveStats.live) || {};
  const idle = $("live-idle");
  if (idle) {
    idle.hidden = running;
    idle.textContent = "brak sesji";
    idle.className = "banner";
  }
  const meta = $("live-session-meta");
  if (meta) {
    if (slot) {
      const bit = stopping ? "zatrzymywanie" : (running ? "jedzie" : "slot");
      const run = slot.queue || slot.phase || "";
      meta.textContent = [bit, alias, slot.account, run].filter(Boolean).join(" · ");
    } else {
      meta.textContent = alias ? `${alias} · brak sesji` : "brak sesji";
    }
  }
  const btn = $("live-stop");
  if (btn) btn.disabled = !running || stopping;
  const pct = livePercent(live, running);
  const stats = $("live-chrome-stats");
  if (stats) {
    stats.hidden = !running;
    if (running) {
      const p = live.progress || {};
      const xy = $("live-frame-xy");
      if (xy) {
        xy.textContent = (p.current != null && p.total != null)
          ? `klatka ${p.current}/${p.total}`
          : (liveBits.phase ? (PHASE_LABELS[liveBits.phase] || liveBits.phase) : "");
      }
      const fill = $("live-bar-fill");
      if (fill) fill.style.width = (pct == null ? 0 : pct) + "%";
      const pctEl = $("live-pct");
      if (pctEl) pctEl.textContent = pct == null ? "" : (pct + "%");
      const etaEl = $("live-eta");
      if (etaEl) {
        const eta = liveEtaClock(slot, live);
        etaEl.textContent = eta ? "ETA " + eta : "";
      }
    }
  }
  const badge = $("live-phone-badge");
  const model = $("live-phone-model");
  const dev = (devices || []).find((d) => d.alias === alias);
  if (badge) {
    const st = dev ? deviceStatusKind(dev) : { text: "OFFLINE", cls: "offline" };
    badge.textContent = st.text;
    badge.className = "badge " + st.cls;
  }
  if (model) model.textContent = (dev && dev.model) || alias || "";
  try { paintLiveCaption(); } catch (_) { /* ignore */ }
}

function paintLive() {
  // Każdy panel osobno — throw w flocie nie gasi Stop/logu.
  try { paintLiveChrome(); } catch (_) { /* ignore */ }
  try { paintLiveProgress(); } catch (_) { /* ignore */ }
  try { paintLiveLog(); } catch (_) { /* ignore */ }
  try { paintLiveTerminal(); } catch (_) { /* ignore */ }
  try { paintLiveFleet(); } catch (_) { /* ignore */ }
  const alias = resolveLiveFocus();
  if (!sessionRunningOn(alias)) {
    try { showLivePreviewPlaceholder("idle"); } catch (_) { /* ignore */ }
  }
}

/** Wejście / poll: focus jednego telefonu, SSE tego slotu, Stop tylko jego serialu. */
function refreshLive() {
  try { ensureCatalog().catch(() => {}); } catch (_) { /* ignore */ }
  let alias = "";
  try { alias = resolveLiveFocus(); } catch (_) { alias = liveFocusDevice || ""; }
  try { connectLiveStream(alias); } catch (_) { /* SSE padło — reszta UI żyje */ }
  paintLive();
  if (currentView() === "na-zywo") {
    try { updateSessionLine(); } catch (_) { /* ignore */ }
  }
}

async function stopLiveSession() {
  const alias = resolveLiveFocus();
  if (!alias || !sessionRunningOn(alias)) return;
  showBanner("live-msg", "Zatrzymuję…");
  const btn = $("live-stop");
  if (btn) btn.disabled = true;
  try {
    await fetch("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_alias: alias }),
    });
    showBanner("live-msg", "Wysłano Stop.", "ok");
    await refreshPulpit();
  } catch (e) {
    showBanner("live-msg", e.message || "stop nieudany", "err");
  }
}

const livePreviewIo = new IntersectionObserver((entries) => {
  livePreviewOnScreen = entries.some((e) => e.isIntersecting);
}, { threshold: 0.15 });

// ============================================================
// Ustawienia — globalne (secrets, scheduler, bot.json). Nie karta konta.
// ============================================================
let ustBot = null;          // ostatni GET /api/bot-config — daily_dms / overrides zostają
let ustKnownLoaded = false; // known_steps ładujemy raz, lista jest read-only

function ustNum(id) {
  return Number($(id).value);
}

function ustStep(name) {
  const steps = ((ustBot && ustBot.toggles) || {}).steps || {};
  return steps[name] || {};
}

function paintUstSecrets(st) {
  const el = $("ust-secrets-status");
  const keyEl = $("ust-api-key");
  const wsEl = $("ust-api-ws");
  if (!el || !st) return;
  // Surowy klucz nigdy nie wraca z API — malujemy tylko maskę i źródło.
  if (!st.configured) {
    el.innerHTML = '<span style="color: var(--warn);">brak klucza — Vision w trybie stub</span>';
  } else if (st.source === "env") {
    el.innerHTML = `<span style="color: var(--ok);">ustawiony</span> `
      + `<code>${escapeHtml(st.masked || "")}</code> · ze zmiennej środowiskowej`
      + (st.env_overrides_file
        ? ' <span style="color: var(--warn);">(przesłania plik)</span>' : "");
  } else {
    el.innerHTML = `<span style="color: var(--ok);">ustawiony</span> `
      + `<code>${escapeHtml(st.masked || "")}</code> · z pliku`;
  }
  if (st.workspace_id) {
    el.innerHTML += `<br>workspace: <code>${escapeHtml(st.workspace_id)}</code>`
      + (st.workspace_source === "env" ? " · ze zmiennej środowiskowej" : " · z pliku");
  } else if (st.configured) {
    el.innerHTML += '<br><span style="color: var(--warn);">brak workspace ID — klucz identity-linked zwróci 400</span>';
  }
  if (keyEl) {
    keyEl.value = "";
    keyEl.disabled = st.source === "env";
  }
  if (wsEl) {
    wsEl.disabled = st.workspace_source === "env";
    wsEl.value = st.workspace_id || "";
  }
  const clearBtn = $("ust-secrets-clear");
  if (clearBtn) clearBtn.disabled = st.source === "env" || !st.configured;
}

function paintUstScheduler(st) {
  const en = $("ust-sched-enabled");
  if (en) en.checked = !!st.enabled;
  if (st.active_hours) {
    const a = $("ust-hour-from");
    const b = $("ust-hour-to");
    if (a) a.value = st.active_hours[0];
    if (b) b.value = st.active_hours[1];
  }
  const cycle = $("ust-cycle");
  if (cycle && st.cycle_days) cycle.value = String(st.cycle_days);
}

function paintUstBot(bot) {
  ustBot = bot || {};
  const lim = ustBot.limits || {};
  const hum = ustBot.humanizer || {};
  const caps = lim.per_hour_caps || {};
  const setVal = (id, v) => { const el = $(id); if (el && v != null) el.value = v; };
  const setChk = (id, v) => { const el = $(id); if (el) el.checked = !!v; };
  setVal("ust-likes", lim.daily_likes);
  setVal("ust-follows", lim.daily_follows);
  setVal("ust-comments", lim.daily_comments);
  setVal("ust-cooldown", lim.cooldown_after_warning_hours);
  setVal("ust-hour-likes", caps.likes);
  setVal("ust-hour-follows", caps.follows);
  setVal("ust-hour-comments", caps.comments);
  // daily_dms disabled — pokaż wartość z pliku, nie edytuj.
  setVal("ust-dms", lim.daily_dms);
  setVal("ust-hum-speed", hum.speed);
  setChk("ust-hum-after", hum.after_each_step);
  const prof = $("ust-hum-profile");
  if (prof && hum.profile_name) {
    if (![...prof.options].some((o) => o.value === hum.profile_name)) {
      const opt = document.createElement("option");
      opt.value = hum.profile_name;
      opt.textContent = hum.profile_name;
      prof.appendChild(opt);
    }
    prof.value = hum.profile_name;
  }
  setChk("ust-step-wake", ustStep("wake").enabled !== false);
  setChk("ust-step-unlock", ustStep("unlock").enabled !== false);
  setChk("ust-step-stats", ustStep("stats_snapshot").enabled !== false);
  setChk("ust-step-stats-start", !!ustStep("stats_snapshot").on_session_start);
  setChk("ust-step-stats-end", !!ustStep("stats_snapshot").on_session_end);
  setChk("ust-step-close", ustStep("close_ig").enabled !== false);
  setChk("ust-step-sleep", ustStep("sleep_screen").enabled !== false);
}

async function refreshUstawienia() {
  if (!$("view-ustawienia")) return;
  try {
    const [secrets, sched, bot, profiles] = await Promise.all([
      api("/api/secrets"),
      api("/api/scheduler"),
      api("/api/bot-config"),
      api("/api/humanizer_profiles").catch(() => ["default"]),
    ]);
    paintUstSecrets(secrets);
    paintUstScheduler(sched);
    const profSel = $("ust-hum-profile");
    if (profSel) {
      const names = Array.isArray(profiles) && profiles.length ? profiles : ["default"];
      fillSelect(profSel, names, (bot.humanizer || {}).profile_name || "default");
    }
    paintUstBot(bot);
    if ($("ust-steps-box") && $("ust-steps-box").open) loadUstKnownSteps();
  } catch (e) {
    showBanner("ust-msg", e.message || "nie wczytano ustawień", "err");
  }
}

async function loadUstKnownSteps() {
  const pre = $("ust-known-steps");
  if (!pre || ustKnownLoaded) return;
  try {
    const steps = await api("/api/known_steps");
    const list = Array.isArray(steps) ? steps : [];
    pre.textContent = list.length ? list.join("\n") : "(pusto)";
    ustKnownLoaded = true;
  } catch (e) {
    pre.textContent = e.message || "nie wczytano";
  }
}

async function saveUstSecrets(clearKey) {
  const keyEl = $("ust-api-key");
  const wsEl = $("ust-api-ws");
  const body = {};
  if (clearKey) body.anthropic_api_key = "";
  else if (keyEl && keyEl.value.trim() && !keyEl.disabled) {
    body.anthropic_api_key = keyEl.value.trim();
  }
  if (wsEl && !wsEl.disabled) body.anthropic_workspace_id = wsEl.value.trim();
  if (!Object.keys(body).length) {
    showBanner("ust-msg", "Wpisz klucz i/lub workspace ID.", "err");
    return;
  }
  try {
    const st = await api("/api/secrets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    paintUstSecrets(st);
    showBanner("ust-msg", clearKey ? "Klucz usunięty." : "Zapisano sekrety.", "ok");
  } catch (e) {
    showBanner("ust-msg", e.message || "zapis sekretów nieudany", "err");
  }
}

async function saveUstSchedulerEnabled() {
  const en = $("ust-sched-enabled");
  if (!en) return;
  const next = !!en.checked;
  if (next && !confirm("Włączyć globalny bezpiecznik? Ręczny Start na telefonach z schedulerem ON dostanie 423.")) {
    en.checked = false;
    return;
  }
  try {
    const st = await api("/api/scheduler", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    });
    paintUstScheduler(st);
    showBanner("ust-msg", next ? "Scheduler globalnie ON." : "Scheduler globalnie OFF.", "ok");
  } catch (e) {
    en.checked = !next;
    showBanner("ust-msg", e.message || "nie zmieniono bezpiecznika", "err");
  }
}

async function saveUstSchedulerFrames() {
  const body = {
    active_hours: [ustNum("ust-hour-from"), ustNum("ust-hour-to")],
    cycle_days: ustNum("ust-cycle"),
  };
  try {
    const st = await api("/api/scheduler", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    paintUstScheduler(st);
    showBanner("ust-msg", "Ramy harmonogramu zapisane.", "ok");
  } catch (e) {
    showBanner("ust-msg", e.message || "nie zapisano ram", "err");
  }
}

async function saveUstBot() {
  const prevLim = (ustBot && ustBot.limits) || {};
  const prevHum = (ustBot && ustBot.humanizer) || {};
  const payload = {
    limits: {
      daily_likes: ustNum("ust-likes"),
      daily_follows: ustNum("ust-follows"),
      daily_comments: ustNum("ust-comments"),
      cooldown_after_warning_hours: ustNum("ust-cooldown"),
      per_hour_caps: {
        likes: ustNum("ust-hour-likes"),
        follows: ustNum("ust-hour-follows"),
        comments: ustNum("ust-hour-comments"),
      },
    },
    humanizer: {
      profile_name: ($("ust-hum-profile") && $("ust-hum-profile").value) || "default",
      speed: ustNum("ust-hum-speed"),
      after_each_step: !!($("ust-hum-after") && $("ust-hum-after").checked),
    },
    toggles: {
      steps: {
        wake: { enabled: !!$("ust-step-wake").checked },
        unlock: { enabled: !!$("ust-step-unlock").checked },
        stats_snapshot: {
          enabled: !!$("ust-step-stats").checked,
          on_session_start: !!$("ust-step-stats-start").checked,
          on_session_end: !!$("ust-step-stats-end").checked,
        },
        close_ig: { enabled: !!$("ust-step-close").checked },
        sleep_screen: { enabled: !!$("ust-step-sleep").checked },
      },
    },
  };
  // daily_dms i pauzy override — zostaw z pliku, UI ich nie edytuje.
  if ("daily_dms" in prevLim) payload.limits.daily_dms = prevLim.daily_dms;
  if (prevHum.overrides && typeof prevHum.overrides === "object") {
    payload.humanizer.overrides = prevHum.overrides;
  }
  try {
    const bot = await api("/api/bot-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    paintUstBot(bot);
    showBanner("ust-msg", "Zapisano config/bot.json. Konto może nadpisać w Konta.", "ok");
  } catch (e) {
    showBanner("ust-msg", e.message || "nie zapisano bot.json", "err");
  }
}

// ============================================================
// Lejek — surowy / kandydaci / wizyty + filtr HTTP (bez ADB).
// ============================================================
let ljAccount = "";
let ljVisitFilter = "all";   // all | interact | none
let ljLimits = { raw: 40, cand: 40, visit: 40 };
let ljFormBound = false;     // poll nie nadpisuje pól, gdy operator edytuje
let ljLogFull = false;
let ljSaveTimer = 0;
let ljVisitItems = [];       // ostatnia paczka visited (filtr zakładek w JS)

function ljFmtFollowers(n) {
  if (n == null || n === "") return "—";
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(Math.round(v));
}

function ljSourceKind(src) {
  const s = String(src || "").toLowerCase();
  if (s.includes("comment")) return "commenters";
  if (s.includes("like")) return "likers";
  if (s.includes("hash") || s.includes("tag")) return "hashtag";
  return s ? "other" : "other";
}

function ljSourceLabel(src) {
  const k = ljSourceKind(src);
  if (k === "commenters") return "commenters";
  if (k === "likers") return "likers";
  if (k === "hashtag") return "hashtag";
  return String(src || "—");
}

function ljOutcomeClass(outcome) {
  if (outcome === "like + follow") return "like-follow";
  if (outcome === "follow") return "follow";
  if (outcome === "like") return "like";
  if (outcome === "tylko wizyta - gate") return "gate";
  if (outcome === "nisza nie") return "nisza";
  if (outcome === "private") return "private";
  if (outcome === "open_failed") return "open";
  return "skip";
}

function ljFmtTime(ts) {
  const s = String(ts || "");
  const m = s.match(/T(\d{2}:\d{2})/);
  return m ? m[1] : (s.slice(11, 16) || "—");
}

function ljFmtLastRun(ts) {
  if (!ts) return "Ostatni filtr —";
  const d = new Date(String(ts).replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "Ostatni filtr " + ts;
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `Ostatni filtr ${dd}.${mm} ${hh}:${mi}`;
}

function ljAddTimeRow(value) {
  const wrap = $("lj-times");
  if (!wrap) return;
  const row = document.createElement("div");
  row.className = "lj-time-row";
  row.innerHTML = `<input type="time" value="${escapeHtml(value || "12:00")}" />`
    + `<button type="button" class="lj-time-del" title="Usuń godzinę">×</button>`;
  row.querySelector(".lj-time-del").addEventListener("click", () => {
    row.remove();
    ljScheduleSave();
  });
  row.querySelector("input").addEventListener("change", () => ljScheduleSave());
  wrap.appendChild(row);
}

function ljCollectTimes() {
  return [...document.querySelectorAll("#lj-times input")].map((i) => i.value).filter(Boolean);
}

function ljAddProxyUrlRow(name, value) {
  const wrap = $("lj-proxy-urls");
  if (!wrap) return;
  const row = document.createElement("div");
  row.className = "lj-proxy-url-row";
  row.dataset.name = name;
  row.innerHTML = `<div class="lj-proxy-url-lab"><span>${escapeHtml(name)}</span>`
    + `<button type="button" class="lj-time-del" title="Usuń dostawcę">×</button></div>`
    + `<textarea rows="2" spellcheck="false" placeholder="http://user:pass@host:port">${escapeHtml(value || "")}</textarea>`;
  row.querySelector(".lj-time-del").addEventListener("click", () => row.remove());
  wrap.appendChild(row);
}

function ljPaintProxyUrls(map) {
  const wrap = $("lj-proxy-urls");
  if (!wrap) return;
  const src = map && Object.keys(map).length ? map : { webshare: "", rayobyte: "" };
  wrap.innerHTML = "";
  Object.keys(src).sort().forEach((name) => ljAddProxyUrlRow(name, src[name]));
}

function ljCollectProxyUrls() {
  const out = {};
  document.querySelectorAll("#lj-proxy-urls .lj-proxy-url-row").forEach((row) => {
    const name = row.dataset.name;
    const ta = row.querySelector("textarea");
    if (name) out[name] = (ta && ta.value) || "";
  });
  return out;
}

async function ljSaveProxyUrls() {
  await api("/api/filter-proxies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proxies: ljCollectProxyUrls() }),
  });
}

function ljPaintTimes(times) {
  const wrap = $("lj-times");
  if (!wrap) return;
  wrap.innerHTML = "";
  const list = (times && times.length) ? times : ["10:30", "18:00"];
  list.forEach((t) => ljAddTimeRow(t));
}

function ljCollectForm() {
  return {
    enabled: !!($("lj-enabled") && $("lj-enabled").checked),
    times: ljCollectTimes(),
    limit_per_run: Number($("lj-limit").value) || 100,
    sleep_between_s: Number($("lj-sleep-between").value) || 0,
    sleep_request_s: Number($("lj-sleep-request").value) || 0,
    request_timeout_s: Number($("lj-timeout") && $("lj-timeout").value) || 30,
    retry_backoff_base_s: Number($("lj-backoff-base") && $("lj-backoff-base").value) || 4,
    retry_backoff_max_s: Number($("lj-backoff-max") && $("lj-backoff-max").value) || 30,
    proxy: ($("lj-proxy") && $("lj-proxy").value) || "losowo",
    followers_min: Number($("lj-fmin").value) || 0,
    followers_max: Number($("lj-fmax").value) || 20000,
    min_posts: Number($("lj-min-posts").value) || 0,
    require_public: !!($("lj-public") && $("lj-public").checked),
    reject_arabic_bio: !!($("lj-arabic") && $("lj-arabic").checked),
  };
}

function ljPaintForm(st) {
  const cfg = (st && st.config) || {};
  const en = $("lj-enabled");
  if (en) en.checked = !!cfg.enabled;
  const lab = $("lj-enabled-lab");
  if (lab) lab.textContent = cfg.enabled ? "ON" : "OFF";
  const lim = $("lj-limit");
  if (lim) lim.value = cfg.limit_per_run ?? 100;
  if ($("lj-sleep-between")) $("lj-sleep-between").value = cfg.sleep_between_s ?? 0;
  if ($("lj-sleep-request")) $("lj-sleep-request").value = cfg.sleep_request_s ?? 2;
  if ($("lj-timeout")) $("lj-timeout").value = cfg.request_timeout_s ?? 30;
  if ($("lj-backoff-base")) $("lj-backoff-base").value = cfg.retry_backoff_base_s ?? 4;
  if ($("lj-backoff-max")) $("lj-backoff-max").value = cfg.retry_backoff_max_s ?? 30;
  const px = $("lj-proxy");
  if (px) {
    const names = (st.proxies && st.proxies.names) || [];
    const want = [...new Set(["losowo", ...names, "webshare", "rayobyte"])];
    const keep = cfg.proxy || px.value || "losowo";
    px.innerHTML = want.map((n) =>
      `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    px.value = want.includes(keep) ? keep : "losowo";
  }
  if ($("lj-fmin")) $("lj-fmin").value = cfg.followers_min ?? 60;
  if ($("lj-fmax")) $("lj-fmax").value = cfg.followers_max ?? 20000;
  if ($("lj-min-posts")) $("lj-min-posts").value = cfg.min_posts ?? 1;
  if ($("lj-public")) $("lj-public").checked = cfg.require_public !== false;
  if ($("lj-arabic")) $("lj-arabic").checked = cfg.reject_arabic_bio !== false;
  ljPaintTimes(cfg.times);
  ljFormBound = true;
}

function ljPaintJob(st) {
  const job = (st && st.job) || {};
  const running = !!job.running && (!job.account || job.account === ljAccount);
  const stopping = !!job.stopping;
  const el = $("lj-job-status");
  if (el) {
    const label = running ? (stopping ? "filtr STOP…" : "filtr RUNNING") : "filtr IDLE";
    const cls = running ? (stopping ? "stop" : "on") : "";
    el.innerHTML = `<i class="lj-job-dot ${cls}" aria-hidden="true"></i> ${label}`;
  }
  const stopBtn = $("lj-stop");
  if (stopBtn) stopBtn.disabled = !running;
  const pending = Number(st && st.pending) || 0;
  if ($("lj-pending")) $("lj-pending").textContent = `pending filtra: ${pending}`;
  if ($("lj-filter-foot")) {
    $("lj-filter-foot").textContent = `${pending} pending — kolejka HTTP, nie telefonu.`;
  }
  if ($("lj-kpi-raw")) $("lj-kpi-raw").textContent = String(st.raw_unique ?? "—");
  if ($("lj-kpi-cand")) $("lj-kpi-cand").textContent = String(st.candidates ?? "—");
  const last = st.last_run || job.result || {};
  const finished = last.finished_at || "";
  if ($("lj-last")) {
    const extra = (last.checked != null)
      ? ` · checked ${last.checked} / saved ${last.saved ?? "—"} / removed ${last.removed ?? "—"}`
      : "";
    $("lj-last").textContent = ljFmtLastRun(finished) + extra;
  }
  ljPaintLog(job);
}

function ljPaintLog(job) {
  const pre = $("lj-log");
  if (!pre) return;
  if (ljLogFull && job && job.terminal_log) {
    pre.textContent = job.terminal_log;
    pre.classList.add("full");
    return;
  }
  const tail = (job && job.terminal_tail) || [];
  pre.textContent = tail.length ? tail.join("\n") : "—";
  pre.classList.toggle("full", false);
}

function ljPaintRaw(data) {
  const body = $("lj-raw-body");
  const count = $("lj-raw-count");
  const more = $("lj-raw-more");
  if (count) count.textContent = String((data && data.unique) != null ? data.unique : (data && data.total) || 0);
  const items = (data && data.items) || [];
  if (body) {
    body.innerHTML = items.length ? items.map((r) => {
      const src = ljSourceKind(r.source);
      return `<div class="lj-row">`
        + `<span class="lj-nick">@${escapeHtml(r.username || "—")}</span>`
        + `<span class="lj-fol">${escapeHtml(ljFmtFollowers(r.followers))}</span>`
        + `<span class="lj-src ${src}">${escapeHtml(ljSourceLabel(r.source))}</span>`
        + `</div>`;
    }).join("") : `<div class="lj-empty">Brak raw_profiles.</div>`;
  }
  if (more) more.hidden = !data || data.shown >= data.unique || data.shown >= (data.total || 0);
}

function ljPaintCand(data) {
  const body = $("lj-cand-body");
  const count = $("lj-cand-count");
  const more = $("lj-cand-more");
  if (count) count.textContent = String((data && data.total) || 0);
  const items = (data && data.items) || [];
  if (body) {
    body.innerHTML = items.length ? items.map((r) =>
      `<div class="lj-row lj-row-2">`
      + `<span class="lj-nick">@${escapeHtml(r.username || "—")}</span>`
      + `<span class="lj-ok">ok</span>`
      + `</div>`
    ).join("") : `<div class="lj-empty">Brak kandydatów — odpal filtr.</div>`;
  }
  if (more) more.hidden = !data || data.shown >= (data.total || 0);
}

function ljPaintVisit() {
  const body = $("lj-visit-body");
  let items = ljVisitItems;
  if (ljVisitFilter === "interact") items = items.filter((r) => r.interacted);
  else if (ljVisitFilter === "none") items = items.filter((r) => !r.interacted);
  if (body) {
    body.innerHTML = items.length ? items.map((r) => {
      const oc = r.outcome || "skip";
      return `<div class="lj-row lj-row-3">`
        + `<span class="lj-nick">@${escapeHtml(r.username || "—")}</span>`
        + `<span class="lj-fol">${escapeHtml(ljFmtTime(r.ts))}</span>`
        + `<span class="lj-out ${ljOutcomeClass(oc)}">${escapeHtml(oc)}</span>`
        + `</div>`;
    }).join("") : `<div class="lj-empty">${
      ljVisitItems.length ? "Brak w tym filtrze." : "Brak wizyt."
    }</div>`;
  }
}

function ljPaintVisitMeta(data) {
  const count = $("lj-visit-count");
  if (count) count.textContent = String((data && data.total) || 0);
  const today = (data && data.today) || {};
  if ($("lj-kpi-today")) $("lj-kpi-today").textContent = String(today.viewed ?? "—");
  if ($("lj-kpi-ix")) {
    const n = today.interacted;
    const m = today.viewed;
    $("lj-kpi-ix").textContent = (n == null || m == null) ? "—" : `${n}/${m}`;
  }
  const more = $("lj-visit-more");
  if (more) more.hidden = !data || data.shown >= (data.total || 0);
}

async function ljSaveForm() {
  const acc = ljAccount;
  if (!acc) return;
  await api(`/api/profile-filter/${encodeURIComponent(acc)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ljCollectForm()),
  });
}

function ljScheduleSave() {
  clearTimeout(ljSaveTimer);
  ljSaveTimer = setTimeout(() => {
    ljSaveForm().catch((e) => showBanner("lj-msg", e.message || "zapis filtra", "err"));
  }, 450);
}

async function refreshLejek(opts) {
  const reloadForm = !!(opts && opts.reloadForm);
  await ensureAccounts();
  const sel = $("lj-account");
  const names = accounts.map((a) => a.alias);
  const prefer = ljAccount && names.includes(ljAccount) ? ljAccount : names[0];
  if (sel) fillSelect(sel, names, prefer);
  const acc = (sel && sel.value) || prefer || "";
  if (acc !== ljAccount) {
    ljAccount = acc;
    ljFormBound = false;
    ljLimits = { raw: 40, cand: 40, visit: 40 };
  }
  const title = $("lj-filter-title");
  if (title) title.textContent = (acc || "konto") + " filtr HTTP (bez telefonu)";
  updateSessionLine();
  if (!acc) {
    showBanner("lj-msg", "Brak kont.", "err");
    return;
  }
  try {
    const [flt, raw, cand, vis] = await Promise.all([
      api(`/api/profile-filter/${encodeURIComponent(acc)}`),
      api(`/api/raw-profiles/${encodeURIComponent(acc)}?limit=${ljLimits.raw}&unique=1`),
      api(`/api/target-candidates/${encodeURIComponent(acc)}?limit=${ljLimits.cand}`),
      api(`/api/visited-profiles/${encodeURIComponent(acc)}?limit=${ljLimits.visit}`),
    ]);
    if (reloadForm || !ljFormBound) {
      ljPaintForm(flt);
      try {
        const pxMap = await api("/api/filter-proxies");
        ljPaintProxyUrls(pxMap.proxies);
      } catch (_) {
        ljPaintProxyUrls({});
      }
    }
    ljPaintJob(flt);
    ljPaintRaw(raw);
    ljPaintCand(cand);
    ljVisitItems = vis.items || [];
    ljPaintVisitMeta(vis);
    ljPaintVisit();
    showBanner("lj-msg", "");
  } catch (e) {
    showBanner("lj-msg", e.message || "lejek: błąd API", "err");
  }
}

async function ljRunFilter() {
  if (!ljAccount) return;
  showBanner("lj-msg", "zapisuję i odpalam filtr…");
  try {
    await ljSaveForm();
    await api(`/api/profile-filter/${encodeURIComponent(ljAccount)}/run`, { method: "POST" });
    showBanner("lj-msg", "filtr w tle — bez telefonu", "ok");
    await refreshLejek({ reloadForm: false });
  } catch (e) {
    showBanner("lj-msg", e.message || "start filtra", "err");
  }
}

async function ljStopFilter() {
  if (!ljAccount) return;
  showBanner("lj-msg", "zatrzymuję filtr…");
  try {
    await api(`/api/profile-filter/${encodeURIComponent(ljAccount)}/stop`, { method: "POST" });
    showBanner("lj-msg", "wysłano STOP", "ok");
    await refreshLejek({ reloadForm: false });
  } catch (e) {
    showBanner("lj-msg", e.message || "stop filtra", "err");
  }
}

async function ljToggleLog() {
  ljLogFull = !ljLogFull;
  const btn = $("lj-log-more");
  if (btn) btn.textContent = ljLogFull ? "Zwiń log" : "Pokaż więcej logów";
  if (!ljAccount) return;
  try {
    const logSt = await api(`/api/profile-filter/${encodeURIComponent(ljAccount)}/log`);
    ljPaintLog({
      terminal_tail: logSt.terminal_tail,
      terminal_log: logSt.terminal_log,
    });
  } catch (_) { /* ogon ze statusu wystarczy */ }
}

function bindLejekUi() {
  const accSel = $("lj-account");
  if (!accSel) return;
  accSel.addEventListener("change", () => {
    ljAccount = accSel.value;
    ljFormBound = false;
    ljLimits = { raw: 40, cand: 40, visit: 40 };
    refreshLejek({ reloadForm: true }).catch(() => {});
  });
  // Brak przycisku Lejka nie może uciąć applyView() — inaczej #na-zywo zostaje hidden.
  const ljClick = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  ljClick("lj-run-top", () => ljRunFilter());
  ljClick("lj-run", () => ljRunFilter());
  ljClick("lj-stop", () => ljStopFilter());
  ljClick("lj-time-add", () => {
    ljAddTimeRow("12:00");
    ljScheduleSave();
  });
  ljClick("lj-proxy-add", () => {
    const inp = $("lj-proxy-new-name");
    const name = ((inp && inp.value) || "").trim();
    if (!name) {
      showBanner("lj-msg", "wpisz nazwę dostawcy", "err");
      return;
    }
    const exists = [...document.querySelectorAll("#lj-proxy-urls .lj-proxy-url-row")]
      .some((r) => r.dataset.name === name);
    if (exists) return;
    ljAddProxyUrlRow(name, "");
    if (inp) inp.value = "";
  });
  ljClick("lj-proxy-save", () => {
    ljSaveProxyUrls()
      .then(() => {
        showBanner("lj-msg", "zapisano adresy proxy", "ok");
        return refreshLejek({ reloadForm: true });
      })
      .catch((e) => showBanner("lj-msg", e.message || "zapis proxy", "err"));
  });
  const ljEn = $("lj-enabled");
  if (ljEn) ljEn.addEventListener("change", () => {
    const lab = $("lj-enabled-lab");
    if (lab) lab.textContent = $("lj-enabled").checked ? "ON" : "OFF";
    ljScheduleSave();
  });
  ["lj-proxy", "lj-fmin", "lj-fmax", "lj-min-posts", "lj-limit",
    "lj-sleep-between", "lj-sleep-request", "lj-timeout",
    "lj-backoff-base", "lj-backoff-max", "lj-public", "lj-arabic"]
    .forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("change", () => ljScheduleSave());
    });
  ljClick("lj-raw-more", () => {
    ljLimits.raw = Math.min(500, ljLimits.raw + 40);
    refreshLejek({ reloadForm: false }).catch(() => {});
  });
  ljClick("lj-cand-more", () => {
    ljLimits.cand = Math.min(500, ljLimits.cand + 40);
    refreshLejek({ reloadForm: false }).catch(() => {});
  });
  ljClick("lj-visit-more", () => {
    ljLimits.visit = Math.min(500, ljLimits.visit + 40);
    refreshLejek({ reloadForm: false }).catch(() => {});
  });
  ljClick("lj-log-more", () => ljToggleLog());
  document.querySelectorAll("#lj-visit-tabs .lj-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      ljVisitFilter = btn.dataset.ljVisit || "all";
      document.querySelectorAll("#lj-visit-tabs .lj-tab").forEach((b) => {
        b.classList.toggle("on", b === btn);
      });
      ljPaintVisit();
    });
  });
}

// ============================================================
// Treści — kolejka z tożsamością (konto + typ + nazwa kanoniczna)
// ============================================================
let trDevice = "";
let trAccount = "";       // puste = całe urządzenie (grupowanie po koncie)
let trKind = "";
let trStatus = "";
let trItems = [];
let trSel = null;         // {account, id}
const TR_ST_LABEL = {
  ready: "Gotowe",
  on_device: "Na telefonie",
  publishing: "Publikacja",
  published: "Opublikowane",
  failed: "Błąd",
};

function trDefaultDevice() {
  // Jadący telefon albo focus z Pulpitu — nie pierwszy z listy na ślepo.
  const running = sessionsList().find((s) => s && s.running);
  if (running && running.device_alias) return running.device_alias;
  if (pulpitFocusDevice) return pulpitFocusDevice;
  if (liveFocusDevice) return liveFocusDevice;
  return (devices[0] && devices[0].alias) || "";
}

function trCanUpload() {
  return !!(trAccount && $("tr-upload-kind") && $("tr-upload-kind").value);
}

function trSyncDrop() {
  const drop = $("tr-drop");
  if (!drop) return;
  const ok = trCanUpload();
  drop.classList.toggle("is-blocked", !ok);
  const hint = $("tr-drop-hint");
  if (hint) {
    hint.textContent = ok
      ? "Zostaną w kolejce jako Gotowe. Plugin wypchnie je przy sesji (nazwa kanoniczna od razu)."
      : "Wybierz konto i typ (Post / Relacja) — bez tego nie wolno wrzucić sieroty.";
  }
}

function trPaintAccounts() {
  const box = $("tr-accounts");
  if (!box) return;
  const onDev = accounts.filter((a) => a.device_alias === trDevice);
  if (!trAccount && onDev.length === 1) trAccount = onDev[0].alias;
  if (trAccount && !onDev.some((a) => a.alias === trAccount)) trAccount = "";
  const chips = [{ alias: "", label: "Wszystkie konta" }].concat(
    onDev.map((a) => ({ alias: a.alias, label: a.alias })),
  );
  box.innerHTML = chips.map((c) => {
    const on = (c.alias || "") === (trAccount || "") ? " on" : "";
    const color = c.alias ? accColor(c.alias) : "var(--dim)";
    return `<button type="button" class="tr-acc${on}" data-tr-acc="${escapeHtml(c.alias)}"
      ><i class="dot" style="background:${color}"></i>${escapeHtml(c.label)}</button>`;
  }).join("");
  box.querySelectorAll("[data-tr-acc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      trAccount = btn.dataset.trAcc || "";
      trSyncDrop();
      refreshTresci().catch(() => {});
    });
  });
}

function trKindLabel(kind) {
  return kind === "story" ? "Relacja" : "Post";
}

function trCardHtml(item) {
  const acc = item.account || "";
  const name = ((item.files || [])[0] || {}).canonical_name || item.id;
  const color = accColor(acc);
  const st = item.status || "ready";
  const thumb = item.is_image && item.file_url
    ? `<img src="${escapeHtml(item.file_url)}" alt="" />`
    : (item.is_video ? "wideo" : "plik");
  return `<article class="tr-card${trSel && trSel.id === item.id && trSel.account === acc ? " sel" : ""}"
      data-tr-id="${escapeHtml(item.id)}" data-tr-acc="${escapeHtml(acc)}">
    <div class="tr-thumb">${thumb}</div>
    <div class="tr-card-body">
      <div class="tr-cname">${escapeHtml(name)}</div>
      <div class="tr-meta">
        <span class="dot" style="width:7px;height:7px;border-radius:50%;background:${color};display:inline-block"></span>
        <span>${escapeHtml(acc)}</span>
        <span class="tr-kind">${trKindLabel(item.kind)}</span>
        <span class="tr-st ${escapeHtml(st)}">${escapeHtml(TR_ST_LABEL[st] || st)}</span>
      </div>
    </div>
  </article>`;
}

function trPaintGrid() {
  const box = $("tr-grid");
  if (!box) return;
  if (!trItems.length) {
    box.innerHTML = `<div class="empty">Brak plików w kolejce — źródłem prawdy jest PC, nie galeria na telefonie.</div>`;
    return;
  }
  const groupByAcc = !trAccount;
  if (!groupByAcc) {
    box.innerHTML = `<div class="tr-cards">${trItems.map(trCardHtml).join("")}</div>`;
  } else {
    const groups = {};
    for (const it of trItems) {
      const a = it.account || "?";
      (groups[a] || (groups[a] = [])).push(it);
    }
    box.innerHTML = Object.keys(groups).sort().map((acc) => {
      const color = accColor(acc);
      return `<section>
        <h3 class="tr-group-title"><i class="dot" style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block"></i>${escapeHtml(acc)}</h3>
        <div class="tr-cards">${groups[acc].map(trCardHtml).join("")}</div>
      </section>`;
    }).join("");
  }
  box.querySelectorAll(".tr-card").forEach((el) => {
    el.addEventListener("click", () => {
      trSel = { account: el.dataset.trAcc, id: el.dataset.trId };
      trPaintGrid();
      trPaintInsp();
    });
  });
}

function trSelectedItem() {
  if (!trSel) return null;
  return trItems.find((i) => i.id === trSel.id && i.account === trSel.account) || null;
}

function trPaintInsp() {
  const box = $("tr-insp");
  if (!box) return;
  const it = trSelectedItem();
  if (!it) {
    box.innerHTML = `<p class="placeholder">Wybierz kartę — opis i usuwanie. Publikacja idzie z Runów (wybór pliku w kroku).</p>`;
    return;
  }
  const name = ((it.files || [])[0] || {}).canonical_name || it.id;
  const st = it.status || "ready";
  const locked = st === "published";
  box.innerHTML = `
    <h3>${escapeHtml(name)}</h3>
    <p class="tr-meta">
      <span class="dot" style="width:8px;height:8px;border-radius:50%;background:${accColor(it.account)};display:inline-block"></span>
      ${escapeHtml(it.account)} · ${trKindLabel(it.kind)} ·
      <span class="tr-st ${escapeHtml(st)}">${escapeHtml(TR_ST_LABEL[st] || st)}</span>
    </p>
    <label>Opis (post; relacja opcjonalnie)
      <textarea id="tr-caption" ${locked ? "disabled" : ""}>${escapeHtml(it.caption || "")}</textarea>
    </label>
    <p class="path">PC: ${escapeHtml(((it.files || [])[0] || {}).local_name || "—")}</p>
    <p class="path">Telefon: ${escapeHtml(it.phone_path || "— (plugin wypchnie przy sesji)")}</p>
    ${it.error ? `<p class="banner err" style="display:block">${escapeHtml(it.error)}</p>` : ""}
    <div class="tr-insp-actions">
      <button type="button" id="tr-save-cap" ${locked ? "disabled" : ""}>Zapisz opis</button>
      <button type="button" id="tr-retry" ${st === "failed" ? "" : "hidden"}>Ponów</button>
      <button type="button" class="danger insp-del" id="tr-del">Usuń</button>
    </div>`;
  const capBtn = $("tr-save-cap");
  if (capBtn) capBtn.addEventListener("click", () => trPatch({ caption: $("tr-caption").value }));
  const retryBtn = $("tr-retry");
  if (retryBtn) retryBtn.addEventListener("click", () => trPatch({ status: "ready" }));
  const delBtn = $("tr-del");
  if (delBtn) delBtn.addEventListener("click", () => trDelete());
}

async function trPatch(fields) {
  const it = trSelectedItem();
  if (!it) return;
  try {
    await api(`/api/publish/${encodeURIComponent(it.account)}/${encodeURIComponent(it.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    await refreshTresci();
  } catch (e) {
    showBanner("tr-msg", e.message || "zapis", "err");
  }
}

async function trPush() {
  const it = trSelectedItem();
  if (!it) return;
  try {
    await api(`/api/publish/${encodeURIComponent(it.account)}/${encodeURIComponent(it.id)}/push`, {
      method: "POST",
    });
    showBanner("tr-msg", "wypchnięto na telefon", "ok");
    await refreshTresci();
  } catch (e) {
    showBanner("tr-msg", e.message || "push", "err");
  }
}

async function trDelete() {
  const it = trSelectedItem();
  if (!it) return;
  if (!confirm("Usunąć plik z kolejki na PC" + (it.phone_path ? " i z telefonu?" : "?"))) return;
  try {
    await api(`/api/publish/${encodeURIComponent(it.account)}/${encodeURIComponent(it.id)}`, {
      method: "DELETE",
    });
    trSel = null;
    await refreshTresci();
  } catch (e) {
    showBanner("tr-msg", e.message || "usuń", "err");
  }
}

async function trUploadFiles(fileList) {
  if (!trCanUpload()) {
    showBanner("tr-msg", "Wybierz konto i typ (Post / Relacja), zanim wrzucisz plik.", "err");
    return;
  }
  const kind = $("tr-upload-kind").value;
  const files = [...fileList];
  for (const f of files) {
    const fd = new FormData();
    fd.append("account", trAccount);
    fd.append("kind", kind);
    fd.append("file", f, f.name);
    try {
      await api("/api/publish", { method: "POST", body: fd });
    } catch (e) {
      showBanner("tr-msg", e.message || "upload", "err");
      return;
    }
  }
  showBanner("tr-msg", "dodano " + files.length + " plik(ów) — zostają Gotowe na PC", "ok");
  await refreshTresci();
}

async function refreshTresci() {
  if (!$("view-tresci")) return;
  // Każdy panel osobno — throw w chips/siatce/insp nie gasi reszty (jak paintLive*).
  try {
    const aliases = (devices || []).map((d) => d.alias).filter(Boolean);
    if (!trDevice || !aliases.includes(trDevice)) trDevice = trDefaultDevice();
    const devSel = $("tr-device");
    if (devSel) fillSelect(devSel, aliases, trDevice);
    if (devSel && devSel.value) trDevice = devSel.value;
  } catch (_) { /* select nie blokuje siatki */ }
  try { trPaintAccounts(); } catch (_) { /* ignore */ }
  try { trSyncDrop(); } catch (_) { /* ignore */ }
  const q = new URLSearchParams();
  if (trDevice) q.set("device", trDevice);
  if (trAccount) q.set("account", trAccount);
  if (trKind) q.set("kind", trKind);
  if (trStatus) q.set("status", trStatus);
  try {
    const data = await api("/api/publish?" + q.toString());
    trItems = (data && data.items) || [];
  } catch (_) {
    trItems = [];
  }
  if (trSel && !trItems.some((i) => i.id === trSel.id && i.account === trSel.account)) {
    trSel = null;
  }
  try { trPaintGrid(); } catch (_) { /* ignore */ }
  try { trPaintInsp(); } catch (_) { /* ignore */ }
}

function bindTresciUi() {
  const dev = $("tr-device");
  if (!dev) return;
  dev.addEventListener("change", () => {
    trDevice = dev.value;
    trAccount = "";
    refreshTresci().catch(() => {});
  });
  document.querySelectorAll("#tr-kind-chips [data-tr-kind]").forEach((btn) => {
    btn.addEventListener("click", () => {
      trKind = btn.dataset.trKind || "";
      document.querySelectorAll("#tr-kind-chips [data-tr-kind]").forEach((b) => {
        b.classList.toggle("on", b === btn);
      });
      refreshTresci().catch(() => {});
    });
  });
  document.querySelectorAll("#tr-status-chips [data-tr-status]").forEach((btn) => {
    btn.addEventListener("click", () => {
      trStatus = btn.dataset.trStatus || "";
      document.querySelectorAll("#tr-status-chips [data-tr-status]").forEach((b) => {
        b.classList.toggle("on", b === btn);
      });
      refreshTresci().catch(() => {});
    });
  });
  const kindSel = $("tr-upload-kind");
  if (kindSel) kindSel.addEventListener("change", () => trSyncDrop());
  const addBtn = $("tr-add");
  const fileInp = $("tr-file");
  if (addBtn && fileInp) {
    addBtn.addEventListener("click", () => {
      if (!trCanUpload()) {
        showBanner("tr-msg", "Wybierz konto i typ (Post / Relacja), zanim wrzucisz plik.", "err");
        return;
      }
      fileInp.click();
    });
    fileInp.addEventListener("change", () => {
      if (fileInp.files && fileInp.files.length) trUploadFiles(fileInp.files);
      fileInp.value = "";
    });
  }
  const drop = $("tr-drop");
  if (drop) {
    ["dragenter", "dragover"].forEach((ev) => {
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        if (trCanUpload()) drop.classList.add("is-over");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.remove("is-over");
      });
    });
    drop.addEventListener("drop", (e) => {
      const files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) trUploadFiles(files);
    });
    drop.addEventListener("click", (e) => {
      if (e.target.closest("button, select, label, input")) return;
      if (!trCanUpload()) {
        showBanner("tr-msg", "Wybierz konto i typ (Post / Relacja), zanim wrzucisz plik.", "err");
      }
    });
  }
}

// Bind UI w try — throw (brak węzła po merge HTML) nie może uciąć applyView().
// Inaczej #view-tresci / #view-na-zywo zostają hidden i pada cały hash-router.
try {
  bindLejekUi();
} catch (_) { /* Lejek nie blokuje raila */ }

try {
  const runyAcc = $("runy-account");
  if (runyAcc) runyAcc.addEventListener("change", () => {
    runyAccount = $("runy-account").value;
    runyQueues = {};
    runyDirty = {};
    runyEst = {};
    runySelectedTaskId = "";
    pluginAccountSettings = {};
    runyActions = [];
    runyActionEditId = "";
    refreshRuny().catch(() => {});
  });
  const runySel = $("runy-select");
  if (runySel) runySel.addEventListener("change", () => {
    selectRun($("runy-select").value);
  });
  const runyNew = $("runy-new");
  if (runyNew) runyNew.addEventListener("click", () => createRun());
  const runyNewName = $("runy-new-name");
  if (runyNewName) runyNewName.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") createRun();
  });
  const runySave = $("runy-save");
  if (runySave) runySave.addEventListener("click", () => saveRun(runyActiveName));
  const runyDel = $("runy-del");
  if (runyDel) runyDel.addEventListener("click", () => deleteRun(runyActiveName));
  const runyDry = $("runy-dry");
  if (runyDry) runyDry.addEventListener("click", () => dryRunQueue());
  ["runy-gap-min", "runy-gap-max"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("input", () => applyQueueFoot());
  });
  document.querySelectorAll('input[name="runy-onerror"]').forEach((r) => {
    r.addEventListener("change", () => applyQueueFoot());
  });
  document.querySelectorAll(".run-lib-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.libTab;
      if (!tab || tab === runyLibTab) return;
      runyLibTab = tab;
      runyActionEditId = "";
      renderLibrary();
      // Tacka zapisanych — po „Zapisz jako akcję” warto ją pokazać w kadrze.
      if (tab === "saved") {
        const tray = document.querySelector(".run-lib-tray");
        if (tray) tray.scrollIntoView({ block: "nearest" });
      }
    });
  });
  const tlRegen = $("tl-regen");
  if (tlRegen) tlRegen.addEventListener("click", () => regenWeek());
  const liveStopBtn = $("live-stop");
  if (liveStopBtn) liveStopBtn.addEventListener("click", () => stopLiveSession());
  const liveFpsInp = $("live-fps-input");
  if (liveFpsInp) {
    liveFpsInp.addEventListener("change", () => {
      const n = parseInt(liveFpsInp.value, 10);
      if (!Number.isFinite(n)) return;
      setLiveScrcpyFps(n);
    });
  }
  const liveAutoBtn = $("live-autoscroll");
  if (liveAutoBtn) {
    liveAutoBtn.addEventListener("click", () => {
      liveAutoscroll = !liveAutoscroll;
      liveAutoBtn.classList.toggle("on", liveAutoscroll);
      liveAutoBtn.setAttribute("aria-pressed", liveAutoscroll ? "true" : "false");
      liveAutoBtn.textContent = liveAutoscroll ? "Autoscroll ON" : "Autoscroll OFF";
      if (liveAutoscroll) {
        paintLiveLog();
        paintLiveTerminal();
      }
    });
  }
  const liveFilter = $("live-log-filter");
  if (liveFilter) {
    liveFilter.addEventListener("input", () => {
      liveLogFilter = liveFilter.value || "";
      paintLiveLog();
    });
  }
  const liveTermInp = $("live-term-filter");
  if (liveTermInp) {
    liveTermInp.addEventListener("input", () => {
      liveTermFilter = liveTermInp.value || "";
      paintLiveTerminal();
    });
  }
  const ustSecretsSave = $("ust-secrets-save");
  if (ustSecretsSave) {
    ustSecretsSave.addEventListener("click", () => saveUstSecrets(false));
    const ustClear = $("ust-secrets-clear");
    if (ustClear) ustClear.addEventListener("click", () => {
      if (confirm("Usunąć zapisany klucz API? AI Vision wróci do trybu stub.")) {
        saveUstSecrets(true);
      }
    });
    const ustEn = $("ust-sched-enabled");
    if (ustEn) ustEn.addEventListener("change", () => saveUstSchedulerEnabled());
    const ustSchedSave = $("ust-sched-save");
    if (ustSchedSave) ustSchedSave.addEventListener("click", () => saveUstSchedulerFrames());
    const ustBotSave = $("ust-bot-save");
    if (ustBotSave) ustBotSave.addEventListener("click", () => saveUstBot());
    const stepsBox = $("ust-steps-box");
    if (stepsBox) stepsBox.addEventListener("toggle", () => {
      if (stepsBox.open) loadUstKnownSteps();
    });
  }
  const livePreviewBox = $("live-preview");
  if (livePreviewBox) livePreviewIo.observe(livePreviewBox);
  bindFlotaOnce();
  bindTresciUi();
} catch (_) { /* bind (Runy/Ustawienia/Treści) nie blokuje applyView */ }

applyView();
tickClock();
setInterval(tickClock, 1000);
refreshPulpit().then(() => pollPreviews());
setInterval(() => { refreshPulpit().catch(() => {}); }, POLL_MS);
setInterval(() => {
  if (document.hidden || currentView() !== "lejek") return;
  refreshLejek({ reloadForm: false }).catch(() => {});
}, POLL_MS);
setInterval(() => {
  if (document.hidden || currentView() !== "tresci") return;
  refreshTresci().catch(() => {});
}, POLL_MS);
setInterval(() => { pollPreviews().catch(() => {}); }, PREVIEW_MS);
armLivePreviewPoll();
setInterval(() => { pollLiveStats().catch(() => {}); }, LIVE_STATS_MS);
setInterval(() => { if (typeof pollKontaAlerts === "function") pollKontaAlerts().catch(() => {}); }, POLL_MS);
setInterval(() => {
  if (document.hidden || currentView() !== "flota") return;
  refreshFlotaCalib().catch(() => {});
}, POLL_MS);
