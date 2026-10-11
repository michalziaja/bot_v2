/* Widok Statystyki w /app — native KPI/limity/sesje; iframe /stats pod foldem. */
"use strict";

(function () {
  const POLL_MS = 4000;
  const SNAP_LIMIT = 12;
  const $ = (id) => document.getElementById(id);

  let mode = "24h";
  let lastFrameSrc = "";
  let pollTimer = null;
  let fetching = false;

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function statsViewActive() {
    const raw = (location.hash || "#pulpit").replace(/^#/, "") || "pulpit";
    return raw === "statystyki";
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
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

  function counterOf(map, key) {
    return Number((map || {})[key] || 0) || 0;
  }

  /** Harvest = zapisani z search/scraper (saved + likers + commenters). */
  function harvestOf(c) {
    return counterOf(c, "saved")
      + counterOf(c, "likers_saved")
      + counterOf(c, "commenters_saved");
  }

  /** Polubienia sesji: liked + kafelki + komentarze (jak SessionStats._count_likes). */
  function likesOf(c) {
    return counterOf(c, "liked")
      + counterOf(c, "tiles_liked")
      + counterOf(c, "likes")
      + counterOf(c, "comment_likes");
  }

  function fmtUsd(n) {
    const v = Number(n) || 0;
    if (!v) return "$0";
    return "$" + v.toFixed(v < 0.01 ? 4 : 2);
  }

  function fmtInt(n) {
    const v = Math.round(Number(n) || 0);
    return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function fmtTok(n) {
    const v = Number(n) || 0;
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(v >= 10_000_000 ? 0 : 1) + "M";
    if (v >= 1000) return Math.round(v / 1000) + "k";
    return String(Math.round(v));
  }

  function fmtMin(s) {
    const sec = Math.max(0, Number(s) || 0);
    if (sec < 90) return Math.round(sec) + " s";
    const m = Math.round(sec / 60);
    if (m < 60) return m + " min";
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return mm ? (h + "h " + mm + "m") : (h + "h");
  }

  function parseTs(value) {
    if (!value || typeof value !== "string") return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function fmtWhen(iso) {
    const d = parseTs(iso);
    if (!d) return "—";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return dd + "." + mm + " " + hh + ":" + mi;
  }

  function ymd(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function ensureDefaultDates() {
    const fromEl = $("st-from");
    const toEl = $("st-to");
    if (!fromEl || !toEl) return;
    if (!toEl.value) toEl.value = ymd(new Date());
    if (!fromEl.value) {
      const y = new Date();
      y.setDate(y.getDate() - 1);
      fromEl.value = ymd(y);
    }
  }

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

  function sessionInWindow(s, start, end) {
    const d = parseTs(s && (s.started_at || s.ended_at || s.updated_at || s.anchor_at));
    if (!d) return false;
    return d >= start && d <= end;
  }

  function windowBounds() {
    const now = new Date();
    if (mode === "24h") {
      return { start: new Date(now.getTime() - 24 * 3600 * 1000), end: now };
    }
    if (mode === "range") {
      ensureDefaultDates();
      const from = $("st-from") && $("st-from").value;
      const to = $("st-to") && $("st-to").value;
      if (!from || !to) return null;
      const start = new Date(from + "T00:00:00");
      const end = new Date(to + "T23:59:59");
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
      return { start, end };
    }
    return null;
  }

  function queueName(row) {
    const q = row && row.queue;
    if (q && typeof q === "object") return q.name || "—";
    if (typeof q === "string" && q) return q;
    return (row && row.plugin) || "—";
  }

  function sessionDurationS(row, snap) {
    const timing = (snap && snap.timing) || (row && row.timing) || {};
    if (timing.total_s) return Number(timing.total_s) || 0;
    if (timing.wall_s) return Number(timing.wall_s) || 0;
    const a = parseTs(row && row.started_at);
    const b = parseTs((row && row.ended_at) || (row && row.updated_at));
    if (a && b) return Math.max(0, (b - a) / 1000);
    if (a) return Math.max(0, (Date.now() - a.getTime()) / 1000);
    return 0;
  }

  function sessionStatus(row, liveSid, running) {
    const sum = (row && row.summary) || {};
    if (running && liveSid && row && row.session_id === liveSid) {
      return { label: "JEDZIE", cls: "live" };
    }
    if (sum.ok === false || sum.aborted || Number(sum.tasks_failed) > 0) {
      return { label: "FAIL", cls: "fail" };
    }
    if (sum.ok === true) return { label: "OK", cls: "ok" };
    if (row && row.ended_at) return { label: "OK", cls: "ok" };
    return { label: "—", cls: "offline" };
  }

  function showBanner(text, kind) {
    const el = $("st-banner");
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

  function syncModeUi() {
    document.querySelectorAll("#st-modes button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.mode === mode);
    });
    const isRange = mode === "range";
    const isLast = mode === "last";
    const rw = $("st-range-wrap");
    const tw = $("st-range-to-wrap");
    if (rw) rw.hidden = !isRange;
    if (tw) tw.hidden = !isRange;
    const auto = $("st-auto");
    if (auto) auto.hidden = !isLast;
    if (isRange) ensureDefaultDates();
  }

  function setCanvasCtx(acc) {
    const el = $("session-line");
    if (!el || !statsViewActive()) return;
    el.textContent = acc || "wybierz konto";
  }

  function renderKpi(box, counters, extra) {
    const c = counters || {};
    const ast = (extra && extra.account_stats) || {};
    const startF = ast.start && ast.start.followers;
    const endF = ast.end && ast.end.followers;
    const followers = (endF != null ? endF : startF);
    const delta = ast.delta && ast.delta.followers;
    const ai = (extra && extra.ai) || {};
    const tok = (Number(ai.tokens_in) || 0) + (Number(ai.tokens_out) || 0);
    const items = [
      {
        k: "Followers",
        v: followers == null ? "—" : fmtInt(followers),
        delta: delta == null ? "" : ((delta > 0 ? "+ " : "") + fmtInt(delta)),
        dcls: delta > 0 ? "pos" : (delta < 0 ? "neg" : ""),
      },
      { k: "Sesje", v: String((extra && extra.sessions_count) || 0) },
      { k: "Polubienia", v: fmtInt(likesOf(c)) },
      { k: "Follow", v: fmtInt(counterOf(c, "followed")) },
      { k: "Harvest", v: fmtInt(harvestOf(c)) },
      { k: "AI", v: fmtUsd(ai.cost_usd) + " / " + fmtTok(tok) + " tok" },
    ];
    box.innerHTML = items.map((it) => `
      <article class="kpi-card">
        <div class="k">${escapeHtml(it.k)}</div>
        <div class="v">${escapeHtml(it.v)}</div>
        ${it.delta ? `<div class="delta ${it.dcls || ""}">${escapeHtml(it.delta)}</div>` : ""}
      </article>`).join("");
  }

  function renderPluginBars(box, byPluginTime) {
    const rows = Object.entries(byPluginTime || {})
      .map(([name, sec]) => ({ name, sec: Number(sec) || 0 }))
      .filter((r) => r.sec > 0)
      .sort((a, b) => b.sec - a.sec);
    if (!rows.length) {
      box.innerHTML = `<div class="empty">Brak czasów pluginów w tym oknie.</div>`;
      return;
    }
    const max = rows[0].sec || 1;
    box.innerHTML = rows.map((r, i) => {
      const pct = Math.max(4, Math.round(100 * r.sec / max));
      return `<div class="st-bar-row">
        <div class="st-bar-meta">
          <span class="name">${escapeHtml(r.name)}</span>
          <span class="val">${escapeHtml(fmtMin(r.sec))}</span>
        </div>
        <div class="st-bar-track">
          <div class="st-bar-fill c${i % 6}" style="width:${pct}%"></div>
        </div>
      </div>`;
    }).join("");
  }

  function renderLimits(box, used, limits) {
    const lim = limits || {};
    const u = used || {};
    const rows = [
      { k: "Polubienia", used: Number(u.likes) || 0, cap: Number(lim.daily_likes) || 0, cls: "c0" },
      { k: "Follow", used: Number(u.follows) || 0, cap: Number(lim.daily_follows) || 0, cls: "c1" },
      { k: "Komentarze", used: Number(u.comments) || 0, cap: Number(lim.daily_comments) || 0, cls: "c2" },
    ];
    box.innerHTML = rows.map((r) => {
      const uncapped = !r.cap;
      const pct = uncapped ? 0 : Math.min(100, Math.round(100 * r.used / r.cap));
      const val = uncapped
        ? `${fmtInt(r.used)} / brak limitu`
        : `${fmtInt(r.used)} / ${fmtInt(r.cap)}`;
      return `<div class="st-lim-row">
        <div class="st-lim-meta">
          <span class="name">${escapeHtml(r.k)}</span>
          <span class="val">${escapeHtml(val)}</span>
        </div>
        <div class="st-bar-track">
          <div class="st-bar-fill ${r.cls}" style="width:${uncapped ? 0 : pct}%"></div>
        </div>
      </div>`;
    }).join("");
  }

  function renderSessions(box, rows, liveSid, running) {
    if (!rows || !rows.length) {
      box.innerHTML = `<div class="empty">Brak sesji w wybranym oknie.</div>`;
      return;
    }
    const body = rows.slice(0, 8).map((s) => {
      const st = sessionStatus(s, liveSid, running);
      return `<tr>
        <td>${escapeHtml(fmtWhen(s.started_at))}</td>
        <td class="mono">${escapeHtml(queueName(s))}</td>
        <td>${escapeHtml(fmtMin(sessionDurationS(s)))}</td>
        <td><span class="badge ${st.cls}">${escapeHtml(st.label)}</span></td>
      </tr>`;
    }).join("");
    box.innerHTML = `<table>
      <thead><tr><th>Start</th><th>Runa</th><th>Czas</th><th>Status</th></tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  function sesjeLabel(n) {
    const abs = Math.abs(Number(n) || 0);
    const word = (abs === 1) ? "sesja"
      : (abs % 10 >= 2 && abs % 10 <= 4 && (abs % 100 < 10 || abs % 100 >= 20)
        ? "sesje" : "sesji");
    return abs + " " + word;
  }

  function renderFleet(box, devices, counts) {
    if (!devices || !devices.length) {
      box.innerHTML = `<div class="empty">Brak zarejestrowanych telefonów.</div>`;
      return;
    }
    const suffix = (mode === "range") ? "w zakresie" : "dziś";
    box.innerHTML = devices.map((d) => {
      const n = Number((counts || {})[d.alias]) || 0;
      const online = !!(d.connected);
      return `<article class="st-fleet-card${online ? "" : " dim"}">
        <div>
          <div class="who">${escapeHtml(d.alias)}</div>
          <div class="meta">${escapeHtml(sesjeLabel(n))} ${suffix}</div>
        </div>
        <span class="badge ${online ? "online" : "offline"}">${online ? "ONLINE" : "OFFLINE"}</span>
      </article>`;
    }).join("");
  }

  function pluginTimesFromSnap(snap) {
    const out = {};
    const timing = (snap && snap.timing) || {};
    const bp = timing.by_plugin || {};
    for (const [name, slot] of Object.entries(bp)) {
      const sec = Number((slot && slot.duration_s) || 0) || 0;
      if (sec > 0) out[name] = (out[name] || 0) + sec;
    }
    // Stare / niepełne snapshoty: suma z tasks[] gdy brak by_plugin.
    if (!Object.keys(out).length) {
      for (const t of timing.tasks || []) {
        const name = t && t.plugin;
        const sec = Number(t && t.duration_s) || 0;
        if (name && sec > 0) out[name] = (out[name] || 0) + sec;
      }
    }
    return out;
  }

  function addTimes(dst, src) {
    for (const [k, v] of Object.entries(src || {})) {
      dst[k] = (dst[k] || 0) + (Number(v) || 0);
    }
  }

  async function loadSnapshots(acc, ids) {
    const uniq = [...new Set((ids || []).filter(Boolean))].slice(0, SNAP_LIMIT);
    const snaps = await Promise.all(uniq.map((sid) =>
      api(`/api/stats/${encodeURIComponent(acc)}/sessions/${encodeURIComponent(sid)}`)
        .catch(() => null)));
    return snaps.filter(Boolean);
  }

  async function loadLastSession(acc) {
    const live = await api(`/api/stats/${encodeURIComponent(acc)}`);
    let snap = live.live;
    let accountStats = live.account_stats;
    if (!snap && live.sessions && live.sessions[0]) {
      const one = await api(
        `/api/stats/${encodeURIComponent(acc)}/sessions/`
        + encodeURIComponent(live.sessions[0].session_id),
      ).catch(() => null);
      snap = one && one.snapshot;
      accountStats = (one && one.account_stats) || accountStats;
    }
    return { live, snap, accountStats };
  }

  async function loadAggregate(acc) {
    let url = `/api/stats/${encodeURIComponent(acc)}/aggregate?`;
    if (mode === "24h") url += "range=24h";
    else {
      ensureDefaultDates();
      const from = $("st-from").value;
      const to = $("st-to").value;
      if (!from || !to) throw new Error("Podaj zakres dat Od / Do.");
      url += `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
    }
    return api(url);
  }

  async function loadUsedAndCaps(acc) {
    const [bot, cfg, est] = await Promise.all([
      api("/api/bot-config").catch(() => ({})),
      api(`/api/config/${encodeURIComponent(acc)}`).catch(() => ({})),
      api("/api/estimate-actions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: acc }),
      }).catch(() => ({})),
    ]);
    // Effective po merge bot.json — klucz na koncie (w tym 0) wygrywa.
    const limits = deepMerge((bot && bot.limits) || {}, (cfg && cfg.limits) || {});
    return { used: est.used_today || {}, limits };
  }

  async function loadFleetCounts(accList, bounds) {
    const lists = await Promise.all((accList || []).map((a) =>
      api(`/api/stats/${encodeURIComponent(a.alias)}/sessions?limit=40`)
        .catch(() => ({ sessions: [] }))));
    const counts = {};
    (accList || []).forEach((a, i) => {
      const alias = a.device_alias;
      if (!alias) return;
      const rows = ((lists[i] && lists[i].sessions) || []).filter((s) => {
        if (!bounds) return true;
        return sessionInWindow(s, bounds.start, bounds.end);
      });
      counts[alias] = (counts[alias] || 0) + rows.length;
    });
    return counts;
  }

  function syncIframe(acc) {
    const details = $("st-classic");
    const frame = $("st-frame");
    if (!frame || !acc) return;
    const want = `/stats?account=${encodeURIComponent(acc)}`;
    const open = details && details.open;
    if (!open) return;
    if (lastFrameSrc !== want) {
      frame.src = want;
      lastFrameSrc = want;
    }
  }

  async function refreshStats() {
    const sel = $("st-account");
    const kpi = $("st-kpi");
    if (!sel || !kpi || fetching) return;
    fetching = true;
    try {
      const [accs, devices] = await Promise.all([
        api("/api/accounts"),
        api("/api/devices").catch(() => []),
      ]);
      const names = (Array.isArray(accs) ? accs : []).map((a) =>
        typeof a === "string" ? a : a.alias).filter(Boolean);
      fillSelect(sel, names, sel.value);
      const acc = sel.value;
      setCanvasCtx(acc);
      const more = $("st-more");
      if (more) more.href = acc ? `/stats?account=${encodeURIComponent(acc)}` : "/stats";
      if (!acc) {
        kpi.innerHTML = `<div class="empty">Brak kont.</div>`;
        showBanner("");
        return;
      }
      syncIframe(acc);

      let counters = {};
      let ai = {};
      let accountStats = {};
      let sessionsCount = 0;
      let pluginTimes = {};
      let sessionRows = [];
      let liveSid = null;
      let running = false;
      let fleetBounds = windowBounds();

      if (mode === "last") {
        const pack = await loadLastSession(acc);
        const snap = pack.snap || {};
        counters = snap.counters || {};
        ai = snap.ai || {};
        accountStats = pack.accountStats || {};
        sessionsCount = snap.session_id ? 1 : 0;
        pluginTimes = pluginTimesFromSnap(snap);
        sessionRows = (pack.live && pack.live.sessions) || [];
        liveSid = (pack.live && pack.live.live && pack.live.live.session_id)
          || snap.session_id;
        running = !!(pack.live && pack.live.session_running);
        // Flota przy ostatniej sesji: sesje z dzisiejszego dnia kalendarza.
        const day0 = new Date();
        day0.setHours(0, 0, 0, 0);
        fleetBounds = { start: day0, end: new Date() };
      } else {
        const agg = await loadAggregate(acc);
        counters = agg.counters || {};
        ai = agg.ai || {};
        accountStats = agg.account_stats || {};
        sessionsCount = agg.sessions_count || 0;
        const listed = await api(
          `/api/stats/${encodeURIComponent(acc)}/sessions?limit=50`,
        ).catch(() => ({ sessions: [] }));
        const bounds = windowBounds();
        sessionRows = ((listed && listed.sessions) || []).filter((s) =>
          bounds ? sessionInWindow(s, bounds.start, bounds.end) : true);
        const ids = (agg.sessions || []).map((s) => s.session_id)
          .filter(Boolean);
        const snaps = await loadSnapshots(acc, ids);
        snaps.forEach((one) => addTimes(pluginTimes, pluginTimesFromSnap(one.snapshot)));
        running = false;
      }

      const [caps, fleetCounts] = await Promise.all([
        loadUsedAndCaps(acc),
        loadFleetCounts(
          (Array.isArray(accs) ? accs : []).filter((a) => a && a.alias),
          fleetBounds,
        ),
      ]);

      renderKpi(kpi, counters, {
        account_stats: accountStats,
        sessions_count: sessionsCount,
        ai,
      });
      renderPluginBars($("st-plugins"), pluginTimes);
      renderLimits($("st-limits"), caps.used, caps.limits);
      renderSessions($("st-sessions"), sessionRows, liveSid, running);
      renderFleet($("st-fleet"), Array.isArray(devices) ? devices : [], fleetCounts);
      showBanner("");
    } catch (e) {
      showBanner("Nie udało się wczytać statystyk: " + (e.message || e), "err");
    } finally {
      fetching = false;
    }
  }

  function syncPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (!statsViewActive()) return;
    syncModeUi();
    refreshStats().catch(() => {});
    // Auto-refresh ~4 s tylko przy ostatniej sesji (plan krok 10).
    if (mode !== "last") return;
    pollTimer = setInterval(() => {
      if (document.hidden || !statsViewActive() || mode !== "last") return;
      refreshStats().catch(() => {});
    }, POLL_MS);
  }

  const sel = $("st-account");
  if (sel) {
    sel.addEventListener("change", () => {
      lastFrameSrc = "";
      refreshStats().catch(() => {});
    });
  }
  document.querySelectorAll("#st-modes button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.dataset.mode;
      if (!next || next === mode) return;
      mode = next;
      syncPoll();
    });
  });
  ["st-from", "st-to"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("change", () => {
      if (mode === "range") refreshStats().catch(() => {});
    });
  });
  const classic = $("st-classic");
  if (classic) {
    classic.addEventListener("toggle", () => {
      const acc = $("st-account") && $("st-account").value;
      if (classic.open && acc) syncIframe(acc);
    });
  }
  window.addEventListener("hashchange", syncPoll);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && statsViewActive()) refreshStats().catch(() => {});
  });
  syncPoll();
})();
