/* Konta (#konta) — karty, 2FA, override limitów z bot.json.
   Osobny plik: app.js jest już duży (Pulpit / Live / Harmonogram / Flota). */
"use strict";

const KONTA_DAILY = [
  { key: "daily_likes", label: "Lajki", kind: "likes" },
  { key: "daily_follows", label: "Follow", kind: "follows" },
  { key: "daily_comments", label: "Komentarze", kind: "comments" },
  { key: "daily_dms", label: "DM", kind: "dms", disabled: true },
];
const KONTA_HOURLY = [
  { key: "likes", label: "Lajki / h" },
  { key: "follows", label: "Follow / h" },
  { key: "comments", label: "Komentarze / h" },
];
const KONTA_HUM_TOP = [
  { key: "profile_name", label: "Profil", type: "select" },
  { key: "speed", label: "Prędkość", type: "num", min: 0.1, max: 20, step: 0.1 },
  { key: "after_each_step", label: "Pauza po kroku", type: "bool" },
];
const KONTA_HUM_PAUSES = [
  { key: "between_profiles", label: "Między profilami" },
  { key: "between_steps", label: "Między krokami" },
  { key: "before_next_reel", label: "Przed rolką" },
];
// Te same defaulty co schema.DEFAULT_BOT — gdy GET /api/bot-config padnie.
const BOT_FALLBACK = {
  limits: {
    daily_likes: 100, daily_follows: 30, daily_comments: 15, daily_dms: 0,
    per_hour_caps: { likes: 20, follows: 8, comments: 4 },
    cooldown_after_warning_hours: 24,
  },
  humanizer: { profile_name: "default", speed: 1.0, after_each_step: false, overrides: {} },
  toggles: { steps: {} },
};

let kontaFilter = "";
let kontaSelected = "";
let kontaDirty = false;
let kontaGen = 0;
let botConfig = BOT_FALLBACK;
let kontaRows = {};
let humProfiles = ["default"];

function hasOwnKey(obj, key) {
  return !!(obj && Object.prototype.hasOwnProperty.call(obj, key));
}

function kontaCtxLine() {
  const nAcc = (accounts || []).length;
  const nDev = new Set((accounts || []).map((a) => a.device_alias).filter(Boolean)).size;
  const accWord = nAcc === 1 ? "konto" : "konta";
  const telWord = nDev === 1 ? "telefon" : "telefony";
  return `${nAcc} ${accWord} · ${nDev} ${telWord}`;
}

function nextBlockForAccount(cycle, acc) {
  if (!cycle || !acc) return null;
  const day = (cycle.days || []).find((d) => d.index === cycle.today_index);
  if (!day) return null;
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const runtime = cycle.runtime || {};
  const blocks = (day.blocks || []).filter((b) => b.account === acc)
    .slice().sort((a, b) => toMin(a.start) - toMin(b.start));
  for (const b of blocks) {
    if ((runtime[b.id] || {}).status === "running") return Object.assign({}, b, { status: "running" });
  }
  for (const b of blocks) {
    const st = (runtime[b.id] || {}).status || "planned";
    if (st === "done" || st === "failed" || st === "missed") continue;
    const end = toMin(b.start) + (b.duration_s || 0) / 60;
    if (end >= nowMin) return Object.assign({}, b, { status: st });
  }
  return null;
}

function sessionForAccount(acc) {
  const list = (typeof sessionsList === "function") ? sessionsList() : ((status && status.sessions) || []);
  return list.find((s) => s && s.account === acc && (s.running || s.stopping)) || null;
}

function deviceByAlias(alias) {
  return (devices || []).find((d) => d.alias === alias) || null;
}

function cloneNote(dev, pkg) {
  if (!dev || !pkg) return pkg || "—";
  const c = (dev.clones || []).find((x) => x.package === pkg);
  if (!c) return pkg;
  const note = (c.note || "").trim();
  return note ? `${note} / slot ${c.slot || "?"}` : `slot ${c.slot || "?"} · ${pkg}`;
}

function parseTags(raw) {
  return String(raw || "").split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean);
}

function formatAgo(iso) {
  if (!iso) return "brak";
  const t = Date.parse(String(iso).slice(0, 19).replace(" ", "T"));
  if (!Number.isFinite(t)) return String(iso);
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec} s temu`;
  if (sec < 3600) return `${Math.round(sec / 60)} min temu`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h temu`;
  return `${Math.round(sec / 86400)} d temu`;
}

function formatCap(used, cap) {
  const u = Number(used) || 0;
  const c = Number(cap);
  if (!c) return `${u} / brak`;
  return `${u} / ${c}`;
}

function barPct(used, cap) {
  const c = Number(cap);
  if (!c) return 0;
  return Math.max(0, Math.min(100, Math.round((100 * (Number(used) || 0)) / c)));
}

function barKind(used, cap) {
  const c = Number(cap);
  if (!c) return "";
  const p = barPct(used, cap);
  if (p >= 100) return "over";
  if (p >= 80) return "warn";
  return "";
}

function effLimits(raw) {
  return deepMerge((botConfig && botConfig.limits) || BOT_FALLBACK.limits, (raw && raw.limits) || {});
}

function effHumanizer(raw) {
  return deepMerge((botConfig && botConfig.humanizer) || BOT_FALLBACK.humanizer, (raw && raw.humanizer) || {});
}

function setKontaMsg(text, kind) {
  showBanner("konta-msg", text, kind);
}

function markKontaDirty() {
  kontaDirty = true;
}

function filteredKontaAliases() {
  return (accounts || [])
    .filter((a) => !kontaFilter || a.device_alias === kontaFilter)
    .map((a) => a.alias);
}

function deviceFilterAliases() {
  const fromAcc = (accounts || []).map((a) => a.device_alias).filter(Boolean);
  const fromDev = (devices || []).map((d) => d.alias);
  return [...new Set([...fromAcc, ...fromDev])].sort();
}

async function fetchUsedToday(acc) {
  try {
    const est = await api("/api/estimate-actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: acc, scenario: DEFAULT_QUEUE }),
    });
    return est.used_today || { likes: 0, follows: 0, comments: 0 };
  } catch (_) {
    return { likes: 0, follows: 0, comments: 0 };
  }
}

async function fetchLastActive(acc) {
  try {
    const st = await api(`/api/stats/${encodeURIComponent(acc)}`);
    if (st.live && (st.live.updated_at || st.live.started_at)) {
      return st.live.updated_at || st.live.started_at;
    }
    const s = (st.sessions || [])[0];
    return (s && (s.ended_at || s.updated_at || s.started_at)) || null;
  } catch (_) {
    return null;
  }
}

async function loadKontaRow(alias) {
  const [cfg, cred, twofa, used, lastAt, plugins, profile] = await Promise.all([
    api(`/api/config/${encodeURIComponent(alias)}`),
    api(`/api/accounts/${encodeURIComponent(alias)}/credentials`).catch(() => ({ configured: false })),
    api(`/api/verification-code/${encodeURIComponent(alias)}`).catch(() => ({ waiting: false })),
    fetchUsedToday(alias),
    fetchLastActive(alias),
    api(`/api/plugins/${encodeURIComponent(alias)}`).catch(() => ({})),
    api(`/api/accounts/${encodeURIComponent(alias)}/profile`).catch(() => ({ bio: "", has_avatar: false })),
  ]);
  return { cfg, cred, twofa, used, lastAt, plugins, profile };
}

function renderKontaFilters() {
  const box = $("konta-filters");
  if (!box) return;
  const aliases = deviceFilterAliases();
  const chips = [`<button type="button" class="konta-filter${kontaFilter ? "" : " on"}" data-dev="">Wszystkie</button>`]
    .concat(aliases.map((a) =>
      `<button type="button" class="konta-filter${kontaFilter === a ? " on" : ""}" data-dev="${escapeHtml(a)}">${escapeHtml(a)}</button>`));
  box.innerHTML = chips.join("");
  box.querySelectorAll(".konta-filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      kontaFilter = btn.dataset.dev || "";
      renderKontaFilters();
      renderKontaGrid();
    });
  });
}

function renderKontaGrid() {
  const grid = $("konta-grid");
  if (!grid) return;
  const aliases = filteredKontaAliases();
  if (!aliases.length) {
    grid.innerHTML = `<div class="empty">Brak kont${kontaFilter ? " na tym telefonie" : ""}.</div>`;
    return;
  }
  grid.innerHTML = aliases.map((alias) => {
    const row = kontaRows[alias] || {};
    const cfg = row.cfg || {};
    const cred = row.cred || {};
    const twofa = row.twofa || {};
    const used = row.used || {};
    const lim = effLimits(cfg);
    const niche = cfg.niche || {};
    const keys = [...(niche.keywords || [])].slice(0, 6);
    const devAlias = (cfg.device || {}).device_alias || "";
    const pkg = (cfg.device || {}).app_clone_package || "";
    const dev = deviceByAlias(devAlias);
    const online = !dev || isOnline(dev);
    const logged = !!cred.configured;
    const paused = cfg.schedule && cfg.schedule.enabled === false;
    const nxt = nextBlockForAccount(timelines[devAlias], alias);
    const live = sessionForAccount(alias);
    const wait = !!twofa.waiting;
    let dot = "ok";
    if (!online) dot = "warn";
    else if (!logged) dot = "";
    const loginTxt = live ? "jedzie" : (logged ? "zalogowane" : "brak danych logowania");
    const chips = keys.length
      ? keys.map((k) => `<span class="acc-chip">${escapeHtml(k)}</span>`).join("")
      : `<span class="acc-chip dim">brak niszy</span>`;
    let twofaHtml = "";
    if (wait) {
      const why = online ? "Czekam na kod 2FA" : "telefon offline — czekam na kod 2FA";
      twofaHtml = `<div class="acc-2fa" data-stop="1">
        <div class="acc-2fa-banner">${why}${twofa.prompt ? " · " + escapeHtml(twofa.prompt) : ""}</div>
        <div class="acc-2fa-row">
          <input class="acc-2fa-in" type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="Kod 2FA" />
          <button type="button" class="primary acc-2fa-ok">Zatwierdź</button>
        </div>
      </div>`;
    } else if (!online) {
      twofaHtml = `<div class="acc-2fa-banner">ADB offline${dev && dev.adb_id ? " · " + escapeHtml(dev.adb_id) : ""}</div>`;
    }
    return `<article class="acc-card${kontaSelected === alias ? " sel" : ""}" data-alias="${escapeHtml(alias)}">
      <div class="acc-card-head">
        <span class="acc-dot ${dot}"></span>
        <div>
          <h2>${escapeHtml(alias)}</h2>
          <div class="acc-login">${escapeHtml(loginTxt)}${cred.username_masked ? " · " + escapeHtml(cred.username_masked) : ""}</div>
        </div>
        <div class="acc-dev">
          <strong>${escapeHtml(devAlias || "—")}</strong>
          ${escapeHtml(pkg || "brak pakietu")}
        </div>
      </div>
      <div class="acc-chips">${chips}</div>
      <div class="acc-limits">
        <div class="acc-lim"><div class="k">Lajki dziś</div><div class="v">${escapeHtml(formatCap(used.likes, lim.daily_likes))}</div></div>
        <div class="acc-lim"><div class="k">Follow dziś</div><div class="v">${escapeHtml(formatCap(used.follows, lim.daily_follows))}</div></div>
      </div>
      <div class="acc-next">${nxt
        ? `${escapeHtml(nxt.start)} · ${escapeHtml(nxt.scenario || DEFAULT_QUEUE)}`
        : `<span class="dim">brak bloku dziś</span>`}</div>
      ${paused ? `<div class="acc-paused">wyłączone w osi</div>` : ""}
      ${twofaHtml}
      <div class="acc-actions" data-stop="1">
        <button type="button" class="acc-edit">Edytuj</button>
        <button type="button" class="acc-login-btn">Login</button>
        <button type="button" class="disc acc-disc">Discovery niszy</button>
      </div>
    </article>`;
  }).join("");
  grid.querySelectorAll(".acc-card").forEach((card) => {
    const alias = card.dataset.alias;
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-stop]")) return;
      selectKonta(alias);
    });
    const edit = card.querySelector(".acc-edit");
    if (edit) edit.addEventListener("click", (ev) => { ev.stopPropagation(); selectKonta(alias); });
    const login = card.querySelector(".acc-login-btn");
    if (login) login.addEventListener("click", (ev) => { ev.stopPropagation(); kontaLogin(alias); });
    const disc = card.querySelector(".acc-disc");
    if (disc) disc.addEventListener("click", (ev) => { ev.stopPropagation(); kontaDiscover(alias); });
    const ok = card.querySelector(".acc-2fa-ok");
    if (ok) {
      ok.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const inp = card.querySelector(".acc-2fa-in");
        kontaSubmit2FA(alias, inp ? inp.value : "");
      });
    }
  });
}

function ownCheck(id, checked, hint) {
  return `<label class="insp-own">
    <input type="checkbox" data-own="${escapeHtml(id)}" ${checked ? "checked" : ""} />
    własne
    ${hint ? `<span class="insp-hint">${escapeHtml(hint)}</span>` : ""}
  </label>`;
}

function limitFieldHtml(key, label, rawVal, effVal, used, disabled) {
  const own = rawVal !== undefined;
  const cap = own ? rawVal : effVal;
  const hint = `jak globalne: ${effVal == null ? "—" : effVal}`;
  const pct = barPct(used, cap);
  const kind = barKind(used, cap);
  return `<div class="insp-field">
    <span>${escapeHtml(label)} · ${escapeHtml(formatCap(used, cap))}</span>
    ${ownCheck(key, own, hint)}
    <input type="number" min="0" data-lim="${escapeHtml(key)}" value="${escapeHtml(cap == null ? "" : cap)}" ${own && !disabled ? "" : "disabled"} ${disabled ? "title=\"w schemacie, nie w runtime\"" : ""} />
    <div class="insp-bar ${kind}"><i style="width:${pct}%"></i></div>
  </div>`;
}

function renderKontaInsp() {
  const box = $("konta-insp");
  if (!box) return;
  const alias = kontaSelected;
  const row = alias && kontaRows[alias];
  if (!row || !row.cfg) {
    box.innerHTML = `<p class="placeholder">Wybierz kartę — tu limity, nisza i urządzenie.</p>`;
    return;
  }
  const cfg = row.cfg;
  const cred = row.cred || {};
  const prof = row.profile || {};
  const used = row.used || {};
  const rawLim = cfg.limits || {};
  const rawHour = rawLim.per_hour_caps || {};
  const lim = effLimits(cfg);
  const hour = lim.per_hour_caps || {};
  const botLim = (botConfig && botConfig.limits) || BOT_FALLBACK.limits;
  const botHour = botLim.per_hour_caps || {};
  const rawHum = cfg.humanizer || {};
  const hum = effHumanizer(cfg);
  const botHum = (botConfig && botConfig.humanizer) || BOT_FALLBACK.humanizer;
  const niche = cfg.niche || {};
  const nd = ((row.plugins || {}).niche_discover || {}).write || {};
  const dev = cfg.device || {};
  const hardware = deviceByAlias(dev.device_alias) || {};
  const paused = cfg.schedule && cfg.schedule.enabled === false;
  const live = sessionForAccount(alias);
  const logged = !!cred.configured;
  const tv = niche.tag_variants || {};
  const tvRows = Object.keys(tv).map((expr) => {
    const tags = Array.isArray(tv[expr]) ? tv[expr].join(", ") : String(tv[expr] || "");
    return `<div class="tv-row">
      <input data-tv-expr type="text" value="${escapeHtml(expr)}" placeholder="wyrażenie" />
      <input data-tv-tags type="text" value="${escapeHtml(tags)}" placeholder="#tag, #inny" />
      <button type="button" data-tv-del>✕</button>
    </div>`;
  }).join("");
  const dailyHtml = KONTA_DAILY.map((f) => limitFieldHtml(
    f.key, f.label,
    hasOwnKey(rawLim, f.key) ? rawLim[f.key] : undefined,
    lim[f.key],
    used[f.kind] || 0,
    !!f.disabled,
  )).join("");
  const hourHtml = KONTA_HOURLY.map((f) => {
    const own = hasOwnKey(rawHour, f.key);
    const cap = own ? rawHour[f.key] : hour[f.key];
    return `<div class="insp-field">
      <span>${escapeHtml(f.label)}</span>
      ${ownCheck("hour." + f.key, own, `jak globalne: ${botHour[f.key] == null ? "—" : botHour[f.key]}`)}
      <input type="number" min="0" data-hour="${escapeHtml(f.key)}" value="${escapeHtml(cap == null ? "" : cap)}" ${own ? "" : "disabled"} />
    </div>`;
  }).join("");
  const humTop = KONTA_HUM_TOP.map((f) => {
    const own = hasOwnKey(rawHum, f.key);
    const val = own ? rawHum[f.key] : hum[f.key];
    const hint = `jak globalne: ${botHum[f.key] == null ? "—" : botHum[f.key]}`;
    let input = "";
    if (f.type === "select") {
      const opts = (humProfiles.length ? humProfiles : ["default"]).map((n) =>
        `<option value="${escapeHtml(n)}" ${String(val) === n ? "selected" : ""}>${escapeHtml(n)}</option>`).join("");
      input = `<select data-hum="${escapeHtml(f.key)}" ${own ? "" : "disabled"}>${opts}</select>`;
    } else if (f.type === "bool") {
      input = `<input type="checkbox" data-hum="${escapeHtml(f.key)}" ${val ? "checked" : ""} ${own ? "" : "disabled"} />`;
    } else {
      input = `<input type="number" data-hum="${escapeHtml(f.key)}" min="${f.min}" max="${f.max}" step="${f.step}" value="${escapeHtml(val == null ? "" : val)}" ${own ? "" : "disabled"} />`;
    }
    return `<div class="insp-field">
      <span>${escapeHtml(f.label)}</span>
      ${ownCheck("hum." + f.key, own, hint)}
      ${input}
    </div>`;
  }).join("");
  const rawPauses = ((rawHum.overrides || {}).per_action) || {};
  const effPauses = ((hum.overrides || {}).per_action) || {};
  const pauseHtml = KONTA_HUM_PAUSES.map((f) => {
    const own = hasOwnKey(rawPauses, f.key);
    const rng = own ? rawPauses[f.key] : (effPauses[f.key] || {});
    return `<div class="insp-field">
      <span>${escapeHtml(f.label)} (s)</span>
      ${ownCheck("pause." + f.key, own, "puste = z profilu / global")}
      <div class="tv-row">
        <input type="number" step="0.1" data-pmin="${escapeHtml(f.key)}" placeholder="min" value="${escapeHtml(rng.min == null ? "" : rng.min)}" ${own ? "" : "disabled"} />
        <input type="number" step="0.1" data-pmax="${escapeHtml(f.key)}" placeholder="max" value="${escapeHtml(rng.max == null ? "" : rng.max)}" ${own ? "" : "disabled"} />
      </div>
    </div>`;
  }).join("");
  const ai = cfg.ai || {};
  const thr = ai.thresholds || {};
  const models = ai.model_per_mode || {};
  const spoof = `
    <label class="insp-field">spoofed_mac<input data-spoof="spoofed_mac" value="${escapeHtml(dev.spoofed_mac || "")}" /></label>
    <label class="insp-field">spoofed_imei<input data-spoof="spoofed_imei" value="${escapeHtml(dev.spoofed_imei || "")}" /></label>
    <label class="insp-field">spoofed_android_id<input data-spoof="spoofed_android_id" value="${escapeHtml(dev.spoofed_android_id || "")}" /></label>
    <label class="insp-field">spoofed_gsf<input data-spoof="spoofed_gsf" value="${escapeHtml(dev.spoofed_gsf || "")}" /></label>`;
  const devOpts = (devices || []).map((d) =>
    `<option value="${escapeHtml(d.alias)}" ${d.alias === dev.device_alias ? "selected" : ""}>${escapeHtml(d.alias)}</option>`).join("");
  const pkgList = ((hardware.clones || []).map((c) =>
    `<option value="${escapeHtml(c.package)}">`)).join("");
  box.innerHTML = `
    <div class="insp-head">
      <h2>${escapeHtml(alias)}</h2>
      <div class="insp-meta">
        <span class="badge ${logged ? "online" : "offline"}">${logged ? "zalogowane" : "brak logowania"}</span>
        ${live ? " · jedzie" : ""}
        · ostatnia aktywność: ${escapeHtml(formatAgo(row.lastAt))}
      </div>
    </div>
    <section class="insp-sec">
      <h3>Sesja</h3>
      <div class="insp-row"><span class="k">Alias</span><span class="v">${escapeHtml(alias)}</span></div>
      <div class="insp-row"><span class="k">IG login</span><span class="v">${escapeHtml(cred.username_masked || "—")}</span></div>
      <div class="insp-row"><span class="k">Device</span><span class="v">${escapeHtml(dev.device_alias || "—")}</span></div>
      <div class="insp-row"><span class="k">Package</span><span class="v">${escapeHtml(dev.app_clone_package || "—")}</span></div>
      <div class="kn-auth-tabs ki-auth-tabs" role="tablist" aria-label="Po klonie">
        <button type="button" class="kn-tab on" id="ki-tab-login" role="tab" aria-selected="true" aria-controls="ki-pane-login" data-ki-auth="login">Zaloguj</button>
        <button type="button" class="kn-tab" id="ki-tab-signup" role="tab" aria-selected="false" aria-controls="ki-pane-signup" data-ki-auth="signup">Załóż nowe konto</button>
      </div>
      <div id="ki-pane-login" class="kn-auth-pane" role="tabpanel" aria-labelledby="ki-tab-login">
      <label class="insp-field">Nowy login IG
        <input id="ki-ig" type="text" autocomplete="username" placeholder="${escapeHtml(cred.username_masked || "login")}" />
      </label>
      <label class="insp-field">Hasło (credentials, nie logi)
        <input id="ki-pass" type="password" autocomplete="new-password" placeholder="zostaw puste = bez zmiany" />
      </label>
      <div class="insp-actions">
        <button type="button" id="ki-cred-save">Zapisz dane logowania</button>
        <button type="button" class="primary" id="ki-login">Login</button>
      </div>
      </div>
      <div id="ki-pane-signup" class="kn-auth-pane" role="tabpanel" aria-labelledby="ki-tab-signup" hidden>
        <p class="insp-hint">Bot otworzy tego klona i przejdzie rejestrację IG. Kod SMS/email wpiszesz tutaj; captcha zostaje na telefonie.</p>
        <label class="insp-field">Imię i nazwisko
          <input id="ki-su-name" type="text" autocomplete="name" value="${escapeHtml((cred.signup && cred.signup.full_name) || "")}" />
        </label>
        <label class="insp-field">Username IG
          <input id="ki-su-user" type="text" autocomplete="username" placeholder="${escapeHtml(cred.username_masked || "login IG")}" />
        </label>
        <label class="insp-field">Hasło nowego konta
          <input id="ki-su-pass" type="password" autocomplete="new-password" placeholder="hasło do założenia" />
        </label>
        <label class="insp-field">Data urodzenia
          <input id="ki-su-bday" type="date" value="${escapeHtml((cred.signup && cred.signup.birthday) || "")}" />
        </label>
        <label class="insp-field">E-mail albo telefon
          <input id="ki-su-contact" type="text" autocomplete="email" placeholder="${escapeHtml((cred.signup && cred.signup.contact_masked) || "email / numer")}" />
        </label>
        <div class="insp-actions">
          <button type="button" id="ki-su-save">Zapisz profil konta</button>
          <button type="button" class="primary" id="ki-signup">Załóż nowe konto</button>
        </div>
      </div>
    </section>
    <section class="insp-sec">
      <h3>Profil IG</h3>
      <p class="insp-hint">Biogram (do 150 znaków) i zdjęcie. Zapis zostaje na PC; „Wstaw na IG” odpala bota na tym klonie.</p>
      <label class="insp-field">Biogram
        <textarea id="ki-bio" maxlength="150" rows="3" placeholder="kim jesteś, czym się zajmujesz">${escapeHtml(prof.bio || "")}</textarea>
      </label>
      <div class="ki-avatar-row">
        ${prof.has_avatar
          ? `<img class="ki-avatar-preview" alt="Zdjęcie profilowe" src="/api/accounts/${encodeURIComponent(alias)}/avatar/file?v=${encodeURIComponent(prof.filename || "1")}" />`
          : `<div class="ki-avatar-preview ki-avatar-empty" aria-hidden="true"></div>`}
        <label class="insp-field ki-avatar-field">Zdjęcie profilowe
          <input id="ki-avatar" type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" />
          <span id="ki-avatar-hint" class="insp-hint">${prof.has_avatar
            ? `Jest „${escapeHtml(prof.filename || "avatar")}” — nowy plik nadpisze.`
            : "Brak pliku — wybierz jpg/png/webp."}</span>
        </label>
      </div>
      <div class="insp-actions">
        <button type="button" id="ki-prof-save">Zapisz profil</button>
        <button type="button" class="primary" id="ki-prof-apply">Wstaw na IG</button>
      </div>
    </section>
    <section class="insp-sec">
      <h3>Limity</h3>
      <p class="insp-hint">Odznaczone „własne” kasuje klucz z JSON konta — runtime bierze wartość z bot.json.</p>
      ${dailyHtml}
      <label class="insp-field">Cooldown po ostrzeżeniu (h)
        ${ownCheck("daily.cooldown_after_warning_hours", hasOwnKey(rawLim, "cooldown_after_warning_hours"),
          `jak globalne: ${botLim.cooldown_after_warning_hours == null ? "—" : botLim.cooldown_after_warning_hours}`)}
        <input type="number" min="0" data-lim="cooldown_after_warning_hours"
          value="${escapeHtml((hasOwnKey(rawLim, "cooldown_after_warning_hours") ? rawLim.cooldown_after_warning_hours : lim.cooldown_after_warning_hours) ?? "")}"
          ${hasOwnKey(rawLim, "cooldown_after_warning_hours") ? "" : "disabled"} />
      </label>
      <h3>Godzinowe</h3>
      ${hourHtml}
      <button type="button" class="primary" id="ki-lim-save">Zapisz limity</button>
    </section>
    <section class="insp-sec">
      <h3>Nisza</h3>
      <div class="insp-chips">${(niche.keywords || []).slice(0, 12).map((k) =>
        `<span class="acc-chip">${escapeHtml(k)}</span>`).join("") || `<span class="acc-chip dim">pusto</span>`}</div>
      <label class="insp-field">description<textarea id="ki-desc">${escapeHtml(niche.description || "")}</textarea></label>
      <label class="insp-field">audience<textarea id="ki-aud">${escapeHtml(niche.audience || "")}</textarea></label>
      <label class="insp-field">keywords (przecinek)<input id="ki-kw" type="text" value="${escapeHtml((niche.keywords || []).join(", "))}" /></label>
      <label class="insp-field">hashtags (przecinek)<input id="ki-ht" type="text" value="${escapeHtml((niche.hashtags || []).join(", "))}" /></label>
      <label class="insp-field">exclusions<input id="ki-ex" type="text" value="${escapeHtml((niche.exclusions || []).join(", "))}" /></label>
      <label class="insp-own"><input type="checkbox" id="ki-acc" ${niche.accepted !== false ? "checked" : ""} /> accepted</label>
      <label class="insp-own"><input type="checkbox" id="ki-sync" ${nd.sync_content_search_phrases ? "checked" : ""} /> Sync fraz do content_search</label>
      <label class="insp-field">Nowe #/keywords z analizy
        <select id="ki-merge">
          <option value="append" ${nd.discovered_merge !== "replace" ? "selected" : ""}>Dopisz (append)</option>
          <option value="replace" ${nd.discovered_merge === "replace" ? "selected" : ""}>Nadpisz discovered (replace)</option>
        </select>
      </label>
      <div class="insp-field">
        <span>Znaczniki powiązane (tag_variants)</span>
        <div id="ki-tv">${tvRows || ""}</div>
        <button type="button" id="ki-tv-add">+ wyrażenie</button>
      </div>
      <div class="insp-hint">Ostatnie discovery: ${escapeHtml(niche.discovered_at || "—")}</div>
      <div class="insp-actions">
        <button type="button" class="primary" id="ki-niche-save">Zapisz niszę</button>
        <button type="button" class="disc" id="ki-disc">Uruchom discovery</button>
        <button type="button" id="ki-niche-clear">Wyczyść niszę</button>
      </div>
    </section>
    <section class="insp-sec">
      <h3>Urządzenie</h3>
      <label class="insp-field">device_alias
        <select id="ki-dev">${devOpts}</select>
      </label>
      <label class="insp-field">app_clone_package
        <input id="ki-pkg" type="text" list="ki-pkg-list" value="${escapeHtml(dev.app_clone_package || "")}" />
        <datalist id="ki-pkg-list">${pkgList}</datalist>
      </label>
      <div class="insp-row"><span class="k">Klon</span><span class="v">${escapeHtml(cloneNote(hardware, dev.app_clone_package))}</span></div>
      <div class="insp-row"><span class="k">Android</span><span class="v">${escapeHtml(hardware.android_version || "—")}</span></div>
      <div class="insp-row"><span class="k">Status</span><span class="v">${hardware.alias ? (isOnline(hardware) ? "online" : "offline") : "—"}</span></div>
      <label class="insp-own"><input type="checkbox" id="ki-pause" ${paused ? "checked" : ""} /> Pauza konta (wyłącz z osi)</label>
      <div class="insp-actions">
        <button type="button" class="primary" id="ki-dev-save">Zapisz urządzenie</button>
        <button type="button" id="ki-pkg-clear">Usuń APPID z konta</button>
      </div>
      <details>
        <summary>Spoof (opcjonalnie)</summary>
        ${spoof}
        <button type="button" id="ki-spoof-save">Zapisz spoof</button>
      </details>
    </section>
    <details class="insp-sec" ${hasOwnKey(rawHum, "speed") || hasOwnKey(rawHum, "profile_name") ? "open" : ""}>
      <summary>Humanizer — override</summary>
      <p class="insp-hint">Odznaczone „własne” usuwa klucz — dziedziczy z bot.json.</p>
      ${humTop}
      ${pauseHtml}
      <button type="button" class="primary" id="ki-hum-save">Zapisz humanizer</button>
    </details>
    <details>
      <summary>AI Vision (progi)</summary>
      <label class="insp-field">próg follow<input type="number" id="ki-ai-follow" min="0" max="100" value="${escapeHtml(thr.follow == null ? 50 : thr.follow)}" /></label>
      <label class="insp-field">próg like_only<input type="number" id="ki-ai-like" min="0" max="100" value="${escapeHtml(thr.like_only == null ? 35 : thr.like_only)}" /></label>
      <label class="insp-field">reel_niche
        <select id="ki-ai-reel">${["haiku", "sonnet", "opus"].map((m) =>
          `<option ${models.reel_niche === m ? "selected" : ""}>${m}</option>`).join("")}</select>
      </label>
      <button type="button" id="ki-ai-save">Zapisz AI</button>
    </details>
    <button type="button" class="danger insp-del" id="ki-del">Usuń konto</button>`;
  bindKontaInsp(alias);
}

function bindOwnToggles(root) {
  root.querySelectorAll("[data-own]").forEach((cb) => {
    cb.addEventListener("change", () => {
      markKontaDirty();
      const id = cb.dataset.own || "";
      const on = cb.checked;
      const map = [
        [/^daily_|^cooldown_/, `[data-lim="${CSS.escape(id)}"]`],
        [/^hour\./, `[data-hour="${CSS.escape(id.slice(5))}"]`],
        [/^hum\./, `[data-hum="${CSS.escape(id.slice(4))}"]`],
        [/^pause\./, `[data-pmin="${CSS.escape(id.slice(6))}"], [data-pmax="${CSS.escape(id.slice(6))}"]`],
        [/^daily\./, `[data-lim="${CSS.escape(id.slice(6))}"]`],
      ];
      for (const pair of map) {
        if (!pair[0].test(id)) continue;
        root.querySelectorAll(pair[1]).forEach((el) => { el.disabled = !on; });
        break;
      }
    });
  });
}

function bindKontaInsp(alias) {
  const box = $("konta-insp");
  if (!box) return;
  box.querySelectorAll("input, textarea, select").forEach((el) => {
    el.addEventListener("input", markKontaDirty);
    el.addEventListener("change", markKontaDirty);
  });
  bindOwnToggles(box);
  const addTv = $("ki-tv-add");
  if (addTv) {
    addTv.addEventListener("click", () => {
      const wrap = $("ki-tv");
      if (!wrap) return;
      const row = document.createElement("div");
      row.className = "tv-row";
      row.innerHTML = `<input data-tv-expr type="text" placeholder="wyrażenie" />
        <input data-tv-tags type="text" placeholder="#tag, #inny" />
        <button type="button" data-tv-del>✕</button>`;
      wrap.appendChild(row);
      markKontaDirty();
    });
  }
  box.querySelectorAll("[data-tv-del]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".tv-row");
      if (row) row.remove();
      markKontaDirty();
    });
  });
  const click = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  click("ki-lim-save", () => saveKontaLimits(alias));
  click("ki-niche-save", () => saveKontaNiche(alias));
  click("ki-niche-clear", () => clearKontaNiche(alias));
  click("ki-disc", () => kontaDiscover(alias));
  click("ki-dev-save", () => saveKontaDevice(alias));
  click("ki-pkg-clear", () => clearKontaPkg(alias));
  click("ki-spoof-save", () => saveKontaSpoof(alias));
  click("ki-hum-save", () => saveKontaHumanizer(alias));
  click("ki-ai-save", () => saveKontaAi(alias));
  click("ki-cred-save", () => saveKontaCred(alias));
  click("ki-login", () => kontaLogin(alias));
  click("ki-su-save", () => saveKontaSignup(alias, { start: false }));
  click("ki-signup", () => saveKontaSignup(alias, { start: true }));
  click("ki-prof-save", () => saveKontaProfile(alias, { apply: false }));
  click("ki-prof-apply", () => saveKontaProfile(alias, { apply: true }));
  click("ki-del", () => deleteKonta(alias));
  const av = $("ki-avatar");
  if (av) av.addEventListener("change", () => paintKiAvatarHint(alias));
  document.querySelectorAll("[data-ki-auth]").forEach((btn) => {
    btn.addEventListener("click", () => setKiAuthTab(btn.dataset.kiAuth));
  });
  setKiAuthTab("login");
  const pause = $("ki-pause");
  if (pause) pause.addEventListener("change", () => toggleKontaPause(alias, pause.checked));
}

async function postConfig(alias, body) {
  await api(`/api/config/${encodeURIComponent(alias)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function saveKontaLimits(alias) {
  const box = $("konta-insp");
  const row = kontaRows[alias];
  if (!box || !row) return;
  const next = deepClone(row.cfg);
  const lim = Object.assign({}, next.limits || {});
  const hour = Object.assign({}, lim.per_hour_caps || {});
  for (const f of KONTA_DAILY) {
    const cb = box.querySelector(`[data-own="${f.key}"]`);
    const inp = box.querySelector(`[data-lim="${f.key}"]`);
    if (cb && cb.checked && inp) lim[f.key] = Number(inp.value) || 0;
    else delete lim[f.key];
  }
  const cdCb = box.querySelector(`[data-own="daily.cooldown_after_warning_hours"]`);
  const cdInp = box.querySelector(`[data-lim="cooldown_after_warning_hours"]`);
  if (cdCb && cdCb.checked && cdInp) lim.cooldown_after_warning_hours = Number(cdInp.value) || 0;
  else delete lim.cooldown_after_warning_hours;
  for (const f of KONTA_HOURLY) {
    const cb = box.querySelector(`[data-own="hour.${f.key}"]`);
    const inp = box.querySelector(`[data-hour="${f.key}"]`);
    if (cb && cb.checked && inp) hour[f.key] = Number(inp.value) || 0;
    else delete hour[f.key];
  }
  if (Object.keys(hour).length) lim.per_hour_caps = hour;
  else delete lim.per_hour_caps;
  if (Object.keys(lim).length) next.limits = lim;
  else delete next.limits;
  try {
    await postConfig(alias, next);
    kontaDirty = false;
    setKontaMsg("Zapisano limity (override albo dziedziczenie).", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano limitów", "err");
  }
}

function collectTagVariants(box) {
  const out = {};
  box.querySelectorAll(".tv-row").forEach((row) => {
    const exprEl = row.querySelector("[data-tv-expr]");
    const tagsEl = row.querySelector("[data-tv-tags]");
    const expr = ((exprEl && exprEl.value) || "").trim();
    if (!expr) return;
    out[expr] = parseTags(tagsEl ? tagsEl.value : "");
  });
  return out;
}

async function saveKontaNiche(alias) {
  const box = $("konta-insp");
  const row = kontaRows[alias];
  if (!box || !row) return;
  const next = deepClone(row.cfg);
  const niche = Object.assign({}, next.niche || {});
  niche.description = ($("ki-desc") && $("ki-desc").value) || "";
  niche.audience = ($("ki-aud") && $("ki-aud").value) || "";
  niche.keywords = parseTags($("ki-kw") && $("ki-kw").value);
  niche.hashtags = parseTags($("ki-ht") && $("ki-ht").value);
  niche.exclusions = parseTags($("ki-ex") && $("ki-ex").value);
  niche.accepted = !!( $("ki-acc") && $("ki-acc").checked );
  niche.tag_variants = collectTagVariants(box);
  next.niche = niche;
  try {
    await postConfig(alias, next);
    const plugins = deepClone(row.plugins || {});
    plugins.niche_discover = plugins.niche_discover || {};
    plugins.niche_discover.write = Object.assign({}, plugins.niche_discover.write || {}, {
      sync_content_search_phrases: !!( $("ki-sync") && $("ki-sync").checked ),
      discovered_merge: ($("ki-merge") && $("ki-merge").value) || "append",
    });
    await api(`/api/plugins/${encodeURIComponent(alias)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ niche_discover: plugins.niche_discover }),
    });
    kontaDirty = false;
    setKontaMsg("Zapisano niszę.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano niszy", "err");
  }
}

async function clearKontaNiche(alias) {
  if (!window.confirm(`Wyczyścić niszę konta „${alias}”?`)) return;
  try {
    await api(`/api/niche/${encodeURIComponent(alias)}`, { method: "DELETE" });
    kontaDirty = false;
    setKontaMsg("Nisza wyczyszczona.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie wyczyszczono", "err");
  }
}

async function saveKontaDevice(alias) {
  const row = kontaRows[alias];
  if (!row) return;
  const next = deepClone(row.cfg);
  next.device = next.device || {};
  next.device.device_alias = ($("ki-dev") && $("ki-dev").value) || next.device.device_alias;
  next.device.app_clone_package = (($("ki-pkg") && $("ki-pkg").value) || "").trim();
  try {
    await postConfig(alias, next);
    if (next.device.app_clone_package) {
      await api(`/api/accounts/${encodeURIComponent(alias)}/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_clone_package: next.device.app_clone_package }),
      });
    }
    kontaDirty = false;
    setKontaMsg("Zapisano urządzenie.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano urządzenia", "err");
  }
}

async function clearKontaPkg(alias) {
  if (!window.confirm(`Usunąć APPID z konta „${alias}”?`)) return;
  try {
    await api(`/api/accounts/${encodeURIComponent(alias)}/clone`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app_clone_package: "" }),
    });
    kontaDirty = false;
    setKontaMsg("Wyczyszczono pakiet klona.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie wyczyszczono APPID", "err");
  }
}

async function saveKontaSpoof(alias) {
  const box = $("konta-insp");
  const row = kontaRows[alias];
  if (!box || !row) return;
  const next = deepClone(row.cfg);
  next.device = next.device || {};
  box.querySelectorAll("[data-spoof]").forEach((inp) => {
    const v = (inp.value || "").trim();
    next.device[inp.dataset.spoof] = v || null;
  });
  try {
    await postConfig(alias, next);
    kontaDirty = false;
    setKontaMsg("Zapisano spoof.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano spoof", "err");
  }
}

async function saveKontaHumanizer(alias) {
  const box = $("konta-insp");
  const row = kontaRows[alias];
  if (!box || !row) return;
  const next = deepClone(row.cfg);
  const hum = Object.assign({}, next.humanizer || {});
  const ov = Object.assign({}, hum.overrides || {});
  const per = Object.assign({}, ov.per_action || {});
  for (const f of KONTA_HUM_TOP) {
    const cb = box.querySelector(`[data-own="hum.${f.key}"]`);
    const inp = box.querySelector(`[data-hum="${f.key}"]`);
    if (cb && cb.checked && inp) {
      if (f.type === "bool") hum[f.key] = !!inp.checked;
      else if (f.type === "num") hum[f.key] = Number(inp.value);
      else hum[f.key] = inp.value;
    } else {
      delete hum[f.key];
    }
  }
  for (const f of KONTA_HUM_PAUSES) {
    const cb = box.querySelector(`[data-own="pause.${f.key}"]`);
    const a = box.querySelector(`[data-pmin="${f.key}"]`);
    const b = box.querySelector(`[data-pmax="${f.key}"]`);
    if (cb && cb.checked && a && b) {
      per[f.key] = { min: Number(a.value) || 0, max: Number(b.value) || 0 };
    } else {
      delete per[f.key];
    }
  }
  if (Object.keys(per).length) ov.per_action = per;
  else delete ov.per_action;
  if (Object.keys(ov).length) hum.overrides = ov;
  else delete hum.overrides;
  if (Object.keys(hum).length) next.humanizer = hum;
  else delete next.humanizer;
  try {
    await postConfig(alias, next);
    kontaDirty = false;
    setKontaMsg("Zapisano humanizer (override albo dziedziczenie).", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano humanizera", "err");
  }
}

async function saveKontaAi(alias) {
  const row = kontaRows[alias];
  if (!row) return;
  const next = deepClone(row.cfg);
  next.ai = next.ai || {};
  next.ai.thresholds = next.ai.thresholds || {};
  next.ai.model_per_mode = next.ai.model_per_mode || {};
  const fol = Number($("ki-ai-follow") && $("ki-ai-follow").value);
  const like = Number($("ki-ai-like") && $("ki-ai-like").value);
  if (Number.isFinite(fol)) next.ai.thresholds.follow = fol;
  if (Number.isFinite(like)) next.ai.thresholds.like_only = like;
  const reel = $("ki-ai-reel") && $("ki-ai-reel").value;
  if (reel) next.ai.model_per_mode.reel_niche = reel;
  try {
    await postConfig(alias, next);
    kontaDirty = false;
    setKontaMsg("Zapisano progi AI.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano AI", "err");
  }
}

function setKiAuthTab(tab) {
  // Komentarz: ustawienia gotowego klona — Zaloguj vs Załóż nowe konto
  const want = tab === "signup" ? "signup" : "login";
  ["login", "signup"].forEach((name) => {
    const btn = $(`ki-tab-${name}`);
    const pane = $(`ki-pane-${name}`);
    if (btn) {
      btn.classList.toggle("on", name === want);
      btn.setAttribute("aria-selected", name === want ? "true" : "false");
    }
    if (pane) pane.hidden = name !== want;
  });
}

function kiSignupPayload(alias) {
  const user = (($("ki-su-user") && $("ki-su-user").value) || "").trim()
    || (($("ki-ig") && $("ki-ig").value) || "").trim()
    || alias;
  return {
    ig_username: user,
    ig_password: (($("ki-su-pass") && $("ki-su-pass").value) || "").trim(),
    signup: {
      full_name: (($("ki-su-name") && $("ki-su-name").value) || "").trim(),
      birthday: (($("ki-su-bday") && $("ki-su-bday").value) || "").trim(),
      contact: (($("ki-su-contact") && $("ki-su-contact").value) || "").trim(),
    },
  };
}

async function saveKontaSignup(alias, opts) {
  const start = !!(opts && opts.start);
  const payload = kiSignupPayload(alias);
  const ready = !!(payload.ig_username && payload.ig_password
    && payload.signup.full_name && payload.signup.birthday && payload.signup.contact);
  try {
    if (ready) {
      await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if ($("ki-su-pass")) $("ki-su-pass").value = "";
      setKontaMsg("Zapisano profil zakładania konta.", "ok");
      await refreshKonta({ force: true, keep: alias });
    } else if (!(start && await knHasSavedSignup(alias))) {
      setKontaMsg("Do założenia: imię, username, hasło, data urodzenia i e-mail/telefon.", "err");
      return;
    }
    if (start) await kontaSignup(alias);
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano profilu signup", "err");
  }
}

function paintKiAvatarHint(alias) {
  const hint = $("ki-avatar-hint");
  if (!hint) return;
  const inp = $("ki-avatar");
  const file = inp && inp.files && inp.files[0];
  if (file) {
    hint.textContent = `Wybrane: „${file.name}” — zapiszę jako avatar.*`;
    return;
  }
  const row = kontaRows[alias] || {};
  const prof = row.profile || {};
  hint.textContent = prof.has_avatar
    ? `Jest „${prof.filename || "avatar"}” — nowy plik nadpisze.`
    : "Brak pliku — wybierz jpg/png/webp.";
}

async function kiUploadAvatar(alias) {
  const inp = $("ki-avatar");
  const file = inp && inp.files && inp.files[0];
  if (!file || !alias) return false;
  const fd = new FormData();
  fd.append("file", file, file.name);
  await api(`/api/accounts/${encodeURIComponent(alias)}/avatar`, {
    method: "POST",
    body: fd,
  });
  inp.value = "";
  return true;
}

async function saveKontaProfile(alias, opts) {
  const apply = !!(opts && opts.apply);
  const bio = (($("ki-bio") && $("ki-bio").value) || "").trim();
  try {
    await api(`/api/accounts/${encodeURIComponent(alias)}/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bio }),
    });
    try {
      await kiUploadAvatar(alias);
    } catch (av) {
      setKontaMsg("Zdjęcie: " + (av.message || av), "err");
      return;
    }
    kontaDirty = false;
    if (apply) {
      await api(`/api/accounts/${encodeURIComponent(alias)}/profile/apply`, {
        method: "POST",
      });
      setKontaMsg(`Wstawiam profil IG na „${alias}"…`, "ok");
    } else {
      setKontaMsg("Zapisano bio i zdjęcie na PC.", "ok");
    }
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano profilu IG", "err");
  }
}

async function saveKontaCred(alias) {
  const user = (($("ki-ig") && $("ki-ig").value) || "").trim();
  const pass = (($("ki-pass") && $("ki-pass").value) || "").trim();
  if (!user || !pass) {
    setKontaMsg("Login i hasło są wymagane do zapisu credentials.", "err");
    return;
  }
  try {
    await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ig_username: user, ig_password: pass }),
    });
    if ($("ki-pass")) $("ki-pass").value = "";
    setKontaMsg("Zapisano dane logowania.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano credentials", "err");
  }
}

async function toggleKontaPause(alias, paused) {
  const row = kontaRows[alias];
  const dev = ((row && row.cfg && row.cfg.device) || {}).device_alias;
  if (!dev) {
    setKontaMsg("Brak device_alias — nie zapiszę pauzy.", "err");
    return;
  }
  try {
    await api(`/api/schedule-accounts/${encodeURIComponent(dev)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify([{ account: alias, enabled: !paused }]),
    });
    setKontaMsg(paused ? "Konto wyłączone w osi." : "Konto wróciło do osi.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie zapisano pauzy", "err");
  }
}

async function kontaLogin(alias) {
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: alias }),
    });
    setKontaMsg(`Login wystartował dla ${alias}.`, "ok");
  } catch (e) {
    setKontaMsg(e.message || "login nieudany", "err");
  }
}

async function kontaDiscover(alias) {
  try {
    await api("/api/run-plugin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: alias, plugin: "niche_discover" }),
    });
    setKontaMsg(`Discovery niszy wystartowało dla ${alias}.`, "ok");
  } catch (e) {
    setKontaMsg(e.message || "discovery nieudane", "err");
  }
}

async function kontaSubmit2FA(alias, code) {
  const trimmed = String(code || "").trim();
  if (!trimmed) {
    setKontaMsg("Wpisz kod 2FA.", "err");
    return;
  }
  try {
    await api(`/api/verification-code/${encodeURIComponent(alias)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: trimmed }),
    });
    setKontaMsg("Kod 2FA wysłany.", "ok");
    await refreshKonta({ force: true, keep: alias });
  } catch (e) {
    setKontaMsg(e.message || "nie wysłano kodu", "err");
  }
}

async function deleteKonta(alias) {
  if (!window.confirm(
    `Usunąć konto „${alias}”? Config i credentials znikną; data trafi do trash.\nKlon (APK) z telefonu też zejdzie — chyba że inne konto trzyma ten sam pakiet. Stock IG zostaje.`
  )) return;
  const typed = window.prompt(`Wpisz alias („${alias}") albo słowo „usuń".`);
  if (typed == null) return;
  if (typed !== alias && typed.toLowerCase() !== "usuń") {
    setKontaMsg("Alias nie pasuje — anulowano.", "err");
    return;
  }
  try {
    const d = await api(`/api/accounts/${encodeURIComponent(alias)}`, { method: "DELETE" });
    if (kontaSelected === alias) kontaSelected = "";
    delete kontaRows[alias];
    const clone = (d && d.clone) || (d && d.removed && d.removed.clone) || {};
    let extra = "";
    if (clone.uninstalled) extra = ` Zdjęto klona ${clone.package || ""}.`;
    else if (clone.skipped === "shared") extra = " Klon zostaje — inne konto trzyma ten pakiet.";
    else if (clone.skipped === "stock_or_foreign") extra = " Stock IG nie był ruszany.";
    else if (clone.error) extra = ` Konto skasowane, ale klon na telefonie: ${clone.error}`;
    setKontaMsg(`Usunięto konto „${alias}".${extra}`, clone.error ? "err" : "ok");
    await refreshKonta({ force: true });
    if (typeof refreshFlota === "function") await refreshFlota();
  } catch (e) {
    setKontaMsg(e.message || "nie usunięto", "err");
  }
}

function selectKonta(alias) {
  kontaSelected = alias;
  kontaDirty = false;
  renderKontaGrid();
  renderKontaInsp();
}

let knPreviewIdentity = null;
let knReadyAlias = "";
let knApkRows = [];
// Komentarz: szablony z preview.spoof_profiles — model + fingerprint z {token}
let knSpoofProfiles = [];
const KN_PREFS_KEY = "v2.kn.prefs";
const KN_MODEL_DEVICE = "__device__";
const KN_SAFEGUARD_DEFAULTS = {
  kill_before_verify: true,
  no_parallel_ig: true,
  one_clone_job: true,
  stop_stock: true,
  sole_ig_pid: true,
};

function knSpoofVal(eid, v) {
  // Komentarz: pola tożsamości są inputami — preview je wypełnia, operator może zmienić
  const el = $(eid);
  if (!el) return "";
  if (v !== undefined) {
    el.value = v == null || v === "" ? "" : String(v);
    return el.value;
  }
  return (el.value || "").trim();
}

function knSpoofUseBoxes() {
  return [...document.querySelectorAll("#kn-spoof [data-spoof-use]")];
}

function knSpoofUse() {
  // Komentarz: checkbox = czy to pole idzie do argv klona
  const use = {};
  knSpoofUseBoxes().forEach((cb) => {
    use[cb.dataset.spoofUse] = !!cb.checked;
  });
  return use;
}

function knSafeguardBoxes() {
  return [...document.querySelectorAll("#kn-safeguards [data-safeguard]")];
}

function knSafeguards() {
  // Komentarz: payload identity.safeguards — disabled one_clone_job i tak idzie jako ON
  const sg = { ...KN_SAFEGUARD_DEFAULTS };
  knSafeguardBoxes().forEach((cb) => {
    const key = cb.dataset.safeguard;
    if (!key) return;
    sg[key] = !!cb.checked;
  });
  return sg;
}

function knDefaultPrefs() {
  return { use: {}, safeguards: { ...KN_SAFEGUARD_DEFAULTS }, lastModel: "", lastAuthTab: "login" };
}

function knLoadPrefs() {
  // Komentarz: odczyt przy otwarciu / Odśwież — nie resetujemy odznaczeń
  try {
    const raw = localStorage.getItem(KN_PREFS_KEY);
    if (!raw) return knDefaultPrefs();
    const p = JSON.parse(raw);
    const use = (p && p.use && typeof p.use === "object") ? p.use : {};
    const sg = (p && p.safeguards && typeof p.safeguards === "object") ? p.safeguards : {};
    const lastModel = (p && typeof p.lastModel === "string") ? p.lastModel : "";
    const lastAuthTab = (p && p.lastAuthTab === "signup") ? "signup" : "login";
    return {
      use,
      safeguards: { ...KN_SAFEGUARD_DEFAULTS, ...sg },
      lastModel,
      lastAuthTab,
    };
  } catch (_) {
    return knDefaultPrefs();
  }
}

function knSavePrefs(partial) {
  const cur = knLoadPrefs();
  const next = {
    use: partial && partial.use ? partial.use : cur.use,
    safeguards: partial && partial.safeguards ? partial.safeguards : cur.safeguards,
    lastModel: partial && Object.prototype.hasOwnProperty.call(partial, "lastModel")
      ? String(partial.lastModel || "")
      : cur.lastModel,
    lastAuthTab: partial && Object.prototype.hasOwnProperty.call(partial, "lastAuthTab")
      ? (partial.lastAuthTab === "signup" ? "signup" : "login")
      : (cur.lastAuthTab === "signup" ? "signup" : "login"),
  };
  try {
    localStorage.setItem(KN_PREFS_KEY, JSON.stringify(next));
  } catch (_) { /* tryb prywatny / pełny storage */ }
}

function knPersistUse() {
  knSavePrefs({ use: knSpoofUse() });
}

function knPersistSafeguards() {
  knSavePrefs({ safeguards: knSafeguards() });
}

function paintKnSpoofCount() {
  const boxes = knSpoofUseBoxes();
  const n = boxes.filter((b) => b.checked).length;
  const el = $("kn-spoof-count");
  if (el) el.textContent = boxes.length ? `(${n}/${boxes.length} w użyciu)` : "";
}

function syncKnSpoofEnabled() {
  // Komentarz: odznaczony spoof — pole widać, ale nie edytujemy
  knSpoofUseBoxes().forEach((cb) => {
    const item = cb.closest(".kn-spoof-item");
    if (item) item.classList.toggle("is-off", !cb.checked);
    if (cb.dataset.spoofUse === "gps") {
      ["kn-spoof-gps-lat", "kn-spoof-gps-lon"].forEach((id) => {
        const inp = $(id);
        if (inp) inp.disabled = !cb.checked;
      });
      return;
    }
    const inp = item && item.querySelector("input[type='text']");
    if (inp) inp.disabled = !cb.checked;
  });
  paintKnSpoofCount();
}

function resetKnSpoofUse(use) {
  // Komentarz: stosuj zapisany use; pusta mapa = HTML defaults (ON), nie „wszystko ON” z preview
  knSpoofUseBoxes().forEach((cb) => {
    if (cb.disabled) {
      cb.checked = true;
      return;
    }
    const key = cb.dataset.spoofUse;
    cb.checked = !use || use[key] !== false;
  });
  syncKnSpoofEnabled();
}

function paintKnSafeguards(sg) {
  const map = { ...KN_SAFEGUARD_DEFAULTS, ...(sg || {}) };
  knSafeguardBoxes().forEach((cb) => {
    const key = cb.dataset.safeguard;
    if (cb.disabled) {
      cb.checked = KN_SAFEGUARD_DEFAULTS[key] !== false;
      return;
    }
    cb.checked = key in map ? !!map[key] : !!KN_SAFEGUARD_DEFAULTS[key];
  });
}

function knFingerprintToken() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789";
  let s = "";
  for (let i = 0; i < 10; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

function knFingerprintFromTemplate(tmpl) {
  const t = String(tmpl || "");
  if (t.includes("{token}")) return t.replace("{token}", knFingerprintToken());
  return t;
}

function knDeviceForSpoof() {
  // Komentarz: model/product z karty Floty albo z adb devices -l (product:OnePlus8T)
  const alias = (($("kn-device") && $("kn-device").value) || "").trim();
  const fromList = (typeof devices !== "undefined" ? devices : []).find((d) => d.alias === alias) || {};
  const serial = fromList.adb_id || "";
  const adb = (typeof adbRows !== "undefined" ? adbRows : []).find(
    (r) => r.serial === serial || r.registered_as === alias,
  ) || {};
  return {
    model: fromList.model || "",
    product: fromList.product || adb.product || "",
    manufacturer: fromList.manufacturer || "",
    android_version: fromList.android_version || "",
  };
}

function knPrettyProduct(product) {
  const p = String(product || "").trim();
  if (!p) return "";
  return p.replace(/([A-Za-z])(\d)/g, "$1 $2").replace(/_/g, " ");
}

function knPrettyDeviceModel(dev) {
  const d = dev || {};
  const match = knProfileMatchingDevice(d);
  if (match) return match.model;
  const product = knPrettyProduct(d.product);
  if (product) return product;
  const mfr = String(d.manufacturer || "").trim();
  const model = String(d.model || "").replace(/_/g, " ").trim();
  if (mfr && model && !model.toLowerCase().includes(mfr.toLowerCase())) return `${mfr} ${model}`;
  return model || mfr || "ten telefon";
}

function knNormModelBlob(s) {
  return String(s || "").toLowerCase().replace(/[\s_-]+/g, "");
}

function knProfileMatchingDevice(dev) {
  const d = dev || {};
  const blob = knNormModelBlob([d.model, d.product, d.manufacturer].filter(Boolean).join(" "));
  if (!blob) return null;
  return (knSpoofProfiles || []).find((p) => {
    const compact = knNormModelBlob(p && p.model);
    return compact && blob.includes(compact);
  }) || null;
}

function knFingerprintFromDevice(dev) {
  const d = dev || {};
  const mfr = String(d.manufacturer || "samsung").toLowerCase().replace(/\s+/g, "");
  const product = String(d.product || "device").toLowerCase().replace(/\s+/g, "");
  const ver = String(d.android_version || "11");
  return `${mfr}/${product}/${product}:${ver}/RP1A.200720.012/${knFingerprintToken()}:user/release-keys`;
}

function knResolveModelPick(val) {
  if (val === KN_MODEL_DEVICE) {
    const dev = knDeviceForSpoof();
    const match = knProfileMatchingDevice(dev);
    if (match) {
      return { model: match.model, fingerprint: knFingerprintFromTemplate(match.fingerprint) };
    }
    return { model: knPrettyDeviceModel(dev), fingerprint: knFingerprintFromDevice(dev) };
  }
  const prof = (knSpoofProfiles || []).find((p) => p && p.model === val);
  if (prof) {
    return { model: prof.model, fingerprint: knFingerprintFromTemplate(prof.fingerprint) };
  }
  return { model: val, fingerprint: "" };
}

function knApplyModelPick(opts) {
  // Komentarz: tylko model + fingerprint z szablonu; android_id / MAC / IMEI / GAID bez zmian
  const sel = $("kn-spoof-model-pick");
  const val = (sel && sel.value) || "";
  if (!val) return;
  const resolved = knResolveModelPick(val);
  if (resolved.model) knSpoofVal("kn-spoof-model", resolved.model);
  if (opts && opts.regenFp && resolved.fingerprint) knSpoofVal("kn-spoof-fp", resolved.fingerprint);
  if (knPreviewIdentity && resolved.model) {
    knPreviewIdentity.model = resolved.model;
    if (opts && opts.regenFp && resolved.fingerprint) knPreviewIdentity.fingerprint = resolved.fingerprint;
  }
}

function knDevicePickLabel(dev) {
  const pretty = knPrettyDeviceModel(dev);
  return pretty && pretty !== "ten telefon" ? `jak ten telefon (${pretty})` : "jak ten telefon";
}

function fillKnModelPick(preferred) {
  const sel = $("kn-spoof-model-pick");
  if (!sel) return;
  const dev = knDeviceForSpoof();
  const opts = [{ value: KN_MODEL_DEVICE, label: knDevicePickLabel(dev) }];
  (knSpoofProfiles || []).forEach((p) => {
    if (!p || !p.model) return;
    opts.push({ value: p.model, label: p.model });
  });
  const identModel = knSpoofVal("kn-spoof-model");
  const want = preferred || "";
  if (want && !opts.some((o) => o.value === want)) {
    opts.push({
      value: want,
      label: want === KN_MODEL_DEVICE ? knDevicePickLabel(dev) : want,
    });
  }
  sel.innerHTML = opts.map((o) =>
    `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join("");
  if (want && opts.some((o) => o.value === want)) {
    sel.value = want;
    return;
  }
  const byIdent = opts.find((o) => o.value === identModel);
  if (byIdent) {
    sel.value = byIdent.value;
    return;
  }
  // Komentarz: bez lastModel nie udawaj „jak ten telefon”, gdy preview wylosował inny model
  if (identModel) {
    const extra = document.createElement("option");
    extra.value = identModel;
    extra.textContent = identModel;
    sel.appendChild(extra);
    sel.value = identModel;
    return;
  }
  sel.value = KN_MODEL_DEVICE;
}

function knOnModelPickChange() {
  const sel = $("kn-spoof-model-pick");
  const val = (sel && sel.value) || "";
  knSavePrefs({ lastModel: val });
  if (!val) return;
  knApplyModelPick({ regenFp: true });
}

function knChosenPreviewModel() {
  // Komentarz: backend może honorować model w POST /api/clones/preview; jak nie — nadpiszemy lokalnie
  const prefs = knLoadPrefs();
  const pick = (($("kn-spoof-model-pick") && $("kn-spoof-model-pick").value) || prefs.lastModel || "").trim();
  if (!pick) return "";
  if (pick === KN_MODEL_DEVICE) return knPrettyDeviceModel(knDeviceForSpoof());
  return pick;
}

function paintKnSpoof(ident) {
  const id = ident || {};
  knSpoofVal("kn-spoof-model", id.model);
  knSpoofVal("kn-spoof-aid", id.android_id);
  knSpoofVal("kn-spoof-fp", id.fingerprint);
  knSpoofVal("kn-spoof-gaid", id.gaid);
  knSpoofVal("kn-spoof-mac", id.mac);
  knSpoofVal("kn-spoof-imei", id.imei);
  knSpoofVal("kn-spoof-drm", id.media_drm_id);
  knSpoofVal("kn-spoof-build-time", id.build_time);
  knSpoofVal("kn-spoof-boot-time", id.boot_time);
  knSpoofVal("kn-spoof-gps-lat", id.gps_lat);
  knSpoofVal("kn-spoof-gps-lon", id.gps_lon);
  knSpoofVal("kn-spoof-dns", id.dns);
  knSpoofVal("kn-spoof-sim-country", id.sim_country);
  knSpoofVal("kn-spoof-sim-operator", id.sim_operator);
  knSpoofVal("kn-spoof-sim-code", id.sim_operator_code);
  // Komentarz: use / safeguards / lastModel z prefs, nie z preview i nie „wszystko ON”
  const prefs = knLoadPrefs();
  resetKnSpoofUse(prefs.use);
  paintKnSafeguards(prefs.safeguards);
  fillKnModelPick(prefs.lastModel);
  if (prefs.lastModel) knApplyModelPick({ regenFp: true });
}

function knIdentityPayload() {
  // Komentarz: package + APK z selectów; odznaczone spoofy idą puste + use + safeguards
  const id = { ...(knPreviewIdentity || {}) };
  const pkg = (($("kn-pkg") && $("kn-pkg").value) || "").trim();
  if (pkg) id.package = pkg;
  const use = knSpoofUse();
  id.use = use;
  id.safeguards = knSafeguards();
  id.model = knSpoofVal("kn-spoof-model");
  id.android_id = knSpoofVal("kn-spoof-aid");
  id.fingerprint = knSpoofVal("kn-spoof-fp");
  id.gaid = use.gaid ? knSpoofVal("kn-spoof-gaid") : "";
  id.mac = use.mac ? knSpoofVal("kn-spoof-mac") : "";
  id.imei = use.imei ? knSpoofVal("kn-spoof-imei") : "";
  id.media_drm_id = use.media_drm_id ? knSpoofVal("kn-spoof-drm") : "";
  id.build_time = use.build_time ? knSpoofVal("kn-spoof-build-time") : "";
  id.boot_time = use.boot_time ? knSpoofVal("kn-spoof-boot-time") : "";
  const lat = knSpoofVal("kn-spoof-gps-lat");
  const lon = knSpoofVal("kn-spoof-gps-lon");
  if (use.gps && lat) id.gps_lat = Number(lat);
  else delete id.gps_lat;
  if (use.gps && lon) id.gps_lon = Number(lon);
  else delete id.gps_lon;
  id.dns = use.dns ? knSpoofVal("kn-spoof-dns") : "";
  id.sim_country = use.sim_country ? knSpoofVal("kn-spoof-sim-country") : "";
  id.sim_operator = use.sim_operator ? knSpoofVal("kn-spoof-sim-operator") : "";
  id.sim_operator_code = use.sim_operator_code ? knSpoofVal("kn-spoof-sim-code") : "";
  const apkVal = (($("kn-apk") && $("kn-apk").value) || "").trim();
  if (apkVal.startsWith("local:")) id.apk = apkVal.slice("local:".length);
  else delete id.apk;
  return id;
}

function knAuthTab() {
  const on = document.querySelector(".kn-tab.on[data-kn-auth]");
  return (on && on.dataset.knAuth === "signup") ? "signup" : "login";
}

function setKnAuthTab(tab) {
  // Komentarz: zakładka Zaloguj vs Załóż nowe konto — zapis w prefs
  const want = tab === "signup" ? "signup" : "login";
  ["login", "signup"].forEach((name) => {
    const btn = $(`kn-tab-${name}`);
    const pane = $(`kn-pane-${name}`);
    const active = name === want;
    if (btn) {
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (pane) pane.hidden = !active;
  });
  const loginBtn = $("kn-ok-login");
  const signupBtn = $("kn-ok-signup");
  if (loginBtn) loginBtn.hidden = want !== "login";
  if (signupBtn) signupBtn.hidden = want !== "signup";
  knSavePrefs({ lastAuthTab: want });
}

function setKnLoginEnabled(on) {
  const loginBtn = $("kn-ok-login");
  const signupBtn = $("kn-ok-signup");
  if (loginBtn) loginBtn.disabled = !on;
  if (signupBtn) signupBtn.disabled = !on;
}

async function fillNewAccountDevices() {
  const sel = $("kn-device");
  if (!sel) return;
  // Komentarz: dialog bywa otwarty zanim refreshKonta dociągnie /api/devices
  if (!(devices || []).length) {
    try {
      const dev = await api("/api/devices");
      if (Array.isArray(dev)) devices = dev;
    } catch (e) {
      showBanner("kn-msg", e.message || "nie wczytano urządzeń", "err");
    }
  }
  const aliases = (devices || []).map((d) => d.alias);
  fillSelect(sel, aliases, sel.value || aliases[0] || "");
  try {
    await loadKnPreview();
  } catch (e) {
    showBanner("kn-msg", e.message || "preview", "err");
  }
  loadKnApks().catch(() => {});
  if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
}

/** Odśwież dane dialogu — bez czyszczenia aliasu/loginu i bez startu joba. */
async function refreshNewAccountDialog() {
  const deviceKeep = (($("kn-device") && $("kn-device").value) || "").trim();
  const pkgKeep = (($("kn-pkg") && $("kn-pkg").value) || "").trim();
  const btn = $("kn-refresh");
  if (btn) btn.disabled = true;
  showBanner("kn-msg", "Odświeżam dane…", "ok");
  try {
    const [dev, adb] = await Promise.all([
      api("/api/devices"),
      api("/api/adb-devices").catch(() => adbRows || []),
    ]);
    if (Array.isArray(dev)) devices = dev;
    if (Array.isArray(adb)) adbRows = adb;
    const sel = $("kn-device");
    if (sel) {
      const aliases = (devices || []).map((d) => d.alias);
      fillSelect(sel, aliases, deviceKeep || sel.value || aliases[0] || "");
    }
    let errMsg = "";
    try {
      await loadKnPreview();
      const pkgSel = $("kn-pkg");
      if (pkgSel && pkgKeep && [...pkgSel.options].some((o) => o.value === pkgKeep)) {
        pkgSel.value = pkgKeep;
        if (knPreviewIdentity) knPreviewIdentity.package = pkgKeep;
      }
    } catch (e) {
      errMsg = e.message || "preview";
    }
    try {
      // Komentarz: device_alias → dumpsys stock IG, bez listy Aptoide
      const apkDoc = await loadKnApks();
      if (!errMsg && apkDoc && apkDoc.stock_error) {
        errMsg = "stock IG: " + apkDoc.stock_error;
      }
    } catch (e) {
      errMsg = e.message || "APK";
    }
    if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
    if (errMsg) showBanner("kn-msg", errMsg, "err");
    else showBanner("kn-msg", "Odświeżono listę, preview, spoof i APK.", "ok");
  } catch (e) {
    showBanner("kn-msg", e.message || "odświeżanie nieudane", "err");
  } finally {
    if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
    else if (btn) btn.disabled = false;
  }
}

function fillNewAccountPkgs() {
  // Komentarz: pakiety z preview (wolne APPID), nie z clones[] urządzenia
  const sel = $("kn-pkg");
  if (!sel) return;
  const ident = knPreviewIdentity || {};
  const opts = [];
  sel.querySelectorAll("option").forEach((o) => { if (o.value) opts.push(o.value); });
  if (!opts.length && ident.package) {
    fillSelect(sel, [ident.package], ident.package);
  }
}

async function loadKnPreview() {
  const sel = $("kn-device");
  if (sel && !(sel.value || "").trim() && (devices || []).length) {
    const aliases = devices.map((d) => d.alias);
    fillSelect(sel, aliases, aliases[0] || "");
  }
  const deviceAlias = ((sel && sel.value) || "").trim();
  if (!deviceAlias) {
    knPreviewIdentity = null;
    paintKnSpoof(null);
    showBanner("kn-msg", "Brak urządzenia — dodaj telefon we Flocie albo Odśwież.", "err");
    return;
  }
  try {
    const body = { device_alias: deviceAlias };
    const chosenModel = knChosenPreviewModel();
    if (chosenModel) body.model = chosenModel;
    const d = await api("/api/clones/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    knPreviewIdentity = d.identity || null;
    const profiles = d.spoof_profiles || (d.identity && d.identity.spoof_profiles);
    if (Array.isArray(profiles)) knSpoofProfiles = profiles;
    const pkgs = (d.suggested_packages || []).slice();
    if (knPreviewIdentity && knPreviewIdentity.package && !pkgs.includes(knPreviewIdentity.package)) {
      pkgs.unshift(knPreviewIdentity.package);
    }
    fillSelect($("kn-pkg"), pkgs, (knPreviewIdentity && knPreviewIdentity.package) || "");
    paintKnSpoof(knPreviewIdentity);
    if (!knPreviewIdentity || !knPreviewIdentity.mac) {
      showBanner("kn-msg", "Preview nie zwrócił tożsamości (MAC/IMEI).", "err");
    } else if ($("kn-msg") && !$("kn-msg").hidden && $("kn-msg").classList.contains("err")) {
      showBanner("kn-msg", "");
    }
    if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
  } catch (e) {
    // Komentarz: nawet przy 404/timeout pomaluj to, co już jest — nie zostawiaj samych „—”
    paintKnSpoof(knPreviewIdentity);
    showBanner("kn-msg", e.message || "preview", "err");
    throw e;
  }
}

function knApkOptionValue(row) {
  if (row.local && row.filename) return `local:${row.filename}`;
  return "";
}

function knNormVer(ver) {
  return String(ver || "").trim().toLowerCase();
}

function paintKnStock(d) {
  // Komentarz: versionName stock IG z dumpsys — źródło klona ma być tą samą wersją
  const el = $("kn-stock-ver");
  if (!el) return;
  const st = d && d.stock;
  if (st && (st.version || st.version_code != null)) {
    const code = st.version_code != null ? ` · kod ${st.version_code}` : "";
    // Komentarz: tylko informacja — źródło klona wybierasz z APK na dysku
    el.textContent = `stock IG na telefonie (info): ${st.version || "—"}${code}`;
    return;
  }
  if (d && d.stock_error) {
    el.textContent = `stock IG: nie odczytano (${d.stock_error})`;
    return;
  }
  el.textContent = "";
}

async function loadKnApks() {
  const deviceAlias = (($("kn-device") && $("kn-device").value) || "").trim();
  // Komentarz: device_alias → dumpsys stock, bez Aptoide
  const q = new URLSearchParams();
  if (deviceAlias) q.set("device_alias", deviceAlias);
  const d = await api("/api/clones/apks" + (q.toString() ? `?${q.toString()}` : ""));
  knApkRows = [];
  (d.local || []).forEach((r) => knApkRows.push(r));
  paintKnStock(d);
  const sel = $("kn-apk");
  if (!sel) return d;
  const prev = sel.value;
  const stockVer = knNormVer(d.stock && d.stock.version);
  const labels = knApkRows.map((r) => {
    const tag = r.active ? "lokalny · aktywny" : "lokalny";
    const same = stockVer && knNormVer(r.version) === stockVer ? " · jak na telefonie" : "";
    return `${r.version || r.filename} (${tag}${same})`;
  });
  sel.innerHTML = knApkRows.length
    ? knApkRows.map((r, i) =>
      `<option value="${escapeHtml(knApkOptionValue(r))}">${escapeHtml(labels[i])}</option>`).join("")
    : `<option value="">brak APK — zciągnij stock z telefonu</option>`;
  const values = knApkRows.map(knApkOptionValue);
  const stockHit = knApkRows.find((r) => stockVer && knNormVer(r.version) === stockVer);
  if (stockHit) sel.value = knApkOptionValue(stockHit);
  else if (values.includes(prev)) sel.value = prev;
  else {
    const active = knApkRows.find((r) => r.active);
    sel.value = active ? knApkOptionValue(active) : (values[0] || "");
  }
  return d;
}

async function knPullApkFromDevice() {
  const deviceAlias = (($("kn-device") && $("kn-device").value) || "").trim();
  if (!deviceAlias) throw new Error("wybierz urządzenie");
  showBanner("kn-msg", "Zciągam IG z telefonu…", "ok");
  const d = await api("/api/clones/apks/from-device", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_alias: deviceAlias }),
  });
  showBanner("kn-msg", `Z telefonu: ${d.version || "APK"} (${d.size || "?"} B)`, "ok");
  await loadKnApks();
}

/** Wypełnij dialog po udanym adb install — nie nadpisuj loginu, jeśli operator już pisze. */
function applyPendingClonePrefill() {
  const p = (typeof pendingClonePrefill !== "undefined") ? pendingClonePrefill : null;
  if (!p) return;
  if ($("kn-device") && p.device) $("kn-device").value = p.device;
  if ($("kn-pkg") && p.pkg) {
    const sel = $("kn-pkg");
    if (![...sel.options].some((o) => o.value === p.pkg)) {
      const opt = document.createElement("option");
      opt.value = p.pkg;
      opt.textContent = p.pkg;
      sel.appendChild(opt);
    }
    sel.value = p.pkg;
  }
  if ($("kn-alias") && typeof suggestedAliasForClone === "function" && !$("kn-alias").value) {
    $("kn-alias").value = suggestedAliasForClone(p.device, p.pkg);
  }
  const id = p.identity || {};
  knPreviewIdentity = { ...(knPreviewIdentity || {}), ...id, package: p.pkg || id.package };
  paintKnSpoof(knPreviewIdentity);
  const el = $("kn-clone-result");
  if (el && p.pkg) {
    el.textContent = `APPID ${p.pkg} · model ${id.model || "—"} · android-id ${id.android_id || "—"} · MAC ${id.mac || "—"} · IMEI ${id.imei || "—"}`;
  }
  if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
}

async function openNewAccount() {
  const dlg = $("konta-new-dlg");
  if (!dlg) return;
  knReadyAlias = "";
  setKnLoginEnabled(false);
  setKnAuthTab(knLoadPrefs().lastAuthTab || "login");
  const spoofBox = $("kn-spoof");
  if (spoofBox) spoofBox.open = false;
  paintKnAvatarHint();
  if (!pendingClonePrefill) {
    if ($("kn-alias")) $("kn-alias").value = "";
    if ($("kn-ig")) $("kn-ig").value = "";
    if ($("kn-pass")) $("kn-pass").value = "";
    if ($("kn-su-name")) $("kn-su-name").value = "";
    if ($("kn-su-user")) $("kn-su-user").value = "";
    if ($("kn-su-pass")) $("kn-su-pass").value = "";
    if ($("kn-su-bday")) $("kn-su-bday").value = "";
    if ($("kn-su-contact")) $("kn-su-contact").value = "";
    if ($("kn-clone-result")) $("kn-clone-result").textContent = "";
    paintKnSpoof(null);
  }
  showBanner("kn-msg", "");
  if (typeof dlg.showModal === "function") {
    if (!dlg.open) dlg.showModal();
  } else dlg.setAttribute("open", "");
  const focusEl = $("kn-alias");
  if (focusEl) focusEl.focus();
  try {
    // Komentarz: najpierw modal, potem preview — nie kasuj spoofa po starcie fetcha
    await fillNewAccountDevices();
    if (pendingClonePrefill) applyPendingClonePrefill();
  } catch (e) {
    showBanner("kn-msg", e.message || "preview", "err");
  }
  if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
}

function closeNewAccount() {
  const dlg = $("konta-new-dlg");
  if (!dlg) return;
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
  if ($("kn-pass")) $("kn-pass").value = "";
  if ($("kn-su-pass")) $("kn-su-pass").value = "";
  if ($("kn-avatar")) $("kn-avatar").value = "";
  if ($("kn-bio")) $("kn-bio").value = "";
  knReadyAlias = "";
  setKnLoginEnabled(false);
}

function paintKnAvatarHint() {
  // Komentarz: folder profilu = data/accounts/<alias>/ — set_avatar czyta stamtąd
  const hint = $("kn-avatar-hint");
  if (!hint) return;
  const alias = (($("kn-alias") && $("kn-alias").value) || "").trim() || "<alias>";
  const inp = $("kn-avatar");
  const file = inp && inp.files && inp.files[0];
  if (file) {
    hint.textContent = `Zapiszę „${file.name}” w data/accounts/${alias}/avatar.* — bot wstawi po zalogowaniu.`;
    return;
  }
  hint.textContent = `Opcjonalnie — zapiszę w folderze konta (data/accounts/${alias}/avatar.*); bot wstawi je na IG po zalogowaniu.`;
}

async function knSaveBio(alias) {
  // Komentarz: biogram z modalu Utwórz — ten sam plik co ustawienia konta
  const bio = (($("kn-bio") && $("kn-bio").value) || "").trim();
  if (!alias) return false;
  await api(`/api/accounts/${encodeURIComponent(alias)}/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bio }),
  });
  return true;
}

async function knUploadAvatar(alias) {
  // Komentarz: zdjęcie z inputu → data/accounts/<alias>/avatar.* (przed loginem)
  const inp = $("kn-avatar");
  const file = inp && inp.files && inp.files[0];
  if (!file || !alias) return false;
  const fd = new FormData();
  fd.append("file", file, file.name);
  await api(`/api/accounts/${encodeURIComponent(alias)}/avatar`, {
    method: "POST",
    body: fd,
  });
  return true;
}

async function knHasSavedCredentials(alias) {
  try {
    const st = await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`);
    return !!(st && st.configured);
  } catch (_) {
    return false;
  }
}

function knSignupPayload() {
  // Komentarz: pola z zakładki Załóż nowe konto — username z aliasu gdy puste
  const alias = (($("kn-alias") && $("kn-alias").value) || "").trim();
  const user = (($("kn-su-user") && $("kn-su-user").value) || "").trim() || alias;
  return {
    ig_username: user,
    ig_password: (($("kn-su-pass") && $("kn-su-pass").value) || "").trim(),
    signup: {
      full_name: (($("kn-su-name") && $("kn-su-name").value) || "").trim(),
      birthday: (($("kn-su-bday") && $("kn-su-bday").value) || "").trim(),
      contact: (($("kn-su-contact") && $("kn-su-contact").value) || "").trim(),
    },
  };
}

function knSignupReady(p) {
  return !!(p && p.ig_username && p.ig_password
    && p.signup && p.signup.full_name && p.signup.birthday && p.signup.contact);
}

async function knHasSavedSignup(alias) {
  try {
    const st = await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`);
    return !!(st && st.signup_configured);
  } catch (_) {
    return false;
  }
}

async function knStartLogin(alias) {
  // Komentarz: zapis creds + avatar w folderze konta, potem POST /api/login
  const ig = (($("kn-ig") && $("kn-ig").value) || "").trim();
  const pass = (($("kn-pass") && $("kn-pass").value) || "").trim();
  if (ig && pass) {
    await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ig_username: ig, ig_password: pass }),
    });
  } else if (!(await knHasSavedCredentials(alias))) {
    throw new Error("Do logowania potrzebne Login IG i hasło.");
  }
  try {
    await knUploadAvatar(alias);
    await knSaveBio(alias);
  } catch (av) {
    showBanner("kn-msg", "Profil: " + (av.message || av), "err");
  }
  if ($("kn-pass")) $("kn-pass").value = "";
  closeNewAccount();
  kontaSelected = alias;
  setKontaMsg(`Startuję logowanie „${alias}".`, "ok");
  await refreshKonta({ force: true, keep: alias });
  await kontaLogin(alias);
}

async function knStartSignup(alias) {
  // Komentarz: zapis profilu rejestracji + avatar, potem POST /api/signup
  const payload = knSignupPayload();
  if (knSignupReady(payload)) {
    await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } else if (!(await knHasSavedSignup(alias))) {
    throw new Error("Do założenia konta: imię, username, hasło, data urodzenia i e-mail/telefon.");
  }
  try {
    await knUploadAvatar(alias);
    await knSaveBio(alias);
  } catch (av) {
    showBanner("kn-msg", "Profil: " + (av.message || av), "err");
  }
  if ($("kn-su-pass")) $("kn-su-pass").value = "";
  closeNewAccount();
  kontaSelected = alias;
  setKontaMsg(`Startuję zakładanie konta „${alias}".`, "ok");
  await refreshKonta({ force: true, keep: alias });
  await kontaSignup(alias);
}

async function kontaSignup(alias) {
  try {
    await api("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account: alias }),
    });
    setKontaMsg(`Zakładanie konta wystartowało dla ${alias}.`, "ok");
  } catch (e) {
    setKontaMsg(e.message || "signup nieudany", "err");
  }
}

async function submitNewAccount(ev, opts) {
  if (ev) ev.preventDefault();
  const doLogin = !!(opts && opts.login);
  const doSignup = !!(opts && opts.signup);
  const alias = (($("kn-alias") && $("kn-alias").value) || "").trim();
  const ig = (($("kn-ig") && $("kn-ig").value) || "").trim();
  const pass = (($("kn-pass") && $("kn-pass").value) || "").trim();
  const su = knSignupPayload();
  const deviceAlias = (($("kn-device") && $("kn-device").value) || "").trim();
  if (!alias || !deviceAlias) {
    showBanner("kn-msg", "Wymagane: alias i urządzenie.", "err");
    return;
  }
  if (doSignup) {
    const target = knReadyAlias || alias;
    try {
      await knStartSignup(target);
    } catch (e) {
      showBanner("kn-msg", e.message || "zakładanie konta nieudane", "err");
    }
    return;
  }
  if (doLogin) {
    const target = knReadyAlias || alias;
    try {
      await knStartLogin(target);
    } catch (e) {
      showBanner("kn-msg", e.message || "logowanie nieudane", "err");
    }
    return;
  }
  if (knReadyAlias) {
    const hint = knAuthTab() === "signup" ? "Załóż konto" : "Zaloguj";
    showBanner("kn-msg", `Konto „${knReadyAlias}" już jest — użyj ${hint}.`, "ok");
    return;
  }
  try {
    const ident = knIdentityPayload();
    const pkg = (ident.package || "").trim();
    if (!pkg) {
      showBanner("kn-msg", "Wybierz wolną nazwę pakietu.", "err");
      return;
    }
    if (!ident.apk) {
      showBanner("kn-msg", "Wybierz wersję APK z dysku (albo zciągnij stock na dysk).", "err");
      return;
    }
    if (!ident.model || !ident.fingerprint || !ident.android_id) {
      showBanner("kn-msg", "Uzupełnij model, fingerprint i android_id.", "err");
      return;
    }
    showBanner("kn-msg", "Buduję klona, instaluję i otwieram aplikację…", "ok");
    const st = await startCloneJob(deviceAlias, ident);
    if (!st || st.state !== "ok" || !st.package) {
      throw new Error((st && st.error) || "klon nie powstał");
    }
    const verified = st.verified;
    if (verified && verified.ok === false) {
      throw new Error(verified.error || "aplikacja nie wstała");
    }
    const readyPkg = (st && st.package) || pkg;
    await api("/api/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        alias,
        ig_username: (knAuthTab() === "signup" ? su.ig_username : ig) || alias,
        device_alias: deviceAlias,
        app_clone_package: readyPkg,
      }),
    });
    if (knAuthTab() === "signup" && knSignupReady(su)) {
      await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(su),
      });
    } else if (ig && pass) {
      await api(`/api/accounts/${encodeURIComponent(alias)}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ig_username: ig, ig_password: pass }),
      });
    }
    try {
      await knUploadAvatar(alias);
      await knSaveBio(alias);
    } catch (av) {
      showBanner("kn-msg", "Konto jest, profil: " + (av.message || av), "err");
    }
    knReadyAlias = alias;
    pendingClonePrefill = null;
    setKnLoginEnabled(true);
    if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
    kontaSelected = alias;
    await refreshKonta({ force: true, keep: alias });
    // Komentarz: po klonie — login albo signup, zależnie od zakładki
    if (knAuthTab() === "signup") {
      const suReady = knSignupReady(su) || await knHasSavedSignup(alias);
      if (suReady) {
        showBanner("kn-msg", `Klon ${readyPkg} gotowy — zakładam konto „${alias}"…`, "ok");
        try {
          await knStartSignup(alias);
        } catch (se) {
          showBanner("kn-msg", se.message || "klon jest, signup nie wszedł", "err");
        }
        return;
      }
      showBanner("kn-msg", `Klon ${readyPkg} wstaje — uzupełnij dane i Załóż konto.`, "ok");
      return;
    }
    const credsReady = !!(ig && pass) || await knHasSavedCredentials(alias);
    if (credsReady) {
      showBanner("kn-msg", `Klon ${readyPkg} gotowy — loguję „${alias}"…`, "ok");
      try {
        await knStartLogin(alias);
      } catch (le) {
        showBanner("kn-msg", le.message || "klon jest, login nie wszedł", "err");
      }
      return;
    }
    showBanner("kn-msg", `Klon ${readyPkg} wstaje — konto „${alias}" zapisane. Możesz Zaloguj.`, "ok");
  } catch (e) {
    if ($("kn-pass")) $("kn-pass").value = "";
    showBanner("kn-msg", e.message || "nie utworzono", "err");
  }
}

async function refreshKonta(opts) {
  const force = !!(opts && opts.force);
  const keep = (opts && opts.keep) || kontaSelected;
  if (kontaDirty && !force) return;
  const gen = ++kontaGen;
  try {
    const [acc, dev, adb, bot, st, profs] = await Promise.all([
      api("/api/accounts"),
      api("/api/devices").catch(() => devices || []),
      api("/api/adb-devices").catch(() => []),
      api("/api/bot-config").catch(() => BOT_FALLBACK),
      api("/api/status").catch(() => status || {}),
      api("/api/humanizer_profiles").catch(() => ["default"]),
    ]);
    if (gen !== kontaGen) return;
    accounts = Array.isArray(acc) ? acc : [];
    if (Array.isArray(dev)) devices = dev;
    adbRows = Array.isArray(adb) ? adb : [];
    botConfig = bot && bot.limits ? bot : BOT_FALLBACK;
    status = st || {};
    humProfiles = Array.isArray(profs) && profs.length ? profs : ["default"];
    const aliases = accounts.map((a) => a.alias);
    const needTl = [...new Set(accounts.map((a) => a.device_alias).filter(Boolean))];
    await Promise.all(needTl.map(async (d) => {
      try { timelines[d] = await api(`/api/timeline/${encodeURIComponent(d)}`); }
      catch (_) { /* brak osi */ }
    }));
    const loaded = {};
    await Promise.all(aliases.map(async (a) => {
      try { loaded[a] = await loadKontaRow(a); }
      catch (_) { loaded[a] = { cfg: {}, cred: {}, twofa: {}, used: {}, lastAt: null, plugins: {} }; }
    }));
    if (gen !== kontaGen) return;
    kontaRows = loaded;
    if (keep && kontaRows[keep]) kontaSelected = keep;
    else if (kontaSelected && !kontaRows[kontaSelected]) kontaSelected = "";
    kontaDirty = false;
    renderKontaFilters();
    renderKontaGrid();
    renderKontaInsp();
    if (typeof updateSessionLine === "function") updateSessionLine();
  } catch (e) {
    setKontaMsg("Nie udało się wczytać kont: " + (e.message || e), "err");
  }
}

async function pollKontaAlerts() {
  if (document.hidden || currentView() !== "konta" || kontaDirty) return;
  const aliases = Object.keys(kontaRows);
  await Promise.all(aliases.map(async (a) => {
    try {
      kontaRows[a].twofa = await api(`/api/verification-code/${encodeURIComponent(a)}`);
    } catch (_) { /* zostaw poprzedni */ }
  }));
  try { devices = await api("/api/devices"); } catch (_) { /* */ }
  renderKontaGrid();
}

function bindKontaOnce() {
  if ($("konta-new") && !$("konta-new").dataset.bound) {
    $("konta-new").dataset.bound = "1";
    $("konta-new").addEventListener("click", () => {
      openNewAccount().catch((e) => showBanner("kn-msg", e.message || "preview", "err"));
    });
  }
  if ($("kn-cancel") && !$("kn-cancel").dataset.bound) {
    $("kn-cancel").dataset.bound = "1";
    $("kn-cancel").addEventListener("click", () => closeNewAccount());
  }
  if ($("konta-new-form") && !$("konta-new-form").dataset.bound) {
    $("konta-new-form").dataset.bound = "1";
    $("konta-new-form").addEventListener("submit", (ev) => submitNewAccount(ev));
  }
  if ($("kn-device") && !$("kn-device").dataset.bound) {
    $("kn-device").dataset.bound = "1";
    $("kn-device").addEventListener("change", () => {
      loadKnPreview().catch((e) => showBanner("kn-msg", e.message || "preview", "err"));
      loadKnApks().catch(() => {});
      if (typeof syncCloneCreateEnabled === "function") syncCloneCreateEnabled();
    });
  }
  if ($("kn-pkg") && !$("kn-pkg").dataset.bound) {
    $("kn-pkg").dataset.bound = "1";
    $("kn-pkg").addEventListener("change", () => {
      if (knPreviewIdentity) knPreviewIdentity.package = $("kn-pkg").value;
    });
  }
  if ($("kn-refresh") && !$("kn-refresh").dataset.bound) {
    $("kn-refresh").dataset.bound = "1";
    $("kn-refresh").addEventListener("click", () => {
      refreshNewAccountDialog().catch((e) => showBanner("kn-msg", e.message || "odświeżanie", "err"));
    });
  }
  if ($("kn-clone-cancel") && !$("kn-clone-cancel").dataset.bound) {
    $("kn-clone-cancel").dataset.bound = "1";
    $("kn-clone-cancel").addEventListener("click", () => {
      if (typeof cancelCloneJob !== "function") return;
      cancelCloneJob().catch((e) => showBanner("kn-msg", e.message || "anulowanie nieudane", "err"));
    });
  }
  if ($("kn-apk-device") && !$("kn-apk-device").dataset.bound) {
    $("kn-apk-device").dataset.bound = "1";
    $("kn-apk-device").addEventListener("click", () => {
      knPullApkFromDevice().catch((e) => showBanner("kn-msg", e.message || "pull", "err"));
    });
  }
  if ($("kn-ok-login") && !$("kn-ok-login").dataset.bound) {
    $("kn-ok-login").dataset.bound = "1";
    $("kn-ok-login").addEventListener("click", () => submitNewAccount(null, { login: true }));
  }
  if ($("kn-ok-signup") && !$("kn-ok-signup").dataset.bound) {
    $("kn-ok-signup").dataset.bound = "1";
    $("kn-ok-signup").addEventListener("click", () => submitNewAccount(null, { signup: true }));
  }
  document.querySelectorAll("[data-kn-auth]").forEach((btn) => {
    if (btn.dataset.boundTab) return;
    btn.dataset.boundTab = "1";
    btn.addEventListener("click", () => setKnAuthTab(btn.dataset.knAuth));
  });
  if ($("kn-spoof") && !$("kn-spoof").dataset.boundUse) {
    $("kn-spoof").dataset.boundUse = "1";
    $("kn-spoof").addEventListener("change", (ev) => {
      const t = ev && ev.target;
      if (!t) return;
      if (t.id === "kn-spoof-model-pick") {
        knOnModelPickChange();
        return;
      }
      if (t.getAttribute && t.getAttribute("data-spoof-use")) {
        syncKnSpoofEnabled();
        knPersistUse();
      }
    });
  }
  if ($("kn-safeguards") && !$("kn-safeguards").dataset.boundUse) {
    $("kn-safeguards").dataset.boundUse = "1";
    $("kn-safeguards").addEventListener("change", () => knPersistSafeguards());
  }
  if ($("kn-avatar") && !$("kn-avatar").dataset.bound) {
    $("kn-avatar").dataset.bound = "1";
    $("kn-avatar").addEventListener("change", () => paintKnAvatarHint());
  }
  if ($("kn-alias") && !$("kn-alias").dataset.boundHint) {
    $("kn-alias").dataset.boundHint = "1";
    $("kn-alias").addEventListener("input", () => paintKnAvatarHint());
  }
  if (typeof resumeCloneJobPoll === "function") resumeCloneJobPoll().catch(() => {});
}

bindKontaOnce();
if (typeof currentView === "function" && currentView() === "konta") {
  refreshKonta({ force: true }).catch(() => {});
}
